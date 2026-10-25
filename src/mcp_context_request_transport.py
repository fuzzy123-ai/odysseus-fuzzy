"""Inactive bounded HTTP transport for the native MCP context runtime.

The application supplies synchronous, server-owned identity and prepare-proof
resolvers. This adapter borrows the runtime and never activates a route.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable

from fastapi import Request
from fastapi.responses import Response

import src.mcp_context_contract as _contract_module
import src.mcp_context_authority as _authority_module
import src.mcp_context_pack_coordinator as _coordinator_module
import src.mcp_context_request_runtime as _runtime_module
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import McpContextBearerProof, release_mcp_bearer_proof
from src.mcp_context_contract import (
    MCP_CONTEXT_BODY_MAX_BYTES,
    MCP_CONTEXT_RESULT_MAX_BYTES,
    MCP_CONTEXT_SCOPE,
    McpContextContractError,
    McpContextToolCall,
    parse_mcp_context_request,
)
from src.mcp_context_request_runtime import (
    McpContextRequestRuntime,
    McpContextRequestRuntimeError,
)
from src.mcp_context_causal_observation import (
    runtime_context_enter as _observation_runtime_context_enter,
    runtime_context_exit as _observation_runtime_context_exit,
    record_content_rejection as _record_content_rejection,
    span_begin as _observation_span_begin,
    span_end as _observation_span_end,
)

MCP_CONTEXT_TRANSPORT_BODY_TIMEOUT_SECONDS = 10.0
_STATUS_INVALID = 400
_STATUS_AUTHENTICATION = 401
_STATUS_FORBIDDEN = 403
_STATUS_TIMEOUT = 408
_STATUS_BUDGET = 413
_STATUS_UNAVAILABLE = 503
_PREPARE_METHOD = "harbour_context_prepare"
_MISSING = object()
_TRUSTED_FAILURE_CLASS = RuntimeError


def _class_pin(cls: type, names: tuple[str, ...]) -> tuple[tuple[str, object], ...]:
    namespace = cls.__dict__
    return tuple((name, namespace.get(name, _MISSING)) for name in names)


_IDENTITY_FIELDS = (
    "token_id", "owner_alias", "owner_subject_id", "cache_generation",
    "security_action_revision", "scope",
)
_IDENTITY_PIN = _class_pin(
    McpAuthenticatedIdentity,
    (*_IDENTITY_FIELDS, "__init__", "__getattribute__"),
)
_CALL_PIN = _class_pin(
    McpContextToolCall,
    ("method", "request_id", "arguments", "__init__", "__getattribute__"),
)
_PROOF_PIN = _class_pin(
    McpContextBearerProof,
    ("__init__", "__setattr__", "__init_subclass__", "__repr__"),
)

_TRUSTED_RUNTIME_CLASS = McpContextRequestRuntime
_TRUSTED_RUNTIME_PIN = dict(_runtime_module._RUNTIME_PIN)
_TRUSTED_RUNTIME_HANDLE = _TRUSTED_RUNTIME_PIN["handle"]

# Every code object below comes from an import-time frozen product pin.  The
# observer never samples a live alias or invokes a mutable classifier.
_CONTENT_REJECTION_CODE_MAP = (
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3141, 3157, "runtime", "runtime.content_preflight"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3157, 3174, "runtime", "runtime.native_lease_acquire"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3174, 3195, "runtime", "runtime.native_lease_shape"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3195, 3202, "runtime", "runtime.native_lease_live"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3202, 3210, "runtime", "runtime.record_live"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3215, 3234, "runtime", "runtime.publication_open"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3234, 3242, "runtime", "runtime.postpublication_validation"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3242, 3256, "runtime", "runtime.publication_exit"),
    (_TRUSTED_RUNTIME_PIN["_existing"].__code__, 3280, 3334, "runtime", "runtime.cleanup"),
    (_coordinator_module._TRUSTED_ACQUIRE_NATIVE_LEASE_CAPABILITY.__code__, 1006, 1028, "authority", "authority.native_lease_acquire"),
    (_coordinator_module._TRUSTED_READ_NATIVE_LEASE_STATE.__code__, 399, 419, "authority", "authority.native_lease_acquire"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1600, 1602, "authority", "authority.lease_recheck.snapshot"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1602, 1607, "authority", "authority.lease_recheck.state_lookup"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1607, 1608, "authority", "authority.lease_recheck.cancelled_marker"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1608, 1609, "authority", "authority.lease_recheck.missing"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1609, 1611, "authority", "authority.lease_recheck.cancelled_row"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1611, 1617, "authority", "authority.lease_recheck.clock_read"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1617, 1619, "authority", "authority.lease_recheck.clock_validity"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1619, 1622, "authority", "authority.lease_recheck.expiry"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1622, 1624, "authority", "authority.lease_recheck.binding"),
    (_runtime_module._TRUSTED_RECHECK_LEASE.__code__, 1624, 1630, "authority", "authority.lease_recheck.propagation"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1490, 1492, "authority", "authority.binding_recheck.snapshot"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1492, 1500, "authority", "authority.binding_recheck.identity"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1500, 1508, "authority", "authority.binding_recheck.proof_ownership"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1508, 1510, "authority", "authority.binding_recheck.live_precheck"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1510, 1511, "authority", "authority.binding_recheck.proof_shape"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1511, 1512, "authority", "authority.binding_recheck.authority_state"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1512, 1513, "authority", "authority.binding_recheck.live_postcheck"),
    (_runtime_module._TRUSTED_RECHECK_BINDING.__code__, 1513, 1526, "authority", "authority.binding_recheck.final_ownership"),
    (_runtime_module._ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_CODE, 2227, 2505, "authority", "authority.publication_open"),
    (_coordinator_module._TRUSTED_ISSUE_CAPTURED_PERMIT.__code__, 1166, 1369, "confirmation", "confirmation.permit_issue"),
    (_coordinator_module._TRUSTED_REJECT_NATIVE_PERMIT.__code__, 1369, 1630, "confirmation", "confirmation.permit_reject"),
    (_coordinator_module._TRUSTED_FENCE.__code__, 2185, 2505, "confirmation", "confirmation.publication_fence"),
    (_coordinator_module._TRUSTED_ISSUE_CONTENT.__code__, 363, 450, "cursor", "cursor.content_issue"),
    (_coordinator_module._TRUSTED_VERIFY_CONTENT.__code__, 467, 567, "cursor", "cursor.content_verify"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1215, 1236, "coordinator", "coordinator.selection_validate"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1236, 1254, "coordinator", "coordinator.permit_issue"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1254, 1274, "coordinator", "coordinator.cursor_verify"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1274, 1278, "coordinator", "coordinator.forge_dispatch"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1278, 1283, "coordinator", "coordinator.planning_dispatch"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1283, 1288, "coordinator", "coordinator.knowledge_dispatch"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1288, 1307, "coordinator", "coordinator.inner_page_contract"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1307, 1313, "coordinator", "coordinator.content_cursor_issue"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1313, 1325, "coordinator", "coordinator.response_encode"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1325, 1329, "coordinator", "coordinator.final_freshness"),
    (_runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1329, 1342, "coordinator", "coordinator.finish"),
    (_coordinator_module._TRUSTED_PUBLISH_FORGE.__code__, 298, 343, "native", "native.execution"),
    (_coordinator_module._TRUSTED_PUBLISH_FORGE.__code__, 343, 350, "native", "native.recovery"),
    (_coordinator_module._TRUSTED_PUBLISH_FORGE.__code__, 350, 354, "native", "native.consumption"),
    (_coordinator_module._TRUSTED_PUBLISH_KNOWLEDGE.__code__, 298, 343, "native", "native.execution"),
    (_coordinator_module._TRUSTED_PUBLISH_KNOWLEDGE.__code__, 343, 350, "native", "native.recovery"),
    (_coordinator_module._TRUSTED_PUBLISH_KNOWLEDGE.__code__, 350, 354, "native", "native.consumption"),
    (_coordinator_module._TRUSTED_PUBLISH_PLANNING.__code__, 180, 228, "planning", "planning.execution"),
    (_coordinator_module._TRUSTED_PUBLISH_PLANNING.__code__, 228, 240, "planning", "planning.failure"),
    (_coordinator_module._TRUSTED_PUBLISH_PLANNING.__code__, 240, 246, "planning", "planning.consumption"),
    (_coordinator_module._TRUSTED_COORDINATOR_BEGIN.__code__, 1021, 1042, "coordinator", "coordinator.selection_validate"),
    (_coordinator_module._TRUSTED_COORDINATOR_CHECK_ACTIVE.__code__, 1042, 1049, "coordinator", "coordinator.active_guard"),
    (_coordinator_module._TRUSTED_COORDINATOR_FINISH.__code__, 1049, 1063, "coordinator", "coordinator.finish"),
    (_coordinator_module._TRUSTED_COORDINATOR_ABORT.__code__, 1063, 1073, "coordinator", "coordinator.finish"),
    (_coordinator_module._TRUSTED_COORDINATOR_CAPTURE.__code__, 1073, 1090, "coordinator", "coordinator.capture"),
    (_coordinator_module._TRUSTED_COORDINATOR_FENCE.__code__, 1090, 1098, "coordinator", "coordinator.fence"),
    (_coordinator_module._TRUSTED_COORDINATOR_ENCODE.__code__, 1098, 1105, "coordinator", "coordinator.response_encode"),
    ("transparent_wrapper", McpContextRequestRuntimeError, _TRUSTED_RUNTIME_HANDLE.__code__, 3385, 3387),
    ("transparent_wrapper", _coordinator_module.McpContextPackCoordinatorError, _runtime_module._TRUSTED_PUBLISH_CONTENT.__code__, 1340, 1342),
)
_TRUSTED_RUNTIME_ASSERT_PINS = _TRUSTED_RUNTIME_PIN["_assert_pins"]
_TRUSTED_PARSE = _runtime_module._TRUSTED_PARSE
_TRUSTED_RELEASE_PROOF = release_mcp_bearer_proof
_TRUSTED_REQUEST_CLASS = Request
_TRUSTED_REQUEST_STREAM = Request.stream
_TRUSTED_RESPONSE_CLASS = Response
_TRUSTED_RESPONSE_METACLASS = type(Response)
_TRUSTED_RESPONSE_BASES = Response.__bases__
_TRUSTED_RESPONSE_MRO = Response.__mro__
_TRUSTED_RESPONSE_NAMESPACE_TYPE = type(Response.__dict__)
_TRUSTED_RESPONSE_DICT_DESCRIPTOR = Response.__dict__["__dict__"]
_TRUSTED_GETSET_DESCRIPTOR_TYPE = type(_TRUSTED_RESPONSE_DICT_DESCRIPTOR)
_TRUSTED_RESPONSE_PIN = _class_pin(
    Response,
    (
        "__module__", "__dict__", "__getattribute__", "__setattr__", "__call__",
        "__init_subclass__", "__init__", "render", "init_headers", "status_code",
        "raw_headers", "body", "media_type", "background", "headers",
    ),
)
_TRUSTED_TYPE = type
_TRUSTED_TYPE_NEW = type.__new__
_TRUSTED_TYPE_GETATTRIBUTE = type.__getattribute__
_TRUSTED_OBJECT_NEW = object.__new__
_TRUSTED_OBJECT_GETATTRIBUTE = object.__getattribute__
_TRUSTED_OBJECT_SETATTR = object.__setattr__
_TRUSTED_RLOCK_TYPE = type(threading.RLock())
_TRUSTED_PROOF_LOCK = _authority_module._PROOF_LOCK
_TRUSTED_PROOFS = _authority_module._PROOFS
_MODULE_NAMESPACE = globals()


def _make_publication_factory(
    *, module_namespace: dict[str, object], response_class: type,
    response_metaclass: type, response_bases: tuple[type, ...],
    response_mro: tuple[type, ...], response_namespace_type: type,
    response_pin: tuple[tuple[str, object], ...], getset_descriptor_type: type,
    type_class: type, type_new: object, type_getattribute: object,
    object_class: type, object_new: object, object_getattribute: object,
    rlock_type: type, result_max_bytes: int,
):
    """Build a fresh, private, single-seal publication owner and view."""
    missing = _MISSING
    list_class, tuple_class = list, tuple
    str_class, int_class, bytes_class, bool_class = str, int, bytes, bool
    len_function = len
    all_function, any_function = all, any
    enumerate_function, reversed_function, iter_function = enumerate, reversed, iter
    attribute_error, key_error = AttributeError, KeyError
    base_exception = BaseException
    invalid_status = _STATUS_INVALID
    authentication_status = _STATUS_AUTHENTICATION
    forbidden_status = _STATUS_FORBIDDEN
    timeout_status = _STATUS_TIMEOUT
    budget_status = _STATUS_BUDGET
    unavailable_status = _STATUS_UNAVAILABLE
    module_name = __name__
    shield_name = "_TransportResponseShield"
    view_name = "_TransportResponseView"
    allowed_errors = (
        invalid_status, authentication_status, forbidden_status,
        timeout_status, budget_status, unavailable_status,
    )
    fixed_prefix = (
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
    )
    shared_pin_box: list[tuple[tuple[str, object], ...] | None] = [None]

    def set_shared_pins(pins: tuple[tuple[str, object], ...]) -> None:
        if shared_pin_box[0] is not None or type_class(pins) is not tuple_class:
            raise RuntimeError("publication pins already set")
        shared_pin_box[0] = pins

    def exact_identities(actual: object, expected: tuple[object, ...]) -> bool:
        return bool_class(
            type_class(actual) is tuple_class
            and len_function(actual) == len_function(expected)
            and all_function(
                item is expected[index]
                for index, item in enumerate_function(actual)
            )
        )

    def exact_namespace(
        namespace: object, expected: tuple[tuple[str, object], ...],
    ) -> bool:
        return bool_class(
            type_class(namespace) is response_namespace_type
            and len_function(namespace) == len_function(expected)
            and all_function(
                namespace.get(name, missing) is value for name, value in expected
            )
        )

    def response_surface_ok() -> bool:
        try:
            pins = shared_pin_box[0]
            if pins is None or any_function(
                module_namespace.get(name, missing) is not expected
                for name, expected in pins
            ):
                return False
            namespace = type_getattribute(response_class, "__dict__")
            return bool_class(
                type_class(response_class) is response_metaclass
                and exact_identities(
                    type_getattribute(response_class, "__bases__"), response_bases,
                )
                and exact_identities(
                    type_getattribute(response_class, "__mro__"), response_mro,
                )
                and type_class(namespace) is response_namespace_type
                and all_function(
                    namespace.get(name, missing) is expected
                    for name, expected in response_pin
                )
            )
        except base_exception:
            return False

    def noncooperative_subclass_hook(cls: type, **_kwargs: object) -> None:
        return None

    shield_hook = classmethod(noncooperative_subclass_hook)
    view_hook = classmethod(noncooperative_subclass_hook)

    def descriptor_matches(descriptor: object, owner: type, name: str) -> bool:
        try:
            return bool_class(
                type_class(descriptor) is getset_descriptor_type
                and descriptor.__name__ == name
                and descriptor.__objclass__ is owner
            )
        except base_exception:
            return False

    def make_publication():
        lock = rlock_type()
        raw_headers: list[tuple[bytes, bytes]] = []
        # sealed, status, body, canonical headers, media type, background/emitted
        state: list[object] = [False, unavailable_status, b"", (), None, False]
        view_box: list[object | None] = [None]

        def seal(status: int, body: bytes) -> None:
            with lock:
                if state[0] is not False:
                    return
                success = bool_class(
                    type_class(status) is int_class and status == 200
                    and type_class(body) is bytes_class
                    and 0 < len_function(body) <= result_max_bytes
                )
                error = bool_class(
                    type_class(status) is int_class and status in allowed_errors
                    and type_class(body) is bytes_class and body == b""
                )
                if success:
                    canonical = (
                        *fixed_prefix,
                        (b"content-length", str_class(len_function(body)).encode("latin-1")),
                        (b"content-type", b"application/json"),
                    )
                    media_type: str | None = "application/json"
                elif error:
                    canonical = (*fixed_prefix, (b"content-length", b"0"))
                    media_type = None
                else:
                    status, body, media_type = unavailable_status, b"", None
                    canonical = (*fixed_prefix, (b"content-length", b"0"))
                raw_headers.extend(canonical)
                state[1], state[2], state[3], state[4], state[0] = (
                    status, body, canonical, media_type, True,
                )

        def owned_get(name: str) -> object:
            with lock:
                if name == "status_code":
                    return state[1]
                if name == "raw_headers":
                    return raw_headers
                if name == "body":
                    return state[2]
                if name == "media_type":
                    return state[4]
                if name == "background":
                    return state[5] if state[5] is not False and state[5] is not True else None
                if name == "headers":
                    return view_box[0]
                if name == "raw":
                    return list_class(raw_headers)
            raise attribute_error(name)

        def owned_set(name: str, value: object) -> None:
            if name != "background":
                raise attribute_error("transport response fields are immutable")
            with lock:
                if state[5] is False:
                    state[5] = value

        def text_headers() -> tuple[tuple[str, str], ...]:
            with lock:
                return tuple_class(
                    (name.decode("latin-1"), value.decode("latin-1"))
                    for name, value in state[3]
                )

        def header_getitem(self: object, key: str) -> str:
            if type_class(key) is not str_class:
                raise key_error(key)
            wanted = key.lower()
            for name, value in reversed_function(text_headers()):
                if name == wanted:
                    return value
            raise key_error(key)

        def header_get(self: object, key: str, default: object = None) -> object:
            try:
                return header_getitem(self, key)
            except key_error:
                return default

        def header_items(self: object) -> list[tuple[str, str]]:
            return list_class(text_headers())

        def header_keys(self: object) -> list[str]:
            return [name for name, _value in text_headers()]

        def header_values(self: object) -> list[str]:
            return [value for _name, value in text_headers()]

        def header_iter(self: object):
            return iter_function(header_keys(self))

        def header_len(self: object) -> int:
            return len_function(text_headers())

        def header_contains(self: object, key: object) -> bool:
            return bool_class(
                type_class(key) is str_class
                and any_function(
                    name == key.lower() for name, _value in text_headers()
                )
            )

        def view_getattribute(self: object, name: str) -> object:
            if name in (
                "status_code", "raw_headers", "body", "media_type", "background",
                "headers", "raw",
            ):
                return owned_get(name)
            return object_getattribute(self, name)

        def view_setattr(self: object, name: str, value: object) -> None:
            owned_set(name, value)

        async def view_call(
            self: object, scope: object, receive: object, send: object,
        ) -> None:
            with lock:
                if state[0] is not True or state[5] is True:
                    return
                status, body = state[1], state[2]
                background = state[5] if state[5] is not False else None
                state[5] = True
            await send({
                "type": "http.response.start", "status": status,
                "headers": raw_headers,
            })
            await send({"type": "http.response.body", "body": body})
            if background is not None:
                await background()

        shield_supplied = {
            "__module__": module_name,
            "__qualname__": shield_name,
            "__init_subclass__": shield_hook,
        }
        shield = type_new(type_class, shield_name, (object_class,), shield_supplied)
        shield_namespace = type_getattribute(shield, "__dict__")
        shield_pin = (
            ("__module__", module_name),
            ("__init_subclass__", shield_hook),
            ("__dict__", shield_namespace.get("__dict__", missing)),
            ("__weakref__", shield_namespace.get("__weakref__", missing)),
            ("__doc__", None),
        )
        shield_ok = bool_class(
            type_class(shield) is type_class
            and type_getattribute(shield, "__name__") == shield_name
            and type_getattribute(shield, "__qualname__") == shield_name
            and exact_identities(type_getattribute(shield, "__bases__"), (object_class,))
            and exact_identities(type_getattribute(shield, "__mro__"), (shield, object_class))
            and exact_namespace(shield_namespace, shield_pin)
            and descriptor_matches(shield_namespace.get("__dict__", missing), shield, "__dict__")
            and descriptor_matches(
                shield_namespace.get("__weakref__", missing), shield, "__weakref__",
            )
        )
        view_supplied = {
            "__module__": module_name,
            "__qualname__": view_name,
            "__getattribute__": view_getattribute,
            "__setattr__": view_setattr,
            "__call__": view_call,
            "__getitem__": header_getitem,
            "get": header_get,
            "items": header_items,
            "keys": header_keys,
            "values": header_values,
            "__iter__": header_iter,
            "__len__": header_len,
            "__contains__": header_contains,
            "__init_subclass__": view_hook,
        }
        view_class = type_new(
            type_class, view_name, (shield, response_class), view_supplied,
        )
        view_namespace = type_getattribute(view_class, "__dict__")
        view_pin = (
            ("__module__", module_name),
            ("__getattribute__", view_getattribute),
            ("__setattr__", view_setattr),
            ("__call__", view_call),
            ("__getitem__", header_getitem),
            ("get", header_get), ("items", header_items), ("keys", header_keys),
            ("values", header_values), ("__iter__", header_iter),
            ("__len__", header_len), ("__contains__", header_contains),
            ("__init_subclass__", view_hook), ("__doc__", None),
        )
        view_ok = bool_class(
            type_class(view_class) is type_class
            and type_getattribute(view_class, "__name__") == view_name
            and type_getattribute(view_class, "__qualname__") == view_name
            and exact_identities(
                type_getattribute(view_class, "__bases__"), (shield, response_class),
            )
            and exact_identities(
                type_getattribute(view_class, "__mro__"),
                (view_class, shield, *response_mro),
            )
            and exact_namespace(view_namespace, view_pin)
        )
        view = object_new(view_class)
        view_box[0] = view
        initial_ok = bool_class(shield_ok and view_ok and response_surface_ok())

        def publication_guard() -> bool:
            try:
                return bool_class(
                    initial_ok and response_surface_ok()
                    and exact_namespace(type_getattribute(shield, "__dict__"), shield_pin)
                    and exact_namespace(type_getattribute(view_class, "__dict__"), view_pin)
                )
            except base_exception:
                return False

        return view, seal, publication_guard

    return make_publication, set_shared_pins


_PUBLICATION_FACTORY, _SET_PUBLICATION_SHARED_PINS = _make_publication_factory(
    module_namespace=_MODULE_NAMESPACE,
    response_class=_TRUSTED_RESPONSE_CLASS,
    response_metaclass=_TRUSTED_RESPONSE_METACLASS,
    response_bases=_TRUSTED_RESPONSE_BASES,
    response_mro=_TRUSTED_RESPONSE_MRO,
    response_namespace_type=_TRUSTED_RESPONSE_NAMESPACE_TYPE,
    response_pin=_TRUSTED_RESPONSE_PIN,
    getset_descriptor_type=_TRUSTED_GETSET_DESCRIPTOR_TYPE,
    type_class=_TRUSTED_TYPE,
    type_new=_TRUSTED_TYPE_NEW,
    type_getattribute=_TRUSTED_TYPE_GETATTRIBUTE,
    object_class=object,
    object_new=_TRUSTED_OBJECT_NEW,
    object_getattribute=_TRUSTED_OBJECT_GETATTRIBUTE,
    rlock_type=_TRUSTED_RLOCK_TYPE,
    result_max_bytes=MCP_CONTEXT_RESULT_MAX_BYTES,
)
_TRUSTED_PUBLICATION_FACTORY = _PUBLICATION_FACTORY


def _make_lifecycle_factory(rlock_type: type, failure_class: type):
    unavailable_status = _STATUS_UNAVAILABLE
    object_factory = object
    base_exception = BaseException

    def make_lifecycle():
        lock = rlock_type()
        # closed, generation, active token, publication token, deferred close
        state: list[object] = [False, 0, None, None, False]

        def begin() -> tuple[object, int]:
            with lock:
                if state[0] or state[2] is not None or state[3] is not None or state[4]:
                    raise failure_class(unavailable_status)
                token = (object_factory(), state[1])
                state[2] = token[0]
                return token

        def checkpoint(token: tuple[object, int]) -> None:
            with lock:
                if (
                    state[0] or state[1] != token[1] or state[2] is not token[0]
                    or (state[3] is not None and state[3] is not token[0])
                    or (state[4] and state[3] is not token[0])
                ):
                    raise failure_class(unavailable_status)

        def reserve(token: tuple[object, int]) -> None:
            checkpoint(token)
            with lock:
                if (
                    state[0] or state[1] != token[1] or state[2] is not token[0]
                    or state[3] is not None or state[4]
                ):
                    raise failure_class(unavailable_status)
                state[3] = token[0]

        def finish(token: tuple[object, int]) -> None:
            with lock:
                if state[3] is token[0]:
                    state[3] = None
                    if state[4] and not state[0]:
                        state[4], state[0] = False, True
                        state[1] += 1
                if state[2] is token[0]:
                    state[2] = None

        def abort(token: tuple[object, int] | None) -> None:
            if token is None:
                return
            try:
                finish(token)
            except base_exception:
                return

        def close() -> None:
            try:
                with lock:
                    if state[0]:
                        return
                    if state[3] is not None:
                        state[4] = True
                        return
                    state[4], state[0] = False, True
                    state[1] += 1
            except base_exception:
                return

        return (begin, checkpoint, reserve, finish, abort, close)

    return make_lifecycle


_LIFECYCLE_FACTORY = _make_lifecycle_factory(
    _TRUSTED_RLOCK_TYPE, _TRUSTED_FAILURE_CLASS,
)
_TRUSTED_LIFECYCLE_FACTORY = _LIFECYCLE_FACTORY


def _make_private_proof_release(
    proof_class: type, proof_lock: object, proofs: dict[int, object],
):
    id_function, type_class, tuple_class, len_function = id, type, tuple, len
    dict_get, dict_pop = dict.get, dict.pop
    base_exception = BaseException

    def release_pending(proof: object) -> None:
        if type_class(proof) is not proof_class:
            return
        try:
            with proof_lock:
                key = id_function(proof)
                row = dict_get(proofs, key)
                if (
                    type_class(row) is tuple_class and len_function(row) >= 2
                    and row[0]() is proof and row[1] == "pending"
                ):
                    dict_pop(proofs, key, None)
        except base_exception:
            return

    return release_pending


_PRIVATE_RELEASE_PROOF = _make_private_proof_release(
    McpContextBearerProof, _TRUSTED_PROOF_LOCK, _TRUSTED_PROOFS,
)


def _make_handle(
    *, module_namespace: dict[str, object],
    runtime_module_namespace: dict[str, object],
    contract_module_namespace: dict[str, object],
    authority_module_namespace: dict[str, object], publication_factory: object,
    private_release_proof: object, request_class: type, request_stream: object,
    response_class: type, runtime_class: type, runtime_handle: object,
    runtime_assert_pins: object, parse: object, identity_class: type,
    identity_pin: tuple[tuple[str, object], ...], identity_fields: tuple[str, ...],
    call_class: type, call_pin: tuple[tuple[str, object], ...], proof_class: type,
    proof_pin: tuple[tuple[str, object], ...], contract_error: type[BaseException],
    runtime_error: type[BaseException], transport_failure: type[BaseException],
    cancelled_error: type[BaseException], timeout_error: type[BaseException],
    timeout_factory: object, body_max_bytes: int, body_timeout_max: float,
    scope_value: str, prepare_method: str, observation_context_enter: object,
    observation_context_exit: object, observation_span_begin: object,
    observation_span_end: object, content_rejection_record: object,
    content_rejection_code_map: tuple[tuple[object, ...], ...],
):
    missing = _MISSING
    type_class = type
    type_getattribute = type.__getattribute__
    object_getattribute = object.__getattribute__
    len_function, bytearray_class, bytes_class = len, bytearray, bytes
    str_class, int_class, bool_class, float_class = str, int, bool, float
    tuple_class, base_exception = tuple, BaseException
    all_function, isinstance_function = all, isinstance
    invalid_status = _STATUS_INVALID
    authentication_status = _STATUS_AUTHENTICATION
    forbidden_status = _STATUS_FORBIDDEN
    timeout_status = _STATUS_TIMEOUT
    budget_status = _STATUS_BUDGET
    unavailable_status = _STATUS_UNAVAILABLE
    transport_box: list[object | None] = [None]
    transport_pin_box: list[tuple[tuple[str, object], ...] | None] = [None]

    def set_transport_pin(
        transport_class: type, pin: tuple[tuple[str, object], ...],
    ) -> None:
        if transport_box[0] is not None:
            raise RuntimeError("transport pin already set")
        transport_box[0], transport_pin_box[0] = transport_class, pin

    def class_matches(cls: type, pin: tuple[tuple[str, object], ...]) -> bool:
        try:
            namespace = type_getattribute(cls, "__dict__")
            return all_function(
                namespace.get(name, missing) is value for name, value in pin
            )
        except base_exception:
            return False

    def identity_exact(identity: object) -> bool:
        if type_class(identity) is not identity_class or not class_matches(
            identity_class, identity_pin,
        ):
            return False
        try:
            token_id, alias, subject, cache, security, scope = (
                object_getattribute(identity, name) for name in identity_fields
            )
        except base_exception:
            return False
        return bool_class(
            type_class(token_id) is str_class and 1 <= len_function(token_id) <= 256
            and type_class(alias) is str_class and 1 <= len_function(alias) <= 256
            and type_class(subject) is str_class and 1 <= len_function(subject) <= 256
            and type_class(cache) is int_class
            and not isinstance_function(cache, bool_class)
            and cache >= 0 and type_class(security) is int_class
            and not isinstance_function(security, bool_class) and security >= 0
            and type_class(scope) is str_class and scope == scope_value
        )

    async def handle(self: object, request: object) -> Response:
        view, seal, publication_guard = publication_factory()
        token: tuple[object, int] | None = None
        proof: object = None
        lifecycle: object = None
        abort_operation: object = None

        def fixed_error(status: int = unavailable_status) -> Response:
            seal(status, b"")
            return view

        try:
            transport_class, transport_pin = transport_box[0], transport_pin_box[0]
            if (
                transport_class is None or transport_pin is None
                or type_class(self) is not transport_class
                or not class_matches(transport_class, transport_pin)
                or not publication_guard()
                or runtime_module_namespace.get("McpContextRequestRuntime", missing)
                is not runtime_class
                or contract_module_namespace.get("parse_mcp_context_request", missing)
                is not parse
                or contract_module_namespace.get("McpContextToolCall", missing)
                is not call_class
                or contract_module_namespace.get("McpContextContractError", missing)
                is not contract_error
                or authority_module_namespace.get("McpContextBearerProof", missing)
                is not proof_class
                or module_namespace.get("Response", missing) is not response_class
            ):
                return fixed_error()
            runtime = object_getattribute(self, "_runtime")
            runtime_pin = object_getattribute(self, "_runtime_pin")
            authenticate = object_getattribute(self, "_authenticate")
            authenticate_pin = object_getattribute(self, "_authenticate_pin")
            capture_proof = object_getattribute(self, "_capture_prepare_proof")
            capture_proof_pin = object_getattribute(self, "_capture_prepare_proof_pin")
            call_body_timeout = object_getattribute(self, "_body_timeout")
            call_body_timeout_pin = object_getattribute(self, "_body_timeout_pin")
            lifecycle = object_getattribute(self, "_lifecycle")
            lifecycle_pin = object_getattribute(self, "_lifecycle_pin")
            if (
                runtime is not runtime_pin or type_class(runtime) is not runtime_class
                or authenticate is not authenticate_pin
                or capture_proof is not capture_proof_pin
                or call_body_timeout is not call_body_timeout_pin
                or type_class(call_body_timeout) is not float_class
                or not 0.0 < call_body_timeout <= body_timeout_max
                or lifecycle is not lifecycle_pin
                or type_class(lifecycle) is not tuple_class
                or len_function(lifecycle) != 6
                or runtime_class.__dict__.get("handle", missing) is not runtime_handle
                or runtime_class.__dict__.get("_assert_pins", missing)
                is not runtime_assert_pins
            ):
                return fixed_error()
            begin, lifecycle_checkpoint, reserve, finish, abort, close = lifecycle
            abort_operation = abort

            def guard() -> None:
                if (
                    object_getattribute(self, "_runtime") is not runtime
                    or object_getattribute(self, "_runtime_pin") is not runtime
                    or object_getattribute(self, "_authenticate") is not authenticate
                    or object_getattribute(self, "_authenticate_pin") is not authenticate
                    or object_getattribute(self, "_capture_prepare_proof") is not capture_proof
                    or object_getattribute(self, "_capture_prepare_proof_pin") is not capture_proof
                    or object_getattribute(self, "_body_timeout") is not call_body_timeout
                    or object_getattribute(self, "_body_timeout_pin") is not call_body_timeout
                    or object_getattribute(self, "_lifecycle") is not lifecycle
                    or object_getattribute(self, "_lifecycle_pin") is not lifecycle
                    or not class_matches(transport_class, transport_pin)
                    or not publication_guard()
                    or runtime_module_namespace.get("McpContextRequestRuntime", missing)
                    is not runtime_class
                    or contract_module_namespace.get("parse_mcp_context_request", missing)
                    is not parse
                    or authority_module_namespace.get("McpContextBearerProof", missing)
                    is not proof_class
                    or runtime_class.__dict__.get("handle", missing) is not runtime_handle
                    or not class_matches(identity_class, identity_pin)
                    or not class_matches(call_class, call_pin)
                    or not class_matches(proof_class, proof_pin)
                ):
                    raise transport_failure(unavailable_status)
                try:
                    runtime_assert_pins(runtime)
                except base_exception:
                    raise transport_failure(unavailable_status) from None
                lifecycle_checkpoint(token)

            if type_class(request) is not request_class:
                raise transport_failure(invalid_status)
            token = begin()
            guard()
            try:
                identity = authenticate(request)
            except cancelled_error:
                raise
            except base_exception:
                raise transport_failure(authentication_status) from None
            guard()
            if not identity_exact(identity):
                raise transport_failure(authentication_status)

            raw_buffer = bytearray_class()
            try:
                async with timeout_factory(call_body_timeout):
                    guard()
                    async for chunk in request_stream(request):
                        guard()
                        if type_class(chunk) is not bytes_class:
                            raise transport_failure(invalid_status)
                        if len_function(raw_buffer) + len_function(chunk) > body_max_bytes:
                            raise transport_failure(budget_status)
                        raw_buffer.extend(chunk)
                    guard()
            except cancelled_error:
                raise
            except timeout_error:
                raise transport_failure(timeout_status) from None
            except transport_failure:
                raise
            except base_exception:
                raise transport_failure(invalid_status) from None
            raw = bytes_class(raw_buffer)

            try:
                call = parse(raw)
            except contract_error as exc:
                status = (
                    budget_status
                    if type_class(exc) is contract_error
                    and object_getattribute(exc, "code") == "budget_exceeded"
                    else invalid_status
                )
                raise transport_failure(status) from None
            except base_exception:
                raise transport_failure(invalid_status) from None
            guard()
            if type_class(call) is not call_class or not class_matches(call_class, call_pin):
                raise transport_failure(invalid_status)
            try:
                method = object_getattribute(call, "method")
            except base_exception:
                raise transport_failure(invalid_status) from None
            if type_class(method) is not str_class:
                raise transport_failure(invalid_status)
            try:
                observation_rpc_id = object_getattribute(call, "request_id")
            except base_exception:
                observation_rpc_id = None

            if method == prepare_method:
                guard()
                try:
                    proof = capture_proof(request)
                except cancelled_error:
                    raise
                except base_exception:
                    raise transport_failure(forbidden_status) from None
                guard()
                if type_class(proof) is not proof_class or not class_matches(
                    proof_class, proof_pin,
                ):
                    raise transport_failure(forbidden_status)

            guard()
            reserve(token)
            try:
                observation_scope = object_getattribute(request, "scope")
            except base_exception:
                observation_scope = None
            observation_binding = observation_context_enter(
                observation_scope, method, observation_rpc_id,
            )
            observation_span = observation_span_begin("transport.runtime_handle")
            try:
                try:
                    frozen = runtime_handle(
                        runtime, raw, identity=identity, bearer_proof=proof,
                    )
                except runtime_error as exc:
                    try:
                        content_rejection_record(exc, method, content_rejection_code_map)
                    except base_exception:
                        pass
                    raise transport_failure(forbidden_status) from None
                except cancelled_error:
                    raise
                except base_exception as exc:
                    try:
                        content_rejection_record(exc, method, content_rejection_code_map)
                    except base_exception:
                        pass
                    raise transport_failure(forbidden_status) from None
            finally:
                observation_span_end(observation_span)
                observation_context_exit(observation_binding)

            proof = None
            seal(200, frozen)
            finish(token)
            token = None
            return view
        except cancelled_error:
            private_release_proof(proof)
            if abort_operation is not None and token is not None:
                abort_operation(token)
                token = None
            raise
        except transport_failure as exc:
            private_release_proof(proof)
            if abort_operation is not None and token is not None:
                abort_operation(token)
                token = None
            arguments = object_getattribute(exc, "args")
            status = arguments[0] if len_function(arguments) == 1 else unavailable_status
            return fixed_error(status)
        except base_exception:
            private_release_proof(proof)
            if abort_operation is not None and token is not None:
                abort_operation(token)
                token = None
            return fixed_error()
        finally:
            if abort_operation is not None and token is not None:
                abort_operation(token)

    return handle, set_transport_pin


_HANDLE, _SET_TRANSPORT_PIN = _make_handle(
    module_namespace=_MODULE_NAMESPACE,
    runtime_module_namespace=_runtime_module.__dict__,
    contract_module_namespace=_contract_module.__dict__,
    authority_module_namespace=_authority_module.__dict__,
    publication_factory=_TRUSTED_PUBLICATION_FACTORY,
    private_release_proof=_PRIVATE_RELEASE_PROOF,
    request_class=_TRUSTED_REQUEST_CLASS,
    request_stream=_TRUSTED_REQUEST_STREAM,
    response_class=_TRUSTED_RESPONSE_CLASS,
    runtime_class=_TRUSTED_RUNTIME_CLASS,
    runtime_handle=_TRUSTED_RUNTIME_HANDLE,
    runtime_assert_pins=_TRUSTED_RUNTIME_ASSERT_PINS,
    parse=_TRUSTED_PARSE,
    identity_class=McpAuthenticatedIdentity,
    identity_pin=_IDENTITY_PIN,
    identity_fields=_IDENTITY_FIELDS,
    call_class=McpContextToolCall,
    call_pin=_CALL_PIN,
    proof_class=McpContextBearerProof,
    proof_pin=_PROOF_PIN,
    contract_error=McpContextContractError,
    runtime_error=McpContextRequestRuntimeError,
    transport_failure=_TRUSTED_FAILURE_CLASS,
    cancelled_error=asyncio.CancelledError,
    timeout_error=TimeoutError,
    timeout_factory=asyncio.timeout,
    body_max_bytes=MCP_CONTEXT_BODY_MAX_BYTES,
    body_timeout_max=10.0,
    scope_value=MCP_CONTEXT_SCOPE,
    prepare_method=_PREPARE_METHOD,
    observation_context_enter=_observation_runtime_context_enter,
    observation_context_exit=_observation_runtime_context_exit,
    observation_span_begin=_observation_span_begin,
    observation_span_end=_observation_span_end,
    content_rejection_record=_record_content_rejection,
    content_rejection_code_map=_CONTENT_REJECTION_CODE_MAP,
)


class McpContextRequestTransport:
    """Bind one exact native runtime to a bounded Starlette request stream."""

    __slots__ = (
        "_runtime", "_runtime_pin", "_authenticate", "_authenticate_pin",
        "_capture_prepare_proof", "_capture_prepare_proof_pin",
        "_body_timeout", "_body_timeout_pin",
        "_lifecycle", "_lifecycle_pin",
    )

    def __init__(
        self,
        runtime: McpContextRequestRuntime,
        authenticate: Callable[[Request], McpAuthenticatedIdentity],
        capture_prepare_proof: Callable[[Request], McpContextBearerProof],
        _lifecycle_factory: object = _TRUSTED_LIFECYCLE_FACTORY,
        _object_setattr: object = _TRUSTED_OBJECT_SETATTR,
    ) -> None:
        if (
            type(runtime) is not _TRUSTED_RUNTIME_CLASS
            or not callable(authenticate)
            or not callable(capture_prepare_proof)
            or type(MCP_CONTEXT_TRANSPORT_BODY_TIMEOUT_SECONDS) is not float
            or not 0.0 < MCP_CONTEXT_TRANSPORT_BODY_TIMEOUT_SECONDS <= 10.0
        ):
            raise ValueError("invalid context request transport")
        body_timeout = MCP_CONTEXT_TRANSPORT_BODY_TIMEOUT_SECONDS
        lifecycle = _lifecycle_factory()
        _object_setattr(self, "_runtime", runtime)
        _object_setattr(self, "_runtime_pin", runtime)
        _object_setattr(self, "_authenticate", authenticate)
        _object_setattr(self, "_authenticate_pin", authenticate)
        _object_setattr(self, "_capture_prepare_proof", capture_prepare_proof)
        _object_setattr(self, "_capture_prepare_proof_pin", capture_prepare_proof)
        _object_setattr(self, "_body_timeout", body_timeout)
        _object_setattr(self, "_body_timeout_pin", body_timeout)
        _object_setattr(self, "_lifecycle", lifecycle)
        _object_setattr(self, "_lifecycle_pin", lifecycle)

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("context request transport cannot be subclassed")

    def __repr__(self) -> str:
        return "McpContextRequestTransport(<redacted>)"

    handle = _HANDLE

    def close(
        self, _object_getattribute: object = _TRUSTED_OBJECT_GETATTRIBUTE,
        _base_exception: type[BaseException] = BaseException,
    ) -> None:
        """Fence this adapter without closing its borrowed runtime or graph."""
        try:
            lifecycle = _object_getattribute(self, "_lifecycle")
            if lifecycle is _object_getattribute(self, "_lifecycle_pin"):
                lifecycle[5]()
        except _base_exception:
            return


_TRANSPORT_PIN = _class_pin(
    McpContextRequestTransport,
    (
        "__init__", "__init_subclass__", "__repr__", "handle", "close",
        *McpContextRequestTransport.__slots__,
    ),
)
_SET_TRANSPORT_PIN(McpContextRequestTransport, _TRANSPORT_PIN)

_PUBLICATION_SHARED_PINS = (
    ("Response", _TRUSTED_RESPONSE_CLASS),
    ("_TRUSTED_RESPONSE_CLASS", _TRUSTED_RESPONSE_CLASS),
    ("_TRUSTED_RESPONSE_METACLASS", _TRUSTED_RESPONSE_METACLASS),
    ("_TRUSTED_RESPONSE_BASES", _TRUSTED_RESPONSE_BASES),
    ("_TRUSTED_RESPONSE_MRO", _TRUSTED_RESPONSE_MRO),
    ("_TRUSTED_RESPONSE_NAMESPACE_TYPE", _TRUSTED_RESPONSE_NAMESPACE_TYPE),
    ("_TRUSTED_RESPONSE_DICT_DESCRIPTOR", _TRUSTED_RESPONSE_DICT_DESCRIPTOR),
    ("_TRUSTED_GETSET_DESCRIPTOR_TYPE", _TRUSTED_GETSET_DESCRIPTOR_TYPE),
    ("_TRUSTED_RESPONSE_PIN", _TRUSTED_RESPONSE_PIN),
    ("_TRUSTED_TYPE", _TRUSTED_TYPE),
    ("_TRUSTED_TYPE_NEW", _TRUSTED_TYPE_NEW),
    ("_TRUSTED_TYPE_GETATTRIBUTE", _TRUSTED_TYPE_GETATTRIBUTE),
    ("_TRUSTED_OBJECT_NEW", _TRUSTED_OBJECT_NEW),
    ("_TRUSTED_OBJECT_GETATTRIBUTE", _TRUSTED_OBJECT_GETATTRIBUTE),
    ("_TRUSTED_RLOCK_TYPE", _TRUSTED_RLOCK_TYPE),
    ("_TRUSTED_REQUEST_CLASS", _TRUSTED_REQUEST_CLASS),
    ("_TRUSTED_REQUEST_STREAM", _TRUSTED_REQUEST_STREAM),
    ("_TRUSTED_RUNTIME_CLASS", _TRUSTED_RUNTIME_CLASS),
    ("_TRUSTED_RUNTIME_HANDLE", _TRUSTED_RUNTIME_HANDLE),
    ("_TRUSTED_RUNTIME_ASSERT_PINS", _TRUSTED_RUNTIME_ASSERT_PINS),
    ("_TRUSTED_PARSE", _TRUSTED_PARSE),
    ("_TRUSTED_RELEASE_PROOF", _TRUSTED_RELEASE_PROOF),
    ("_TRUSTED_PROOF_LOCK", _TRUSTED_PROOF_LOCK),
    ("_TRUSTED_PROOFS", _TRUSTED_PROOFS),
    ("_TRUSTED_FAILURE_CLASS", _TRUSTED_FAILURE_CLASS),
    ("_PUBLICATION_FACTORY", _PUBLICATION_FACTORY),
    ("_TRUSTED_PUBLICATION_FACTORY", _TRUSTED_PUBLICATION_FACTORY),
    ("_LIFECYCLE_FACTORY", _LIFECYCLE_FACTORY),
    ("_TRUSTED_LIFECYCLE_FACTORY", _TRUSTED_LIFECYCLE_FACTORY),
    ("_PRIVATE_RELEASE_PROOF", _PRIVATE_RELEASE_PROOF),
)
_SET_PUBLICATION_SHARED_PINS(_PUBLICATION_SHARED_PINS)
del _SET_PUBLICATION_SHARED_PINS, _SET_TRANSPORT_PIN, _HANDLE

__all__ = [
    "MCP_CONTEXT_TRANSPORT_BODY_TIMEOUT_SECONDS",
    "McpContextRequestTransport",
]
