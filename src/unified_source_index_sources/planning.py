"""Default-off Unified Source Index adapter for approved Planning JSON projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

from src.planning_mcp_service import PlanningMcpService, PlanningServiceError
from src.rag_text_chunking import (
    EXACT_CHARACTER_SPLITTER_VERSION,
    split_text_into_exact_character_ranges,
)
from src.unified_source_index_contract import (
    MAX_TEXT_CHARS,
    ChunkRecord,
    Classification,
    ContentPolicy,
    EntityKind,
    EntityRecord,
    EvidenceRef,
    PolicyEvidence,
    RecordKind,
    RecordRef,
    RelationKind,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterCapabilityManifest,
    SourceAdapterOperation,
)
from src.unified_source_index_source_registry import SourceAdapterRegistration


PLANNING_SOURCE_ADAPTER_ID = "planning.approved_json"
PLANNING_SOURCE_ADAPTER_VERSION = "v1"
PLANNING_SOURCE_DOMAIN_ID = "planning"
PLANNING_SOURCE_EXACT_READER_BOUNDARY = "planning.owner_source_projection_reader"
PLANNING_SOURCE_EXTRACTOR_PROFILE = "planning.approved_json.exact.v1"
PLANNING_SOURCE_GRAPH_PROFILE = "planning.approved_json.graph.v1"

PLANNING_SOURCE_BINDING_SCHEMA = "odysseus.usi.planning_source_authority_binding.v1"
PLANNING_SOURCE_EVIDENCE_SCHEMA = "odysseus.usi.planning_source_evidence.v1"
PLANNING_PROJECTION_LOCATOR_SCHEMA = "odysseus.usi.planning_projection_locator.v1"
PLANNING_CHARACTER_RANGE_REQUEST_SCHEMA = "odysseus.usi.planning_character_range_request.v1"
PLANNING_UNAVAILABLE_OBSERVATION_SCHEMA = "odysseus.usi.planning_unavailable_observation.v1"

_OWNER_CAPTURE_SCHEMA = "odysseus.planning.owner_source_capture.v1"
_OWNER_DESCRIPTOR_SCHEMA = "odysseus.planning.owner_source_descriptor.v1"
_OWNER_READ_SCHEMA = "odysseus.planning.owner_source_projection_read.v1"
_OWNER_PROJECTION_SCHEMA = "odysseus.planning.owner_source_projection.v1"
_OWNER_POLICY_REF = "docs/plans/harbor-planning-project-storage-contract.md#privacy-and-redaction"

DEFAULT_MAX_SOURCES = 100
MAX_LOGICAL_ROWS = 100
MAX_DISCOVERY_LIMIT = 100
MAX_SNAPSHOT_METADATA_BYTES = 65_536
MAX_SUPPORTING_REFS = 24
MAX_PROJECTION_UTF8_BYTES = 2_000_000
MAX_PROJECTION_DEPTH = 32
MAX_PRIMITIVE_NODES = 100_000
MAX_PLANNING_CHUNKS = 8
MAX_PLANNING_ENTITIES = 4_096
MAX_PLANNING_RELATIONS = 8_192

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOGICAL_REF_RE = re.compile(r"^planning:roadmap:sha256:[0-9a-f]{64}$")
_ERROR_CODES = frozenset(
    {
        "invalid_authority",
        "invalid_binding",
        "invalid_snapshot",
        "invalid_request",
        "record_not_found",
        "record_still_available",
        "unavailable_state_unproven",
        "stale_authority",
        "source_limit_exceeded",
        "projection_limit_exceeded",
        "graph_limit_exceeded",
        "planning_source_adapter_failed",
    }
)

_CAPTURE_KEYS = frozenset(
    {
        "schema_id",
        "owner_ref",
        "selection_mode",
        "descriptors",
        "ineligible",
        "classification",
        "content_policy",
        "owner_policy_ref",
        "eligible_count",
        "ineligible_count",
        "snapshot_digest",
        "projection_exact",
        "raw_source_exact",
        "raw_source_included",
        "read_only",
        "writes_performed",
    }
)
_DESCRIPTOR_KEYS = frozenset(
    {
        "schema_id",
        "owner_ref",
        "project_id",
        "roadmap_id",
        "revision",
        "revision_state",
        "source_ref",
        "content_hash",
        "canonical_projection_hash",
        "supporting_refs",
        "classification",
        "content_policy",
        "owner_policy_ref",
        "projection_exact",
        "raw_source_exact",
        "raw_source_included",
    }
)
_INELIGIBLE_KEYS = frozenset(
    {
        "project_id",
        "roadmap_id",
        "eligibility",
        "revision",
        "revision_state",
        "content_hash",
        "updated_at",
        "source_ref",
    }
)
_SUPPORTING_REF_KEYS = frozenset({"source_ref", "kind", "access"})
_READ_KEYS = frozenset(
    {
        "schema_id",
        "owner_ref",
        "snapshot_digest",
        "descriptor",
        "projection",
        "classification",
        "content_policy",
        "owner_policy_ref",
        "projection_exact",
        "raw_source_exact",
        "raw_source_included",
        "read_only",
        "writes_performed",
    }
)
_PROJECTION_KEYS = frozenset({"schema_id", "project_id", "roadmap"})
_ROADMAP_KEYS = frozenset(
    {
        "roadmap_id",
        "project_id",
        "revision",
        "content_hash",
        "revision_state",
        "title",
        "objective",
        "assumptions",
        "constraints",
        "nodes",
        "edges",
        "gates",
        "done_contract",
        "source_refs",
        "created_at",
        "updated_at",
    }
)
_NODE_KEYS = frozenset(
    {
        "node_id",
        "kind",
        "title",
        "objective",
        "depends_on",
        "gate_ids",
        "deliverables",
        "allowed_paths",
        "blocked_paths",
        "capability_requirements",
        "verification_rule_ids",
    }
)
_EDGE_KEYS = frozenset({"from", "to", "kind"})
_GATE_KEYS = frozenset(
    {
        "gate_id",
        "kind",
        "title",
        "blocks",
        "decision_needed",
        "safe_default",
        "approval_scope_schema",
        "required_verification_rule_ids",
    }
)


class PlanningSourceAdapterError(ValueError):
    """A fixed-code public error that never includes source content or causes."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "planning_source_adapter_failed"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"PlanningSourceAdapterError(code={self.code!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningSourceAuthorityBinding:
    owner_scope: OwnerScope
    owner_ref: str
    snapshot_digest: str
    adapter_id: str
    adapter_version: str
    adapter_generation: str
    binding_digest: str = ""
    schema: str = PLANNING_SOURCE_BINDING_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            values = _binding_values(self)
            expected = _digest(
                {
                    "adapter_generation": values[5],
                    "adapter_id": values[3],
                    "adapter_version": values[4],
                    "owner_ref": values[1],
                    "owner_scope": values[0].value,
                    "schema": values[6],
                    "snapshot_digest": values[2],
                }
            )
            if values[7] and values[7] != expected:
                _fail("invalid_binding")
            object.__setattr__(self, "owner_scope", OwnerScope(values[0].value))
            object.__setattr__(self, "binding_digest", expected)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_binding"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningSourceAuthorityBinding(binding_digest={self.binding_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningSourceEvidence:
    logical_ref: str
    project_id: str
    roadmap_id: str
    approved_revision: int
    domain_content_hash: str
    canonical_projection_hash: str
    source_ref: str
    supporting_refs: tuple[str, ...]
    owner_ref: str
    owner_policy_ref: str
    snapshot_digest: str
    binding_digest: str
    source_id: str
    source_version_id: str
    schema: str = PLANNING_SOURCE_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            values = _evidence_values(self)
            object.__setattr__(self, "supporting_refs", tuple(values[7]))
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningSourceEvidence(logical_ref={self.logical_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningProjectionLocator:
    logical_ref: str
    source_id: str
    source_version_id: str
    canonical_projection_hash: str
    snapshot_digest: str
    binding_digest: str
    locator_digest: str = ""
    schema: str = PLANNING_PROJECTION_LOCATOR_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            values = _locator_values(self)
            expected = _digest(
                {
                    "binding_digest": values[5],
                    "canonical_projection_hash": values[3],
                    "logical_ref": values[0],
                    "schema": values[7],
                    "snapshot_digest": values[4],
                    "source_id": values[1],
                    "source_version_id": values[2],
                }
            )
            if values[6] and values[6] != expected:
                _fail("invalid_request")
            object.__setattr__(self, "locator_digest", expected)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningProjectionLocator(locator_digest={self.locator_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningCharacterRangeRequest:
    projection_locator: PlanningProjectionLocator
    text_range: TextRangeLocator
    request_digest: str = ""
    schema: str = PLANNING_CHARACTER_RANGE_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            locator = _copy_locator(self.projection_locator)
            text_range = _copy_text_range(self.text_range, "invalid_request")
            if text_range.end_char > MAX_PROJECTION_UTF8_BYTES:
                _fail("invalid_request")
            schema = _exact_text(self.schema, 128, "invalid_request")
            if schema != PLANNING_CHARACTER_RANGE_REQUEST_SCHEMA:
                _fail("invalid_request")
            expected = _digest(
                {
                    "projection_locator_digest": locator.locator_digest,
                    "schema": schema,
                    "text_range": {
                        "end_char": text_range.end_char,
                        "start_char": text_range.start_char,
                    },
                }
            )
            supplied = self.request_digest
            if type(supplied) is not str or (supplied and supplied != expected):
                _fail("invalid_request")
            object.__setattr__(self, "projection_locator", locator)
            object.__setattr__(self, "text_range", text_range)
            object.__setattr__(self, "request_digest", expected)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningCharacterRangeRequest(request_digest={self.request_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningSourceDescriptor:
    source: SourceRecord
    source_version: SourceVersionRecord
    projection_locator: PlanningProjectionLocator
    evidence: PlanningSourceEvidence

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            source = _copy_source(self.source, "invalid_snapshot")
            version = _copy_version(self.source_version, source, "invalid_snapshot")
            locator = _copy_locator(self.projection_locator)
            evidence = _copy_evidence(self.evidence)
            if (
                source.source_kind is not SourceKind.PLANNING
                or source.classification is not Classification.PRIVATE
                or source.content_policy is not ContentPolicy.INLINE_LOCAL
                or source.provider_ref != PLANNING_SOURCE_ADAPTER_ID
                or source.canonical_ref != evidence.logical_ref
                or version.source_id != source.source_id
                or version.provider_ref != source.provider_ref
                or version.owner_scope != source.owner_scope
                or version.classification is not Classification.PRIVATE
                or version.content_policy is not ContentPolicy.INLINE_LOCAL
                or version.policy_evidence != source.policy_evidence()
                or version.content_hash != evidence.canonical_projection_hash
                or version.revision_ref
                != f"approved:{evidence.approved_revision}:{evidence.canonical_projection_hash}"
                or locator.logical_ref != evidence.logical_ref
                or locator.source_id != source.source_id
                or locator.source_version_id != version.source_version_id
                or locator.canonical_projection_hash != evidence.canonical_projection_hash
                or locator.snapshot_digest != evidence.snapshot_digest
                or locator.binding_digest != evidence.binding_digest
                or evidence.source_id != source.source_id
                or evidence.source_version_id != version.source_version_id
            ):
                _fail("invalid_snapshot")
            object.__setattr__(self, "source", source)
            object.__setattr__(self, "source_version", version)
            object.__setattr__(self, "projection_locator", locator)
            object.__setattr__(self, "evidence", evidence)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningSourceDescriptor(logical_ref={self.evidence.logical_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningDiscoveryPage:
    items: tuple[PlanningSourceDescriptor, ...]
    next_cursor: int | None
    snapshot_digest: str
    binding_digest: str

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            if type(self.items) is not tuple or len(self.items) > MAX_DISCOVERY_LIMIT:
                _fail("invalid_snapshot")
            if self.next_cursor is not None and (
                type(self.next_cursor) is not int or self.next_cursor < 0
            ):
                _fail("invalid_snapshot")
            snapshot = _sha(self.snapshot_digest, "invalid_snapshot")
            binding = _sha(self.binding_digest, "invalid_snapshot")
            items = tuple(_copy_descriptor(item) for item in self.items)
            if any(
                item.evidence.snapshot_digest != snapshot
                or item.evidence.binding_digest != binding
                for item in items
            ):
                _fail("invalid_snapshot")
            object.__setattr__(self, "items", items)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningDiscoveryPage(item_count={len(self.items)}, next_cursor={self.next_cursor!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningSourceOccurrence:
    source: SourceRecord
    source_version: SourceVersionRecord
    chunks: tuple[ChunkRecord, ...]
    entities: tuple[EntityRecord, ...]
    relations: tuple[RelationRecord, ...]
    projection_locator: PlanningProjectionLocator
    evidence: PlanningSourceEvidence
    splitter_version: str

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            descriptor = PlanningSourceDescriptor(
                self.source,
                self.source_version,
                self.projection_locator,
                self.evidence,
            )
            if (
                type(self.chunks) is not tuple
                or not 1 <= len(self.chunks) <= MAX_PLANNING_CHUNKS
                or type(self.entities) is not tuple
                or len(self.entities) > MAX_PLANNING_ENTITIES
                or type(self.relations) is not tuple
                or len(self.relations) > MAX_PLANNING_RELATIONS
                or type(self.splitter_version) is not str
                or self.splitter_version != EXACT_CHARACTER_SPLITTER_VERSION
            ):
                _fail("invalid_snapshot")
            chunks = tuple(
                _copy_chunk(item, descriptor.source_version, "invalid_snapshot")
                for item in self.chunks
            )
            entities = tuple(
                _copy_entity(item, descriptor.source_version, "invalid_snapshot")
                for item in self.entities
            )
            entities_by_ref = {item.ref(): item for item in entities}
            entity_refs = set(entities_by_ref)
            relations = tuple(_copy_relation(item, "invalid_snapshot") for item in self.relations)

            def endpoint_evidence(relation: RelationRecord) -> tuple[EvidenceRef, ...]:
                endpoint_refs = (
                    entities_by_ref[relation.source_ref].evidence_ref(),
                    entities_by_ref[relation.target_ref].evidence_ref(),
                )
                unique = {
                    (evidence.record_kind.value, evidence.record_id): evidence
                    for evidence in endpoint_refs
                }
                return tuple(unique[key] for key in sorted(unique))

            if any(
                item.source_id != descriptor.source.source_id
                or item.source_version_id != descriptor.source_version.source_version_id
                or item.extractor_profile_ref != PLANNING_SOURCE_EXTRACTOR_PROFILE
                or item.owner_scope != descriptor.source.owner_scope
                or item.classification is not Classification.PRIVATE
                or item.content_policy is not ContentPolicy.INLINE_LOCAL
                or item.policy_evidence != descriptor.source_version.policy_evidence_ref()
                or type(item.locator) is not TextRangeLocator
                or type(item.content) is not str
                for item in chunks
            ):
                _fail("invalid_snapshot")
            if any(
                item.source_id != descriptor.source.source_id
                or item.source_version_id != descriptor.source_version.source_version_id
                or item.extractor_profile_ref != PLANNING_SOURCE_GRAPH_PROFILE
                or item.owner_scope != descriptor.source.owner_scope
                or item.classification is not Classification.PRIVATE
                or item.content_policy is not ContentPolicy.INLINE_LOCAL
                or item.policy_evidence != descriptor.source_version.policy_evidence_ref()
                for item in entities
            ):
                _fail("invalid_snapshot")
            if any(
                item.source_ref not in entity_refs
                or item.target_ref not in entity_refs
                or item.owner_scope != descriptor.source.owner_scope
                or item.classification is not Classification.PRIVATE
                or item.content_policy is not ContentPolicy.METADATA_ONLY
                or item.evidence_refs != endpoint_evidence(item)
                for item in relations
            ):
                _fail("invalid_snapshot")
            total_characters = sum(len(chunk.content) for chunk in chunks)
            if total_characters > MAX_PROJECTION_UTF8_BYTES:
                _fail("invalid_snapshot")
            ordered_start = 0
            total_utf8_bytes = 0
            canonical_parts: list[str] = []
            for chunk in chunks:
                if (
                    chunk.locator.start_char != ordered_start
                    or chunk.locator.end_char - chunk.locator.start_char != len(chunk.content)
                ):
                    _fail("invalid_snapshot")
                try:
                    total_utf8_bytes += len(chunk.content.encode("utf-8"))
                except UnicodeError:
                    _fail("invalid_snapshot")
                if total_utf8_bytes > MAX_PROJECTION_UTF8_BYTES:
                    _fail("invalid_snapshot")
                canonical_parts.append(chunk.content)
                ordered_start = chunk.locator.end_char
            if ordered_start != total_characters:
                _fail("invalid_snapshot")
            projection_text = "".join(canonical_parts)
            canonical_projection_hash = content_hash(projection_text)
            bound_hashes = {
                descriptor.source_version.content_hash,
                descriptor.projection_locator.canonical_projection_hash,
                descriptor.evidence.canonical_projection_hash,
            }
            if bound_hashes != {canonical_projection_hash}:
                _fail("invalid_snapshot")
            projection = _projection_from_occurrence_text(
                projection_text,
                descriptor.evidence,
            )
            expected_entities, expected_relations = _graph_records(
                descriptor.source_version,
                projection,
                projection_text,
            )
            if entities != expected_entities or relations != expected_relations:
                _fail("invalid_snapshot")
            object.__setattr__(self, "source", descriptor.source)
            object.__setattr__(self, "source_version", descriptor.source_version)
            object.__setattr__(self, "chunks", chunks)
            object.__setattr__(self, "entities", entities)
            object.__setattr__(self, "relations", relations)
            object.__setattr__(self, "projection_locator", descriptor.projection_locator)
            object.__setattr__(self, "evidence", descriptor.evidence)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return (
            "PlanningSourceOccurrence("
            f"logical_ref={self.evidence.logical_ref!r}, chunk_count={len(self.chunks)}, "
            f"entity_count={len(self.entities)}, relation_count={len(self.relations)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PlanningExactRead:
    logical_ref: str
    content: str = field(repr=False)
    content_hash: str
    request: PlanningCharacterRangeRequest
    evidence: PlanningSourceEvidence

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            logical_ref = _logical_ref_value(self.logical_ref, "invalid_snapshot")
            content = _exact_text(
                self.content,
                MAX_PROJECTION_UTF8_BYTES,
                "invalid_snapshot",
            )
            if len(content.encode("utf-8")) > MAX_PROJECTION_UTF8_BYTES:
                _fail("invalid_snapshot")
            supplied_hash = _sha(self.content_hash, "invalid_snapshot")
            request = _copy_request(self.request)
            evidence = _copy_evidence(self.evidence)
            if (
                not content
                or supplied_hash != content_hash(content)
                or logical_ref != request.projection_locator.logical_ref
                or logical_ref != evidence.logical_ref
                or request.projection_locator.canonical_projection_hash
                != evidence.canonical_projection_hash
                or request.projection_locator.source_id != evidence.source_id
                or request.projection_locator.source_version_id
                != evidence.source_version_id
                or request.projection_locator.snapshot_digest != evidence.snapshot_digest
                or request.projection_locator.binding_digest != evidence.binding_digest
                or request.text_range.end_char - request.text_range.start_char != len(content)
            ):
                _fail("invalid_snapshot")
            object.__setattr__(self, "request", request)
            object.__setattr__(self, "evidence", evidence)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return f"PlanningExactRead(logical_ref={self.logical_ref!r}, character_count={len(self.content)})"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningUnavailableObservation:
    logical_ref: str
    project_id: str
    roadmap_id: str
    reason: str
    snapshot_digest: str
    binding_digest: str
    observation_digest: str = ""
    schema: str = PLANNING_UNAVAILABLE_OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            logical_ref = _logical_ref_value(self.logical_ref, "invalid_snapshot")
            project_id = _identifier(self.project_id, "invalid_snapshot")
            roadmap_id = _identifier(self.roadmap_id, "invalid_snapshot")
            reason = _exact_text(self.reason, 96, "invalid_snapshot")
            snapshot = _sha(self.snapshot_digest, "invalid_snapshot")
            binding = _sha(self.binding_digest, "invalid_snapshot")
            schema = _exact_text(self.schema, 128, "invalid_snapshot")
            if (
                schema != PLANNING_UNAVAILABLE_OBSERVATION_SCHEMA
                or reason != "ineligible_no_approved_revision"
                or logical_ref != _logical_ref(project_id, roadmap_id)
            ):
                _fail("invalid_snapshot")
            expected = _digest(
                {
                    "binding_digest": binding,
                    "logical_ref": logical_ref,
                    "project_id": project_id,
                    "reason": reason,
                    "roadmap_id": roadmap_id,
                    "schema": schema,
                    "snapshot_digest": snapshot,
                }
            )
            if type(self.observation_digest) is not str or (
                self.observation_digest and self.observation_digest != expected
            ):
                _fail("invalid_snapshot")
            object.__setattr__(self, "observation_digest", expected)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_snapshot"
        if failure is not None:
            _fail(failure)

    def __repr__(self) -> str:
        return (
            "PlanningUnavailableObservation("
            f"logical_ref={self.logical_ref!r}, reason={self.reason!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedDescriptor:
    logical_ref: str
    project_id: str
    roadmap_id: str
    revision: int
    domain_content_hash: str
    canonical_projection_hash: str
    source_ref: str
    supporting_refs: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedIneligible:
    logical_ref: str
    project_id: str
    roadmap_id: str


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedSnapshot:
    owner_ref: str
    descriptors: tuple[_CapturedDescriptor, ...]
    ineligible: tuple[_CapturedIneligible, ...]
    snapshot_digest: str


@dataclass(slots=True)
class _PrimitiveBudget:
    nodes: int = 0
    utf8_bytes: int = 0


def planning_source_capability_manifest() -> SourceAdapterCapabilityManifest:
    return SourceAdapterCapabilityManifest(
        adapter_id=PLANNING_SOURCE_ADAPTER_ID,
        adapter_version=PLANNING_SOURCE_ADAPTER_VERSION,
        domain_id=PLANNING_SOURCE_DOMAIN_ID,
        source_kind=SourceKind.PLANNING,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.PRIVATE,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        query_capability=QueryCapability.EXACT_READER,
        operations=tuple(SourceAdapterOperation),
        exact_reader_boundary=PLANNING_SOURCE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


def planning_source_registration() -> SourceAdapterRegistration:
    return SourceAdapterRegistration(planning_source_capability_manifest(), factory=None)


class PlanningSourceAdapter:
    """Read-only view over one exact public Planning owner-source snapshot."""

    __slots__ = (
        "_service",
        "_bound_service",
        "_owner_scope",
        "_expected_owner_ref",
        "_observed_at",
        "_max_sources",
        "_snapshot",
        "_binding_fields",
    )

    def __init__(
        self,
        *,
        service: PlanningMcpService,
        owner_scope: OwnerScope,
        expected_owner_ref: str,
        observed_at: str,
        max_sources: int = DEFAULT_MAX_SOURCES,
    ) -> None:
        failure: str | None = None
        try:
            if type(service) is not PlanningMcpService or type(owner_scope) is not OwnerScope:
                _fail("invalid_authority")
            scope = OwnerScope(_exact_text(owner_scope.value, 64, "invalid_authority"))
            owner_ref = _exact_text(expected_owner_ref, 512, "invalid_authority")
            observed = _timestamp(observed_at, "invalid_authority")
            if type(max_sources) is not int or not 1 <= max_sources <= MAX_LOGICAL_ROWS:
                _fail("invalid_authority")
            service_object = service
            raw = service_object.capture_owner_source_snapshot(
                owner_ref,
                max_sources=max_sources,
            )
            if service is not service_object or type(service_object) is not PlanningMcpService:
                _fail("invalid_authority")
            snapshot = _capture_snapshot(raw, owner_ref=owner_ref, max_sources=max_sources)
            manifest = planning_source_capability_manifest()
            fields = (
                scope.value,
                owner_ref,
                snapshot.snapshot_digest,
                manifest.adapter_id,
                manifest.adapter_version,
                manifest.generation_ref,
            )
            binding = PlanningSourceAuthorityBinding(
                owner_scope=scope,
                owner_ref=owner_ref,
                snapshot_digest=snapshot.snapshot_digest,
                adapter_id=fields[3],
                adapter_version=fields[4],
                adapter_generation=fields[5],
            )
            self._service = service_object
            self._bound_service = service_object
            self._owner_scope = scope.value
            self._expected_owner_ref = owner_ref
            self._observed_at = observed
            self._max_sources = max_sources
            self._snapshot = snapshot
            self._binding_fields = fields + (binding.binding_digest,)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except PlanningServiceError:
            failure = "invalid_authority"
        except Exception:
            failure = "planning_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    @staticmethod
    def logical_ref(project_id: str, roadmap_id: str) -> str:
        failure: str | None = None
        try:
            return _logical_ref(
                _identifier(project_id, "invalid_request"),
                _identifier(roadmap_id, "invalid_request"),
            )
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _fail(failure)

    @property
    def binding(self) -> PlanningSourceAuthorityBinding:
        failure: str | None = None
        try:
            return _binding_from_fields(self._binding_fields)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "invalid_binding"
        if failure is not None:
            _fail(failure)

    @property
    def manifest(self) -> SourceAdapterCapabilityManifest:
        return planning_source_capability_manifest()

    def discover(
        self,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
        cursor: int = 0,
        limit: int = MAX_DISCOVERY_LIMIT,
    ) -> PlanningDiscoveryPage:
        failure: str | None = None
        try:
            if (
                type(cursor) is not int
                or cursor < 0
                or cursor > MAX_LOGICAL_ROWS
                or cursor > len(self._snapshot.descriptors)
                or type(limit) is not int
                or not 1 <= limit <= MAX_DISCOVERY_LIMIT
            ):
                _fail("invalid_request")
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            if cursor > len(snapshot.descriptors):
                _fail("invalid_request")
            selected = snapshot.descriptors[cursor : cursor + limit]
            items = tuple(_descriptor(binding, item, self._observed_at) for item in selected)
            end = cursor + len(selected)
            return PlanningDiscoveryPage(
                items=items,
                next_cursor=end if end < len(snapshot.descriptors) else None,
                snapshot_digest=snapshot.snapshot_digest,
                binding_digest=binding.binding_digest,
            )
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def observe_version(
        self,
        logical_ref: str,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> PlanningSourceDescriptor:
        failure: str | None = None
        try:
            validated_ref = _logical_ref_value(logical_ref, "invalid_request")
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            item = _descriptor_by_ref(snapshot, validated_ref)
            return _descriptor(binding, item, self._observed_at)
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def extract(
        self,
        logical_ref: str,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> PlanningSourceOccurrence:
        failure: str | None = None
        try:
            validated_ref = _logical_ref_value(logical_ref, "invalid_request")
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            captured = _descriptor_by_ref(snapshot, validated_ref)
            descriptor = _descriptor(binding, captured, self._observed_at)
            projection, projection_text = self._read_projection(snapshot, captured)
            ranges = split_text_into_exact_character_ranges(
                projection_text,
                max_chars=MAX_TEXT_CHARS,
                max_ranges=MAX_PLANNING_CHUNKS,
            )
            if (
                not ranges
                or len(ranges) > MAX_PLANNING_CHUNKS
                or ranges[0].char_start != 0
                or ranges[-1].char_end != len(projection_text)
                or any(
                    left.char_end != right.char_start
                    for left, right in zip(ranges, ranges[1:])
                )
            ):
                _fail("invalid_snapshot")
            chunks = tuple(
                ChunkRecord.create(
                    descriptor.source_version,
                    locator=TextRangeLocator(item.char_start, item.char_end),
                    extractor_profile_ref=PLANNING_SOURCE_EXTRACTOR_PROFILE,
                    content_hash=content_hash(projection_text[item.char_start : item.char_end]),
                    content=projection_text[item.char_start : item.char_end],
                    classification=Classification.PRIVATE,
                    content_policy=ContentPolicy.INLINE_LOCAL,
                )
                for item in ranges
            )
            entities, relations = _graph_records(
                descriptor.source_version,
                projection,
                projection_text,
            )
            return PlanningSourceOccurrence(
                descriptor.source,
                descriptor.source_version,
                chunks,
                entities,
                relations,
                descriptor.projection_locator,
                descriptor.evidence,
                EXACT_CHARACTER_SPLITTER_VERSION,
            )
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except ValueError:
            failure = "projection_limit_exceeded"
        except Exception:
            failure = "planning_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def read_exact(
        self,
        request: PlanningCharacterRangeRequest,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> PlanningExactRead:
        failure: str | None = None
        try:
            if type(request) is not PlanningCharacterRangeRequest:
                _fail("invalid_request")
            request_copy = _copy_request(request)
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            captured = _descriptor_by_ref(
                snapshot,
                request_copy.projection_locator.logical_ref,
            )
            descriptor = _descriptor(binding, captured, self._observed_at)
            if request_copy.projection_locator != descriptor.projection_locator:
                _fail("invalid_request")
            _projection, projection_text = self._read_projection(snapshot, captured)
            start = request_copy.text_range.start_char
            end = request_copy.text_range.end_char
            if end > len(projection_text):
                _fail("invalid_request")
            selected = projection_text[start:end]
            return PlanningExactRead(
                logical_ref=captured.logical_ref,
                content=selected,
                content_hash=content_hash(selected),
                request=request_copy,
                evidence=descriptor.evidence,
            )
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def observe_unavailable(
        self,
        logical_ref: str,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> PlanningUnavailableObservation:
        failure: str | None = None
        try:
            ref = _logical_ref_value(logical_ref, "invalid_request")
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            if any(item.logical_ref == ref for item in snapshot.descriptors):
                _fail("record_still_available")
            matches = [item for item in snapshot.ineligible if item.logical_ref == ref]
            if len(matches) != 1:
                _fail("unavailable_state_unproven")
            item = matches[0]
            return PlanningUnavailableObservation(
                logical_ref=item.logical_ref,
                project_id=item.project_id,
                roadmap_id=item.roadmap_id,
                reason="ineligible_no_approved_revision",
                snapshot_digest=snapshot.snapshot_digest,
                binding_digest=binding.binding_digest,
            )
        except PlanningSourceAdapterError as exc:
            failure = exc.code
        except Exception:
            failure = "planning_source_adapter_failed"
        if failure is not None:
            _fail(failure)

    def _operation_capture(
        self,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> tuple[PlanningSourceAuthorityBinding, _CapturedSnapshot]:
        expected_binding = _sha(expected_binding_digest, "invalid_request")
        expected_snapshot = _sha(expected_snapshot_digest, "invalid_request")
        binding = _binding_from_fields(self._binding_fields)
        if (
            expected_binding != binding.binding_digest
            or expected_snapshot != binding.snapshot_digest
        ):
            _fail("stale_authority")
        service = self._service
        if type(service) is not PlanningMcpService or service is not self._bound_service:
            _fail("stale_authority")
        try:
            raw = service.capture_owner_source_snapshot(
                self._expected_owner_ref,
                max_sources=self._max_sources,
            )
        except PlanningServiceError:
            _fail("stale_authority")
        if (
            self._service is not service
            or service is not self._bound_service
            or type(service) is not PlanningMcpService
        ):
            _fail("stale_authority")
        snapshot = _capture_snapshot(
            raw,
            owner_ref=self._expected_owner_ref,
            max_sources=self._max_sources,
        )
        if snapshot != self._snapshot or snapshot.snapshot_digest != expected_snapshot:
            _fail("stale_authority")
        return binding, snapshot

    def _read_projection(
        self,
        snapshot: _CapturedSnapshot,
        captured: _CapturedDescriptor,
    ) -> tuple[dict[str, Any], str]:
        service = self._service
        if type(service) is not PlanningMcpService or service is not self._bound_service:
            _fail("stale_authority")
        try:
            raw = service.read_owner_source_projection(
                _descriptor_payload(captured, snapshot.owner_ref),
                expected_owner_ref=snapshot.owner_ref,
                expected_snapshot_digest=snapshot.snapshot_digest,
            )
        except PlanningServiceError as exc:
            if exc.code in {
                "owner_source_stale",
                "owner_source_changed",
                "source_version_stale",
            }:
                _fail("stale_authority")
            _fail("invalid_snapshot")
        if (
            self._service is not service
            or service is not self._bound_service
            or type(service) is not PlanningMcpService
        ):
            _fail("stale_authority")
        projection = _capture_read(
            raw,
            snapshot=snapshot,
            descriptor=captured,
        )
        projection_bytes = _canonical_bytes(projection)
        expected_hash = "sha256:" + hashlib.sha256(projection_bytes).hexdigest()
        if expected_hash != captured.canonical_projection_hash:
            _fail("invalid_snapshot")
        try:
            projection_text = projection_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("invalid_snapshot")
        return projection, projection_text


def _fail(code: str) -> None:
    raise PlanningSourceAdapterError(code)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except Exception:
        _fail("invalid_snapshot")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_text(value: object, maximum: int, code: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value):
        _fail(code)
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        _fail(code)
    if len(encoded) > maximum * 4:
        _fail(code)
    return value


def _identifier(value: object, code: str) -> str:
    text = _exact_text(value, 256, code)
    if any(ord(character) < 32 for character in text):
        _fail(code)
    return text


def _sha(value: object, code: str) -> str:
    text = _exact_text(value, 71, code)
    if _SHA256_RE.fullmatch(text) is None:
        _fail(code)
    return text


def _logical_ref_value(value: object, code: str) -> str:
    text = _exact_text(value, 96, code)
    if _LOGICAL_REF_RE.fullmatch(text) is None:
        _fail(code)
    return text


def _timestamp(value: object, code: str) -> str:
    text = _exact_text(value, 64, code)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    return text


def _logical_ref(project_id: str, roadmap_id: str) -> str:
    return "planning:roadmap:" + _digest(
        {"project_id": project_id, "roadmap_id": roadmap_id}
    )


def _binding_values(
    value: object,
) -> tuple[OwnerScope, str, str, str, str, str, str, str]:
    if type(value) is not PlanningSourceAuthorityBinding:
        _fail("invalid_binding")
    if type(value.owner_scope) is not OwnerScope:
        _fail("invalid_binding")
    scope = OwnerScope(_exact_text(value.owner_scope.value, 64, "invalid_binding"))
    owner = _exact_text(value.owner_ref, 512, "invalid_binding")
    snapshot = _sha(value.snapshot_digest, "invalid_binding")
    adapter_id = _exact_text(value.adapter_id, 128, "invalid_binding")
    adapter_version = _exact_text(value.adapter_version, 64, "invalid_binding")
    generation = _exact_text(value.adapter_generation, 96, "invalid_binding")
    schema = _exact_text(value.schema, 128, "invalid_binding")
    supplied = value.binding_digest
    if type(supplied) is not str or (supplied and _SHA256_RE.fullmatch(supplied) is None):
        _fail("invalid_binding")
    if (
        adapter_id != PLANNING_SOURCE_ADAPTER_ID
        or adapter_version != PLANNING_SOURCE_ADAPTER_VERSION
        or generation != planning_source_capability_manifest().generation_ref
        or schema != PLANNING_SOURCE_BINDING_SCHEMA
    ):
        _fail("invalid_binding")
    return scope, owner, snapshot, adapter_id, adapter_version, generation, schema, supplied


def _binding_from_fields(fields: object) -> PlanningSourceAuthorityBinding:
    if type(fields) is not tuple or len(fields) != 7:
        _fail("invalid_binding")
    if any(type(item) is not str for item in fields):
        _fail("invalid_binding")
    return PlanningSourceAuthorityBinding(
        owner_scope=OwnerScope(fields[0]),
        owner_ref=fields[1],
        snapshot_digest=fields[2],
        adapter_id=fields[3],
        adapter_version=fields[4],
        adapter_generation=fields[5],
        binding_digest=fields[6],
    )


def _exact_keys(value: object, expected: frozenset[str], code: str) -> bool:
    if type(value) is not dict or len(value) != len(expected):
        return False
    keys = tuple(value.keys())
    if any(type(key) is not str or len(key) > 512 for key in keys):
        _fail(code)
    return frozenset(keys) == expected

def _capture_snapshot(
    value: object,
    *,
    owner_ref: str,
    max_sources: int,
) -> _CapturedSnapshot:
    if not _exact_keys(value, _CAPTURE_KEYS, "invalid_snapshot"):
        _fail("invalid_snapshot")
    for name, maximum in (
        ("schema_id", 128),
        ("owner_ref", 512),
        ("selection_mode", 64),
        ("classification", 32),
        ("content_policy", 32),
        ("owner_policy_ref", 256),
        ("snapshot_digest", 71),
    ):
        _exact_text(value.get(name), maximum, "invalid_snapshot")
    descriptors_raw = value.get("descriptors")
    ineligible_raw = value.get("ineligible")
    if (
        type(descriptors_raw) is not list
        or type(ineligible_raw) is not list
        or len(descriptors_raw) > max_sources
        or len(descriptors_raw) + len(ineligible_raw) > MAX_LOGICAL_ROWS
    ):
        _fail("source_limit_exceeded")
    expected_owner = _exact_text(owner_ref, 512, "invalid_snapshot")
    if (
        value.get("schema_id") != _OWNER_CAPTURE_SCHEMA
        or value.get("owner_ref") != expected_owner
        or value.get("selection_mode") != "latest_approved"
        or value.get("classification") != "private"
        or value.get("content_policy") != "inline_local"
        or value.get("owner_policy_ref") != _OWNER_POLICY_REF
        or type(value.get("eligible_count")) is not int
        or value.get("eligible_count") != len(descriptors_raw)
        or type(value.get("ineligible_count")) is not int
        or value.get("ineligible_count") != len(ineligible_raw)
        or value.get("projection_exact") is not True
        or value.get("raw_source_exact") is not False
        or value.get("raw_source_included") is not False
        or value.get("read_only") is not True
        or value.get("writes_performed") is not False
    ):
        _fail("invalid_snapshot")
    descriptors = tuple(_capture_descriptor(item, expected_owner) for item in descriptors_raw)
    ineligible = tuple(_capture_ineligible(item) for item in ineligible_raw)
    if descriptors != tuple(sorted(descriptors, key=lambda item: (item.project_id, item.roadmap_id, item.revision))):
        _fail("invalid_snapshot")
    if ineligible != tuple(sorted(ineligible, key=lambda item: (item.project_id, item.roadmap_id))):
        _fail("invalid_snapshot")
    refs = [item.logical_ref for item in descriptors] + [item.logical_ref for item in ineligible]
    if len(set(refs)) != len(refs):
        _fail("invalid_snapshot")
    manifest = {
        "schema_id": _OWNER_CAPTURE_SCHEMA,
        "owner_ref": expected_owner,
        "selection_mode": "latest_approved",
        "descriptors": [_descriptor_payload(item, expected_owner) for item in descriptors],
        "ineligible": [_ineligible_payload(item) for item in ineligible],
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": _OWNER_POLICY_REF,
    }
    canonical = _canonical_bytes(manifest)
    if len(canonical) > MAX_SNAPSHOT_METADATA_BYTES:
        _fail("source_limit_exceeded")
    expected_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    if _sha(value.get("snapshot_digest"), "invalid_snapshot") != expected_digest:
        _fail("invalid_snapshot")
    return _CapturedSnapshot(expected_owner, descriptors, ineligible, expected_digest)


def _capture_descriptor(value: object, owner_ref: str) -> _CapturedDescriptor:
    if not _exact_keys(value, _DESCRIPTOR_KEYS, "invalid_snapshot"):
        _fail("invalid_snapshot")
    for name, maximum in (
        ("schema_id", 128),
        ("owner_ref", 512),
        ("revision_state", 32),
        ("source_ref", 4_096),
        ("content_hash", 71),
        ("canonical_projection_hash", 71),
        ("classification", 32),
        ("content_policy", 32),
        ("owner_policy_ref", 256),
    ):
        _exact_text(value.get(name), maximum, "invalid_snapshot")
    project_id = _identifier(value.get("project_id"), "invalid_snapshot")
    roadmap_id = _identifier(value.get("roadmap_id"), "invalid_snapshot")
    revision = value.get("revision")
    supporting = value.get("supporting_refs")
    if (
        type(revision) is not int
        or revision < 1
        or type(supporting) is not list
        or len(supporting) > MAX_SUPPORTING_REFS
        or value.get("schema_id") != _OWNER_DESCRIPTOR_SCHEMA
        or value.get("owner_ref") != owner_ref
        or value.get("revision_state") != "approved"
        or value.get("classification") != "private"
        or value.get("content_policy") != "inline_local"
        or value.get("owner_policy_ref") != _OWNER_POLICY_REF
        or value.get("projection_exact") is not True
        or value.get("raw_source_exact") is not False
        or value.get("raw_source_included") is not False
    ):
        _fail("invalid_snapshot")
    source_ref = _exact_text(value.get("source_ref"), 4_096, "invalid_snapshot")
    domain_hash = _sha(value.get("content_hash"), "invalid_snapshot")
    projection_hash = _sha(value.get("canonical_projection_hash"), "invalid_snapshot")
    support_values = tuple(_capture_supporting_ref(item) for item in supporting)
    if len({item[0] for item in support_values}) != len(support_values):
        _fail("invalid_snapshot")
    return _CapturedDescriptor(
        _logical_ref(project_id, roadmap_id),
        project_id,
        roadmap_id,
        revision,
        domain_hash,
        projection_hash,
        source_ref,
        support_values,
    )


def _capture_supporting_ref(value: object) -> tuple[str, str, str]:
    if not _exact_keys(value, _SUPPORTING_REF_KEYS, "invalid_snapshot"):
        _fail("invalid_snapshot")
    for name, maximum in (("source_ref", 4_096), ("kind", 64), ("access", 64)):
        _exact_text(value.get(name), maximum, "invalid_snapshot")
    if (
        value.get("kind") != "rendered_handoff_ref"
        or value.get("access") != "reference_only"
    ):
        _fail("invalid_snapshot")
    return (
        _exact_text(value.get("source_ref"), 4_096, "invalid_snapshot"),
        "rendered_handoff_ref",
        "reference_only",
    )


def _capture_ineligible(value: object) -> _CapturedIneligible:
    if not _exact_keys(value, _INELIGIBLE_KEYS, "invalid_snapshot"):
        _fail("invalid_snapshot")
    _exact_text(value.get("eligibility"), 96, "invalid_snapshot")
    for name, maximum in (("revision_state", 32), ("content_hash", 71), ("updated_at", 64), ("source_ref", 4_096)):
        _exact_text(value.get(name), maximum, "invalid_snapshot", allow_empty=True)
    project_id = _identifier(value.get("project_id"), "invalid_snapshot")
    roadmap_id = _identifier(value.get("roadmap_id"), "invalid_snapshot")
    if (
        value.get("eligibility") != "ineligible_no_approved_revision"
        or value.get("revision") is not None
        or value.get("revision_state") != ""
        or value.get("content_hash") != ""
        or value.get("updated_at") != ""
        or value.get("source_ref") != ""
    ):
        _fail("invalid_snapshot")
    return _CapturedIneligible(_logical_ref(project_id, roadmap_id), project_id, roadmap_id)


def _descriptor_payload(item: _CapturedDescriptor, owner_ref: str) -> dict[str, Any]:
    return {
        "schema_id": _OWNER_DESCRIPTOR_SCHEMA,
        "owner_ref": owner_ref,
        "project_id": item.project_id,
        "roadmap_id": item.roadmap_id,
        "revision": item.revision,
        "revision_state": "approved",
        "source_ref": item.source_ref,
        "content_hash": item.domain_content_hash,
        "canonical_projection_hash": item.canonical_projection_hash,
        "supporting_refs": [
            {"source_ref": ref, "kind": kind, "access": access}
            for ref, kind, access in item.supporting_refs
        ],
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": _OWNER_POLICY_REF,
        "projection_exact": True,
        "raw_source_exact": False,
        "raw_source_included": False,
    }


def _ineligible_payload(item: _CapturedIneligible) -> dict[str, Any]:
    return {
        "project_id": item.project_id,
        "roadmap_id": item.roadmap_id,
        "eligibility": "ineligible_no_approved_revision",
        "revision": None,
        "revision_state": "",
        "content_hash": "",
        "updated_at": "",
        "source_ref": "",
    }


def _capture_read(
    value: object,
    *,
    snapshot: _CapturedSnapshot,
    descriptor: _CapturedDescriptor,
) -> dict[str, Any]:
    if not _exact_keys(value, _READ_KEYS, "invalid_snapshot"):
        _fail("invalid_snapshot")
    for name, maximum in (
        ("schema_id", 128),
        ("owner_ref", 512),
        ("snapshot_digest", 71),
        ("classification", 32),
        ("content_policy", 32),
        ("owner_policy_ref", 256),
    ):
        _exact_text(value.get(name), maximum, "invalid_snapshot")
    raw_descriptor = _capture_descriptor(value.get("descriptor"), snapshot.owner_ref)
    if (
        value.get("schema_id") != _OWNER_READ_SCHEMA
        or value.get("owner_ref") != snapshot.owner_ref
        or value.get("snapshot_digest") != snapshot.snapshot_digest
        or raw_descriptor != descriptor
        or value.get("classification") != "private"
        or value.get("content_policy") != "inline_local"
        or value.get("owner_policy_ref") != _OWNER_POLICY_REF
        or value.get("projection_exact") is not True
        or value.get("raw_source_exact") is not False
        or value.get("raw_source_included") is not False
        or value.get("read_only") is not True
        or value.get("writes_performed") is not False
    ):
        _fail("invalid_snapshot")
    projection = _freeze_json(value.get("projection"))
    if (
        type(projection) is not dict
        or len(projection) != len(_PROJECTION_KEYS)
        or frozenset(projection) != _PROJECTION_KEYS
        or projection.get("schema_id") != _OWNER_PROJECTION_SCHEMA
        or projection.get("project_id") != descriptor.project_id
    ):
        _fail("invalid_snapshot")
    _validate_projection_structure(
        projection,
        expected_project_id=descriptor.project_id,
        expected_roadmap_id=descriptor.roadmap_id,
        expected_revision=descriptor.revision,
        expected_domain_content_hash=descriptor.domain_content_hash,
    )
    canonical = _canonical_bytes(projection)
    if len(canonical) > MAX_PROJECTION_UTF8_BYTES:
        _fail("projection_limit_exceeded")
    return projection


def _freeze_json(value: object) -> Any:
    budget = _PrimitiveBudget()
    return _freeze_json_node(value, budget, depth=0)


def _freeze_json_node(value: object, budget: _PrimitiveBudget, *, depth: int) -> Any:
    if depth > MAX_PROJECTION_DEPTH:
        _fail("projection_limit_exceeded")
    budget.nodes += 1
    if budget.nodes > MAX_PRIMITIVE_NODES:
        _fail("projection_limit_exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("invalid_snapshot")
        return value
    if type(value) is str:
        if len(value) > MAX_PROJECTION_UTF8_BYTES:
            _fail("projection_limit_exceeded")
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            _fail("invalid_snapshot")
        if budget.utf8_bytes + len(encoded) > MAX_PROJECTION_UTF8_BYTES:
            _fail("projection_limit_exceeded")
        budget.utf8_bytes += len(encoded)
        return value
    if type(value) is list:
        if len(value) > MAX_PRIMITIVE_NODES - budget.nodes:
            _fail("projection_limit_exceeded")
        return [
            _freeze_json_node(item, budget, depth=depth + 1)
            for item in value
        ]
    if type(value) is dict:
        if len(value) * 2 > MAX_PRIMITIVE_NODES - budget.nodes:
            _fail("projection_limit_exceeded")
        captured: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or len(key) > 512:
                _fail("invalid_snapshot")
            try:
                encoded = key.encode("utf-8")
            except UnicodeError:
                _fail("invalid_snapshot")
            budget.nodes += 1
            if (
                budget.nodes > MAX_PRIMITIVE_NODES
                or budget.utf8_bytes + len(encoded) > MAX_PROJECTION_UTF8_BYTES
            ):
                _fail("projection_limit_exceeded")
            budget.utf8_bytes += len(encoded)
            captured[key] = _freeze_json_node(item, budget, depth=depth + 1)
        return captured
    _fail("invalid_snapshot")


def _validate_projection_structure(
    projection: dict[str, Any],
    *,
    expected_project_id: str,
    expected_roadmap_id: str,
    expected_revision: int,
    expected_domain_content_hash: str,
) -> None:
    roadmap = projection.get("roadmap")
    if type(roadmap) is not dict or frozenset(roadmap) != _ROADMAP_KEYS:
        _fail("invalid_snapshot")
    if (
        roadmap.get("project_id") != expected_project_id
        or roadmap.get("roadmap_id") != expected_roadmap_id
        or type(roadmap.get("revision")) is not int
        or roadmap.get("revision") != expected_revision
        or roadmap.get("revision_state") != "approved"
        or roadmap.get("content_hash") != expected_domain_content_hash
    ):
        _fail("invalid_snapshot")
    nodes = roadmap.get("nodes")
    edges = roadmap.get("edges")
    gates = roadmap.get("gates")
    if type(nodes) is not list or type(edges) is not list or type(gates) is not list:
        _fail("invalid_snapshot")
    if 2 + len(nodes) + len(gates) > MAX_PLANNING_ENTITIES:
        _fail("graph_limit_exceeded")
    node_ids: set[str] = set()
    dependency_count = 0
    gate_ref_count = 0
    for node in nodes:
        if type(node) is not dict or frozenset(node) != _NODE_KEYS:
            _fail("invalid_snapshot")
        node_id = _identifier(node.get("node_id"), "invalid_snapshot")
        if node_id in node_ids:
            _fail("invalid_snapshot")
        node_ids.add(node_id)
        dependencies = node.get("depends_on")
        gate_ids = node.get("gate_ids")
        if type(dependencies) is not list or type(gate_ids) is not list:
            _fail("invalid_snapshot")
        dependency_count += len(dependencies)
        gate_ref_count += len(gate_ids)
    gate_ids_set: set[str] = set()
    gate_blocks_count = 0
    for gate in gates:
        if type(gate) is not dict or frozenset(gate) != _GATE_KEYS:
            _fail("invalid_snapshot")
        gate_id = _identifier(gate.get("gate_id"), "invalid_snapshot")
        if gate_id in gate_ids_set or type(gate.get("blocks")) is not list:
            _fail("invalid_snapshot")
        gate_ids_set.add(gate_id)
        gate_blocks_count += len(gate["blocks"])
    for node in nodes:
        if any(
            type(ref) is not str or ref not in node_ids
            for ref in node["depends_on"]
        ) or any(
            type(ref) is not str or ref not in gate_ids_set
            for ref in node["gate_ids"]
        ):
            _fail("invalid_snapshot")
    for edge in edges:
        if (
            type(edge) is not dict
            or frozenset(edge) != _EDGE_KEYS
            or type(edge.get("from")) is not str
            or type(edge.get("to")) is not str
            or edge["from"] not in node_ids
            or edge["to"] not in node_ids
            or type(edge.get("kind")) is not str
            or not edge["kind"]
            or len(edge["kind"]) > 80
        ):
            _fail("invalid_snapshot")
    for gate in gates:
        if any(
            type(ref) is not str or ref not in node_ids
            for ref in gate["blocks"]
        ):
            _fail("invalid_snapshot")
    relation_count = (
        1
        + len(nodes)
        + len(gates)
        + dependency_count
        + len(edges)
        + gate_blocks_count
        + gate_ref_count
    )
    if relation_count > MAX_PLANNING_RELATIONS:
        _fail("graph_limit_exceeded")


def _projection_from_occurrence_text(
    projection_text: str,
    evidence: PlanningSourceEvidence,
) -> dict[str, Any]:
    try:
        parsed = json.loads(projection_text)
        projection = _freeze_json(parsed)
        if (
            type(projection) is not dict
            or len(projection) != len(_PROJECTION_KEYS)
            or frozenset(projection) != _PROJECTION_KEYS
            or projection.get("schema_id") != _OWNER_PROJECTION_SCHEMA
            or projection.get("project_id") != evidence.project_id
        ):
            _fail("invalid_snapshot")
        _validate_projection_structure(
            projection,
            expected_project_id=evidence.project_id,
            expected_roadmap_id=evidence.roadmap_id,
            expected_revision=evidence.approved_revision,
            expected_domain_content_hash=evidence.domain_content_hash,
        )
        if _canonical_bytes(projection).decode("utf-8") != projection_text:
            _fail("invalid_snapshot")
        return projection
    except PlanningSourceAdapterError:
        _fail("invalid_snapshot")
    except Exception:
        _fail("invalid_snapshot")


def _descriptor(
    binding: PlanningSourceAuthorityBinding,
    item: _CapturedDescriptor,
    observed_at: str,
) -> PlanningSourceDescriptor:
    source = SourceRecord(
        owner_scope=binding.owner_scope.value,
        source_kind=SourceKind.PLANNING,
        canonical_ref=item.logical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref=PLANNING_SOURCE_ADAPTER_ID,
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref=f"approved:{item.revision}:{item.canonical_projection_hash}",
        content_hash=item.canonical_projection_hash,
        version_observed_at=observed_at,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
    )
    locator = PlanningProjectionLocator(
        logical_ref=item.logical_ref,
        source_id=source.source_id,
        source_version_id=version.source_version_id,
        canonical_projection_hash=item.canonical_projection_hash,
        snapshot_digest=binding.snapshot_digest,
        binding_digest=binding.binding_digest,
    )
    evidence = PlanningSourceEvidence(
        logical_ref=item.logical_ref,
        project_id=item.project_id,
        roadmap_id=item.roadmap_id,
        approved_revision=item.revision,
        domain_content_hash=item.domain_content_hash,
        canonical_projection_hash=item.canonical_projection_hash,
        source_ref=item.source_ref,
        supporting_refs=tuple(ref for ref, _kind, _access in item.supporting_refs),
        owner_ref=binding.owner_ref,
        owner_policy_ref=_OWNER_POLICY_REF,
        snapshot_digest=binding.snapshot_digest,
        binding_digest=binding.binding_digest,
        source_id=source.source_id,
        source_version_id=version.source_version_id,
    )
    return PlanningSourceDescriptor(source, version, locator, evidence)


def _descriptor_by_ref(
    snapshot: _CapturedSnapshot,
    logical_ref: object,
) -> _CapturedDescriptor:
    ref = _logical_ref_value(logical_ref, "invalid_request")
    matches = [item for item in snapshot.descriptors if item.logical_ref == ref]
    if len(matches) != 1:
        _fail("record_not_found")
    return matches[0]


def _graph_records(
    version: SourceVersionRecord,
    projection: dict[str, Any],
    projection_text: str,
) -> tuple[tuple[EntityRecord, ...], tuple[RelationRecord, ...]]:
    roadmap = projection["roadmap"]
    nodes = roadmap["nodes"]
    gates = roadmap["gates"]
    whole_range = TextRangeLocator(0, len(projection_text))
    projection_hash = content_hash(projection_text)

    entities: list[EntityRecord] = []
    by_key: dict[str, EntityRecord] = {}

    def add(key: str, kind: EntityKind, label: str) -> EntityRecord:
        if key in by_key:
            _fail("invalid_snapshot")
        entity = EntityRecord.create(
            version,
            entity_kind=kind,
            natural_key=key,
            locator=whole_range,
            extractor_profile_ref=PLANNING_SOURCE_GRAPH_PROFILE,
            content_hash=projection_hash,
            label=label,
            classification=Classification.PRIVATE,
            content_policy=ContentPolicy.INLINE_LOCAL,
        )
        entities.append(entity)
        by_key[key] = entity
        return entity

    project = add(
        f"project:{projection['project_id']}",
        EntityKind.OTHER,
        projection["project_id"],
    )
    roadmap_entity = add(
        f"roadmap:{roadmap['roadmap_id']}",
        EntityKind.OTHER,
        roadmap["title"],
    )
    node_entities = {
        node["node_id"]: add(
            f"node:{node['node_id']}",
            EntityKind.TASK if node["kind"] == "work" else EntityKind.OTHER,
            node["title"],
        )
        for node in nodes
    }
    gate_entities = {
        gate["gate_id"]: add(
            f"gate:{gate['gate_id']}",
            EntityKind.OTHER,
            gate["title"],
        )
        for gate in gates
    }

    relations: list[RelationRecord] = []

    def relate(
        source_entity: EntityRecord,
        target_entity: EntityRecord,
        kind: RelationKind,
        method_ref: str,
    ) -> None:
        relations.append(
            RelationRecord.create(
                source_entity.ref(),
                target_entity.ref(),
                relation_kind=kind,
                method_ref=method_ref,
                confidence=1.0,
                evidence_refs=(
                    source_entity.evidence_ref(),
                    target_entity.evidence_ref(),
                ),
                classification=Classification.PRIVATE,
            )
        )

    relate(roadmap_entity, project, RelationKind.BELONGS_TO, "planning.roadmap.belongs_to_project")
    for node in nodes:
        entity = node_entities[node["node_id"]]
        relate(entity, roadmap_entity, RelationKind.BELONGS_TO, "planning.node.belongs_to_roadmap")
        for dependency in node["depends_on"]:
            relate(entity, node_entities[dependency], RelationKind.RELATED_TO, "planning.node.depends_on")
        for gate_id in node["gate_ids"]:
            relate(entity, gate_entities[gate_id], RelationKind.REFERENCES, "planning.node.gate_reference")
    for gate in gates:
        gate_entity = gate_entities[gate["gate_id"]]
        relate(gate_entity, roadmap_entity, RelationKind.BELONGS_TO, "planning.gate.belongs_to_roadmap")
        for blocked in gate["blocks"]:
            relate(gate_entity, node_entities[blocked], RelationKind.RELATED_TO, "planning.gate.blocks")
    for edge in roadmap["edges"]:
        relate(
            node_entities[edge["from"]],
            node_entities[edge["to"]],
            RelationKind.RELATED_TO,
            "planning.edge." + edge["kind"],
        )
    return tuple(entities), tuple(relations)


def _evidence_values(
    value: object,
) -> tuple[str, str, str, int, str, str, str, tuple[str, ...], str, str, str, str, str, str, str]:
    if type(value) is not PlanningSourceEvidence:
        _fail("invalid_snapshot")
    logical_ref = _logical_ref_value(value.logical_ref, "invalid_snapshot")
    project = _identifier(value.project_id, "invalid_snapshot")
    roadmap = _identifier(value.roadmap_id, "invalid_snapshot")
    revision = value.approved_revision
    if type(revision) is not int or revision < 1:
        _fail("invalid_snapshot")
    domain_hash = _sha(value.domain_content_hash, "invalid_snapshot")
    projection_hash = _sha(value.canonical_projection_hash, "invalid_snapshot")
    source_ref = _exact_text(value.source_ref, 4_096, "invalid_snapshot")
    supporting = value.supporting_refs
    if (
        type(supporting) is not tuple
        or len(supporting) > MAX_SUPPORTING_REFS
        or any(type(item) is not str or not item or len(item) > 4_096 for item in supporting)
    ):
        _fail("invalid_snapshot")
    owner_ref = _exact_text(value.owner_ref, 512, "invalid_snapshot")
    policy = _exact_text(value.owner_policy_ref, 128, "invalid_snapshot")
    snapshot = _sha(value.snapshot_digest, "invalid_snapshot")
    binding = _sha(value.binding_digest, "invalid_snapshot")
    source_id = _exact_text(value.source_id, 96, "invalid_snapshot")
    version_id = _exact_text(value.source_version_id, 96, "invalid_snapshot")
    schema = _exact_text(value.schema, 128, "invalid_snapshot")
    if (
        logical_ref != _logical_ref(project, roadmap)
        or policy != _OWNER_POLICY_REF
        or schema != PLANNING_SOURCE_EVIDENCE_SCHEMA
        or len(set(supporting)) != len(supporting)
    ):
        _fail("invalid_snapshot")
    return (
        logical_ref,
        project,
        roadmap,
        revision,
        domain_hash,
        projection_hash,
        source_ref,
        tuple(supporting),
        owner_ref,
        policy,
        snapshot,
        binding,
        source_id,
        version_id,
        schema,
    )


def _copy_evidence(value: object) -> PlanningSourceEvidence:
    values = _evidence_values(value)
    return PlanningSourceEvidence(
        logical_ref=values[0],
        project_id=values[1],
        roadmap_id=values[2],
        approved_revision=values[3],
        domain_content_hash=values[4],
        canonical_projection_hash=values[5],
        source_ref=values[6],
        supporting_refs=values[7],
        owner_ref=values[8],
        owner_policy_ref=values[9],
        snapshot_digest=values[10],
        binding_digest=values[11],
        source_id=values[12],
        source_version_id=values[13],
        schema=values[14],
    )


def _locator_values(
    value: object,
) -> tuple[str, str, str, str, str, str, str, str]:
    if type(value) is not PlanningProjectionLocator:
        _fail("invalid_request")
    logical_ref = _logical_ref_value(value.logical_ref, "invalid_request")
    source_id = _exact_text(value.source_id, 96, "invalid_request")
    version_id = _exact_text(value.source_version_id, 96, "invalid_request")
    projection_hash = _sha(value.canonical_projection_hash, "invalid_request")
    snapshot = _sha(value.snapshot_digest, "invalid_request")
    binding = _sha(value.binding_digest, "invalid_request")
    supplied = value.locator_digest
    schema = _exact_text(value.schema, 128, "invalid_request")
    if (
        type(supplied) is not str
        or (supplied and _SHA256_RE.fullmatch(supplied) is None)
        or schema != PLANNING_PROJECTION_LOCATOR_SCHEMA
    ):
        _fail("invalid_request")
    return logical_ref, source_id, version_id, projection_hash, snapshot, binding, supplied, schema


def _copy_locator(value: object) -> PlanningProjectionLocator:
    values = _locator_values(value)
    return PlanningProjectionLocator(
        logical_ref=values[0],
        source_id=values[1],
        source_version_id=values[2],
        canonical_projection_hash=values[3],
        snapshot_digest=values[4],
        binding_digest=values[5],
        locator_digest=values[6],
        schema=values[7],
    )


def _copy_text_range(value: object, code: str) -> TextRangeLocator:
    if (
        type(value) is not TextRangeLocator
        or type(value.start_char) is not int
        or type(value.end_char) is not int
        or value.start_char < 0
        or value.end_char <= value.start_char
    ):
        _fail(code)
    try:
        return TextRangeLocator(value.start_char, value.end_char)
    except Exception:
        _fail(code)


def _copy_request(value: object) -> PlanningCharacterRangeRequest:
    if type(value) is not PlanningCharacterRangeRequest:
        _fail("invalid_request")
    return PlanningCharacterRangeRequest(
        projection_locator=_copy_locator(value.projection_locator),
        text_range=_copy_text_range(value.text_range, "invalid_request"),
        request_digest=value.request_digest,
        schema=value.schema,
    )


def _copy_source(value: object, code: str) -> SourceRecord:
    if type(value) is not SourceRecord:
        _fail(code)
    if type(value.source_kind) is not SourceKind or type(value.classification) is not Classification or type(value.content_policy) is not ContentPolicy:
        _fail(code)
    strings = (
        value.owner_scope,
        value.canonical_ref,
        value.provider_ref,
        value.source_created_at,
        value.first_seen_at,
        value.source_modified_at,
        value.valid_from,
        value.valid_to,
        value.source_id,
    )
    if any(type(item) is not str for item in strings):
        _fail(code)
    try:
        return SourceRecord(
            owner_scope=value.owner_scope,
            source_kind=value.source_kind,
            canonical_ref=value.canonical_ref,
            classification=value.classification,
            content_policy=value.content_policy,
            provider_ref=value.provider_ref,
            source_created_at=value.source_created_at,
            first_seen_at=value.first_seen_at,
            source_modified_at=value.source_modified_at,
            valid_from=value.valid_from,
            valid_to=value.valid_to,
            source_id=value.source_id,
        )
    except Exception:
        _fail(code)


def _copy_policy(value: object, code: str) -> PolicyEvidence:
    if type(value) is not PolicyEvidence or type(value.parent_kind) is not RecordKind or type(value.classification) is not Classification or type(value.content_policy) is not ContentPolicy:
        _fail(code)
    strings = (
        value.parent_id,
        value.source_id,
        value.source_version_id,
        value.owner_scope,
        value.evidence_id,
    )
    if any(type(item) is not str for item in strings):
        _fail(code)
    try:
        return PolicyEvidence(
            value.parent_kind,
            value.parent_id,
            value.source_id,
            value.source_version_id,
            value.owner_scope,
            value.classification,
            value.content_policy,
            value.evidence_id,
        )
    except Exception:
        _fail(code)


def _copy_version(value: object, source: SourceRecord, code: str) -> SourceVersionRecord:
    if type(value) is not SourceVersionRecord or type(value.classification) is not Classification or type(value.content_policy) is not ContentPolicy:
        _fail(code)
    strings = (
        value.source_id,
        value.revision_ref,
        value.content_hash,
        value.provider_ref,
        value.version_observed_at,
        value.owner_scope,
        value.source_created_at,
        value.first_seen_at,
        value.source_modified_at,
        value.indexed_at,
        value.valid_from,
        value.valid_to,
        value.source_version_id,
    )
    if any(type(item) is not str for item in strings):
        _fail(code)
    policy = _copy_policy(value.policy_evidence, code)
    try:
        copied = SourceVersionRecord(
            value.source_id,
            value.revision_ref,
            value.content_hash,
            value.provider_ref,
            value.version_observed_at,
            value.owner_scope,
            value.classification,
            value.content_policy,
            policy,
            value.source_created_at,
            value.first_seen_at,
            value.source_modified_at,
            value.indexed_at,
            value.valid_from,
            value.valid_to,
            value.source_version_id,
        )
    except Exception:
        _fail(code)
    if copied.source_id != source.source_id or copied.owner_scope != source.owner_scope:
        _fail(code)
    return copied


def _copy_chunk(value: object, version: SourceVersionRecord, code: str) -> ChunkRecord:
    if type(value) is not ChunkRecord or type(value.classification) is not Classification or type(value.content_policy) is not ContentPolicy:
        _fail(code)
    locator = _copy_text_range(value.locator, code)
    policy = _copy_policy(value.policy_evidence, code)
    strings = (
        value.source_id,
        value.source_version_id,
        value.extractor_profile_ref,
        value.content_hash,
        value.owner_scope,
        value.indexed_at,
        value.valid_from,
        value.valid_to,
        value.chunk_id,
    )
    if any(type(item) is not str for item in strings) or type(value.content) is not str:
        _fail(code)
    try:
        copied = ChunkRecord(
            value.source_id,
            value.source_version_id,
            locator,
            value.extractor_profile_ref,
            value.content_hash,
            value.owner_scope,
            value.classification,
            value.content_policy,
            policy,
            value.content,
            value.indexed_at,
            value.valid_from,
            value.valid_to,
            value.chunk_id,
        )
    except Exception:
        _fail(code)
    if copied.source_version_id != version.source_version_id:
        _fail(code)
    return copied


def _copy_entity(value: object, version: SourceVersionRecord, code: str) -> EntityRecord:
    if type(value) is not EntityRecord or type(value.entity_kind) is not EntityKind or type(value.classification) is not Classification or type(value.content_policy) is not ContentPolicy:
        _fail(code)
    locator = _copy_text_range(value.locator, code)
    policy = _copy_policy(value.policy_evidence, code)
    strings = (
        value.source_id,
        value.source_version_id,
        value.natural_key,
        value.extractor_profile_ref,
        value.content_hash,
        value.owner_scope,
        value.label,
        value.indexed_at,
        value.valid_from,
        value.valid_to,
        value.entity_id,
    )
    if any(type(item) is not str for item in strings):
        _fail(code)
    try:
        copied = EntityRecord(
            value.source_id,
            value.source_version_id,
            value.entity_kind,
            value.natural_key,
            locator,
            value.extractor_profile_ref,
            value.content_hash,
            value.owner_scope,
            value.classification,
            value.content_policy,
            policy,
            value.label,
            value.indexed_at,
            value.valid_from,
            value.valid_to,
            value.entity_id,
        )
    except Exception:
        _fail(code)
    if copied.source_version_id != version.source_version_id:
        _fail(code)
    return copied


def _copy_record_ref(value: object, code: str) -> RecordRef:
    if type(value) is not RecordRef or type(value.record_kind) is not RecordKind or type(value.record_id) is not str:
        _fail(code)
    try:
        return RecordRef(value.record_kind, value.record_id)
    except Exception:
        _fail(code)


def _copy_evidence_ref(value: object, code: str) -> EvidenceRef:
    if type(value) is not EvidenceRef or type(value.record_kind) is not RecordKind:
        _fail(code)
    strings = (value.record_id, value.source_id, value.source_version_id, value.content_hash)
    if any(type(item) is not str for item in strings):
        _fail(code)
    locator = None if value.locator is None else _copy_text_range(value.locator, code)
    policy = _copy_policy(value.policy_evidence, code)
    try:
        return EvidenceRef(
            value.record_kind,
            value.record_id,
            value.source_id,
            value.source_version_id,
            locator,
            value.content_hash,
            policy,
        )
    except Exception:
        _fail(code)


def _copy_relation(value: object, code: str) -> RelationRecord:
    if type(value) is not RelationRecord or type(value.relation_kind) is not RelationKind or type(value.classification) is not Classification or type(value.content_policy) is not ContentPolicy:
        _fail(code)
    source_ref = _copy_record_ref(value.source_ref, code)
    target_ref = _copy_record_ref(value.target_ref, code)
    if type(value.evidence_refs) is not tuple:
        _fail(code)
    evidence = tuple(_copy_evidence_ref(item, code) for item in value.evidence_refs)
    strings = (
        value.method_ref,
        value.owner_scope,
        value.indexed_at,
        value.valid_from,
        value.valid_to,
        value.relation_id,
    )
    if any(type(item) is not str for item in strings) or type(value.confidence) is not float:
        _fail(code)
    try:
        return RelationRecord(
            source_ref,
            target_ref,
            value.relation_kind,
            value.method_ref,
            value.confidence,
            evidence,
            value.owner_scope,
            value.classification,
            value.content_policy,
            value.indexed_at,
            value.valid_from,
            value.valid_to,
            value.relation_id,
        )
    except Exception:
        _fail(code)


def _copy_descriptor(value: object) -> PlanningSourceDescriptor:
    if type(value) is not PlanningSourceDescriptor:
        _fail("invalid_snapshot")
    return PlanningSourceDescriptor(
        value.source,
        value.source_version,
        value.projection_locator,
        value.evidence,
    )


__all__ = [
    "DEFAULT_MAX_SOURCES",
    "MAX_DISCOVERY_LIMIT",
    "MAX_LOGICAL_ROWS",
    "MAX_PLANNING_CHUNKS",
    "MAX_PLANNING_ENTITIES",
    "MAX_PLANNING_RELATIONS",
    "MAX_PROJECTION_DEPTH",
    "MAX_PROJECTION_UTF8_BYTES",
    "MAX_PRIMITIVE_NODES",
    "MAX_SNAPSHOT_METADATA_BYTES",
    "MAX_SUPPORTING_REFS",
    "PLANNING_CHARACTER_RANGE_REQUEST_SCHEMA",
    "PLANNING_PROJECTION_LOCATOR_SCHEMA",
    "PLANNING_SOURCE_ADAPTER_ID",
    "PLANNING_SOURCE_ADAPTER_VERSION",
    "PLANNING_SOURCE_BINDING_SCHEMA",
    "PLANNING_SOURCE_EVIDENCE_SCHEMA",
    "PLANNING_SOURCE_EXACT_READER_BOUNDARY",
    "PLANNING_SOURCE_EXTRACTOR_PROFILE",
    "PLANNING_SOURCE_GRAPH_PROFILE",
    "PLANNING_UNAVAILABLE_OBSERVATION_SCHEMA",
    "PlanningCharacterRangeRequest",
    "PlanningDiscoveryPage",
    "PlanningExactRead",
    "PlanningProjectionLocator",
    "PlanningSourceAdapter",
    "PlanningSourceAdapterError",
    "PlanningSourceAuthorityBinding",
    "PlanningSourceDescriptor",
    "PlanningSourceEvidence",
    "PlanningSourceOccurrence",
    "PlanningUnavailableObservation",
    "planning_source_capability_manifest",
    "planning_source_registration",
]
