"""Closed, content-free contract primitives for the MCP context bridge.

This module deliberately has no route or middleware registration.  It validates
the wire shape and produces detached native selections/cursor envelopes for a
later, separately authorized integration slice.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


MCP_CONTEXT_SCOPE = "mcp:harbour:context:read"
MCP_CONTEXT_AUDIENCE = "authenticated_mcp_context_read"
MCP_CONTEXT_PACK_SCHEMA = "odysseus.mcp.context_pack.v1"

MCP_CONTEXT_BODY_MAX_BYTES = 262_144
MCP_CONTEXT_ARGUMENTS_MAX_BYTES = 32_768
MCP_CONTEXT_RESULT_MAX_BYTES = 65_536
MCP_CONTEXT_AGGREGATE_MAX_BYTES = 262_144
MCP_CONTEXT_JSON_DEPTH_MAX = 16
MCP_CONTEXT_JSON_NODES_MAX = 4_096
MCP_CONTEXT_RAW_PAGE_MAX_BYTES = 16_384
MCP_CONTEXT_MANIFEST_ITEMS_MAX = 8
MCP_CONTEXT_CURSOR_MAX_CHARS = 2_048
MCP_CONTEXT_SELECTION_MAX = 300

MCP_CONTEXT_METHODS = (
    "harbour_context_prepare",
    "harbour_context_manifest",
    "harbour_context_page",
    "harbour_context_cancel",
)

_METHOD_FIELDS = {
    "harbour_context_prepare": frozenset(("chat_session_id", "selection", "page_bytes")),
    "harbour_context_manifest": frozenset(("request_id", "manifest_cursor")),
    "harbour_context_page": frozenset(("request_id", "content_cursor")),
    "harbour_context_cancel": frozenset(("request_id",)),
}
_METHOD_REQUIRED = {
    "harbour_context_prepare": frozenset(("chat_session_id", "selection")),
    "harbour_context_manifest": frozenset(("request_id",)),
    "harbour_context_page": frozenset(("request_id",)),
    "harbour_context_cancel": frozenset(("request_id",)),
}


class McpContextContractError(ValueError):
    """A fresh fixed-code contract failure with no caller data attached."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in {
            "invalid_request",
            "invalid_cursor",
            "budget_exceeded",
        }:
            code = "invalid_request"
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"McpContextContractError({self.code!r})"


def _fail(code: str) -> None:
    raise McpContextContractError(code) from None


def _safe_string(value: object, *, maximum: int, ascii_only: bool = True) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        _fail("invalid_request")
    if ascii_only and not value.isascii():
        _fail("invalid_request")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _fail("invalid_request")
    return value


def _safe_id(value: object, *, maximum: int = 256) -> str:
    value = _safe_string(value, maximum=maximum)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is None:
        _fail("invalid_request")
    return value


def _digest(value: object) -> str:
    value = _safe_string(value, maximum=71)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        _fail("invalid_cursor")
    return value


def _cursor_id(value: object, *, maximum: int = 512) -> str:
    try:
        return _safe_id(value, maximum=maximum)
    except McpContextContractError:
        raise McpContextContractError("invalid_cursor") from None


def _reject_constant(_value: str) -> object:
    _fail("invalid_request")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("invalid_request")
        result[key] = value
    return result


def _check_json_tree(value: object) -> None:
    """Iterative, exact depth/node validation; recursion is never input controlled."""
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MCP_CONTEXT_JSON_NODES_MAX or depth > MCP_CONTEXT_JSON_DEPTH_MAX:
            _fail("budget_exceeded")
        kind = type(item)
        if item is None or kind in {str, bool, int, float}:
            if kind is float and not math.isfinite(item):
                _fail("invalid_request")
        elif kind is list:
            stack.extend((child, depth + 1) for child in item)
        elif kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _fail("invalid_request")
                # Keys are JSON nodes as well, so the advertised limit cannot be bypassed.
                nodes += 1
                if nodes > MCP_CONTEXT_JSON_NODES_MAX:
                    _fail("budget_exceeded")
                stack.append((child, depth + 1))
        else:
            _fail("invalid_request")


def _json_bytes(value: object, *, maximum: int, error: str = "budget_exceeded") -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise McpContextContractError("invalid_request") from None
    if len(encoded) > maximum:
        raise McpContextContractError(error)
    return encoded


