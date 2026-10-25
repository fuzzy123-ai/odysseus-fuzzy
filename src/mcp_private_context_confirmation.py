"""Process-local private-context consent for authenticated MCP reads.

The service can be wired via `src/mcp_context_app.py` (`install_mcp_context_app`)
and `routes/chat_routes.py` (`/mcp-context-disclose`). It supports a cookie-free
MCP intent, a later authenticated browser attachment, the paired acknowledgement,
and private guarded Planning, Forge, or Knowledge page completion receipts.
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
from src.context_metadata_binding import McpContextMetadataBinding
import src.context_manifest_producer as _context_manifest_module
import src.forge_context_page as _forge_page_module
import src.forge_context_reader as _forge_reader_module
import src.harbour_knowledge_active_runtime as _knowledge_module
import src.harbour_knowledge_staging_destination as _knowledge_staging_module
import src.harbour_knowledge_store as _knowledge_store_module
import src.repo_git_adapter as _repo_git_module
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    McpContextBinding,
    McpContextReadLease,
)
from src.mcp_context_causal_observation import (
    span_begin as _observation_span_begin,
    span_end as _observation_span_end,
)
from src.private_context_confirmation import ConfirmationError, ManifestItem

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

def _selection_types():
    # Deferred for the same import-cycle reason as ``_app_types``.
    from src.private_context_confirmation_app import (
        ContextSelection, ForgeSelectionLocator, SourceSelection,
    )
    return ContextSelection, SourceSelection, ForgeSelectionLocator

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
    __slots__ = ("__native_rejection_capsule_ref", "__weakref__")
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
class _NativeCompletionReceipt:
    __slots__ = ("__weakref__",)
    def __init_subclass__(cls, **kwargs: object) -> None: raise TypeError("receipt cannot be subclassed")
    def __copy__(self): raise TypeError("receipt cannot be copied")
    def __deepcopy__(self, memo: object): raise TypeError("receipt cannot be copied")
    def __reduce__(self): raise TypeError("receipt cannot be pickled")
    def __reduce_ex__(self, protocol: object): raise TypeError("receipt cannot be pickled")
    def __repr__(self) -> str: return "NativeCompletionReceipt(<redacted>)"
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

@dataclass(frozen=True, slots=True, repr=False, weakref_slot=True)
class VerifiedNativeCapture:
    source: str
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
class _NativeCompletionRow:
    receipt_ref: weakref.ReferenceType[_NativeCompletionReceipt]
    capture_ref: weakref.ReferenceType[VerifiedNativeCapture]
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
    source: str
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

@dataclass(frozen=True, slots=True, repr=False, weakref_slot=True)
class _NativePermitRejectionCapsule:
    service_ref: weakref.ReferenceType[object]
    lock: object
    entries: dict[str, _Entry]
    permits: dict[int, _PermitRow]
    exchanges: dict[int, _HumanExchangeRow]
    completions: dict[int, _CompletionRow]
    native_completions: dict[int, _NativeCompletionRow]
    authority: McpContextAuthority
    execution_local: object

_NATIVE_WEAKREF_TYPE = weakref.ReferenceType
_NATIVE_WEAKREF_REF = weakref.ref
_NATIVE_WEAKREF_CALL = weakref.ReferenceType.__call__
_NATIVE_RLOCK_TYPE = type(threading.RLock())
_NATIVE_LOCAL_TYPE = type(threading.local())
_NATIVE_CURRENT_THREAD = threading.current_thread
_NATIVE_GET_IDENT = threading.get_ident
_NATIVE_GETPID = os.getpid
_NATIVE_MEMBER_GET = type(_Entry.__dict__["request_id"]).__get__
_NATIVE_MEMBER_SET = type(_Entry.__dict__["request_id"]).__set__
_PERMIT_CAPSULE_REF_SLOT = Permit.__dict__["_Permit__native_rejection_capsule_ref"]
_CAPSULE_SLOTS = tuple(
    _NativePermitRejectionCapsule.__dict__[name]
    for name in (
        "service_ref", "lock", "entries", "permits", "exchanges", "completions",
        "native_completions", "authority", "execution_local",
    )
)
_PERMIT_ROW_SLOTS = tuple(
    _PermitRow.__dict__[name]
    for name in (
        "permit_ref", "entry_ref", "service_ref", "authority_ref", "lease_ref",
        "binding_ref", "recipient", "thread_ref", "thread_ident", "pid", "request_id",
        "generation", "nonce",
    )
)
_PERMIT_ROW_ENTRY_REF_SLOT = _PermitRow.__dict__["entry_ref"]
_HUMAN_ROW_REQUEST_ID_SLOT = _HumanExchangeRow.__dict__["request_id"]
_COMPLETION_ROW_ENTRY_REF_SLOT = _CompletionRow.__dict__["entry_ref"]
_NATIVE_COMPLETION_ROW_ENTRY_REF_SLOT = _NativeCompletionRow.__dict__["entry_ref"]
_ENTRY_REQUEST_ID_SLOT = _Entry.__dict__["request_id"]
_ENTRY_STATE_SLOT = _Entry.__dict__["state"]
_ENTRY_LEASE_REF_SLOT = _Entry.__dict__["lease_ref"]
_ENTRY_BINDING_REF_SLOT = _Entry.__dict__["binding_ref"]
_ENTRY_PERMIT_REF_SLOT = _Entry.__dict__["permit_ref"]
_ENTRY_GENERATION_SLOT = _Entry.__dict__["generation"]
_ENTRY_PERMIT_NONCE_SLOT = _Entry.__dict__["permit_nonce"]
_ENTRY_PERMIT_DEADLINE_SLOT = _Entry.__dict__["permit_deadline"]
_ENTRY_PERMIT_MONO_DEADLINE_SLOT = _Entry.__dict__["permit_mono_deadline"]
_ENTRY_SCRUB_SLOT_VALUES = tuple(
    (_Entry.__dict__[name], value)
    for name, value in (
        ("cookie", ""), ("page_bytes", 0), ("page_request_sha256", None), ("intent", False),
        ("selection", None), ("cookie_snapshot", None), ("mcp_snapshot", None),
        ("metadata_binding", None), ("receipt", None), ("challenge", None),
        ("challenge_deadline", None), ("challenge_mono_deadline", None),
        ("permit_ref", None), ("permit_nonce", None), ("exchange_nonce", None),
        ("permit_deadline", None), ("permit_mono_deadline", None),
        ("execution_nonce", None), ("execution_thread_ref", None),
        ("execution_thread_ident", None), ("execution_pid", None),
        ("ack_wall_time", None), ("ack_mono_time", None), ("ack_deadline", None),
        ("ack_mono_deadline", None), ("ack_descriptor", None), ("ack_attestation", None),
        ("ack_role_hmacs", None), ("disclosure_generation", None),
        ("disclosure_nonce", None), ("disclosure_operation_id", None),
        ("pending_ack_operation", None), ("permit_operation_nonce", None),
        ("lease_ref", None), ("binding_ref", None),
    )
)
_LEASE_AUTHORITY_SLOT = McpContextReadLease.__dict__["_McpContextReadLease__authority"]
_LEASE_BINDING_SLOT = McpContextReadLease.__dict__["_McpContextReadLease__binding"]
_BINDING_RECIPIENT_SLOTS = tuple(
    McpContextBinding.__dict__[name]
    for name in (
        "_McpContextBinding__owner_subject_id", "_McpContextBinding__chat_session_id",
        "_McpContextBinding__token_id", "_McpContextBinding__scope",
        "_McpContextBinding__cache_generation",
        "_McpContextBinding__security_action_revision", "_McpContextBinding__process_epoch",
    )
)

_TRUSTED_PRODUCER_PIN_METHOD = CombinedContextManifestProducer.__dict__["_assert_mcp_provider_pins"]
_TRUSTED_FORGE_HELPER = _forge_page_module.read_native_forge_context_page
_TRUSTED_FORGE_PAGE = _forge_page_module.ForgeContextPage
_TRUSTED_FORGE_BYTEFORM = _forge_page_module.ForgeContextPage.__dict__["byteform"]
_TRUSTED_FORGE_METADATA = _forge_page_module.ForgeContextMetadata
_TRUSTED_LOCAL_FORGE = _forge_page_module.LocalProjectForge
_TRUSTED_FORGE_READER = _forge_reader_module.ImmutableForgeContextReader
_TRUSTED_FORGE_RANGE = _repo_git_module.ForgeContentRange
_TRUSTED_FORGE_READER_RANGE = _forge_reader_module.ForgeContentRange
_TRUSTED_FORGE_READER_INIT = _TRUSTED_FORGE_READER.__dict__["__init__"]
_TRUSTED_FORGE_READER_NEW = _TRUSTED_FORGE_READER.__dict__.get("__new__")
_TRUSTED_FORGE_READER_GETATTRIBUTE = _TRUSTED_FORGE_READER.__dict__.get("__getattribute__")
_TRUSTED_FORGE_READ_RANGE = _TRUSTED_FORGE_READER.__dict__["read_exact_range"]
_TRUSTED_KNOWLEDGE_HELPER = _knowledge_module.read_native_knowledge_context_page
_TRUSTED_KNOWLEDGE_PAGE = _knowledge_module.KnowledgeContextPage
_TRUSTED_KNOWLEDGE_BYTEFORM = _knowledge_module.KnowledgeContextPage.__dict__["byteform"]
_TRUSTED_KNOWLEDGE_COMPOSITION = _knowledge_module.ActiveKnowledgeComposition
_TRUSTED_KNOWLEDGE_OPERATION = _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__["_context_operation"]
_TRUSTED_KNOWLEDGE_CAPTURE = _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__["_capture_context_material"]
_TRUSTED_KNOWLEDGE_READ = _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__["_read_context_payload"]
_TRUSTED_STAGING = _knowledge_staging_module.HarbourKnowledgeStagingDestinationService
_TRUSTED_SELECTED_RUNTIME = _TRUSTED_STAGING.__dict__["selected_runtime"]
_TRUSTED_KNOWLEDGE_STORE = _knowledge_store_module.HarbourKnowledgeStore
_TRUSTED_READ_PAYLOAD = _TRUSTED_KNOWLEDGE_STORE.__dict__["read_payload_at_generation"]

_FORGE_RANGE_FIELDS = (
    "schema", "owner_scope", "repo_id", "version_id", "commit_sha", "snapshot_digest",
    "path", "file_content_sha256", "file_byte_count", "offset", "content", "authority_binding",
)
_FORGE_PAGE_FIELDS = (
    "payload_bytes", "total", "offset", "end", "whole_sha256", "page_sha256", "manifest_digest",
)
_KNOWLEDGE_PAGE_FIELDS = _FORGE_PAGE_FIELDS

_TRUSTED_TYPE_GETATTRIBUTE = type.__getattribute__
_TRUSTED_STATICMETHOD_TYPE = staticmethod
_TRUSTED_STATICMETHOD_GET = staticmethod.__get__
_TRUSTED_MEMBER_DESCRIPTOR_TYPE = types.MemberDescriptorType

def _raw_effective_special(
    value_class: type,
    name: str,
    _type_getattribute: object = type.__getattribute__,
    _staticmethod_type: type = staticmethod,
    _staticmethod_get: object = staticmethod.__get__,
) -> object:
    if type(value_class) is not type or name not in {"__new__", "__getattribute__"}:
        _fail("context_stale")
    mro = _type_getattribute(value_class, "__mro__")
    if type(mro) is not tuple or not mro:
        _fail("context_stale")
    for base in mro:
        if type(base) is not type:
            _fail("context_stale")
        namespace = _type_getattribute(base, "__dict__")
        if name in namespace:
            raw = namespace[name]
            if type(raw) is _staticmethod_type:
                return _staticmethod_get(raw, None, value_class)
            return raw
    _fail("context_stale")

_TRUSTED_RAW_EFFECTIVE_SPECIAL = _raw_effective_special

def _value_class_state(
    value_class: type,
    fields: tuple[str, ...],
    _type_getattribute: object = type.__getattribute__,
    _effective: object = _TRUSTED_RAW_EFFECTIVE_SPECIAL,
    _member_descriptor_type: type = types.MemberDescriptorType,
) -> tuple[object, ...]:
    namespace = _type_getattribute(value_class, "__dict__")
    field_slots = tuple(namespace.get(name) for name in fields)
    if any(type(slot) is not _member_descriptor_type for slot in field_slots):
        _fail("context_stale")
    return (
        namespace.get("__init__"), namespace.get("__new__"),
        _effective(value_class, "__new__"),
        namespace.get("__getattribute__"),
        _effective(value_class, "__getattribute__"),
        namespace.get("__post_init__"), namespace.get("__setattr__"),
        field_slots,
    )

_TRUSTED_VALUE_CLASS_STATE = _value_class_state
_TRUSTED_FORGE_RANGE_STATE = _TRUSTED_VALUE_CLASS_STATE(_TRUSTED_FORGE_RANGE, _FORGE_RANGE_FIELDS)
_TRUSTED_FORGE_PAGE_STATE = _TRUSTED_VALUE_CLASS_STATE(_TRUSTED_FORGE_PAGE, _FORGE_PAGE_FIELDS)
_TRUSTED_KNOWLEDGE_PAGE_STATE = _TRUSTED_VALUE_CLASS_STATE(
    _TRUSTED_KNOWLEDGE_PAGE, _KNOWLEDGE_PAGE_FIELDS,
)

def _value_class_matches(value_class: type, fields: tuple[str, ...], expected: tuple[object, ...],
                         _state: object = _TRUSTED_VALUE_CLASS_STATE) -> bool:
    current = _state(value_class, fields)
    return (all(value is expected_value for value, expected_value in zip(current[:-1], expected[:-1]))
            and all(value is expected_value
                    for value, expected_value in zip(current[-1], expected[-1])))

_TRUSTED_VALUE_CLASS_MATCHES = _value_class_matches

_SELECTION_FIELDS = ("items",)
_SOURCE_SELECTION_FIELDS = ("source", "selection_ref", "revision_ref", "forge_locator")
_FORGE_LOCATOR_FIELDS = ("repo_id", "version_id", "path")
_SNAPSHOT_FIELDS = ("items", "target_manifest_digests", "mcp_metadata_binding")
_MANIFEST_ITEM_FIELDS = (
    "source", "selection_ref", "revision_ref", "boundary_sha256", "record_sha256",
    "payload_sha256", "classification", "provenance_ref",
)
_MCP_METADATA_FIELDS = (
    "schema", "audience", "owner_sid", "chat_session_id", "recipient_token_id", "scope",
    "cache_generation", "security_revision", "app_epoch",
)
_TRUSTED_COMBINED_SNAPSHOT_CLASS = CombinedContextSnapshot
_TRUSTED_MANIFEST_ITEM_CLASS = ManifestItem
_TRUSTED_METADATA_BINDING_CLASS = McpContextMetadataBinding
_TRUSTED_SNAPSHOT_STATE = _TRUSTED_VALUE_CLASS_STATE(
    _TRUSTED_COMBINED_SNAPSHOT_CLASS, _SNAPSHOT_FIELDS,
)
_TRUSTED_MANIFEST_ITEM_STATE = _TRUSTED_VALUE_CLASS_STATE(
    _TRUSTED_MANIFEST_ITEM_CLASS, _MANIFEST_ITEM_FIELDS,
)
_TRUSTED_MCP_METADATA_STATE = _TRUSTED_VALUE_CLASS_STATE(
    _TRUSTED_METADATA_BINDING_CLASS, _MCP_METADATA_FIELDS,
)

def _trusted_slots(value: object, expected_type: type, state: tuple[object, ...]) -> tuple[object, ...]:
    if type(value) is not expected_type:
        _fail("context_stale")
    slots = state[-1]
    try:
        return tuple(type(slot).__get__(slot, value, expected_type) for slot in slots)
    except BaseException:
        _fail("context_stale")

def _service_selection_projection(
    value: object,
    type_pins: tuple[type, type, type],
    state_pins: tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]],
) -> tuple[tuple[object, ...], ...]:
    context_type, source_type, locator_type = type_pins
    context_state, source_state, locator_state = state_pins
    if (not _TRUSTED_VALUE_CLASS_MATCHES(context_type, _SELECTION_FIELDS, context_state)
            or not _TRUSTED_VALUE_CLASS_MATCHES(source_type, _SOURCE_SELECTION_FIELDS, source_state)
            or not _TRUSTED_VALUE_CLASS_MATCHES(locator_type, _FORGE_LOCATOR_FIELDS, locator_state)):
        _fail("context_stale")
    (items,) = _TRUSTED_SLOTS(value, context_type, context_state)
    if type(items) is not tuple or not 1 <= len(items) <= 300:
        _fail("context_stale")
    projected: list[tuple[object, ...]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        source, selection_ref, revision_ref, locator = _TRUSTED_SLOTS(
            item, source_type, source_state,
        )
        if (type(source) is not str or source not in {"forge", "planning", "knowledge"}
                or type(selection_ref) is not str or not selection_ref or len(selection_ref) > 256
                or type(revision_ref) is not str or not revision_ref or len(revision_ref) > 256
                or not selection_ref.isascii() or not revision_ref.isascii()):
            _fail("context_stale")
        key = (source, selection_ref, revision_ref)
        if key in seen:
            _fail("context_stale")
        seen.add(key)
        if source == "forge":
            repo_id, version_id, path = _TRUSTED_SLOTS(locator, locator_type, locator_state)
            if (any(type(part) is not str or not part or len(part) > 1024 for part in
                    (repo_id, version_id, path)) or not repo_id.isascii()
                    or not version_id.isascii() or not path.isascii()):
                _fail("context_stale")
            locator_projection: object = (repo_id, version_id, path)
        else:
            if locator is not None:
                _fail("context_stale")
            locator_projection = None
        projected.append((source, selection_ref, revision_ref, locator_projection))
    return tuple(projected)

def _service_snapshot_projection(value: object, *, require_metadata: bool = True) -> tuple[object, ...]:
    if (not _TRUSTED_VALUE_CLASS_MATCHES(
                _TRUSTED_COMBINED_SNAPSHOT_CLASS, _SNAPSHOT_FIELDS, _TRUSTED_SNAPSHOT_STATE,
            ) or not _TRUSTED_VALUE_CLASS_MATCHES(
                _TRUSTED_MANIFEST_ITEM_CLASS, _MANIFEST_ITEM_FIELDS, _TRUSTED_MANIFEST_ITEM_STATE,
            ) or not _TRUSTED_VALUE_CLASS_MATCHES(
                _TRUSTED_METADATA_BINDING_CLASS, _MCP_METADATA_FIELDS,
                _TRUSTED_MCP_METADATA_STATE,
            )):
        _fail("context_stale")
    items, targets, metadata = _TRUSTED_SLOTS(
        value, _TRUSTED_COMBINED_SNAPSHOT_CLASS, _TRUSTED_SNAPSHOT_STATE,
    )
    if (type(require_metadata) is not bool
            or type(items) is not tuple or not 1 <= len(items) <= 300
            or type(targets) is not tuple or len(targets) != len(items)
            or (type(metadata) is not _TRUSTED_METADATA_BINDING_CLASS if require_metadata
                else metadata is not None)):
        _fail("context_stale")
    item_rows: list[tuple[str, ...]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        row = _TRUSTED_SLOTS(item, _TRUSTED_MANIFEST_ITEM_CLASS, _TRUSTED_MANIFEST_ITEM_STATE)
        if any(type(part) is not str for part in row):
            _fail("context_stale")
        source, selection_ref, revision_ref, boundary, record, payload, classification, provenance = row
        if (source not in {"forge", "planning", "knowledge"}
                or classification not in {"public", "private"}
                or any(not part or len(part) > 256 or not part.isascii()
                       for part in (selection_ref, revision_ref, provenance))
                or any(len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
                       for digest in (boundary, record, payload))):
            _fail("context_stale")
        key = (source, selection_ref, revision_ref)
        if key in seen:
            _fail("context_stale")
        seen.add(key)
        item_rows.append(row)
    for target in targets:
        if type(target) is not str:
            _fail("context_stale")
        if require_metadata:
            valid_target = (len(target) == 64
                            and all(ch in "0123456789abcdef" for ch in target))
        else:
            valid_target = (
                (len(target) == 64
                 and all(ch in "0123456789abcdef" for ch in target))
                or (len(target) == 71 and target[:7] == "sha256:"
                    and all(ch in "0123456789abcdef" for ch in target[7:]))
            )
        if not valid_target:
            _fail("context_stale")
    if not require_metadata:
        return tuple(item_rows), targets, None
    metadata_row = _TRUSTED_SLOTS(
        metadata, _TRUSTED_METADATA_BINDING_CLASS, _TRUSTED_MCP_METADATA_STATE,
    )
    if (any(type(part) is not str for part in (*metadata_row[:6], metadata_row[8]))
            or any(type(part) is not int or isinstance(part, bool) or part < 0
                   for part in metadata_row[6:8])
            or metadata_row[0] != "odysseus.context.mcp_metadata_binding.v1"
            or metadata_row[1] != "authenticated_mcp_context_read"
            or metadata_row[5] != "mcp:harbour:context:read"):
        _fail("context_stale")
    return tuple(item_rows), targets, metadata_row

_TRUSTED_SLOTS = _trusted_slots
_TRUSTED_SERVICE_SELECTION_PROJECTION = _service_selection_projection
_TRUSTED_SERVICE_SNAPSHOT_PROJECTION = _service_snapshot_projection
_TRUSTED_KNOWLEDGE_COMPOSITION_GETATTRIBUTE_SLOT = (
    _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__.get("__getattribute__")
)
_TRUSTED_KNOWLEDGE_COMPOSITION_GETATTRIBUTE = _TRUSTED_KNOWLEDGE_COMPOSITION.__getattribute__
_TRUSTED_STAGING_GETATTRIBUTE_SLOT = _TRUSTED_STAGING.__dict__.get("__getattribute__")
_TRUSTED_STAGING_GETATTRIBUTE = _TRUSTED_STAGING.__getattribute__
_TRUSTED_KNOWLEDGE_STORE_GETATTRIBUTE_SLOT = _TRUSTED_KNOWLEDGE_STORE.__dict__.get("__getattribute__")
_TRUSTED_KNOWLEDGE_STORE_GETATTRIBUTE = _TRUSTED_KNOWLEDGE_STORE.__getattribute__

def _instance_dict(value: object) -> dict[str, object]:
    try:
        result = vars(value)
    except TypeError:
        return {}
    except BaseException:
        _fail("context_stale")
    if type(result) is not dict:
        _fail("context_stale")
    return result

def _assert_native_body_pins(service: object, source: str, binding: object) -> object | None:
    if source == "forge":
        if (_forge_page_module.ForgeContextPage is not _TRUSTED_FORGE_PAGE
                or _forge_reader_module.ImmutableForgeContextReader is not _TRUSTED_FORGE_READER
                or _forge_reader_module.ForgeContentRange is not _TRUSTED_FORGE_READER_RANGE
                or _repo_git_module.ForgeContentRange is not _TRUSTED_FORGE_RANGE
                or globals().get("_value_class_state") is not _TRUSTED_VALUE_CLASS_STATE
                or globals().get("_value_class_matches") is not _TRUSTED_VALUE_CLASS_MATCHES
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    _TRUSTED_FORGE_PAGE, _FORGE_PAGE_FIELDS, _TRUSTED_FORGE_PAGE_STATE,
                )
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    _TRUSTED_FORGE_RANGE, _FORGE_RANGE_FIELDS, _TRUSTED_FORGE_RANGE_STATE,
                )):
            _fail("context_stale")
    elif source == "knowledge":
        if (_knowledge_module.KnowledgeContextPage is not _TRUSTED_KNOWLEDGE_PAGE
                or _knowledge_module.ActiveKnowledgeComposition is not _TRUSTED_KNOWLEDGE_COMPOSITION
                or _knowledge_staging_module.HarbourKnowledgeStagingDestinationService is not _TRUSTED_STAGING
                or _knowledge_store_module.HarbourKnowledgeStore is not _TRUSTED_KNOWLEDGE_STORE
                or globals().get("_value_class_state") is not _TRUSTED_VALUE_CLASS_STATE
                or globals().get("_value_class_matches") is not _TRUSTED_VALUE_CLASS_MATCHES
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    _TRUSTED_KNOWLEDGE_PAGE, _KNOWLEDGE_PAGE_FIELDS,
                    _TRUSTED_KNOWLEDGE_PAGE_STATE,
                )
                or _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__.get("__getattribute__")
                    is not _TRUSTED_KNOWLEDGE_COMPOSITION_GETATTRIBUTE_SLOT
                or _TRUSTED_KNOWLEDGE_COMPOSITION.__getattribute__
                    is not _TRUSTED_KNOWLEDGE_COMPOSITION_GETATTRIBUTE
                or _TRUSTED_STAGING.__dict__.get("__getattribute__")
                    is not _TRUSTED_STAGING_GETATTRIBUTE_SLOT
                or _TRUSTED_STAGING.__getattribute__ is not _TRUSTED_STAGING_GETATTRIBUTE
                or _TRUSTED_KNOWLEDGE_STORE.__dict__.get("__getattribute__")
                    is not _TRUSTED_KNOWLEDGE_STORE_GETATTRIBUTE_SLOT
                or _TRUSTED_KNOWLEDGE_STORE.__getattribute__
                    is not _TRUSTED_KNOWLEDGE_STORE_GETATTRIBUTE):
            _fail("context_stale")
    else:
        _fail("invalid_request")
    producer = service._producer
    if (_context_manifest_module.CombinedContextManifestProducer is not CombinedContextManifestProducer
            or CombinedContextManifestProducer.__dict__.get("_assert_mcp_provider_pins") is not _TRUSTED_PRODUCER_PIN_METHOD):
        _fail("context_stale")
    _TRUSTED_PRODUCER_PIN_METHOD(producer)
    pinned = service._native_provider_identity
    if (type(pinned) is not tuple or len(pinned) != 7
            or producer._forge is not pinned[0]
            or producer._forge._local_forge is not pinned[1]
            or producer._forge._local_forge.store is not pinned[2]
            or producer._knowledge is not pinned[3]
            or producer is not pinned[6]):
        _fail("context_stale")
    if source == "forge":
        reader = _forge_reader_module.ImmutableForgeContextReader
        if (_forge_page_module.read_native_forge_context_page is not _TRUSTED_FORGE_HELPER
                or _forge_page_module.ForgeContextPage is not _TRUSTED_FORGE_PAGE
                or globals().get("_value_class_state") is not _TRUSTED_VALUE_CLASS_STATE
                or globals().get("_value_class_matches") is not _TRUSTED_VALUE_CLASS_MATCHES
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    _TRUSTED_FORGE_PAGE, _FORGE_PAGE_FIELDS, _TRUSTED_FORGE_PAGE_STATE,
                )
                or _forge_page_module.ForgeContextPage.__dict__.get("byteform") is not _TRUSTED_FORGE_BYTEFORM
                or _forge_page_module.ForgeContextMetadata is not _TRUSTED_FORGE_METADATA
                or _forge_page_module.LocalProjectForge is not _TRUSTED_LOCAL_FORGE
                or type(producer._forge) is not _TRUSTED_FORGE_METADATA
                or type(producer._forge._local_forge) is not _TRUSTED_LOCAL_FORGE
                or _forge_page_module.ImmutableForgeContextReader is not _TRUSTED_FORGE_READER
                or _forge_page_module.ForgeContentRange is not _TRUSTED_FORGE_RANGE
                or reader is not _TRUSTED_FORGE_READER
                or _repo_git_module.ForgeContentRange is not _TRUSTED_FORGE_RANGE
                or _forge_reader_module.ForgeContentRange is not _TRUSTED_FORGE_READER_RANGE
                or _TRUSTED_FORGE_READER_RANGE is not _TRUSTED_FORGE_RANGE
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    _TRUSTED_FORGE_RANGE, _FORGE_RANGE_FIELDS, _TRUSTED_FORGE_RANGE_STATE,
                )
                or reader.__dict__.get("__init__") is not _TRUSTED_FORGE_READER_INIT
                or reader.__dict__.get("__new__") is not _TRUSTED_FORGE_READER_NEW
                or reader.__dict__.get("__getattribute__") is not _TRUSTED_FORGE_READER_GETATTRIBUTE
                or reader.__dict__.get("read_exact_range") is not _TRUSTED_FORGE_READ_RANGE
                or reader.__new__ is not object.__new__ or reader.__getattribute__ is not object.__getattribute__):
            _fail("context_stale")
        return None
    composition = pinned[4]
    staging = pinned[5]
    if (_knowledge_module.read_native_knowledge_context_page is not _TRUSTED_KNOWLEDGE_HELPER
            or _knowledge_module.KnowledgeContextPage is not _TRUSTED_KNOWLEDGE_PAGE
            or globals().get("_value_class_state") is not _TRUSTED_VALUE_CLASS_STATE
            or globals().get("_value_class_matches") is not _TRUSTED_VALUE_CLASS_MATCHES
            or not _TRUSTED_VALUE_CLASS_MATCHES(
                _TRUSTED_KNOWLEDGE_PAGE, _KNOWLEDGE_PAGE_FIELDS, _TRUSTED_KNOWLEDGE_PAGE_STATE,
            )
            or _knowledge_module.KnowledgeContextPage.__dict__.get("byteform") is not _TRUSTED_KNOWLEDGE_BYTEFORM
            or _knowledge_module.ActiveKnowledgeComposition is not _TRUSTED_KNOWLEDGE_COMPOSITION
            or _knowledge_module.HarbourKnowledgeStagingDestinationService is not _TRUSTED_STAGING
            or _knowledge_module.HarbourKnowledgeStore is not _TRUSTED_KNOWLEDGE_STORE
            or type(composition) is not _TRUSTED_KNOWLEDGE_COMPOSITION
            or _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__.get("__getattribute__")
                is not _TRUSTED_KNOWLEDGE_COMPOSITION_GETATTRIBUTE_SLOT
            or _TRUSTED_KNOWLEDGE_COMPOSITION.__getattribute__
                is not _TRUSTED_KNOWLEDGE_COMPOSITION_GETATTRIBUTE
            or producer._knowledge._composition is not composition
            or composition._staging is not staging
            or _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__.get("_context_operation") is not _TRUSTED_KNOWLEDGE_OPERATION
            or _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__.get("_capture_context_material") is not _TRUSTED_KNOWLEDGE_CAPTURE
            or _TRUSTED_KNOWLEDGE_COMPOSITION.__dict__.get("_read_context_payload") is not _TRUSTED_KNOWLEDGE_READ
            or any(name in _instance_dict(composition) for name in ("_context_operation", "_capture_context_material", "_read_context_payload"))
            or _knowledge_staging_module.HarbourKnowledgeStagingDestinationService is not _TRUSTED_STAGING
            or type(staging) is not _TRUSTED_STAGING
            or _TRUSTED_STAGING.__dict__.get("__getattribute__") is not _TRUSTED_STAGING_GETATTRIBUTE_SLOT
            or _TRUSTED_STAGING.__getattribute__ is not _TRUSTED_STAGING_GETATTRIBUTE
            or _TRUSTED_STAGING.__dict__.get("selected_runtime") is not _TRUSTED_SELECTED_RUNTIME
            or "selected_runtime" in _instance_dict(staging)):
        _fail("context_stale")
    try:
        owner_sid = binding.owner_sid
    except AttributeError:
        owner_sid = getattr(binding, "owner_subject_id", None)
    if type(owner_sid) is not str:
        _fail("invalid_binding")
    selected = _TRUSTED_SELECTED_RUNTIME(staging, owner_sid)
    if (type(selected) is not tuple or len(selected) != 5
            or type(selected[4]) is not _TRUSTED_KNOWLEDGE_STORE
            or _knowledge_store_module.HarbourKnowledgeStore is not _TRUSTED_KNOWLEDGE_STORE
            or _TRUSTED_KNOWLEDGE_STORE.__dict__.get("__getattribute__")
                is not _TRUSTED_KNOWLEDGE_STORE_GETATTRIBUTE_SLOT
            or _TRUSTED_KNOWLEDGE_STORE.__getattribute__ is not _TRUSTED_KNOWLEDGE_STORE_GETATTRIBUTE
            or _TRUSTED_KNOWLEDGE_STORE.__dict__.get("read_payload_at_generation") is not _TRUSTED_READ_PAYLOAD
            or "read_payload_at_generation" in _instance_dict(selected[4])):
        _fail("context_stale")
    return selected

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
        self._dual_snapshot_pin = type(producer).__dict__.get("capture_dual_snapshot")
        self._dual_content_snapshot_pin = type(producer).__dict__.get(
            "capture_dual_snapshot_content",
        )
        self._dual_pair_pin = type(producer).__dict__.get("_dual_sample_pair")
        self._pair_pin = type(self)._pair
        self._fresh_pair_pin = type(self)._fresh_pair
        self._capture_dual_content_pin = type(self)._capture_dual_content
        self._planning_content_revalidation_pin = (
            type(self)._revalidate_planning_content_under_guard
        )
        self._issue_captured_permit_pin = type(self)._issue_captured_permit
        self._page_request_descriptor_pin = type(self).__dict__.get("_page_request_descriptor")
        self._session_factory_pin = session_factory
        try:
            forge_metadata = producer._forge; local_forge = forge_metadata._local_forge
            knowledge_producer = producer._knowledge; composition = knowledge_producer._composition
            self._native_provider_identity = (forge_metadata, local_forge, local_forge.store,
                knowledge_producer, composition, composition._staging, producer)
            self._selection_types_pin = _TRUSTED_SELECTION_TYPES()
            self._selection_states_pin = (
                _TRUSTED_VALUE_CLASS_STATE(self._selection_types_pin[0], _SELECTION_FIELDS),
                _TRUSTED_VALUE_CLASS_STATE(self._selection_types_pin[1], _SOURCE_SELECTION_FIELDS),
                _TRUSTED_VALUE_CLASS_STATE(self._selection_types_pin[2], _FORGE_LOCATOR_FIELDS),
            )
        except BaseException:
            _fail("invalid_context")
        self._entries: dict[str, _Entry] = {}; self._permits: dict[int, _PermitRow] = {}; self._exchanges: dict[int, _HumanExchangeRow] = {}; self._completions: dict[int, _CompletionRow] = {}; self._native_completions: dict[int, _NativeCompletionRow] = {}
        self._app_ref: weakref.ReferenceType[object] | None = None
        self._app_method_pin: tuple[tuple[str, object], ...] | None = None
        self._lock = threading.RLock(); self._closed = False
        self._last_wall: float | None = None; self._last_mono: float | None = None
        self._key = secrets.token_bytes(32); self._wall = wall_time or (lambda: datetime.now(timezone.utc)); self._mono = monotonic
        self._attestation_key = secrets.token_bytes(32)
        self._execution_local = threading.local()
        self._native_rejection_capsule = _NativePermitRejectionCapsule(
            _NATIVE_WEAKREF_REF(self), self._lock, self._entries, self._permits, self._exchanges,
            self._completions, self._native_completions, self._authority, self._execution_local,
        )

    def prepare(self, lease: McpContextReadLease, cookie: str, selection: ContextSelection, *, page_bytes: int = 16384) -> PreparedRequest:
        self._reject_authority_reentry()
        self._assert_pins()
        pair = self._pair_pin
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
            with self._dual_pair_pin(self._producer):
                with self._guards(lease, cookie) as binding:
                    cookie_first, mcp_first = self._capture_dual(binding, lease, selection)
                    cookie_last, mcp_last = self._capture_dual(binding, lease, selection)
                if cookie_first != cookie_last or mcp_first != mcp_last: _fail("context_stale")
                pair(cookie_first, mcp_first, selection)
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
            request_id = "r." + secrets.token_urlsafe(32)
            if request_id in self._entries: _fail("capacity")
            self._entries[request_id] = _Entry(request_id, weakref.ref(lease), weakref.ref(lease.binding),
                owner_sid, selection, cookie, page_bytes, cookie_first, mcp_first,
                mcp_first.mcp_metadata_binding, end_wall, deadline, mono_deadline)
            self._entries[request_id].page_request_sha256 = self._page_request_descriptor(selection, page_bytes).digest
        return PreparedRequest(request_id, True, len(selection.items))

    def prepare_intent(
        self,
        lease: McpContextReadLease,
        selection: ContextSelection,
        *,
        page_bytes: int = 16384,
        _return_origin: bool = False,
    ) -> PreparedRequest | tuple[PreparedRequest, tuple[object, ...]]:
        self._reject_authority_reentry()
        self._assert_pins()
        _, ContextSelection = _app_types()
        if (type(lease) is not McpContextReadLease or type(selection) is not ContextSelection
                or type(page_bytes) is not int or not 1 <= page_bytes <= 16384
                or type(_return_origin) is not bool):
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
            request_id = "r." + secrets.token_urlsafe(32)
            if request_id in self._entries: _fail("capacity")
            binding = lease.binding
            entry = _Entry(request_id, weakref.ref(lease), weakref.ref(binding), owner_sid,
                selection, "", page_bytes, None, first, first.mcp_metadata_binding, wall,
                deadline, mono_deadline)
            entry.intent = True; entry.state = "intent"; entry.page_request_sha256 = descriptor.digest
            entry_values = tuple(
                _NATIVE_MEMBER_GET(slot, entry, _Entry)
                for slot in _PRISTINE_INTENT_ENTRY_SLOTS
            )
            selection_projection = _TRUSTED_SERVICE_SELECTION_PROJECTION(
                selection, self._selection_types_pin, self._selection_states_pin,
            )
            item_rows, target_digests, metadata_row = (
                _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(first)
            )
            recipient = tuple(
                _NATIVE_MEMBER_GET(slot, binding, McpContextBinding)
                for slot in _BINDING_RECIPIENT_SLOTS
            )
            lease_id = _NATIVE_MEMBER_GET(
                _REQUEST_REJECTION_LEASE_REQUEST_ID_SLOT,
                lease,
                McpContextReadLease,
            )
            metadata = _NATIVE_MEMBER_GET(
                _PRISTINE_INTENT_SNAPSHOT_METADATA_SLOT,
                first,
                CombinedContextSnapshot,
            )
            prepared = PreparedRequest(request_id, True, len(selection_projection))
            prepared_values = tuple(
                _NATIVE_MEMBER_GET(slot, prepared, PreparedRequest)
                for slot in _PRISTINE_INTENT_PREPARED_SLOTS
            )
            origin = (
                entry, first, metadata, selection, lease, binding,
                request_id, lease_id, owner_sid, recipient,
                selection_projection, page_bytes, descriptor.digest,
                item_rows, target_digests, metadata_row,
                wall, deadline, mono_deadline,
                (self._selection_types_pin, self._selection_states_pin),
            )
            if (
                type(origin) is not tuple
                or len(origin) != _PRISTINE_INTENT_ORIGIN_SIZE
                or type(entry) is not _Entry
                or type(first) is not CombinedContextSnapshot
                or type(metadata) is not McpContextMetadataBinding
                or type(selection_projection) is not tuple
                or type(item_rows) is not tuple
                or type(target_digests) is not tuple
                or type(metadata_row) is not tuple
                or type(recipient) is not tuple
                or type(lease_id) is not str
                or type(descriptor.digest) is not str
                or len(descriptor.digest) != 64
                or any(character not in "0123456789abcdef"
                       for character in descriptor.digest)
                or prepared_values != (request_id, True, len(selection_projection))
                or tuple(
                    _NATIVE_MEMBER_GET(slot, entry, _Entry)
                    for slot in _PRISTINE_INTENT_ENTRY_SLOTS
                ) != entry_values
                or _TRUSTED_SERVICE_SELECTION_PROJECTION(
                    selection, self._selection_types_pin, self._selection_states_pin,
                ) != selection_projection
                or _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(first)
                    != (item_rows, target_digests, metadata_row)
                or _NATIVE_MEMBER_GET(
                    _LEASE_AUTHORITY_SLOT, lease, McpContextReadLease,
                ) is not self._authority
                or _NATIVE_MEMBER_GET(
                    _LEASE_BINDING_SLOT, lease, McpContextReadLease,
                ) is not binding
                or tuple(
                    _NATIVE_MEMBER_GET(slot, binding, McpContextBinding)
                    for slot in _BINDING_RECIPIENT_SLOTS
                ) != recipient
            ):
                _fail("context_stale")
            self._entries[request_id] = entry
        return (prepared, origin) if _return_origin else prepared

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
        pair = self._pair_pin
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
            with self._dual_pair_pin(self._producer), self._guards(lease, cookie) as binding:
                if binding is not lease.binding: _fail("invalid_binding")
                cookie_binding = self._cookie_authority.current_binding()
                self._cookie_authority.recheck(cookie_binding)
                if (self._cookie_authority.current_binding() is not cookie_binding
                        or cookie_binding.owner_sid != binding.owner_subject_id
                        or cookie_binding.chat_session_id != binding.chat_session_id
                        or cookie_binding.security_revision != binding.security_action_revision):
                    _fail("invalid_binding")
                self._registered_app_method("_assert_owned_chat")(app, cookie_binding)
                cookie_first, mcp_first = self._capture_dual(
                    binding, lease, entry.selection,
                )
                cookie_last, mcp_last = self._capture_dual(
                    binding, lease, entry.selection,
                )
                if (cookie_first != cookie_last or mcp_first != mcp_last
                        or entry.mcp_snapshot is None or mcp_first != entry.mcp_snapshot):
                    _fail("context_stale")
                pair(cookie_first, mcp_first, entry.selection)
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
        captured_issue = self._issue_captured_permit_pin
        if (type(self).__dict__.get("_issue_captured_permit") is not captured_issue
                or "_issue_captured_permit" in vars(self)):
            self._assert_request_pins(request_id)
            _fail("invalid_binding")
        permit, _fresh_snapshot = captured_issue(self, request_id, None, None, None)
        return permit

    def _issue_captured_permit(
        self,
        request_id: str,
        lease: McpContextReadLease | None,
        original_selection: object | None,
        page_bytes: int | None,
    ) -> tuple[Permit, CombinedContextSnapshot]:
        """Issue one permit together with the exact fresh MCP sample that admitted it."""
        content_admission = lease is not None or original_selection is not None or page_bytes is not None
        if content_admission:
            if (type(lease) is not McpContextReadLease
                    or type(original_selection) is not self._selection_types_pin[0]
                    or type(page_bytes) is not int or not 1 <= page_bytes <= 16384):
                _fail("invalid_request")
            with self._lock:
                early_entry = dict.get(self._entries, request_id) if type(request_id) is str else None
                early_lease = early_entry.lease_ref() if type(early_entry) is _Entry else None
                if (type(early_entry) is not _Entry
                        or type(early_entry.state) is not str
                        or early_entry.state != "disclosed"
                        or type(early_entry.request_id) is not str
                        or early_entry.request_id != request_id or early_lease is not lease
                        or early_entry.binding_ref() is not lease.binding
                        or early_entry.selection is not original_selection
                        or type(early_entry.page_bytes) is not int
                        or early_entry.page_bytes != page_bytes):
                    if type(early_entry) is _Entry:
                        self._terminate(early_entry)
                    _fail("invalid_binding")
            descriptor_slot = self._page_request_descriptor_pin
            if (type(descriptor_slot) is not staticmethod
                    or type(self).__dict__.get("_page_request_descriptor") is not descriptor_slot
                    or "_page_request_descriptor" in vars(self)):
                with self._lock:
                    if self._entries.get(request_id) is early_entry:
                        self._terminate(early_entry)
                _fail("invalid_binding")
            try:
                descriptor = staticmethod.__get__(
                    descriptor_slot, None, type(self),
                )(original_selection, page_bytes)
            except BaseException:
                with self._lock:
                    if self._entries.get(request_id) is early_entry:
                        self._terminate(early_entry)
                raise
            if early_entry.page_request_sha256 != descriptor.digest:
                with self._lock:
                    if self._entries.get(request_id) is early_entry:
                        self._terminate(early_entry)
                _fail("invalid_binding")
        else:
            descriptor = None
        self._reject_authority_reentry()
        self._assert_request_pins(request_id)
        fresh_pair = self._fresh_pair_pin
        if (type(self).__dict__.get("_fresh_pair") is not fresh_pair
                or "_fresh_pair" in vars(self)):
            with self._lock:
                pinned_entry = self._entries.get(request_id) if type(request_id) is str else None
                if type(pinned_entry) is _Entry:
                    self._terminate(pinned_entry)
            _fail("invalid_binding")
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
            retained_lease = entry.lease_ref(); binding = entry.binding_ref()
            if retained_lease is None or binding is None:
                self._terminate(entry); _fail("invalid_binding")
            if (content_admission
                    and (retained_lease is not lease or entry.binding_ref() is not lease.binding
                         or entry.selection is not original_selection
                         or entry.page_bytes != page_bytes
                         or descriptor is None
                         or entry.page_request_sha256 != descriptor.digest)):
                self._terminate(entry); _fail("invalid_binding")
            entry.state = "permit_issuing"; entry.generation += 1
            service_generation = entry.generation; service_nonce = secrets.randbits(63)
            entry.permit_operation_nonce = service_nonce
        try:
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("invalid_binding")
            fresh_snapshot = fresh_pair(self, entry, retained_lease)
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
            _NATIVE_MEMBER_SET(
                _PERMIT_CAPSULE_REF_SLOT, handle,
                _NATIVE_WEAKREF_REF(self._native_rejection_capsule),
            )
            permit_ref = weakref.ref(handle, lambda dead, k=key: self._drop_permit(k, dead))
            entry.permit_ref = permit_ref; entry.permit_nonce = nonce
            entry.permit_deadline = wall_deadline; entry.permit_mono_deadline = mono_deadline
            entry.state = "permitted"; entry.generation = generation; entry.permit_operation_nonce = None
            recipient = (binding.owner_subject_id, binding.chat_session_id, binding.token_id,
                         binding.scope, binding.cache_generation, binding.security_action_revision,
                         binding.process_epoch)
            self._permits[key] = _PermitRow(
                permit_ref, weakref.ref(entry), weakref.ref(self), weakref.ref(self._authority),
                weakref.ref(retained_lease), weakref.ref(binding), recipient, weakref.ref(thread),
                threading.get_ident(), os.getpid(), entry.request_id, generation, nonce)
            return handle, fresh_snapshot

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

    def _reject_native_permit(
        self,
        permit: object,
        _permit_type: type = Permit,
        _capsule_type: type = _NativePermitRejectionCapsule,
        _permit_row_type: type = _PermitRow,
        _entry_type: type = _Entry,
        _exchange_row_type: type = _HumanExchangeRow,
        _completion_row_type: type = _CompletionRow,
        _native_completion_row_type: type = _NativeCompletionRow,
        _lease_type: type = McpContextReadLease,
        _binding_type: type = McpContextBinding,
        _weakref_type: type = _NATIVE_WEAKREF_TYPE,
        _rlock_type: type = _NATIVE_RLOCK_TYPE,
        _local_type: type = _NATIVE_LOCAL_TYPE,
        _weakref_call: object = _NATIVE_WEAKREF_CALL,
        _member_get: object = _NATIVE_MEMBER_GET,
        _member_set: object = _NATIVE_MEMBER_SET,
        _permit_capsule_slot: object = _PERMIT_CAPSULE_REF_SLOT,
        _capsule_slots: tuple[object, ...] = _CAPSULE_SLOTS,
        _permit_row_slots: tuple[object, ...] = _PERMIT_ROW_SLOTS,
        _permit_row_entry_ref_slot: object = _PERMIT_ROW_ENTRY_REF_SLOT,
        _human_row_request_id_slot: object = _HUMAN_ROW_REQUEST_ID_SLOT,
        _completion_row_entry_ref_slot: object = _COMPLETION_ROW_ENTRY_REF_SLOT,
        _native_completion_row_entry_ref_slot: object = _NATIVE_COMPLETION_ROW_ENTRY_REF_SLOT,
        _entry_request_id_slot: object = _ENTRY_REQUEST_ID_SLOT,
        _entry_state_slot: object = _ENTRY_STATE_SLOT,
        _entry_permit_ref_slot: object = _ENTRY_PERMIT_REF_SLOT,
        _entry_generation_slot: object = _ENTRY_GENERATION_SLOT,
        _entry_permit_nonce_slot: object = _ENTRY_PERMIT_NONCE_SLOT,
        _entry_permit_deadline_slot: object = _ENTRY_PERMIT_DEADLINE_SLOT,
        _entry_permit_mono_deadline_slot: object = _ENTRY_PERMIT_MONO_DEADLINE_SLOT,
        _entry_scrub_slot_values: tuple[tuple[object, object], ...] = _ENTRY_SCRUB_SLOT_VALUES,
        _lease_authority_slot: object = _LEASE_AUTHORITY_SLOT,
        _lease_binding_slot: object = _LEASE_BINDING_SLOT,
        _binding_recipient_slots: tuple[object, ...] = _BINDING_RECIPIENT_SLOTS,
        _authority_type: type = McpContextAuthority,
        _current_thread: object = _NATIVE_CURRENT_THREAD,
        _get_ident: object = _NATIVE_GET_IDENT,
        _getpid: object = _NATIVE_GETPID,
    ) -> bool:
        """Consume and scrub one genuine still-permitted native publication permit."""
        try:
            if type(permit) is not _permit_type:
                return False
            capsule_ref = _member_get(_permit_capsule_slot, permit, _permit_type)
            if type(capsule_ref) is not _weakref_type:
                return False
            capsule = _weakref_call(capsule_ref)
            if type(capsule) is not _capsule_type:
                return False
            (service_ref, lock, entries, permits, exchanges, completions, native_completions,
             authority, execution_local) = tuple(
                _member_get(slot, capsule, _capsule_type) for slot in _capsule_slots
            )
            if (type(service_ref) is not _weakref_type or _weakref_call(service_ref) is not self
                    or type(lock) is not _rlock_type or type(entries) is not dict
                    or type(permits) is not dict or type(exchanges) is not dict
                    or type(completions) is not dict or type(native_completions) is not dict
                    or type(authority) is not _authority_type
                    or type(execution_local) is not _local_type):
                return False
            if getattr(execution_local, "active", False):
                execution_local.violation = True
                return False
            current_thread = _current_thread()
            current_ident = _get_ident()
            current_pid = _getpid()
            with lock:
                row = dict.get(permits, id(permit))
                if type(row) is not _permit_row_type:
                    return False
                (permit_ref, entry_ref, row_service_ref, authority_ref, lease_ref, binding_ref,
                 recipient, thread_ref, thread_ident, pid, request_id, generation, nonce) = tuple(
                    _member_get(slot, row, _permit_row_type) for slot in _permit_row_slots
                )
                refs = (
                    permit_ref, entry_ref, row_service_ref, authority_ref, lease_ref, binding_ref,
                    thread_ref,
                )
                if any(type(ref) is not _weakref_type for ref in refs):
                    return False
                entry = _weakref_call(entry_ref)
                lease = _weakref_call(lease_ref)
                binding = _weakref_call(binding_ref)
                if (type(entry) is not _entry_type or type(lease) is not _lease_type
                        or type(binding) is not _binding_type):
                    return False
                entry_request_id = _member_get(_entry_request_id_slot, entry, _entry_type)
                entry_state = _member_get(_entry_state_slot, entry, _entry_type)
                entry_permit_ref = _member_get(_entry_permit_ref_slot, entry, _entry_type)
                entry_generation = _member_get(_entry_generation_slot, entry, _entry_type)
                entry_nonce = _member_get(_entry_permit_nonce_slot, entry, _entry_type)
                entry_deadline = _member_get(_entry_permit_deadline_slot, entry, _entry_type)
                entry_mono_deadline = _member_get(
                    _entry_permit_mono_deadline_slot, entry, _entry_type,
                )
                if type(entry_permit_ref) is not _weakref_type:
                    return False
                lease_authority = _member_get(_lease_authority_slot, lease, _lease_type)
                lease_binding = _member_get(_lease_binding_slot, lease, _lease_type)
                direct_recipient = tuple(
                    _member_get(slot, binding, _binding_type) for slot in _binding_recipient_slots
                )
                recipient_types = (str, str, str, str, int, int, str)
                if (type(recipient) is not tuple or len(recipient) != len(recipient_types)
                        or len(direct_recipient) != len(recipient_types)
                        or any(type(value) is not expected
                               for value, expected in zip(recipient, recipient_types))
                        or any(type(value) is not expected
                               for value, expected in zip(direct_recipient, recipient_types))
                        or type(request_id) is not str or type(entry_request_id) is not str
                        or type(entry_state) is not str
                        or type(generation) is not int or isinstance(generation, bool)
                        or type(nonce) is not int or isinstance(nonce, bool)
                        or type(entry_generation) is not int or isinstance(entry_generation, bool)
                        or type(entry_nonce) is not int or isinstance(entry_nonce, bool)
                        or type(thread_ident) is not int or isinstance(thread_ident, bool)
                        or type(pid) is not int or isinstance(pid, bool)
                        or entry_deadline is None or entry_mono_deadline is None
                        or _weakref_call(permit_ref) is not permit
                        or _weakref_call(row_service_ref) is not self
                        or _weakref_call(authority_ref) is not authority
                        or _weakref_call(thread_ref) is not current_thread
                        or thread_ident != current_ident or pid != current_pid
                        or entry_request_id != request_id or dict.get(entries, request_id) is not entry
                        or entry_state != "permitted" or entry_permit_ref is not permit_ref
                        or _weakref_call(entry_permit_ref) is not permit
                        or entry_generation != generation or entry_nonce != nonce
                        or lease_authority is not authority or lease_binding is not binding
                        or direct_recipient != recipient
                        or dict.get(permits, id(permit)) is not row):
                    return False

                permit_keys: list[int] = []
                exchange_keys: list[int] = []
                completion_keys: list[int] = []
                native_completion_keys: list[int] = []
                for key, candidate in tuple(dict.items(entries)):
                    if type(key) is not str or type(candidate) is not _entry_type:
                        return False
                for key, candidate in tuple(dict.items(permits)):
                    if type(key) is not int or type(candidate) is not _permit_row_type:
                        return False
                    candidate_ref = _member_get(
                        _permit_row_entry_ref_slot, candidate, _permit_row_type,
                    )
                    if type(candidate_ref) is not _weakref_type:
                        return False
                    if _weakref_call(candidate_ref) is entry:
                        permit_keys.append(key)
                for key, candidate in tuple(dict.items(exchanges)):
                    if type(key) is not int or type(candidate) is not _exchange_row_type:
                        return False
                    candidate_request_id = _member_get(
                        _human_row_request_id_slot, candidate, _exchange_row_type,
                    )
                    if type(candidate_request_id) is not str:
                        return False
                    if candidate_request_id == request_id:
                        exchange_keys.append(key)
                for key, candidate in tuple(dict.items(completions)):
                    if type(key) is not int or type(candidate) is not _completion_row_type:
                        return False
                    candidate_ref = _member_get(
                        _completion_row_entry_ref_slot, candidate, _completion_row_type,
                    )
                    if type(candidate_ref) is not _weakref_type:
                        return False
                    if _weakref_call(candidate_ref) is entry:
                        completion_keys.append(key)
                for key, candidate in tuple(dict.items(native_completions)):
                    if type(key) is not int or type(candidate) is not _native_completion_row_type:
                        return False
                    candidate_ref = _member_get(
                        _native_completion_row_entry_ref_slot, candidate,
                        _native_completion_row_type,
                    )
                    if type(candidate_ref) is not _weakref_type:
                        return False
                    if _weakref_call(candidate_ref) is entry:
                        native_completion_keys.append(key)
                if id(permit) not in permit_keys:
                    return False

                for key in permit_keys:
                    dict.pop(permits, key, None)
                for key in exchange_keys:
                    dict.pop(exchanges, key, None)
                for key in completion_keys:
                    dict.pop(completions, key, None)
                for key in native_completion_keys:
                    dict.pop(native_completions, key, None)
                for slot, value in _entry_scrub_slot_values:
                    _member_set(slot, entry, value)
                _member_set(_entry_state_slot, entry, "expired")
                _member_set(_entry_generation_slot, entry, entry_generation + 1)
                if dict.get(entries, request_id) is entry:
                    dict.pop(entries, request_id, None)
                return True
        except BaseException:
            return False

    def _revalidate_under_guard(self, entry: _Entry, lease: McpContextReadLease, binding: object) -> None:
        pair = self._pair_pin
        now, mono = self._clock()
        if getattr(self._execution_local, "violation", False): _fail("revoked")
        if (now >= entry.deadline or mono >= entry.mono_deadline or now >= entry.permit_deadline
                or mono >= entry.permit_mono_deadline or entry.selection is None
                or entry.cookie_snapshot is None or entry.mcp_snapshot is None): _fail("expired")
        if lease is not entry.lease_ref() or binding is not lease.binding or entry.binding_ref() is not binding:
            _fail("invalid_binding")
        with self._dual_pair_pin(self._producer):
            c1, m1 = self._capture_dual(binding, lease, entry.selection)
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("readback_failed")
            self._registered_app_method("_readback_ack_under_guard")(app, entry, binding)
            c2, m2 = self._capture_dual(binding, lease, entry.selection)
            if c1 != c2 or m1 != m2 or c1 != entry.cookie_snapshot or m1 != entry.mcp_snapshot:
                _fail("context_stale")
            pair(c1, m1, entry.selection)
            if (c1.mcp_metadata_binding is not None or m1.mcp_metadata_binding is None
                    or m1.mcp_metadata_binding.canonical() != entry.metadata_binding.canonical()): _fail("context_stale")
        self._assert_pins()
        if getattr(self._execution_local, "violation", False): _fail("revoked")

    def _revalidate_planning_content_under_guard(
        self,
        entry: _Entry,
        lease: McpContextReadLease,
        binding: object,
        target: object,
    ) -> bytes:
        pair = self._pair_pin
        capture_content = self._capture_dual_content_pin
        now, mono = self._clock()
        if getattr(self._execution_local, "violation", False):
            _fail("revoked")
        if (
            now >= entry.deadline
            or mono >= entry.mono_deadline
            or now >= entry.permit_deadline
            or mono >= entry.permit_mono_deadline
            or entry.selection is None
            or entry.cookie_snapshot is None
            or entry.mcp_snapshot is None
        ):
            _fail("expired")
        if (
            lease is not entry.lease_ref()
            or binding is not lease.binding
            or entry.binding_ref() is not binding
        ):
            _fail("invalid_binding")
        with self._dual_pair_pin(self._producer):
            first = capture_content(self, binding, lease, entry.selection, target)
            app = self._app_ref() if self._app_ref is not None else None
            if app is None:
                _fail("readback_failed")
            self._registered_app_method("_readback_ack_under_guard")(
                app, entry, binding,
            )
            final = capture_content(self, binding, lease, entry.selection, target)
            if (
                type(first) is not tuple
                or type(final) is not tuple
                or len(first) != 4
                or len(final) != 4
            ):
                _fail("context_stale")
            c1, m1, cookie_payload1, mcp_payload1 = first
            c2, m2, cookie_payload2, mcp_payload2 = final
            if (
                c1 != c2
                or m1 != m2
                or c1 != entry.cookie_snapshot
                or m1 != entry.mcp_snapshot
            ):
                _fail("context_stale")
            pair(c1, m1, entry.selection)
            if (
                c1.mcp_metadata_binding is not None
                or m1.mcp_metadata_binding is None
                or m1.mcp_metadata_binding.canonical()
                    != entry.metadata_binding.canonical()
                or type(cookie_payload1) is not bytes
                or type(mcp_payload1) is not bytes
                or type(cookie_payload2) is not bytes
                or type(mcp_payload2) is not bytes
                or cookie_payload1 != mcp_payload1
                or cookie_payload1 != cookie_payload2
                or cookie_payload1 != mcp_payload2
            ):
                _fail("context_stale")
            selected = next(
                (
                    item for item in m1.items
                    if (
                        item.source,
                        item.selection_ref,
                        item.revision_ref,
                    ) == (
                        target.source,
                        target.selection_ref,
                        target.revision_ref,
                    )
                ),
                None,
            )
            if (
                selected is None
                or selected.payload_sha256 is None
                or hashlib.sha256(cookie_payload1).hexdigest()
                    != selected.payload_sha256
            ):
                _fail("context_stale")
            payload = cookie_payload1
        self._assert_pins()
        if getattr(self._execution_local, "violation", False):
            _fail("revoked")
        return payload

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
        from src.planning_context_reader import PlanningContextPage, PlanningContextReaderError
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
            content_revalidation = self._planning_content_revalidation_pin
            native_item = next((item for item in entry.mcp_snapshot.items
                                if (item.source, item.selection_ref, item.revision_ref) ==
                                   (target.source, target.selection_ref, target.revision_ref)), None)
            if native_item is None or native_item.payload_sha256 is None: _fail("context_stale")
            with self._guards(lease, entry.cookie) as held_binding:
                if held_binding is not binding: _fail("invalid_binding")
                cookie_binding = self._cookie_authority.current_binding()
                self._cookie_authority.recheck(cookie_binding)
                if (cookie_binding.chat_session_id != binding.chat_session_id
                        or cookie_binding.owner_sid != binding.owner_subject_id): _fail("invalid_binding")
                self._assert_pins()
                payload = content_revalidation(
                    self, entry, lease, held_binding, target,
                )
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

    def _execute_native_page(self, opaquePermit: object, *, source: str, target_index: int, offset: int,
                             validate_detached: object = None) -> VerifiedNativeCapture:
        self._reject_authority_reentry()
        if (type(source) is not str or source not in {"forge", "knowledge"}
                or type(target_index) is not int or not 0 <= target_index <= (1 << 53) - 1
                or type(offset) is not int or not 0 <= offset <= (1 << 53) - 1
                or validate_detached is not None and (type(validate_detached) not in {types.FunctionType, types.MethodType}
                    or inspect.iscoroutinefunction(validate_detached)
                    or inspect.isgeneratorfunction(validate_detached)
                    or inspect.isasyncgenfunction(validate_detached))):
            _fail("invalid_request")
        try: instance_methods = vars(self)
        except BaseException: instance_methods = {"_execute_native_page": None}
        if (type(self).__dict__.get("_execute_native_page") is not _TRUSTED_NATIVE_EXECUTOR
                or type(self).__dict__.get("_consume_native_completion") is not _TRUSTED_NATIVE_COMPLETION_CONSUMER
                or "_execute_native_page" in instance_methods or "_consume_native_completion" in instance_methods
                or globals().get("_assert_native_body_pins") is not _TRUSTED_ASSERT_NATIVE_BODY_PINS):
            _fail("permit_invalid")
        entry, lease, binding, generation, nonce = self._reserve_permit(opaquePermit, "executing")
        self._execution_local.active = True; self._execution_local.violation = False
        try:
            now, mono = self._clock()
            if (getattr(self._execution_local, "violation", False)
                    or now >= entry.permit_deadline or mono >= entry.permit_mono_deadline): _fail("permit_invalid")
            _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, binding); self._assert_pins()
            selection = entry.selection
            if (selection is None or target_index >= len(selection.items)
                    or entry.page_request_sha256 is None or not 1 <= entry.page_bytes <= 16384
                    or entry.cookie_snapshot is None or entry.mcp_snapshot is None): _fail("invalid_request")
            target = selection.items[target_index]
            if target.source != source: _fail("invalid_request")
            key = (target.source, target.selection_ref, target.revision_ref)
            cookie_matches = tuple(item for item in entry.cookie_snapshot.items
                                   if (item.source, item.selection_ref, item.revision_ref) == key)
            mcp_matches = tuple(item for item in entry.mcp_snapshot.items
                                if (item.source, item.selection_ref, item.revision_ref) == key)
            if len(cookie_matches) != 1 or len(mcp_matches) != 1: _fail("context_stale")
            cookie_item, mcp_item = cookie_matches[0], mcp_matches[0]
            paired = lambda item: (item.source, item.selection_ref, item.revision_ref,
                                   item.payload_sha256, item.classification, item.provenance_ref)
            if paired(cookie_item) != paired(mcp_item): _fail("context_stale")
            target_digests = entry.cookie_snapshot.target_manifest_digests
            if (len(target_digests) != len(selection.items) or selection.items[target_index] is not target
                    or type(target_digests[target_index]) is not str): _fail("context_stale")
            expected_manifest_digest = target_digests[target_index]
            with self._guards(lease, entry.cookie) as held_binding:
                if held_binding is not binding: _fail("invalid_binding")
                self._revalidate_under_guard(entry, lease, held_binding)
                cookie_binding = self._cookie_authority.current_binding()
                self._cookie_authority.recheck(cookie_binding)
                if (cookie_binding.chat_session_id != binding.chat_session_id
                        or cookie_binding.owner_sid != binding.owner_subject_id
                        or cookie_binding.security_revision != binding.security_action_revision): _fail("invalid_binding")
                selected_before = _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, cookie_binding)
                if source == "forge":
                    locator = target.forge_locator
                    if locator is None: _fail("invalid_request")
                    page = _TRUSTED_FORGE_HELPER(
                        metadata_provider=self._producer._forge,
                        local_forge=self._producer._forge._local_forge,
                        binding=cookie_binding, selection=target, locator=locator,
                        expected_item=cookie_item, expected_manifest_digest=expected_manifest_digest,
                        offset=offset, page_bytes=entry.page_bytes)
                    page_type = _TRUSTED_FORGE_PAGE
                else:
                    if (target.forge_locator is not None
                            or cookie_item.boundary_sha256 == mcp_item.boundary_sha256
                            or cookie_item.record_sha256 == mcp_item.record_sha256): _fail("context_stale")
                    _, ContextSelection = _app_types()
                    knowledge_selection = ContextSelection(tuple(item for item in selection.items if item.source == "knowledge"))
                    page = _TRUSTED_KNOWLEDGE_HELPER(
                        composition=self._producer._knowledge._composition,
                        binding=cookie_binding, knowledge_selection=knowledge_selection,
                        target=target, expected_item=mcp_item,
                        expected_manifest_digest=expected_manifest_digest,
                        offset=offset, page_bytes=entry.page_bytes)
                    page_type = _TRUSTED_KNOWLEDGE_PAGE
                selected_after = _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, cookie_binding)
                if source == "knowledge" and (type(selected_after) is not tuple
                        or selected_after[1:4] != selected_before[1:4]
                        or selected_after[4] is not selected_before[4]):
                    _fail("context_stale")
                page_commitment = self._native_page_commitment(source, page, page_type)
                if validate_detached is not None:
                    child_result = validate_detached(page)
                    if inspect.isawaitable(child_result) or inspect.isgenerator(child_result) or inspect.isasyncgen(child_result):
                        close = getattr(child_result, "close", None)
                        if callable(close):
                            try: close()
                            except BaseException: pass
                        _fail("invalid_request")
                    if child_result is not None: _fail("invalid_request")
                selected_detached = _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, cookie_binding)
                if source == "knowledge" and (type(selected_detached) is not tuple
                        or selected_detached[1:4] != selected_after[1:4]
                        or selected_detached[4] is not selected_after[4]):
                    _fail("context_stale")
                if (self._native_page_commitment(source, page, page_type) != page_commitment
                        or self._cookie_authority.current_binding() is not cookie_binding
                        or selection.items[target_index] is not target
                        or target.source != source): _fail("context_stale")
                self._cookie_authority.recheck(cookie_binding)
                self._revalidate_under_guard(entry, lease, held_binding)
                _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, cookie_binding)
                self._assert_pins()
            final_clock = self._clock(); _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, binding)
            self._assert_pins()
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
                receipt = _NativeCompletionReceipt(); capture = VerifiedNativeCapture(source, page, receipt)
                completion_key = id(receipt)
                receipt_ref = weakref.ref(receipt, lambda dead, k=completion_key: self._drop_native_completion(k, dead))
                capture_ref = weakref.ref(capture, lambda dead, k=completion_key: self._drop_native_capture(k, dead))
                self._native_completions[completion_key] = _NativeCompletionRow(
                    receipt_ref, capture_ref, weakref.ref(entry), weakref.ref(self), weakref.ref(self._authority),
                    weakref.ref(lease), weakref.ref(binding), weakref.ref(threading.current_thread()),
                    entry.request_id, generation, nonce, os.getpid(), self._recipient(binding), source, page_commitment)
                entry.state = "consumed"
                return capture
        except (McpPrivateContextConfirmationError, McpContextAuthorityError):
            with self._lock:
                for completion_key, row in tuple(self._native_completions.items()):
                    if row.entry_ref() is entry: self._native_completions.pop(completion_key, None)
                if self._entries.get(entry.request_id) is entry: self._terminate(entry)
            raise
        except BaseException:
            with self._lock:
                for completion_key, row in tuple(self._native_completions.items()):
                    if row.entry_ref() is entry: self._native_completions.pop(completion_key, None)
                if self._entries.get(entry.request_id) is entry: self._terminate(entry)
            _fail("context_stale")
        finally:
            self._execution_local.active = False; self._execution_local.violation = False

    def _drop_native_completion(self, key: int, dead: weakref.ReferenceType[_NativeCompletionReceipt]) -> None:
        with self._lock:
            row = self._native_completions.get(key)
            if row is not None and row.receipt_ref is dead:
                self._native_completions.pop(key, None); entry = row.entry_ref()
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)

    def _drop_native_capture(self, key: int, dead: weakref.ReferenceType[VerifiedNativeCapture]) -> None:
        with self._lock:
            row = self._native_completions.get(key)
            if row is not None and row.capture_ref is dead:
                self._native_completions.pop(key, None); entry = row.entry_ref()
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)

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

    def _consume_native_completion(self, capture: object) -> None:
        self._reject_authority_reentry()
        if (type(capture) is not VerifiedNativeCapture or type(capture.source) is not str
                or capture.source not in {"forge", "knowledge"}
                or type(capture.receipt) is not _NativeCompletionReceipt): _fail("permit_invalid")
        receipt = capture.receipt; source = capture.source; page = capture.page
        page_type = _TRUSTED_FORGE_PAGE if source == "forge" else _TRUSTED_KNOWLEDGE_PAGE
        try: instance_methods = vars(self)
        except BaseException: instance_methods = {"_execute_native_page": None}
        if (type(self).__dict__.get("_execute_native_page") is not _TRUSTED_NATIVE_EXECUTOR
                or type(self).__dict__.get("_consume_native_completion") is not _TRUSTED_NATIVE_COMPLETION_CONSUMER
                or "_execute_native_page" in instance_methods or "_consume_native_completion" in instance_methods
                or globals().get("_assert_native_body_pins") is not _TRUSTED_ASSERT_NATIVE_BODY_PINS):
            with self._lock:
                row = self._native_completions.get(id(receipt)); entry = row.entry_ref() if type(row) is _NativeCompletionRow else None
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)
            _fail("permit_invalid")
        with self._lock:
            pin_row = self._native_completions.get(id(receipt))
            pin_entry = pin_row.entry_ref() if type(pin_row) is _NativeCompletionRow else None
            pin_binding = pin_row.binding_ref() if type(pin_row) is _NativeCompletionRow else None
        try:
            if pin_binding is None:
                _fail("permit_invalid")
            _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, pin_binding)
        except BaseException:
            with self._lock:
                if pin_entry is not None and self._entries.get(pin_row.request_id) is pin_entry:
                    self._terminate(pin_entry)
            raise
        with self._lock:
            row = self._native_completions.get(id(receipt))
            if row is None: _fail("permit_invalid")
            entry = row.entry_ref()
            try: page_digest = self._native_page_commitment(source, page, page_type)
            except BaseException: page_digest = None
            if (row.receipt_ref() is not receipt or row.capture_ref() is not capture
                    or row.service_ref() is not self or row.authority_ref() is not self._authority
                    or row.thread_ref() is not threading.current_thread() or row.pid != os.getpid()
                    or row.source != source or page_digest != row.page_sha256):
                self._native_completions.pop(id(receipt), None)
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)
                _fail("permit_invalid")
            if (entry is None or self._entries.get(row.request_id) is not entry or entry.state != "consumed"
                    or entry.generation != row.generation or entry.execution_nonce != row.nonce
                    or row.lease_ref() is None or row.binding_ref() is None
                    or self._recipient(row.binding_ref()) != row.recipient):
                if entry is not None and self._entries.get(row.request_id) is entry: self._terminate(entry)
                _fail("permit_invalid")
            entry.state = "native_completion_consuming"; entry.generation += 1
            reserved_generation = entry.generation; reserved_nonce = entry.execution_nonce
        try:
            lease = row.lease_ref(); binding = row.binding_ref()
            if lease is None or binding is None: _fail("permit_invalid")
            self._clock(); _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, binding); self._assert_pins()
            with self._guards(lease, entry.cookie) as held_binding:
                if held_binding is not binding: _fail("invalid_binding")
                self._revalidate_under_guard(entry, lease, held_binding)
                cookie_binding = self._cookie_authority.current_binding()
                self._cookie_authority.recheck(cookie_binding)
                _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, cookie_binding)
            now, mono = self._clock(); _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, binding); self._assert_pins()
        except BaseException:
            with self._lock:
                if self._entries.get(row.request_id) is entry: self._terminate(entry)
            raise
        with self._lock:
            if (self._closed or self._entries.get(row.request_id) is not entry
                    or self._native_completions.get(id(receipt)) is not row
                    or entry.state != "native_completion_consuming" or entry.generation != reserved_generation
                    or entry.execution_nonce != reserved_nonce or reserved_nonce != row.nonce
                    or row.receipt_ref() is not receipt or row.capture_ref() is not capture
                    or row.thread_ref() is not threading.current_thread() or row.pid != os.getpid()
                    or entry.lease_ref() is not row.lease_ref() or entry.binding_ref() is not row.binding_ref()
                    or entry.permit_deadline is None or entry.permit_mono_deadline is None
                    or self._recipient(row.binding_ref()) != row.recipient
                    or now >= entry.permit_deadline or mono >= entry.permit_mono_deadline
                    or now >= entry.deadline or mono >= entry.mono_deadline
                    or self._native_page_commitment(source, page, page_type) != row.page_sha256):
                if self._entries.get(row.request_id) is entry: self._terminate(entry)
                _fail("permit_invalid")
            self._native_completions.pop(id(receipt), None)
            entry.permit_ref = None; entry.permit_nonce = None
            entry.permit_deadline = None; entry.permit_mono_deadline = None; entry.permit_operation_nonce = None
            entry.execution_nonce = None; entry.execution_thread_ref = None
            entry.execution_thread_ident = None; entry.execution_pid = None
            entry.state = "disclosed"

    @staticmethod
    def _native_page_commitment(source: str, page: object, expected_type: type) -> str:
        if source == "forge": expected_byteform, manifest_prefix = "git_blob", False
        elif source == "knowledge": expected_byteform, manifest_prefix = "knowledge_payload", True
        else: _fail("context_stale")
        try:
            values = (page.payload_bytes, page.total, page.offset, page.end, page.whole_sha256,
                      page.page_sha256, page.manifest_digest, page.byteform)
        except BaseException:
            _fail("context_stale")
        payload, total, offset, end, whole, page_sha, manifest, byteform = values
        sha = lambda value: type(value) is str and len(value) == 71 and value.startswith("sha256:") \
            and all(char in "0123456789abcdef" for char in value[7:])
        raw_sha = lambda value: type(value) is str and len(value) == 64 \
            and all(char in "0123456789abcdef" for char in value)
        if (type(page) is not expected_type or type(payload) is not bytes or len(payload) > 16384
                or type(total) is not int or type(offset) is not int or type(end) is not int
                or not 0 <= offset <= end <= total <= (1 << 53) - 1 or len(payload) != end - offset
                or not sha(whole) or not sha(page_sha)
                or page_sha != "sha256:" + hashlib.sha256(payload).hexdigest()
                or (not sha(manifest) if manifest_prefix else not raw_sha(manifest))
                or type(byteform) is not str or byteform != expected_byteform): _fail("context_stale")
        return hashlib.sha256(_canon({"source": source, "offset": offset, "end": end, "total": total,
            "whole_sha256": whole, "page_sha256": page_sha, "manifest_digest": manifest,
            "byteform": byteform, "payload_sha256": hashlib.sha256(payload).hexdigest()})).hexdigest()

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

    def _fence_disclosed_publication(
        self,
        request_id: str,
        lease: McpContextReadLease,
        original_selection: object,
        authenticated_mcp_snapshot: CombinedContextSnapshot,
        *,
        page_bytes: int,
    ) -> None:
        """Recheck one retained disclosure without issuing or extending authority."""
        if (type(self) is not McpPrivateContextConfirmationService
                or type(request_id) is not str or not request_id
                or type(lease) is not McpContextReadLease
                or type(page_bytes) is not int or not 1 <= page_bytes <= 16384):
            _fail("context_stale")
        cleanup_anchor = _TRUSTED_PUBLICATION_CLEANUP_ANCHOR(self, request_id, lease)
        entry: _Entry | None = None
        try:
            if cleanup_anchor is None:
                _fail("context_stale")
            namespace = type(self).__dict__
            instance = vars(self)
            if (type(self._lock) is not _NATIVE_RLOCK_TYPE
                    or namespace.get("_fence_disclosed_publication") is not _TRUSTED_DISCLOSURE_FENCE
                    or namespace.get("_clock") is not _TRUSTED_FENCE_CLOCK
                    or namespace.get("_guards") is not _TRUSTED_FENCE_GUARDS
                    or namespace.get("_capture_cookie") is not _TRUSTED_FENCE_CAPTURE_COOKIE
                    or namespace.get("_capture_mcp") is not _TRUSTED_FENCE_CAPTURE_MCP
                    or namespace.get("_registered_app_method") is not _TRUSTED_FENCE_APP_METHOD
                    or namespace.get("_valid_attestation") is not _TRUSTED_FENCE_VALID_ATTESTATION
                    or namespace.get("_page_request_descriptor")
                        is not _TRUSTED_PAGE_REQUEST_DESCRIPTOR_SLOT
                    or any(name in instance for name in (
                        "_fence_disclosed_publication", "_clock", "_guards", "_capture_cookie",
                        "_capture_mcp", "_registered_app_method", "_valid_attestation",
                        "_page_request_descriptor",
                    ))):
                _fail("context_stale")
            _TRUSTED_SERVICE_ASSERT_PINS(self)
            selection_projection = _TRUSTED_SERVICE_SELECTION_PROJECTION(
                original_selection, self._selection_types_pin, self._selection_states_pin,
            )
            supplied_mcp = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(authenticated_mcp_snapshot)
            selected_keys = tuple(row[:3] for row in selection_projection)
            supplied_items = {row[:3]: row for row in supplied_mcp[0]}
            if (len(supplied_items) != len(selected_keys)
                    or set(supplied_items) != set(selected_keys)
                    or len(supplied_mcp[1]) != len(selected_keys)):
                _fail("context_stale")
            expected_descriptor = _TRUSTED_PAGE_REQUEST_DESCRIPTOR(original_selection, page_bytes)
            current_thread = _NATIVE_CURRENT_THREAD()
            current_ident = _NATIVE_GET_IDENT()
            current_pid = _NATIVE_GETPID()
            with self._lock:
                entry = self._entries.get(request_id)
                if (self._closed or type(entry) is not _Entry
                        or type(entry.state) is not str or entry.state != "disclosed"
                        or entry.request_id != request_id or entry.lease_ref() is not lease
                        or entry.binding_ref() is not lease.binding
                        or entry.selection is None or entry.page_bytes != page_bytes
                        or entry.page_request_sha256 != expected_descriptor.digest
                        or entry.cookie_snapshot is None or entry.mcp_snapshot is None
                        or entry.receipt is None or entry.ack_wall_time is None
                        or entry.ack_mono_time is None or entry.ack_deadline is None
                        or entry.ack_mono_deadline is None
                        or entry.permit_ref is not None or entry.permit_nonce is not None
                        or entry.permit_deadline is not None or entry.permit_mono_deadline is not None
                        or entry.execution_nonce is not None or entry.execution_thread_ref is not None
                        or entry.execution_thread_ident is not None or entry.execution_pid is not None
                        or not _TRUSTED_FENCE_VALID_ATTESTATION(self, entry)):
                    _fail("context_stale")
                retained_selection = _TRUSTED_SERVICE_SELECTION_PROJECTION(
                    entry.selection, self._selection_types_pin, self._selection_states_pin,
                )
                retained_cookie = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(
                    entry.cookie_snapshot, require_metadata=False,
                )
                retained_mcp = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(entry.mcp_snapshot)
                retained_metadata = _TRUSTED_SLOTS(
                    entry.metadata_binding, _TRUSTED_METADATA_BINDING_CLASS,
                    _TRUSTED_MCP_METADATA_STATE,
                )
                if (retained_selection != selection_projection or retained_mcp != supplied_mcp
                        or retained_metadata != retained_mcp[2]):
                    _fail("context_stale")
                generation = entry.generation
                retained_scalars = (
                    entry.deadline, entry.mono_deadline, entry.ack_wall_time, entry.ack_mono_time,
                    entry.ack_deadline, entry.ack_mono_deadline,
                )
                retained_refs = (
                    entry.metadata_binding, entry.receipt, entry.cookie_snapshot,
                    entry.mcp_snapshot, entry.selection, entry.lease_ref, entry.binding_ref,
                )
            now, mono = _TRUSTED_FENCE_CLOCK(self)
            if (now >= retained_scalars[0] or mono >= retained_scalars[1]
                    or now >= retained_scalars[4] or mono >= retained_scalars[5]):
                _fail("expired")
            binding = lease.binding
            sources = frozenset(row[0] for row in selection_projection)
            for source in sources:
                if source in {"forge", "knowledge"}:
                    _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, binding)
            observation_span = _observation_span_begin("fence.guards_scope")
            try:
                with _TRUSTED_FENCE_GUARDS(self, lease, entry.cookie) as held_binding:
                    if held_binding is not binding or entry.binding_ref() is not binding:
                        _fail("invalid_binding")
                    capture_span = _observation_span_begin("fence.cookie_first")
                    try:
                        cookie_first = _TRUSTED_FENCE_CAPTURE_COOKIE(self, held_binding, original_selection)
                    finally:
                        _observation_span_end(capture_span)
                    capture_span = _observation_span_begin("fence.cookie_last")
                    try:
                        cookie_last = _TRUSTED_FENCE_CAPTURE_COOKIE(self, held_binding, original_selection)
                    finally:
                        _observation_span_end(capture_span)
                    capture_span = _observation_span_begin("fence.mcp_first")
                    try:
                        mcp_first = _TRUSTED_FENCE_CAPTURE_MCP(self, lease, original_selection)
                    finally:
                        _observation_span_end(capture_span)
                    capture_span = _observation_span_begin("fence.mcp_last")
                    try:
                        mcp_last = _TRUSTED_FENCE_CAPTURE_MCP(self, lease, original_selection)
                    finally:
                        _observation_span_end(capture_span)
                    cookie_first_projection = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(
                        cookie_first, require_metadata=False,
                    )
                    cookie_last_projection = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(
                        cookie_last, require_metadata=False,
                    )
                    mcp_first_projection = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(mcp_first)
                    mcp_last_projection = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(mcp_last)
                    if (cookie_first_projection != cookie_last_projection
                            or cookie_first_projection != retained_cookie
                            or mcp_first_projection != mcp_last_projection
                            or mcp_first_projection != retained_mcp
                            or mcp_first_projection != supplied_mcp):
                        _fail("context_stale")
                    cookie_by_key = {row[:3]: row for row in cookie_first_projection[0]}
                    mcp_by_key = {row[:3]: row for row in mcp_first_projection[0]}
                    if (len(cookie_by_key) != len(selected_keys)
                            or len(mcp_by_key) != len(selected_keys)
                            or set(cookie_by_key) != set(selected_keys)
                            or set(mcp_by_key) != set(selected_keys)):
                        _fail("context_stale")
                    for key in selected_keys:
                        cookie_row = cookie_by_key[key]
                        mcp_row = mcp_by_key[key]
                        if ((cookie_row[0], cookie_row[1], cookie_row[2], cookie_row[5],
                                cookie_row[6], cookie_row[7])
                                != (mcp_row[0], mcp_row[1], mcp_row[2], mcp_row[5],
                                    mcp_row[6], mcp_row[7])):
                            _fail("context_stale")
                    app = self._app_ref() if self._app_ref is not None else None
                    if app is None:
                        _fail("readback_failed")
                    readback = _TRUSTED_FENCE_APP_METHOD(self, "_readback_ack_under_guard")
                    readback_span = _observation_span_begin("fence.readback")
                    try:
                        readback(app, entry, held_binding)
                    finally:
                        _observation_span_end(readback_span)
                    for source in sources:
                        if source in {"forge", "knowledge"}:
                            _TRUSTED_ASSERT_NATIVE_BODY_PINS(self, source, held_binding)
            finally:
                _observation_span_end(observation_span)
            _TRUSTED_SERVICE_ASSERT_PINS(self)
            final_now, final_mono = _TRUSTED_FENCE_CLOCK(self)
            with self._lock:
                if (self._closed or self._entries.get(request_id) is not entry
                        or type(entry.state) is not str or entry.state != "disclosed"
                        or entry.generation != generation
                        or entry.lease_ref() is not lease or entry.binding_ref() is not binding
                        or entry.page_bytes != page_bytes
                        or entry.page_request_sha256 != expected_descriptor.digest
                        or _NATIVE_CURRENT_THREAD() is not current_thread
                        or _NATIVE_GET_IDENT() != current_ident or _NATIVE_GETPID() != current_pid
                        or final_now >= retained_scalars[0] or final_mono >= retained_scalars[1]
                        or final_now >= retained_scalars[4] or final_mono >= retained_scalars[5]
                        or (entry.deadline, entry.mono_deadline, entry.ack_wall_time,
                            entry.ack_mono_time, entry.ack_deadline, entry.ack_mono_deadline)
                            != retained_scalars
                        or any(current is not retained for current, retained in zip(
                            (entry.metadata_binding, entry.receipt, entry.cookie_snapshot,
                             entry.mcp_snapshot, entry.selection, entry.lease_ref,
                             entry.binding_ref),
                            retained_refs,
                        ))
                        or _TRUSTED_SERVICE_SELECTION_PROJECTION(
                            entry.selection, self._selection_types_pin, self._selection_states_pin,
                        ) != selection_projection
                        or _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(entry.mcp_snapshot) != supplied_mcp
                        or _TRUSTED_SERVICE_SNAPSHOT_PROJECTION(
                            entry.cookie_snapshot, require_metadata=False,
                        ) != retained_cookie
                        or _TRUSTED_SLOTS(
                            entry.metadata_binding, _TRUSTED_METADATA_BINDING_CLASS,
                            _TRUSTED_MCP_METADATA_STATE,
                        ) != retained_metadata
                        or not _TRUSTED_FENCE_VALID_ATTESTATION(self, entry)):
                    _fail("context_stale")
        except BaseException:
            _TRUSTED_TERMINATE_ANCHORED_PUBLICATION(cleanup_anchor)
            _fail("context_stale")

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
            self._permits.clear(); self._exchanges.clear(); self._completions.clear(); self._native_completions.clear()
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
                or type(self._producer).__dict__.get("capture_dual_snapshot") is not self._dual_snapshot_pin
                or type(self._producer).__dict__.get("capture_dual_snapshot_content")
                    is not self._dual_content_snapshot_pin
                or type(self._producer).__dict__.get("_dual_sample_pair") is not self._dual_pair_pin
                or type(self)._pair is not self._pair_pin
                or "_pair" in vars(self)
                or type(self).__dict__.get("_issue_captured_permit")
                    is not self._issue_captured_permit_pin
                or "_issue_captured_permit" in vars(self)
                or type(self).__dict__.get("_fresh_pair") is not self._fresh_pair_pin
                or "_fresh_pair" in vars(self)
                or type(self).__dict__.get("_capture_dual_content")
                    is not self._capture_dual_content_pin
                or "_capture_dual_content" in vars(self)
                or type(self).__dict__.get("_revalidate_planning_content_under_guard")
                    is not self._planning_content_revalidation_pin
                or "_revalidate_planning_content_under_guard" in vars(self)
                or type(self).__dict__.get("_page_request_descriptor")
                    is not self._page_request_descriptor_pin
                or "_page_request_descriptor" in vars(self)
                or self._cookie_authority._auth_manager is not self._auth_pin
                or self._authority._session_factory is not self._session_factory_pin
                or self._authority._session_factory is not self._authority._session_factory_pin
                or globals().get("_selection_types") is not _TRUSTED_SELECTION_TYPES
                or globals().get("_raw_effective_special") is not _TRUSTED_RAW_EFFECTIVE_SPECIAL
                or _TRUSTED_SELECTION_TYPES() != self._selection_types_pin
                or globals().get("_canon") is not _TRUSTED_SERVICE_CANON
                or globals().get("CombinedContextSnapshot") is not _TRUSTED_COMBINED_SNAPSHOT_CLASS
                or globals().get("ManifestItem") is not _TRUSTED_MANIFEST_ITEM_CLASS
                or globals().get("McpContextMetadataBinding") is not _TRUSTED_METADATA_BINDING_CLASS
                or globals().get("_trusted_slots") is not _TRUSTED_SLOTS
                or globals().get("_service_selection_projection")
                    is not _TRUSTED_SERVICE_SELECTION_PROJECTION
                or globals().get("_service_snapshot_projection")
                    is not _TRUSTED_SERVICE_SNAPSHOT_PROJECTION
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    self._selection_types_pin[0], _SELECTION_FIELDS,
                    self._selection_states_pin[0],
                )
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    self._selection_types_pin[1], _SOURCE_SELECTION_FIELDS,
                    self._selection_states_pin[1],
                )
                or not _TRUSTED_VALUE_CLASS_MATCHES(
                    self._selection_types_pin[2], _FORGE_LOCATOR_FIELDS,
                    self._selection_states_pin[2],
                )):
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
        for key, row in tuple(self._native_completions.items()):
            if row.entry_ref() is entry: self._native_completions.pop(key, None)
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
        with self._dual_pair_pin(self._producer), self._guards(lease, entry.cookie) as binding:
            if entry.binding_ref() is not binding: _fail("invalid_binding")
            cookie_first, mcp_first = self._capture_dual(
                binding, lease, entry.selection,
            )
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("readback_failed")
            self._registered_app_method("_readback_ack_under_guard")(app, entry, binding)
            cookie_last, mcp_last = self._capture_dual(
                binding, lease, entry.selection,
            )
            if (cookie_first != cookie_last or mcp_first != mcp_last
                    or cookie_first != entry.cookie_snapshot or mcp_first != entry.mcp_snapshot): _fail("context_stale")
            if (not callable(getattr(entry.metadata_binding, "canonical", None))
                    or not callable(getattr(entry.mcp_snapshot.mcp_metadata_binding, "canonical", None))
                    or entry.mcp_snapshot.mcp_metadata_binding.canonical() != entry.metadata_binding.canonical()): _fail("context_stale")
        if entry.receipt is None: _fail("readback_failed")

    def _fresh_pair(
        self, entry: _Entry, lease: McpContextReadLease,
    ) -> CombinedContextSnapshot:
        selection = entry.selection
        if selection is None: _fail("context_stale")
        pair = self._pair_pin
        with self._dual_pair_pin(self._producer), self._guards(lease, entry.cookie) as binding:
            if entry.binding_ref() is not binding: _fail("invalid_binding")
            c1, m1 = self._capture_dual(binding, lease, selection)
            app = self._app_ref() if self._app_ref is not None else None
            if app is None: _fail("readback_failed")
            self._registered_app_method("_readback_ack_under_guard")(app, entry, binding)
            c2, m2 = self._capture_dual(binding, lease, selection)
            if c1 != c2 or m1 != m2 or c1 != entry.cookie_snapshot or m1 != entry.mcp_snapshot:
                _fail("context_stale")
            pair(c1, m1, selection)
        self._assert_pins()
        return m1

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

    def _capture_dual(
        self,
        binding: object,
        lease: McpContextReadLease,
        selection: object,
    ) -> tuple[CombinedContextSnapshot, CombinedContextSnapshot]:
        capture_dual = self._dual_snapshot_pin
        if (
            type(self._producer).__dict__.get("capture_dual_snapshot")
            is not capture_dual
        ):
            _fail("invalid_binding")
        cookie_binding = self._cookie_authority.current_binding()
        self._cookie_authority.recheck(cookie_binding)
        if (
            cookie_binding.owner_sid != binding.owner_subject_id
            or cookie_binding.chat_session_id != binding.chat_session_id
            or cookie_binding.security_revision != binding.security_action_revision
        ):
            _fail("invalid_binding")
        values = capture_dual(
            self._producer, cookie_binding, lease, selection,
        )
        if type(values) is not tuple or len(values) != 2:
            _fail("context_stale")
        cookie, mcp = values
        if (
            type(cookie) is not CombinedContextSnapshot
            or cookie.mcp_metadata_binding is not None
            or type(mcp) is not CombinedContextSnapshot
            or mcp.mcp_metadata_binding is None
        ):
            _fail("context_stale")
        foundation = self._authority.recheck_lease(lease)
        metadata = mcp.mcp_metadata_binding
        if (
            not callable(getattr(metadata, "canonical", None))
            or metadata.canonical() != {
                "schema": "odysseus.context.mcp_metadata_binding.v1",
                "audience": "authenticated_mcp_context_read",
                "owner_sid": foundation.owner_subject_id,
                "chat_session_id": foundation.chat_session_id,
                "recipient_token_id": foundation.token_id,
                "scope": foundation.scope,
                "cache_generation": foundation.cache_generation,
                "security_revision": foundation.security_action_revision,
                "app_epoch": foundation.process_epoch,
            }
        ):
            _fail("invalid_binding")
        return cookie, mcp

    def _capture_dual_content(
        self,
        binding: object,
        lease: McpContextReadLease,
        selection: object,
        target: object,
    ) -> tuple[CombinedContextSnapshot, CombinedContextSnapshot, bytes, bytes]:
        capture_dual = self._dual_content_snapshot_pin
        if (
            type(self._producer).__dict__.get("capture_dual_snapshot_content")
            is not capture_dual
        ):
            _fail("invalid_binding")
        cookie_binding = self._cookie_authority.current_binding()
        self._cookie_authority.recheck(cookie_binding)
        if (
            cookie_binding.owner_sid != binding.owner_subject_id
            or cookie_binding.chat_session_id != binding.chat_session_id
            or cookie_binding.security_revision != binding.security_action_revision
        ):
            _fail("invalid_binding")
        values = capture_dual(
            self._producer, cookie_binding, lease, selection, target,
        )
        if type(values) is not tuple or len(values) != 4:
            _fail("context_stale")
        cookie, mcp, cookie_payload, mcp_payload = values
        if (
            type(cookie) is not CombinedContextSnapshot
            or cookie.mcp_metadata_binding is not None
            or type(mcp) is not CombinedContextSnapshot
            or mcp.mcp_metadata_binding is None
            or type(cookie_payload) is not bytes
            or type(mcp_payload) is not bytes
        ):
            _fail("context_stale")
        foundation = self._authority.recheck_lease(lease)
        metadata = mcp.mcp_metadata_binding
        if (
            not callable(getattr(metadata, "canonical", None))
            or metadata.canonical() != {
                "schema": "odysseus.context.mcp_metadata_binding.v1",
                "audience": "authenticated_mcp_context_read",
                "owner_sid": foundation.owner_subject_id,
                "chat_session_id": foundation.chat_session_id,
                "recipient_token_id": foundation.token_id,
                "scope": foundation.scope,
                "cache_generation": foundation.cache_generation,
                "security_revision": foundation.security_action_revision,
                "app_epoch": foundation.process_epoch,
            }
        ):
            _fail("invalid_binding")
        return cookie, mcp, cookie_payload, mcp_payload
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


_SERVICE_INSTANCE_DICT_SLOT = McpPrivateContextConfirmationService.__dict__["__dict__"]
_SERVICE_INSTANCE_DICT_GET = type(_SERVICE_INSTANCE_DICT_SLOT).__get__


def _publication_cleanup_anchor(
    service: object,
    request_id: object,
    lease: object,
    _service_type: type = McpPrivateContextConfirmationService,
    _capsule_type: type = _NativePermitRejectionCapsule,
    _entry_type: type = _Entry,
    _lease_type: type = McpContextReadLease,
    _binding_type: type = McpContextBinding,
    _authority_type: type = McpContextAuthority,
    _weakref_type: type = _NATIVE_WEAKREF_TYPE,
    _rlock_type: type = _NATIVE_RLOCK_TYPE,
    _local_type: type = _NATIVE_LOCAL_TYPE,
    _weakref_call: object = _NATIVE_WEAKREF_CALL,
    _member_get: object = _NATIVE_MEMBER_GET,
    _instance_dict_slot: object = _SERVICE_INSTANCE_DICT_SLOT,
    _instance_dict_get: object = _SERVICE_INSTANCE_DICT_GET,
    _capsule_slots: tuple[object, ...] = _CAPSULE_SLOTS,
    _entry_request_id_slot: object = _ENTRY_REQUEST_ID_SLOT,
    _entry_state_slot: object = _ENTRY_STATE_SLOT,
    _entry_lease_ref_slot: object = _ENTRY_LEASE_REF_SLOT,
    _entry_binding_ref_slot: object = _ENTRY_BINDING_REF_SLOT,
    _lease_authority_slot: object = _LEASE_AUTHORITY_SLOT,
    _lease_binding_slot: object = _LEASE_BINDING_SLOT,
) -> tuple[object, ...] | None:
    """Capture an original-map cleanup capability for one genuine disclosed entry."""
    try:
        if (type(service) is not _service_type or type(request_id) is not str
                or not request_id or type(lease) is not _lease_type):
            return None
        instance = _instance_dict_get(_instance_dict_slot, service, _service_type)
        if (type(instance) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(instance)))):
            return None
        capsule = dict.get(instance, "_native_rejection_capsule")
        if type(capsule) is not _capsule_type:
            return None
        (service_ref, lock, entries, permits, exchanges, completions, native_completions,
         authority, execution_local) = tuple(
            _member_get(slot, capsule, _capsule_type) for slot in _capsule_slots
        )
        if (type(service_ref) is not _weakref_type or _weakref_call(service_ref) is not service
                or type(lock) is not _rlock_type or type(entries) is not dict
                or type(permits) is not dict or type(exchanges) is not dict
                or type(completions) is not dict or type(native_completions) is not dict
                or type(authority) is not _authority_type
                or type(execution_local) is not _local_type
                or dict.get(instance, "_lock") is not lock
                or dict.get(instance, "_entries") is not entries
                or dict.get(instance, "_permits") is not permits
                or dict.get(instance, "_exchanges") is not exchanges
                or dict.get(instance, "_completions") is not completions
                or dict.get(instance, "_native_completions") is not native_completions
                or dict.get(instance, "_authority") is not authority
                or dict.get(instance, "_execution_local") is not execution_local):
            return None
        with lock:
            entry = None
            for key, candidate in tuple(dict.items(entries)):
                if type(key) is not str or type(candidate) is not _entry_type:
                    return None
                if key == request_id:
                    entry = candidate
            if type(entry) is not _entry_type:
                return None
            entry_request_id = _member_get(_entry_request_id_slot, entry, _entry_type)
            entry_state = _member_get(_entry_state_slot, entry, _entry_type)
            lease_ref = _member_get(_entry_lease_ref_slot, entry, _entry_type)
            binding_ref = _member_get(_entry_binding_ref_slot, entry, _entry_type)
            if (type(entry_request_id) is not str or entry_request_id != request_id
                    or type(entry_state) is not str or entry_state != "disclosed"
                    or type(lease_ref) is not _weakref_type
                    or type(binding_ref) is not _weakref_type
                    or _weakref_call(lease_ref) is not lease):
                return None
            binding = _weakref_call(binding_ref)
            if (type(binding) is not _binding_type
                    or _member_get(_lease_authority_slot, lease, _lease_type) is not authority
                    or _member_get(_lease_binding_slot, lease, _lease_type) is not binding):
                return None
            return (
                service, capsule, lock, entries, permits, exchanges, completions,
                native_completions, authority, execution_local, entry, lease, binding,
                lease_ref, binding_ref, request_id,
            )
    except BaseException:
        return None


def _terminate_anchored_publication(
    anchor: object,
    _service_type: type = McpPrivateContextConfirmationService,
    _capsule_type: type = _NativePermitRejectionCapsule,
    _entry_type: type = _Entry,
    _permit_row_type: type = _PermitRow,
    _exchange_row_type: type = _HumanExchangeRow,
    _completion_row_type: type = _CompletionRow,
    _native_completion_row_type: type = _NativeCompletionRow,
    _lease_type: type = McpContextReadLease,
    _binding_type: type = McpContextBinding,
    _authority_type: type = McpContextAuthority,
    _weakref_type: type = _NATIVE_WEAKREF_TYPE,
    _rlock_type: type = _NATIVE_RLOCK_TYPE,
    _local_type: type = _NATIVE_LOCAL_TYPE,
    _weakref_call: object = _NATIVE_WEAKREF_CALL,
    _member_get: object = _NATIVE_MEMBER_GET,
    _member_set: object = _NATIVE_MEMBER_SET,
    _instance_dict_slot: object = _SERVICE_INSTANCE_DICT_SLOT,
    _instance_dict_get: object = _SERVICE_INSTANCE_DICT_GET,
    _capsule_slots: tuple[object, ...] = _CAPSULE_SLOTS,
    _permit_row_entry_ref_slot: object = _PERMIT_ROW_ENTRY_REF_SLOT,
    _human_row_request_id_slot: object = _HUMAN_ROW_REQUEST_ID_SLOT,
    _completion_row_entry_ref_slot: object = _COMPLETION_ROW_ENTRY_REF_SLOT,
    _native_completion_row_entry_ref_slot: object = _NATIVE_COMPLETION_ROW_ENTRY_REF_SLOT,
    _entry_request_id_slot: object = _ENTRY_REQUEST_ID_SLOT,
    _entry_state_slot: object = _ENTRY_STATE_SLOT,
    _entry_lease_ref_slot: object = _ENTRY_LEASE_REF_SLOT,
    _entry_binding_ref_slot: object = _ENTRY_BINDING_REF_SLOT,
    _entry_generation_slot: object = _ENTRY_GENERATION_SLOT,
    _entry_scrub_slot_values: tuple[tuple[object, object], ...] = _ENTRY_SCRUB_SLOT_VALUES,
    _lease_authority_slot: object = _LEASE_AUTHORITY_SLOT,
    _lease_binding_slot: object = _LEASE_BINDING_SLOT,
) -> bool:
    """Scrub only the still-identical entry proven by an original-map anchor."""
    try:
        if type(anchor) is not tuple or len(anchor) != 16:
            return False
        (service, capsule, lock, entries, permits, exchanges, completions,
         native_completions, authority, execution_local, entry, lease, binding,
         lease_ref, binding_ref, request_id) = anchor
        if (type(service) is not _service_type or type(capsule) is not _capsule_type
                or type(lock) is not _rlock_type or type(entries) is not dict
                or type(permits) is not dict or type(exchanges) is not dict
                or type(completions) is not dict or type(native_completions) is not dict
                or type(authority) is not _authority_type
                or type(execution_local) is not _local_type
                or type(entry) is not _entry_type or type(lease) is not _lease_type
                or type(binding) is not _binding_type or type(lease_ref) is not _weakref_type
                or type(binding_ref) is not _weakref_type or type(request_id) is not str):
            return False
        instance = _instance_dict_get(_instance_dict_slot, service, _service_type)
        if (type(instance) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(instance)))
                or dict.get(instance, "_native_rejection_capsule") is not capsule
                or dict.get(instance, "_lock") is not lock
                or dict.get(instance, "_entries") is not entries
                or dict.get(instance, "_permits") is not permits
                or dict.get(instance, "_exchanges") is not exchanges
                or dict.get(instance, "_completions") is not completions
                or dict.get(instance, "_native_completions") is not native_completions
                or dict.get(instance, "_authority") is not authority
                or dict.get(instance, "_execution_local") is not execution_local):
            return False
        capsule_values = tuple(
            _member_get(slot, capsule, _capsule_type) for slot in _capsule_slots
        )
        retained_capsule_values = (
            lock, entries, permits, exchanges, completions, native_completions,
            authority, execution_local,
        )
        if (len(capsule_values) != 9
                or type(capsule_values[0]) is not _weakref_type
                or _weakref_call(capsule_values[0]) is not service
                or any(current is not retained for current, retained in zip(
                    capsule_values[1:], retained_capsule_values,
                ))):
            return False
        with lock:
            matched_entry = None
            for key, candidate in tuple(dict.items(entries)):
                if type(key) is not str or type(candidate) is not _entry_type:
                    return False
                if key == request_id:
                    matched_entry = candidate
            entry_request_id = _member_get(_entry_request_id_slot, entry, _entry_type)
            entry_state = _member_get(_entry_state_slot, entry, _entry_type)
            current_lease_ref = _member_get(_entry_lease_ref_slot, entry, _entry_type)
            current_binding_ref = _member_get(_entry_binding_ref_slot, entry, _entry_type)
            entry_generation = _member_get(_entry_generation_slot, entry, _entry_type)
            if (type(entry_request_id) is not str or entry_request_id != request_id
                    or type(entry_state) is not str or entry_state != "disclosed"
                    or current_lease_ref is not lease_ref
                    or current_binding_ref is not binding_ref
                    or _weakref_call(lease_ref) is not lease
                    or _weakref_call(binding_ref) is not binding
                    or _member_get(_lease_authority_slot, lease, _lease_type) is not authority
                    or _member_get(_lease_binding_slot, lease, _lease_type) is not binding
                    or type(entry_generation) is not int or isinstance(entry_generation, bool)
                    or matched_entry is not entry):
                return False

            permit_keys: list[int] = []
            exchange_keys: list[int] = []
            completion_keys: list[int] = []
            native_completion_keys: list[int] = []
            for key, candidate in tuple(dict.items(permits)):
                if type(key) is not int or type(candidate) is not _permit_row_type:
                    return False
                candidate_ref = _member_get(
                    _permit_row_entry_ref_slot, candidate, _permit_row_type,
                )
                if type(candidate_ref) is not _weakref_type:
                    return False
                if _weakref_call(candidate_ref) is entry:
                    permit_keys.append(key)
            for key, candidate in tuple(dict.items(exchanges)):
                if type(key) is not int or type(candidate) is not _exchange_row_type:
                    return False
                candidate_request_id = _member_get(
                    _human_row_request_id_slot, candidate, _exchange_row_type,
                )
                if type(candidate_request_id) is not str:
                    return False
                if candidate_request_id == request_id:
                    exchange_keys.append(key)
            for key, candidate in tuple(dict.items(completions)):
                if type(key) is not int or type(candidate) is not _completion_row_type:
                    return False
                candidate_ref = _member_get(
                    _completion_row_entry_ref_slot, candidate, _completion_row_type,
                )
                if type(candidate_ref) is not _weakref_type:
                    return False
                if _weakref_call(candidate_ref) is entry:
                    completion_keys.append(key)
            for key, candidate in tuple(dict.items(native_completions)):
                if type(key) is not int or type(candidate) is not _native_completion_row_type:
                    return False
                candidate_ref = _member_get(
                    _native_completion_row_entry_ref_slot, candidate,
                    _native_completion_row_type,
                )
                if type(candidate_ref) is not _weakref_type:
                    return False
                if _weakref_call(candidate_ref) is entry:
                    native_completion_keys.append(key)

            for key in permit_keys:
                dict.pop(permits, key, None)
            for key in exchange_keys:
                dict.pop(exchanges, key, None)
            for key in completion_keys:
                dict.pop(completions, key, None)
            for key in native_completion_keys:
                dict.pop(native_completions, key, None)
            for slot, value in _entry_scrub_slot_values:
                _member_set(slot, entry, value)
            _member_set(_entry_state_slot, entry, "expired")
            _member_set(_entry_generation_slot, entry, entry_generation + 1)
            if dict.get(entries, request_id) is entry:
                dict.pop(entries, request_id, None)
            return True
    except BaseException:
        return False


_REQUEST_REJECTION_SERVICE_PIN_NAMES = (
    "_reject_authority_reentry", "_registered_request_app_method",
    "_registered_app_method", "_assert_pins", "_terminate", "_scrub",
)
_REQUEST_REJECTION_SERVICE_DESCRIPTORS = tuple(
    (name, McpPrivateContextConfirmationService.__dict__[name])
    for name in _REQUEST_REJECTION_SERVICE_PIN_NAMES
)
_REQUEST_REJECTION_HUMAN_ROW_SLOTS = tuple(
    _HumanExchangeRow.__dict__[name]
    for name in (
        "exchange_ref", "app_ref", "service_ref", "authority_ref", "lease_ref",
        "binding_ref", "thread_ref", "request_id", "challenge", "chat_session_id",
        "generation", "nonce", "raw_user_command", "recipient9", "thread_ident", "pid",
    )
)
_REQUEST_REJECTION_COMPLETION_ROW_SLOTS = tuple(
    _CompletionRow.__dict__[name]
    for name in (
        "receipt_ref", "capture_ref", "entry_ref", "service_ref", "authority_ref",
        "lease_ref", "binding_ref", "thread_ref", "request_id", "generation", "nonce",
        "pid", "recipient", "page_sha256",
    )
)
_REQUEST_REJECTION_NATIVE_COMPLETION_ROW_SLOTS = tuple(
    _NativeCompletionRow.__dict__[name]
    for name in (
        "receipt_ref", "capture_ref", "entry_ref", "service_ref", "authority_ref",
        "lease_ref", "binding_ref", "thread_ref", "request_id", "generation", "nonce",
        "pid", "recipient", "source", "page_sha256",
    )
)
_REQUEST_REJECTION_PLANNING_CAPTURE_RECEIPT_SLOT = (
    VerifiedPlanningCapture.__dict__["receipt"]
)
_REQUEST_REJECTION_NATIVE_CAPTURE_SOURCE_SLOT = VerifiedNativeCapture.__dict__["source"]
_REQUEST_REJECTION_NATIVE_CAPTURE_RECEIPT_SLOT = VerifiedNativeCapture.__dict__["receipt"]
_REQUEST_REJECTION_LEASE_REQUEST_ID_SLOT = (
    McpContextReadLease.__dict__["_McpContextReadLease__request_id"]
)
_NATIVE_LOCAL_DICT_GET = _NATIVE_LOCAL_TYPE.__dict__["__getattribute__"]
_PRISTINE_INTENT_SENTINEL = object()
_PRISTINE_INTENT_ENTRY_NAMES = (
    "request_id", "lease_ref", "binding_ref", "owner_sid", "selection",
    "cookie", "page_bytes", "cookie_snapshot", "mcp_snapshot",
    "metadata_binding", "issued", "deadline", "mono_deadline", "generation",
    "state", "challenge", "challenge_deadline", "challenge_mono_deadline",
    "receipt", "ack_wall_time", "ack_mono_time", "exchange_nonce",
    "ack_deadline", "ack_mono_deadline", "ack_descriptor", "ack_attestation",
    "ack_role_hmacs", "disclosure_generation", "disclosure_nonce",
    "disclosure_operation_id", "pending_ack_operation", "permit_operation_nonce",
    "permit_ref", "permit_nonce", "permit_deadline", "permit_mono_deadline",
    "page_request_sha256", "intent", "execution_nonce", "execution_thread_ref",
    "execution_thread_ident", "execution_pid",
)
_PRISTINE_INTENT_ENTRY_SLOTS = tuple(
    _Entry.__dict__[name] for name in _PRISTINE_INTENT_ENTRY_NAMES
)
_PRISTINE_INTENT_BINDING_AUTHORITY_SLOT = (
    McpContextBinding.__dict__["_McpContextBinding__authority"]
)
_PRISTINE_INTENT_BINDING_ISSUER_SLOT = (
    McpContextBinding.__dict__["_McpContextBinding__issuer"]
)
_PRISTINE_INTENT_AUTHORITY_DICT_SLOT = McpContextAuthority.__dict__["__dict__"]
_PRISTINE_INTENT_AUTHORITY_DICT_GET = type(_PRISTINE_INTENT_AUTHORITY_DICT_SLOT).__get__
_PRISTINE_INTENT_SNAPSHOT_METADATA_SLOT = (
    CombinedContextSnapshot.__dict__["mcp_metadata_binding"]
)
_PRISTINE_INTENT_SNAPSHOT_SLOTS = tuple(
    CombinedContextSnapshot.__dict__[name] for name in _SNAPSHOT_FIELDS
)
_PRISTINE_INTENT_MANIFEST_ITEM_SLOTS = tuple(
    ManifestItem.__dict__[name] for name in _MANIFEST_ITEM_FIELDS
)
_PRISTINE_INTENT_METADATA_SLOTS = tuple(
    McpContextMetadataBinding.__dict__[name] for name in _MCP_METADATA_FIELDS
)
_PRISTINE_INTENT_PREPARED_SLOTS = tuple(
    PreparedRequest.__dict__[name]
    for name in ("request_id", "private_required", "item_count")
)
_PRISTINE_INTENT_ORIGIN_SIZE = 20
_PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS = (
    (
        "_NativePermitRejectionCapsule", _NativePermitRejectionCapsule,
        tuple(zip(
            (
                "service_ref", "lock", "entries", "permits", "exchanges",
                "completions", "native_completions", "authority", "execution_local",
            ),
            _CAPSULE_SLOTS,
        )),
    ),
    (
        "_Entry", _Entry,
        tuple(zip(_PRISTINE_INTENT_ENTRY_NAMES, _PRISTINE_INTENT_ENTRY_SLOTS)),
    ),
    ("_PermitRow", _PermitRow, (("entry_ref", _PERMIT_ROW_ENTRY_REF_SLOT),)),
    (
        "_HumanExchangeRow", _HumanExchangeRow,
        (("request_id", _HUMAN_ROW_REQUEST_ID_SLOT),),
    ),
    (
        "_CompletionRow", _CompletionRow,
        (("entry_ref", _COMPLETION_ROW_ENTRY_REF_SLOT),),
    ),
    (
        "_NativeCompletionRow", _NativeCompletionRow,
        (("entry_ref", _NATIVE_COMPLETION_ROW_ENTRY_REF_SLOT),),
    ),
    (
        "McpContextReadLease", McpContextReadLease,
        (
            ("_McpContextReadLease__authority", _LEASE_AUTHORITY_SLOT),
            ("_McpContextReadLease__binding", _LEASE_BINDING_SLOT),
            ("_McpContextReadLease__request_id", _REQUEST_REJECTION_LEASE_REQUEST_ID_SLOT),
        ),
    ),
    (
        "McpContextBinding", McpContextBinding,
        (
            ("_McpContextBinding__authority", _PRISTINE_INTENT_BINDING_AUTHORITY_SLOT),
            ("_McpContextBinding__issuer", _PRISTINE_INTENT_BINDING_ISSUER_SLOT),
            *tuple(zip(
                (
                    "_McpContextBinding__owner_subject_id",
                    "_McpContextBinding__chat_session_id",
                    "_McpContextBinding__token_id",
                    "_McpContextBinding__scope",
                    "_McpContextBinding__cache_generation",
                    "_McpContextBinding__security_action_revision",
                    "_McpContextBinding__process_epoch",
                ),
                _BINDING_RECIPIENT_SLOTS,
            )),
        ),
    ),
    (
        "CombinedContextSnapshot", CombinedContextSnapshot,
        tuple(zip(_SNAPSHOT_FIELDS, _PRISTINE_INTENT_SNAPSHOT_SLOTS)),
    ),
    (
        "ManifestItem", ManifestItem,
        tuple(zip(_MANIFEST_ITEM_FIELDS, _PRISTINE_INTENT_MANIFEST_ITEM_SLOTS)),
    ),
    (
        "McpContextMetadataBinding", McpContextMetadataBinding,
        tuple(zip(_MCP_METADATA_FIELDS, _PRISTINE_INTENT_METADATA_SLOTS)),
    ),
    (
        "PreparedRequest", PreparedRequest,
        tuple(zip(
            ("request_id", "private_required", "item_count"),
            _PRISTINE_INTENT_PREPARED_SLOTS,
        )),
    ),
    (
        "McpContextAuthority", McpContextAuthority,
        (("__dict__", _PRISTINE_INTENT_AUTHORITY_DICT_SLOT),),
    ),
)


def _acquire_request_rejection_capability(
    service: object,
    _service_type: type = McpPrivateContextConfirmationService,
    _capsule_type: type = _NativePermitRejectionCapsule,
    _authority_type: type = McpContextAuthority,
    _weakref_type: type = _NATIVE_WEAKREF_TYPE,
    _rlock_type: type = _NATIVE_RLOCK_TYPE,
    _local_type: type = _NATIVE_LOCAL_TYPE,
    _weakref_call: object = _NATIVE_WEAKREF_CALL,
    _member_get: object = _NATIVE_MEMBER_GET,
    _instance_dict_slot: object = _SERVICE_INSTANCE_DICT_SLOT,
    _instance_dict_get: object = _SERVICE_INSTANCE_DICT_GET,
    _authority_dict_slot: object = _PRISTINE_INTENT_AUTHORITY_DICT_SLOT,
    _authority_dict_get: object = _PRISTINE_INTENT_AUTHORITY_DICT_GET,
    _capsule_slots: tuple[object, ...] = _CAPSULE_SLOTS,
    _method_descriptors: tuple[tuple[str, object], ...] = _REQUEST_REJECTION_SERVICE_DESCRIPTORS,
    _error_type: type = McpPrivateContextConfirmationError,
) -> tuple[object, ...]:
    """Capture the original storage needed to reject one drifted browser request."""
    try:
        if type(service) is not _service_type:
            raise _error_type("invalid_context")
        class_state = _service_type.__dict__
        instance = _instance_dict_get(_instance_dict_slot, service, _service_type)
        if (type(instance) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(instance)))
                or class_state.get("__getattribute__") is not None
                or class_state.get("__getattr__") is not None
                or any(class_state.get(name) is not descriptor or name in instance
                       for name, descriptor in _method_descriptors)):
            raise _error_type("invalid_context")
        capsule = dict.get(instance, "_native_rejection_capsule")
        if type(capsule) is not _capsule_type:
            raise _error_type("invalid_context")
        (service_ref, lock, entries, permits, exchanges, completions,
         native_completions, authority, execution_local) = tuple(
            _member_get(slot, capsule, _capsule_type) for slot in _capsule_slots
        )
        if (type(service_ref) is not _weakref_type
                or _weakref_call(service_ref) is not service
                or type(lock) is not _rlock_type
                or type(entries) is not dict or type(permits) is not dict
                or type(exchanges) is not dict or type(completions) is not dict
                or type(native_completions) is not dict
                or type(authority) is not _authority_type
                or type(execution_local) is not _local_type
                or dict.get(instance, "_lock") is not lock
                or dict.get(instance, "_entries") is not entries
                or dict.get(instance, "_permits") is not permits
                or dict.get(instance, "_exchanges") is not exchanges
                or dict.get(instance, "_completions") is not completions
                or dict.get(instance, "_native_completions") is not native_completions
                or dict.get(instance, "_authority") is not authority
                or dict.get(instance, "_execution_local") is not execution_local):
            raise _error_type("invalid_context")
        if _authority_type.__dict__.get("__dict__") is not _authority_dict_slot:
            raise _error_type("invalid_context")
        authority_instance = _authority_dict_get(
            _authority_dict_slot, authority, _authority_type,
        )
        if (type(authority_instance) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(authority_instance)))):
            raise _error_type("invalid_context")
        binding_issuer = dict.get(authority_instance, "_binding_issuer")
        if type(binding_issuer) is not object:
            raise _error_type("invalid_context")
        return (
            service, capsule, lock, entries, permits, exchanges, completions,
            native_completions, authority, execution_local, binding_issuer,
        )
    except _error_type:
        raise
    except BaseException:
        raise _error_type("invalid_context") from None


def _classify_pristine_intent(
    capability: object,
    request_id: object,
    lease_id: object,
    lease: object,
    binding: object,
    owner_sid: object,
    recipient: object,
    selection: object,
    page_bytes: object,
    origin: object,
    _sentinel: object = _PRISTINE_INTENT_SENTINEL,
    _native_descriptor_pins: tuple[tuple[str, type, tuple[tuple[str, object], ...]], ...]
        = _PRISTINE_INTENT_NATIVE_DESCRIPTOR_PINS,
    _service_type: type = McpPrivateContextConfirmationService,
    _capsule_type: type = _NativePermitRejectionCapsule,
    _entry_type: type = _Entry,
    _permit_row_type: type = _PermitRow,
    _exchange_row_type: type = _HumanExchangeRow,
    _completion_row_type: type = _CompletionRow,
    _native_completion_row_type: type = _NativeCompletionRow,
    _lease_type: type = McpContextReadLease,
    _binding_type: type = McpContextBinding,
    _snapshot_type: type = CombinedContextSnapshot,
    _metadata_type: type = McpContextMetadataBinding,
    _authority_type: type = McpContextAuthority,
    _weakref_type: type = _NATIVE_WEAKREF_TYPE,
    _rlock_type: type = _NATIVE_RLOCK_TYPE,
    _local_type: type = _NATIVE_LOCAL_TYPE,
    _weakref_call: object = _NATIVE_WEAKREF_CALL,
    _member_get: object = _NATIVE_MEMBER_GET,
    _instance_dict_slot: object = _SERVICE_INSTANCE_DICT_SLOT,
    _instance_dict_get: object = _SERVICE_INSTANCE_DICT_GET,
    _authority_dict_slot: object = _PRISTINE_INTENT_AUTHORITY_DICT_SLOT,
    _authority_dict_get: object = _PRISTINE_INTENT_AUTHORITY_DICT_GET,
    _capsule_slots: tuple[object, ...] = _CAPSULE_SLOTS,
    _entry_slots: tuple[object, ...] = _PRISTINE_INTENT_ENTRY_SLOTS,
    _lease_authority_slot: object = _LEASE_AUTHORITY_SLOT,
    _lease_binding_slot: object = _LEASE_BINDING_SLOT,
    _lease_request_id_slot: object = _REQUEST_REJECTION_LEASE_REQUEST_ID_SLOT,
    _binding_authority_slot: object = _PRISTINE_INTENT_BINDING_AUTHORITY_SLOT,
    _binding_issuer_slot: object = _PRISTINE_INTENT_BINDING_ISSUER_SLOT,
    _binding_recipient_slots: tuple[object, ...] = _BINDING_RECIPIENT_SLOTS,
    _snapshot_metadata_slot: object = _PRISTINE_INTENT_SNAPSHOT_METADATA_SLOT,
    _permit_row_entry_ref_slot: object = _PERMIT_ROW_ENTRY_REF_SLOT,
    _human_row_request_id_slot: object = _HUMAN_ROW_REQUEST_ID_SLOT,
    _completion_row_entry_ref_slot: object = _COMPLETION_ROW_ENTRY_REF_SLOT,
    _native_completion_row_entry_ref_slot: object = _NATIVE_COMPLETION_ROW_ENTRY_REF_SLOT,
    _origin_size: int = _PRISTINE_INTENT_ORIGIN_SIZE,
    _selection_projection: object = _TRUSTED_SERVICE_SELECTION_PROJECTION,
    _snapshot_projection: object = _TRUSTED_SERVICE_SNAPSHOT_PROJECTION,
    _error_type: type = McpPrivateContextConfirmationError,
) -> object | None:
    """Identify only an untouched prepare-intent row without calling service code."""
    try:
        module_state = globals()
        for global_name, value_type, descriptor_pins in _native_descriptor_pins:
            namespace = value_type.__dict__
            if (module_state.get(global_name) is not value_type
                    or any(namespace.get(name) is not descriptor
                           for name, descriptor in descriptor_pins)):
                return None
        if type(capability) is not tuple or len(capability) != 11:
            return None
        (service, capsule, lock, entries, permits, exchanges, completions,
         native_completions, authority, execution_local, binding_issuer) = capability
        if (type(service) is not _service_type or type(capsule) is not _capsule_type
                or type(lock) is not _rlock_type or type(entries) is not dict
                or type(permits) is not dict or type(exchanges) is not dict
                or type(completions) is not dict or type(native_completions) is not dict
                or type(authority) is not _authority_type
                or type(execution_local) is not _local_type
                or type(binding_issuer) is not object):
            return None
        instance = _instance_dict_get(_instance_dict_slot, service, _service_type)
        authority_instance = _authority_dict_get(
            _authority_dict_slot, authority, _authority_type,
        )
        capsule_values = tuple(
            _member_get(slot, capsule, _capsule_type) for slot in _capsule_slots
        )
        if (type(instance) is not dict
                or type(authority_instance) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(authority_instance)))
                or dict.get(authority_instance, "_binding_issuer") is not binding_issuer
                or len(capsule_values) != 9
                or type(capsule_values[0]) is not _weakref_type
                or _weakref_call(capsule_values[0]) is not service
                or any(current is not retained for current, retained in zip(
                    capsule_values[1:], capability[2:10],
                ))
                or dict.get(instance, "_native_rejection_capsule") is not capsule
                or dict.get(instance, "_lock") is not lock
                or dict.get(instance, "_entries") is not entries
                or dict.get(instance, "_permits") is not permits
                or dict.get(instance, "_exchanges") is not exchanges
                or dict.get(instance, "_completions") is not completions
                or dict.get(instance, "_native_completions") is not native_completions
                or dict.get(instance, "_authority") is not authority
                or dict.get(instance, "_execution_local") is not execution_local):
            return None
        closed = dict.get(instance, "_closed")
        if type(closed) is not bool:
            return None
        if closed:
            raise _error_type("closed")
        if type(origin) is not tuple or len(origin) != _origin_size:
            return None
        (
            origin_entry, origin_snapshot, origin_metadata, origin_selection,
            origin_lease, origin_binding, origin_request_id, origin_lease_id,
            origin_owner_sid, origin_recipient, origin_selection_projection,
            origin_page_bytes, origin_page_digest, origin_item_rows,
            origin_target_digests, origin_metadata_row, origin_issued,
            origin_deadline, origin_mono_deadline, origin_selection_pins,
        ) = origin
        selection_types_pin = dict.get(instance, "_selection_types_pin")
        selection_states_pin = dict.get(instance, "_selection_states_pin")
        if (type(request_id) is not str or type(lease_id) is not str
                or type(lease) is not _lease_type or type(binding) is not _binding_type
                or type(owner_sid) is not str or not owner_sid
                or type(recipient) is not tuple or len(recipient) != 7
                or tuple(type(value) for value in recipient)
                    != (str, str, str, str, int, int, str)
                or type(page_bytes) is not int or isinstance(page_bytes, bool)
                or not 1 <= page_bytes <= 16_384
                or type(origin_entry) is not _entry_type
                or type(origin_snapshot) is not _snapshot_type
                or type(origin_metadata) is not _metadata_type
                or origin_selection is not selection
                or origin_lease is not lease or origin_binding is not binding
                or type(origin_request_id) is not str
                or type(origin_lease_id) is not str
                or type(origin_owner_sid) is not str or not origin_owner_sid
                or type(origin_recipient) is not tuple
                or len(origin_recipient) != 7
                or tuple(type(value) for value in origin_recipient)
                    != (str, str, str, str, int, int, str)
                or type(origin_selection_projection) is not tuple
                or not origin_selection_projection
                or type(origin_page_bytes) is not int
                or isinstance(origin_page_bytes, bool)
                or not 1 <= origin_page_bytes <= 16_384
                or type(origin_page_digest) is not str
                or len(origin_page_digest) != 64
                or any(character not in "0123456789abcdef"
                       for character in origin_page_digest)
                or type(origin_item_rows) is not tuple or not origin_item_rows
                or type(origin_target_digests) is not tuple
                or len(origin_target_digests) != len(origin_item_rows)
                or type(origin_metadata_row) is not tuple
                or len(origin_metadata_row) != len(_MCP_METADATA_FIELDS)
                or type(origin_issued) is not datetime
                or type(origin_deadline) is not datetime
                or type(origin_mono_deadline) is not float
                or type(origin_selection_pins) is not tuple
                or len(origin_selection_pins) != 2
                or type(selection_types_pin) is not tuple
                or len(selection_types_pin) != 3
                or type(selection_states_pin) is not tuple
                or len(selection_states_pin) != 3
                or origin_selection_pins[0] is not selection_types_pin
                or origin_selection_pins[1] is not selection_states_pin
                or origin_request_id != request_id
                or origin_lease_id != lease_id
                or origin_owner_sid != owner_sid
                or origin_recipient != recipient
                or origin_page_bytes != page_bytes):
            return None

        with lock:
            entry = dict.get(entries, request_id)
            if (type(entry) is not _entry_type or entry is not origin_entry
                    or dict.get(entries, request_id) is not entry):
                return None
            values = tuple(
                _member_get(slot, entry, _entry_type) for slot in _entry_slots
            )
            (
                entry_request_id, lease_ref, binding_ref, entry_owner_sid,
                entry_selection, cookie, entry_page_bytes, cookie_snapshot,
                mcp_snapshot, metadata_binding, issued, deadline, mono_deadline,
                generation, state, challenge, challenge_deadline,
                challenge_mono_deadline, receipt, ack_wall_time, ack_mono_time,
                exchange_nonce, ack_deadline, ack_mono_deadline, ack_descriptor,
                ack_attestation, ack_role_hmacs, disclosure_generation,
                disclosure_nonce, disclosure_operation_id, pending_ack_operation,
                permit_operation_nonce, permit_ref, permit_nonce, permit_deadline,
                permit_mono_deadline, page_request_sha256, intent, execution_nonce,
                execution_thread_ref, execution_thread_ident, execution_pid,
            ) = values
            if (
                type(entry_request_id) is not str
                or type(lease_ref) is not _weakref_type
                or type(binding_ref) is not _weakref_type
                or type(entry_owner_sid) is not str
                or type(cookie) is not str
                or type(entry_page_bytes) is not int
                or isinstance(entry_page_bytes, bool)
                or type(mcp_snapshot) is not _snapshot_type
                or type(metadata_binding) is not _metadata_type
                or type(issued) is not datetime
                or type(deadline) is not datetime
                or type(mono_deadline) is not float
                or type(generation) is not int
                or isinstance(generation, bool)
                or type(state) is not str
                or type(page_request_sha256) is not str
            ):
                return None
            try:
                current_selection_projection = _selection_projection(
                    selection, selection_types_pin, selection_states_pin,
                )
                current_item_rows, current_target_digests, current_metadata_row = (
                    _snapshot_projection(mcp_snapshot)
                )
            except BaseException:
                return None
            optional_values = (
                challenge, challenge_deadline, challenge_mono_deadline, receipt,
                ack_wall_time, ack_mono_time, exchange_nonce, ack_deadline,
                ack_mono_deadline, ack_descriptor, ack_attestation, ack_role_hmacs,
                disclosure_generation, disclosure_nonce, disclosure_operation_id,
                pending_ack_operation, permit_operation_nonce, permit_ref,
                permit_nonce, permit_deadline, permit_mono_deadline, execution_nonce,
                execution_thread_ref, execution_thread_ident, execution_pid,
            )
            if (entry_request_id != request_id
                    or _weakref_call(lease_ref) is not lease
                    or _weakref_call(binding_ref) is not binding
                    or entry_owner_sid != owner_sid
                    or entry_selection is not selection
                    or selection is not origin_selection
                    or cookie != ""
                    or entry_page_bytes != page_bytes
                    or cookie_snapshot is not None
                    or mcp_snapshot is not origin_snapshot
                    or metadata_binding is not origin_metadata
                    or _member_get(
                        _snapshot_metadata_slot, mcp_snapshot, _snapshot_type,
                    ) is not metadata_binding
                    or issued is not origin_issued
                    or deadline is not origin_deadline
                    or mono_deadline != origin_mono_deadline
                    or generation != 0
                    or state != "intent" or intent is not True
                    or any(value is not None for value in optional_values)
                    or len(page_request_sha256) != 64
                    or any(character not in "0123456789abcdef"
                           for character in page_request_sha256)
                    or page_request_sha256 != origin_page_digest
                    or current_selection_projection != origin_selection_projection
                    or current_item_rows != origin_item_rows
                    or current_target_digests != origin_target_digests
                    or current_metadata_row != origin_metadata_row
                    or _member_get(_lease_authority_slot, lease, _lease_type)
                        is not authority
                    or _member_get(_lease_binding_slot, lease, _lease_type)
                        is not binding
                    or _member_get(_lease_request_id_slot, lease, _lease_type)
                        != lease_id
                    or _member_get(_binding_authority_slot, binding, _binding_type)
                        is not binding_issuer
                    or _member_get(_binding_issuer_slot, binding, _binding_type)
                        is not binding_issuer
                    or tuple(_member_get(slot, binding, _binding_type)
                             for slot in _binding_recipient_slots) != recipient):
                return None
            for candidate in tuple(dict.values(permits)):
                if type(candidate) is not _permit_row_type:
                    return None
                entry_ref = _member_get(
                    _permit_row_entry_ref_slot, candidate, _permit_row_type,
                )
                if type(entry_ref) is not _weakref_type:
                    return None
                if _weakref_call(entry_ref) is entry:
                    return None
            for candidate in tuple(dict.values(exchanges)):
                if type(candidate) is not _exchange_row_type:
                    return None
                candidate_request_id = _member_get(
                    _human_row_request_id_slot, candidate, _exchange_row_type,
                )
                if type(candidate_request_id) is not str:
                    return None
                if candidate_request_id == request_id:
                    return None
            for candidates, row_type, entry_ref_slot in (
                (completions, _completion_row_type, _completion_row_entry_ref_slot),
                (
                    native_completions, _native_completion_row_type,
                    _native_completion_row_entry_ref_slot,
                ),
            ):
                for candidate in tuple(dict.values(candidates)):
                    if type(candidate) is not row_type:
                        return None
                    entry_ref = _member_get(entry_ref_slot, candidate, row_type)
                    if type(entry_ref) is not _weakref_type:
                        return None
                    if _weakref_call(entry_ref) is entry:
                        return None
            try:
                final_selection_projection = _selection_projection(
                    selection, selection_types_pin, selection_states_pin,
                )
                final_snapshot_projection = _snapshot_projection(mcp_snapshot)
            except BaseException:
                return None
            if (dict.get(entries, request_id) is not entry
                    or tuple(_member_get(slot, entry, _entry_type)
                             for slot in _entry_slots) != values
                    or final_selection_projection != origin_selection_projection
                    or final_snapshot_projection != (
                        origin_item_rows, origin_target_digests, origin_metadata_row,
                    )):
                return None
            return _sentinel
    except _error_type:
        raise
    except BaseException:
        return None


def _reject_request_binding_drift(
    capability: object,
    request_id: object,
    app_binding_valid: object,
    _service_type: type = McpPrivateContextConfirmationService,
    _capsule_type: type = _NativePermitRejectionCapsule,
    _entry_type: type = _Entry,
    _permit_row_type: type = _PermitRow,
    _exchange_row_type: type = _HumanExchangeRow,
    _completion_row_type: type = _CompletionRow,
    _native_completion_row_type: type = _NativeCompletionRow,
    _permit_type: type = Permit,
    _exchange_type: type = HumanExchange,
    _planning_receipt_type: type = _PlanningCompletionReceipt,
    _native_receipt_type: type = _NativeCompletionReceipt,
    _planning_capture_type: type = VerifiedPlanningCapture,
    _native_capture_type: type = VerifiedNativeCapture,
    _lease_type: type = McpContextReadLease,
    _authority_type: type = McpContextAuthority,
    _weakref_type: type = _NATIVE_WEAKREF_TYPE,
    _rlock_type: type = _NATIVE_RLOCK_TYPE,
    _local_type: type = _NATIVE_LOCAL_TYPE,
    _weakref_call: object = _NATIVE_WEAKREF_CALL,
    _member_get: object = _NATIVE_MEMBER_GET,
    _member_set: object = _NATIVE_MEMBER_SET,
    _instance_dict_slot: object = _SERVICE_INSTANCE_DICT_SLOT,
    _instance_dict_get: object = _SERVICE_INSTANCE_DICT_GET,
    _capsule_slots: tuple[object, ...] = _CAPSULE_SLOTS,
    _method_descriptors: tuple[tuple[str, object], ...] = _REQUEST_REJECTION_SERVICE_DESCRIPTORS,
    _permit_row_slots: tuple[object, ...] = _PERMIT_ROW_SLOTS,
    _human_row_slots: tuple[object, ...] = _REQUEST_REJECTION_HUMAN_ROW_SLOTS,
    _completion_row_slots: tuple[object, ...] = _REQUEST_REJECTION_COMPLETION_ROW_SLOTS,
    _native_completion_row_slots: tuple[object, ...] = _REQUEST_REJECTION_NATIVE_COMPLETION_ROW_SLOTS,
    _binding_recipient_slots: tuple[object, ...] = _BINDING_RECIPIENT_SLOTS,
    _binding_type: type = McpContextBinding,
    _lease_authority_slot: object = _LEASE_AUTHORITY_SLOT,
    _lease_binding_slot: object = _LEASE_BINDING_SLOT,
    _lease_request_id_slot: object = _REQUEST_REJECTION_LEASE_REQUEST_ID_SLOT,
    _planning_capture_receipt_slot: object = _REQUEST_REJECTION_PLANNING_CAPTURE_RECEIPT_SLOT,
    _native_capture_source_slot: object = _REQUEST_REJECTION_NATIVE_CAPTURE_SOURCE_SLOT,
    _native_capture_receipt_slot: object = _REQUEST_REJECTION_NATIVE_CAPTURE_RECEIPT_SLOT,
    _local_dict_get: object = _NATIVE_LOCAL_DICT_GET,
    _permit_row_entry_ref_slot: object = _PERMIT_ROW_ENTRY_REF_SLOT,
    _human_row_request_id_slot: object = _HUMAN_ROW_REQUEST_ID_SLOT,
    _completion_row_entry_ref_slot: object = _COMPLETION_ROW_ENTRY_REF_SLOT,
    _native_completion_row_entry_ref_slot: object = _NATIVE_COMPLETION_ROW_ENTRY_REF_SLOT,
    _entry_request_id_slot: object = _ENTRY_REQUEST_ID_SLOT,
    _entry_state_slot: object = _ENTRY_STATE_SLOT,
    _entry_lease_ref_slot: object = _ENTRY_LEASE_REF_SLOT,
    _entry_binding_ref_slot: object = _ENTRY_BINDING_REF_SLOT,
    _entry_permit_ref_slot: object = _ENTRY_PERMIT_REF_SLOT,
    _entry_generation_slot: object = _ENTRY_GENERATION_SLOT,
    _entry_scrub_slot_values: tuple[tuple[object, object], ...] = _ENTRY_SCRUB_SLOT_VALUES,
    _error_type: type = McpPrivateContextConfirmationError,
) -> None:
    """Reject drift without consulting replaceable service behavior."""
    if type(capability) is not tuple or len(capability) != 11:
        raise _error_type("invalid_context") from None
    (service, capsule, lock, entries, permits, exchanges, completions,
     native_completions, authority, execution_local, binding_issuer) = capability
    try:
        if type(service) is not _service_type or type(capsule) is not _capsule_type:
            raise _error_type("invalid_context")
        instance = _instance_dict_get(_instance_dict_slot, service, _service_type)
        if (type(instance) is not dict
                or any(type(key) is not str for key in tuple(dict.keys(instance)))
                or type(lock) is not _rlock_type or type(entries) is not dict
                or type(permits) is not dict or type(exchanges) is not dict
                or type(completions) is not dict or type(native_completions) is not dict
                or type(authority) is not _authority_type
                or type(execution_local) is not _local_type
                or dict.get(instance, "_native_rejection_capsule") is not capsule
                or dict.get(instance, "_lock") is not lock
                or dict.get(instance, "_entries") is not entries
                or dict.get(instance, "_permits") is not permits
                or dict.get(instance, "_exchanges") is not exchanges
                or dict.get(instance, "_completions") is not completions
                or dict.get(instance, "_native_completions") is not native_completions
                or dict.get(instance, "_authority") is not authority
                or dict.get(instance, "_execution_local") is not execution_local):
            raise _error_type("invalid_context")
        capsule_values = tuple(
            _member_get(slot, capsule, _capsule_type) for slot in _capsule_slots
        )
        if (len(capsule_values) != 9
                or type(capsule_values[0]) is not _weakref_type
                or _weakref_call(capsule_values[0]) is not service
                or any(current is not retained for current, retained in zip(
                    capsule_values[1:], capability[2:10],
                ))):
            raise _error_type("invalid_context")
    except _error_type:
        raise
    except BaseException:
        raise _error_type("invalid_context") from None

    if type(request_id) is not str:
        raise _error_type("invalid_request") from None
    try:
        local_state = _local_dict_get(execution_local, "__dict__")
    except BaseException:
        raise _error_type("invalid_context") from None
    if type(local_state) is not dict:
        raise _error_type("invalid_context") from None
    if dict.get(local_state, "active", False) is True:
        dict.__setitem__(local_state, "violation", True)
        raise _error_type("permit_invalid") from None
    if type(app_binding_valid) is not bool:
        raise _error_type("invalid_context") from None

    class_state = _service_type.__dict__
    service_binding_valid = (
        class_state.get("__getattribute__") is None
        and class_state.get("__getattr__") is None
        and all(class_state.get(name) is descriptor and name not in instance
                for name, descriptor in _method_descriptors)
    )
    if app_binding_valid and service_binding_valid:
        return

    try:
        with lock:
            entry = dict.get(entries, request_id)
            if entry is None:
                raise _error_type("invalid_binding")
            if type(entry) is not _entry_type:
                raise _error_type("invalid_binding")
            entry_request_id = _member_get(_entry_request_id_slot, entry, _entry_type)
            entry_state = _member_get(_entry_state_slot, entry, _entry_type)
            entry_lease_ref = _member_get(_entry_lease_ref_slot, entry, _entry_type)
            entry_binding_ref = _member_get(_entry_binding_ref_slot, entry, _entry_type)
            entry_permit_ref = _member_get(_entry_permit_ref_slot, entry, _entry_type)
            entry_generation = _member_get(_entry_generation_slot, entry, _entry_type)
            if (type(entry_request_id) is not str or entry_request_id != request_id
                    or type(entry_state) is not str
                    or type(entry_lease_ref) is not _weakref_type
                    or type(entry_binding_ref) is not _weakref_type
                    or entry_permit_ref is not None
                    and type(entry_permit_ref) is not _weakref_type
                    or type(entry_generation) is not int
                    or isinstance(entry_generation, bool)
                    or dict.get(entries, request_id) is not entry):
                raise _error_type("invalid_binding")

            permit_rows = []
            exchange_rows = []
            completion_rows = []
            native_completion_rows = []
            entry_records = {}
            recipient_types = (str, str, str, str, int, int, str)
            for key, candidate in tuple(dict.items(entries)):
                if type(key) is not str or type(candidate) is not _entry_type:
                    raise _error_type("invalid_binding")
                candidate_request_id = _member_get(
                    _entry_request_id_slot, candidate, _entry_type,
                )
                candidate_state = _member_get(_entry_state_slot, candidate, _entry_type)
                candidate_lease_ref = _member_get(
                    _entry_lease_ref_slot, candidate, _entry_type,
                )
                candidate_binding_ref = _member_get(
                    _entry_binding_ref_slot, candidate, _entry_type,
                )
                candidate_permit_ref = _member_get(
                    _entry_permit_ref_slot, candidate, _entry_type,
                )
                candidate_generation = _member_get(
                    _entry_generation_slot, candidate, _entry_type,
                )
                candidate_lease = (
                    _weakref_call(candidate_lease_ref)
                    if type(candidate_lease_ref) is _weakref_type else None
                )
                candidate_binding = (
                    _weakref_call(candidate_binding_ref)
                    if type(candidate_binding_ref) is _weakref_type else None
                )
                if (type(candidate_request_id) is not str or candidate_request_id != key
                        or type(candidate_state) is not str
                        or type(candidate_lease_ref) is not _weakref_type
                        or type(candidate_binding_ref) is not _weakref_type
                        or candidate_permit_ref is not None
                        and type(candidate_permit_ref) is not _weakref_type
                        or type(candidate_generation) is not int
                        or isinstance(candidate_generation, bool)
                        or candidate_lease is not None
                        and (type(candidate_lease) is not _lease_type
                             or _member_get(
                                 _lease_authority_slot, candidate_lease, _lease_type,
                             ) is not authority
                             or _member_get(
                                 _lease_binding_slot, candidate_lease, _lease_type,
                             ) is not candidate_binding
                             or type(_member_get(
                                 _lease_request_id_slot, candidate_lease, _lease_type,
                             )) is not str
                             or not _member_get(
                                 _lease_request_id_slot, candidate_lease, _lease_type,
                             ))
                        or candidate_binding is not None
                        and type(candidate_binding) is not _binding_type):
                    raise _error_type("invalid_binding")
                entry_records[key] = (
                    candidate, candidate_lease_ref, candidate_binding_ref,
                    candidate_permit_ref, candidate_generation,
                )
            if entry_records.get(request_id, (None,))[0] is not entry:
                raise _error_type("invalid_binding")

            for key, candidate in tuple(dict.items(permits)):
                if type(key) is not int or isinstance(key, bool) or type(candidate) is not _permit_row_type:
                    raise _error_type("invalid_binding")
                (permit_ref, candidate_entry_ref, row_service_ref, row_authority_ref,
                 row_lease_ref, row_binding_ref, recipient, thread_ref, thread_ident,
                 pid, candidate_request_id, generation, nonce) = tuple(
                    _member_get(slot, candidate, _permit_row_type)
                    for slot in _permit_row_slots
                )
                refs = (
                    permit_ref, candidate_entry_ref, row_service_ref, row_authority_ref,
                    row_lease_ref, row_binding_ref, thread_ref,
                )
                row_entry = _weakref_call(candidate_entry_ref) if type(candidate_entry_ref) is _weakref_type else None
                record = entry_records.get(candidate_request_id)
                row_lease = _weakref_call(row_lease_ref) if type(row_lease_ref) is _weakref_type else object()
                row_binding = _weakref_call(row_binding_ref) if type(row_binding_ref) is _weakref_type else object()
                if (any(type(ref) is not _weakref_type for ref in refs)
                        or type(candidate_request_id) is not str
                        or record is None or record[0] is not row_entry
                        or _weakref_call(row_service_ref) is not service
                        or _weakref_call(row_authority_ref) is not authority
                        or row_lease is not _weakref_call(record[1])
                        or row_binding is not _weakref_call(record[2])
                        or record[3] is not permit_ref
                        or type(generation) is not int or isinstance(generation, bool)
                        or type(nonce) is not int or isinstance(nonce, bool)
                        or type(thread_ident) is not int or isinstance(thread_ident, bool)
                        or type(pid) is not int or isinstance(pid, bool)
                        or type(recipient) is not tuple or len(recipient) != len(recipient_types)
                        or any(type(value) is not expected
                               for value, expected in zip(recipient, recipient_types))):
                    raise _error_type("invalid_binding")
                permit = _weakref_call(permit_ref)
                if permit is not None and (type(permit) is not _permit_type or id(permit) != key):
                    raise _error_type("invalid_binding")
                if row_binding is not None:
                    if (type(row_binding) is not _binding_type
                            or tuple(_member_get(slot, row_binding, _binding_type)
                                     for slot in _binding_recipient_slots) != recipient):
                        raise _error_type("invalid_binding")
                if row_entry is entry:
                    permit_rows.append((key, candidate))

            app_ref = dict.get(instance, "_app_ref")
            app_value = (_weakref_call(app_ref) if type(app_ref) is _weakref_type else None)
            if type(app_ref) is not _weakref_type or app_value is None:
                raise _error_type("invalid_binding")
            for key, candidate in tuple(dict.items(exchanges)):
                if type(key) is not int or isinstance(key, bool) or type(candidate) is not _exchange_row_type:
                    raise _error_type("invalid_binding")
                (exchange_ref, row_app_ref, row_service_ref, row_authority_ref,
                 row_lease_ref, row_binding_ref, thread_ref, candidate_request_id,
                 challenge, chat_session_id, generation, nonce, raw_user_command,
                 recipient9, thread_ident, pid) = tuple(
                    _member_get(slot, candidate, _exchange_row_type)
                    for slot in _human_row_slots
                )
                refs = (
                    exchange_ref, row_app_ref, row_service_ref, row_authority_ref,
                    row_lease_ref, row_binding_ref, thread_ref,
                )
                record = entry_records.get(candidate_request_id)
                row_lease = _weakref_call(row_lease_ref) if type(row_lease_ref) is _weakref_type else object()
                row_binding = _weakref_call(row_binding_ref) if type(row_binding_ref) is _weakref_type else object()
                if (any(type(ref) is not _weakref_type for ref in refs)
                        or type(candidate_request_id) is not str or record is None
                        or _weakref_call(row_app_ref) is not app_value
                        or _weakref_call(row_service_ref) is not service
                        or _weakref_call(row_authority_ref) is not authority
                        or row_lease is not _weakref_call(record[1])
                        or row_binding is not _weakref_call(record[2])
                        or type(challenge) is not str or type(chat_session_id) is not str
                        or type(raw_user_command) is not str or type(recipient9) is not bytes
                        or type(generation) is not int or isinstance(generation, bool)
                        or type(nonce) is not int or isinstance(nonce, bool)
                        or type(thread_ident) is not int or isinstance(thread_ident, bool)
                        or type(pid) is not int or isinstance(pid, bool)):
                    raise _error_type("invalid_binding")
                exchange = _weakref_call(exchange_ref)
                if exchange is not None and (
                    type(exchange) is not _exchange_type or id(exchange) != key
                ):
                    raise _error_type("invalid_binding")
                if row_binding is not None:
                    if (type(row_binding) is not _binding_type
                            or _member_get(
                                _binding_recipient_slots[1], row_binding, _binding_type,
                            ) != chat_session_id):
                        raise _error_type("invalid_binding")
                if record[0] is entry:
                    exchange_rows.append((key, candidate))

            for key, candidate in tuple(dict.items(completions)):
                if type(key) is not int or isinstance(key, bool) or type(candidate) is not _completion_row_type:
                    raise _error_type("invalid_binding")
                (receipt_ref, capture_ref, candidate_entry_ref, row_service_ref,
                 row_authority_ref, row_lease_ref, row_binding_ref, thread_ref,
                 candidate_request_id, generation, nonce, pid, recipient,
                 page_sha256) = tuple(
                    _member_get(slot, candidate, _completion_row_type)
                    for slot in _completion_row_slots
                )
                refs = (
                    receipt_ref, capture_ref, candidate_entry_ref, row_service_ref,
                    row_authority_ref, row_lease_ref, row_binding_ref, thread_ref,
                )
                row_entry = _weakref_call(candidate_entry_ref) if type(candidate_entry_ref) is _weakref_type else None
                record = entry_records.get(candidate_request_id)
                row_lease = _weakref_call(row_lease_ref) if type(row_lease_ref) is _weakref_type else object()
                row_binding = _weakref_call(row_binding_ref) if type(row_binding_ref) is _weakref_type else object()
                if (any(type(ref) is not _weakref_type for ref in refs)
                        or type(candidate_request_id) is not str
                        or record is None or record[0] is not row_entry
                        or _weakref_call(row_service_ref) is not service
                        or _weakref_call(row_authority_ref) is not authority
                        or row_lease is not _weakref_call(record[1])
                        or row_binding is not _weakref_call(record[2])
                        or type(generation) is not int or isinstance(generation, bool)
                        or type(nonce) is not int or isinstance(nonce, bool)
                        or type(pid) is not int or isinstance(pid, bool)
                        or type(recipient) is not tuple or len(recipient) != len(recipient_types)
                        or any(type(value) is not expected
                               for value, expected in zip(recipient, recipient_types))
                        or type(page_sha256) is not str):
                    raise _error_type("invalid_binding")
                receipt = _weakref_call(receipt_ref)
                capture = _weakref_call(capture_ref)
                if (receipt is not None
                        and (type(receipt) is not _planning_receipt_type or id(receipt) != key)
                        or capture is not None
                        and (type(capture) is not _planning_capture_type
                             or _member_get(
                                 _planning_capture_receipt_slot,
                                 capture,
                                 _planning_capture_type,
                             ) is not receipt)):
                    raise _error_type("invalid_binding")
                if row_binding is not None:
                    if (type(row_binding) is not _binding_type
                            or tuple(_member_get(slot, row_binding, _binding_type)
                                     for slot in _binding_recipient_slots) != recipient):
                        raise _error_type("invalid_binding")
                if row_entry is entry:
                    completion_rows.append((key, candidate))

            for key, candidate in tuple(dict.items(native_completions)):
                if (type(key) is not int or isinstance(key, bool)
                        or type(candidate) is not _native_completion_row_type):
                    raise _error_type("invalid_binding")
                (receipt_ref, capture_ref, candidate_entry_ref, row_service_ref,
                 row_authority_ref, row_lease_ref, row_binding_ref, thread_ref,
                 candidate_request_id, generation, nonce, pid, recipient, source,
                 page_sha256) = tuple(
                    _member_get(slot, candidate, _native_completion_row_type)
                    for slot in _native_completion_row_slots
                )
                refs = (
                    receipt_ref, capture_ref, candidate_entry_ref, row_service_ref,
                    row_authority_ref, row_lease_ref, row_binding_ref, thread_ref,
                )
                row_entry = _weakref_call(candidate_entry_ref) if type(candidate_entry_ref) is _weakref_type else None
                record = entry_records.get(candidate_request_id)
                row_lease = _weakref_call(row_lease_ref) if type(row_lease_ref) is _weakref_type else object()
                row_binding = _weakref_call(row_binding_ref) if type(row_binding_ref) is _weakref_type else object()
                if (any(type(ref) is not _weakref_type for ref in refs)
                        or type(candidate_request_id) is not str
                        or record is None or record[0] is not row_entry
                        or _weakref_call(row_service_ref) is not service
                        or _weakref_call(row_authority_ref) is not authority
                        or row_lease is not _weakref_call(record[1])
                        or row_binding is not _weakref_call(record[2])
                        or type(generation) is not int or isinstance(generation, bool)
                        or type(nonce) is not int or isinstance(nonce, bool)
                        or type(pid) is not int or isinstance(pid, bool)
                        or type(recipient) is not tuple or len(recipient) != len(recipient_types)
                        or any(type(value) is not expected
                               for value, expected in zip(recipient, recipient_types))
                        or type(source) is not str or source not in {"forge", "knowledge"}
                        or type(page_sha256) is not str):
                    raise _error_type("invalid_binding")
                receipt = _weakref_call(receipt_ref)
                capture = _weakref_call(capture_ref)
                if (receipt is not None
                        and (type(receipt) is not _native_receipt_type or id(receipt) != key)
                        or capture is not None
                        and (type(capture) is not _native_capture_type
                             or _member_get(
                                 _native_capture_receipt_slot,
                                 capture,
                                 _native_capture_type,
                             ) is not receipt
                             or _member_get(
                                 _native_capture_source_slot,
                                 capture,
                                 _native_capture_type,
                             ) != source)):
                    raise _error_type("invalid_binding")
                if row_binding is not None:
                    if (type(row_binding) is not _binding_type
                            or tuple(_member_get(slot, row_binding, _binding_type)
                                     for slot in _binding_recipient_slots) != recipient):
                        raise _error_type("invalid_binding")
                if row_entry is entry:
                    native_completion_rows.append((key, candidate))

            if (dict.get(entries, request_id) is not entry
                    or any(dict.get(permits, key) is not row for key, row in permit_rows)
                    or any(dict.get(exchanges, key) is not row for key, row in exchange_rows)
                    or any(dict.get(completions, key) is not row for key, row in completion_rows)
                    or any(dict.get(native_completions, key) is not row
                           for key, row in native_completion_rows)):
                raise _error_type("invalid_binding")
            for key, _row in permit_rows:
                dict.pop(permits, key, None)
            for key, _row in exchange_rows:
                dict.pop(exchanges, key, None)
            for key, _row in completion_rows:
                dict.pop(completions, key, None)
            for key, _row in native_completion_rows:
                dict.pop(native_completions, key, None)
            for slot, value in _entry_scrub_slot_values:
                _member_set(slot, entry, value)
            _member_set(_entry_state_slot, entry, "expired")
            _member_set(_entry_generation_slot, entry, entry_generation + 1)
            if dict.get(entries, request_id) is not entry:
                raise _error_type("invalid_binding")
            dict.pop(entries, request_id, None)
    except _error_type:
        raise
    except BaseException:
        raise _error_type("invalid_binding") from None
    raise _error_type("invalid_binding") from None


_TRUSTED_PUBLICATION_CLEANUP_ANCHOR = _publication_cleanup_anchor
_TRUSTED_TERMINATE_ANCHORED_PUBLICATION = _terminate_anchored_publication
_TRUSTED_PLANNING_EXECUTOR = McpPrivateContextConfirmationService._execute_planning_page
_TRUSTED_COMPLETION_CONSUMER = McpPrivateContextConfirmationService._consume_planning_completion
_TRUSTED_NATIVE_EXECUTOR = McpPrivateContextConfirmationService._execute_native_page
_TRUSTED_NATIVE_COMPLETION_CONSUMER = McpPrivateContextConfirmationService._consume_native_completion
_TRUSTED_ASSERT_NATIVE_BODY_PINS = _assert_native_body_pins
_TRUSTED_SELECTION_TYPES = _selection_types
_TRUSTED_SERVICE_CANON = _canon
_TRUSTED_DISCLOSURE_FENCE = McpPrivateContextConfirmationService._fence_disclosed_publication
_TRUSTED_FENCE_CLOCK = McpPrivateContextConfirmationService._clock
_TRUSTED_FENCE_GUARDS = McpPrivateContextConfirmationService._guards
_TRUSTED_FENCE_CAPTURE_COOKIE = McpPrivateContextConfirmationService._capture_cookie
_TRUSTED_FENCE_CAPTURE_MCP = McpPrivateContextConfirmationService._capture_mcp
_TRUSTED_FENCE_APP_METHOD = McpPrivateContextConfirmationService._registered_app_method
_TRUSTED_FENCE_VALID_ATTESTATION = McpPrivateContextConfirmationService._valid_attestation
_TRUSTED_PAGE_REQUEST_DESCRIPTOR_SLOT = (
    McpPrivateContextConfirmationService.__dict__["_page_request_descriptor"]
)
_TRUSTED_PAGE_REQUEST_DESCRIPTOR = McpPrivateContextConfirmationService._page_request_descriptor
_TRUSTED_SERVICE_ASSERT_PINS = McpPrivateContextConfirmationService._assert_pins
_TRUSTED_TERMINATE = McpPrivateContextConfirmationService._terminate
_TRUSTED_ISSUE_CAPTURED_PERMIT = McpPrivateContextConfirmationService._issue_captured_permit
