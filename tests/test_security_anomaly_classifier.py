import json

import pytest

from src.runtime_event_envelope import build_runtime_event
from src.security_anomaly_classifier import (
    SecurityAnomalyClassifierError,
    classify_security_anomalies,
)


def _event(**overrides):
    payload = {
        "surface": "auth",
        "component": "login",
        "event_type": "auth_failure",
        "status": "failed",
        "severity": "warn",
        "owner_scope": "security_test",
        "correlation_id": "corr-auth",
        "privacy_level": "private_metadata",
        "metadata": {},
    }
    payload.update(overrides)
    owner_scope = payload.pop("owner_scope")
    return build_runtime_event(owner_scope=owner_scope, **payload)


def _triggers(report):
    return {incident["trigger"] for incident in report["incidents"]}


def test_classifier_detects_repeated_auth_failures_without_raw_content():
    events = [
        _event(correlation_id=f"corr-auth-{idx}", metadata={"attempt": idx})
        for idx in range(3)
    ]

    report = classify_security_anomalies(events)
    encoded = json.dumps(report, sort_keys=True)

    assert report["status"] == "success"
    assert "repeated_auth_failures" in _triggers(report)
    incident = report["incidents"][0]
    assert incident["level"] == 2
    assert incident["severity"] == "medium"
    assert incident["raw_content_visible"] is False
    assert any(action["type"] == "redacted_debug_bundle" for action in incident["recommended_actions"])
    assert "password" not in encoded.lower()


def test_classifier_detects_endpoint_probing_from_redacted_metadata_and_summary():
    events = [
        _event(
            surface="http",
            component="router",
            event_type="endpoint_probe",
            status="blocked",
            correlation_id=f"corr-probe-{idx}",
            metadata={"endpoint_hash": f"endpoint-{idx}"},
        )
        for idx in range(2)
    ]

    report = classify_security_anomalies(
        events,
        observability_summary={"http_404_count": 3, "unique_endpoint_hash_count": 5},
    )

    assert "suspicious_endpoint_probing" in _triggers(report)
    incident = next(item for item in report["incidents"] if item["trigger"] == "suspicious_endpoint_probing")
    assert incident["affected_surfaces"] == ("http",)
    assert incident["raw_content_visible"] is False


def test_classifier_detects_telegram_abuse_patterns():
    events = [
        _event(
            surface="telegram",
            component="polling",
            event_type="telegram_rate_limit",
            status="blocked",
            correlation_id=f"corr-tg-{idx}",
            metadata={"reason": "rate_limit"},
        )
        for idx in range(3)
    ]

    report = classify_security_anomalies(events)

    assert "telegram_abuse_or_spam_pattern" in _triggers(report)
    incident = report["incidents"][0]
    assert incident["affected_surfaces"] == ("telegram",)


def test_classifier_detects_service_down_and_prepares_gated_restart_only():
    events = [
        _event(
            surface="ops",
            component="podman",
            event_type="service_down",
            status="failed",
            severity="error",
            correlation_id="corr-service-down",
            metadata={"service_ref": "odysseus"},
        )
    ]

    report = classify_security_anomalies(events)
    incident = report["incidents"][0]
    restart = next(action for action in incident["recommended_actions"] if action["type"] == "service_restart")

    assert incident["trigger"] == "service_down_security_relevant"
    assert incident["level"] == 3
    assert restart["requires_confirmation"] is True
    assert restart["status"] == "proposed"


def test_classifier_detects_secret_leak_indicators_without_secret_value():
    events = [
        _event(
            surface="security",
            component="redaction",
            event_type="secret_leak_indicator",
            status="blocked",
            severity="error",
            correlation_id="corr-secret",
            metadata={"indicator": "secret_detected"},
        )
    ]

    report = classify_security_anomalies(events)
    encoded = json.dumps(report, sort_keys=True)
    incident = report["incidents"][0]
    token_action = next(action for action in incident["recommended_actions"] if action["type"] == "token_rotation_prepare")

    assert incident["trigger"] == "secret_leak_indicator_detected"
    assert incident["severity"] == "high"
    assert token_action["requires_confirmation"] is True
    assert "secret-value" not in encoded


def test_classifier_rejects_raw_or_secret_marked_inputs():
    event = _event()
    event["raw_content_visible"] = True
    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies([event])

    with pytest.raises(SecurityAnomalyClassifierError):
        classify_security_anomalies([], observability_summary={"note": "Bearer secret-token"})
