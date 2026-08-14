"""Default-off, generation-fenced USI adapter for Native Knowledge.

Only ``KnowledgeExactRead.content`` may carry source content.  The adapter has
no provider, filesystem, runtime, registry, persistence, or live side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import re
from typing import Any

from src.native_knowledge_store import (
    KnowledgeAccessDenied,
    KnowledgeGenerationMismatch,
    KnowledgeNotFound,
    KnowledgeStoreSnapshot,
    KnowledgeTombstoned,
    KnowledgeVersion,
    NativeKnowledgeStore,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    canonical_json,
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


KNOWLEDGE_SOURCE_ADAPTER_ID = "knowledge.native"
KNOWLEDGE_SOURCE_ADAPTER_VERSION = "v3"
KNOWLEDGE_SOURCE_DOMAIN_ID = "native_knowledge"
KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY = "native_knowledge_store.read_exact_at_generation"
KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE = "knowledge.native.content.v1"
KNOWLEDGE_SOURCE_BINDING_SCHEMA = "odysseus.usi.knowledge_source_authority_binding.v3"
KNOWLEDGE_SOURCE_RECORD_EVIDENCE_SCHEMA = "odysseus.usi.knowledge_record_evidence.v3"
KNOWLEDGE_SOURCE_LOCATOR_SCHEMA = "odysseus.usi.knowledge_record_locator.v3"
KNOWLEDGE_SOURCE_UNAVAILABLE_OBSERVATION_SCHEMA = "odysseus.usi.knowledge_unavailable_observation.v3"
MAX_DISCOVERY_LIMIT = 99
MAX_KNOWLEDGE_RECORDS = 99
_CAPTURE_PROBE_LIMIT = 100
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECORD_REF_RE = re.compile(r"^knowledge:record:[0-9a-f]{64}$")
_USI_SOURCE_RE = re.compile(r"^usi_source_[0-9a-f]{64}$")
_USI_VERSION_RE = re.compile(r"^usi_version_[0-9a-f]{64}$")
_ERROR_CODES = frozenset(
    {
        "access_denied",
        "budget_exceeded",
        "invalid_authority",
        "invalid_request",
        "invalid_snapshot",
        "knowledge_source_adapter_failed",
        "record_not_found",
        "record_still_available",
        "stale_authority",
        "tombstoned",
    }
)


class KnowledgeSourceAdapterError(ValueError):
    """Fresh, bounded, content-free public adapter failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "knowledge_source_adapter_failed"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"KnowledgeSourceAdapterError(code={self.code!r})"


class KnowledgeUnavailableReason(StrEnum):
    ACCESS_CHANGED = "access_changed"
    DELETED = "deleted"
    NOT_FOUND = "not_found"


def _fail(code: str) -> None:
    error = KnowledgeSourceAdapterError(code)
    try:
        raise error
    except KnowledgeSourceAdapterError as caught:
        BaseException.__context__.__set__(caught, None)
        BaseException.__cause__.__set__(caught, None)
        caught.__suppress_context__ = False
        raise


