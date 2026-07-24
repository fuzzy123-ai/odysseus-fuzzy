"""Backend-neutral, policy-aware source adapter contracts for USI jobs.

USI-04 defines the bounded adapter seam.  Real domain adapters arrive in the
UDA roadmap; the deterministic fake here proves discovery, version
observation, extraction, exact-read policy and unavailable-source semantics
without reading user or provider data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    EntityRecord,
    EvidenceRef,
    RecordKind,
    RecordRef,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    canonical_json,
    content_hash,
)
from src.unified_source_index_stores import _owner_scope


MAX_ADAPTER_PAGE_SIZE = 1_000
MAX_ADAPTER_CURSOR_CHARS = 512
MAX_ADAPTER_WARNINGS = 32
MAX_EXACT_READ_CHARS = 1_000_000

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


class SourceAdapterError(ValueError):
    """Raised when an adapter violates bounds, identity or policy."""


class SourceUnavailableError(SourceAdapterError):
    """Raised when a source can no longer be observed or read."""


class UnavailableReason(StrEnum):
    DELETED = "deleted"
    ACCESS_REVOKED = "access_revoked"
    PROVIDER_MISSING = "provider_missing"
    POLICY_BLOCKED = "policy_blocked"


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
        if _CLASSIFICATION_RANK[record.classification] < _CLASSIFICATION_RANK[
            source.classification
        ]:
            raise SourceAdapterError("extracted record weakens classification propagation")
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
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise SourceAdapterError(f"{field_name} is invalid") from exc


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


def _require_classification(
    value: Classification,
    ceiling: Classification,
    message: str,
) -> None:
    if _CLASSIFICATION_RANK[value] > _CLASSIFICATION_RANK[ceiling]:
        raise SourceAdapterError(message)


__all__ = [
    "AdapterCapability",
    "AdapterScope",
    "DeterministicFakeSourceAdapter",
    "DiscoveryItem",
    "DiscoveryPage",
    "ExactReadRequest",
    "ExactReadResult",
    "ExtractionProfile",
    "ExtractionResult",
    "FakeAdapterDocument",
    "MAX_ADAPTER_CURSOR_CHARS",
    "MAX_ADAPTER_PAGE_SIZE",
    "PolicyReadContext",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceUnavailableError",
    "UnavailableObservation",
    "UnavailableReason",
    "validate_adapter_output",
]
