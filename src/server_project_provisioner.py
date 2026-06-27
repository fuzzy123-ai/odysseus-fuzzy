"""Operator-gated workspace provisioner for universal server projects."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.agent_identity import AgentIdentity
from src.server_project_registry import ServerProjectRecord
from src.workspace_policy import WorkerWorkspaceAssignment, WorkspaceIsolationMode


_TRUE_VALUES = {"1", "true", "yes", "on"}
_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_DECISIONS = ("blocked", "hold", "plan_ready", "provisioned")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]?\s*\S*")
_SAFE_PROJECTS_ROOT_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]{1,80}$")


class ServerProjectProvisioningError(ValueError):
    """Raised when project workspace provisioning is unsafe or invalid."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectProvisioningError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectProvisioningError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectProvisioningError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ServerProjectProvisioningError(f"unsupported {field_name}: {value!r}")
    return text


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _resolve_projects_root(projects_root: str | Path) -> Path:
    root = Path(projects_root).expanduser()
    if str(root).strip() in {"", ".", ".."}:
        raise ServerProjectProvisioningError("projects_root must be an explicit directory")
    if _SECRET_RE.search(str(root)):
        raise ServerProjectProvisioningError("projects_root appears to contain secret material")
    if not root.is_absolute() and not _SAFE_PROJECTS_ROOT_NAME_RE.fullmatch(str(root)):
        raise ServerProjectProvisioningError("relative projects_root must be a simple directory name")
    return root.resolve()


def _validate_record(record: ServerProjectRecord) -> ServerProjectRecord:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectProvisioningError("record must be a ServerProjectRecord")
    expected_workspace = f"projects/{record.project_slug}"
    if record.project_spec.workspace_root != expected_workspace:
        raise ServerProjectProvisioningError("project workspace_root must match projects/<project-slug>")
    if record.project_spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectProvisioningError("project repository must not be Odysseus")
    if record.chat_scope != f"project:{record.project_slug}":
        raise ServerProjectProvisioningError("project chat_scope must match project:<project-slug>")
    return record


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _normalize_text(value, field_name="path", max_len=160)
        if "\\" in text or text.startswith("/") or ".." in text.split("/"):
            raise ServerProjectProvisioningError("path must be repo-relative and use forward slashes")
        if text not in result:
            result.append(text)
    return tuple(result)


