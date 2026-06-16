"""Small backend contract for workspace sandbox access policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, Iterable

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule


_MAX_TEXT = 120
_MAX_ROOT = 120
_ROOT_SLUG_RE = re.compile(r"[^a-z0-9._/-]+")


class WorkspacePolicyError(ValueError):
    """Raised when a workspace policy payload is invalid or unsafe."""


class WorkspaceAccessAction(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class WorkspaceAccessDecision:
    allowed: bool
    reason: str
    normalized_path: str
    warnings: tuple[str, ...]
    required_handoff: bool


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise WorkspacePolicyError(f"{field_name} must not be empty")
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 3] + "..."
    return text


def _normalize_root(value: Any, *, field_name: str, allow_empty: bool) -> str:
    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise WorkspacePolicyError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise WorkspacePolicyError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise WorkspacePolicyError(f"{field_name} must be repo-relative")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise WorkspacePolicyError(f"{field_name} must not contain traversal segments")
    normalized = "/".join(parts)
    normalized = _ROOT_SLUG_RE.sub("-", normalized)
    if len(normalized) > _MAX_ROOT:
        raise WorkspacePolicyError(f"{field_name} exceeds max length {_MAX_ROOT}")
    return normalized


def _normalize_path(value: Any, *, field_name: str) -> str:
    return _normalize_root(value, field_name=field_name, allow_empty=False)


def _normalize_path_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_path(value, field_name=field_name)
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    if not allow_empty and not normalized:
        raise WorkspacePolicyError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


def _has_prefix(path: str, root: str) -> bool:
    if not root:
        return True
    return path == root or path.startswith(f"{root}/")


def _matches_any(path: str, roots: tuple[str, ...]) -> bool:
    return any(_has_prefix(path, root) for root in roots)


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    workspace_root: str
    system_root: str
    writable_roots: tuple[str, ...]
    blocked_roots: tuple[str, ...]
    hot_files: tuple[str, ...]
    agent_owned_files: tuple[str, ...]
    deletable_files: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        workspace_root: str,
        system_root: str,
        writable_roots: Iterable[Any],
        blocked_roots: Iterable[Any],
        hot_files: Iterable[Any],
        agent_owned_files: Iterable[Any],
        deletable_files: Iterable[Any] = (),
    ) -> "WorkspacePolicy":
        workspace = _normalize_root(workspace_root, field_name="workspace_root", allow_empty=True)
        system = _normalize_root(system_root, field_name="system_root", allow_empty=False)
        writable = _normalize_path_list(writable_roots, field_name="writable_root", allow_empty=False)
        blocked = _normalize_path_list(blocked_roots, field_name="blocked_root", allow_empty=True)
        hot = _normalize_path_list(hot_files, field_name="hot_file", allow_empty=True)
        owned = _normalize_path_list(agent_owned_files, field_name="agent_owned_file", allow_empty=True)
        deletable = _normalize_path_list(deletable_files, field_name="deletable_file", allow_empty=True)
        return cls(
            workspace_root=workspace,
            system_root=system,
            writable_roots=writable,
            blocked_roots=blocked,
            hot_files=hot,
            agent_owned_files=owned,
            deletable_files=deletable,
        )

    def decide(
        self,
        *,
        agent_identity: AgentIdentity,
        context_capsule: ContextCapsule,
        action: WorkspaceAccessAction | str,
        path: str,
    ) -> WorkspaceAccessDecision:
        if not isinstance(agent_identity, AgentIdentity):
            raise WorkspacePolicyError("agent_identity must be an AgentIdentity")
        if not isinstance(context_capsule, ContextCapsule):
            raise WorkspacePolicyError("context_capsule must be a ContextCapsule")

        normalized_action = action if isinstance(action, WorkspaceAccessAction) else WorkspaceAccessAction(str(action))
        normalized_path = _normalize_path(path, field_name="path")
        warnings: list[str] = []

        if not _has_prefix(normalized_path, self.workspace_root):
            return WorkspaceAccessDecision(
                allowed=False,
                reason="outside_workspace_root",
                normalized_path=normalized_path,
                warnings=(),
                required_handoff=True,
            )

        if _has_prefix(normalized_path, self.system_root):
            warnings.append("touches_system_root")

        if _matches_any(normalized_path, self.blocked_roots):
            return WorkspaceAccessDecision(
                allowed=False,
                reason="blocked_root",
                normalized_path=normalized_path,
                warnings=tuple(warnings),
                required_handoff=True,
            )

        if normalized_path in context_capsule.blocked_files:
            return WorkspaceAccessDecision(
                allowed=False,
                reason="capsule_blocked_file",
                normalized_path=normalized_path,
                warnings=tuple(warnings),
                required_handoff=True,
            )

        if normalized_action == WorkspaceAccessAction.READ:
            if normalized_path in context_capsule.allowed_files:
                return WorkspaceAccessDecision(
                    allowed=True,
                    reason="capsule_allowed_read",
                    normalized_path=normalized_path,
                    warnings=tuple(warnings),
                    required_handoff=False,
                )
            return WorkspaceAccessDecision(
                allowed=False,
                reason="read_outside_capsule_scope",
                normalized_path=normalized_path,
                warnings=tuple(warnings),
                required_handoff=True,
            )

        if normalized_path in self.hot_files and normalized_path not in self.agent_owned_files:
            return WorkspaceAccessDecision(
                allowed=False,
                reason="hot_file_conflict",
                normalized_path=normalized_path,
                warnings=tuple(warnings),
                required_handoff=True,
            )

        if normalized_action == WorkspaceAccessAction.DELETE:
            if normalized_path not in self.deletable_files:
                return WorkspaceAccessDecision(
                    allowed=False,
                    reason="delete_not_explicitly_allowed",
                    normalized_path=normalized_path,
                    warnings=tuple(warnings),
                    required_handoff=True,
                )

        if not _matches_any(normalized_path, self.writable_roots):
            return WorkspaceAccessDecision(
                allowed=False,
                reason="outside_writable_roots",
                normalized_path=normalized_path,
                warnings=tuple(warnings),
                required_handoff=True,
            )

        if normalized_path not in context_capsule.allowed_files:
            return WorkspaceAccessDecision(
                allowed=False,
                reason="write_outside_capsule_scope",
                normalized_path=normalized_path,
                warnings=tuple(warnings),
                required_handoff=True,
            )

        return WorkspaceAccessDecision(
            allowed=True,
            reason=f"{normalized_action.value}_allowed",
            normalized_path=normalized_path,
            warnings=tuple(warnings),
            required_handoff=False,
        )
