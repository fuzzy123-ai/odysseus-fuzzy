"""Authenticated read-only composition for the active Harbour Knowledge path."""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from typing import Any, Callable, Iterator

from src.constants import APP_KEY_FILE, DATA_DIR
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_staging_destination import HarbourKnowledgeStagingDestinationService
from src.harbour_knowledge_store import (
    HarbourKnowledgeStore,
    HarbourKnowledgeStoreError,
    KnowledgeIndexRecord,
    KnowledgeIndexSnapshot,
    KnowledgeInboxSignal,
    KnowledgeInboxSignalSnapshot,
    KNOWLEDGE_KINDS,
    KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA,
)
from src.unified_source_index_adapters import (
    AdapterScope, ExtractionPageBudget, ExtractionProfile, UnavailableObservation,
    MAX_PAGED_EXTRACTION_PAGE_RECORDS,
)
from src.unified_source_index_contract import (
    Classification, RecordKind, RecordRef, SourceRecord, SourceVersionRecord,
)
from src.unified_source_index_harbour_query import HarbourKnowledgeQueryGuard
from src.unified_source_index_lexical import (
    LexicalRetrievalError, UnifiedSourceIndexLexicalRetriever,
    _CLASSIFICATION_RANK, _decode_guarded_cursor, _encode_guarded_cursor,
    _is_stale, _lexical_score, _result_sort_key, _snippet, build_fts_expression,
)
from src.unified_source_index_migrations import validate_schema
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_query import (
    LexicalProviderAdapter,
    ProviderRegistration,
    UnifiedSourceIndexQueryPlanner,
)
from src.unified_source_index_query_contract import (
    LexicalMatchMode, LexicalQuery, QueryResultItem, QueryResultPage,
    QueryScoreComponents,
)
from src.unified_source_index_runtime_config import (
    UnifiedSourceIndexRuntimeConfig,
    UnifiedSourceIndexRuntimeMode,
)
from src.unified_source_index_runtime_contract import RuntimeGeneration
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import StoredRecord
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
    KnowledgeSourceAdapter,
    knowledge_source_capability_manifest,
    project_harbour_knowledge_index_records,
)
from src.private_context_confirmation import (
    ContextManifest,
    ConfirmationError,
    DisclosureBinding,
    ManifestItem,
    VerifiedDisclosure,
)
from src.private_context_confirmation_app import (
    ChatConfirmationRepository,
    ContextSelection,
    CookieConfirmationAuthority,
    PrivateContextConfirmationApp,
    SourceSelection,
)


_ZERO = "sha256:" + "0" * 64
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_KNOWLEDGE_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeInboxSnapshot:
    """Detached, content-free capture used only by the unified Inbox."""

    owner_binding_digest: str
    selection_binding_digest: str
    canonical_generation_digest: str
    signal_snapshot_digest: str
    captured_head_boundary: str
    items: tuple[KnowledgeInboxSignal, ...]
    clipped: bool
    binding_digest: str

    def __repr__(self) -> str:
        return "KnowledgeInboxSnapshot(<redacted>)"


_CONTEXT_SHA_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_CONTEXT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_CONTEXT_MAX_PAGE = 1_048_576
_CONTEXT_MAX_JSON_INT = (1 << 53) - 1


class KnowledgeContextError(Exception):
    """Closed, content-free error boundary for Knowledge context reads."""

    _CODES = frozenset({"capture_unavailable", "request_unavailable", "read_unavailable"})

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self._CODES:
            raise ValueError("invalid knowledge context error code") from None
        self.code = code
        super().__init__(code)


def _context_sha(value: object) -> str:
    if type(value) is not str or _CONTEXT_SHA_RE.fullmatch(value) is None:
        raise ValueError("knowledge context unavailable")
    return value[7:] if value.startswith("sha256:") else value


def _context_digest(value: object) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("knowledge context unavailable") from None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _context_framed_digest(domain: str, value: object) -> str:
    if type(domain) is not str or not domain.isascii() or not domain:
        raise ValueError("knowledge context unavailable")
    try:
        domain_bytes = ("odysseus.harbour.knowledge.context.v2\0" + domain).encode("ascii")
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("knowledge context unavailable") from None
    framed = (
        len(domain_bytes).to_bytes(4, "big") + domain_bytes
        + len(encoded).to_bytes(8, "big") + encoded
    )
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _context_opaque(value: object) -> str:
    if type(value) is not str or _CONTEXT_REF_RE.fullmatch(value) is None:
        raise ValueError("knowledge context unavailable")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeContextManifestItem:
    """Detached Knowledge metadata; no title, payload or raw provenance."""

    source: str = field(repr=False)
    selection_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    boundary_sha256: str = field(repr=False)
    record_sha256: str = field(repr=False)
    payload_sha256: str = field(repr=False)
    classification: str = field(repr=False)
    provenance_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.source) is not str or self.source != "knowledge":
            raise ValueError("knowledge context unavailable")
        if type(self.classification) is not str or self.classification not in {"public", "private"}:
            raise ValueError("knowledge context unavailable")
        for value in (self.selection_ref, self.revision_ref, self.provenance_ref):
            _context_opaque(value)
        for value in (self.boundary_sha256, self.record_sha256, self.payload_sha256):
            _context_sha(value)

    def __repr__(self) -> str:
        return "KnowledgeContextManifestItem(<metadata-only>)"

    def _canonical(self) -> dict[str, str]:
        return {
            "boundary_sha256": _context_sha(self.boundary_sha256),
            "classification": self.classification,
            "payload_sha256": _context_sha(self.payload_sha256),
            "provenance_ref": self.provenance_ref,
            "record_sha256": _context_sha(self.record_sha256),
            "revision_ref": self.revision_ref,
            "selection_ref": self.selection_ref,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeContextManifest:
    items: tuple[KnowledgeContextManifestItem, ...] = field(repr=False)
    owner_binding_digest: str = field(repr=False)
    selection_binding_digest: str = field(repr=False)
    canonical_generation_digest: str = field(repr=False)
    usi_snapshot_digest: str = field(repr=False)
    captured_head_boundary: str = field(repr=False)
    binding_digest: str = field(repr=False)
    _digest_value: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (type(self.items) is not tuple or not 1 <= len(self.items) <= 300
                or any(type(item) is not KnowledgeContextManifestItem for item in self.items)):
            raise ValueError("knowledge context unavailable")
        canonical = tuple(item._canonical() for item in self.items)
        if canonical != tuple(sorted(canonical, key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))):
            raise ValueError("knowledge context unavailable")
        if len({item.selection_ref for item in self.items}) != len(self.items):
            raise ValueError("knowledge context unavailable")
        for value in (
            self.owner_binding_digest, self.selection_binding_digest,
            self.canonical_generation_digest, self.usi_snapshot_digest,
            self.captured_head_boundary, self.binding_digest,
        ):
            _context_sha(value)
        digest = _context_digest({
            "binding_digest": _context_sha(self.binding_digest),
            "captured_head_boundary": _context_sha(self.captured_head_boundary),
            "canonical_generation_digest": _context_sha(self.canonical_generation_digest),
            "items": canonical,
            "owner_binding_digest": _context_sha(self.owner_binding_digest),
            "selection_binding_digest": _context_sha(self.selection_binding_digest),
            "usi_snapshot_digest": _context_sha(self.usi_snapshot_digest),
        })
        object.__setattr__(self, "_digest_value", digest)

    @property
    def digest(self) -> str:
        return self._digest_value

    @property
    def manifest_digest(self) -> str:
        return self._digest_value

    def __repr__(self) -> str:
        return "KnowledgeContextManifest(<metadata-only>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeContextPageRequest:
    selection: ContextSelection = field(repr=False)
    target: SourceSelection = field(repr=False)
    chat_session_id: str = field(repr=False)
    expected_knowledge_manifest_digest: str = field(repr=False)
    offset: int = field(repr=False)
    limit: int = field(repr=False)
    disclosure_message_id: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.selection) is not ContextSelection or type(self.target) is not SourceSelection:
            raise ValueError("knowledge context request unavailable")
        if self.target.source != "knowledge" or self.target not in self.selection.items:
            raise ValueError("knowledge context request unavailable")
        if type(self.chat_session_id) is not str or _CONTEXT_REF_RE.fullmatch(self.chat_session_id) is None:
            raise ValueError("knowledge context request unavailable")
        _context_sha(self.expected_knowledge_manifest_digest)
        if type(self.offset) is not int or isinstance(self.offset, bool) or self.offset < 0:
            raise ValueError("knowledge context request unavailable")
        if (type(self.limit) is not int or isinstance(self.limit, bool)
                or not 1 <= self.limit <= _CONTEXT_MAX_PAGE):
            raise ValueError("knowledge context request unavailable")
        if self.disclosure_message_id is not None and (
            type(self.disclosure_message_id) is not int
            or isinstance(self.disclosure_message_id, bool)
            or not 1 <= self.disclosure_message_id <= _CONTEXT_MAX_JSON_INT
        ):
            raise ValueError("knowledge context request unavailable")

    @property
    def expected_manifest_digest(self) -> str:
        return self.expected_knowledge_manifest_digest

    @property
    def original_selection(self) -> ContextSelection:
        return self.selection

    @property
    def knowledge_selection(self) -> ContextSelection:
        return self.selection

    @property
    def chat_id(self) -> str:
        return self.chat_session_id

    def __repr__(self) -> str:
        return "KnowledgeContextPageRequest(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class KnowledgeContextPage:
    payload_bytes: bytes = field(repr=False)
    total: int
    offset: int
    end: int
    whole_sha256: str = field(repr=False)
    page_sha256: str = field(repr=False)
    manifest_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.payload_bytes) is not bytes:
            raise ValueError("knowledge context page unavailable")
        if (type(self.total) is not int or isinstance(self.total, bool) or self.total < 0
                or type(self.offset) is not int or isinstance(self.offset, bool) or self.offset < 0
                or type(self.end) is not int or isinstance(self.end, bool)
                or self.end < self.offset or self.end > self.total
                or self.end - self.offset != len(self.payload_bytes)):
            raise ValueError("knowledge context page unavailable")
        _context_sha(self.whole_sha256)
        _context_sha(self.page_sha256)
        _context_sha(self.manifest_digest)

    @property
    def payload(self) -> bytes:
        return self.payload_bytes

    @property
    def whole_payload_sha256(self) -> str:
        return self.whole_sha256

    @property
    def whole_sha(self) -> str:
        return self.whole_sha256

    @property
    def page_sha(self) -> str:
        return self.page_sha256

    def __repr__(self) -> str:
        return "KnowledgeContextPage(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _KnowledgeContextEntry:
    metadata: KnowledgeContextManifestItem
    knowledge_id: str
    revision_id: str
    generation: int
    payload_media_type: str


