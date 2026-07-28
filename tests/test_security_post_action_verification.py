import copy
import inspect
import json
from dataclasses import replace

import pytest

from src.security_executor_contracts import SECURITY_EXECUTION_REQUEST_SCHEMA, SecurityExecutionRequest, build_rollback_descriptor
from src.security_executor_kernel import SecurityExecutorKernel
from src.security_incident_audit import SecurityIncidentAuditChain, SecurityIncidentAuditError
from src.security_incident_store import SecurityIncidentStore
from src.security_post_action_verification import IssuedVerificationSource, PostActionVerificationError, PostActionVerifier, VERIFICATION_TEST_ONLY_ISSUER, VerificationObservation, issue_test_verification_source


def _request(**overrides):
    value = {"schema": SECURITY_EXECUTION_REQUEST_SCHEMA, "action_id": "act-verify", "action_version": 3, "action_type": "crowdsec_temp_block", "scope_fingerprint": "scope:sha256:" + "a" * 64, "policy_revision": "policy:sha256:" + "b" * 64, "policy_gate": "crowdsec-remediation-go", "timeout_seconds": 30, "idempotency_key": "idem-verify", "rollback_descriptor": "rollback:sha256:" + "0" * 64, "expires_at": 150.0}
    value.update(overrides)
    if "rollback_descriptor" not in overrides:
        value["rollback_descriptor"] = build_rollback_descriptor(SecurityExecutionRequest.from_mapping(value))
    return value


def _store(tmp_path, clock):
    store = SecurityIncidentStore(tmp_path / "verify.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-verify", incident_ref="evidence:sha256:" + "1" * 64, audit_ref="audit:sha256:" + "2" * 64)
    store.create_action(action_id="act-verify", incident_id="inc-verify", action_type="crowdsec_temp_block", scope_fingerprint="scope:sha256:" + "a" * 64, policy_revision="policy:sha256:" + "b" * 64, idempotency_key="idem-verify", ttl_seconds=60, audit_ref="audit:sha256:" + "3" * 64)
    prepared = store.transition(action_id="act-verify", expected_version=1, target_state="prepared", audit_ref="audit:sha256:" + "4" * 64)
    store.approve(action_id="act-verify", expected_version=prepared.version, approval_id="approval-verify", approval_ref="approval:sha256:" + "5" * 64, scope_fingerprint="scope:sha256:" + "a" * 64, policy_revision="policy:sha256:" + "b" * 64, audit_ref="audit:sha256:" + "6" * 64)
    return store


def _execute(store, clock):
    def fake(request):
        return {"provider_response": "authorization: raw-secret"}
    fake.security_executor_test_fake = True
    acknowledgement = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": fake}, clock=lambda: clock[0]).execute(_request())
    assert acknowledgement["verified"] is False and acknowledgement["verification_state"] == "not_verified"
    return acknowledgement


def _source(outcome="success", *, source_ref=None):
    refs = {"success": "a", "failed": "b", "unknown": "c", None: "d"}
    return issue_test_verification_source(source_ref=source_ref or "observer:sha256:" + refs[outcome] * 64, outcome=outcome, test_issuer=VERIFICATION_TEST_ONLY_ISSUER)


@pytest.mark.parametrize(("outcome", "state", "status", "verified"), (("success", "verified", "success", True), ("failed", "failed", "blocked", False), ("unknown", "failed", "blocked", False)))
def test_independent_verification_success_failed_unknown_matrix(tmp_path, outcome, state, status, verified):
    clock = [100.0]; store = _store(tmp_path, clock); acknowledgement = _execute(store, clock); source = _source(outcome)
    result = PostActionVerifier(store, source).verify(_request())
    assert result["status"] == status and result["outcome"] == outcome and result["verified"] is verified and result["closure_blocked"] is (not verified)
    assert store.get_action("act-verify").state == state and acknowledgement["receipt_ref"].startswith("receipt:sha256:")


def test_acknowledgement_never_verifies_and_issued_source_receives_no_raw_action_id(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); acknowledgement = _execute(store, clock); source = _source()
    verifier = PostActionVerifier(store, source)
    assert verifier.verify(acknowledgement)["reason"] == "invalid_execution_request" and store.get_action("act-verify").state == "executed"
    assert verifier.verify(_request())["verified"] is True
    assert "callback" not in inspect.signature(issue_test_verification_source).parameters


def test_issued_source_observation_is_unforgeable_single_use_and_not_serializable(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); _execute(store, clock); source = _source()
    with pytest.raises(PostActionVerificationError):
        VerificationObservation()
    with pytest.raises(PostActionVerificationError):
        IssuedVerificationSource()
    with pytest.raises(PostActionVerificationError):
        PostActionVerifier(store, object())
    assert PostActionVerifier(store, source).verify(_request())["status"] == "success"
    with pytest.raises(PostActionVerificationError):
        copy.copy(source)
    with pytest.raises(PostActionVerificationError):
        source.__reduce_ex__(4)


@pytest.mark.parametrize("outcome", ("success", "failed", "unknown"))
def test_terminal_replay_recomputes_exact_evidence_and_audit_binding(tmp_path, outcome):
    clock = [100.0]; store = _store(tmp_path, clock); _execute(store, clock); source = _source(outcome)
    assert PostActionVerifier(store, source).verify(_request())["outcome"] == outcome
    replay = PostActionVerifier(store, source).verify(_request())
    assert replay["outcome"] == outcome and replay["idempotent_replay"] is True


