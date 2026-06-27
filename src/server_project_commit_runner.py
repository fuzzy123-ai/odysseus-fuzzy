"""Operator-gated local commit runner for autonomous server projects."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Protocol

from src.server_project_registry import ServerProjectRecord


_TRUE_VALUES = {"1", "true", "yes", "on"}
_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_DECISIONS = ("blocked", "hold", "plan_ready", "committed", "failed")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_SAFE_PROJECTS_ROOT_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]{1,80}$")
_MAX_TIMEOUT_SECONDS = 300


class ProjectCommitCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "ProjectCommitCommandResult":
        ...


class ServerProjectCommitRunnerError(ValueError):
    """Raised when a local project commit request is unsafe."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectCommitRunnerError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectCommitRunnerError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectCommitRunnerError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ServerProjectCommitRunnerError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ServerProjectCommitRunnerError(f"{field_name} must not be empty")
    if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ServerProjectCommitRunnerError(f"{field_name} must be repo-relative and use forward slashes")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ServerProjectCommitRunnerError(f"{field_name} must not contain traversal segments")
    path = "/".join(parts)
    lowered = path.lower()
    if lowered == ".git" or lowered.startswith(".git/") or lowered.endswith("/.env") or lowered == ".env":
        raise ServerProjectCommitRunnerError(f"{field_name} targets a blocked project path")
    if len(path) > 180:
        raise ServerProjectCommitRunnerError(f"{field_name} exceeds max length 180")
    return path


def _dedupe_paths(values: Iterable[Any]) -> tuple[str, ...]:
    paths: list[str] = []
    for value in values:
        path = _normalize_repo_path(value, field_name="changed_path")
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _resolve_projects_root(projects_root: str | Path) -> Path:
    root = Path(projects_root).expanduser()
    if str(root).strip() in {"", ".", ".."}:
        raise ServerProjectCommitRunnerError("projects_root must be an explicit directory")
    if _SECRET_RE.search(str(root)):
        raise ServerProjectCommitRunnerError("projects_root appears to contain secret material")
    if not root.is_absolute() and not _SAFE_PROJECTS_ROOT_NAME_RE.fullmatch(str(root)):
        raise ServerProjectCommitRunnerError("relative projects_root must be a simple directory name")
    return root.resolve()


