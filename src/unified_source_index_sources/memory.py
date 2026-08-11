"""Pure, default-off USI adapter for an accepted Memory eligibility snapshot.

The adapter never discovers or reads a Memory file.  Its only source input is
an already captured :class:`MemoryOwnerEligibilitySnapshot`, and every public
operation is pinned to an external, content-addressed authority binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any

from src.memory_owner_eligibility import (
    EligibleMemoryRecord,
    MEMORY_ELIGIBILITY_SCHEMA,
    MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA,
    MemoryOwnerEligibilitySnapshot,
)
from src.unified_source_index_contract import (
    MAX_TEXT_CHARS,
    ChunkRecord,
    Classification,
    ContentPolicy,
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


MEMORY_SOURCE_ADAPTER_ID = "memory.accepted"
MEMORY_SOURCE_ADAPTER_VERSION = "v1"
MEMORY_SOURCE_DOMAIN_ID = "personal_memory"
MEMORY_SOURCE_EXACT_READER_BOUNDARY = "memory.accepted_reader"
MEMORY_SOURCE_EXTRACTOR_PROFILE = "memory.record.text.v1"
MEMORY_SOURCE_BINDING_SCHEMA = "odysseus.usi.memory_source_authority_binding.v1"
MEMORY_RECORD_EVIDENCE_SCHEMA = "odysseus.usi.memory_record_evidence.v1"
MEMORY_RECORD_LOCATOR_SCHEMA = "odysseus.usi.memory_record_field_locator.v1"

DEFAULT_MAX_ADAPTER_RECORDS = 1_000
HARD_MAX_ADAPTER_RECORDS = 10_000
DEFAULT_MAX_ADAPTER_DEPTH = 24
HARD_MAX_ADAPTER_DEPTH = 32
DEFAULT_MAX_ADAPTER_NODES = 100_000
HARD_MAX_ADAPTER_NODES = 250_000
DEFAULT_MAX_TOTAL_TEXT_CHARS = 4_000_000
HARD_MAX_TOTAL_TEXT_CHARS = 8_000_000
DEFAULT_DISCOVERY_LIMIT = 100
MAX_DISCOVERY_LIMIT = 1_000

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECORD_REF_RE = re.compile(r"^memory:record:[0-9a-f]{64}$")
_USI_SOURCE_RE = re.compile(r"^usi_source_[0-9a-f]{64}$")
_USI_VERSION_RE = re.compile(r"^usi_version_[0-9a-f]{64}$")
_REJECTION_CODES = (
    "owner_mismatch",
    "legacy_or_unstamped",
    "invalid_record",
    "invalid_stamp",
    "contradictory_state",
    "inactive",
    "not_accepted",
    "incognito",
    "policy_not_go",
    "review_not_accepted",
)
_ERROR_CODES = frozenset(
    {
        "invalid_binding",
        "invalid_snapshot",
        "stale_authority",
        "invalid_request",
        "record_not_found",
        "record_still_available",
        "memory_source_adapter_failed",
    }
)
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_EPOCH_SECONDS = 253_402_300_799


class MemorySourceAdapterError(ValueError):
    """Content-free public error boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe_code = (
            code
            if type(code) is str and code in _ERROR_CODES
            else "memory_source_adapter_failed"
        )
        self.code = safe_code
        super().__init__(safe_code)

    def __repr__(self) -> str:
        return f"MemorySourceAdapterError(code={self.code!r})"


class MemoryUnavailableReason(StrEnum):
    DELETED = "deleted"
    ACCESS_CHANGED = "access_changed"
    INELIGIBLE = "ineligible"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True, repr=False)
