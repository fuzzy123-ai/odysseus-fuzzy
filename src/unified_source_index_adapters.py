"""Backend-neutral, policy-aware source adapter contracts for USI jobs.

USI-04 defines the bounded adapter seam.  Real domain adapters arrive in the
UDA roadmap; the deterministic fake here proves discovery, version
observation, extraction, exact-read policy and unavailable-source semantics
without reading user or provider data.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    EntityRecord,
    EntityKind,
    EvidenceRef,
    LocatorKind,
    MessageRangeLocator,
    MAX_EVIDENCE_REFS,
    PageRangeLocator,
    PolicyEvidence,
    RecordKind,
    RecordRef,
    RelationRecord,
    RelationKind,
    RowRangeLocator,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    UnifiedSourceIndexContractError,
    canonical_json,
    content_hash,
    record_from_json,
)
from src.unified_source_index_stores import _owner_scope


MAX_ADAPTER_PAGE_SIZE = 1_000
MAX_ADAPTER_CURSOR_CHARS = 512
MAX_ADAPTER_WARNINGS = 32
MAX_EXACT_READ_CHARS = 1_000_000
MAX_TOTAL_EXTRACTION_CHARS = 8_388_608
MAX_PAGED_EXTRACTION_PAGE_RECORDS = 100
MAX_PAGED_EXTRACTION_RECORDS = 12_296
MAX_PAGED_EXTRACTION_PAGES = 123

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_HINT_RE = re.compile(
    r"(?:access[_-]?token|api[_-]?key|password|secret|signature)=",
    re.IGNORECASE,
)
_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.PRIVATE: 1,
    Classification.SENSITIVE: 2,
    Classification.SECRET: 3,
    Classification.UNKNOWN: 4,
}


class UnavailableReason(StrEnum):
    DELETED = "deleted"
    ACCESS_REVOKED = "access_revoked"
    PROVIDER_MISSING = "provider_missing"
    POLICY_BLOCKED = "policy_blocked"

_ENUM_LITERALS = {
    Classification: {
        Classification.PUBLIC: "public",
        Classification.PRIVATE: "private",
        Classification.SENSITIVE: "sensitive",
        Classification.SECRET: "secret",
        Classification.UNKNOWN: "unknown",
    },
    ContentPolicy: {
        ContentPolicy.INLINE_LOCAL: "inline_local",
        ContentPolicy.REFERENCE_ONLY: "reference_only",
        ContentPolicy.METADATA_ONLY: "metadata_only",
    },
    EntityKind: {
        EntityKind.SYMBOL: "symbol",
        EntityKind.PERSON: "person",
        EntityKind.TASK: "task",
        EntityKind.DOCUMENT_SECTION: "document_section",
        EntityKind.MESSAGE: "message",
        EntityKind.TABLE_ROW: "table_row",
        EntityKind.CONCEPT: "concept",
        EntityKind.OTHER: "other",
    },
    LocatorKind: {
        LocatorKind.TEXT_RANGE: "text_range",
        LocatorKind.PAGE_RANGE: "page_range",
        LocatorKind.ROW_RANGE: "row_range",
        LocatorKind.MESSAGE_RANGE: "message_range",
        LocatorKind.CODE_RANGE: "code_range",
    },
    RecordKind: {
        RecordKind.SOURCE: "source",
        RecordKind.SOURCE_VERSION: "source_version",
        RecordKind.CHUNK: "chunk",
        RecordKind.ENTITY: "entity",
        RecordKind.RELATION: "relation",
        RecordKind.LINEAGE: "lineage",
        RecordKind.PROJECTION: "projection",
        RecordKind.DERIVED_RUN: "derived_run",
        RecordKind.JOB: "job",
    },
    RelationKind: {
        RelationKind.DEFINES: "defines",
        RelationKind.IMPORTS: "imports",
        RelationKind.CALLS: "calls",
        RelationKind.INHERITS: "inherits",
        RelationKind.REFERENCES: "references",
        RelationKind.TESTS: "tests",
        RelationKind.MENTIONS: "mentions",
        RelationKind.BELONGS_TO: "belongs_to",
        RelationKind.SUPERSEDES: "supersedes",
        RelationKind.RELATED_TO: "related_to",
    },
    SourceKind: {
        SourceKind.TEXT: "text",
        SourceKind.DOCUMENT: "document",
        SourceKind.TABLE: "table",
        SourceKind.MESSAGE: "message",
        SourceKind.CODE: "code",
        SourceKind.MEMORY: "memory",
        SourceKind.PLANNING: "planning",
        SourceKind.INBOX: "inbox",
        SourceKind.OTHER: "other",
    },
    UnavailableReason: {
        UnavailableReason.DELETED: "deleted",
        UnavailableReason.ACCESS_REVOKED: "access_revoked",
        UnavailableReason.PROVIDER_MISSING: "provider_missing",
        UnavailableReason.POLICY_BLOCKED: "policy_blocked",
    },
}


class SourceAdapterError(ValueError):
    """Raised when an adapter violates bounds, identity or policy."""


class SourceUnavailableError(SourceAdapterError):
    """Raised when a source can no longer be observed or read."""


@dataclass(frozen=True, slots=True)
class AdapterCapability:
    adapter_id: str
    adapter_version: str
    owner_scope: str
    domain_kind: str
    source_kind: SourceKind
    content_policy: ContentPolicy
    classification_ceiling: Classification
    supports_exact_reads: bool
    max_discovery_page: int = 100
    max_extract_items: int = 100
    required_job_profile_ref: str = ""
    max_total_extract_chars: int = 1_000_000
    supports_budgeted_paging: bool = False

    def __post_init__(self) -> None:
        for field_name in ("adapter_id", "adapter_version", "domain_kind"):
            object.__setattr__(self, field_name, _token(getattr(self, field_name), field_name))
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        object.__setattr__(self, "source_kind", _enum(self.source_kind, SourceKind, "source_kind"))
        object.__setattr__(
            self,
            "content_policy",
            _enum(self.content_policy, ContentPolicy, "content_policy"),
        )
        object.__setattr__(
            self,
            "classification_ceiling",
            _enum(
                self.classification_ceiling,
                Classification,
                "classification_ceiling",
            ),
        )
        if not isinstance(self.supports_exact_reads, bool):
            raise SourceAdapterError("supports_exact_reads must be boolean")
        object.__setattr__(
            self,
            "max_discovery_page",
            _bounded_integer(
                self.max_discovery_page,
                "max_discovery_page",
                minimum=1,
                maximum=MAX_ADAPTER_PAGE_SIZE,
            ),
        )
        object.__setattr__(
            self,
            "max_extract_items",
            _bounded_integer(
                self.max_extract_items,
                "max_extract_items",
                minimum=1,
                maximum=MAX_ADAPTER_PAGE_SIZE,
            ),
        )
        if type(self.required_job_profile_ref) is not str:
            raise SourceAdapterError("required_job_profile_ref must use exact text")
        if self.required_job_profile_ref:
            object.__setattr__(
                self,
                "required_job_profile_ref",
                _token(self.required_job_profile_ref, "required_job_profile_ref"),
            )
        if type(self.max_total_extract_chars) is not int:
            raise SourceAdapterError("max_total_extract_chars must be exact integer")
        object.__setattr__(
            self,
            "max_total_extract_chars",
            _bounded_integer(
                self.max_total_extract_chars,
                "max_total_extract_chars",
                minimum=1,
                maximum=MAX_TOTAL_EXTRACTION_CHARS,
            ),
        )
        if type(self.supports_budgeted_paging) is not bool:
            raise SourceAdapterError("supports_budgeted_paging must be exact boolean")
        if self.supports_budgeted_paging and not self.required_job_profile_ref:
            raise SourceAdapterError("budgeted paging requires a generation-bound job profile")
        if (
            not self.supports_budgeted_paging
            and self.max_total_extract_chars > 1_000_000
        ):
            raise SourceAdapterError("aggregate budgets above 1 MiB require budgeted paging")
        if self.supports_exact_reads and self.content_policy is ContentPolicy.METADATA_ONLY:
            raise SourceAdapterError("metadata-only adapters cannot advertise exact reads")


@dataclass(frozen=True, slots=True)
class AdapterScope:
    owner_scope: str
    classification_ceiling: Classification
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        owner = _owner_scope(self.owner_scope)
        ceiling = _enum(
            self.classification_ceiling,
            Classification,
            "classification_ceiling",
        )
        if not isinstance(self.source_ids, tuple) or len(self.source_ids) > MAX_ADAPTER_PAGE_SIZE:
            raise SourceAdapterError("source_ids must be a bounded tuple")
        source_ids = tuple(
            sorted(
                {
                    RecordRef(RecordKind.SOURCE, source_id).record_id
                    for source_id in self.source_ids
                }
            )
        )
        object.__setattr__(self, "owner_scope", owner)
        object.__setattr__(self, "classification_ceiling", ceiling)
        object.__setattr__(self, "source_ids", source_ids)


@dataclass(frozen=True, slots=True)
class DiscoveryItem:
    source: SourceRecord
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceRecord):
            raise SourceAdapterError("discovery source must be typed")
        object.__setattr__(self, "fingerprint", _sha256(self.fingerprint, "fingerprint"))


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    items: tuple[DiscoveryItem, ...]
    next_cursor: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or len(self.items) > MAX_ADAPTER_PAGE_SIZE:
            raise SourceAdapterError("discovery items must be a bounded tuple")
        if not all(isinstance(item, DiscoveryItem) for item in self.items):
            raise SourceAdapterError("discovery page contains an invalid item")
        if not isinstance(self.next_cursor, str) or len(self.next_cursor) > MAX_ADAPTER_CURSOR_CHARS:
            raise SourceAdapterError("discovery cursor is invalid or unbounded")
        object.__setattr__(self, "warnings", _warnings(self.warnings))

    @property
    def clipped(self) -> bool:
        return bool(self.next_cursor)


@dataclass(frozen=True, slots=True)
class ExtractionProfile:
    profile_ref: str
    max_items: int
    max_chars: int
    time_budget_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_ref", _token(self.profile_ref, "profile_ref"))
        object.__setattr__(
            self,
            "max_items",
            _bounded_integer(
                self.max_items,
                "max_items",
                minimum=1,
                maximum=MAX_ADAPTER_PAGE_SIZE,
            ),
        )
        object.__setattr__(
            self,
            "max_chars",
            _bounded_integer(
                self.max_chars,
                "max_chars",
                minimum=1,
                maximum=MAX_EXACT_READ_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "time_budget_ms",
            _bounded_integer(
                self.time_budget_ms,
                "time_budget_ms",
                minimum=1,
                maximum=86_400_000,
            ),
        )


@dataclass(frozen=True, slots=True)
class ExtractionPageBudget:
    """One bounded operation allowance under an immutable extraction profile."""

    max_items: int
    max_chars: int
    time_budget_ms: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (self.max_items, self.max_chars, self.time_budget_ms)
        ):
            raise SourceAdapterError("page budget fields must be exact integers")
        object.__setattr__(
            self,
            "max_items",
            _bounded_integer(
                self.max_items,
                "page max_items",
                minimum=1,
                maximum=MAX_PAGED_EXTRACTION_PAGE_RECORDS,
            ),
        )
        object.__setattr__(
            self,
            "max_chars",
            _bounded_integer(
                self.max_chars,
                "page max_chars",
                minimum=0,
                maximum=MAX_EXACT_READ_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "time_budget_ms",
            _bounded_integer(
                self.time_budget_ms,
                "page time_budget_ms",
                minimum=1,
                maximum=86_400_000,
            ),
        )


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    source_version: SourceVersionRecord
    chunks: tuple[ChunkRecord, ...] = ()
    entities: tuple[EntityRecord, ...] = ()
    relations: tuple[RelationRecord, ...] = ()
    clipped: bool = False
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_version, SourceVersionRecord):
            raise SourceAdapterError("extraction requires a typed source version")
        records = (*self.chunks, *self.entities, *self.relations)
        if len(records) > MAX_ADAPTER_PAGE_SIZE:
            raise SourceAdapterError("extraction result is unbounded")
        if not all(
            isinstance(item, (ChunkRecord, EntityRecord, RelationRecord))
            for item in records
        ):
            raise SourceAdapterError("extraction result contains an invalid record")
        for item in (*self.chunks, *self.entities):
            if (
                item.source_id != self.source_version.source_id
                or item.source_version_id != self.source_version.source_version_id
                or item.owner_scope != self.source_version.owner_scope
            ):
                raise SourceAdapterError("extracted occurrence escapes its source version")
        if any(item.owner_scope != self.source_version.owner_scope for item in self.relations):
            raise SourceAdapterError("extracted relation escapes its owner scope")
        if not isinstance(self.clipped, bool):
            raise SourceAdapterError("clipped must be boolean")
        object.__setattr__(self, "warnings", _warnings(self.warnings))

    @property
    def records(self) -> tuple[ChunkRecord | EntityRecord | RelationRecord, ...]:
        return (*self.chunks, *self.entities, *self.relations)


@dataclass(frozen=True, slots=True)
class PagedExtractionPage:
    source_version: SourceVersionRecord
    sequence_start: int
    records: tuple[ChunkRecord | EntityRecord | RelationRecord, ...]
    next_cursor: str = ""
    final_record_count: int | None = None
    final_records_digest: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.source_version) is not SourceVersionRecord:
            raise SourceAdapterError("paged extraction requires an exact source version")
        if type(self.sequence_start) is not int or not 0 <= self.sequence_start <= MAX_PAGED_EXTRACTION_RECORDS:
            raise SourceAdapterError("sequence_start is invalid or unbounded")
        if type(self.records) is not tuple or len(self.records) > MAX_PAGED_EXTRACTION_PAGE_RECORDS:
            raise SourceAdapterError("paged extraction records must be a bounded tuple")
        allowed = (ChunkRecord, EntityRecord, RelationRecord)
        if any(not _has_exact_type(record, allowed) for record in self.records):
            raise SourceAdapterError("paged extraction contains an invalid record")
        if type(self.next_cursor) is not str or len(self.next_cursor) > MAX_ADAPTER_CURSOR_CHARS:
            raise SourceAdapterError("paged extraction cursor is invalid or unbounded")
        if self.final_record_count is not None and (
            type(self.final_record_count) is not int
            or not 0 <= self.final_record_count <= MAX_PAGED_EXTRACTION_RECORDS
        ):
            raise SourceAdapterError("final_record_count is invalid or unbounded")
        if type(self.final_records_digest) is not str:
            raise SourceAdapterError("final_records_digest must use exact text")
        if self.final_records_digest:
            object.__setattr__(
                self,
                "final_records_digest",
                _sha256(self.final_records_digest, "final_records_digest"),
            )
        if self.next_cursor:
            if not self.records:
                raise SourceAdapterError("nonterminal paged extraction must contain records")
            if self.final_record_count is not None or self.final_records_digest:
                raise SourceAdapterError("nonterminal paged extraction cannot contain terminal fields")
        elif self.final_record_count is None or not self.final_records_digest:
            raise SourceAdapterError("terminal paged extraction requires count and digest")
        if (
            type(self.warnings) is not tuple
            or len(self.warnings) > MAX_ADAPTER_WARNINGS
            or any(type(warning) is not str for warning in self.warnings)
        ):
            raise SourceAdapterError("warnings must use exact tuple and text values")
        object.__setattr__(self, "warnings", _warnings(self.warnings))

        detached_version = _detach_exact_record(
            self.source_version, (SourceVersionRecord,)
        )
        detached_records = tuple(
            _detach_exact_record(record, allowed) for record in self.records
        )
        object.__setattr__(self, "source_version", detached_version)
        object.__setattr__(self, "records", detached_records)


@dataclass(frozen=True, slots=True)
class ExactReadRequest:
    evidence: EvidenceRef
    max_chars: int

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, EvidenceRef):
            raise SourceAdapterError("exact read requires typed evidence")
        object.__setattr__(
            self,
            "max_chars",
            _bounded_integer(
                self.max_chars,
                "max_chars",
                minimum=1,
                maximum=MAX_EXACT_READ_CHARS,
            ),
        )


@dataclass(frozen=True, slots=True)
class PolicyReadContext:
    owner_scope: str
    classification_ceiling: Classification
    allow_inline_content: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        object.__setattr__(
            self,
            "classification_ceiling",
            _enum(
                self.classification_ceiling,
                Classification,
                "classification_ceiling",
            ),
        )
        if not isinstance(self.allow_inline_content, bool):
            raise SourceAdapterError("allow_inline_content must be boolean")


@dataclass(frozen=True, slots=True)
class ExactReadResult:
    content: str
    content_hash: str
    clipped: bool

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or len(self.content) > MAX_EXACT_READ_CHARS:
            raise SourceAdapterError("exact-read content is invalid or unbounded")
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, "content_hash"))
        if content_hash(self.content) != self.content_hash:
            raise SourceAdapterError("exact-read content does not match content_hash")
        if not isinstance(self.clipped, bool):
            raise SourceAdapterError("clipped must be boolean")


@dataclass(frozen=True, slots=True)
class UnavailableObservation:
    source_ref: RecordRef
    owner_scope: str
    reason: UnavailableReason
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_ref, RecordRef) or self.source_ref.record_kind is not RecordKind.SOURCE:
            raise SourceAdapterError("unavailable observation requires a source ref")
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        object.__setattr__(self, "reason", _enum(self.reason, UnavailableReason, "reason"))
        object.__setattr__(self, "fingerprint", _sha256(self.fingerprint, "fingerprint"))


@runtime_checkable
class PagedSourceAdapter(Protocol):
    def extract_page(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
        *,
        cursor: str,
        sequence_start: int,
    ) -> PagedExtractionPage: ...


@runtime_checkable
class BudgetedPagedSourceAdapter(Protocol):
    def extract_page_budgeted(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
        budget: ExtractionPageBudget,
        *,
        cursor: str,
        sequence_start: int,
    ) -> PagedExtractionPage: ...


@runtime_checkable
class SourceAdapter(Protocol):
    def describe_capability(self) -> AdapterCapability: ...

    def discover(
        self,
        scope: AdapterScope,
        *,
        cursor: str,
        limit: int,
        time_budget_ms: int,
    ) -> DiscoveryPage: ...

    def observe_version(
        self,
        source_ref: RecordRef,
    ) -> SourceVersionRecord | UnavailableObservation: ...

    def extract(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
    ) -> ExtractionResult: ...

    def read_exact(
        self,
        request: ExactReadRequest,
        policy_context: PolicyReadContext,
    ) -> ExactReadResult: ...

    def observe_unavailable(
        self,
        source_ref: RecordRef,
        reason: UnavailableReason | str,
    ) -> UnavailableObservation: ...


@dataclass(frozen=True, slots=True)
class FakeAdapterDocument:
    canonical_ref: str
    revision_ref: str
    content: str
    classification: Classification = Classification.PRIVATE
    available: bool = True
    version_observed_at: str = "2026-01-01T00:00:00Z"

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_ref", _canonical_ref(self.canonical_ref))
        if not isinstance(self.revision_ref, str) or not self.revision_ref or len(self.revision_ref) > 512:
            raise SourceAdapterError("revision_ref is invalid or unbounded")
        if not isinstance(self.content, str) or len(self.content) > MAX_EXACT_READ_CHARS:
            raise SourceAdapterError("fake content is invalid or unbounded")
        object.__setattr__(
            self,
            "classification",
            _enum(self.classification, Classification, "classification"),
        )
        if not isinstance(self.available, bool):
            raise SourceAdapterError("available must be boolean")
        if not isinstance(self.version_observed_at, str) or not self.version_observed_at:
            raise SourceAdapterError("version_observed_at must be a timestamp")


class DeterministicFakeSourceAdapter:
    """Synthetic adapter with deterministic, cursor-bound behavior."""

    def __init__(
        self,
        capability: AdapterCapability,
        documents: tuple[FakeAdapterDocument, ...],
    ) -> None:
        if not isinstance(capability, AdapterCapability):
            raise SourceAdapterError("capability must be typed")
        if not isinstance(documents, tuple) or len(documents) > MAX_ADAPTER_PAGE_SIZE:
            raise SourceAdapterError("fake documents must be a bounded tuple")
        sources: dict[str, tuple[SourceRecord, FakeAdapterDocument]] = {}
        for document in documents:
            if not isinstance(document, FakeAdapterDocument):
                raise SourceAdapterError("fake document must be typed")
            _require_classification(
                document.classification,
                capability.classification_ceiling,
                "fake document exceeds adapter classification ceiling",
            )
            source = SourceRecord(
                owner_scope=capability.owner_scope,
                source_kind=capability.source_kind,
                canonical_ref=document.canonical_ref,
                classification=document.classification,
                content_policy=capability.content_policy,
                provider_ref=capability.adapter_id,
            )
            if source.source_id in sources:
                raise SourceAdapterError("fake canonical source is duplicated")
            sources[source.source_id] = (source, document)
        self._capability = capability
        self._sources = sources

    def describe_capability(self) -> AdapterCapability:
        return self._capability

    def discover(
        self,
        scope: AdapterScope,
        *,
        cursor: str,
        limit: int,
        time_budget_ms: int,
    ) -> DiscoveryPage:
        self._validate_scope(scope)
        page_limit = _bounded_integer(
            limit,
            "limit",
            minimum=1,
            maximum=self._capability.max_discovery_page,
        )
        _bounded_integer(
            time_budget_ms,
            "time_budget_ms",
            minimum=1,
            maximum=86_400_000,
        )
        after_id = _decode_discovery_cursor(cursor, self._capability, scope) if cursor else ""
        selected_ids = [
            source_id
            for source_id in sorted(self._sources)
            if source_id > after_id and (not scope.source_ids or source_id in scope.source_ids)
        ]
        items: list[DiscoveryItem] = []
        for source_id in selected_ids[:page_limit]:
            source, document = self._sources[source_id]
            _require_classification(
                source.classification,
                scope.classification_ceiling,
                "source exceeds discovery classification ceiling",
            )
            fingerprint = _fingerprint(
                {
                    "source_id": source_id,
                    "revision_ref": document.revision_ref,
                    "content_hash": content_hash(document.content),
                    "available": document.available,
                }
            )
            items.append(DiscoveryItem(source, fingerprint))
        next_cursor = ""
        if len(selected_ids) > page_limit and items:
            next_cursor = _encode_discovery_cursor(
                self._capability,
                scope,
                items[-1].source.source_id,
            )
        return DiscoveryPage(tuple(items), next_cursor)

    def observe_version(
        self,
        source_ref: RecordRef,
    ) -> SourceVersionRecord | UnavailableObservation:
        source, document = self._source_document(source_ref)
        if not document.available:
            return self.observe_unavailable(source_ref, UnavailableReason.DELETED)
        return SourceVersionRecord.create(
            source,
            revision_ref=document.revision_ref,
            content_hash=content_hash(document.content),
            version_observed_at=document.version_observed_at,
        )

    def extract(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
    ) -> ExtractionResult:
        if not isinstance(source_version, SourceVersionRecord):
            raise SourceAdapterError("source_version must be typed")
        if not isinstance(profile, ExtractionProfile):
            raise SourceAdapterError("profile must be typed")
        source, document = self._source_document(
            RecordRef(RecordKind.SOURCE, source_version.source_id)
        )
        if not document.available:
            raise SourceUnavailableError("source is unavailable")
        expected = self.observe_version(source.ref())
        if not isinstance(expected, SourceVersionRecord) or expected != source_version:
            raise SourceAdapterError("source version is stale or belongs to another adapter")
        if not document.content:
            return ExtractionResult(source_version)
        selected_content = document.content[: profile.max_chars]
        clipped = len(selected_content) < len(document.content)
        stored_content = (
            selected_content
            if self._capability.content_policy is ContentPolicy.INLINE_LOCAL
            else None
        )
        chunk = ChunkRecord.create(
            source_version,
            locator=TextRangeLocator(0, len(selected_content)),
            extractor_profile_ref=profile.profile_ref,
            content_hash=content_hash(selected_content),
            content=stored_content,
        )
        return ExtractionResult(
            source_version,
            chunks=(chunk,),
            clipped=clipped,
            warnings=("content_clipped",) if clipped else (),
        )

    def read_exact(
        self,
        request: ExactReadRequest,
        policy_context: PolicyReadContext,
    ) -> ExactReadResult:
        if not isinstance(request, ExactReadRequest) or not isinstance(
            policy_context, PolicyReadContext
        ):
            raise SourceAdapterError("exact-read request and policy context must be typed")
        if not self._capability.supports_exact_reads:
            raise SourceAdapterError("adapter does not support exact reads")
        source_ref = RecordRef(RecordKind.SOURCE, request.evidence.source_id)
        source, document = self._source_document(source_ref)
        if not document.available:
            raise SourceUnavailableError("source is unavailable")
        if policy_context.owner_scope != source.owner_scope:
            raise SourceAdapterError("exact read crosses owner scope")
        _require_classification(
            source.classification,
            policy_context.classification_ceiling,
            "exact read exceeds classification ceiling",
        )
        if not policy_context.allow_inline_content:
            raise SourceAdapterError("exact read is not allowed by policy")
        start, end = _text_range(request.evidence)
        full = document.content[start:end]
        selected = full[: request.max_chars]
        return ExactReadResult(
            selected,
            content_hash(selected),
            len(selected) < len(full),
        )

    def observe_unavailable(
        self,
        source_ref: RecordRef,
        reason: UnavailableReason | str,
    ) -> UnavailableObservation:
        source, document = self._source_document(source_ref)
        normalized_reason = _enum(reason, UnavailableReason, "reason")
        return UnavailableObservation(
            source.ref(),
            source.owner_scope,
            normalized_reason,
            _fingerprint(
                {
                    "source_id": source.source_id,
                    "revision_ref": document.revision_ref,
                    "available": False,
                    "reason": normalized_reason.value,
                }
            ),
        )

    def _validate_scope(self, scope: AdapterScope) -> None:
        if not isinstance(scope, AdapterScope):
            raise SourceAdapterError("scope must be typed")
        if scope.owner_scope != self._capability.owner_scope:
            raise SourceAdapterError("discovery crosses adapter owner scope")
        _require_classification(
            scope.classification_ceiling,
            self._capability.classification_ceiling,
            "scope exceeds adapter classification ceiling",
        )

    def _source_document(
        self,
        source_ref: RecordRef,
    ) -> tuple[SourceRecord, FakeAdapterDocument]:
        if not isinstance(source_ref, RecordRef) or source_ref.record_kind is not RecordKind.SOURCE:
            raise SourceAdapterError("source_ref must identify a source")
        value = self._sources.get(source_ref.record_id)
        if value is None:
            raise SourceAdapterError("source does not belong to this adapter")
        return value


def validate_adapter_output(
    capability: AdapterCapability,
    scope: AdapterScope,
    discovery: DiscoveryItem,
    observed: SourceVersionRecord,
    extraction: ExtractionResult,
) -> None:
    """Validate the complete pre-write adapter output boundary."""

    if not all(
        isinstance(item, expected)
        for item, expected in (
            (capability, AdapterCapability),
            (scope, AdapterScope),
            (discovery, DiscoveryItem),
            (observed, SourceVersionRecord),
            (extraction, ExtractionResult),
        )
    ):
        raise SourceAdapterError("adapter output validation requires typed values")
    source = discovery.source
    if source.owner_scope != capability.owner_scope or source.owner_scope != scope.owner_scope:
        raise SourceAdapterError("adapter output crosses owner scope")
    if source.source_kind is not capability.source_kind:
        raise SourceAdapterError("adapter output changes source kind")
    if source.content_policy is not capability.content_policy:
        raise SourceAdapterError("adapter output changes content policy")
    if source.provider_ref != capability.adapter_id:
        raise SourceAdapterError("adapter output changes provider identity")
    _canonical_ref(source.canonical_ref)
    _require_classification(
        source.classification,
        scope.classification_ceiling,
        "adapter output exceeds job classification ceiling",
    )
    if scope.source_ids and source.source_id not in scope.source_ids:
        raise SourceAdapterError("adapter output escapes the requested source scope")
    if (
        observed.source_id != source.source_id
        or observed.owner_scope != source.owner_scope
        or extraction.source_version != observed
    ):
        raise SourceAdapterError("adapter output has inconsistent source/version identity")
    for record in extraction.records:
        if record.owner_scope != source.owner_scope:
            raise SourceAdapterError("extracted record crosses owner scope")
        _require_classification(
            record.classification,
            source.classification,
            "extracted record weakens classification propagation",
        )
        if isinstance(record, ChunkRecord):
            if record.content_policy is not source.content_policy:
                raise SourceAdapterError("chunk changes inherited content policy")
            if source.content_policy is not ContentPolicy.INLINE_LOCAL and record.content is not None:
                raise SourceAdapterError("non-inline adapter returned stored chunk content")


def _encode_discovery_cursor(
    capability: AdapterCapability,
    scope: AdapterScope,
    after_source_id: str,
) -> str:
    body = {
        "s": "usi.adapter.v1",
        "a": capability.adapter_id,
        "v": capability.adapter_version,
        "o": scope.owner_scope,
        "l": scope.classification_ceiling.value,
        "q": _fingerprint(scope.source_ids),
        "i": RecordRef(RecordKind.SOURCE, after_source_id).record_id,
    }
    checksum = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    encoded = canonical_json({**body, "h": checksum})
    if len(encoded) > MAX_ADAPTER_CURSOR_CHARS:
        raise SourceAdapterError("generated discovery cursor exceeds its bound")
    return encoded


def _decode_discovery_cursor(
    value: str,
    capability: AdapterCapability,
    scope: AdapterScope,
) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ADAPTER_CURSOR_CHARS:
        raise SourceAdapterError("discovery cursor is invalid or unbounded")
    try:
        payload = json.loads(
            value,
            object_pairs_hook=_unique_json_object,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SourceAdapterError("discovery cursor is invalid") from exc
    if not isinstance(payload, dict):
        raise SourceAdapterError("discovery cursor payload must be an object")
    checksum = payload.pop("h", None)
    if checksum != hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest():
        raise SourceAdapterError("discovery cursor checksum does not match")
    expected = {
        "s": "usi.adapter.v1",
        "a": capability.adapter_id,
        "v": capability.adapter_version,
        "o": scope.owner_scope,
        "l": scope.classification_ceiling.value,
        "q": _fingerprint(scope.source_ids),
    }
    if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
        raise SourceAdapterError("discovery cursor belongs to another adapter or scope")
    after_source_id = payload.get("i")
    if not isinstance(after_source_id, str):
        raise SourceAdapterError("discovery cursor source id is invalid")
    return RecordRef(RecordKind.SOURCE, after_source_id).record_id


def _text_range(evidence: EvidenceRef) -> tuple[int, int]:
    locator = evidence.locator
    if not isinstance(locator, TextRangeLocator):
        raise SourceAdapterError("fake exact reader supports text ranges only")
    return locator.start_char, locator.end_char


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceAdapterError(f"discovery cursor contains duplicate field: {key}")
        result[key] = value
    return result


def _canonical_ref(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise SourceAdapterError("canonical_ref is invalid or unbounded")
    if any(char in value for char in ("\r", "\n", "\x00")) or _SECRET_HINT_RE.search(value):
        raise SourceAdapterError("canonical_ref may not contain secret-bearing data")
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and (
        parsed.username is not None or parsed.password is not None or parsed.query
    ):
        raise SourceAdapterError("canonical_ref may not contain provider credentials or query data")
    return value


def _warnings(value: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > MAX_ADAPTER_WARNINGS:
        raise SourceAdapterError("warnings must be a bounded tuple")
    return tuple(_token(item, "warning") for item in value)


def _fingerprint(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise SourceAdapterError(f"{field_name} must be sha256 text")
    normalized = value.lower()
    if not normalized.startswith("sha256:"):
        normalized = "sha256:" + normalized
    if not _SHA256_RE.fullmatch(normalized):
        raise SourceAdapterError(f"{field_name} must be sha256 text")
    return normalized


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise SourceAdapterError(f"{field_name} must be a bounded token")
    return value


def _enum(value, enum_type, field_name: str):
    literals = _ENUM_LITERALS.get(enum_type)
    if literals is None:
        raise SourceAdapterError(f"{field_name} is invalid")
    if type(value) is enum_type:
        expected = next(
            (literal for member, literal in literals.items() if value is member),
            None,
        )
        try:
            raw = object.__getattribute__(value, "_value_")
        except Exception as exc:
            raise SourceAdapterError(f"{field_name} is invalid") from exc
        if expected is None or type(raw) is not str or raw != expected:
            raise SourceAdapterError(f"{field_name} is invalid")
        return value
    if type(value) is str:
        for member, expected in literals.items():
            if value == expected:
                try:
                    raw = object.__getattribute__(member, "_value_")
                except Exception as exc:
                    raise SourceAdapterError(f"{field_name} is invalid") from exc
                if type(raw) is not str or raw != expected:
                    raise SourceAdapterError(f"{field_name} is invalid")
                return member
    raise SourceAdapterError(f"{field_name} is invalid")


def _enum_literal(value, enum_type, field_name: str) -> str:
    member = _enum(value, enum_type, field_name)
    for candidate, literal in _ENUM_LITERALS[enum_type].items():
        if member is candidate:
            return literal
    raise SourceAdapterError(f"{field_name} is invalid")


def _bounded_integer(
    value: int,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SourceAdapterError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


def _has_exact_type(value: object, allowed: tuple[type, ...]) -> bool:
    actual = type(value)
    return any(actual is expected for expected in allowed)


def _capture_exact_contract_value(
    value: object,
    *,
    depth: int = 0,
    ancestors: tuple[int, ...] = (),
) -> object:
    if depth > 64:
        raise SourceAdapterError("paged extraction nested data exceeds its depth bound")
    actual = type(value)
    if value is None or actual is str or actual is int or actual is bool:
        return value
    if actual is float:
        if not math.isfinite(value):
            raise SourceAdapterError("paged extraction contains non-finite data")
        return value
    enum_types = (
        Classification,
        ContentPolicy,
        EntityKind,
        RecordKind,
        RelationKind,
        SourceKind,
    )
    if any(actual is expected for expected in enum_types):
        return _enum_literal(value, actual, "paged extraction enum")
    if actual is tuple:
        if len(value) > MAX_EVIDENCE_REFS or id(value) in ancestors:
            raise SourceAdapterError("paged extraction tuple is cyclic or unbounded")
        lineage = (*ancestors, id(value))
        return tuple(
            _capture_exact_contract_value(item, depth=depth + 1, ancestors=lineage)
            for item in value
        )

    locator_types = (
        TextRangeLocator,
        PageRangeLocator,
        RowRangeLocator,
        MessageRangeLocator,
        CodeRangeLocator,
    )
    if any(actual is expected for expected in locator_types):
        kind = type.__getattribute__(actual, "kind")
        kind = _enum(kind, LocatorKind, "paged extraction locator kind")
        if id(value) in ancestors:
            raise SourceAdapterError("paged extraction nested data is cyclic")
        lineage = (*ancestors, id(value))
        body = {
            field.name: _capture_exact_contract_value(
                object.__getattribute__(value, field.name),
                depth=depth + 1,
                ancestors=lineage,
            )
            for field in fields(actual)
        }
        return {
            "kind": _enum_literal(kind, LocatorKind, "paged extraction locator kind"),
            **body,
        }

    canonical_types = (
        PolicyEvidence,
        RecordRef,
        EvidenceRef,
        SourceRecord,
        SourceVersionRecord,
        ChunkRecord,
        EntityRecord,
        RelationRecord,
    )
    if any(actual is expected for expected in canonical_types):
        if id(value) in ancestors:
            raise SourceAdapterError("paged extraction nested data is cyclic")
        lineage = (*ancestors, id(value))
        body = {
            field.name: _capture_exact_contract_value(
                object.__getattribute__(value, field.name),
                depth=depth + 1,
                ancestors=lineage,
            )
            for field in fields(actual)
        }
        schema = type.__getattribute__(actual, "SCHEMA")
        if type(schema) is not str:
            raise SourceAdapterError("paged extraction record schema is invalid")
        return {"schema": schema, **body}
    raise SourceAdapterError("paged extraction contains a non-exact nested value")


def _detach_exact_record(record: object, allowed: tuple[type, ...]) -> object:
    if not _has_exact_type(record, allowed):
        raise SourceAdapterError("paged extraction contains an invalid record")
    payload = _capture_exact_contract_value(record)
    encoded = canonical_json(payload)
    try:
        detached = record_from_json(encoded)
    except UnifiedSourceIndexContractError as exc:
        raise SourceAdapterError("paged extraction record is not canonical") from exc
    if not _has_exact_type(detached, allowed):
        raise SourceAdapterError("paged extraction canonical detachment changed record types")
    if canonical_json(_capture_exact_contract_value(detached)) != encoded:
        raise SourceAdapterError("paged extraction canonical detachment changed record bytes")
    return detached


def _require_classification(
    value: Classification,
    ceiling: Classification,
    message: str,
) -> None:
    value = _enum(value, Classification, "classification")
    ceiling = _enum(ceiling, Classification, "classification ceiling")
    if _CLASSIFICATION_RANK[value] > _CLASSIFICATION_RANK[ceiling]:
        raise SourceAdapterError(message)


__all__ = [
    "AdapterCapability",
    "AdapterScope",
    "BudgetedPagedSourceAdapter",
    "DeterministicFakeSourceAdapter",
    "DiscoveryItem",
    "DiscoveryPage",
    "ExactReadRequest",
    "ExactReadResult",
    "ExtractionProfile",
    "ExtractionPageBudget",
    "ExtractionResult",
    "FakeAdapterDocument",
    "MAX_ADAPTER_CURSOR_CHARS",
    "MAX_ADAPTER_PAGE_SIZE",
    "MAX_TOTAL_EXTRACTION_CHARS",
    "MAX_PAGED_EXTRACTION_PAGE_RECORDS",
    "MAX_PAGED_EXTRACTION_PAGES",
    "MAX_PAGED_EXTRACTION_RECORDS",
    "PagedExtractionPage",
    "PagedSourceAdapter",
    "PolicyReadContext",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceUnavailableError",
    "UnavailableObservation",
    "UnavailableReason",
    "validate_adapter_output",
]
