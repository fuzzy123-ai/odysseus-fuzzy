"""Operator-gated active executor for universal server project handoffs."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from src.server_project_deploy_handoff import ProjectDeployHandoff


_TRUE_VALUES = {"1", "true", "yes", "on"}
_MAX_TIMEOUT_SECONDS = 7200
_DEFAULT_TIMEOUT_SECONDS = 900
_SECRET_MARKERS = ("token", "secret", "password", "passwd", "api_key", "apikey", "bearer ")


class ProjectCommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "ProjectCommandResult":
        ...


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    lowered = text.lower()
    if any(marker in lowered for marker in ("token=", "secret=", "password=", "api_key=", "bearer ")):
        raise ValueError(f"{field_name} appears to contain secret material")
    return text


def _normalize_step_id(value: Any) -> str:
    return _normalize_text(value, field_name="step_id").lower().replace("-", "_").replace(" ", "_")


def _normalize_argv(values: Iterable[Any]) -> tuple[str, ...]:
    argv = tuple(_normalize_text(value, field_name="argv") for value in values)
    if not argv:
        raise ValueError("argv must not be empty")
    return argv


def _normalize_timeout(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timeout_seconds must be an int")
    if value < 1 or value > _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 1 and {_MAX_TIMEOUT_SECONDS}")
    return value


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _redact_output(value: str) -> str:
    text = str(value or "")
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "[redacted]"
    return text[:4000]


def _merge_env(extra_env: Mapping[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in extra_env.items():
        env[str(key)] = str(value)
    return env


@dataclass(frozen=True, slots=True)
class ProjectExecutionStep:
    step_id: str
    argv: tuple[str, ...]
    summary: str
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    env: Mapping[str, str] | None = None

    @classmethod
    def create(
        cls,
        *,
        step_id: Any,
        argv: Iterable[Any],
        summary: Any,
        timeout_seconds: Any = _DEFAULT_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> "ProjectExecutionStep":
        return cls(
            step_id=_normalize_step_id(step_id),
            argv=_normalize_argv(argv),
            summary=_normalize_text(summary, field_name="summary"),
            timeout_seconds=_normalize_timeout(timeout_seconds),
            env=dict(env or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "argv": list(self.argv),
            "summary": self.summary,
            "timeout_seconds": self.timeout_seconds,
            "env_keys": sorted((self.env or {}).keys()),
        }


@dataclass(frozen=True, slots=True)
class ProjectCommandResult:
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
class ProjectExecutionStepReport:
    step: ProjectExecutionStep
    status: str
    result: ProjectCommandResult | None = None
    blocker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step.to_dict(),
            "status": self.status,
            "result": self.result.to_dict() if self.result else None,
            "blocker": self.blocker,
        }


@dataclass(frozen=True, slots=True)
class ProjectExecutionReport:
    status: str
    executed: bool
    live_enabled: bool
    operator_decision: str
    blockers: tuple[str, ...]
    steps: tuple[ProjectExecutionStepReport, ...]

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "live_enabled": self.live_enabled,
            "operator_decision": self.operator_decision,
            "blockers": list(self.blockers),
            "steps": [step.to_dict() for step in self.steps],
        }


def build_default_project_execution_steps(
    *,
    smoke_tests: Iterable[str] = (),
    include_pre_update_hook: bool = True,
    include_version_metadata_update: bool = True,
    include_container_update: bool = True,
) -> tuple[ProjectExecutionStep, ...]:
    steps: list[ProjectExecutionStep] = []
    if include_pre_update_hook:
        steps.append(
            ProjectExecutionStep.create(
                step_id="pre_update_snapshot",
                argv=("ops/homeserver/pre-update-snapshot.sh",),
                summary="run the blocking pre-update snapshot hook",
                timeout_seconds=1800,
            )
        )
    if include_version_metadata_update:
        steps.append(
            ProjectExecutionStep.create(
                step_id="update_version_metadata_env",
                argv=("ops/homeserver/update-odysseus-version-env.sh",),
                summary="refresh deployment git metadata before container recreate",
                timeout_seconds=300,
            )
        )
    if include_container_update:
        steps.append(
            ProjectExecutionStep.create(
                step_id="podman_compose_up",
                argv=("podman", "compose", "up", "-d", "--build"),
                summary="rebuild and restart the Podman deployment",
                timeout_seconds=1800,
            )
        )
    for index, target in enumerate(smoke_tests, start=1):
        steps.append(
            ProjectExecutionStep.create(
                step_id=f"smoke_test_{index}",
                argv=("python", "-m", "pytest", target, "-q"),
                summary=f"run project smoke test target {target}",
                timeout_seconds=900,
            )
        )
    return tuple(steps)


def project_command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("ops/homeserver/pre-update-snapshot.sh",):
        return True
    if argv == ("ops/homeserver/update-odysseus-version-env.sh",):
        return True
    if argv == ("podman", "compose", "up", "-d", "--build"):
        return True
    if argv == ("podman", "image", "prune", "-f"):
        return True
    if argv == ("git", "status", "--short", "--branch"):
        return True
    if len(argv) >= 4 and argv[:3] == ("python", "-m", "pytest"):
        return True
    return False


def run_project_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> ProjectCommandResult:
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
        return ProjectCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return ProjectCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def execute_project_handoff(
    *,
    handoff: ProjectDeployHandoff,
    steps: Iterable[ProjectExecutionStep] | None = None,
    cwd: str | Path = ".",
    command_runner: ProjectCommandRunner = run_project_subprocess_command,
    live_enabled: bool | None = None,
    operator_decision: str = "missing",
) -> ProjectExecutionReport:
    if not isinstance(handoff, ProjectDeployHandoff):
        raise ValueError("handoff must be a ProjectDeployHandoff")
    resolved_live_enabled = _bool_env(os.getenv("ODYSSEUS_PROJECT_EXECUTOR_LIVE_ENABLED")) if live_enabled is None else bool(live_enabled)
    normalized_operator = _normalize_text(operator_decision, field_name="operator_decision").lower().replace("-", "_")
    selected_steps = tuple(steps) if steps is not None else build_default_project_execution_steps()
    blockers = _execution_blockers(
        handoff=handoff,
        live_enabled=resolved_live_enabled,
        operator_decision=normalized_operator,
        steps=selected_steps,
    )
    if blockers:
        return ProjectExecutionReport(
            status="blocked",
            executed=False,
            live_enabled=resolved_live_enabled,
            operator_decision=normalized_operator,
            blockers=blockers,
            steps=tuple(
                ProjectExecutionStepReport(step=step, status="blocked", blocker="execution gate blocked")
                for step in selected_steps
            ),
        )

    reports: list[ProjectExecutionStepReport] = []
    resolved_cwd = Path(cwd).resolve()
    for step in selected_steps:
        result = command_runner(
            step.argv,
            cwd=resolved_cwd,
            timeout_seconds=step.timeout_seconds,
            env=dict(step.env or {}),
        )
        status = "completed" if result.ok else "failed"
        reports.append(ProjectExecutionStepReport(step=step, status=status, result=result))
        if not result.ok:
            return ProjectExecutionReport(
                status="failed",
                executed=True,
                live_enabled=resolved_live_enabled,
                operator_decision=normalized_operator,
                blockers=(f"step {step.step_id} failed",),
                steps=tuple(reports),
            )
    return ProjectExecutionReport(
        status="completed",
        executed=True,
        live_enabled=resolved_live_enabled,
        operator_decision=normalized_operator,
        blockers=(),
        steps=tuple(reports),
    )


def _execution_blockers(
    *,
    handoff: ProjectDeployHandoff,
    live_enabled: bool,
    operator_decision: str,
    steps: tuple[ProjectExecutionStep, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not live_enabled:
        blockers.append("ODYSSEUS_PROJECT_EXECUTOR_LIVE_ENABLED is not enabled")
    if operator_decision != "go":
        blockers.append("operator decision is not go")
    if handoff.decision != "ready_for_operator_go":
        blockers.append(f"project deploy handoff decision is {handoff.decision}")
    if handoff.live_execution_allowed:
        blockers.append("handoff must not claim live execution itself")
    for step in steps:
        if not isinstance(step, ProjectExecutionStep):
            blockers.append("all steps must be ProjectExecutionStep objects")
            continue
        if not project_command_is_allowed(step.argv):
            blockers.append(f"step {step.step_id} is not in the project executor command whitelist")
    return tuple(blockers)
