"""Inactive authenticated JSON-RPC runtime for MCP context-pack requests.

The runtime owns only the bindings, read leases, and request records that it
creates.  Its authority, confirmation service, producer, cursor authority, and
pack coordinator are one borrowed graph and are never closed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import re
import secrets
import threading
from types import FunctionType, MappingProxyType, MemberDescriptorType
from typing import Callable
from weakref import ReferenceType

import src.mcp_context_authority as _authority_module
import src.mcp_context_contract as _contract_module
import src.mcp_context_pack_coordinator as _coordinator_module
import src.mcp_private_context_confirmation as _confirmation_module
import src.private_context_confirmation_app as _selection_module
from src.context_manifest_producer import CombinedContextManifestProducer
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextBearerProof,
    McpContextBinding,
    McpContextReadLease,
    release_mcp_bearer_proof,
)
from src.mcp_context_contract import (
    MCP_CONTEXT_PACK_SCHEMA,
    MCP_CONTEXT_RESULT_MAX_BYTES,
    MCP_CONTEXT_SCOPE,
    McpContextToolCall,
    encode_context_jsonrpc_response,
    parse_mcp_context_request,
)
from src.mcp_context_cursor_authority import McpContextCursorAuthority
from src.mcp_context_pack_coordinator import McpContextPackCoordinator
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationService,
    PreparedRequest,
)
from src.private_context_confirmation_app import (
    ContextSelection,
    ForgeSelectionLocator,
    SourceSelection,
)


MCP_CONTEXT_REQUEST_ERROR = "context_request_unavailable"
_LEASE_TTL_SECONDS = 300
_MAX_RECORDS = 64
_MAX_OWNER_RECORDS = 8
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 4096
_LEASE_ID_RE = re.compile(r"l\.[A-Za-z0-9_-]{43}")
_REQUEST_ID_RE = re.compile(r"r\.[A-Za-z0-9_-]{43}")
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))
_MISSING = object()
_ORIGINAL_NATIVE_LEASE_STATE = _authority_module._NativeLeaseState
_ORIGINAL_NATIVE_LEASE_CAPABILITY = _authority_module._NativeLeaseCapability
_ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY = (
    _authority_module._acquire_native_lease_capability
)
_ORIGINAL_CONTENT_PUBLICATION_VERIFIER = (
    _authority_module._McpContentPublicationVerifier
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION = (
    _authority_module._open_mcp_content_publication
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_CODE = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION.__code__
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_DEFAULTS = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION.__defaults__
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION.__wrapped__
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_CODE = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED.__code__
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_DEFAULTS = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED.__defaults__
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_CLOSURE = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED.__closure__
)
_ORIGINAL_OPEN_CONTENT_PUBLICATION_CLOSURE = (
    _ORIGINAL_OPEN_CONTENT_PUBLICATION.__closure__
)
_ORIGINAL_CONTENT_PUBLICATION_CONTEXT = (
    _authority_module._CONTENT_PUBLICATION_CONTEXT
)
_ORIGINAL_CONTENT_PUBLICATION_OWNER_CONTEXT = (
    _authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT
)
_ORIGINAL_CONTENT_PUBLICATION_ISSUER = (
    _authority_module._CONTENT_PUBLICATION_ISSUER
)
_ORIGINAL_CONTENT_PUBLICATION_REGISTRY = (
    _authority_module._CONTENT_PUBLICATIONS
)
_ORIGINAL_CONTENT_PUBLICATION_LOCK = (
    _authority_module._CONTENT_PUBLICATION_LOCK
)
_ORIGINAL_CONTENT_PUBLICATION_CAP = (
    _authority_module._CONTENT_PUBLICATION_CAP
)
_ORIGINAL_CONTENT_PUBLICATION_STATES = tuple(
    _authority_module.__dict__[name]
    for name in (
        "_CONTENT_PUBLICATION_ENTRY", "_CONTENT_PUBLICATION_REUSE",
        "_CONTENT_PUBLICATION_FINAL", "_CONTENT_PUBLICATION_POISONED",
        "_CONTENT_PUBLICATION_CLOSED",
    )
)
_ORIGINAL_CONTENT_PUBLICATION_HELPERS = tuple(
    (
        name,
        helper,
        helper.__code__,
        helper.__defaults__,
        helper.__closure__,
    )
    for name in (
        "_poison_registered_content_publication",
        "_poison_active_registered_content_publication",
        "_register_content_publication",
        "_content_publication_control_snapshot",
        "_seed_content_publication_hash",
        "_finalize_content_publication",
        "_close_content_publication_control",
        "_content_publication_dispatch",
        "_content_publication_field",
        "_content_publication_registry_row",
        "_content_publication_anchor_fields_match",
        "_poison_content_publication",
        "_poison_active_content_publication",
        "_record_content_publication_hash",
        "_content_publication_proof_mode",
        "_clear_content_publication",
    )
    for helper in (_authority_module.__dict__[name],)
)
_ORIGINAL_AUTHORITY_FAIL = _authority_module._fail
_ORIGINAL_AUTHORITY_FAIL_CODE = _ORIGINAL_AUTHORITY_FAIL.__code__
_ORIGINAL_AUTHORITY_FAIL_DEFAULTS = _ORIGINAL_AUTHORITY_FAIL.__defaults__
_ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_DEFAULTS = (
    _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY.__defaults__
)
_ORIGINAL_NATIVE_LEASE_STATE_READER = (
    _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_DEFAULTS[0]
)
_ORIGINAL_NATIVE_LEASE_STATE_READER_CODE = (
    _ORIGINAL_NATIVE_LEASE_STATE_READER.__code__
)
_ORIGINAL_NATIVE_LEASE_STATE_READER_DEFAULTS = (
    _ORIGINAL_NATIVE_LEASE_STATE_READER.__defaults__
)
_ORIGINAL_NATIVE_LEASE_STATE_SLOT = (
    _ORIGINAL_NATIVE_LEASE_STATE_READER_DEFAULTS[0]
)
_ORIGINAL_NATIVE_LEASE_STATE_SLOT_CLASS = (
    _ORIGINAL_NATIVE_LEASE_STATE_SLOT.__objclass__
)
_ORIGINAL_NATIVE_LEASE_TERMINAL_REASON = (
    _ORIGINAL_NATIVE_LEASE_CAPABILITY.terminal_reason
)
_ORIGINAL_NATIVE_LEASE_CANCEL = _ORIGINAL_NATIVE_LEASE_CAPABILITY.__dict__["cancel"]
_ORIGINAL_NATIVE_LEASE_CANCEL_CODE = _ORIGINAL_NATIVE_LEASE_CANCEL.__code__
_ORIGINAL_NATIVE_LEASE_CANCEL_DEFAULTS = _ORIGINAL_NATIVE_LEASE_CANCEL.__defaults__
_ORIGINAL_NATIVE_LEASE_CANCELLED = _authority_module._NATIVE_LEASE_CANCELLED
_ORIGINAL_NATIVE_LEASE_CAPABILITY_INIT = (
    _ORIGINAL_NATIVE_LEASE_CAPABILITY.__dict__["__init__"]
)
_ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_CODE = (
    _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY.__code__
)
_ORIGINAL_NATIVE_LEASE_CAPABILITY_INIT_CODE = (
    _ORIGINAL_NATIVE_LEASE_CAPABILITY_INIT.__code__
)
_ORIGINAL_NATIVE_LEASE_TERMINAL_REASON_CODE = (
    _ORIGINAL_NATIVE_LEASE_TERMINAL_REASON.__code__
)
_ORIGINAL_NATIVE_LEASE_STATE_PIN = tuple(
    (name, _ORIGINAL_NATIVE_LEASE_STATE.__dict__.get(name, _MISSING))
    for name in (
        "lock", "leases", "cancelled_leases", "__getattribute__", "__getattr__",
    )
)
_ORIGINAL_NATIVE_LEASE_STATE_SLOT_PIN = tuple(
    (name, _ORIGINAL_NATIVE_LEASE_STATE_SLOT_CLASS.__dict__.get(name, _MISSING))
    for name in (
        "_NativeLeaseStateSlot__native_lease_state",
        "__getattribute__",
        "__getattr__",
    )
)
_ORIGINAL_NATIVE_LEASE_CAPABILITY_PIN = tuple(
    (name, _ORIGINAL_NATIVE_LEASE_CAPABILITY.__dict__.get(name, _MISSING))
    for name in (
        "state",
        "lease",
        "key",
        "record",
        "terminal_reason",
        "require_live",
        "cancel",
        "__init__",
        "__new__",
        "__getattribute__",
        "__getattr__",
    )
)
_ORIGINAL_CONTENT_PUBLICATION_VERIFIER_PIN = tuple(
    (
        name,
        _ORIGINAL_CONTENT_PUBLICATION_VERIFIER.__dict__.get(name, _MISSING),
    )
    for name in (
        "__init__", "__setattr__", "__init_subclass__", "__copy__",
        "__deepcopy__", "__reduce__", "__reduce_ex__", "__repr__",
        *tuple(_ORIGINAL_CONTENT_PUBLICATION_VERIFIER.__slots__),
    )
)
_ORIGINAL_CONTENT_PUBLICATION_VERIFIER_METHODS = tuple(
    (
        name,
        method,
        method.__code__,
        method.__defaults__,
    )
    for name in (
        "__init__", "__setattr__", "__copy__", "__deepcopy__",
        "__reduce__", "__reduce_ex__", "__repr__",
    )
    for method in (_ORIGINAL_CONTENT_PUBLICATION_VERIFIER.__dict__[name],)
)
_GENUINE_AUTHORITY_CLASS = McpContextAuthority
_ORIGINAL_AUTHORITY_ROW_AND_CHAT = _GENUINE_AUTHORITY_CLASS.__dict__["_row_and_chat"]
_ORIGINAL_AUTHORITY_ROW_AND_CHAT_CODE = _ORIGINAL_AUTHORITY_ROW_AND_CHAT.__code__
_ORIGINAL_AUTHORITY_ROW_AND_CHAT_DEFAULTS = _ORIGINAL_AUTHORITY_ROW_AND_CHAT.__defaults__
_GENUINE_SERVICE_CLASS = McpPrivateContextConfirmationService
_GENUINE_PRODUCER_CLASS = CombinedContextManifestProducer
_GENUINE_CURSOR_CLASS = McpContextCursorAuthority
_GENUINE_COORDINATOR_CLASS = McpContextPackCoordinator
_ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY = (
    _confirmation_module._acquire_request_rejection_capability
)
_ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CODE = (
    _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__code__
)
_ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_DEFAULTS = (
    _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__defaults__
)
_ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CLOSURE = (
    _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__closure__
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT = (
    _confirmation_module._classify_pristine_intent
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT_CODE = (
    _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__code__
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS = (
    _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__defaults__
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE = (
    _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__closure__
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT_KWDEFAULTS = (
    _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__kwdefaults__
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT_GLOBALS = (
    _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__globals__
)
_ORIGINAL_CLASSIFY_PRISTINE_INTENT_NAME = (
    _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__name__
)
_ORIGINAL_NATIVE_FUNCTION_TYPE = FunctionType
_ORIGINAL_PRISTINE_INTENT_SENTINEL = (
    _confirmation_module._PRISTINE_INTENT_SENTINEL
)
_ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS = (
    _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
)
_ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE = (
    _confirmation_module._PRISTINE_INTENT_ORIGIN_SIZE
)
_ORIGINAL_PUBLICATION_CLEANUP_ANCHOR = (
    _confirmation_module._publication_cleanup_anchor
)
_ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_CODE = (
    _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR.__code__
)
_ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_DEFAULTS = (
    _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR.__defaults__
)
_ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_CLOSURE = (
    _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR.__closure__
)
_ORIGINAL_TERMINATE_ANCHORED_PUBLICATION = (
    _confirmation_module._terminate_anchored_publication
)
_ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_CODE = (
    _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION.__code__
)
_ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_DEFAULTS = (
    _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION.__defaults__
)
_ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_CLOSURE = (
    _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION.__closure__
)
_PRODUCER_AUTHORITY_DESCRIPTOR = _GENUINE_PRODUCER_CLASS.__dict__.get(
    "_mcp_authority", _MISSING,
)


class McpContextRequestRuntimeError(ValueError):
    """Fresh, fixed-code failure from the request-runtime boundary."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = MCP_CONTEXT_REQUEST_ERROR
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpContextRequestRuntimeError({MCP_CONTEXT_REQUEST_ERROR!r})"


def _fail() -> None:
    raise McpContextRequestRuntimeError() from None


def _class_pin(cls: type, names: tuple[str, ...]) -> tuple[tuple[str, object], ...]:
    namespace = cls.__dict__
    return tuple((name, namespace.get(name, _MISSING)) for name in names)


def _class_matches(cls: type, pin: tuple[tuple[str, object], ...]) -> bool:
    try:
        namespace = cls.__dict__
        return all(namespace.get(name, _MISSING) is expected for name, expected in pin)
    except BaseException:
        return False


_IDENTITY_FIELDS = (
    "token_id", "owner_alias", "owner_subject_id", "cache_generation",
    "security_action_revision", "scope",
)
_CALL_FIELDS = (
    "method", "request_id", "arguments", "__init__", "__post_init__",
    "__getattribute__",
)
_PREPARED_FIELDS = ("request_id", "private_required", "item_count")
_SELECTION_FIELDS = ("items", "__init__", "__post_init__", "__getattribute__")
_SOURCE_FIELDS = (
    "source", "selection_ref", "revision_ref", "forge_locator", "__init__",
    "__post_init__", "__getattribute__",
)
_LOCATOR_FIELDS = (
    "repo_id", "version_id", "path", "__init__", "__post_init__",
    "__getattribute__",
)
_BINDING_FIELDS = (
    "token_id", "owner_alias", "owner_subject_id", "scope", "cache_generation",
    "security_action_revision", "chat_session_id", "process_epoch", "audience",
    "bearer_proof", "_McpContextBinding__issuer",
    "_McpContextBinding__authority", "_McpContextBinding__proof",
    "_McpContextBinding__token_id", "_McpContextBinding__owner_alias",
    "_McpContextBinding__owner_subject_id", "_McpContextBinding__scope",
    "_McpContextBinding__cache_generation",
    "_McpContextBinding__security_action_revision",
    "_McpContextBinding__chat_session_id",
    "_McpContextBinding__process_epoch", "_McpContextBinding__audience",
    "__getattribute__", "__getattr__",
)
_LEASE_FIELDS = (
    "binding", "request_id", "expires_at", "_McpContextReadLease__issuer",
    "_McpContextReadLease__authority", "_McpContextReadLease__binding",
    "_McpContextReadLease__request_id",
    "_McpContextReadLease__issued_monotonic",
    "_McpContextReadLease__deadline_monotonic",
    "_McpContextReadLease__issued_wall",
    "_McpContextReadLease__deadline_wall", "__getattribute__", "__getattr__",
)
_WRAPPER_FIELDS = (
    "_McpContextBearerProof__native", "_McpContextBearerProof__issuer",
    "__getattribute__", "__getattr__",
)
_NATIVE_PROOF_FIELDS = (
    "_McpBearerProof__raw", "_McpBearerProof__issuer", "__getattribute__",
    "__getattr__", "_is_issued",
)

_IDENTITY_PIN = _class_pin(McpAuthenticatedIdentity, _IDENTITY_FIELDS)
_CALL_PIN = _class_pin(McpContextToolCall, _CALL_FIELDS)
_PREPARED_PIN = _class_pin(PreparedRequest, _PREPARED_FIELDS)
_PREPARED_SLOTS = tuple(
    PreparedRequest.__dict__[name]
    for name in ("request_id", "private_required", "item_count")
)
_SELECTION_PIN = _class_pin(ContextSelection, _SELECTION_FIELDS)
_SOURCE_PIN = _class_pin(SourceSelection, _SOURCE_FIELDS)
_LOCATOR_PIN = _class_pin(ForgeSelectionLocator, _LOCATOR_FIELDS)
_BINDING_PIN = _class_pin(McpContextBinding, _BINDING_FIELDS)
_LEASE_PIN = _class_pin(McpContextReadLease, _LEASE_FIELDS)
_WRAPPER_PIN = _class_pin(McpContextBearerProof, _WRAPPER_FIELDS)
_NATIVE_PROOF_CLASS = _authority_module.McpBearerProof
_NATIVE_PROOF_PIN = _class_pin(_NATIVE_PROOF_CLASS, _NATIVE_PROOF_FIELDS)
_AUTHORITY_PIN = _class_pin(
    _GENUINE_AUTHORITY_CLASS,
    (
        "issue_binding", "recheck_binding", "issue_read_lease", "recheck_lease",
        "release_read_lease", "release_binding", "_row_and_chat",
        "__getattribute__",
    ),
)
_SERVICE_PIN = _class_pin(
    _GENUINE_SERVICE_CLASS,
    ("prepare_intent", "revoke", "__getattribute__"),
)
_CURSOR_PIN = _class_pin(
    _GENUINE_CURSOR_CLASS,
    ("_authority", "__getattribute__"),
)
_COORDINATOR_PIN = _class_pin(
    _GENUINE_COORDINATOR_CLASS,
    (
        "publish_manifest", "publish_content", "cancel", "__getattribute__",
        "_authority", "_service", "_producer", "_cursor",
    ),
)

