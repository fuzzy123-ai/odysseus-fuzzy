"""Resolve owner-scoped virtual mount paths into confined host paths."""

from __future__ import annotations

import os
from typing import Optional

from core.mount_manager import MountDefinition, is_sensitive_path, list_mounts_for_owner


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


def resolve_virtual_path(
    raw_path: str,
    *,
    owner: str | None,
    mode: str = "read",
    tool: Optional[str] = None,
) -> str:
    path = normalize_virtual_path(raw_path)
    if not path:
        raise ValueError("path is required")
    if any(part in (".", "..") for part in path.split("/") if part):
        raise ValueError(f"virtual path '{raw_path}' must not contain . or .. segments")

    matches = _matching_mounts(path, owner)
    if not matches:
        raise ValueError(f"virtual path '{raw_path}' is not mounted for this user")
    mount = matches[0]
    if mode == "write" and mount.read_only:
        raise ValueError(f"mount '{mount.name}' is read-only")
    if tool and mount.allowed_tools and tool not in mount.allowed_tools:
        raise ValueError(f"mount '{mount.name}' does not allow tool '{tool}'")

    suffix = path[len(mount.virtual_path):].lstrip("/")
    target = os.path.realpath(os.path.join(mount.host_path, *suffix.split("/"))) if suffix else mount.host_path
    if not _contains(mount.host_path, target):
        raise ValueError(f"virtual path '{raw_path}' escapes mount '{mount.name}'")
    if is_sensitive_path(target):
        raise ValueError(f"virtual path '{raw_path}' resolves to a sensitive path")
    return target


def resolve_virtual_search_root(raw_path: str, *, owner: str | None, tool: Optional[str] = None) -> str:
    return resolve_virtual_path(raw_path, owner=owner, mode="read", tool=tool)
