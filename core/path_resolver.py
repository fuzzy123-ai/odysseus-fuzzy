"""Resolve owner-scoped virtual mount paths into confined host paths."""

from __future__ import annotations

import os
import json
import shutil
import stat
import time
from typing import Any, Optional

from src.constants import DATA_DIR
from core.mount_manager import MountDefinition, is_sensitive_path, list_mounts_for_owner

_WRITE_TOOLS = {"write_file", "edit_file"}
_READ_TOOLS = {"read_file", "ls", "grep", "glob"}
_REPEAT_BLOCK_WINDOW = 120
_REPEAT_BLOCKS: dict[str, list[float]] = {}
_AUDIT_FILE = os.path.join(DATA_DIR, "mount_audit.jsonl")


class MountAccessError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        mount: Optional[str] = None,
        virtual_path: str = "",
        blocked_policy: str = "",
        suggested_next_action: str = "",
        allowed_alternatives: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.mount = mount
        self.virtual_path = normalize_virtual_path(virtual_path)
        self.blocked_policy = blocked_policy
        self.suggested_next_action = suggested_next_action
        self.allowed_alternatives = allowed_alternatives or []
        self.retry_blocked = False

    def feedback(self) -> dict[str, Any]:
        data = {
            "reason_code": self.reason_code,
            "mount": self.mount,
            "virtual_path": self.virtual_path,
            "blocked_policy": self.blocked_policy,
            "suggested_next_action": self.suggested_next_action,
            "allowed_alternatives": self.allowed_alternatives,
        }
        if self.retry_blocked:
            data["retry_guidance"] = (
                "Do not retry this exact action. Explain the blocker or ask for a safer mount."
            )
        return {k: v for k, v in data.items() if v not in (None, "", [])}


