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


@dataclass(frozen=True)
class GameDevMountReport:
    virtual_path: str
    owner: str
    ok: bool
    status: str
    reasons: tuple[str, ...] = ()
    host_path_visible: bool = False


@dataclass(frozen=True)
class GameDevWriteSmokePlan:
    ready: bool
    virtual_path: str
    cleanup_virtual_path: str = ""
    owner: str = "default"
    byte_count: int = 0
    reason: str = ""
    operator_go_required: bool = True
    host_path_visible: bool = False


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


def build_gamedev_mount_report(
    mounts: Sequence[Mapping[str, Any]],
    *,
    virtual_path: str = "/mnt/canyon-racer",
    owner: str = "default",
) -> GameDevMountReport:
    """Validate stored mount-like data without exposing host paths."""

    expected_virtual = str(virtual_path or "").rstrip("/") or "/mnt/canyon-racer"
    expected_owner = str(owner or "default")
    candidates = [
        mount for mount in mounts
        if str(mount.get("virtual_path") or "").rstrip("/") == expected_virtual
        and str(mount.get("owner") or "default") == expected_owner
        and bool(mount.get("enabled", True))
    ]
    if not candidates:
        return GameDevMountReport(
            virtual_path=expected_virtual,
            owner=expected_owner,
            ok=False,
            status="missing_mount",
            reasons=("missing_mount",),
        )
    validation = validate_gamedev_mount_profile(candidates[0])
    return GameDevMountReport(
        virtual_path=expected_virtual,
        owner=expected_owner,
        ok=validation.ok,
        status="go" if validation.ok else "partial",
        reasons=validation.reasons,
        host_path_visible=False,
    )


def public_gamedev_mount_report(report: GameDevMountReport) -> dict[str, Any]:
    """Return report data that is safe to persist or show in operator output."""

    return {
        "virtual_path": report.virtual_path,
        "owner": report.owner,
        "ok": report.ok,
        "status": report.status,
        "reasons": list(report.reasons),
        "host_path_visible": False,
    }


def _normalize_virtual_project_path(path: Any) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.endswith("/") and value != "/":
        value = value.rstrip("/")
    return value


def build_gamedev_write_smoke_plan(
    mount: Mapping[str, Any],
    *,
    virtual_path: str = "/mnt/canyon-racer/.odysseus-write-smoke.txt",
    content: str = "odysseus gamedev mount write smoke\n",
    owner: str = "default",
    operator_go: bool = False,
) -> GameDevWriteSmokePlan:
    """Validate a reversible GameDev write-smoke target without writing it."""

    normalized_path = _normalize_virtual_project_path(virtual_path)
    mount_virtual = _normalize_virtual_project_path(mount.get("virtual_path") or "/mnt/canyon-racer")
    expected_owner = str(mount.get("owner") or "default")
    if expected_owner != str(owner or "default"):
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=str(owner or "default"),
            reason="owner_mismatch",
        )
    if normalized_path == mount_virtual or not normalized_path.startswith(f"{mount_virtual}/"):
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=expected_owner,
            reason="target_must_stay_under_virtual_mount",
        )
    if any(part in (".", "..") for part in normalized_path.split("/") if part):
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=expected_owner,
            reason="target_must_not_contain_dot_segments",
        )
    validation = validate_gamedev_mount_profile(mount)
    if not validation.ok:
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=expected_owner,
            reason="mount_profile_not_write_ready",
        )
    write_policy = mount.get("write_policy") if isinstance(mount.get("write_policy"), Mapping) else {}
    if not bool(write_policy.get("enabled")):
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=expected_owner,
            reason="write_policy_disabled",
        )
    ext = PurePosixPath(normalized_path).suffix.lower()
    allowed_extensions = {
        str(item).lower() if str(item).startswith(".") else f".{str(item).lower()}"
        for item in (write_policy.get("allowed_extensions") or [])
    }
    if ext not in allowed_extensions:
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=expected_owner,
            reason="extension_not_allowed",
        )
    body = str(content or "")
    byte_count = len(body.encode("utf-8"))
    try:
        max_bytes = int(write_policy.get("max_bytes") or 0)
    except (TypeError, ValueError):
        max_bytes = 0
    if byte_count <= 0 or byte_count > max_bytes:
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            owner=expected_owner,
            byte_count=byte_count,
            reason="payload_size_not_allowed",
        )
    if not operator_go:
        return GameDevWriteSmokePlan(
            ready=False,
            virtual_path=normalized_path,
            cleanup_virtual_path=normalized_path,
            owner=expected_owner,
            byte_count=byte_count,
            reason="operator_go_required",
        )
    return GameDevWriteSmokePlan(
        ready=True,
        virtual_path=normalized_path,
        cleanup_virtual_path=normalized_path,
        owner=expected_owner,
        byte_count=byte_count,
        reason="ready_for_reversible_write_smoke",
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
