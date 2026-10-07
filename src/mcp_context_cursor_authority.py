"""Authenticated, process-local cursors for the MCP context read bridge.

The cursor is continuation data only.  Every issue and verification operation
rechecks an already-issued native context lease.  This module never issues or
extends a lease, binding, bearer, scope, permission, or confirmation.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from typing import Callable

from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextBinding,
    McpContextReadLease,
)
from src.mcp_context_contract import (
    MCP_CONTEXT_CURSOR_MAX_CHARS,
    MCP_CONTEXT_PACK_SCHEMA,
    MCP_CONTEXT_RAW_PAGE_MAX_BYTES,
    MCP_CONTEXT_SELECTION_MAX,
    McpContextContractError,
    McpContextCursorEnvelope,
    decode_context_cursor,
    encode_context_cursor,
)


_ERROR_CODES = frozenset(("invalid_request", "invalid_cursor", "context_unavailable"))
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_EPOCH_RE = re.compile(r"^epoch_[0-9a-f]{32}$")

_MANIFEST_PREFIX = "m1"
_CONTENT_PREFIX = "c1"
_MANIFEST_KDF_LABEL = b"odysseus.mcp.context.cursor.manifest.key.v1"
_CONTENT_KDF_LABEL = b"odysseus.mcp.context.cursor.content.key.v1"
_MANIFEST_MAC_DOMAIN = b"odysseus.mcp.context.cursor.manifest.mac.v1"
_CONTENT_MAC_DOMAIN = b"odysseus.mcp.context.cursor.content.mac.v1"
_TAG_BYTES = 32
_TAG_CHARS = 43
_MAX_OFFSET = 2**53 - 1
_MAX_TTL_SECONDS = 900


class McpContextCursorAuthorityError(ValueError):
    """A fixed-code cursor failure without cursor or binding details."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "context_unavailable"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpContextCursorAuthorityError({self.code!r})"


def _fail(code: str) -> None:
    raise McpContextCursorAuthorityError(code) from None


def _frame(value: bytes) -> bytes:
    if type(value) is not bytes or len(value) > 2**32 - 1:
        _fail("context_unavailable")
    return len(value).to_bytes(4, "big") + value


def _derive_key(master_key: bytes, label: bytes) -> bytes:
    if type(master_key) is not bytes or len(master_key) != 32 or type(label) is not bytes:
        _fail("context_unavailable")
    return hmac.new(master_key, _frame(label), hashlib.sha256).digest()


def _binding_aad(binding: McpContextBinding) -> tuple[str, str, str, str]:
    """Capture the exact native principal/chat/process binding used as AAD."""
    if type(binding) is not McpContextBinding:
        _fail("context_unavailable")
    try:
        values = (
            binding.owner_subject_id,
            binding.token_id,
            binding.chat_session_id,
            binding.process_epoch,
        )
    except Exception:
        raise McpContextCursorAuthorityError("context_unavailable") from None
    if (
        any(type(value) is not str or not value or len(value) > 512 for value in values)
        or not values[0].isascii()
        or not values[1].isascii()
        or not values[2].isascii()
        or not values[3].isascii()
        or _SAFE_ID_RE.fullmatch(values[0]) is None
        or _SAFE_ID_RE.fullmatch(values[1]) is None
        or _SAFE_ID_RE.fullmatch(values[2]) is None
        or _EPOCH_RE.fullmatch(values[3]) is None
    ):
        _fail("context_unavailable")
    return values


def _mac(key: bytes, domain: bytes, payload: bytes, aad: tuple[str, str, str, str]) -> bytes:
    """MAC one canonical payload and its exact native binding projection."""
    if type(key) is not bytes or len(key) != 32 or type(domain) is not bytes or type(payload) is not bytes:
        _fail("context_unavailable")
    if type(aad) is not tuple or len(aad) != 4 or any(type(value) is not str for value in aad):
        _fail("context_unavailable")
    try:
        framed = _frame(domain) + _frame(payload)
        for value in aad:
            framed += _frame(value.encode("ascii"))
        return hmac.new(key, framed, hashlib.sha256).digest()
    except McpContextCursorAuthorityError:
        raise
    except Exception:
        raise McpContextCursorAuthorityError("context_unavailable") from None


def _b64encode(value: bytes) -> str:
    if type(value) is not bytes:
        _fail("context_unavailable")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: object, *, exact_bytes: int | None = None) -> bytes:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or "=" in value
        or _B64URL_RE.fullmatch(value) is None
    ):
        _fail("invalid_cursor")
    if exact_bytes is not None and (type(exact_bytes) is not int or exact_bytes < 0):
        _fail("context_unavailable")
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        if _b64encode(decoded) != value or (exact_bytes is not None and len(decoded) != exact_bytes):
            _fail("invalid_cursor")
        return decoded
    except McpContextCursorAuthorityError:
        raise
    except (binascii.Error, UnicodeError, ValueError, TypeError):
        raise McpContextCursorAuthorityError("invalid_cursor") from None