_TRUSTED_PARSE = parse_mcp_context_request
_TRUSTED_ENCODE = encode_context_jsonrpc_response
_TRUSTED_RELEASE_PROOF = release_mcp_bearer_proof
_TRUSTED_ISSUE_BINDING = McpContextAuthority.issue_binding
_TRUSTED_RECHECK_BINDING = McpContextAuthority.recheck_binding
_TRUSTED_ISSUE_LEASE = McpContextAuthority.issue_read_lease
_TRUSTED_RECHECK_LEASE = McpContextAuthority.recheck_lease
_TRUSTED_RELEASE_LEASE = McpContextAuthority.release_read_lease
_TRUSTED_RELEASE_BINDING = McpContextAuthority.release_binding
_TRUSTED_OPEN_CONTENT_PUBLICATION = _ORIGINAL_OPEN_CONTENT_PUBLICATION
_TRUSTED_PREPARE_INTENT = McpPrivateContextConfirmationService.prepare_intent
_TRUSTED_PUBLISH_MANIFEST = McpContextPackCoordinator.publish_manifest
_TRUSTED_PUBLISH_CONTENT = McpContextPackCoordinator.publish_content
_TRUSTED_CANCEL = McpContextPackCoordinator.cancel
_TRUSTED_PUBLICATION_CLEANUP_ANCHOR = _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR
_TRUSTED_TERMINATE_ANCHORED_PUBLICATION = (
    _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION
)


def _publication_cleanup_helpers_intact(
    _module: object = _confirmation_module,
    _anchor: object = _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR,
    _anchor_code: object = _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_CODE,
    _anchor_defaults: object = _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_DEFAULTS,
    _anchor_closure: object = _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_CLOSURE,
    _terminator: object = _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION,
    _terminator_code: object = _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_CODE,
    _terminator_defaults: object = _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_DEFAULTS,
    _terminator_closure: object = _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_CLOSURE,
) -> bool:
    try:
        namespace = _module.__dict__
        return (
            globals().get("_confirmation_module") is _module
            and namespace.get("_publication_cleanup_anchor", _MISSING) is _anchor
            and _anchor.__code__ is _anchor_code
            and _anchor.__defaults__ is _anchor_defaults
            and _anchor.__closure__ is _anchor_closure
            and namespace.get("_terminate_anchored_publication", _MISSING)
                is _terminator
            and _terminator.__code__ is _terminator_code
            and _terminator.__defaults__ is _terminator_defaults
            and _terminator.__closure__ is _terminator_closure
            and globals().get("_TRUSTED_PUBLICATION_CLEANUP_ANCHOR") is _anchor
            and globals().get("_TRUSTED_TERMINATE_ANCHORED_PUBLICATION")
                is _terminator
        )
    except BaseException:
        return False


_ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT = _publication_cleanup_helpers_intact
_ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT_CODE = (
    _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT.__code__
)
_ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT_DEFAULTS = (
    _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT.__defaults__
)
_ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT_CLOSURE = (
    _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT.__closure__
)
_TRUSTED_PUBLICATION_CLEANUP_HELPERS_INTACT = (
    _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT
)
_TRUSTED_TOKEN_URLSAFE = secrets.token_urlsafe
_TRUSTED_JSON_LOADS = json.loads
_TRUSTED_JSON_DUMPS = json.dumps
_TRUSTED_JSON_DECODE_ERROR = json.JSONDecodeError
_TRUSTED_MATH_ISFINITE = math.isfinite
_TRUSTED_RE_FULLMATCH = re.fullmatch
_TRUSTED_BASE64_MODULE = _contract_module.__dict__.get("base64", _MISSING)
_TRUSTED_BASE64_DECODE = _TRUSTED_BASE64_MODULE.b64decode
_TRUSTED_BASE64_ENCODE = _TRUSTED_BASE64_MODULE.urlsafe_b64encode
_TRUSTED_BINASCII_MODULE = _contract_module.__dict__.get("binascii", _MISSING)
_TRUSTED_BINASCII_ERROR = _TRUSTED_BINASCII_MODULE.Error
_TRUSTED_GET_IDENT = threading.get_ident
_TRUSTED_GETPID = os.getpid
_TRUSTED_RLOCK_FACTORY = threading.RLock
_TRUSTED_RLOCK_TYPE = type(threading.RLock())
_TRUSTED_CONTRACT_HELPERS = tuple(
    (name, _contract_module.__dict__.get(name, _MISSING))
    for name in (
        "_fail", "_safe_string", "_safe_id", "_validate_signed_cursor_wire",
        "_reject_constant", "_unique_pairs", "_check_json_tree", "_json_bytes",
        "_selection_from_wire", "_selection_wire_projection",
        "validate_context_tool_arguments", "parse_mcp_context_request",
        "encode_context_jsonrpc_response", "McpContextContractError",
        "McpContextToolCall", "MappingProxyType", "json", "math", "re",
        "base64", "binascii",
        "MCP_CONTEXT_BODY_MAX_BYTES", "MCP_CONTEXT_ARGUMENTS_MAX_BYTES",
        "MCP_CONTEXT_RAW_PAGE_MAX_BYTES", "MCP_CONTEXT_JSON_DEPTH_MAX",
        "MCP_CONTEXT_JSON_NODES_MAX", "MCP_CONTEXT_SELECTION_MAX",
        "MCP_CONTEXT_CURSOR_MAX_CHARS", "MCP_CONTEXT_METHODS",
        "_METHOD_FIELDS", "_METHOD_REQUIRED",
    )
)


@dataclass(frozen=True, slots=True)
class _RequestRecord:
    request_id: str
    lease_id: str
    identity: tuple[object, ...]
    chat_session_id: str
    binding: McpContextBinding
    lease: McpContextReadLease
    selection: ContextSelection
    page_bytes: int
    generation: int
    origin: tuple[object, ...]


_REQUEST_RECORD_FIELD_NAMES = (
    "request_id", "lease_id", "identity", "chat_session_id", "binding",
    "lease", "selection", "page_bytes", "generation", "origin",
)
_REQUEST_RECORD_PIN = _class_pin(
    _RequestRecord,
    (*_REQUEST_RECORD_FIELD_NAMES, "__getattribute__", "__getattr__"),
)
_REQUEST_RECORD_SLOTS = tuple(
    _RequestRecord.__dict__[name] for name in _REQUEST_RECORD_FIELD_NAMES
)


def _identity_projection(identity: object) -> tuple[object, ...]:
    if type(identity) is not McpAuthenticatedIdentity or not _class_matches(
        McpAuthenticatedIdentity, _IDENTITY_PIN,
    ):
        _fail()
    try:
        values = tuple(object.__getattribute__(identity, name) for name in _IDENTITY_FIELDS)
    except BaseException:
        _fail()
    token_id, owner_alias, owner_subject_id, cache, security, scope = values
    if (
        type(token_id) is not str or not 1 <= len(token_id) <= 256
        or type(owner_alias) is not str or not 1 <= len(owner_alias) <= 256
        or type(owner_subject_id) is not str or not 1 <= len(owner_subject_id) <= 256
        or type(cache) is not int or isinstance(cache, bool) or cache < 0
        or type(security) is not int or isinstance(security, bool) or security < 0
        or type(scope) is not str or scope != MCP_CONTEXT_SCOPE
    ):
        _fail()
    return values


def _binding_projection(binding: object) -> tuple[object, ...]:
    if type(binding) is not McpContextBinding or not _class_matches(
        McpContextBinding, _BINDING_PIN,
    ):
        _fail()
    try:
        return tuple(
            object.__getattribute__(binding, name)
            for name in (
                "token_id", "owner_alias", "owner_subject_id", "cache_generation",
                "security_action_revision", "scope", "chat_session_id",
                "process_epoch",
            )
        )
    except BaseException:
        _fail()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail()
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    _fail()


def _check_json_tree(value: object) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            _fail()
        kind = type(item)
        if item is None or kind in {str, bool, int}:
            continue
        if kind is float:
            if not math.isfinite(item):
                _fail()
            continue
        if kind is list:
            stack.extend((child, depth + 1) for child in item)
            continue
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _fail()
                nodes += 1
                if nodes > _MAX_JSON_NODES:
                    _fail()
                stack.append((child, depth + 1))
            continue
        _fail()


