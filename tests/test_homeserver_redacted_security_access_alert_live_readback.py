from __future__ import annotations

import json
from types import SimpleNamespace

from ops.homeserver import redacted_security_access_alert_live_readback as module
from src.security_action_authorization import build_redacted_auth_event
from src.security_auth_incident_bridge import AUTH_BRIDGE_SCHEMA, SecurityAuthIncidentBridge
from src.security_incident_network_context import AccessSourceContext, decide_self_egress_suppression
from src.security_incident_store import SecurityIncidentStore


REVISION = "a" * 40
MANIFEST = "b" * 64
SOURCE_IP = "8.8.8.8"


def _record(now: float = 100):
    envelope = build_redacted_auth_event(
        username=module.SYNTHETIC_USERNAME,
        outcome="failed",
        source_familiarity="unknown",
        session_created="no",
    ).envelope
    context = AccessSourceContext(
        SOURCE_IP,
        "trusted_proxy_forwarded",
        True,
        "trusted_proxy_forwarded",
    )
    audit = decide_self_egress_suppression(
        incident_id=envelope.correlation_ref,
        event_class=module.EVENT_CLASS,
        source_context=context,
        own_public_egress=None,
        now=now,
    )
    return {
        "schema": AUTH_BRIDGE_SCHEMA,
        "event_kind": "login",
        "auth_event": envelope.to_dict(),
        "accessing_ip_context": context.as_incident_projection(),
        "suppression_audit": audit,
        "raw_content_visible": False,
    }


def _executed_store(path):
    store = SecurityIncidentStore(path, clock=lambda: 100)
    result = SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
    action = store.get_action(result["action_id"])
    action = store.transition(
        action_id=action.action_id,
        expected_version=action.version,
        target_state="prepared",
        audit_ref="audit:sha256:" + "1" * 64,
    )
    action = store.approve(
        action_id=action.action_id,
        expected_version=action.version,
        approval_id="standing-live-smoke-fixture",
        approval_ref="approval:sha256:" + "2" * 64,
        scope_fingerprint=action.scope_fingerprint,
        policy_revision=action.policy_revision,
        audit_ref="audit:sha256:" + "3" * 64,
    )
    action = store.transition(
        action_id=action.action_id,
        expected_version=action.version,
        target_state="executing",
        audit_ref="audit:sha256:" + "4" * 64,
    )
    store.transition(
        action_id=action.action_id,
        expected_version=action.version,
        target_state="executed",
        audit_ref="audit:sha256:" + "5" * 64,
        receipt_ref="receipt:sha256:" + "6" * 64,
    )
    return store


def _packet():
    return {
        "revision": REVISION,
        "manifest_sha256": MANIFEST,
        "source_ip": SOURCE_IP,
        "issued_at": 95.0,
        "expires_at": 160.0,
        "synthetic_login_rejected": True,
    }


def _runner(command, **_kwargs):
    if command[:4] == ["git", "-C", module.TARGET_ROOT, "rev-parse"]:
        return SimpleNamespace(returncode=0, stdout=REVISION + "\n")
    if command[:3] == ["podman", "exec", module.APP_CONTAINER]:
        return SimpleNamespace(returncode=0, stdout="ok\n")
    raise AssertionError("unexpected command")


def test_readback_binds_rejected_login_ip_durable_receipt_and_audit_without_leaks(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "security-incidents.sqlite"
    _executed_store(path)
    monkeypatch.setattr(module, "DATABASE_PATH", str(path))

    result = module.collect_live_readback(
        _packet(),
        runner=_runner,
        clock=lambda: 101.0,
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "ok"
    assert all(result[key] is True for key in module._PROOFS)
    assert all(result[key] is False for key in module._VISIBILITY)
    assert result["retry_permitted"] is False
    encoded = json.dumps(result, sort_keys=True)
    assert SOURCE_IP not in encoded
    assert "notify-" not in encoded
    assert "receipt:sha256:" not in encoded
    assert module.validate_envelope(result)


def test_nonterminal_action_is_observed_once_without_manufacturing_success(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "security-incidents.sqlite"
    store = SecurityIncidentStore(path, clock=lambda: 100)
    SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record())
    monkeypatch.setattr(module, "DATABASE_PATH", str(path))

    result = module.collect_live_readback(
        _packet(),
        runner=_runner,
        clock=lambda: 101.0,
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "observed"
    assert result["action_executed"] is False
    assert result["context_bound"] is True
    assert module.validate_envelope(result)


def test_invalid_expired_or_secret_bearing_projection_fails_closed():
    invalid = _packet()
    invalid["source_ip"] = "127.0.0.1"
    assert module.collect_live_readback(invalid)["status"] == "blocked"
    assert module.production_entrypoint(_packet(), execute=False)["status"] == "blocked"

    tampered = module._envelope("blocked")
    tampered["secret_values_visible"] = True
    tampered["evidence_sha256"] = module._digest(tampered)
    assert module.validate_envelope(tampered) is False


def test_main_is_inert_and_fixed_schema(capsys):
    assert module.main([]) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "blocked"
    assert module.validate_envelope(value)