class MemorySourceAuthorityBinding:
    """External opaque-owner pin for exactly one eligibility snapshot."""

    owner_scope: OwnerScope
    owner_ref: str
    source_digest: str
    snapshot_digest: str
    adapter_id: str
    adapter_version: str
    adapter_generation: str
    binding_digest: str = ""
    schema: str = MEMORY_SOURCE_BINDING_SCHEMA

    def __post_init__(self) -> None:
        try:
            values = _binding_values(self)
            expected = _binding_digest(values[:-1])
            supplied = values[-1]
            if supplied and supplied != expected:
                raise MemorySourceAdapterError("invalid_binding")
            object.__setattr__(self, "owner_scope", values[0])
            object.__setattr__(self, "binding_digest", expected)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_binding") from None

    def __repr__(self) -> str:
        return f"MemorySourceAuthorityBinding(binding_digest={self.binding_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemoryRecordEvidence:
    """Content-free proof that one opaque record occurrence came from the pin."""

    record_ref: str
    record_digest: str
    source_digest: str
    snapshot_digest: str
    binding_digest: str
    policy_evidence_ref: str
    review_evidence_ref: str
    source_id: str
    source_version_id: str
    schema: str = MEMORY_RECORD_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        try:
            _evidence_values(self)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_snapshot") from None

    def __repr__(self) -> str:
        return f"MemoryRecordEvidence(record_ref={self.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemoryRecordFieldLocator:
    """Only supported exact-read locator: the whole accepted ``text`` field."""

    record_ref: str
    record_digest: str
    snapshot_digest: str
    binding_digest: str
    text_range: TextRangeLocator
    locator_digest: str = ""
    field_ref: str = "text"
    schema: str = MEMORY_RECORD_LOCATOR_SCHEMA

    def __post_init__(self) -> None:
        try:
            values = _locator_values(self)
            expected = _digest_json(
                {
                    "schema": MEMORY_RECORD_LOCATOR_SCHEMA,
                    "record_ref": values[0],
                    "record_digest": values[1],
                    "snapshot_digest": values[2],
                    "binding_digest": values[3],
                    "field_ref": values[4],
                    "text_range": {"start_char": values[5], "end_char": values[6]},
                }
            )
            supplied = values[7]
            if supplied and supplied != expected:
                raise MemorySourceAdapterError("invalid_request")
            object.__setattr__(self, "text_range", TextRangeLocator(values[5], values[6]))
            object.__setattr__(self, "locator_digest", expected)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_request") from None

    def __repr__(self) -> str:
        return f"MemoryRecordFieldLocator(locator_digest={self.locator_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemorySourceDescriptor:
    source: SourceRecord
    source_version: SourceVersionRecord
    locator: MemoryRecordFieldLocator
    evidence: MemoryRecordEvidence

    def __post_init__(self) -> None:
        try:
            source = self.source
            source_version = self.source_version
            locator = self.locator
            evidence = self.evidence
            if (
                type(source) is not SourceRecord
                or type(source_version) is not SourceVersionRecord
                or type(locator) is not MemoryRecordFieldLocator
                or type(evidence) is not MemoryRecordEvidence
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            detached_source = _detach_contract_record(
                source, SourceRecord, "invalid_snapshot"
            )
            detached_version = _detach_contract_record(
                source_version, SourceVersionRecord, "invalid_snapshot"
            )
            detached_locator = _detach_locator(locator)
            detached_evidence = _detach_evidence(evidence)
            if (
                detached_source.source_kind is not SourceKind.MEMORY
                or detached_source.classification is not Classification.SENSITIVE
                or detached_source.content_policy is not ContentPolicy.INLINE_LOCAL
                or detached_source.provider_ref != MEMORY_SOURCE_ADAPTER_ID
                or detached_version.source_id != detached_source.source_id
                or detached_evidence.source_id != detached_source.source_id
                or detached_evidence.source_version_id
                != detached_version.source_version_id
                or detached_locator.record_ref != detached_evidence.record_ref
                or detached_locator.record_digest != detached_evidence.record_digest
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            object.__setattr__(self, "source", detached_source)
            object.__setattr__(self, "source_version", detached_version)
            object.__setattr__(self, "locator", detached_locator)
            object.__setattr__(self, "evidence", detached_evidence)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_snapshot") from None

    def __repr__(self) -> str:
        return f"MemorySourceDescriptor(record_ref={self.evidence.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemorySourceOccurrence:
    source: SourceRecord
    source_version: SourceVersionRecord
    chunk: ChunkRecord
    locator: MemoryRecordFieldLocator
    evidence: MemoryRecordEvidence

    def __post_init__(self) -> None:
        try:
            source = self.source
            source_version = self.source_version
            chunk = self.chunk
            locator = self.locator
            evidence = self.evidence
            if (
                type(source) is not SourceRecord
                or type(source_version) is not SourceVersionRecord
                or type(chunk) is not ChunkRecord
                or type(locator) is not MemoryRecordFieldLocator
                or type(evidence) is not MemoryRecordEvidence
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            descriptor = MemorySourceDescriptor(
                source,
                source_version,
                locator,
                evidence,
            )
            detached_chunk = _detach_contract_record(
                chunk, ChunkRecord, "invalid_snapshot"
            )
            chunk_locator = detached_chunk.locator
            text_range = descriptor.locator.text_range
            if (
                detached_chunk.source_id != descriptor.source.source_id
                or detached_chunk.source_version_id
                != descriptor.source_version.source_version_id
                or detached_chunk.classification is not Classification.SENSITIVE
                or detached_chunk.content_policy is not ContentPolicy.INLINE_LOCAL
                or type(chunk_locator) is not TextRangeLocator
                or chunk_locator.start_char != text_range.start_char
                or chunk_locator.end_char != text_range.end_char
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            object.__setattr__(self, "source", descriptor.source)
            object.__setattr__(self, "source_version", descriptor.source_version)
            object.__setattr__(self, "chunk", detached_chunk)
            object.__setattr__(self, "locator", descriptor.locator)
            object.__setattr__(self, "evidence", descriptor.evidence)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_snapshot") from None

    def __repr__(self) -> str:
        return f"MemorySourceOccurrence(record_ref={self.evidence.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemoryDiscoveryPage:
    items: tuple[MemorySourceDescriptor, ...]
    next_cursor: int | None
    snapshot_digest: str
    binding_digest: str

    def __post_init__(self) -> None:
        try:
            items = self.items
            next_cursor = self.next_cursor
            snapshot_digest = self.snapshot_digest
            binding_digest = self.binding_digest
            if (
                type(items) is not tuple
                or len(items) > MAX_DISCOVERY_LIMIT
                or any(type(item) is not MemorySourceDescriptor for item in items)
                or (
                    next_cursor is not None
                    and (type(next_cursor) is not int or next_cursor < 0)
                )
                or type(snapshot_digest) is not str
                or not _SHA256_RE.fullmatch(snapshot_digest)
                or type(binding_digest) is not str
                or not _SHA256_RE.fullmatch(binding_digest)
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            detached_items = tuple(_detach_descriptor(item) for item in items)
            object.__setattr__(self, "items", detached_items)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_snapshot") from None

    def __repr__(self) -> str:
        return (
            "MemoryDiscoveryPage("
            f"item_count={len(self.items)}, next_cursor={self.next_cursor!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class MemoryExactRead:
    record_ref: str
    content: str = field(repr=False)
    content_hash: str
    locator: MemoryRecordFieldLocator
    evidence: MemoryRecordEvidence

    def __post_init__(self) -> None:
        try:
            record_ref = self.record_ref
            content = self.content
            supplied_content_hash = self.content_hash
            locator = self.locator
            evidence = self.evidence
            if (
                type(locator) is not MemoryRecordFieldLocator
                or type(evidence) is not MemoryRecordEvidence
                or type(record_ref) is not str
                or type(content) is not str
                or not content
                or len(content) > MAX_TEXT_CHARS
                or type(supplied_content_hash) is not str
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            detached_locator = _detach_locator(locator)
            detached_evidence = _detach_evidence(evidence)
            if (
                record_ref != detached_locator.record_ref
                or record_ref != detached_evidence.record_ref
                or supplied_content_hash != content_hash(content)
            ):
                raise MemorySourceAdapterError("invalid_snapshot")
            object.__setattr__(self, "locator", detached_locator)
            object.__setattr__(self, "evidence", detached_evidence)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_snapshot") from None

    def __repr__(self) -> str:
        return f"MemoryExactRead(record_ref={self.record_ref!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemoryUnavailableObservation:
    record_ref: str
    reason: MemoryUnavailableReason
    snapshot_digest: str
    binding_digest: str
    observation_digest: str = ""

    def __post_init__(self) -> None:
        try:
            record_ref = self.record_ref
            reason = self.reason
            snapshot_digest = self.snapshot_digest
            binding_digest = self.binding_digest
            observation_digest = self.observation_digest
            if (
                type(record_ref) is not str
                or not _RECORD_REF_RE.fullmatch(record_ref)
                or type(reason) is not MemoryUnavailableReason
                or type(snapshot_digest) is not str
                or not _SHA256_RE.fullmatch(snapshot_digest)
                or type(binding_digest) is not str
                or not _SHA256_RE.fullmatch(binding_digest)
                or type(observation_digest) is not str
            ):
                raise MemorySourceAdapterError("invalid_request")
            expected = _digest_json(
                {
                    "schema": "odysseus.usi.memory_unavailable_observation.v1",
                    "record_ref": record_ref,
                    "reason": reason.value,
                    "snapshot_digest": snapshot_digest,
                    "binding_digest": binding_digest,
                }
            )
            if observation_digest and observation_digest != expected:
                raise MemorySourceAdapterError("invalid_request")
            object.__setattr__(self, "observation_digest", expected)
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("invalid_request") from None

    def __repr__(self) -> str:
        return (
            "MemoryUnavailableObservation("
            f"observation_digest={self.observation_digest!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedBinding:
    owner_scope: OwnerScope
    owner_ref: str
    source_digest: str
    snapshot_digest: str
    adapter_generation: str
    binding_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedRecord:
    memory_id: str = field(repr=False)
    text: str = field(repr=False)
    timestamp: int
    record_ref: str
    record_digest: str
    policy_evidence_ref: str
    review_evidence_ref: str


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedSnapshot:
    owner_ref: str
    source_digest: str
    snapshot_digest: str
    total_records: int
    records: tuple[_CapturedRecord, ...]


@dataclass(slots=True)
class _Budget:
    max_depth: int
    max_nodes: int
    max_total_text_chars: int
    nodes: int = 0
    text_chars: int = 0


def memory_source_capability_manifest() -> SourceAdapterCapabilityManifest:
    """Return the deterministic declaration; it never activates the adapter."""

    return SourceAdapterCapabilityManifest(
        adapter_id=MEMORY_SOURCE_ADAPTER_ID,
        adapter_version=MEMORY_SOURCE_ADAPTER_VERSION,
        domain_id=MEMORY_SOURCE_DOMAIN_ID,
        source_kind=SourceKind.MEMORY,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.SENSITIVE,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        query_capability=QueryCapability.EXACT_READER,
        operations=(
            SourceAdapterOperation.DISCOVER,
            SourceAdapterOperation.OBSERVE_VERSION,
            SourceAdapterOperation.EXTRACT,
            SourceAdapterOperation.READ_EXACT,
            SourceAdapterOperation.OBSERVE_UNAVAILABLE,
        ),
        exact_reader_boundary=MEMORY_SOURCE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


def memory_source_registration() -> SourceAdapterRegistration:
    """Return a manifest-only registration with no eager or lazy source read."""

    return SourceAdapterRegistration(memory_source_capability_manifest())


def create_memory_source_authority_binding(
    *,
    owner_scope: OwnerScope,
    snapshot: MemoryOwnerEligibilitySnapshot,
) -> MemorySourceAuthorityBinding:
    """Create non-authorizing evidence that externally pins scope to snapshot."""

    try:
        if type(owner_scope) is not OwnerScope:
            raise MemorySourceAdapterError("invalid_binding")
        captured = _capture_snapshot(
            snapshot,
            max_records=HARD_MAX_ADAPTER_RECORDS,
            max_depth=HARD_MAX_ADAPTER_DEPTH,
            max_nodes=HARD_MAX_ADAPTER_NODES,
            max_total_text_chars=HARD_MAX_TOTAL_TEXT_CHARS,
        )
        manifest = memory_source_capability_manifest()
        return MemorySourceAuthorityBinding(
            owner_scope=OwnerScope(owner_scope.value),
            owner_ref=captured.owner_ref,
            source_digest=captured.source_digest,
            snapshot_digest=captured.snapshot_digest,
            adapter_id=manifest.adapter_id,
            adapter_version=manifest.adapter_version,
            adapter_generation=manifest.generation_ref,
        )
    except MemorySourceAdapterError:
        raise
    except Exception:
        raise MemorySourceAdapterError("memory_source_adapter_failed") from None


class MemorySourceAdapter:
    """Stateless operations over one caller-captured, externally pinned snapshot."""

    __slots__ = (
        "_binding",
        "_snapshot",
        "_initial_binding_digest",
        "_initial_snapshot_digest",
        "_max_records",
        "_max_depth",
        "_max_nodes",
        "_max_total_text_chars",
    )

    def __init__(
        self,
        *,
        binding: MemorySourceAuthorityBinding,
        snapshot: MemoryOwnerEligibilitySnapshot,
        max_records: int = DEFAULT_MAX_ADAPTER_RECORDS,
        max_depth: int = DEFAULT_MAX_ADAPTER_DEPTH,
        max_nodes: int = DEFAULT_MAX_ADAPTER_NODES,
        max_total_text_chars: int = DEFAULT_MAX_TOTAL_TEXT_CHARS,
    ) -> None:
        try:
            _validate_limit(max_records, 1, HARD_MAX_ADAPTER_RECORDS)
            _validate_limit(max_depth, 3, HARD_MAX_ADAPTER_DEPTH)
            _validate_limit(max_nodes, 1, HARD_MAX_ADAPTER_NODES)
            _validate_limit(max_total_text_chars, 1, HARD_MAX_TOTAL_TEXT_CHARS)
            captured_binding = _capture_binding(binding)
            captured_snapshot = _capture_snapshot(
                snapshot,
                max_records=max_records,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_total_text_chars=max_total_text_chars,
            )
            _assert_binding_snapshot(captured_binding, captured_snapshot)
            self._binding = binding
            self._snapshot = snapshot
            self._initial_binding_digest = captured_binding.binding_digest
            self._initial_snapshot_digest = captured_snapshot.snapshot_digest
            self._max_records = max_records
            self._max_depth = max_depth
            self._max_nodes = max_nodes
            self._max_total_text_chars = max_total_text_chars
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("memory_source_adapter_failed") from None

    @property
    def manifest(self) -> SourceAdapterCapabilityManifest:
        return memory_source_capability_manifest()

    def discover(
        self,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
        cursor: int = 0,
        limit: int = DEFAULT_DISCOVERY_LIMIT,
    ) -> MemoryDiscoveryPage:
        try:
            if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= MAX_DISCOVERY_LIMIT:
                raise MemorySourceAdapterError("invalid_request")
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            if cursor > len(snapshot.records):
                raise MemorySourceAdapterError("invalid_request")
            selected = snapshot.records[cursor : cursor + limit]
            items = tuple(_descriptor(binding, snapshot, record) for record in selected)
            next_offset = cursor + len(selected)
            next_cursor = next_offset if next_offset < len(snapshot.records) else None
            return MemoryDiscoveryPage(
                items,
                next_cursor,
                snapshot.snapshot_digest,
                binding.binding_digest,
            )
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("memory_source_adapter_failed") from None

    def observe_version(
        self,
        record_ref: str,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> MemorySourceDescriptor:
        try:
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            return _descriptor(binding, snapshot, _record_by_ref(snapshot, record_ref))
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("memory_source_adapter_failed") from None

    def extract(
        self,
        record_ref: str,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> MemorySourceOccurrence:
        try:
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            record = _record_by_ref(snapshot, record_ref)
            descriptor = _descriptor(binding, snapshot, record)
            chunk = ChunkRecord.create(
                descriptor.source_version,
                locator=descriptor.locator.text_range,
                extractor_profile_ref=MEMORY_SOURCE_EXTRACTOR_PROFILE,
                content_hash=content_hash(record.text),
                content=record.text,
                classification=Classification.SENSITIVE,
                content_policy=ContentPolicy.INLINE_LOCAL,
            )
            return MemorySourceOccurrence(
                descriptor.source,
                descriptor.source_version,
                chunk,
                descriptor.locator,
                descriptor.evidence,
            )
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("memory_source_adapter_failed") from None

    def read_exact(
        self,
        locator: MemoryRecordFieldLocator,
        *,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> MemoryExactRead:
        try:
            if type(locator) is not MemoryRecordFieldLocator:
                raise MemorySourceAdapterError("invalid_request")
            supplied_locator = _locator_values(locator)
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            record = _record_by_ref(snapshot, supplied_locator[0])
            descriptor = _descriptor(binding, snapshot, record)
            if supplied_locator != _locator_values(descriptor.locator):
                raise MemorySourceAdapterError("stale_authority")
            return MemoryExactRead(
                record_ref=record.record_ref,
                content=record.text,
                content_hash=content_hash(record.text),
                locator=descriptor.locator,
                evidence=descriptor.evidence,
            )
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("memory_source_adapter_failed") from None

    def observe_unavailable(
        self,
        record_ref: str,
        *,
        reason: MemoryUnavailableReason,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> MemoryUnavailableObservation:
        try:
            if type(record_ref) is not str or not _RECORD_REF_RE.fullmatch(record_ref) or type(reason) is not MemoryUnavailableReason:
                raise MemorySourceAdapterError("invalid_request")
            binding, snapshot = self._operation_capture(
                expected_binding_digest,
                expected_snapshot_digest,
            )
            if any(record.record_ref == record_ref for record in snapshot.records):
                raise MemorySourceAdapterError("record_still_available")
            return MemoryUnavailableObservation(
                record_ref,
                reason,
                snapshot.snapshot_digest,
                binding.binding_digest,
            )
        except MemorySourceAdapterError:
            raise
        except Exception:
            raise MemorySourceAdapterError("memory_source_adapter_failed") from None

    def _operation_capture(
        self,
        expected_binding_digest: str,
        expected_snapshot_digest: str,
    ) -> tuple[_CapturedBinding, _CapturedSnapshot]:
        if (
            type(expected_binding_digest) is not str
            or not _SHA256_RE.fullmatch(expected_binding_digest)
            or type(expected_snapshot_digest) is not str
            or not _SHA256_RE.fullmatch(expected_snapshot_digest)
        ):
            raise MemorySourceAdapterError("invalid_request")
        binding_object = self._binding
        snapshot_object = self._snapshot
        binding = _capture_binding(binding_object)
        snapshot = _capture_snapshot(
            snapshot_object,
            max_records=self._max_records,
            max_depth=self._max_depth,
            max_nodes=self._max_nodes,
            max_total_text_chars=self._max_total_text_chars,
        )
        if self._binding is not binding_object or self._snapshot is not snapshot_object:
            raise MemorySourceAdapterError("stale_authority")
        if (
            binding.binding_digest != self._initial_binding_digest
            or snapshot.snapshot_digest != self._initial_snapshot_digest
            or binding.binding_digest != expected_binding_digest
            or snapshot.snapshot_digest != expected_snapshot_digest
        ):
            raise MemorySourceAdapterError("stale_authority")
        _assert_binding_snapshot(binding, snapshot)
        return binding, snapshot


def _detach_contract_record(
    value: object,
    expected_type: type[SourceRecord] | type[SourceVersionRecord] | type[ChunkRecord],
    error_code: str,
) -> SourceRecord | SourceVersionRecord | ChunkRecord:
    if type(value) is not expected_type:
        raise MemorySourceAdapterError(error_code)
    try:
        serialized = value.to_json()
    except Exception:
        raise MemorySourceAdapterError(error_code) from None
    if type(serialized) is not str:
        raise MemorySourceAdapterError(error_code)
    try:
        detached = expected_type.from_json(serialized)
    except Exception:
        raise MemorySourceAdapterError(error_code) from None
    if type(detached) is not expected_type:
        raise MemorySourceAdapterError(error_code)
    return detached


def _evidence_values(evidence: object) -> tuple[str, ...]:
    if type(evidence) is not MemoryRecordEvidence:
        raise MemorySourceAdapterError("invalid_snapshot")
    record_ref = evidence.record_ref
    record_digest = evidence.record_digest
    source_digest = evidence.source_digest
    snapshot_digest = evidence.snapshot_digest
    binding_digest = evidence.binding_digest
    policy_evidence_ref = evidence.policy_evidence_ref
    review_evidence_ref = evidence.review_evidence_ref
    source_id = evidence.source_id
    source_version_id = evidence.source_version_id
    schema = evidence.schema
    if (
        type(schema) is not str
        or schema != MEMORY_RECORD_EVIDENCE_SCHEMA
        or type(record_ref) is not str
        or not _RECORD_REF_RE.fullmatch(record_ref)
        or any(
            type(value) is not str or not _SHA256_RE.fullmatch(value)
            for value in (
                record_digest,
                source_digest,
                snapshot_digest,
                binding_digest,
                policy_evidence_ref,
                review_evidence_ref,
            )
        )
        or type(source_id) is not str
        or not _USI_SOURCE_RE.fullmatch(source_id)
        or type(source_version_id) is not str
        or not _USI_VERSION_RE.fullmatch(source_version_id)
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    return (
        record_ref,
        record_digest,
        source_digest,
        snapshot_digest,
        binding_digest,
        policy_evidence_ref,
        review_evidence_ref,
        source_id,
        source_version_id,
        schema,
    )


def _detach_evidence(evidence: object) -> MemoryRecordEvidence:
    values = _evidence_values(evidence)
    return MemoryRecordEvidence(*values[:9], schema=values[9])


def _detach_locator(locator: object) -> MemoryRecordFieldLocator:
    values = _locator_values(locator)
    return MemoryRecordFieldLocator(
        record_ref=values[0],
        record_digest=values[1],
        snapshot_digest=values[2],
        binding_digest=values[3],
        field_ref=values[4],
        text_range=TextRangeLocator(values[5], values[6]),
        locator_digest=values[7],
    )


def _detach_descriptor(descriptor: object) -> MemorySourceDescriptor:
    if type(descriptor) is not MemorySourceDescriptor:
        raise MemorySourceAdapterError("invalid_snapshot")
    source = descriptor.source
    source_version = descriptor.source_version
    locator = descriptor.locator
    evidence = descriptor.evidence
    if (
        type(source) is not SourceRecord
        or type(source_version) is not SourceVersionRecord
        or type(locator) is not MemoryRecordFieldLocator
        or type(evidence) is not MemoryRecordEvidence
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    return MemorySourceDescriptor(source, source_version, locator, evidence)


def _validate_limit(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MemorySourceAdapterError("invalid_request")


def _binding_values(binding: object) -> tuple[OwnerScope, str, str, str, str, str, str, str]:
    if type(binding) is not MemorySourceAuthorityBinding:
        raise MemorySourceAdapterError("invalid_binding")
    owner_scope = binding.owner_scope
    owner_ref = binding.owner_ref
    source_digest = binding.source_digest
    snapshot_digest = binding.snapshot_digest
    adapter_id = binding.adapter_id
    adapter_version = binding.adapter_version
    adapter_generation = binding.adapter_generation
    binding_digest = binding.binding_digest
    schema = binding.schema
    if type(owner_scope) is not OwnerScope:
        raise MemorySourceAdapterError("invalid_binding")
    owner_scope_value = owner_scope.value
    if (
        type(owner_scope_value) is not str
        or type(owner_ref) is not str
        or not _SHA256_RE.fullmatch(owner_ref)
        or type(source_digest) is not str
        or not _SHA256_RE.fullmatch(source_digest)
        or type(snapshot_digest) is not str
        or not _SHA256_RE.fullmatch(snapshot_digest)
        or type(adapter_id) is not str
        or adapter_id != MEMORY_SOURCE_ADAPTER_ID
        or type(adapter_version) is not str
        or adapter_version != MEMORY_SOURCE_ADAPTER_VERSION
        or type(adapter_generation) is not str
        or adapter_generation != memory_source_capability_manifest().generation_ref
        or type(binding_digest) is not str
        or (binding_digest and not _SHA256_RE.fullmatch(binding_digest))
        or type(schema) is not str
        or schema != MEMORY_SOURCE_BINDING_SCHEMA
    ):
        raise MemorySourceAdapterError("invalid_binding")
    try:
        detached_scope = OwnerScope(owner_scope_value)
    except Exception:
        raise MemorySourceAdapterError("invalid_binding") from None
    return (
        detached_scope,
        owner_ref,
        source_digest,
        snapshot_digest,
        adapter_id,
        adapter_version,
        adapter_generation,
        binding_digest,
    )


def _binding_digest(values: tuple[object, ...]) -> str:
    if type(values) is not tuple or len(values) != 7 or type(values[0]) is not OwnerScope:
        raise MemorySourceAdapterError("invalid_binding")
    return _digest_json(
        {
            "schema": MEMORY_SOURCE_BINDING_SCHEMA,
            "owner_scope": values[0].value,
            "owner_ref": values[1],
            "source_digest": values[2],
            "snapshot_digest": values[3],
            "adapter_id": values[4],
            "adapter_version": values[5],
            "adapter_generation": values[6],
        }
    )


def _capture_binding(binding: object) -> _CapturedBinding:
    values = _binding_values(binding)
    expected = _binding_digest(values[:-1])
    if values[-1] != expected:
        raise MemorySourceAdapterError("invalid_binding")
    after = _binding_values(binding)
    if after != values:
        raise MemorySourceAdapterError("stale_authority")
    return _CapturedBinding(
        owner_scope=values[0],
        owner_ref=values[1],
        source_digest=values[2],
        snapshot_digest=values[3],
        adapter_generation=values[6],
        binding_digest=values[7],
    )


def _capture_snapshot(
    snapshot: object,
    *,
    max_records: int,
    max_depth: int,
    max_nodes: int,
    max_total_text_chars: int,
) -> _CapturedSnapshot:
    _validate_limit(max_records, 1, HARD_MAX_ADAPTER_RECORDS)
    _validate_limit(max_depth, 3, HARD_MAX_ADAPTER_DEPTH)
    _validate_limit(max_nodes, 1, HARD_MAX_ADAPTER_NODES)
    _validate_limit(max_total_text_chars, 1, HARD_MAX_TOTAL_TEXT_CHARS)
    if type(snapshot) is not MemoryOwnerEligibilitySnapshot:
        raise MemorySourceAdapterError("invalid_snapshot")
    before = _snapshot_surface(snapshot)
    owner_ref, source_digest, snapshot_digest, total_records, rejection_counts, records = before
    if len(records) > max_records:
        raise MemorySourceAdapterError("invalid_snapshot")
    budget = _Budget(max_depth, max_nodes, max_total_text_chars)
    captured_records = tuple(
        _capture_record(record, owner_ref=owner_ref, budget=budget)
        for record in records
    )
    after = _snapshot_surface(snapshot)
    if (
        after[:4] != before[:4]
        or after[4] is not rejection_counts
        or after[5] is not records
    ):
        raise MemorySourceAdapterError("stale_authority")
    if tuple((item.memory_id, item.record_digest) for item in captured_records) != tuple(
        sorted((item.memory_id, item.record_digest) for item in captured_records)
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    if len({item.record_ref for item in captured_records}) != len(captured_records):
        raise MemorySourceAdapterError("invalid_snapshot")
    rejection_dict = _rejection_dict(rejection_counts)
    if len(captured_records) + sum(rejection_dict.values()) != total_records:
        raise MemorySourceAdapterError("invalid_snapshot")
    expected_snapshot_digest = _digest_json(
        {
            "schema": MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA,
            "eligibility_schema": MEMORY_ELIGIBILITY_SCHEMA,
            "source_digest": source_digest,
            "owner_ref": owner_ref,
            "total_records": total_records,
            "record_digests": [item.record_digest for item in captured_records],
            "rejection_counts": rejection_dict,
        }
    )
    if snapshot_digest != expected_snapshot_digest:
        raise MemorySourceAdapterError("invalid_snapshot")
    return _CapturedSnapshot(
        owner_ref,
        source_digest,
        snapshot_digest,
        total_records,
        captured_records,
    )


def _snapshot_surface(snapshot: MemoryOwnerEligibilitySnapshot) -> tuple[object, ...]:
    owner_ref = snapshot.owner_ref
    source_digest = snapshot.source_digest
    snapshot_digest = snapshot.snapshot_digest
    total_records = snapshot.total_records
    rejection_counts = snapshot.rejection_counts
    records = snapshot.eligible_records
    if (
        type(snapshot.schema) is not str
        or snapshot.schema != MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA
        or type(owner_ref) is not str
        or not _SHA256_RE.fullmatch(owner_ref)
        or type(source_digest) is not str
        or not _SHA256_RE.fullmatch(source_digest)
        or type(snapshot_digest) is not str
        or not _SHA256_RE.fullmatch(snapshot_digest)
        or type(total_records) is not int
        or total_records < 0
        or type(rejection_counts) is not tuple
        or type(records) is not tuple
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    return owner_ref, source_digest, snapshot_digest, total_records, rejection_counts, records


def _rejection_dict(value: tuple[object, ...]) -> dict[str, int]:
    if len(value) != len(_REJECTION_CODES):
        raise MemorySourceAdapterError("invalid_snapshot")
    result: dict[str, int] = {}
    for position, item in enumerate(value):
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or item[0] != _REJECTION_CODES[position]
            or type(item[1]) is not int
            or item[1] < 0
        ):
            raise MemorySourceAdapterError("invalid_snapshot")
        result[item[0]] = item[1]
    return result


def _capture_record(
    record: object,
    *,
    owner_ref: str,
    budget: _Budget,
) -> _CapturedRecord:
    if type(record) is not EligibleMemoryRecord:
        raise MemorySourceAdapterError("invalid_snapshot")
    before = _record_surface(record)
    frozen_record, record_digest, source_status, acceptance_status, policy_status, review_status = before
    detached = _detach_frozen(frozen_record, budget=budget, depth=1)
    after = _record_surface(record)
    if after[0] is not frozen_record or after[1:] != before[1:]:
        raise MemorySourceAdapterError("stale_authority")
    if type(detached) is not dict or _digest_json(detached) != record_digest:
        raise MemorySourceAdapterError("invalid_snapshot")
    if (
        source_status != "active"
        or acceptance_status != "accepted"
        or policy_status != "go"
        or review_status not in {"accepted", "not_required"}
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    memory_id = detached.get("id")
    owner = detached.get("owner")
    text = detached.get("text")
    timestamp = detached.get("timestamp")
    metadata = detached.get("metadata")
    if (
        type(memory_id) is not str
        or not memory_id
        or type(owner) is not str
        or not owner
        or _digest_text(owner) != owner_ref
        or type(text) is not str
        or not text
        or len(text) > MAX_TEXT_CHARS
        or type(timestamp) is not int
        or not 0 <= timestamp <= _MAX_EPOCH_SECONDS
        or type(metadata) is not dict
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    stamp = metadata.get("memory_eligibility")
    if type(stamp) is not dict:
        raise MemorySourceAdapterError("invalid_snapshot")
    policy_ref = stamp.get("policy_evidence_ref")
    review_ref = stamp.get("review_evidence_ref")
    if (
        type(stamp.get("schema")) is not str
        or stamp["schema"] != MEMORY_ELIGIBILITY_SCHEMA
        or stamp.get("source_status") != "active"
        or stamp.get("acceptance_status") != "accepted"
        or stamp.get("incognito") is not False
        or stamp.get("policy_status") != "go"
        or stamp.get("review_status") not in {"accepted", "not_required"}
        or type(policy_ref) is not str
        or not _SHA256_RE.fullmatch(policy_ref)
        or type(review_ref) is not str
        or not _SHA256_RE.fullmatch(review_ref)
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    record_ref = "memory:record:" + _digest_json(
        {"schema": "odysseus.usi.memory_record_ref.v1", "owner_ref": owner_ref, "memory_id": memory_id}
    ).removeprefix("sha256:")
    return _CapturedRecord(
        memory_id,
        text,
        timestamp,
        record_ref,
        record_digest,
        policy_ref,
        review_ref,
    )


def _record_surface(record: EligibleMemoryRecord) -> tuple[object, ...]:
    frozen_record = record.record
    record_digest = record.record_digest
    source_status = record.source_status
    acceptance_status = record.acceptance_status
    policy_status = record.policy_status
    review_status = record.review_status
    if (
        type(frozen_record) is not _MAPPING_PROXY_TYPE
        or any(
            type(value) is not str
            for value in (
                record_digest,
                source_status,
                acceptance_status,
                policy_status,
                review_status,
            )
        )
        or not _SHA256_RE.fullmatch(record_digest)
    ):
        raise MemorySourceAdapterError("invalid_snapshot")
    return (
        frozen_record,
        record_digest,
        source_status,
        acceptance_status,
        policy_status,
        review_status,
    )


def _detach_frozen(value: Any, *, budget: _Budget, depth: int) -> Any:
    budget.nodes += 1
    if budget.nodes > budget.max_nodes or depth > budget.max_depth:
        raise MemorySourceAdapterError("invalid_snapshot")
    value_type = type(value)
    if value_type is _MAPPING_PROXY_TYPE:
        result: dict[str, Any] = {}
        keys = tuple(value.keys())
        budget.nodes += len(keys)
        if budget.nodes > budget.max_nodes:
            raise MemorySourceAdapterError("invalid_snapshot")
        for key in keys:
            if type(key) is not str or key in result:
                raise MemorySourceAdapterError("invalid_snapshot")
            budget.text_chars += len(key)
            if budget.text_chars > budget.max_total_text_chars:
                raise MemorySourceAdapterError("invalid_snapshot")
            result[key] = _detach_frozen(value[key], budget=budget, depth=depth + 1)
        return result
    if value_type is tuple:
        return [_detach_frozen(item, budget=budget, depth=depth + 1) for item in value]
    if value_type is str:
        budget.text_chars += len(value)
        if budget.text_chars > budget.max_total_text_chars:
            raise MemorySourceAdapterError("invalid_snapshot")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise MemorySourceAdapterError("invalid_snapshot") from None
        return value
    if value_type is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise MemorySourceAdapterError("invalid_snapshot")
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise MemorySourceAdapterError("invalid_snapshot")
        return value
    if value_type in {bool, type(None)}:
        return value
    raise MemorySourceAdapterError("invalid_snapshot")


def _assert_binding_snapshot(
    binding: _CapturedBinding,
    snapshot: _CapturedSnapshot,
) -> None:
    if (
        binding.owner_ref != snapshot.owner_ref
        or binding.source_digest != snapshot.source_digest
        or binding.snapshot_digest != snapshot.snapshot_digest
        or binding.adapter_generation != memory_source_capability_manifest().generation_ref
    ):
        raise MemorySourceAdapterError("invalid_binding")


def _record_by_ref(snapshot: _CapturedSnapshot, record_ref: object) -> _CapturedRecord:
    if type(record_ref) is not str or not _RECORD_REF_RE.fullmatch(record_ref):
        raise MemorySourceAdapterError("invalid_request")
    matches = tuple(item for item in snapshot.records if item.record_ref == record_ref)
    if len(matches) != 1:
        raise MemorySourceAdapterError("record_not_found")
    return matches[0]


def _descriptor(
    binding: _CapturedBinding,
    snapshot: _CapturedSnapshot,
    record: _CapturedRecord,
) -> MemorySourceDescriptor:
    observed_at = _timestamp_from_epoch(record.timestamp)
    source = SourceRecord(
        owner_scope=binding.owner_scope.value,
        source_kind=SourceKind.MEMORY,
        canonical_ref=record.record_ref,
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref=MEMORY_SOURCE_ADAPTER_ID,
        source_created_at=observed_at,
    )
    source_version = SourceVersionRecord.create(
        source,
        revision_ref=record.record_digest,
        content_hash=content_hash(record.text),
        version_observed_at=observed_at,
        provider_ref=MEMORY_SOURCE_ADAPTER_ID,
        classification=Classification.SENSITIVE,
        content_policy=ContentPolicy.INLINE_LOCAL,
    )
    text_range = TextRangeLocator(0, len(record.text))
    locator = MemoryRecordFieldLocator(
        record_ref=record.record_ref,
        record_digest=record.record_digest,
        snapshot_digest=snapshot.snapshot_digest,
        binding_digest=binding.binding_digest,
        text_range=text_range,
    )
    evidence = MemoryRecordEvidence(
        record_ref=record.record_ref,
        record_digest=record.record_digest,
        source_digest=snapshot.source_digest,
        snapshot_digest=snapshot.snapshot_digest,
        binding_digest=binding.binding_digest,
        policy_evidence_ref=record.policy_evidence_ref,
        review_evidence_ref=record.review_evidence_ref,
        source_id=source.source_id,
        source_version_id=source_version.source_version_id,
    )
    return MemorySourceDescriptor(source, source_version, locator, evidence)


def _locator_values(locator: object) -> tuple[str, str, str, str, str, int, int, str]:
    if type(locator) is not MemoryRecordFieldLocator:
        raise MemorySourceAdapterError("invalid_request")
    record_ref = locator.record_ref
    record_digest = locator.record_digest
    snapshot_digest = locator.snapshot_digest
    binding_digest = locator.binding_digest
    field_ref = locator.field_ref
    text_range = locator.text_range
    locator_digest = locator.locator_digest
    schema = locator.schema
    if type(text_range) is not TextRangeLocator:
        raise MemorySourceAdapterError("invalid_request")
    start_char = text_range.start_char
    end_char = text_range.end_char
    if (
        type(record_ref) is not str
        or not _RECORD_REF_RE.fullmatch(record_ref)
        or any(
            type(value) is not str or not _SHA256_RE.fullmatch(value)
            for value in (record_digest, snapshot_digest, binding_digest)
        )
        or type(field_ref) is not str
        or field_ref != "text"
        or type(start_char) is not int
        or type(end_char) is not int
        or start_char != 0
        or end_char <= 0
        or type(locator_digest) is not str
        or (locator_digest and not _SHA256_RE.fullmatch(locator_digest))
        or type(schema) is not str
        or schema != MEMORY_RECORD_LOCATOR_SCHEMA
    ):
        raise MemorySourceAdapterError("invalid_request")
    return (
        record_ref,
        record_digest,
        snapshot_digest,
        binding_digest,
        field_ref,
        start_char,
        end_char,
        locator_digest,
    )


def _timestamp_from_epoch(value: int) -> str:
    try:
        return (_EPOCH + timedelta(seconds=value)).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError):
        raise MemorySourceAdapterError("invalid_snapshot") from None


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULT_DISCOVERY_LIMIT",
    "MAX_DISCOVERY_LIMIT",
    "MEMORY_SOURCE_ADAPTER_ID",
    "MEMORY_SOURCE_ADAPTER_VERSION",
    "MEMORY_SOURCE_DOMAIN_ID",
    "MEMORY_SOURCE_EXACT_READER_BOUNDARY",
    "MemoryDiscoveryPage",
    "MemoryExactRead",
    "MemoryRecordEvidence",
    "MemoryRecordFieldLocator",
    "MemorySourceAdapter",
    "MemorySourceAdapterError",
    "MemorySourceAuthorityBinding",
    "MemorySourceDescriptor",
    "MemorySourceOccurrence",
    "MemoryUnavailableObservation",
    "MemoryUnavailableReason",
    "create_memory_source_authority_binding",
    "memory_source_capability_manifest",
    "memory_source_registration",
]
