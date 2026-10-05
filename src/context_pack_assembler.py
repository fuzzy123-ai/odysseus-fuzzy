"""Bounded mixed-source content pagination over the installed context readers."""
from __future__ import annotations

import base64
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from src.context_manifest_producer import CombinedContextManifestProducer, CombinedContextSnapshot
from src.forge_context_page import ForgeContextPageConsumer, ForgeContextPageRequest
from src.harbour_knowledge_active_runtime import KnowledgeContextPageConsumer, KnowledgeContextPageRequest
from src.planning_context_reader import PlanningContextPageConsumer, PlanningContextPageRequest
from src.private_context_confirmation import ContextManifest, ManifestItem
from src.private_context_confirmation_app import (
    ChatConfirmationRepository, ContextSelection, CookieConfirmationAuthority,
    PrivateContextConfirmationApp, SourceSelection,
)


_ERRORS = frozenset({
    "invalid_request", "invalid_cursor", "context_changed", "context_unavailable",
    "disclosure_unverified", "invalid_binding",
})
_MAX_PAGE_BYTES = 1_000_000
_MAX_CURSOR_BYTES = 16_384
_CURSOR_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_CURSOR_KEYS = frozenset({
    "v", "pack", "owner", "chat", "auth", "security", "selection",
    "disclosure", "index", "offset",
})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_BYTEFORMS = {
    "forge": "git_blob",
    "planning": "durable_revision_document",
    "knowledge": "knowledge_payload",
}