def _selection_from_wire(value: object):
    """Construct one detached native ContextSelection from closed JSON data."""
    try:
        from src.private_context_confirmation_app import (
            ContextSelection,
            ForgeSelectionLocator,
            SourceSelection,
        )

        if type(value) is ContextSelection:
            items = value.items
        elif type(value) is list:
            items = []
            for raw in value:
                if type(raw) is not dict:
                    _fail("invalid_request")
                allowed = {"source", "selection_ref", "revision_ref", "forge_locator"}
                if set(raw) - allowed or not {"source", "selection_ref", "revision_ref"} <= set(raw):
                    _fail("invalid_request")
                locator = raw.get("forge_locator")
                if locator is not None:
                    if type(locator) is not dict or set(locator) != {"repo_id", "version_id", "path"}:
                        _fail("invalid_request")
                    locator = ForgeSelectionLocator(locator["repo_id"], locator["version_id"], locator["path"])
                items.append(SourceSelection(raw["source"], raw["selection_ref"], raw["revision_ref"], locator))
        else:
            _fail("invalid_request")
        if type(items) is not tuple:
            items = tuple(items)
        if not 1 <= len(items) <= MCP_CONTEXT_SELECTION_MAX:
            _fail("invalid_request")
        detached = []
        for item in items:
            if type(item) is not SourceSelection:
                _fail("invalid_request")
            locator = item.forge_locator
            if locator is not None:
                if type(locator) is not ForgeSelectionLocator:
                    _fail("invalid_request")
                locator = ForgeSelectionLocator(locator.repo_id, locator.version_id, locator.path)
            detached.append(SourceSelection(item.source, item.selection_ref, item.revision_ref, locator))
        return ContextSelection(tuple(detached))
    except McpContextContractError:
        raise
    except Exception:
        raise McpContextContractError("invalid_request") from None


def _selection_wire_projection(value: object) -> list[dict[str, object]]:
    """Project native or wire selection data into exact builtin JSON values."""
    try:
        from src.private_context_confirmation_app import ContextSelection, ForgeSelectionLocator, SourceSelection

        if type(value) is ContextSelection:
            items = value.items
            if type(items) is not tuple:
                _fail("invalid_request")
            projected: list[dict[str, object]] = []
            for item in items:
                if type(item) is not SourceSelection:
                    _fail("invalid_request")
                locator = item.forge_locator
                row: dict[str, object] = {
                    "source": item.source,
                    "selection_ref": item.selection_ref,
                    "revision_ref": item.revision_ref,
                }
                if locator is not None:
                    if type(locator) is not ForgeSelectionLocator:
                        _fail("invalid_request")
                    row["forge_locator"] = {
                        "repo_id": locator.repo_id,
                        "version_id": locator.version_id,
                        "path": locator.path,
                    }
                projected.append(row)
            return projected
        if type(value) is list:
            return value
    except McpContextContractError:
        raise
    except Exception:
        raise McpContextContractError("invalid_request") from None
    _fail("invalid_request")


def validate_native_context_selection(value: object):
    """Validate and detach an exact native ContextSelection."""
    return _selection_from_wire(value)


@dataclass(frozen=True, slots=True, repr=False)
class McpContextToolCall:
    method: str = field(repr=False)
    request_id: str | int = field(repr=False)
    arguments: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.method) is not str or self.method not in MCP_CONTEXT_METHODS:
            _fail("invalid_request")
        if type(self.request_id) not in {str, int} or isinstance(self.request_id, bool):
            _fail("invalid_request")
        if type(self.request_id) is str:
            _safe_string(self.request_id, maximum=256)
        elif not 0 <= self.request_id <= 2**53 - 1:
            _fail("invalid_request")
        if type(self.arguments) is not dict:
            _fail("invalid_request")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    def __repr__(self) -> str:
        return f"McpContextToolCall(method={self.method!r}, request_id=<redacted>, arguments=<redacted>)"


