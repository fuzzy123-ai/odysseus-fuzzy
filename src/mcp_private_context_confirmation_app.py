"""Inactive SQLAlchemy composition for MCP private-context consent."""
from __future__ import annotations
from typing import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hmac
import re
import time
from fastapi import Request
from core.auth import AuthManager
from core.database import ChatMessage as DbChatMessage, Session as DbSession
from src.context_manifest_producer import CombinedContextManifestProducer
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationService, McpPrivateContextConfirmationError,
    Challenge, DisclosureReceipt, HumanExchange, _fail,
    _acquire_request_rejection_capability, _reject_request_binding_drift,
)
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    _acquire_native_lease_capability,
)


_MCP_CONTEXT_AUTHORITY_TYPE = McpContextAuthority
_MCP_CONTEXT_NATIVE_LEASE_ACQUIRER = _acquire_native_lease_capability
_MCP_REQUEST_REJECTION_FACTORY = _acquire_request_rejection_capability
_MCP_REQUEST_REJECTION_DISPATCH = _reject_request_binding_drift
_BRIDGE_OWNER_DRIFT_REASON = (McpPrivateContextConfirmationError, "invalid_context")


class _BridgeOwnerSlot:
    __slots__ = ("_bridge_owner_capsule", "__dict__", "__weakref__")

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_bridge_owner_capsule":
            raise AttributeError("MCP bridge owner is write-once")
        object.__setattr__(self, name, value)


_BRIDGE_OWNER_SLOT = _BridgeOwnerSlot.__dict__["_bridge_owner_capsule"]
_BRIDGE_APP_PIN_NAMES = (
    "issue_authenticated_challenge", "revoke_authenticated",
    "resolve_authenticated_challenge", "_assert_owned_chat",
    "hold_authenticated_human_exchange", "_cached_browser_submission",
    "_persist_ack", "_readback_ack", "_readback_ack_under_guard",
    "_ack_descriptor", "_sign_descriptor", "_reject_duplicate_keys", "_allocate_ids",
)


def _read_bridge_owner(value: object, _slot=_BRIDGE_OWNER_SLOT):
    return _slot.__get__(value, _BridgeOwnerSlot)


def _write_bridge_owner(value: object, owner: object, _slot=_BRIDGE_OWNER_SLOT) -> None:
    try:
        _slot.__get__(value, _BridgeOwnerSlot)
    except AttributeError:
        _slot.__set__(value, owner)
        return
    _fail("invalid_context")


@dataclass(frozen=True, slots=True, repr=False)
class _BridgeOwner:
    owner_slot: object = field(repr=False)
    service: McpPrivateContextConfirmationService = field(repr=False)
    authority: McpContextAuthority = field(repr=False)
    authority_type: type = field(repr=False)
    native_lease_acquirer: object = field(repr=False)
    auth: AuthManager = field(repr=False)
    session_factory: Callable[[], object] = field(repr=False)
    validator: object = field(repr=False)
    app_descriptors: tuple[tuple[str, object], ...] = field(repr=False)
    request_rejection_factory: object = field(repr=False)
    request_rejection_dispatch: object = field(repr=False)
    request_rejection_capability: tuple[object, ...] = field(repr=False)

    def __repr__(self) -> str:
        return "_BridgeOwner(<redacted>)"


def _validate_bridge_owner(value: object, owner: _BridgeOwner | None = None) -> _BridgeOwner:
    if owner is None:
        owner = _read_bridge_owner(value)
    service = owner.service if type(owner) is _BridgeOwner else None
    authority = getattr(service, "_authority", None)
    pinned = dict(owner.app_descriptors) if type(owner) is _BridgeOwner else {}
    if (
        type(value) is not McpPrivateContextConfirmationApp
        or type(owner) is not _BridgeOwner
        or authority is not owner.authority
        or type(authority) is not owner.authority_type
        or owner.authority_type is not _MCP_CONTEXT_AUTHORITY_TYPE
        or owner.native_lease_acquirer is not _MCP_CONTEXT_NATIVE_LEASE_ACQUIRER
        or _BridgeOwnerSlot.__dict__.get("_bridge_owner_capsule") is not owner.owner_slot
        or owner.owner_slot is not _BRIDGE_OWNER_SLOT
        or owner.validator is not _validate_bridge_owner
        or tuple(pinned) != _BRIDGE_APP_PIN_NAMES
        or any(
            type(value).__dict__.get(name) is not pinned[name] or name in value.__dict__
            for name in _BRIDGE_APP_PIN_NAMES
        )
        or value._service is not service
        or value._auth is not owner.auth
        or value._session_factory is not owner.session_factory
        or service._auth is not owner.auth
        or service._session_factory is not owner.session_factory
        or service._session_factory_pin is not owner.session_factory
        or getattr(authority, "_auth_manager", None) is not owner.auth
        or getattr(authority, "_session_factory", None) is not owner.session_factory
        or getattr(authority, "_session_factory_pin", None) is not owner.session_factory
    ):
        _fail("invalid_context")
    return owner


