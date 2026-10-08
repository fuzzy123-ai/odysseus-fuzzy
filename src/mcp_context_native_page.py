"""Inactive detached publication for consented Forge and Knowledge pages."""
from __future__ import annotations

import base64
import hashlib
import threading
import weakref

import src.forge_context_page as _forge_module
import src.harbour_knowledge_active_runtime as _knowledge_module
import src.mcp_context_contract as _contract_module
from src.mcp_context_contract import MCP_CONTEXT_RESULT_MAX_BYTES, encode_context_jsonrpc_response
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationService,
    Permit,
    VerifiedNativeCapture,
    _NativePermitRejectionCapsule,
)

MCP_FORGE_PAGE_SCHEMA = "odysseus.mcp.context_forge_page.v1"
MCP_KNOWLEDGE_PAGE_SCHEMA = "odysseus.mcp.context_knowledge_page.v1"
MCP_NATIVE_PAGE_ERROR = "native_page_unavailable"
_MAX_JSON_INT = (1 << 53) - 1
_TRUSTED_B64ENCODE = base64.b64encode
_TRUSTED_B64DECODE = base64.b64decode
_TRUSTED_ENCODER = encode_context_jsonrpc_response
_TRUSTED_SERVICE_CLASS = McpPrivateContextConfirmationService
_TRUSTED_PERMIT_CLASS = Permit
_TRUSTED_CAPTURE_CLASS = VerifiedNativeCapture
_TRUSTED_EXECUTE = McpPrivateContextConfirmationService._execute_native_page
_TRUSTED_CONSUME = McpPrivateContextConfirmationService._consume_native_completion
_TRUSTED_REJECT_NATIVE_PERMIT = McpPrivateContextConfirmationService._reject_native_permit
_TRUSTED_RESERVE = McpPrivateContextConfirmationService._reserve_permit
_TRUSTED_SCRUB = McpPrivateContextConfirmationService._scrub
_TRUSTED_REJECT_REENTRY = McpPrivateContextConfirmationService._reject_authority_reentry
_TRUSTED_RECIPIENT = McpPrivateContextConfirmationService._recipient
_TRUSTED_RECIPIENT_DESCRIPTOR = McpPrivateContextConfirmationService.__dict__["_recipient"]
_TRUSTED_TERMINATE = McpPrivateContextConfirmationService._terminate
_TRUSTED_SERVICE_GETATTRIBUTE_SLOT = McpPrivateContextConfirmationService.__dict__.get("__getattribute__")
_TRUSTED_SERVICE_GETATTRIBUTE = McpPrivateContextConfirmationService.__getattribute__
_TRUSTED_SERVICE_DICT_DESCRIPTOR = McpPrivateContextConfirmationService.__dict__["__dict__"]
_TRUSTED_SERVICE_DICT_GET = type(_TRUSTED_SERVICE_DICT_DESCRIPTOR).__get__
_TRUSTED_CAPSULE_CLASS = _NativePermitRejectionCapsule
_TRUSTED_PERMIT_CAPSULE_REF_SLOT = Permit.__dict__["_Permit__native_rejection_capsule_ref"]
_TRUSTED_MEMBER_GET = type(_TRUSTED_PERMIT_CAPSULE_REF_SLOT).__get__
_TRUSTED_WEAKREF_TYPE = weakref.ReferenceType
_TRUSTED_WEAKREF_CALL = weakref.ReferenceType.__call__
_TRUSTED_RLOCK_TYPE = type(threading.RLock())
_TRUSTED_LOCAL_TYPE = type(threading.local())
_TRUSTED_CAPSULE_SLOT_NAMES = (
    "service_ref", "lock", "entries", "permits", "exchanges", "completions",
    "native_completions", "authority", "execution_local",
)
_TRUSTED_CAPSULE_SLOTS = tuple(
    _TRUSTED_CAPSULE_CLASS.__dict__[name]
    for name in _TRUSTED_CAPSULE_SLOT_NAMES
)
_TRUSTED_FORGE_PAGE = _forge_module.ForgeContextPage
_TRUSTED_FORGE_BYTEFORM = _forge_module.ForgeContextPage.__dict__["byteform"]
_TRUSTED_KNOWLEDGE_PAGE = _knowledge_module.KnowledgeContextPage
_TRUSTED_KNOWLEDGE_BYTEFORM = _knowledge_module.KnowledgeContextPage.__dict__["byteform"]

