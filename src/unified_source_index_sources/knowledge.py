"""Harbour-backed, default-off source adapter for USI."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

from src.harbour_authority import TrustedPrincipal
from src.harbour_knowledge_store import (
    HEAD_STATES,
    KNOWLEDGE_KINDS,
    KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA,
    HarbourKnowledgeStore,
    HarbourKnowledgeStoreError,
    KnowledgeHeadMetadata,
    KnowledgeIndexRecord,
    KnowledgeIndexSnapshot,
    KnowledgeMigrationSnapshot,
)
from src.unified_source_index_contract import (
    MAX_TEXT_CHARS,
    ChunkRecord,
    Classification,
    ContentPolicy,
    EvidenceRef,
    PolicyEvidence,
    RecordKind,
    RecordRef,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    canonical_json,
    content_hash,
    make_source_id,
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
from src.unified_source_index_adapters import (
    MAX_ADAPTER_CURSOR_CHARS,
    MAX_ADAPTER_PAGE_SIZE,
    MAX_EXACT_READ_CHARS,
    MAX_PAGED_EXTRACTION_PAGE_RECORDS,
    MAX_PAGED_EXTRACTION_RECORDS,
    AdapterCapability,
    AdapterScope,
    DiscoveryItem,
    DiscoveryPage,
    ExactReadRequest,
    ExactReadResult,
    ExtractionProfile,
    ExtractionPageBudget,
    ExtractionResult,
    PagedExtractionPage,
    PolicyReadContext,
    SourceAdapterError,
    UnavailableObservation,
    UnavailableReason,
    _capture_exact_contract_value,
    _detach_exact_record,
    _enum as _validate_adapter_enum,
)

KNOWLEDGE_SOURCE_ADAPTER_ID = "knowledge.native"
KNOWLEDGE_SOURCE_ADAPTER_VERSION = "v4"
KNOWLEDGE_SOURCE_DOMAIN_ID = "native_knowledge"
KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY = (
    "harbour_knowledge_store.read_payload_at_generation"
)
MAX_DISCOVERY_LIMIT = 100
MAX_KNOWLEDGE_RECORDS = 10_000
MIN_CURSOR_SIGNING_KEY_BYTES = 32
MAX_CURSOR_SIGNING_KEY_BYTES = 128
_DISCOVERY_CURSOR_DOMAIN = b"odysseus.knowledge.cursor.discovery.v1\x00"
_EXTRACTION_CURSOR_DOMAIN = b"odysseus.knowledge.cursor.extraction.v1\x00"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RANK = {
    Classification.PUBLIC: 0,
    Classification.PRIVATE: 1,
    Classification.SENSITIVE: 2,
    Classification.SECRET: 3,
    Classification.UNKNOWN: 4,
}


class KnowledgeSourceAdapterError(SourceAdapterError):
    """Fresh, bounded, content-free adapter failure."""

    def __init__(self, code: str = "adapter_failed") -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str = "adapter_failed") -> None:
    """Raise a fresh error without retaining a provider exception as context."""

    error = KnowledgeSourceAdapterError(code)
    try:
        raise error
    except KnowledgeSourceAdapterError as caught:
        BaseException.__context__.__set__(caught, None)
        BaseException.__cause__.__set__(caught, None)
        caught.__suppress_context__ = False
        raise


def _digest(value: Any) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _opaque(owner_scope: str, knowledge_id: str) -> str:
    framed = f"odysseus.usi.harbour.knowledge.v1\x00{owner_scope}\x00{knowledge_id}"
    return "harbour:knowledge:" + hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _cls(value: str) -> Classification:
    literals = (
        (Classification.PUBLIC, "public"),
        (Classification.PRIVATE, "private"),
        (Classification.SENSITIVE, "sensitive"),
        (Classification.SECRET, "secret"),
        (Classification.UNKNOWN, "unknown"),
    )
    for member, literal in literals:
        try:
            _validate_adapter_enum(member, Classification, "classification")
        except Exception:
            _fail("invalid_authority")
        if type(value) is str and value == literal:
            return member
    return Classification.UNKNOWN


def _classification_literal(value: Classification) -> str:
    try:
        _validate_adapter_enum(value, Classification, "classification")
    except Exception:
        _fail("invalid_request")
    for member, literal in (
        (Classification.PUBLIC, "public"),
        (Classification.PRIVATE, "private"),
        (Classification.SENSITIVE, "sensitive"),
        (Classification.SECRET, "secret"),
        (Classification.UNKNOWN, "unknown"),
    ):
        if value is member:
            return literal
    _fail("invalid_request")


def _vref(record: KnowledgeIndexRecord) -> str:
    identity = {
        "revision_id": record.revision_id,
        "revision": record.revision,
        "generation": record.generation,
        "record_digest": record.record_digest,
    }
    return "harbour-revision:" + _digest(identity)


def _detach_input_record(value: object, allowed: tuple[type, ...], code: str = "invalid_request"):
    if type(value) not in allowed:
        _fail(code)
    try:
        return _detach_exact_record(value, allowed)
    except Exception:
        _fail(code)


def _enc(value: dict[str, Any], key: bytes, domain: bytes) -> str:
    raw = canonical_json(value).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(key, domain + raw, hashlib.sha256).hexdigest()
    encoded = f"{body}.{signature}"
    if len(encoded) > MAX_ADAPTER_CURSOR_CHARS:
        _fail("invalid_request")
    return encoded


def _dec(token: str, key: bytes, domain: bytes) -> dict[str, Any]:
    try:
        if (
            type(token) is not str
            or len(token) > MAX_ADAPTER_CURSOR_CHARS
            or token.count(".") != 1
        ):
            _fail("invalid_request")
        body, checksum = token.split(".", 1)
        if not body or re.fullmatch(r"[A-Za-z0-9_-]+", body) is None:
            _fail("invalid_request")
        if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            _fail("invalid_request")

        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        canonical_body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        if canonical_body != body:
            _fail("invalid_request")
        expected_signature = hmac.new(key, domain + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(checksum, expected_signature):
            _fail("invalid_request")

        decoded = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_json_object)
        if type(decoded) is not dict or canonical_json(decoded).encode("utf-8") != raw:
            _fail("invalid_request")
        return decoded
    except SourceAdapterError:
        raise
    except Exception:
        _fail("invalid_request")


def knowledge_source_capability_manifest():
    return SourceAdapterCapabilityManifest(
        adapter_id=KNOWLEDGE_SOURCE_ADAPTER_ID,
        adapter_version=KNOWLEDGE_SOURCE_ADAPTER_VERSION,
        domain_id=KNOWLEDGE_SOURCE_DOMAIN_ID,
        source_kind=SourceKind.OTHER,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.UNKNOWN,
        owner_scope_requirement=OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        provider_constraint=ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        query_capability=QueryCapability.EXACT_READER,
        operations=tuple(SourceAdapterOperation),
        exact_reader_boundary=KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY,
        productive_default_enabled=False,
    )


def knowledge_source_registration():
    return SourceAdapterRegistration(knowledge_source_capability_manifest())


KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE = knowledge_source_capability_manifest().job_profile_ref


class KnowledgeSourceAdapter:
    """Adapt owner-scoped Harbour Knowledge records to common USI contracts."""

    def __init__(
        self,
        *,
        store: HarbourKnowledgeStore,
        principal: TrustedPrincipal,
        owner_id: str,
        cursor_signing_key: bytes,
        classification_ceiling: Classification = Classification.SENSITIVE,
    ) -> None:
        if (
            type(cursor_signing_key) is not bytes
            or not MIN_CURSOR_SIGNING_KEY_BYTES
            <= len(cursor_signing_key)
            <= MAX_CURSOR_SIGNING_KEY_BYTES
        ):
            _fail("invalid_authority")
        cursor_signing_key = bytes(cursor_signing_key)
        if type(store) is not HarbourKnowledgeStore:
            _fail("invalid_authority")
        if type(principal) is not TrustedPrincipal or type(owner_id) is not str:
            _fail("invalid_authority")
        principal_fields = (
            "_issuer_id",
            "_key_id",
            "_subject_id",
            "_owner_id",
            "_auth_context_digest",
            "_issued_at",
            "_expires_at",
            "_nonce",
            "_attestation",
        )
        try:
            principal_values = tuple(
                object.__getattribute__(principal, name)
                for name in principal_fields
            )
            sealed = object.__getattribute__(principal, "_sealed")
        except Exception:
            _fail("invalid_authority")
        if (
            any(type(value) is not str for value in principal_values)
            or type(sealed) is not bool
            or sealed is not True
        ):
            _fail("invalid_authority")
        principal_owner = object.__getattribute__(principal, "_owner_id")
        if principal_owner != owner_id:
            _fail("invalid_authority")
        if (
            type(classification_ceiling) is not Classification
            or classification_ceiling is Classification.UNKNOWN
        ):
            _fail("invalid_authority")
        try:
            _validate_adapter_enum(
                classification_ceiling, Classification, "classification_ceiling"
            )
        except Exception:
            _fail("invalid_authority")
        try:
            self._owner_scope = OwnerScope.for_subject_id(owner_id).value
        except Exception:
            _fail("invalid_authority")

        self._store = store
        self._principal = principal
        self._owner_id = owner_id
        self._cursor_signing_key = cursor_signing_key
        self._ceiling = classification_ceiling
        self._capture()

    @property
    def manifest(self) -> SourceAdapterCapabilityManifest:
        return knowledge_source_capability_manifest()

    def describe_capability(self) -> AdapterCapability:
        return AdapterCapability(
            KNOWLEDGE_SOURCE_ADAPTER_ID,
            KNOWLEDGE_SOURCE_ADAPTER_VERSION,
            self._owner_scope,
            KNOWLEDGE_SOURCE_DOMAIN_ID,
            SourceKind.OTHER,
            ContentPolicy.INLINE_LOCAL,
            self._ceiling,
            True,
            max_discovery_page=MAX_DISCOVERY_LIMIT,
            max_extract_items=MAX_PAGED_EXTRACTION_PAGE_RECORDS,
            required_job_profile_ref=self.manifest.job_profile_ref,
            max_total_extract_chars=8_388_608,
            supports_budgeted_paging=True,
        )

    def _capture(self, boundary: str | None = None) -> KnowledgeIndexSnapshot:
        try:
            snapshot = self._store.capture_index_snapshot(
                self._principal,
                owner_id=self._owner_id,
                expected_head_boundary=boundary,
            )
        except HarbourKnowledgeStoreError as error:
            _fail(
                "stale_authority"
                if getattr(error, "code", "") == "stale_authority"
                else "invalid_authority"
            )
        except Exception:
            _fail("adapter_failed")
        if (
            type(snapshot) is not KnowledgeIndexSnapshot
            or snapshot.owner_id != self._owner_id
            or type(snapshot.records) is not tuple
            or len(snapshot.records) > MAX_KNOWLEDGE_RECORDS
            or any(type(record) is not KnowledgeIndexRecord for record in snapshot.records)
        ):
            _fail("adapter_failed")
        return snapshot

    def _sid(self, record: KnowledgeIndexRecord) -> str:
        return _project_harbour_knowledge_index_record(
            self._owner_scope, record
        ).source.source_id

    def _allowed(self, record: KnowledgeIndexRecord, ceiling: Classification) -> bool:
        classification = _cls(record.classification)
        return (
            record.state == "active"
            and _RANK[classification] <= _RANK[ceiling]
            and _RANK[classification] <= _RANK[self._ceiling]
        )

    def _version(
        self, record: KnowledgeIndexRecord
    ) -> tuple[SourceRecord, SourceVersionRecord, DiscoveryItem]:
        projection = _project_harbour_knowledge_index_record(
            self._owner_scope, record
        )
        return (
            projection.source,
            projection.source_version,
            DiscoveryItem(projection.source, record.record_digest),
        )

    @staticmethod
    def _copy_scope(scope: AdapterScope) -> AdapterScope:
        if type(scope) is not AdapterScope:
            _fail("invalid_request")
        try:
            owner_scope = object.__getattribute__(scope, "owner_scope")
            ceiling = object.__getattribute__(scope, "classification_ceiling")
            source_ids = object.__getattribute__(scope, "source_ids")
        except Exception:
            _fail("invalid_request")
        if (
            type(owner_scope) is not str
            or type(ceiling) is not Classification
            or type(source_ids) is not tuple
            or len(source_ids) > MAX_ADAPTER_PAGE_SIZE
            or any(type(source_id) is not str for source_id in source_ids)
        ):
            _fail("invalid_request")
        try:
            return AdapterScope(owner_scope, ceiling, source_ids)
        except Exception:
            _fail("invalid_request")

    def discover(
        self,
        scope: AdapterScope,
        *,
        cursor: str,
        limit: int,
        time_budget_ms: int,
    ) -> DiscoveryPage:
        scope = self._copy_scope(scope)
        _classification_literal(scope.classification_ceiling)
        _classification_literal(self._ceiling)
        if (
            scope.owner_scope != self._owner_scope
            or scope.classification_ceiling not in _RANK
            or type(cursor) is not str
            or len(cursor) > MAX_ADAPTER_CURSOR_CHARS
            or type(limit) is not int
            or not 1 <= limit <= min(100, MAX_ADAPTER_PAGE_SIZE)
            or type(time_budget_ms) is not int
            or not 1 <= time_budget_ms <= 86_400_000
        ):
            _fail("invalid_request")
        effective = min(
            (scope.classification_ceiling, self._ceiling),
            key=lambda classification: _RANK[classification],
        )
        effective_literal = _classification_literal(effective)
        scope_fingerprint = _digest(
            {
                "scope": scope.owner_scope,
                "ceiling": effective_literal,
                "ids": list(scope.source_ids),
            }
        )
        last_source_id = ""
        boundary = None
        if cursor:
            payload = _dec(cursor, self._cursor_signing_key, _DISCOVERY_CURSOR_DOMAIN)
            if set(payload) != {"version", "owner", "scope", "boundary", "last"}:
                _fail("invalid_request")
            if (
                payload["version"] != KNOWLEDGE_SOURCE_ADAPTER_VERSION
                or payload["owner"] != self._owner_id
                or payload["scope"] != scope_fingerprint
                or type(payload["boundary"]) is not str
                or type(payload["last"]) is not str
            ):
                _fail("invalid_request")
            try:
                last_source_id = RecordRef(
                    RecordKind.SOURCE, payload["last"]
                ).record_id
            except Exception:
                _fail("invalid_request")
            if last_source_id != payload["last"]:
                _fail("invalid_request")
            boundary = payload["boundary"]

        started = time.monotonic()
        snapshot = self._capture(boundary)
        records = sorted(
            (
                record
                for record in snapshot.records
                if self._allowed(record, effective)
                and (not scope.source_ids or self._sid(record) in scope.source_ids)
                and self._sid(record) > last_source_id
            ),
            key=self._sid,
        )
        items = tuple(self._version(record)[2] for record in records[:limit])
        if (time.monotonic() - started) * 1_000 > time_budget_ms:
            _fail("budget_exceeded")

        next_cursor = ""
        if len(records) > len(items):
            next_cursor = _enc(
                {
                    "version": KNOWLEDGE_SOURCE_ADAPTER_VERSION,
                    "owner": self._owner_id,
                    "scope": scope_fingerprint,
                    "boundary": snapshot.captured_head_boundary,
                    "last": items[-1].source.source_id,
                },
                self._cursor_signing_key,
                _DISCOVERY_CURSOR_DOMAIN,
            )
        return DiscoveryPage(items, next_cursor)

    def _find(
        self, snapshot: KnowledgeIndexSnapshot, source_id: str
    ) -> KnowledgeIndexRecord | None:
        return next(
            (record for record in snapshot.records if self._sid(record) == source_id),
            None,
        )

    def _unavail(
        self, source_ref: RecordRef, reason: UnavailableReason
    ) -> UnavailableObservation:
        fingerprint = _digest({"source": source_ref.record_id, "reason": reason.value})
        return UnavailableObservation(source_ref, self._owner_scope, reason, fingerprint)

    def observe_version(
        self, source_ref: RecordRef
    ) -> SourceVersionRecord | UnavailableObservation:
        source_ref = _detach_input_record(
            source_ref,
            (RecordRef,),
        )
        if source_ref.record_kind is not RecordKind.SOURCE:
            _fail("invalid_request")
        record = self._find(self._capture(), source_ref.record_id)
        if record is None:
            return self._unavail(source_ref, UnavailableReason.PROVIDER_MISSING)
        if record.state != "active":
            return self._unavail(source_ref, UnavailableReason.DELETED)
        if not self._allowed(record, self._ceiling):
            return self._unavail(source_ref, UnavailableReason.POLICY_BLOCKED)
        return self._version(record)[1]

    def observe_unavailable(
        self, source_ref: RecordRef, reason: UnavailableReason
    ) -> UnavailableObservation:
        if type(reason) is not UnavailableReason:
            _fail("invalid_request")
        try:
            reason_value = object.__getattribute__(reason, "_value_")
        except Exception:
            _fail("invalid_request")
        expected_reason_values = (
            (UnavailableReason.DELETED, "deleted"),
            (UnavailableReason.ACCESS_REVOKED, "access_revoked"),
            (UnavailableReason.PROVIDER_MISSING, "provider_missing"),
            (UnavailableReason.POLICY_BLOCKED, "policy_blocked"),
        )
        expected_reason = next(
            (
                value
                for member, value in expected_reason_values
                if member is reason
            ),
            None,
        )
        if (
            type(reason_value) is not str
            or expected_reason is None
            or reason_value != expected_reason
        ):
            _fail("invalid_request")
        source_ref = _detach_input_record(source_ref, (RecordRef,))
        if source_ref.record_kind is not RecordKind.SOURCE:
            _fail("invalid_request")
        actual = self.observe_version(source_ref)
        if type(actual) is not UnavailableObservation or actual.reason is not reason:
            _fail("invalid_request")
        return actual

    def _payload_text(self, record: KnowledgeIndexRecord) -> str | None:
        media_type = record.payload_media_type.split(";", 1)[0].strip().lower()
        is_text = (
            media_type.startswith("text/")
            or media_type == "application/json"
            or media_type.endswith("+json")
        )
        if not is_text:
            return None

        payload = None
        failure_code = ""
        try:
            payload = self._store.read_payload_at_generation(
                self._principal,
                owner_id=self._owner_id,
                knowledge_id=record.knowledge_id,
                revision_id=record.revision_id,
                expected_generation=record.generation,
                expected_payload_sha256=record.payload_sha256,
            )
        except HarbourKnowledgeStoreError as error:
            failure_code = (
                "stale_authority"
                if getattr(error, "code", "") == "stale_authority"
                else "provider_missing"
            )
        except Exception:
            failure_code = "provider_missing"
        if failure_code:
            _fail(failure_code)

        text = None
        malformed = False
        try:
            text = payload.decode("utf-8", errors="strict")
            if media_type == "application/json" or media_type.endswith("+json"):
                json.loads(
                    text,
                    object_pairs_hook=_json_object,
                    parse_constant=_reject_constant,
                )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            malformed = True
        if malformed or text is None:
            _fail("provider_missing")
        return text
    def _current(
        self, version: SourceVersionRecord
    ) -> tuple[KnowledgeIndexRecord, SourceVersionRecord]:
        version = _detach_input_record(
            version,
            (SourceVersionRecord,),
            "invalid_authority",
        )
        if (
            version.owner_scope != self._owner_scope
        ):
            _fail("invalid_authority")
        record = self._find(self._capture(), version.source_id)
        if record is None or record.state != "active":
            _fail("provider_missing")
        if not self._allowed(record, self._ceiling):
            _fail("policy_blocked")

        current_version = self._version(record)[1]
        if current_version != version:
            _fail("stale_authority")
        return record, current_version

    @staticmethod
    def _validate_profile(profile: ExtractionProfile) -> ExtractionProfile:
        if type(profile) is not ExtractionProfile:
            _fail("invalid_request")
        try:
            profile_ref = object.__getattribute__(profile, "profile_ref")
            max_items = object.__getattribute__(profile, "max_items")
            max_chars = object.__getattribute__(profile, "max_chars")
            time_budget_ms = object.__getattribute__(profile, "time_budget_ms")
        except Exception:
            _fail("invalid_request")
        if (
            type(profile_ref) is not str
            or type(max_items) is not int
            or type(max_chars) is not int
            or type(time_budget_ms) is not int
            or profile_ref != KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE
            or not 1 <= max_items <= MAX_ADAPTER_PAGE_SIZE
            or not 1 <= max_chars <= MAX_EXACT_READ_CHARS
            or not 1 <= time_budget_ms <= 86_400_000
        ):
            _fail("invalid_request")
        try:
            return ExtractionProfile(profile_ref, max_items, max_chars, time_budget_ms)
        except Exception:
            _fail("invalid_request")

    @staticmethod
    def _validate_page_budget(
        budget: ExtractionPageBudget,
        profile: ExtractionProfile,
    ) -> ExtractionPageBudget:
        if type(budget) is not ExtractionPageBudget:
            _fail("invalid_request")
        try:
            max_items = object.__getattribute__(budget, "max_items")
            max_chars = object.__getattribute__(budget, "max_chars")
            time_budget_ms = object.__getattribute__(budget, "time_budget_ms")
        except Exception:
            _fail("invalid_request")
        if (
            type(max_items) is not int
            or type(max_chars) is not int
            or type(time_budget_ms) is not int
            or not 1 <= max_items <= MAX_PAGED_EXTRACTION_PAGE_RECORDS
            or not 0 <= max_chars <= MAX_EXACT_READ_CHARS
            or not 1 <= time_budget_ms <= 86_400_000
            or max_items > profile.max_items
            or max_chars > profile.max_chars
            or time_budget_ms > profile.time_budget_ms
        ):
            _fail("invalid_request")
        try:
            return ExtractionPageBudget(max_items, max_chars, time_budget_ms)
        except Exception:
            _fail("invalid_request")

    def _chunks(
        self, version: SourceVersionRecord, record: KnowledgeIndexRecord
    ) -> tuple[ChunkRecord, ...]:
        text = self._payload_text(record)
        if text is None:
            return ()

        chunks = []
        for start in range(0, len(text), MAX_TEXT_CHARS):
            end = min(len(text), start + MAX_TEXT_CHARS)
            content = text[start:end]
            locator = TextRangeLocator(start, end)
            source_version_policy = PolicyEvidence(
                RecordKind.SOURCE_VERSION,
                version.source_version_id,
                version.source_id,
                version.source_version_id,
                self._owner_scope,
                version.classification,
                ContentPolicy.INLINE_LOCAL,
                "",
            )
            chunks.append(
                ChunkRecord(
                    version.source_id,
                    version.source_version_id,
                    locator,
                    KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
                    content_hash(content),
                    self._owner_scope,
                    version.classification,
                    ContentPolicy.INLINE_LOCAL,
                    source_version_policy,
                    content,
                    version.indexed_at,
                    version.valid_from,
                )
            )

        return tuple(
            sorted(
                chunks,
                key=lambda chunk: (RecordKind.CHUNK.value, chunk.chunk_id),
            )
        )

    def extract(
        self, version: SourceVersionRecord, profile: ExtractionProfile
    ) -> ExtractionResult:
        profile = self._validate_profile(profile)
        version = _detach_input_record(version, (SourceVersionRecord,), "invalid_authority")
        started = time.monotonic()
        record, current_version = self._current(version)
        chunks = self._chunks(current_version, record)
        elapsed_ms = (time.monotonic() - started) * 1_000
        if elapsed_ms > profile.time_budget_ms:
            _fail("budget_exceeded")
        if (
            len(chunks) > profile.max_items
            or sum(len(chunk.content or "") for chunk in chunks) > profile.max_chars
        ):
            _fail("budget_exceeded")
        return ExtractionResult(current_version, chunks=chunks)
    def extract_page(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
        *,
        cursor: str,
        sequence_start: int,
    ) -> PagedExtractionPage:
        profile = self._validate_profile(profile)
        return self._extract_page(
            source_version,
            profile,
            ExtractionPageBudget(
                min(profile.max_items, MAX_PAGED_EXTRACTION_PAGE_RECORDS),
                profile.max_chars,
                profile.time_budget_ms,
            ) if type(profile) is ExtractionProfile else None,
            cursor=cursor,
            sequence_start=sequence_start,
        )

    def extract_page_budgeted(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
        budget: ExtractionPageBudget,
        *,
        cursor: str,
        sequence_start: int,
    ) -> PagedExtractionPage:
        return self._extract_page(
            source_version,
            profile,
            budget,
            cursor=cursor,
            sequence_start=sequence_start,
        )

    @staticmethod
    def _copy_exact_read_request(request: ExactReadRequest) -> ExactReadRequest:
        if type(request) is not ExactReadRequest:
            _fail("invalid_request")
        try:
            evidence = object.__getattribute__(request, "evidence")
            max_chars = object.__getattribute__(request, "max_chars")
        except Exception:
            _fail("invalid_request")
        if type(max_chars) is not int or not 1 <= max_chars <= MAX_EXACT_READ_CHARS:
            _fail("invalid_request")
        evidence = _detach_input_record(evidence, (EvidenceRef,))
        try:
            return ExactReadRequest(evidence, max_chars)
        except Exception:
            _fail("invalid_request")

    @staticmethod
    def _copy_policy_read_context(
        policy_context: PolicyReadContext,
    ) -> PolicyReadContext:
        if type(policy_context) is not PolicyReadContext:
            _fail("policy_blocked")
        try:
            owner_scope = object.__getattribute__(policy_context, "owner_scope")
            ceiling = object.__getattribute__(
                policy_context, "classification_ceiling"
            )
            allow_inline = object.__getattribute__(
                policy_context, "allow_inline_content"
            )
        except Exception:
            _fail("policy_blocked")
        if (
            type(owner_scope) is not str
            or type(ceiling) is not Classification
            or type(allow_inline) is not bool
        ):
            _fail("policy_blocked")
        try:
            return PolicyReadContext(owner_scope, ceiling, allow_inline)
        except Exception:
            _fail("policy_blocked")

    def _extract_page(
        self,
        source_version: SourceVersionRecord,
        profile: ExtractionProfile,
        budget: ExtractionPageBudget | None,
        *,
        cursor: str,
        sequence_start: int,
    ) -> PagedExtractionPage:
        profile = self._validate_profile(profile)
        budget = self._validate_page_budget(budget, profile)
        if (
            type(source_version) is not SourceVersionRecord
        ):
            _fail("invalid_authority")
        source_version = _detach_input_record(
            source_version,
            (SourceVersionRecord,),
            "invalid_authority",
        )
        if source_version.owner_scope != self._owner_scope:
            _fail("invalid_authority")
        if (
            type(sequence_start) is not int
            or not 0 <= sequence_start <= MAX_PAGED_EXTRACTION_RECORDS
            or type(cursor) is not str
            or len(cursor) > MAX_ADAPTER_CURSOR_CHARS
        ):
            _fail("invalid_request")

        # Validate caller-controlled paging data before metadata or payload reads.
        binding = _digest(
            {
                "source_version_id": source_version.source_version_id,
                "profile_ref": profile.profile_ref,
                "max_items": profile.max_items,
                "max_chars": profile.max_chars,
                "time_budget_ms": profile.time_budget_ms,
            }
        )
        position = 0
        if cursor:
            payload = _dec(cursor, self._cursor_signing_key, _EXTRACTION_CURSOR_DOMAIN)
            if set(payload) != {"binding", "sequence"}:
                _fail("invalid_request")
            candidate_sequence = payload["sequence"]
            if (
                payload["binding"] != binding
                or type(candidate_sequence) is not int
                or not 0 <= candidate_sequence <= MAX_PAGED_EXTRACTION_RECORDS
                or candidate_sequence != sequence_start
            ):
                _fail("invalid_request")
            position = candidate_sequence
        elif sequence_start != 0:
            _fail("invalid_request")

        started = time.monotonic()
        record, current_version = self._current(source_version)
        chunks = self._chunks(current_version, record)
        if position > len(chunks):
            _fail("invalid_request")

        selected: list[ChunkRecord] = []
        inline_chars = 0
        page_limit = min(budget.max_items, MAX_PAGED_EXTRACTION_PAGE_RECORDS)
        while position + len(selected) < len(chunks) and len(selected) < page_limit:
            chunk = chunks[position + len(selected)]
            chunk_chars = len(chunk.content or "")
            if selected and inline_chars + chunk_chars > budget.max_chars:
                break
            if not selected and chunk_chars > budget.max_chars:
                _fail("budget_exceeded")
            selected.append(chunk)
            inline_chars += chunk_chars
        if position < len(chunks) and not selected:
            _fail("budget_exceeded")

        next_sequence = position + len(selected)
        terminal = next_sequence == len(chunks)
        next_cursor = ""
        final_count = None
        final_digest = ""
        if terminal:
            final_count = len(chunks)
            digest_input = b"\n".join(
                canonical_json(_capture_exact_contract_value(chunk)).encode("utf-8")
                for chunk in chunks
            )
            final_digest = "sha256:" + hashlib.sha256(digest_input).hexdigest()
        else:
            if next_sequence <= position:
                _fail("invalid_request")
            next_cursor = _enc(
                {"binding": binding, "sequence": next_sequence},
                self._cursor_signing_key,
                _EXTRACTION_CURSOR_DOMAIN,
            )

        if (time.monotonic() - started) * 1_000 > budget.time_budget_ms:
            _fail("budget_exceeded")
        return PagedExtractionPage(
            source_version=current_version,
            sequence_start=position,
            records=tuple(selected),
            next_cursor=next_cursor,
            final_record_count=final_count,
            final_records_digest=final_digest,
        )

    def read_exact(
        self,
        request: ExactReadRequest,
        policy_context: PolicyReadContext,
    ) -> ExactReadResult:
        request = self._copy_exact_read_request(request)
        policy_context = self._copy_policy_read_context(policy_context)
        _classification_literal(policy_context.classification_ceiling)
        _classification_literal(self._ceiling)
        if (
            policy_context.owner_scope != self._owner_scope
            or policy_context.allow_inline_content is not True
            or policy_context.classification_ceiling not in _RANK
        ):
            _fail("policy_blocked")

        evidence = request.evidence
        if evidence.record_kind is not RecordKind.CHUNK:
            _fail("invalid_request")

        ceiling = min(
            (policy_context.classification_ceiling, self._ceiling),
            key=lambda classification: _RANK[classification],
        )
        record = self._find(self._capture(), evidence.source_id)
        if record is None or not self._allowed(record, ceiling):
            _fail("policy_blocked")

        current_version = self._version(record)[1]
        chunks = self._chunks(current_version, record)
        match = next(
            (chunk for chunk in chunks if chunk.chunk_id == evidence.record_id),
            None,
        )
        if match is None or match.evidence_ref() != evidence:
            _fail("invalid_request")

        text = match.content or ""
        if content_hash(text) != match.content_hash:
            _fail("invalid_request")
        content = text[: request.max_chars]
        return ExactReadResult(
            content,
            content_hash(content),
            len(content) < len(text),
        )


HARBOUR_KNOWLEDGE_METADATA_REFERENCE_SCHEMA = "odysseus.harbour.knowledge_metadata_reference.v1"
_HARBOUR_MAX_HEADS = 10_000
_HARBOUR_MAX_METADATA_BYTES = 16_777_216
_HARBOUR_DIGEST_FIELDS = (
    "knowledge_id_digest", "revision_id_digest", "payload_sha256",
    "classification_digest", "retention_digest", "provenance_digest",
    "source_revisions_digest", "references_digest", "revision_metadata_digest",
    "head_metadata_digest", "record_digest",
)
_HARBOUR_HEAD_FIELDS = _HARBOUR_DIGEST_FIELDS + (
    "revision", "generation", "state", "knowledge_kind",
)


@dataclass(frozen=True, slots=True, repr=False)
class HarbourKnowledgeMetadataReference:
    """Detached metadata only; never a USI source or authenticated origin."""

    schema_id: str
    snapshot_digest: str
    owner_ref_digest: str
    record_digest: str
    knowledge_id_digest: str
    revision: int
    generation: int
    state: str
    knowledge_kind: str
    payload_sha256: str
    classification_digest: str
    retention_digest: str
    provenance_digest: str
    source_revisions_digest: str
    references_digest: str
    revision_metadata_digest: str
    head_metadata_digest: str

    def __repr__(self) -> str:
        return "HarbourKnowledgeMetadataReference(<metadata-only>)"


@dataclass(frozen=True, slots=True, repr=False)
class HarbourKnowledgeIndexProjection:
    """Canonical content-free USI parents for one Harbour index record."""

    source: SourceRecord
    source_version: SourceVersionRecord

    def __repr__(self) -> str:
        return "HarbourKnowledgeIndexProjection(<metadata-only>)"


def _project_harbour_knowledge_index_record(
    owner_scope: str, record: KnowledgeIndexRecord,
) -> HarbourKnowledgeIndexProjection:
    if type(owner_scope) is not str or type(record) is not KnowledgeIndexRecord:
        _fail("invalid_snapshot")
    try:
        detached = KnowledgeIndexRecord(**{
            item.name: object.__getattribute__(record, item.name)
            for item in fields(KnowledgeIndexRecord)
        })
        if (
            type(detached.knowledge_id) is not str
            or not 1 <= len(detached.knowledge_id) <= 128
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", detached.knowledge_id) is None
            or type(detached.revision_id) is not str
            or not 1 <= len(detached.revision_id) <= 128
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", detached.revision_id) is None
            or type(detached.revision) is not int
            or not 1 <= detached.revision <= 2**63 - 1
            or type(detached.generation) is not int
            or not detached.revision <= detached.generation <= 2**63 - 1
            or type(detached.state) is not str
            or detached.state not in HEAD_STATES
            or type(detached.record_digest) is not str
            or _SHA256_RE.fullmatch(detached.record_digest) is None
            or type(detached.classification) is not str
            or detached.classification not in {"public", "private", "sensitive", "secret", "unknown"}
        ):
            _fail("invalid_snapshot")
        canonical_ref = _opaque(owner_scope, detached.knowledge_id)
        source_id = make_source_id(owner_scope, SourceKind.OTHER, canonical_ref)
        classification = _cls(detached.classification)
        timestamp = detached.created_at
        source = SourceRecord(
            owner_scope, SourceKind.OTHER, canonical_ref, classification,
            ContentPolicy.INLINE_LOCAL, KNOWLEDGE_SOURCE_ADAPTER_ID,
            timestamp, timestamp, timestamp, timestamp,
        )
        version = SourceVersionRecord(
            source_id, _vref(detached), detached.payload_sha256,
            KNOWLEDGE_SOURCE_ADAPTER_ID, timestamp, owner_scope,
            classification, ContentPolicy.INLINE_LOCAL,
            source.policy_evidence(), timestamp, timestamp, timestamp,
            timestamp, timestamp,
        )
        return HarbourKnowledgeIndexProjection(source, version)
    except Exception:
        _fail("invalid_snapshot")


def project_harbour_knowledge_index_records(
    owner_scope: str, record: KnowledgeIndexRecord,
) -> HarbourKnowledgeIndexProjection:
    """Purely project one detached Harbour metadata record to canonical USI parents."""
    return _project_harbour_knowledge_index_record(owner_scope, record)


def _harbour_metadata_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _harbour_metadata_digest(kind: str, value: object) -> str:
    encoded = _harbour_metadata_json({
        "schema_id": KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA, "kind": kind, "value": value,
    })
    return "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _harbour_metadata_text(value: object, *, digest: bool = False) -> str:
    # Exact types and length precede regex, encoding and other input work.
    if type(value) is not str or len(value) > 128:
        raise ValueError
    if digest and (len(value) != 71 or _SHA256_RE.fullmatch(value) is None):
        raise ValueError
    return value.encode("ascii").decode("ascii")


def _harbour_metadata_reference_frame(snapshot: object) -> tuple[HarbourKnowledgeMetadataReference, ...] | None:
    try:
        if type(snapshot) is not KnowledgeMigrationSnapshot:
            return None
        # Read every input slot once. Only detached built-in scalars and local
        # containers participate in validation, hashing and output construction.
        schema, owner, boundary, heads, digest = (
            snapshot.schema_id, snapshot.owner_ref_digest, snapshot.captured_head_boundary,
            snapshot.heads, snapshot.snapshot_digest,
        )
        schema = _harbour_metadata_text(schema)
        if schema != KNOWLEDGE_MIGRATION_SNAPSHOT_SCHEMA:
            return None
        owner = _harbour_metadata_text(owner, digest=True)
        boundary = _harbour_metadata_text(boundary, digest=True)
        digest = _harbour_metadata_text(digest, digest=True)
        if type(heads) is not tuple or len(heads) > _HARBOUR_MAX_HEADS:
            return None
        records = []
        total_bytes = 0
        previous_id = None
        for head in heads:
            if type(head) is not KnowledgeHeadMetadata:
                return None
            record = {name: getattr(head, name) for name in _HARBOUR_HEAD_FIELDS}
            for name in _HARBOUR_DIGEST_FIELDS:
                record[name] = _harbour_metadata_text(record[name], digest=True)
            for name in ("revision", "generation"):
                if type(record[name]) is not int or not 1 <= record[name] <= 2**63 - 1:
                    return None
            if record["generation"] < record["revision"]:
                return None
            for name, allowed in (("state", HEAD_STATES), ("knowledge_kind", KNOWLEDGE_KINDS)):
                record[name] = _harbour_metadata_text(record[name])
                if record[name] not in allowed:
                    return None
            identity = record["knowledge_id_digest"]
            if previous_id is not None and identity <= previous_id:
                return None
            previous_id = identity
            total_bytes += len(_harbour_metadata_json(record))
            if total_bytes > _HARBOUR_MAX_METADATA_BYTES:
                return None
            records.append(record)

        # Inner digests are intentionally opaque: their R/T source values are
        # not exported. Verify only the complete exported record and envelopes.
        for record in records:
            metadata = {name: value for name, value in record.items() if name != "record_digest"}
            if record["record_digest"] != _harbour_metadata_digest("head_record", {
                "owner_ref_digest": owner, "metadata": metadata,
            }):
                return None
        if boundary != _harbour_metadata_digest("head_boundary", {
            "owner_ref_digest": owner, "record_digests": [r["record_digest"] for r in records],
        }):
            return None
        if digest != _harbour_metadata_digest("snapshot", {
            "schema_id": schema, "owner_ref_digest": owner,
            "captured_head_boundary": boundary, "heads": records,
        }):
            return None
        return tuple(HarbourKnowledgeMetadataReference(
            schema_id=HARBOUR_KNOWLEDGE_METADATA_REFERENCE_SCHEMA,
            snapshot_digest=digest, owner_ref_digest=owner,
            **{name: value for name, value in record.items() if name != "revision_id_digest"},
        ) for record in records)
    except Exception:
        return None


def project_harbour_knowledge_metadata(snapshot: KnowledgeMigrationSnapshot) -> tuple[HarbourKnowledgeMetadataReference, ...]:
    """Validate exported H1R-05A metadata and return detached references.

    This pure conversion does not capture, authenticate origin, establish
    freshness or create USI authority. Self-consistent synthetic snapshots are
    accepted; opaque inner digests are copied, never re-authenticated.
    """
    references = _harbour_metadata_reference_frame(snapshot)
    if references is None:
        _fail("invalid_snapshot")
    return references


__all__ = [
    "HARBOUR_KNOWLEDGE_METADATA_REFERENCE_SCHEMA",
    "HarbourKnowledgeMetadataReference",
    "project_harbour_knowledge_metadata",
    "HarbourKnowledgeIndexProjection",
    "project_harbour_knowledge_index_records",
    "KNOWLEDGE_SOURCE_ADAPTER_ID",
    "KNOWLEDGE_SOURCE_ADAPTER_VERSION",
    "KNOWLEDGE_SOURCE_DOMAIN_ID",
    "KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY",
    "KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE",
    "MAX_DISCOVERY_LIMIT",
    "MAX_KNOWLEDGE_RECORDS",
    "KnowledgeSourceAdapter",
    "KnowledgeSourceAdapterError",
    "knowledge_source_capability_manifest",
    "knowledge_source_registration",
]
