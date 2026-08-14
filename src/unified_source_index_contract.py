"""Backend-neutral records and identities for the Unified Source Index.

This module is deliberately limited to stdlib value objects.  It performs no
storage, engine, network, filesystem, or runtime access.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum, StrEnum
import hashlib
import json
import math
import re
from typing import Any, ClassVar, Iterable, Mapping, Sequence, TypeVar


USI_CONTRACT_SCHEMA = "odysseus.unified_source_index.contract.v1"
MAX_SCOPE_SOURCES = 256
MAX_SCOPE_VERSIONS = 2048
MAX_EVIDENCE_REFS = 4096
MAX_TEXT_CHARS = 262_144

_MAX_POSITION = 1_000_000_000
_OWNER_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._/@+-]{0,159}$")
_TOKEN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:@/+~-]{0,255}$")
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_ID_RE = re.compile(r"^usi_(source|version|chunk|entity|relation|lineage|projection|run|job|scope|policy)_[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_FORGE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_FORGE_VERSION_RE = re.compile(r"^pv_[0-9a-f]{32}$")


class UnifiedSourceIndexContractError(ValueError):
    """Raised when a USI value is incomplete, unbounded, or unsafe."""


class Classification(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    SECRET = "secret"
    UNKNOWN = "unknown"


class ContentPolicy(StrEnum):
    INLINE_LOCAL = "inline_local"
    REFERENCE_ONLY = "reference_only"
    METADATA_ONLY = "metadata_only"


class SourceKind(StrEnum):
    TEXT = "text"
    DOCUMENT = "document"
    TABLE = "table"
    MESSAGE = "message"
    CODE = "code"
    MEMORY = "memory"
    PLANNING = "planning"
    INBOX = "inbox"
    OTHER = "other"


class LocatorKind(StrEnum):
    TEXT_RANGE = "text_range"
    PAGE_RANGE = "page_range"
    ROW_RANGE = "row_range"
    MESSAGE_RANGE = "message_range"
    CODE_RANGE = "code_range"


class RecordKind(StrEnum):
    SOURCE = "source"
    SOURCE_VERSION = "source_version"
    CHUNK = "chunk"
    ENTITY = "entity"
    RELATION = "relation"
    LINEAGE = "lineage"
    PROJECTION = "projection"
    DERIVED_RUN = "derived_run"
    JOB = "job"


class EntityKind(StrEnum):
    SYMBOL = "symbol"
    PERSON = "person"
    TASK = "task"
    DOCUMENT_SECTION = "document_section"
    MESSAGE = "message"
    TABLE_ROW = "table_row"
    CONCEPT = "concept"
    OTHER = "other"


class RelationKind(StrEnum):
    DEFINES = "defines"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    REFERENCES = "references"
    TESTS = "tests"
    MENTIONS = "mentions"
    BELONGS_TO = "belongs_to"
    SUPERSEDES = "supersedes"
    RELATED_TO = "related_to"


class LineageReason(StrEnum):
    EDITED = "edited"
    RENAMED = "renamed"
    MOVED = "moved"
    COPIED = "copied"
    SPLIT = "split"
    MERGED = "merged"
    DELETED = "deleted"
    RECONCILED = "reconciled"


class ProjectionKind(StrEnum):
    LEXICAL = "lexical"
    EMBEDDING = "embedding"
    CODE_GRAPH = "code_graph"
    DERIVED_GRAPH = "derived_graph"


class DerivedRunKind(StrEnum):
    CLUSTER = "cluster"
    SUMMARY = "summary"
    RAPTOR = "raptor"


class IndexJobKind(StrEnum):
    DISCOVERY = "discovery"
    EXTRACTION = "extraction"
    PROJECTION = "projection"
    DELETION = "deletion"
    REBUILD = "rebuild"
    RECONCILIATION = "reconciliation"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.PRIVATE: 1,
    Classification.SENSITIVE: 2,
    Classification.SECRET: 3,
    Classification.UNKNOWN: 4,
}
_CONTENT_POLICY_RANK = {
    ContentPolicy.INLINE_LOCAL: 0,
    ContentPolicy.REFERENCE_ONLY: 1,
    ContentPolicy.METADATA_ONLY: 2,
}
_ID_PREFIX = {
    RecordKind.SOURCE: "source",
    RecordKind.SOURCE_VERSION: "version",
    RecordKind.CHUNK: "chunk",
    RecordKind.ENTITY: "entity",
    RecordKind.RELATION: "relation",
    RecordKind.LINEAGE: "lineage",
    RecordKind.PROJECTION: "projection",
    RecordKind.DERIVED_RUN: "run",
    RecordKind.JOB: "job",
}


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnifiedSourceIndexContractError("canonical mappings require string keys")
            result[key] = _primitive(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_primitive(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise UnifiedSourceIndexContractError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used by USI IDs."""

    return json.dumps(
        _primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_mapping(value: str | bytes) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise UnifiedSourceIndexContractError(f"duplicate JSON field: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(value, object_pairs_hook=pairs, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UnifiedSourceIndexContractError("invalid canonical JSON") from exc
    if not isinstance(payload, Mapping):
        raise UnifiedSourceIndexContractError("canonical record JSON must contain an object")
    return payload


class _CanonicalRecord:
    SCHEMA: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            **{item.name: _primitive(getattr(self, item.name)) for item in fields(self)},
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, value: str | bytes):
        return cls.from_dict(_json_mapping(value))


def _payload(
    value: Mapping[str, Any],
    *,
    schema: str,
    allowed: set[str],
    required: set[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UnifiedSourceIndexContractError("record payload must be a mapping")
    data = dict(value)
    supplied_schema = data.pop("schema", None)
    if supplied_schema is not None and supplied_schema != schema:
        raise UnifiedSourceIndexContractError(f"expected schema {schema}")
    unknown = set(data) - allowed
    if unknown:
        raise UnifiedSourceIndexContractError(f"unknown fields: {', '.join(sorted(unknown))}")
    missing = required - set(data)
    if missing:
        raise UnifiedSourceIndexContractError(f"missing fields: {', '.join(sorted(missing))}")
    return data


E = TypeVar("E", bound=Enum)


def _enum(value: E | str, enum_type: type[E], field_name: str) -> E:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise UnifiedSourceIndexContractError(f"{field_name} must be a {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise UnifiedSourceIndexContractError(f"invalid {field_name}: {value!r}") from exc


def _text(value: Any, field_name: str, *, max_len: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise UnifiedSourceIndexContractError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise UnifiedSourceIndexContractError(f"{field_name} must not be empty")
    if len(normalized) > max_len:
        raise UnifiedSourceIndexContractError(f"{field_name} exceeds {max_len} characters")
    if _CONTROL_RE.search(normalized):
        raise UnifiedSourceIndexContractError(f"{field_name} contains control characters")
    return normalized


def _token(value: Any, field_name: str) -> str:
    token = _text(value, field_name, max_len=256)
    if not _TOKEN_RE.fullmatch(token):
        raise UnifiedSourceIndexContractError(f"{field_name} is not a bounded reference")
    return token


def _owner_scope(value: Any) -> str:
    scope = _text(value, "owner_scope", max_len=192)
    if not _OWNER_SCOPE_RE.fullmatch(scope) or "*" in scope or scope.lower().endswith(":all"):
        raise UnifiedSourceIndexContractError("owner_scope must be an explicit kind:identifier scope")
    return scope


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise UnifiedSourceIndexContractError(f"{field_name} must be a SHA-256 string")
    match = _SHA256_RE.fullmatch(value.strip())
    if not match:
        raise UnifiedSourceIndexContractError(f"{field_name} must be a SHA-256 string")
    return f"sha256:{match.group(1).lower()}"


def content_hash(content: str | bytes) -> str:
    if not isinstance(content, (str, bytes)):
        raise UnifiedSourceIndexContractError("content must be text or bytes")
    data = content.encode("utf-8") if isinstance(content, str) else content
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _forge_code_identity_ref(kind: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "schema": "odysseus.usi.forge_code_occurrence.v1",
                "kind": kind,
                "payload": payload,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"forge-code-{kind}:sha256:{digest}"


def _integer(value: Any, field_name: str, *, minimum: int = 0, maximum: int = _MAX_POSITION) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise UnifiedSourceIndexContractError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnifiedSourceIndexContractError("confidence must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise UnifiedSourceIndexContractError("confidence must be between 0 and 1")
    return result


def _timestamp(value: Any, field_name: str, *, required: bool = False) -> str:
    if value in (None, "") and not required:
        return ""
    text = _text(value, field_name, max_len=40)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise UnifiedSourceIndexContractError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UnifiedSourceIndexContractError(f"{field_name} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def _time_window(valid_from: str, valid_to: str) -> None:
    if not (valid_from and valid_to):
        return
    start = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
    end = datetime.fromisoformat(valid_to.replace("Z", "+00:00"))
    if end < start:
        raise UnifiedSourceIndexContractError("valid_to must not precede valid_from")


def _stable_id(kind: str, key: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_json({"schema": USI_CONTRACT_SCHEMA, "kind": kind, "key": key}).encode("utf-8")
    ).hexdigest()
    return f"usi_{kind}_{digest}"


def _set_stable_id(instance: Any, field_name: str, expected: str) -> None:
    supplied = getattr(instance, field_name)
    if supplied and supplied != expected:
        raise UnifiedSourceIndexContractError(f"{field_name} does not match canonical identity")
    object.__setattr__(instance, field_name, expected)


def _record_id(value: Any, kind: RecordKind, field_name: str = "record_id") -> str:
    result = _text(value, field_name, max_len=96)
    expected = _ID_PREFIX[kind]
    match = _ID_RE.fullmatch(result)
    if not match or match.group(1) != expected:
        raise UnifiedSourceIndexContractError(f"{field_name} is not a {kind.value} identifier")
    return result


def _path(value: Any) -> str:
    path = _text(value, "path", max_len=1024)
    if "\\" in path or path.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", path):
        raise UnifiedSourceIndexContractError("code path must be relative and use forward slashes")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnifiedSourceIndexContractError("code path contains invalid segments")
    return path


@dataclass(frozen=True, slots=True)
class TextRangeLocator:
    start_char: int
    end_char: int
    kind: ClassVar[LocatorKind] = LocatorKind.TEXT_RANGE

    def __post_init__(self) -> None:
        start = _integer(self.start_char, "start_char")
        end = _integer(self.end_char, "end_char")
        if end <= start:
            raise UnifiedSourceIndexContractError("text range must be non-empty and half-open")
        object.__setattr__(self, "start_char", start)
        object.__setattr__(self, "end_char", end)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "start_char": self.start_char, "end_char": self.end_char}


@dataclass(frozen=True, slots=True)
class PageRangeLocator:
    start_page: int
    end_page: int
    kind: ClassVar[LocatorKind] = LocatorKind.PAGE_RANGE

    def __post_init__(self) -> None:
        start = _integer(self.start_page, "start_page", minimum=1)
        end = _integer(self.end_page, "end_page", minimum=1)
        if end < start:
            raise UnifiedSourceIndexContractError("end_page must not precede start_page")
        object.__setattr__(self, "start_page", start)
        object.__setattr__(self, "end_page", end)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "start_page": self.start_page, "end_page": self.end_page}


@dataclass(frozen=True, slots=True)
class RowRangeLocator:
    table_ref: str
    start_row: int
    end_row: int
    kind: ClassVar[LocatorKind] = LocatorKind.ROW_RANGE

    def __post_init__(self) -> None:
        table_ref = _token(self.table_ref, "table_ref")
        start = _integer(self.start_row, "start_row")
        end = _integer(self.end_row, "end_row")
        if end <= start:
            raise UnifiedSourceIndexContractError("row range must be non-empty and half-open")
        object.__setattr__(self, "table_ref", table_ref)
        object.__setattr__(self, "start_row", start)
        object.__setattr__(self, "end_row", end)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "table_ref": self.table_ref, "start_row": self.start_row, "end_row": self.end_row}


@dataclass(frozen=True, slots=True)
class MessageRangeLocator:
    thread_ref: str
    start_message: int
    end_message: int
    kind: ClassVar[LocatorKind] = LocatorKind.MESSAGE_RANGE

    def __post_init__(self) -> None:
        thread_ref = _token(self.thread_ref, "thread_ref")
        start = _integer(self.start_message, "start_message")
        end = _integer(self.end_message, "end_message")
        if end <= start:
            raise UnifiedSourceIndexContractError("message range must be non-empty and half-open")
        object.__setattr__(self, "thread_ref", thread_ref)
        object.__setattr__(self, "start_message", start)
        object.__setattr__(self, "end_message", end)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "thread_ref": self.thread_ref, "start_message": self.start_message, "end_message": self.end_message}


@dataclass(frozen=True, slots=True)
class CodeRangeLocator:
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    kind: ClassVar[LocatorKind] = LocatorKind.CODE_RANGE

    def __post_init__(self) -> None:
        if type(self.path) is not str or any(
            type(value) is not int
            for value in (
                self.start_line,
                self.start_column,
                self.end_line,
                self.end_column,
            )
        ):
            raise UnifiedSourceIndexContractError(
                "code range locator must use exact string and integer scalar types"
            )
        path = _path(self.path)
        start_line = _integer(self.start_line, "start_line", minimum=1, maximum=10_000_000)
        end_line = _integer(self.end_line, "end_line", minimum=1, maximum=10_000_000)
        start_column = _integer(self.start_column, "start_column", maximum=1_000_000)
        end_column = _integer(self.end_column, "end_column", maximum=1_000_000)
        if (end_line, end_column) <= (start_line, start_column):
            raise UnifiedSourceIndexContractError("code range must be non-empty and ordered")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "start_line", start_line)
        object.__setattr__(self, "start_column", start_column)
        object.__setattr__(self, "end_line", end_line)
        object.__setattr__(self, "end_column", end_column)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }


@dataclass(frozen=True, slots=True)
class ForgeCodeOccurrenceEvidence(_CanonicalRecord):
    """Inspectable immutable Forge tuple carried with a persisted code occurrence."""

    owner_scope: str
    repo_id: str
    version_id: str
    commit_sha: str
    snapshot_digest: str
    authority_binding: tuple[str, str, str, str]
    path: str
    file_content_sha256: str
    locator: CodeRangeLocator
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.forge_code_occurrence_evidence"

    def __post_init__(self) -> None:
        if any(
            type(value) is not str
            for value in (
                self.owner_scope,
                self.repo_id,
                self.version_id,
                self.commit_sha,
                self.snapshot_digest,
                self.path,
                self.file_content_sha256,
            )
        ):
            raise UnifiedSourceIndexContractError("Forge occurrence evidence requires exact string scalars")
        owner = _owner_scope(self.owner_scope)
        repo_id = _token(self.repo_id, "repo_id")
        if not _FORGE_VERSION_RE.fullmatch(self.version_id):
            raise UnifiedSourceIndexContractError("version_id is not an immutable Forge version")
        if not _FORGE_COMMIT_RE.fullmatch(self.commit_sha):
            raise UnifiedSourceIndexContractError("commit_sha is not an immutable Forge commit")
        snapshot_digest = _sha256(self.snapshot_digest, "snapshot_digest")
        file_digest = _sha256(self.file_content_sha256, "file_content_sha256")
        if type(self.authority_binding) is not tuple or len(self.authority_binding) != 4 or any(
            type(value) is not str for value in self.authority_binding
        ):
            raise UnifiedSourceIndexContractError(
                "authority_binding must be four exact bounded string primitives"
            )
        authority = tuple(
            _token(value, f"authority_binding[{index}]")
            for index, value in enumerate(self.authority_binding)
        )
        path = _path(self.path)
        if type(self.locator) is not CodeRangeLocator or self.locator.path != path:
            raise UnifiedSourceIndexContractError(
                "Forge occurrence locator must use the exact canonical evidence path"
            )
        locator = CodeRangeLocator(
            path,
            self.locator.start_line,
            self.locator.start_column,
            self.locator.end_line,
            self.locator.end_column,
        )
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "snapshot_digest", snapshot_digest)
        object.__setattr__(self, "authority_binding", authority)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "file_content_sha256", file_digest)
        object.__setattr__(self, "locator", locator)

    def source_ref(self) -> str:
        return _forge_code_identity_ref(
            "source",
            {"repo_id": self.repo_id, "path": self.path},
        )

    def revision_ref(self) -> str:
        return _forge_code_identity_ref(
            "version",
            {
                "repo_id": self.repo_id,
                "version_id": self.version_id,
                "commit_sha": self.commit_sha,
                "snapshot_digest": self.snapshot_digest,
                "authority_binding": self.authority_binding,
                "path": self.path,
                "file_content_sha256": self.file_content_sha256,
            },
        )

    def occurrence_ref(self, extractor_profile_ref: str) -> str:
        return _forge_code_identity_ref(
            "occurrence",
            {
                "owner_scope": self.owner_scope,
                "repo_id": self.repo_id,
                "version_id": self.version_id,
                "commit_sha": self.commit_sha,
                "snapshot_digest": self.snapshot_digest,
                "authority_binding": self.authority_binding,
                "path": self.path,
                "file_content_sha256": self.file_content_sha256,
                "locator": normalized_locator(self.locator),
                "extractor_profile_ref": _token(
                    extractor_profile_ref,
                    "extractor_profile_ref",
                ),
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ForgeCodeOccurrenceEvidence":
        names = {item.name for item in fields(cls)}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=names)
        if not isinstance(data["authority_binding"], list):
            raise UnifiedSourceIndexContractError("authority_binding must be a canonical JSON array")
        data["authority_binding"] = tuple(data["authority_binding"])
        data["locator"] = locator_from_dict(data["locator"])
        return cls(**data)


Locator = TextRangeLocator | PageRangeLocator | RowRangeLocator | MessageRangeLocator | CodeRangeLocator
TextLocator = TextRangeLocator
PageLocator = PageRangeLocator
RowLocator = RowRangeLocator
MessageLocator = MessageRangeLocator
CodeLocator = CodeRangeLocator


def locator_from_dict(value: Mapping[str, Any]) -> Locator:
    if not isinstance(value, Mapping):
        raise UnifiedSourceIndexContractError("locator must be a mapping")
    try:
        kind = _enum(value.get("kind"), LocatorKind, "locator kind")
    except AttributeError as exc:
        raise UnifiedSourceIndexContractError("locator kind is required") from exc
    constructors: dict[LocatorKind, tuple[type[Any], tuple[str, ...]]] = {
        LocatorKind.TEXT_RANGE: (TextRangeLocator, ("start_char", "end_char")),
        LocatorKind.PAGE_RANGE: (PageRangeLocator, ("start_page", "end_page")),
        LocatorKind.ROW_RANGE: (RowRangeLocator, ("table_ref", "start_row", "end_row")),
        LocatorKind.MESSAGE_RANGE: (MessageRangeLocator, ("thread_ref", "start_message", "end_message")),
        LocatorKind.CODE_RANGE: (CodeRangeLocator, ("path", "start_line", "start_column", "end_line", "end_column")),
    }
    constructor, names = constructors[kind]
    allowed = {"kind", *names}
    if set(value) != allowed:
        raise UnifiedSourceIndexContractError("locator fields do not match locator kind")
    return constructor(**{name: value[name] for name in names})


def normalized_locator(locator: Locator) -> str:
    if not isinstance(locator, (TextRangeLocator, PageRangeLocator, RowRangeLocator, MessageRangeLocator, CodeRangeLocator)):
        raise UnifiedSourceIndexContractError("locator must be a typed USI locator")
    return canonical_json(locator.to_dict())


def make_source_id(owner_scope: str, source_kind: SourceKind | str, canonical_ref: str) -> str:
    return _stable_id(
        "source",
        {
            "owner_scope": _owner_scope(owner_scope),
            "source_kind": _enum(source_kind, SourceKind, "source_kind").value,
            "canonical_ref": _text(canonical_ref, "canonical_ref", max_len=2048),
        },
    )


def make_source_version_id(source_id: str, revision_ref: str, source_content_hash: str) -> str:
    return _stable_id(
        "version",
        {
            "source_id": _record_id(source_id, RecordKind.SOURCE, "source_id"),
            "revision_ref": _text(revision_ref, "revision_ref", max_len=512),
            "content_hash": _sha256(source_content_hash, "content_hash"),
        },
    )


def make_chunk_id(source_version_id: str, locator: Locator, extractor_profile_ref: str) -> str:
    return _stable_id(
        "chunk",
        {
            "source_version_id": _record_id(source_version_id, RecordKind.SOURCE_VERSION, "source_version_id"),
            "locator_kind": locator.kind.value,
            "normalized_locator": normalized_locator(locator),
            "extractor_profile_ref": _token(extractor_profile_ref, "extractor_profile_ref"),
        },
    )


def make_entity_id(
    source_version_id: str,
    entity_kind: EntityKind | str,
    natural_key: str,
    locator: Locator,
    extractor_profile_ref: str,
) -> str:
    return _stable_id(
        "entity",
        {
            "source_version_id": _record_id(source_version_id, RecordKind.SOURCE_VERSION, "source_version_id"),
            "entity_kind": _enum(entity_kind, EntityKind, "entity_kind").value,
            "natural_key": _text(natural_key, "natural_key", max_len=512),
            "normalized_locator": normalized_locator(locator),
            "extractor_profile_ref": _token(extractor_profile_ref, "extractor_profile_ref"),
        },
    )


def make_relation_id(source_ref: "RecordRef", target_ref: "RecordRef", relation_kind: RelationKind | str, method_ref: str) -> str:
    return _stable_id(
        "relation",
        {
            "source_ref": source_ref.to_dict(),
            "target_ref": target_ref.to_dict(),
            "relation_kind": _enum(relation_kind, RelationKind, "relation_kind").value,
            "method_ref": _token(method_ref, "method_ref"),
        },
    )


def make_lineage_id(previous_id: str, current_id: str, reason: LineageReason | str, method_ref: str) -> str:
    return _stable_id(
        "lineage",
        {
            "previous_chunk_id": _record_id(previous_id, RecordKind.CHUNK, "previous_chunk_id"),
            "current_chunk_id": _record_id(current_id, RecordKind.CHUNK, "current_chunk_id"),
            "reason": _enum(reason, LineageReason, "reason").value,
            "method_ref": _token(method_ref, "method_ref"),
        },
    )


def make_projection_id(
    projection_kind: ProjectionKind | str,
    projection_profile_ref: str,
    input_snapshot_ref: str,
    config_hash: str,
    input_record_ids: Iterable[str],
) -> str:
    ids = tuple(sorted({_text(item, "input_record_id", max_len=96) for item in input_record_ids}))
    if not ids or len(ids) > MAX_EVIDENCE_REFS:
        raise UnifiedSourceIndexContractError("projection input_record_ids must be non-empty and bounded")
    return _stable_id(
        "projection",
        {
            "projection_kind": _enum(projection_kind, ProjectionKind, "projection_kind").value,
            "projection_profile_ref": _token(projection_profile_ref, "projection_profile_ref"),
            "input_snapshot_ref": _token(input_snapshot_ref, "input_snapshot_ref"),
            "config_hash": _sha256(config_hash, "config_hash"),
            "input_record_ids": ids,
        },
    )


def make_derived_run_id(
    derived_kind: DerivedRunKind | str,
    source_scope_id: str,
    input_snapshot_ref: str,
    algorithm_ref: str,
    algorithm_version: str,
    config_hash: str,
) -> str:
    return _stable_id(
        "run",
        {
            "derived_kind": _enum(derived_kind, DerivedRunKind, "derived_kind").value,
            "source_scope_id": _text(source_scope_id, "source_scope_id", max_len=96),
            "input_snapshot_ref": _token(input_snapshot_ref, "input_snapshot_ref"),
            "algorithm_ref": _token(algorithm_ref, "algorithm_ref"),
            "algorithm_version": _token(algorithm_version, "algorithm_version"),
            "config_hash": _sha256(config_hash, "config_hash"),
        },
    )


def make_job_id(job_kind: IndexJobKind | str, source_scope_id: str, request_ref: str, profile_ref: str) -> str:
    return _stable_id(
        "job",
        {
            "job_kind": _enum(job_kind, IndexJobKind, "job_kind").value,
            "source_scope_id": _text(source_scope_id, "source_scope_id", max_len=96),
            "request_ref": _token(request_ref, "request_ref"),
            "profile_ref": _token(profile_ref, "profile_ref"),
        },
    )


@dataclass(frozen=True, slots=True)
class PolicyEvidence(_CanonicalRecord):
    parent_kind: RecordKind
    parent_id: str
    source_id: str
    source_version_id: str
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    evidence_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.policy_evidence"

    def __post_init__(self) -> None:
        kind = _enum(self.parent_kind, RecordKind, "parent_kind")
        if kind not in {RecordKind.SOURCE, RecordKind.SOURCE_VERSION, RecordKind.CHUNK, RecordKind.ENTITY}:
            raise UnifiedSourceIndexContractError("policy evidence must name a source occurrence")
        parent_id = _record_id(self.parent_id, kind, "parent_id")
        source_id = _record_id(self.source_id, RecordKind.SOURCE, "source_id")
        version_id = self.source_version_id
        if kind is RecordKind.SOURCE:
            if parent_id != source_id or version_id:
                raise UnifiedSourceIndexContractError("source policy evidence has inconsistent ancestry")
        else:
            version_id = _record_id(version_id, RecordKind.SOURCE_VERSION, "source_version_id")
            if kind is RecordKind.SOURCE_VERSION and parent_id != version_id:
                raise UnifiedSourceIndexContractError("version policy evidence has inconsistent ancestry")
        owner = _owner_scope(self.owner_scope)
        classification = _enum(self.classification, Classification, "classification")
        policy = _enum(self.content_policy, ContentPolicy, "content_policy")
        expected = _stable_id(
            "policy",
            {
                "parent_kind": kind.value,
                "parent_id": parent_id,
                "source_id": source_id,
                "source_version_id": version_id,
                "owner_scope": owner,
                "classification": classification.value,
                "content_policy": policy.value,
            },
        )
        object.__setattr__(self, "parent_kind", kind)
        object.__setattr__(self, "parent_id", parent_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version_id", version_id)
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        _set_stable_id(self, "evidence_id", expected)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PolicyEvidence":
        names = {item.name for item in fields(cls)}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=names - {"evidence_id"})
        return cls(**data)


@dataclass(frozen=True, slots=True)
class RecordRef(_CanonicalRecord):
    record_kind: RecordKind
    record_id: str
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.record_ref"

    def __post_init__(self) -> None:
        kind = _enum(self.record_kind, RecordKind, "record_kind")
        object.__setattr__(self, "record_kind", kind)
        object.__setattr__(self, "record_id", _record_id(self.record_id, kind))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RecordRef":
        data = _payload(value, schema=cls.SCHEMA, allowed={"record_kind", "record_id"}, required={"record_kind", "record_id"})
        return cls(**data)


@dataclass(frozen=True, slots=True)
class EvidenceRef(_CanonicalRecord):
    record_kind: RecordKind
    record_id: str
    source_id: str
    source_version_id: str
    locator: Locator | None
    content_hash: str
    policy_evidence: PolicyEvidence
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.evidence_ref"

    def __post_init__(self) -> None:
        kind = _enum(self.record_kind, RecordKind, "record_kind")
        if kind not in {RecordKind.SOURCE_VERSION, RecordKind.CHUNK, RecordKind.ENTITY}:
            raise UnifiedSourceIndexContractError("evidence refs must name a version, chunk, or entity")
        record_id = _record_id(self.record_id, kind)
        source_id = _record_id(self.source_id, RecordKind.SOURCE, "source_id")
        version_id = _record_id(self.source_version_id, RecordKind.SOURCE_VERSION, "source_version_id")
        locator = self.locator
        if kind is RecordKind.SOURCE_VERSION:
            if locator is not None or record_id != version_id:
                raise UnifiedSourceIndexContractError("version evidence must not contain a locator")
        elif not isinstance(locator, (TextRangeLocator, PageRangeLocator, RowRangeLocator, MessageRangeLocator, CodeRangeLocator)):
            raise UnifiedSourceIndexContractError("chunk/entity evidence requires a typed locator")
        policy = self.policy_evidence
        if not isinstance(policy, PolicyEvidence):
            raise UnifiedSourceIndexContractError("policy_evidence is required")
        if (
            policy.parent_kind is not kind
            or policy.parent_id != record_id
            or policy.source_id != source_id
            or policy.source_version_id != version_id
        ):
            raise UnifiedSourceIndexContractError("policy evidence does not describe the evidence record")
        object.__setattr__(self, "record_kind", kind)
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version_id", version_id)
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, "content_hash"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceRef":
        names = {item.name for item in fields(cls)}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=names)
        data["locator"] = locator_from_dict(data["locator"]) if data["locator"] is not None else None
        data["policy_evidence"] = PolicyEvidence.from_dict(data["policy_evidence"])
        return cls(**data)