def _safe_id(value: object, *, failure: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 512
        or not value.isascii()
        or _SAFE_ID_RE.fullmatch(value) is None
    ):
        _fail(failure)
    return value


def _safe_digest(value: object, *, failure: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.isascii()
        or _DIGEST_RE.fullmatch(value) is None
    ):
        _fail(failure)
    return value


def _safe_integer(value: object, *, minimum: int, maximum: int, failure: str) -> int:
    if type(value) is not int or isinstance(value, bool) or not minimum <= value <= maximum:
        _fail(failure)
    return value


def _bounded_time(value: object) -> float:
    """Normalize hostile clock/lease numerics without leaking conversion errors."""
    if type(value) not in {int, float} or isinstance(value, bool):
        _fail("context_unavailable")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        raise McpContextCursorAuthorityError("context_unavailable") from None
    if not math.isfinite(normalized) or not 0 <= normalized <= _MAX_OFFSET:
        _fail("context_unavailable")
    return normalized


def _lease_projection(lease: McpContextReadLease) -> tuple[str, float]:
    if type(lease) is not McpContextReadLease:
        # Let the native authority provide the fixed authorization denial.
        return "", float("nan")
    try:
        request_id = lease.request_id
        expires_at = lease.expires_at
    except Exception:
        raise McpContextCursorAuthorityError("context_unavailable") from None
    if (
        type(request_id) is not str
        or not 1 <= len(request_id) <= 256
        or not request_id.isascii()
        or _SAFE_ID_RE.fullmatch(request_id) is None
    ):
        _fail("context_unavailable")
    return request_id, _bounded_time(expires_at)


def _canonical_envelope_segment(envelope: McpContextCursorEnvelope) -> str:
    """Revalidate even an object.__setattr__-mutated frozen envelope."""
    if type(envelope) is not McpContextCursorEnvelope:
        _fail("invalid_cursor")
    try:
        encoded = encode_context_cursor(envelope)
    except McpContextContractError:
        raise McpContextCursorAuthorityError("invalid_cursor") from None
    except Exception:
        raise McpContextCursorAuthorityError("invalid_cursor") from None
    if type(encoded) is not str or not encoded or "=" in encoded or _B64URL_RE.fullmatch(encoded) is None:
        _fail("invalid_cursor")
    return encoded


