"""Deterministic, SID-bound Forge context-pack composition."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from src.repo_git_adapter import (
    ForgeContentCursor,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotRequest,
    open_forge_content_cursor,
    read_forge_content_page,
)

REQUEST_SCHEMA = "odysseus.forge_context_pack.request.v1"
PACK_SCHEMA = "odysseus.forge_context_pack.v1"


class ForgeContextPackError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ForgeContextAuthority:
    sid: str
    security_revision: int
    reader_request: ForgeSnapshotRequest
    reader: Any
    planning_runtime: Any | None
    planning_snapshot: Mapping[str, Any] | None = None


class ForgeContextPackComposer:
    """Creates authenticated opaque cursors from immutable reader evidence.

    The key is process-local by design; a restart invalidates continuations.
    It is never serialized, configured, logged, or returned to callers.
    """

    def __init__(self, *, cursor_key: bytes | None = None) -> None:
        self._key = cursor_key if type(cursor_key) is bytes and len(cursor_key) >= 32 else secrets.token_bytes(32)

    def compose(self, authority: ForgeContextAuthority, body: Mapping[str, Any]) -> dict[str, Any]:
        if type(authority) is not ForgeContextAuthority or type(body) is not dict:
            self._fail("invalid_request")
        if type(authority.sid) is not str or not authority.sid or type(authority.security_revision) is not int or authority.security_revision < 0:
            self._fail("invalid_authority")
        request = self._request(body)
        # Freeze the opaque Planning projection before any reader callback.
        # Neither caller-owned request aliases nor mutable snapshot refs may
        # change the request authenticated by this composition operation.
        planning = self._planning(authority, request["planning"])
        reader_request = authority.reader_request
        if (request["version_id"], request["commit_sha"]) != (reader_request.version_id, reader_request.commit_sha):
            self._fail("immutable_tuple_mismatch")
        # The reader receives the complete requested set before hashing any
        # blob.  Tree metadata is harmless; unrelated file *content* is never
        # read or hashed to compose a public code page.
        inventory = authority.reader.inventory(reader_request, paths=tuple(request["code_paths"]))
        if request["manifest_sha256"] != inventory.manifest_sha256:
            self._fail("immutable_tuple_mismatch")
        paths = request["code_paths"]
        state = self._cursor_or_initial(authority, request, request["cursor"])
        start = state["index"]
        pages: list[dict[str, Any]] = []
        remaining = request["page_bytes"]
        for index in range(start, len(paths)):
            path = paths[index]
            file = inventory.file(path)
            if file.path != path:
                self._fail("invalid_request")
            page_size = min(request["page_bytes"], max(1, remaining))
            if index == start and state.get("forge_cursor") is not None:
                cursor = _cursor_from_dict(state["forge_cursor"])
            else:
                cursor = open_forge_content_cursor(reader_request, inventory, path=file.path, page_bytes=page_size)
            page = read_forge_content_page(authority.reader, reader_request, inventory, cursor)
            pages.append({"path": file.path, "offset": page.offset, "content_b64": base64.b64encode(page.content).decode("ascii"),
                "content_sha256": page.page_content_sha256, "file_content_sha256": file.content_sha256,
                "file_byte_count": file.byte_count, "page_hash": page.page_hash})
            remaining -= len(page.content)
            if not page.complete:
                next_state = {"index": index, "forge_cursor": page.next_cursor.to_dict()}
                break
            next_state = {"index": index + 1, "forge_cursor": None}
            if remaining <= 0:
                break
        else:
            next_state = {"index": len(paths), "forge_cursor": None}
        semantic = {"schema": PACK_SCHEMA, "sid_scope": self._sid_scope(authority.sid),
            "forge": {"repo_id": reader_request.repo_id, "version_id": reader_request.version_id,
                      "commit_sha": reader_request.commit_sha, "manifest_sha256": inventory.manifest_sha256,
                      "snapshot_digest": inventory.snapshot_digest}, "code": pages, "planning": planning,
            "knowledge": {"status": "exact_reader_unavailable"},
            "code_complete": next_state["index"] >= len(paths),
            # Knowledge and Planning remain deliberately partial.  Code can be
            # exhausted while the composite packet remains incomplete.
            "complete": False,
            "read_only": True, "authority_granted": False, "send_performed": False}
        semantic["pack_id"] = "sha256:" + _digest(semantic)
        semantic["next_cursor"] = None if semantic["code_complete"] else self._seal(authority, request, next_state)
        return semantic

    def _request(self, body: Mapping[str, Any]) -> dict[str, Any]:
        required = {"schema", "version_id", "commit_sha", "manifest_sha256", "code_paths", "planning", "page_bytes", "cursor"}
        if type(body) is not dict or len(body) != len(required):
            self._fail("invalid_request")
        body = dict.copy(body)
        if (any(type(key) is not str for key in body) or set(body) != required
                or type(body["schema"]) is not str or body["schema"] != REQUEST_SCHEMA):
            self._fail("invalid_request")
        paths = body["code_paths"]
        if type(paths) is not list or not 1 <= len(paths) <= 64:
            self._fail("invalid_request")
        paths = list.copy(paths)
        if (not paths or len(paths) > 64 or any(type(x) is not str for x in paths)
                or len({x.casefold() for x in paths}) != len(paths)):
            self._fail("invalid_request")
        if type(body["page_bytes"]) is not int or not 1 <= body["page_bytes"] <= 1_000_000:
            self._fail("invalid_request")
        if body["cursor"] is not None and type(body["cursor"]) is not str:
            self._fail("invalid_cursor")
        if any(type(body[k]) is not str or not body[k] for k in ("version_id", "commit_sha", "manifest_sha256")):
            self._fail("invalid_request")
        body["code_paths"] = paths
        body["planning"] = self._planning_request(body["planning"], "invalid_request")
        return body

    def _planning_request(self, value: Any, failure_code: str) -> dict[str, Any]:
        if type(value) is not dict or len(value) != 5:
            self._fail(failure_code)
        planning = dict.copy(value)
        if (any(type(key) is not str for key in planning)
                or set(planning) != {"project_id", "roadmap_id", "revision_id", "content_sha256", "head_generation"}):
            self._fail(failure_code)
        if (any(type(planning[key]) is not str or not planning[key] for key in
                ("project_id", "roadmap_id", "revision_id", "content_sha256"))
                or type(planning["head_generation"]) is not int or planning["head_generation"] <= 0):
            self._fail(failure_code)
        return planning

    def _cursor_or_initial(self, authority: ForgeContextAuthority, request: Mapping[str, Any], token: Any) -> dict[str, Any]:
        if token is None:
            return {"index": 0, "forge_cursor": None}
        try:
            encoded, proof = token.split(".", 1)
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if not hmac.compare_digest(proof, hmac.new(self._key, raw, hashlib.sha256).hexdigest()):
                self._fail("invalid_cursor")
            value = json.loads(raw.decode("utf-8"))
            if (set(value) != {"sid", "security_revision", "request", "index", "forge_cursor"}
                or value["sid"] != self._sid_scope(authority.sid) or value["request"] != _immutable_request_digest(request)
                or value.get("security_revision") != authority.security_revision
                or type(value["index"]) is not int or not 0 <= value["index"] <= len(request["code_paths"])
                or value["forge_cursor"] is not None and type(value["forge_cursor"]) is not dict):
                self._fail("invalid_cursor")
            return {"index": value["index"], "forge_cursor": value["forge_cursor"]}
        except (ValueError, UnicodeError, json.JSONDecodeError, KeyError):
            self._fail("invalid_cursor")

    def _seal(self, authority: ForgeContextAuthority, request: Mapping[str, Any], state: Mapping[str, Any]) -> str:
        raw = json.dumps({"sid": self._sid_scope(authority.sid), "security_revision": authority.security_revision,
            "request": _immutable_request_digest(request), "index": state["index"],
            "forge_cursor": state["forge_cursor"]}, sort_keys=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def _planning(self, authority: ForgeContextAuthority, planning: Mapping[str, Any]) -> dict[str, Any]:
        # Titles, payloads, and client-selected identifiers are never
        # projected.  The route creates this typed snapshot from the accepted
        # SID-bound durable reader and repeats its head fence after bytes flow.
        snapshot = authority.planning_snapshot
        if snapshot is None:
            return {"status": "approval_required", "refs": []}
        if type(snapshot) is not dict or len(snapshot) != 2:
            self._fail("planning_authority_invalid")
        snapshot = dict.copy(snapshot)
        if any(type(key) is not str for key in snapshot) or set(snapshot) != {"request", "refs"}:
            self._fail("planning_authority_invalid")
        captured_request = self._planning_request(snapshot["request"], "planning_authority_invalid")
        if captured_request != planning:
            return {"status": "approval_required", "refs": []}
        refs = snapshot["refs"]
        if type(refs) is not tuple:
            self._fail("planning_authority_invalid")
        captured_refs = []
        for value in refs:
            if type(value) is not dict or len(value) != 2:
                self._fail("planning_authority_invalid")
            value = dict.copy(value)
            if (any(type(key) is not str for key in value) or set(value) != {"ref", "head_binding"}
                    or any(type(part) is not str or not part.startswith("sha256:") for part in value.values())):
                self._fail("planning_authority_invalid")
            captured_refs.append(value)
        return {"status": "approval_required", "refs": captured_refs}

    @staticmethod
    def _sid_scope(sid: str) -> str:
        return "sid:" + hashlib.sha256(sid.encode("utf-8")).hexdigest()

    @staticmethod
    def _fail(code: str) -> None:
        raise ForgeContextPackError(code)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _immutable_request_digest(request: Mapping[str, Any]) -> str:
    """Authenticate only immutable request fields; the continuation is proof."""
    return _digest({key: value for key, value in request.items() if key != "cursor"})


def _cursor_from_dict(value: Any) -> ForgeContentCursor:
    """Reconstruct the adapter's typed cursor instead of passing a raw dict."""
    if type(value) is not dict:
        raise ForgeContextPackError("invalid_cursor")
    copied = dict(value)
    binding = copied.get("authority_binding")
    if type(binding) is not dict or set(binding) != {
        "adapter_id", "adapter_version", "adapter_generation", "admission_policy_generation",
    }:
        raise ForgeContextPackError("invalid_cursor")
    try:
        copied["authority_binding"] = ForgeSnapshotAuthorityBinding(
            binding["adapter_id"], binding["adapter_version"], binding["adapter_generation"],
            binding["admission_policy_generation"],
        )
        return ForgeContentCursor(**copied)
    except (TypeError, ValueError):
        raise ForgeContextPackError("invalid_cursor") from None