def validate_context_tool_arguments(method: object, arguments: object) -> dict[str, object]:
    if type(method) is not str or method not in MCP_CONTEXT_METHODS or type(arguments) is not dict:
        _fail("invalid_request")
    if set(arguments) - _METHOD_FIELDS[method] or not _METHOD_REQUIRED[method] <= set(arguments):
        _fail("invalid_request")
    projected_arguments = dict(arguments)
    if "selection" in projected_arguments:
        projected_arguments["selection"] = _selection_wire_projection(projected_arguments["selection"])
    try:
        _check_json_tree(projected_arguments)
    except RecursionError:
        _fail("budget_exceeded")
    _json_bytes(projected_arguments, maximum=MCP_CONTEXT_ARGUMENTS_MAX_BYTES)
    result: dict[str, object] = {}
    for key, value in projected_arguments.items():
        if type(key) is not str:
            _fail("invalid_request")
        if key in {"chat_session_id", "request_id"}:
            result[key] = _safe_id(value, maximum=256)
        elif key in {"manifest_cursor", "content_cursor"}:
            if value is not None:
                decode_context_cursor(value)
            result[key] = value
        elif key == "page_bytes":
            if type(value) is not int or isinstance(value, bool) or not 1 <= value <= MCP_CONTEXT_RAW_PAGE_MAX_BYTES:
                _fail("invalid_request")
            result[key] = value
        elif key == "selection":
            result[key] = _selection_from_wire(value)
        else:
            _fail("invalid_request")
    return result


