"""Inactive publication boundary for one consented Planning context page.

This module registers no route and mints no cursor or authority.  It turns one
page issued by the private confirmation service into a complete, bounded
JSON-RPC response only after the service consumes its one-shot completion.
"""

from __future__ import annotations

import base64
import hashlib
import re

from src.mcp_context_contract import (
    MCP_CONTEXT_RESULT_MAX_BYTES,
    encode_context_jsonrpc_response,
)
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationService,
    Permit,
    VerifiedPlanningCapture,
)
from src.planning_context_reader import PlanningContextPage


MCP_PLANNING_PAGE_SCHEMA = "odysseus.mcp.context_planning_page.v1"
MCP_PLANNING_PAGE_ERROR = "planning_page_unavailable"

_MAX_JSON_INT = (1 << 53) - 1
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$\Z")
_RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$\Z")
_TRUSTED_B64ENCODE = base64.b64encode
_TRUSTED_B64DECODE = base64.b64decode
_TRUSTED_ENCODER = encode_context_jsonrpc_response
_TRUSTED_EXECUTE = McpPrivateContextConfirmationService._execute_planning_page
_TRUSTED_CONSUME = McpPrivateContextConfirmationService._consume_planning_completion


class McpPlanningPagePublicationError(ValueError):
    """A fixed-code, content-free publication failure."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = MCP_PLANNING_PAGE_ERROR
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpPlanningPagePublicationError(code={self.code!r})"


def _fail() -> None:
    raise McpPlanningPagePublicationError() from None


def _validate_request(
    service: object,
    permit: object,
    request_id: object,
    target_index: object,
    offset: object,
) -> None:
    if type(service) is not McpPrivateContextConfirmationService or type(permit) is not Permit:
        _fail()
    if type(request_id) not in {str, int} or isinstance(request_id, bool):
        _fail()
    if type(request_id) is str:
        if (
            not request_id
            or len(request_id) > 256
            or not request_id.isascii()
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in request_id)
        ):
            _fail()
    elif not 0 <= request_id <= _MAX_JSON_INT:
        _fail()
    if (
        type(target_index) is not int
        or not 0 <= target_index <= _MAX_JSON_INT
        or type(offset) is not int
        or not 0 <= offset <= _MAX_JSON_INT
    ):
        _fail()
    _assert_service_methods(service)


def _assert_service_methods(service: McpPrivateContextConfirmationService) -> None:
    if (
        McpPrivateContextConfirmationService._execute_planning_page is not _TRUSTED_EXECUTE
        or McpPrivateContextConfirmationService._consume_planning_completion is not _TRUSTED_CONSUME
    ):
        _fail()
    try:
        instance_values = vars(service)
    except BaseException:
        _fail()
    if (
        "_execute_planning_page" in instance_values
        or "_consume_planning_completion" in instance_values
    ):
        _fail()


def _page_values(page: object) -> tuple[object, ...]:
    if type(page) is not PlanningContextPage:
        _fail()
    try:
        values = (
            page.page_bytes,
            page.total_bytes,
            page.offset,
            page.end,
            page.whole_sha256,
            page.page_sha256,
            page.manifest_digest,
            page.byteform,
        )
    except BaseException:
        _fail()
    page_bytes, total_bytes, offset, end, whole, page_digest, manifest, byteform = values
    if (
        type(page_bytes) is not bytes
        or type(total_bytes) is not int
        or type(offset) is not int
        or type(end) is not int
        or not 0 <= offset <= end <= total_bytes <= _MAX_JSON_INT
        or len(page_bytes) != end - offset
        or len(page_bytes) > 16_384
        or type(whole) is not str
        or _SHA256_RE.fullmatch(whole) is None
        or type(page_digest) is not str
        or _SHA256_RE.fullmatch(page_digest) is None
        or page_digest != "sha256:" + hashlib.sha256(page_bytes).hexdigest()
        or type(manifest) is not str
        or _RAW_SHA256_RE.fullmatch(manifest) is None
        or type(byteform) is not str
        or byteform != "durable_revision_document"
    ):
        _fail()
    return values


def _result_for_page(page: object, target_index: int) -> tuple[dict[str, object], tuple[object, ...]]:
    values = _page_values(page)
    return _result_for_values(values, target_index), values


def _result_for_values(values: tuple[object, ...], target_index: int) -> dict[str, object]:
    page_bytes, total_bytes, offset, end, whole, page_digest, manifest, byteform = values
    if base64.b64encode is not _TRUSTED_B64ENCODE:
        _fail()
    try:
        content_bytes = _TRUSTED_B64ENCODE(page_bytes)
        content_b64 = content_bytes.decode("ascii")
        decoded = _TRUSTED_B64DECODE(content_b64, validate=True)
    except BaseException:
        _fail()
    if (
        type(content_bytes) is not bytes
        or type(content_b64) is not str
        or type(decoded) is not bytes
        or decoded != page_bytes
        or _TRUSTED_B64ENCODE(decoded) != content_bytes
    ):
        _fail()
    return {
        "schema": MCP_PLANNING_PAGE_SCHEMA,
        "target_index": target_index,
        "offset": offset,
        "end": end,
        "total_bytes": total_bytes,
        "whole_sha256": whole,
        "page_sha256": page_digest,
        "manifest_digest": manifest,
        "byteform": byteform,
        "content_b64": content_b64,
    }


def publish_planning_page(
    service: McpPrivateContextConfirmationService,
    permit: Permit,
    *,
    request_id: str | int,
    target_index: int,
    offset: int,
) -> bytes:
    """Publish one exact Planning page after its service completion is consumed."""

    _validate_request(service, permit, request_id, target_index, offset)
    frozen_page: object | None = None
    frozen_values: tuple[object, ...] | None = None
    frozen_encoded: bytes | None = None

    def encode_detached(page: object) -> None:
        nonlocal frozen_page, frozen_values, frozen_encoded
        if frozen_page is not None or frozen_values is not None or frozen_encoded is not None:
            _fail()
        trusted_result, values = _result_for_page(page, target_index)
        try:
            expected = _TRUSTED_ENCODER(request_id, trusted_result)
            replaceable_result = _result_for_values(values, target_index)
            encoded = encode_context_jsonrpc_response(request_id, replaceable_result)
        except BaseException:
            _fail()
        if (
            type(encoded) is not bytes
            or not encoded
            or len(encoded) > MCP_CONTEXT_RESULT_MAX_BYTES
            or encoded != expected
            or _page_values(page) != values
        ):
            _fail()
        frozen_page = page
        frozen_values = values
        frozen_encoded = bytes(expected)

    capture: object | None = None
    try:
        capture = _TRUSTED_EXECUTE(
            service,
            permit,
            target_index=target_index,
            offset=offset,
            validate_detached=encode_detached,
        )
        if (
            type(capture) is not VerifiedPlanningCapture
            or frozen_page is None
            or capture.page is not frozen_page
            or frozen_values is None
            or frozen_encoded is None
            or _page_values(capture.page) != frozen_values
        ):
            _fail()
        _assert_service_methods(service)
    except BaseException:
        if type(capture) is VerifiedPlanningCapture:
            try:
                _TRUSTED_CONSUME(service, capture)
            except BaseException:
                pass
        _fail()

    try:
        _TRUSTED_CONSUME(service, capture)
    except BaseException:
        _fail()
    return frozen_encoded


__all__ = [
    "MCP_PLANNING_PAGE_ERROR",
    "MCP_PLANNING_PAGE_SCHEMA",
    "McpPlanningPagePublicationError",
    "publish_planning_page",
]
