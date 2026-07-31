from __future__ import annotations

import concurrent.futures
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_routes import setup_auth_routes

from src.security_auth_incident_bridge import AUTH_BRIDGE_SCHEMA, SecurityAuthIncidentBridge
from src.security_evidence_broker import build_security_evidence_envelope
from src.security_incident_network_context import AccessSourceContext, build_own_public_egress_snapshot, decide_self_egress_suppression
from src.security_incident_store import RedactionError, SecurityIncidentStore


def _record(*, kind="login", outcome="failed", ip="8.8.8.8", suppress=False):
    envelope = build_security_evidence_envelope({
        "source": "auth_outcome", "event_type": "authentication", "status": outcome,
        "severity": "warn", "dimensions": {"outcome": outcome, "source_familiarity": "unknown", "session_created": "yes" if outcome == "success" else "no"},
        "references": {"principal_ref": "principal:sha256:" + "a" * 64}, "measurements": {"event_count": 1},
    })
    context = AccessSourceContext(ip, "direct_peer", True, "direct_peer")
    own = build_own_public_egress_snapshot((ip,), observed_at=100, ttl_seconds=60) if suppress else None
    audit = decide_self_egress_suppression(incident_id=envelope.correlation_ref, event_class="external_access_origin_only" if outcome == "success" else "authentication_failure", source_context=context, own_public_egress=own, now=100)
    return {"schema": AUTH_BRIDGE_SCHEMA, "event_kind": kind, "auth_event": envelope.to_dict(), "accessing_ip_context": context.as_incident_projection(), "suppression_audit": audit, "raw_content_visible": False}


def _matching_snapshot(ip="8.8.8.8", *, observed_at=100, ttl_seconds=60):
    return build_own_public_egress_snapshot((ip,), observed_at=observed_at, ttl_seconds=ttl_seconds)


def _assert_proposed_notification(store, result):
    assert result["status"] == "action_proposed" and result["action_created"] is True
    assert store.get_incident_context_for_action(result["action_id"]).suppression_decision == "notify"


def test_failed_login_creates_one_redacted_incident_action_and_context(tmp_path):
    store = SecurityIncidentStore(tmp_path / "bridge.sqlite", clock=lambda: 100)
    result = SecurityAuthIncidentBridge(store).process(_record())
    replay = SecurityAuthIncidentBridge(store).process(_record())
    assert result["status"] == "action_proposed" and replay["action_created"] is False
    context = store.get_incident_context_for_action(result["action_id"])
    assert context.accessing_ip == "8.8.8.8" and context.event_class == "authentication_failure"
    assert "principal" not in str(context) and "username" not in str(context)


def test_successful_exact_self_egress_is_retained_but_has_no_action(tmp_path):
    store = SecurityIncidentStore(tmp_path / "suppressed.sqlite", clock=lambda: 100)
    result = SecurityAuthIncidentBridge(store, own_public_egress_provider=_matching_snapshot, clock=lambda: 100).process(_record(outcome="success", suppress=True))
    assert result["status"] == "suppressed" and result["action_created"] is False
    assert store.get_incident_context(result["incident_id"]).suppression_decision == "suppress_notification"


def test_routine_status_and_logout_never_create_incidents(tmp_path):
    store = SecurityIncidentStore(tmp_path / "routine.sqlite", clock=lambda: 100)
    bridge = SecurityAuthIncidentBridge(store)
    assert bridge.process(_record(kind="status"))["status"] == "ignored_routine_auth_event"
    assert bridge.process(_record(kind="logout"))["status"] == "ignored_routine_auth_event"
    assert store.audit_events() == ()


@pytest.mark.parametrize("tamper", ["ip", "audit"])
def test_tampering_fails_closed_before_action_creation(tmp_path, tamper):
    store = SecurityIncidentStore(tmp_path / f"{tamper}.sqlite", clock=lambda: 100)
    value = _record()
    if tamper == "ip": value["accessing_ip_context"]["is_public"] = False
    elif tamper == "audit": value["suppression_audit"]["event_class"] = "external_access_origin_only"
    with pytest.raises(RedactionError):
        SecurityAuthIncidentBridge(store).process(value)
    with store._read() as db:
        assert tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("incidents", "incident_contexts", "actions", "audit_references")) == (0, 0, 0, 0)


def test_forged_suppression_without_trusted_provider_fails_open_to_notification(tmp_path):
    store = SecurityIncidentStore(tmp_path / "no-provider.sqlite", clock=lambda: 100)
    _assert_proposed_notification(store, SecurityAuthIncidentBridge(store, clock=lambda: 100).process(_record(outcome="success", suppress=True)))


def test_provider_exception_stale_snapshot_and_mismatch_fail_open_to_notification(tmp_path):
    providers = (
        lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        lambda: _matching_snapshot(observed_at=0),
        lambda: _matching_snapshot("1.1.1.1"),
    )
    for number, provider in enumerate(providers):
        store = SecurityIncidentStore(tmp_path / f"fail-open-{number}.sqlite", clock=lambda: 100)
        result = SecurityAuthIncidentBridge(store, own_public_egress_provider=provider, clock=lambda: 100).process(_record(outcome="success", suppress=True))
        _assert_proposed_notification(store, result)


def test_disagreeing_supplied_decision_fails_open_even_with_matching_provider(tmp_path):
    store = SecurityIncidentStore(tmp_path / "disagree.sqlite", clock=lambda: 100)
    result = SecurityAuthIncidentBridge(store, own_public_egress_provider=_matching_snapshot, clock=lambda: 100).process(_record(outcome="success"))
    _assert_proposed_notification(store, result)


