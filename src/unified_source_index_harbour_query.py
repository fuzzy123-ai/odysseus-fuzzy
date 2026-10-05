"""Lifecycle fence for Harbour backed records returned by lexical search."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping

from src.harbour_authority import (
    TrustedPrincipal,
)
from src.harbour_knowledge_store import (
    KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA,
    HarbourKnowledgeStore,
    KnowledgeIndexRecord,
    KnowledgeIndexSnapshot,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    RecordKind,
    SourceRecord,
    SourceKind,
    SourceVersionRecord,
    make_chunk_id,
    make_source_id,
    make_source_version_id,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_ADAPTER_ID,
    HarbourKnowledgeIndexProjection,
    project_harbour_knowledge_index_records,
)
from src.unified_source_index_stores import (
    StoreSnapshot,
    StoreReadTransaction,
    StoredRecord,
)


_CLASS_RANK = {
    Classification.PUBLIC: 0,
    Classification.PRIVATE: 1,
    Classification.SENSITIVE: 2,
    Classification.SECRET: 3,
    Classification.UNKNOWN: 4,
}
_POLICY_RANK = {
    ContentPolicy.INLINE_LOCAL: 0,
    ContentPolicy.REFERENCE_ONLY: 1,
    ContentPolicy.METADATA_ONLY: 2,
}
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class HarbourKnowledgeQueryGuardError(ValueError):
    """Bounded failure while capturing lifecycle authority."""


@dataclass(frozen=True, slots=True)
class _ValidatedStoreRead:
    """Stable read members captured once before a guarded page uses the store."""

    snapshot: StoreSnapshot
    closed: bool
    get: Callable[..., Any]
    list_records: Callable[..., Any]
    close: Callable[..., Any]


def _capture_store_read(
    read: object,
    *,
    expected_snapshot: StoreSnapshot | None = None,
) -> _ValidatedStoreRead:
    """Capture mutable transaction members once and reject malformed boundaries."""
    missing = object()
    close: object = missing
    try:
        close = getattr(read, "close")
    except Exception:
        pass

    failed = False

    def capture(name: str) -> object:
        nonlocal failed
        try:
            return getattr(read, name)
        except Exception:
            failed = True
            return missing

    snapshot = capture("snapshot")
    closed = capture("closed")
    get = capture("get")
    list_records = capture("list_records")
    if (
        failed
        or type(snapshot) is not StoreSnapshot
        or (expected_snapshot is not None and snapshot != expected_snapshot)
        or type(closed) is not bool
        or closed is not False
        or not callable(get)
        or not callable(list_records)
        or not callable(close)
    ):
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise HarbourKnowledgeQueryGuardError(
            "read transaction is invalid"
        ) from None
    return _ValidatedStoreRead(snapshot, closed, get, list_records, close)


@dataclass(frozen=True, slots=True)
class HarbourKnowledgeQueryScope:
    owner_scope: str
    owner_id: str
    classification_ceiling: Classification
    requested_classification_ceiling: Classification
    query_fingerprint: str
    domain: str | None
    captured_head_boundary: str
    snapshot_digest: str
    _active: Mapping[str, HarbourKnowledgeIndexProjection]

    def allows(self, chunk: ChunkRecord, read: StoreReadTransaction) -> bool:
        """Validate both owner-scoped parents before the caller reads chunk content."""
        validated_read = (
            read if type(read) is _ValidatedStoreRead else _capture_store_read(read)
        )
        if type(chunk) is not ChunkRecord:
            return False
        try:
            source_item = validated_read.get(
                RecordKind.SOURCE, chunk.source_id,
                owner_scope=self.owner_scope, include_tombstone=True,
            )
            version_item = validated_read.get(
                RecordKind.SOURCE_VERSION, chunk.source_version_id,
                owner_scope=self.owner_scope, include_tombstone=True,
            )
        except Exception as exc:
            raise HarbourKnowledgeQueryGuardError("parent read failed") from None

        if (
            type(source_item) is not StoredRecord
            or type(version_item) is not StoredRecord
            or type(source_item.record) is not SourceRecord
            or type(version_item.record) is not SourceVersionRecord
        ):
            return False
        source, version = source_item.record, version_item.record
        try:
            canonical_source_id = make_source_id(
                source.owner_scope, source.source_kind, source.canonical_ref,
            )
            canonical_version_id = make_source_version_id(
                version.source_id, version.revision_ref, version.content_hash,
            )
            canonical_chunk_id = make_chunk_id(
                chunk.source_version_id, chunk.locator, chunk.extractor_profile_ref,
            )
        except Exception:
            return False
        if (
            source.source_id != canonical_source_id
            or version.source_version_id != canonical_version_id
            or chunk.chunk_id != canonical_chunk_id
            or source_item.record_id != chunk.source_id
            or version_item.record_id != chunk.source_version_id
            or source_item.owner_scope != self.owner_scope
            or version_item.owner_scope != self.owner_scope
            or source.source_id != chunk.source_id
            or version.source_version_id != chunk.source_version_id
            or version.source_id != source.source_id
            or chunk.source_id != source.source_id
            or chunk.source_version_id != version.source_version_id
            or any(
                record.owner_scope != self.owner_scope
                for record in (source, version, chunk)
            )
            or source.provider_ref != version.provider_ref
            or not source.provider_ref
        ):
            return False

        if (
            source.policy_evidence() != version.policy_evidence
            or chunk.policy_evidence != version.policy_evidence_ref()
        ):
            return False
        if (
            _CLASS_RANK[version.classification] < _CLASS_RANK[source.classification]
            or _CLASS_RANK[chunk.classification] < _CLASS_RANK[version.classification]
            or _POLICY_RANK[version.content_policy] < _POLICY_RANK[source.content_policy]
            or _POLICY_RANK[chunk.content_policy] < _POLICY_RANK[version.content_policy]
            or _CLASS_RANK[chunk.classification]
            > _CLASS_RANK[self.classification_ceiling]
            or _CLASS_RANK[chunk.classification]
            > _CLASS_RANK[self.requested_classification_ceiling]
        ):
            return False

        harbour_marker = (
            source.source_id in self._active
            or source.provider_ref == KNOWLEDGE_SOURCE_ADAPTER_ID
            or version.provider_ref == KNOWLEDGE_SOURCE_ADAPTER_ID
            or source.canonical_ref.startswith("harbour:")
            or version.revision_ref.startswith("harbour-revision:")
        )
        if harbour_marker:
            projection = self._active.get(source.source_id)
            if projection is None or projection.source != source or projection.source_version != version:
                return False
        if self.domain is not None:
            expected_kind = {
                "code": SourceKind.CODE,
                "document": SourceKind.DOCUMENT,
                "memory": SourceKind.MEMORY,
            }.get(self.domain)
            if expected_kind is not None:
                if source.source_kind is not expected_kind or harbour_marker:
                    return False
            elif self.domain == "native_knowledge":
                if source.source_kind is not SourceKind.OTHER or not harbour_marker:
                    return False
            else:
                return False
        return True


class HarbourKnowledgeQueryGuard:
    """Capture one authenticated Harbour metadata snapshot for a lexical page."""

    def __init__(
        self,
        *,
        store: HarbourKnowledgeStore,
        principal: TrustedPrincipal,
        owner_id: str,
        owner_scope: str,
        classification_ceiling: Classification,
    ) -> None:
        if (
            type(store) is not HarbourKnowledgeStore
            or type(principal) is not TrustedPrincipal
            or type(owner_id) is not str
            or type(owner_scope) is not str
            or type(classification_ceiling) is not Classification
        ):
            raise HarbourKnowledgeQueryGuardError("guard authority is invalid")
        self._store = store
        self._principal = principal
        self._owner_id = owner_id
        self._owner_scope = owner_scope
        self._ceiling = classification_ceiling

    def capture(
        self,
        *,
        owner_scope: str,
        requested_ceiling: Classification,
        query_fingerprint: str,
        domain: str | None = None,
        expected_head_boundary: str | None = None,
        expected_snapshot_digest: str | None = None,
    ) -> HarbourKnowledgeQueryScope:
        try:
            owner_id = self._owner_id
            expected_scope = OwnerScope.for_subject_id(owner_id).value
            if (
                self._principal.owner_id != owner_id
                or self._owner_scope != expected_scope
                or type(owner_scope) is not str
                or owner_scope != self._owner_scope
                or type(requested_ceiling) is not Classification
                or _CLASS_RANK[requested_ceiling] > _CLASS_RANK[self._ceiling]
                or type(query_fingerprint) is not str
                or _DIGEST_RE.fullmatch(query_fingerprint) is None
                or (
                    domain is not None
                    and (type(domain) is not str or domain not in {
                        "code", "document", "memory", "native_knowledge",
                    })
                )
                or (
                    expected_head_boundary is not None
                    and (
                        type(expected_head_boundary) is not str
                        or _DIGEST_RE.fullmatch(expected_head_boundary) is None
                    )
                )
                or (
                    expected_snapshot_digest is not None
                    and (
                        type(expected_snapshot_digest) is not str
                        or _DIGEST_RE.fullmatch(expected_snapshot_digest) is None
                    )
                )
            ):
                raise HarbourKnowledgeQueryGuardError("owner binding is invalid")
            snapshot = self._store.capture_index_snapshot(
                self._principal,
                owner_id=owner_id,
                expected_head_boundary=expected_head_boundary,
            )
        except HarbourKnowledgeQueryGuardError:
            raise
        except Exception:
            raise HarbourKnowledgeQueryGuardError("Harbour snapshot capture failed") from None

        if (
            type(snapshot) is not KnowledgeIndexSnapshot
            or snapshot.schema_id != KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA
            or snapshot.owner_id != owner_id
            or type(snapshot.records) is not tuple
            or len(snapshot.records) > 10_000
            or type(snapshot.captured_head_boundary) is not str
            or type(snapshot.snapshot_digest) is not str
            or _DIGEST_RE.fullmatch(snapshot.captured_head_boundary) is None
            or _DIGEST_RE.fullmatch(snapshot.snapshot_digest) is None
            or (
                expected_snapshot_digest is not None
                and not hmac.compare_digest(
                    snapshot.snapshot_digest, expected_snapshot_digest,
                )
            )
        ):
            raise HarbourKnowledgeQueryGuardError("Harbour snapshot is invalid")

        active: dict[str, HarbourKnowledgeIndexProjection] = {}
        previous_id = ""
        try:
            for record in snapshot.records:
                if type(record) is not KnowledgeIndexRecord or record.knowledge_id <= previous_id:
                    raise ValueError
                previous_id = record.knowledge_id
                if record.state == "active":
                    projection = project_harbour_knowledge_index_records(
                        self._owner_scope, record,
                    )
                    active[projection.source.source_id] = projection
        except Exception:
            raise HarbourKnowledgeQueryGuardError("Harbour snapshot record is invalid") from None

        return HarbourKnowledgeQueryScope(
            self._owner_scope,
            owner_id,
            self._ceiling,
            requested_ceiling,
            query_fingerprint,
            domain,
            snapshot.captured_head_boundary,
            snapshot.snapshot_digest,
            MappingProxyType(active),
        )


__all__ = [
    "HarbourKnowledgeQueryGuard",
    "HarbourKnowledgeQueryGuardError",
    "HarbourKnowledgeQueryScope",
]
