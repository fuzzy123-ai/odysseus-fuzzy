"""Operator-gated push runner for autonomous server project repositories."""

from __future__ import annotations

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
_DECISIONS = ("blocked", "hold", "plan_ready", "pushed", "failed")
_REMOTE_ALLOWLIST = ("fuzzy",)
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_SAFE_PROJECTS_ROOT_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]{1,80}$")
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_MAX_TIMEOUT_SECONDS = 300


class ProjectPushCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "ProjectPushCommandResult":
        ...


class ServerProjectPushRunnerError(ValueError):
    """Raised when a project push request is unsafe."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectPushRunnerError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectPushRunnerError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectPushRunnerError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ServerProjectPushRunnerError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_remote(value: Any) -> str:
    remote = _normalize_text(value, field_name="remote_name", max_len=40)
    if remote not in _REMOTE_ALLOWLIST:
        raise ServerProjectPushRunnerError("push remote must be fuzzy; origin is blocked")
    return remote


def _normalize_branch(value: Any) -> str:
    branch = _normalize_text(value, field_name="branch", max_len=120)
    lowered = branch.lower()
    if (
        not _SAFE_BRANCH_RE.fullmatch(branch)
        or branch.startswith(("-", "/"))
        or ".." in branch
        or lowered.endswith(".lock")
    ):
        raise ServerProjectPushRunnerError("branch is not a safe Git branch name")
    if not branch.startswith(("project/", "codex/", "release/", "dev", "main")):
        raise ServerProjectPushRunnerError("branch must use project/, codex/, release/, dev or main")
    return branch


def _normalize_commit_ref(value: Any) -> str:
    ref = _normalize_text(value, field_name="commit_ref", max_len=80)
    if not re.fullmatch(r"[A-Fa-f0-9]{7,40}", ref):
        raise ServerProjectPushRunnerError("commit_ref must be a 7-40 character Git hash")
    return ref.lower()


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _resolve_projects_root(projects_root: str | Path) -> Path:
    root = Path(projects_root).expanduser()
    if str(root).strip() in {"", ".", ".."}:
        raise ServerProjectPushRunnerError("projects_root must be an explicit directory")
    if _SECRET_RE.search(str(root)):
        raise ServerProjectPushRunnerError("projects_root appears to contain secret material")
    if not root.is_absolute() and not _SAFE_PROJECTS_ROOT_NAME_RE.fullmatch(str(root)):
        raise ServerProjectPushRunnerError("relative projects_root must be a simple directory name")
    return root.resolve()


def _assert_child_path(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ServerProjectPushRunnerError("push path escapes project repository") from exc
    return resolved


def _validate_record(record: ServerProjectRecord) -> ServerProjectRecord:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectPushRunnerError("record must be a ServerProjectRecord")
    if record.project_spec.workspace_root != f"projects/{record.project_slug}":
        raise ServerProjectPushRunnerError("project workspace_root must match projects/<project-slug>")
    if record.project_spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectPushRunnerError("project repository must not be Odysseus")
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
class ProjectPushCommandResult:
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
class ProjectPushPlan:
    project_slug: str
    repo_directory: str
    remote_name: str
    branch: str
    commit_ref: str
    commit_confirmed: bool
    operator_decision: str
    live_enabled: bool
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[dict[str, Any], ...]
    next_human_decision: str

    @property
    def can_push(self) -> bool:
        return self.decision == "plan_ready" and self.live_enabled and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_directory": self.repo_directory,
            "remote_name": self.remote_name,
            "branch": self.branch,
            "commit_ref": self.commit_ref,
            "commit_confirmed": self.commit_confirmed,
            "operator_decision": self.operator_decision,
            "live_enabled": self.live_enabled,
            "can_push": self.can_push,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class ProjectPushReport:
    status: str
    executed: bool
    plan: ProjectPushPlan
    command_results: tuple[ProjectPushCommandResult, ...]
    pushed_ref: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "command_results": [result.to_dict() for result in self.command_results],
            "pushed_ref": self.pushed_ref,
            "blockers": list(self.blockers),
        }


def build_project_push_plan(
    *,
    record: ServerProjectRecord,
    branch: Any,
    commit_ref: Any,
    commit_confirmed: bool,
    remote_name: Any = "fuzzy",
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> ProjectPushPlan:
    safe_record = _validate_record(record)
    normalized_remote = _normalize_remote(remote_name)
    normalized_branch = _normalize_branch(branch)
    normalized_ref = _normalize_commit_ref(commit_ref)
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_PROJECT_PUSH_RUNNER_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )

    blockers: list[str] = []
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")
    if not resolved_live:
        blockers.append("ODYSSEUS_PROJECT_PUSH_RUNNER_LIVE_ENABLED is not enabled")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if not commit_confirmed:
        blockers.append("local commit must be confirmed before push")

    if normalized_operator == "no_go":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    repo_directory = f"{safe_record.project_spec.workspace_root}/repo"
    return ProjectPushPlan(
        project_slug=safe_record.project_slug,
        repo_directory=repo_directory,
        remote_name=normalized_remote,
        branch=normalized_branch,
        commit_ref=normalized_ref,
        commit_confirmed=bool(commit_confirmed),
        operator_decision=normalized_operator,
        live_enabled=resolved_live,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        planned_steps=(
            {
                "step_id": "git_status",
                "summary": "capture local project repository status before push",
                "executes": True,
            },
            {
                "step_id": "git_push",
                "summary": f"push {normalized_branch} to {normalized_remote}",
                "executes": True,
            },
        ),
        next_human_decision=_next_decision(decision),
    )


def run_project_push(
    *,
    record: ServerProjectRecord,
    projects_root: str | Path,
    branch: Any,
    commit_ref: Any,
    commit_confirmed: bool,
    remote_name: Any = "fuzzy",
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    command_runner: ProjectPushCommandRunner | None = None,
) -> ProjectPushReport:
    plan = build_project_push_plan(
        record=record,
        branch=branch,
        commit_ref=commit_ref,
        commit_confirmed=commit_confirmed,
        remote_name=remote_name,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
    )
    if not plan.can_push:
        return ProjectPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(),
            pushed_ref="",
            blockers=plan.blockers,
        )

    root = _resolve_projects_root(projects_root)
    workspace_path = _assert_child_path(root, root / plan.project_slug)
    repo_path = _assert_child_path(root, workspace_path / "repo")
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return ProjectPushReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(),
            pushed_ref="",
            blockers=("project local Git repository is missing; run repo provisioning first",),
        )

    runner = command_runner or run_git_push_subprocess_command
    commands = (
        ("git", "status", "--short", "--branch"),
        ("git", "push", plan.remote_name, plan.branch),
    )
    results: list[ProjectPushCommandResult] = []
    for command in commands:
        result = runner(command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
        results.append(result)
        if not result.ok:
            return ProjectPushReport(
                status="failed",
                executed=True,
                plan=plan,
                command_results=tuple(results),
                pushed_ref="",
                blockers=(f"push command failed: {' '.join(command[:2])}",),
            )

    return ProjectPushReport(
        status="pushed",
        executed=True,
        plan=plan,
        command_results=tuple(results),
        pushed_ref=f"{plan.remote_name}/{plan.branch}@{plan.commit_ref}",
        blockers=(),
    )


def run_git_push_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> ProjectPushCommandResult:
    if not project_push_command_is_allowed(argv):
        raise ServerProjectPushRunnerError("unsupported project push command")
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
        return ProjectPushCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return ProjectPushCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def project_push_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if len(argv) == 4 and argv[:2] == ("git", "push"):
        _normalize_remote(argv[2])
        _normalize_branch(argv[3])
        return True
    return False


def _next_decision(decision: str) -> str:
    if decision == "plan_ready":
        return "Project branch can be pushed to fuzzy; deploy and exposure remain separate gated actions."
    if decision == "blocked":
        return "Do not push until the blocked operator or remote decision changes."
    return "Provide a confirmed commit, live_enabled=true and operator_decision=go before pushing."
