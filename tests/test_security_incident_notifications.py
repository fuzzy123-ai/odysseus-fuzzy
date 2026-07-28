from __future__ import annotations

import pytest

from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_incident_notifications import (
    SecurityIncidentNotificationError,
    build_incident_notification_payload,
    canonical_operator_notification_body_ref,
    canonical_operator_notification_smoke_body,
    canonical_operator_notification_target_class_ref,
    format_incident_notification_for_telegram,
)


def test_canonical_operator_smoke_body_and_opaque_refs_are_fixed_and_redacted():
    body = canonical_operator_notification_smoke_body()
    assert body == canonical_operator_notification_smoke_body()
    assert canonical_operator_notification_body_ref().startswith("body:sha256:")
    assert canonical_operator_notification_target_class_ref().startswith("target_class:sha256:")
    assert "token" not in body.lower() and "chat_id" not in body.lower()
from src.security_response_policy import decide_incident_response


def _incident():
    return build_security_incident(
        level=3,
        severity="high",
        confidence=0.88,
        status="open",
        trigger="Repeated auth failures and endpoint probing",
        affected_surfaces=("telegram", "odysseus_api"),
        correlation_ids=("corr-security-1",),
        evidence_refs=("debug:sha256:abcdef0123456789",),
        recommended_actions=(
            build_recommended_action(
                action_type="redacted_debug_bundle",
                summary="Create redacted evidence bundle",
                risk="Low risk read-only diagnostic",
                action_id="act-debug",
            ),
            build_recommended_action(
                action_type="service_restart",
                summary="Prepare restart request for affected worker",
                risk="May interrupt active work",
                action_id="act-restart",
            ),
        ),
        incident_id="inc-test",
    )


def test_formats_telegram_incident_with_action_ids_and_gate_hint():
    incident = _incident()
    policy = decide_incident_response(incident, incident_mode=True)

    text = format_incident_notification_for_telegram(incident, policy_decision=policy)

    assert "Security incident" in text
    assert "inc-test" in text
    assert "act-debug" in text
    assert "act-restart" in text
    assert "/incident approve <action_id>" in text
    assert "/incident deny <action_id>" in text
    assert "chat_id" not in text.lower()
    assert "token" not in text.lower()


def test_builds_prepare_only_notification_payload_without_delivery_target():
    incident = _incident()
    policy = decide_incident_response(incident, incident_mode=True)

    payload = build_incident_notification_payload(
        incident,
        policy_decision=policy,
        debug_bundle={"bundle_id": "bundle-safe-1", "event_count": 4, "raw_content_visible": False},
    )

    assert payload["schema"] == "odysseus.security_incident_notification.v1"
    assert payload["channel"] == "telegram"
    assert payload["dry_run"] is True
    assert payload["delivery_performed"] is False
    assert payload["raw_content_visible"] is False
    assert payload["requires_operator_decision"] is True
    assert payload["debug_bundle"]["bundle_id"] == "bundle-safe-1"
    rendered = str(payload).lower()
    assert "chat_id" not in rendered
    assert "telegram_token" not in rendered
    assert "authorization" not in rendered


def test_rejects_raw_incident_content():
    incident = dict(_incident())
    incident["raw_content_visible"] = True

    with pytest.raises(SecurityIncidentNotificationError):
        build_incident_notification_payload(incident)


def test_rejects_private_paths_in_debug_bundle_refs():
    incident = _incident()

    with pytest.raises(SecurityIncidentNotificationError):
        build_incident_notification_payload(
            incident,
            debug_bundle={"bundle_id": r"C:\Users\nkatz\private.log", "event_count": 1},
        )


def test_ui_channel_is_supported_but_still_prepare_only():
    payload = build_incident_notification_payload(_incident(), channel="ui")

    assert payload["channel"] == "ui"
    assert payload["delivery_performed"] is False
    assert payload["notification_decision"]["dispatch_allowed"] is False
