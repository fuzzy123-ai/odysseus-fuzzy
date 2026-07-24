"""Typed, content-free mappings from code engines to canonical USI identity.

The module contains value contracts only.  It performs no filesystem, source,
engine, process, network, store, or runtime access.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Any, ClassVar, Iterable, Mapping, TypeVar
import unicodedata

from src.unified_source_index_contract import (
    CodeRangeLocator,
    EntityKind,
    EntityRecord,
    RecordKind,
    RecordRef,
    RelationRecord,
    RelationKind,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    UnifiedSourceIndexContractError,
    canonical_json,
    make_entity_id,
    make_relation_id,
    make_source_id,
    make_source_version_id,
)


CODE_INTELLIGENCE_CONTRACT_SCHEMA = "odysseus.code_intelligence.mapping.v1"
MAX_PATH_BYTES = 4096
MAX_BYTE_OFFSET = 10_000_000_000
MAX_LINE = 10_000_000
MAX_COLUMN = 1_000_000
MAX_BATCH_ITEMS = 100_000

_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_FALLBACK_RE = re.compile(r"^cbm_(file|symbol|edge)_[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class CodeIntelligenceContractError(ValueError):
    """Raised when a mapping is ambiguous, incomplete, or path-unsafe."""


class ExtractionMethod(StrEnum):
    CBM_PARSER = "cbm_parser"
    CBM_INDEX = "cbm_index"
    AST_LSP = "ast_lsp"
    USI_RECONCILE = "usi_reconcile"
    FALLBACK = "fallback"


class CodeSymbolKind(StrEnum):
    MODULE = "module"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    TRAIT = "trait"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    PROPERTY = "property"
    FIELD = "field"
    VARIABLE = "variable"
    TYPE_ALIAS = "type_alias"
    OTHER = "other"


E = TypeVar("E", bound=StrEnum)


def normalize_repo_relative_path(value: Any) -> str:
    """Return one NFC, forward-slash, repository-relative UTF-8 path."""

    if not isinstance(value, str):
        raise CodeIntelligenceContractError("relative_path must be text")
    path = unicodedata.normalize("NFC", value.strip())
    try:
        encoded = path.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise CodeIntelligenceContractError("relative_path must be valid UTF-8") from exc
    if not path or len(encoded) > MAX_PATH_BYTES:
        raise CodeIntelligenceContractError("relative_path must be non-empty and bounded")
    if _CONTROL_RE.search(path) or "\\" in path:
        raise CodeIntelligenceContractError("relative_path contains unsafe characters")
    if path.startswith(("/", "~")) or _DRIVE_RE.match(path):
        raise CodeIntelligenceContractError("relative_path must not be absolute")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CodeIntelligenceContractError("relative_path contains invalid segments")
    return path


def symbol_natural_key(
    symbol_kind: CodeSymbolKind | str,
    qualified_name: Any,
    signature_fingerprint: Any,
) -> str:
    """Build the natural key required by the USI symbol entity."""

    kind = _enum(symbol_kind, CodeSymbolKind, "symbol_kind")
    name = _name(qualified_name, "qualified_name")
    signature = _sha256(signature_fingerprint, "signature_fingerprint")
    return f"code:{kind.value}:{name}:{signature}"


@dataclass(frozen=True, slots=True)
class CodeLocation:
    relative_path: str
    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    encoding: str = "utf-8"
    SCHEMA: ClassVar[str] = f"{CODE_INTELLIGENCE_CONTRACT_SCHEMA}.location"

    def __post_init__(self) -> None:
        path = normalize_repo_relative_path(self.relative_path)
        start_byte = _integer(self.start_byte, "start_byte", maximum=MAX_BYTE_OFFSET)
        end_byte = _integer(self.end_byte, "end_byte", maximum=MAX_BYTE_OFFSET)
        start_line = _integer(self.start_line, "start_line", minimum=1, maximum=MAX_LINE)
        end_line = _integer(self.end_line, "end_line", minimum=1, maximum=MAX_LINE)
        start_column = _integer(self.start_column, "start_column", maximum=MAX_COLUMN)
        end_column = _integer(self.end_column, "end_column", maximum=MAX_COLUMN)
        if end_byte <= start_byte:
            raise CodeIntelligenceContractError("byte range must be non-empty and half-open")
        if (end_line, end_column) <= (start_line, start_column):
            raise CodeIntelligenceContractError("line/column range must be non-empty and ordered")
        if self.encoding != "utf-8":
            raise CodeIntelligenceContractError("code locations must use utf-8")
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "start_byte", start_byte)
        object.__setattr__(self, "end_byte", end_byte)
        object.__setattr__(self, "start_line", start_line)
        object.__setattr__(self, "start_column", start_column)
        object.__setattr__(self, "end_line", end_line)
        object.__setattr__(self, "end_column", end_column)

    def to_usi_locator(self) -> CodeRangeLocator:
        return CodeRangeLocator(
            self.relative_path,
            self.start_line,
            self.start_column,
            self.end_line,
            self.end_column,
        )

    def to_dict(self) -> dict[str, Any]:
        return _record_dict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CodeLocation":
        return cls(**_strict_fields(cls, value))


@dataclass(frozen=True, slots=True)
class ExtractionEvidence:
    method: ExtractionMethod
    confidence: float
    extractor_name: str
    extractor_version: str
    incomplete_parse: bool
    SCHEMA: ClassVar[str] = f"{CODE_INTELLIGENCE_CONTRACT_SCHEMA}.extraction_evidence"

    def __post_init__(self) -> None:
        method = _enum(self.method, ExtractionMethod, "method")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise CodeIntelligenceContractError("confidence must be numeric")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise CodeIntelligenceContractError("confidence must be between 0 and 1")
        if not isinstance(self.incomplete_parse, bool):
            raise CodeIntelligenceContractError("incomplete_parse must be boolean")
        extractor_name = _opaque(self.extractor_name, "extractor_name")
        extractor_version = _opaque(self.extractor_version, "extractor_version")
        _opaque(f"{extractor_name}@{extractor_version}", "extractor_profile_ref")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "extractor_name", extractor_name)
        object.__setattr__(self, "extractor_version", extractor_version)

    @property
    def extractor_profile_ref(self) -> str:
        return f"{self.extractor_name}@{self.extractor_version}"

    def to_dict(self) -> dict[str, Any]:
        return _record_dict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExtractionEvidence":
        return cls(**_strict_fields(cls, value))


@dataclass(frozen=True, slots=True)
class CodeFileMapping:
    repo_id: str
    engine_project_ref: str
    engine_file_ref: str
    owner_scope: str
    source_id: str
    source_version_id: str
    relative_path: str
    revision_ref: str
    content_hash: str
    byte_length: int
    evidence: ExtractionEvidence
    fallback_key: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_INTELLIGENCE_CONTRACT_SCHEMA}.file"

    def __post_init__(self) -> None:
        repo_id = _opaque(self.repo_id, "repo_id")
        project_ref = _opaque(self.engine_project_ref, "engine_project_ref")
        file_ref = _optional_opaque(self.engine_file_ref, "engine_file_ref")
        if not isinstance(self.owner_scope, str):
            raise CodeIntelligenceContractError("owner_scope must be text")
        owner_scope = self.owner_scope.strip()
        source_id = _usi_id(self.source_id, RecordKind.SOURCE, "source_id")
        version_id = _usi_id(self.source_version_id, RecordKind.SOURCE_VERSION, "source_version_id")
        path = normalize_repo_relative_path(self.relative_path)
        revision_ref = _opaque(self.revision_ref, "revision_ref")
        source_hash = _sha256(self.content_hash, "content_hash")
        byte_length = _integer(self.byte_length, "byte_length", maximum=MAX_BYTE_OFFSET)
        if not isinstance(self.evidence, ExtractionEvidence):
            raise CodeIntelligenceContractError("evidence must be ExtractionEvidence")
        try:
            expected_source_id = make_source_id(
                owner_scope,
                SourceKind.CODE,
                f"repo:{repo_id}/{path}",
            )
            expected_version_id = make_source_version_id(source_id, revision_ref, source_hash)
        except UnifiedSourceIndexContractError as exc:
            raise CodeIntelligenceContractError("canonical USI file identity is invalid") from exc
        if source_id != expected_source_id:
            raise CodeIntelligenceContractError("source_id does not match canonical repo identity")
        if version_id != expected_version_id:
            raise CodeIntelligenceContractError("source_version_id does not match canonical version identity")
        expected = _fallback(
            "file",
            {
                "repo_id": repo_id,
                "source_id": source_id,
                "source_version_id": version_id,
                "relative_path": path,
                "content_hash": source_hash,
            },
        )
        _matching_fallback(self.fallback_key, expected, "file")
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "engine_project_ref", project_ref)
        object.__setattr__(self, "engine_file_ref", file_ref)
        object.__setattr__(self, "owner_scope", owner_scope)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version_id", version_id)
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "revision_ref", revision_ref)
        object.__setattr__(self, "content_hash", source_hash)
        object.__setattr__(self, "byte_length", byte_length)
        object.__setattr__(self, "fallback_key", expected)

    @classmethod
    def create(
        cls,
        source: SourceRecord,
        version: SourceVersionRecord,
        *,
        repo_id: str,
        relative_path: str,
        byte_length: int,
        engine_project_ref: str,
        engine_file_ref: str = "",
        evidence: ExtractionEvidence,
    ) -> "CodeFileMapping":
        if not isinstance(source, SourceRecord) or source.source_kind is not SourceKind.CODE:
            raise CodeIntelligenceContractError("source must be a USI code SourceRecord")
        if not isinstance(version, SourceVersionRecord) or version.source_id != source.source_id:
            raise CodeIntelligenceContractError("version must belong to source")
        safe_repo = _opaque(repo_id, "repo_id")
        safe_path = normalize_repo_relative_path(relative_path)
        if source.canonical_ref != f"repo:{safe_repo}/{safe_path}":
            raise CodeIntelligenceContractError("source canonical_ref does not match repo-relative path")
        return cls(
            safe_repo,
            engine_project_ref,
            engine_file_ref,
            source.owner_scope,
            source.source_id,
            version.source_version_id,
            safe_path,
            version.revision_ref,
            version.content_hash,
            byte_length,
            evidence,
        )

    @property
    def effective_file_ref(self) -> str:
        return self.engine_file_ref or self.fallback_key

    def to_dict(self) -> dict[str, Any]:
        return _record_dict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CodeFileMapping":
        data = _strict_fields(cls, value)
        data["evidence"] = ExtractionEvidence.from_dict(data["evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CodeSymbolMapping:
    engine_project_ref: str
    engine_file_ref: str
    engine_symbol_ref: str
    file_fallback_key: str
    source_id: str
    source_version_id: str
    entity_id: str
    symbol_kind: CodeSymbolKind
    qualified_name: str
    signature_fingerprint: str
    location: CodeLocation
    evidence: ExtractionEvidence
    fallback_key: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_INTELLIGENCE_CONTRACT_SCHEMA}.symbol"

    def __post_init__(self) -> None:
        project_ref = _opaque(self.engine_project_ref, "engine_project_ref")
        file_ref = _optional_opaque(self.engine_file_ref, "engine_file_ref")
        symbol_ref = _optional_opaque(self.engine_symbol_ref, "engine_symbol_ref")
        file_key = _fallback_value(self.file_fallback_key, "file_fallback_key", "file")
        source_id = _usi_id(self.source_id, RecordKind.SOURCE, "source_id")
        version_id = _usi_id(self.source_version_id, RecordKind.SOURCE_VERSION, "source_version_id")
        entity_id = _usi_id(self.entity_id, RecordKind.ENTITY, "entity_id")
        kind = _enum(self.symbol_kind, CodeSymbolKind, "symbol_kind")
        name = _name(self.qualified_name, "qualified_name")
        signature = _sha256(self.signature_fingerprint, "signature_fingerprint")
        if not isinstance(self.location, CodeLocation):
            raise CodeIntelligenceContractError("location must be CodeLocation")
        if not isinstance(self.evidence, ExtractionEvidence):
            raise CodeIntelligenceContractError("evidence must be ExtractionEvidence")
        natural_key = symbol_natural_key(kind, name, signature)
        try:
            expected_entity_id = make_entity_id(
                version_id,
                EntityKind.SYMBOL,
                natural_key,
                self.location.to_usi_locator(),
                self.evidence.extractor_profile_ref,
            )
        except UnifiedSourceIndexContractError as exc:
            raise CodeIntelligenceContractError("canonical USI symbol identity is invalid") from exc
        if entity_id != expected_entity_id:
            raise CodeIntelligenceContractError("entity_id does not match canonical symbol identity")
        expected = _fallback(
            "symbol",
            {
                "source_id": source_id,
                "source_version_id": version_id,
                "entity_id": entity_id,
                "symbol_kind": kind.value,
                "qualified_name": name,
                "signature_fingerprint": signature,
                "location": self.location.to_dict(),
            },
        )
        _matching_fallback(self.fallback_key, expected, "symbol")
        object.__setattr__(self, "engine_project_ref", project_ref)
        object.__setattr__(self, "engine_file_ref", file_ref)
        object.__setattr__(self, "engine_symbol_ref", symbol_ref)
        object.__setattr__(self, "file_fallback_key", file_key)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version_id", version_id)
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "symbol_kind", kind)
        object.__setattr__(self, "qualified_name", name)
        object.__setattr__(self, "signature_fingerprint", signature)
        object.__setattr__(self, "fallback_key", expected)

    @classmethod
    def create(
        cls,
        file_mapping: CodeFileMapping,
        entity: EntityRecord,
        *,
        symbol_kind: CodeSymbolKind | str,
        qualified_name: str,
        signature_fingerprint: str,
        location: CodeLocation,
        engine_symbol_ref: str = "",
        evidence: ExtractionEvidence,
    ) -> "CodeSymbolMapping":
        if not isinstance(file_mapping, CodeFileMapping):
            raise CodeIntelligenceContractError("file_mapping must be CodeFileMapping")
        if not isinstance(entity, EntityRecord) or entity.entity_kind is not EntityKind.SYMBOL:
            raise CodeIntelligenceContractError("entity must be a USI symbol EntityRecord")
        if (
            entity.source_id != file_mapping.source_id
            or entity.source_version_id != file_mapping.source_version_id
        ):
            raise CodeIntelligenceContractError("entity must belong to mapped source version")
        if not isinstance(location, CodeLocation) or location.relative_path != file_mapping.relative_path:
            raise CodeIntelligenceContractError("symbol location must belong to mapped file")
        if entity.locator != location.to_usi_locator():
            raise CodeIntelligenceContractError("entity locator does not match code location")
        natural_key = symbol_natural_key(symbol_kind, qualified_name, signature_fingerprint)
        if entity.natural_key != natural_key:
            raise CodeIntelligenceContractError("entity natural_key does not match symbol identity")
        if entity.extractor_profile_ref != evidence.extractor_profile_ref:
            raise CodeIntelligenceContractError("entity extractor profile does not match evidence")
        return cls(
            file_mapping.engine_project_ref,
            file_mapping.engine_file_ref,
            engine_symbol_ref,
            file_mapping.fallback_key,
            entity.source_id,
            entity.source_version_id,
            entity.entity_id,
            symbol_kind,
            qualified_name,
            signature_fingerprint,
            location,
            evidence,
        )

    @property
    def effective_symbol_ref(self) -> str:
        return self.engine_symbol_ref or self.fallback_key

    def to_dict(self) -> dict[str, Any]:
        return _record_dict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CodeSymbolMapping":
        data = _strict_fields(cls, value)
        data["location"] = CodeLocation.from_dict(data["location"])
        data["evidence"] = ExtractionEvidence.from_dict(data["evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CodeEdgeMapping:
    engine_project_ref: str
    engine_edge_ref: str
    relation_id: str
    source_entity_id: str
    target_entity_id: str
    relation_kind: RelationKind
    evidence: ExtractionEvidence
    fallback_key: str = ""
    SCHEMA: ClassVar[str] = f"{CODE_INTELLIGENCE_CONTRACT_SCHEMA}.edge"

    def __post_init__(self) -> None:
        project_ref = _opaque(self.engine_project_ref, "engine_project_ref")
        edge_ref = _optional_opaque(self.engine_edge_ref, "engine_edge_ref")
        relation_id = _usi_id(self.relation_id, RecordKind.RELATION, "relation_id")
        source_entity_id = _usi_id(self.source_entity_id, RecordKind.ENTITY, "source_entity_id")
        target_entity_id = _usi_id(self.target_entity_id, RecordKind.ENTITY, "target_entity_id")
        relation_kind = _enum(self.relation_kind, RelationKind, "relation_kind")
        if not isinstance(self.evidence, ExtractionEvidence):
            raise CodeIntelligenceContractError("evidence must be ExtractionEvidence")
        try:
            expected_relation_id = make_relation_id(
                RecordRef(RecordKind.ENTITY, source_entity_id),
                RecordRef(RecordKind.ENTITY, target_entity_id),
                relation_kind,
                self.evidence.extractor_profile_ref,
            )
        except UnifiedSourceIndexContractError as exc:
            raise CodeIntelligenceContractError("canonical USI edge identity is invalid") from exc
        if relation_id != expected_relation_id:
            raise CodeIntelligenceContractError("relation_id does not match canonical edge identity")
        expected = _fallback(
            "edge",
            {
                "relation_id": relation_id,
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "relation_kind": relation_kind.value,
            },
        )
        _matching_fallback(self.fallback_key, expected, "edge")
        object.__setattr__(self, "engine_project_ref", project_ref)
        object.__setattr__(self, "engine_edge_ref", edge_ref)
        object.__setattr__(self, "relation_id", relation_id)
        object.__setattr__(self, "source_entity_id", source_entity_id)
        object.__setattr__(self, "target_entity_id", target_entity_id)
        object.__setattr__(self, "relation_kind", relation_kind)
        object.__setattr__(self, "fallback_key", expected)

    @classmethod
    def create(
        cls,
        relation: RelationRecord,
        *,
        engine_project_ref: str,
        engine_edge_ref: str = "",
        evidence: ExtractionEvidence,
    ) -> "CodeEdgeMapping":
        if not isinstance(relation, RelationRecord):
            raise CodeIntelligenceContractError("relation must be a USI RelationRecord")
        if (
            relation.source_ref.record_kind is not RecordKind.ENTITY
            or relation.target_ref.record_kind is not RecordKind.ENTITY
        ):
            raise CodeIntelligenceContractError("code edges must connect USI entities")
        if relation.method_ref != evidence.extractor_profile_ref:
            raise CodeIntelligenceContractError("relation method does not match evidence extractor")
        return cls(
            engine_project_ref,
            engine_edge_ref,
            relation.relation_id,
            relation.source_ref.record_id,
            relation.target_ref.record_id,
            relation.relation_kind,
            evidence,
        )

    @property
    def effective_edge_ref(self) -> str:
        return self.engine_edge_ref or self.fallback_key

    def to_dict(self) -> dict[str, Any]:
        return _record_dict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CodeEdgeMapping":
        data = _strict_fields(cls, value)
        data["evidence"] = ExtractionEvidence.from_dict(data["evidence"])
        return cls(**data)


MappingRecord = CodeFileMapping | CodeSymbolMapping | CodeEdgeMapping


def mapping_from_dict(value: Mapping[str, Any]) -> MappingRecord:
    if not isinstance(value, Mapping):
        raise CodeIntelligenceContractError("mapping must be an object")
    schema = value.get("schema")
    by_schema = {
        CodeFileMapping.SCHEMA: CodeFileMapping,
        CodeSymbolMapping.SCHEMA: CodeSymbolMapping,
        CodeEdgeMapping.SCHEMA: CodeEdgeMapping,
    }
    constructor = by_schema.get(schema)
    if constructor is None:
        raise CodeIntelligenceContractError("unsupported mapping schema")
    return constructor.from_dict(value)


def mapping_from_json(value: str | bytes) -> MappingRecord:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise CodeIntelligenceContractError(f"duplicate JSON field: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(value, object_pairs_hook=no_duplicates)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CodeIntelligenceContractError("invalid mapping JSON") from exc
    if not isinstance(payload, Mapping):
        raise CodeIntelligenceContractError("mapping JSON must contain an object")
    return mapping_from_dict(payload)


def validate_mapping_batch(
    files: Iterable[CodeFileMapping],
    symbols: Iterable[CodeSymbolMapping],
    edges: Iterable[CodeEdgeMapping],
) -> tuple[tuple[CodeFileMapping, ...], tuple[CodeSymbolMapping, ...], tuple[CodeEdgeMapping, ...]]:
    """Validate bounded mappings and reject every ambiguous identity reuse."""

    file_items = _typed_items(files, CodeFileMapping, "files")
    symbol_items = _typed_items(symbols, CodeSymbolMapping, "symbols")
    edge_items = _typed_items(edges, CodeEdgeMapping, "edges")

    _unique(file_items, lambda item: item.fallback_key, "file fallback_key")
    _unique(file_items, lambda item: (item.repo_id, item.source_version_id, item.relative_path), "file locator")
    _consistent(file_items, lambda item: (item.engine_project_ref, item.engine_file_ref), lambda item: item.fallback_key, "engine file ref", ignore_empty_tail=True)
    _consistent(file_items, lambda item: (item.repo_id, item.relative_path), lambda item: item.source_id, "repo path")

    file_by_key = {item.fallback_key: item for item in file_items}
    _unique(symbol_items, lambda item: item.fallback_key, "symbol fallback_key")
    _unique(symbol_items, lambda item: item.entity_id, "symbol entity_id")
    _consistent(symbol_items, lambda item: (item.engine_project_ref, item.engine_symbol_ref), lambda item: item.entity_id, "engine symbol ref", ignore_empty_tail=True)
    for symbol in symbol_items:
        file_mapping = file_by_key.get(symbol.file_fallback_key)
        if file_mapping is None:
            raise CodeIntelligenceContractError("symbol references an unknown file mapping")
        if (
            symbol.source_id != file_mapping.source_id
            or symbol.source_version_id != file_mapping.source_version_id
            or symbol.location.relative_path != file_mapping.relative_path
        ):
            raise CodeIntelligenceContractError("symbol ancestry does not match file mapping")

    symbol_ids = {item.entity_id for item in symbol_items}
    _unique(edge_items, lambda item: item.fallback_key, "edge fallback_key")
    _unique(edge_items, lambda item: item.relation_id, "edge relation_id")
    _consistent(edge_items, lambda item: (item.engine_project_ref, item.engine_edge_ref), lambda item: item.relation_id, "engine edge ref", ignore_empty_tail=True)
    for edge in edge_items:
        if edge.source_entity_id not in symbol_ids or edge.target_entity_id not in symbol_ids:
            raise CodeIntelligenceContractError("edge endpoint is absent from symbol mappings")

    return (
        tuple(sorted(file_items, key=lambda item: item.fallback_key)),
        tuple(sorted(symbol_items, key=lambda item: item.fallback_key)),
        tuple(sorted(edge_items, key=lambda item: item.fallback_key)),
    )


def _record_dict(instance: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": instance.SCHEMA}
    for item in fields(instance):
        value = getattr(instance, item.name)
        if isinstance(value, StrEnum):
            result[item.name] = value.value
        elif hasattr(value, "to_dict"):
            result[item.name] = value.to_dict()
        else:
            result[item.name] = value
    return result


def _strict_fields(cls: type[Any], value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CodeIntelligenceContractError("record must be an object")
    data = dict(value)
    if data.pop("schema", None) != cls.SCHEMA:
        raise CodeIntelligenceContractError(f"expected schema {cls.SCHEMA}")
    names = {item.name for item in fields(cls)}
    unknown = set(data) - names
    missing = names - set(data)
    if unknown:
        raise CodeIntelligenceContractError(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise CodeIntelligenceContractError(f"missing fields: {', '.join(sorted(missing))}")
    return data


def _enum(value: E | str, enum_type: type[E], field_name: str) -> E:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise CodeIntelligenceContractError(f"{field_name} must be {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise CodeIntelligenceContractError(f"invalid {field_name}") from exc


def _opaque(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_RE.fullmatch(value):
        raise CodeIntelligenceContractError(f"{field_name} must be an opaque bounded reference")
    return value


def _optional_opaque(value: Any, field_name: str) -> str:
    if value == "":
        return ""
    return _opaque(value, field_name)


def _name(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeIntelligenceContractError(f"{field_name} must be text")
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized or len(normalized) > 256 or _CONTROL_RE.search(normalized):
        raise CodeIntelligenceContractError(f"{field_name} must be non-empty and bounded")
    if "/" in normalized or "\\" in normalized or _DRIVE_RE.match(normalized):
        raise CodeIntelligenceContractError(f"{field_name} must not contain a host path")
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise CodeIntelligenceContractError(f"{field_name} must be valid UTF-8") from exc
    return normalized


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeIntelligenceContractError(f"{field_name} must be SHA-256")
    match = _SHA256_RE.fullmatch(value.strip())
    if not match:
        raise CodeIntelligenceContractError(f"{field_name} must be SHA-256")
    return f"sha256:{match.group(1).lower()}"


def _integer(value: Any, field_name: str, *, minimum: int = 0, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CodeIntelligenceContractError(f"{field_name} is outside its bounded range")
    return value


def _usi_id(value: Any, kind: RecordKind, field_name: str) -> str:
    try:
        return RecordRef(kind, value).record_id
    except Exception as exc:
        raise CodeIntelligenceContractError(f"{field_name} is not a {kind.value} identifier") from exc


def _fallback(kind: str, key: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "schema": CODE_INTELLIGENCE_CONTRACT_SCHEMA,
                "kind": kind,
                "key": key,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"cbm_{kind}_{digest}"


def _fallback_value(value: Any, field_name: str, kind: str) -> str:
    if not isinstance(value, str) or not _FALLBACK_RE.fullmatch(value) or not value.startswith(f"cbm_{kind}_"):
        raise CodeIntelligenceContractError(f"{field_name} is not a {kind} fallback key")
    return value


def _matching_fallback(supplied: Any, expected: str, kind: str) -> None:
    if supplied not in ("", expected):
        _fallback_value(supplied, "fallback_key", kind)
        raise CodeIntelligenceContractError("fallback_key does not match canonical identity")


def _typed_items(values: Iterable[Any], expected: type[Any], field_name: str) -> tuple[Any, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodeIntelligenceContractError(f"{field_name} must be iterable") from exc
    if len(items) > MAX_BATCH_ITEMS or not all(isinstance(item, expected) for item in items):
        raise CodeIntelligenceContractError(f"{field_name} must be typed and bounded")
    return items


def _unique(items: Iterable[Any], key, label: str) -> None:
    seen: set[Any] = set()
    for item in items:
        value = key(item)
        if value in seen:
            raise CodeIntelligenceContractError(f"duplicate {label}")
        seen.add(value)


def _consistent(items: Iterable[Any], key, identity, label: str, *, ignore_empty_tail: bool = False) -> None:
    seen: dict[Any, Any] = {}
    for item in items:
        value = key(item)
        if ignore_empty_tail and isinstance(value, tuple) and value[-1] == "":
            continue
        canonical = identity(item)
        previous = seen.setdefault(value, canonical)
        if previous != canonical:
            raise CodeIntelligenceContractError(f"ambiguous {label}")
