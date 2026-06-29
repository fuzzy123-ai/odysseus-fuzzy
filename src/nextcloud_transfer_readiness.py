"""Offline transfer readiness planner for private Nextcloud ingestion."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from src.nextcloud_source_provider import (
    NextcloudSourceReadinessReport,
    assess_nextcloud_source_provider,
)
from src.nextcloud_source_policy import SourcePolicyIssue, validate_root_path


ALLOWED_TRANSFER_TOOLS = (
    "nextcloud_sync",
    "rclone_webdav",
    "rsync_ssh",
    "server_side_copy",
)
ALLOWED_RUNTIME_BACKENDS = (
    "podman_pod",
    "rootless_podman",
)
READINESS_STATUSES = ("ready_for_live_go", "needs_operator_input", "blocked", "deferred")
_FORBIDDEN_DRY_RUN_TOKENS = (
    "--delete",
    "--delete-before",
    "--delete-after",
    "--remove",
    "--remove-source-files",
    "--inplace",
    "--force",
    "--chmod",
    " docker ",
    " docker-compose ",
    " rm ",
    " mv ",
)
_SAFE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")


@dataclass(frozen=True, slots=True)
class NextcloudTransferReadinessPlan:
    status: str
    provider_id: str
    transfer_tool: str | None
    runtime_backend: str | None
    source_confirmed: bool
    target_confirmed: bool
    disk_budget_verified: bool
    dry_run_no_delete: bool
    operator_live_go: bool
    reasons: tuple[str, ...]
    next_actions: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    blocked_live_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider_id": self.provider_id,
            "transfer_tool": self.transfer_tool,
            "runtime_backend": self.runtime_backend,
            "source_confirmed": self.source_confirmed,
            "target_confirmed": self.target_confirmed,
            "disk_budget_verified": self.disk_budget_verified,
            "dry_run_no_delete": self.dry_run_no_delete,
            "operator_live_go": self.operator_live_go,
            "reasons": self.reasons,
            "next_actions": self.next_actions,
            "errors": self.errors,
            "warnings": self.warnings,
            "blocked_live_actions": self.blocked_live_actions,
        }


def build_nextcloud_transfer_readiness_plan(
    config: Mapping[str, Any],
) -> NextcloudTransferReadinessPlan:
    """Validate operator transfer inputs without touching live Nextcloud data."""

    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")

    source = _source_report(config.get("source_provider") or config)
    errors = [issue.code for issue in source.errors]
    warnings = [issue.code for issue in source.warnings]
    reasons: list[str] = []
    next_actions: list[str] = []

    transfer_tool = _normalize_transfer_tool(config.get("transfer_tool"), errors)
    runtime_backend = _normalize_runtime_backend(config.get("runtime_backend"), errors)
    source_confirmed = _validate_source_confirmation(config, source, errors)
    target_confirmed = _validate_target(config.get("target_path"), errors)
    disk_budget_verified = _validate_disk_budget(config, errors, warnings)
    dry_run_no_delete = _validate_dry_run(config, errors)
    operator_live_go = bool(config.get("operator_live_go"))

    if source.status == "deferred":
        reasons.append("source_provider_deferred")
    elif source.status == "ready":
        reasons.append("source_provider_ready")
    elif source.status == "partial":
        reasons.append("source_provider_partial")
    else:
        reasons.append("source_provider_blocked")

    if transfer_tool:
        reasons.append("transfer_tool_chosen")
    if runtime_backend:
        reasons.append("podman_runtime_confirmed")
    if source_confirmed and target_confirmed:
        reasons.append("source_and_target_paths_confirmed")
    if disk_budget_verified:
        reasons.append("disk_budget_verified")
    if dry_run_no_delete:
        reasons.append("dry_run_no_delete")
    if not operator_live_go:
        reasons.append("operator_live_go_missing")

    status = _derive_status(
        source_status=source.status,
        errors=errors,
        transfer_tool=transfer_tool,
        runtime_backend=runtime_backend,
        source_confirmed=source_confirmed,
        target_confirmed=target_confirmed,
        disk_budget_verified=disk_budget_verified,
        dry_run_no_delete=dry_run_no_delete,
        operator_live_go=operator_live_go,
    )
    next_actions.extend(
        _next_actions(
            status=status,
            transfer_tool=transfer_tool,
            runtime_backend=runtime_backend,
            source_confirmed=source_confirmed,
            target_confirmed=target_confirmed,
            disk_budget_verified=disk_budget_verified,
            dry_run_no_delete=dry_run_no_delete,
            operator_live_go=operator_live_go,
        )
    )

    return NextcloudTransferReadinessPlan(
        status=status,
        provider_id=source.provider_id,
        transfer_tool=transfer_tool,
        runtime_backend=runtime_backend,
        source_confirmed=source_confirmed,
        target_confirmed=target_confirmed,
        disk_budget_verified=disk_budget_verified,
        dry_run_no_delete=dry_run_no_delete,
        operator_live_go=operator_live_go,
        reasons=tuple(dict.fromkeys(reasons)),
        next_actions=tuple(dict.fromkeys(next_actions)),
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        blocked_live_actions=(
            "live_transfer",
            "delete_move_or_overwrite",
            "credential_capture",
            "network_sync",
            "memory_or_graph_write",
            "docker_runtime",
        ),
    )


def _source_report(config_or_report: Any) -> NextcloudSourceReadinessReport:
    if isinstance(config_or_report, NextcloudSourceReadinessReport):
        return config_or_report
    return assess_nextcloud_source_provider(config_or_report)


def _normalize_transfer_tool(value: Any, errors: list[str]) -> str | None:
    tool = str(value or "").strip().lower()
    if not tool:
        errors.append("transfer_tool_missing")
        return None
    if tool not in ALLOWED_TRANSFER_TOOLS:
        errors.append("transfer_tool_unsupported")
        return None
    return tool


def _normalize_runtime_backend(value: Any, errors: list[str]) -> str | None:
    backend = str(value or "podman_pod").strip().lower()
    if backend in {"podman", "pods"}:
        backend = "podman_pod"
    if backend not in ALLOWED_RUNTIME_BACKENDS:
        errors.append("runtime_backend_unsupported")
        return None
    return backend


def _validate_source_confirmation(
    config: Mapping[str, Any],
    source: NextcloudSourceReadinessReport,
    errors: list[str],
) -> bool:
    label = str(config.get("source_label") or "").strip().lower()
    confirmed = bool(config.get("source_path_confirmed"))
    has_source_location = bool(source.root_path or source.webdav_endpoint)
    if not label or not _SAFE_LABEL_RE.fullmatch(label):
        errors.append("source_label_invalid")
        return False
    if not has_source_location:
        errors.append("source_location_missing")
        return False
    if not confirmed:
        errors.append("source_path_not_confirmed")
        return False
    return True


def _validate_target(value: Any, errors: list[str]) -> bool:
    try:
        validate_root_path(value)
    except ValueError:
        errors.append("target_path_invalid")
        return False
    return True


def _validate_disk_budget(
    config: Mapping[str, Any],
    errors: list[str],
    warnings: list[str],
) -> bool:
    try:
        expected_bytes = int(config.get("expected_bytes", 0))
        available_bytes = int(config.get("available_bytes", 0))
        reserve_bytes = int(config.get("reserve_bytes", 0))
    except (TypeError, ValueError):
        errors.append("disk_budget_invalid")
        return False
    if expected_bytes <= 0 or available_bytes <= 0 or reserve_bytes < 0:
        errors.append("disk_budget_invalid")
        return False
    if expected_bytes < 100 * 1024**3:
        warnings.append("expected_bytes_below_100gb_scope")
    if available_bytes - reserve_bytes < expected_bytes:
        errors.append("disk_budget_insufficient")
        return False
    return True


def _validate_dry_run(config: Mapping[str, Any], errors: list[str]) -> bool:
    command = " ".join(str(config.get("dry_run_command") or "").split())
    if not command:
        errors.append("dry_run_command_missing")
        return False
    lowered = f" {command.lower()} "
    if not bool(config.get("dry_run_reviewed")):
        errors.append("dry_run_not_reviewed")
        return False
    if not any(token in lowered for token in ("--dry-run", "--dryrun", " --checksum ", " --size-only ")):
        errors.append("dry_run_marker_missing")
        return False
    if any(token in lowered for token in _FORBIDDEN_DRY_RUN_TOKENS):
        errors.append("dry_run_contains_destructive_token")
        return False
    return True


def _derive_status(
    *,
    source_status: str,
    errors: list[str],
    transfer_tool: str | None,
    runtime_backend: str | None,
    source_confirmed: bool,
    target_confirmed: bool,
    disk_budget_verified: bool,
    dry_run_no_delete: bool,
    operator_live_go: bool,
) -> str:
    if source_status == "deferred":
        return "deferred"
    if errors:
        return "blocked"
    ready_without_go = all(
        (
            source_status in {"ready", "partial"},
            transfer_tool,
            runtime_backend,
            source_confirmed,
            target_confirmed,
            disk_budget_verified,
            dry_run_no_delete,
        )
    )
    if ready_without_go and not operator_live_go:
        return "needs_operator_input"
    if ready_without_go and operator_live_go:
        return "ready_for_live_go"
    return "needs_operator_input"


def _next_actions(
    *,
    status: str,
    transfer_tool: str | None,
    runtime_backend: str | None,
    source_confirmed: bool,
    target_confirmed: bool,
    disk_budget_verified: bool,
    dry_run_no_delete: bool,
    operator_live_go: bool,
) -> tuple[str, ...]:
    actions: list[str] = []
    if not transfer_tool:
        actions.append("Choose a copy-only transfer tool before writing a live runbook.")
    if not runtime_backend:
        actions.append("Use the Podman/pod runtime path; Docker-based Nextcloud control is out of scope.")
    if not source_confirmed:
        actions.append("Confirm the redacted source label and source path out of band.")
    if not target_confirmed:
        actions.append("Confirm a normalized homeserver target path.")
    if not disk_budget_verified:
        actions.append("Verify expected bytes, available bytes, and reserved free space.")
    if not dry_run_no_delete:
        actions.append("Review a no-delete dry-run command before any live transfer.")
    if status == "blocked":
        actions.append("Fix blocking transfer-readiness errors before asking for live Go.")
    elif not operator_live_go:
        actions.append("Ask the operator for explicit live Go or mark the transfer gate deferred.")
    else:
        actions.append("Proceed only with the smallest approved live batch and redacted evidence capture.")
    return tuple(actions)