def _policy_tuple(
    evidence: Sequence[PolicyEvidence],
    *,
    owner_scope: str | None = None,
    classification: Classification | str | None = None,
    content_policy: ContentPolicy | str | None = None,
) -> tuple[str, Classification, ContentPolicy]:
    if not evidence:
        raise UnifiedSourceIndexContractError("policy evidence is required")
    if len(evidence) > MAX_EVIDENCE_REFS or not all(isinstance(item, PolicyEvidence) for item in evidence):
        raise UnifiedSourceIndexContractError("policy evidence must be typed and bounded")
    owners = {item.owner_scope for item in evidence}
    if len(owners) != 1:
        raise UnifiedSourceIndexContractError("cross-owner policy evidence fails closed")
    inherited_owner = next(iter(owners))
    inherited_classification = max((item.classification for item in evidence), key=_CLASSIFICATION_RANK.__getitem__)
    inherited_policy = max((item.content_policy for item in evidence), key=_CONTENT_POLICY_RANK.__getitem__)
    child_owner = inherited_owner if owner_scope is None else _owner_scope(owner_scope)
    child_classification = inherited_classification if classification is None else _enum(classification, Classification, "classification")
    child_policy = inherited_policy if content_policy is None else _enum(content_policy, ContentPolicy, "content_policy")
    if child_owner != inherited_owner:
        raise UnifiedSourceIndexContractError("downstream owner_scope must match its parent")
    if _CLASSIFICATION_RANK[child_classification] < _CLASSIFICATION_RANK[inherited_classification]:
        raise UnifiedSourceIndexContractError("downstream classification cannot weaken parent evidence")
    if _CONTENT_POLICY_RANK[child_policy] < _CONTENT_POLICY_RANK[inherited_policy]:
        raise UnifiedSourceIndexContractError("downstream content_policy cannot weaken parent evidence")
    return child_owner, child_classification, child_policy