class McpContextCursorAuthority:
    """Sign and verify lease-bound manifest and content continuations."""

    __slots__ = (
        "_authority",
        "_wall_time",
        "_wall_time_pin",
        "_lock",
        "_closed",
        "_last_wall",
        "_master_key",
        "_manifest_key",
        "_content_key",
    )

    def __init__(
        self,
        authority: McpContextAuthority,
        *,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        if type(authority) is not McpContextAuthority or not callable(wall_time):
            raise ValueError("invalid cursor authority")
        try:
            master_key = secrets.token_bytes(32)
        except Exception:
            raise ValueError("invalid cursor authority") from None
        if type(master_key) is not bytes or len(master_key) != 32:
            raise ValueError("invalid cursor authority")
        self._authority = authority
        self._wall_time = wall_time
        self._wall_time_pin = wall_time
        self._lock = threading.RLock()
        self._closed = False
        self._last_wall: float | None = None
        self._master_key: bytes | None = master_key
        self._manifest_key: bytes | None = _derive_key(master_key, _MANIFEST_KDF_LABEL)
        self._content_key: bytes | None = _derive_key(master_key, _CONTENT_KDF_LABEL)

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("cursor authority cannot be subclassed")

    def __repr__(self) -> str:
        return "<McpContextCursorAuthority redacted>"

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._master_key = None
            self._manifest_key = None
            self._content_key = None

    def _wall_now(self) -> float:
        with self._lock:
            callback = self._wall_time
            if self._closed or callback is not self._wall_time_pin:
                _fail("context_unavailable")
        try:
            value = callback()
        except Exception:
            raise McpContextCursorAuthorityError("context_unavailable") from None
        now = _bounded_time(value)
        with self._lock:
            if self._closed or self._wall_time is not callback or self._wall_time_pin is not callback:
                _fail("context_unavailable")
            if self._last_wall is not None and now < self._last_wall:
                _fail("context_unavailable")
            self._last_wall = now
        return now

    def _key(self, kind: str) -> tuple[bytes, bytes, str]:
        with self._lock:
            if self._closed:
                _fail("context_unavailable")
            if kind == "manifest":
                key, domain, prefix = self._manifest_key, _MANIFEST_MAC_DOMAIN, _MANIFEST_PREFIX
            elif kind == "content":
                key, domain, prefix = self._content_key, _CONTENT_MAC_DOMAIN, _CONTENT_PREFIX
            else:
                _fail("context_unavailable")
            if type(key) is not bytes or len(key) != 32:
                _fail("context_unavailable")
            return key, domain, prefix

    def _recheck(self, lease: McpContextReadLease) -> McpContextBinding:
        # Call the exact class implementation so an instance-level callback
        # cannot replace the native authority boundary.
        return McpContextAuthority.recheck_lease(self._authority, lease)

    @staticmethod
    def _issue_fields(
        pack_id: object,
        selection: object,
        snapshot: object,
        target_index: object,
        offset: object,
        page_bytes: object,
    ) -> tuple[str, str, str, int, int, int]:
        return (
            _safe_id(pack_id, failure="invalid_request"),
            _safe_digest(selection, failure="invalid_request"),
            _safe_digest(snapshot, failure="invalid_request"),
            _safe_integer(
                target_index, minimum=0, maximum=MCP_CONTEXT_SELECTION_MAX - 1,
                failure="invalid_request",
            ),
            _safe_integer(offset, minimum=0, maximum=_MAX_OFFSET, failure="invalid_request"),
            _safe_integer(
                page_bytes, minimum=1, maximum=MCP_CONTEXT_RAW_PAGE_MAX_BYTES,
                failure="invalid_request",
            ),
        )

    @staticmethod
    def _verify_fields(
        pack_id: object,
        selection: object,
        snapshot: object,
        page_bytes: object,
    ) -> tuple[str, str, str, int]:
        return (
            _safe_id(pack_id, failure="invalid_cursor"),
            _safe_digest(selection, failure="invalid_cursor"),
            _safe_digest(snapshot, failure="invalid_cursor"),
            _safe_integer(
                page_bytes, minimum=1, maximum=MCP_CONTEXT_RAW_PAGE_MAX_BYTES,
                failure="invalid_cursor",
            ),
        )

    def _issue(
        self,
        kind: str,
        lease: McpContextReadLease,
        *,
        pack_id: object,
        selection: object,
        snapshot: object,
        target_index: object,
        offset: object,
        page_bytes: object,
    ) -> str:
        pack, selection_digest, snapshot_digest, index, exact_offset, page_size = self._issue_fields(
            pack_id, selection, snapshot, target_index, offset, page_bytes
        )
        first = self._recheck(lease)
        aad = _binding_aad(first)
        lease_values = _lease_projection(lease)
        now = self._wall_now()
        second = self._recheck(lease)
        if second is not first or _binding_aad(second) != aad or _lease_projection(lease) != lease_values:
            _fail("context_unavailable")
        expiry = math.floor(min(lease_values[1], now + _MAX_TTL_SECONDS))
        if type(expiry) is not int or expiry <= now:
            _fail("context_unavailable")
        try:
            envelope = McpContextCursorEnvelope(
                request_id=lease_values[0],
                pack_id=pack,
                recipient=aad[1],
                chat=aad[2],
                process_epoch=aad[3],
                selection=selection_digest,
                snapshot=snapshot_digest,
                kind=kind,
                target_index=index,
                offset=exact_offset,
                page_bytes=page_size,
                expiry=expiry,
                schema=MCP_CONTEXT_PACK_SCHEMA,
            )
        except McpContextContractError:
            raise McpContextCursorAuthorityError("invalid_request") from None
        except Exception:
            raise McpContextCursorAuthorityError("context_unavailable") from None
        payload_segment = _canonical_envelope_segment(envelope)
        payload = _b64decode(payload_segment)
        key, domain, prefix = self._key(kind)
        tag_segment = _b64encode(_mac(key, domain, payload, aad))
        if len(tag_segment) != _TAG_CHARS:
            _fail("context_unavailable")
        token = f"{prefix}.{payload_segment}.{tag_segment}"
        if len(token) > MCP_CONTEXT_CURSOR_MAX_CHARS:
            _fail("invalid_request")
        return token

    def issue_manifest_cursor(
        self,
        lease: McpContextReadLease,
        *,
        pack_id: str,
        selection: str,
        snapshot: str,
        target_index: int,
        offset: int,
        page_bytes: int,
    ) -> str:
        return self._issue(
            "manifest", lease, pack_id=pack_id, selection=selection, snapshot=snapshot,
            target_index=target_index, offset=offset, page_bytes=page_bytes,
        )

    def issue_content_cursor(
        self,
        lease: McpContextReadLease,
        *,
        pack_id: str,
        selection: str,
        snapshot: str,
        target_index: int,
        offset: int,
        page_bytes: int,
    ) -> str:
        return self._issue(
            "content", lease, pack_id=pack_id, selection=selection, snapshot=snapshot,
            target_index=target_index, offset=offset, page_bytes=page_bytes,
        )

    @staticmethod
    def _outer(token: object, expected_prefix: str) -> tuple[str, bytes, bytes]:
        if (
            type(token) is not str
            or not 1 <= len(token) <= MCP_CONTEXT_CURSOR_MAX_CHARS
            or not token.isascii()
            or "=" in token
        ):
            _fail("invalid_cursor")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != expected_prefix or not parts[1] or len(parts[2]) != _TAG_CHARS:
            _fail("invalid_cursor")
        payload = _b64decode(parts[1])
        tag = _b64decode(parts[2], exact_bytes=_TAG_BYTES)
        return parts[1], payload, tag

    def _verify(
        self,
        kind: str,
        token: object,
        lease: McpContextReadLease,
        *,
        pack_id: object,
        selection: object,
        snapshot: object,
        page_bytes: object,
    ) -> McpContextCursorEnvelope:
        expected_prefix = _MANIFEST_PREFIX if kind == "manifest" else _CONTENT_PREFIX
        payload_segment, payload, supplied_tag = self._outer(token, expected_prefix)
        first = self._recheck(lease)
        aad = _binding_aad(first)
        lease_values = _lease_projection(lease)
        key, domain, _prefix = self._key(kind)
        expected_tag = _mac(key, domain, payload, aad)
        if not hmac.compare_digest(supplied_tag, expected_tag):
            _fail("invalid_cursor")

        # Authentication deliberately precedes JSON parsing.
        try:
            envelope = decode_context_cursor(payload_segment)
        except McpContextContractError:
            raise McpContextCursorAuthorityError("invalid_cursor") from None
        except Exception:
            raise McpContextCursorAuthorityError("invalid_cursor") from None
        if _canonical_envelope_segment(envelope) != payload_segment:
            _fail("invalid_cursor")
        expected_pack, expected_selection, expected_snapshot, expected_page_size = self._verify_fields(
            pack_id, selection, snapshot, page_bytes
        )
        now = self._wall_now()
        if (
            type(envelope) is not McpContextCursorEnvelope
            or envelope.schema != MCP_CONTEXT_PACK_SCHEMA
            or envelope.kind != kind
            or envelope.request_id != lease_values[0]
            or envelope.pack_id != expected_pack
            or envelope.recipient != aad[1]
            or envelope.chat != aad[2]
            or envelope.process_epoch != aad[3]
            or envelope.selection != expected_selection
            or envelope.snapshot != expected_snapshot
            or type(envelope.target_index) is not int
            or isinstance(envelope.target_index, bool)
            or not 0 <= envelope.target_index < MCP_CONTEXT_SELECTION_MAX
            or type(envelope.offset) is not int
            or isinstance(envelope.offset, bool)
            or not 0 <= envelope.offset <= _MAX_OFFSET
            or envelope.page_bytes != expected_page_size
            or type(envelope.expiry) is not int
            or isinstance(envelope.expiry, bool)
            or envelope.expiry <= now
            or envelope.expiry > math.floor(lease_values[1])
            or envelope.expiry > math.floor(now + _MAX_TTL_SECONDS)
        ):
            _fail("invalid_cursor")
        second = self._recheck(lease)
        if second is not first or _binding_aad(second) != aad or _lease_projection(lease) != lease_values:
            _fail("context_unavailable")
        with self._lock:
            current = self._manifest_key if kind == "manifest" else self._content_key
            if self._closed or type(current) is not bytes or not hmac.compare_digest(current, key):
                _fail("invalid_cursor")
        return envelope

    def verify_manifest_cursor(
        self,
        token: str,
        lease: McpContextReadLease,
        *,
        pack_id: str,
        selection: str,
        snapshot: str,
        page_bytes: int,
    ) -> McpContextCursorEnvelope:
        return self._verify(
            "manifest", token, lease, pack_id=pack_id, selection=selection,
            snapshot=snapshot, page_bytes=page_bytes,
        )

    def verify_content_cursor(
        self,
        token: str,
        lease: McpContextReadLease,
        *,
        pack_id: str,
        selection: str,
        snapshot: str,
        page_bytes: int,
    ) -> McpContextCursorEnvelope:
        return self._verify(
            "content", token, lease, pack_id=pack_id, selection=selection,
            snapshot=snapshot, page_bytes=page_bytes,
        )


__all__ = ["McpContextCursorAuthority", "McpContextCursorAuthorityError"]
