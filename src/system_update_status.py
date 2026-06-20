"""Read-only update and backup status collectors for the admin System UI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from core.constants import BASE_DIR
from src.version_info import get_version_info

_DEFAULT_COMMAND_TIMEOUT_SECONDS = 8
_MAX_OUTPUT_CHARS = 4000
_SECRET_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|bearer\s+|chat_id)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class StatusCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


CommandRunner = Callable[[tuple[str, ...], int], StatusCommandResult]
ToolResolver = Callable[[str], str | None]


def _redact(value: Any) -> str:
    text = str(value or "")
    if _SECRET_RE.search(text):
        return "[redacted]"
    return text[:_MAX_OUTPUT_CHARS]


def _run_command(argv: tuple[str, ...], timeout_seconds: int) -> StatusCommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return StatusCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or "command timed out"),
            timed_out=True,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    except Exception as exc:
        return StatusCommandResult(
            exit_code=127,
            stderr=str(exc),
            duration_seconds=round(time.monotonic() - started, 3),
        )
    return StatusCommandResult(
        exit_code=int(completed.returncode),
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        duration_seconds=round(time.monotonic() - started, 3),
    )


def parse_systemctl_show(output: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for raw_line in str(output or "").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        key = key.strip()
        if key:
            props[key] = value.strip()
    return props


def parse_restic_snapshots_json(output: str, *, limit: int = 10) -> list[dict[str, Any]]:
    payload = json.loads(output or "[]")
    if not isinstance(payload, list):
        raise ValueError("restic snapshots payload must be a list")
    snapshots: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        snapshot_id = str(item.get("short_id") or item.get("id") or "")
        paths = item.get("paths") if isinstance(item.get("paths"), list) else []
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        snapshots.append(
            {
                "id": snapshot_id[:12],
                "time": str(item.get("time") or ""),
                "hostname": str(item.get("hostname") or ""),
                "paths": [str(path) for path in paths[:5]],
                "tags": [str(tag) for tag in tags[:10]],
            }
        )
    snapshots.sort(key=lambda snapshot: snapshot.get("time") or "", reverse=True)
    return snapshots[: max(1, min(int(limit), 50))]


def _tool_available(name: str, resolver: ToolResolver) -> bool:
    try:
        return bool(resolver(name))
    except Exception:
        return False


def _runner_available(path_text: str | None) -> bool:
    if not path_text:
        return False
    try:
        return Path(path_text).expanduser().exists()
    except Exception:
        return False


def _default_updater_wrapper(env: Mapping[str, str]) -> str:
    return str(env.get("ODYSSEUS_AUTO_UPDATE_WRAPPER") or "/home/homebase/.local/bin/odysseus-auto-update.sh")


def _restic_repository(env: Mapping[str, str]) -> str:
    explicit = env.get("RESTIC_REPOSITORY") or env.get("ODYSSEUS_RESTIC_REPOSITORY")
    if explicit:
        return str(explicit)
    default_repo = Path("/mnt/backup/restic/homeserver")
    return str(default_repo) if default_repo.exists() else ""


def _systemd_unit_status(
    unit_name: str,
    *,
    runner: CommandRunner,
    tool_resolver: ToolResolver,
) -> dict[str, Any]:
    if not _tool_available("systemctl", tool_resolver):
        return {"available": False, "reason": "systemctl not available"}
    result = runner(
        (
            "systemctl",
            "--user",
            "show",
            unit_name,
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "UnitFileState",
            "-p",
            "Result",
            "-p",
            "ExecMainStatus",
            "-p",
            "ExecMainStartTimestamp",
            "-p",
            "ExecMainExitTimestamp",
            "-p",
            "NextElapseUSecRealtime",
            "-p",
            "LastTriggerUSec",
        ),
        _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    if not result.ok:
        return {
            "available": False,
            "reason": _redact(result.stderr or result.stdout or "systemctl query failed"),
            "exit_code": result.exit_code,
        }
    props = parse_systemctl_show(result.stdout)
    return {
        "available": True,
        "unit": unit_name,
        "load_state": props.get("LoadState") or "",
        "active_state": props.get("ActiveState") or "unknown",
        "sub_state": props.get("SubState") or "",
        "unit_file_state": props.get("UnitFileState") or "",
        "result": props.get("Result") or "",
        "exec_main_status": props.get("ExecMainStatus") or "",
        "started_at": props.get("ExecMainStartTimestamp") or "",
        "finished_at": props.get("ExecMainExitTimestamp") or "",
        "next_run_at": props.get("NextElapseUSecRealtime") or "",
        "last_triggered_at": props.get("LastTriggerUSec") or "",
    }


def _restic_snapshot_status(
    *,
    env: Mapping[str, str],
    runner: CommandRunner,
    tool_resolver: ToolResolver,
) -> dict[str, Any]:
    repo = _restic_repository(env)
    if not repo:
        return {"available": False, "reason": "RESTIC_REPOSITORY is not configured", "snapshots": []}
    if not _tool_available("restic", tool_resolver):
        return {"available": False, "reason": "restic not available", "repository": repo, "snapshots": []}
    result = runner(
        ("restic", "-r", repo, "snapshots", "--json", "--latest", "10"),
        _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    if not result.ok:
        return {
            "available": False,
            "reason": _redact(result.stderr or result.stdout or "restic snapshots failed"),
            "repository": repo,
            "snapshots": [],
            "exit_code": result.exit_code,
        }
    try:
        snapshots = parse_restic_snapshots_json(result.stdout, limit=10)
    except Exception as exc:
        return {
            "available": False,
            "reason": f"restic snapshots parse failed: {_redact(exc)}",
            "repository": repo,
            "snapshots": [],
        }
    return {
        "available": True,
        "repository": repo,
        "latest_snapshot": snapshots[0] if snapshots else None,
        "snapshots": snapshots,
    }


def _version_status(version_provider: Callable[..., dict[str, Any]], *, force_refresh: bool) -> dict[str, Any]:
    try:
        return dict(version_provider(force_refresh=force_refresh))
    except TypeError:
        return dict(version_provider())
    except Exception as exc:
        return {"status": "unknown", "error": _redact(exc)}


def collect_system_update_status(
    *,
    env: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
    tool_resolver: ToolResolver | None = None,
    version_provider: Callable[..., dict[str, Any]] = get_version_info,
    force_version_refresh: bool = False,
) -> dict[str, Any]:
    """Collect bounded, read-only update/backup status for the admin UI."""

    effective_env = dict(os.environ if env is None else env)
    command_runner = runner or _run_command
    resolver = tool_resolver or shutil.which
    version = _version_status(version_provider, force_refresh=force_version_refresh)
    wrapper = _default_updater_wrapper(effective_env)
    wrapper_available = _runner_available(wrapper)
    timer = _systemd_unit_status(
        "odysseus-auto-update.timer",
        runner=command_runner,
        tool_resolver=resolver,
    )
    service = _systemd_unit_status(
        "odysseus-auto-update.service",
        runner=command_runner,
        tool_resolver=resolver,
    )
    backup_timer = _systemd_unit_status(
        "odysseus-homeserver-backup.timer",
        runner=command_runner,
        tool_resolver=resolver,
    )
    backup_service = _systemd_unit_status(
        "odysseus-homeserver-backup.service",
        runner=command_runner,
        tool_resolver=resolver,
    )
    backups = _restic_snapshot_status(
        env=effective_env,
        runner=command_runner,
        tool_resolver=resolver,
    )
    live_enabled = str(effective_env.get("ODYSSEUS_UPDATER_LIVE_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    update_now_reasons = []
    if not wrapper_available:
        update_now_reasons.append("updater wrapper not available")
    if not live_enabled:
        update_now_reasons.append("ODYSSEUS_UPDATER_LIVE_ENABLED is not enabled")
    if backups.get("available") is not True:
        update_now_reasons.append("backup snapshot status is not available")
    backup_now_reasons = []
    if not _tool_available("systemctl", resolver):
        backup_now_reasons.append("systemctl not available")
    if backup_service.get("available") is not True or backup_service.get("load_state") == "not-found":
        backup_now_reasons.append("backup systemd service is not available")
    if service.get("available") is not True or service.get("load_state") == "not-found":
        update_now_reasons.append("update systemd service is not available")
    return {
        "status": "success",
        "version": version,
        "updater": {
            "runner_path": wrapper,
            "runner_available": wrapper_available,
            "live_enabled": live_enabled,
            "service": service,
        },
        "schedule": {
            "timer": timer,
            "backup_timer": backup_timer,
        },
        "backups": {
            **backups,
            "service": backup_service,
        },
        "capabilities": {
            "systemctl": _tool_available("systemctl", resolver),
            "restic": _tool_available("restic", resolver),
            "podman": _tool_available("podman", resolver),
            "docker": _tool_available("docker", resolver),
        },
        "actions": {
            "check_updates_enabled": True,
            "backup_now_enabled": not backup_now_reasons,
            "backup_now_disabled_reasons": backup_now_reasons,
            "update_now_enabled": not update_now_reasons,
            "update_now_disabled_reasons": update_now_reasons,
        },
    }


def start_system_update_action(
    action: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
    tool_resolver: ToolResolver | None = None,
    version_provider: Callable[..., dict[str, Any]] = get_version_info,
) -> dict[str, Any]:
    """Start one approved system update/backup action without blocking for completion."""

    action_map = {
        "backup_now": {
            "unit": "odysseus-homeserver-backup.service",
            "enabled_key": "backup_now_enabled",
            "reasons_key": "backup_now_disabled_reasons",
        },
        "update_now": {
            "unit": "odysseus-auto-update.service",
            "enabled_key": "update_now_enabled",
            "reasons_key": "update_now_disabled_reasons",
        },
    }
    if action not in action_map:
        raise ValueError(f"unsupported system update action: {action}")

    effective_env = dict(os.environ if env is None else env)
    command_runner = runner or _run_command
    resolver = tool_resolver or shutil.which
    status = collect_system_update_status(
        env=effective_env,
        runner=command_runner,
        tool_resolver=resolver,
        version_provider=version_provider,
        force_version_refresh=(action == "update_now"),
    )
    action_spec = action_map[action]
    blockers = list(status.get("actions", {}).get(action_spec["reasons_key"], []))
    if status.get("actions", {}).get(action_spec["enabled_key"]) is not True:
        return {
            "status": "blocked",
            "action": action,
            "started": False,
            "blockers": blockers or ["action is not enabled"],
            "system_status": status,
        }

    unit = action_spec["unit"]
    result = command_runner(
        ("systemctl", "--user", "start", "--no-block", unit),
        _DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    if not result.ok:
        return {
            "status": "failed",
            "action": action,
            "started": False,
            "blockers": [_redact(result.stderr or result.stdout or "systemctl start failed")],
            "result": {
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_seconds": result.duration_seconds,
            },
            "system_status": status,
        }
    return {
        "status": "started",
        "action": action,
        "started": True,
        "unit": unit,
        "result": {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
        },
        "system_status": collect_system_update_status(
            env=effective_env,
            runner=command_runner,
            tool_resolver=resolver,
            version_provider=version_provider,
            force_version_refresh=(action == "update_now"),
        ),
    }
