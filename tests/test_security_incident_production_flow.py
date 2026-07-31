"""Offline SIRP-10 integration evidence; no runtime service or transport is used."""

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.runtime_event_envelope import build_runtime_event
from src.security_action_authorization import SecurityActionAuthorization
from src.security_anomaly_classifier import classify_security_anomalies
from src.security_evidence_broker import build_security_evidence_envelope
from src.security_evidence_sources import auth_outcome_projection
from src.security_executor_contracts import SecurityExecutionRequest, build_rollback_descriptor
from src.security_executor_kernel import SecurityExecutorKernel
from src.security_incident_audit import SecurityIncidentAuditChain, SecurityIncidentAuditError
from src.security_incident_commands import SecurityIncidentCommandError, SecurityIncidentCommands
from src.security_incident_service import SecurityIncidentService
from src.security_incident_store import SecurityIncidentStore
from src.security_post_action_verification import (
    PostActionVerifier,
    VERIFICATION_TEST_ONLY_ISSUER,
    issue_test_verification_source,
)
from src.security_response_policy import decide_incident_response
from src.security_rollback import (
    ROLLBACK_TEST_ONLY_ISSUER,
    SecurityRollbackManager,
    issue_test_rollback_acknowledgement,
    issue_test_rollback_adapter,
)


def test_app_composes_bounded_egress_refresh_and_loopback_proxy_trust_across_lifecycle():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "trusted_proxy_networks_from_config(" in source
    assert 'os.getenv("ODYSSEUS_SECURITY_TRUSTED_PROXY_NETWORKS")' in source
    assert "PublicEgressRefreshController()" in source
    assert "discovery_enabled_from_disable_value(os.getenv(PUBLIC_EGRESS_DISABLE_ENV))" in source
    assert "asyncio.create_task(egress_controller.run(_publish_security_egress))" in source
    assert "app.state.security_own_public_egress_snapshot = snapshot" in source
    assert "egress_task.cancel()" in source
    assert "await egress_task" in source
    assert "app.state.security_own_public_egress_snapshot = None" in source


def _ref(kind, char):
    return f"{kind}:sha256:{char * 64}"


def _request(action_id="act-flow", *, expires_at=150.0):
    values = {
        "action_id": action_id, "action_version": 3, "action_type": "crowdsec_temp_block",
        "scope_fingerprint": _ref("scope", "c"), "policy_revision": _ref("policy", "d"),
        "policy_gate": "crowdsec-remediation-go", "timeout_seconds": 30,
        "idempotency_key": "idem-flow", "expires_at": expires_at,
    }
    provisional = SecurityExecutionRequest(rollback_descriptor=_ref("rollback", "0"), **values)
    return SecurityExecutionRequest(rollback_descriptor=build_rollback_descriptor(provisional), **values)


class _FlowAuth:
    def validate_token(self, token): return token == "flow-session"
    def get_username_for_token(self, token): return "admin" if token == "flow-session" else None
    def is_admin(self, username): return username == "admin"
    def verify_password(self, username, password): return username == "admin" and password == "flow-password"
    def totp_verify_live(self, username, code): return username == "admin" and code == "654321"
    def totp_enabled(self, username): return username == "admin"
    def security_action_revision(self): return 0


def _prepared_store(tmp_path, clock):
    store = SecurityIncidentStore(tmp_path / "sirp10-flow.sqlite", clock=lambda: clock[0])
    projection = auth_outcome_projection(
        outcome="failed", principal_ref=_ref("principal", "a"),
        source_familiarity="unknown", session_created="not_applicable",
    )
    brokered = SecurityIncidentService(store).create_from_evidence(projection)
    action = store.create_action(
        action_id="act-flow", incident_id=brokered.incident.incident_id,
        action_type="crowdsec_temp_block", scope_fingerprint=_ref("scope", "c"),
        policy_revision=_ref("policy", "d"), idempotency_key="idem-flow", ttl_seconds=60,
        audit_ref=_ref("audit", "e"), metadata={"correlation_ref": brokered.evidence.correlation_ref},
    )
    prepared = store.transition(action_id=action.action_id, expected_version=action.version, target_state="prepared", audit_ref=_ref("audit", "f"))
    return store, brokered.evidence