def _sorted_policy_evidence(value: Iterable[PolicyEvidence]) -> tuple[PolicyEvidence, ...]:
    items = tuple(value)
    if not items or len(items) > MAX_EVIDENCE_REFS or not all(isinstance(item, PolicyEvidence) for item in items):
        raise UnifiedSourceIndexContractError("policy evidence must be non-empty, typed, and bounded")
    by_id = {item.evidence_id: item for item in items}
    return tuple(by_id[key] for key in sorted(by_id))


def _sorted_evidence(value: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    items = tuple(value)
    if not items or len(items) > MAX_EVIDENCE_REFS or not all(isinstance(item, EvidenceRef) for item in items):
        raise UnifiedSourceIndexContractError("evidence_refs must be non-empty, typed, and bounded")
    by_key = {(item.record_kind.value, item.record_id): item for item in items}
    return tuple(by_key[key] for key in sorted(by_key))


@dataclass(frozen=True, slots=True)
class SourceRecord(_CanonicalRecord):
    owner_scope: str
    source_kind: SourceKind
    canonical_ref: str
    classification: Classification
    content_policy: ContentPolicy
    provider_ref: str
    source_created_at: str = ""
    first_seen_at: str = ""
    source_modified_at: str = ""
    valid_from: str = ""
    valid_to: str = ""
    source_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.source"

    def __post_init__(self) -> None:
        owner = _owner_scope(self.owner_scope)
        kind = _enum(self.source_kind, SourceKind, "source_kind")
        canonical_ref = _text(self.canonical_ref, "canonical_ref", max_len=2048)
        classification = _enum(self.classification, Classification, "classification")
        policy = _enum(self.content_policy, ContentPolicy, "content_policy")
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "source_kind", kind)
        object.__setattr__(self, "canonical_ref", canonical_ref)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "provider_ref", _token(self.provider_ref, "provider_ref"))
        for name in ("source_created_at", "first_seen_at", "source_modified_at", "valid_from", "valid_to"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        _time_window(self.valid_from, self.valid_to)
        _set_stable_id(self, "source_id", make_source_id(owner, kind, canonical_ref))

    def policy_evidence(self) -> PolicyEvidence:
        return PolicyEvidence(RecordKind.SOURCE, self.source_id, self.source_id, "", self.owner_scope, self.classification, self.content_policy)

    def ref(self) -> RecordRef:
        return RecordRef(RecordKind.SOURCE, self.source_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceRecord":
        names = {item.name for item in fields(cls)}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required={"owner_scope", "source_kind", "canonical_ref", "classification", "content_policy", "provider_ref"})
        return cls(**data)


@dataclass(frozen=True, slots=True)
class SourceVersionRecord(_CanonicalRecord):
    source_id: str
    revision_ref: str
    content_hash: str
    provider_ref: str
    version_observed_at: str
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    policy_evidence: PolicyEvidence
    source_created_at: str = ""
    first_seen_at: str = ""
    source_modified_at: str = ""
    indexed_at: str = ""
    valid_from: str = ""
    valid_to: str = ""
    source_version_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.source_version"

    def __post_init__(self) -> None:
        source_id = _record_id(self.source_id, RecordKind.SOURCE, "source_id")
        evidence = self.policy_evidence
        if not isinstance(evidence, PolicyEvidence) or evidence.parent_kind is not RecordKind.SOURCE or evidence.parent_id != source_id:
            raise UnifiedSourceIndexContractError("source version requires matching source policy evidence")
        owner, classification, policy = _policy_tuple((evidence,), owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        revision = _text(self.revision_ref, "revision_ref", max_len=512)
        source_hash = _sha256(self.content_hash, "content_hash")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "revision_ref", revision)
        object.__setattr__(self, "content_hash", source_hash)
        object.__setattr__(self, "provider_ref", _token(self.provider_ref, "provider_ref"))
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "version_observed_at", _timestamp(self.version_observed_at, "version_observed_at", required=True))
        for name in ("source_created_at", "first_seen_at", "source_modified_at", "indexed_at", "valid_from", "valid_to"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        _time_window(self.valid_from, self.valid_to)
        _set_stable_id(self, "source_version_id", make_source_version_id(source_id, revision, source_hash))

    @classmethod
    def create(cls, source: SourceRecord, *, revision_ref: str, content_hash: str, version_observed_at: str, provider_ref: str | None = None, classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None, indexed_at: str = "", valid_from: str = "", valid_to: str = "") -> "SourceVersionRecord":
        if not isinstance(source, SourceRecord):
            raise UnifiedSourceIndexContractError("source must be a SourceRecord")
        owner, child_classification, child_policy = _policy_tuple((source.policy_evidence(),), classification=classification, content_policy=content_policy)
        return cls(source.source_id, revision_ref, content_hash, provider_ref or source.provider_ref, version_observed_at, owner, child_classification, child_policy, source.policy_evidence(), source.source_created_at, source.first_seen_at, source.source_modified_at, indexed_at, valid_from, valid_to)

    def policy_evidence_ref(self) -> PolicyEvidence:
        return PolicyEvidence(RecordKind.SOURCE_VERSION, self.source_version_id, self.source_id, self.source_version_id, self.owner_scope, self.classification, self.content_policy)

    def evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(RecordKind.SOURCE_VERSION, self.source_version_id, self.source_id, self.source_version_id, None, self.content_hash, self.policy_evidence_ref())

    def ref(self) -> RecordRef:
        return RecordRef(RecordKind.SOURCE_VERSION, self.source_version_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceVersionRecord":
        names = {item.name for item in fields(cls)}
        required = {"source_id", "revision_ref", "content_hash", "provider_ref", "version_observed_at", "owner_scope", "classification", "content_policy", "policy_evidence"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["policy_evidence"] = PolicyEvidence.from_dict(data["policy_evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ChunkRecord(_CanonicalRecord):
    source_id: str
    source_version_id: str
    locator: Locator
    extractor_profile_ref: str
    content_hash: str
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    policy_evidence: PolicyEvidence
    content: str | None = None
    indexed_at: str = ""
    valid_from: str = ""
    valid_to: str = ""
    chunk_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.chunk"

    def __post_init__(self) -> None:
        source_id = _record_id(self.source_id, RecordKind.SOURCE, "source_id")
        version_id = _record_id(self.source_version_id, RecordKind.SOURCE_VERSION, "source_version_id")
        evidence = self.policy_evidence
        if not isinstance(evidence, PolicyEvidence) or evidence.parent_kind is not RecordKind.SOURCE_VERSION or evidence.parent_id != version_id or evidence.source_id != source_id:
            raise UnifiedSourceIndexContractError("chunk requires matching source-version policy evidence")
        owner, classification, policy = _policy_tuple((evidence,), owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        normalized_hash = _sha256(self.content_hash, "content_hash")
        content_value = self.content
        if content_value is not None:
            if not isinstance(content_value, str) or len(content_value) > MAX_TEXT_CHARS:
                raise UnifiedSourceIndexContractError("chunk content must be bounded text")
            if policy is not ContentPolicy.INLINE_LOCAL:
                raise UnifiedSourceIndexContractError("only inline_local chunks may carry content")
            if content_hash(content_value) != normalized_hash:
                raise UnifiedSourceIndexContractError("chunk content does not match content_hash")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version_id", version_id)
        object.__setattr__(self, "extractor_profile_ref", _token(self.extractor_profile_ref, "extractor_profile_ref"))
        object.__setattr__(self, "content_hash", normalized_hash)
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        for name in ("indexed_at", "valid_from", "valid_to"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        _time_window(self.valid_from, self.valid_to)
        _set_stable_id(self, "chunk_id", make_chunk_id(version_id, self.locator, self.extractor_profile_ref))

    @classmethod
    def create(cls, version: SourceVersionRecord, *, locator: Locator, extractor_profile_ref: str, content_hash: str, content: str | None = None, classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None, indexed_at: str = "", valid_from: str = "", valid_to: str = "") -> "ChunkRecord":
        if not isinstance(version, SourceVersionRecord):
            raise UnifiedSourceIndexContractError("version must be a SourceVersionRecord")
        evidence = version.policy_evidence_ref()
        owner, child_classification, child_policy = _policy_tuple((evidence,), classification=classification, content_policy=content_policy)
        return cls(version.source_id, version.source_version_id, locator, extractor_profile_ref, content_hash, owner, child_classification, child_policy, evidence, content, indexed_at, valid_from, valid_to)

    def policy_evidence_ref(self) -> PolicyEvidence:
        return PolicyEvidence(RecordKind.CHUNK, self.chunk_id, self.source_id, self.source_version_id, self.owner_scope, self.classification, self.content_policy)

    def evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(RecordKind.CHUNK, self.chunk_id, self.source_id, self.source_version_id, self.locator, self.content_hash, self.policy_evidence_ref())

    def ref(self) -> RecordRef:
        return RecordRef(RecordKind.CHUNK, self.chunk_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChunkRecord":
        names = {item.name for item in fields(cls)}
        required = {"source_id", "source_version_id", "locator", "extractor_profile_ref", "content_hash", "owner_scope", "classification", "content_policy", "policy_evidence"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["locator"] = locator_from_dict(data["locator"])
        data["policy_evidence"] = PolicyEvidence.from_dict(data["policy_evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CodeOccurrenceRecords:
    """One exact code source -> version -> chunk occurrence chain.

    This small aggregate is deliberately not another persisted record.  It is
    the typed boundary used by source adapters and stores when three existing
    USI records must be handled as one occurrence without accepting a foreign
    owner, parent version, locator kind, or policy-evidence alias.
    """

    source: SourceRecord
    source_version: SourceVersionRecord
    chunk: ChunkRecord
    forge_evidence: ForgeCodeOccurrenceEvidence | None = None

    def __post_init__(self) -> None:
        if type(self.source) is not SourceRecord:
            raise UnifiedSourceIndexContractError("code occurrence source must use the exact SourceRecord type")
        if type(self.source_version) is not SourceVersionRecord:
            raise UnifiedSourceIndexContractError(
                "code occurrence version must use the exact SourceVersionRecord type"
            )
        if type(self.chunk) is not ChunkRecord:
            raise UnifiedSourceIndexContractError("code occurrence chunk must use the exact ChunkRecord type")
        if self.forge_evidence is not None and type(self.forge_evidence) is not ForgeCodeOccurrenceEvidence:
            raise UnifiedSourceIndexContractError(
                "Forge occurrence evidence must use the exact typed contract"
            )
        try:
            source = SourceRecord.from_json(self.source.to_json())
            source_version = SourceVersionRecord.from_json(self.source_version.to_json())
            chunk = ChunkRecord.from_json(self.chunk.to_json())
            forge_evidence = (
                None
                if self.forge_evidence is None
                else ForgeCodeOccurrenceEvidence.from_json(self.forge_evidence.to_json())
            )
        except (TypeError, ValueError):
            raise UnifiedSourceIndexContractError(
                "code occurrence records contain noncanonical values"
            ) from None
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_version", source_version)
        object.__setattr__(self, "chunk", chunk)
        object.__setattr__(self, "forge_evidence", forge_evidence)
        if source.source_kind is not SourceKind.CODE:
            raise UnifiedSourceIndexContractError("code occurrence source must have code source_kind")
        if type(chunk.locator) is not CodeRangeLocator:
            raise UnifiedSourceIndexContractError("code occurrence requires an exact CodeRangeLocator")
        if source_version.source_id != source.source_id:
            raise UnifiedSourceIndexContractError("code occurrence version has a foreign source parent")
        if chunk.source_id != source.source_id:
            raise UnifiedSourceIndexContractError("code occurrence chunk has a foreign source parent")
        if chunk.source_version_id != source_version.source_version_id:
            raise UnifiedSourceIndexContractError("code occurrence chunk has a foreign version parent")
        if source_version.policy_evidence != source.policy_evidence():
            raise UnifiedSourceIndexContractError("code occurrence version has aliased source policy evidence")
        if chunk.policy_evidence != source_version.policy_evidence_ref():
            raise UnifiedSourceIndexContractError("code occurrence chunk has aliased version policy evidence")
        if not (
            source.owner_scope
            == source_version.owner_scope
            == chunk.owner_scope
        ):
            raise UnifiedSourceIndexContractError("code occurrence crosses owner scope")
        forge_bound = (
            source.provider_ref == "forge.code"
            or source.canonical_ref.startswith("forge-code-source:")
            or source_version.provider_ref == "forge.code"
            or source_version.revision_ref.startswith("forge-code-version:")
            or chunk.extractor_profile_ref.startswith("forge-code-")
        )
        if forge_evidence is None:
            if forge_bound:
                raise UnifiedSourceIndexContractError(
                    "Forge-bound code occurrence requires inspectable Forge evidence"
                )
            return
        if not forge_bound:
            raise UnifiedSourceIndexContractError(
                "Forge occurrence evidence cannot be attached to an unmarked code chain"
            )
        if not chunk.extractor_profile_ref.startswith("forge-code-"):
            raise UnifiedSourceIndexContractError(
                "Forge occurrence extractor profile must carry the forge-code marker"
            )
        expected_source, expected_version, expected_chunk = _forge_code_expected_records(
            forge_evidence,
            extractor_profile_ref=chunk.extractor_profile_ref,
            version_observed_at=source_version.version_observed_at,
            indexed_at=chunk.indexed_at,
        )
        if (
            source.to_json(),
            source_version.to_json(),
            chunk.to_json(),
        ) != (
            expected_source.to_json(),
            expected_version.to_json(),
            expected_chunk.to_json(),
        ):
            raise UnifiedSourceIndexContractError(
                "Forge occurrence records do not match canonical evidence and parent identities"
            )


def _forge_code_expected_records(
    evidence: ForgeCodeOccurrenceEvidence,
    *,
    extractor_profile_ref: str,
    version_observed_at: str,
    indexed_at: str,
) -> tuple[SourceRecord, SourceVersionRecord, ChunkRecord]:
    source = SourceRecord(
        owner_scope=evidence.owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=evidence.source_ref(),
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref="forge.code",
    )
    source_version = SourceVersionRecord.create(
        source,
        revision_ref=evidence.revision_ref(),
        content_hash=evidence.file_content_sha256,
        version_observed_at=version_observed_at,
        indexed_at=indexed_at,
    )
    chunk = ChunkRecord.create(
        source_version,
        locator=evidence.locator,
        extractor_profile_ref=extractor_profile_ref,
        content_hash=evidence.file_content_sha256,
        content=None,
        indexed_at=indexed_at,
    )
    return source, source_version, chunk


@dataclass(frozen=True, slots=True)
class EntityRecord(_CanonicalRecord):
    source_id: str
    source_version_id: str
    entity_kind: EntityKind
    natural_key: str
    locator: Locator
    extractor_profile_ref: str
    content_hash: str
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    policy_evidence: PolicyEvidence
    label: str = ""
    indexed_at: str = ""
    valid_from: str = ""
    valid_to: str = ""
    entity_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.entity"

    def __post_init__(self) -> None:
        source_id = _record_id(self.source_id, RecordKind.SOURCE, "source_id")
        version_id = _record_id(self.source_version_id, RecordKind.SOURCE_VERSION, "source_version_id")
        evidence = self.policy_evidence
        if not isinstance(evidence, PolicyEvidence) or evidence.parent_kind is not RecordKind.SOURCE_VERSION or evidence.parent_id != version_id or evidence.source_id != source_id:
            raise UnifiedSourceIndexContractError("entity requires matching source-version policy evidence")
        owner, classification, policy = _policy_tuple((evidence,), owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        kind = _enum(self.entity_kind, EntityKind, "entity_kind")
        natural_key = _text(self.natural_key, "natural_key", max_len=512)
        profile = _token(self.extractor_profile_ref, "extractor_profile_ref")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_version_id", version_id)
        object.__setattr__(self, "entity_kind", kind)
        object.__setattr__(self, "natural_key", natural_key)
        object.__setattr__(self, "extractor_profile_ref", profile)
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, "content_hash"))
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "label", _text(self.label, "label", max_len=256, allow_empty=True))
        for name in ("indexed_at", "valid_from", "valid_to"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        _time_window(self.valid_from, self.valid_to)
        _set_stable_id(self, "entity_id", make_entity_id(version_id, kind, natural_key, self.locator, profile))

    @classmethod
    def create(cls, version: SourceVersionRecord, *, entity_kind: EntityKind | str, natural_key: str, locator: Locator, extractor_profile_ref: str, content_hash: str, label: str = "", classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None, indexed_at: str = "", valid_from: str = "", valid_to: str = "") -> "EntityRecord":
        if not isinstance(version, SourceVersionRecord):
            raise UnifiedSourceIndexContractError("version must be a SourceVersionRecord")
        evidence = version.policy_evidence_ref()
        owner, child_classification, child_policy = _policy_tuple((evidence,), classification=classification, content_policy=content_policy)
        return cls(version.source_id, version.source_version_id, entity_kind, natural_key, locator, extractor_profile_ref, content_hash, owner, child_classification, child_policy, evidence, label, indexed_at, valid_from, valid_to)

    def policy_evidence_ref(self) -> PolicyEvidence:
        return PolicyEvidence(RecordKind.ENTITY, self.entity_id, self.source_id, self.source_version_id, self.owner_scope, self.classification, self.content_policy)

    def evidence_ref(self) -> EvidenceRef:
        return EvidenceRef(RecordKind.ENTITY, self.entity_id, self.source_id, self.source_version_id, self.locator, self.content_hash, self.policy_evidence_ref())

    def ref(self) -> RecordRef:
        return RecordRef(RecordKind.ENTITY, self.entity_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EntityRecord":
        names = {item.name for item in fields(cls)}
        required = {"source_id", "source_version_id", "entity_kind", "natural_key", "locator", "extractor_profile_ref", "content_hash", "owner_scope", "classification", "content_policy", "policy_evidence"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["locator"] = locator_from_dict(data["locator"])
        data["policy_evidence"] = PolicyEvidence.from_dict(data["policy_evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class RelationRecord(_CanonicalRecord):
    source_ref: RecordRef
    target_ref: RecordRef
    relation_kind: RelationKind
    method_ref: str
    confidence: float
    evidence_refs: tuple[EvidenceRef, ...]
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    indexed_at: str = ""
    valid_from: str = ""
    valid_to: str = ""
    relation_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.relation"

    def __post_init__(self) -> None:
        if not isinstance(self.source_ref, RecordRef) or not isinstance(self.target_ref, RecordRef):
            raise UnifiedSourceIndexContractError("relation endpoints must be typed RecordRef values")
        evidence = _sorted_evidence(self.evidence_refs)
        owner, classification, policy = _policy_tuple(tuple(item.policy_evidence for item in evidence), owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        if policy is not ContentPolicy.METADATA_ONLY:
            raise UnifiedSourceIndexContractError("relations must use metadata_only content policy")
        kind = _enum(self.relation_kind, RelationKind, "relation_kind")
        method = _token(self.method_ref, "method_ref")
        object.__setattr__(self, "relation_kind", kind)
        object.__setattr__(self, "method_ref", method)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        for name in ("indexed_at", "valid_from", "valid_to"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        _time_window(self.valid_from, self.valid_to)
        _set_stable_id(self, "relation_id", make_relation_id(self.source_ref, self.target_ref, kind, method))

    @classmethod
    def create(cls, source_ref: RecordRef, target_ref: RecordRef, *, relation_kind: RelationKind | str, method_ref: str, confidence: float, evidence_refs: Iterable[EvidenceRef], classification: Classification | str | None = None, indexed_at: str = "", valid_from: str = "", valid_to: str = "") -> "RelationRecord":
        evidence = _sorted_evidence(evidence_refs)
        owner, child_classification, _ = _policy_tuple(tuple(item.policy_evidence for item in evidence), classification=classification, content_policy=ContentPolicy.METADATA_ONLY)
        return cls(source_ref, target_ref, relation_kind, method_ref, confidence, evidence, owner, child_classification, ContentPolicy.METADATA_ONLY, indexed_at, valid_from, valid_to)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RelationRecord":
        names = {item.name for item in fields(cls)}
        required = {"source_ref", "target_ref", "relation_kind", "method_ref", "confidence", "evidence_refs", "owner_scope", "classification", "content_policy"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["source_ref"] = RecordRef.from_dict(data["source_ref"])
        data["target_ref"] = RecordRef.from_dict(data["target_ref"])
        data["evidence_refs"] = tuple(EvidenceRef.from_dict(item) for item in data["evidence_refs"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class LineageRecord(_CanonicalRecord):
    previous: EvidenceRef
    current: EvidenceRef
    reason: LineageReason
    method_ref: str
    confidence: float
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    valid_from: str = ""
    valid_to: str = ""
    lineage_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.lineage"

    def __post_init__(self) -> None:
        if not isinstance(self.previous, EvidenceRef) or not isinstance(self.current, EvidenceRef) or self.previous.record_kind is not RecordKind.CHUNK or self.current.record_kind is not RecordKind.CHUNK:
            raise UnifiedSourceIndexContractError("lineage requires complete previous/current chunk evidence")
        if self.previous.record_id == self.current.record_id:
            raise UnifiedSourceIndexContractError("lineage endpoints must be different chunk occurrences")
        owner, classification, policy = _policy_tuple((self.previous.policy_evidence, self.current.policy_evidence), owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        if policy is not ContentPolicy.METADATA_ONLY:
            raise UnifiedSourceIndexContractError("lineage must use metadata_only content policy")
        reason = _enum(self.reason, LineageReason, "reason")
        method = _token(self.method_ref, "method_ref")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "method_ref", method)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "valid_from", _timestamp(self.valid_from, "valid_from"))
        object.__setattr__(self, "valid_to", _timestamp(self.valid_to, "valid_to"))
        _time_window(self.valid_from, self.valid_to)
        _set_stable_id(self, "lineage_id", make_lineage_id(self.previous.record_id, self.current.record_id, reason, method))

    @classmethod
    def create(cls, previous: EvidenceRef, current: EvidenceRef, *, reason: LineageReason | str, method_ref: str, confidence: float, classification: Classification | str | None = None, valid_from: str = "", valid_to: str = "") -> "LineageRecord":
        owner, child_classification, _ = _policy_tuple((previous.policy_evidence, current.policy_evidence), classification=classification, content_policy=ContentPolicy.METADATA_ONLY)
        return cls(previous, current, reason, method_ref, confidence, owner, child_classification, ContentPolicy.METADATA_ONLY, valid_from, valid_to)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LineageRecord":
        names = {item.name for item in fields(cls)}
        required = {"previous", "current", "reason", "method_ref", "confidence", "owner_scope", "classification", "content_policy"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["previous"] = EvidenceRef.from_dict(data["previous"])
        data["current"] = EvidenceRef.from_dict(data["current"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class SourceScope(_CanonicalRecord):
    owner_scope: str
    source_ids: tuple[str, ...]
    source_version_ids: tuple[str, ...]
    classification: Classification
    content_policy: ContentPolicy
    policy_evidence: tuple[PolicyEvidence, ...]
    scope_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.source_scope"

    def __post_init__(self) -> None:
        sources = tuple(sorted({_record_id(item, RecordKind.SOURCE, "source_id") for item in self.source_ids}))
        versions = tuple(sorted({_record_id(item, RecordKind.SOURCE_VERSION, "source_version_id") for item in self.source_version_ids}))
        if not sources or len(sources) > MAX_SCOPE_SOURCES or len(versions) > MAX_SCOPE_VERSIONS:
            raise UnifiedSourceIndexContractError("source scope must be non-empty and bounded")
        evidence = _sorted_policy_evidence(self.policy_evidence)
        owner, classification, policy = _policy_tuple(evidence, owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        if {item.source_id for item in evidence} != set(sources):
            raise UnifiedSourceIndexContractError("source scope evidence must cover exactly its sources")
        evidence_versions = {item.source_version_id for item in evidence if item.source_version_id}
        if evidence_versions != set(versions):
            raise UnifiedSourceIndexContractError("source scope evidence must cover exactly its versions")
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "source_ids", sources)
        object.__setattr__(self, "source_version_ids", versions)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "policy_evidence", evidence)
        expected = _stable_id("scope", {"owner_scope": owner, "source_ids": sources, "source_version_ids": versions, "classification": classification.value, "content_policy": policy.value})
        _set_stable_id(self, "scope_id", expected)

    @classmethod
    def create(cls, evidence: Iterable[PolicyEvidence], *, classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None) -> "SourceScope":
        items = _sorted_policy_evidence(evidence)
        owner, child_classification, child_policy = _policy_tuple(items, classification=classification, content_policy=content_policy)
        sources = tuple(item.source_id for item in items)
        versions = tuple(item.source_version_id for item in items if item.source_version_id)
        return cls(owner, sources, versions, child_classification, child_policy, items)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceScope":
        names = {item.name for item in fields(cls)}
        required = {"owner_scope", "source_ids", "source_version_ids", "classification", "content_policy", "policy_evidence"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["source_ids"] = tuple(data["source_ids"])
        data["source_version_ids"] = tuple(data["source_version_ids"])
        data["policy_evidence"] = tuple(PolicyEvidence.from_dict(item) for item in data["policy_evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ProjectionManifest(_CanonicalRecord):
    projection_kind: ProjectionKind
    projection_profile_ref: str
    input_snapshot_ref: str
    config_hash: str
    input_evidence: tuple[EvidenceRef, ...]
    implementation_ref: str
    implementation_version: str
    output_generation_ref: str
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    indexed_at: str = ""
    projection_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.projection_manifest"

    def __post_init__(self) -> None:
        evidence = _sorted_evidence(self.input_evidence)
        owner, classification, policy = _policy_tuple(tuple(item.policy_evidence for item in evidence), owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        kind = _enum(self.projection_kind, ProjectionKind, "projection_kind")
        profile = _token(self.projection_profile_ref, "projection_profile_ref")
        snapshot = _token(self.input_snapshot_ref, "input_snapshot_ref")
        config = _sha256(self.config_hash, "config_hash")
        object.__setattr__(self, "projection_kind", kind)
        object.__setattr__(self, "projection_profile_ref", profile)
        object.__setattr__(self, "input_snapshot_ref", snapshot)
        object.__setattr__(self, "config_hash", config)
        object.__setattr__(self, "input_evidence", evidence)
        for name in ("implementation_ref", "implementation_version", "output_generation_ref"):
            object.__setattr__(self, name, _token(getattr(self, name), name))
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "indexed_at", _timestamp(self.indexed_at, "indexed_at"))
        _set_stable_id(self, "projection_id", make_projection_id(kind, profile, snapshot, config, (item.record_id for item in evidence)))

    @classmethod
    def create(cls, *, projection_kind: ProjectionKind | str, projection_profile_ref: str, input_snapshot_ref: str, config_hash: str, input_evidence: Iterable[EvidenceRef], implementation_ref: str, implementation_version: str, output_generation_ref: str, classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None, indexed_at: str = "") -> "ProjectionManifest":
        evidence = _sorted_evidence(input_evidence)
        owner, child_classification, child_policy = _policy_tuple(tuple(item.policy_evidence for item in evidence), classification=classification, content_policy=content_policy)
        return cls(projection_kind, projection_profile_ref, input_snapshot_ref, config_hash, evidence, implementation_ref, implementation_version, output_generation_ref, owner, child_classification, child_policy, indexed_at)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProjectionManifest":
        names = {item.name for item in fields(cls)}
        required = names - {"indexed_at", "projection_id"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["input_evidence"] = tuple(EvidenceRef.from_dict(item) for item in data["input_evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class DerivedRunRecord(_CanonicalRecord):
    derived_kind: DerivedRunKind
    source_scope: SourceScope
    input_snapshot_ref: str
    algorithm_ref: str
    algorithm_version: str
    config_hash: str
    input_evidence: tuple[EvidenceRef, ...]
    embedding_snapshot_ref: str
    quality_evidence_refs: tuple[str, ...]
    rebuild_evidence_ref: str
    input_count: int
    max_nodes: int
    max_depth: int
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    started_at: str = ""
    completed_at: str = ""
    derived_run_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.derived_run"

    def __post_init__(self) -> None:
        if not isinstance(self.source_scope, SourceScope):
            raise UnifiedSourceIndexContractError("source_scope must be typed and bounded")
        evidence = _sorted_evidence(self.input_evidence)
        if any(item.source_id not in self.source_scope.source_ids for item in evidence):
            raise UnifiedSourceIndexContractError("derived input evidence escapes source_scope")
        if not self.source_scope.source_version_ids or any(
            item.source_version_id not in self.source_scope.source_version_ids for item in evidence
        ):
            raise UnifiedSourceIndexContractError("derived input evidence escapes source versions")
        policies = tuple(item.policy_evidence for item in evidence) + self.source_scope.policy_evidence
        owner, classification, policy = _policy_tuple(policies, owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        kind = _enum(self.derived_kind, DerivedRunKind, "derived_kind")
        snapshot = _token(self.input_snapshot_ref, "input_snapshot_ref")
        algorithm = _token(self.algorithm_ref, "algorithm_ref")
        algorithm_version = _token(self.algorithm_version, "algorithm_version")
        config = _sha256(self.config_hash, "config_hash")
        embedding = _text(self.embedding_snapshot_ref, "embedding_snapshot_ref", max_len=256, allow_empty=True)
        if kind is DerivedRunKind.CLUSTER and not embedding:
            raise UnifiedSourceIndexContractError("cluster runs require embedding_snapshot_ref")
        quality = tuple(sorted({_token(item, "quality_evidence_ref") for item in self.quality_evidence_refs}))
        if not quality or len(quality) > 32:
            raise UnifiedSourceIndexContractError("quality evidence must be non-empty and bounded")
        input_count = _integer(self.input_count, "input_count", minimum=1, maximum=MAX_EVIDENCE_REFS)
        if input_count != len(evidence):
            raise UnifiedSourceIndexContractError("input_count must match exact input evidence")
        object.__setattr__(self, "derived_kind", kind)
        object.__setattr__(self, "input_snapshot_ref", snapshot)
        object.__setattr__(self, "algorithm_ref", algorithm)
        object.__setattr__(self, "algorithm_version", algorithm_version)
        object.__setattr__(self, "config_hash", config)
        object.__setattr__(self, "input_evidence", evidence)
        object.__setattr__(self, "embedding_snapshot_ref", embedding)
        object.__setattr__(self, "quality_evidence_refs", quality)
        object.__setattr__(self, "rebuild_evidence_ref", _token(self.rebuild_evidence_ref, "rebuild_evidence_ref"))
        object.__setattr__(self, "input_count", input_count)
        object.__setattr__(self, "max_nodes", _integer(self.max_nodes, "max_nodes", minimum=1, maximum=1_000_000))
        object.__setattr__(self, "max_depth", _integer(self.max_depth, "max_depth", minimum=0, maximum=64))
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "started_at", _timestamp(self.started_at, "started_at"))
        object.__setattr__(self, "completed_at", _timestamp(self.completed_at, "completed_at"))
        if self.completed_at and not self.started_at:
            raise UnifiedSourceIndexContractError("completed_at requires started_at")
        _time_window(self.started_at, self.completed_at)
        _set_stable_id(self, "derived_run_id", make_derived_run_id(kind, self.source_scope.scope_id, snapshot, algorithm, algorithm_version, config))

    @classmethod
    def create(cls, *, derived_kind: DerivedRunKind | str, source_scope: SourceScope, input_snapshot_ref: str, algorithm_ref: str, algorithm_version: str, config_hash: str, input_evidence: Iterable[EvidenceRef], embedding_snapshot_ref: str = "", quality_evidence_refs: Iterable[str], rebuild_evidence_ref: str, max_nodes: int, max_depth: int, classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None, started_at: str = "", completed_at: str = "") -> "DerivedRunRecord":
        if not isinstance(source_scope, SourceScope):
            raise UnifiedSourceIndexContractError("source_scope must be a SourceScope")
        evidence = _sorted_evidence(input_evidence)
        policies = tuple(item.policy_evidence for item in evidence) + source_scope.policy_evidence
        owner, child_classification, child_policy = _policy_tuple(policies, classification=classification, content_policy=content_policy)
        return cls(derived_kind, source_scope, input_snapshot_ref, algorithm_ref, algorithm_version, config_hash, evidence, embedding_snapshot_ref, tuple(quality_evidence_refs), rebuild_evidence_ref, len(evidence), max_nodes, max_depth, owner, child_classification, child_policy, started_at, completed_at)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DerivedRunRecord":
        names = {item.name for item in fields(cls)}
        required = names - {"started_at", "completed_at", "derived_run_id"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["source_scope"] = SourceScope.from_dict(data["source_scope"])
        data["input_evidence"] = tuple(EvidenceRef.from_dict(item) for item in data["input_evidence"])
        data["quality_evidence_refs"] = tuple(data["quality_evidence_refs"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class IndexJobRecord(_CanonicalRecord):
    job_kind: IndexJobKind
    source_scope: SourceScope
    request_ref: str
    profile_ref: str
    status: JobStatus
    max_items: int
    time_budget_ms: int
    cursor: str
    attempt_count: int
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    started_at: str = ""
    completed_at: str = ""
    job_id: str = ""
    SCHEMA: ClassVar[str] = f"{USI_CONTRACT_SCHEMA}.index_job"

    def __post_init__(self) -> None:
        if not isinstance(self.source_scope, SourceScope):
            raise UnifiedSourceIndexContractError("source_scope must be typed and bounded")
        owner, classification, policy = _policy_tuple(self.source_scope.policy_evidence, owner_scope=self.owner_scope, classification=self.classification, content_policy=self.content_policy)
        kind = _enum(self.job_kind, IndexJobKind, "job_kind")
        request = _token(self.request_ref, "request_ref")
        profile = _token(self.profile_ref, "profile_ref")
        object.__setattr__(self, "job_kind", kind)
        object.__setattr__(self, "request_ref", request)
        object.__setattr__(self, "profile_ref", profile)
        object.__setattr__(self, "status", _enum(self.status, JobStatus, "status"))
        object.__setattr__(self, "max_items", _integer(self.max_items, "max_items", minimum=1, maximum=1_000_000))
        object.__setattr__(self, "time_budget_ms", _integer(self.time_budget_ms, "time_budget_ms", minimum=1, maximum=86_400_000))
        object.__setattr__(self, "cursor", _text(self.cursor, "cursor", max_len=1024, allow_empty=True))
        object.__setattr__(self, "attempt_count", _integer(self.attempt_count, "attempt_count", maximum=10_000))
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "content_policy", policy)
        object.__setattr__(self, "started_at", _timestamp(self.started_at, "started_at"))
        object.__setattr__(self, "completed_at", _timestamp(self.completed_at, "completed_at"))
        if self.completed_at and not self.started_at:
            raise UnifiedSourceIndexContractError("completed_at requires started_at")
        _time_window(self.started_at, self.completed_at)
        _set_stable_id(self, "job_id", make_job_id(kind, self.source_scope.scope_id, request, profile))

    @classmethod
    def create(cls, *, job_kind: IndexJobKind | str, source_scope: SourceScope, request_ref: str, profile_ref: str, status: JobStatus | str = JobStatus.PENDING, max_items: int, time_budget_ms: int, cursor: str = "", attempt_count: int = 0, classification: Classification | str | None = None, content_policy: ContentPolicy | str | None = None, started_at: str = "", completed_at: str = "") -> "IndexJobRecord":
        if not isinstance(source_scope, SourceScope):
            raise UnifiedSourceIndexContractError("source_scope must be a SourceScope")
        owner, child_classification, child_policy = _policy_tuple(source_scope.policy_evidence, classification=classification, content_policy=content_policy)
        return cls(job_kind, source_scope, request_ref, profile_ref, status, max_items, time_budget_ms, cursor, attempt_count, owner, child_classification, child_policy, started_at, completed_at)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IndexJobRecord":
        names = {item.name for item in fields(cls)}
        required = names - {"started_at", "completed_at", "job_id"}
        data = _payload(value, schema=cls.SCHEMA, allowed=names, required=required)
        data["source_scope"] = SourceScope.from_dict(data["source_scope"])
        return cls(**data)


_RECORD_BY_SCHEMA = {
    cls.SCHEMA: cls
    for cls in (
        PolicyEvidence,
        RecordRef,
        EvidenceRef,
        SourceRecord,
        SourceVersionRecord,
        ChunkRecord,
        EntityRecord,
        RelationRecord,
        LineageRecord,
        SourceScope,
        ProjectionManifest,
        DerivedRunRecord,
        IndexJobRecord,
    )
}


def record_from_dict(value: Mapping[str, Any]) -> _CanonicalRecord:
    schema = value.get("schema") if isinstance(value, Mapping) else None
    record_type = _RECORD_BY_SCHEMA.get(schema)
    if record_type is None:
        raise UnifiedSourceIndexContractError("unknown or missing USI record schema")
    return record_type.from_dict(value)


def record_from_json(value: str | bytes) -> _CanonicalRecord:
    return record_from_dict(_json_mapping(value))


# Readable aliases for callers that prefer the architecture's identifier term.
stable_source_id = make_source_id
stable_source_version_id = make_source_version_id
stable_chunk_id = make_chunk_id
stable_entity_id = make_entity_id
stable_relation_id = make_relation_id
stable_lineage_id = make_lineage_id
stable_projection_id = make_projection_id
stable_derived_run_id = make_derived_run_id
stable_job_id = make_job_id