_PAGE_FIELDS = (
    "payload_bytes", "total", "offset", "end", "whole_sha256", "page_sha256", "manifest_digest",
)

def _value_class_state(value_class: type) -> tuple[object, ...]:
    namespace = value_class.__dict__
    return (
        namespace.get("__init__"), namespace.get("__new__"), value_class.__new__,
        namespace.get("__getattribute__"), value_class.__getattribute__,
        namespace.get("__post_init__"), namespace.get("__setattr__"),
        tuple(namespace.get(name) for name in _PAGE_FIELDS),
    )

_TRUSTED_VALUE_CLASS_STATE = _value_class_state
_TRUSTED_FORGE_PAGE_STATE = _TRUSTED_VALUE_CLASS_STATE(_TRUSTED_FORGE_PAGE)
_TRUSTED_KNOWLEDGE_PAGE_STATE = _TRUSTED_VALUE_CLASS_STATE(_TRUSTED_KNOWLEDGE_PAGE)

def _value_class_matches(value_class: type, expected: tuple[object, ...],
                         _state: object = _TRUSTED_VALUE_CLASS_STATE) -> bool:
    current = _state(value_class)
    return (all(value is expected_value for value, expected_value in zip(current[:-1], expected[:-1]))
            and all(value is expected_value
                    for value, expected_value in zip(current[-1], expected[-1])))

_TRUSTED_VALUE_CLASS_MATCHES = _value_class_matches


class McpNativePagePublicationError(ValueError):
    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = MCP_NATIVE_PAGE_ERROR
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpNativePagePublicationError(code={self.code!r})"


def _fail() -> None:
    raise McpNativePagePublicationError() from None


def _original_service_state(service: object, permit: object) -> dict[str, object]:
    try:
        if type(service) is not _TRUSTED_SERVICE_CLASS or type(permit) is not _TRUSTED_PERMIT_CLASS:
            _fail()
        capsule_ref = _TRUSTED_MEMBER_GET(
            _TRUSTED_PERMIT_CAPSULE_REF_SLOT, permit, _TRUSTED_PERMIT_CLASS,
        )
        if type(capsule_ref) is not _TRUSTED_WEAKREF_TYPE:
            _fail()
        capsule = _TRUSTED_WEAKREF_CALL(capsule_ref)
        if type(capsule) is not _TRUSTED_CAPSULE_CLASS:
            _fail()
        (service_ref, lock, entries, permits, exchanges, completions, native_completions,
         authority, execution_local) = tuple(
            _TRUSTED_MEMBER_GET(slot, capsule, _TRUSTED_CAPSULE_CLASS)
            for slot in _TRUSTED_CAPSULE_SLOTS
        )
        if (type(service_ref) is not _TRUSTED_WEAKREF_TYPE
                or _TRUSTED_WEAKREF_CALL(service_ref) is not service
                or type(lock) is not _TRUSTED_RLOCK_TYPE
                or type(entries) is not dict or type(permits) is not dict
                or type(exchanges) is not dict or type(completions) is not dict
                or type(native_completions) is not dict
                or type(execution_local) is not _TRUSTED_LOCAL_TYPE):
            _fail()
        state = _TRUSTED_SERVICE_DICT_GET(
            _TRUSTED_SERVICE_DICT_DESCRIPTOR, service, _TRUSTED_SERVICE_CLASS,
        )
        if (type(state) is not dict
                or dict.get(state, "_native_rejection_capsule") is not capsule
                or dict.get(state, "_lock") is not lock
                or dict.get(state, "_entries") is not entries
                or dict.get(state, "_permits") is not permits
                or dict.get(state, "_exchanges") is not exchanges
                or dict.get(state, "_completions") is not completions
                or dict.get(state, "_native_completions") is not native_completions
                or dict.get(state, "_authority") is not authority
                or dict.get(state, "_execution_local") is not execution_local):
            _fail()
        return state
    except McpNativePagePublicationError:
        raise
    except BaseException:
        _fail()