def _capture_bridge_owner(
    value: object,
    _reader=_read_bridge_owner,
    _validator=_validate_bridge_owner,
) -> _BridgeOwner:
    owner = _reader(value)
    if type(owner) is not _BridgeOwner or owner.validator is not _validator:
        _fail("invalid_context")
    return _validator(value, owner)


_BRIDGE_OWNER_REQUEST_FIELD_NAMES = (
    "owner_slot", "service", "authority", "authority_type",
    "native_lease_acquirer", "auth", "session_factory", "validator",
    "app_descriptors", "request_rejection_factory",
    "request_rejection_dispatch", "request_rejection_capability",
)
_BRIDGE_OWNER_REQUEST_SLOTS = tuple(
    _BridgeOwner.__dict__[name] for name in _BRIDGE_OWNER_REQUEST_FIELD_NAMES
)
_BRIDGE_OWNER_MEMBER_GET = type(_BridgeOwner.__dict__["service"]).__get__
_BRIDGE_OBJECT_GETATTRIBUTE = object.__getattribute__


def _request_bridge_preflight(
    value: object,
    request_id: object,
    _reader=_read_bridge_owner,
    _capture=_capture_bridge_owner,
    _member_get=_BRIDGE_OWNER_MEMBER_GET,
    _owner_slots=_BRIDGE_OWNER_REQUEST_SLOTS,
    _object_getattribute=_BRIDGE_OBJECT_GETATTRIBUTE,
    _factory=_MCP_REQUEST_REJECTION_FACTORY,
    _dispatch=_MCP_REQUEST_REJECTION_DISPATCH,
) -> _BridgeOwner:
    """Validate raw bridge/storage identity before rejecting a drifted request."""
    try:
        if type(value) is not McpPrivateContextConfirmationApp:
            raise McpPrivateContextConfirmationError("invalid_context")
        owner = _reader(value)
        if type(owner) is not _BridgeOwner:
            raise McpPrivateContextConfirmationError("invalid_context")
        (owner_slot, service, authority, authority_type, native_lease_acquirer,
         auth, session_factory, validator, app_descriptors,
         request_rejection_factory, request_rejection_dispatch,
         capability) = tuple(
            _member_get(slot, owner, _BridgeOwner) for slot in _owner_slots
        )
        if (owner_slot is not _BRIDGE_OWNER_SLOT
                or _BridgeOwnerSlot.__dict__.get("_bridge_owner_capsule") is not owner_slot
                or type(service) is not McpPrivateContextConfirmationService
                or type(authority) is not authority_type
                or authority_type is not _MCP_CONTEXT_AUTHORITY_TYPE
                or native_lease_acquirer is not _MCP_CONTEXT_NATIVE_LEASE_ACQUIRER
                or validator is not _validate_bridge_owner
                or request_rejection_factory is not _factory
                or request_rejection_dispatch is not _dispatch
                or type(auth) is not AuthManager
                or not callable(session_factory)
                or type(capability) is not tuple or len(capability) != 11):
            raise McpPrivateContextConfirmationError("invalid_context")
        (cap_service, _capsule, cap_lock, cap_entries, cap_permits,
         cap_exchanges, cap_completions, cap_native_completions,
         cap_authority, cap_execution_local, _cap_binding_issuer) = capability
        app_state = _object_getattribute(value, "__dict__")
        service_state = _object_getattribute(service, "__dict__")
        authority_state = _object_getattribute(authority, "__dict__")
        if (type(app_state) is not dict or type(service_state) is not dict
                or type(authority_state) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(app_state)))
                or any(type(key) is not str for key in tuple(dict.keys(service_state)))
                or any(type(key) is not str for key in tuple(dict.keys(authority_state)))
                or cap_service is not service or cap_authority is not authority
                or dict.get(service_state, "_native_rejection_capsule") is not _capsule
                or dict.get(service_state, "_lock") is not cap_lock
                or dict.get(service_state, "_entries") is not cap_entries
                or dict.get(service_state, "_permits") is not cap_permits
                or dict.get(service_state, "_exchanges") is not cap_exchanges
                or dict.get(service_state, "_completions") is not cap_completions
                or dict.get(service_state, "_native_completions") is not cap_native_completions
                or dict.get(service_state, "_execution_local") is not cap_execution_local):
            raise McpPrivateContextConfirmationError("invalid_context")
        if (type(app_descriptors) is not tuple
                or any(type(item) is not tuple or len(item) != 2
                       or type(item[0]) is not str for item in app_descriptors)):
            raise McpPrivateContextConfirmationError("invalid_context")
        pinned = dict(app_descriptors)
        app_class_state = McpPrivateContextConfirmationApp.__dict__
        app_binding_valid = (
            tuple(pinned) == _BRIDGE_APP_PIN_NAMES
            and app_class_state.get("__getattribute__") is None
            and app_class_state.get("__getattr__") is None
            and all(app_class_state.get(name) is pinned[name] and name not in app_state
                    for name in _BRIDGE_APP_PIN_NAMES)
            and dict.get(app_state, "_service") is service
            and dict.get(app_state, "_auth") is auth
            and dict.get(app_state, "_session_factory") is session_factory
            and dict.get(service_state, "_auth") is auth
            and dict.get(service_state, "_session_factory") is session_factory
            and dict.get(service_state, "_session_factory_pin") is session_factory
            and dict.get(service_state, "_authority") is authority
            and dict.get(authority_state, "_auth_manager") is auth
            and dict.get(authority_state, "_session_factory") is session_factory
            and dict.get(authority_state, "_session_factory_pin") is session_factory
        )
    except McpPrivateContextConfirmationError:
        raise
    except BaseException:
        raise McpPrivateContextConfirmationError("invalid_context") from None
    _dispatch(capability, request_id, app_binding_valid)
    return _capture(value)