class ContextPackAssemblerError(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERRORS else "context_unavailable"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"ContextPackAssemblerError(code={self.code!r})"


def _fail(code: str) -> None:
    raise ContextPackAssemblerError(code) from None


def _sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    except Exception:
        _fail("context_unavailable")


def _selection_key(selection: ContextSelection) -> tuple[tuple[str, str, str], ...]:
    return tuple((item.source, item.selection_ref, item.revision_ref) for item in selection.items)


def _selection_digest(selection: ContextSelection) -> str:
    return _sha(_canonical(_selection_key(selection)))


@dataclass(frozen=True, slots=True, repr=False)
class _NormalizedPage:
    source: str
    target_index: int
    offset: int
    end: int
    total: int
    whole_sha256: str
    page_sha256: str
    manifest_digest: str
    byteform: str
    content_bytes: bytes


class ContextPackAssembler:
    """Orchestrate one concrete bounded page at a time, without persistence."""

    __slots__ = (
        "_producer", "_confirmation", "_forge_pages", "_planning_pages", "_knowledge_pages",
        "_authority", "_repository", "_cursor_key",
    )

    def __init__(
        self,
        *,
        manifest_producer: CombinedContextManifestProducer,
        confirmation_app: PrivateContextConfirmationApp,
        forge_pages: ForgeContextPageConsumer,
        planning_pages: PlanningContextPageConsumer,
        knowledge_pages: KnowledgeContextPageConsumer,
    ) -> None:
        if (
            type(manifest_producer) is not CombinedContextManifestProducer
            or type(confirmation_app) is not PrivateContextConfirmationApp
            or type(forge_pages) is not ForgeContextPageConsumer
            or type(planning_pages) is not PlanningContextPageConsumer
            or type(knowledge_pages) is not KnowledgeContextPageConsumer
            or confirmation_app._producer is not manifest_producer
        ):
            _fail("invalid_request")
        authority = confirmation_app._authority.fork()
        if type(authority) is not CookieConfirmationAuthority:
            _fail("invalid_request")
        self._producer = manifest_producer
        self._confirmation = confirmation_app
        self._forge_pages = forge_pages
        self._planning_pages = planning_pages
        self._knowledge_pages = knowledge_pages
        self._authority = authority
        self._repository = ChatConfirmationRepository(confirmation_app._repository._session_factory, authority)
        self._cursor_key = secrets.token_bytes(32)

    def page(
        self,
        cookie: str,
        chat_session_id: str,
        selection: ContextSelection,
        page_bytes: int,
        cursor: str | None = None,
        disclosure_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Return one page and a signed continuation, or raise a fixed error."""
        if (
            type(cookie) is not str or type(chat_session_id) is not str
            or type(selection) is not ContextSelection or type(page_bytes) is not int
            or isinstance(page_bytes, bool) or not 1 <= page_bytes <= _MAX_PAGE_BYTES
            or (cursor is not None and type(cursor) is not str)
            or (disclosure_message_id is not None and (type(disclosure_message_id) is not int or isinstance(disclosure_message_id, bool) or disclosure_message_id < 1))
        ):
            _fail("invalid_request")
        token = self._decode_cursor(cursor) if cursor is not None else None
        try:
            with self._authority.hold(cookie, chat_session_id) as binding:
                self._repository.assert_chat_owned(binding)
                snapshot = self._producer.capture_snapshot(binding, selection)
                pack_id = self._pack_id(binding, chat_session_id, selection, snapshot, disclosure_message_id)
                index, offset = self._start_position(token, pack_id, binding, chat_session_id, selection, disclosure_message_id)
                private = any(item.classification == "private" for item in snapshot.items)
                expected_subset = ContextManifest(snapshot.items) if private else None
                if private:
                    if disclosure_message_id is None:
                        _fail("disclosure_unverified")
                    self._confirmation.verify_disclosure(
                        cookie, chat_session_id, disclosure_message_id, selection,
                        expected_subset=expected_subset,
                    )
                target = selection.items[index]
                expected_manifest = snapshot.target_manifest_digests[index]
                target_item = self._target_item(snapshot, target)
                normalized = self._read_one(
                    cookie, chat_session_id, selection, target, index, expected_manifest, offset,
                    page_bytes, disclosure_message_id, target_item,
                )
                final = self._producer.capture_snapshot(binding, selection)
                if final != snapshot:
                    _fail("context_changed")
                if private:
                    self._confirmation.verify_disclosure(
                        cookie, chat_session_id, disclosure_message_id, selection,
                        expected_subset=ContextManifest(final.items),
                    )
                self._authority.recheck(binding)
                self._repository.assert_chat_owned(binding)
                next_index = index + 1 if normalized.end == normalized.total else index
                next_offset = 0 if normalized.end == normalized.total else normalized.end
                complete = next_index >= len(selection.items)
                next_cursor = "" if complete else self._encode_cursor({
                    "v": 1, "pack": pack_id, "owner": binding.owner_sid,
                    "chat": chat_session_id, "auth": binding.auth_session_binding,
                    "security": binding.security_revision, "selection": _selection_digest(selection),
                    "disclosure": disclosure_message_id, "index": next_index, "offset": next_offset,
                })
                return {
                    "schema": "odysseus.context_pack.v1",
                    "pack_id": pack_id,
                    "items": [item._canonical() for item in snapshot.items],
                    "page": {
                        "target_index": normalized.target_index,
                        "offset": normalized.offset,
                        "end": normalized.end,
                        "total": normalized.total,
                        "whole_sha256": normalized.whole_sha256,
                        "page_sha256": normalized.page_sha256,
                        "byteform": normalized.byteform,
                        "content_b64": base64.b64encode(normalized.content_bytes).decode("ascii"),
                    },
                    "next_cursor": next_cursor,
                    "complete": complete,
                    "read_only": True,
                    "authority_granted": False,
                    "send_performed": False,
                }
        except ContextPackAssemblerError:
            raise
        except Exception:
            _fail("context_unavailable")

    def _read_one(
        self, cookie: str, chat: str, selection: ContextSelection, target: SourceSelection,
        index: int, manifest_digest: str, offset: int, limit: int, disclosure: int | None,
        target_item: ManifestItem,
    ) -> _NormalizedPage:
        try:
            if target.source == "forge":
                result = self._forge_pages.read_page(
                    cookie, ForgeContextPageRequest(selection, target, chat, manifest_digest, offset, limit, disclosure),
                )
                content = result.payload_bytes
                byteform = "git_blob"
            elif target.source == "planning":
                result = self._planning_pages.read_page(
                    cookie, PlanningContextPageRequest(selection, target, chat, manifest_digest, offset, limit, disclosure),
                )
                content = result.page_bytes
                byteform = "durable_revision_document"
            elif target.source == "knowledge":
                result = self._knowledge_pages.read_page(
                    cookie, KnowledgeContextPageRequest(selection, target, chat, manifest_digest, offset, limit, disclosure),
                )
                content = result.payload_bytes
                byteform = "knowledge_payload"
            else:
                _fail("invalid_request")
            normalized = _NormalizedPage(
                target.source, index, result.offset, result.end,
                result.total_bytes if target.source == "planning" else result.total,
                result.whole_sha256, result.page_sha256, result.manifest_digest,
                byteform, content,
            )
            native_byteform = getattr(result, "byteform", byteform)
        except ContextPackAssemblerError:
            raise
        except Exception:
            _fail("context_unavailable")
        if normalized.byteform != _BYTEFORMS.get(normalized.source) or native_byteform != normalized.byteform:
            _fail("context_unavailable")
        if (
            type(normalized.content_bytes) is not bytes
            or type(normalized.offset) is not int or type(normalized.end) is not int
            or type(normalized.total) is not int
            or normalized.offset != offset
            or not 0 <= normalized.offset <= normalized.end <= normalized.total
            or normalized.end - normalized.offset != len(normalized.content_bytes)
            or len(normalized.content_bytes) > limit
            or (len(normalized.content_bytes) == 0 and normalized.end != normalized.total)
            or normalized.manifest_digest != manifest_digest
            or normalized.page_sha256 != _sha(normalized.content_bytes)
            or type(normalized.whole_sha256) is not str
            or not _SHA256_RE.fullmatch(normalized.whole_sha256)
            or normalized.whole_sha256 != "sha256:" + target_item.payload_sha256
        ):
            _fail("context_unavailable")
        return normalized

    @staticmethod
    def _target_item(snapshot: CombinedContextSnapshot, target: SourceSelection) -> ManifestItem:
        matched = tuple(
            item for item in snapshot.items
            if (item.source, item.selection_ref, item.revision_ref)
            == (target.source, target.selection_ref, target.revision_ref)
        )
        if len(matched) != 1:
            _fail("context_changed")
        return matched[0]

    def _pack_id(self, binding: object, chat: str, selection: ContextSelection, snapshot: CombinedContextSnapshot, disclosure: int | None) -> str:
        return "sha256:" + hashlib.sha256(_canonical({
            "schema": "odysseus.context_pack.v1", "owner": binding.owner_sid,
            "chat": chat, "auth": binding.auth_session_binding,
            "security": binding.security_revision, "selection": _selection_key(selection),
            "items": [item._canonical() for item in snapshot.items],
            "target_manifest_digests": snapshot.target_manifest_digests,
            "disclosure": disclosure,
        })).hexdigest()

    def _encode_cursor(self, value: dict[str, object]) -> str:
        payload = _canonical(value)
        signed = payload + b"." + hmac.new(self._cursor_key, payload, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(signed).rstrip(b"=").decode("ascii")
        if len(encoded) > _MAX_CURSOR_BYTES:
            _fail("invalid_cursor")
        return encoded

    def _decode_cursor(self, value: str | None) -> dict[str, object]:
        if value is None or not 1 <= len(value) <= _MAX_CURSOR_BYTES or any(char not in _CURSOR_ALPHABET for char in value):
            _fail("invalid_cursor")
        try:
            padded = value.encode("ascii") + b"=" * (-len(value) % 4)
            raw = base64.urlsafe_b64decode(padded)
            if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
                _fail("invalid_cursor")
            if len(raw) < 33 or raw[-33:-32] != b".":
                _fail("invalid_cursor")
            payload, signature = raw[:-33], raw[-32:]
            if len(signature) != 32 or not hmac.compare_digest(signature, hmac.new(self._cursor_key, payload, hashlib.sha256).digest()):
                _fail("invalid_cursor")
            decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=self._cursor_pairs)
        except ContextPackAssemblerError:
            raise
        except Exception:
            _fail("invalid_cursor")
        if not self._cursor_shape(decoded, 0) or type(decoded) is not dict or set(decoded) != _CURSOR_KEYS:
            _fail("invalid_cursor")
        if _canonical(decoded) != payload:
            _fail("invalid_cursor")
        return decoded

    @staticmethod
    def _cursor_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate cursor key")
            result[key] = value
        return result

    @classmethod
    def _cursor_shape(cls, value: object, depth: int) -> bool:
        if depth > 5:
            return False
        if type(value) is dict:
            return all(type(key) is str and cls._cursor_shape(item, depth + 1) for key, item in value.items())
        if type(value) is list:
            return all(cls._cursor_shape(item, depth + 1) for item in value)
        return value is None or type(value) in {str, int, bool}

    @staticmethod
    def _start_position(token: dict[str, object] | None, pack: str, binding: object, chat: str, selection: ContextSelection, disclosure: int | None) -> tuple[int, int]:
        if token is None:
            return 0, 0
        expected = {
            "pack": pack, "owner": binding.owner_sid, "chat": chat,
            "auth": binding.auth_session_binding, "security": binding.security_revision,
            "disclosure": disclosure,
        }
        if set(token) != _CURSOR_KEYS or any(token.get(key) != value for key, value in expected.items()):
            _fail("invalid_cursor")
        serialized_selection = token.get("selection")
        if (
            type(token.get("v")) is not int or token.get("v") != 1
            or any(type(token.get(key)) is not str for key in ("pack", "owner", "chat", "auth"))
            or type(token.get("security")) is not int or isinstance(token.get("security"), bool)
            or (token.get("disclosure") is not None and (type(token.get("disclosure")) is not int or isinstance(token.get("disclosure"), bool)))
            or type(serialized_selection) is not str
            or not _SHA256_RE.fullmatch(serialized_selection)
            or serialized_selection != _selection_digest(selection)
        ):
            _fail("invalid_cursor")
        index, offset = token.get("index"), token.get("offset")
        if type(index) is not int or isinstance(index, bool) or type(offset) is not int or isinstance(offset, bool) or index < 0 or offset < 0 or index >= len(selection.items):
            _fail("invalid_cursor")
        return index, offset


__all__ = ["ContextPackAssembler", "ContextPackAssemblerError"]