@dataclass(frozen=True, slots=True, repr=False)
class _KnowledgeContextCapture:
    manifest: KnowledgeContextManifest
    entries: tuple[_KnowledgeContextEntry, ...]
    owner_id: str
    canonical_generation: str
    selection_digest: str
    store: HarbourKnowledgeStore
    principal: Any



def _digest(value: object) -> str:
    import json
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()


def _inbox_digest(domain: str, value: object) -> str:
    """Canonical, length-framed Inbox-only digest; D0 digests remain unchanged."""
    try:
        if type(domain) is not str:
            raise ValueError
        domain_bytes = ("odysseus.harbour.inbox.knowledge.v1\0" + domain).encode("ascii")
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("knowledge inbox digest unavailable") from None
    return "sha256:" + hashlib.sha256(
        len(domain_bytes).to_bytes(4, "big") + domain_bytes
        + len(encoded).to_bytes(4, "big") + encoded
    ).hexdigest()


def _inbox_digest_value(value: object) -> str:
    if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError("knowledge inbox capture unavailable")
    if any(char not in "0123456789abcdef" for char in value[7:]):
        raise ValueError("knowledge inbox capture unavailable")
    return value


def _inbox_timestamp(value: object) -> str:
    if type(value) is not str or _KNOWLEDGE_UTC_TIMESTAMP.fullmatch(value) is None:
        raise ValueError("knowledge inbox capture unavailable")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, OverflowError):
        raise ValueError("knowledge inbox capture unavailable") from None
    if parsed.tzinfo != timezone.utc or parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise ValueError("knowledge inbox capture unavailable")
    return value


def _validate_inbox_signal(value: object) -> KnowledgeInboxSignal:
    if type(value) is not KnowledgeInboxSignal:
        raise ValueError("knowledge inbox capture unavailable")
    if (
        type(value.signal_type) is not str or value.signal_type != "purge_requested"
        or type(value.knowledge_kind) is not str or value.knowledge_kind not in KNOWLEDGE_KINDS
        or type(value.state) is not str or value.state != "purge_pending"
        or type(value.generation) is not int or isinstance(value.generation, bool) or value.generation < 1
        or type(value.revision) is not int or isinstance(value.revision, bool) or value.revision < 1
    ):
        raise ValueError("knowledge inbox capture unavailable")
    for field in (
        value.signal_handle, value.revision_ref_digest, value.head_metadata_digest,
        value.revision_metadata_digest, value.event_digest, value.receipt_handle,
        value.binding_digest,
    ):
        _inbox_digest_value(field)
    _inbox_timestamp(value.occurred_at)
    return value


def _private_existing_file(path: Path, *, max_bytes: int) -> tuple[bytes, tuple[int, ...]]:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        current = current / part
        item = current.lstat()
        attrs = getattr(item, "st_file_attributes", 0)
        if (stat.S_ISLNK(item.st_mode) or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or not stat.S_ISDIR(item.st_mode)):
            raise ValueError("private path unavailable")
    item = absolute.lstat()
    attrs = getattr(item, "st_file_attributes", 0)
    if (stat.S_ISLNK(item.st_mode) or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or not stat.S_ISREG(item.st_mode) or item.st_size > max_bytes
            or (os.name != "nt" and item.st_mode & 0o077)):
        raise ValueError("private file unavailable")
    descriptor = os.open(absolute, os.O_RDONLY)
    try:
        held = os.fstat(descriptor)
        identity = (held.st_dev, held.st_ino, held.st_mode, held.st_size,
                    held.st_mtime_ns, getattr(held, "st_file_attributes", 0))
        initial = (item.st_dev, item.st_ino, item.st_mode, item.st_size,
                   item.st_mtime_ns, attrs)
        if identity != initial:
            raise ValueError("private file changed")
        content = bytearray()
        while len(content) <= max_bytes:
            block = os.read(descriptor, max_bytes + 1 - len(content))
            if not block:
                break
            content.extend(block)
        if len(content) > max_bytes:
            raise ValueError("private file unbounded")
        after = os.fstat(descriptor)
        current = absolute.lstat()
        current_attrs = getattr(current, "st_file_attributes", 0)
        if ((after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns,
             getattr(after, "st_file_attributes", 0)) != identity
                or (current.st_dev, current.st_ino, current.st_mode, current.st_size,
                    current.st_mtime_ns, current_attrs) != identity):
            raise ValueError("private file changed")
        value = bytes(content)
        return value, identity + (hashlib.sha256(value).hexdigest(),)
    finally:
        os.close(descriptor)