def _require_sha(value: object, code: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        _fail(code)
    return value


def _require_generation(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        _fail(code)
    return value


def _canonical_timestamp_frame(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _canonical_timestamp(value: object) -> str:
    if type(value) is not str:
        _fail("invalid_authority")
    normalized = _canonical_timestamp_frame(value)
    if normalized is None:
        _fail("invalid_authority")
    return normalized


def _digest_frame(value: Any) -> str | None:
    try:
        encoded = canonical_json(value).encode("utf-8", errors="strict")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
    except Exception:
        return None


def _digest(value: Any) -> str:
    result = _digest_frame(value)
    if result is None:
        _fail("invalid_snapshot")
    return result


def _copy_text_frame(value: str) -> str | None:
    try:
        return value.encode("utf-8", errors="strict").decode("utf-8", errors="strict")
    except Exception:
        return None


def _copy_text(value: object, code: str = "invalid_snapshot") -> str:
    if type(value) is not str:
        _fail(code)
    copied = _copy_text_frame(value)
    if copied is None:
        _fail(code)
    return copied


def _owner_scope_frame(value: OwnerScope) -> OwnerScope | None:
    try:
        return OwnerScope(value.value)
    except Exception:
        return None


def _copy_owner_scope(value: object) -> OwnerScope:
    if type(value) is not OwnerScope:
        _fail("invalid_authority")
    copied = _owner_scope_frame(value)
    if copied is None:
        _fail("invalid_authority")
    return copied


def _hash_text_frame(value: str) -> str | None:
    try:
        return hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()
    except Exception:
        return None


def _domain_hash(value: str, code: str) -> str:
    digest = _hash_text_frame(value)
    if digest is None:
        _fail(code)
    return "sha256:" + digest


def _owner_ref(native_owner_id: str) -> str:
    return _domain_hash("odysseus.usi.knowledge.owner.v1\x00" + native_owner_id, "invalid_authority")


def _policy_ref(native_policy: str) -> str:
    return _domain_hash("odysseus.usi.knowledge.policy.v1\x00" + native_policy, "invalid_authority")


def _record_ref(owner_ref: str, knowledge_id: str) -> str:
    digest = _hash_text_frame(
        "odysseus.usi.knowledge.record.v1\x00" + owner_ref + "\x00" + knowledge_id
    )
    if digest is None:
        _fail("invalid_snapshot")
    return "knowledge:record:" + digest


def _binding_payload(binding: "KnowledgeSourceAuthorityBinding") -> dict[str, object]:
    return {
        "adapter_generation": binding.adapter_generation,
        "adapter_id": binding.adapter_id,
        "adapter_version": binding.adapter_version,
        "export_digest": binding.export_digest,
        "observed_at": binding.observed_at,
        "owner_ref": binding.owner_ref,
        "owner_scope": binding.owner_scope.value,
        "policy_evidence_ref": binding.policy_evidence_ref,
        "policy_ref": binding.policy_ref,
        "review_evidence_ref": binding.review_evidence_ref,
        "schema": binding.schema,
        "store_generation": binding.store_generation,
    }


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeSourceAuthorityBinding:
    owner_scope: OwnerScope
    owner_ref: str
    policy_ref: str
    export_digest: str
    store_generation: int
    observed_at: str
    policy_evidence_ref: str
    review_evidence_ref: str
    adapter_id: str
    adapter_version: str
    adapter_generation: str
    binding_digest: str = ""
    schema: str = KNOWLEDGE_SOURCE_BINDING_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeSourceAuthorityBinding:
            _fail("invalid_authority")
        owner_scope = _copy_owner_scope(self.owner_scope)
        for value in (
            self.owner_ref,
            self.policy_ref,
            self.export_digest,
            self.policy_evidence_ref,
            self.review_evidence_ref,
        ):
            _require_sha(value, "invalid_authority")
        _require_generation(self.store_generation, "invalid_authority")
        if type(self.adapter_id) is not str or self.adapter_id != KNOWLEDGE_SOURCE_ADAPTER_ID:
            _fail("invalid_authority")
        if type(self.adapter_version) is not str or self.adapter_version != KNOWLEDGE_SOURCE_ADAPTER_VERSION:
            _fail("invalid_authority")
        if (
            type(self.adapter_generation) is not str
            or self.adapter_generation != knowledge_source_capability_manifest().generation_ref
        ):
            _fail("invalid_authority")
        if type(self.schema) is not str or self.schema != KNOWLEDGE_SOURCE_BINDING_SCHEMA:
            _fail("invalid_authority")
        observed_at = _canonical_timestamp(self.observed_at)
        object.__setattr__(self, "owner_scope", owner_scope)
        object.__setattr__(self, "observed_at", observed_at)
        expected = _digest(_binding_payload(self))
        if self.binding_digest and (
            type(self.binding_digest) is not str or self.binding_digest != expected
        ):
            _fail("invalid_authority")
        object.__setattr__(self, "binding_digest", expected)

    def __repr__(self) -> str:
        return f"KnowledgeSourceAuthorityBinding(binding_digest={self.binding_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeRecordEvidence:
    record_ref: str
    version: int
    version_id: str
    content_hash: str
    export_digest: str
    store_generation: int
    binding_digest: str
    policy_evidence_ref: str
    review_evidence_ref: str
    source_id: str
    source_version_id: str
    relation_count: int = 0
    schema: str = KNOWLEDGE_SOURCE_RECORD_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeRecordEvidence:
            _fail("invalid_snapshot")
        if type(self.record_ref) is not str or not _RECORD_REF_RE.fullmatch(self.record_ref):
            _fail("invalid_snapshot")
        if type(self.version) is not int or self.version < 1:
            _fail("invalid_snapshot")
        _require_generation(self.store_generation, "invalid_snapshot")
        for value in (
            self.version_id,
            self.content_hash,
            self.export_digest,
            self.binding_digest,
            self.policy_evidence_ref,
            self.review_evidence_ref,
        ):
            _require_sha(value, "invalid_snapshot")
        if type(self.source_id) is not str or not _USI_SOURCE_RE.fullmatch(self.source_id):
            _fail("invalid_snapshot")
        if type(self.source_version_id) is not str or not _USI_VERSION_RE.fullmatch(self.source_version_id):
            _fail("invalid_snapshot")
        if type(self.relation_count) is not int or self.relation_count != 0:
            _fail("invalid_snapshot")
        if type(self.schema) is not str or self.schema != KNOWLEDGE_SOURCE_RECORD_EVIDENCE_SCHEMA:
            _fail("invalid_snapshot")

    def __repr__(self) -> str:
        return f"KnowledgeRecordEvidence(record_ref={self.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeRecordLocator:
    record_ref: str
    version: int
    version_id: str
    export_digest: str
    store_generation: int
    binding_digest: str
    text_range: TextRangeLocator
    locator_digest: str = ""
    field_ref: str = "content"
    schema: str = KNOWLEDGE_SOURCE_LOCATOR_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeRecordLocator:
            _fail("invalid_request")
        if type(self.record_ref) is not str or not _RECORD_REF_RE.fullmatch(self.record_ref):
            _fail("invalid_request")
        if type(self.version) is not int or self.version < 1:
            _fail("invalid_request")
        _require_sha(self.version_id, "invalid_request")
        _require_sha(self.export_digest, "invalid_request")
        _require_generation(self.store_generation, "invalid_request")
        _require_sha(self.binding_digest, "invalid_request")
        if type(self.text_range) is not TextRangeLocator or self.text_range.start_char != 0:
            _fail("invalid_request")
        if type(self.field_ref) is not str or self.field_ref != "content":
            _fail("invalid_request")
        if type(self.schema) is not str or self.schema != KNOWLEDGE_SOURCE_LOCATOR_SCHEMA:
            _fail("invalid_request")
        expected = _digest(
            {
                "binding_digest": self.binding_digest,
                "export_digest": self.export_digest,
                "field_ref": self.field_ref,
                "record_ref": self.record_ref,
                "schema": self.schema,
                "store_generation": self.store_generation,
                "text_range": {
                    "end_char": self.text_range.end_char,
                    "start_char": self.text_range.start_char,
                },
                "version": self.version,
                "version_id": self.version_id,
            }
        )
        if self.locator_digest and (
            type(self.locator_digest) is not str or self.locator_digest != expected
        ):
            _fail("invalid_request")
        object.__setattr__(self, "locator_digest", expected)

    def __repr__(self) -> str:
        return f"KnowledgeRecordLocator(locator_digest={self.locator_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeSourceDescriptor:
    source: SourceRecord
    source_version: SourceVersionRecord
    locator: KnowledgeRecordLocator
    evidence: KnowledgeRecordEvidence

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeSourceDescriptor:
            _fail("invalid_snapshot")
        if type(self.source) is not SourceRecord or type(self.source_version) is not SourceVersionRecord:
            _fail("invalid_snapshot")
        if type(self.locator) is not KnowledgeRecordLocator or type(self.evidence) is not KnowledgeRecordEvidence:
            _fail("invalid_snapshot")
        if (
            self.source.canonical_ref != self.evidence.record_ref
            or self.source.canonical_ref != self.locator.record_ref
            or self.source.source_id != self.evidence.source_id
            or self.source_version.source_version_id != self.evidence.source_version_id
            or self.source_version.source_id != self.source.source_id
            or self.source_version.revision_ref != self.locator.version_id
            or self.source_version.content_hash != self.evidence.content_hash
            or self.locator.version != self.evidence.version
            or self.locator.version_id != self.evidence.version_id
            or self.locator.export_digest != self.evidence.export_digest
            or self.locator.store_generation != self.evidence.store_generation
            or self.locator.binding_digest != self.evidence.binding_digest
        ):
            _fail("invalid_snapshot")

    def __repr__(self) -> str:
        return f"KnowledgeSourceDescriptor(record_ref={self.locator.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeSourceOccurrence:
    source: SourceRecord
    source_version: SourceVersionRecord
    chunk: ChunkRecord
    locator: KnowledgeRecordLocator
    evidence: KnowledgeRecordEvidence
    relations: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeSourceOccurrence:
            _fail("invalid_snapshot")
        if (
            type(self.source) is not SourceRecord
            or type(self.source_version) is not SourceVersionRecord
            or type(self.chunk) is not ChunkRecord
            or type(self.locator) is not KnowledgeRecordLocator
            or type(self.evidence) is not KnowledgeRecordEvidence
        ):
            _fail("invalid_snapshot")
        if type(self.relations) is not tuple or self.relations:
            _fail("invalid_snapshot")
        if self.chunk.content is not None or self.chunk.content_policy is not ContentPolicy.REFERENCE_ONLY:
            _fail("invalid_snapshot")
        if (
            self.source.source_id != self.source_version.source_id
            or self.source.source_id != self.evidence.source_id
            or self.chunk.source_id != self.source.source_id
            or self.chunk.source_version_id != self.source_version.source_version_id
            or self.chunk.content_hash != self.evidence.content_hash
            or self.locator.record_ref != self.evidence.record_ref
            or self.locator.store_generation != self.evidence.store_generation
        ):
            _fail("invalid_snapshot")

    def __repr__(self) -> str:
        return f"KnowledgeSourceOccurrence(record_ref={self.locator.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeExactRead:
    record_ref: str
    content: str
    content_hash: str
    locator: KnowledgeRecordLocator
    evidence: KnowledgeRecordEvidence

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeExactRead:
            _fail("invalid_snapshot")
        if type(self.record_ref) is not str or not _RECORD_REF_RE.fullmatch(self.record_ref):
            _fail("invalid_snapshot")
        if type(self.content) is not str or not self.content:
            _fail("invalid_snapshot")
        if _require_sha(self.content_hash, "invalid_snapshot") != content_hash(self.content):
            _fail("invalid_snapshot")
        if type(self.locator) is not KnowledgeRecordLocator or type(self.evidence) is not KnowledgeRecordEvidence:
            _fail("invalid_snapshot")
        if (
            self.locator.record_ref != self.record_ref
            or self.evidence.record_ref != self.record_ref
            or self.evidence.content_hash != self.content_hash
            or self.locator.store_generation != self.evidence.store_generation
        ):
            _fail("invalid_snapshot")

    def __repr__(self) -> str:
        return f"KnowledgeExactRead(record_ref={self.record_ref!r}, content_hash={self.content_hash!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeDiscoveryPage:
    items: tuple[KnowledgeSourceDescriptor, ...]
    next_cursor: int | None
    export_digest: str
    store_generation: int
    binding_digest: str

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeDiscoveryPage:
            _fail("invalid_snapshot")
        if type(self.items) is not tuple or not all(
            type(item) is KnowledgeSourceDescriptor for item in self.items
        ):
            _fail("invalid_snapshot")
        if self.next_cursor is not None and (
            type(self.next_cursor) is not int or self.next_cursor < 0
        ):
            _fail("invalid_snapshot")
        _require_sha(self.export_digest, "invalid_snapshot")
        _require_generation(self.store_generation, "invalid_snapshot")
        _require_sha(self.binding_digest, "invalid_snapshot")
        if any(
            item.locator.export_digest != self.export_digest
            or item.locator.store_generation != self.store_generation
            or item.locator.binding_digest != self.binding_digest
            for item in self.items
        ):
            _fail("invalid_snapshot")

    def __repr__(self) -> str:
        return f"KnowledgeDiscoveryPage(item_count={len(self.items)}, next_cursor={self.next_cursor!r})"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeUnavailableObservation:
    record_ref: str
    reason: KnowledgeUnavailableReason
    prior_version_id: str
    prior_locator_digest: str
    prior_export_digest: str
    observed_export_digest: str
    prior_store_generation: int
    observed_store_generation: int
    binding_digest: str
    observation_digest: str = ""
    schema: str = KNOWLEDGE_SOURCE_UNAVAILABLE_OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not KnowledgeUnavailableObservation:
            _fail("invalid_request")
        if (
            type(self.record_ref) is not str
            or not _RECORD_REF_RE.fullmatch(self.record_ref)
            or type(self.reason) is not KnowledgeUnavailableReason
        ):
            _fail("invalid_request")
        for value in (
            self.prior_version_id,
            self.prior_locator_digest,
            self.prior_export_digest,
            self.observed_export_digest,
            self.binding_digest,
        ):
            _require_sha(value, "invalid_request")
        prior = _require_generation(self.prior_store_generation, "invalid_request")
        observed = _require_generation(self.observed_store_generation, "invalid_request")
        if observed != prior + 1:
            _fail("invalid_request")
        if type(self.schema) is not str or self.schema != KNOWLEDGE_SOURCE_UNAVAILABLE_OBSERVATION_SCHEMA:
            _fail("invalid_request")
        expected = _digest(
            {
                "binding_digest": self.binding_digest,
                "observed_export_digest": self.observed_export_digest,
                "observed_store_generation": self.observed_store_generation,
                "prior_export_digest": self.prior_export_digest,
                "prior_locator_digest": self.prior_locator_digest,
                "prior_store_generation": self.prior_store_generation,
                "prior_version_id": self.prior_version_id,
                "reason": self.reason.value,
                "record_ref": self.record_ref,
                "schema": self.schema,
            }
        )
        if self.observation_digest and (
            type(self.observation_digest) is not str or self.observation_digest != expected
        ):
            _fail("invalid_snapshot")
        object.__setattr__(self, "observation_digest", expected)

    def __repr__(self) -> str:
        return f"KnowledgeUnavailableObservation(record_ref={self.record_ref!r}, reason={self.reason.value!r})"


def knowledge_source_capability_manifest() -> SourceAdapterCapabilityManifest:
    return SourceAdapterCapabilityManifest(
        adapter_id=KNOWLEDGE_SOURCE_ADAPTER_ID,
        adapter_version=KNOWLEDGE_SOURCE_ADAPTER_VERSION,
        domain_id=KNOWLEDGE_SOURCE_DOMAIN_ID,
        source_kind=SourceKind.OTHER,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        classification_ceiling=Classification.SENSITIVE,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        query_capability=QueryCapability.EXACT_READER,
        operations=tuple(SourceAdapterOperation),
        exact_reader_boundary=KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


def knowledge_source_registration() -> SourceAdapterRegistration:
    return SourceAdapterRegistration(knowledge_source_capability_manifest())


@dataclass(frozen=True, slots=True, repr=False)
class _FrozenRecord:
    knowledge_id: str
    owner_id: str
    policy: str
    version: int
    version_id: str
    content: str


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedExport:
    store_generation: int
    records: tuple[_FrozenRecord, ...]
    record_refs: tuple[str, ...]
    digest: str


def _freeze_record(record: object, native_owner_id: str, native_policy: str) -> _FrozenRecord:
    if type(record) is not KnowledgeVersion:
        _fail("invalid_snapshot")
    if any(
        type(value) is not str
        for value in (
            record.knowledge_id,
            record.owner_id,
            record.policy,
            record.version_id,
            record.content,
        )
    ):
        _fail("invalid_snapshot")
    if (
        type(record.version) is not int
        or record.version < 1
        or record.owner_id != native_owner_id
        or record.policy != native_policy
    ):
        _fail("invalid_snapshot")
    _require_sha(record.version_id, "invalid_snapshot")
    validated = _validate_version_frame(record)
    if validated is None:
        _fail("invalid_snapshot")
    return _FrozenRecord(
        knowledge_id=_copy_text(validated.knowledge_id),
        owner_id=_copy_text(validated.owner_id),
        policy=_copy_text(validated.policy),
        version=validated.version,
        version_id=_copy_text(validated.version_id),
        content=_copy_text(validated.content),
    )


def _validate_version_frame(record: KnowledgeVersion) -> KnowledgeVersion | None:
    try:
        return KnowledgeVersion(
            knowledge_id=record.knowledge_id,
            owner_id=record.owner_id,
            policy=record.policy,
            version=record.version,
            version_id=record.version_id,
            content=record.content,
        )
    except Exception:
        return None


def _export_digest(records: tuple[_FrozenRecord, ...], record_refs: tuple[str, ...]) -> str:
    return _digest(
        [
            {
                "content_hash": content_hash(record.content),
                "record_ref": record_ref,
                "version": record.version,
                "version_id": record.version_id,
            }
            for record_ref, record in zip(record_refs, records, strict=True)
        ]
    )


def _capture_frame(
    store: NativeKnowledgeStore,
    native_owner_id: str,
    native_policy: str,
) -> tuple[str | None, object | None]:
    try:
        return None, store.capture(
            owner_id=native_owner_id,
            policy=native_policy,
            limit=_CAPTURE_PROBE_LIMIT,
        )
    except KnowledgeGenerationMismatch:
        return "stale_authority", None
    except KnowledgeAccessDenied:
        return "access_denied", None
    except KnowledgeNotFound:
        return "record_not_found", None
    except KnowledgeTombstoned:
        return "tombstoned", None
    except Exception:
        return "invalid_snapshot", None


def _successor_capture_frame(
    store: NativeKnowledgeStore,
    native_owner_id: str,
    native_policy: str,
    prior_generation: int,
) -> tuple[str | None, object | None]:
    try:
        return None, store.capture_exact_successor(
            owner_id=native_owner_id,
            policy=native_policy,
            limit=_CAPTURE_PROBE_LIMIT,
            prior_generation=prior_generation,
        )
    except KnowledgeGenerationMismatch:
        return "stale_authority", None
    except KnowledgeAccessDenied:
        return "access_denied", None
    except KnowledgeNotFound:
        return "record_not_found", None
    except KnowledgeTombstoned:
        return "tombstoned", None
    except Exception:
        return "invalid_snapshot", None


def _read_frame(
    store: NativeKnowledgeStore,
    native_owner_id: str,
    native_policy: str,
    record: _FrozenRecord,
    expected_generation: int,
) -> tuple[str | None, object | None]:
    try:
        return None, store.read_exact_at_generation(
            owner_id=native_owner_id,
            knowledge_id=record.knowledge_id,
            policy=native_policy,
            version=record.version,
            expected_generation=expected_generation,
        )
    except KnowledgeGenerationMismatch:
        return "stale_authority", None
    except KnowledgeAccessDenied:
        return "access_denied", None
    except KnowledgeNotFound:
        return "record_not_found", None
    except KnowledgeTombstoned:
        return "tombstoned", None
    except Exception:
        return "knowledge_source_adapter_failed", None


def _freeze_snapshot(
    snapshot: object,
    native_owner_id: str,
    native_policy: str,
) -> _CapturedExport:
    if type(snapshot) is not KnowledgeStoreSnapshot:
        _fail("invalid_snapshot")
    generation = _require_generation(snapshot.generation, "invalid_snapshot")
    if (
        type(snapshot.owner_id) is not str
        or type(snapshot.policy) is not str
        or snapshot.owner_id != native_owner_id
        or snapshot.policy != native_policy
        or type(snapshot.records) is not tuple
    ):
        _fail("invalid_snapshot")
    if len(snapshot.records) >= _CAPTURE_PROBE_LIMIT:
        _fail("budget_exceeded")
    owner_ref = _owner_ref(native_owner_id)
    entries: list[tuple[str, _FrozenRecord]] = []
    seen: set[str] = set()
    for native_record in snapshot.records:
        record = _freeze_record(native_record, native_owner_id, native_policy)
        record_ref = _record_ref(owner_ref, record.knowledge_id)
        if record_ref in seen:
            _fail("invalid_snapshot")
        seen.add(record_ref)
        entries.append((record_ref, record))
    entries.sort(key=lambda item: item[0])
    record_refs = tuple(item[0] for item in entries)
    records = tuple(item[1] for item in entries)
    return _CapturedExport(generation, records, record_refs, _export_digest(records, record_refs))


def _capture_store(
    store: NativeKnowledgeStore,
    native_owner_id: str,
    native_policy: str,
) -> _CapturedExport:
    error_code, snapshot = _capture_frame(store, native_owner_id, native_policy)
    if error_code is not None:
        _fail(error_code)
    return _freeze_snapshot(snapshot, native_owner_id, native_policy)


def _capture_successor(
    store: NativeKnowledgeStore,
    native_owner_id: str,
    native_policy: str,
    prior_generation: int,
) -> _CapturedExport:
    error_code, snapshot = _successor_capture_frame(
        store,
        native_owner_id,
        native_policy,
        prior_generation,
    )
    if error_code is not None:
        _fail(error_code)
    return _freeze_snapshot(snapshot, native_owner_id, native_policy)


def _same_snapshot(left: _CapturedExport, right: _CapturedExport) -> bool:
    return (
        left.store_generation == right.store_generation
        and left.digest == right.digest
        and left.record_refs == right.record_refs
        and left.records == right.records
    )


def _record_at(exported: _CapturedExport, index: int) -> _FrozenRecord:
    return exported.records[index]


def _record_by_ref(exported: _CapturedExport, record_ref: object) -> _FrozenRecord:
    if type(record_ref) is not str or not _RECORD_REF_RE.fullmatch(record_ref):
        _fail("invalid_request")
    if record_ref not in exported.record_refs:
        _fail("record_not_found")
    index = exported.record_refs.index(record_ref)
    return _record_at(exported, index)


def create_knowledge_source_authority_binding(
    *,
    owner_scope: OwnerScope,
    store: NativeKnowledgeStore,
    native_owner_id: str,
    native_policy: str,
    review_status: str,
    policy_evidence_ref: str,
    review_evidence_ref: str,
    observed_at: str,
) -> KnowledgeSourceAuthorityBinding:
    owner_scope_copy = _copy_owner_scope(owner_scope)
    if type(store) is not NativeKnowledgeStore:
        _fail("invalid_authority")
    if type(native_owner_id) is not str or type(native_policy) is not str:
        _fail("invalid_authority")
    if type(review_status) is not str or review_status != "accepted":
        _fail("invalid_authority")
    policy_ref_value = _require_sha(policy_evidence_ref, "invalid_authority")
    review_ref_value = _require_sha(review_evidence_ref, "invalid_authority")
    observed_at_value = _canonical_timestamp(observed_at)
    native_owner_copy = _copy_text(native_owner_id, "invalid_authority")
    native_policy_copy = _copy_text(native_policy, "invalid_authority")
    exported = _capture_store(store, native_owner_copy, native_policy_copy)
    manifest = knowledge_source_capability_manifest()
    return KnowledgeSourceAuthorityBinding(
        owner_scope=owner_scope_copy,
        owner_ref=_owner_ref(native_owner_copy),
        policy_ref=_policy_ref(native_policy_copy),
        export_digest=exported.digest,
        store_generation=exported.store_generation,
        observed_at=observed_at_value,
        policy_evidence_ref=policy_ref_value,
        review_evidence_ref=review_ref_value,
        adapter_id=manifest.adapter_id,
        adapter_version=manifest.adapter_version,
        adapter_generation=manifest.generation_ref,
    )


def _copy_binding(binding: object) -> KnowledgeSourceAuthorityBinding:
    if type(binding) is not KnowledgeSourceAuthorityBinding:
        _fail("invalid_authority")
    return KnowledgeSourceAuthorityBinding(
        owner_scope=binding.owner_scope,
        owner_ref=binding.owner_ref,
        policy_ref=binding.policy_ref,
        export_digest=binding.export_digest,
        store_generation=binding.store_generation,
        observed_at=binding.observed_at,
        policy_evidence_ref=binding.policy_evidence_ref,
        review_evidence_ref=binding.review_evidence_ref,
        adapter_id=binding.adapter_id,
        adapter_version=binding.adapter_version,
        adapter_generation=binding.adapter_generation,
        binding_digest=binding.binding_digest,
        schema=binding.schema,
    )


class KnowledgeSourceAdapter:
    __slots__ = (
        "_binding",
        "_store",
        "_native_owner_id",
        "_native_policy",
        "_bound_export",
    )

    def __init__(
        self,
        *,
        binding: KnowledgeSourceAuthorityBinding,
        store: NativeKnowledgeStore,
        native_owner_id: str,
        native_policy: str,
    ) -> None:
        binding_copy = _copy_binding(binding)
        if type(store) is not NativeKnowledgeStore:
            _fail("invalid_authority")
        if type(native_owner_id) is not str or type(native_policy) is not str:
            _fail("invalid_authority")
        native_owner_copy = _copy_text(native_owner_id, "invalid_authority")
        native_policy_copy = _copy_text(native_policy, "invalid_authority")
        exported = _capture_store(store, native_owner_copy, native_policy_copy)
        manifest = knowledge_source_capability_manifest()
        if (
            binding_copy.owner_ref != _owner_ref(native_owner_copy)
            or binding_copy.policy_ref != _policy_ref(native_policy_copy)
            or binding_copy.export_digest != exported.digest
            or binding_copy.store_generation != exported.store_generation
            or binding_copy.adapter_generation != manifest.generation_ref
        ):
            _fail("invalid_authority")
        self._binding = binding_copy
        self._store = store
        self._native_owner_id = native_owner_copy
        self._native_policy = native_policy_copy
        self._bound_export = exported

    @property
    def manifest(self) -> SourceAdapterCapabilityManifest:
        return knowledge_source_capability_manifest()

    def discover(
        self,
        *,
        expected_binding_digest: str,
        expected_export_digest: str,
        expected_store_generation: int,
        cursor: int = 0,
        limit: int = MAX_DISCOVERY_LIMIT,
    ) -> KnowledgeDiscoveryPage:
        if (
            type(cursor) is not int
            or cursor < 0
            or type(limit) is not int
            or not 1 <= limit <= MAX_DISCOVERY_LIMIT
        ):
            _fail("invalid_request")
        binding, exported = self._ordinary_capture(
            expected_binding_digest,
            expected_export_digest,
            expected_store_generation,
        )
        if cursor > len(exported.records):
            _fail("invalid_request")
        selected = exported.records[cursor : cursor + limit]
        items = tuple(
            _descriptor(binding, exported, record, self._native_owner_id, self._native_policy)
            for record in selected
        )
        next_cursor = (
            cursor + len(selected)
            if cursor + len(selected) < len(exported.records)
            else None
        )
        return KnowledgeDiscoveryPage(
            items,
            next_cursor,
            exported.digest,
            exported.store_generation,
            binding.binding_digest,
        )

    def observe_version(
        self,
        record_ref: str,
        *,
        expected_binding_digest: str,
        expected_export_digest: str,
        expected_store_generation: int,
    ) -> KnowledgeSourceDescriptor:
        binding, exported = self._ordinary_capture(
            expected_binding_digest,
            expected_export_digest,
            expected_store_generation,
        )
        record = _record_by_ref(exported, record_ref)
        return _descriptor(
            binding,
            exported,
            record,
            self._native_owner_id,
            self._native_policy,
        )

    def extract(
        self,
        record_ref: str,
        *,
        expected_binding_digest: str,
        expected_export_digest: str,
        expected_store_generation: int,
    ) -> KnowledgeSourceOccurrence:
        binding, exported = self._ordinary_capture(
            expected_binding_digest,
            expected_export_digest,
            expected_store_generation,
        )
        record = _record_by_ref(exported, record_ref)
        descriptor = _descriptor(
            binding,
            exported,
            record,
            self._native_owner_id,
            self._native_policy,
        )
        chunk = ChunkRecord.create(
            descriptor.source_version,
            locator=descriptor.locator.text_range,
            extractor_profile_ref=KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
            content_hash=content_hash(record.content),
            content=None,
            classification=Classification.SENSITIVE,
            content_policy=ContentPolicy.REFERENCE_ONLY,
        )
        return KnowledgeSourceOccurrence(
            descriptor.source,
            descriptor.source_version,
            chunk,
            descriptor.locator,
            descriptor.evidence,
            (),
        )

    def read_exact(
        self,
        locator: KnowledgeRecordLocator,
        *,
        expected_binding_digest: str,
        expected_export_digest: str,
        expected_store_generation: int,
    ) -> KnowledgeExactRead:
        _, exported = self._ordinary_capture(
            expected_binding_digest,
            expected_export_digest,
            expected_store_generation,
        )
        record, descriptor = self._bound_record_and_descriptor(locator)
        error_code, native_exact = _read_frame(
            self._store,
            self._native_owner_id,
            self._native_policy,
            record,
            exported.store_generation,
        )
        if error_code is not None:
            _fail(error_code)
        frozen_exact = _freeze_record(
            native_exact,
            self._native_owner_id,
            self._native_policy,
        )
        if frozen_exact != record:
            _fail("stale_authority")
        content = _copy_text(frozen_exact.content)
        return KnowledgeExactRead(
            record_ref=locator.record_ref,
            content=content,
            content_hash=content_hash(content),
            locator=descriptor.locator,
            evidence=descriptor.evidence,
        )

    def observe_unavailable(
        self,
        locator: KnowledgeRecordLocator,
        *,
        expected_binding_digest: str,
        expected_export_digest: str,
        expected_store_generation: int,
    ) -> KnowledgeUnavailableObservation:
        binding = self._validate_expected_authority(
            expected_binding_digest,
            expected_export_digest,
            expected_store_generation,
        )
        record, descriptor = self._bound_record_and_descriptor(locator)
        observed = _capture_successor(
            self._store,
            self._native_owner_id,
            self._native_policy,
            binding.store_generation,
        )
        if observed.store_generation != binding.store_generation + 1:
            _fail("stale_authority")
        if locator.record_ref in observed.record_refs:
            _fail("record_still_available")
        expected_entries = tuple(
            (record_ref, item)
            for record_ref, item in zip(
                self._bound_export.record_refs,
                self._bound_export.records,
                strict=True,
            )
            if record_ref != locator.record_ref
        )
        expected_refs = tuple(item[0] for item in expected_entries)
        expected_records = tuple(item[1] for item in expected_entries)
        expected_digest = _export_digest(expected_records, expected_refs)
        if (
            observed.record_refs != expected_refs
            or observed.records != expected_records
            or observed.digest != expected_digest
        ):
            _fail("stale_authority")
        error_code, native_exact = _read_frame(
            self._store,
            self._native_owner_id,
            self._native_policy,
            record,
            observed.store_generation,
        )
        if error_code == "tombstoned":
            reason = KnowledgeUnavailableReason.DELETED
        elif error_code == "record_not_found":
            reason = KnowledgeUnavailableReason.NOT_FOUND
        elif error_code is not None:
            _fail(error_code)
        else:
            frozen_exact = _freeze_record(
                native_exact,
                self._native_owner_id,
                self._native_policy,
            )
            if frozen_exact != record:
                _fail("stale_authority")
            reason = KnowledgeUnavailableReason.ACCESS_CHANGED
        return KnowledgeUnavailableObservation(
            record_ref=locator.record_ref,
            reason=reason,
            prior_version_id=record.version_id,
            prior_locator_digest=descriptor.locator.locator_digest,
            prior_export_digest=binding.export_digest,
            observed_export_digest=observed.digest,
            prior_store_generation=binding.store_generation,
            observed_store_generation=observed.store_generation,
            binding_digest=binding.binding_digest,
        )

    def _ordinary_capture(
        self,
        expected_binding_digest: str,
        expected_export_digest: str,
        expected_store_generation: int,
    ) -> tuple[KnowledgeSourceAuthorityBinding, _CapturedExport]:
        binding = self._validate_expected_authority(
            expected_binding_digest,
            expected_export_digest,
            expected_store_generation,
        )
        exported = _capture_store(
            self._store,
            self._native_owner_id,
            self._native_policy,
        )
        if not _same_snapshot(exported, self._bound_export):
            _fail("stale_authority")
        return binding, exported

    def _validate_expected_authority(
        self,
        expected_binding_digest: object,
        expected_export_digest: object,
        expected_store_generation: object,
    ) -> KnowledgeSourceAuthorityBinding:
        if (
            type(expected_binding_digest) is not str
            or type(expected_export_digest) is not str
            or type(expected_store_generation) is not int
            or expected_store_generation < 0
            or expected_binding_digest != self._binding.binding_digest
            or expected_export_digest != self._binding.export_digest
            or expected_store_generation != self._binding.store_generation
        ):
            _fail("stale_authority")
        return self._binding

    def _bound_record_and_descriptor(
        self,
        locator: object,
    ) -> tuple[_FrozenRecord, KnowledgeSourceDescriptor]:
        if type(locator) is not KnowledgeRecordLocator:
            _fail("invalid_request")
        if locator.record_ref not in self._bound_export.record_refs:
            _fail("invalid_request")
        index = self._bound_export.record_refs.index(locator.record_ref)
        record = _record_at(self._bound_export, index)
        descriptor = _descriptor(
            self._binding,
            self._bound_export,
            record,
            self._native_owner_id,
            self._native_policy,
        )
        if locator != descriptor.locator:
            _fail("invalid_request")
        return record, descriptor


def _descriptor(
    binding: KnowledgeSourceAuthorityBinding,
    exported: _CapturedExport,
    record: _FrozenRecord,
    native_owner_id: str,
    native_policy: str,
) -> KnowledgeSourceDescriptor:
    owner_ref = _owner_ref(native_owner_id)
    record_ref = _record_ref(owner_ref, record.knowledge_id)
    source = SourceRecord(
        owner_scope=binding.owner_scope.value,
        source_kind=SourceKind.OTHER,
        canonical_ref=record_ref,
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref=KNOWLEDGE_SOURCE_ADAPTER_ID,
    )
    source_version = SourceVersionRecord.create(
        source,
        revision_ref=record.version_id,
        content_hash=content_hash(record.content),
        version_observed_at=binding.observed_at,
        provider_ref=KNOWLEDGE_SOURCE_ADAPTER_ID,
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
    )
    locator = KnowledgeRecordLocator(
        record_ref=record_ref,
        version=record.version,
        version_id=record.version_id,
        export_digest=exported.digest,
        store_generation=exported.store_generation,
        binding_digest=binding.binding_digest,
        text_range=TextRangeLocator(0, len(record.content)),
    )
    evidence = KnowledgeRecordEvidence(
        record_ref=record_ref,
        version=record.version,
        version_id=record.version_id,
        content_hash=content_hash(record.content),
        export_digest=exported.digest,
        store_generation=exported.store_generation,
        binding_digest=binding.binding_digest,
        policy_evidence_ref=binding.policy_evidence_ref,
        review_evidence_ref=binding.review_evidence_ref,
        source_id=source.source_id,
        source_version_id=source_version.source_version_id,
    )
    return KnowledgeSourceDescriptor(source, source_version, locator, evidence)


__all__ = [
    "KNOWLEDGE_SOURCE_ADAPTER_ID",
    "KNOWLEDGE_SOURCE_ADAPTER_VERSION",
    "KNOWLEDGE_SOURCE_BINDING_SCHEMA",
    "KNOWLEDGE_SOURCE_DOMAIN_ID",
    "KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY",
    "KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE",
    "KNOWLEDGE_SOURCE_LOCATOR_SCHEMA",
    "KNOWLEDGE_SOURCE_RECORD_EVIDENCE_SCHEMA",
    "MAX_DISCOVERY_LIMIT",
    "MAX_KNOWLEDGE_RECORDS",
    "KnowledgeDiscoveryPage",
    "KnowledgeExactRead",
    "KnowledgeRecordEvidence",
    "KnowledgeRecordLocator",
    "KnowledgeSourceAdapter",
    "KnowledgeSourceAdapterError",
    "KnowledgeSourceAuthorityBinding",
    "KnowledgeSourceDescriptor",
    "KnowledgeSourceOccurrence",
    "KnowledgeUnavailableObservation",
    "KnowledgeUnavailableReason",
    "create_knowledge_source_authority_binding",
    "knowledge_source_capability_manifest",
    "knowledge_source_registration",
]
