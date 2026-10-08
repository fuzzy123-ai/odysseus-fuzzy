"""Inactive mixed-source MCP context pack publication.

The coordinator borrows an already configured confirmation service, manifest
producer, and cursor authority.  It registers no route and grants no authority.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import threading
import types

import src.mcp_context_contract as _contract_module
import src.mcp_context_native_page as _native_module
import src.mcp_context_planning_page as _planning_module
from src.context_manifest_producer import CombinedContextManifestProducer, CombinedContextSnapshot
from src.context_metadata_binding import McpContextMetadataBinding
from src.mcp_context_authority import McpContextReadLease
from src.mcp_context_contract import (
    MCP_CONTEXT_MANIFEST_ITEMS_MAX,
    MCP_CONTEXT_PACK_SCHEMA,
    MCP_CONTEXT_RAW_PAGE_MAX_BYTES,
    MCP_CONTEXT_RESULT_MAX_BYTES,
    McpContextCursorEnvelope,
    encode_context_jsonrpc_response,
)
from src.mcp_context_cursor_authority import McpContextCursorAuthority
from src.mcp_private_context_confirmation import McpPrivateContextConfirmationService
from src.private_context_confirmation import ManifestItem
from src.private_context_confirmation_app import (
    ContextSelection,
    ForgeSelectionLocator,
    SourceSelection,
)


MCP_CONTEXT_PACK_ERROR = "context_pack_unavailable"
_MAX_JSON_INT = (1 << 53) - 1
_RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$\Z")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$\Z")

_TRUSTED_SERVICE_CLASS = McpPrivateContextConfirmationService
_TRUSTED_PRODUCER_CLASS = CombinedContextManifestProducer
_TRUSTED_CURSOR_CLASS = McpContextCursorAuthority
_TRUSTED_LEASE_CLASS = McpContextReadLease
_TRUSTED_SELECTION_CLASS = ContextSelection
_TRUSTED_SOURCE_CLASS = SourceSelection
_TRUSTED_LOCATOR_CLASS = ForgeSelectionLocator
_TRUSTED_SNAPSHOT_CLASS = CombinedContextSnapshot
_TRUSTED_ITEM_CLASS = ManifestItem
_TRUSTED_METADATA_CLASS = McpContextMetadataBinding

_TRUSTED_CAPTURE = CombinedContextManifestProducer.capture_mcp_snapshot
_TRUSTED_SERVICE_ASSERT_PINS = McpPrivateContextConfirmationService._assert_pins
_TRUSTED_ISSUE_PERMIT = McpPrivateContextConfirmationService.issue_permit
_TRUSTED_FENCE = McpPrivateContextConfirmationService._fence_disclosed_publication
_TRUSTED_REVOKE = McpPrivateContextConfirmationService.revoke
_TRUSTED_ISSUE_MANIFEST = McpContextCursorAuthority.issue_manifest_cursor
_TRUSTED_ISSUE_CONTENT = McpContextCursorAuthority.issue_content_cursor
_TRUSTED_VERIFY_MANIFEST = McpContextCursorAuthority.verify_manifest_cursor
_TRUSTED_VERIFY_CONTENT = McpContextCursorAuthority.verify_content_cursor
_TRUSTED_PUBLISH_FORGE = _native_module.publish_forge_page
_TRUSTED_PUBLISH_KNOWLEDGE = _native_module.publish_knowledge_page
_TRUSTED_PUBLISH_PLANNING = _planning_module.publish_planning_page
_TRUSTED_ENCODER = encode_context_jsonrpc_response
_TRUSTED_JSON_DUMPS = json.dumps
_TRUSTED_JSON_LOADS = json.loads
_TRUSTED_B64ENCODE = base64.b64encode
_TRUSTED_B64DECODE = base64.b64decode
_TRUSTED_SHA256 = hashlib.sha256
_TRUSTED_CURRENT_THREAD = threading.current_thread
_TRUSTED_GET_IDENT = threading.get_ident
_TRUSTED_GETPID = os.getpid
_TRUSTED_RLOCK_FACTORY = threading.RLock
_TRUSTED_RLOCK_TYPE = type(threading.RLock())
_TRUSTED_MANIFEST_ITEMS_MAX = MCP_CONTEXT_MANIFEST_ITEMS_MAX
_TRUSTED_PACK_SCHEMA = MCP_CONTEXT_PACK_SCHEMA
_TRUSTED_RAW_PAGE_MAX_BYTES = MCP_CONTEXT_RAW_PAGE_MAX_BYTES
_TRUSTED_RESULT_MAX_BYTES = MCP_CONTEXT_RESULT_MAX_BYTES
_TRUSTED_FORGE_SCHEMA = _native_module.MCP_FORGE_PAGE_SCHEMA
_TRUSTED_KNOWLEDGE_SCHEMA = _native_module.MCP_KNOWLEDGE_PAGE_SCHEMA
_TRUSTED_PLANNING_SCHEMA = _planning_module.MCP_PLANNING_PAGE_SCHEMA

_SELECTION_FIELDS = ("items",)
_SOURCE_FIELDS = ("source", "selection_ref", "revision_ref", "forge_locator")
_LOCATOR_FIELDS = ("repo_id", "version_id", "path")
_SNAPSHOT_FIELDS = ("items", "target_manifest_digests", "mcp_metadata_binding")
_ITEM_FIELDS = (
    "source", "selection_ref", "revision_ref", "boundary_sha256", "record_sha256",
    "payload_sha256", "classification", "provenance_ref",
)
_METADATA_FIELDS = (
    "schema", "audience", "owner_sid", "chat_session_id", "recipient_token_id", "scope",
    "cache_generation", "security_revision", "app_epoch",
)
_SERVICE_DEPENDENCY_FIELDS = ("_authority", "_producer")
_PRODUCER_DEPENDENCY_FIELDS = ("_mcp_authority",)
_CURSOR_DEPENDENCY_FIELDS = ("_authority",)
_LEASE_FIELDS = ("binding",)
_CURSOR_ENVELOPE_FIELDS = (
    "request_id", "pack_id", "recipient", "chat", "process_epoch", "selection",
    "snapshot", "kind", "target_index", "offset", "page_bytes", "expiry", "schema",
)


class McpContextPackCoordinatorError(ValueError):
    """One fixed, content-free coordinator failure."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = MCP_CONTEXT_PACK_ERROR
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpContextPackCoordinatorError(code={self.code!r})"


