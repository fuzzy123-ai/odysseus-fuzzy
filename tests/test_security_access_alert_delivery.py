from __future__ import annotations

import ipaddress
import json

import pytest

from src.security_access_alert_delivery import (
    SecurityAccessAlertDeliveryCoordinator,
    automatic_delivery_enabled,
    server_owned_telegram_readiness,
)
from src.security_auth_incident_bridge import AUTH_BRIDGE_SCHEMA, SecurityAuthIncidentBridge
from src.security_evidence_broker import build_security_evidence_envelope
from src.security_incident_delivery import (
    SecurityIncidentDeliveryAdapter,
    TrustedTelegramDeliveryReadiness,
    build_access_alert_delivery_request,
    delivery_idempotency_key,
    issue_test_delivery_transport,
)
from src.security_incident_network_context import AccessSourceContext, decide_self_egress_suppression
from src.security_incident_notifications import (
    canonical_access_alert_body,
    canonical_access_alert_body_ref,
)
from src.security_incident_store import SecurityIncidentStore


def _record(*, outcome="failed", kind="login", ip="8.8.8.8"):
    event_class = (
        "step_up_failure" if kind == "step_up"
        else "authentication_failure" if outcome != "success"
        else "external_access_origin_only"
    )
    envelope = build_security_evidence_envelope({
        "source": "auth_outcome", "event_type": "authentication", "status": outcome,
        "severity": "warn", "dimensions": {
            "outcome": outcome, "source_familiarity": "unknown",
            "session_created": "yes" if outcome == "success" else "no",
        },
        "references": {"principal_ref": "principal:sha256:" + "a" * 64},
        "measurements": {"event_count": 1},
    })
    context = AccessSourceContext(
        ip, "direct_peer", ipaddress.ip_address(ip).is_global, "direct_peer",
    )
    audit = decide_self_egress_suppression(
        incident_id=envelope.correlation_ref, event_class=event_class,
        source_context=context, own_public_egress=None, now=100,
    )
    return {
        "schema": AUTH_BRIDGE_SCHEMA, "event_kind": kind,
        "auth_event": envelope.to_dict(),
        "accessing_ip_context": context.as_incident_projection(),
        "suppression_audit": audit, "raw_content_visible": False,
    }


def _readiness(ready=True):
    credential_keys = {
        "DATA_BRAVE_API_KEY", "EMBEDDING_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
        "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
        "NEXTCLOUD_WEBDAV_APP_PASSWORD", "ODYSSEUS_ADMIN_PASSWORD",
        "ODYSSEUS_INTERNAL_TOKEN", "OPENAI_API_KEY", "SERPER_API_KEY",
        "TAVILY_API_KEY", "TELEGRAM_BOT_TOKEN",
    }
    return TrustedTelegramDeliveryReadiness.from_redacted_probe({
        "schema_id": "odysseus.homeserver.redacted_runtime_probe.v1",
        "status": "ok",
        "container": "odysseus_odysseus_1",
        "container_running": True,
        "environment_entry_count": len(credential_keys),
        "credential_presence": {key: False for key in credential_keys},
        "unknown_sensitive_key_count": 0,
        "raw_environment_visible": False,
        "secret_values_visible": False,
        "telegram_delivery_readiness": {
            "opaque_target_configured": ready,
            "agent_reply_enabled": ready,
            "send_ready": ready,
            "raw_target_visible": False,
            "secret_values_visible": False,
        },
    })


def _fake(calls, *, fail=False):
    def effect(request):
        calls.append(request)
        if fail:
            raise RuntimeError("provider secret response")
        return {"status": "acknowledged", "receipt_ref": "receipt:sha256:" + "b" * 64}
    effect.security_incident_delivery_test_fake = True
    return issue_test_delivery_transport(effect)


def _coordinator(store, calls, *, ready=True, fail=False):
    adapter = SecurityIncidentDeliveryAdapter(
        store, transport=_fake(calls, fail=fail), clock=lambda: 100,
    )
    return SecurityAccessAlertDeliveryCoordinator(
        store, adapter, readiness_provider=lambda: _readiness(ready),
    )