_TRUSTED_ORIGINAL_SERVICE_STATE = _original_service_state


def _sha256(value: object) -> bool:
    return (type(value) is str and len(value) == 71 and value.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in value[7:]))


def _raw_sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _page_values(page: object, source: str) -> tuple[object, ...]:
    expected_type = _TRUSTED_FORGE_PAGE if source == "forge" else _TRUSTED_KNOWLEDGE_PAGE
    expected_byteform = "git_blob" if source == "forge" else "knowledge_payload"
    try:
        values = (page.payload_bytes, page.total, page.offset, page.end, page.whole_sha256,
                  page.page_sha256, page.manifest_digest, page.byteform)
    except BaseException:
        _fail()
    payload, total, offset, end, whole, page_sha, manifest, byteform = values
    if (type(page) is not expected_type or type(payload) is not bytes or len(payload) > 16_384
            or type(total) is not int or type(offset) is not int or type(end) is not int
            or not 0 <= offset <= end <= total <= _MAX_JSON_INT or len(payload) != end - offset
            or not _sha256(whole) or not _sha256(page_sha)
            or page_sha != "sha256:" + hashlib.sha256(payload).hexdigest()
            or (not _raw_sha256(manifest) if source == "forge" else not _sha256(manifest))
            or type(byteform) is not str or byteform != expected_byteform):
        _fail()
    return values


def _result_for_values(source: str, values: tuple[object, ...], target_index: int) -> dict[str, object]:
    payload, total, offset, end, whole, page_sha, manifest, byteform = values
    if base64.b64encode is not _TRUSTED_B64ENCODE or base64.b64decode is not _TRUSTED_B64DECODE:
        _fail()
    try:
        content_bytes = _TRUSTED_B64ENCODE(payload)
        content_b64 = content_bytes.decode("ascii")
        decoded = _TRUSTED_B64DECODE(content_b64, validate=True)
    except BaseException:
        _fail()
    if (type(content_bytes) is not bytes or type(content_b64) is not str or type(decoded) is not bytes
            or decoded != payload or _TRUSTED_B64ENCODE(decoded) != content_bytes):
        _fail()
    return {
        "schema": MCP_FORGE_PAGE_SCHEMA if source == "forge" else MCP_KNOWLEDGE_PAGE_SCHEMA,
        "target_index": target_index,
        "offset": offset,
        "end": end,
        "total_bytes": total,
        "whole_sha256": whole,
        "page_sha256": page_sha,
        "manifest_digest": manifest,
        "byteform": byteform,
        "content_b64": content_b64,
    }


_TRUSTED_NORMALIZER = _page_values
_TRUSTED_RESULT_BUILDER = _result_for_values


def _reject_preissued_permit(
    service: object,
    permit: object,
    _service_class: type = _TRUSTED_SERVICE_CLASS,
    _permit_class: type = _TRUSTED_PERMIT_CLASS,
    _reject: object = _TRUSTED_REJECT_NATIVE_PERMIT,
) -> None:
    """Best-effort rejection of one authenticated, still-live preissued permit."""
    try:
        if type(service) is _service_class and type(permit) is _permit_class:
            _reject(service, permit)
    except BaseException:
        return


_TRUSTED_REJECT_PREISSUED_PERMIT = _reject_preissued_permit