def _app_secret() -> bytes:
    raw, _identity = _private_existing_file(Path(APP_KEY_FILE), max_bytes=128)
    try:
        decoded = base64.urlsafe_b64decode(raw.strip())
    except BaseException:
        raise ValueError("application key unavailable") from None
    if len(decoded) != 32:
        raise ValueError("application key unavailable")
    return decoded


class _ReadOnlySQLiteUSI:
    """Existing backend read protocol whose connector is pinned to mode=ro."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        self._path = Path(os.path.abspath(path))
        self._busy_timeout_ms = busy_timeout_ms
        self._closed = False
        backend = SQLiteUnifiedSourceIndexStore.__new__(SQLiteUnifiedSourceIndexStore)
        backend._database_path = str(self._path)
        backend._busy_timeout_ms = busy_timeout_ms
        backend._connect = lambda: self._connect(backend)
        self._backend = backend
        self.current_snapshot()

    def _connect(self, _backend: SQLiteUnifiedSourceIndexStore) -> sqlite3.Connection:
        if self._closed:
            raise ValueError("USI reader is closed")
        from src import harbour_knowledge_store as path_guard
        identity = path_guard._existing_path_identity(self._path)
        connection = sqlite3.connect(
            self._path.as_uri() + "?mode=ro", uri=True,
            timeout=self._busy_timeout_ms / 1000, isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=" + str(self._busy_timeout_ms))
            connection.execute("PRAGMA query_only=ON")
            validate_schema(connection)
            path_guard._verify_existing_path_identity(self._path, identity)
            return connection
        except BaseException:
            connection.close()
            raise

    def current_snapshot(self):
        return self._backend.current_snapshot()

    def begin_read(self, snapshot=None):
        if self._closed:
            raise ValueError("USI reader is closed")
        return self._backend.begin_read(snapshot)

    def search_chunks(self, *, owner_scope: str, query: str, limit: int = 20):
        if self._closed:
            raise ValueError("USI reader is closed")
        return self._backend.search_chunks(owner_scope=owner_scope, query=query, limit=limit)

    def close(self) -> None:
        self._closed = True


class _OwnerSnapshotLexicalRetriever(UnifiedSourceIndexLexicalRetriever):
    """Guarded lexical profile whose FTS document set is one complete owner manifest."""

    provider_id = "usi.lexical.owner_snapshot.v1"

    def __init__(self, *, usi: _ReadOnlySQLiteUSI, lifecycle_guard: HarbourKnowledgeQueryGuard,
                 records: tuple[object, ...], cursor_signing_key: bytes, deadline: float,
                 expected_head_boundary: str, expected_snapshot_digest: str) -> None:
        super().__init__(usi, lifecycle_guard=lifecycle_guard, cursor_signing_key=cursor_signing_key)
        self._owner_records = records
        self._owner_deadline = deadline
        self._expected_head_boundary = expected_head_boundary
        self._expected_snapshot_digest = expected_snapshot_digest

    def search(self, query: LexicalQuery) -> QueryResultPage:
        if (not isinstance(query, LexicalQuery) or query.domain != "native_knowledge"
                or time.monotonic() >= self._owner_deadline):
            raise LexicalRetrievalError("owner snapshot query is unavailable")
        physical_snapshot = self._store.current_snapshot()
        try:
            scope = self._lifecycle_guard.capture(
                owner_scope=query.owner_scope,
                requested_ceiling=query.classification_ceiling,
                query_fingerprint=replace(query, cursor="").fingerprint,
                domain=query.domain,
                expected_head_boundary=self._expected_head_boundary,
                expected_snapshot_digest=self._expected_snapshot_digest,
            )
            expression, tokens = build_fts_expression(query.query_text, query.match_mode)
            historical_snapshot, hits = self._store._backend.search_historical_owner_corpus(
                owner_scope=query.owner_scope, records=self._owner_records,
                query=expression, limit=query.max_candidates + 1,
                deadline=self._owner_deadline,
            )
            cursor_claims = _decode_guarded_cursor(
                query.cursor, historical_snapshot, query, self._cursor_signing_key,
            ) if query.cursor else None
            if cursor_claims and (cursor_claims[1] != scope.captured_head_boundary
                                  or cursor_claims[2] != scope.snapshot_digest):
                raise LexicalRetrievalError("owner snapshot cursor is stale")
            read = self._store.begin_read(historical_snapshot)
        except LexicalRetrievalError:
            raise
        except Exception:
            raise LexicalRetrievalError("owner snapshot capture failed") from None
        try:
            after = cursor_claims[0] if cursor_claims else None
            clipped = len(hits) > query.max_candidates
            candidates = hits[:query.max_candidates]
            filtered = 0
            items: list[QueryResultItem] = []
            for hit in candidates:
                if time.monotonic() >= self._owner_deadline:
                    raise LexicalRetrievalError("owner snapshot deadline exceeded")
                chunk = hit.item.record
                try:
                    allowed = scope.allows(chunk, read)
                except Exception:
                    raise LexicalRetrievalError("owner snapshot parent validation failed") from None
                if (not allowed or (query.source_ids and chunk.source_id not in query.source_ids)
                        or _CLASSIFICATION_RANK[chunk.classification]
                        > _CLASSIFICATION_RANK[query.classification_ceiling]):
                    filtered += 1
                    continue
                lexical_score = _lexical_score(hit.rank)
                snippet, snippet_clipped = _snippet(
                    chunk.content or "", tokens, query.snippet_chars,
                )
                items.append(QueryResultItem(
                    owner_scope=chunk.owner_scope,
                    source_ref=RecordRef(RecordKind.SOURCE, chunk.source_id),
                    source_version_ref=RecordRef(RecordKind.SOURCE_VERSION, chunk.source_version_id),
                    chunk_ref=chunk.ref(), locator=chunk.locator,
                    content_hash=chunk.content_hash, classification=chunk.classification,
                    content_policy=chunk.content_policy,
                    scores=QueryScoreComponents(lexical_score, 0.0, lexical_score),
                    snippet=snippet, snippet_clipped=snippet_clipped,
                    stale=_is_stale(chunk.indexed_at, query.stale_after),
                    indexed_at=chunk.indexed_at, why_selected="lexical_token",
                ))
            items.sort(key=_result_sort_key)
            if after is not None:
                items = [item for item in items if _result_sort_key(item) > after]
            selected = tuple(items[:query.limit])
            next_cursor = ""
            if len(items) > query.limit and selected:
                next_cursor = _encode_guarded_cursor(
                    historical_snapshot, query, selected[-1], scope, self._cursor_signing_key,
                )
            if (self._store.current_snapshot() != physical_snapshot
                    or time.monotonic() >= self._owner_deadline):
                raise LexicalRetrievalError("owner snapshot currentness changed")
            return QueryResultPage(
                selected, historical_snapshot, candidate_count=len(candidates),
                policy_filtered_count=filtered, candidate_clipped=clipped,
                next_cursor=next_cursor,
            )
        except LexicalRetrievalError:
            raise
        except Exception:
            raise LexicalRetrievalError("owner snapshot lexical page failed") from None
        finally:
            try:
                read.close()
            except Exception:
                raise LexicalRetrievalError("owner snapshot read could not close") from None


class _OwnerSnapshotLexicalProvider(LexicalProviderAdapter):
    provider_id = "usi.lexical.owner_snapshot.v1"

    def __init__(self, retriever: _OwnerSnapshotLexicalRetriever) -> None:
        super().__init__(retriever)


class ActiveKnowledgeComposition:
    """Per-query account-bound principal and existing-only read composition."""

    def __init__(self, *, config: UnifiedSourceIndexRuntimeConfig, auth_manager: Any,
                 key: bytes, staging: HarbourKnowledgeStagingDestinationService,
                 usi: _ReadOnlySQLiteUSI) -> None:
        self._config = config
        # This is the configured USI runtime generation. It is deliberately
        # typed and kept separate from Harbour's canonical generation below.
        self._usi_generation = RuntimeGeneration(config.selected_generation)
        self._auth = auth_manager
        self._key = key
        self._staging = staging
        self._usi = usi
        self._condition = threading.Condition(threading.RLock())
        self._context_local = threading.local()
        self._closed = False
        self._inflight = 0

    @classmethod
    def open_existing(cls, config: UnifiedSourceIndexRuntimeConfig, auth_manager: Any) -> "ActiveKnowledgeComposition":
        if _selected_runtime_generation(config) is None:
            raise ValueError("knowledge generation unavailable")
        key = _app_secret()
        staging = HarbourKnowledgeStagingDestinationService.open_existing(
            Path(DATA_DIR) / "harbour-knowledge-staging",
            trusted_issuers=(AuthenticatedPrincipalIssuer(
                issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
                secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
            ),),
        )
        usi = None
        try:
            usi = _ReadOnlySQLiteUSI(config.sqlite_path)
            if staging.current_selection() is None:
                raise ValueError("knowledge selection unavailable")
            return cls(config=config, auth_manager=auth_manager, key=key, staging=staging, usi=usi)
        except BaseException:
            if usi is not None:
                try:
                    usi.close()
                except BaseException:
                    pass
            try:
                staging.close()
            except BaseException:
                pass
            raise

    def execute(self, query: object) -> object:
        with self._condition:
            if self._closed:
                raise ValueError("knowledge runtime closed")
            self._inflight += 1
        try:
            return self._execute(query)
        finally:
            with self._condition:
                self._inflight -= 1
                self._condition.notify_all()

    @contextmanager
    def guard_inbox_read(self):
        """Reserve the active composition while an Inbox read is revalidated."""
        with self._condition:
            if self._closed:
                raise ValueError("knowledge runtime closed")
            self._inflight += 1
        try:
            yield
        finally:
            with self._condition:
                self._inflight -= 1
                self._condition.notify_all()

    @contextmanager
    def _context_operation(self) -> Iterator[None]:
        """Retain this composition until context metadata or bytes are closed."""
        depth = getattr(self._context_local, "depth", 0)
        if type(depth) is int and depth > 0:
            self._context_local.depth = depth + 1
            try:
                yield
            finally:
                self._context_local.depth = depth
            return
        with self._condition:
            if self._closed:
                raise ValueError("knowledge runtime closed")
            self._inflight += 1
        self._context_local.depth = 1
        try:
            yield
        finally:
            self._context_local.depth = 0
            with self._condition:
                self._inflight -= 1
                self._condition.notify_all()

    def _context_runtime(self, binding: DisclosureBinding) -> tuple[
        str, HarbourKnowledgeStore, Any, str, str, Any,
    ]:
        if type(binding) is not DisclosureBinding:
            raise ValueError("knowledge context binding unavailable")
        subject = binding.owner_sid
        if (not self._auth.has_active_subject_id(subject)
                or subject not in self._config.allowed_owners
                or "native_knowledge" not in self._config.allowed_domains):
            raise ValueError("knowledge context owner unavailable")
        selected = self._staging.selected_runtime(subject)
        if selected is None:
            raise ValueError("knowledge context selection unavailable")
        _reference, owners, canonical_generation, selection_digest, store = selected
        if (type(canonical_generation) is not str
                or _GENERATION_ID.fullmatch(canonical_generation) is None
                or type(selection_digest) is not str
                or subject not in owners
                or type(store) is not HarbourKnowledgeStore):
            raise ValueError("knowledge context selection unavailable")
        selection_digest = "sha256:" + _context_sha(selection_digest)
        current_key = _app_secret()
        if not hmac.compare_digest(hashlib.sha256(current_key).digest(), hashlib.sha256(self._key).digest()):
            raise ValueError("knowledge context key changed")
        issuer = AuthenticatedPrincipalIssuer(
            issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
            secret=hmac.new(current_key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
        )
        principal = issuer.issue(
            subject_id=subject, owner_id=subject,
            auth_context_digest=_context_digest({
                "binding": {
                    "chat": binding.chat_session_id,
                    "owner": binding.owner_sid,
                    "security_revision": binding.security_revision,
                },
                "canonical_generation": canonical_generation,
                "selection": selection_digest,
                "usi_generation": self._usi_generation.to_dict(),
            }),
            ttl_seconds=60,
        )
        return subject, store, principal, canonical_generation, selection_digest, self._usi.current_snapshot()

    def _validate_context_usi_parents(
        self, owner_scope: str, snapshot: Any,
        projections: tuple[tuple[KnowledgeIndexRecord, Any], ...],
    ) -> None:
        read = self._usi.begin_read(snapshot)
        try:
            for _record, projection in projections:
                source_item = read.get(
                    RecordKind.SOURCE, projection.source.source_id,
                    owner_scope=owner_scope,
                )
                version_item = read.get(
                    RecordKind.SOURCE_VERSION, projection.source_version.source_version_id,
                    owner_scope=owner_scope,
                )
                if (type(source_item) is not StoredRecord
                        or type(source_item.record) is not SourceRecord
                        or type(version_item) is not StoredRecord
                        or type(version_item.record) is not SourceVersionRecord
                        or source_item.record != projection.source
                        or version_item.record != projection.source_version
                        or source_item.record.owner_scope != owner_scope
                        or version_item.record.owner_scope != owner_scope
                        or version_item.record.source_id != source_item.record.source_id
                        or _context_sha(version_item.record.content_hash) != _context_sha(_record.payload_sha256)
                        or version_item.record.classification is not source_item.record.classification
                        or version_item.record.content_policy is not source_item.record.content_policy):
                    raise ValueError("knowledge context USI parent unavailable")
        finally:
            try:
                read.close()
            except BaseException:
                raise ValueError("knowledge context USI cleanup unavailable") from None

    def _capture_context_material(
        self, binding: DisclosureBinding, selection: ContextSelection,
    ) -> _KnowledgeContextCapture:
        if type(selection) is not ContextSelection:
            raise ValueError("knowledge context selection unavailable")
        if any(item.source != "knowledge" for item in selection.items):
            raise ValueError("knowledge context source unavailable")
        subject, store, principal, canonical_generation, selection_digest, usi_snapshot = self._context_runtime(binding)
        snapshot = store.capture_index_snapshot(principal, owner_id=subject)
        if (type(snapshot) is not KnowledgeIndexSnapshot
                or snapshot.owner_id != subject
                or type(snapshot.records) is not tuple):
            raise ValueError("knowledge context snapshot unavailable")
        owner_scope = OwnerScope.for_subject_id(subject).value
        projections = []
        for record in snapshot.records:
            if type(record) is not KnowledgeIndexRecord or record.state != "active":
                continue
            projection = project_harbour_knowledge_index_records(owner_scope, record)
            source_id = projection.source.source_id
            allowed_sources = self._config.allowed_sources
            if type(allowed_sources) is tuple and source_id not in allowed_sources:
                continue
            classification = projection.source.classification.value
            if classification not in {"public", "private"}:
                continue
            projections.append((record, projection))
        projections = tuple(projections)

        entries: list[_KnowledgeContextEntry] = []
        matched_projections: list[tuple[KnowledgeIndexRecord, Any]] = []
        owner_heads = tuple(sorted(({
            "knowledge_id": record.knowledge_id,
            "knowledge_kind": record.knowledge_kind,
            "revision_id": record.revision_id,
            "revision": record.revision,
            "parent_revision_id": record.parent_revision_id,
            "generation": record.generation,
            "state": record.state,
            "tombstoned_at": record.tombstoned_at,
            "purge_requested_at": record.purge_requested_at,
            "payload_schema_id": record.payload_schema_id,
            "payload_media_type": record.payload_media_type,
            "payload_sha256": record.payload_sha256,
            "classification": record.classification,
            "source_revisions_json": record.source_revisions_json,
            "provenance_json": record.provenance_json,
            "references_json": record.references_json,
            "retention_json": record.retention_json,
            "created_at": record.created_at,
            "record_digest": record.record_digest,
        } for record in snapshot.records), key=lambda value: (
            value["knowledge_id"], value["revision_id"],
        )))
        for selected_item in selection.items:
            matches = []
            for record, projection in projections:
                source_id = projection.source.source_id
                version_id = projection.source_version.source_version_id
                if selected_item.selection_ref == source_id and selected_item.revision_ref == version_id:
                    matches.append((record, projection))
            if len(matches) != 1:
                raise ValueError("knowledge context selection unavailable")
            record, projection = matches[0]
            matched_projections.append((record, projection))
            classification = projection.source.classification.value
            record_digest = "sha256:" + _context_sha(record.record_digest)
            payload_digest = "sha256:" + _context_sha(record.payload_sha256)
            provenance_ref = _context_framed_digest("provenance", {
                "record": record_digest,
                "provenance": record.provenance_json,
                "source_revisions": record.source_revisions_json,
                "references": record.references_json,
            })
            boundary = _context_framed_digest("complete-boundary", {
                "domain": "odysseus.knowledge.context.boundary.v2",
                "owner_binding": _context_digest({
                    "chat": binding.chat_session_id,
                    "owner": binding.owner_sid,
                    "security_revision": binding.security_revision,
                }),
                "owner_head": {
                    "boundary": snapshot.captured_head_boundary,
                    "snapshot": snapshot.snapshot_digest,
                    "records": owner_heads,
                },
                "selected": {
                    "canonical_generation": canonical_generation,
                    "selection_digest": selection_digest,
                },
                "usi": {
                    "snapshot": usi_snapshot.to_dict(),
                    "runtime_generation": self._usi_generation.to_dict(),
                },
                "config": {
                    "allowed_owners": list(self._config.allowed_owners),
                    "allowed_sources": list(self._config.allowed_sources),
                    "allowed_domains": list(self._config.allowed_domains),
                },
                "source": projection.source.to_dict(),
                "source_version": projection.source_version.to_dict(),
                "knowledge_metadata": {
                    "generation": record.generation,
                    "payload_sha256": payload_digest,
                    "provenance": record.provenance_json,
                    "record_digest": record_digest,
                    "references": record.references_json,
                    "revision": record.revision,
                    "revision_id": record.revision_id,
                    "source_revisions": record.source_revisions_json,
                    "state": record.state,
                },
                "key_digest": hashlib.sha256(self._key).hexdigest(),
            })
            metadata = KnowledgeContextManifestItem(
                source="knowledge", selection_ref=selected_item.selection_ref,
                revision_ref=selected_item.revision_ref, boundary_sha256=boundary,
                record_sha256=record_digest, payload_sha256=payload_digest,
                classification=classification, provenance_ref=provenance_ref,
            )
            entries.append(_KnowledgeContextEntry(
                metadata=metadata, knowledge_id=record.knowledge_id,
                revision_id=record.revision_id, generation=record.generation,
                payload_media_type=record.payload_media_type,
            ))

        if len({entry.metadata.selection_ref for entry in entries}) != len(entries):
            raise ValueError("knowledge context selection unavailable")
        self._validate_context_usi_parents(owner_scope, usi_snapshot, tuple(matched_projections))
        selection_binding = _context_digest({
            "items": [
                {"source": item.source, "selection": item.selection_ref, "revision": item.revision_ref}
                for item in selection.items
            ],
            "owner": subject,
            "selection_digest": selection_digest,
        })
        canonical_generation_digest = _context_digest({
            "canonical_generation": canonical_generation,
            "selection_digest": selection_digest,
        })
        usi_snapshot_digest = _context_digest(usi_snapshot.to_dict())
        owner_binding_digest = _context_digest({
            "chat": binding.chat_session_id,
            "owner": binding.owner_sid,
            "security_revision": binding.security_revision,
        })
        binding_digest = _context_digest({
            "canonical_generation": canonical_generation,
            "head_boundary": snapshot.captured_head_boundary,
            "owner_binding": owner_binding_digest,
            "selection_binding": selection_binding,
            "usi_snapshot": usi_snapshot_digest,
        })
        manifest = KnowledgeContextManifest(
            tuple(sorted(
                (entry.metadata for entry in entries),
                key=lambda item: json.dumps(item._canonical(), sort_keys=True, separators=(",", ":")),
            )), owner_binding_digest, selection_binding, canonical_generation_digest,
            usi_snapshot_digest, "sha256:" + _context_sha(snapshot.captured_head_boundary),
            binding_digest,
        )
        current = self._staging.current_selection()
        if current is None or current[2] != selection_digest:
            raise ValueError("knowledge context selection changed")
        if self._usi.current_snapshot() != usi_snapshot:
            raise ValueError("knowledge context USI changed")
        return _KnowledgeContextCapture(
            manifest=manifest, entries=tuple(entries), owner_id=subject,
            canonical_generation=canonical_generation, selection_digest=selection_digest,
            store=store, principal=principal,
        )

    def _read_context_payload(self, binding: DisclosureBinding, entry: _KnowledgeContextEntry) -> bytes:
        subject, store, principal, _generation, _selection_digest, _usi_snapshot = self._context_runtime(binding)
        payload = store.read_payload_at_generation(
            principal, owner_id=subject, knowledge_id=entry.knowledge_id,
            revision_id=entry.revision_id, expected_generation=entry.generation,
            expected_payload_sha256=entry.metadata.payload_sha256,
        )
        if type(payload) is not bytes or hashlib.sha256(payload).hexdigest() != _context_sha(entry.metadata.payload_sha256):
            raise ValueError("knowledge context payload changed")
        return payload

    def capture_inbox_snapshot(self, *, identity: Any, limit: int,
                               expected_capture: Any = None,
                               _reservation_held: bool = False) -> KnowledgeInboxSnapshot:
        """Capture D0 purge-pending signals through the selected active SID store."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("knowledge inbox limit invalid")
        subject, alias = getattr(identity, "owner_subject_id", None), getattr(identity, "owner_alias", None)
        if type(subject) is not str or type(alias) is not str:
            raise ValueError("knowledge account unavailable")
        reservation = nullcontext() if _reservation_held is True else self.guard_inbox_read()
        with reservation:
            if self._auth.subject_id_for_username(alias) != subject or not self._auth.has_active_subject_id(subject):
                raise ValueError("knowledge account unavailable")
            # Configured ownership is an admission check.  Do it before
            # touching the selected staging store or issuing a principal.
            if subject not in self._config.allowed_owners:
                raise ValueError("knowledge owner unavailable")
            selected = self._staging.selected_runtime(subject)
            if selected is None:
                raise LookupError("knowledge selection unavailable")
            _reference, owners, generation, selection_digest, store = selected
            if (type(generation) is not str or _GENERATION_ID.fullmatch(generation) is None
                    or type(selection_digest) is not str or subject not in owners):
                raise ValueError("knowledge selection unavailable")
            _inbox_digest_value(selection_digest)
            key = _app_secret()
            if not hmac.compare_digest(hashlib.sha256(key).digest(), hashlib.sha256(self._key).digest()):
                raise ValueError("knowledge key changed")
            issuer = AuthenticatedPrincipalIssuer(
                issuer_id="harbour-knowledge-runtime", key_id="app-key-v1",
                secret=hmac.new(key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest(),
            )
            principal = issuer.issue(subject_id=subject, owner_id=subject,
                auth_context_digest=_digest({"purpose":"unified_inbox","subject":subject,
                    "selection":selection_digest,"generation":generation}), ttl_seconds=60)
            if expected_capture is not None and type(expected_capture) is not KnowledgeInboxSnapshot:
                raise ValueError("knowledge inbox stale")
            prior_boundary = (
                expected_capture.captured_head_boundary if expected_capture is not None else None
            )
            snapshot = store.capture_inbox_signal_snapshot(
                principal, owner_id=subject, limit=limit,
                expected_head_boundary=prior_boundary,
            )
            if (
                type(snapshot) is not KnowledgeInboxSignalSnapshot
                or snapshot.schema_id != KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA
                or type(snapshot.signals) is not tuple or len(snapshot.signals) > limit
                or type(snapshot.clipped) is not bool
            ):
                raise ValueError("knowledge inbox capture unavailable")
            owner_binding = _inbox_digest_value(snapshot.owner_binding_digest)
            head_boundary = _inbox_digest_value(snapshot.captured_head_boundary)
            snapshot_digest = _inbox_digest_value(snapshot.snapshot_digest)
            items = tuple(_validate_inbox_signal(item) for item in snapshot.signals)
            selection_binding = _inbox_digest("selection", {
                "generation": generation, "owner": owner_binding, "selection": selection_digest,
            })
            generation_binding = _inbox_digest("generation", {"generation": generation})
            binding = _inbox_digest("binding", {
                "head_boundary": head_boundary, "selection": selection_binding,
                "snapshot": snapshot_digest,
            })
            result = KnowledgeInboxSnapshot(
                owner_binding, selection_binding, generation_binding, snapshot_digest,
                head_boundary, items, snapshot.clipped, binding,
            )
            if expected_capture is not None:
                if expected_capture != result:
                    raise ValueError("knowledge inbox stale")
            return result

    def _execute(self, query: object) -> object:
        from src.unified_source_index_query import FederatedQuery, UnifiedSourceIndexQueryPlanner
        if type(query) is not FederatedQuery or query.domain not in self._config.allowed_domains:
            raise ValueError("knowledge query denied")
        if (query.limit > self._config.query_max_results
                or query.budget.time_budget_ms > self._config.query_timeout_seconds * 1000
                or query.budget.max_total_candidates > min(500, self._config.query_max_results * 8)):
            raise ValueError("knowledge query budget denied")
        deadline = time.monotonic() + min(
            query.budget.time_budget_ms / 1000,
            float(self._config.query_timeout_seconds),
        )

        def check_deadline() -> None:
            if time.monotonic() >= deadline:
                raise ValueError("knowledge query timed out")

        subject = self._auth.subject_id_for_owner_scope(query.owner_scope)
        if subject is None:
            raise ValueError("knowledge account unavailable")
        if not self._auth.has_active_subject_id(subject):
            raise ValueError("knowledge account unavailable")
        try:
            expected_scope = OwnerScope.for_subject_id(subject).value
        except BaseException:
            raise ValueError("knowledge account unavailable") from None
        if query.owner_scope != expected_scope:
            raise ValueError("knowledge query denied")
        if subject not in self._config.allowed_owners:
            raise ValueError("knowledge owner unavailable")
        selected = self._staging.selected_runtime(subject)
        if selected is None:
            raise ValueError("knowledge selection unavailable")
        reference, _owners, canonical_generation, selection_digest, store = selected
        current_key = _app_secret()
        if not hmac.compare_digest(hashlib.sha256(current_key).digest(), hashlib.sha256(self._key).digest()):
            raise ValueError("knowledge key changed")
        domain = query.domain
        binding = _digest({"owner": subject, "domain": domain, "purpose": "read_only_query",
                           "selection": selection_digest,
                           "canonical_generation": canonical_generation,
                           "usi_runtime_generation": self._usi_generation.to_dict()})
        issuer_key = hmac.new(current_key, b"odysseus.harbour.knowledge.issuer.v1\0", hashlib.sha256).digest()
        issuer = AuthenticatedPrincipalIssuer(
            issuer_id="harbour-knowledge-runtime", key_id="app-key-v1", secret=issuer_key,
        )
        principal = issuer.issue(subject_id=subject, owner_id=subject,
                                 auth_context_digest=binding, ttl_seconds=60)
        cursor_key = hmac.new(current_key,
            b"odysseus.harbour.knowledge.cursor.v1\0" + binding.encode("ascii"), hashlib.sha256).digest()
        ceiling = query.classification_ceiling
        guard = HarbourKnowledgeQueryGuard(
            store=store, principal=principal, owner_id=subject,
            owner_scope=expected_scope, classification_ceiling=ceiling,
        )
        native = KnowledgeSourceAdapter(
            store=store, principal=principal, owner_id=subject,
            cursor_signing_key=cursor_key, classification_ceiling=ceiling,
        )
        native_manifest = native.manifest
        capability = native.describe_capability()
        if (native_manifest.generation_ref != self._usi_generation.generation_ref
                or capability.adapter_id != native_manifest.adapter_id
                or capability.adapter_version != native_manifest.adapter_version
                or capability.required_job_profile_ref != native_manifest.job_profile_ref):
            raise ValueError("knowledge adapter generation unavailable")
        source_ids = tuple(query.source_ids or self._config.allowed_sources)
        if not set(source_ids) <= set(self._config.allowed_sources):
            raise ValueError("knowledge source unavailable")
        scope = AdapterScope(expected_scope, ceiling, source_ids)
        manifest_scope = guard.capture(
            owner_scope=expected_scope, requested_ceiling=ceiling,
            query_fingerprint=_digest({
                "profile": "usi.lexical.owner_snapshot.v1", "owner": subject,
                "sources": list(source_ids), "ceiling": ceiling.value,
                "selection": selection_digest,
            }),
            domain="native_knowledge",
        )
        expected_projections = {
            source_id: projection
            for source_id, projection in manifest_scope._active.items()
            if source_id in source_ids
            and _CLASSIFICATION_RANK[projection.source.classification]
            <= _CLASSIFICATION_RANK[ceiling]
        }
        native_chunks = {}
        manifest_records: list[object] = []
        manifest_source_ids: set[str] = set()
        discovered_count = 0
        cursor = ""
        pages = 0
        profile_ms = min(query.budget.time_budget_ms, self._config.query_timeout_seconds * 1000)
        profile = ExtractionProfile(
            KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
            max_items=MAX_PAGED_EXTRACTION_PAGE_RECORDS,
            max_chars=1_000_000, time_budget_ms=profile_ms,
        )
        corpus_bytes = chunk_count = 0
        while source_ids:
            check_deadline()
            remaining = 10_000 - discovered_count
            if remaining <= 0:
                raise ValueError("knowledge manifest exceeded bound")
            page = native.discover(
                scope, cursor=cursor, limit=min(100, remaining),
                time_budget_ms=max(1, min(profile_ms, int((deadline - time.monotonic()) * 1000))),
            )
            check_deadline()
            pages += 1
            if pages > 101:
                raise ValueError("knowledge manifest exceeded bound")
            for item in page.items:
                discovered_count += 1
                check_deadline()
                projection = expected_projections.get(item.source.source_id)
                if projection is None or projection.source != item.source:
                    raise ValueError("knowledge manifest snapshot changed")
                if item.source.source_id in manifest_source_ids:
                    raise ValueError("knowledge manifest contains duplicate sources")
                manifest_source_ids.add(item.source.source_id)
                version = native.observe_version(RecordRef(RecordKind.SOURCE, item.source.source_id))
                check_deadline()
                if (type(version) is UnavailableObservation
                        or version != projection.source_version):
                    raise ValueError("knowledge manifest is incomplete")
                manifest_records.extend((item.source, version))
                extraction_cursor = ""
                sequence_start = extracted_count = 0
                while True:
                    check_deadline()
                    page_budget = ExtractionPageBudget(
                        MAX_PAGED_EXTRACTION_PAGE_RECORDS, 1_000_000,
                        max(1, min(profile_ms, int((deadline - time.monotonic()) * 1000))),
                    )
                    extracted = native.extract_page_budgeted(
                        version, profile, page_budget,
                        cursor=extraction_cursor, sequence_start=sequence_start,
                    )
                    check_deadline()
                    if extracted.source_version != version or extracted.sequence_start != sequence_start:
                        raise ValueError("knowledge manifest changed during extraction")
                    for chunk in extracted.records:
                        check_deadline()
                        extracted_count += 1
                        chunk_count += 1
                        corpus_bytes += len((chunk.content or "").encode("utf-8"))
                        if chunk_count > 8_192 or corpus_bytes > 8 * 1024 * 1024:
                            raise ValueError("knowledge corpus exceeded bound")
                        if chunk.chunk_id in native_chunks:
                            raise ValueError("knowledge manifest contains duplicate records")
                        native_chunks[chunk.chunk_id] = chunk
                        manifest_records.append(chunk)
                        if len(manifest_records) > 24_576:
                            raise ValueError("knowledge manifest exceeded bound")
                    if not extracted.next_cursor:
                        if extracted.final_record_count != extracted_count:
                            raise ValueError("knowledge manifest is incomplete")
                        break
                    extraction_cursor = extracted.next_cursor
                    sequence_start += len(extracted.records)
                    if sequence_start >= 10_000:
                        raise ValueError("knowledge source exceeded bound")
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        if manifest_source_ids != set(expected_projections):
            raise ValueError("knowledge manifest is incomplete")
        profile_context = _digest({
            "profile": "usi.lexical.owner_snapshot.v1", "owner": subject,
            "sources": list(source_ids), "ceiling": ceiling.value,
            "selection": selection_digest, "generation": canonical_generation,
        })
        corpus_cursor_key = hmac.new(
            current_key,
            b"odysseus.usi.lexical.owner_snapshot.v1\0" + profile_context.encode("ascii"),
            hashlib.sha256,
        ).digest()
        owner_retriever = _OwnerSnapshotLexicalRetriever(
            usi=self._usi, lifecycle_guard=guard, records=tuple(manifest_records),
            cursor_signing_key=corpus_cursor_key, deadline=deadline,
            expected_head_boundary=manifest_scope.captured_head_boundary,
            expected_snapshot_digest=manifest_scope.snapshot_digest,
        )
        provider = _OwnerSnapshotLexicalProvider(owner_retriever)
        planner = UnifiedSourceIndexQueryPlanner((ProviderRegistration(provider),))
        bounded = replace(query, source_ids=source_ids, limit=min(query.limit, self._config.query_max_results))
        snapshot = self._usi.current_snapshot()
        check_deadline()
        result = planner.execute(bounded)
        check_deadline()
        for item in result.items:
            chunk = native_chunks.get(item.evidence.record_id)
            if (chunk is None or chunk.source_id != item.evidence.source_id
                    or chunk.source_version_id != item.evidence.source_version_id
                    or chunk.content_hash != item.evidence.content_hash):
                raise ValueError("native knowledge projection is stale")
        if (self._usi.current_snapshot() != snapshot
                or self._auth.subject_id_for_owner_scope(query.owner_scope) != subject
                or not self._auth.has_active_subject_id(subject)):
            raise ValueError("knowledge currentness changed")
        check_deadline()
        try:
            postflight_key = _app_secret()
        except (OSError, ValueError):
            raise ValueError("application key unavailable") from None
        check_deadline()
        if not hmac.compare_digest(
            hashlib.sha256(postflight_key).digest(), hashlib.sha256(self._key).digest(),
        ):
            raise ValueError("knowledge key changed")
        current = self._staging.current_selection()
        if current is None or current[2] != selection_digest:
            raise ValueError("knowledge selection changed")
        check_deadline()
        return result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            while self._inflight:
                self._condition.wait(timeout=0.1)
        failed = False
        try:
            self._usi.close()
        except BaseException:
            failed = True
        try:
            self._staging.close()
        except BaseException:
            failed = True
        if failed:
            raise ValueError("knowledge cleanup failed") from None


class KnowledgeContextManifestProducer:
    """Capture authenticated, detached metadata for selected Knowledge rows."""

    def __init__(self, composition: ActiveKnowledgeComposition) -> None:
        if not isinstance(composition, ActiveKnowledgeComposition):
            raise ValueError("knowledge context composition unavailable")
        self._composition = composition

    def capture_metadata(
        self, binding: DisclosureBinding, knowledge_selection: ContextSelection,
    ) -> KnowledgeContextManifest:
        try:
            with self._composition._context_operation():
                return self._composition._capture_context_material(binding, knowledge_selection).manifest
        except ConfirmationError:
            raise
        except KnowledgeContextError:
            raise
        except Exception:
            raise KnowledgeContextError("capture_unavailable") from None

    def capture(self, binding: DisclosureBinding, selection: ContextSelection) -> ContextManifest:
        with self._composition._context_operation():
            captured = self._composition._capture_context_material(binding, selection)
            return self._foundation(captured.manifest, private_only=False)

    def capture_private_subset(
        self, binding: DisclosureBinding, selection: ContextSelection,
    ) -> ContextManifest:
        with self._composition._context_operation():
            captured = self._composition._capture_context_material(binding, selection)
            return self._foundation(captured.manifest, private_only=True)

    @staticmethod
    def _foundation(
        manifest: KnowledgeContextManifest, *, private_only: bool,
    ) -> ContextManifest:
        selected = tuple(
            item for item in manifest.items
            if not private_only or item.classification == "private"
        )
        if not selected:
            raise ValueError("private context unavailable")
        foundation_items = tuple(sorted(
            (
                ManifestItem(
                    item.source, item.selection_ref, item.revision_ref,
                    item.boundary_sha256, item.record_sha256, item.payload_sha256,
                    item.classification, item.provenance_ref,
                )
                for item in selected
            ),
            key=lambda item: json.dumps(item._canonical(), sort_keys=True, separators=(",", ":")),
        ))
        return ContextManifest(foundation_items)


class KnowledgeContextPageConsumer:
    """Cookie-authenticated exact Knowledge page reader."""

    def __init__(
        self,
        composition: ActiveKnowledgeComposition,
        auth_manager: object,
        confirmation_service: PrivateContextConfirmationApp,
        session_factory: Callable[[], object],
    ) -> None:
        if (not isinstance(composition, ActiveKnowledgeComposition)
                or auth_manager is None
                or type(confirmation_service) is not PrivateContextConfirmationApp
                or not callable(session_factory)):
            raise ValueError("knowledge context consumer unavailable")
        self._composition = composition
        self._producer = KnowledgeContextManifestProducer(composition)
        self._confirmation = confirmation_service
        confirmation_authority = getattr(confirmation_service, "_authority", None)
        if (
            type(confirmation_authority) is not CookieConfirmationAuthority
            or confirmation_authority._auth_manager is not auth_manager
        ):
            raise ValueError("knowledge context consumer unavailable")
        # This authority is intentionally distinct from the confirmation app's
        # authority while remaining in its binding family for page operations.
        self._authority = confirmation_authority.fork()
        if (
            type(self._authority) is not CookieConfirmationAuthority
            or self._authority._auth_manager is not auth_manager
        ):
            raise ValueError("knowledge context consumer unavailable")
        self._repository = ChatConfirmationRepository(session_factory, self._authority)

    def read_page(self, cookie: str, request: KnowledgeContextPageRequest) -> KnowledgeContextPage:
        if type(request) is not KnowledgeContextPageRequest:
            raise KnowledgeContextError("request_unavailable")
        try:
            return self._read_page(cookie, request)
        except ConfirmationError:
            raise
        except KnowledgeContextError:
            raise
        except Exception:
            raise KnowledgeContextError("read_unavailable") from None

    def _read_page(self, cookie: str, request: KnowledgeContextPageRequest) -> KnowledgeContextPage:
        with self._authority.hold(cookie, request.chat_session_id) as binding:
            with self._composition._context_operation():
                self._repository.assert_chat_owned(binding)
                knowledge_selection = ContextSelection(tuple(
                    item for item in request.selection.items if item.source == "knowledge"
                ))
                first = self._composition._capture_context_material(binding, knowledge_selection)
                if not hmac.compare_digest(
                    _context_sha(first.manifest.digest),
                    _context_sha(request.expected_knowledge_manifest_digest),
                ):
                    raise ValueError("knowledge context manifest stale")
                target = next((entry for entry in first.entries
                               if entry.metadata.selection_ref == request.target.selection_ref
                               and entry.metadata.revision_ref == request.target.revision_ref), None)
                if target is None:
                    raise ValueError("knowledge context target unavailable")
                private_selected = any(item.classification == "private" for item in first.manifest.items)
                if private_selected:
                    if request.disclosure_message_id is None:
                        raise ConfirmationError("invalid_context") from None
                    expected_subset = self._producer.capture_private_subset(
                        binding, knowledge_selection,
                    )
                    verified = self._confirmation.verify_disclosure(
                        cookie, request.chat_session_id,
                        request.disclosure_message_id, request.selection,
                        expected_subset=expected_subset,
                    )
                    if type(verified) is not VerifiedDisclosure:
                        raise ConfirmationError("invalid_context") from None
                payload = self._composition._read_context_payload(binding, target)
                final = self._composition._capture_context_material(binding, knowledge_selection)
                if (final.manifest.digest != first.manifest.digest
                        or not hmac.compare_digest(
                            _context_sha(final.manifest.digest),
                            _context_sha(request.expected_knowledge_manifest_digest),
                        )):
                    raise ValueError("knowledge context changed")
                if private_selected:
                    final_subset = self._producer.capture_private_subset(
                        binding, knowledge_selection,
                    )
                    verified = self._confirmation.verify_disclosure(
                        cookie, request.chat_session_id,
                        request.disclosure_message_id, request.selection,
                        expected_subset=final_subset,
                    )
                    if type(verified) is not VerifiedDisclosure:
                        raise ConfirmationError("invalid_context") from None
                self._authority.recheck(binding)
                self._repository.assert_chat_owned(binding)
                total = len(payload)
                if request.offset > total:
                    raise ValueError("knowledge context offset unavailable")
                end = min(total, request.offset + request.limit)
                page = payload[request.offset:end]
                whole_sha = "sha256:" + hashlib.sha256(payload).hexdigest()
                page_sha = "sha256:" + hashlib.sha256(page).hexdigest()
                return KnowledgeContextPage(
                    payload_bytes=page, total=total, offset=request.offset,
                    end=end, whole_sha256=whole_sha, page_sha256=page_sha,
                    manifest_digest=final.manifest.digest,
                )


def build_active_knowledge_composition(config: UnifiedSourceIndexRuntimeConfig,
                                       auth_manager: Any) -> ActiveKnowledgeComposition | None:
    if (type(config) is not UnifiedSourceIndexRuntimeConfig
            or config.mode is not UnifiedSourceIndexRuntimeMode.READ_ONLY
            or not config.runtime_enabled or auth_manager is None):
        return None
    if _selected_runtime_generation(config) is None:
        return None
    try:
        return ActiveKnowledgeComposition.open_existing(config, auth_manager)
    except BaseException:
        return None


__all__ = [
    "ActiveKnowledgeComposition",
    "KnowledgeContextError",
    "KnowledgeContextManifest",
    "KnowledgeContextManifestItem",
    "KnowledgeContextManifestProducer",
    "KnowledgeContextPage",
    "KnowledgeContextPageConsumer",
    "KnowledgeContextPageRequest",
    "KnowledgeInboxSnapshot",
    "build_active_knowledge_composition",
]


def _selected_runtime_generation(config: UnifiedSourceIndexRuntimeConfig) -> RuntimeGeneration | None:
    try:
        selected = RuntimeGeneration(config.selected_generation)
        capability = knowledge_source_capability_manifest()
        if selected.generation_ref != capability.generation_ref:
            return None
        return selected
    except BaseException:
        return None
