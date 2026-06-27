"""Operator-gated local Git repository provisioner for server projects."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from src.server_project_registry import ServerProjectRecord


_TRUE_VALUES = {"1", "true", "yes", "on"}
_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_DECISIONS = ("blocked", "hold", "plan_ready", "provisioned")
_REMOTE_PROVIDERS = ("none", "github", "gitea", "forgejo")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]?\s*\S*")
_SAFE_PROJECTS_ROOT_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]{1,80}$")
_SAFE_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
_MAX_TIMEOUT_SECONDS = 300


class ProjectRepoCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "ProjectRepoCommandResult":
        ...


class ServerProjectRepoProvisioningError(ValueError):
    """Raised when local project Git repository provisioning is unsafe."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectRepoProvisioningError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectRepoProvisioningError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectRepoProvisioningError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ServerProjectRepoProvisioningError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_branch(value: Any) -> str:
    branch = _normalize_text(value, field_name="default_branch", max_len=120)
    lowered = branch.lower()
    if not _SAFE_BRANCH_RE.fullmatch(branch) or branch.startswith(("-", "/")) or ".." in branch or lowered.endswith(".lock"):
        raise ServerProjectRepoProvisioningError("default_branch is not a safe Git branch name")
    return branch


def _normalize_namespace(value: Any, *, provider: str) -> str:
    namespace = _normalize_text(value, field_name="remote_namespace", allow_empty=True, max_len=80)
    if not namespace:
        return ""
    if not _SAFE_NAMESPACE_RE.fullmatch(namespace):
        raise ServerProjectRepoProvisioningError("remote_namespace must be a simple provider namespace")
    return namespace


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _resolve_projects_root(projects_root: str | Path) -> Path:
    root = Path(projects_root).expanduser()
    if str(root).strip() in {"", ".", ".."}:
        raise ServerProjectRepoProvisioningError("projects_root must be an explicit directory")
    if _SECRET_RE.search(str(root)):
        raise ServerProjectRepoProvisioningError("projects_root appears to contain secret material")
    if not root.is_absolute() and not _SAFE_PROJECTS_ROOT_NAME_RE.fullmatch(str(root)):
        raise ServerProjectRepoProvisioningError("relative projects_root must be a simple directory name")
    return root.resolve()


