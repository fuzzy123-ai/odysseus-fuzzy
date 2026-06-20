"""Operator-gated active runner for Odysseus updates."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from src.odysseus_updater import OdysseusUpdaterBundle

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_TIMEOUT_SECONDS = 900
_MAX_TIMEOUT_SECONDS = 7200
_REDACTION_MARKER = "[redacted]"

_SECRET_PATTERNS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "bearer ",
    "chat_id",
)


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> "UpdaterCommandResult":
        ...


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_step_id(value: Any) -> str:
    text = (
        _normalize_text(value, field_name="step_id")
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return "_".join(part for part in text.split("_") if part)


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


def _normalize_bool_env(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def _redact_output(value: str) -> str:
    text = str(value or "")
    lowered = text.lower()
    if any(pattern in lowered for pattern in _SECRET_PATTERNS):
        return _REDACTION_MARKER
    return text[:4000]


def _merge_env(extra_env: Mapping[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    for key, value in extra_env.items():
        env[str(key)] = str(value)
    return env


@dataclass(frozen=True, slots=True)
class UpdaterExecutionStep:
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
    ) -> "UpdaterExecutionStep":
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
class UpdaterCommandResult:
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
class UpdaterStepReport:
    step: UpdaterExecutionStep
    status: str
    result: UpdaterCommandResult | None = None
    blocker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step.to_dict(),
            "status": self.status,
            "result": self.result.to_dict() if self.result else None,
            "blocker": self.blocker,
        }


@dataclass(frozen=True, slots=True)
class OdysseusUpdateExecutionReport:
    status: str
    executed: bool
    live_enabled: bool
    operator_decision: str
    blockers: tuple[str, ...]
    steps: tuple[UpdaterStepReport, ...]

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


def build_default_odysseus_update_steps(
    *,
    reason: str = "manual Odysseus auto update",
    include_pre_update_hook: bool = True,
    include_docker_update: bool = True,
    smoke_tests: Iterable[str] = (),
) -> tuple[UpdaterExecutionStep, ...]:
    steps: list[UpdaterExecutionStep] = []
    if include_pre_update_hook:
        steps.append(
            UpdaterExecutionStep.create(
                step_id="pre_update_snapshot",
                argv=("ops/homeserver/pre-update-snapshot.sh",),
                summary="run the blocking pre-update snapshot hook",
                timeout_seconds=1800,
                env={"ODYSSEUS_UPDATE_REASON": reason},
            )
        )
    steps.append(
        UpdaterExecutionStep.create(
            step_id="git_pull_ff_only",
            argv=("git", "pull", "--ff-only"),
            summary="fast-forward the current checkout",
            timeout_seconds=900,
        )
    )
    if include_docker_update:
        steps.extend(
            (
                UpdaterExecutionStep.create(
                    step_id="docker_compose_up",
                    argv=("docker", "compose", "up", "-d", "--build"),
                    summary="rebuild and restart the Docker Compose deployment",
                    timeout_seconds=1800,
                ),
                UpdaterExecutionStep.create(
                    step_id="docker_image_prune",
                    argv=("docker", "image", "prune", "-f"),
                    summary="remove dangling Docker images after successful update",
                    timeout_seconds=900,
                ),
            )
        )
    for index, target in enumerate(smoke_tests, start=1):
        steps.append(
            UpdaterExecutionStep.create(
                step_id=f"smoke_test_{index}",
                argv=("python", "-m", "pytest", target, "-q"),
                summary=f"run smoke test target {target}",
                timeout_seconds=900,
            )
        )
    return tuple(steps)


def command_is_allowed(argv: tuple[str, ...]) -> bool:
    if argv == ("git", "pull", "--ff-only"):
        return True
    if argv == ("git", "fetch", "--all", "--tags", "--prune"):
        return True
    if argv == ("docker", "compose", "version"):
        return True
    if argv == ("docker", "compose", "up", "-d", "--build"):
        return True
    if argv == ("docker", "image", "prune", "-f"):
        return True
    if argv == ("ops/homeserver/pre-update-snapshot.sh",):
        return True
    if len(argv) >= 4 and argv[:3] == ("python", "-m", "pytest"):
        return True
    return False


def run_subprocess_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: Mapping[str, str],
) -> UpdaterCommandResult:
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
        duration = time.monotonic() - started
        return UpdaterCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(duration, 3),
        )
    duration = time.monotonic() - started
    return UpdaterCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        timed_out=False,
        duration_seconds=round(duration, 3),
    )


def _execution_blockers(
    *,
    bundle: OdysseusUpdaterBundle,
    live_enabled: bool,
    operator_decision: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not live_enabled:
        blockers.append("ODYSSEUS_UPDATER_LIVE_ENABLED is not enabled")
    if operator_decision != "go":
        blockers.append("operator decision is not go")
    if bundle.decision != "go":
        blockers.append(f"updater bundle decision is {bundle.decision}")
    if bundle.live_update_decision != "go" or bundle.live_execution_blocked:
        blockers.append("updater bundle has not been built with active execution approval")
    return tuple(blockers)


def execute_odysseus_update(
    *,
    bundle: OdysseusUpdaterBundle,
    steps: Iterable[UpdaterExecutionStep] | None = None,
    cwd: str | Path = ".",
    operator_decision: str = "go",
    live_enabled: bool | None = None,
    command_runner: CommandRunner | None = None,
    stop_on_failure: bool = True,
) -> OdysseusUpdateExecutionReport:
    normalized_operator = _normalize_text(operator_decision, field_name="operator_decision").lower()
    enabled = (
        _normalize_bool_env(os.getenv("ODYSSEUS_UPDATER_LIVE_ENABLED"))
        if live_enabled is None
        else bool(live_enabled)
    )
    planned_steps = tuple(steps or build_default_odysseus_update_steps())
    blockers = list(
        _execution_blockers(
            bundle=bundle,
            live_enabled=enabled,
            operator_decision=normalized_operator,
        )
    )
    step_reports: list[UpdaterStepReport] = []
    for step in planned_steps:
        if not command_is_allowed(step.argv):
            blockers.append(f"step {step.step_id} is not in the updater command whitelist")
            step_reports.append(
                UpdaterStepReport(
                    step=step,
                    status="blocked",
                    blocker="command is not whitelisted",
                )
            )

    if blockers:
        return OdysseusUpdateExecutionReport(
            status="blocked",
            executed=False,
            live_enabled=enabled,
            operator_decision=normalized_operator,
            blockers=tuple(dict.fromkeys(blockers)),
            steps=tuple(step_reports),
        )

    runner = command_runner or run_subprocess_command
    resolved_cwd = Path(cwd).resolve()
    for step in planned_steps:
        result = runner(
            step.argv,
            cwd=resolved_cwd,
            timeout_seconds=step.timeout_seconds,
            env=step.env or {},
        )
        status = "passed" if result.ok else "failed"
        step_reports.append(UpdaterStepReport(step=step, status=status, result=result))
        if not result.ok and stop_on_failure:
            return OdysseusUpdateExecutionReport(
                status="failed",
                executed=True,
                live_enabled=enabled,
                operator_decision=normalized_operator,
                blockers=(f"step {step.step_id} failed",),
                steps=tuple(step_reports),
            )

    final_status = "completed" if all(step.status == "passed" for step in step_reports) else "failed"
    return OdysseusUpdateExecutionReport(
        status=final_status,
        executed=True,
        live_enabled=enabled,
        operator_decision=normalized_operator,
        blockers=() if final_status == "completed" else ("one or more updater steps failed",),
        steps=tuple(step_reports),
    )
