"""Application-owned composition for authenticated MCP private context reads."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timezone
import json
import re
import secrets
import threading
import time
from typing import Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from core.auth import AuthManager
from src.context_manifest_producer import CombinedContextManifestProducer
from src.context_source_app import ContextSourceServices
from src.mcp_authenticated_dispatch import (
    MCP_CONTEXT_SCOPE,
    McpAuthenticatedIdentity,
    authenticate_mcp_request,
)
from src.mcp_context_authority import (
    McpContextAuthority,
    issue_mcp_bearer_proof,
)
from src.mcp_context_cursor_authority import McpContextCursorAuthority
from src.mcp_context_pack_coordinator import McpContextPackCoordinator
from src.mcp_context_request_runtime import McpContextRequestRuntime
from src.mcp_context_request_transport import McpContextRequestTransport
from src.mcp_context_causal_observation import record_app_graph_absent
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
)
from src.mcp_private_context_confirmation_app import McpPrivateContextConfirmationApp
from src.private_context_confirmation_app import CookieConfirmationAuthority


_EPOCH_RE = re.compile(r"epoch_[0-9a-f]{32}\Z")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_MAX_BROWSER_BODY = 4_096
_BROWSER_BODY_TIMEOUT_SECONDS = 10.0
_REQUEST_STREAM_DESCRIPTOR = Request.__dict__["stream"]
_BROWSER_MONOTONIC = time.monotonic
_BROWSER_WAIT_FOR = asyncio.wait_for
_BROWSER_SLEEP = asyncio.sleep


class _McpContextOwnerSlot:
    """Keep the construction authority outside the instance dictionary."""

    __slots__ = (
        "_mcp_context_owner_capsule", "_mcp_context_config_capsule",
        "__dict__", "__weakref__",
    )

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"_mcp_context_owner_capsule", "_mcp_context_config_capsule"}:
            raise AttributeError("MCP context owner is write-once")
        object.__setattr__(self, name, value)


_MCP_CONTEXT_OWNER_SLOT = _McpContextOwnerSlot.__dict__["_mcp_context_owner_capsule"]
_MCP_CONTEXT_CONFIG_SLOT = _McpContextOwnerSlot.__dict__["_mcp_context_config_capsule"]


def _read_mcp_context_owner(
    value: object,
    _slot=_MCP_CONTEXT_OWNER_SLOT,
):
    return _slot.__get__(value, _McpContextOwnerSlot)


def _write_mcp_context_owner(
    value: object,
    owner: object,
    _slot=_MCP_CONTEXT_OWNER_SLOT,
) -> None:
    try:
        _slot.__get__(value, _McpContextOwnerSlot)
    except AttributeError:
        _slot.__set__(value, owner)
        return
    raise ValueError("invalid MCP context composition")


def _read_mcp_context_config(value: object, _slot=_MCP_CONTEXT_CONFIG_SLOT):
    try:
        return _slot.__get__(value, _McpContextOwnerSlot)
    except AttributeError:
        return None


def _write_mcp_context_config(
    value: object,
    binding: object,
    _slot=_MCP_CONTEXT_CONFIG_SLOT,
) -> None:
    try:
        _slot.__get__(value, _McpContextOwnerSlot)
    except AttributeError:
        _slot.__set__(value, binding)
        return
    raise ValueError("MCP context configuration owner already bound")


@dataclass(frozen=True, slots=True, repr=False)
class _McpContextConfig:
    config_slot: object = field(repr=False)
    owner: object = field(repr=False)
    owner_type: type = field(repr=False)
    snapshot_descriptor: object = field(repr=False)
    binding_descriptor: object = field(repr=False)
    binding: tuple[object, ...] = field(repr=False)

    def __repr__(self) -> str:
        return "_McpContextConfig(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _McpContextOwner:
    owner_slot: object = field(repr=False)
    config_slot: object = field(repr=False)
    app: FastAPI = field(repr=False)
    auth: AuthManager = field(repr=False)
    session_factory: Callable[[], object] = field(repr=False)
    sources: ContextSourceServices | None = field(repr=False)
    source_producer: object = field(repr=False)
    confirmation: object = field(repr=False)
    confirmation_repository: object = field(repr=False)
    confirmation_authority: object = field(repr=False)
    providers: tuple[object, object, object] | None = field(repr=False)
    owned_types: tuple[tuple[str, type], ...] = field(repr=False)
    close_operations: tuple[tuple[str, type, object], ...] = field(repr=False)
    bridge_descriptors: tuple[object, object, object, object] = field(repr=False)
    service_disclose_descriptor: object = field(repr=False)
    auth_guard_descriptor: object = field(repr=False)
    authenticate_dispatch: object = field(repr=False)
    issue_bearer_proof: object = field(repr=False)
    identity_type: type = field(repr=False)
    sources_type: type = field(repr=False)
    context_scope: str = field(repr=False)
    graph_type: type = field(repr=False)
    raw_bearer_descriptor: object = field(repr=False)
    fixed_transport_descriptor: object = field(repr=False)
    disclose_browser_descriptor: object = field(repr=False)
    transport_handle_descriptor: object = field(repr=False)
    config_type: type = field(repr=False)

    def __repr__(self) -> str:
        return "_McpContextOwner(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _McpContextGraph:
    authority: McpContextAuthority = field(repr=False)
    producer: CombinedContextManifestProducer = field(repr=False)
    service: McpPrivateContextConfirmationService = field(repr=False)
    bridge: McpPrivateContextConfirmationApp = field(repr=False)
    cursor: McpContextCursorAuthority = field(repr=False)
    coordinator: McpContextPackCoordinator = field(repr=False)
    runtime: McpContextRequestRuntime = field(repr=False)
    transport: McpContextRequestTransport = field(repr=False)
    authenticate: object = field(repr=False)
    capture_prepare_proof: object = field(repr=False)

    def __repr__(self) -> str:
        return "_McpContextGraph(<redacted>)"


class McpContextApp(_McpContextOwnerSlot):
    """Own one replaceable native graph while borrowing existing app providers."""

    def __init__(
        self,
        app: FastAPI,
        *,
        auth_manager: AuthManager,
        session_factory: Callable[[], object],
        context_sources: ContextSourceServices | None,
    ) -> None:
        if (
            type(app) is not FastAPI
            or type(auth_manager) is not AuthManager
            or not callable(session_factory)
            or (context_sources is not None and type(context_sources) is not ContextSourceServices)
            or getattr(app.state, "auth_manager", None) is not auth_manager
        ):
            raise ValueError("invalid MCP context composition")
        source_producer = None
        confirmation = None
        confirmation_repository = None
        confirmation_authority = None
        providers = None
        if context_sources is not None:
            source_producer = context_sources.producer
            confirmation = context_sources.confirmation
            confirmation_repository = getattr(confirmation, "_repository", None)
            confirmation_authority = getattr(confirmation, "_authority", None)
            providers = (
                context_sources.forge,
                context_sources.planning,
                context_sources.knowledge,
            )
            if (
                getattr(confirmation_repository, "_session_factory", None)
                is not session_factory
                or getattr(confirmation_repository, "_authority", None)
                is not confirmation_authority
                or getattr(confirmation_authority, "_auth_manager", None)
                is not auth_manager
            ):
                raise ValueError("invalid MCP context composition")
        owned_types = (
            ("authority", McpContextAuthority),
            ("producer", CombinedContextManifestProducer),
            ("cookie_authority", CookieConfirmationAuthority),
            ("service", McpPrivateContextConfirmationService),
            ("bridge", McpPrivateContextConfirmationApp),
            ("cursor", McpContextCursorAuthority),
            ("coordinator", McpContextPackCoordinator),
            ("runtime", McpContextRequestRuntime),
            ("transport", McpContextRequestTransport),
        )
        owned_type_map = dict(owned_types)
        close_operations = tuple(
            (name, owned_type_map[name], owned_type_map[name].__dict__["close"])
            for name in ("transport", "runtime", "coordinator", "cursor", "service", "authority")
        )
        bridge_descriptors = (
            McpPrivateContextConfirmationApp.__dict__["issue_authenticated_challenge"],
            McpPrivateContextConfirmationApp.__dict__["revoke_authenticated"],
            McpPrivateContextConfirmationApp.__dict__["resolve_authenticated_challenge"],
            McpPrivateContextConfirmationApp.__dict__["hold_authenticated_human_exchange"],
        )
        raw_bearer_descriptor = type(self).__dict__["_raw_bearer"]
        fixed_transport_descriptor = type(self).__dict__["_fixed_transport_response"]
        disclose_browser_descriptor = type(self).__dict__["disclose_browser_challenge"]
        transport_handle_descriptor = McpContextRequestTransport.__dict__["handle"]
        owner = _McpContextOwner(
            _MCP_CONTEXT_OWNER_SLOT,
            _MCP_CONTEXT_CONFIG_SLOT,
            app,
            auth_manager,
            session_factory,
            context_sources,
            source_producer,
            confirmation,
            confirmation_repository,
            confirmation_authority,
            providers,
            owned_types,
            close_operations,
            bridge_descriptors,
            McpPrivateContextConfirmationService.__dict__["disclose"],
            AuthManager.__dict__["guard_session_authority"],
            authenticate_mcp_request,
            issue_mcp_bearer_proof,
            McpAuthenticatedIdentity,
            ContextSourceServices,
            MCP_CONTEXT_SCOPE,
            _McpContextGraph,
            raw_bearer_descriptor,
            fixed_transport_descriptor,
            disclose_browser_descriptor,
            transport_handle_descriptor,
            _McpContextConfig,
        )
        _write_mcp_context_owner(self, owner)
        self._app = app
        self._auth = auth_manager
        self._session_factory = session_factory
        self._sources = context_sources
        self._lock = threading.RLock()
        self._graph: _McpContextGraph | None = None
        self._started = False
        self._config_owner: object | None = None
        self._config_owner_type: type | None = None
        self._config_snapshot_descriptor: object | None = None
        self._config_binding_descriptor: object | None = None
        self._config_binding: tuple[object, ...] | None = None
        self._config_version: str | None = None
        self._effective_gates = (False, False)
        self._force_reconcile = True
        self._transition_generation = 0
        self._raw_bearer_descriptor = raw_bearer_descriptor
        self._fixed_transport_descriptor = fixed_transport_descriptor
        self._disclose_browser_descriptor = disclose_browser_descriptor
        self._authenticate_dispatch = owner.authenticate_dispatch
        self._issue_bearer_proof = owner.issue_bearer_proof
        self._identity_type = owner.identity_type
        self._sources_type = owner.sources_type
        self._context_scope = owner.context_scope
        self._graph_type = owner.graph_type
        self._owned_types = dict(owned_types)
        self._close_operations = close_operations
        self._bridge_descriptors = bridge_descriptors
        self._service_disclose_descriptor = owner.service_disclose_descriptor
        self._publish_disabled(self._fresh_epoch())

    def __repr__(self) -> str:
        return "McpContextApp(<redacted>)"

    @staticmethod
    def _fresh_epoch() -> str:
        return "epoch_" + secrets.token_hex(16)

    def _publish_disabled(self, epoch: str) -> None:
        if _EPOCH_RE.fullmatch(epoch) is None:
            raise ValueError("invalid MCP context epoch")
        self._app.state.mcp_context_plugin_enabled = False
        self._app.state.allow_context_reads = False
        self._app.state._process_epoch = epoch

    def bind_plugin_context(self, app: FastAPI, config_owner: object) -> None:
        """Bind exactly one installed plugin state as the gate configuration owner."""
        construction_owner = _read_mcp_context_owner(self)
        config_type = construction_owner.config_type
        with self._lock:
            existing = _read_mcp_context_config(self)
            if existing is not None:
                if (
                    type(existing) is config_type
                    and existing.owner is config_owner
                    and type(existing.binding) is tuple
                    and len(existing.binding) == 5
                    and existing.binding[0] is app
                ):
                    return
                raise ValueError("MCP context configuration owner already bound")
        descriptor = getattr(type(config_owner), "context_snapshot", None)
        binding_descriptor = getattr(type(config_owner), "context_owner_pins", None)
        if app is not self._app or not callable(descriptor) or not callable(binding_descriptor):
            raise ValueError("invalid MCP context configuration owner")
        try:
            binding = binding_descriptor(config_owner)
        except Exception:
            raise ValueError("invalid MCP context configuration owner") from None
        if (
            type(binding) is not tuple
            or len(binding) != 5
            or binding[0] is not app
            or not all(value is not None for value in binding[1:])
        ):
            raise ValueError("invalid MCP context configuration owner")
        with self._lock:
            existing = _read_mcp_context_config(self)
            if existing is not None:
                if (
                    type(existing) is config_type
                    and existing.owner is config_owner
                    and type(existing.binding) is tuple
                    and len(existing.binding) == 5
                    and existing.binding[0] is app
                ):
                    return
                raise ValueError("MCP context configuration owner already bound")
            config = config_type(
                construction_owner.config_slot, config_owner, type(config_owner),
                descriptor, binding_descriptor, binding,
            )
            _write_mcp_context_config(self, config)
            self._config_owner = config_owner
            self._config_owner_type = type(config_owner)
            self._config_snapshot_descriptor = descriptor
            self._config_binding_descriptor = binding_descriptor
            self._config_binding = binding
            self._force_reconcile = True
        self.synchronize()

    def _configuration_snapshot(self) -> tuple[bool, bool, str]:
        construction_owner = _read_mcp_context_owner(self)
        config = _read_mcp_context_config(self)
        if config is None:
            return False, False, "unbound"
        if type(config) is not construction_owner.config_type:
            return False, False, "invalid"
        owner = config.owner
        owner_type = config.owner_type
        descriptor = config.snapshot_descriptor
        binding_descriptor = config.binding_descriptor
        binding = config.binding
        if (
            _McpContextOwnerSlot.__dict__.get("_mcp_context_config_capsule")
            is not config.config_slot
            or config.config_slot is not construction_owner.config_slot
            or type(owner) is not owner_type
            or owner_type.__dict__.get("context_snapshot") is not descriptor
            or owner_type.__dict__.get("context_owner_pins") is not binding_descriptor
            or type(binding) is not tuple
            or self._config_owner is not owner
            or self._config_owner_type is not owner_type
            or self._config_snapshot_descriptor is not descriptor
            or self._config_binding_descriptor is not binding_descriptor
            or self._config_binding is not binding
        ):
            return False, False, "invalid"
        try:
            if binding_descriptor(owner) is not binding or binding[0] is not self._app:
                raise ValueError
            snapshot = descriptor(owner)
            if (
                owner_type.__dict__.get("context_snapshot") is not descriptor
                or owner_type.__dict__.get("context_owner_pins") is not binding_descriptor
                or self._config_owner is not owner
                or self._config_owner_type is not owner_type
                or self._config_snapshot_descriptor is not descriptor
                or self._config_binding_descriptor is not binding_descriptor
                or self._config_binding is not binding
                or binding_descriptor(owner) is not binding
                or binding[0] is not self._app
            ):
                raise ValueError
        except Exception:
            return False, False, "unavailable"
        if (
            type(snapshot) is not tuple
            or len(snapshot) != 3
            or type(snapshot[0]) is not bool
            or type(snapshot[1]) is not bool
            or type(snapshot[2]) is not str
            or not snapshot[2]
        ):
            return False, False, "invalid"
        return snapshot

    def _assert_owner_pins(self) -> None:
        owner = _read_mcp_context_owner(self)
        if type(owner) is not _McpContextOwner:
            raise ValueError("invalid MCP context composition")
        owned_types = dict(owner.owned_types)
        raw_descriptor = type(self).__dict__.get("_raw_bearer")
        expected_globals = {
            "McpContextAuthority": owned_types["authority"],
            "CombinedContextManifestProducer": owned_types["producer"],
            "McpPrivateContextConfirmationService": owned_types["service"],
            "McpPrivateContextConfirmationApp": owned_types["bridge"],
            "McpContextCursorAuthority": owned_types["cursor"],
            "McpContextPackCoordinator": owned_types["coordinator"],
            "McpContextRequestRuntime": owned_types["runtime"],
            "McpContextRequestTransport": owned_types["transport"],
            "authenticate_mcp_request": owner.authenticate_dispatch,
            "issue_mcp_bearer_proof": owner.issue_bearer_proof,
            "McpAuthenticatedIdentity": owner.identity_type,
            "ContextSourceServices": owner.sources_type,
            "MCP_CONTEXT_SCOPE": owner.context_scope,
            "CookieConfirmationAuthority": owned_types["cookie_authority"],
            "_McpContextGraph": owner.graph_type,
            "_McpContextOwner": type(owner),
            "_McpContextConfig": owner.config_type,
        }
        sources = owner.sources
        source_drift = False
        if sources is not None:
            source_drift = (
                type(sources) is not owner.sources_type
                or sources.producer is not owner.source_producer
                or sources.confirmation is not owner.confirmation
                or owner.providers is None
                or any(
                    current is not pinned
                    for current, pinned in zip(
                        (sources.forge, sources.planning, sources.knowledge),
                        owner.providers,
                    )
                )
                or getattr(owner.confirmation, "_repository", None)
                is not owner.confirmation_repository
                or getattr(owner.confirmation, "_authority", None)
                is not owner.confirmation_authority
                or getattr(owner.confirmation_repository, "_session_factory", None)
                is not owner.session_factory
                or getattr(owner.confirmation_repository, "_authority", None)
                is not owner.confirmation_authority
                or getattr(owner.confirmation_authority, "_auth_manager", None)
                is not owner.auth
            )
        if (
            type(self) is not McpContextApp
            or _McpContextOwnerSlot.__dict__.get("_mcp_context_owner_capsule")
            is not owner.owner_slot
            or owner.owner_slot is not _MCP_CONTEXT_OWNER_SLOT
            or _McpContextOwnerSlot.__dict__.get("_mcp_context_config_capsule")
            is not owner.config_slot
            or self._app is not owner.app
            or self._auth is not owner.auth
            or self._session_factory is not owner.session_factory
            or self._sources is not sources
            or self._authenticate_dispatch is not owner.authenticate_dispatch
            or self._issue_bearer_proof is not owner.issue_bearer_proof
            or self._identity_type is not owner.identity_type
            or self._sources_type is not owner.sources_type
            or self._context_scope != owner.context_scope
            or self._graph_type is not owner.graph_type
            or getattr(owner.app.state, "auth_manager", None) is not owner.auth
            or (sources is not None and getattr(owner.app.state, "context_sources", None) is not sources)
            or (sources is not None and getattr(owner.app.state, "private_context_confirmation", None)
                is not owner.confirmation)
            or source_drift
            or tuple(self._owned_types) != tuple(name for name, _value in owner.owned_types)
            or any(self._owned_types.get(name) is not value for name, value in owner.owned_types)
            or self._close_operations is not owner.close_operations
            or self._bridge_descriptors is not owner.bridge_descriptors
            or self._service_disclose_descriptor is not owner.service_disclose_descriptor
            or raw_descriptor is not owner.raw_bearer_descriptor
            or self._raw_bearer_descriptor is not owner.raw_bearer_descriptor
            or type(self).__dict__.get("_fixed_transport_response")
            is not owner.fixed_transport_descriptor
            or self._fixed_transport_descriptor is not owner.fixed_transport_descriptor
            or type(self).__dict__.get("disclose_browser_challenge")
            is not owner.disclose_browser_descriptor
            or self._disclose_browser_descriptor is not owner.disclose_browser_descriptor
            or not isinstance(raw_descriptor, staticmethod)
            or any(globals().get(name) is not value for name, value in expected_globals.items())
            or any(
                owned_type.__dict__.get("close") is not close
                for _name, owned_type, close in owner.close_operations
            )
            or McpPrivateContextConfirmationService.__dict__.get("disclose")
            is not owner.service_disclose_descriptor
            or AuthManager.__dict__.get("guard_session_authority")
            is not owner.auth_guard_descriptor
            or owned_types["transport"].__dict__.get("handle")
            is not owner.transport_handle_descriptor
        ):
            raise ValueError("invalid MCP context composition")

    def start(self) -> None:
        self._assert_owner_pins()
        with self._lock:
            if self._started:
                return
            self._started = True
            self._force_reconcile = True
        self.synchronize()

    def stop(self) -> None:
        owner = _read_mcp_context_owner(self)
        detached = None
        with owner.auth_guard_descriptor(owner.auth):
            with self._lock:
                self._started = False
                self._force_reconcile = True
                self._transition_generation += 1
                detached, self._graph = self._graph, None
                self._effective_gates = (False, False)
                self._config_version = None
                self._publish_disabled(self._fresh_epoch())
        self._close_graph(detached)

    def synchronize(self) -> None:
        """Reconcile exact persisted gates using the repository lock order."""
        self._assert_owner_pins()
        owner = _read_mcp_context_owner(self)
        detached = None
        should_build = False
        transition = -1
        with owner.auth_guard_descriptor(owner.auth):
            enabled, allow_reads, version = self._configuration_snapshot()
            desired = (enabled is True, allow_reads is True)
            with self._lock:
                current_graph = self._graph
                active = self._started and desired == (True, True)
                unchanged = (
                    not self._force_reconcile
                    and self._effective_gates == desired
                    and self._config_version == version
                    and (current_graph is not None) is active
                )
                if unchanged:
                    return
                self._force_reconcile = False
                self._transition_generation += 1
                transition = self._transition_generation
                detached, self._graph = current_graph, None
                epoch = self._fresh_epoch()
                self._publish_disabled(epoch)
                self._effective_gates = desired
                self._config_version = version
                should_build = active
        self._close_graph(detached)
        if not should_build:
            return
        try:
            candidate = self._build_graph()
        except Exception:
            return
        publish = False
        with owner.auth_guard_descriptor(owner.auth):
            current = self._configuration_snapshot()
            with self._lock:
                publish = (
                    self._started
                    and self._transition_generation == transition
                    and self._graph is None
                    and self._effective_gates == (True, True)
                    and current == (True, True, version)
                    and self._config_version == version
                )
                if publish:
                    try:
                        self._assert_graph(candidate)
                    except Exception:
                        publish = False
                    else:
                        self._graph = candidate
                        self._app.state.mcp_context_plugin_enabled = True
                        self._app.state.allow_context_reads = True
        if not publish:
            self._close_graph(candidate)

    def _build_graph(self) -> _McpContextGraph:
        self._assert_owner_pins()
        owner = _read_mcp_context_owner(self)
        sources = owner.sources
        if (
            type(sources) is not owner.sources_type
            or getattr(owner.app.state, "context_sources", None) is not sources
            or sources.confirmation is not getattr(owner.app.state, "private_context_confirmation", None)
            or sources.confirmation is not owner.confirmation
            or sources.confirmation._authority is not owner.confirmation_authority
            or sources.confirmation._repository is not owner.confirmation_repository
            or sources.confirmation._authority._auth_manager is not owner.auth
            or sources.confirmation._repository._session_factory is not owner.session_factory
            or sources.confirmation._repository._authority is not owner.confirmation_authority
        ):
            raise ValueError("MCP context providers unavailable")
        created: dict[str, object] = {}
        try:
            constructors = dict(owner.owned_types)
            authority_type = constructors["authority"]
            producer_type = constructors["producer"]
            service_type = constructors["service"]
            bridge_type = constructors["bridge"]
            cursor_type = constructors["cursor"]
            coordinator_type = constructors["coordinator"]
            runtime_type = constructors["runtime"]
            transport_type = constructors["transport"]
            self._assert_owner_pins()
            authority = authority_type(
                owner.auth,
                owner.session_factory,
                app_state=owner.app.state,
            )
            created["authority"] = authority
            self._assert_owner_pins()
            producer = producer_type(
                auth_manager=owner.auth,
                forge=owner.providers[0],
                planning=owner.providers[1],
                knowledge=owner.providers[2],
                mcp_authority=authority,
            )
            self._assert_owner_pins()
            cookie_authority = constructors["cookie_authority"](owner.auth)
            service = service_type(
                auth_manager=owner.auth,
                authority=authority,
                producer=producer,
                cookie_authority=cookie_authority,
                session_factory=owner.session_factory,
            )
            created["service"] = service
            self._assert_owner_pins()
            bridge = bridge_type(
                service=service,
                auth_manager=owner.auth,
                session_factory=owner.session_factory,
            )
            cursor = cursor_type(authority)
            created["cursor"] = cursor
            coordinator = coordinator_type(service, producer, cursor)
            created["coordinator"] = coordinator
            runtime = runtime_type(
                authority, service, producer, cursor, coordinator,
            )
            created["runtime"] = runtime
            raw_bearer = owner.raw_bearer_descriptor.__func__
            authenticate_dispatch = owner.authenticate_dispatch
            issue_bearer_proof = owner.issue_bearer_proof
            identity_type = owner.identity_type
            context_scope = owner.context_scope

            def authenticate(
                request,
                _raw=raw_bearer,
                _dispatch=authenticate_dispatch,
                _identity_type=identity_type,
                _context_scope=context_scope,
            ):
                _raw(request)
                identity = _dispatch(request)
                try:
                    delattr(request.state, "_mcp_bearer_proof")
                except Exception:
                    pass
                if type(identity) is not _identity_type or identity.scope != _context_scope:
                    raise ValueError("invalid MCP context identity")
                return identity

            def capture(request, _raw=raw_bearer, _issue=issue_bearer_proof):
                return _issue(_raw(request))

            transport = transport_type(runtime, authenticate, capture)
            created["transport"] = transport
            self._assert_owner_pins()
            graph = owner.graph_type(
                authority, producer, service, bridge, cursor,
                coordinator, runtime, transport, authenticate, capture,
            )
            self._assert_graph(graph)
            return graph
        except Exception:
            self._close_created(created)
            raise

    def _close_created(self, created: dict[str, object]) -> None:
        owner = _read_mcp_context_owner(self)
        operations = {
            name: (owned_type, close)
            for name, owned_type, close in owner.close_operations
        }
        for name in ("transport", "runtime", "coordinator", "cursor", "service", "authority"):
            value = created.get(name)
            owned_type, close = operations[name]
            if type(value) is owned_type:
                try:
                    close(value)
                except Exception:
                    pass

    def _close_graph(self, graph: _McpContextGraph | None) -> None:
        if graph is None:
            return
        owner = _read_mcp_context_owner(self)
        for name, owned_type, close in owner.close_operations:
            value = object.__getattribute__(graph, name)
            if type(value) is not owned_type:
                continue
            try:
                close(value)
            except Exception:
                pass

    def _assert_graph(self, graph: _McpContextGraph) -> None:
        owner = _read_mcp_context_owner(self)
        if (
            getattr(owner.app.state, 'context_sources', None) is not owner.sources
            or owner.sources.confirmation is not getattr(owner.app.state, 'private_context_confirmation', None)
        ):
            raise ValueError('invalid MCP context graph')
        self._assert_owner_pins()
        owned_types = dict(owner.owned_types)
        if (
            type(graph) is not owner.graph_type
            or type(graph.authority) is not owned_types["authority"]
            or type(graph.producer) is not owned_types["producer"]
            or type(graph.service) is not owned_types["service"]
            or type(graph.bridge) is not owned_types["bridge"]
            or type(graph.cursor) is not owned_types["cursor"]
            or type(graph.coordinator) is not owned_types["coordinator"]
            or type(graph.runtime) is not owned_types["runtime"]
            or type(graph.transport) is not owned_types["transport"]
            or graph.authority._auth_manager is not owner.auth
            or graph.authority._session_factory is not owner.session_factory
            or graph.authority._session_factory_pin is not owner.session_factory
            or graph.producer._mcp_authority is not graph.authority
            or graph.service._authority is not graph.authority
            or graph.service._producer is not graph.producer
            or graph.service._auth is not owner.auth
            or graph.service._session_factory is not owner.session_factory
            or graph.service._session_factory_pin is not owner.session_factory
            or graph.bridge._service is not graph.service
            or graph.bridge._auth is not owner.auth
            or graph.bridge._session_factory is not owner.session_factory
            or graph.cursor._authority is not graph.authority
            or graph.coordinator._authority is not graph.authority
            or graph.runtime._authority is not graph.authority
            or graph.transport._runtime is not graph.runtime
            or graph.transport._authenticate is not graph.authenticate
            or graph.transport._authenticate_pin is not graph.authenticate
            or graph.transport._capture_prepare_proof is not graph.capture_prepare_proof
            or graph.transport._capture_prepare_proof_pin is not graph.capture_prepare_proof
            or type(graph.transport).__dict__.get("handle")
            is not owner.transport_handle_descriptor
            or "handle" in getattr(graph.transport, "__dict__", {})
            or type(owner.sources) is not owner.sources_type
            or getattr(owner.app.state, "context_sources", None) is not owner.sources
            or owner.sources.confirmation
            is not getattr(owner.app.state, "private_context_confirmation", None)
            or graph.producer._forge is not owner.providers[0]
            or graph.producer._planning is not owner.providers[1]
            or graph.producer._knowledge is not owner.providers[2]
            or tuple(
                McpPrivateContextConfirmationApp.__dict__.get(name)
                for name in (
                    "issue_authenticated_challenge",
                    "revoke_authenticated",
                    "resolve_authenticated_challenge",
                    "hold_authenticated_human_exchange",
                )
            ) != owner.bridge_descriptors
            or any(name in graph.bridge.__dict__ for name in (
                "issue_authenticated_challenge",
                "revoke_authenticated",
                "resolve_authenticated_challenge",
                "hold_authenticated_human_exchange",
            ))
        ):
            raise ValueError("invalid MCP context graph")

    @staticmethod
    def _raw_bearer(request: Request) -> str:
        headers = request.headers
        authorizations = headers.getlist("authorization")
        cookies = headers.getlist("cookie")
        if len(authorizations) != 1 or cookies:
            raise ValueError("invalid MCP context credentials")
        value = authorizations[0]
        if type(value) is not str or not value.startswith("Bearer "):
            raise ValueError("invalid MCP context credentials")
        raw = value[7:]
        if (
            not raw.startswith("ody_")
            or not raw.isascii()
            or not 8 <= len(raw) <= 512
            or re.fullmatch(r"ody_[A-Za-z0-9_-]+", raw) is None
        ):
            raise ValueError("invalid MCP context credentials")
        return raw

    @staticmethod
    def _fixed_transport_response(status: int, _response_type=Response) -> Response:
        return _response_type(
            content=b"",
            status_code=status,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def handle_mcp_request(self, request: Request) -> Response:
        owner = _read_mcp_context_owner(self)
        fixed_response = owner.fixed_transport_descriptor.__func__
        try:
            self._assert_owner_pins()
            owner.raw_bearer_descriptor.__func__(request)
            self.synchronize()
            with self._lock:
                graph = self._graph
                if not self._started or graph is None:
                    try:
                        record_app_graph_absent()
                    except BaseException:
                        pass
                    return fixed_response(403)
                self._assert_graph(graph)
                transport = graph.transport
                handle = owner.transport_handle_descriptor
            return await handle(transport, request)
        except Exception:
            return fixed_response(401)

    def _active_bridge(self) -> McpPrivateContextConfirmationApp:
        bridge, _descriptor = self._active_bridge_operation(0)
        return bridge

    def _active_bridge_operation(
        self,
        descriptor_index: int,
    ) -> tuple[McpPrivateContextConfirmationApp, object]:
        with self._lock:
            active_graph = self._graph
            if self._started and active_graph is not None:
                self._assert_graph(active_graph)
        self.synchronize()
        owner = _read_mcp_context_owner(self)
        with self._lock:
            graph = self._graph
            if not self._started or graph is None:
                raise McpPrivateContextConfirmationError("invalid_context")
            self._assert_graph(graph)
            bridge = graph.bridge
            descriptor = owner.bridge_descriptors[descriptor_index]
            names = (
                "issue_authenticated_challenge",
                "revoke_authenticated",
                "resolve_authenticated_challenge",
                "hold_authenticated_human_exchange",
            )
            if (
                McpPrivateContextConfirmationApp.__dict__.get(names[descriptor_index])
                is not descriptor
                or names[descriptor_index] in bridge.__dict__
            ):
                raise McpPrivateContextConfirmationError("invalid_context")
            return bridge, descriptor

    def issue_browser_challenge(self, request: Request, request_id: str):
        bridge, issue = self._active_bridge_operation(0)
        return issue(bridge, request, request_id)

    def revoke_browser_consent(self, request: Request, request_id: str) -> None:
        bridge, revoke = self._active_bridge_operation(1)
        revoke(bridge, request, request_id)

    def resolve_browser_challenge(
        self,
        challenge: str,
        owner_sid: str,
        chat_session_id: str,
    ) -> tuple[McpPrivateContextConfirmationApp, str]:
        bridge, resolve = self._active_bridge_operation(2)
        request_id = resolve(
            bridge,
            challenge,
            owner_sid,
            chat_session_id,
        )
        return bridge, request_id

    def capture_browser_disclosure_entry(self, app: FastAPI):
        """Return the constructor-pinned disclosure entry after current graph validation."""
        self.synchronize()
        owner = _read_mcp_context_owner(self)
        with self._lock:
            graph = self._graph
            if app is not owner.app or not self._started or graph is None:
                raise McpPrivateContextConfirmationError("invalid_context")
            self._assert_graph(graph)
            if (
                type(self).__dict__.get("disclose_browser_challenge")
                is not owner.disclose_browser_descriptor
                or "disclose_browser_challenge" in self.__dict__
            ):
                raise McpPrivateContextConfirmationError("invalid_context")
            return owner.disclose_browser_descriptor

    def disclose_browser_challenge(
        self,
        request: Request,
        challenge: str,
        owner_sid: str,
        chat_session_id: str,
        raw_user_command: str,
    ):
        """Run the exact persisted human exchange without composition locks."""
        self.synchronize()
        owner = _read_mcp_context_owner(self)
        with self._lock:
            graph = self._graph
            if not self._started or graph is None:
                raise McpPrivateContextConfirmationError("invalid_context")
            self._assert_graph(graph)
            bridge, service = graph.bridge, graph.service
            resolve = owner.bridge_descriptors[2]
            hold = owner.bridge_descriptors[3]
            disclose = owner.service_disclose_descriptor
        request_id = resolve(bridge, challenge, owner_sid, chat_session_id)
        with hold(
            bridge,
            request,
            request_id,
            challenge,
            raw_user_command=raw_user_command,
        ) as exchange:
            receipt = disclose(
                service,
                request_id,
                challenge,
                raw_user_command=raw_user_command,
                human_exchange=exchange,
            )
        return request_id, receipt


def _browser_error() -> JSONResponse:
    return JSONResponse(
        {"error": "context_unavailable"},
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def _decode_exact_browser_body(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_BROWSER_BODY:
        raise ValueError("invalid browser request")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if type(key) is not str or key in value:
                raise ValueError("duplicate browser member")
            value[key] = item
        return value

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if type(value) is not dict or set(value) != {"request_id"}:
        raise ValueError("invalid browser request")
    request_id = value["request_id"]
    if type(request_id) is not str or _REQUEST_ID_RE.fullmatch(request_id) is None:
        raise ValueError("invalid browser request")
    return value


async def _exact_browser_body(
    request: Request,
    _decode=_decode_exact_browser_body,
    _stream_descriptor=_REQUEST_STREAM_DESCRIPTOR,
    _monotonic=_BROWSER_MONOTONIC,
    _wait_for=_BROWSER_WAIT_FOR,
    _sleep=_BROWSER_SLEEP,
) -> dict[str, object]:
    content_types = request.headers.getlist("content-type")
    if (
        type(request) is not Request
        or Request.__dict__.get("stream") is not _stream_descriptor
        or request.url.query
        or len(content_types) != 1
        or content_types[0].split(";", 1)[0].strip().lower() != "application/json"
    ):
        raise ValueError("invalid browser request")
    cached = getattr(request, "_body", None)
    if cached is not None:
        return _decode(cached)
    deadline = _monotonic() + _BROWSER_BODY_TIMEOUT_SECONDS
    chunks: list[bytes] = []
    received = 0
    original_receive = object.__getattribute__(request, "_receive")
    if not callable(original_receive):
        raise ValueError("invalid browser request")

    async def bounded_receive():
        if _monotonic() >= deadline:
            raise TimeoutError("browser request deadline exceeded")
        message = await original_receive()
        if _monotonic() >= deadline:
            raise TimeoutError("browser request deadline exceeded")
        if (
            type(message) is dict
            and message.get("type") == "http.request"
            and message.get("body", b"") == b""
            and message.get("more_body", False) is True
        ):
            await _sleep(0)
            if _monotonic() >= deadline:
                raise TimeoutError("browser request deadline exceeded")
        return message

    object.__setattr__(request, "_receive", bounded_receive)
    try:
        stream = _stream_descriptor(request)
        iterator = stream.__aiter__()
        while True:
            remaining = deadline - _monotonic()
            if remaining <= 0.0:
                raise TimeoutError("browser request deadline exceeded")
            try:
                chunk = await _wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                if _monotonic() >= deadline:
                    raise TimeoutError("browser request deadline exceeded")
                break
            if _monotonic() >= deadline:
                raise TimeoutError("browser request deadline exceeded")
            if type(chunk) is not bytes or len(chunk) > _MAX_BROWSER_BODY - received:
                raise ValueError("invalid browser request")
            chunks.append(chunk)
            received += len(chunk)
    finally:
        if object.__getattribute__(request, "_receive") is bounded_receive:
            object.__setattr__(request, "_receive", original_receive)
    raw = b"".join(chunks)
    value = _decode(raw)
    request._body = raw
    return value


def create_mcp_context_router(composition: McpContextApp) -> APIRouter:
    if type(composition) is not McpContextApp:
        raise ValueError("invalid MCP context composition")
    router = APIRouter(prefix="/api/context/mcp", tags=["mcp_context"])
    issue_browser_challenge = McpContextApp.__dict__["issue_browser_challenge"]
    revoke_browser_consent = McpContextApp.__dict__["revoke_browser_consent"]
    exact_browser_body = _exact_browser_body
    browser_error = _browser_error

    @router.post("/challenge")
    async def challenge(request: Request):
        try:
            if globals().get("_exact_browser_body") is not exact_browser_body:
                raise ValueError("invalid browser request reader")
            body = await exact_browser_body(request)
            issued = await asyncio.to_thread(
                issue_browser_challenge,
                composition,
                request,
                body["request_id"],
            )
            expires = issued.expires_at.astimezone(timezone.utc).isoformat(
                timespec="microseconds",
            ).replace("+00:00", "Z")
            return JSONResponse(
                {"request_id": body["request_id"], "token": issued.token, "expires_at": expires},
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            return browser_error()

    @router.post("/revoke")
    async def revoke(request: Request):
        try:
            if globals().get("_exact_browser_body") is not exact_browser_body:
                raise ValueError("invalid browser request reader")
            body = await exact_browser_body(request)
            await asyncio.to_thread(
                revoke_browser_consent,
                composition,
                request,
                body["request_id"],
            )
            return JSONResponse(
                {"status": "revoked"},
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            return browser_error()

    return router


def install_mcp_context_app(
    app: FastAPI,
    *,
    auth_manager: AuthManager,
    session_factory: Callable[[], object],
    context_sources: ContextSourceServices | None,
) -> McpContextApp:
    if getattr(app.state, "mcp_context_app", None) is not None:
        raise ValueError("MCP context composition already installed")
    paths = {getattr(route, "path", None) for route in app.routes}
    if paths & {"/api/context/mcp/challenge", "/api/context/mcp/revoke"}:
        raise ValueError("MCP context routes already installed")
    composition = McpContextApp(
        app,
        auth_manager=auth_manager,
        session_factory=session_factory,
        context_sources=context_sources,
    )
    app.include_router(create_mcp_context_router(composition))
    app.state.mcp_context_app = composition
    return composition


__all__ = [
    "McpContextApp",
    "create_mcp_context_router",
    "install_mcp_context_app",
]