def _step_up_and_approve(store, clock):
    authorization = SecurityActionAuthorization(_FlowAuth(), clock=lambda: clock[0])
    commands = SecurityIncidentCommands(store, authorization)
    prepared = store.get_action("act-flow")
    step_up = authorization.step_up(session_token="flow-session", username="admin", password="flow-password", totp_code="654321", action=prepared, auth_kind="browser_cookie")
    projection = step_up.envelope.to_dict()
    encoded = json.dumps(projection, sort_keys=True)
    assert step_up.raw_content_visible is False and all(value not in encoded for value in ("flow-session", "flow-password", "654321"))
    approved = commands.approve(action_id="act-flow", session_token="flow-session", username="admin")
    assert approved["status"] == "approved" and approved["executed"] is False and approved["action_version"] == 3
    assert store.get_approval("act-flow").consumed_at is None and store.get_action("act-flow").state == "approved"
    with pytest.raises(SecurityIncidentCommandError):
        commands.approve(action_id="act-flow", session_token="flow-session", username="admin")
    return projection


def _sealed_rollback_adapter(calls):
    adapter = None

    def acknowledge(operation):
        calls.append(operation)
        return issue_test_rollback_acknowledgement(adapter, operation, test_issuer=ROLLBACK_TEST_ONLY_ISSUER)

    adapter = issue_test_rollback_adapter(acknowledge, test_issuer=ROLLBACK_TEST_ONLY_ISSUER)
    return adapter


def test_offline_lifecycle_is_bounded_redacted_and_terminally_evidenced(tmp_path):
    clock = [100.0]
    events = tuple(
        build_runtime_event(
            surface="auth", component="login", event_type="auth_failure", status="failed", severity="warn",
            owner_scope="sirp10_test", correlation_id=f"corr-{index}", event_id=f"evt-{index}",
            ts=f"2026-07-28T08:00:0{index}Z", metadata={"attempt": index},
        )
        for index in range(3)
    )
    classified = classify_security_anomalies(reversed(events))
    assert classified["incident_count"] == 1 and classified["raw_content_visible"] is False
    policy = decide_incident_response(classified["incidents"][0])
    assert policy["allowed_to_execute"] is False and policy["raw_content_visible"] is False

    store, evidence = _prepared_store(tmp_path, clock)
    assert evidence.raw_content_visible is False
    auth_projection = _step_up_and_approve(store, clock)
    assert auth_projection["raw_content_visible"] is False and auth_projection["source"] == "auth_outcome"
    request, effects = _request(), []

    def effect(value):
        effects.append(value)
        return {"provider_response": "secret raw log omitted"}

    effect.security_executor_test_fake = True
    receipt = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": effect}, clock=lambda: clock[0]).execute(request)
    assert receipt["acknowledgement_received"] is True and receipt["verified"] is False
    assert len(effects) == 1 and store.get_action("act-flow").state == "executed" and store.get_approval("act-flow").consumed_at == clock[0]
    assert "secret" not in json.dumps(receipt, sort_keys=True)

    verified = PostActionVerifier(
        store, issue_test_verification_source(source_ref=_ref("source", "3"), outcome="success", test_issuer=VERIFICATION_TEST_ONLY_ISSUER),
    ).verify(request)
    assert verified["verified"] is True and verified["closure_blocked"] is False
    assert store.get_action_evidence("act-flow").verification_ref == verified["evidence_ref"]

    rollback_calls = []
    rolled_back = SecurityRollbackManager(
        store, fake_adapters={"crowdsec_temp_block": _sealed_rollback_adapter(rollback_calls)}, clock=lambda: clock[0],
    ).rollback(request)
    terminal = store.get_action_evidence("act-flow")
    assert rolled_back["status"] == "success" and terminal.state == "rolled_back" and terminal.rollback_ref == rolled_back["rollback_ref"]
    snapshot = SecurityIncidentAuditChain.snapshot(store, action_id="act-flow")
    SecurityIncidentAuditChain.verify(snapshot)
    projection = SecurityIncidentAuditChain.projection(snapshot)
    executed = next(item for item in snapshot.entries if item.event_type == "action_executed")
    verified_entry = next(item for item in snapshot.entries if item.event_type == "action_verified")
    rolled_entry = next(item for item in snapshot.entries if item.event_type == "action_rolled_back")
    assert len(rollback_calls) == 1 and executed.receipt_ref == receipt["receipt_ref"]
    assert verified_entry.verification_ref == verified["evidence_ref"] and rolled_entry.rollback_ref == rolled_back["rollback_ref"]
    encoded = json.dumps(projection, sort_keys=True)
    assert projection["raw_content_visible"] is False and all(value not in encoded for value in ("act-flow", "flow-session", "flow-password", "654321", "secret raw log omitted"))
    with pytest.raises(SecurityIncidentAuditError):
        SecurityIncidentAuditChain.verify(replace(snapshot, entries=tuple(replace(item, verification_ref="") if item.event_type == "action_verified" else item for item in snapshot.entries)))


