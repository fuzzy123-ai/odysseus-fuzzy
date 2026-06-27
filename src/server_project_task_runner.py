"""Operator-gated task runner for autonomous server project development."""

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
_DECISIONS = ("blocked", "hold", "plan_ready", "completed", "failed")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_SAFE_PROJECTS_ROOT_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]{1,80}$")
_MAX_FILE_BYTES = 256_000
_MAX_TIMEOUT_SECONDS = 1800
_DEFAULT_TIMEOUT_SECONDS = 300


class ProjectTaskCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "ProjectTaskCommandResult":
        ...


class ServerProjectTaskRunnerError(ValueError):
    """Raised when a project task request is unsafe or invalid."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectTaskRunnerError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectTaskRunnerError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectTaskRunnerError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ServerProjectTaskRunnerError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ServerProjectTaskRunnerError(f"{field_name} must not be empty")
    if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ServerProjectTaskRunnerError(f"{field_name} must be repo-relative and use forward slashes")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ServerProjectTaskRunnerError(f"{field_name} must not contain traversal segments")
    path = "/".join(parts)
    lowered = path.lower()
    if lowered == ".git" or lowered.startswith(".git/") or lowered.endswith("/.env") or lowered == ".env":
        raise ServerProjectTaskRunnerError(f"{field_name} targets a blocked project path")
    if len(path) > 180:
        raise ServerProjectTaskRunnerError(f"{field_name} exceeds max length 180")
    return path


def _normalize_timeout(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServerProjectTaskRunnerError("timeout_seconds must be an int")
    if value < 1 or value > _MAX_TIMEOUT_SECONDS:
        raise ServerProjectTaskRunnerError(f"timeout_seconds must be between 1 and {_MAX_TIMEOUT_SECONDS}")
    return value


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _resolve_projects_root(projects_root: str | Path) -> Path:
    root = Path(projects_root).expanduser()
    if str(root).strip() in {"", ".", ".."}:
        raise ServerProjectTaskRunnerError("projects_root must be an explicit directory")
    if _SECRET_RE.search(str(root)):
        raise ServerProjectTaskRunnerError("projects_root appears to contain secret material")
    if not root.is_absolute() and not _SAFE_PROJECTS_ROOT_NAME_RE.fullmatch(str(root)):
        raise ServerProjectTaskRunnerError("relative projects_root must be a simple directory name")
    return root.resolve()


def _assert_child_path(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ServerProjectTaskRunnerError("task path escapes project repository") from exc
    return resolved


def _validate_record(record: ServerProjectRecord) -> ServerProjectRecord:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectTaskRunnerError("record must be a ServerProjectRecord")
    if record.project_spec.workspace_root != f"projects/{record.project_slug}":
        raise ServerProjectTaskRunnerError("project workspace_root must match projects/<project-slug>")
    if record.project_spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectTaskRunnerError("project repository must not be Odysseus")
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
class ProjectTaskFileWrite:
    path: str
    content: str

    @classmethod
    def create(cls, *, path: Any, content: Any) -> "ProjectTaskFileWrite":
        text = str(content or "")
        if len(text.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ServerProjectTaskRunnerError(f"file content exceeds max size {_MAX_FILE_BYTES}")
        if _SECRET_RE.search(text):
            raise ServerProjectTaskRunnerError("file content appears to contain secret material")
        return cls(path=_normalize_repo_path(path, field_name="file_write.path"), content=text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "byte_count": len(self.content.encode("utf-8")),
        }


@dataclass(frozen=True, slots=True)
class ProjectTaskCheck:
    argv: tuple[str, ...]
    timeout_seconds: int

    @classmethod
    def create(
        cls,
        *,
        argv: Iterable[Any],
        timeout_seconds: Any = _DEFAULT_TIMEOUT_SECONDS,
    ) -> "ProjectTaskCheck":
        normalized = tuple(_normalize_text(value, field_name="argv", max_len=140) for value in argv)
        if not normalized:
            raise ServerProjectTaskRunnerError("argv must not be empty")
        if not project_task_command_is_allowed(normalized):
            raise ServerProjectTaskRunnerError("task check command is not allowed")
        return cls(argv=normalized, timeout_seconds=_normalize_timeout(timeout_seconds))

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProjectTaskCommandResult:
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
class ProjectTaskCheckReport:
    check: ProjectTaskCheck
    status: str
    result: ProjectTaskCommandResult | None = None
    blocker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check.to_dict(),
            "status": self.status,
            "result": self.result.to_dict() if self.result else None,
            "blocker": self.blocker,
        }


@dataclass(frozen=True, slots=True)
class ProjectTaskPlan:
    project_slug: str
    repo_directory: str
    objective: str
    file_writes: tuple[ProjectTaskFileWrite, ...]
    checks: tuple[ProjectTaskCheck, ...]
    operator_decision: str
    live_enabled: bool
    decision: str
    blockers: tuple[str, ...]
    next_human_decision: str

    @property
    def can_execute(self) -> bool:
        return self.decision == "plan_ready" and self.live_enabled and self.operator_decision == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_directory": self.repo_directory,
            "objective": self.objective,
            "file_writes": [write.to_dict() for write in self.file_writes],
            "checks": [check.to_dict() for check in self.checks],
            "operator_decision": self.operator_decision,
            "live_enabled": self.live_enabled,
            "can_execute": self.can_execute,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class ProjectTaskReport:
    status: str
    executed: bool
    plan: ProjectTaskPlan
    written_files: tuple[str, ...]
    check_reports: tuple[ProjectTaskCheckReport, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "written_files": list(self.written_files),
            "check_reports": [report.to_dict() for report in self.check_reports],
            "blockers": list(self.blockers),
        }


def build_project_task_plan(
    *,
    record: ServerProjectRecord,
    objective: Any,
    file_writes: Iterable[ProjectTaskFileWrite],
    checks: Iterable[ProjectTaskCheck],
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
) -> ProjectTaskPlan:
    safe_record = _validate_record(record)
    normalized_objective = _normalize_text(objective, field_name="objective", max_len=500)
    writes = tuple(file_writes)
    task_checks = tuple(checks)
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )
    resolved_live = (
        _bool_env(os.getenv("ODYSSEUS_PROJECT_TASK_RUNNER_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )

    blockers: list[str] = []
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")
    if not resolved_live:
        blockers.append("ODYSSEUS_PROJECT_TASK_RUNNER_LIVE_ENABLED is not enabled")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if not writes:
        blockers.append("at least one file write is required")
    if not task_checks:
        blockers.append("at least one task check is required")

    if normalized_operator == "no_go":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    return ProjectTaskPlan(
        project_slug=safe_record.project_slug,
        repo_directory=f"{safe_record.project_spec.workspace_root}/repo",
        objective=normalized_objective,
        file_writes=writes,
        checks=task_checks,
        operator_decision=normalized_operator,
        live_enabled=resolved_live,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        next_human_decision=_next_decision(decision),
    )


def run_project_task(
    *,
    record: ServerProjectRecord,
    projects_root: str | Path,
    objective: Any,
    file_writes: Iterable[ProjectTaskFileWrite],
    checks: Iterable[ProjectTaskCheck],
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    command_runner: ProjectTaskCommandRunner | None = None,
) -> ProjectTaskReport:
    plan = build_project_task_plan(
        record=record,
        objective=objective,
        file_writes=file_writes,
        checks=checks,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
    )
    if not plan.can_execute:
        return ProjectTaskReport(
            status="blocked",
            executed=False,
            plan=plan,
            written_files=(),
            check_reports=tuple(
                ProjectTaskCheckReport(check=check, status="blocked", blocker="task gate blocked")
                for check in plan.checks
            ),
            blockers=plan.blockers,
        )

    root = _resolve_projects_root(projects_root)
    workspace_path = _assert_child_path(root, root / plan.project_slug)
    repo_path = _assert_child_path(root, workspace_path / "repo")
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return ProjectTaskReport(
            status="blocked",
            executed=False,
            plan=plan,
            written_files=(),
            check_reports=(),
            blockers=("project local Git repository is missing; run repo provisioning first",),
        )

    written: list[str] = []
    for file_write in plan.file_writes:
        target = _assert_child_path(repo_path, repo_path / file_write.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_write.content, encoding="utf-8")
        written.append(f"{plan.repo_directory}/{file_write.path}")

    runner = command_runner or run_task_subprocess_command
    reports: list[ProjectTaskCheckReport] = []
    for check in plan.checks:
        result = runner(check.argv, cwd=repo_path, timeout_seconds=check.timeout_seconds, env={})
        status = "completed" if result.ok else "failed"
        reports.append(ProjectTaskCheckReport(check=check, status=status, result=result))
        if not result.ok:
            return ProjectTaskReport(
                status="failed",
                executed=True,
                plan=plan,
                written_files=tuple(written),
                check_reports=tuple(reports),
                blockers=(f"task check failed: {' '.join(check.argv)}",),
            )

    return ProjectTaskReport(
        status="completed",
        executed=True,
        plan=plan,
        written_files=tuple(written),
        check_reports=tuple(reports),
        blockers=(),
    )


def run_task_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> ProjectTaskCommandResult:
    if not project_task_command_is_allowed(argv):
        raise ServerProjectTaskRunnerError("unsupported task check command")
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
        return ProjectTaskCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return ProjectTaskCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def project_task_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if len(argv) >= 3 and argv[:3] == ("python", "-m", "pytest"):
        return all(_check_arg_is_safe(arg) for arg in argv[3:])
    if argv in {
        ("npm", "test"),
        ("npm", "run", "test"),
        ("npm", "run", "build"),
    }:
        return True
    if len(argv) == 3 and argv[:2] == ("node", "--check"):
        return _check_arg_is_safe(argv[2])
    return False


def _check_arg_is_safe(value: str) -> bool:
    if value in {"-q", "-x", "--maxfail=1"}:
        return True
    try:
        _normalize_repo_path(value, field_name="check_arg")
    except ServerProjectTaskRunnerError:
        return False
    return True


def _next_decision(decision: str) -> str:
    if decision == "plan_ready":
        return "Project task may run locally; commit, push and deploy remain separate gated actions."
    if decision == "blocked":
        return "Do not run this project task until the operator decision changes."
    return "Provide live_enabled=true, operator_decision=go, file writes and checks to run a project task."
