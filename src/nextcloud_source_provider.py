"""Offline readiness validator for read-only Nextcloud source providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.nextcloud_source_policy import (
    RECOMMENDED_ACTOR,
    REQUIRED_FOLDERS,
    SourcePolicyIssue,
    normalize_actor,
    validate_folder_names,
    validate_permission_scope,
    validate_provider_id,
    validate_root_path,
    validate_webdav_endpoint,
)


ALLOWED_STATUSES = ("ready", "partial", "blocked", "deferred")


@dataclass(frozen=True, slots=True)
class NextcloudSourceReadinessReport:
    provider_id: str
    status: str
    actor: str | None
    permission_scope: tuple[str, ...]
    root_path: str | None
    webdav_endpoint: str | None
    folders: tuple[str, ...]
    reasons: tuple[str, ...]
    next_actions: tuple[str, ...]
    errors: tuple[SourcePolicyIssue, ...]
    warnings: tuple[SourcePolicyIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "actor": self.actor,
            "permission_scope": self.permission_scope,
            "root_path": self.root_path,
            "webdav_endpoint": self.webdav_endpoint,
            "folders": self.folders,
            "reasons": self.reasons,
            "next_actions": self.next_actions,
            "errors": tuple(issue.code for issue in self.errors),
            "warnings": tuple(issue.code for issue in self.warnings),
        }


def assess_nextcloud_source_provider(config: Mapping[str, Any]) -> NextcloudSourceReadinessReport:
    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")

    errors: list[SourcePolicyIssue] = []
    warnings: list[SourcePolicyIssue] = []
    reasons: list[str] = []
    next_actions: list[str] = []

    provider_id = _capture(validate_provider_id, config.get("provider_id"), field="provider_id", errors=errors)
    actor = _capture(normalize_actor, config.get("actor"), field="actor", errors=errors)
    permission_scope = _capture(
        validate_permission_scope,
        config.get("permission_scope"),
        field="permission_scope",
        errors=errors,
        default=(),
    )

    folders = _capture(
        validate_folder_names,
        config.get("folders"),
        field="folders",
        errors=errors,
        default=(),
    )

    root_path = None
    webdav_endpoint = None

    if provider_id == "nextcloud_sync":
        root_path = _capture(validate_root_path, config.get("root_path"), field="root_path", errors=errors)
    elif provider_id == "nextcloud_webdav":
        webdav_endpoint = _capture(
            validate_webdav_endpoint,
            config.get("webdav_endpoint"),
            field="webdav_endpoint",
            errors=errors,
        )

    if bool(config.get("enabled")) is False:
        reasons.append("provider_disabled_by_config")
        next_actions.append("Keep the provider disabled until the fake/offline configuration is reviewed.")

    if actor and actor != RECOMMENDED_ACTOR:
        warnings.append(
            SourcePolicyIssue(
                code="non_recommended_actor",
                message=f"actor should usually be {RECOMMENDED_ACTOR}",
                severity="warning",
            )
        )
        reasons.append("actor_not_recommended")
        next_actions.append(f"Switch actor to {RECOMMENDED_ACTOR} for designated intake ownership.")

    if folders and tuple(folders) == REQUIRED_FOLDERS:
        pass

    if not errors and not warnings and "provider_disabled_by_config" not in reasons:
        reasons.append("offline_readonly_policy_satisfied")

    status = _derive_status(errors=errors, warnings=warnings, reasons=reasons)
    next_actions = list(dict.fromkeys(next_actions or _default_next_actions(status, provider_id)))
    reasons = list(dict.fromkeys(reasons))

    return NextcloudSourceReadinessReport(
        provider_id=provider_id or "unknown",
        status=status,
        actor=actor,
        permission_scope=tuple(permission_scope),
        root_path=root_path,
        webdav_endpoint=webdav_endpoint,
        folders=tuple(folders),
        reasons=tuple(reasons),
        next_actions=tuple(next_actions),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def summarize_nextcloud_source_provider(config: Mapping[str, Any]) -> dict[str, Any]:
    return assess_nextcloud_source_provider(config).to_dict()


def _capture(
    validator,
    value: Any,
    *,
    field: str,
    errors: list[SourcePolicyIssue],
    default: Any = None,
):
    try:
        return validator(value)
    except ValueError as exc:
        errors.append(SourcePolicyIssue(code=f"invalid_{field}", message=str(exc), severity="error"))
        return default


def _derive_status(
    *,
    errors: list[SourcePolicyIssue],
    warnings: list[SourcePolicyIssue],
    reasons: list[str],
) -> str:
    if any(reason == "provider_disabled_by_config" for reason in reasons):
        return "deferred"
    if errors:
        return "blocked"
    if warnings:
        return "partial"
    return "ready"


def _default_next_actions(status: str, provider_id: str | None) -> tuple[str, ...]:
    if status == "ready":
        return ("Proceed with offline review-gated planning only; do not execute network sync.",)
    if status == "partial":
        return ("Resolve the warning-level configuration drift before enabling any projection workflow.",)
    if status == "blocked":
        target = provider_id or "the provider"
        return (f"Fix the invalid offline configuration for {target} before any further readiness step.",)
    return ("Leave the provider deferred and revisit only when a safe offline config review is needed.",)