@pytest.mark.parametrize("outcome", ("success", "failed", "unknown"))
def test_tampered_terminal_evidence_or_audit_never_replays(tmp_path, outcome):
    clock = [100.0]; store = _store(tmp_path, clock); _execute(store, clock); source = _source(outcome)
    PostActionVerifier(store, source).verify(_request())
    class TamperedEvidence:
        def __getattr__(self, name): return getattr(store, name)
        def get_action(self, action_id):
            action = store.get_action(action_id)
            field = "verification_ref" if outcome == "success" else "failure_ref"
            return replace(action, **{field: ("verification" if outcome == "success" else "verification-" + outcome) + ":sha256:" + "f" * 64})
    assert PostActionVerifier(TamperedEvidence(), source).verify(_request())["reason"] == "durable_verification_mismatch"
    class TamperedAudit:
        def __getattr__(self, name): return getattr(store, name)
        def audit_events(self, action_id=None):
            return tuple(replace(event, reference="audit:sha256:" + "e" * 64) if event.event_type in {"action_verified", "action_failed"} else event for event in store.audit_events(action_id))
    assert PostActionVerifier(TamperedAudit(), source).verify(_request())["reason"] == "durable_verification_mismatch"


def test_unavailable_fixture_and_exact_source_identity_are_fail_closed(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); _execute(store, clock)
    result = PostActionVerifier(store, _source(None)).verify(_request())
    assert result["outcome"] == "unknown" and "raw-secret" not in json.dumps(result) and store.get_action("act-verify").state == "failed"
    store2 = _store(tmp_path / "other", clock) if (tmp_path / "other").mkdir() is None else None
    _execute(store2, clock); source = _source(); verified = PostActionVerifier(store2, source); assert verified.verify(_request())["outcome"] == "success"
    request = SecurityExecutionRequest.from_mapping(_request())
    assert store2.get_action("act-verify").verification_ref == verified._evidence_ref(request, "success", "observer:sha256:" + "a" * 64)
    different_source = _source("success", source_ref="observer:sha256:" + "e" * 64)
    assert PostActionVerifier(store2, different_source).verify(_request())["reason"] == "durable_verification_mismatch"


def test_fixture_issuance_never_executes_caller_code(tmp_path):
    class Callback:
        called = False
        def __call__(self):
            self.called = True
            raise AssertionError("must not run")
    callback = Callback()
    with pytest.raises(PostActionVerificationError):
        issue_test_verification_source(source_ref="observer:sha256:" + "f" * 64, outcome=callback, test_issuer=VERIFICATION_TEST_ONLY_ISSUER)
    assert callback.called is False


def test_audit_projection_binds_redacted_execution_and_verification_evidence(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); _execute(store, clock); PostActionVerifier(store, _source()).verify(_request())
    snapshot = SecurityIncidentAuditChain.snapshot(store, action_id="act-verify")
    projection = SecurityIncidentAuditChain.projection(snapshot); encoded = json.dumps(projection, sort_keys=True)
    executed = next(item for item in snapshot.entries if item.event_type == "action_executed")
    verified = next(item for item in snapshot.entries if item.event_type == "action_verified")
    assert executed.receipt_ref and verified.verification_ref and not executed.verification_ref and not verified.receipt_ref
    assert "act-verify" not in encoded and "inc-verify" not in encoded and "raw-secret" not in encoded
    with pytest.raises(SecurityIncidentAuditError):
        SecurityIncidentAuditChain.verify(replace(snapshot, entries=(replace(executed, receipt_ref="receipt:sha256:" + "0" * 64),) + snapshot.entries[1:]))


def test_audit_projection_rejects_raw_or_missing_terminal_evidence():
    class Record:
        sequence = 1; incident_id = "incident-raw"; action_id = "action-raw"; action_version = 1; event_type = "action_executed"; reference = "audit:sha256:" + "a" * 64; occurred_at = 1.0
    class UnsafeStore:
        def audit_events(self, action_id=None): return (Record(),)
        def get_action_evidence(self, action_id): return object()
    with pytest.raises(SecurityIncidentAuditError):
        SecurityIncidentAuditChain.snapshot(UnsafeStore())


def test_audit_snapshot_of_expirable_nonterminal_history_never_writes(tmp_path):
    clock = [0.0]
    store = SecurityIncidentStore(tmp_path / "read-only-audit.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-audit", incident_ref="evidence:sha256:" + "1" * 64, audit_ref="audit:sha256:" + "2" * 64)
    store.create_action(action_id="act-audit", incident_id="inc-audit", action_type="crowdsec_temp_block", scope_fingerprint="scope:sha256:" + "a" * 64, policy_revision="policy:sha256:" + "b" * 64, idempotency_key="idem-audit", ttl_seconds=1, audit_ref="audit:sha256:" + "3" * 64)
    before = store.audit_events("act-audit")
    clock[0] = 2.0
    snapshot = SecurityIncidentAuditChain.snapshot(store, action_id="act-audit")
    after = store.audit_events("act-audit")
    assert len(before) == len(after) == 1 and snapshot.entries[0].event_type == "action_proposed"