def test_critical_auth_event_always_notifies_even_when_audit_claims_suppression(tmp_path):
    store = SecurityIncidentStore(tmp_path / "critical-notify.sqlite", clock=lambda: 100)
    value = _record(suppress=True)
    value["suppression_audit"]["decision"] = "suppress_notification"
    value["suppression_audit"]["reason_code"] = "suppressed_exact_fresh_self_egress_match"
    result = SecurityAuthIncidentBridge(store, own_public_egress_provider=_matching_snapshot, clock=lambda: 100).process(value)
    _assert_proposed_notification(store, result)


def test_cross_incident_suppression_audit_is_rejected_without_durable_write(tmp_path):
    store = SecurityIncidentStore(tmp_path / "cross-incident.sqlite", clock=lambda: 100)
    value = _record()
    value["suppression_audit"]["incident_ref"] = "incident:sha256:" + "f" * 64
    with pytest.raises(RedactionError):
        SecurityAuthIncidentBridge(store).process(value)
    with store._read() as db:
        assert tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("incidents", "incident_contexts", "actions", "audit_references")) == (0, 0, 0, 0)


def test_auth_evidence_schema_mismatch_is_rejected_before_durable_write(tmp_path):
    store = SecurityIncidentStore(tmp_path / "wrong-evidence-schema.sqlite", clock=lambda: 100)
    value = _record()
    value["auth_event"]["schema"] = "odysseus.security_evidence.v0"
    with pytest.raises(ValueError):
        SecurityAuthIncidentBridge(store).process(value)
    with store._read() as db:
        assert tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("incidents", "incident_contexts", "actions", "audit_references")) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("outcome", "event_class", "decision", "reason"),
    (
        ("failed", "authentication_failure", "suppress_notification", "suppressed_exact_fresh_self_egress_match"),
        ("success", "external_access_origin_only", "suppress_notification", "notification_required_egress_mismatch"),
    ),
)
def test_store_rejects_direct_noncanonical_suppression_without_context_write(tmp_path, outcome, event_class, decision, reason):
    store = SecurityIncidentStore(tmp_path / f"direct-{outcome}.sqlite", clock=lambda: 100)
    value = _record(outcome=outcome, suppress=True)
    value["suppression_audit"]["decision"] = decision
    value["suppression_audit"]["reason_code"] = reason
    incident = store.create_incident(
        incident_id=f"incident-direct-{outcome}", incident_ref="incident:sha256:" + "b" * 64,
        audit_ref="audit:sha256:" + "c" * 64,
    )
    with pytest.raises(RedactionError):
        store.bind_incident_context(
            incident_id=incident.incident_id, event_class=event_class,
            access_context=value["accessing_ip_context"], suppression_audit=value["suppression_audit"],
            correlation_ref=value["auth_event"]["correlation_ref"],
            notification_binding_ref="body:sha256:" + "d" * 64, audit_ref="audit:sha256:" + "e" * 64,
        )
    with store._read() as db:
        assert db.execute("SELECT COUNT(*) FROM incident_contexts").fetchone()[0] == 0


def test_incident_context_rows_are_immutable(tmp_path):
    store = SecurityIncidentStore(tmp_path / "immutable-context.sqlite", clock=lambda: 100)
    result = SecurityAuthIncidentBridge(store).process(_record())
    with store._immediate() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE incident_contexts SET reason_code='direct_peer' WHERE incident_id=?", (result["incident_id"],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM incident_contexts WHERE incident_id=?", (result["incident_id"],))


def test_concurrent_replay_creates_one_action(tmp_path):
    store = SecurityIncidentStore(tmp_path / "concurrent.sqlite", clock=lambda: 100)
    value = _record()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: SecurityAuthIncidentBridge(store).process(value), range(2)))
    assert sum(item["action_created"] for item in results) == 1


def test_distinct_source_contexts_never_collide_and_bind_distinct_body_refs(tmp_path):
    store = SecurityIncidentStore(tmp_path / "multi-ip.sqlite", clock=lambda: 100)
    first = SecurityAuthIncidentBridge(store).process(_record(ip="8.8.8.8"))
    second = SecurityAuthIncidentBridge(store).process(_record(ip="1.1.1.1"))
    assert first["incident_id"] != second["incident_id"] and first["action_id"] != second["action_id"]
    assert store.get_incident_context_for_action(first["action_id"]).notification_binding_ref != store.get_incident_context_for_action(second["action_id"]).notification_binding_ref


def test_partial_incident_context_replay_recovers_without_duplicate_action(tmp_path):
    store = SecurityIncidentStore(tmp_path / "partial.sqlite", clock=lambda: 100)
    bridge = SecurityAuthIncidentBridge(store)
    value = _record()
    original = store.create_action
    store.create_action = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash"))
    with pytest.raises(RuntimeError): bridge.process(value)
    store.create_action = original
    recovered = bridge.process(value)
    assert recovered["status"] == "action_proposed" and bridge.process(value)["action_created"] is False


def test_auth_ingress_emits_one_combined_record_for_success_and_failure_without_secret_values(tmp_path):
    class Auth:
        signup_enabled = False
        def verify_password(self, username, password): return username == "admin" and password == "correct"
        def totp_enabled(self, _username): return False
        def create_session_trusted(self, username): return "session" if username == "admin" else None

    store = SecurityIncidentStore(tmp_path / "route.sqlite", clock=lambda: 100)
    app = FastAPI(); app.state.security_auth_incident_bridge = SecurityAuthIncidentBridge(store)
    app.include_router(setup_auth_routes(Auth()))
    client = TestClient(app, client=("8.8.8.8", 45000))
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "correct"}).status_code == 200
    records = app.state.security_auth_incident_records
    assert [item["event_kind"] for item in records] == ["login", "login"]
    rendered = str(records)
    assert all(value not in rendered for value in ("admin", "wrong", "correct"))