def parse_mcp_context_request(raw: object) -> McpContextToolCall:
    if type(raw) is not bytes:
        _fail("invalid_request")
    if len(raw) > MCP_CONTEXT_BODY_MAX_BYTES:
        _fail("budget_exceeded")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except McpContextContractError:
        raise
    except (RecursionError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise McpContextContractError("invalid_request") from None
    try:
        _check_json_tree(value)
    except RecursionError:
        raise McpContextContractError("budget_exceeded") from None
    if type(value) is not dict or set(value) != {"jsonrpc", "id", "method", "params"}:
        _fail("invalid_request")
    if value["jsonrpc"] != "2.0" or type(value["jsonrpc"]) is not str:
        _fail("invalid_request")
    request_id = value["id"]
    if type(request_id) not in {str, int} or isinstance(request_id, bool):
        _fail("invalid_request")
    if type(request_id) is str:
        _safe_string(request_id, maximum=256)
    elif not 0 <= request_id <= 2**53 - 1:
        _fail("invalid_request")
    method = value["method"]
    args = validate_context_tool_arguments(method, value["params"])
    return McpContextToolCall(method, request_id, args)


@dataclass(frozen=True, slots=True, repr=False)
class McpContextCursorEnvelope:
    request_id: str = field(repr=False)
    pack_id: str = field(repr=False)
    recipient: str = field(repr=False)
    chat: str = field(repr=False)
    process_epoch: str = field(repr=False)
    selection: str = field(repr=False)
    snapshot: str = field(repr=False)
    kind: str = field(repr=False)
    target_index: int = field(repr=False)
    offset: int = field(repr=False)
    page_bytes: int = field(repr=False)
    expiry: int | float = field(repr=False)
    schema: str = field(default=MCP_CONTEXT_PACK_SCHEMA, repr=False)

    def __post_init__(self) -> None:
        if self.schema != MCP_CONTEXT_PACK_SCHEMA or type(self.schema) is not str:
            _fail("invalid_cursor")
        for value in (self.request_id, self.pack_id, self.recipient, self.chat, self.process_epoch):
            _cursor_id(value, maximum=512)
        _digest(self.selection)
        _digest(self.snapshot)
        if type(self.kind) is not str or self.kind not in {"manifest", "content"}:
            _fail("invalid_cursor")
        if type(self.target_index) is not int or isinstance(self.target_index, bool) or not 0 <= self.target_index < MCP_CONTEXT_SELECTION_MAX:
            _fail("invalid_cursor")
        if type(self.offset) is not int or isinstance(self.offset, bool) or not 0 <= self.offset <= 2**31 - 1:
            _fail("invalid_cursor")
        if type(self.page_bytes) is not int or isinstance(self.page_bytes, bool) or not 1 <= self.page_bytes <= MCP_CONTEXT_RAW_PAGE_MAX_BYTES:
            _fail("invalid_cursor")
        if type(self.expiry) not in {int, float} or isinstance(self.expiry, bool) or not math.isfinite(self.expiry) or self.expiry <= 0:
            _fail("invalid_cursor")

    @property
    def chat_session_id(self) -> str:
        return self.chat

    @property
    def expires_at(self) -> int | float:
        return self.expiry

    def __repr__(self) -> str:
        return "McpContextCursorEnvelope(<redacted>)"


def encode_context_cursor(cursor: McpContextCursorEnvelope) -> str:
    if type(cursor) is not McpContextCursorEnvelope:
        _fail("invalid_cursor")
    # Re-run the closed scalar validation while taking the projection.  Frozen
    # dataclasses can still be corrupted through object.__setattr__.
    try:
        McpContextCursorEnvelope(
            cursor.request_id, cursor.pack_id, cursor.recipient, cursor.chat,
            cursor.process_epoch, cursor.selection, cursor.snapshot, cursor.kind,
            cursor.target_index, cursor.offset, cursor.page_bytes, cursor.expiry,
            cursor.schema,
        )
    except McpContextContractError:
        raise
    except Exception:
        raise McpContextContractError("invalid_cursor") from None
    payload = {
        "schema": cursor.schema,
        "request_id": cursor.request_id,
        "pack_id": cursor.pack_id,
        "recipient": cursor.recipient,
        "chat": cursor.chat,
        "process_epoch": cursor.process_epoch,
        "selection": cursor.selection,
        "snapshot": cursor.snapshot,
        "kind": cursor.kind,
        "target_index": cursor.target_index,
        "offset": cursor.offset,
        "page_bytes": cursor.page_bytes,
        "expiry": cursor.expiry,
    }
    encoded = base64.urlsafe_b64encode(_json_bytes(payload, maximum=4096)).decode("ascii").rstrip("=")
    if not encoded or len(encoded) > MCP_CONTEXT_CURSOR_MAX_CHARS or "=" in encoded:
        _fail("budget_exceeded")
    return encoded


def decode_context_cursor(value: object) -> McpContextCursorEnvelope:
    if type(value) is not str or not value or len(value) > MCP_CONTEXT_CURSOR_MAX_CHARS:
        _fail("invalid_cursor")
    if not value.isascii() or "=" in value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        _fail("invalid_cursor")
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
            _fail("invalid_cursor")
        parsed = json.loads(decoded.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except McpContextContractError:
        raise McpContextContractError("invalid_cursor") from None
    except (binascii.Error, UnicodeError, ValueError, TypeError, json.JSONDecodeError, RecursionError):
        raise McpContextContractError("invalid_cursor") from None
    try:
        _check_json_tree(parsed)
    except (McpContextContractError, RecursionError):
        raise McpContextContractError("invalid_cursor") from None
    if type(parsed) is not dict or set(parsed) != {
        "schema", "request_id", "pack_id", "recipient", "chat", "process_epoch",
        "selection", "snapshot", "kind", "target_index", "offset", "page_bytes", "expiry",
    }:
        _fail("invalid_cursor")
    try:
        return McpContextCursorEnvelope(**parsed)
    except McpContextContractError:
        raise
    except Exception:
        raise McpContextContractError("invalid_cursor") from None


def encode_context_jsonrpc_response(request_id: str | int, result: Mapping[str, object]) -> bytes:
    if type(request_id) not in {str, int} or isinstance(request_id, bool) or type(result) is not dict:
        _fail("invalid_request")
    if type(request_id) is str:
        _safe_string(request_id, maximum=256)
    elif not 0 <= request_id <= 2**53 - 1:
        _fail("invalid_request")
    # Serialize first, then decode the exact builtin tree which will be
    # validated and emitted.  This prevents a mutable caller graph changing
    # between validation and encoding.
    try:
        detached = json.loads(_json_bytes(dict(result), maximum=MCP_CONTEXT_RESULT_MAX_BYTES).decode("utf-8"))
        _check_json_tree(detached)
    except RecursionError:
        raise McpContextContractError("budget_exceeded") from None
    except McpContextContractError:
        raise
    except Exception:
        raise McpContextContractError("invalid_request") from None
    return _json_bytes(
        {"jsonrpc": "2.0", "id": request_id, "result": detached},
        maximum=MCP_CONTEXT_RESULT_MAX_BYTES,
    )


MCP_CONTEXT_TOOL_SCHEMAS = MappingProxyType({
    "harbour_context_prepare": MappingProxyType({
        "required": ("chat_session_id", "selection"),
        "optional": ("page_bytes",),
    }),
    "harbour_context_manifest": MappingProxyType({
        "required": ("request_id",),
        "optional": ("manifest_cursor",),
    }),
    "harbour_context_page": MappingProxyType({
        "required": ("request_id",),
        "optional": ("content_cursor",),
    }),
    "harbour_context_cancel": MappingProxyType({
        "required": ("request_id",),
        "optional": (),
    }),
})


validate_context_selection = validate_native_context_selection
parse_context_request = parse_mcp_context_request
encode_cursor = encode_context_cursor
decode_cursor = decode_context_cursor