def _fail() -> None:
    raise McpContextPackCoordinatorError() from None


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
        _fail()
    mro = _type_getattribute(value_class, "__mro__")
    if type(mro) is not tuple or not mro:
        _fail()
    for base in mro:
        if type(base) is not type:
            _fail()
        namespace = _type_getattribute(base, "__dict__")
        if name in namespace:
            raw = namespace[name]
            if type(raw) is _staticmethod_type:
                return _staticmethod_get(raw, None, value_class)
            return raw
    _fail()


_TRUSTED_RAW_EFFECTIVE_SPECIAL = _raw_effective_special


def _class_state(
    value_class: type,
    fields: tuple[str, ...],
    require_member_fields: bool = False,
    _type_getattribute: object = type.__getattribute__,
    _effective: object = _TRUSTED_RAW_EFFECTIVE_SPECIAL,
    _member_descriptor_type: type = types.MemberDescriptorType,
) -> tuple[object, ...]:
    namespace = _type_getattribute(value_class, "__dict__")
    field_slots = tuple(namespace.get(name) for name in fields)
    if (type(require_member_fields) is not bool
            or (require_member_fields
                and any(type(slot) is not _member_descriptor_type for slot in field_slots))):
        _fail()
    return (
        namespace.get("__init__"), namespace.get("__new__"),
        _effective(value_class, "__new__"),
        namespace.get("__getattribute__"),
        _effective(value_class, "__getattribute__"),
        namespace.get("__post_init__"), namespace.get("__setattr__"),
        field_slots,
    )


_TRUSTED_CLASS_STATE = _class_state
_TRUSTED_SERVICE_STATE = _class_state(_TRUSTED_SERVICE_CLASS, _SERVICE_DEPENDENCY_FIELDS)
_TRUSTED_PRODUCER_STATE = _class_state(_TRUSTED_PRODUCER_CLASS, _PRODUCER_DEPENDENCY_FIELDS)
_TRUSTED_CURSOR_STATE = _class_state(_TRUSTED_CURSOR_CLASS, _CURSOR_DEPENDENCY_FIELDS)
_TRUSTED_LEASE_STATE = _class_state(_TRUSTED_LEASE_CLASS, _LEASE_FIELDS)
_TRUSTED_SELECTION_STATE = _class_state(_TRUSTED_SELECTION_CLASS, _SELECTION_FIELDS, True)
_TRUSTED_SOURCE_STATE = _class_state(_TRUSTED_SOURCE_CLASS, _SOURCE_FIELDS, True)
_TRUSTED_LOCATOR_STATE = _class_state(_TRUSTED_LOCATOR_CLASS, _LOCATOR_FIELDS, True)
_TRUSTED_SNAPSHOT_STATE = _class_state(_TRUSTED_SNAPSHOT_CLASS, _SNAPSHOT_FIELDS, True)
_TRUSTED_ITEM_STATE = _class_state(_TRUSTED_ITEM_CLASS, _ITEM_FIELDS, True)
_TRUSTED_METADATA_STATE = _class_state(_TRUSTED_METADATA_CLASS, _METADATA_FIELDS, True)
_TRUSTED_CURSOR_ENVELOPE_CLASS = McpContextCursorEnvelope
_TRUSTED_CURSOR_ENVELOPE_STATE = _class_state(
    _TRUSTED_CURSOR_ENVELOPE_CLASS, _CURSOR_ENVELOPE_FIELDS, True,
)


def _class_matches(value_class: type, fields: tuple[str, ...], expected: tuple[object, ...]) -> bool:
    current = _TRUSTED_CLASS_STATE(value_class, fields)
    return (
        all(value is pinned for value, pinned in zip(current[:-1], expected[:-1]))
        and all(value is pinned for value, pinned in zip(current[-1], expected[-1]))
    )


_TRUSTED_CLASS_MATCHES = _class_matches


def _slots(value: object, expected_type: type, state: tuple[object, ...]) -> tuple[object, ...]:
    if type(value) is not expected_type:
        _fail()
    try:
        return tuple(type(slot).__get__(slot, value, expected_type) for slot in state[-1])
    except BaseException:
        _fail()


_TRUSTED_SLOTS = _slots


def _safe_opaque(value: object) -> str:
    if (type(value) is not str or not value or len(value) > 256 or not value.isascii()
            or any(ord(character) < 0x21 or ord(character) > 0x7e for character in value)):
        _fail()
    return value


_TRUSTED_SAFE_OPAQUE = _safe_opaque


def _raw_digest(value: object) -> str:
    if type(value) is not str or _RAW_SHA256.fullmatch(value) is None:
        _fail()
    return value


_TRUSTED_RAW_DIGEST = _raw_digest