def _assert_child_path(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ServerProjectRepoProvisioningError("repo path escapes projects_root") from exc
    return resolved


def _validate_record(record: ServerProjectRecord) -> ServerProjectRecord:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectRepoProvisioningError("record must be a ServerProjectRecord")
    if record.project_spec.workspace_root != f"projects/{record.project_slug}":
        raise ServerProjectRepoProvisioningError("project workspace_root must match projects/<project-slug>")
    if record.project_spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectRepoProvisioningError("project repository must not be Odysseus")
    return record


def _redact_output(value: str) -> str:
    text = str(value or "")
    if _SECRET_RE.search(text):
        return "[redacted]"
    return text[:3000]


def _merge_env(extra_env: Mapping[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in extra_env.items():
        env[str(key)] = str(value)
    return env


@dataclass(frozen=True, slots=True)
class ProjectRepoCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": _redact_output(self.stdout),
            "stderr": _redact_output(self.stderr),
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProjectRepoProvisioningPlan:
    project_slug: str
    repo_name: str
    repo_directory: str
    default_branch: str
    remote_provider: str
    remote_namespace: str
    operator_decision: str
    live_enabled: bool
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[dict[str, Any], ...]
    provider_gate: str
    next_human_decision: str

    @property
    def can_execute_local_init(self) -> bool:
        return self.decision == "plan_ready" and self.live_enabled and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_name": self.repo_name,
            "repo_directory": self.repo_directory,
            "default_branch": self.default_branch,
            "remote_provider": self.remote_provider,
            "remote_namespace": self.remote_namespace,
            "operator_decision": self.operator_decision,
            "live_enabled": self.live_enabled,
            "can_execute_local_init": self.can_execute_local_init,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "provider_gate": self.provider_gate,
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class ProjectRepoProvisioningReport:
    status: str
    executed: bool
    plan: ProjectRepoProvisioningPlan
    command_result: ProjectRepoCommandResult | None
    written_files: tuple[str, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "command_result": self.command_result.to_dict() if self.command_result else None,
            "written_files": list(self.written_files),
            "blockers": list(self.blockers),
        }


def build_project_repo_provisioning_plan(
    *,
    record: ServerProjectRecord,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    remote_provider: Any = "none",
    remote_namespace: Any = "",
    default_branch: Any | None = None,
) -> ProjectRepoProvisioningPlan:
    safe_record = _validate_record(record)
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_PROJECT_REPO_PROVISIONER_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )
    provider = _normalize_choice(remote_provider, field_name="remote_provider", choices=_REMOTE_PROVIDERS)
    namespace = _normalize_namespace(remote_namespace, provider=provider)
    branch = _normalize_branch(default_branch if default_branch is not None else safe_record.project_spec.default_branch)
    repo_directory = f"{safe_record.project_spec.workspace_root}/repo"
    provider_gate = _provider_gate(provider=provider, namespace=namespace, repo_name=safe_record.project_spec.repo_name)

    blockers: list[str] = []
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")
    if not resolved_live:
        blockers.append("ODYSSEUS_PROJECT_REPO_PROVISIONER_LIVE_ENABLED is not enabled")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if provider != "none" and not namespace:
        blockers.append("remote_namespace is required for provider repo planning")

    if normalized_operator == "no_go":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    return ProjectRepoProvisioningPlan(
        project_slug=safe_record.project_slug,
        repo_name=safe_record.project_spec.repo_name,
        repo_directory=repo_directory,
        default_branch=branch,
        remote_provider=provider,
        remote_namespace=namespace,
        operator_decision=normalized_operator,
        live_enabled=resolved_live,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        planned_steps=(
            {
                "step_id": "verify_workspace_repo_directory",
                "summary": f"verify provisioned repo directory {repo_directory}",
                "executes": False,
            },
            {
                "step_id": "git_init",
                "summary": f"initialize local Git repository on branch {branch}",
                "executes": True,
            },
            {
                "step_id": "write_repo_marker",
                "summary": "write redacted local repo metadata marker",
                "executes": True,
            },
            {
                "step_id": "remote_provider_gate",
                "summary": provider_gate,
                "executes": False,
            },
        ),
        provider_gate=provider_gate,
        next_human_decision=_next_decision(decision, provider),
    )


def provision_project_local_git_repo(
    *,
    record: ServerProjectRecord,
    projects_root: str | Path,
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    remote_provider: Any = "none",
    remote_namespace: Any = "",
    default_branch: Any | None = None,
    command_runner: ProjectRepoCommandRunner | None = None,
) -> ProjectRepoProvisioningReport:
    plan = build_project_repo_provisioning_plan(
        record=record,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
        remote_provider=remote_provider,
        remote_namespace=remote_namespace,
        default_branch=default_branch,
    )
    if not plan.can_execute_local_init:
        return ProjectRepoProvisioningReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_result=None,
            written_files=(),
            blockers=plan.blockers,
        )

    root = _resolve_projects_root(projects_root)
    workspace_path = _assert_child_path(root, root / plan.project_slug)
    repo_path = _assert_child_path(root, workspace_path / "repo")
    if not repo_path.is_dir():
        return ProjectRepoProvisioningReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_result=None,
            written_files=(),
            blockers=("project workspace repo directory is missing; run workspace provisioning first",),
        )
    if (repo_path / ".git").exists():
        result = ProjectRepoCommandResult(exit_code=0, stdout="repository already initialized")
    else:
        runner = command_runner or run_git_subprocess_command
        result = runner(
            ("git", "init", "-b", plan.default_branch),
            cwd=repo_path,
            timeout_seconds=_MAX_TIMEOUT_SECONDS,
            env={},
        )
    if not result.ok:
        return ProjectRepoProvisioningReport(
            status="failed",
            executed=True,
            plan=plan,
            command_result=result,
            written_files=(),
            blockers=("git init failed",),
        )

    marker_path = repo_path / ".odysseus-repo.json"
    marker_path.write_text(
        json.dumps(
            {
                "schema": "odysseus.project_repo.v1",
                "project_slug": plan.project_slug,
                "repo_name": plan.repo_name,
                "default_branch": plan.default_branch,
                "remote_provider": plan.remote_provider,
                "remote_namespace": plan.remote_namespace,
                "provider_gate": plan.provider_gate,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ProjectRepoProvisioningReport(
        status="provisioned",
        executed=True,
        plan=plan,
        command_result=result,
        written_files=(f"{plan.repo_directory}/.odysseus-repo.json",),
        blockers=(),
    )


def run_git_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> ProjectRepoCommandResult:
    if not git_command_is_allowed(argv):
        raise ServerProjectRepoProvisioningError("unsupported Git provisioning command")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=_merge_env(env),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ProjectRepoCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return ProjectRepoCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def git_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if len(argv) == 4 and argv[:3] == ("git", "init", "-b"):
        _normalize_branch(argv[3])
        return True
    return False


def _provider_gate(*, provider: str, namespace: str, repo_name: str) -> str:
    if provider == "none":
        return "No remote provider selected; keep repository local until a provider gate is opened"
    target = f"{provider}/{namespace}/{repo_name}"
    return f"Remote provider requested for {target}; requires separate provider credentials, network Go and push policy review"


def _next_decision(decision: str, provider: str) -> str:
    if decision == "plan_ready" and provider == "none":
        return "Local Git repository can be initialized; choose a provider later if remote publication is needed."
    if decision == "plan_ready":
        return "Local Git repository can be initialized; remote provider creation remains a separate live Go."
    if decision == "blocked":
        return "Do not initialize the local repository until the operator decision changes."
    return "Provide live_enabled=true and operator_decision=go to initialize the local project Git repository."
