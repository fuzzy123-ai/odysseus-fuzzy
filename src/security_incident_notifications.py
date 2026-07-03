"""Redacted operator notifications for security incidents.

This module prepares notification payloads only. It does not dispatch Telegram
messages, read live incident stores, or expose delivery targets.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.security_incident_model import SecurityIncidentModelError, summarize_incident
from src.user_notification_contract import (
    NotificationContractError,
    build_user_notification_decision,
)


SECURITY_INCIDENT_NOTIFICATION_SCHEMA = "odysseus.security_incident_notification.v1"
ALLOWED_NOTIFICATION_CHANNELS = {"telegram", "ui"}
MAX_ACTIONS = 5
FORBIDDEN_TEXT_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "token=",
    "telegram_token",
    "chat_id",
    "private_document_text",
    "private_email_body",
    "image_base64",
    "unredacted_tool_output",
)
HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)


class SecurityIncidentNotificationError(ValueError):
    """Raised when an incident notification would cross the redaction boundary."""


def build_incident_notification_payload(
    incident: Mapping[str, Any],
    *,
    channel: str = "telegram",
    policy_decision: Mapping[str, Any] | None = None,
    debug_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a dry-run notification payload for Telegram or future UI delivery."""

    normalized_channel = _safe_channel(channel)
    summary = _safe_incident_summary(incident)
    actions = tuple(_safe_action(action) for action in incident.get("recommended_actions", ()))[:MAX_ACTIONS]
    policy = _safe_policy_projection(policy_decision)
    bundle = _safe_debug_bundle_projection(debug_bundle)
    text = format_incident_notification_for_telegram(
        incident,
        policy_decision=policy,
        debug_bundle=bundle,
    )

    severity = _notification_severity(summary)
    metadata = {
        "incident_id": summary["incident_id"],
        "incident_level": str(summary["level"]),
        "incident_severity": str(summary["severity"]),
        "requires_confirmation": str(summary["requires_confirmation_count"] > 0).lower(),
        "action_count": str(summary["recommended_action_count"]),
    }
    if policy:
        metadata["policy_decision"] = str(policy.get("decision") or "")
    if bundle:
        metadata["debug_bundle_id"] = str(bundle.get("bundle_id") or "")

    try:
        decision = build_user_notification_decision(
            {
                "event": "security_incident",
                "message": text,
                "severity": severity,
                "channel": normalized_channel if normalized_channel == "telegram" else "auto",
                "dry_run": True,
                "metadata": metadata,
            },
            configured_channels=("telegram",),
            live_dispatch_enabled=False,
            target_configured=False,
        )
    except NotificationContractError as exc:
        raise SecurityIncidentNotificationError(str(exc)) from exc

    payload = {
        "schema": SECURITY_INCIDENT_NOTIFICATION_SCHEMA,
        "event": "security_incident",
        "channel": normalized_channel,
        "severity": severity,
        "message": text,
        "incident": summary,
        "actions": actions,
        "policy": policy,
        "debug_bundle": bundle,
        "notification_decision": decision.as_public_dict(),
        "requires_operator_decision": bool(summary["requires_confirmation_count"] > 0),
        "dry_run": True,
        "delivery_performed": False,
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(payload)
    return payload


def format_incident_notification_for_telegram(
    incident: Mapping[str, Any],
    *,
    policy_decision: Mapping[str, Any] | None = None,
    debug_bundle: Mapping[str, Any] | None = None,
) -> str:
    """Render a compact redacted incident notification suitable for Telegram."""

    summary = _safe_incident_summary(incident)
    actions = tuple(_safe_action(action) for action in incident.get("recommended_actions", ()))[:MAX_ACTIONS]
    policy = _safe_policy_projection(policy_decision)
    bundle = _safe_debug_bundle_projection(debug_bundle)
    surfaces = ", ".join(str(item) for item in summary["affected_surfaces"]) or "unknown"
    lines = [
        "Security incident",
        (
            f"ID {summary['incident_id']} | Level {summary['level']} "
            f"{summary['level_name']} | {summary['severity']} | "
            f"confidence {summary['confidence']:.3f} | {summary['status']}"
        ),
        f"Surface: {surfaces}",
    ]
    if policy:
        gate = "yes" if bool(policy.get("operator_gate_required")) else "no"
        lines.append(f"Policy: {policy.get('decision')} ({policy.get('reason')}); operator gate: {gate}")
    if bundle:
        lines.append(f"Debug bundle: {bundle.get('bundle_id')} ({bundle.get('event_count')} events)")
    if actions:
        lines.append("Actions:")
        for action in actions:
            suffix = "confirm" if action["requires_confirmation"] else "auto-safe"
            lines.append(f"- {action['action_id']}: {action['type']} [{suffix}]")
    else:
        lines.append("Actions: none")
    confirm_ids = [action["action_id"] for action in actions if action["requires_confirmation"]]
    if confirm_ids:
        joined = ", ".join(confirm_ids)
        lines.append(f"Approve/deny: /incident approve <action_id> or /incident deny <action_id> ({joined})")
    else:
        lines.append("No operator approval required for current recommendations.")
    text = "\n".join(lines)
    _reject_forbidden_text(text)
    return text


def _safe_channel(value: Any) -> str:
    channel = str(value or "telegram").strip().lower()
    if channel not in ALLOWED_NOTIFICATION_CHANNELS:
        raise SecurityIncidentNotificationError("unsupported notification channel")
    return channel


def _safe_incident_summary(incident: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(incident, Mapping):
        raise SecurityIncidentNotificationError("incident must be a mapping")
    if bool(incident.get("raw_content_visible")):
        raise SecurityIncidentNotificationError("incident raw_content_visible must be false")
    try:
        summary = summarize_incident(incident)
    except SecurityIncidentModelError as exc:
        raise SecurityIncidentNotificationError(str(exc)) from exc
    _reject_forbidden_payload(summary)
    return summary


def _safe_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        raise SecurityIncidentNotificationError("recommended action must be a mapping")
    if bool(action.get("raw_content_visible")):
        raise SecurityIncidentNotificationError("action raw_content_visible must be false")
    out = {
        "action_id": _safe_text(action.get("action_id"), field="action_id", max_chars=120),
        "type": _safe_text(action.get("type"), field="action_type", max_chars=120),
        "summary": _safe_text(action.get("summary"), field="summary", max_chars=180),
        "risk": _safe_text(action.get("risk"), field="risk", max_chars=180),
        "requires_confirmation": bool(action.get("requires_confirmation")),
        "policy_gate": _safe_text(action.get("policy_gate"), field="policy_gate", max_chars=160),
        "status": _safe_text(action.get("status"), field="action_status", max_chars=80),
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(out)
    return out


def _safe_policy_projection(policy_decision: Mapping[str, Any] | None) -> dict[str, Any]:
    if not policy_decision:
        return {}
    if not isinstance(policy_decision, Mapping):
        raise SecurityIncidentNotificationError("policy_decision must be a mapping")
    if bool(policy_decision.get("raw_content_visible")):
        raise SecurityIncidentNotificationError("policy_decision raw_content_visible must be false")
    out = {
        "decision": _safe_text(policy_decision.get("decision"), field="decision", max_chars=80),
        "reason": _safe_text(policy_decision.get("reason"), field="reason", max_chars=180),
        "operator_gate_required": bool(policy_decision.get("operator_gate_required")),
        "allowed_to_execute": False,
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(out)
    return out


def _safe_debug_bundle_projection(debug_bundle: Mapping[str, Any] | None) -> dict[str, Any]:
    if not debug_bundle:
        return {}
    if not isinstance(debug_bundle, Mapping):
        raise SecurityIncidentNotificationError("debug_bundle must be a mapping")
    if bool(debug_bundle.get("raw_content_visible")):
        raise SecurityIncidentNotificationError("debug_bundle raw_content_visible must be false")
    bundle_id = debug_bundle.get("bundle_id") or debug_bundle.get("id") or debug_bundle.get("ref") or ""
    out = {
        "bundle_id": _safe_text(bundle_id, field="bundle_id", max_chars=120),
        "event_count": _safe_count(debug_bundle.get("event_count") or debug_bundle.get("events")),
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(out)
    return out


def _notification_severity(summary: Mapping[str, Any]) -> str:
    incident_severity = str(summary.get("severity") or "info")
    if incident_severity in {"critical", "high"}:
        return "error"
    if incident_severity in {"medium"}:
        return "warning"
    return "info"


def _safe_text(value: Any, *, field: str, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "..."
    _reject_forbidden_text(text, field=field)
    return text


def _safe_count(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 1_000_000))
    except (TypeError, ValueError):
        return 0


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_forbidden_text(str(key), field="field_name")
            _reject_forbidden_payload(nested)
        return
    if isinstance(value, (tuple, list, set)):
        for item in value:
            _reject_forbidden_payload(item)
        return
    if isinstance(value, str):
        _reject_forbidden_text(value)


def _reject_forbidden_text(text: str, *, field: str = "value") -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS):
        raise SecurityIncidentNotificationError(f"{field} contains a forbidden marker")
    if HOST_PATH_RE.search(text):
        raise SecurityIncidentNotificationError(f"{field} contains a private host path")