def _assert_child_path(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ServerProjectCommitRunnerError("commit path escapes project repository") from exc
    return resolved


def _validate_record(record: ServerProjectRecord) -> ServerProjectRecord:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectCommitRunnerError("record must be a ServerProjectRecord")
    if record.project_spec.workspace_root != f"projects/{record.project_slug}":
        raise ServerProjectCommitRunnerError("project workspace_root must match projects/<project-slug>")
    if record.project_spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectCommitRunnerError("project repository must not be Odysseus")
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


def _default_commit_message(*, objective: str, project_slug: str) -> str:
    normalized = _normalize_text(objective, field_name="objective", max_len=80).lower()
    normalized = re.sub(r"[^a-z0-9 ._/-]+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ._/-")
    if not normalized:
        normalized = f"update {project_slug}"
    return f"feat: {normalized}"[:120]


@dataclass(frozen=True, slots=True)
class ProjectCommitCommandResult:
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
class ProjectCommitPlan:
    project_slug: str
    repo_directory: str
    objective: str
    changed_paths: tuple[str, ...]
    commit_message: str
    checks_passed: bool
    push_remote: str
    operator_decision: str
    live_enabled: bool
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[dict[str, Any], ...]
    push_gate: str
    next_human_decision: str

    @property
    def can_commit(self) -> bool:
        return self.decision == "plan_ready" and self.live_enabled and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_directory": self.repo_directory,
            "objective": self.objective,
            "changed_paths": list(self.changed_paths),
            "commit_message": self.commit_message,
            "checks_passed": self.checks_passed,
            "push_remote": self.push_remote,
            "operator_decision": self.operator_decision,
            "live_enabled": self.live_enabled,
            "can_commit": self.can_commit,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "push_gate": self.push_gate,
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class ProjectCommitReport:
    status: str
    executed: bool
    plan: ProjectCommitPlan
    command_results: tuple[ProjectCommitCommandResult, ...]
    committed_paths: tuple[str, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "command_results": [result.to_dict() for result in self.command_results],
            "committed_paths": list(self.committed_paths),
            "blockers": list(self.blockers),
        }


def build_project_commit_plan(
    *,
    record: ServerProjectRecord,
    objective: Any,
    changed_paths: Iterable[Any],
    checks_passed: bool,
    commit_message: Any | None = None,
    push_remote: Any = "fuzzy",
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> ProjectCommitPlan:
    safe_record = _validate_record(record)
    normalized_objective = _normalize_text(objective, field_name="objective", max_len=500)
    paths = _dedupe_paths(changed_paths)
    message = _normalize_text(
        commit_message if commit_message is not None else _default_commit_message(
            objective=normalized_objective,
            project_slug=safe_record.project_slug,
        ),
        field_name="commit_message",
        max_len=120,
    )
    remote = _normalize_text(push_remote, field_name="push_remote", max_len=40)
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_PROJECT_COMMIT_RUNNER_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )

    blockers: list[str] = []
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")
    if not resolved_live:
        blockers.append("ODYSSEUS_PROJECT_COMMIT_RUNNER_LIVE_ENABLED is not enabled")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if not paths:
        blockers.append("changed paths are required before commit")
    if not checks_passed:
        blockers.append("task checks must be green before commit")
    if remote != "fuzzy":
        blockers.append("push remote must be fuzzy; origin is blocked")

    if normalized_operator == "no_go" or remote == "origin":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    repo_directory = f"{safe_record.project_spec.workspace_root}/repo"
    push_gate = (
        "Push is not executed by the commit runner; review push target fuzzy in a separate push gate"
        if remote == "fuzzy"
        else "Push target is blocked until the remote is changed to fuzzy"
    )
    return ProjectCommitPlan(
        project_slug=safe_record.project_slug,
        repo_directory=repo_directory,
        objective=normalized_objective,
        changed_paths=paths,
        commit_message=message,
        checks_passed=bool(checks_passed),
        push_remote=remote,
        operator_decision=normalized_operator,
        live_enabled=resolved_live,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        planned_steps=(
            {
                "step_id": "git_status",
                "summary": "capture local project repository status",
                "executes": True,
            },
            {
                "step_id": "git_add",
                "summary": f"stage {len(paths)} reviewed project path(s)",
                "executes": True,
            },
            {
                "step_id": "git_commit",
                "summary": f"commit reviewed project changes: {message}",
                "executes": True,
            },
            {
                "step_id": "push_gate",
                "summary": push_gate,
                "executes": False,
            },
        ),
        push_gate=push_gate,
        next_human_decision=_next_decision(decision),
    )


def run_project_local_commit(
    *,
    record: ServerProjectRecord,
    projects_root: str | Path,
    objective: Any,
    changed_paths: Iterable[Any],
    checks_passed: bool,
    commit_message: Any | None = None,
    push_remote: Any = "fuzzy",
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    command_runner: ProjectCommitCommandRunner | None = None,
) -> ProjectCommitReport:
    plan = build_project_commit_plan(
        record=record,
        objective=objective,
        changed_paths=changed_paths,
        checks_passed=checks_passed,
        commit_message=commit_message,
        push_remote=push_remote,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
    )
    if not plan.can_commit:
        return ProjectCommitReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(),
            committed_paths=(),
            blockers=plan.blockers,
        )

    root = _resolve_projects_root(projects_root)
    workspace_path = _assert_child_path(root, root / plan.project_slug)
    repo_path = _assert_child_path(root, workspace_path / "repo")
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return ProjectCommitReport(
            status="blocked",
            executed=False,
            plan=plan,
            command_results=(),
            committed_paths=(),
            blockers=("project local Git repository is missing; run repo provisioning first",),
        )
    for path in plan.changed_paths:
        _assert_child_path(repo_path, repo_path / path)

    runner = command_runner or run_git_commit_subprocess_command
    commands = (
        ("git", "status", "--short", "--branch"),
        ("git", "add", "--", *plan.changed_paths),
        ("git", "commit", "-m", plan.commit_message),
    )
    results: list[ProjectCommitCommandResult] = []
    for command in commands:
        result = runner(command, cwd=repo_path, timeout_seconds=_MAX_TIMEOUT_SECONDS, env={})
        results.append(result)
        if not result.ok:
            return ProjectCommitReport(
                status="failed",
                executed=True,
                plan=plan,
                command_results=tuple(results),
                committed_paths=(),
                blockers=(f"commit command failed: {' '.join(command[:2])}",),
            )

    return ProjectCommitReport(
        status="committed",
        executed=True,
        plan=plan,
        command_results=tuple(results),
        committed_paths=tuple(f"{plan.repo_directory}/{path}" for path in plan.changed_paths),
        blockers=(),
    )


def run_git_commit_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> ProjectCommitCommandResult:
    if not project_commit_command_is_allowed(argv):
        raise ServerProjectCommitRunnerError("unsupported project commit command")
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
        return ProjectCommitCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return ProjectCommitCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def project_commit_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if len(argv) >= 4 and argv[:3] == ("git", "add", "--"):
        return all(_path_arg_is_safe(path) for path in argv[3:])
    if len(argv) == 4 and argv[:3] == ("git", "commit", "-m"):
        _normalize_text(argv[3], field_name="commit_message", max_len=120)
        return True
    return False


def _path_arg_is_safe(value: str) -> bool:
    try:
        _normalize_repo_path(value, field_name="changed_path")
    except ServerProjectCommitRunnerError:
        return False
    return True


def _next_decision(decision: str) -> str:
    if decision == "plan_ready":
        return "Project changes can be committed locally; push remains a separate gated action."
    if decision == "blocked":
        return "Do not commit until the blocked operator or remote decision changes."
    return "Provide green checks, changed paths, live_enabled=true and operator_decision=go before committing."