class McpPrivateContextConfirmationApp(_BridgeOwnerSlot):
    """Inactive browser witness; no route or dispatcher is registered here."""
    def __init__(self, *, service: McpPrivateContextConfirmationService,
                 auth_manager: AuthManager, session_factory: Callable[[], object]) -> None:
        authority = getattr(service, "_authority", None)
        if (type(service) is not McpPrivateContextConfirmationService or type(auth_manager) is not AuthManager
                or not callable(session_factory) or service._auth is not auth_manager
                or service._session_factory is not session_factory
                or service._session_factory_pin is not session_factory
                or authority is None
                or type(authority) is not _MCP_CONTEXT_AUTHORITY_TYPE
                or authority._auth_manager is not auth_manager
                or authority._session_factory is not session_factory
                or authority._session_factory_pin is not session_factory
                or service._authority._session_factory_pin is not session_factory): _fail("invalid_context")
        request_rejection_capability = _MCP_REQUEST_REJECTION_FACTORY(service)
        owner = _BridgeOwner(
            _BRIDGE_OWNER_SLOT, service, authority, _MCP_CONTEXT_AUTHORITY_TYPE,
            _MCP_CONTEXT_NATIVE_LEASE_ACQUIRER, auth_manager, session_factory, _validate_bridge_owner,
            tuple((name, type(self).__dict__[name]) for name in _BRIDGE_APP_PIN_NAMES),
            _MCP_REQUEST_REJECTION_FACTORY, _MCP_REQUEST_REJECTION_DISPATCH,
            request_rejection_capability,
        )
        _write_bridge_owner(self, owner)
        self._service, self._auth, self._session_factory = service, auth_manager, session_factory
        owner.validator(self, owner)
        service._register_app(self)

    def issue_authenticated_challenge(
        self, request: Request, request_id: str, _capture_owner=_capture_bridge_owner,
        _request_preflight=_request_bridge_preflight,
    ) -> Challenge:
        """Attach a cookie-free MCP intent to one real same-origin browser request."""
        owner = _request_preflight(self, request_id)
        service = owner.service
        service._reject_authority_reentry()
        service._registered_request_app_method("issue_authenticated_challenge", request_id)
        import json
        from routes.auth_routes import SESSION_COOKIE
        if (type(self) is not McpPrivateContextConfirmationApp
                or "issue_authenticated_challenge" in self.__dict__
                or type(request) is not Request or type(request_id) is not str or not request_id):
            _fail("invalid_request")
        headers = request.headers
        blocked = ("authorization", "cf-connecting-ip", "cf-ray", "cf-visitor", "x-forwarded-for",
                   "x-forwarded-host", "x-real-ip", "forwarded", "x-forwarded-proto",
                   "x-api-key", "x-api-token", "x-auth-token", "x-tool-token", "x-internal-token",
                   "x-odysseus-internal-token", "x-odysseus-owner")
        try: request_auth_manager = request.app.state.auth_manager
        except Exception: _fail("invalid_binding")
        if (request_auth_manager is not owner.auth or request.method != "POST"
                or request.url.path != "/api/context/mcp/challenge" or request.scope.get("type") != "http"
                or request.url.query or any(headers.getlist(name) for name in blocked)
                or getattr(request.state, "api_token", False)
                or len(headers.getlist("sec-fetch-site")) != 1 or len(headers.getlist("sec-fetch-mode")) != 1
                or headers.get("sec-fetch-site", "").lower() != "same-origin"
                or headers.get("sec-fetch-mode", "").lower() != "cors"):
            _fail("invalid_binding")
        origins = headers.getlist("origin")
        if len(origins) != 1 or origins[0].rstrip("/") != f"{request.url.scheme}://{request.url.netloc}".rstrip("/"):
            _fail("invalid_binding")
        cookies = headers.getlist("cookie")
        if len(cookies) != 1:
            _fail("invalid_binding")
        names = [piece.split("=", 1)[0].strip() for piece in cookies[0].split(";") if "=" in piece]
        if names.count(SESSION_COOKIE) != 1:
            _fail("invalid_binding")
        cookie = request.cookies.get(SESSION_COOKIE)
        if type(cookie) is not str or not cookie:
            _fail("invalid_binding")
        if len(headers.getlist("content-type")) != 1 or headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            _fail("invalid_binding")
        if getattr(request, "_form", None) is not None or type(getattr(request, "_body", None)) is not bytes:
            _fail("invalid_binding")
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result: raise ValueError
                    result[key] = value
                return result
            body = json.loads(request._body, object_pairs_hook=unique)
        except Exception:
            _fail("invalid_binding")
        if type(body) is not dict or set(body) != {"request_id"} or body.get("request_id") != request_id:
            _fail("invalid_binding")
        _capture_owner(self)
        return service._attach_authenticated_challenge(request_id, cookie)

    def revoke_authenticated(
        self, request: Request, request_id: str, _capture_owner=_capture_bridge_owner,
    ) -> None:
        """Revoke one exact browser-owned consent entry, leaving its MCP lease intact."""
        owner = _capture_owner(self)
        service = owner.service
        service._reject_authority_reentry()
        import json
        from routes.auth_routes import SESSION_COOKIE
        if (type(self) is not McpPrivateContextConfirmationApp
                or type(request) is not Request or type(request_id) is not str or not request_id):
            _fail("invalid_request")
        headers = request.headers
        blocked = ("authorization", "cf-connecting-ip", "cf-ray", "cf-visitor", "x-forwarded-for",
                   "x-forwarded-host", "x-real-ip", "forwarded", "x-forwarded-proto",
                   "x-api-key", "x-api-token", "x-auth-token", "x-tool-token", "x-internal-token",
                   "x-odysseus-internal-token", "x-odysseus-owner")
        try: request_auth_manager = request.app.state.auth_manager
        except Exception: _fail("invalid_binding")
        if (request_auth_manager is not owner.auth or request.method != "POST"
                or request.url.path != "/api/context/mcp/revoke" or request.scope.get("type") != "http"
                or request.url.query or any(headers.getlist(name) for name in blocked)
                or getattr(request.state, "api_token", False)
                or len(headers.getlist("sec-fetch-site")) != 1 or len(headers.getlist("sec-fetch-mode")) != 1
                or headers.get("sec-fetch-site", "").lower() != "same-origin"
                or headers.get("sec-fetch-mode", "").lower() != "cors"):
            _fail("invalid_binding")
        origins = headers.getlist("origin")
        cookies = headers.getlist("cookie")
        if (len(origins) != 1
                or origins[0].rstrip("/") != f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
                or len(cookies) != 1):
            _fail("invalid_binding")
        names = [piece.split("=", 1)[0].strip() for piece in cookies[0].split(";") if "=" in piece]
        cookie = request.cookies.get(SESSION_COOKIE)
        if names.count(SESSION_COOKIE) != 1 or type(cookie) is not str or not cookie:
            _fail("invalid_binding")
        if (len(headers.getlist("content-type")) != 1
                or headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                or getattr(request, "_form", None) is not None
                or type(getattr(request, "_body", None)) is not bytes):
            _fail("invalid_binding")
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result: raise ValueError
                    result[key] = value
                return result
            body = json.loads(request._body, object_pairs_hook=unique)
        except Exception:
            _fail("invalid_binding")
        if type(body) is not dict or set(body) != {"request_id"} or body.get("request_id") != request_id:
            _fail("invalid_binding")
        with service._lock:
            entry = service._entries.get(request_id)
            binding = entry.binding_ref() if entry is not None else None
            expected_cookie = entry.cookie if entry is not None else None
        if binding is None or expected_cookie != cookie:
            _fail("invalid_binding")
        with service._cookie_authority.hold(cookie, binding.chat_session_id) as current:
            if (current.owner_sid != entry.owner_sid or current.owner_sid != binding.owner_subject_id
                    or current.chat_session_id != binding.chat_session_id):
                _fail("invalid_binding")
            dict(owner.app_descriptors)["_assert_owned_chat"](self, current)
            with service._lock:
                if (service._entries.get(request_id) is not entry
                        or entry.binding_ref() is not binding or entry.cookie != cookie):
                    _fail("invalid_binding")
            _capture_owner(self)
            service.revoke(request_id)

    def resolve_authenticated_challenge(
        self,
        challenge_token: str,
        owner_sid: str,
        chat_session_id: str,
        _capture_owner=_capture_bridge_owner,
    ) -> str:
        """Resolve a challenged entry without creating a parallel authority registry."""
        owner = _capture_owner(self)
        service = owner.service
        service._reject_authority_reentry()
        if (type(self) is not McpPrivateContextConfirmationApp
                or type(challenge_token) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge_token) is None
                or type(owner_sid) is not str or not owner_sid
                or type(chat_session_id) is not str or not chat_session_id):
            _fail("invalid_request")
        now, mono = service._clock()
        try:
            current_epoch = service._authority._state_value("_process_epoch")
        except Exception:
            _fail("invalid_binding")
        matches = []
        with service._lock:
            service._prune(now, mono)
            for entry in tuple(service._entries.values()):
                binding = entry.binding_ref()
                if (entry.state == "challenged" and type(entry.challenge) is str
                        and hmac.compare_digest(entry.challenge, challenge_token)
                        and entry.owner_sid == owner_sid and binding is not None
                        and binding.owner_subject_id == owner_sid
                        and binding.chat_session_id == chat_session_id
                        and binding.process_epoch == current_epoch
                        and entry.challenge_deadline is not None
                        and entry.challenge_mono_deadline is not None
                        and now < entry.challenge_deadline and mono < entry.challenge_mono_deadline):
                    matches.append(entry.request_id)
        if len(matches) != 1:
            _fail("invalid_binding")
        _capture_owner(self)
        return matches[0]

    def _assert_owned_chat(self, binding, _capture_owner=_capture_bridge_owner):
        capsule = _capture_owner(self)
        service = capsule.service
        session_factory = capsule.session_factory
        service._cookie_authority.recheck(binding)
        owner = service._cookie_authority.require_current(binding)
        _capture_owner(self)
        db = session_factory()
        try:
            parent = db.query(DbSession).filter(DbSession.id == binding.chat_session_id).one_or_none()
            if parent is None or parent.owner != owner:
                _fail("invalid_binding")
        except McpPrivateContextConfirmationError:
            raise
        except Exception:
            _fail("invalid_binding")
        finally:
            try: db.close()
            except BaseException: _fail("invalid_binding")
        service._cookie_authority.recheck(binding)
        _capture_owner(self)

    @contextmanager
    def hold_authenticated_human_exchange(self, request: Request, request_id: str,
                                          challenge_token: str, *, raw_user_command: str,
                                          _capture_owner=_capture_bridge_owner,
                                          _request_preflight=_request_bridge_preflight):
        owner = _request_preflight(self, request_id)
        service = owner.service
        service._reject_authority_reentry()
        service._registered_request_app_method("hold_authenticated_human_exchange", request_id)
        from routes.auth_routes import SESSION_COOKIE
        if (type(self) is not McpPrivateContextConfirmationApp
                or type(request) is not Request or type(request_id) is not str or type(challenge_token) is not str
                or type(raw_user_command) is not str or raw_user_command != "/mcp-context-disclose " + challenge_token): _fail("invalid_request")
        headers = request.headers
        blocked = ("authorization", "cf-connecting-ip", "cf-ray", "cf-visitor", "x-forwarded-for",
                   "x-forwarded-host", "x-real-ip", "forwarded", "x-forwarded-proto",
                   "x-api-key", "x-api-token", "x-auth-token", "x-tool-token", "x-internal-token",
                   "x-odysseus-internal-token", "x-odysseus-owner")
        try: request_auth_manager = request.app.state.auth_manager
        except Exception: _fail("invalid_binding")
        if (request_auth_manager is not owner.auth or request.method != "POST"
                or request.url.path not in {"/api/chat", "/api/chat_stream"}
                or request.scope.get("type") != "http"
                or len(headers.getlist("sec-fetch-site")) != 1
                or len(headers.getlist("sec-fetch-mode")) != 1
                or request.headers.get("sec-fetch-site", "").lower() != "same-origin"
                or request.headers.get("sec-fetch-mode", "").lower() != "cors"
                or any(headers.getlist(name) for name in blocked)
                or getattr(request.state, "api_token", False)):
            _fail("invalid_binding")
        origins = headers.getlist("origin")
        if len(origins) != 1: _fail("invalid_binding")
        origin = origins[0]
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if not origin or origin.rstrip("/") != expected_origin.rstrip("/"): _fail("invalid_binding")
        cookies = headers.getlist("cookie")
        if len(cookies) != 1: _fail("invalid_binding")
        cookie_names = [piece.split("=", 1)[0].strip() for piece in cookies[0].split(";") if "=" in piece]
        if cookie_names.count(SESSION_COOKIE) != 1: _fail("invalid_binding")
        cookie = request.cookies.get(SESSION_COOKIE)
        if type(cookie) is not str or not cookie: _fail("invalid_binding")
        with service._lock:
            entry = service._entries.get(request_id); lease = entry.lease_ref() if entry is not None else None
        if lease is None: _fail("revoked")
        actual_message, actual_session, incognito = service._registered_request_app_method("_cached_browser_submission", request_id)(request)
        if (actual_message != raw_user_command or actual_session != lease.binding.chat_session_id
                or incognito): _fail("invalid_binding")
        _capture_owner(self)
        with service._hold_human_exchange(request_id, challenge_token, lease.binding.chat_session_id,
                                          self, lease, cookie, raw_user_command) as exchange:
            yield exchange

    @staticmethod
    def _cached_browser_submission(request):
        import json
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1: _fail("invalid_binding")
        content_type = content_types[0].split(";", 1)[0].strip().lower()
        cached_form = getattr(request, "_form", None)
        cached_body = getattr(request, "_body", None)
        if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
            from starlette.datastructures import FormData
            if type(cached_form) is not FormData or type(cached_body) is bytes:
                _fail("invalid_binding")
            try:
                names = [key for key, _value in cached_form.multi_items()]
                if len(names) != len(set(names)): _fail("invalid_binding")
                message = cached_form.get("message")
                session = cached_form.get("session")
                incognito = cached_form.get("incognito")
            except Exception: _fail("invalid_binding")
        elif content_type == "application/json":
            from starlette.datastructures import FormData
            if (type(cached_body) is not bytes or cached_form is not None
                    and (type(cached_form) is not FormData or len(cached_form) != 0)):
                _fail("invalid_binding")
            try:
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result: raise ValueError
                        result[key] = value
                    return result
                body = json.loads(cached_body, object_pairs_hook=unique)
            except Exception: _fail("invalid_binding")
            if type(body) is not dict: _fail("invalid_binding")
            message, session, incognito = body.get("message"), body.get("session"), body.get("incognito")
        else:
            _fail("invalid_binding")
        if type(message) is not str or type(session) is not str or not session: _fail("invalid_binding")
        if incognito is True or (type(incognito) is str and incognito.strip().lower() == "true"):
            _fail("invalid_binding")
        return message, session, False

    def _persist_ack(self, entry, lease, challenge: str, raw_user_command: str,
                     human_exchange: HumanExchange, operation,
                     _capture_owner=_capture_bridge_owner) -> DisclosureReceipt:
        owner = _capture_owner(self)
        service = owner.service
        session_factory = owner.session_factory
        authority = owner.authority
        native_capability = owner.native_lease_acquirer(authority, lease)
        validate_native_lease = native_capability.require_live
        cancel_native_lease = native_capability.cancel

        def guard_authority_interval() -> None:
            validate_native_lease()
            try:
                _capture_owner(self)
            except McpPrivateContextConfirmationError:
                try:
                    cancel_native_lease(_BRIDGE_OWNER_DRIFT_REASON)
                except Exception:
                    pass
                raise

        binding = service._cookie_authority.current_binding()
        guard_authority_interval()
        service._cookie_authority.recheck(binding)
        guard_authority_interval()
        authority.recheck_lease(lease)
        guard_authority_interval()
        if binding.chat_session_id != lease.binding.chat_session_id: _fail("invalid_binding")
        import json
        guard_authority_interval()
        db = session_factory()
        try:
            parent = db.query(DbSession).filter(DbSession.id == binding.chat_session_id).one_or_none()
            owner_alias = service._cookie_authority.require_current(binding)
            if parent is None or parent.owner != owner_alias: _fail("invalid_binding")
            left, right = service._registered_app_method("_allocate_ids")(db)
            receipt = DisclosureReceipt(entry.request_id, left, right)
            prior_count = db.query(DbChatMessage).filter(DbChatMessage.session_id == binding.chat_session_id).count()
            ack_content = "MCP private context disclosure confirmed."
            created_wall, _created_mono = service._clock()
            now = created_wall.replace(tzinfo=None)
            entry.receipt = receipt
            common = service._registered_app_method("_ack_descriptor")(
                self, entry, binding, (left, right), raw_user_command, ack_content, created_wall)
            common_bytes = json.dumps(common, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            entry.ack_descriptor = common_bytes
            sign_descriptor = service._registered_app_method("_sign_descriptor")
            envelope_user = sign_descriptor(self, common, "user", left, raw_user_command)
            envelope_ack = sign_descriptor(self, common, "assistant", right, ack_content)
            entry.ack_role_hmacs = {"user": envelope_user, "assistant": envelope_ack}
            db.add(DbChatMessage(id=str(left), session_id=binding.chat_session_id, role="user", content=raw_user_command, meta_data=json.dumps({"mcp_private_context_envelope": envelope_user}, separators=(",",":")), timestamp=now))
            db.add(DbChatMessage(id=str(right), session_id=binding.chat_session_id, role="assistant", content=ack_content, meta_data=json.dumps({"mcp_private_context_envelope": envelope_ack}, separators=(",",":")), timestamp=now))
            parent.message_count = prior_count + 2
            parent.last_accessed = parent.last_message_at = parent.updated_at = now
            guard_authority_interval()
            service._cookie_authority.recheck(binding)
            guard_authority_interval()
            authority.recheck_lease(lease)
            guard_authority_interval()
            service._assert_pins()
            guard_authority_interval()
            commit_before = service._clock()
            service._check_pending_disclosure_before_commit(entry, operation, commit_before)
            db.commit()
            commit_return = service._clock()
        except BaseException as error:
            try: db.rollback()
            except BaseException: pass
            if isinstance(error, (McpPrivateContextConfirmationError, McpContextAuthorityError)):
                raise
            _fail("persistence_failed")
        finally:
            try: db.close()
            except BaseException: _fail("persistence_failed")
        service._seal_ack_attestation(entry, operation, commit_before, commit_return)
        guard_authority_interval()
        service._cookie_authority.recheck(binding)
        guard_authority_interval()
        authority.recheck_lease(lease)
        guard_authority_interval()
        service._registered_app_method("_readback_ack_under_guard")(self, entry, binding)
        return receipt

    def _readback_ack(self, entry, _capture_owner=_capture_bridge_owner):
        owner = _capture_owner(self)
        service = owner.service
        lease = entry.lease_ref()
        if lease is None: _fail("readback_failed")
        with service._guards(lease, entry.cookie) as binding:
            _capture_owner(self)
            service._registered_app_method("_readback_ack_under_guard")(self, entry, binding)

    def _readback_ack_under_guard(self, entry, binding, _capture_owner=_capture_bridge_owner):
        owner = _capture_owner(self)
        service = owner.service
        session_factory = owner.session_factory
        import json, hmac
        receipt = entry.receipt
        try: descriptor = json.loads(entry.ack_descriptor) if type(entry.ack_descriptor) is bytes else None
        except Exception: descriptor = None
        if (type(receipt) is not DisclosureReceipt or receipt.request_id != entry.request_id
                or type(descriptor) is not dict or not service._valid_attestation(entry)):
            _fail("readback_failed")
        ids = (receipt.user_message_id, receipt.acknowledgement_message_id)
        if (len(set(ids)) != 2 or any(type(mid) is not int or not 1 <= mid <= (1 << 53) - 1 for mid in ids)
                or descriptor.get("ids") != list(ids)):
            _fail("readback_failed")
        try:
            created_wall = datetime.fromisoformat(descriptor["message_created_wall"].replace("Z", "+00:00"))
            rebuilt = service._registered_app_method("_ack_descriptor")(
                self, entry, binding, ids, descriptor["user_content"], descriptor["assistant_content"], created_wall)
            if json.dumps(rebuilt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") != entry.ack_descriptor:
                _fail("readback_failed")
        except (McpPrivateContextConfirmationError, McpContextAuthorityError): raise
        except BaseException: _fail("readback_failed")
        cookie_binding = service._cookie_authority.current_binding()
        alias = service._cookie_authority.require_current(cookie_binding)
        _capture_owner(self)
        db = session_factory()
        try:
            parent = db.query(DbSession).filter(DbSession.id == binding.chat_session_id).one_or_none()
            if parent is None or parent.owner != alias: _fail("readback_failed")
            rows = [db.query(DbChatMessage).filter(DbChatMessage.id == str(mid)).one_or_none() for mid in ids]
            if any(row is None for row in rows) or rows[0].id == rows[1].id: _fail("readback_failed")
            expected = (("user", descriptor["user_content"], ids[0]), ("assistant", descriptor["assistant_content"], ids[1]))
            for row, (role, content, message_id) in zip(rows, expected):
                if (row.session_id != binding.chat_session_id or row.role != role or row.content != content
                        or str(row.id) != str(message_id)):
                    _fail("readback_failed")
                reject_duplicates = service._registered_app_method("_reject_duplicate_keys")
                decoded = json.loads(row.meta_data, object_pairs_hook=reject_duplicates)
                if type(decoded) is not dict or set(decoded) != {"mcp_private_context_envelope"}: _fail("readback_failed")
                expected_envelope = service._registered_app_method("_sign_descriptor")(
                    self, descriptor, role, message_id, content)
                if not hmac.compare_digest(decoded["mcp_private_context_envelope"], expected_envelope): _fail("readback_failed")
                if row.timestamp is None or row.timestamp.replace(tzinfo=timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") != descriptor["message_created_wall"]:
                    _fail("readback_failed")
        except McpPrivateContextConfirmationError: raise
        except Exception: _fail("readback_failed")
        finally:
            try: db.close()
            except BaseException: _fail("readback_failed")
        service._cookie_authority.recheck(cookie_binding)
        service._authority.recheck_lease(entry.lease_ref())
        _capture_owner(self)

    def _ack_descriptor(
        self, entry, binding, ids, user_content, assistant_content, created_wall,
        _capture_owner=_capture_bridge_owner,
    ):
        owner = _capture_owner(self)
        service = owner.service
        import hashlib, hmac, json
        cookie_commitment = hmac.new(service._key, b"mcp-private-context-cookie-commitment.v1\x00" + entry.cookie.encode(), hashlib.sha256).hexdigest()
        item = lambda x: {"source": x.source, "selection_ref": x.selection_ref, "revision_ref": x.revision_ref, "boundary_sha256": x.boundary_sha256, "record_sha256": x.record_sha256, "payload_sha256": x.payload_sha256, "classification": x.classification, "provenance_ref": x.provenance_ref}
        lease = entry.lease_ref()
        if lease is None: _fail("readback_failed")
        recipient = list(service._recipient(lease.binding))
        cookie_binding = service._cookie_authority.current_binding()
        cookie_identity = {"owner_sid": cookie_binding.owner_sid, "chat_session_id": cookie_binding.chat_session_id,
                           "auth_session_binding": cookie_binding.auth_session_binding,
                           "security_revision": cookie_binding.security_revision}
        operation = entry.pending_ack_operation
        if operation is None or entry.disclosure_generation is None or entry.disclosure_nonce is None: _fail("readback_failed")
        return {"schema": "mcp_private_context_envelope.v2", "audience": "authenticated_mcp_context_ack", "request_id": entry.request_id, "challenge": entry.challenge, "generation": entry.disclosure_generation, "exchange_nonce": entry.disclosure_nonce, "operation_id": operation.operation_id, "recipient": recipient, "recipient9": entry.metadata_binding.canonical(), "current_binding_sha256": hashlib.sha256(json.dumps(recipient, separators=(",", ":")).encode()).hexdigest(), "cookie_binding_sha256": hashlib.sha256(json.dumps(cookie_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest(), "cookie_commitment": cookie_commitment, "ids": list(ids), "selection": [(x.source,x.selection_ref,x.revision_ref) for x in entry.selection.items], "page_request_sha256": entry.page_request_sha256, "page_bytes": entry.page_bytes, "cookie": {"items": [item(x) for x in entry.cookie_snapshot.items], "targets": list(entry.cookie_snapshot.target_manifest_digests)}, "mcp": {"items": [item(x) for x in entry.mcp_snapshot.items], "targets": list(entry.mcp_snapshot.target_manifest_digests), "metadata_binding": entry.metadata_binding.canonical()}, "message_created_wall": created_wall.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"), "user_content": user_content, "assistant_content": assistant_content}

    def _sign_descriptor(
        self, descriptor, role, message_id, content, _capture_owner=_capture_bridge_owner,
    ):
        owner = _capture_owner(self)
        import hashlib, hmac, json
        body = {"descriptor": descriptor, "role": role, "message_id": message_id, "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return "mpce2." + hmac.new(owner.service._key, b"mcp-private-context-envelope.v2\x00" + canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def _reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError
            result[key] = value
        return result

    @staticmethod
    def _allocate_ids(db):
        selected = []
        for _ in range(32):
            candidate = __import__("secrets").randbelow((1 << 53) - 1) + 1
            if candidate in selected: continue
            if db.query(DbChatMessage.id).filter(DbChatMessage.id == str(candidate)).one_or_none() is not None: continue
            selected.append(candidate)
            if len(selected) == 2: return tuple(selected)
        _fail("persistence_failed")
