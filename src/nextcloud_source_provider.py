"""Offline readiness validator for read-only Nextcloud source providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.chat_security_state import ChatSecurityState
from src.runtime_event_envelope import build_runtime_event, stable_payload_hash
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
from src.secure_policy_gate import PolicyDecision, PolicyGateResult, decide_source_access


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
    secure_policy_decision: str = ""
    secure_policy_allowed: bool = True
    secure_policy_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        runtime_event = _nextcloud_readiness_runtime_event(self)
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
            "secure_policy_decision": self.secure_policy_decision,
            "secure_policy_allowed": self.secure_policy_allowed,
            "secure_policy_reason": self.secure_policy_reason,
            "correlation_id": runtime_event["correlation_id"],
            "runtime_event": runtime_event,
        }


def assess_nextcloud_source_provider(
    config: Mapping[str, Any],
    *,
    security_mode: Any = "normal",
    source_classification: Any = "private",
) -> NextcloudSourceReadinessReport:
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

    secure_policy = _decide_private_source_policy(
        provider_id=provider_id or "nextcloud_source",
        actor=actor or RECOMMENDED_ACTOR,
        security_mode=security_mode,
        source_classification=source_classification,
    )
    if not secure_policy.allowed:
        reasons.append(secure_policy.block_reason)
        next_actions.append(secure_policy.next_action)

    if not errors and not warnings and "provider_disabled_by_config" not in reasons:
        reasons.append("offline_readonly_policy_satisfied")

    status = _derive_status(errors=errors, warnings=warnings, reasons=reasons)
    if not secure_policy.allowed:
        status = "partial" if secure_policy.decision == PolicyDecision.REQUIRE_REVIEW else "blocked"
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
        secure_policy_decision=secure_policy.decision.value,
        secure_policy_allowed=secure_policy.allowed,
        secure_policy_reason=secure_policy.block_reason,
    )


def summarize_nextcloud_source_provider(config: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    return assess_nextcloud_source_provider(config, **kwargs).to_dict()


def _decide_private_source_policy(
    *,
    provider_id: str,
    actor: str,
    security_mode: Any,
    source_classification: Any,
) -> PolicyGateResult:
    state = ChatSecurityState.create(
        chat_id=f"{provider_id}-readiness",
        thread_id=f"{provider_id}-readiness",
        security_mode=str(security_mode or "normal"),
        created_at="2026-06-22T00:00:00Z",
        requested_by=actor,
    )
    return decide_source_access(
        state=state,
        source_classifications=[source_classification],
    )


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


def _nextcloud_event_status(status: str) -> str:
    if status == "ready":
        return "success"
    if status == "partial":
        return "warn"
    if status == "blocked":
        return "blocked"
    if status == "deferred":
        return "skipped"
    return "unknown"


def _nextcloud_correlation_id(report: NextcloudSourceReadinessReport) -> str:
    return stable_payload_hash(
        {
            "surface": "nextcloud",
            "provider_id": report.provider_id,
            "status": report.status,
        }
    )


def _nextcloud_readiness_runtime_event(report: NextcloudSourceReadinessReport) -> dict[str, Any]:
    status = _nextcloud_event_status(report.status)
    return build_runtime_event(
        surface="universal_inbox",
        component="nextcloud_source_provider",
        event_type="nextcloud_readiness",
        status=status,
        severity="warn" if status in {"warn", "blocked"} else "info",
        owner_scope="nextcloud_source",
        correlation_id=_nextcloud_correlation_id(report),
        privacy_level="private_metadata",
        metadata={
            "provider_id": report.provider_id,
            "status": report.status,
            "error_count": len(report.errors),
            "warning_count": len(report.warnings),
            "secure_policy_decision": report.secure_policy_decision or "unknown",
            "secure_policy_allowed": bool(report.secure_policy_allowed),
        },
    )
