"""Offline policy helpers for mount-backed GameDev project access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from collections.abc import Sequence
from typing import Any, Mapping


GODOT_WRITE_EXTENSIONS: tuple[str, ...] = (
    ".cfg",
    ".gd",
    ".gdshader",
    ".godot",
    ".import",
    ".ini",
    ".json",
    ".md",
    ".shader",
    ".tres",
    ".tscn",
    ".txt",
    ".uid",
    ".yml",
    ".yaml",
)

GAMEDEV_SAFE_FILE_TOOLS: tuple[str, ...] = (
    "read_file",
    "write_file",
    "edit_file",
    "ls",
    "grep",
    "glob",
)

SHELL_LIKE_TOOLS: tuple[str, ...] = (
    "bash",
    "cmd",
    "powershell",
    "python",
    "shell",
    "ssh_command",
)

GAMEDEV_COMMAND_INTENTS: dict[str, dict[str, str]] = {
    "inspect_project": {
        "risk": "read_only",
        "description": "Inspect project metadata and file layout without running the game.",
    },
    "godot_lint": {
        "risk": "bounded_tool",
        "description": "Run an explicitly configured Godot syntax/import check.",
    },
    "godot_test": {
        "risk": "bounded_tool",
        "description": "Run an explicitly configured non-export project test command.",
    },
    "godot_export": {
        "risk": "operator_go_required",
        "description": "Export/build artifacts only after an explicit operator gate.",
    },
}


@dataclass(frozen=True)
class GameDevProfileValidation:
    ok: bool
    missing_extensions: tuple[str, ...] = ()
    shell_like_tools: tuple[str, ...] = ()
    broad_host_root: bool = False
    backup_disabled: bool = False

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.missing_extensions:
            reasons.append("missing_godot_extensions")
        if self.shell_like_tools:
            reasons.append("shell_like_tools_not_allowed")
        if self.broad_host_root:
            reasons.append("broad_host_root")
        if self.backup_disabled:
            reasons.append("write_policy_backup_disabled")
        return tuple(reasons)


@dataclass(frozen=True)
class GameDevCommandDecision:
    allowed: bool
    intent: str
    risk: str = ""
    reason: str = ""
    operator_go_required: bool = False


@dataclass(frozen=True)
class GameDevCommandPlan:
    allowed: bool
    intent: str
    argv: tuple[str, ...] = ()
    cwd_virtual_path: str = ""
    risk: str = ""
    reason: str = ""
    operator_go_required: bool = False


def godot_mount_profile(
    *,
    name: str,
    host_path: str,
    virtual_path: str = "/mnt/canyon-racer",
    owner: str = "default",
) -> dict[str, Any]:
    """Return the expected writable mount profile for a Godot project folder."""

    return {
        "name": name,
        "host_path": host_path,
        "virtual_path": virtual_path,
        "owner": owner,
        "read_only": False,
        "enabled": True,
        "allowed_tools": list(GAMEDEV_SAFE_FILE_TOOLS),
        "write_policy": {
            "enabled": True,
            "create_only": False,
            "backup": True,
            "allowed_extensions": list(GODOT_WRITE_EXTENSIONS),
            "max_bytes": 1_000_000,
        },
    }


def is_broad_host_root(path: str) -> bool:
    """Return true for drive/filesystem roots that are too broad to mount."""

    value = str(path or "").strip()
    if not value:
        return True
    win = PureWindowsPath(value)
    if win.drive and str(win).rstrip("\\/") == win.drive:
        return True
    posix = PurePosixPath(value.replace("\\", "/"))
    return str(posix) == "/"


def validate_gamedev_mount_profile(mount: Mapping[str, Any]) -> GameDevProfileValidation:
    """Validate stored mount data against the safe Godot profile contract."""

    write_policy = mount.get("write_policy") if isinstance(mount.get("write_policy"), Mapping) else {}
    allowed_extensions = {
        str(ext).lower()
        for ext in (write_policy.get("allowed_extensions") or [])
        if str(ext).startswith(".")
    }
    missing = tuple(ext for ext in GODOT_WRITE_EXTENSIONS if ext not in allowed_extensions)
    tools = {str(tool).lower() for tool in (mount.get("allowed_tools") or [])}
    shell_like = tuple(tool for tool in SHELL_LIKE_TOOLS if tool in tools)
    broad_root = is_broad_host_root(str(mount.get("host_path") or ""))
    backup_disabled = bool(write_policy.get("enabled")) and not bool(write_policy.get("backup"))

    return GameDevProfileValidation(
        ok=not missing and not shell_like and not broad_root and not backup_disabled,
        missing_extensions=missing,
        shell_like_tools=shell_like,
        broad_host_root=broad_root,
        backup_disabled=backup_disabled,
    )


def decide_gamedev_command_intent(intent: str, *, operator_go: bool = False) -> GameDevCommandDecision:
    """Decide whether a named GameDev command intent may proceed.

    This deliberately accepts intent identifiers, not shell strings. Runtime
    adapters can map allowed intents to configured commands later.
    """

    normalized = str(intent or "").strip().lower()
    spec = GAMEDEV_COMMAND_INTENTS.get(normalized)
    if spec is None:
        return GameDevCommandDecision(
            allowed=False,
            intent=normalized,
            reason="unknown_command_intent",
        )
    risk = spec["risk"]
    if risk == "operator_go_required" and not operator_go:
        return GameDevCommandDecision(
            allowed=False,
            intent=normalized,
            risk=risk,
            reason="operator_go_required",
            operator_go_required=True,
        )
    return GameDevCommandDecision(
        allowed=True,
        intent=normalized,
        risk=risk,
        reason="allowed_named_intent",
        operator_go_required=risk == "operator_go_required",
    )


def build_gamedev_command_plan(
    intent: str,
    command_catalog: Mapping[str, Sequence[str]],
    *,
    cwd_virtual_path: str = "/mnt/canyon-racer",
    operator_go: bool = False,
) -> GameDevCommandPlan:
    """Create a bounded command plan for a named GameDev intent.

    The returned plan is data only. It does not execute commands and rejects
    shell-like launchers so callers do not smuggle arbitrary shell strings into
    a mount-backed project flow.
    """

    decision = decide_gamedev_command_intent(intent, operator_go=operator_go)
    if not decision.allowed:
        return GameDevCommandPlan(
            allowed=False,
            intent=decision.intent,
            risk=decision.risk,
            reason=decision.reason,
            operator_go_required=decision.operator_go_required,
        )
    if not str(cwd_virtual_path or "").startswith("/mnt/"):
        return GameDevCommandPlan(
            allowed=False,
            intent=decision.intent,
            risk=decision.risk,
            reason="cwd_must_be_virtual_mount",
        )
    raw_argv = command_catalog.get(decision.intent)
    if isinstance(raw_argv, str) or not raw_argv:
        return GameDevCommandPlan(
            allowed=False,
            intent=decision.intent,
            risk=decision.risk,
            reason="command_not_configured_as_argv",
        )
    argv = tuple(str(part) for part in raw_argv if str(part))
    if not argv:
        return GameDevCommandPlan(
            allowed=False,
            intent=decision.intent,
            risk=decision.risk,
            reason="command_not_configured_as_argv",
        )
    executable = argv[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1].removesuffix(".exe")
    if executable in SHELL_LIKE_TOOLS:
        return GameDevCommandPlan(
            allowed=False,
            intent=decision.intent,
            risk=decision.risk,
            reason="shell_like_executable_not_allowed",
        )
    return GameDevCommandPlan(
        allowed=True,
        intent=decision.intent,
        argv=argv,
        cwd_virtual_path=cwd_virtual_path,
        risk=decision.risk,
        reason="allowed_named_command_plan",
        operator_go_required=decision.operator_go_required,
    )