def _assert_pins(service: McpPrivateContextConfirmationService, permit: Permit) -> None:
    service_namespace = _TRUSTED_SERVICE_CLASS.__dict__
    if (globals().get("McpPrivateContextConfirmationService") is not _TRUSTED_SERVICE_CLASS
            or globals().get("Permit") is not _TRUSTED_PERMIT_CLASS
            or globals().get("VerifiedNativeCapture") is not _TRUSTED_CAPTURE_CLASS
            or globals().get("_NativePermitRejectionCapsule") is not _TRUSTED_CAPSULE_CLASS
            or service_namespace.get("__dict__") is not _TRUSTED_SERVICE_DICT_DESCRIPTOR
            or _TRUSTED_PERMIT_CLASS.__dict__.get("_Permit__native_rejection_capsule_ref")
                is not _TRUSTED_PERMIT_CAPSULE_REF_SLOT
            or any(_TRUSTED_CAPSULE_CLASS.__dict__.get(name) is not slot
                   for name, slot in zip(_TRUSTED_CAPSULE_SLOT_NAMES, _TRUSTED_CAPSULE_SLOTS))
            or service_namespace.get("__getattribute__") is not _TRUSTED_SERVICE_GETATTRIBUTE_SLOT
            or type.__getattribute__(
                _TRUSTED_SERVICE_CLASS, "__getattribute__",
            ) is not _TRUSTED_SERVICE_GETATTRIBUTE
            or service_namespace.get("_execute_native_page") is not _TRUSTED_EXECUTE
            or service_namespace.get("_consume_native_completion") is not _TRUSTED_CONSUME
            or service_namespace.get("_reject_native_permit") is not _TRUSTED_REJECT_NATIVE_PERMIT
            or service_namespace.get("_reserve_permit") is not _TRUSTED_RESERVE
            or service_namespace.get("_scrub") is not _TRUSTED_SCRUB
            or service_namespace.get("_reject_authority_reentry") is not _TRUSTED_REJECT_REENTRY
            or service_namespace.get("_recipient") is not _TRUSTED_RECIPIENT_DESCRIPTOR
            or service_namespace.get("_terminate") is not _TRUSTED_TERMINATE
            or _forge_module.ForgeContextPage is not _TRUSTED_FORGE_PAGE
            or globals().get("_value_class_state") is not _TRUSTED_VALUE_CLASS_STATE
            or globals().get("_value_class_matches") is not _TRUSTED_VALUE_CLASS_MATCHES
            or not _TRUSTED_VALUE_CLASS_MATCHES(_TRUSTED_FORGE_PAGE, _TRUSTED_FORGE_PAGE_STATE)
            or _forge_module.ForgeContextPage.__dict__.get("byteform") is not _TRUSTED_FORGE_BYTEFORM
            or _knowledge_module.KnowledgeContextPage is not _TRUSTED_KNOWLEDGE_PAGE
            or not _TRUSTED_VALUE_CLASS_MATCHES(
                _TRUSTED_KNOWLEDGE_PAGE, _TRUSTED_KNOWLEDGE_PAGE_STATE,
            )
            or _knowledge_module.KnowledgeContextPage.__dict__.get("byteform") is not _TRUSTED_KNOWLEDGE_BYTEFORM
            or globals().get("_page_values") is not _TRUSTED_NORMALIZER
            or globals().get("_result_for_values") is not _TRUSTED_RESULT_BUILDER
            or base64.b64encode is not _TRUSTED_B64ENCODE or base64.b64decode is not _TRUSTED_B64DECODE
            or _contract_module.encode_context_jsonrpc_response is not _TRUSTED_ENCODER
            or globals().get("encode_context_jsonrpc_response") is not _TRUSTED_ENCODER
            or globals().get("_original_service_state") is not _TRUSTED_ORIGINAL_SERVICE_STATE
            or globals().get("_reject_preissued_permit") is not _TRUSTED_REJECT_PREISSUED_PERMIT):
        _fail()
    instance_values = _TRUSTED_ORIGINAL_SERVICE_STATE(service, permit)
    if any(name in instance_values for name in (
        "_execute_native_page", "_consume_native_completion", "_reserve_permit", "_scrub",
        "_reject_native_permit", "_reject_authority_reentry", "_recipient", "_terminate",
    )):
        _fail()


