"""Safe planning facade for Odysseus-controlled Nextcloud operations.

This module intentionally does not execute WebDAV, rclone, occ, Podman, or
filesystem commands. It defines the narrow control surface that can later be
exposed as an MCP server or internal tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.nextcloud_source_provider import assess_nextcloud_source_provider
from src.nextcloud_transfer_readiness import ALLOWED_RUNTIME_BACKENDS


READ_ONLY_ACTIONS = ("list", "stat", "read", "download")
REVIEW_GATED_ACTIONS = ("copy", "write_sidecar", "project_tags")
FORBIDDEN_ACTIONS = ("delete", "move", "overwrite", "occ_admin")
ALLOWED_ACTIONS = READ_ONLY_ACTIONS + REVIEW_GATED_ACTIONS + FORBIDDEN_ACTIONS


@dataclass(frozen=True, slots=True)
class NextcloudControlPlan:
    action: str
    status: str
    provider_id: str
    runtime_backend: str | None
    actor: str | None
    read_only: bool
    review_gated: bool
    live_execution_allowed: bool
    reasons: tuple[str, ...]
    next_actions: tuple[str, ...]
    errors: tuple[str, ...]
    blocked_operations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status,
            "provider_id": self.provider_id,
            "runtime_backend": self.runtime_backend,
            "actor": self.actor,
            "read_only": self.read_only,
            "review_gated": self.review_gated,
            "live_execution_allowed": self.live_execution_allowed,
            "reasons": self.reasons,
            "next_actions": self.next_actions,
            "errors": self.errors,
            "blocked_operations": self.blocked_operations,
        }


def plan_nextcloud_control_action(config: Mapping[str, Any]) -> NextcloudControlPlan:
    """Return a safe operation plan without touching live Nextcloud."""

    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")

    source = assess_nextcloud_source_provider(config.get("source_provider") or config)
    errors = [issue.code for issue in source.errors]
    reasons: list[str] = []
    next_actions: list[str] = []

    action = _normalize_action(config.get("action"), errors)
    runtime_backend = _normalize_runtime_backend(config.get("runtime_backend"), errors)
    operator_live_go = bool(config.get("operator_live_go"))
    review_approved = bool(config.get("review_approved"))

    read_only = action in READ_ONLY_ACTIONS
    review_gated = action in REVIEW_GATED_ACTIONS
    forbidden = action in FORBIDDEN_ACTIONS

    if source.status == "ready":
        reasons.append("source_provider_ready")
    elif source.status == "partial":
        reasons.append("source_provider_partial")
    elif source.status == "deferred":
        reasons.append("source_provider_deferred")
    else:
        reasons.append("source_provider_blocked")
        errors.append("source_provider_not_ready")

    if runtime_backend:
        reasons.append("podman_runtime_confirmed")
    if read_only:
        reasons.append("read_only_action")
    if review_gated:
        reasons.append("review_gated_action")
    if forbidden:
        errors.append("action_forbidden")
    if review_gated and not review_approved:
        reasons.append("review_approval_missing")
        next_actions.append("Collect explicit review approval before planning any Nextcloud write.")
    if not operator_live_go:
        reasons.append("operator_live_go_missing")

    live_execution_allowed = bool(
        not errors
        and runtime_backend
        and source.status in {"ready", "partial"}
        and (read_only or (review_gated and review_approved))
        and operator_live_go
    )
    status = _derive_status(
        errors=errors,
        source_status=source.status,
        action=action,
        read_only=read_only,
        review_gated=review_gated,
        review_approved=review_approved,
        operator_live_go=operator_live_go,
    )
    next_actions.extend(
        _next_actions(
            status=status,
            action=action,
            runtime_backend=runtime_backend,
            read_only=read_only,
            review_gated=review_gated,
            review_approved=review_approved,
            operator_live_go=operator_live_go,
        )
    )

    return NextcloudControlPlan(
        action=action or "unknown",
        status=status,
        provider_id=source.provider_id,
        runtime_backend=runtime_backend,
        actor=source.actor,
        read_only=read_only,
        review_gated=review_gated,
        live_execution_allowed=live_execution_allowed,
        reasons=tuple(dict.fromkeys(reasons)),
        next_actions=tuple(dict.fromkeys(next_actions)),
        errors=tuple(dict.fromkeys(errors)),
        blocked_operations=("delete", "move", "overwrite", "occ_admin", "docker_runtime"),
    )


def _normalize_action(value: Any, errors: list[str]) -> str | None:
    action = str(value or "").strip().lower()
    if not action:
        errors.append("action_missing")
        return None
    if action not in ALLOWED_ACTIONS:
        errors.append("action_unsupported")
        return None
    return action


def _normalize_runtime_backend(value: Any, errors: list[str]) -> str | None:
    backend = str(value or "podman_pod").strip().lower()
    if backend in {"podman", "pods"}:
        backend = "podman_pod"
    if backend not in ALLOWED_RUNTIME_BACKENDS:
        errors.append("runtime_backend_unsupported")
        return None
    return backend


def _derive_status(
    *,
    errors: list[str],
    source_status: str,
    action: str | None,
    read_only: bool,
    review_gated: bool,
    review_approved: bool,
    operator_live_go: bool,
) -> str:
    if source_status == "deferred":
        return "deferred"
    if errors:
        return "blocked"
    if read_only and operator_live_go:
        return "ready_for_live_go"
    if review_gated and review_approved and operator_live_go:
        return "ready_for_live_go"
    if action:
        return "needs_operator_input"
    return "blocked"


def _next_actions(
    *,
    status: str,
    action: str | None,
    runtime_backend: str | None,
    read_only: bool,
    review_gated: bool,
    review_approved: bool,
    operator_live_go: bool,
) -> tuple[str, ...]:
    actions: list[str] = []
    if not action:
        actions.append("Choose a supported Nextcloud control action.")
    if not runtime_backend:
        actions.append("Use the Podman/pod runtime path; Docker-based control is out of scope.")
    if review_gated and not review_approved:
        actions.append("Approve the concrete copy/sidecar/tag projection plan before live execution.")
    if status == "blocked":
        actions.append("Fix blocking Nextcloud control errors before asking for live Go.")
    elif not operator_live_go:
        actions.append("Ask the operator for explicit live Go or keep the action as dry-run only.")
    elif read_only:
        actions.append("Proceed with the smallest read-only live probe and redacted evidence.")
    else:
        actions.append("Proceed only with the smallest review-approved live batch and redacted evidence.")
    return tuple(actions)