@pytest.mark.parametrize(
    ("kind", "outcome", "meaning"),
    [
        ("login", "failed", "Fehlgeschlagener Login"),
        ("step_up", "failed", "Fehlgeschlagene Sicherheitsfreigabe"),
        ("login", "success", "Erfolgreicher externer Login"),
    ],
)
@pytest.mark.parametrize(
    "ip",
    ["8.8.8.8", "2606:4700:4700::1111", "10.20.30.40", "fd00::1234"],
)
def test_dynamic_body_is_exact_ip_aware_bounded_and_contains_no_identity_or_secret(
    tmp_path, kind, outcome, meaning, ip,
):
    store = SecurityIncidentStore(tmp_path / f"{kind}-{outcome}-{ip.replace(':', '-')}.sqlite", clock=lambda: 100)
    result = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(
        _record(kind=kind, outcome=outcome, ip=ip)
    )
    context = store.get_incident_context_for_action(result["action_id"])
    body = canonical_access_alert_body(event_class=context.event_class, accessing_ip=context.accessing_ip)
    assert meaning in body and f"Zugreifende IP: {ip}" in body and len(body) <= 320
    assert context.notification_binding_ref == canonical_access_alert_body_ref(
        event_class=context.event_class, accessing_ip=ip,
    )
    encoded = json.dumps({"body": body, "ref": context.notification_binding_ref})
    assert all(marker not in encoded for marker in ("username", "admin", "password", "token", "header"))


def test_coordinator_recovers_proposed_prepared_and_approved_exactly_once(tmp_path):
    for initial in ("proposed", "prepared", "approved"):
        store = SecurityIncidentStore(tmp_path / f"{initial}.sqlite", clock=lambda: 100)
        created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
        action = store.get_action(created["action_id"])
        if initial in {"prepared", "approved"}:
            action = store.transition(
                action_id=action.action_id, expected_version=action.version,
                target_state="prepared", audit_ref="audit:sha256:" + "c" * 64,
            )
        if initial == "approved":
            action = store.approve(
                action_id=action.action_id, expected_version=action.version,
                approval_id="approval-fixture", approval_ref="approval:sha256:" + "d" * 64,
                scope_fingerprint=action.scope_fingerprint, policy_revision=action.policy_revision,
                audit_ref="audit:sha256:" + "e" * 64,
            )
        calls = []
        coordinator = _coordinator(store, calls)
        assert coordinator.reconcile()["attempted"] == 1
        assert store.get_action(created["action_id"]).state == "executed"
        assert coordinator.reconcile()["examined"] == 0
        assert len(calls) == 1


def test_readiness_false_retains_approved_without_transport_and_can_recover_later(tmp_path):
    store = SecurityIncidentStore(tmp_path / "readiness.sqlite", clock=lambda: 100)
    created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
    calls = []
    _coordinator(store, calls, ready=False).reconcile()
    assert store.get_action(created["action_id"]).state == "approved"
    assert calls == []
    _coordinator(store, calls, ready=True).reconcile()
    assert store.get_action(created["action_id"]).state == "executed"
    assert len(calls) == 1


def test_bounded_reconcile_rotates_past_first_window_during_readiness_outage(tmp_path):
    store = SecurityIncidentStore(tmp_path / "frontier.sqlite", clock=lambda: 100)
    action_ids = []
    for suffix in range(1, 11):
        created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(
            _record(ip=f"10.0.0.{suffix}")
        )
        action_ids.append(created["action_id"])
    calls = []
    coordinator = _coordinator(store, calls, ready=False)
    first = coordinator.reconcile()
    second = coordinator.reconcile()
    assert first["examined"] == 8
    assert second["examined"] == 2
    assert all(store.get_action(action_id).state == "approved" for action_id in action_ids)
    assert calls == []


