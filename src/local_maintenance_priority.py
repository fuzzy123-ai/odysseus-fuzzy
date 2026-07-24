"""Command-plan helpers for low-priority external maintenance jobs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
from pathlib import PurePosixPath, PureWindowsPath
import sys
from typing import Any, Sequence

from src.local_model_scheduler import wait_for_local_model_foreground_clear


class LocalMaintenancePriorityError(ValueError):
    """Raised when a maintenance priority command plan is unsafe."""


_METHODS = {"nice_ionice", "systemd_scope"}
_PRIORITY_CLASSES = {"P2", "P3"}
_DESTRUCTIVE_EXECUTABLES = {
    "dd",
    "halt",
    "mkfs",
    "poweroff",
    "reboot",
    "rm",
    "rmdir",
    "shred",
    "shutdown",
}
_DESTRUCTIVE_PODMAN_SUBCOMMANDS = {
    "kill",
    "pod",
    "restart",
    "rm",
    "rmi",
    "stop",
    "system",
    "volume",
}
_SHELL_TOKENS = {";", "|", "||", "&&", ">", ">>", "<", "<<", "`"}
_PRIVATE_POSIX_PREFIXES = ("/home/", "/root/", "/opt/odysseus/.env")
_PRIVATE_WINDOWS_RE = re.compile(r"^[A-Za-z]:\\Users\\[^\\]+\\", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LocalMaintenancePriorityPlan:
    method: str
    priority_class: str
    reason: str
    execution_argv: tuple[str, ...]
    maintenance_argv: tuple[str, ...]
    nice_value: int | None = None
    ionice_class: int | None = None
    ionice_level: int | None = None
    cpu_weight: int | None = None
    io_weight: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "priority_class": self.priority_class,
            "reason": self.reason,
            "execution_argv_redacted": tuple(_redact_token(token) for token in self.execution_argv),
            "maintenance_argv_redacted": tuple(_redact_token(token) for token in self.maintenance_argv),
            "nice_value": self.nice_value,
            "ionice_class": self.ionice_class,
            "ionice_level": self.ionice_level,
            "cpu_weight": self.cpu_weight,
            "io_weight": self.io_weight,
            "executes": False,
        }


@dataclass(frozen=True, slots=True)
class LocalMaintenancePreflightEvidence:
    load_average_1m: float | None = None
    available_ram_mb: int | None = None
    warm_models: tuple[str, ...] = ()
    active_maintenance: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "load_average_1m": self.load_average_1m,
            "available_ram_mb": self.available_ram_mb,
            "warm_models": self.warm_models,
            "active_maintenance": self.active_maintenance,
        }


@dataclass(frozen=True, slots=True)
class LocalMaintenanceLauncherPlan:
    priority_plan: LocalMaintenancePriorityPlan
    required_model: str
    max_load_average_1m: float
    min_available_ram_mb: int
    wait_timeout_seconds: int
    command_timeout_seconds: int
    report_path: str
    preflight_status: str
    preflight_failures: tuple[str, ...]
    preflight_warnings: tuple[str, ...]
    evidence: LocalMaintenancePreflightEvidence | None = None

    @property
    def execution_argv(self) -> tuple[str, ...]:
        return self.priority_plan.execution_argv

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.local_maintenance_launcher_plan.v1",
            "priority": self.priority_plan.to_dict(),
            "required_model": self.required_model,
            "max_load_average_1m": self.max_load_average_1m,
            "min_available_ram_mb": self.min_available_ram_mb,
            "wait_timeout_seconds": self.wait_timeout_seconds,
            "command_timeout_seconds": self.command_timeout_seconds,
            "report_path_redacted": _redact_token(self.report_path),
            "preflight_status": self.preflight_status,
            "preflight_failures": self.preflight_failures,
            "preflight_warnings": self.preflight_warnings,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "executes": False,
        }


def build_low_priority_maintenance_plan(
    argv: Sequence[str],
    *,
    method: str = "nice_ionice",
    priority_class: str = "P2",
    reason: str = "external_memory_maintenance",
    nice_value: int | None = None,
    ionice_class: int | None = None,
    ionice_level: int | None = None,
    cpu_weight: int | None = None,
    io_weight: int | None = None,
) -> LocalMaintenancePriorityPlan:
    maintenance_argv = _normalize_argv(argv)
    normalized_method = _normalize_choice(method, _METHODS, "method")
    normalized_priority = _normalize_choice(priority_class, _PRIORITY_CLASSES, "priority_class")
    _validate_maintenance_argv(maintenance_argv)

    if normalized_method == "nice_ionice":
        default_nice = 19 if normalized_priority == "P3" else 10
        default_ionice_class = 3 if normalized_priority == "P3" else 2
        default_ionice_level = None if default_ionice_class == 3 else 7
        normalized_nice = _bounded_int(
            default_nice if nice_value is None else nice_value,
            field_name="nice_value",
            minimum=1,
            maximum=19,
        )
        normalized_ionice_class = _bounded_int(
            default_ionice_class if ionice_class is None else ionice_class,
            field_name="ionice_class",
            minimum=1,
            maximum=3,
        )
        normalized_ionice_level = (
            None
            if normalized_ionice_class == 3
            else _bounded_int(
                default_ionice_level if ionice_level is None else ionice_level,
                field_name="ionice_level",
                minimum=0,
                maximum=7,
            )
        )
        prefix = [
            "nice",
            "-n",
            str(normalized_nice),
            "ionice",
            "-c",
            str(normalized_ionice_class),
        ]
        if normalized_ionice_level is not None:
            prefix.extend(("-n", str(normalized_ionice_level)))
        execution_argv = (*prefix, *maintenance_argv)
        return LocalMaintenancePriorityPlan(
            method=normalized_method,
            priority_class=normalized_priority,
            reason=_safe_reason(reason),
            execution_argv=execution_argv,
            maintenance_argv=maintenance_argv,
            nice_value=normalized_nice,
            ionice_class=normalized_ionice_class,
            ionice_level=normalized_ionice_level,
        )

    default_weight = 10 if normalized_priority == "P3" else 20
    normalized_cpu = _bounded_int(default_weight if cpu_weight is None else cpu_weight, field_name="cpu_weight", minimum=1, maximum=100)
    normalized_io = _bounded_int(default_weight if io_weight is None else io_weight, field_name="io_weight", minimum=1, maximum=100)
    execution_argv = (
        "systemd-run",
        "--user",
        "--scope",
        "-p",
        f"CPUWeight={normalized_cpu}",
        "-p",
        f"IOWeight={normalized_io}",
        *maintenance_argv,
    )
    return LocalMaintenancePriorityPlan(
        method=normalized_method,
        priority_class=normalized_priority,
        reason=_safe_reason(reason),
        execution_argv=execution_argv,
        maintenance_argv=maintenance_argv,
        cpu_weight=normalized_cpu,
        io_weight=normalized_io,
    )


def build_foreground_aware_maintenance_plan(
    argv: Sequence[str],
    *,
    method: str = "nice_ionice",
    priority_class: str = "P3",
    reason: str = "external_memory_maintenance",
    wait_timeout_seconds: int = 600,
    marker_path: str | None = None,
) -> LocalMaintenancePriorityPlan:
    """Compatibility planner that waits for exact-Gemma busy work first."""

    maintenance_argv = _normalize_argv(argv)
    _validate_maintenance_argv(maintenance_argv)
    guarded = _add_foreground_wait_guard(
        maintenance_argv,
        wait_timeout_seconds=wait_timeout_seconds,
        marker_path=marker_path,
    )
    return build_low_priority_maintenance_plan(
        guarded,
        method=method,
        priority_class=priority_class,
        reason=reason,
    )


def build_guarded_maintenance_launcher_plan(
    argv: Sequence[str],
    *,
    method: str = "nice_ionice",
    priority_class: str = "P3",
    reason: str = "external_memory_maintenance",
    required_model: str = "gemma3:4b",
    max_load_average_1m: float | None = None,
    min_available_ram_mb: int = 4096,
    wait_timeout_seconds: int = 600,
    command_timeout_seconds: int = 1800,
    report_path: str = "/tmp/odysseus-local-maintenance-report.json",
    evidence: LocalMaintenancePreflightEvidence | dict[str, Any] | None = None,
) -> LocalMaintenanceLauncherPlan:
    """Build a production-safe launcher plan without executing it."""

    normalized_priority = _normalize_choice(priority_class, _PRIORITY_CLASSES, "priority_class")
    required_model_text = _safe_model(required_model)
    load_limit = _bounded_float(
        1.0 if max_load_average_1m is None and normalized_priority == "P3" else 2.0 if max_load_average_1m is None else max_load_average_1m,
        field_name="max_load_average_1m",
        minimum=0.1,
        maximum=128.0,
    )
    min_ram = _bounded_int(min_available_ram_mb, field_name="min_available_ram_mb", minimum=512, maximum=1048576)
    wait_timeout = _bounded_int(wait_timeout_seconds, field_name="wait_timeout_seconds", minimum=1, maximum=86400)
    command_timeout = _bounded_int(command_timeout_seconds, field_name="command_timeout_seconds", minimum=1, maximum=86400)
    normalized_report_path = _safe_report_path(report_path)
    normalized_evidence = _normalize_evidence(evidence)
    failures, warnings = _evaluate_launcher_preflight(
        evidence=normalized_evidence,
        required_model=required_model_text,
        max_load_average_1m=load_limit,
        min_available_ram_mb=min_ram,
    )
    priority_plan = build_foreground_aware_maintenance_plan(
        argv,
        method=method,
        priority_class=normalized_priority,
        reason=reason,
        wait_timeout_seconds=wait_timeout,
    )
    return LocalMaintenanceLauncherPlan(
        priority_plan=priority_plan,
        required_model=required_model_text,
        max_load_average_1m=load_limit,
        min_available_ram_mb=min_ram,
        wait_timeout_seconds=wait_timeout,
        command_timeout_seconds=command_timeout,
        report_path=normalized_report_path,
        preflight_status="blocked" if failures else "unknown" if warnings else "ready",
        preflight_failures=failures,
        preflight_warnings=warnings,
        evidence=normalized_evidence,
    )


def _normalize_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
        raise LocalMaintenancePriorityError("argv must be a sequence, not a shell string")
    normalized = tuple(str(token) for token in argv)
    if not normalized:
        raise LocalMaintenancePriorityError("argv must not be empty")
    if any(not token.strip() for token in normalized):
        raise LocalMaintenancePriorityError("argv tokens must not be empty")
    return normalized


def _validate_maintenance_argv(argv: tuple[str, ...]) -> None:
    for token in argv:
        if "\n" in token or "\r" in token or "$(" in token:
            raise LocalMaintenancePriorityError("argv contains shell control syntax")
        if token.strip() in _SHELL_TOKENS:
            raise LocalMaintenancePriorityError("argv contains shell control syntax")

    executable = _basename(argv[0]).lower()
    if executable in _DESTRUCTIVE_EXECUTABLES:
        raise LocalMaintenancePriorityError("destructive executable is not allowed")
    if executable == "podman" and len(argv) > 1:
        subcommand = str(argv[1]).strip().lower()
        if subcommand in _DESTRUCTIVE_PODMAN_SUBCOMMANDS:
            raise LocalMaintenancePriorityError("destructive podman subcommand is not allowed")
    if any(str(token).strip() == "--privileged" for token in argv):
        raise LocalMaintenancePriorityError("privileged maintenance commands are not allowed")


def _normalize_choice(value: str, choices: set[str], field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in choices:
        raise LocalMaintenancePriorityError(f"{field_name} must be one of {sorted(choices)}")
    return normalized


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise LocalMaintenancePriorityError(f"{field_name} must be an int") from None
    if normalized < minimum or normalized > maximum:
        raise LocalMaintenancePriorityError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _bounded_float(value: Any, *, field_name: str, minimum: float, maximum: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise LocalMaintenancePriorityError(f"{field_name} must be a number") from None
    if normalized < minimum or normalized > maximum:
        raise LocalMaintenancePriorityError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _safe_reason(value: Any) -> str:
    text = " ".join(str(value or "external_memory_maintenance").split())
    return text[:120] or "external_memory_maintenance"


def _safe_model(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > 120 or any(token in text for token in _SHELL_TOKENS):
        raise LocalMaintenancePriorityError("required_model is invalid")
    return text


def _safe_report_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise LocalMaintenancePriorityError("report_path must not be empty")
    if "\n" in text or "\r" in text or "$(" in text or any(token in text for token in _SHELL_TOKENS):
        raise LocalMaintenancePriorityError("report_path contains shell control syntax")
    return text


def _basename(value: str) -> str:
    raw = str(value)
    return PureWindowsPath(raw).name if "\\" in raw else PurePosixPath(raw).name


def _redact_token(token: str) -> str:
    raw = str(token)
    normalized = raw.replace("\\", "/")
    if _PRIVATE_WINDOWS_RE.match(raw):
        return f"<private-path>/{PureWindowsPath(raw).name}"
    if any(normalized.startswith(prefix) for prefix in _PRIVATE_POSIX_PREFIXES):
        return f"<private-path>/{PurePosixPath(normalized).name}"
    return raw


def _normalize_evidence(value: LocalMaintenancePreflightEvidence | dict[str, Any] | None) -> LocalMaintenancePreflightEvidence | None:
    if value is None:
        return None
    if isinstance(value, LocalMaintenancePreflightEvidence):
        return value
    if not isinstance(value, dict):
        raise LocalMaintenancePriorityError("evidence must be a dict or LocalMaintenancePreflightEvidence")
    warm_models_raw = value.get("warm_models") or ()
    if isinstance(warm_models_raw, (str, bytes)):
        warm_models = (str(warm_models_raw),)
    else:
        warm_models = tuple(str(item) for item in warm_models_raw)
    load = value.get("load_average_1m")
    ram = value.get("available_ram_mb")
    active = value.get("active_maintenance")
    return LocalMaintenancePreflightEvidence(
        load_average_1m=None if load is None else _bounded_float(load, field_name="evidence.load_average_1m", minimum=0.0, maximum=1024.0),
        available_ram_mb=None if ram is None else _bounded_int(ram, field_name="evidence.available_ram_mb", minimum=0, maximum=1048576),
        warm_models=warm_models,
        active_maintenance=None if active is None else bool(active),
    )


def _evaluate_launcher_preflight(
    *,
    evidence: LocalMaintenancePreflightEvidence | None,
    required_model: str,
    max_load_average_1m: float,
    min_available_ram_mb: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if evidence is None:
        return (), ("preflight_evidence_missing",)
    failures: list[str] = []
    warnings: list[str] = []
    if evidence.load_average_1m is None:
        warnings.append("load_average_missing")
    elif evidence.load_average_1m > max_load_average_1m:
        failures.append("load_average_too_high")
    if evidence.available_ram_mb is None:
        warnings.append("available_ram_missing")
    elif evidence.available_ram_mb < min_available_ram_mb:
        failures.append("available_ram_too_low")
    if not evidence.warm_models:
        warnings.append("warm_model_evidence_missing")
    elif required_model not in evidence.warm_models:
        failures.append("required_model_not_warm")
    if evidence.active_maintenance is None:
        warnings.append("active_maintenance_unknown")
    elif evidence.active_maintenance:
        failures.append("maintenance_already_active")
    return tuple(failures), tuple(warnings)


def _add_foreground_wait_guard(
    argv: tuple[str, ...],
    *,
    wait_timeout_seconds: int,
    marker_path: str | None,
) -> tuple[str, ...]:
    timeout = _bounded_int(wait_timeout_seconds, field_name="wait_timeout_seconds", minimum=1, maximum=86400)
    guard = [
        "python",
        "-m",
        "src.local_maintenance_priority",
        "--wait-foreground-clear",
        "--timeout",
        str(timeout),
    ]
    if marker_path:
        guard.extend(("--marker-path", str(marker_path)))
    guard.append("--")
    podman_container_index = _podman_exec_container_index(argv)
    if podman_container_index is None:
        return (*guard, *argv)
    return (*argv[: podman_container_index + 1], *guard, *argv[podman_container_index + 1 :])


def _podman_exec_container_index(argv: tuple[str, ...]) -> int | None:
    if len(argv) < 4 or _basename(argv[0]).lower() != "podman" or argv[1] != "exec":
        return None
    index = 2
    options_with_value = {"-e", "--env", "-u", "--user", "-w", "--workdir"}
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return index + 1 if index + 1 < len(argv) else None
        if not token.startswith("-"):
            return index
        if token in options_with_value and index + 1 < len(argv):
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in options_with_value):
            index += 1
            continue
        index += 1
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wait for exact Gemma maintenance work before external maintenance."
    )
    parser.add_argument("--wait-foreground-clear", action="store_true")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--marker-path", default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv) if argv is not None else None)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]

    if args.wait_foreground_clear:
        result = wait_for_local_model_foreground_clear(
            path=args.marker_path,
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
        )
        if result.reason != "clear":
            print(json.dumps({"status": "timeout", "wait": result.to_dict()}, sort_keys=True), file=sys.stderr)
            return 75

    if not command:
        print(json.dumps({"status": "clear"}, sort_keys=True))
        return 0

    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(json.dumps({"status": "exec_failed", "error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 127
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