def test_unknown_independent_verification_blocks_closure_then_has_rollback_evidence(tmp_path):
    clock = [100.0]
    store, _ = _prepared_store(tmp_path, clock)
    _step_up_and_approve(store, clock)
    calls, request = [], _request()

    def effect(_request):
        calls.append(True)

    effect.security_executor_test_fake = True
    assert SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": effect}, clock=lambda: clock[0]).execute(request)["verified"] is False
    unknown = PostActionVerifier(
        store, issue_test_verification_source(source_ref=_ref("source", "4"), outcome="unknown", test_issuer=VERIFICATION_TEST_ONLY_ISSUER),
    ).verify(request)
    assert unknown["status"] == "blocked" and unknown["outcome"] == "unknown" and unknown["closure_blocked"] is True
    assert store.get_action_evidence("act-flow").failure_ref == unknown["evidence_ref"]
    rollback_calls = []
    result = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": _sealed_rollback_adapter(rollback_calls)}, clock=lambda: clock[0]).rollback(request)
    snapshot = SecurityIncidentAuditChain.snapshot(store, action_id="act-flow")
    SecurityIncidentAuditChain.verify(snapshot)
    failed_entry = next(item for item in snapshot.entries if item.event_type == "action_failed")
    rolled_entry = next(item for item in snapshot.entries if item.event_type == "action_rolled_back")
    assert result["status"] == "success" and store.get_action("act-flow").state == "rolled_back" and len(calls) == len(rollback_calls) == 1
    assert failed_entry.failure_ref == unknown["evidence_ref"] and rolled_entry.rollback_ref == result["rollback_ref"]


def test_broker_rejects_raw_content_before_it_can_create_durable_incident(tmp_path):
    clock = [100.0]
    unsafe = {
        "source": "auth_outcome", "event_type": "authentication", "status": "failed", "severity": "warn",
        "dimensions": {"outcome": "failed", "source_familiarity": "unknown", "session_created": "not_applicable"},
        "references": {"principal_ref": _ref("principal", "a")}, "measurements": {"event_count": 1},
        "raw_log": "must_not_cross_boundary",
    }
    try:
        build_security_evidence_envelope(unsafe)
    except ValueError:
        pass
    else:
        raise AssertionError("raw evidence unexpectedly accepted")
    store = SecurityIncidentStore(tmp_path / "raw-rejected.sqlite", clock=lambda: clock[0])
    assert store.audit_events() == ()