def _audit_mount_event(
    *,
    owner: str | None,
    mount: str | None,
    virtual_path: str,
    tool: str | None,
    action: str,
    result: str,
    reason_code: str = "",
    byte_count: Optional[int] = None,
) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        row = {
            "ts": time.time(),
            "owner": owner or "default",
            "mount": mount,
            "virtual_path": normalize_virtual_path(virtual_path),
            "tool": tool,
            "action": action,
            "result": result,
            "reason_code": reason_code,
        }
        if byte_count is not None:
            row["byte_count"] = byte_count
        with open(_AUDIT_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def audit_mount_write_success(
    *,
    owner: str | None,
    mount: str | None,
    virtual_path: str,
    tool: str | None,
    byte_count: Optional[int] = None,
) -> None:
    _audit_mount_event(
        owner=owner,
        mount=mount,
        virtual_path=virtual_path,
        tool=tool,
        action="write",
        result="allowed",
        byte_count=byte_count,
    )


def _raise_blocked(
    message: str,
    *,
    reason_code: str,
    mount: Optional[MountDefinition] = None,
    owner: str | None = None,
    virtual_path: str = "",
    tool: Optional[str] = None,
    blocked_policy: str = "",
    suggested_next_action: str = "",
    allowed_alternatives: Optional[list[str]] = None,
) -> None:
    err = MountAccessError(
        message,
        reason_code=reason_code,
        mount=mount.name if mount else None,
        virtual_path=virtual_path,
        blocked_policy=blocked_policy,
        suggested_next_action=suggested_next_action,
        allowed_alternatives=allowed_alternatives,
    )
    key = "|".join([str(owner or "default"), str(tool or ""), reason_code, normalize_virtual_path(virtual_path)])
    now = time.time()
    bucket = _REPEAT_BLOCKS.setdefault(key, [])
    bucket[:] = [item for item in bucket if now - item < _REPEAT_BLOCK_WINDOW]
    bucket.append(now)
    err.retry_blocked = len(bucket) >= 2
    _audit_mount_event(
        owner=owner,
        mount=mount.name if mount else None,
        virtual_path=virtual_path,
        tool=tool,
        action="write" if tool in _WRITE_TOOLS else "read",
        result="blocked",
        reason_code=reason_code,
    )
    raise err

def normalize_virtual_path(raw_path: str) -> str:
    value = str(raw_path or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.endswith("/") and value != "/":
        value = value.rstrip("/")
    return value


def is_virtual_mount_path(raw_path: str) -> bool:
    value = normalize_virtual_path(raw_path)
    return value == "/mnt" or value.startswith("/mnt/")


def _contains(root: str, candidate: str) -> bool:
    nroot = os.path.normcase(os.path.realpath(root))
    ncandidate = os.path.normcase(os.path.realpath(candidate))
    if ncandidate == nroot:
        return True
    try:
        return os.path.commonpath([ncandidate, nroot]) == nroot
    except ValueError:
        return False


def _matching_mounts(path: str, owner: str | None) -> list[MountDefinition]:
    matches = []
    for mount in list_mounts_for_owner(owner):
        root = normalize_virtual_path(mount.virtual_path)
        if path == root or path.startswith(f"{root}/"):
            matches.append(mount)
    matches.sort(key=lambda item: len(item.virtual_path), reverse=True)
    return matches


def _is_reparse_or_symlink(path: str) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _reject_write_links(host_root: str, target: str, *, mount: MountDefinition, owner: str | None, virtual_path: str, tool: Optional[str]) -> None:
    current = os.path.realpath(host_root)
    target_real = os.path.realpath(target)
    if _is_reparse_or_symlink(current):
        _raise_blocked(
            "Writable mount root cannot be a symlink or reparse point.",
            reason_code="unsafe_reparse_point",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="write_policy.no_symlink_components",
            suggested_next_action="Ask an admin to mount a real directory, not a symlink or junction.",
        )
    rel = os.path.relpath(target_real, current)
    if rel == ".":
        return
    for part in rel.split(os.sep)[:-1]:
        current = os.path.join(current, part)
        if _is_reparse_or_symlink(current):
            _raise_blocked(
                "Writable mount path contains a symlink or reparse point.",
                reason_code="unsafe_reparse_point",
                mount=mount,
                owner=owner,
                virtual_path=virtual_path,
                tool=tool,
                blocked_policy="write_policy.no_symlink_components",
                suggested_next_action="Choose a normal directory inside the mount.",
            )
    if os.path.exists(target_real) and _is_reparse_or_symlink(target_real):
        _raise_blocked(
            "Writable mount target is a symlink or reparse point.",
            reason_code="unsafe_reparse_point",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="write_policy.no_symlink_targets",
            suggested_next_action="Choose a normal file path inside the mount.",
        )


def _check_write_target(
    mount: MountDefinition,
    target: str,
    *,
    owner: str | None,
    virtual_path: str,
    tool: Optional[str],
    content_size: Optional[int],
) -> None:
    policy = mount.write_policy
    if mount.read_only:
        _raise_blocked(
            f"Mount '{mount.name}' is read-only.",
            reason_code="mount_read_only",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="read_only",
            suggested_next_action="Use read_file/ls/grep/glob or ask an admin for a writable mount.",
            allowed_alternatives=sorted(_READ_TOOLS),
        )
    if not policy.enabled:
        _raise_blocked(
            f"Mount '{mount.name}' has no write policy enabled.",
            reason_code="write_policy_missing",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="write_policy.enabled=false",
            suggested_next_action="Ask an admin to enable write_policy for this mount.",
            allowed_alternatives=sorted(_READ_TOOLS),
        )
    if tool not in _WRITE_TOOLS:
        return
    if tool and mount.allowed_tools and tool not in mount.allowed_tools:
        _raise_blocked(
            f"Mount '{mount.name}' does not allow tool '{tool}'.",
            reason_code="tool_not_allowed",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="allowed_tools",
            suggested_next_action="Use one of the allowed tools for this mount.",
            allowed_alternatives=mount.allowed_tools,
        )
    if policy.create_only and os.path.exists(target):
        _raise_blocked(
            f"Mount '{mount.name}' only allows creating new files.",
            reason_code="create_only_overwrite",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="write_policy.create_only",
            suggested_next_action="Choose a new file path or ask an admin to disable create_only.",
        )
    ext = os.path.splitext(target)[1].lower()
    if ext not in set(policy.allowed_extensions):
        _raise_blocked(
            f"Extension '{ext or '(none)'}' is not allowed for writable mount '{mount.name}'.",
            reason_code="extension_not_allowed",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="write_policy.allowed_extensions",
            suggested_next_action="Choose an allowed text-file extension.",
            allowed_alternatives=policy.allowed_extensions,
        )
    if content_size is not None and content_size > policy.max_bytes:
        _raise_blocked(
            f"Write payload is larger than this mount allows.",
            reason_code="write_too_large",
            mount=mount,
            owner=owner,
            virtual_path=virtual_path,
            tool=tool,
            blocked_policy="write_policy.max_bytes",
            suggested_next_action="Write a smaller file or ask an admin to raise the mount limit.",
        )
    if os.path.exists(target):
        try:
            if os.path.getsize(target) > policy.max_bytes:
                _raise_blocked(
                    "Target file is larger than this mount allows.",
                    reason_code="target_too_large",
                    mount=mount,
                    owner=owner,
                    virtual_path=virtual_path,
                    tool=tool,
                    blocked_policy="write_policy.max_bytes",
                    suggested_next_action="Choose a smaller file or ask an admin to raise the mount limit.",
                )
        except OSError:
            pass
    _reject_write_links(mount.host_path, target, mount=mount, owner=owner, virtual_path=virtual_path, tool=tool)


def resolve_virtual_path(
    raw_path: str,
    *,
    owner: str | None,
    mode: str = "read",
    tool: Optional[str] = None,
    content_size: Optional[int] = None,
) -> str:
    path = normalize_virtual_path(raw_path)
    if not path:
        _raise_blocked(
            "path is required",
            reason_code="path_required",
            owner=owner,
            virtual_path=raw_path,
            tool=tool,
            suggested_next_action="Provide a virtual path under /mnt/name.",
        )
    if any(part in (".", "..") for part in path.split("/") if part):
        _raise_blocked(
            f"virtual path '{raw_path}' must not contain . or .. segments",
            reason_code="path_escapes_mount",
            owner=owner,
            virtual_path=raw_path,
            tool=tool,
            blocked_policy="virtual_path.no_dot_segments",
            suggested_next_action="Use a normalized path under /mnt/name without . or ...",
        )

    matches = _matching_mounts(path, owner)
    if not matches:
        _raise_blocked(
            f"virtual path '{raw_path}' is not mounted for this user",
            reason_code="mount_not_found",
            owner=owner,
            virtual_path=raw_path,
            tool=tool,
            blocked_policy="owner_scope",
            suggested_next_action="Ask the user/admin which mounted /mnt path to use.",
        )
    mount = matches[0]
    if tool and mount.allowed_tools and tool not in mount.allowed_tools:
        _raise_blocked(
            f"Mount '{mount.name}' does not allow tool '{tool}'.",
            reason_code="tool_not_allowed",
            mount=mount,
            owner=owner,
            virtual_path=path,
            tool=tool,
            blocked_policy="allowed_tools",
            suggested_next_action="Use one of the allowed tools for this mount.",
            allowed_alternatives=mount.allowed_tools,
        )

    suffix = path[len(mount.virtual_path):].lstrip("/")
    target = os.path.realpath(os.path.join(mount.host_path, *suffix.split("/"))) if suffix else mount.host_path
    if not _contains(mount.host_path, target):
        _raise_blocked(
            f"virtual path '{raw_path}' escapes mount '{mount.name}'",
            reason_code="path_escapes_mount",
            mount=mount,
            owner=owner,
            virtual_path=path,
            tool=tool,
            blocked_policy="mount_containment",
            suggested_next_action=f"Use a path under {mount.virtual_path}.",
        )
    if is_sensitive_path(target):
        _raise_blocked(
            f"virtual path '{raw_path}' resolves to a sensitive path",
            reason_code="sensitive_target",
            mount=mount,
            owner=owner,
            virtual_path=path,
            tool=tool,
            blocked_policy="sensitive_path",
            suggested_next_action="Choose a non-sensitive file inside the mount.",
        )
    if mode == "write":
        _check_write_target(mount, target, owner=owner, virtual_path=path, tool=tool, content_size=content_size)
    return target


def resolve_virtual_search_root(raw_path: str, *, owner: str | None, tool: Optional[str] = None) -> str:
    return resolve_virtual_path(raw_path, owner=owner, mode="read", tool=tool)


def verify_virtual_write_target(raw_path: str, *, owner: str | None, tool: Optional[str], target: str) -> None:
    path = normalize_virtual_path(raw_path)
    matches = _matching_mounts(path, owner)
    if not matches:
        _raise_blocked(
            f"virtual path '{raw_path}' is not mounted for this user",
            reason_code="mount_not_found",
            owner=owner,
            virtual_path=raw_path,
            tool=tool,
            blocked_policy="owner_scope",
            suggested_next_action="Ask the user/admin which mounted /mnt path to use.",
        )
    mount = matches[0]
    resolved = os.path.realpath(target)
    if not _contains(mount.host_path, resolved):
        _raise_blocked(
            f"virtual path '{raw_path}' escapes mount '{mount.name}'",
            reason_code="path_escapes_mount",
            mount=mount,
            owner=owner,
            virtual_path=path,
            tool=tool,
            blocked_policy="mount_containment",
            suggested_next_action=f"Use a path under {mount.virtual_path}.",
        )
    if is_sensitive_path(resolved):
        _raise_blocked(
            f"virtual path '{raw_path}' resolves to a sensitive path",
            reason_code="sensitive_target",
            mount=mount,
            owner=owner,
            virtual_path=path,
            tool=tool,
            blocked_policy="sensitive_path",
            suggested_next_action="Choose a non-sensitive file inside the mount.",
        )
    _reject_write_links(mount.host_path, resolved, mount=mount, owner=owner, virtual_path=path, tool=tool)


def virtual_metadata(raw_path: str, *, owner: str | None) -> dict[str, str]:
    path = normalize_virtual_path(raw_path)
    matches = _matching_mounts(path, owner)
    if not matches:
        return {"virtual_path": path}
    return {"mount": matches[0].name, "virtual_path": path}


def backup_virtual_target(raw_path: str, *, owner: str | None, target: str) -> Optional[str]:
    path = normalize_virtual_path(raw_path)
    matches = _matching_mounts(path, owner)
    if not matches or not matches[0].write_policy.backup or not os.path.exists(target):
        return None
    backup = f"{target}.bak"
    shutil.copy2(target, backup)
    return backup
