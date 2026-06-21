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


class WorkspaceIsolationMode(StrEnum):
    SHARED_READONLY = "shared_readonly"
    BRANCH = "branch"
    WORKTREE = "worktree"


@dataclass(frozen=True, slots=True)
class WorkspaceAccessDecision:
    allowed: bool
    reason: str
    normalized_path: str
    warnings: tuple[str, ...]
    required_handoff: bool


@dataclass(frozen=True, slots=True)
class WorkerWorkspaceAssignment:
    agent_identity: AgentIdentity
    plan_id: str
    node_id: str
    isolation_mode: WorkspaceIsolationMode
    integration_base_branch: str
    worker_branch: str
    worker_workspace_root: str
    owned_files: tuple[str, ...]
    blocked_files: tuple[str, ...]
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        agent_identity: AgentIdentity,
        plan_id: Any,
        node_id: Any,
        isolation_mode: WorkspaceIsolationMode | str,
        integration_base_branch: Any,
        worker_branch: Any,
        worker_workspace_root: Any = "",
        owned_files: Iterable[Any],
        blocked_files: Iterable[Any] = (),
        created_at: Any,
    ) -> "WorkerWorkspaceAssignment":
        if not isinstance(agent_identity, AgentIdentity):
            raise WorkspacePolicyError("agent_identity must be an AgentIdentity")
        mode = (
            isolation_mode
            if isinstance(isolation_mode, WorkspaceIsolationMode)
            else WorkspaceIsolationMode(str(isolation_mode))
        )
        owned = _normalize_path_list(
            owned_files,
            field_name="owned_file",
            allow_empty=mode == WorkspaceIsolationMode.SHARED_READONLY,
        )
        blocked = _normalize_path_list(blocked_files, field_name="blocked_file", allow_empty=True)
        overlap = sorted(set(owned) & set(blocked))
        if overlap:
            raise WorkspacePolicyError(f"owned_files and blocked_files overlap: {', '.join(overlap)}")
        base_branch = _normalize_branch(integration_base_branch, field_name="integration_base_branch")
        branch = _normalize_branch(worker_branch, field_name="worker_branch")
        workspace_root = _normalize_root(worker_workspace_root, field_name="worker_workspace_root", allow_empty=True)
        if mode == WorkspaceIsolationMode.SHARED_READONLY and (branch != base_branch or workspace_root or owned):
            raise WorkspacePolicyError("shared_readonly assignments must not claim a worker branch, workspace root, or owned files")
        if mode == WorkspaceIsolationMode.BRANCH and branch == base_branch:
            raise WorkspacePolicyError("branch assignments require a worker branch distinct from the integration base")
        if mode == WorkspaceIsolationMode.WORKTREE:
            if branch == base_branch:
                raise WorkspacePolicyError("worktree assignments require a worker branch distinct from the integration base")
            if not workspace_root:
                raise WorkspacePolicyError("worktree assignments require worker_workspace_root")
        return cls(
            agent_identity=agent_identity,
            plan_id=_normalize_root(plan_id, field_name="plan_id", allow_empty=False),
            node_id=_normalize_root(node_id, field_name="node_id", allow_empty=False),
            isolation_mode=mode,
            integration_base_branch=base_branch,
            worker_branch=branch,
            worker_workspace_root=workspace_root,
            owned_files=owned,
            blocked_files=blocked,
            created_at=_normalize_text(created_at, field_name="created_at", allow_empty=False),
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_identity.agent_id,
            "run_id": self.agent_identity.run_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "isolation_mode": self.isolation_mode.value,
            "integration_base_branch": self.integration_base_branch,
            "worker_branch": self.worker_branch,
            "worker_workspace_root": self.worker_workspace_root,
            "owned_file_count": len(self.owned_files),
            "blocked_file_count": len(self.blocked_files),
        }


@dataclass(frozen=True, slots=True)
class WorkspaceIntegrationDecision:
    allowed: bool
    reason: str
    blocking_files: tuple[str, ...]
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


def _normalize_branch(value: Any, *, field_name: str) -> str:
    normalized = _normalize_root(value, field_name=field_name, allow_empty=False)
    if normalized.endswith(".lock"):
        raise WorkspacePolicyError(f"{field_name} must not end with .lock")
    return normalized


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


def evaluate_workspace_integration_gate(
    assignment: WorkerWorkspaceAssignment,
    *,
    target_branch: Any,
    changed_files: Iterable[Any],
    dirty_files: Iterable[Any] = (),
    tests_passed: bool,
    gates_verified: bool,
) -> WorkspaceIntegrationDecision:
    if not isinstance(assignment, WorkerWorkspaceAssignment):
        raise WorkspacePolicyError("assignment must be a WorkerWorkspaceAssignment")
    normalized_target = _normalize_branch(target_branch, field_name="target_branch")
    changed = _normalize_path_list(changed_files, field_name="changed_file", allow_empty=False)
    dirty = _normalize_path_list(dirty_files, field_name="dirty_file", allow_empty=True)
    warnings: list[str] = []

    if assignment.isolation_mode == WorkspaceIsolationMode.SHARED_READONLY:
        return WorkspaceIntegrationDecision(
            allowed=False,
            reason="read_only_assignment_cannot_integrate",
            blocking_files=(),
            warnings=(),
            required_handoff=True,
        )
    if normalized_target != assignment.integration_base_branch:
        return WorkspaceIntegrationDecision(
            allowed=False,
            reason="wrong_integration_branch",
            blocking_files=(),
            warnings=(),
            required_handoff=True,
        )

    owned = set(assignment.owned_files)
    blocked = set(assignment.blocked_files)
    out_of_scope = tuple(path for path in changed if path not in owned)
    blocked_changes = tuple(path for path in changed if path in blocked)
    unrelated_dirty = tuple(path for path in dirty if path not in owned)
    if out_of_scope or blocked_changes or unrelated_dirty:
        return WorkspaceIntegrationDecision(
            allowed=False,
            reason=_integration_scope_reason(
                out_of_scope=out_of_scope,
                blocked_changes=blocked_changes,
                unrelated_dirty=unrelated_dirty,
            ),
            blocking_files=tuple(dict.fromkeys((*out_of_scope, *blocked_changes, *unrelated_dirty))),
            warnings=tuple(warnings),
            required_handoff=True,
        )
    if not tests_passed:
        return WorkspaceIntegrationDecision(
            allowed=False,
            reason="tests_not_passed",
            blocking_files=(),
            warnings=tuple(warnings),
            required_handoff=True,
        )
    if not gates_verified:
        return WorkspaceIntegrationDecision(
            allowed=False,
            reason="quality_gates_not_verified",
            blocking_files=(),
            warnings=tuple(warnings),
            required_handoff=True,
        )
    if assignment.isolation_mode == WorkspaceIsolationMode.BRANCH:
        warnings.append("branch_isolation_requires_clean_shared_worktree")
    return WorkspaceIntegrationDecision(
        allowed=True,
        reason="integration_allowed",
        blocking_files=(),
        warnings=tuple(warnings),
        required_handoff=False,
    )


def _integration_scope_reason(
    *,
    out_of_scope: tuple[str, ...],
    blocked_changes: tuple[str, ...],
    unrelated_dirty: tuple[str, ...],
) -> str:
    if blocked_changes:
        return "blocked_file_changed"
    if unrelated_dirty:
        return "unrelated_dirty_files"
    if out_of_scope:
        return "changed_files_outside_assignment"
    return "scope_blocked"