def _assert_child_path(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ServerProjectProvisioningError("workspace path escapes projects_root") from exc
    return resolved


def _assignment(record: ServerProjectRecord, *, created_at: str) -> WorkerWorkspaceAssignment:
    run_slug = record.project_slug[:60].strip("-") or "project"
    scope_slug = record.project_slug[:72].strip("-") or "project"
    identity = AgentIdentity.create(
        agent_id="odysseus-project-runner",
        role_id="project-developer",
        project_id=record.project_slug,
        memory_scope=f"project-{scope_slug}",
        workspace_scope=record.project_slug,
        run_id=f"provision-{run_slug}",
    )
    return WorkerWorkspaceAssignment.create(
        agent_identity=identity,
        plan_id=f"project-{record.project_slug}",
        node_id="workspace-provisioning",
        isolation_mode=WorkspaceIsolationMode.WORKTREE,
        integration_base_branch=record.project_spec.default_branch,
        worker_branch=f"project/{record.project_slug}/work",
        worker_workspace_root=record.project_spec.workspace_root,
        owned_files=(record.project_spec.workspace_root,),
        blocked_files=(".git", ".env", f"{record.project_spec.workspace_root}/.env"),
        created_at=created_at,
    )


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceProvisioningPlan:
    project_slug: str
    repo_name: str
    workspace_root: str
    repo_directory: str
    metadata_directory: str
    assignment: WorkerWorkspaceAssignment
    operator_decision: str
    live_enabled: bool
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[dict[str, Any], ...]
    next_human_decision: str

    @property
    def can_execute(self) -> bool:
        return self.decision == "plan_ready" and self.live_enabled and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_name": self.repo_name,
            "workspace_root": self.workspace_root,
            "repo_directory": self.repo_directory,
            "metadata_directory": self.metadata_directory,
            "assignment": self.assignment.audit_summary(),
            "operator_decision": self.operator_decision,
            "live_enabled": self.live_enabled,
            "can_execute": self.can_execute,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceProvisioningReport:
    status: str
    executed: bool
    plan: ProjectWorkspaceProvisioningPlan
    created_paths: tuple[str, ...]
    reused_paths: tuple[str, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "created_paths": list(self.created_paths),
            "reused_paths": list(self.reused_paths),
            "blockers": list(self.blockers),
        }


def build_project_workspace_provisioning_plan(
    *,
    record: ServerProjectRecord,
    created_at: Any,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> ProjectWorkspaceProvisioningPlan:
    safe_record = _validate_record(record)
    timestamp = _normalize_text(created_at, field_name="created_at", max_len=40)
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_PROJECT_PROVISIONER_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )
    workspace_root = safe_record.project_spec.workspace_root
    repo_directory = f"{workspace_root}/repo"
    metadata_directory = f"{workspace_root}/.odysseus"
    assignment = _assignment(safe_record, created_at=timestamp)

    blockers: list[str] = []
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")
    if not resolved_live:
        blockers.append("ODYSSEUS_PROJECT_PROVISIONER_LIVE_ENABLED is not enabled")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")

    if normalized_operator == "no_go":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    return ProjectWorkspaceProvisioningPlan(
        project_slug=safe_record.project_slug,
        repo_name=safe_record.project_spec.repo_name,
        workspace_root=workspace_root,
        repo_directory=repo_directory,
        metadata_directory=metadata_directory,
        assignment=assignment,
        operator_decision=normalized_operator,
        live_enabled=resolved_live,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        planned_steps=(
            {
                "step_id": "resolve_projects_root",
                "summary": "resolve configured server projects root without persisting the host path",
                "executes": False,
            },
            {
                "step_id": "create_project_workspace",
                "summary": f"create workspace {workspace_root}",
                "executes": True,
            },
            {
                "step_id": "create_repo_directory",
                "summary": f"create local repo directory {repo_directory}",
                "executes": True,
            },
            {
                "step_id": "write_project_metadata",
                "summary": f"write redacted project metadata under {metadata_directory}",
                "executes": True,
            },
        ),
        next_human_decision=_next_decision(decision),
    )


def provision_project_workspace(
    *,
    record: ServerProjectRecord,
    projects_root: str | Path,
    created_at: Any,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> ProjectWorkspaceProvisioningReport:
    plan = build_project_workspace_provisioning_plan(
        record=record,
        created_at=created_at,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
    )
    if not plan.can_execute:
        return ProjectWorkspaceProvisioningReport(
            status="blocked",
            executed=False,
            plan=plan,
            created_paths=(),
            reused_paths=(),
            blockers=plan.blockers,
        )

    root = _resolve_projects_root(projects_root)
    workspace_path = _assert_child_path(root, root / plan.project_slug)
    repo_path = _assert_child_path(root, workspace_path / "repo")
    metadata_path = _assert_child_path(root, workspace_path / ".odysseus")
    paths = (
        (workspace_path, plan.workspace_root),
        (repo_path, plan.repo_directory),
        (metadata_path, plan.metadata_directory),
    )
    created: list[str] = []
    reused: list[str] = []
    for path, label in paths:
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        (reused if existed else created).append(label)

    marker_path = metadata_path / "project.json"
    marker = {
        "schema": "odysseus.project_workspace.v1",
        "project_slug": plan.project_slug,
        "repo_name": plan.repo_name,
        "chat_scope": record.chat_scope,
        "project_type": record.project_spec.project_type,
        "workspace_root": plan.workspace_root,
        "repo_directory": plan.repo_directory,
        "default_branch": record.project_spec.default_branch,
        "worker_branch": plan.assignment.worker_branch,
        "created_at": _normalize_text(created_at, field_name="created_at", max_len=40),
    }
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    created_paths = _dedupe(created)
    reused_paths = _dedupe(reused)
    return ProjectWorkspaceProvisioningReport(
        status="provisioned",
        executed=True,
        plan=plan,
        created_paths=created_paths,
        reused_paths=reused_paths,
        blockers=(),
    )


def _next_decision(decision: str) -> str:
    if decision == "plan_ready":
        return "Workspace may be provisioned locally; keep remote repo/provider creation in the separate Git gate."
    if decision == "blocked":
        return "Do not provision this project workspace until the operator decision changes."
    return "Provide live_enabled=true and operator_decision=go to create the local project workspace."