def _validate_request(service: object, permit: object, source: str, request_id: object,
                      target_index: object, offset: object) -> None:
    if (type(service) is not _TRUSTED_SERVICE_CLASS or type(permit) is not _TRUSTED_PERMIT_CLASS
            or type(source) is not str or source not in {"forge", "knowledge"}
            or type(request_id) not in {str, int} or isinstance(request_id, bool)
            or type(target_index) is not int or not 0 <= target_index <= _MAX_JSON_INT
            or type(offset) is not int or not 0 <= offset <= _MAX_JSON_INT):
        _fail()
    if type(request_id) is str:
        if (not request_id or len(request_id) > 256 or not request_id.isascii()
                or any(ord(char) < 0x20 or ord(char) == 0x7f for char in request_id)):
            _fail()
    elif not 0 <= request_id <= _MAX_JSON_INT:
        _fail()
    _assert_pins(service, permit)


def _publish(service: McpPrivateContextConfirmationService, permit: Permit, *, source: str,
             request_id: str | int, target_index: int, offset: int,
             _reject: object = _TRUSTED_REJECT_PREISSUED_PERMIT) -> bytes:
    try:
        _validate_request(service, permit, source, request_id, target_index, offset)
    except BaseException:
        _reject(service, permit)
        _fail()
    frozen_page: object | None = None
    frozen_values: tuple[object, ...] | None = None
    frozen_encoded: bytes | None = None

    def encode_detached(page: object) -> None:
        nonlocal frozen_page, frozen_values, frozen_encoded
        if frozen_page is not None or frozen_values is not None or frozen_encoded is not None:
            _fail()
        _assert_pins(service, permit)
        values = _TRUSTED_NORMALIZER(page, source)
        try:
            trusted_result = _TRUSTED_RESULT_BUILDER(source, values, target_index)
            expected = _TRUSTED_ENCODER(request_id, trusted_result)
            replaceable_values = _page_values(page, source)
            replaceable_result = _result_for_values(source, replaceable_values, target_index)
            encoded = encode_context_jsonrpc_response(request_id, replaceable_result)
        except BaseException:
            _fail()
        if (type(expected) is not bytes or type(encoded) is not bytes or not encoded
                or len(encoded) > MCP_CONTEXT_RESULT_MAX_BYTES or encoded != expected
                or replaceable_values != values or _TRUSTED_NORMALIZER(page, source) != values):
            _fail()
        frozen_page, frozen_values, frozen_encoded = page, values, bytes(expected)

    capture: object | None = None
    try:
        capture = _TRUSTED_EXECUTE(service, permit, source=source, target_index=target_index,
                                   offset=offset, validate_detached=encode_detached)
        _assert_pins(service, permit)
        if (type(capture) is not _TRUSTED_CAPTURE_CLASS or capture.source != source
                or frozen_page is None or capture.page is not frozen_page
                or frozen_values is None or frozen_encoded is None
                or _TRUSTED_NORMALIZER(capture.page, source) != frozen_values
                or type(frozen_encoded) is not bytes or not frozen_encoded
                or len(frozen_encoded) > MCP_CONTEXT_RESULT_MAX_BYTES):
            _fail()
        _assert_pins(service, permit)
    except BaseException:
        if type(capture) is _TRUSTED_CAPTURE_CLASS:
            try: _TRUSTED_CONSUME(service, capture)
            except BaseException: pass
        else:
            _reject(service, permit)
        _fail()
    try:
        _TRUSTED_CONSUME(service, capture)
    except BaseException:
        _fail()
    return frozen_encoded


def publish_forge_page(service: McpPrivateContextConfirmationService, permit: Permit, *,
                       request_id: str | int, target_index: int, offset: int) -> bytes:
    return _publish(service, permit, source="forge", request_id=request_id,
                    target_index=target_index, offset=offset)


def publish_knowledge_page(service: McpPrivateContextConfirmationService, permit: Permit, *,
                           request_id: str | int, target_index: int, offset: int) -> bytes:
    return _publish(service, permit, source="knowledge", request_id=request_id,
                    target_index=target_index, offset=offset)


__all__ = [
    "MCP_FORGE_PAGE_SCHEMA", "MCP_KNOWLEDGE_PAGE_SCHEMA", "MCP_NATIVE_PAGE_ERROR",
    "McpNativePagePublicationError", "publish_forge_page", "publish_knowledge_page",
]