def test_ready_backlog_attempts_at_most_one_external_effect_per_reconcile(tmp_path):
    store = SecurityIncidentStore(tmp_path / "ready-frontier.sqlite", clock=lambda: 100)
    action_ids = []
    for suffix in range(1, 11):
        created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(
            _record(ip=f"10.0.1.{suffix}")
        )
        action_ids.append(created["action_id"])
    calls = []
    coordinator = _coordinator(store, calls, ready=True)
    for _ in range(12):
        before = len(calls)
        coordinator.reconcile()
        assert len(calls) - before <= 1
        if all(store.get_action(action_id).state == "executed" for action_id in action_ids):
            break
    assert len(calls) == len(action_ids)
    assert all(store.get_action(action_id).state == "executed" for action_id in action_ids)


def test_missing_bot_token_retains_approved_and_recovers_without_exposing_token(
    tmp_path, monkeypatch,
):
    store = SecurityIncidentStore(tmp_path / "token-readiness.sqlite", clock=lambda: 100)
    created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
    calls = []
    adapter = SecurityIncidentDeliveryAdapter(
        store, transport=_fake(calls), clock=lambda: 100,
    )
    monkeypatch.setattr(
        "src.user_notification_delivery._configured_telegram_target",
        lambda: "configured-target",
    )
    monkeypatch.setattr(
        "plugins.telegram.plugin._chat_allowed",
        lambda target: target == "configured-target",
    )
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    coordinator = SecurityAccessAlertDeliveryCoordinator(store, adapter)
    assert server_owned_telegram_readiness().values()["send_ready"] is False
    coordinator.reconcile()
    assert store.get_action(created["action_id"]).state == "approved"
    assert calls == []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-only-token")
    coordinator.reconcile()
    assert store.get_action(created["action_id"]).state == "executed"
    assert len(calls) == 1


def test_auth_alert_dedupes_within_window_and_reopens_in_next_window(tmp_path):
    now = [100.0]
    store = SecurityIncidentStore(tmp_path / "dedupe-window.sqlite", clock=lambda: now[0])
    bridge = SecurityAuthIncidentBridge(store, clock=lambda: now[0])
    first = bridge.process(_record())
    replay = bridge.process(_record())
    assert replay["incident_id"] == first["incident_id"]
    assert replay["action_id"] == first["action_id"]
    assert replay["action_created"] is False
    now[0] = 301.0
    next_window = bridge.process(_record())
    assert next_window["incident_id"] != first["incident_id"]
    assert next_window["action_id"] != first["action_id"]
    assert next_window["action_created"] is True


def test_provider_ambiguity_is_terminal_failed_and_never_retried(tmp_path):
    store = SecurityIncidentStore(tmp_path / "provider.sqlite", clock=lambda: 100)
    created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
    calls = []
    coordinator = _coordinator(store, calls, fail=True)
    coordinator.reconcile()
    assert store.get_action(created["action_id"]).state == "failed"
    assert coordinator.reconcile()["examined"] == 0
    assert len(calls) == 1


def test_stable_idempotency_excludes_readiness_and_grant_expiry(tmp_path):
    store = SecurityIncidentStore(tmp_path / "identity.sqlite", clock=lambda: 100)
    created = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
    action = store.get_action(created["action_id"])
    action = store.transition(
        action_id=action.action_id, expected_version=action.version,
        target_state="prepared", audit_ref="audit:sha256:" + "c" * 64,
    )
    action = store.approve(
        action_id=action.action_id, expected_version=action.version,
        approval_id="approval-identity", approval_ref="approval:sha256:" + "d" * 64,
        scope_fingerprint=action.scope_fingerprint, policy_revision=action.policy_revision,
        audit_ref="audit:sha256:" + "e" * 64,
    )
    ready = build_access_alert_delivery_request(action, _readiness(True), store)
    blocked = build_access_alert_delivery_request(action, _readiness(False), store)
    assert delivery_idempotency_key(ready) == delivery_idempotency_key(blocked)
    assert action.idempotency_key == delivery_idempotency_key(ready)


def test_delivery_enable_gate_is_exact_true_only():
    assert automatic_delivery_enabled("true") is True
    for value in (None, "", "false", "TRUE", "1", "yes", " true "):
        assert automatic_delivery_enabled(value) is False