def _validate_coordinator_response(
    raw: object,
    *,
    rpc_request_id: str | int,
    method: str,
) -> bool:
    if type(raw) is not bytes or not raw or len(raw) > MCP_CONTEXT_RESULT_MAX_BYTES:
        _fail()
    try:
        value = _TRUSTED_JSON_LOADS(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _check_json_tree(value)
    except McpContextRequestRuntimeError:
        raise
    except BaseException:
        _fail()
    if (
        type(value) is not dict
        or set(value) != {"jsonrpc", "id", "result"}
        or type(value["jsonrpc"]) is not str
        or value["jsonrpc"] != "2.0"
        or type(value["id"]) is not type(rpc_request_id)
        or value["id"] != rpc_request_id
        or type(value["result"]) is not dict
    ):
        _fail()
    result = value["result"]
    expected = (
        {"schema", "pack_id", "items", "manifest_cursor", "complete", "read_only",
         "authority_granted", "send_performed"}
        if method == "harbour_context_manifest"
        else {"schema", "pack_id", "page", "content_cursor", "complete", "read_only",
              "authority_granted", "send_performed"}
    )
    if (
        set(result) != expected
        or type(result["schema"]) is not str
        or result["schema"] != MCP_CONTEXT_PACK_SCHEMA
        or type(result["pack_id"]) is not str
        or not result["pack_id"]
        or type(result["complete"]) is not bool
        or result["read_only"] is not True
        or result["authority_granted"] is not False
        or result["send_performed"] is not False
    ):
        _fail()
    if method == "harbour_context_manifest":
        if type(result["items"]) is not list or (
            result["manifest_cursor"] is not None
            and type(result["manifest_cursor"]) is not str
        ):
            _fail()
    elif method == "harbour_context_page":
        if type(result["page"]) is not dict or (
            result["content_cursor"] is not None
            and type(result["content_cursor"]) is not str
        ):
            _fail()
    else:
        _fail()
    return result["complete"]


class McpContextRequestRuntime:
    """Own request records around the already accepted context-pack graph."""

    __slots__ = (
        "_authority", "_service", "_producer", "_cursor", "_coordinator",
        "_lock", "_closed", "_generation", "_pid", "_records", "_pending",
        "_lease_ids", "_active_records", "_active_threads",
        "_request_rejection_capability",
    )

    def __init__(
        self,
        authority: McpContextAuthority,
        service: McpPrivateContextConfirmationService,
        producer: CombinedContextManifestProducer,
        cursor_authority: McpContextCursorAuthority,
        coordinator: McpContextPackCoordinator,
    ) -> None:
        if (
            type(authority) is not _GENUINE_AUTHORITY_CLASS
            or type(service) is not _GENUINE_SERVICE_CLASS
            or type(producer) is not _GENUINE_PRODUCER_CLASS
            or type(cursor_authority) is not _GENUINE_CURSOR_CLASS
            or type(coordinator) is not _GENUINE_COORDINATOR_CLASS
        ):
            raise ValueError("invalid request runtime")
        self._authority = authority
        self._service = service
        self._producer = producer
        self._cursor = cursor_authority
        self._coordinator = coordinator
        self._lock = _TRUSTED_RLOCK_FACTORY()
        self._closed = False
        self._generation = 0
        self._pid = _TRUSTED_GETPID()
        self._records: dict[str, _RequestRecord] = {}
        self._pending: dict[object, tuple[str, str]] = {}
        self._lease_ids: set[str] = set()
        self._active_records: dict[str, object] = {}
        self._active_threads: dict[int, object] = {}
        if (
            _confirmation_module._acquire_request_rejection_capability
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY
            or _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__code__
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CODE
            or _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__defaults__
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_DEFAULTS
            or _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__closure__
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CLOSURE
            or _confirmation_module._classify_pristine_intent
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__code__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CODE
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__defaults__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__closure__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE
            or type(_ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS) is not tuple
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE is not None
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__kwdefaults__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_KWDEFAULTS
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__globals__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_GLOBALS
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__name__
                != _ORIGINAL_CLASSIFY_PRISTINE_INTENT_NAME
            or type(_ORIGINAL_CLASSIFY_PRISTINE_INTENT)
                is not _ORIGINAL_NATIVE_FUNCTION_TYPE
            or FunctionType is not _ORIGINAL_NATIVE_FUNCTION_TYPE
            or _confirmation_module._PRISTINE_INTENT_SENTINEL
                is not _ORIGINAL_PRISTINE_INTENT_SENTINEL
            or _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
                is not _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
            or _confirmation_module._PRISTINE_INTENT_ORIGIN_SIZE
                != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
            or _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE != 20
            or not all(
                _confirmation_module.__dict__.get(global_name, _MISSING)
                    is value_type
                and all(value_type.__dict__.get(name, _MISSING) is descriptor
                        for name, descriptor in descriptor_pins)
                for global_name, value_type, descriptor_pins
                in _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
            )
        ):
            raise ValueError("invalid request runtime")
        self._request_rejection_capability = (
            _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY(service)
        )
        self._assert_pins()
        self._assert_graph()

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("context request runtime cannot be subclassed")

    def __repr__(self) -> str:
        return "McpContextRequestRuntime(<redacted>)"

    def _assert_pins(self) -> None:
        if (
            _confirmation_module._acquire_request_rejection_capability
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY
            or _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__code__
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CODE
            or _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__defaults__
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_DEFAULTS
            or _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__closure__
                is not _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CLOSURE
            or _confirmation_module._classify_pristine_intent
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__code__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CODE
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__defaults__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__closure__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE
            or type(_ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS) is not tuple
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE is not None
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__kwdefaults__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_KWDEFAULTS
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__globals__
                is not _ORIGINAL_CLASSIFY_PRISTINE_INTENT_GLOBALS
            or _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__name__
                != _ORIGINAL_CLASSIFY_PRISTINE_INTENT_NAME
            or type(_ORIGINAL_CLASSIFY_PRISTINE_INTENT)
                is not _ORIGINAL_NATIVE_FUNCTION_TYPE
            or FunctionType is not _ORIGINAL_NATIVE_FUNCTION_TYPE
            or _confirmation_module._PRISTINE_INTENT_SENTINEL
                is not _ORIGINAL_PRISTINE_INTENT_SENTINEL
            or _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
                is not _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
            or _confirmation_module._PRISTINE_INTENT_ORIGIN_SIZE
                != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
            or _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE != 20
            or not all(
                _confirmation_module.__dict__.get(global_name, _MISSING)
                    is value_type
                and all(value_type.__dict__.get(name, _MISSING) is descriptor
                        for name, descriptor in descriptor_pins)
                for global_name, value_type, descriptor_pins
                in _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
            )
        ):
            _fail()
        try:
            refreshed_rejection_capability = (
                _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY(self._service)
            )
        except BaseException:
            _fail()
        retained_rejection_capability = self._request_rejection_capability
        if (
            type(retained_rejection_capability) is not tuple
            or len(retained_rejection_capability) != 11
            or type(refreshed_rejection_capability) is not tuple
            or len(refreshed_rejection_capability) != 11
        ):
            _fail()
        checks = (
            _authority_module._NativeLeaseState is _ORIGINAL_NATIVE_LEASE_STATE,
            _authority_module._NativeLeaseCapability
            is _ORIGINAL_NATIVE_LEASE_CAPABILITY,
            _authority_module._acquire_native_lease_capability
            is _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY,
            _authority_module._McpContentPublicationVerifier
            is _ORIGINAL_CONTENT_PUBLICATION_VERIFIER,
            _authority_module._open_mcp_content_publication
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION.__code__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_CODE,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION.__defaults__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_DEFAULTS,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION.__wrapped__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED.__code__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_CODE,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED.__defaults__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_DEFAULTS,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED.__closure__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED_CLOSURE,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION.__closure__
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_CLOSURE,
            type(_ORIGINAL_OPEN_CONTENT_PUBLICATION_CLOSURE) is tuple,
            len(_ORIGINAL_OPEN_CONTENT_PUBLICATION_CLOSURE) == 1,
            _ORIGINAL_OPEN_CONTENT_PUBLICATION_CLOSURE[0].cell_contents
            is _ORIGINAL_OPEN_CONTENT_PUBLICATION_WRAPPED,
            _authority_module._CONTENT_PUBLICATION_CONTEXT
            is _ORIGINAL_CONTENT_PUBLICATION_CONTEXT,
            _authority_module._CONTENT_PUBLICATION_OWNER_CONTEXT
            is _ORIGINAL_CONTENT_PUBLICATION_OWNER_CONTEXT,
            _authority_module._CONTENT_PUBLICATION_ISSUER
            is _ORIGINAL_CONTENT_PUBLICATION_ISSUER,
            _authority_module._CONTENT_PUBLICATIONS
            is _ORIGINAL_CONTENT_PUBLICATION_REGISTRY,
            _authority_module._CONTENT_PUBLICATION_LOCK
            is _ORIGINAL_CONTENT_PUBLICATION_LOCK,
            _authority_module._CONTENT_PUBLICATION_CAP
            == _ORIGINAL_CONTENT_PUBLICATION_CAP,
            all(
                _authority_module.__dict__.get(name, _MISSING) is expected
                for name, expected in zip(
                    (
                        "_CONTENT_PUBLICATION_ENTRY", "_CONTENT_PUBLICATION_REUSE",
                        "_CONTENT_PUBLICATION_FINAL", "_CONTENT_PUBLICATION_POISONED",
                        "_CONTENT_PUBLICATION_CLOSED",
                    ),
                    _ORIGINAL_CONTENT_PUBLICATION_STATES,
                )
            ),
            all(
                _authority_module.__dict__.get(name, _MISSING) is helper
                and helper.__code__ is code
                and helper.__defaults__ is defaults
                and helper.__closure__ is closure
                for name, helper, code, defaults, closure
                in _ORIGINAL_CONTENT_PUBLICATION_HELPERS
            ),
            _authority_module._fail is _ORIGINAL_AUTHORITY_FAIL,
            _ORIGINAL_AUTHORITY_FAIL.__code__
            is _ORIGINAL_AUTHORITY_FAIL_CODE,
            _ORIGINAL_AUTHORITY_FAIL.__defaults__
            is _ORIGINAL_AUTHORITY_FAIL_DEFAULTS,
            _GENUINE_AUTHORITY_CLASS.__dict__.get("_row_and_chat", _MISSING)
            is _ORIGINAL_AUTHORITY_ROW_AND_CHAT,
            _ORIGINAL_AUTHORITY_ROW_AND_CHAT.__code__
            is _ORIGINAL_AUTHORITY_ROW_AND_CHAT_CODE,
            _ORIGINAL_AUTHORITY_ROW_AND_CHAT.__defaults__
            is _ORIGINAL_AUTHORITY_ROW_AND_CHAT_DEFAULTS,
            _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY.__code__
            is _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_CODE,
            _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY.__defaults__
            is _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_DEFAULTS,
            type(_ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_DEFAULTS) is tuple,
            len(_ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_DEFAULTS) == 1,
            _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY_DEFAULTS[0]
            is _ORIGINAL_NATIVE_LEASE_STATE_READER,
            _authority_module._read_native_lease_state
            is _ORIGINAL_NATIVE_LEASE_STATE_READER,
            _ORIGINAL_NATIVE_LEASE_STATE_READER.__code__
            is _ORIGINAL_NATIVE_LEASE_STATE_READER_CODE,
            _ORIGINAL_NATIVE_LEASE_STATE_READER.__defaults__
            is _ORIGINAL_NATIVE_LEASE_STATE_READER_DEFAULTS,
            type(_ORIGINAL_NATIVE_LEASE_STATE_READER_DEFAULTS) is tuple,
            len(_ORIGINAL_NATIVE_LEASE_STATE_READER_DEFAULTS) == 1,
            _ORIGINAL_NATIVE_LEASE_STATE_READER_DEFAULTS[0]
            is _ORIGINAL_NATIVE_LEASE_STATE_SLOT,
            _authority_module._NATIVE_LEASE_STATE_SLOT
            is _ORIGINAL_NATIVE_LEASE_STATE_SLOT,
            _authority_module._NativeLeaseStateSlot
            is _ORIGINAL_NATIVE_LEASE_STATE_SLOT_CLASS,
            type(_ORIGINAL_NATIVE_LEASE_STATE_SLOT) is MemberDescriptorType,
            _ORIGINAL_NATIVE_LEASE_STATE_SLOT.__objclass__
            is _ORIGINAL_NATIVE_LEASE_STATE_SLOT_CLASS,
            _ORIGINAL_NATIVE_LEASE_CAPABILITY.__dict__.get(
                "__init__", _MISSING,
            ) is _ORIGINAL_NATIVE_LEASE_CAPABILITY_INIT,
            _ORIGINAL_NATIVE_LEASE_CAPABILITY_INIT.__code__
            is _ORIGINAL_NATIVE_LEASE_CAPABILITY_INIT_CODE,
            _ORIGINAL_NATIVE_LEASE_TERMINAL_REASON.__code__
            is _ORIGINAL_NATIVE_LEASE_TERMINAL_REASON_CODE,
            _ORIGINAL_NATIVE_LEASE_CAPABILITY.__dict__.get("cancel", _MISSING)
            is _ORIGINAL_NATIVE_LEASE_CANCEL,
            _ORIGINAL_NATIVE_LEASE_CANCEL.__code__
            is _ORIGINAL_NATIVE_LEASE_CANCEL_CODE,
            _ORIGINAL_NATIVE_LEASE_CANCEL.__defaults__
            is _ORIGINAL_NATIVE_LEASE_CANCEL_DEFAULTS,
            type(_ORIGINAL_NATIVE_LEASE_CANCEL_DEFAULTS) is tuple,
            len(_ORIGINAL_NATIVE_LEASE_CANCEL_DEFAULTS) == 1,
            _ORIGINAL_NATIVE_LEASE_CANCEL_DEFAULTS[0] is None,
            _authority_module._NATIVE_LEASE_CANCELLED
            is _ORIGINAL_NATIVE_LEASE_CANCELLED,
            _class_matches(
                _ORIGINAL_NATIVE_LEASE_STATE,
                _ORIGINAL_NATIVE_LEASE_STATE_PIN,
            ),
            _class_matches(
                _ORIGINAL_NATIVE_LEASE_STATE_SLOT_CLASS,
                _ORIGINAL_NATIVE_LEASE_STATE_SLOT_PIN,
            ),
            _class_matches(
                _ORIGINAL_NATIVE_LEASE_CAPABILITY,
                _ORIGINAL_NATIVE_LEASE_CAPABILITY_PIN,
            ),
            _class_matches(
                _ORIGINAL_CONTENT_PUBLICATION_VERIFIER,
                _ORIGINAL_CONTENT_PUBLICATION_VERIFIER_PIN,
            ),
            all(
                _ORIGINAL_CONTENT_PUBLICATION_VERIFIER.__dict__.get(
                    name, _MISSING,
                ) is method
                and method.__code__ is code
                and method.__defaults__ is defaults
                for name, method, code, defaults
                in _ORIGINAL_CONTENT_PUBLICATION_VERIFIER_METHODS
            ),
            _contract_module.parse_mcp_context_request is _TRUSTED_PARSE,
            _contract_module.encode_context_jsonrpc_response is _TRUSTED_ENCODE,
            _authority_module.release_mcp_bearer_proof is _TRUSTED_RELEASE_PROOF,
            _authority_module.McpContextAuthority is _GENUINE_AUTHORITY_CLASS,
            _authority_module.McpContextBearerProof is McpContextBearerProof,
            _authority_module.McpContextBinding is McpContextBinding,
            _authority_module.McpContextReadLease is McpContextReadLease,
            _authority_module.McpBearerProof is _NATIVE_PROOF_CLASS,
            _coordinator_module.McpContextPackCoordinator is _GENUINE_COORDINATOR_CLASS,
            _confirmation_module.McpPrivateContextConfirmationService
            is _GENUINE_SERVICE_CLASS,
            _confirmation_module._acquire_request_rejection_capability
            is _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY,
            _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__code__
            is _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CODE,
            _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__defaults__
            is _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_DEFAULTS,
            _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY.__closure__
            is _ORIGINAL_ACQUIRE_REQUEST_REJECTION_CAPABILITY_CLOSURE,
            _confirmation_module._classify_pristine_intent
            is _ORIGINAL_CLASSIFY_PRISTINE_INTENT,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__code__
            is _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CODE,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__defaults__
            is _ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__closure__
            is _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE,
            type(_ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS) is tuple,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CLOSURE is None,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__kwdefaults__
            is _ORIGINAL_CLASSIFY_PRISTINE_INTENT_KWDEFAULTS,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT_KWDEFAULTS is None,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__globals__
            is _ORIGINAL_CLASSIFY_PRISTINE_INTENT_GLOBALS,
            _ORIGINAL_CLASSIFY_PRISTINE_INTENT.__name__
            == _ORIGINAL_CLASSIFY_PRISTINE_INTENT_NAME,
            type(_ORIGINAL_CLASSIFY_PRISTINE_INTENT)
            is _ORIGINAL_NATIVE_FUNCTION_TYPE,
            FunctionType is _ORIGINAL_NATIVE_FUNCTION_TYPE,
            _confirmation_module._PRISTINE_INTENT_SENTINEL
            is _ORIGINAL_PRISTINE_INTENT_SENTINEL,
            _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
            is _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS,
            _confirmation_module._PRISTINE_INTENT_ORIGIN_SIZE
            == _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE,
            _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE == 20,
            all(
                _confirmation_module.__dict__.get(global_name, _MISSING)
                    is value_type
                and _class_matches(value_type, descriptor_pins)
                for global_name, value_type, descriptor_pins
                in _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
            ),
            type(retained_rejection_capability) is tuple,
            len(retained_rejection_capability) == 11,
            type(refreshed_rejection_capability) is tuple,
            len(refreshed_rejection_capability) == 11,
            all(current is retained for current, retained in zip(
                refreshed_rejection_capability, retained_rejection_capability,
            )),
            retained_rejection_capability[0] is self._service,
            retained_rejection_capability[8] is self._authority,
            _confirmation_module._publication_cleanup_anchor
            is _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR,
            _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR.__code__
            is _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_CODE,
            _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR.__defaults__
            is _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_DEFAULTS,
            _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR.__closure__
            is _ORIGINAL_PUBLICATION_CLEANUP_ANCHOR_CLOSURE,
            _confirmation_module._terminate_anchored_publication
            is _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION,
            _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION.__code__
            is _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_CODE,
            _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION.__defaults__
            is _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_DEFAULTS,
            _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION.__closure__
            is _ORIGINAL_TERMINATE_ANCHORED_PUBLICATION_CLOSURE,
            globals().get("_publication_cleanup_helpers_intact")
            is _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT,
            globals().get("_TRUSTED_PUBLICATION_CLEANUP_HELPERS_INTACT")
            is _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT,
            _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT.__code__
            is _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT_CODE,
            _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT.__defaults__
            is _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT_DEFAULTS,
            _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT.__closure__
            is _ORIGINAL_PUBLICATION_CLEANUP_HELPERS_INTACT_CLOSURE,
            McpContextAuthority is _GENUINE_AUTHORITY_CLASS,
            McpPrivateContextConfirmationService is _GENUINE_SERVICE_CLASS,
            CombinedContextManifestProducer is _GENUINE_PRODUCER_CLASS,
            McpContextCursorAuthority is _GENUINE_CURSOR_CLASS,
            McpContextPackCoordinator is _GENUINE_COORDINATOR_CLASS,
            _selection_module.ContextSelection is ContextSelection,
            _selection_module.SourceSelection is SourceSelection,
            _selection_module.ForgeSelectionLocator is ForgeSelectionLocator,
            parse_mcp_context_request is _TRUSTED_PARSE,
            encode_context_jsonrpc_response is _TRUSTED_ENCODE,
            release_mcp_bearer_proof is _TRUSTED_RELEASE_PROOF,
            secrets.token_urlsafe is _TRUSTED_TOKEN_URLSAFE,
            json.loads is _TRUSTED_JSON_LOADS,
            json.dumps is _TRUSTED_JSON_DUMPS,
            json.JSONDecodeError is _TRUSTED_JSON_DECODE_ERROR,
            math.isfinite is _TRUSTED_MATH_ISFINITE,
            re.fullmatch is _TRUSTED_RE_FULLMATCH,
            _TRUSTED_BASE64_MODULE.b64decode is _TRUSTED_BASE64_DECODE,
            _TRUSTED_BASE64_MODULE.urlsafe_b64encode is _TRUSTED_BASE64_ENCODE,
            _TRUSTED_BINASCII_MODULE.Error is _TRUSTED_BINASCII_ERROR,
            threading.get_ident is _TRUSTED_GET_IDENT,
            os.getpid is _TRUSTED_GETPID,
            threading.RLock is _TRUSTED_RLOCK_FACTORY,
            type(self._lock) is _TRUSTED_RLOCK_TYPE,
            _class_matches(McpAuthenticatedIdentity, _IDENTITY_PIN),
            _class_matches(McpContextToolCall, _CALL_PIN),
            _class_matches(PreparedRequest, _PREPARED_PIN),
            _class_matches(ContextSelection, _SELECTION_PIN),
            _class_matches(SourceSelection, _SOURCE_PIN),
            _class_matches(ForgeSelectionLocator, _LOCATOR_PIN),
            _class_matches(McpContextBinding, _BINDING_PIN),
            _class_matches(McpContextReadLease, _LEASE_PIN),
            _class_matches(McpContextBearerProof, _WRAPPER_PIN),
            _class_matches(_NATIVE_PROOF_CLASS, _NATIVE_PROOF_PIN),
            _class_matches(_GENUINE_AUTHORITY_CLASS, _AUTHORITY_PIN),
            _class_matches(_GENUINE_SERVICE_CLASS, _SERVICE_PIN),
            _class_matches(_GENUINE_CURSOR_CLASS, _CURSOR_PIN),
            _class_matches(_GENUINE_COORDINATOR_CLASS, _COORDINATOR_PIN),
            all(
                _contract_module.__dict__.get(name, _MISSING) is expected
                for name, expected in _TRUSTED_CONTRACT_HELPERS
            ),
        )
        if not all(checks):
            _fail()
        namespace = type(self).__dict__
        if any(namespace.get(name, _MISSING) is not expected for name, expected in _RUNTIME_PIN):
            _fail()

    def _assert_graph(self) -> None:
        try:
            authority = object.__getattribute__(self, "_authority")
            service = object.__getattribute__(self, "_service")
            producer = object.__getattribute__(self, "_producer")
            cursor = object.__getattribute__(self, "_cursor")
            coordinator = object.__getattribute__(self, "_coordinator")
            if (
                type(authority) is not _GENUINE_AUTHORITY_CLASS
                or type(service) is not _GENUINE_SERVICE_CLASS
                or type(producer) is not _GENUINE_PRODUCER_CLASS
                or type(cursor) is not _GENUINE_CURSOR_CLASS
                or type(coordinator) is not _GENUINE_COORDINATOR_CLASS
                or CombinedContextManifestProducer is not _GENUINE_PRODUCER_CLASS
                or type(_PRODUCER_AUTHORITY_DESCRIPTOR) is not MemberDescriptorType
                or _GENUINE_PRODUCER_CLASS.__dict__.get(
                    "_mcp_authority", _MISSING,
                ) is not _PRODUCER_AUTHORITY_DESCRIPTOR
            ):
                _fail()
            authority_state = object.__getattribute__(authority, "__dict__")
            service_state = object.__getattribute__(service, "__dict__")
            producer_authority = _PRODUCER_AUTHORITY_DESCRIPTOR.__get__(
                producer, _GENUINE_PRODUCER_CLASS,
            )
            if (
                type(authority_state) is not dict
                or type(service_state) is not dict
                or any(name in authority_state for name in (
                    "issue_binding", "recheck_binding", "issue_read_lease",
                    "recheck_lease", "release_read_lease", "release_binding",
                ))
                or any(name in service_state for name in ("prepare_intent", "revoke"))
                or service_state.get("_authority") is not authority
                or service_state.get("_producer") is not producer
                or producer_authority is not authority
                or object.__getattribute__(cursor, "_authority") is not authority
                or object.__getattribute__(coordinator, "_authority") is not authority
                or object.__getattribute__(coordinator, "_service") is not service
                or object.__getattribute__(coordinator, "_producer") is not producer
                or object.__getattribute__(coordinator, "_cursor") is not cursor
            ):
                _fail()
        except McpContextRequestRuntimeError:
            raise
        except BaseException:
            _fail()

    def _begin_call(self) -> tuple[object, int, int, int]:
        thread_id = _TRUSTED_GET_IDENT()
        pid = _TRUSTED_GETPID()
        with self._lock:
            if (
                self._closed
                or type(self._generation) is not int
                or self._generation < 0
                or type(self._pid) is not int
                or self._pid != pid
                or thread_id in self._active_threads
            ):
                _fail()
            token = (object(), self._generation, thread_id, pid)
            self._active_threads[thread_id] = token[0]
            return token

    def _checkpoint(self, token: tuple[object, int, int, int]) -> None:
        with self._lock:
            if (
                self._closed
                or token[1] != self._generation
                or token[2] != _TRUSTED_GET_IDENT()
                or token[3] != _TRUSTED_GETPID()
                or self._pid != token[3]
                or self._active_threads.get(token[2]) is not token[0]
            ):
                _fail()

    def _end_call(self, token: tuple[object, int, int, int] | None) -> None:
        if token is None:
            return
        try:
            with self._lock:
                if self._active_threads.get(token[2]) is token[0]:
                    self._active_threads.pop(token[2], None)
        except BaseException:
            return

    def _reserve_prepare(self, owner_sid: str) -> tuple[object, str]:
        with self._lock:
            active = tuple(self._records.values())
            pending = tuple(self._pending.values())
            if (
                self._closed
                or len(active) + len(pending) >= _MAX_RECORDS
                or sum(1 for row in active if row.identity[2] == owner_sid)
                + sum(1 for row in pending if row[0] == owner_sid) >= _MAX_OWNER_RECORDS
            ):
                _fail()
            lease_id = ""
            for _attempt in range(4):
                candidate = "l." + _TRUSTED_TOKEN_URLSAFE(32)
                if (
                    type(candidate) is str
                    and _LEASE_ID_RE.fullmatch(candidate) is not None
                    and candidate not in self._lease_ids
                ):
                    lease_id = candidate
                    break
            if not lease_id:
                _fail()
            reservation = object()
            self._lease_ids.add(lease_id)
            self._pending[reservation] = (owner_sid, lease_id)
            return reservation, lease_id

    def _release_reservation(self, reservation: object, *, keep_lease: bool) -> None:
        with self._lock:
            row = self._pending.pop(reservation, None)
            if row is not None and not keep_lease:
                self._lease_ids.discard(row[1])

    def _install_record(
        self,
        reservation: object,
        record: _RequestRecord,
        call_token: tuple[object, int, int, int],
    ) -> None:
        with self._lock:
            pending = self._pending.get(reservation)
            if (
                self._closed
                or call_token[1] != self._generation
                or pending != (record.identity[2], record.lease_id)
                or record.request_id in self._records
                or record.generation != self._generation
                or type(record.origin) is not tuple
                or len(record.origin) != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
            ):
                _fail()
            self._records[record.request_id] = record
            self._pending.pop(reservation, None)

    def _claim_record(
        self,
        request_id: str,
        identity: tuple[object, ...],
    ) -> tuple[_RequestRecord, object]:
        with self._lock:
            record = self._records.get(request_id)
            if (
                self._closed
                or type(record) is not _RequestRecord
                or record.request_id != request_id
                or record.identity != identity
                or record.generation != self._generation
                or type(record.origin) is not tuple
                or len(record.origin) != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
                or request_id in self._active_records
            ):
                _fail()
            token = object()
            self._active_records[request_id] = token
            return record, token

    def _release_record_claim(self, request_id: str, token: object) -> None:
        with self._lock:
            if self._active_records.get(request_id) is token:
                self._active_records.pop(request_id, None)

    def _detach_record(self, record: _RequestRecord) -> _RequestRecord | None:
        with self._lock:
            if self._records.get(record.request_id) is not record:
                return None
            self._records.pop(record.request_id, None)
            self._lease_ids.discard(record.lease_id)
            return record

    def _release_owned(self, record: _RequestRecord, *, cancel_service: bool) -> bool:
        ok = True
        if cancel_service:
            try:
                _TRUSTED_CANCEL(self._coordinator, record.request_id)
            except BaseException:
                ok = False
        try:
            _TRUSTED_RELEASE_LEASE(self._authority, record.lease)
        except BaseException:
            ok = False
        try:
            _TRUSTED_RELEASE_BINDING(self._authority, record.binding)
        except BaseException:
            ok = False
        return ok

    def _record_live(self, record: _RequestRecord) -> None:
        self._assert_pins()
        self._assert_graph()
        _TRUSTED_RECHECK_BINDING(self._authority, record.binding)
        current = _TRUSTED_RECHECK_LEASE(self._authority, record.lease)
        if current is not record.binding:
            _fail()
        projection = _binding_projection(record.binding)
        if projection[:6] != record.identity or projection[6] != record.chat_session_id:
            _fail()
        if (
            type(record.lease) is not McpContextReadLease
            or not _class_matches(McpContextReadLease, _LEASE_PIN)
            or object.__getattribute__(record.lease, "binding") is not record.binding
            or object.__getattribute__(record.lease, "request_id") != record.lease_id
            or type(record.origin) is not tuple
            or len(record.origin) != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
            or record.origin[3] is not record.selection
            or record.origin[4] is not record.lease
            or record.origin[5] is not record.binding
            or record.origin[6] != record.request_id
            or record.origin[7] != record.lease_id
            or record.origin[8] != record.identity[2]
            or record.origin[11] != record.page_bytes
        ):
            _fail()

    @staticmethod
    def _call_projection(call: object) -> tuple[str, str | int, MappingProxyType]:
        if type(call) is not McpContextToolCall or not _class_matches(McpContextToolCall, _CALL_PIN):
            _fail()
        try:
            method = object.__getattribute__(call, "method")
            request_id = object.__getattribute__(call, "request_id")
            arguments = object.__getattribute__(call, "arguments")
        except BaseException:
            _fail()
        if (
            type(method) is not str
            or type(request_id) not in {str, int}
            or isinstance(request_id, bool)
            or type(arguments) is not _MAPPING_PROXY_TYPE
        ):
            _fail()
        return method, request_id, arguments

    def _prepare(
        self,
        rpc_request_id: str | int,
        arguments: MappingProxyType,
        identity_object: McpAuthenticatedIdentity,
        identity: tuple[object, ...],
        proof: McpContextBearerProof | None,
        call_token: tuple[object, int, int, int],
    ) -> bytes:
        if type(proof) is not McpContextBearerProof:
            _fail()
        native_function_type = _ORIGINAL_NATIVE_FUNCTION_TYPE
        classifier_function = _ORIGINAL_CLASSIFY_PRISTINE_INTENT
        classifier_code = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CODE
        classifier_defaults = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS
        classifier_globals = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_GLOBALS
        classifier_name = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_NAME
        classifier_sentinel = _ORIGINAL_PRISTINE_INTENT_SENTINEL
        classifier_descriptor_pins = (
            _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
        )
        native_member_get = MemberDescriptorType.__get__
        native_member_descriptor_type = MemberDescriptorType
        native_object_getattribute = object.__getattribute__
        authority_dict_type = classifier_descriptor_pins[12][1]
        authority_dict_slot = classifier_descriptor_pins[12][2][0][1]
        native_type_getattribute = type.__getattribute__
        native_reference_type = ReferenceType
        native_staticmethod_type = staticmethod
        native_staticmethod_get = staticmethod.__get__
        trusted_cancel = _TRUSTED_CANCEL
        trusted_release_lease = _TRUSTED_RELEASE_LEASE
        trusted_release_binding = _TRUSTED_RELEASE_BINDING
        runtime_type = _TRUSTED_RUNTIME_CLASS
        runtime_pin = _RUNTIME_PIN
        runtime_slots = _RUNTIME_SLOTS
        record_type = _RequestRecord
        record_pin = _REQUEST_RECORD_PIN
        record_slots = _REQUEST_RECORD_SLOTS
        if type(self) is not runtime_type or len(runtime_slots) != 15:
            _fail()
        runtime_values: list[object] = []
        for runtime_slot in runtime_slots:
            runtime_values.append(native_member_get(runtime_slot, self, runtime_type))
        (
            runtime_authority_anchor, runtime_service_anchor,
            runtime_producer_anchor, runtime_cursor_anchor,
            runtime_coordinator_anchor, runtime_lock_anchor,
            runtime_closed_anchor, runtime_generation_anchor, runtime_pid_anchor,
            runtime_records_anchor, runtime_pending_anchor,
            runtime_lease_ids_anchor, runtime_active_records_anchor,
            runtime_active_threads_anchor, rejection_capability_anchor,
        ) = runtime_values
        runtime_lock_type = type(runtime_lock_anchor)
        if (
            type(runtime_closed_anchor) is not bool
            or type(runtime_generation_anchor) is not int
            or isinstance(runtime_generation_anchor, bool)
            or type(runtime_pid_anchor) is not int
            or isinstance(runtime_pid_anchor, bool)
            or type(runtime_records_anchor) is not dict
            or type(runtime_pending_anchor) is not dict
            or type(runtime_lease_ids_anchor) is not set
            or type(runtime_active_records_anchor) is not dict
            or type(runtime_active_threads_anchor) is not dict
            or type(rejection_capability_anchor) is not tuple
            or len(rejection_capability_anchor) != 11
        ):
            _fail()
        descriptor_pins_intact = True
        for global_name, value_type, descriptor_pins in classifier_descriptor_pins:
            if _confirmation_module.__dict__.get(global_name, _MISSING) is not value_type:
                descriptor_pins_intact = False
                break
            value_namespace = native_type_getattribute(value_type, "__dict__")
            for descriptor_name, descriptor in descriptor_pins:
                if value_namespace.get(descriptor_name, _MISSING) is not descriptor:
                    descriptor_pins_intact = False
                    break
            if not descriptor_pins_intact:
                break
        if (
            type(classifier_function) is not native_function_type
            or classifier_function.__code__ is not classifier_code
            or classifier_function.__defaults__ is not classifier_defaults
            or type(classifier_defaults) is not tuple
            or classifier_function.__globals__ is not classifier_globals
            or classifier_function.__name__ != classifier_name
            or classifier_function.__closure__ is not None
            or classifier_function.__kwdefaults__ is not None
            or _confirmation_module._classify_pristine_intent
                is not classifier_function
            or _confirmation_module._PRISTINE_INTENT_SENTINEL
                is not classifier_sentinel
            or _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
                is not classifier_descriptor_pins
            or FunctionType is not native_function_type
            or not descriptor_pins_intact
        ):
            _fail()
        native_getset_get = type(authority_dict_slot).__get__
        chat_session_id = arguments["chat_session_id"]
        selection = arguments["selection"]
        page_bytes = arguments.get("page_bytes", 16_384)
        if (
            type(chat_session_id) is not str
            or type(selection) is not ContextSelection
            or not _class_matches(ContextSelection, _SELECTION_PIN)
            or type(page_bytes) is not int
            or isinstance(page_bytes, bool)
        ):
            _fail()
        reservation: object | None = None
        binding: McpContextBinding | None = None
        lease: McpContextReadLease | None = None
        prepared_id: str | None = None
        cleanup_request_id: str | None = None
        origin_entry: object | None = None
        installed = False
        try:
            try:
                reservation, lease_id = self._reserve_prepare(identity[2])
            except BaseException:
                try:
                    _TRUSTED_RELEASE_PROOF(proof)
                except BaseException:
                    pass
                raise
            binding = _TRUSTED_ISSUE_BINDING(
                self._authority, identity_object, proof, chat_session_id,
            )
            self._checkpoint(call_token)
            lease = _TRUSTED_ISSUE_LEASE(
                self._authority, binding, lease_id, ttl_seconds=_LEASE_TTL_SECONDS,
            )
            self._checkpoint(call_token)
            prepared_envelope = _TRUSTED_PREPARE_INTENT(
                self._service,
                lease,
                selection,
                page_bytes=page_bytes,
                _return_origin=True,
            )
            if type(prepared_envelope) is not tuple or len(prepared_envelope) != 2:
                _fail()
            prepared, origin = prepared_envelope
            if (
                type(origin) is not tuple
                or len(origin) != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
                or type(origin[6]) is not str
                or _REQUEST_ID_RE.fullmatch(origin[6]) is None
                or type(origin[0]) is not classifier_descriptor_pins[1][1]
            ):
                _fail()
            origin_entry = origin[0]
            cleanup_request_id = origin[6]
            if type(prepared) is not PreparedRequest:
                _fail()
            prepared_values: list[object] = []
            for slot in _PREPARED_SLOTS:
                prepared_values.append(
                    native_member_get(slot, prepared, PreparedRequest)
                )
            prepared_id, required, count = prepared_values
            if not _class_matches(PreparedRequest, _PREPARED_PIN):
                _fail()
            selection_items = object.__getattribute__(selection, "items")
            if (
                type(prepared_id) is not str
                or _REQUEST_ID_RE.fullmatch(prepared_id) is None
                or required is not True
                or type(count) is not int
                or isinstance(count, bool)
                or type(selection_items) is not tuple
                or count != len(selection_items)
                or origin[4] is not lease
                or origin[5] is not binding
                or cleanup_request_id != prepared_id
                or origin[7] != lease_id
                or origin[8] != identity[2]
                or origin[11] != page_bytes
                or origin[3] is not selection
            ):
                _fail()
            # Capture and validate the genuine service-owned request ID before
            # the first post-return generation check.  A close callback at the
            # return boundary can then revoke that exact partial allocation.
            self._checkpoint(call_token)
            frozen = _TRUSTED_ENCODE(
                rpc_request_id,
                {
                    "request_id": prepared_id,
                    "selected_count": count,
                    "requires_private_confirmation": required,
                },
            )
            self._assert_pins()
            self._checkpoint(call_token)
            if type(frozen) is not bytes or not frozen or len(frozen) > MCP_CONTEXT_RESULT_MAX_BYTES:
                _fail()
            binding_projection = _binding_projection(binding)
            recipient = (
                binding_projection[2], binding_projection[6], binding_projection[0],
                binding_projection[5], binding_projection[3], binding_projection[4],
                binding_projection[7],
            )
            record = _RequestRecord(
                cleanup_request_id, lease_id, identity, chat_session_id, binding, lease,
                selection, page_bytes, call_token[1], origin,
            )
            record_anchor_values: list[object] = []
            for record_slot in record_slots:
                record_anchor_values.append(
                    native_member_get(record_slot, record, record_type)
                )
            (
                record_request_id_anchor, record_lease_id_anchor,
                record_identity_anchor, record_chat_session_id_anchor,
                record_binding_anchor, record_lease_anchor,
                record_selection_anchor, record_page_bytes_anchor,
                record_generation_anchor, record_origin_anchor,
            ) = record_anchor_values
            rejection_capability = rejection_capability_anchor
            if type(rejection_capability) is not tuple or len(rejection_capability) != 11:
                _fail()
            (
                rejection_service, rejection_capsule, rejection_lock,
                rejection_entries, rejection_permits, rejection_exchanges,
                rejection_completions, rejection_native_completions,
                rejection_authority, rejection_execution_local, rejection_binding_issuer,
            ) = rejection_capability
            rejection_service_type = type(rejection_service)
            rejection_lock_type = type(rejection_lock)
            rejection_authority_type = type(rejection_authority)
            rejection_execution_local_type = type(rejection_execution_local)
            origin_issued_type = type(origin[16])
            origin_deadline_type = type(origin[17])
            classification = native_function_type(
                classifier_code,
                classifier_globals,
                classifier_name,
                classifier_defaults,
            )(
                rejection_capability_anchor,
                prepared_id,
                lease_id,
                lease,
                binding,
                identity[2],
                recipient,
                selection,
                page_bytes,
                origin,
            )
            descriptor_pins_intact = True
            for global_name, value_type, descriptor_pins in classifier_descriptor_pins:
                if _confirmation_module.__dict__.get(global_name, _MISSING) is not value_type:
                    descriptor_pins_intact = False
                    break
                value_namespace = native_type_getattribute(value_type, "__dict__")
                for descriptor_name, descriptor in descriptor_pins:
                    if value_namespace.get(descriptor_name, _MISSING) is not descriptor:
                        descriptor_pins_intact = False
                        break
                if not descriptor_pins_intact:
                    break
            if (
                type(classifier_function) is not native_function_type
                or classifier_function.__code__ is not classifier_code
                or classifier_function.__defaults__ is not classifier_defaults
                or classifier_function.__globals__ is not classifier_globals
                or classifier_function.__name__ != classifier_name
                or classifier_function.__closure__ is not None
                or classifier_function.__kwdefaults__ is not None
                or _confirmation_module._classify_pristine_intent
                    is not classifier_function
                or _confirmation_module._PRISTINE_INTENT_SENTINEL
                    is not classifier_sentinel
                or _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
                    is not classifier_descriptor_pins
                or FunctionType is not native_function_type
                or not descriptor_pins_intact
                or classification is not classifier_sentinel
            ):
                _fail()
            # FINAL_NATIVE_RUNTIME_OWNERSHIP_GUARD_START
            runtime_record_pins_intact = True
            runtime_namespace = native_type_getattribute(runtime_type, "__dict__")
            for runtime_name, runtime_expected in runtime_pin:
                if runtime_namespace.get(runtime_name, _MISSING) is not runtime_expected:
                    runtime_record_pins_intact = False
                    break
            record_namespace = native_type_getattribute(record_type, "__dict__")
            if runtime_record_pins_intact:
                for record_name, record_expected in record_pin:
                    if record_namespace.get(record_name, _MISSING) is not record_expected:
                        runtime_record_pins_intact = False
                        break
            if not runtime_record_pins_intact:
                _fail()
            current_record_values: list[object] = []
            for record_slot in record_slots:
                current_record_values.append(
                    native_member_get(record_slot, record, record_type)
                )
            current_identity = current_record_values[2]
            if (
                type(current_record_values[0]) is not str
                or type(current_record_values[1]) is not str
                or type(current_identity) is not tuple
                or len(current_identity) != 6
                or type(current_identity[0]) is not str
                or type(current_identity[1]) is not str
                or type(current_identity[2]) is not str
                or type(current_identity[3]) is not int
                or isinstance(current_identity[3], bool)
                or type(current_identity[4]) is not int
                or isinstance(current_identity[4], bool)
                or type(current_identity[5]) is not str
                or type(current_record_values[3]) is not str
                or type(current_record_values[7]) is not int
                or isinstance(current_record_values[7], bool)
                or type(current_record_values[8]) is not int
                or isinstance(current_record_values[8], bool)
                or type(current_record_values[9]) is not tuple
                or len(current_record_values[9]) != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
            ):
                _fail()
            if (
                current_record_values[0] != record_request_id_anchor
                or current_record_values[1] != record_lease_id_anchor
                or current_identity is not record_identity_anchor
                or current_identity[0] != record_identity_anchor[0]
                or current_identity[1] != record_identity_anchor[1]
                or current_identity[2] != record_identity_anchor[2]
                or current_identity[3] != record_identity_anchor[3]
                or current_identity[4] != record_identity_anchor[4]
                or current_identity[5] != record_identity_anchor[5]
                or current_record_values[3] != record_chat_session_id_anchor
                or current_record_values[4] is not record_binding_anchor
                or current_record_values[5] is not record_lease_anchor
                or current_record_values[6] is not record_selection_anchor
                or current_record_values[7] != record_page_bytes_anchor
                or current_record_values[8] != record_generation_anchor
                or current_record_values[9] is not record_origin_anchor
            ):
                _fail()
            # FINAL_NATIVE_RUNTIME_OWNERSHIP_GUARD_END
            # FINAL_PREPARE_LIVE_STATE_GATE_START
            live_state_ok = True
            capsule_type = classifier_descriptor_pins[0][1]
            capsule_slot_pins = classifier_descriptor_pins[0][2]
            entry_type = classifier_descriptor_pins[1][1]
            entry_slot_pins = classifier_descriptor_pins[1][2]
            permit_row_type = classifier_descriptor_pins[2][1]
            permit_entry_ref_slot = classifier_descriptor_pins[2][2][0][1]
            exchange_row_type = classifier_descriptor_pins[3][1]
            exchange_request_id_slot = classifier_descriptor_pins[3][2][0][1]
            completion_row_type = classifier_descriptor_pins[4][1]
            completion_entry_ref_slot = classifier_descriptor_pins[4][2][0][1]
            native_completion_row_type = classifier_descriptor_pins[5][1]
            native_completion_entry_ref_slot = classifier_descriptor_pins[5][2][0][1]
            lease_type = classifier_descriptor_pins[6][1]
            lease_slot_pins = classifier_descriptor_pins[6][2]
            binding_type = classifier_descriptor_pins[7][1]
            binding_slot_pins = classifier_descriptor_pins[7][2]
            snapshot_type = classifier_descriptor_pins[8][1]
            snapshot_slot_pins = classifier_descriptor_pins[8][2]
            item_type = classifier_descriptor_pins[9][1]
            item_slot_pins = classifier_descriptor_pins[9][2]
            metadata_type = classifier_descriptor_pins[10][1]
            metadata_slot_pins = classifier_descriptor_pins[10][2]
            selection_types_pin = origin[19][0]
            selection_states_pin = origin[19][1]
            with rejection_lock:
                service_instance = native_object_getattribute(
                    rejection_service, "__dict__",
                )
                if (
                    type(rejection_service) is not rejection_service_type
                    or rejection_capability_anchor is not rejection_capability
                    or rejection_service is not runtime_service_anchor
                    or type(rejection_capsule) is not capsule_type
                    or type(rejection_lock) is not rejection_lock_type
                    or type(rejection_entries) is not dict
                    or type(rejection_permits) is not dict
                    or type(rejection_exchanges) is not dict
                    or type(rejection_completions) is not dict
                    or type(rejection_native_completions) is not dict
                    or type(rejection_authority) is not rejection_authority_type
                    or rejection_authority is not runtime_authority_anchor
                    or type(rejection_execution_local) is not rejection_execution_local_type
                    or type(service_instance) is not dict
                    or dict.get(service_instance, "_native_rejection_capsule")
                        is not rejection_capsule
                    or dict.get(service_instance, "_lock") is not rejection_lock
                    or dict.get(service_instance, "_entries") is not rejection_entries
                    or dict.get(service_instance, "_permits") is not rejection_permits
                    or dict.get(service_instance, "_exchanges") is not rejection_exchanges
                    or dict.get(service_instance, "_completions") is not rejection_completions
                    or dict.get(service_instance, "_native_completions")
                        is not rejection_native_completions
                    or dict.get(service_instance, "_authority") is not rejection_authority
                    or dict.get(service_instance, "_execution_local")
                        is not rejection_execution_local
                    or dict.get(service_instance, "_closed") is not False
                    or dict.get(service_instance, "_selection_types_pin")
                        is not selection_types_pin
                    or dict.get(service_instance, "_selection_states_pin")
                        is not selection_states_pin
                    or type(selection_types_pin) is not tuple
                    or len(selection_types_pin) != 3
                    or type(selection_states_pin) is not tuple
                    or len(selection_states_pin) != 3
                ):
                    live_state_ok = False
                capsule_values: list[object] = []
                if live_state_ok:
                    for _capsule_name, capsule_slot in capsule_slot_pins:
                        capsule_values.append(
                            native_member_get(capsule_slot, rejection_capsule, capsule_type)
                        )
                    if (
                        len(capsule_values) != 9
                        or type(capsule_values[0]) is not native_reference_type
                        or capsule_values[0]() is not rejection_service
                    ):
                        live_state_ok = False
                    else:
                        capsule_index = 1
                        while capsule_index < 9:
                            if capsule_values[capsule_index] is not rejection_capability[capsule_index + 1]:
                                live_state_ok = False
                                break
                            capsule_index += 1
                entry = dict.get(rejection_entries, cleanup_request_id)
                entry_values: list[object] = []
                if live_state_ok:
                    if type(entry) is not entry_type or entry is not origin_entry:
                        live_state_ok = False
                    else:
                        for _entry_name, entry_slot in entry_slot_pins:
                            entry_values.append(native_member_get(entry_slot, entry, entry_type))
                if live_state_ok:
                    optional_index = 15
                    while optional_index <= 35:
                        if entry_values[optional_index] is not None:
                            live_state_ok = False
                            break
                        optional_index += 1
                if live_state_ok:
                    optional_index = 38
                    while optional_index <= 41:
                        if entry_values[optional_index] is not None:
                            live_state_ok = False
                            break
                        optional_index += 1
                if live_state_ok and (
                    len(entry_values) != 42
                    or type(entry_values[0]) is not str
                    or entry_values[0] != cleanup_request_id
                    or type(entry_values[1]) is not native_reference_type
                    or entry_values[1]() is not lease
                    or type(entry_values[2]) is not native_reference_type
                    or entry_values[2]() is not binding
                    or type(entry_values[3]) is not str
                    or entry_values[3] != identity[2]
                    or entry_values[4] is not selection
                    or type(entry_values[5]) is not str
                    or entry_values[5] != ""
                    or type(entry_values[6]) is not int
                    or isinstance(entry_values[6], bool)
                    or entry_values[6] != page_bytes
                    or entry_values[7] is not None
                    or entry_values[8] is not origin[1]
                    or entry_values[9] is not origin[2]
                    or type(entry_values[10]) is not origin_issued_type
                    or entry_values[10] is not origin[16]
                    or type(entry_values[11]) is not origin_deadline_type
                    or entry_values[11] is not origin[17]
                    or type(entry_values[12]) is not float
                    or entry_values[12] != origin[18]
                    or type(entry_values[13]) is not int
                    or isinstance(entry_values[13], bool)
                    or entry_values[13] != 0
                    or type(entry_values[14]) is not str
                    or entry_values[14] != "intent"
                    or type(entry_values[36]) is not str
                    or entry_values[36] != origin[12]
                    or entry_values[37] is not True
                ):
                    live_state_ok = False
                current_selection_projection: list[tuple[object, ...]] = []
                if live_state_ok:
                    selection_index = 0
                    while selection_index < 3:
                        selection_class = selection_types_pin[selection_index]
                        selection_state = selection_states_pin[selection_index]
                        if type(selection_class) is not type or type(selection_state) is not tuple or len(selection_state) != 8:
                            live_state_ok = False
                            break
                        selection_namespace = native_type_getattribute(selection_class, "__dict__")
                        selection_mro = native_type_getattribute(selection_class, "__mro__")
                        effective_new = _MISSING
                        effective_getattribute = _MISSING
                        if type(selection_mro) is not tuple or not selection_mro:
                            live_state_ok = False
                            break
                        for selection_base in selection_mro:
                            if type(selection_base) is not type:
                                live_state_ok = False
                                break
                            base_namespace = native_type_getattribute(selection_base, "__dict__")
                            if effective_new is _MISSING and "__new__" in base_namespace:
                                effective_new = base_namespace["__new__"]
                                if type(effective_new) is native_staticmethod_type:
                                    effective_new = native_staticmethod_get(
                                        effective_new, None, selection_class,
                                    )
                            if (
                                effective_getattribute is _MISSING
                                and "__getattribute__" in base_namespace
                            ):
                                effective_getattribute = base_namespace["__getattribute__"]
                                if type(effective_getattribute) is native_staticmethod_type:
                                    effective_getattribute = native_staticmethod_get(
                                        effective_getattribute, None, selection_class,
                                    )
                        if not live_state_ok:
                            break
                        if (
                            selection_namespace.get("__init__") is not selection_state[0]
                            or selection_namespace.get("__new__") is not selection_state[1]
                            or effective_new is not selection_state[2]
                            or selection_namespace.get("__getattribute__") is not selection_state[3]
                            or effective_getattribute is not selection_state[4]
                            or selection_namespace.get("__post_init__") is not selection_state[5]
                            or selection_namespace.get("__setattr__") is not selection_state[6]
                            or type(selection_state[7]) is not tuple
                        ):
                            live_state_ok = False
                            break
                        for selection_slot in selection_state[7]:
                            if type(selection_slot) is not native_member_descriptor_type:
                                live_state_ok = False
                                break
                        if not live_state_ok:
                            break
                        selection_index += 1
                if live_state_ok:
                    if type(selection) is not selection_types_pin[0] or len(selection_states_pin[0][7]) != 1:
                        live_state_ok = False
                    else:
                        current_selection_items = native_member_get(
                            selection_states_pin[0][7][0], selection, selection_types_pin[0],
                        )
                        if type(current_selection_items) is not tuple or len(current_selection_items) != len(origin[10]):
                            live_state_ok = False
                if live_state_ok:
                    selection_item_index = 0
                    while selection_item_index < len(current_selection_items):
                        selection_item = current_selection_items[selection_item_index]
                        if type(selection_item) is not selection_types_pin[1] or len(selection_states_pin[1][7]) != 4:
                            live_state_ok = False
                            break
                        selection_values: list[object] = []
                        for selection_slot in selection_states_pin[1][7]:
                            selection_values.append(native_member_get(
                                selection_slot, selection_item, selection_types_pin[1],
                            ))
                        source, selection_ref, revision_ref, locator = selection_values
                        if type(source) is not str or type(selection_ref) is not str or type(revision_ref) is not str:
                            live_state_ok = False
                            break
                        if locator is None:
                            locator_projection: object = None
                        else:
                            if type(locator) is not selection_types_pin[2] or len(selection_states_pin[2][7]) != 3:
                                live_state_ok = False
                                break
                            locator_values: list[object] = []
                            for locator_slot in selection_states_pin[2][7]:
                                locator_values.append(native_member_get(
                                    locator_slot, locator, selection_types_pin[2],
                                ))
                            locator_value_index = 0
                            while locator_value_index < 3:
                                if type(locator_values[locator_value_index]) is not str:
                                    live_state_ok = False
                                    break
                                locator_value_index += 1
                            if not live_state_ok:
                                break
                            locator_projection = tuple(locator_values)
                        selection_row = (source, selection_ref, revision_ref, locator_projection)
                        if selection_row != origin[10][selection_item_index]:
                            live_state_ok = False
                            break
                        current_selection_projection.append(selection_row)
                        selection_item_index += 1
                snapshot = entry_values[8] if len(entry_values) == 42 else None
                if live_state_ok:
                    if type(snapshot) is not snapshot_type or snapshot is not origin[1] or len(snapshot_slot_pins) != 3:
                        live_state_ok = False
                    else:
                        snapshot_values: list[object] = []
                        for _snapshot_name, snapshot_slot in snapshot_slot_pins:
                            snapshot_values.append(native_member_get(snapshot_slot, snapshot, snapshot_type))
                        snapshot_items, snapshot_targets, snapshot_metadata = snapshot_values
                        if (
                            type(snapshot_items) is not tuple
                            or type(snapshot_targets) is not tuple
                            or len(snapshot_items) != len(origin[13])
                            or len(snapshot_targets) != len(origin[14])
                            or snapshot_metadata is not origin[2]
                            or type(snapshot_metadata) is not metadata_type
                        ):
                            live_state_ok = False
                if live_state_ok:
                    snapshot_index = 0
                    while snapshot_index < len(snapshot_items):
                        snapshot_item = snapshot_items[snapshot_index]
                        if type(snapshot_item) is not item_type or len(item_slot_pins) != 8:
                            live_state_ok = False
                            break
                        snapshot_row: list[object] = []
                        for _item_name, item_slot in item_slot_pins:
                            snapshot_row.append(native_member_get(item_slot, snapshot_item, item_type))
                        snapshot_part_index = 0
                        while snapshot_part_index < 8:
                            if type(snapshot_row[snapshot_part_index]) is not str:
                                live_state_ok = False
                                break
                            snapshot_part_index += 1
                        if not live_state_ok or tuple(snapshot_row) != origin[13][snapshot_index]:
                            live_state_ok = False
                            break
                        target_digest = snapshot_targets[snapshot_index]
                        if type(target_digest) is not str or target_digest != origin[14][snapshot_index]:
                            live_state_ok = False
                            break
                        snapshot_index += 1
                if live_state_ok:
                    metadata_row: list[object] = []
                    for _metadata_name, metadata_slot in metadata_slot_pins:
                        metadata_row.append(native_member_get(
                            metadata_slot, snapshot_metadata, metadata_type,
                        ))
                    if len(metadata_row) != 9:
                        live_state_ok = False
                    else:
                        metadata_index = 0
                        while metadata_index < 9:
                            expected_metadata_type = int if metadata_index in (6, 7) else str
                            if (
                                type(metadata_row[metadata_index]) is not expected_metadata_type
                                or (
                                    expected_metadata_type is int
                                    and isinstance(metadata_row[metadata_index], bool)
                                )
                            ):
                                live_state_ok = False
                                break
                            metadata_index += 1
                    if live_state_ok and tuple(metadata_row) != origin[15]:
                        live_state_ok = False
                if live_state_ok:
                    if type(lease) is not lease_type or type(binding) is not binding_type:
                        live_state_ok = False
                    else:
                        lease_values: list[object] = []
                        for _lease_name, lease_slot in lease_slot_pins:
                            lease_values.append(native_member_get(lease_slot, lease, lease_type))
                        binding_values: list[object] = []
                        for _binding_name, binding_slot in binding_slot_pins:
                            binding_values.append(native_member_get(binding_slot, binding, binding_type))
                if live_state_ok and (
                        lease is not origin[4]
                        or binding is not origin[5]
                        or len(lease_values) != 3
                        or lease_values[0] is not rejection_authority
                        or lease_values[1] is not binding
                        or type(lease_values[2]) is not str
                        or lease_values[2] != lease_id
                        or len(binding_values) != 9
                        or binding_values[0] is not rejection_binding_issuer
                        or binding_values[1] is not rejection_binding_issuer
                        or tuple(binding_values[2:]) != recipient
                        or tuple(binding_values[2:]) != origin[9]
                ):
                    live_state_ok = False
                if live_state_ok:
                    for permit_row in tuple(dict.values(rejection_permits)):
                        if type(permit_row) is not permit_row_type:
                            live_state_ok = False
                            break
                        permit_entry_ref = native_member_get(
                            permit_entry_ref_slot, permit_row, permit_row_type,
                        )
                        if type(permit_entry_ref) is not native_reference_type or permit_entry_ref() is entry:
                            live_state_ok = False
                            break
                if live_state_ok:
                    for exchange_row in tuple(dict.values(rejection_exchanges)):
                        if type(exchange_row) is not exchange_row_type:
                            live_state_ok = False
                            break
                        exchange_request_id = native_member_get(
                            exchange_request_id_slot, exchange_row, exchange_row_type,
                        )
                        if type(exchange_request_id) is not str or exchange_request_id == cleanup_request_id:
                            live_state_ok = False
                            break
                if live_state_ok:
                    for completion_row in tuple(dict.values(rejection_completions)):
                        if type(completion_row) is not completion_row_type:
                            live_state_ok = False
                            break
                        completion_entry_ref = native_member_get(
                            completion_entry_ref_slot, completion_row, completion_row_type,
                        )
                        if type(completion_entry_ref) is not native_reference_type or completion_entry_ref() is entry:
                            live_state_ok = False
                            break
                if live_state_ok:
                    for native_completion_row in tuple(dict.values(rejection_native_completions)):
                        if type(native_completion_row) is not native_completion_row_type:
                            live_state_ok = False
                            break
                        native_completion_entry_ref = native_member_get(
                            native_completion_entry_ref_slot,
                            native_completion_row,
                            native_completion_row_type,
                        )
                        if (
                            type(native_completion_entry_ref) is not native_reference_type
                            or native_completion_entry_ref() is entry
                        ):
                            live_state_ok = False
                            break
                if live_state_ok and dict.get(rejection_entries, cleanup_request_id) is not entry:
                    live_state_ok = False
                if live_state_ok:
                    with runtime_lock_anchor:
                        final_runtime_values: list[object] = []
                        for runtime_slot in runtime_slots:
                            final_runtime_values.append(
                                native_member_get(runtime_slot, self, runtime_type)
                            )
                        authority_instance = native_getset_get(
                            authority_dict_slot, rejection_authority, authority_dict_type,
                        )
                        if (
                            type(authority_instance) is not dict
                            or any(type(key) is not str for key in tuple(dict.keys(authority_instance)))
                            or type(rejection_binding_issuer) is not object
                            or dict.get(authority_instance, "_binding_issuer")
                                is not rejection_binding_issuer
                            or type(final_runtime_values[6]) is not bool
                            or type(final_runtime_values[7]) is not int
                            or isinstance(final_runtime_values[7], bool)
                            or type(final_runtime_values[8]) is not int
                            or isinstance(final_runtime_values[8], bool)
                            or type(final_runtime_values[9]) is not dict
                            or type(final_runtime_values[10]) is not dict
                            or type(final_runtime_values[11]) is not set
                            or type(final_runtime_values[12]) is not dict
                            or type(final_runtime_values[13]) is not dict
                            or type(final_runtime_values[14]) is not tuple
                            or len(final_runtime_values[14]) != 11
                        ):
                            live_state_ok = False
                        final_runtime_index = 0
                        while live_state_ok and final_runtime_index < 6:
                            if final_runtime_values[final_runtime_index] is not runtime_values[final_runtime_index]:
                                live_state_ok = False
                            final_runtime_index += 1
                        final_runtime_index = 9
                        while live_state_ok and final_runtime_index < 15:
                            if final_runtime_values[final_runtime_index] is not runtime_values[final_runtime_index]:
                                live_state_ok = False
                            final_runtime_index += 1
                        pending = dict.get(runtime_pending_anchor, reservation)
                        if (
                            not live_state_ok
                            or final_runtime_values[6] is not False
                            or final_runtime_values[7] != runtime_generation_anchor
                            or final_runtime_values[8] != runtime_pid_anchor
                            or type(runtime_lock_anchor) is not runtime_lock_type
                            or type(call_token) is not tuple
                            or len(call_token) != 4
                            or type(call_token[1]) is not int
                            or isinstance(call_token[1], bool)
                            or call_token[1] != final_runtime_values[7]
                            or type(pending) is not tuple
                            or len(pending) != 2
                            or type(pending[0]) is not str
                            or type(pending[1]) is not str
                            or pending[0] != record_identity_anchor[2]
                            or pending[1] != record_lease_id_anchor
                            or dict.get(runtime_records_anchor, cleanup_request_id)
                                is not None
                            or dict.get(runtime_active_records_anchor, cleanup_request_id)
                                is not None
                            or record_lease_id_anchor not in runtime_lease_ids_anchor
                            or type(record) is not record_type
                            or current_record_values[0] != cleanup_request_id
                            or current_record_values[1] != record_lease_id_anchor
                            or current_record_values[2] is not record_identity_anchor
                            or current_record_values[3] != record_chat_session_id_anchor
                            or current_record_values[4] is not record_binding_anchor
                            or current_record_values[5] is not record_lease_anchor
                            or current_record_values[6] is not record_selection_anchor
                            or current_record_values[7] != record_page_bytes_anchor
                            or current_record_values[8] != final_runtime_values[7]
                            or current_record_values[9] is not record_origin_anchor
                        ):
                            live_state_ok = False
                        else:
                            runtime_records_anchor[cleanup_request_id] = record
                            runtime_pending_anchor.pop(reservation, None)
                            installed = True
            # FINAL_PREPARE_LIVE_STATE_GATE_END
            if not live_state_ok or not installed:
                _fail()
            return frozen
        except BaseException:
            if not installed:
                if cleanup_request_id is not None and origin_entry is not None:
                    try:
                        with rejection_capability_anchor[2]:
                            if (
                                dict.get(
                                    rejection_capability_anchor[3],
                                    cleanup_request_id,
                                ) is origin_entry
                            ):
                                trusted_cancel(
                                    runtime_coordinator_anchor,
                                    cleanup_request_id,
                                )
                    except BaseException:
                        pass
                if lease is not None:
                    try:
                        trusted_release_lease(
                            rejection_capability_anchor[8], lease,
                        )
                    except BaseException:
                        pass
                if binding is not None:
                    try:
                        trusted_release_binding(
                            rejection_capability_anchor[8], binding,
                        )
                    except BaseException:
                        pass
                if reservation is not None:
                    with runtime_lock_anchor:
                        runtime_pending_anchor.pop(reservation, None)
                        if type(lease_id) is str:
                            runtime_lease_ids_anchor.discard(lease_id)
            raise

    def _existing(
        self,
        method: str,
        rpc_request_id: str | int,
        arguments: MappingProxyType,
        identity: tuple[object, ...],
        proof: McpContextBearerProof | None,
        call_token: tuple[object, int, int, int],
    ) -> bytes:
        if proof is not None:
            _fail()
        native_function_type = _ORIGINAL_NATIVE_FUNCTION_TYPE
        classifier_function = _ORIGINAL_CLASSIFY_PRISTINE_INTENT
        classifier_code = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_CODE
        classifier_defaults = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_DEFAULTS
        classifier_globals = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_GLOBALS
        classifier_name = _ORIGINAL_CLASSIFY_PRISTINE_INTENT_NAME
        classifier_sentinel = _ORIGINAL_PRISTINE_INTENT_SENTINEL
        classifier_descriptor_pins = (
            _ORIGINAL_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
        )
        native_member_get = MemberDescriptorType.__get__
        native_member_descriptor_type = MemberDescriptorType
        native_object_getattribute = object.__getattribute__
        authority_dict_type = classifier_descriptor_pins[12][1]
        authority_dict_slot = classifier_descriptor_pins[12][2][0][1]
        native_type_getattribute = type.__getattribute__
        native_reference_type = ReferenceType
        native_staticmethod_type = staticmethod
        native_staticmethod_get = staticmethod.__get__
        trusted_cancel = _TRUSTED_CANCEL
        trusted_release_lease = _TRUSTED_RELEASE_LEASE
        trusted_release_binding = _TRUSTED_RELEASE_BINDING
        runtime_type = _TRUSTED_RUNTIME_CLASS
        runtime_pin = _RUNTIME_PIN
        runtime_slots = _RUNTIME_SLOTS
        record_type = _RequestRecord
        record_pin = _REQUEST_RECORD_PIN
        record_slots = _REQUEST_RECORD_SLOTS
        if type(self) is not runtime_type or len(runtime_slots) != 15:
            _fail()
        runtime_values: list[object] = []
        for runtime_slot in runtime_slots:
            runtime_values.append(native_member_get(runtime_slot, self, runtime_type))
        (
            runtime_authority_anchor, runtime_service_anchor,
            runtime_producer_anchor, runtime_cursor_anchor,
            runtime_coordinator_anchor, runtime_lock_anchor,
            runtime_closed_anchor, runtime_generation_anchor, runtime_pid_anchor,
            runtime_records_anchor, runtime_pending_anchor,
            runtime_lease_ids_anchor, runtime_active_records_anchor,
            runtime_active_threads_anchor, rejection_capability_anchor,
        ) = runtime_values
        runtime_lock_type = type(runtime_lock_anchor)
        if (
            type(runtime_closed_anchor) is not bool
            or type(runtime_generation_anchor) is not int
            or isinstance(runtime_generation_anchor, bool)
            or type(runtime_pid_anchor) is not int
            or isinstance(runtime_pid_anchor, bool)
            or type(runtime_records_anchor) is not dict
            or type(runtime_pending_anchor) is not dict
            or type(runtime_lease_ids_anchor) is not set
            or type(runtime_active_records_anchor) is not dict
            or type(runtime_active_threads_anchor) is not dict
            or type(rejection_capability_anchor) is not tuple
            or len(rejection_capability_anchor) != 11
        ):
            _fail()
        descriptor_pins_intact = True
        for global_name, value_type, descriptor_pins in classifier_descriptor_pins:
            if _confirmation_module.__dict__.get(global_name, _MISSING) is not value_type:
                descriptor_pins_intact = False
                break
            value_namespace = native_type_getattribute(value_type, "__dict__")
            for descriptor_name, descriptor in descriptor_pins:
                if value_namespace.get(descriptor_name, _MISSING) is not descriptor:
                    descriptor_pins_intact = False
                    break
            if not descriptor_pins_intact:
                break
        if (
            type(classifier_function) is not native_function_type
            or classifier_function.__code__ is not classifier_code
            or classifier_function.__defaults__ is not classifier_defaults
            or type(classifier_defaults) is not tuple
            or classifier_function.__globals__ is not classifier_globals
            or classifier_function.__name__ != classifier_name
            or classifier_function.__closure__ is not None
            or classifier_function.__kwdefaults__ is not None
            or _confirmation_module._classify_pristine_intent
                is not classifier_function
            or _confirmation_module._PRISTINE_INTENT_SENTINEL
                is not classifier_sentinel
            or _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
                is not classifier_descriptor_pins
            or FunctionType is not native_function_type
            or not descriptor_pins_intact
        ):
            _fail()
        native_getset_get = type(authority_dict_slot).__get__
        request_id = arguments["request_id"]
        if type(request_id) is not str:
            _fail()
        record, record_token = self._claim_record(request_id, identity)
        record_anchor_values: list[object] = []
        for record_slot in record_slots:
            record_anchor_values.append(
                native_member_get(record_slot, record, record_type)
            )
        (
            record_request_id_anchor, record_lease_id_anchor,
            record_identity_anchor, record_chat_session_id_anchor,
            record_binding_anchor, record_lease_anchor,
            record_selection_anchor, record_page_bytes_anchor,
            record_generation_anchor, record_origin_anchor,
        ) = record_anchor_values
        cleanup_origin = record_origin_anchor
        cleanup_entry = cleanup_origin[0]
        cleanup_lease = record_lease_anchor
        cleanup_binding = record_binding_anchor
        cleanup_lease_id = record_lease_id_anchor
        completed = False
        preserve_record = False
        content_capability = None
        content_native_lock = None
        content_native_leases = None
        content_native_key = None
        content_native_row = None
        content_native_cancel_sentinel = None
        content_cleanup_anchor = None
        try:
            if method != "harbour_context_page":
                self._record_live(record)
                self._checkpoint(call_token)
            if method == "harbour_context_manifest":
                self._assert_pins()
                binding_projection = _binding_projection(record_binding_anchor)
                recipient = (
                    binding_projection[2], binding_projection[6], binding_projection[0],
                    binding_projection[5], binding_projection[3], binding_projection[4],
                    binding_projection[7],
                )
                origin = record_origin_anchor
                rejection_capability = rejection_capability_anchor
                if type(rejection_capability) is not tuple or len(rejection_capability) != 11:
                    _fail()
                (
                    rejection_service, rejection_capsule, rejection_lock,
                    rejection_entries, rejection_permits, rejection_exchanges,
                    rejection_completions, rejection_native_completions,
                    rejection_authority, rejection_execution_local, rejection_binding_issuer,
                ) = rejection_capability
                rejection_service_type = type(rejection_service)
                rejection_lock_type = type(rejection_lock)
                rejection_authority_type = type(rejection_authority)
                rejection_execution_local_type = type(rejection_execution_local)
                origin_issued_type = type(origin[16])
                origin_deadline_type = type(origin[17])
                classification = native_function_type(
                    classifier_code,
                    classifier_globals,
                    classifier_name,
                    classifier_defaults,
                )(
                    rejection_capability_anchor,
                    record_request_id_anchor,
                    record_lease_id_anchor,
                    record_lease_anchor,
                    record_binding_anchor,
                    record_identity_anchor[2],
                    recipient,
                    record_selection_anchor,
                    record_page_bytes_anchor,
                    record_origin_anchor,
                )
                descriptor_pins_intact = True
                for global_name, value_type, descriptor_pins in classifier_descriptor_pins:
                    if _confirmation_module.__dict__.get(global_name, _MISSING) is not value_type:
                        descriptor_pins_intact = False
                        break
                    value_namespace = native_type_getattribute(value_type, "__dict__")
                    for descriptor_name, descriptor in descriptor_pins:
                        if value_namespace.get(descriptor_name, _MISSING) is not descriptor:
                            descriptor_pins_intact = False
                            break
                    if not descriptor_pins_intact:
                        break
                if (
                    type(classifier_function) is not native_function_type
                    or classifier_function.__code__ is not classifier_code
                    or classifier_function.__defaults__ is not classifier_defaults
                    or classifier_function.__globals__ is not classifier_globals
                    or classifier_function.__name__ != classifier_name
                    or classifier_function.__closure__ is not None
                    or classifier_function.__kwdefaults__ is not None
                    or _confirmation_module._classify_pristine_intent
                        is not classifier_function
                    or _confirmation_module._PRISTINE_INTENT_SENTINEL
                        is not classifier_sentinel
                    or _confirmation_module._PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS
                        is not classifier_descriptor_pins
                    or FunctionType is not native_function_type
                    or not descriptor_pins_intact
                ):
                    _fail()
                # FINAL_NATIVE_RUNTIME_OWNERSHIP_GUARD_START
                runtime_record_pins_intact = True
                runtime_namespace = native_type_getattribute(runtime_type, "__dict__")
                for runtime_name, runtime_expected in runtime_pin:
                    if runtime_namespace.get(runtime_name, _MISSING) is not runtime_expected:
                        runtime_record_pins_intact = False
                        break
                record_namespace = native_type_getattribute(record_type, "__dict__")
                if runtime_record_pins_intact:
                    for record_name, record_expected in record_pin:
                        if record_namespace.get(record_name, _MISSING) is not record_expected:
                            runtime_record_pins_intact = False
                            break
                if not runtime_record_pins_intact:
                    _fail()
                current_record_values: list[object] = []
                for record_slot in record_slots:
                    current_record_values.append(
                        native_member_get(record_slot, record, record_type)
                    )
                current_identity = current_record_values[2]
                if (
                    type(current_record_values[0]) is not str
                    or type(current_record_values[1]) is not str
                    or type(current_identity) is not tuple
                    or len(current_identity) != 6
                    or type(current_identity[0]) is not str
                    or type(current_identity[1]) is not str
                    or type(current_identity[2]) is not str
                    or type(current_identity[3]) is not int
                    or isinstance(current_identity[3], bool)
                    or type(current_identity[4]) is not int
                    or isinstance(current_identity[4], bool)
                    or type(current_identity[5]) is not str
                    or type(current_record_values[3]) is not str
                    or type(current_record_values[7]) is not int
                    or isinstance(current_record_values[7], bool)
                    or type(current_record_values[8]) is not int
                    or isinstance(current_record_values[8], bool)
                    or type(current_record_values[9]) is not tuple
                    or len(current_record_values[9]) != _ORIGINAL_PRISTINE_INTENT_ORIGIN_SIZE
                ):
                    _fail()
                if (
                    current_record_values[0] != record_request_id_anchor
                    or current_record_values[1] != record_lease_id_anchor
                    or current_identity is not record_identity_anchor
                    or current_identity[0] != record_identity_anchor[0]
                    or current_identity[1] != record_identity_anchor[1]
                    or current_identity[2] != record_identity_anchor[2]
                    or current_identity[3] != record_identity_anchor[3]
                    or current_identity[4] != record_identity_anchor[4]
                    or current_identity[5] != record_identity_anchor[5]
                    or current_record_values[3] != record_chat_session_id_anchor
                    or current_record_values[4] is not record_binding_anchor
                    or current_record_values[5] is not record_lease_anchor
                    or current_record_values[6] is not record_selection_anchor
                    or current_record_values[7] != record_page_bytes_anchor
                    or current_record_values[8] != record_generation_anchor
                    or current_record_values[9] is not record_origin_anchor
                ):
                    _fail()
                # FINAL_NATIVE_RUNTIME_OWNERSHIP_GUARD_END
                if classification is not None and classification is not classifier_sentinel:
                    _fail()
                if classification is classifier_sentinel:
                    # FINAL_EXISTING_LIVE_STATE_GATE_START
                    live_state_ok = True
                    capsule_type = classifier_descriptor_pins[0][1]
                    capsule_slot_pins = classifier_descriptor_pins[0][2]
                    entry_type = classifier_descriptor_pins[1][1]
                    entry_slot_pins = classifier_descriptor_pins[1][2]
                    permit_row_type = classifier_descriptor_pins[2][1]
                    permit_entry_ref_slot = classifier_descriptor_pins[2][2][0][1]
                    exchange_row_type = classifier_descriptor_pins[3][1]
                    exchange_request_id_slot = classifier_descriptor_pins[3][2][0][1]
                    completion_row_type = classifier_descriptor_pins[4][1]
                    completion_entry_ref_slot = classifier_descriptor_pins[4][2][0][1]
                    native_completion_row_type = classifier_descriptor_pins[5][1]
                    native_completion_entry_ref_slot = classifier_descriptor_pins[5][2][0][1]
                    lease_type = classifier_descriptor_pins[6][1]
                    lease_slot_pins = classifier_descriptor_pins[6][2]
                    binding_type = classifier_descriptor_pins[7][1]
                    binding_slot_pins = classifier_descriptor_pins[7][2]
                    snapshot_type = classifier_descriptor_pins[8][1]
                    snapshot_slot_pins = classifier_descriptor_pins[8][2]
                    item_type = classifier_descriptor_pins[9][1]
                    item_slot_pins = classifier_descriptor_pins[9][2]
                    metadata_type = classifier_descriptor_pins[10][1]
                    metadata_slot_pins = classifier_descriptor_pins[10][2]
                    selection_types_pin = origin[19][0]
                    selection_states_pin = origin[19][1]
                    with rejection_lock:
                        service_instance = native_object_getattribute(
                            rejection_service, "__dict__",
                        )
                        if (
                            type(rejection_service) is not rejection_service_type
                            or rejection_capability_anchor is not rejection_capability
                            or rejection_service is not runtime_service_anchor
                            or type(rejection_capsule) is not capsule_type
                            or type(rejection_lock) is not rejection_lock_type
                            or type(rejection_entries) is not dict
                            or type(rejection_permits) is not dict
                            or type(rejection_exchanges) is not dict
                            or type(rejection_completions) is not dict
                            or type(rejection_native_completions) is not dict
                            or type(rejection_authority) is not rejection_authority_type
                            or rejection_authority is not runtime_authority_anchor
                            or type(rejection_execution_local) is not rejection_execution_local_type
                            or type(service_instance) is not dict
                            or dict.get(service_instance, "_native_rejection_capsule")
                                is not rejection_capsule
                            or dict.get(service_instance, "_lock") is not rejection_lock
                            or dict.get(service_instance, "_entries") is not rejection_entries
                            or dict.get(service_instance, "_permits") is not rejection_permits
                            or dict.get(service_instance, "_exchanges") is not rejection_exchanges
                            or dict.get(service_instance, "_completions") is not rejection_completions
                            or dict.get(service_instance, "_native_completions")
                                is not rejection_native_completions
                            or dict.get(service_instance, "_authority") is not rejection_authority
                            or dict.get(service_instance, "_execution_local")
                                is not rejection_execution_local
                            or dict.get(service_instance, "_closed") is not False
                            or dict.get(service_instance, "_selection_types_pin")
                                is not selection_types_pin
                            or dict.get(service_instance, "_selection_states_pin")
                                is not selection_states_pin
                            or type(selection_types_pin) is not tuple
                            or len(selection_types_pin) != 3
                            or type(selection_states_pin) is not tuple
                            or len(selection_states_pin) != 3
                        ):
                            live_state_ok = False
                        capsule_values: list[object] = []
                        if live_state_ok:
                            for _capsule_name, capsule_slot in capsule_slot_pins:
                                capsule_values.append(
                                    native_member_get(capsule_slot, rejection_capsule, capsule_type)
                                )
                            if (
                                len(capsule_values) != 9
                                or type(capsule_values[0]) is not native_reference_type
                                or capsule_values[0]() is not rejection_service
                            ):
                                live_state_ok = False
                            else:
                                capsule_index = 1
                                while capsule_index < 9:
                                    if capsule_values[capsule_index] is not rejection_capability[capsule_index + 1]:
                                        live_state_ok = False
                                        break
                                    capsule_index += 1
                        entry = dict.get(rejection_entries, request_id)
                        entry_values: list[object] = []
                        if live_state_ok:
                            if type(entry) is not entry_type or entry is not origin[0]:
                                live_state_ok = False
                            else:
                                for _entry_name, entry_slot in entry_slot_pins:
                                    entry_values.append(native_member_get(entry_slot, entry, entry_type))
                        if live_state_ok:
                            optional_index = 15
                            while optional_index <= 35:
                                if entry_values[optional_index] is not None:
                                    live_state_ok = False
                                    break
                                optional_index += 1
                        if live_state_ok:
                            optional_index = 38
                            while optional_index <= 41:
                                if entry_values[optional_index] is not None:
                                    live_state_ok = False
                                    break
                                optional_index += 1
                        if live_state_ok and (
                            len(entry_values) != 42
                            or type(entry_values[0]) is not str
                            or entry_values[0] != request_id
                            or type(entry_values[1]) is not native_reference_type
                            or entry_values[1]() is not record_lease_anchor
                            or type(entry_values[2]) is not native_reference_type
                            or entry_values[2]() is not record_binding_anchor
                            or type(entry_values[3]) is not str
                            or entry_values[3] != identity[2]
                            or entry_values[4] is not record_selection_anchor
                            or type(entry_values[5]) is not str
                            or entry_values[5] != ""
                            or type(entry_values[6]) is not int
                            or isinstance(entry_values[6], bool)
                            or entry_values[6] != record_page_bytes_anchor
                            or entry_values[7] is not None
                            or entry_values[8] is not origin[1]
                            or entry_values[9] is not origin[2]
                            or type(entry_values[10]) is not origin_issued_type
                            or entry_values[10] is not origin[16]
                            or type(entry_values[11]) is not origin_deadline_type
                            or entry_values[11] is not origin[17]
                            or type(entry_values[12]) is not float
                            or entry_values[12] != origin[18]
                            or type(entry_values[13]) is not int
                            or isinstance(entry_values[13], bool)
                            or entry_values[13] != 0
                            or type(entry_values[14]) is not str
                            or entry_values[14] != "intent"
                            or type(entry_values[36]) is not str
                            or entry_values[36] != origin[12]
                            or entry_values[37] is not True
                        ):
                            live_state_ok = False
                        current_selection_projection: list[tuple[object, ...]] = []
                        if live_state_ok:
                            selection_index = 0
                            while selection_index < 3:
                                selection_class = selection_types_pin[selection_index]
                                selection_state = selection_states_pin[selection_index]
                                if type(selection_class) is not type or type(selection_state) is not tuple or len(selection_state) != 8:
                                    live_state_ok = False
                                    break
                                selection_namespace = native_type_getattribute(selection_class, "__dict__")
                                selection_mro = native_type_getattribute(selection_class, "__mro__")
                                effective_new = _MISSING
                                effective_getattribute = _MISSING
                                if type(selection_mro) is not tuple or not selection_mro:
                                    live_state_ok = False
                                    break
                                for selection_base in selection_mro:
                                    if type(selection_base) is not type:
                                        live_state_ok = False
                                        break
                                    base_namespace = native_type_getattribute(selection_base, "__dict__")
                                    if effective_new is _MISSING and "__new__" in base_namespace:
                                        effective_new = base_namespace["__new__"]
                                        if type(effective_new) is native_staticmethod_type:
                                            effective_new = native_staticmethod_get(
                                                effective_new, None, selection_class,
                                            )
                                    if (
                                        effective_getattribute is _MISSING
                                        and "__getattribute__" in base_namespace
                                    ):
                                        effective_getattribute = base_namespace["__getattribute__"]
                                        if type(effective_getattribute) is native_staticmethod_type:
                                            effective_getattribute = native_staticmethod_get(
                                                effective_getattribute, None, selection_class,
                                            )
                                if not live_state_ok:
                                    break
                                if (
                                    selection_namespace.get("__init__") is not selection_state[0]
                                    or selection_namespace.get("__new__") is not selection_state[1]
                                    or effective_new is not selection_state[2]
                                    or selection_namespace.get("__getattribute__") is not selection_state[3]
                                    or effective_getattribute is not selection_state[4]
                                    or selection_namespace.get("__post_init__") is not selection_state[5]
                                    or selection_namespace.get("__setattr__") is not selection_state[6]
                                    or type(selection_state[7]) is not tuple
                                ):
                                    live_state_ok = False
                                    break
                                for selection_slot in selection_state[7]:
                                    if type(selection_slot) is not native_member_descriptor_type:
                                        live_state_ok = False
                                        break
                                if not live_state_ok:
                                    break
                                selection_index += 1
                        if live_state_ok:
                            if type(record_selection_anchor) is not selection_types_pin[0] or len(selection_states_pin[0][7]) != 1:
                                live_state_ok = False
                            else:
                                current_selection_items = native_member_get(
                                    selection_states_pin[0][7][0],
                                    record_selection_anchor,
                                    selection_types_pin[0],
                                )
                                if type(current_selection_items) is not tuple or len(current_selection_items) != len(origin[10]):
                                    live_state_ok = False
                        if live_state_ok:
                            selection_item_index = 0
                            while selection_item_index < len(current_selection_items):
                                selection_item = current_selection_items[selection_item_index]
                                if type(selection_item) is not selection_types_pin[1] or len(selection_states_pin[1][7]) != 4:
                                    live_state_ok = False
                                    break
                                selection_values: list[object] = []
                                for selection_slot in selection_states_pin[1][7]:
                                    selection_values.append(native_member_get(
                                        selection_slot, selection_item, selection_types_pin[1],
                                    ))
                                source, selection_ref, revision_ref, locator = selection_values
                                if type(source) is not str or type(selection_ref) is not str or type(revision_ref) is not str:
                                    live_state_ok = False
                                    break
                                if locator is None:
                                    locator_projection: object = None
                                else:
                                    if type(locator) is not selection_types_pin[2] or len(selection_states_pin[2][7]) != 3:
                                        live_state_ok = False
                                        break
                                    locator_values: list[object] = []
                                    for locator_slot in selection_states_pin[2][7]:
                                        locator_values.append(native_member_get(
                                            locator_slot, locator, selection_types_pin[2],
                                        ))
                                    locator_value_index = 0
                                    while locator_value_index < 3:
                                        if type(locator_values[locator_value_index]) is not str:
                                            live_state_ok = False
                                            break
                                        locator_value_index += 1
                                    if not live_state_ok:
                                        break
                                    locator_projection = tuple(locator_values)
                                selection_row = (source, selection_ref, revision_ref, locator_projection)
                                if selection_row != origin[10][selection_item_index]:
                                    live_state_ok = False
                                    break
                                current_selection_projection.append(selection_row)
                                selection_item_index += 1
                        snapshot = entry_values[8] if len(entry_values) == 42 else None
                        if live_state_ok:
                            if type(snapshot) is not snapshot_type or snapshot is not origin[1] or len(snapshot_slot_pins) != 3:
                                live_state_ok = False
                            else:
                                snapshot_values: list[object] = []
                                for _snapshot_name, snapshot_slot in snapshot_slot_pins:
                                    snapshot_values.append(native_member_get(snapshot_slot, snapshot, snapshot_type))
                                snapshot_items, snapshot_targets, snapshot_metadata = snapshot_values
                                if (
                                    type(snapshot_items) is not tuple
                                    or type(snapshot_targets) is not tuple
                                    or len(snapshot_items) != len(origin[13])
                                    or len(snapshot_targets) != len(origin[14])
                                    or snapshot_metadata is not origin[2]
                                    or type(snapshot_metadata) is not metadata_type
                                ):
                                    live_state_ok = False
                        if live_state_ok:
                            snapshot_index = 0
                            while snapshot_index < len(snapshot_items):
                                snapshot_item = snapshot_items[snapshot_index]
                                if type(snapshot_item) is not item_type or len(item_slot_pins) != 8:
                                    live_state_ok = False
                                    break
                                snapshot_row: list[object] = []
                                for _item_name, item_slot in item_slot_pins:
                                    snapshot_row.append(native_member_get(item_slot, snapshot_item, item_type))
                                snapshot_part_index = 0
                                while snapshot_part_index < 8:
                                    if type(snapshot_row[snapshot_part_index]) is not str:
                                        live_state_ok = False
                                        break
                                    snapshot_part_index += 1
                                if not live_state_ok or tuple(snapshot_row) != origin[13][snapshot_index]:
                                    live_state_ok = False
                                    break
                                target_digest = snapshot_targets[snapshot_index]
                                if type(target_digest) is not str or target_digest != origin[14][snapshot_index]:
                                    live_state_ok = False
                                    break
                                snapshot_index += 1
                        if live_state_ok:
                            metadata_row: list[object] = []
                            for _metadata_name, metadata_slot in metadata_slot_pins:
                                metadata_row.append(native_member_get(
                                    metadata_slot, snapshot_metadata, metadata_type,
                                ))
                            if len(metadata_row) != 9:
                                live_state_ok = False
                            else:
                                metadata_index = 0
                                while metadata_index < 9:
                                    expected_metadata_type = int if metadata_index in (6, 7) else str
                                    if (
                                        type(metadata_row[metadata_index]) is not expected_metadata_type
                                        or (
                                            expected_metadata_type is int
                                            and isinstance(metadata_row[metadata_index], bool)
                                        )
                                    ):
                                        live_state_ok = False
                                        break
                                    metadata_index += 1
                            if live_state_ok and tuple(metadata_row) != origin[15]:
                                live_state_ok = False
                        if live_state_ok:
                            if (
                                type(record_lease_anchor) is not lease_type
                                or type(record_binding_anchor) is not binding_type
                            ):
                                live_state_ok = False
                            else:
                                lease_values: list[object] = []
                                for _lease_name, lease_slot in lease_slot_pins:
                                    lease_values.append(native_member_get(
                                        lease_slot, record_lease_anchor, lease_type,
                                    ))
                                binding_values: list[object] = []
                                for _binding_name, binding_slot in binding_slot_pins:
                                    binding_values.append(native_member_get(
                                        binding_slot, record_binding_anchor, binding_type,
                                    ))
                        if live_state_ok and (
                                record_lease_anchor is not origin[4]
                                or record_binding_anchor is not origin[5]
                                or len(lease_values) != 3
                                or lease_values[0] is not rejection_authority
                                or lease_values[1] is not record_binding_anchor
                                or type(lease_values[2]) is not str
                                or lease_values[2] != record_lease_id_anchor
                                or len(binding_values) != 9
                                or binding_values[0] is not rejection_binding_issuer
                                or binding_values[1] is not rejection_binding_issuer
                                or tuple(binding_values[2:]) != recipient
                                or tuple(binding_values[2:]) != origin[9]
                        ):
                            live_state_ok = False
                        if live_state_ok:
                            for permit_row in tuple(dict.values(rejection_permits)):
                                if type(permit_row) is not permit_row_type:
                                    live_state_ok = False
                                    break
                                permit_entry_ref = native_member_get(
                                    permit_entry_ref_slot, permit_row, permit_row_type,
                                )
                                if type(permit_entry_ref) is not native_reference_type or permit_entry_ref() is entry:
                                    live_state_ok = False
                                    break
                        if live_state_ok:
                            for exchange_row in tuple(dict.values(rejection_exchanges)):
                                if type(exchange_row) is not exchange_row_type:
                                    live_state_ok = False
                                    break
                                exchange_request_id = native_member_get(
                                    exchange_request_id_slot, exchange_row, exchange_row_type,
                                )
                                if type(exchange_request_id) is not str or exchange_request_id == request_id:
                                    live_state_ok = False
                                    break
                        if live_state_ok:
                            for completion_row in tuple(dict.values(rejection_completions)):
                                if type(completion_row) is not completion_row_type:
                                    live_state_ok = False
                                    break
                                completion_entry_ref = native_member_get(
                                    completion_entry_ref_slot, completion_row, completion_row_type,
                                )
                                if type(completion_entry_ref) is not native_reference_type or completion_entry_ref() is entry:
                                    live_state_ok = False
                                    break
                        if live_state_ok:
                            for native_completion_row in tuple(dict.values(rejection_native_completions)):
                                if type(native_completion_row) is not native_completion_row_type:
                                    live_state_ok = False
                                    break
                                native_completion_entry_ref = native_member_get(
                                    native_completion_entry_ref_slot,
                                    native_completion_row,
                                    native_completion_row_type,
                                )
                                if (
                                    type(native_completion_entry_ref) is not native_reference_type
                                    or native_completion_entry_ref() is entry
                                ):
                                    live_state_ok = False
                                    break
                        if live_state_ok and dict.get(rejection_entries, request_id) is not entry:
                            live_state_ok = False
                        if live_state_ok:
                            with runtime_lock_anchor:
                                final_runtime_values: list[object] = []
                                for runtime_slot in runtime_slots:
                                    final_runtime_values.append(
                                        native_member_get(
                                            runtime_slot, self, runtime_type,
                                        )
                                    )
                                authority_instance = native_getset_get(
                                    authority_dict_slot, rejection_authority, authority_dict_type,
                                )
                                if (
                                    type(authority_instance) is not dict
                                    or any(type(key) is not str for key in tuple(dict.keys(authority_instance)))
                                    or type(rejection_binding_issuer) is not object
                                    or dict.get(authority_instance, "_binding_issuer")
                                        is not rejection_binding_issuer
                                    or type(final_runtime_values[6]) is not bool
                                    or type(final_runtime_values[7]) is not int
                                    or isinstance(final_runtime_values[7], bool)
                                    or type(final_runtime_values[8]) is not int
                                    or isinstance(final_runtime_values[8], bool)
                                    or type(final_runtime_values[9]) is not dict
                                    or type(final_runtime_values[10]) is not dict
                                    or type(final_runtime_values[11]) is not set
                                    or type(final_runtime_values[12]) is not dict
                                    or type(final_runtime_values[13]) is not dict
                                    or type(final_runtime_values[14]) is not tuple
                                    or len(final_runtime_values[14]) != 11
                                ):
                                    live_state_ok = False
                                final_runtime_index = 0
                                while live_state_ok and final_runtime_index < 6:
                                    if (
                                        final_runtime_values[final_runtime_index]
                                        is not runtime_values[final_runtime_index]
                                    ):
                                        live_state_ok = False
                                    final_runtime_index += 1
                                final_runtime_index = 9
                                while live_state_ok and final_runtime_index < 15:
                                    if (
                                        final_runtime_values[final_runtime_index]
                                        is not runtime_values[final_runtime_index]
                                    ):
                                        live_state_ok = False
                                    final_runtime_index += 1
                                if (
                                    not live_state_ok
                                    or final_runtime_values[6] is not False
                                    or final_runtime_values[7] != runtime_generation_anchor
                                    or final_runtime_values[8] != runtime_pid_anchor
                                    or type(runtime_lock_anchor) is not runtime_lock_type
                                    or type(call_token) is not tuple
                                    or len(call_token) != 4
                                    or type(call_token[1]) is not int
                                    or isinstance(call_token[1], bool)
                                    or call_token[1] != final_runtime_values[7]
                                    or dict.get(runtime_records_anchor, request_id)
                                        is not record
                                    or dict.get(runtime_active_records_anchor, request_id)
                                        is not record_token
                                    or record_lease_id_anchor
                                        not in runtime_lease_ids_anchor
                                    or type(record) is not record_type
                                    or current_record_values[0] != request_id
                                    or current_record_values[1]
                                        != record_lease_id_anchor
                                    or current_record_values[2]
                                        is not record_identity_anchor
                                    or current_record_values[3]
                                        != record_chat_session_id_anchor
                                    or current_record_values[4]
                                        is not record_binding_anchor
                                    or current_record_values[5]
                                        is not record_lease_anchor
                                    or current_record_values[6]
                                        is not record_selection_anchor
                                    or current_record_values[7]
                                        != record_page_bytes_anchor
                                    or current_record_values[8]
                                        != final_runtime_values[7]
                                    or current_record_values[9] is not origin
                                    or origin is not record_origin_anchor
                                    or origin[0] is not entry
                                    or origin[3] is not record_selection_anchor
                                    or origin[4] is not record_lease_anchor
                                    or origin[5] is not record_binding_anchor
                                    or origin[6] != record_request_id_anchor
                                    or origin[7] != record_lease_id_anchor
                                    or origin[8] != record_identity_anchor[2]
                                    or origin[9] != recipient
                                    or origin[11] != record_page_bytes_anchor
                                    or type(identity) is not tuple
                                    or len(identity) != 6
                                    or type(identity[0]) is not str
                                    or type(identity[1]) is not str
                                    or type(identity[2]) is not str
                                    or type(identity[3]) is not int
                                    or isinstance(identity[3], bool)
                                    or type(identity[4]) is not int
                                    or isinstance(identity[4], bool)
                                    or type(identity[5]) is not str
                                    or record_identity_anchor[0] != identity[0]
                                    or record_identity_anchor[1] != identity[1]
                                    or record_identity_anchor[2] != identity[2]
                                    or record_identity_anchor[3] != identity[3]
                                    or record_identity_anchor[4] != identity[4]
                                    or record_identity_anchor[5] != identity[5]
                                    or record_chat_session_id_anchor
                                        != origin[9][1]
                                ):
                                    live_state_ok = False
                                if live_state_ok:
                                    preserve_record = True
                    # FINAL_EXISTING_LIVE_STATE_GATE_END
                    if not live_state_ok or not preserve_record:
                        _fail()
                    _fail()
                with runtime_lock_anchor:
                    final_runtime_values = []
                    for runtime_slot in runtime_slots:
                        final_runtime_values.append(
                            native_member_get(runtime_slot, self, runtime_type)
                        )
                    normal_state_ok = True
                    authority_instance = native_getset_get(
                        authority_dict_slot, rejection_authority, authority_dict_type,
                    )
                    if (
                        type(authority_instance) is not dict
                        or any(type(key) is not str for key in tuple(dict.keys(authority_instance)))
                        or type(rejection_binding_issuer) is not object
                        or dict.get(authority_instance, "_binding_issuer")
                            is not rejection_binding_issuer
                        or type(final_runtime_values[6]) is not bool
                        or type(final_runtime_values[7]) is not int
                        or isinstance(final_runtime_values[7], bool)
                        or type(final_runtime_values[8]) is not int
                        or isinstance(final_runtime_values[8], bool)
                        or type(final_runtime_values[9]) is not dict
                        or type(final_runtime_values[10]) is not dict
                        or type(final_runtime_values[11]) is not set
                        or type(final_runtime_values[12]) is not dict
                        or type(final_runtime_values[13]) is not dict
                        or type(final_runtime_values[14]) is not tuple
                        or len(final_runtime_values[14]) != 11
                    ):
                        normal_state_ok = False
                    final_runtime_index = 0
                    while normal_state_ok and final_runtime_index < 6:
                        if (
                            final_runtime_values[final_runtime_index]
                            is not runtime_values[final_runtime_index]
                        ):
                            normal_state_ok = False
                        final_runtime_index += 1
                    final_runtime_index = 9
                    while normal_state_ok and final_runtime_index < 15:
                        if (
                            final_runtime_values[final_runtime_index]
                            is not runtime_values[final_runtime_index]
                        ):
                            normal_state_ok = False
                        final_runtime_index += 1
                    if (
                        not normal_state_ok
                        or final_runtime_values[6] is not False
                        or final_runtime_values[7] != runtime_generation_anchor
                        or final_runtime_values[8] != runtime_pid_anchor
                        or type(runtime_lock_anchor) is not runtime_lock_type
                        or type(call_token) is not tuple
                        or len(call_token) != 4
                        or type(call_token[1]) is not int
                        or isinstance(call_token[1], bool)
                        or call_token[1] != final_runtime_values[7]
                        or dict.get(runtime_records_anchor, request_id) is not record
                        or dict.get(runtime_active_records_anchor, request_id)
                            is not record_token
                        or record_lease_id_anchor not in runtime_lease_ids_anchor
                        or type(record) is not record_type
                        or current_record_values[0] != request_id
                        or current_record_values[1] != record_lease_id_anchor
                        or current_record_values[2] is not record_identity_anchor
                        or current_record_values[3]
                            != record_chat_session_id_anchor
                        or current_record_values[4] is not record_binding_anchor
                        or current_record_values[5] is not record_lease_anchor
                        or current_record_values[6] is not record_selection_anchor
                        or current_record_values[7] != record_page_bytes_anchor
                        or current_record_values[8] != final_runtime_values[7]
                        or current_record_values[9] is not record_origin_anchor
                    ):
                        _fail()
                frozen = _TRUSTED_PUBLISH_MANIFEST(
                    runtime_coordinator_anchor,
                    record_lease_anchor,
                    record_selection_anchor,
                    record_request_id_anchor,
                    rpc_request_id,
                    record_page_bytes_anchor,
                    arguments.get("manifest_cursor"),
                )
                self._assert_pins()
                self._assert_graph()
                self._checkpoint(call_token)
                _validate_coordinator_response(
                    frozen, rpc_request_id=rpc_request_id, method=method,
                )
                self._checkpoint(call_token)
                return frozen
            if method == "harbour_context_page":
                self._assert_pins()
                self._assert_graph()
                self._checkpoint(call_token)
                if _TRUSTED_PUBLICATION_CLEANUP_HELPERS_INTACT() is not True:
                    _fail()
                content_cleanup_anchor = _TRUSTED_PUBLICATION_CLEANUP_ANCHOR(
                    self._service,
                    record.request_id,
                    record.lease,
                )
                if (
                    type(content_cleanup_anchor) is not tuple
                    or len(content_cleanup_anchor) != 16
                ):
                    _fail()
                content_capability = _ORIGINAL_ACQUIRE_NATIVE_LEASE_CAPABILITY(
                    self._authority,
                    record.lease,
                    _ORIGINAL_NATIVE_LEASE_STATE_READER,
                )
                if type(content_capability) is not _ORIGINAL_NATIVE_LEASE_CAPABILITY:
                    _fail()
                authority_state = MemberDescriptorType.__get__(
                    _ORIGINAL_NATIVE_LEASE_STATE_SLOT,
                    self._authority,
                    _ORIGINAL_NATIVE_LEASE_STATE_SLOT_CLASS,
                )
                if (
                    type(authority_state) is not _ORIGINAL_NATIVE_LEASE_STATE
                    or authority_state is not content_capability.state
                ):
                    _fail()
                native_lock = authority_state.lock
                native_leases = authority_state.leases
                native_cancelled_leases = authority_state.cancelled_leases
                native_lease = content_capability.lease
                native_key = content_capability.key
                native_row = content_capability.record
                if (
                    type(native_lock) is not _TRUSTED_RLOCK_TYPE
                    or type(native_leases) is not dict
                    or type(native_cancelled_leases) is not dict
                    or native_leases is native_cancelled_leases
                    or type(native_lease) is not McpContextReadLease
                    or native_lease is not record.lease
                    or type(native_key) is not int
                    or native_key != id(native_lease)
                    or type(native_row) is not list
                    or len(native_row) != 3
                    or type(native_row[0]) is not ReferenceType
                    or type(native_row[1]) is not tuple
                ):
                    _fail()
                with native_lock:
                    if (
                        dict.get(native_leases, native_key) is not native_row
                        or native_row[0]() is not native_lease
                        or native_row[2] is not None
                    ):
                        _fail()
                with runtime_lock_anchor:
                    if (
                        self._closed
                        or self._records.get(request_id) is not record
                        or self._active_records.get(request_id) is not record_token
                        or record.generation != self._generation
                    ):
                        _fail()
                content_native_lock = native_lock
                content_native_leases = native_leases
                content_native_key = native_key
                content_native_row = native_row
                content_native_cancel_sentinel = _ORIGINAL_NATIVE_LEASE_CANCELLED
                with _TRUSTED_OPEN_CONTENT_PUBLICATION(
                    self._authority,
                    record.lease,
                    record.binding,
                    content_capability,
                    call_token[0],
                    record_token,
                ):
                    self._record_live(record)
                    self._checkpoint(call_token)
                    frozen = _TRUSTED_PUBLISH_CONTENT(
                        self._coordinator,
                        record.lease,
                        record.selection,
                        record.request_id,
                        rpc_request_id,
                        record.page_bytes,
                        arguments.get("content_cursor"),
                    )
                    self._assert_pins()
                    self._assert_graph()
                    self._checkpoint(call_token)
                    complete = _validate_coordinator_response(
                        frozen, rpc_request_id=rpc_request_id, method=method,
                    )
                    self._checkpoint(call_token)
                    self._assert_pins()
                if complete:
                    detached = self._detach_record(record)
                    if detached is not record or not self._release_owned(
                        record, cancel_service=False,
                    ):
                        _fail()
                    completed = True
                    self._checkpoint(call_token)
                else:
                    with content_native_lock:
                        terminal_reason = content_native_row[2]
                        live_row = dict.get(content_native_leases, content_native_key)
                    if terminal_reason is not None or live_row is not content_native_row:
                        _fail()
                return frozen
            if method == "harbour_context_cancel":
                # Cancellation starts only after its acknowledgement is frozen.
                # An encoder failure must leave the request wholly untouched.
                preserve_record = True
                frozen = _TRUSTED_ENCODE(rpc_request_id, {"cancelled": True})
                self._assert_pins()
                self._checkpoint(call_token)
                if type(frozen) is not bytes or not frozen or len(frozen) > MCP_CONTEXT_RESULT_MAX_BYTES:
                    _fail()
                preserve_record = False
                trusted_cancel(self._coordinator, record.request_id)
                self._assert_pins()
                self._assert_graph()
                self._checkpoint(call_token)
                detached = self._detach_record(record)
                if detached is not record or not self._release_owned(
                    record, cancel_service=False,
                ):
                    _fail()
                completed = True
                self._checkpoint(call_token)
                return frozen
            _fail()
        except BaseException:
            if content_native_row is not None:
                try:
                    with content_native_lock:
                        if content_native_row[2] is None:
                            content_native_row[2] = content_native_cancel_sentinel
                except BaseException:
                    pass
            if content_cleanup_anchor is not None:
                try:
                    cleanup_verified = (
                        _TRUSTED_PUBLICATION_CLEANUP_HELPERS_INTACT() is True
                        and _TRUSTED_TERMINATE_ANCHORED_PUBLICATION(
                                content_cleanup_anchor,
                            ) is True
                    )
                except BaseException:
                    cleanup_verified = False
                if cleanup_verified is not True:
                    cleanup_verified = False
            if not completed and not preserve_record:
                detached = None
                with runtime_lock_anchor:
                    if dict.get(runtime_records_anchor, request_id) is record:
                        runtime_records_anchor.pop(request_id, None)
                        if type(cleanup_lease_id) is str:
                            runtime_lease_ids_anchor.discard(cleanup_lease_id)
                        detached = record
                if detached is record:
                    try:
                        with rejection_capability_anchor[2]:
                            if (
                                dict.get(
                                    rejection_capability_anchor[3], request_id,
                                ) is cleanup_entry
                            ):
                                trusted_cancel(
                                    runtime_coordinator_anchor, request_id,
                                )
                    except BaseException:
                        pass
                    try:
                        trusted_release_lease(
                            rejection_capability_anchor[8], cleanup_lease,
                        )
                    except BaseException:
                        pass
                    try:
                        trusted_release_binding(
                            rejection_capability_anchor[8], cleanup_binding,
                        )
                    except BaseException:
                        pass
            raise
        finally:
            with runtime_lock_anchor:
                if dict.get(runtime_active_records_anchor, request_id) is record_token:
                    runtime_active_records_anchor.pop(request_id, None)

    def handle(
        self,
        raw: bytes,
        *,
        identity: McpAuthenticatedIdentity,
        bearer_proof: McpContextBearerProof | None = None,
    ) -> bytes:
        native_member_get = MemberDescriptorType.__get__
        runtime_type = _TRUSTED_RUNTIME_CLASS
        runtime_slots = _RUNTIME_SLOTS
        cleanup_lock: object | None = None
        cleanup_active_threads: object | None = None
        call_token: tuple[object, int, int, int] | None = None
        try:
            if type(self) is not runtime_type or len(runtime_slots) != 15:
                _fail()
            cleanup_lock = native_member_get(runtime_slots[5], self, runtime_type)
            cleanup_active_threads = native_member_get(
                runtime_slots[13], self, runtime_type,
            )
            if (
                type(cleanup_lock) is not _TRUSTED_RLOCK_TYPE
                or type(cleanup_active_threads) is not dict
            ):
                _fail()
            self._assert_pins()
            call_token = self._begin_call()
            self._assert_graph()
            identity_values = _identity_projection(identity)
            call = _TRUSTED_PARSE(raw)
            self._checkpoint(call_token)
            method, rpc_request_id, arguments = self._call_projection(call)
            if method == "harbour_context_prepare":
                result = self._prepare(
                    rpc_request_id, arguments, identity, identity_values,
                    bearer_proof, call_token,
                )
            else:
                result = self._existing(
                    method, rpc_request_id, arguments, identity_values,
                    bearer_proof, call_token,
                )
            self._checkpoint(call_token)
            if type(result) is not bytes:
                _fail()
            return result
        except BaseException:
            raise McpContextRequestRuntimeError() from None
        finally:
            try:
                if (
                    type(call_token) is tuple
                    and len(call_token) == 4
                    and type(call_token[2]) is int
                    and not isinstance(call_token[2], bool)
                    and type(cleanup_lock) is _TRUSTED_RLOCK_TYPE
                    and type(cleanup_active_threads) is dict
                ):
                    with cleanup_lock:
                        if (
                            dict.get(cleanup_active_threads, call_token[2])
                            is call_token[0]
                        ):
                            cleanup_active_threads.pop(call_token[2], None)
            except BaseException:
                pass

    def close(self) -> None:
        records: tuple[_RequestRecord, ...] = ()
        safe = False
        try:
            self._assert_pins()
            self._assert_graph()
            safe = True
        except BaseException:
            safe = False
        try:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                self._generation += 1
                records = tuple(self._records.values())
                self._records.clear()
                self._pending.clear()
                self._lease_ids.clear()
            if safe:
                for record in records:
                    self._release_owned(record, cancel_service=True)
        except BaseException:
            return


_TRUSTED_RUNTIME_CLASS = McpContextRequestRuntime
_RUNTIME_PIN = _class_pin(
    McpContextRequestRuntime,
    (
        "__getattribute__", "__getattr__",
        "__init__", "__init_subclass__", "__repr__", "_assert_pins",
        "_assert_graph", "_begin_call", "_checkpoint", "_end_call",
        "_reserve_prepare", "_release_reservation", "_install_record",
        "_claim_record", "_release_record_claim", "_detach_record",
        "_release_owned", "_record_live", "_call_projection", "_prepare",
        "_existing", "handle", "close", *McpContextRequestRuntime.__slots__,
    ),
)
_RUNTIME_SLOTS = tuple(
    McpContextRequestRuntime.__dict__[name]
    for name in McpContextRequestRuntime.__slots__
)


__all__ = [
    "MCP_CONTEXT_REQUEST_ERROR",
    "McpContextRequestRuntime",
    "McpContextRequestRuntimeError",
]