def _prefixed_digest(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail()
    return value


_TRUSTED_PREFIXED_DIGEST = _prefixed_digest


def _selection_projection(value: object) -> tuple[tuple[object, ...], ...]:
    (items,) = _TRUSTED_SLOTS(value, _TRUSTED_SELECTION_CLASS, _TRUSTED_SELECTION_STATE)
    if type(items) is not tuple or not 1 <= len(items) <= 300:
        _fail()
    projected: list[tuple[object, ...]] = []
    keys: set[tuple[str, str, str]] = set()
    for item in items:
        source, selection_ref, revision_ref, locator = _TRUSTED_SLOTS(
            item, _TRUSTED_SOURCE_CLASS, _TRUSTED_SOURCE_STATE,
        )
        if type(source) is not str or source not in {"forge", "planning", "knowledge"}:
            _fail()
        selection_ref = _TRUSTED_SAFE_OPAQUE(selection_ref)
        revision_ref = _TRUSTED_SAFE_OPAQUE(revision_ref)
        key = (source, selection_ref, revision_ref)
        if key in keys:
            _fail()
        keys.add(key)
        if source == "forge":
            repo_id, version_id, path = _TRUSTED_SLOTS(
                locator, _TRUSTED_LOCATOR_CLASS, _TRUSTED_LOCATOR_STATE,
            )
            repo_id = _TRUSTED_SAFE_OPAQUE(repo_id)
            version_id = _TRUSTED_SAFE_OPAQUE(version_id)
            if (type(path) is not str or not path or len(path) > 1024 or not path.isascii()
                    or path.startswith(("/", "~")) or "\\" in path
                    or any(part in {"", ".", ".."} for part in path.split("/"))):
                _fail()
            locator_value: object = (repo_id, version_id, path)
        else:
            if locator is not None:
                _fail()
            locator_value = None
        projected.append((source, selection_ref, revision_ref, locator_value))
    return tuple(projected)


def _snapshot_projection(value: object) -> tuple[object, ...]:
    items, targets, metadata = _TRUSTED_SLOTS(
        value, _TRUSTED_SNAPSHOT_CLASS, _TRUSTED_SNAPSHOT_STATE,
    )
    if (type(items) is not tuple or not 1 <= len(items) <= 300
            or type(targets) is not tuple or len(targets) != len(items)
            or type(metadata) is not _TRUSTED_METADATA_CLASS):
        _fail()
    item_rows: list[tuple[str, ...]] = []
    keys: set[tuple[str, str, str]] = set()
    for item in items:
        row = _TRUSTED_SLOTS(item, _TRUSTED_ITEM_CLASS, _TRUSTED_ITEM_STATE)
        if any(type(value) is not str for value in row):
            _fail()
        source, selection_ref, revision_ref, boundary, record, payload, classification, provenance = row
        if (source not in {"forge", "planning", "knowledge"}
                or classification not in {"public", "private"}):
            _fail()
        selection_ref = _TRUSTED_SAFE_OPAQUE(selection_ref)
        revision_ref = _TRUSTED_SAFE_OPAQUE(revision_ref)
        provenance = _TRUSTED_SAFE_OPAQUE(provenance)
        boundary = _TRUSTED_RAW_DIGEST(boundary)
        record = _TRUSTED_RAW_DIGEST(record)
        payload = _TRUSTED_RAW_DIGEST(payload)
        key = (source, selection_ref, revision_ref)
        if key in keys:
            _fail()
        keys.add(key)
        item_rows.append((source, selection_ref, revision_ref, boundary, record, payload,
                          classification, provenance))
    target_rows = tuple(_TRUSTED_RAW_DIGEST(value) for value in targets)
    metadata_row = _TRUSTED_SLOTS(metadata, _TRUSTED_METADATA_CLASS, _TRUSTED_METADATA_STATE)
    if (any(type(value) is not str for value in (*metadata_row[:6], metadata_row[8]))
            or any(type(value) is not int or isinstance(value, bool) or value < 0
                   for value in metadata_row[6:8])
            or metadata_row[0] != "odysseus.context.mcp_metadata_binding.v1"
            or metadata_row[1] != "authenticated_mcp_context_read"
            or metadata_row[5] != "mcp:harbour:context:read"):
        _fail()
    for value in (*metadata_row[:6], metadata_row[8]):
        if not value or len(value) > 512 or not value.isascii():
            _fail()
    return tuple(item_rows), target_rows, metadata_row


_TRUSTED_SELECTION_PROJECTION = _selection_projection
_TRUSTED_SNAPSHOT_PROJECTION = _snapshot_projection


def _canonical(value: object) -> bytes:
    try:
        encoded = _TRUSTED_JSON_DUMPS(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except BaseException:
        _fail()
    if type(encoded) is not bytes or len(encoded) > _TRUSTED_RESULT_MAX_BYTES:
        _fail()
    return encoded


_TRUSTED_CANONICAL = _canonical


def _domain_digest(domain: bytes, value: object) -> str:
    if type(domain) is not bytes or not domain:
        _fail()
    body = _TRUSTED_CANONICAL(value)
    frame = len(domain).to_bytes(4, "big") + domain + len(body).to_bytes(4, "big") + body
    return "sha256:" + _TRUSTED_SHA256(frame).hexdigest()


_TRUSTED_DOMAIN_DIGEST = _domain_digest


def _identity(
    consent_request_id: str,
    selection_projection: tuple[tuple[object, ...], ...],
    snapshot_projection: tuple[object, ...],
) -> tuple[str, str, str]:
    selection_digest = _TRUSTED_DOMAIN_DIGEST(
        b"odysseus.mcp.context.pack.selection.v1",
        {"schema": _TRUSTED_PACK_SCHEMA, "selection": selection_projection},
    )
    snapshot_digest = _TRUSTED_DOMAIN_DIGEST(
        b"odysseus.mcp.context.pack.snapshot.v1",
        {
            "schema": _TRUSTED_PACK_SCHEMA,
            "items": snapshot_projection[0],
            "target_manifest_digests": snapshot_projection[1],
            "binding": snapshot_projection[2],
        },
    )
    pack_hex = _TRUSTED_DOMAIN_DIGEST(
        b"odysseus.mcp.context.pack.id.v1",
        {
            "schema": _TRUSTED_PACK_SCHEMA,
            "request_id": consent_request_id,
            "selection": selection_digest,
            "snapshot": snapshot_digest,
        },
    )[7:]
    return "pack_" + pack_hex, selection_digest, snapshot_digest


_TRUSTED_IDENTITY = _identity


def _ordered_items(
    selection_projection: tuple[tuple[object, ...], ...],
    snapshot_projection: tuple[object, ...],
) -> tuple[dict[str, str], ...]:
    rows = snapshot_projection[0]
    by_key = {row[:3]: row for row in rows}
    selected_keys = tuple(row[:3] for row in selection_projection)
    if (len(by_key) != len(rows) or len(selected_keys) != len(rows)
            or set(by_key) != set(selected_keys)
            or len(snapshot_projection[1]) != len(selected_keys)):
        _fail()
    ordered = []
    for key in selected_keys:
        row = by_key[key]
        ordered.append({
            "source": row[0], "selection_ref": row[1], "revision_ref": row[2],
            "boundary_sha256": row[3], "record_sha256": row[4],
            "payload_sha256": row[5], "classification": row[6],
            "provenance_ref": row[7],
        })
    return tuple(ordered)


_TRUSTED_ORDERED_ITEMS = _ordered_items


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail()
        result[key] = value
    return result


_TRUSTED_UNIQUE_PAIRS = _unique_pairs


def _check_json_tree(value: object) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 4096 or depth > 16:
            _fail()
        kind = type(current)
        if current is None or kind in {str, bool, int, float}:
            continue
        if kind is list:
            stack.extend((child, depth + 1) for child in current)
            continue
        if kind is dict:
            for key, child in current.items():
                if type(key) is not str:
                    _fail()
                nodes += 1
                if nodes > 4096:
                    _fail()
                stack.append((child, depth + 1))
            continue
        _fail()


_TRUSTED_CHECK_JSON_TREE = _check_json_tree


def _parse_inner(
    raw: object,
    *,
    source: str,
    rpc_request_id: str | int,
    target_index: int,
    offset: int,
    expected_item: dict[str, str],
    page_bytes: int,
) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > _TRUSTED_RESULT_MAX_BYTES:
        _fail()
    try:
        parsed = _TRUSTED_JSON_LOADS(
            raw.decode("utf-8"), object_pairs_hook=_TRUSTED_UNIQUE_PAIRS,
            parse_constant=lambda _value: _fail(),
        )
    except McpContextPackCoordinatorError:
        raise
    except BaseException:
        _fail()
    _TRUSTED_CHECK_JSON_TREE(parsed)
    if (type(parsed) is not dict or set(parsed) != {"jsonrpc", "id", "result"}
            or type(parsed["jsonrpc"]) is not str or parsed["jsonrpc"] != "2.0"
            or type(parsed["id"]) is not type(rpc_request_id)
            or parsed["id"] != rpc_request_id or type(parsed["result"]) is not dict):
        _fail()
    result = parsed["result"]
    fields = {
        "schema", "target_index", "offset", "end", "total_bytes", "whole_sha256",
        "page_sha256", "manifest_digest", "byteform", "content_b64",
    }
    if set(result) != fields:
        _fail()
    schemas = {
        "forge": _TRUSTED_FORGE_SCHEMA,
        "planning": _TRUSTED_PLANNING_SCHEMA,
        "knowledge": _TRUSTED_KNOWLEDGE_SCHEMA,
    }
    byteforms = {
        "forge": "git_blob", "planning": "durable_revision_document",
        "knowledge": "knowledge_payload",
    }
    if (type(result["schema"]) is not str or result["schema"] != schemas[source]
            or type(result["target_index"]) is not int or result["target_index"] != target_index
            or type(result["offset"]) is not int or result["offset"] != offset
            or type(result["end"]) is not int or type(result["total_bytes"]) is not int
            or not 0 <= offset <= result["end"] <= result["total_bytes"] <= _MAX_JSON_INT
            or result["end"] - offset > page_bytes
            or type(result["byteform"]) is not str or result["byteform"] != byteforms[source]
            or _TRUSTED_PREFIXED_DIGEST(result["whole_sha256"])
                != "sha256:" + expected_item["payload_sha256"]
            or _TRUSTED_PREFIXED_DIGEST(result["page_sha256"]) != result["page_sha256"]):
        _fail()
    if source == "knowledge":
        _TRUSTED_PREFIXED_DIGEST(result["manifest_digest"])
    else:
        _TRUSTED_RAW_DIGEST(result["manifest_digest"])
    content_b64 = result["content_b64"]
    if type(content_b64) is not str or not content_b64.isascii():
        _fail()
    try:
        content = _TRUSTED_B64DECODE(content_b64, validate=True)
        canonical_b64 = _TRUSTED_B64ENCODE(content).decode("ascii")
    except (binascii.Error, UnicodeError, ValueError, TypeError):
        _fail()
    except BaseException:
        _fail()
    if (type(content) is not bytes or canonical_b64 != content_b64
            or len(content) != result["end"] - offset
            or len(content) > _TRUSTED_RAW_PAGE_MAX_BYTES
            or result["page_sha256"] != "sha256:" + _TRUSTED_SHA256(content).hexdigest()):
        _fail()
    if _TRUSTED_ENCODER(rpc_request_id, result) != raw:
        _fail()
    return {
        "target_index": target_index,
        "offset": offset,
        "end": result["end"],
        "total": result["total_bytes"],
        "whole_sha256": result["whole_sha256"],
        "page_sha256": result["page_sha256"],
        "byteform": result["byteform"],
        "content_b64": content_b64,
    }


_TRUSTED_PARSE_INNER = _parse_inner


def _static_pin_guard() -> None:
    service_namespace = _TRUSTED_SERVICE_CLASS.__dict__
    producer_namespace = _TRUSTED_PRODUCER_CLASS.__dict__
    cursor_namespace = _TRUSTED_CURSOR_CLASS.__dict__
    coordinator_namespace = _TRUSTED_COORDINATOR_CLASS.__dict__
    class_pins = (
        (_TRUSTED_SERVICE_CLASS, _SERVICE_DEPENDENCY_FIELDS, _TRUSTED_SERVICE_STATE),
        (_TRUSTED_PRODUCER_CLASS, _PRODUCER_DEPENDENCY_FIELDS, _TRUSTED_PRODUCER_STATE),
        (_TRUSTED_CURSOR_CLASS, _CURSOR_DEPENDENCY_FIELDS, _TRUSTED_CURSOR_STATE),
        (_TRUSTED_LEASE_CLASS, _LEASE_FIELDS, _TRUSTED_LEASE_STATE),
        (_TRUSTED_SELECTION_CLASS, _SELECTION_FIELDS, _TRUSTED_SELECTION_STATE),
        (_TRUSTED_SOURCE_CLASS, _SOURCE_FIELDS, _TRUSTED_SOURCE_STATE),
        (_TRUSTED_LOCATOR_CLASS, _LOCATOR_FIELDS, _TRUSTED_LOCATOR_STATE),
        (_TRUSTED_SNAPSHOT_CLASS, _SNAPSHOT_FIELDS, _TRUSTED_SNAPSHOT_STATE),
        (_TRUSTED_ITEM_CLASS, _ITEM_FIELDS, _TRUSTED_ITEM_STATE),
        (_TRUSTED_METADATA_CLASS, _METADATA_FIELDS, _TRUSTED_METADATA_STATE),
        (_TRUSTED_CURSOR_ENVELOPE_CLASS, _CURSOR_ENVELOPE_FIELDS,
         _TRUSTED_CURSOR_ENVELOPE_STATE),
    )
    if (globals().get("_static_pin_guard") is not _TRUSTED_STATIC_PIN_GUARD
            or globals().get("McpPrivateContextConfirmationService") is not _TRUSTED_SERVICE_CLASS
            or globals().get("CombinedContextManifestProducer") is not _TRUSTED_PRODUCER_CLASS
            or globals().get("McpContextCursorAuthority") is not _TRUSTED_CURSOR_CLASS
            or globals().get("McpContextReadLease") is not _TRUSTED_LEASE_CLASS
            or globals().get("ContextSelection") is not _TRUSTED_SELECTION_CLASS
            or globals().get("SourceSelection") is not _TRUSTED_SOURCE_CLASS
            or globals().get("ForgeSelectionLocator") is not _TRUSTED_LOCATOR_CLASS
            or globals().get("CombinedContextSnapshot") is not _TRUSTED_SNAPSHOT_CLASS
            or globals().get("ManifestItem") is not _TRUSTED_ITEM_CLASS
            or globals().get("McpContextMetadataBinding") is not _TRUSTED_METADATA_CLASS
            or globals().get("McpContextCursorEnvelope") is not _TRUSTED_CURSOR_ENVELOPE_CLASS
            or any(not _TRUSTED_CLASS_MATCHES(value_class, fields, state)
                   for value_class, fields, state in class_pins)
            or not _TRUSTED_CLASS_MATCHES(
                _TRUSTED_COORDINATOR_CLASS, _TRUSTED_COORDINATOR_SLOT_NAMES,
                _TRUSTED_COORDINATOR_STATE,
            )
            or producer_namespace.get("capture_mcp_snapshot") is not _TRUSTED_CAPTURE
            or service_namespace.get("_assert_pins") is not _TRUSTED_SERVICE_ASSERT_PINS
            or service_namespace.get("issue_permit") is not _TRUSTED_ISSUE_PERMIT
            or service_namespace.get("_fence_disclosed_publication") is not _TRUSTED_FENCE
            or service_namespace.get("revoke") is not _TRUSTED_REVOKE
            or cursor_namespace.get("issue_manifest_cursor") is not _TRUSTED_ISSUE_MANIFEST
            or cursor_namespace.get("issue_content_cursor") is not _TRUSTED_ISSUE_CONTENT
            or cursor_namespace.get("verify_manifest_cursor") is not _TRUSTED_VERIFY_MANIFEST
            or cursor_namespace.get("verify_content_cursor") is not _TRUSTED_VERIFY_CONTENT
            or any(coordinator_namespace.get(name) is not descriptor
                   for name, descriptor in _TRUSTED_COORDINATOR_NAMESPACE)
            or threading.RLock is not _TRUSTED_RLOCK_FACTORY):
        _fail()


_TRUSTED_STATIC_PIN_GUARD = _static_pin_guard


class McpContextPackCoordinator:
    """Publish detached manifest and content pages for one retained consent."""

    __slots__ = (
        "_service", "_producer", "_cursor", "_authority", "_lock", "_closed",
        "_generation", "_active",
    )

    def __init__(
        self,
        service: McpPrivateContextConfirmationService,
        producer: CombinedContextManifestProducer,
        cursor_authority: McpContextCursorAuthority,
    ) -> None:
        _TRUSTED_STATIC_PIN_GUARD()
        if (type(service) is not _TRUSTED_SERVICE_CLASS
                or type(producer) is not _TRUSTED_PRODUCER_CLASS
                or type(cursor_authority) is not _TRUSTED_CURSOR_CLASS):
            _fail()
        try:
            _TRUSTED_SERVICE_ASSERT_PINS(service)
            authority = service._authority
            if (service._producer is not producer or producer._mcp_authority is not authority
                    or cursor_authority._authority is not authority):
                _fail()
        except McpContextPackCoordinatorError:
            raise
        except BaseException:
            _fail()
        self._service = service
        self._producer = producer
        self._cursor = cursor_authority
        self._authority = authority
        self._lock = _TRUSTED_RLOCK_FACTORY()
        self._closed = False
        self._generation = 0
        self._active: dict[str, tuple[object, int, int, int]] = {}
        _TRUSTED_COORDINATOR_ASSERT_PINS(self)

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("context pack coordinator cannot be subclassed")

    def __repr__(self) -> str:
        return "McpContextPackCoordinator(<redacted>)"

    def _assert_pins(self) -> None:
        _TRUSTED_STATIC_PIN_GUARD()
        service_namespace = _TRUSTED_SERVICE_CLASS.__dict__
        producer_namespace = _TRUSTED_PRODUCER_CLASS.__dict__
        cursor_namespace = _TRUSTED_CURSOR_CLASS.__dict__
        coordinator_namespace = _TRUSTED_COORDINATOR_CLASS.__dict__
        if (type(self) is not _TRUSTED_COORDINATOR_CLASS
                or any(coordinator_namespace.get(name) is not descriptor
                       for name, descriptor in _TRUSTED_COORDINATOR_NAMESPACE)
                or globals().get("McpPrivateContextConfirmationService") is not _TRUSTED_SERVICE_CLASS
                or globals().get("CombinedContextManifestProducer") is not _TRUSTED_PRODUCER_CLASS
                or globals().get("McpContextCursorAuthority") is not _TRUSTED_CURSOR_CLASS
                or globals().get("McpContextReadLease") is not _TRUSTED_LEASE_CLASS
                or globals().get("ContextSelection") is not _TRUSTED_SELECTION_CLASS
                or globals().get("SourceSelection") is not _TRUSTED_SOURCE_CLASS
                or globals().get("ForgeSelectionLocator") is not _TRUSTED_LOCATOR_CLASS
                or globals().get("CombinedContextSnapshot") is not _TRUSTED_SNAPSHOT_CLASS
                or globals().get("ManifestItem") is not _TRUSTED_ITEM_CLASS
                or globals().get("McpContextMetadataBinding") is not _TRUSTED_METADATA_CLASS
                or producer_namespace.get("capture_mcp_snapshot") is not _TRUSTED_CAPTURE
                or service_namespace.get("_assert_pins") is not _TRUSTED_SERVICE_ASSERT_PINS
                or service_namespace.get("issue_permit") is not _TRUSTED_ISSUE_PERMIT
                or service_namespace.get("_fence_disclosed_publication") is not _TRUSTED_FENCE
                or service_namespace.get("revoke") is not _TRUSTED_REVOKE
                or cursor_namespace.get("issue_manifest_cursor") is not _TRUSTED_ISSUE_MANIFEST
                or cursor_namespace.get("issue_content_cursor") is not _TRUSTED_ISSUE_CONTENT
                or cursor_namespace.get("verify_manifest_cursor") is not _TRUSTED_VERIFY_MANIFEST
                or cursor_namespace.get("verify_content_cursor") is not _TRUSTED_VERIFY_CONTENT
                or _native_module.publish_forge_page is not _TRUSTED_PUBLISH_FORGE
                or _native_module.publish_knowledge_page is not _TRUSTED_PUBLISH_KNOWLEDGE
                or _planning_module.publish_planning_page is not _TRUSTED_PUBLISH_PLANNING
                or _contract_module.encode_context_jsonrpc_response is not _TRUSTED_ENCODER
                or globals().get("encode_context_jsonrpc_response") is not _TRUSTED_ENCODER
                or globals().get("_class_state") is not _TRUSTED_CLASS_STATE
                or globals().get("_raw_effective_special") is not _TRUSTED_RAW_EFFECTIVE_SPECIAL
                or globals().get("_class_matches") is not _TRUSTED_CLASS_MATCHES
                or globals().get("_slots") is not _TRUSTED_SLOTS
                or globals().get("_selection_projection") is not _TRUSTED_SELECTION_PROJECTION
                or globals().get("_snapshot_projection") is not _TRUSTED_SNAPSHOT_PROJECTION
                or globals().get("_canonical") is not _TRUSTED_CANONICAL
                or globals().get("_safe_opaque") is not _TRUSTED_SAFE_OPAQUE
                or globals().get("_raw_digest") is not _TRUSTED_RAW_DIGEST
                or globals().get("_prefixed_digest") is not _TRUSTED_PREFIXED_DIGEST
                or globals().get("_domain_digest") is not _TRUSTED_DOMAIN_DIGEST
                or globals().get("_identity") is not _TRUSTED_IDENTITY
                or globals().get("_ordered_items") is not _TRUSTED_ORDERED_ITEMS
                or globals().get("_unique_pairs") is not _TRUSTED_UNIQUE_PAIRS
                or globals().get("_check_json_tree") is not _TRUSTED_CHECK_JSON_TREE
                or globals().get("_parse_inner") is not _TRUSTED_PARSE_INNER
                or MCP_CONTEXT_MANIFEST_ITEMS_MAX != _TRUSTED_MANIFEST_ITEMS_MAX
                or MCP_CONTEXT_PACK_SCHEMA != _TRUSTED_PACK_SCHEMA
                or MCP_CONTEXT_RAW_PAGE_MAX_BYTES != _TRUSTED_RAW_PAGE_MAX_BYTES
                or MCP_CONTEXT_RESULT_MAX_BYTES != _TRUSTED_RESULT_MAX_BYTES
                or _native_module.MCP_FORGE_PAGE_SCHEMA != _TRUSTED_FORGE_SCHEMA
                or _native_module.MCP_KNOWLEDGE_PAGE_SCHEMA != _TRUSTED_KNOWLEDGE_SCHEMA
                or _planning_module.MCP_PLANNING_PAGE_SCHEMA != _TRUSTED_PLANNING_SCHEMA
                or json.dumps is not _TRUSTED_JSON_DUMPS or json.loads is not _TRUSTED_JSON_LOADS
                or base64.b64encode is not _TRUSTED_B64ENCODE
                or base64.b64decode is not _TRUSTED_B64DECODE
                or hashlib.sha256 is not _TRUSTED_SHA256
                or threading.current_thread is not _TRUSTED_CURRENT_THREAD
                or threading.get_ident is not _TRUSTED_GET_IDENT or os.getpid is not _TRUSTED_GETPID
                or threading.RLock is not _TRUSTED_RLOCK_FACTORY
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_SERVICE_CLASS, _SERVICE_DEPENDENCY_FIELDS,
                    _TRUSTED_SERVICE_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_PRODUCER_CLASS, _PRODUCER_DEPENDENCY_FIELDS,
                    _TRUSTED_PRODUCER_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_CURSOR_CLASS, _CURSOR_DEPENDENCY_FIELDS,
                    _TRUSTED_CURSOR_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_LEASE_CLASS, _LEASE_FIELDS, _TRUSTED_LEASE_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_SELECTION_CLASS, _SELECTION_FIELDS, _TRUSTED_SELECTION_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_SOURCE_CLASS, _SOURCE_FIELDS, _TRUSTED_SOURCE_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_LOCATOR_CLASS, _LOCATOR_FIELDS, _TRUSTED_LOCATOR_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_SNAPSHOT_CLASS, _SNAPSHOT_FIELDS, _TRUSTED_SNAPSHOT_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_ITEM_CLASS, _ITEM_FIELDS, _TRUSTED_ITEM_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_METADATA_CLASS, _METADATA_FIELDS, _TRUSTED_METADATA_STATE,
                )
                or not _TRUSTED_CLASS_MATCHES(
                    _TRUSTED_CURSOR_ENVELOPE_CLASS, _CURSOR_ENVELOPE_FIELDS,
                    _TRUSTED_CURSOR_ENVELOPE_STATE,
                )):
            _fail()
        try:
            _TRUSTED_SERVICE_ASSERT_PINS(self._service)
            service_state = vars(self._service)
        except BaseException:
            _fail()
        if (type(service_state) is not dict
                or any(name in service_state for name in (
                    "issue_permit", "_fence_disclosed_publication", "revoke",
                ))
                or self._service._producer is not self._producer
                or self._service._authority is not self._authority
                or self._producer._mcp_authority is not self._authority
                or self._cursor._authority is not self._authority
                or type(self._lock) is not _TRUSTED_RLOCK_TYPE
                or type(self._closed) is not bool
                or type(self._generation) is not int or self._generation < 0
                or type(self._active) is not dict):
            _fail()

    @staticmethod
    def _rpc_id(value: object) -> str | int:
        if type(value) not in {str, int} or isinstance(value, bool):
            _fail()
        if type(value) is str:
            if (not value or len(value) > 256 or not value.isascii()
                    or any(ord(character) < 0x20 or ord(character) == 0x7f for character in value)):
                _fail()
        elif not 0 <= value <= _MAX_JSON_INT:
            _fail()
        return value

    def _begin(self, consent_request_id: object) -> tuple[str, tuple[object, int, int, int]]:
        request_id = _TRUSTED_SAFE_OPAQUE(consent_request_id)
        _TRUSTED_COORDINATOR_ASSERT_PINS(self)
        with self._lock:
            if self._closed or request_id in self._active:
                _fail()
            token = (object(), self._generation, _TRUSTED_GET_IDENT(), _TRUSTED_GETPID())
            self._active[request_id] = token
        return request_id, token

    def _check_active(self, request_id: str, token: tuple[object, int, int, int]) -> None:
        with self._lock:
            if (self._closed or self._active.get(request_id) is not token
                    or token[1] != self._generation or token[2] != _TRUSTED_GET_IDENT()
                    or token[3] != _TRUSTED_GETPID()):
                _fail()

    def _finish(self, request_id: str, token: tuple[object, int, int, int], frozen: bytes,
                *, revoke: bool) -> bytes:
        if type(frozen) is not bytes or not frozen or len(frozen) > _TRUSTED_RESULT_MAX_BYTES:
            _fail()
        with self._lock:
            if (self._closed or self._active.get(request_id) is not token
                    or token[1] != self._generation or token[2] != _TRUSTED_GET_IDENT()
                    or token[3] != _TRUSTED_GETPID()):
                _fail()
            self._active.pop(request_id)
            if revoke:
                _TRUSTED_REVOKE(self._service, request_id)
            return frozen

    def _abort(self, request_id: object, token: object | None) -> None:
        try:
            if type(request_id) is str:
                with self._lock:
                    if token is not None and self._active.get(request_id) is token:
                        self._active.pop(request_id, None)
                _TRUSTED_REVOKE(self._service, request_id)
        except BaseException:
            return

    def _capture(
        self, lease: object, selection: object, consent_request_id: str,
    ) -> tuple[CombinedContextSnapshot, tuple[tuple[object, ...], ...], tuple[object, ...],
               tuple[dict[str, str], ...], str, str, str]:
        if type(lease) is not _TRUSTED_LEASE_CLASS:
            _fail()
        selection_projection = _TRUSTED_SELECTION_PROJECTION(selection)
        snapshot = _TRUSTED_CAPTURE(self._producer, lease, selection)
        snapshot_projection = _TRUSTED_SNAPSHOT_PROJECTION(snapshot)
        ordered = _TRUSTED_ORDERED_ITEMS(selection_projection, snapshot_projection)
        pack_id, selection_digest, snapshot_digest = _TRUSTED_IDENTITY(
            consent_request_id, selection_projection, snapshot_projection,
        )
        _TRUSTED_COORDINATOR_ASSERT_PINS(self)
        return (snapshot, selection_projection, snapshot_projection, ordered, pack_id,
                selection_digest, snapshot_digest)

    def _fence(self, request_id: str, lease: McpContextReadLease,
               selection: ContextSelection, snapshot: CombinedContextSnapshot,
               page_bytes: int) -> None:
        _TRUSTED_FENCE(
            self._service, request_id, lease, selection, snapshot, page_bytes=page_bytes,
        )
        _TRUSTED_COORDINATOR_ASSERT_PINS(self)

    def _encode(self, rpc_request_id: str | int, result: dict[str, object]) -> bytes:
        encoded = _TRUSTED_ENCODER(rpc_request_id, result)
        _TRUSTED_COORDINATOR_ASSERT_PINS(self)
        if type(encoded) is not bytes or not encoded or len(encoded) > _TRUSTED_RESULT_MAX_BYTES:
            _fail()
        return bytes(encoded)

    def publish_manifest(
        self,
        lease: McpContextReadLease,
        original_selection: ContextSelection,
        consent_request_id: str,
        rpc_request_id: str | int,
        page_bytes: int,
        cursor: str | None = None,
    ) -> bytes:
        request_id: object = consent_request_id
        token: object | None = None
        try:
            request_id, token = _TRUSTED_COORDINATOR_BEGIN(self, consent_request_id)
            rpc_id = _TRUSTED_COORDINATOR_RPC_ID(rpc_request_id)
            if type(page_bytes) is not int or not 1 <= page_bytes <= _TRUSTED_RAW_PAGE_MAX_BYTES:
                _fail()
            (snapshot, selection_projection, snapshot_projection, ordered, pack_id,
             selection_digest, snapshot_digest) = _TRUSTED_COORDINATOR_CAPTURE(
                self,
                lease, original_selection, request_id,
            )
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            if cursor is None:
                index = 0
            else:
                envelope = _TRUSTED_VERIFY_MANIFEST(
                    self._cursor, cursor, lease, pack_id=pack_id, selection=selection_digest,
                    snapshot=snapshot_digest, page_bytes=page_bytes,
                )
                envelope_values = _TRUSTED_SLOTS(
                    envelope, _TRUSTED_CURSOR_ENVELOPE_CLASS,
                    _TRUSTED_CURSOR_ENVELOPE_STATE,
                )
                if (type(envelope_values[8]) is not int
                        or type(envelope_values[9]) is not int):
                    _fail()
                index = envelope_values[8]
                if envelope_values[9] != 0:
                    _fail()
            if not 0 <= index < len(ordered):
                _fail()
            end = min(len(ordered), index + _TRUSTED_MANIFEST_ITEMS_MAX)
            complete = end == len(ordered)
            next_cursor = None
            if not complete:
                next_cursor = _TRUSTED_ISSUE_MANIFEST(
                    self._cursor, lease, pack_id=pack_id, selection=selection_digest,
                    snapshot=snapshot_digest, target_index=end, offset=0,
                    page_bytes=page_bytes,
                )
            result = {
                "schema": _TRUSTED_PACK_SCHEMA,
                "pack_id": pack_id,
                "items": list(ordered[index:end]),
                "manifest_cursor": next_cursor,
                "complete": complete,
                "read_only": True,
                "authority_granted": False,
                "send_performed": False,
            }
            _TRUSTED_COORDINATOR_FENCE(
                self, request_id, lease, original_selection, snapshot, page_bytes,
            )
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            frozen = _TRUSTED_COORDINATOR_ENCODE(self, rpc_id, result)
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            _TRUSTED_COORDINATOR_FENCE(
                self, request_id, lease, original_selection, snapshot, page_bytes,
            )
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            return _TRUSTED_COORDINATOR_FINISH(self, request_id, token, frozen, revoke=False)
        except BaseException:
            _TRUSTED_COORDINATOR_ABORT(self, request_id, token)
            _fail()

    def publish_content(
        self,
        lease: McpContextReadLease,
        original_selection: ContextSelection,
        consent_request_id: str,
        rpc_request_id: str | int,
        page_bytes: int,
        cursor: str | None = None,
    ) -> bytes:
        request_id: object = consent_request_id
        token: object | None = None
        try:
            request_id, token = _TRUSTED_COORDINATOR_BEGIN(self, consent_request_id)
            rpc_id = _TRUSTED_COORDINATOR_RPC_ID(rpc_request_id)
            if type(page_bytes) is not int or not 1 <= page_bytes <= _TRUSTED_RAW_PAGE_MAX_BYTES:
                _fail()
            (snapshot, selection_projection, snapshot_projection, ordered, pack_id,
             selection_digest, snapshot_digest) = _TRUSTED_COORDINATOR_CAPTURE(
                self,
                lease, original_selection, request_id,
            )
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            if cursor is None:
                target_index, offset = 0, 0
            else:
                envelope = _TRUSTED_VERIFY_CONTENT(
                    self._cursor, cursor, lease, pack_id=pack_id, selection=selection_digest,
                    snapshot=snapshot_digest, page_bytes=page_bytes,
                )
                envelope_values = _TRUSTED_SLOTS(
                    envelope, _TRUSTED_CURSOR_ENVELOPE_CLASS,
                    _TRUSTED_CURSOR_ENVELOPE_STATE,
                )
                if (type(envelope_values[8]) is not int
                        or type(envelope_values[9]) is not int):
                    _fail()
                target_index, offset = envelope_values[8], envelope_values[9]
            if not 0 <= target_index < len(ordered) or not 0 <= offset <= _MAX_JSON_INT:
                _fail()
            source = selection_projection[target_index][0]
            if type(source) is not str or source != ordered[target_index]["source"]:
                _fail()
            permit = _TRUSTED_ISSUE_PERMIT(self._service, request_id)
            if source == "forge":
                inner = _TRUSTED_PUBLISH_FORGE(
                    self._service, permit, request_id=rpc_id,
                    target_index=target_index, offset=offset,
                )
            elif source == "planning":
                inner = _TRUSTED_PUBLISH_PLANNING(
                    self._service, permit, request_id=rpc_id,
                    target_index=target_index, offset=offset,
                )
            elif source == "knowledge":
                inner = _TRUSTED_PUBLISH_KNOWLEDGE(
                    self._service, permit, request_id=rpc_id,
                    target_index=target_index, offset=offset,
                )
            else:
                _fail()
            _TRUSTED_COORDINATOR_ASSERT_PINS(self)
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            page = _TRUSTED_PARSE_INNER(
                inner, source=source, rpc_request_id=rpc_id, target_index=target_index,
                offset=offset, expected_item=ordered[target_index], page_bytes=page_bytes,
            )
            _TRUSTED_COORDINATOR_FENCE(
                self, request_id, lease, original_selection, snapshot, page_bytes,
            )
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            end = page["end"]
            total = page["total"]
            if type(end) is not int or type(total) is not int:
                _fail()
            if end < total:
                next_target, next_offset, complete = target_index, end, False
            elif target_index + 1 < len(ordered):
                next_target, next_offset, complete = target_index + 1, 0, False
            else:
                next_target, next_offset, complete = target_index, end, True
            next_cursor = None
            if not complete:
                next_cursor = _TRUSTED_ISSUE_CONTENT(
                    self._cursor, lease, pack_id=pack_id, selection=selection_digest,
                    snapshot=snapshot_digest, target_index=next_target, offset=next_offset,
                    page_bytes=page_bytes,
                )
            result = {
                "schema": _TRUSTED_PACK_SCHEMA,
                "pack_id": pack_id,
                "page": page,
                "content_cursor": next_cursor,
                "complete": complete,
                "read_only": True,
                "authority_granted": False,
                "send_performed": False,
            }
            frozen = _TRUSTED_COORDINATOR_ENCODE(self, rpc_id, result)
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            _TRUSTED_COORDINATOR_FENCE(
                self, request_id, lease, original_selection, snapshot, page_bytes,
            )
            _TRUSTED_COORDINATOR_CHECK_ACTIVE(self, request_id, token)
            return _TRUSTED_COORDINATOR_FINISH(self, request_id, token, frozen, revoke=complete)
        except BaseException:
            _TRUSTED_COORDINATOR_ABORT(self, request_id, token)
            _fail()

    def cancel(self, request_id: str) -> None:
        try:
            exact = _TRUSTED_SAFE_OPAQUE(request_id)
            _TRUSTED_COORDINATOR_ASSERT_PINS(self)
            with self._lock:
                dict.pop(self._active, exact, None)
                _TRUSTED_REVOKE(self._service, exact)
            _TRUSTED_COORDINATOR_ASSERT_PINS(self)
        except BaseException:
            _fail()

    def close(self) -> None:
        _TRUSTED_STATIC_PIN_GUARD()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1


_TRUSTED_COORDINATOR_CLASS = McpContextPackCoordinator
_TRUSTED_COORDINATOR_SLOT_NAMES = tuple(McpContextPackCoordinator.__slots__)
_TRUSTED_COORDINATOR_STATE = _TRUSTED_CLASS_STATE(
    _TRUSTED_COORDINATOR_CLASS, _TRUSTED_COORDINATOR_SLOT_NAMES,
)
_TRUSTED_COORDINATOR_ASSERT_PINS = McpContextPackCoordinator._assert_pins
_TRUSTED_COORDINATOR_RPC_ID = McpContextPackCoordinator._rpc_id
_TRUSTED_COORDINATOR_BEGIN = McpContextPackCoordinator._begin
_TRUSTED_COORDINATOR_CHECK_ACTIVE = McpContextPackCoordinator._check_active
_TRUSTED_COORDINATOR_FINISH = McpContextPackCoordinator._finish
_TRUSTED_COORDINATOR_ABORT = McpContextPackCoordinator._abort
_TRUSTED_COORDINATOR_CAPTURE = McpContextPackCoordinator._capture
_TRUSTED_COORDINATOR_FENCE = McpContextPackCoordinator._fence
_TRUSTED_COORDINATOR_ENCODE = McpContextPackCoordinator._encode
_TRUSTED_COORDINATOR_NAMESPACE = tuple(
    (name, McpContextPackCoordinator.__dict__[name])
    for name in (
        "__init__", "__init_subclass__", "__repr__", "_assert_pins", "_rpc_id",
        "_begin", "_check_active", "_finish", "_abort", "_capture", "_fence",
        "_encode", "publish_manifest", "publish_content", "cancel", "close",
        *_TRUSTED_COORDINATOR_SLOT_NAMES,
    )
)


__all__ = [
    "MCP_CONTEXT_PACK_ERROR",
    "McpContextPackCoordinator",
    "McpContextPackCoordinatorError",
]
