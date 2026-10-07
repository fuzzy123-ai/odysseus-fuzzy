"""Inactive, process-local private-context consent for authenticated MCP reads.

The service remains unwired to routes or dispatchers. It supports a cookie-free
MCP intent, a later authenticated browser attachment, the paired acknowledgement,
and a private guarded Planning-page completion receipt.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import inspect
import json
import math
import os
import secrets
import threading
import time
import types
import weakref
from typing import Iterator

from core.auth import AuthManager
from src.context_manifest_producer import CombinedContextManifestProducer, CombinedContextSnapshot
from src.mcp_context_authority import McpContextAuthority, McpContextAuthorityError, McpContextReadLease
from src.private_context_confirmation import ConfirmationError

_AUDIENCE = "authenticated_mcp_context_read"
_SCHEMA = "mcp_private_context_envelope"
_DOMAIN = b"mcp-private-context-envelope.v1\x00"
_CODES = frozenset({"invalid_context", "invalid_binding", "invalid_request", "closed", "capacity", "expired", "challenge_invalid", "challenge_spent", "permit_invalid", "permit_spent", "revoked", "persistence_failed", "readback_failed", "context_stale"})
_APP_PIN_NAMES = ("issue_authenticated_challenge", "_assert_owned_chat", "hold_authenticated_human_exchange",
                  "_cached_browser_submission", "_persist_ack", "_readback_ack",
                  "_readback_ack_under_guard", "_ack_descriptor", "_sign_descriptor",
                  "_reject_duplicate_keys", "_allocate_ids")

def _app_types():
    # Deferred to preserve the inactive app/service composition boundary.
    from src.private_context_confirmation_app import CookieConfirmationAuthority, ContextSelection
    return CookieConfirmationAuthority, ContextSelection

class McpPrivateContextConfirmationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _CODES else "invalid_context"
        super().__init__(self.code)

def _fail(code: str) -> None: raise McpPrivateContextConfirmationError(code) from None
def _canon(value: object) -> bytes: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
def _now_text(value: datetime) -> str: return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

@dataclass(frozen=True, slots=True)
class PreparedRequest:
    request_id: str
    private_required: bool
    item_count: int
@dataclass(frozen=True, slots=True)
class Challenge:
    token: str
    expires_at: datetime
@dataclass(frozen=True, slots=True)
class DisclosureReceipt:
    request_id: str
    user_message_id: int
    acknowledgement_message_id: int
class HumanExchange:
    """Opaque one-shot witness of a cookie-authenticated browser exchange."""
    __slots__ = ("__weakref__",)
    def __init_subclass__(cls, **kwargs: object) -> None: raise TypeError("human exchange cannot be subclassed")
    def __copy__(self): raise TypeError("human exchange cannot be copied")
    def __deepcopy__(self, memo: object): raise TypeError("human exchange cannot be copied")
    def __reduce__(self): raise TypeError("human exchange cannot be pickled")
    def __reduce_ex__(self, protocol: object): raise TypeError("human exchange cannot be pickled")
    def __repr__(self) -> str: return "HumanExchange(<redacted>)"
class Permit:
    """Opaque, exact-object capability; authority remains solely in the registry."""
    __slots__ = ("__weakref__",)
    def __init_subclass__(cls, **kwargs: object) -> None: raise TypeError("permit cannot be subclassed")
    def __copy__(self): raise TypeError("permit cannot be copied")
    def __deepcopy__(self, memo: object): raise TypeError("permit cannot be copied")
    def __reduce__(self): raise TypeError("permit cannot be pickled")
    def __reduce_ex__(self, protocol: object): raise TypeError("permit cannot be pickled")
    def __repr__(self) -> str: return "Permit(<redacted>)"
class _PlanningCompletionReceipt:
    __slots__ = ("__weakref__",)
    def __init_subclass__(cls, **kwargs: object) -> None: raise TypeError("receipt cannot be subclassed")
    def __copy__(self): raise TypeError("receipt cannot be copied")
    def __deepcopy__(self, memo: object): raise TypeError("receipt cannot be copied")
    def __reduce__(self): raise TypeError("receipt cannot be pickled")
    def __reduce_ex__(self, protocol: object): raise TypeError("receipt cannot be pickled")
    def __repr__(self) -> str: return "CompletionReceipt(<redacted>)"
@dataclass(frozen=True, slots=True)
class DetachedCaptureMaterial:
    """Detached identity material only; it contains no source payload."""
    cookie_snapshot: CombinedContextSnapshot
    mcp_snapshot: CombinedContextSnapshot
    item_count: int

@dataclass(frozen=True, slots=True, repr=False, weakref_slot=True)
class VerifiedPlanningCapture:
    page: object
    receipt: object = field(repr=False)

@dataclass(slots=True, weakref_slot=True)
class _Entry:
    request_id: str
    lease_ref: weakref.ReferenceType[object]
    binding_ref: weakref.ReferenceType[object]
    owner_sid: str
    selection: object | None
    cookie: str
    page_bytes: int
    cookie_snapshot: CombinedContextSnapshot | None
    mcp_snapshot: CombinedContextSnapshot | None
    metadata_binding: object | None
    issued: datetime
    deadline: datetime
    mono_deadline: float
    generation: int = 0
    state: str = "prepared"
    challenge: str | None = None
    challenge_deadline: datetime | None = None
    challenge_mono_deadline: float | None = None
    receipt: DisclosureReceipt | None = None
    # Set only by an application implementation that returns clocks for its committed ACK.
    ack_wall_time: datetime | None = None
    ack_mono_time: float | None = None
    exchange_nonce: int | None = None
    ack_deadline: datetime | None = None
    ack_mono_deadline: float | None = None
    ack_descriptor: object | None = None
    ack_attestation: object | None = None
    ack_role_hmacs: object | None = None
    disclosure_generation: int | None = None
    disclosure_nonce: int | None = None
    disclosure_operation_id: str | None = None
    pending_ack_operation: _PendingAckOperation | None = None
    permit_operation_nonce: int | None = None
    permit_ref: weakref.ReferenceType[Permit] | None = None
    permit_nonce: int | None = None
    permit_deadline: datetime | None = None
    permit_mono_deadline: float | None = None
    page_request_sha256: str | None = None
    intent: bool = False
    execution_nonce: int | None = None
    execution_thread_ref: weakref.ReferenceType[threading.Thread] | None = None
    execution_thread_ident: int | None = None
    execution_pid: int | None = None

@dataclass(frozen=True, slots=True)
class _PageRequestDescriptor:
    body: bytes
    digest: str
    page_bytes: int
    selection: object

@dataclass(frozen=True, slots=True)
class _PermitRow:
    permit_ref: weakref.ReferenceType[Permit]
    entry_ref: weakref.ReferenceType[_Entry]
    service_ref: weakref.ReferenceType[object]
    authority_ref: weakref.ReferenceType[object]
    lease_ref: weakref.ReferenceType[object]
    binding_ref: weakref.ReferenceType[object]
    recipient: tuple[object, ...]
    thread_ref: weakref.ReferenceType[threading.Thread]
    thread_ident: int
    pid: int
    request_id: str
    generation: int
    nonce: int

@dataclass(frozen=True, slots=True)
class _CompletionRow:
    receipt_ref: weakref.ReferenceType[_PlanningCompletionReceipt]
    capture_ref: weakref.ReferenceType[VerifiedPlanningCapture]
    entry_ref: weakref.ReferenceType[_Entry]
    service_ref: weakref.ReferenceType[object]
    authority_ref: weakref.ReferenceType[object]
    lease_ref: weakref.ReferenceType[object]
    binding_ref: weakref.ReferenceType[object]
    thread_ref: weakref.ReferenceType[threading.Thread]
    request_id: str
    generation: int
    nonce: int
    pid: int
    recipient: tuple[object, ...]
    page_sha256: str

@dataclass(frozen=True, slots=True)
class _PendingAckOperation:
    generation: int
    nonce: int
    operation_id: str

@dataclass(frozen=True, slots=True)
class _HumanExchangeRow:
    exchange_ref: weakref.ReferenceType[HumanExchange]
    app_ref: weakref.ReferenceType[object]
    service_ref: weakref.ReferenceType[object]
    authority_ref: weakref.ReferenceType[object]
    lease_ref: weakref.ReferenceType[McpContextReadLease]
    binding_ref: weakref.ReferenceType[object]
    thread_ref: weakref.ReferenceType[threading.Thread]
    request_id: str
    challenge: str
    chat_session_id: str
    generation: int
    nonce: int
    raw_user_command: str
    recipient9: bytes
    thread_ident: int
    pid: int

class McpPrivateContextConfirmationService:
    """Fail-closed consent registry.  It owns no borrowed authority/service."""
    def __init__(self, *, auth_manager: AuthManager, authority: McpContextAuthority,
                 producer: CombinedContextManifestProducer, cookie_authority: CookieConfirmationAuthority,
                 session_factory: object, monotonic: object = time.monotonic,
                 wall_time: object = None) -> None:
        if (type(auth_manager) is not AuthManager or type(authority) is not McpContextAuthority
                or type(producer) is not CombinedContextManifestProducer or not callable(session_factory)):
            _fail("invalid_context")
        CookieConfirmationAuthority, _ = _app_types()
        if type(cookie_authority) is not CookieConfirmationAuthority: _fail("invalid_context")
        if authority._auth_manager is not auth_manager or producer._auth_manager is not auth_manager:
            _fail("invalid_context")
        self._auth, self._authority, self._producer = auth_manager, authority, producer
        self._cookie_authority, self._session_factory = cookie_authority, session_factory
        if (cookie_authority._auth_manager is not auth_manager or producer._mcp_authority is not authority
                or authority._session_factory is not session_factory
                or authority._session_factory is not authority._session_factory_pin): _fail("invalid_context")
        if not callable(monotonic) or (wall_time is not None and not callable(wall_time)): _fail("invalid_context")
        self._auth_pin = auth_manager; self._authority_pin = authority
        self._producer_pin = producer; self._cookie_authority_pin = cookie_authority
        self._planning_reader_pin = type(producer._planning).read_document
        self._session_factory_pin = session_factory
        self._entries: dict[str, _Entry] = {}; self._permits: dict[int, _PermitRow] = {}; self._exchanges: dict[int, _HumanExchangeRow] = {}; self._completions: dict[int, _CompletionRow] = {}
        self._app_ref: weakref.ReferenceType[object] | None = None
        self._app_method_pin: tuple[tuple[str, object], ...] | None = None
        self._lock = threading.RLock(); self._closed = False
        self._last_wall: float | None = None; self._last_mono: float | None = None
        self._key = secrets.token_bytes(32); self._wall = wall_time or (lambda: datetime.now(timezone.utc)); self._mono = monotonic
        self._attestation_key = secrets.token_bytes(32)
        self._execution_local = threading.local()

    def prepare(self, lease: McpContextReadLease, cookie: str, selection: ContextSelection, *, page_bytes: int = 16384) -> PreparedRequest:
        self._reject_authority_reentry()
        self._assert_pins()
        _, ContextSelection = _app_types()
        if (type(lease) is not McpContextReadLease or type(selection) is not ContextSelection
                or type(cookie) is not str or type(page_bytes) is not int or not 1 <= page_bytes <= 16384):
            _fail("invalid_request")
        now, mono = self._clock()
        try:
            foundation = self._authority.recheck_lease(lease)
            lease_expiry = lease.expires_at
            if type(lease_expiry) not in (int, float) or isinstance(lease_expiry, bool) or not math.isfinite(lease_expiry): _fail("expired")
            lease_ttl = float(lease_expiry) - now.timestamp()
            ttl = min(900.0, lease_ttl)
            if not math.isfinite(ttl) or ttl <= 0: _fail("expired")
            with self._guards(lease, cookie) as binding:
                cookie_first = self._capture_cookie(binding, selection)
                cookie_last = self._capture_cookie(binding, selection)
                mcp_first = self._capture_mcp(lease, selection)
                mcp_last = self._capture_mcp(lease, selection)
            if cookie_first != cookie_last or mcp_first != mcp_last: _fail("context_stale")
            self._pair(cookie_first, mcp_first, selection)
            foundation = self._authority.recheck_lease(lease)
            end_wall, end_mono = self._clock()
            self._assert_pins()
            if end_wall.timestamp() >= lease_expiry or end_mono < mono: _fail("expired")
            deadline = now + timedelta(seconds=ttl)
            mono_deadline = mono + ttl
            if end_wall >= deadline or end_mono >= mono_deadline: _fail("expired")
        except (McpPrivateContextConfirmationError, McpContextAuthorityError):
            raise
        except Exception:
            _fail("invalid_binding")
        with self._lock:
            self._prune(end_wall, end_mono)
            if self._closed: _fail("closed")
            owner_sid = foundation.owner_subject_id
            active = tuple(self._entries.values())
            if (len(active) >= 64 or sum(1 for x in active if x.owner_sid == owner_sid) >= 8
                    or any(x.lease_ref() is lease for x in active)):
                _fail("capacity")
            request_id = secrets.token_urlsafe(32)
            if request_id in self._entries: _fail("capacity")
            self._entries[request_id] = _Entry(request_id, weakref.ref(lease), weakref.ref(lease.binding),
                owner_sid, selection, cookie, page_bytes, cookie_first, mcp_first,
                mcp_first.mcp_metadata_binding, end_wall, deadline, mono_deadline)
            self._entries[request_id].page_request_sha256 = self._page_request_descriptor(selection, page_bytes).digest
        return PreparedRequest(request_id, True, len(selection.items))

    def prepare_intent(self, lease: McpContextReadLease, selection: ContextSelection, *, page_bytes: int = 16384) -> PreparedRequest:
        self._reject_authority_reentry()
        self._assert_pins()
        _, ContextSelection = _app_types()
        if (type(lease) is not McpContextReadLease or type(selection) is not ContextSelection
                or type(page_bytes) is not int or not 1 <= page_bytes <= 16384):
            _fail("invalid_request")
        now, mono = self._clock()
        try:
            with self._authority.live_lease(lease) as binding:
                if lease.binding is not binding: _fail("invalid_binding")
                first = self._capture_mcp(lease, selection)
                final = self._capture_mcp(lease, selection)
                if first != final: _fail("context_stale")
                foundation = self._authority.recheck_lease(lease)
            wall, mono_end = self._clock()
            self._assert_pins()
            lease_expiry = lease.expires_at
            if type(lease_expiry) not in (int, float) or isinstance(lease_expiry, bool) or not math.isfinite(lease_expiry): _fail("expired")
            remaining = min(900.0, float(lease_expiry) - now.timestamp())
            deadline = now + timedelta(seconds=remaining); mono_deadline = mono + remaining
            if (not math.isfinite(remaining) or remaining <= 0 or mono_end < mono
                    or wall >= deadline or mono_end >= mono_deadline or wall.timestamp() >= lease_expiry): _fail("expired")
        except (McpPrivateContextConfirmationError, McpContextAuthorityError): raise
        except Exception: _fail("invalid_binding")
        descriptor = self._page_request_descriptor(selection, page_bytes)
        with self._lock:
            self._prune(wall, mono_end)
            if self._closed: _fail("closed")
            active = tuple(self._entries.values()); owner_sid = foundation.owner_subject_id
            if (len(active) >= 64 or sum(1 for x in active if x.owner_sid == owner_sid) >= 8
                    or any(x.lease_ref() is lease for x in active)):
                _fail("capacity")
            request_id = secrets.token_urlsafe(32)
            if request_id in self._entries: _fail("capacity")
            entry = _Entry(request_id, weakref.ref(lease), weakref.ref(lease.binding), owner_sid,
                selection, "", page_bytes, None, first, first.mcp_metadata_binding, wall,
                deadline, mono_deadline)
            entry.intent = True; entry.state = "intent"; entry.page_request_sha256 = descriptor.digest
            self._entries[request_id] = entry
        return PreparedRequest(request_id, True, len(selection.items))

    @staticmethod
    def _page_request_descriptor(selection: object, page_bytes: int) -> _PageRequestDescriptor:
        if type(page_bytes) is not int or not 1 <= page_bytes <= 16384: _fail("invalid_request")
        items = []
        for item in selection.items:
            locator = item.forge_locator
            items.append({"source": item.source, "selection_ref": item.selection_ref,
                "revision_ref": item.revision_ref, "forge_locator": None if locator is None else
                {"repo_id": locator.repo_id, "version_id": locator.version_id, "path": locator.path}})
        body = _canon({"schema": "mcp_private_context_page_request.v1", "audience": "authenticated_mcp_context_page_request",
            "selection": items, "page_bytes": page_bytes})
        return _PageRequestDescriptor(body, hashlib.sha256(body).hexdigest(), page_bytes, selection)

    def issue_challenge(self, request_id: str) -> Challenge:
        self._reject_authority_reentry()
        self._assert_request_pins(request_id)
        now, mono = self._clock()
        with self._lock:
            entry = self._entry(request_id, now, mono, "prepared")
            if entry.intent or entry.cookie_snapshot is None or entry.mcp_snapshot is None:
                _fail("invalid_binding")
            wall_deadline = min(entry.deadline, now + timedelta(seconds=300))
            mono_deadline = min(entry.mono_deadline, mono + 300.0)
            if wall_deadline <= now or mono_deadline <= mono: self._terminate(entry); _fail("expired")
            entry.challenge = secrets.token_urlsafe(32)
            entry.challenge_deadline = wall_deadline; entry.challenge_mono_deadline = mono_deadline
            entry.state = "challenged"; entry.generation += 1
            return Challenge(entry.challenge, entry.challenge_deadline)

    def _attach_authenticated_challenge(self, request_id: str, cookie: str) -> Challenge:
        """Attach one real browser cookie to a cookie-free MCP intent."""
        self._reject_authority_reentry()
        self._assert_request_pins(request_id)
        if type(cookie) is not str or not cookie: _fail("invalid_binding")
        now, mono = self._clock()
        with self._lock:
            entry = self._entry(request_id, now, mono, "intent")
            if not entry.intent or entry.cookie or entry.cookie_snapshot is not None: _fail("invalid_binding")
            lease = entry.lease_ref()
            if lease is None: self._terminate(entry); _fail("revoked")
            entry.state = "attaching"; entry.generation += 1
            generation = entry.generation; nonce = secrets.randbits(63)
            entry.execution_nonce = nonce
        token = secrets.token_urlsafe(32)
        try:
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("invalid_binding")
            with self._guards(lease, cookie) as binding:
                if binding is not lease.binding: _fail("invalid_binding")
                cookie_binding = self._cookie_authority.current_binding()
                self._cookie_authority.recheck(cookie_binding)
                if (self._cookie_authority.current_binding() is not cookie_binding
                        or cookie_binding.owner_sid != binding.owner_subject_id
                        or cookie_binding.chat_session_id != binding.chat_session_id
                        or cookie_binding.security_revision != binding.security_action_revision):
                    _fail("invalid_binding")
                self._registered_app_method("_assert_owned_chat")(app, cookie_binding)
                cookie_first = self._capture_cookie(binding, entry.selection)
                cookie_last = self._capture_cookie(binding, entry.selection)
                mcp_first = self._capture_mcp(lease, entry.selection)
                mcp_last = self._capture_mcp(lease, entry.selection)
                if (cookie_first != cookie_last or mcp_first != mcp_last
                        or entry.mcp_snapshot is None or mcp_first != entry.mcp_snapshot):
                    _fail("context_stale")
                self._pair(cookie_first, mcp_first, entry.selection)
            final_clock = self._clock()
            self._assert_pins()
        except (McpPrivateContextConfirmationError, McpContextAuthorityError):
            with self._lock:
                if self._entries.get(request_id) is entry: self._terminate(entry)
            raise
        except BaseException:
            with self._lock:
                if self._entries.get(request_id) is entry: self._terminate(entry)
            _fail("invalid_binding")
        with self._lock:
            wall, final_mono = final_clock
            if (self._closed or self._entries.get(request_id) is not entry or entry.state != "attaching"
                    or entry.generation != generation or entry.execution_nonce != nonce
                    or wall >= entry.deadline or final_mono >= entry.mono_deadline):
                if self._entries.get(request_id) is entry: self._terminate(entry)
                _fail("revoked")
            challenge_wall = min(entry.deadline, wall + timedelta(seconds=300))
            challenge_mono = min(entry.mono_deadline, final_mono + 300.0)
            if challenge_wall <= wall or challenge_mono <= final_mono:
                self._terminate(entry); _fail("expired")
            entry.cookie = cookie; entry.cookie_snapshot = cookie_first
            entry.metadata_binding = mcp_first.mcp_metadata_binding
            entry.challenge = token; entry.challenge_deadline = challenge_wall
            entry.challenge_mono_deadline = challenge_mono
            entry.intent = False; entry.execution_nonce = None
            entry.state = "challenged"; entry.generation += 1
            return Challenge(token, challenge_wall)

    def disclose(self, request_id: str, challenge_token: str, *, raw_user_command: str,
                 human_exchange: object) -> DisclosureReceipt:
        self._reject_authority_reentry()
        self._assert_request_pins(request_id)
        now, mono = self._clock()
        if (type(challenge_token) is not str or type(raw_user_command) is not str
                or raw_user_command != "/mcp-context-disclose " + challenge_token
                or type(human_exchange) is not HumanExchange): _fail("invalid_request")
        with self._lock:
            entry = self._entry(request_id, now, mono, "human_reserved")
            if (type(challenge_token) is not str or entry.challenge is None or not hmac.compare_digest(challenge_token, entry.challenge)
                    or entry.challenge_deadline is None or entry.challenge_mono_deadline is None
                    or now >= entry.challenge_deadline or mono >= entry.challenge_mono_deadline):
                self._terminate(entry); _fail("challenge_invalid")
            witness = self._exchanges.get(id(human_exchange)); binding = entry.binding_ref()
            app = self._app_ref() if self._app_ref is not None else None
            lease = entry.lease_ref()
            if (type(witness) is not _HumanExchangeRow or witness.exchange_ref() is not human_exchange
                    or witness.request_id != request_id or witness.challenge != challenge_token
                    or binding is None or witness.chat_session_id != binding.chat_session_id):
                self._terminate(entry); _fail("invalid_binding")
            if (binding is None or app is None or lease is None
                    or witness.app_ref() is not app or witness.service_ref() is not self
                    or witness.authority_ref() is not self._authority or witness.lease_ref() is not lease
                    or witness.binding_ref() is not binding or witness.thread_ref() is not threading.current_thread()
                    or witness.generation != entry.generation or witness.nonce != entry.exchange_nonce
                    or witness.raw_user_command != raw_user_command or witness.thread_ident != threading.get_ident()
                    or witness.pid != os.getpid()
                    or witness.recipient9 != _canon(entry.metadata_binding.canonical())):
                self._terminate(entry); _fail("invalid_binding")
            self._exchanges.pop(id(human_exchange), None)
            entry.state = "disclosing"; generation = entry.generation; exchange_nonce = entry.exchange_nonce
            pending_operation = entry.pending_ack_operation
            if (pending_operation is None or pending_operation.generation != generation
                    or pending_operation.nonce != exchange_nonce
                    or pending_operation.operation_id != entry.disclosure_operation_id):
                self._terminate(entry); _fail("revoked")
            entry.disclosure_generation = generation; entry.disclosure_nonce = exchange_nonce
        lease = entry.lease_ref()
        if lease is None:
            with self._lock: self._terminate(entry)
            _fail("revoked")
        try:
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("invalid_binding")
            self._registered_app_method("_persist_ack")(app, entry, lease, challenge_token, raw_user_command,
                human_exchange, pending_operation)
            receipt = entry.receipt
            if type(receipt) is not DisclosureReceipt or receipt.request_id != request_id:
                _fail("readback_failed")
        except (McpPrivateContextConfirmationError, McpContextAuthorityError):
            with self._lock: self._terminate(entry)
            raise
        except BaseException:
            with self._lock: self._terminate(entry)
            _fail("persistence_failed")
        final_clock = self._clock()
        self._assert_request_pins(request_id)
        with self._lock:
            now, mono = final_clock
            if (self._closed or self._entries.get(request_id) is not entry or entry.generation != generation
                    or entry.state != "disclosing" or entry.ack_deadline is None or entry.ack_mono_deadline is None
                    or entry.exchange_nonce != exchange_nonce or entry.pending_ack_operation is not pending_operation
                    or now >= entry.ack_deadline or mono >= entry.ack_mono_deadline
                    or not self._valid_attestation(entry)):
                self._terminate(entry); _fail("revoked")
            entry.receipt = receipt
            entry.state = "pending_ack"
        return receipt

    def issue_permit(self, request_id: str) -> Permit:
        self._reject_authority_reentry()
        self._assert_request_pins(request_id)
        now, mono = self._clock()
        with self._lock:
            entry = self._entry(request_id, now, mono, "disclosed")
            if entry.receipt is None or entry.ack_wall_time is None or entry.ack_mono_time is None:
                self._terminate(entry); _fail("readback_failed")
            ack_wall, ack_mono = entry.ack_wall_time, entry.ack_mono_time
            if (type(ack_wall) is not datetime or ack_wall.tzinfo is None
                    or type(ack_mono) not in (int, float) or not math.isfinite(ack_mono)):
                self._terminate(entry); _fail("readback_failed")
            try: ack_wall_ts = ack_wall.timestamp()
            except Exception:
                self._terminate(entry); _fail("readback_failed")
            if (not math.isfinite(ack_wall_ts) or ack_wall_ts > now.timestamp() or float(ack_mono) > mono
                    or now.timestamp() >= entry.deadline.timestamp() or mono >= entry.mono_deadline
                    or entry.ack_deadline is None or entry.ack_mono_deadline is None
                    or now >= entry.ack_deadline or mono >= entry.ack_mono_deadline
                    or not self._valid_attestation(entry)):
                self._terminate(entry); _fail("readback_failed")
            wall_deadline = min(entry.deadline, entry.ack_deadline)
            mono_deadline = min(entry.mono_deadline, entry.ack_mono_deadline)
            if wall_deadline <= now or mono_deadline <= mono:
                self._terminate(entry); _fail("expired")
            if entry.permit_ref is not None and entry.permit_ref() is not None: _fail("permit_spent")
            lease = entry.lease_ref(); binding = entry.binding_ref()
            if lease is None or binding is None: self._terminate(entry); _fail("invalid_binding")
            entry.state = "permit_issuing"; entry.generation += 1
            service_generation = entry.generation; service_nonce = secrets.randbits(63)
            entry.permit_operation_nonce = service_nonce
        try:
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("invalid_binding")
            self._fresh_pair(entry, lease)
            self._registered_app_method("_readback_ack")(app, entry)
            self._assert_pins()
        except (McpPrivateContextConfirmationError, McpContextAuthorityError):
            with self._lock: self._terminate(entry)
            raise
        except BaseException:
            with self._lock: self._terminate(entry)
            _fail("readback_failed")
        try:
            final_clock = self._clock()
            self._assert_pins()
        except BaseException:
            with self._lock:
                if (self._entries.get(request_id) is entry and entry.state == "permit_issuing"
                        and entry.generation == service_generation and entry.permit_operation_nonce == service_nonce):
                    self._terminate(entry)
            raise
        with self._lock:
            now, mono = final_clock
            if (self._entries.get(request_id) is not entry or entry.state != "permit_issuing"
                    or self._closed or entry.generation != service_generation
                    or entry.permit_operation_nonce != service_nonce
                    or entry.ack_deadline is None or entry.ack_mono_deadline is None
                    or now >= entry.ack_deadline or mono >= entry.ack_mono_deadline
                    or not self._valid_attestation(entry)):
                self._terminate(entry); _fail("revoked")
            handle = Permit(); key = id(handle); generation = entry.generation + 1
            nonce = secrets.randbits(63)
            thread = threading.current_thread()
            permit_ref = weakref.ref(handle, lambda dead, k=key: self._drop_permit(k, dead))
            entry.permit_ref = permit_ref; entry.permit_nonce = nonce
            entry.permit_deadline = wall_deadline; entry.permit_mono_deadline = mono_deadline
            entry.state = "permitted"; entry.generation = generation; entry.permit_operation_nonce = None
            recipient = (binding.owner_subject_id, binding.chat_session_id, binding.token_id,
                         binding.scope, binding.cache_generation, binding.security_action_revision,
                         binding.process_epoch)
            self._permits[key] = _PermitRow(
                permit_ref, weakref.ref(entry), weakref.ref(self), weakref.ref(self._authority),
                weakref.ref(lease), weakref.ref(binding), recipient, weakref.ref(thread),
                threading.get_ident(), os.getpid(), entry.request_id, generation, nonce)
            return handle

    @contextmanager
    def verify(self, permit: Permit) -> Iterator[DetachedCaptureMaterial]:
        entry, lease, binding, generation, operation_nonce = self._reserve_permit(permit, "verifying")
        try:
            now, mono = self._clock()
            if getattr(self._execution_local, "violation", False): _fail("revoked")
            if now >= entry.permit_deadline or mono >= entry.permit_mono_deadline: _fail("permit_invalid")
            self._assert_pins()
            if getattr(self._execution_local, "violation", False): _fail("revoked")
            with self._guards(lease, entry.cookie) as held_binding:
                if held_binding is not binding: _fail("invalid_binding")
                self._revalidate_under_guard(entry, lease, held_binding)
                material = DetachedCaptureMaterial(entry.cookie_snapshot, entry.mcp_snapshot, len(entry.selection.items))
                yield material
                self._revalidate_under_guard(entry, lease, held_binding)
            final_wall, final_mono = self._clock()
            self._assert_pins()
            with self._lock:
                if (self._closed or self._entries.get(entry.request_id) is not entry
                        or entry.state != "verifying" or entry.generation != generation
                        or entry.permit_nonce != operation_nonce or entry.permit_deadline is None
                        or entry.permit_mono_deadline is None or final_wall >= entry.permit_deadline
                        or final_mono >= entry.permit_mono_deadline): _fail("revoked")
                self._terminate(entry)
        except BaseException:
            with self._lock: self._terminate(entry)
            raise

    def _reserve_permit(self, permit: object, state: str):
        self._reject_authority_reentry()
        if type(permit) is not Permit or state not in {"verifying", "executing"}: _fail("permit_invalid")
        with self._lock:
            row = self._permits.get(id(permit))
            if (row is None or row.permit_ref() is not permit or row.entry_ref() is None
                    or row.service_ref() is not self or row.authority_ref() is not self._authority
                    or row.thread_ref() is not threading.current_thread()
                    or row.thread_ident != threading.get_ident() or row.pid != os.getpid()): _fail("permit_invalid")
            entry = row.entry_ref(); lease = row.lease_ref(); binding = row.binding_ref()
            if (entry is None or lease is None or binding is None or row.request_id != entry.request_id
                    or self._entries.get(row.request_id) is not entry or entry.state != "permitted"
                    or entry.permit_ref is None or entry.permit_ref() is not permit
                    or entry.generation != row.generation or entry.permit_nonce != row.nonce
                    or self._recipient(binding) != row.recipient or lease.binding is not binding
                    or entry.permit_deadline is None or entry.permit_mono_deadline is None):
                if entry is not None: self._terminate(entry)
                _fail("permit_invalid")
            if self._permits.get(id(permit)) is not row: _fail("permit_invalid")
            self._permits.pop(id(permit), None)
            entry.state = state; entry.generation += 1
            nonce = secrets.randbits(63); entry.permit_nonce = nonce
            entry.execution_nonce = nonce
            thread = threading.current_thread(); entry.execution_thread_ref = weakref.ref(thread)
            entry.execution_thread_ident = threading.get_ident(); entry.execution_pid = os.getpid()
            return entry, lease, binding, entry.generation, nonce

    def _revalidate_under_guard(self, entry: _Entry, lease: McpContextReadLease, binding: object) -> None:
        now, mono = self._clock()
        if getattr(self._execution_local, "violation", False): _fail("revoked")
        if (now >= entry.deadline or mono >= entry.mono_deadline or now >= entry.permit_deadline
                or mono >= entry.permit_mono_deadline or entry.selection is None
                or entry.cookie_snapshot is None or entry.mcp_snapshot is None): _fail("expired")
        if lease is not entry.lease_ref() or binding is not lease.binding or entry.binding_ref() is not binding:
            _fail("invalid_binding")
        c1 = self._capture_cookie(binding, entry.selection); c2 = self._capture_cookie(binding, entry.selection)
        m1 = self._capture_mcp(lease, entry.selection); m2 = self._capture_mcp(lease, entry.selection)
        if c1 != c2 or m1 != m2 or c1 != entry.cookie_snapshot or m1 != entry.mcp_snapshot:
            _fail("context_stale")
        self._pair(c1, m1, entry.selection)
        if (c1.mcp_metadata_binding is not None or m1.mcp_metadata_binding is None
                or m1.mcp_metadata_binding.canonical() != entry.metadata_binding.canonical()): _fail("context_stale")
        app = self._app_ref() if self._app_ref is not None else None
        if app is None: _fail("readback_failed")
        self._registered_app_method("_readback_ack_under_guard")(app, entry, binding)
        self._assert_pins()
        if getattr(self._execution_local, "violation", False): _fail("revoked")

    def _execute_planning_page(self, opaquePermit: object, *, target_index: int, offset: int,
                               validate_detached: object = None) -> VerifiedPlanningCapture:
        self._reject_authority_reentry()
        if (type(target_index) is not int or not 0 <= target_index <= (1 << 53) - 1
                or type(offset) is not int or not 0 <= offset <= (1 << 53) - 1
                or validate_detached is not None and (type(validate_detached) not in {types.FunctionType, types.MethodType}
                    or inspect.iscoroutinefunction(validate_detached)
                    or inspect.isgeneratorfunction(validate_detached)
                    or inspect.isasyncgenfunction(validate_detached))):
            _fail("invalid_request")
        from src.planning_context_reader import PlanningContextManifestProducer, PlanningContextPage, PlanningContextReaderError
        entry, lease, binding, generation, nonce = self._reserve_permit(opaquePermit, "executing")
        self._execution_local.active = True; self._execution_local.violation = False
        try:
            now, mono = self._clock()
            if getattr(self._execution_local, "violation", False): _fail("revoked")
            if now >= entry.permit_deadline or mono >= entry.permit_mono_deadline: _fail("permit_invalid")
            self._assert_pins()
            if getattr(self._execution_local, "violation", False): _fail("revoked")
            selection = entry.selection
            if (selection is None or target_index >= len(selection.items)
                    or entry.page_request_sha256 is None or entry.page_bytes < 1
                    or entry.page_bytes > 16384): _fail("invalid_request")
            target = selection.items[target_index]
            if target.source != "planning": _fail("invalid_request")
            native_item = next((item for item in entry.mcp_snapshot.items
                                if (item.source, item.selection_ref, item.revision_ref) ==
                                   (target.source, target.selection_ref, target.revision_ref)), None)
            if native_item is None or native_item.payload_sha256 is None: _fail("context_stale")
            with self._guards(lease, entry.cookie) as held_binding:
                if held_binding is not binding: _fail("invalid_binding")
                self._revalidate_under_guard(entry, lease, held_binding)
                cookie_binding = self._cookie_authority.current_binding()
                self._cookie_authority.recheck(cookie_binding)
                if (cookie_binding.chat_session_id != binding.chat_session_id
                        or cookie_binding.owner_sid != binding.owner_subject_id): _fail("invalid_binding")
                planning = self._producer._planning
                if type(planning) is not PlanningContextManifestProducer: _fail("invalid_binding")
                self._assert_pins()
                payload = planning.read_document(cookie_binding, target)
                self._cookie_authority.recheck(cookie_binding)
                self._authority.recheck_lease(lease)
                if type(payload) is not bytes: _fail("context_stale")
                payload_digest = hashlib.sha256(payload).hexdigest()
                if payload_digest != native_item.payload_sha256: _fail("context_stale")
                whole = "sha256:" + payload_digest
                if offset > len(payload): _fail("invalid_request")
                end = min(len(payload), offset + entry.page_bytes)
                page_bytes = bytes(payload[offset:end])
                target_digests = entry.cookie_snapshot.target_manifest_digests
                if (len(target_digests) != len(selection.items) or target_index >= len(target_digests)
                        or selection.items[target_index] is not target): _fail("context_stale")
                page = PlanningContextPage(page_bytes=page_bytes, total_bytes=len(payload), offset=offset,
                    end=end, whole_sha256=whole, page_sha256="sha256:" + hashlib.sha256(page_bytes).hexdigest(),
                    manifest_digest=target_digests[target_index])
                page_commitment = self._page_commitment(page, PlanningContextPage)
                if validate_detached is not None:
                    child_result = validate_detached(page)
                    if inspect.isawaitable(child_result) or inspect.isgenerator(child_result) or inspect.isasyncgen(child_result):
                        close = getattr(child_result, "close", None)
                        if callable(close):
                            try: close()
                            except BaseException: pass
                        _fail("invalid_request")
                    if child_result is not None: _fail("invalid_request")
                if self._page_commitment(page, PlanningContextPage) != page_commitment:
                    _fail("context_stale")
                if self._cookie_authority.current_binding() is not cookie_binding:
                    _fail("invalid_binding")
                if getattr(self._execution_local, "violation", False): _fail("revoked")
                self._cookie_authority.recheck(cookie_binding)
                self._revalidate_under_guard(entry, lease, held_binding)
                self._assert_pins()
            final_clock = self._clock()
            self._assert_pins()
            if (entry.lease_ref() is not lease or entry.binding_ref() is not binding
                    or lease.binding is not binding or self._recipient(binding) != self._recipient(entry.binding_ref())):
                _fail("invalid_binding")
            with self._lock:
                wall, final_mono = final_clock
                if (self._closed or self._entries.get(entry.request_id) is not entry
                        or entry.state != "executing" or entry.generation != generation
                        or entry.execution_nonce != nonce or entry.permit_nonce != nonce
                        or entry.execution_thread_ref is None or entry.execution_thread_ref() is not threading.current_thread()
                        or entry.execution_thread_ident != threading.get_ident() or entry.execution_pid != os.getpid()
                        or wall >= entry.permit_deadline or final_mono >= entry.permit_mono_deadline
                        or getattr(self._execution_local, "violation", False)):
                    _fail("revoked")
                receipt = _PlanningCompletionReceipt(); capture = VerifiedPlanningCapture(page, receipt)
                key = id(receipt); page_digest = page_commitment
                receipt_ref = weakref.ref(receipt, lambda dead, k=key: self._drop_completion(k, dead))
                capture_ref = weakref.ref(capture, lambda dead, k=key: self._drop_capture(k, dead))
                self._completions[key] = _CompletionRow(receipt_ref, capture_ref, weakref.ref(entry),
                    weakref.ref(self), weakref.ref(self._authority), weakref.ref(lease), weakref.ref(binding),
                    weakref.ref(threading.current_thread()), entry.request_id, generation, nonce, os.getpid(),
                    self._recipient(binding), page_digest)
                entry.state = "consumed"
                return capture
        except (McpPrivateContextConfirmationError, McpContextAuthorityError, PlanningContextReaderError):
            with self._lock:
                for key, row in tuple(self._completions.items()):
                    if row.entry_ref() is entry: self._completions.pop(key, None)
                if self._entries.get(entry.request_id) is entry: self._terminate(entry)
            raise
        except BaseException:
            with self._lock:
                for key, row in tuple(self._completions.items()):
                    if row.entry_ref() is entry: self._completions.pop(key, None)
                if self._entries.get(entry.request_id) is entry: self._terminate(entry)
            _fail("context_stale")
        finally:
            self._execution_local.active = False
            self._execution_local.violation = False

    def _drop_completion(self, key: int, dead: weakref.ReferenceType[_PlanningCompletionReceipt]) -> None:
        with self._lock:
            row = self._completions.get(key)
            if row is not None and row.receipt_ref is dead:
                self._completions.pop(key, None)
                entry = row.entry_ref()
                if entry is not None and self._entries.get(row.request_id) is entry:
                    self._terminate(entry)

    def _drop_capture(self, key: int, dead: weakref.ReferenceType[VerifiedPlanningCapture]) -> None:
        with self._lock:
            row = self._completions.get(key)
            if row is not None and row.capture_ref is dead:
                self._completions.pop(key, None)
                entry = row.entry_ref()
                if entry is not None and self._entries.get(row.request_id) is entry:
                    self._terminate(entry)

    def _consume_planning_completion(self, capture: object) -> None:
        self._reject_authority_reentry()
        from src.planning_context_reader import PlanningContextPage
        if type(capture) is not VerifiedPlanningCapture or type(capture.receipt) is not _PlanningCompletionReceipt:
            _fail("permit_invalid")
        receipt = capture.receipt; page = capture.page
        try:
            instance_methods = vars(self)
        except BaseException:
            instance_methods = {"_execute_planning_page": None}
        if (type(self).__dict__.get("_execute_planning_page") is not _TRUSTED_PLANNING_EXECUTOR
                or type(self).__dict__.get("_consume_planning_completion") is not _TRUSTED_COMPLETION_CONSUMER
                or "_execute_planning_page" in instance_methods
                or "_consume_planning_completion" in instance_methods):
            with self._lock:
                row = self._completions.get(id(receipt))
                entry = row.entry_ref() if type(row) is _CompletionRow else None
                if entry is not None and self._entries.get(row.request_id) is entry:
                    self._terminate(entry)
            _fail("permit_invalid")
        with self._lock:
            row = self._completions.get(id(receipt))
            if row is None: _fail("permit_invalid")
            entry = row.entry_ref()
            try: page_digest = self._page_commitment(page, PlanningContextPage)
            except BaseException: page_digest = None
            if (row.receipt_ref() is not receipt or row.capture_ref() is not capture
                    or row.service_ref() is not self or row.authority_ref() is not self._authority
                    or row.thread_ref() is not threading.current_thread() or row.pid != os.getpid()
                    or page_digest != row.page_sha256):
                self._completions.pop(id(receipt), None)
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)
                _fail("permit_invalid")
            if (entry is None or self._entries.get(row.request_id) is not entry or entry.state != "consumed"
                    or entry.generation != row.generation or entry.execution_nonce != row.nonce
                    or row.lease_ref() is None or row.binding_ref() is None
                    or self._recipient(row.binding_ref()) != row.recipient):
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)
                _fail("permit_invalid")
            entry.state = "completion_consuming"; entry.generation += 1
            reserved_generation = entry.generation; reserved_nonce = entry.execution_nonce
        try:
            self._clock()
            self._assert_pins()
            lease = row.lease_ref(); binding = row.binding_ref()
            if lease is None or binding is None: _fail("permit_invalid")
            with self._authority.live_lease(lease) as live_binding:
                if live_binding is not binding or lease.binding is not binding: _fail("invalid_binding")
                self._assert_pins()
            now, mono = self._clock()
            self._assert_pins()
        except BaseException:
            with self._lock:
                if self._entries.get(row.request_id) is entry: self._terminate(entry)
            raise
        with self._lock:
            if (self._closed or self._entries.get(row.request_id) is not entry
                    or self._completions.get(id(receipt)) is not row
                    or entry.state != "completion_consuming" or entry.generation != reserved_generation
                    or entry.execution_nonce != reserved_nonce or reserved_nonce != row.nonce
                    or row.receipt_ref() is not receipt or row.capture_ref() is not capture
                    or row.thread_ref() is not threading.current_thread() or row.pid != os.getpid()
                    or entry.lease_ref() is not row.lease_ref() or entry.binding_ref() is not row.binding_ref()
                    or entry.permit_deadline is None or entry.permit_mono_deadline is None
                    or self._recipient(row.binding_ref()) != row.recipient
                    or now >= entry.permit_deadline or mono >= entry.permit_mono_deadline
                    or now >= entry.deadline or mono >= entry.mono_deadline):
                if self._entries.get(row.request_id) is entry: self._terminate(entry)
                _fail("permit_invalid")
            self._completions.pop(id(receipt), None)
            entry.permit_ref = None; entry.permit_nonce = None
            entry.permit_deadline = None; entry.permit_mono_deadline = None
            entry.permit_operation_nonce = None
            entry.execution_nonce = None; entry.execution_thread_ref = None
            entry.execution_thread_ident = None; entry.execution_pid = None
            entry.state = "disclosed"

    @staticmethod
    def _page_commitment(page: object, expected_type: type) -> str:
        if (type(page) is not expected_type or type(page.page_bytes) is not bytes
                or type(page.total_bytes) is not int or type(page.offset) is not int
                or type(page.end) is not int or type(page.whole_sha256) is not str
                or type(page.page_sha256) is not str or type(page.manifest_digest) is not str
                or page.byteform != "durable_revision_document"
                or page.page_sha256 != "sha256:" + hashlib.sha256(page.page_bytes).hexdigest()
                or not 0 <= page.offset <= page.end <= page.total_bytes
                or len(page.page_bytes) != page.end - page.offset): _fail("context_stale")
        return hashlib.sha256(_canon({"offset": page.offset, "end": page.end, "total_bytes": page.total_bytes,
            "whole_sha256": page.whole_sha256, "page_sha256": page.page_sha256,
            "manifest_digest": page.manifest_digest, "byteform": page.byteform,
            "page_bytes_sha256": hashlib.sha256(page.page_bytes).hexdigest()})).hexdigest()

    def revoke(self, request_id: str) -> None:
        if type(request_id) is not str: _fail("invalid_request")
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None: _fail("invalid_request")
            self._terminate(entry)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for entry in tuple(self._entries.values()): self._terminate(entry)
            self._permits.clear(); self._exchanges.clear(); self._completions.clear()
            self._app_ref = None; self._app_method_pin = None

    def _register_app(self, app: object) -> None:
        self._reject_authority_reentry()
        from src.mcp_private_context_confirmation_app import McpPrivateContextConfirmationApp
        with self._lock:
            if self._closed or self._app_ref is not None or type(app) is not McpPrivateContextConfirmationApp:
                _fail("invalid_context")
            self._app_ref = weakref.ref(app, self._drop_app)
            self._app_method_pin = tuple((name, McpPrivateContextConfirmationApp.__dict__[name])
                                         for name in _APP_PIN_NAMES)
        self._assert_pins()

    def _drop_app(self, dead: weakref.ReferenceType[object]) -> None:
        with self._lock:
            if self._app_ref is not dead: return
            for entry in tuple(self._entries.values()): self._terminate(entry)
            self._permits.clear(); self._exchanges.clear()
            self._app_ref = None; self._app_method_pin = None

    def _registered_app_method(self, name: str):
        from src.mcp_private_context_confirmation_app import McpPrivateContextConfirmationApp
        self._assert_pins()
        app = self._app_ref() if self._app_ref is not None else None
        pinned = dict(self._app_method_pin or ())
        expected = pinned.get(name)
        if (type(app) is not McpPrivateContextConfirmationApp or self._app_ref() is not app
                or type(app).__dict__.get(name) is not expected or name in app.__dict__
                or app._service is not self or app._auth is not self._auth
                or app._session_factory is not self._session_factory): _fail("invalid_binding")
        return expected.__func__ if type(expected) is staticmethod else expected

    def _assert_request_pins(self, request_id: object) -> None:
        if type(request_id) is not str: _fail("invalid_request")
        try:
            self._assert_pins()
        except BaseException:
            with self._lock:
                entry = self._entries.get(request_id)
                if entry is not None: self._terminate(entry)
            raise

    def _registered_request_app_method(self, name: str, request_id: object):
        if type(request_id) is not str: _fail("invalid_request")
        try:
            return self._registered_app_method(name)
        except BaseException:
            with self._lock:
                entry = self._entries.get(request_id)
                if entry is not None: self._terminate(entry)
            raise

    def _check_pending_disclosure_before_commit(self, entry: _Entry, operation: _PendingAckOperation,
                                               observed: tuple[datetime, float]) -> None:
        self._assert_pins()
        now, mono = observed
        with self._lock:
            if (self._closed or self._entries.get(entry.request_id) is not entry
                    or entry.state != "disclosing" or entry.pending_ack_operation is not operation
                    or entry.generation != operation.generation or entry.exchange_nonce != operation.nonce
                    or entry.disclosure_generation != operation.generation
                    or entry.disclosure_nonce != operation.nonce
                    or entry.disclosure_operation_id != operation.operation_id
                    or now >= entry.deadline or mono >= entry.mono_deadline
                    or entry.challenge_deadline is None or now >= entry.challenge_deadline
                    or entry.challenge_mono_deadline is None or mono >= entry.challenge_mono_deadline):
                _fail("revoked")

    def _assert_pins(self) -> None:
        CookieConfirmationAuthority, _ = _app_types()
        if (type(self._auth) is not AuthManager or self._auth is not self._auth_pin
                or type(self._authority) is not McpContextAuthority or self._authority is not self._authority_pin
                or type(self._producer) is not CombinedContextManifestProducer or self._producer is not self._producer_pin
                or type(self._cookie_authority) is not CookieConfirmationAuthority
                or self._cookie_authority is not self._cookie_authority_pin
                or self._session_factory is not self._session_factory_pin
                or self._authority._auth_manager is not self._auth_pin
                or self._producer._auth_manager is not self._auth_pin
                or self._producer._mcp_authority is not self._authority_pin
                or type(self._producer._planning).__dict__.get("read_document") is not self._planning_reader_pin
                or self._cookie_authority._auth_manager is not self._auth_pin
                or self._authority._session_factory is not self._session_factory_pin
                or self._authority._session_factory is not self._authority._session_factory_pin):
            _fail("invalid_binding")
        try:
            self._producer._assert_mcp_provider_pins()
        except McpContextAuthorityError:
            raise
        except Exception:
            _fail("invalid_binding")
        app = self._app_ref() if self._app_ref is not None else None
        if (app is None) != (self._app_method_pin is None): _fail("invalid_binding")
        if app is not None:
            from src.mcp_private_context_confirmation_app import McpPrivateContextConfirmationApp
            pinned = dict(self._app_method_pin or ())
            if (type(app) is not McpPrivateContextConfirmationApp
                    or tuple(pinned) != _APP_PIN_NAMES
                    or any(type(app).__dict__.get(name) is not pinned[name] or name in app.__dict__
                           for name in _APP_PIN_NAMES)
                    or app._service is not self or app._auth is not self._auth
                    or app._session_factory is not self._session_factory):
                _fail("invalid_binding")

    def _reject_authority_reentry(self) -> None:
        if getattr(self._execution_local, "active", False):
            self._execution_local.violation = True
            _fail("permit_invalid")

    @staticmethod
    def _recipient(binding: object) -> tuple[object, ...]:
        return (binding.owner_subject_id, binding.chat_session_id, binding.token_id,
                binding.scope, binding.cache_generation, binding.security_action_revision,
                binding.process_epoch)

    @contextmanager
    def _hold_human_exchange(self, request_id: object, challenge_token: object, chat_session_id: object,
                             app: object, lease: McpContextReadLease, cookie: str, raw_user_command: str):
        self._reject_authority_reentry()
        if any(type(value) is not str or not value for value in (request_id, challenge_token, chat_session_id)): _fail("invalid_request")
        self._assert_request_pins(request_id)
        entry = None; exchange = None; exchange_ref = None; key = None; pending = None
        try:
            with self._guards(lease, cookie) as binding:
                now, mono = self._clock()
                self._assert_pins()
                with self._lock:
                    entry = self._entry(request_id, now, mono, "challenged")
                    if (type(entry.cookie) is not str or not entry.cookie
                            or type(cookie) is not str or not hmac.compare_digest(entry.cookie, cookie)):
                        _fail("invalid_binding")
                    if (self._app_ref is None or self._app_ref() is not app or entry.lease_ref() is not lease
                            or entry.challenge is None or not hmac.compare_digest(entry.challenge, challenge_token)
                            or entry.binding_ref() is not binding or binding.chat_session_id != chat_session_id): _fail("invalid_binding")
                    exchange = HumanExchange(); key = id(exchange)
                    entry.exchange_nonce = secrets.randbits(63); entry.generation += 1; entry.state = "human_reserved"
                    entry.disclosure_generation = entry.generation; entry.disclosure_nonce = entry.exchange_nonce
                    entry.disclosure_operation_id = secrets.token_urlsafe(32)
                    entry.pending_ack_operation = _PendingAckOperation(entry.generation, entry.exchange_nonce, entry.disclosure_operation_id)
                    exchange_ref = weakref.ref(exchange, lambda dead, k=key: self._drop_exchange(k, dead))
                    self._exchanges[key] = _HumanExchangeRow(
                        exchange_ref, weakref.ref(app), weakref.ref(self), weakref.ref(self._authority),
                        weakref.ref(lease), weakref.ref(binding), weakref.ref(threading.current_thread()),
                        request_id, challenge_token, chat_session_id, entry.generation, entry.exchange_nonce,
                        raw_user_command, _canon(entry.metadata_binding.canonical()), threading.get_ident(), os.getpid())
                try:
                    yield exchange
                finally:
                    self._drop_exchange(key, exchange_ref)
                    with self._lock:
                        if self._entries.get(request_id) is entry and entry.state == "human_reserved":
                            self._terminate(entry)
                        elif self._entries.get(request_id) is entry and entry.state == "pending_ack":
                            pending = entry.pending_ack_operation
            if pending is not None:
                final_clock = self._clock()
                self._assert_pins()
                with self._lock:
                    now, mono = final_clock
                    if (self._closed or self._entries.get(request_id) is not entry
                            or entry.state != "pending_ack" or entry.pending_ack_operation is not pending
                            or entry.generation != pending.generation or entry.disclosure_generation != pending.generation
                            or entry.disclosure_nonce != pending.nonce or entry.exchange_nonce != pending.nonce
                            or entry.disclosure_operation_id != pending.operation_id
                            or entry.ack_deadline is None or entry.ack_mono_deadline is None
                            or now >= entry.ack_deadline or mono >= entry.ack_mono_deadline
                            or not self._valid_attestation(entry)):
                        if self._entries.get(request_id) is entry: self._terminate(entry)
                        _fail("revoked")
                    entry.state = "disclosed"; entry.generation += 1
        except BaseException:
            with self._lock:
                if entry is not None and self._entries.get(entry.request_id) is entry:
                    self._terminate(entry)
            raise

    def _drop_exchange(self, key: int, dead: weakref.ReferenceType[HumanExchange]) -> None:
        with self._lock:
            row = self._exchanges.get(key)
            if type(row) is _HumanExchangeRow and row.exchange_ref is dead:
                self._exchanges.pop(key, None)
                entry = self._entries.get(row.request_id)
                if entry is not None and entry.state == "human_reserved" and entry.exchange_nonce == row.nonce:
                    self._terminate(entry)
    def _drop_permit(self, key: int, dead_ref: weakref.ReferenceType[Permit]) -> None:
        with self._lock:
            row = self._permits.get(key)
            if row is not None and row.permit_ref is dead_ref:
                self._permits.pop(key, None)
                entry = row.entry_ref()
                if entry is not None and entry.permit_ref is dead_ref:
                    self._terminate(entry)
    def _scrub(self, entry: _Entry) -> None:
        request_id = entry.request_id
        for key, row in tuple(self._permits.items()):
            if row.entry_ref() is entry: self._permits.pop(key, None)
        for key, row in tuple(self._exchanges.items()):
            if type(row) is _HumanExchangeRow and row.request_id == request_id: self._exchanges.pop(key, None)
        for key, row in tuple(self._completions.items()):
            if row.entry_ref() is entry: self._completions.pop(key, None)
        entry.cookie = ""; entry.page_bytes = 0; entry.page_request_sha256 = None; entry.intent = False
        entry.selection = None; entry.cookie_snapshot = None; entry.mcp_snapshot = None
        entry.metadata_binding = None; entry.receipt = None; entry.challenge = None
        entry.challenge_deadline = None; entry.challenge_mono_deadline = None
        entry.permit_ref = None; entry.permit_nonce = None; entry.exchange_nonce = None
        entry.permit_deadline = None; entry.permit_mono_deadline = None
        entry.execution_nonce = None; entry.execution_thread_ref = None
        entry.execution_thread_ident = None; entry.execution_pid = None
        entry.ack_wall_time = None; entry.ack_mono_time = None
        entry.ack_deadline = None; entry.ack_mono_deadline = None
        entry.ack_descriptor = None; entry.ack_attestation = None; entry.ack_role_hmacs = None; entry.exchange_nonce = None
        entry.disclosure_generation = None; entry.disclosure_nonce = None; entry.disclosure_operation_id = None
        entry.pending_ack_operation = None; entry.permit_operation_nonce = None
        entry.lease_ref = None; entry.binding_ref = None
        entry.state = "expired"; entry.generation += 1

    def _terminate(self, entry: _Entry) -> None:
        self._scrub(entry)
        if self._entries.get(entry.request_id) is entry: self._entries.pop(entry.request_id, None)

    def _revalidate(self, entry: _Entry, lease: McpContextReadLease) -> None:
        now, mono = self._clock()
        if (now >= entry.deadline or mono >= entry.mono_deadline or entry.selection is None
                or entry.cookie_snapshot is None or entry.mcp_snapshot is None): _fail("expired")
        if entry.permit_deadline is not None and (now >= entry.permit_deadline or mono >= (entry.permit_mono_deadline or float("inf"))): _fail("expired")
        with self._guards(lease, entry.cookie) as binding:
            if entry.binding_ref() is not binding: _fail("invalid_binding")
            cookie_first = self._capture_cookie(binding, entry.selection)
            cookie_last = self._capture_cookie(binding, entry.selection)
            mcp_first = self._capture_mcp(lease, entry.selection)
            mcp_last = self._capture_mcp(lease, entry.selection)
            if (cookie_first != cookie_last or mcp_first != mcp_last
                    or cookie_first != entry.cookie_snapshot or mcp_first != entry.mcp_snapshot): _fail("context_stale")
            if (not callable(getattr(entry.metadata_binding, "canonical", None))
                    or not callable(getattr(entry.mcp_snapshot.mcp_metadata_binding, "canonical", None))
                    or entry.mcp_snapshot.mcp_metadata_binding.canonical() != entry.metadata_binding.canonical()): _fail("context_stale")
        if entry.receipt is None: _fail("readback_failed")
        app = self._app_ref() if self._app_ref is not None else None
        if app is None: _fail("readback_failed")
        self._registered_app_method("_readback_ack")(app, entry)

    def _fresh_pair(self, entry: _Entry, lease: McpContextReadLease) -> None:
        selection = entry.selection
        if selection is None: _fail("context_stale")
        with self._guards(lease, entry.cookie) as binding:
            if entry.binding_ref() is not binding: _fail("invalid_binding")
            c1 = self._capture_cookie(binding, selection); c2 = self._capture_cookie(binding, selection)
            m1 = self._capture_mcp(lease, selection); m2 = self._capture_mcp(lease, selection)
            if c1 != c2 or m1 != m2 or c1 != entry.cookie_snapshot or m1 != entry.mcp_snapshot:
                _fail("context_stale")
            self._pair(c1, m1, selection)
        self._assert_pins()

    def _seal_ack_attestation(self, entry: _Entry, operation: _PendingAckOperation,
                              before: tuple[datetime, float], returned: tuple[datetime, float]) -> None:
        bw, bm = before; rw, rm = returned
        if rw < bw or rm < bm or type(entry.ack_descriptor) is not bytes or entry.receipt is None: _fail("readback_failed")
        if entry.pending_ack_operation is not operation: _fail("revoked")
        self._check_pending_disclosure_before_commit(entry, operation, returned)
        wall_deadline = min(entry.deadline, bw + timedelta(seconds=300))
        mono_deadline = min(entry.mono_deadline, bm + 300.0)
        if wall_deadline <= rw or mono_deadline <= rm: _fail("expired")
        body = {"schema": "mcp_private_context_ack_attestation.v1", "audience": "authenticated_mcp_context_ack_commit", "descriptor_sha256": hashlib.sha256(entry.ack_descriptor).hexdigest(), "role_hmacs": dict(entry.ack_role_hmacs), "request_id": entry.request_id, "challenge": entry.challenge, "generation": entry.disclosure_generation, "exchange_nonce": entry.disclosure_nonce, "operation_id": operation.operation_id, "recipient": list(self._recipient(entry.binding_ref())), "recipient9": entry.metadata_binding.canonical(), "ids": [entry.receipt.user_message_id, entry.receipt.acknowledgement_message_id], "commit_before_wall": _now_text(bw), "commit_before_mono": bm, "commit_return_wall": _now_text(rw), "commit_return_mono": rm, "wall_deadline": _now_text(wall_deadline), "mono_deadline": mono_deadline}
        signature = hmac.new(self._attestation_key, b"mcp-private-context-ack-attestation.v1\x00" + _canon(body), hashlib.sha256).hexdigest()
        entry.ack_wall_time = rw; entry.ack_mono_time = rm
        entry.ack_deadline = wall_deadline; entry.ack_mono_deadline = mono_deadline
        entry.ack_attestation = {"body": body, "hmac": signature}

    def _valid_attestation(self, entry: _Entry) -> bool:
        attestation = entry.ack_attestation
        if type(attestation) is not dict or set(attestation) != {"body", "hmac"}: return False
        try:
            expected = hmac.new(self._attestation_key, b"mcp-private-context-ack-attestation.v1\x00" + _canon(attestation["body"]), hashlib.sha256).hexdigest()
            body = attestation["body"]
            return (hmac.compare_digest(expected, attestation["hmac"])
                    and body["schema"] == "mcp_private_context_ack_attestation.v1"
                    and body["audience"] == "authenticated_mcp_context_ack_commit"
                    and body["descriptor_sha256"] == hashlib.sha256(entry.ack_descriptor).hexdigest()
                    and body["request_id"] == entry.request_id and body["challenge"] == entry.challenge
                    and body["generation"] == entry.disclosure_generation
                    and body["exchange_nonce"] == entry.disclosure_nonce
                    and entry.pending_ack_operation is not None and body["operation_id"] == entry.pending_ack_operation.operation_id
                    and body["role_hmacs"] == entry.ack_role_hmacs
                    and body["recipient"] == list(self._recipient(entry.binding_ref()))
                    and body["recipient9"] == entry.metadata_binding.canonical()
                    and body["ids"] == [entry.receipt.user_message_id, entry.receipt.acknowledgement_message_id]
                    and body["wall_deadline"] == _now_text(entry.ack_deadline)
                    and body["mono_deadline"] == entry.ack_mono_deadline
                    and body["commit_return_wall"] == _now_text(entry.ack_wall_time)
                    and body["commit_return_mono"] == entry.ack_mono_time
                    and entry.ack_deadline == min(entry.deadline, datetime.fromisoformat(body["commit_before_wall"].replace("Z", "+00:00")) + timedelta(seconds=300))
                    and entry.ack_mono_deadline == min(entry.mono_deadline, body["commit_before_mono"] + 300.0))
        except Exception: return False

    @contextmanager
    def _guards(self, lease: McpContextReadLease, cookie: str):
        original = self._authority.recheck_lease(lease)
        if lease.binding is not original: _fail("invalid_binding")
        with self._authority.live_lease(lease) as binding:
            if binding is not original: _fail("invalid_binding")
            with self._cookie_authority.hold(cookie, binding.chat_session_id) as cookie_binding:
                if cookie_binding.owner_sid != binding.owner_subject_id or cookie_binding.chat_session_id != binding.chat_session_id or cookie_binding.security_revision != binding.security_action_revision: _fail("invalid_binding")
                yield binding

    def _capture_cookie(self, binding: object, selection: object) -> CombinedContextSnapshot:
        cookie_binding = self._cookie_authority.current_binding()
        self._cookie_authority.recheck(cookie_binding)
        if (cookie_binding.owner_sid != binding.owner_subject_id
                or cookie_binding.chat_session_id != binding.chat_session_id
                or cookie_binding.security_revision != binding.security_action_revision): _fail("invalid_binding")
        value = self._producer.capture_snapshot(cookie_binding, selection)
        if type(value) is not CombinedContextSnapshot or value.mcp_metadata_binding is not None: _fail("context_stale")
        return value
    def _capture_mcp(self, lease: McpContextReadLease, selection: object) -> CombinedContextSnapshot:
        value = self._producer.capture_mcp_snapshot(lease, selection)
        if type(value) is not CombinedContextSnapshot or value.mcp_metadata_binding is None: _fail("context_stale")
        foundation = self._authority.recheck_lease(lease); metadata = value.mcp_metadata_binding
        if (not callable(getattr(metadata, "canonical", None))
                or metadata.canonical() != {"schema": "odysseus.context.mcp_metadata_binding.v1", "audience": "authenticated_mcp_context_read", "owner_sid": foundation.owner_subject_id, "chat_session_id": foundation.chat_session_id, "recipient_token_id": foundation.token_id, "scope": foundation.scope, "cache_generation": foundation.cache_generation, "security_revision": foundation.security_action_revision, "app_epoch": foundation.process_epoch}): _fail("invalid_binding")
        return value
    @staticmethod
    def _pair(cookie: CombinedContextSnapshot, mcp: CombinedContextSnapshot, selection: object) -> None:
        if (len(cookie.items) != len(mcp.items) or len(cookie.items) != len(selection.items)
                or len(cookie.target_manifest_digests) != len(cookie.items)
                or len(mcp.target_manifest_digests) != len(mcp.items)):
            _fail("context_stale")
        key = lambda item: (item.source, item.selection_ref, item.revision_ref)
        selected_keys = tuple((item.source, item.selection_ref, item.revision_ref) for item in selection.items)
        if len(set(selected_keys)) != len(selected_keys): _fail("context_stale")
        left = {key(item): item for item in cookie.items}; right = {key(item): item for item in mcp.items}
        if (len(left) != len(cookie.items) or len(right) != len(mcp.items)
                or set(left) != set(selected_keys) or set(right) != set(selected_keys)):
            _fail("context_stale")
        for item_key in selected_keys:
            a, b = left[item_key], right[item_key]
            if (a.source, a.selection_ref, a.revision_ref, a.payload_sha256, a.classification, a.provenance_ref) != (b.source, b.selection_ref, b.revision_ref, b.payload_sha256, b.classification, b.provenance_ref): _fail("context_stale")
    def _envelope(self, entry: _Entry, role: str = "capture", content: str = "") -> str:
        item = lambda x: {"source": x.source, "selection_ref": x.selection_ref, "revision_ref": x.revision_ref, "boundary_sha256": x.boundary_sha256, "record_sha256": x.record_sha256, "payload_sha256": x.payload_sha256, "classification": x.classification, "provenance_ref": x.provenance_ref}
        metadata = entry.metadata_binding
        if metadata is None or not callable(getattr(metadata, "canonical", None)): _fail("context_stale")
        if role not in {"user", "assistant", "capture"} or type(content) is not str: _fail("invalid_request")
        payload = {"schema": _SCHEMA, "audience": _AUDIENCE, "request_id": entry.request_id, "role": role, "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "ack_wall_time": _now_text(entry.ack_wall_time) if entry.ack_wall_time is not None else None, "ack_monotonic": entry.ack_mono_time, "selection": [(x.source,x.selection_ref,x.revision_ref) for x in entry.selection.items], "page_request_sha256": entry.page_request_sha256, "page_bytes": entry.page_bytes, "cookie": {"items": [item(x) for x in entry.cookie_snapshot.items], "targets": list(entry.cookie_snapshot.target_manifest_digests)}, "mcp": {"items": [item(x) for x in entry.mcp_snapshot.items], "targets": list(entry.mcp_snapshot.target_manifest_digests), "metadata_binding": metadata.canonical()}}
        return "mpce1." + hmac.new(self._key, _DOMAIN + _canon(payload), hashlib.sha256).hexdigest()
    def _clock(self) -> tuple[datetime, float]:
        with self._lock:
            try:
                value, mono = self._wall(), self._mono()
                if (type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None
                        or type(mono) not in (int, float) or not math.isfinite(mono)):
                    raise ValueError
                value = value.astimezone(timezone.utc)
                wall_ts = value.timestamp(); mono = float(mono)
                if not math.isfinite(wall_ts) or not math.isfinite(mono): raise ValueError
            except Exception:
                for entry in tuple(self._entries.values()): self._terminate(entry)
                _fail("expired")
            if ((self._last_wall is not None and wall_ts < self._last_wall)
                    or (self._last_mono is not None and mono < self._last_mono)):
                for entry in tuple(self._entries.values()): self._terminate(entry)
                _fail("expired")
            self._last_wall = wall_ts; self._last_mono = mono
        return value, mono

    def _entry(self, request_id: object, now: datetime, mono: float, state: str) -> _Entry:
        if type(request_id) is not str: _fail("invalid_request")
        self._prune(now, mono); entry = self._entries.get(request_id)
        if entry is None: _fail("invalid_request")
        if entry.state in {"revoked", "closed"}: _fail("revoked")
        if entry.state != state: _fail("challenge_spent" if state == "challenged" else "permit_spent")
        return entry

    def _prune(self, now: datetime, mono: float) -> None:
        for entry in tuple(self._entries.values()):
            expired = (now >= entry.deadline or mono >= entry.mono_deadline
                       or entry.lease_ref() is None or entry.binding_ref() is None)
            if entry.state in {"challenged", "human_reserved", "disclosing", "pending_ack"} and entry.challenge_deadline is not None:
                expired = expired or now >= entry.challenge_deadline or mono >= (entry.challenge_mono_deadline or float("inf"))
            if entry.permit_deadline is not None:
                expired = expired or now >= entry.permit_deadline or mono >= (entry.permit_mono_deadline or float("inf"))
            if entry.state in {"disclosed", "permit_issuing", "permitted", "verifying"} and entry.ack_deadline is not None:
                expired = expired or now >= entry.ack_deadline or mono >= (entry.ack_mono_deadline or float("inf"))
            if expired: self._terminate(entry)


_TRUSTED_PLANNING_EXECUTOR = McpPrivateContextConfirmationService._execute_planning_page
_TRUSTED_COMPLETION_CONSUMER = McpPrivateContextConfirmationService._consume_planning_completion
