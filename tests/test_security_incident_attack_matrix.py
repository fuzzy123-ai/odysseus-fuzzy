"""SIRP-10 fail-closed attack matrix using only sealed local fakes."""

import copy
import json
import pickle

import pytest

from src.security_action_authorization import SecurityActionAuthorization, SecurityActionAuthorizationError
from src.security_executor_contracts import SecurityExecutionReceipt, SecurityExecutionRequest, SecurityExecutorContractError, build_rollback_descriptor
from src.security_executor_kernel import SecurityExecutorKernel
from src.security_incident_commands import SecurityIncidentCommandError, SecurityIncidentCommands
from src.security_incident_delivery import record_dry_run_delivery
from src.security_incident_store import ConflictError, SecurityIncidentStore
from src.security_post_action_verification import (
    IssuedVerificationSource,
    PostActionVerifier,
    VERIFICATION_TEST_ONLY_ISSUER,
    VerificationObservation,
    issue_test_verification_source,
)
from src.security_rollback import (
    IssuedRollbackAdapter,
    RollbackAcknowledgement,
    RollbackOperation,
    ROLLBACK_TEST_ONLY_ISSUER,
    issue_test_rollback_acknowledgement,
    issue_test_rollback_adapter,
)


def _ref(kind, char): return f"{kind}:sha256:{char * 64}"


def _request(**changes):
    values = {
        "action_id": "act-attack", "action_version": 3, "action_type": "crowdsec_temp_block",
        "scope_fingerprint": _ref("scope", "c"), "policy_revision": _ref("policy", "d"),
        "policy_gate": "crowdsec-remediation-go", "timeout_seconds": 30, "idempotency_key": "idem-attack", "expires_at": 150.0,
    }
    values.update(changes)
    provisional = SecurityExecutionRequest(rollback_descriptor=_ref("rollback", "0"), **values)
    return SecurityExecutionRequest(rollback_descriptor=build_rollback_descriptor(provisional), **values)


def _store(tmp_path, clock, *, approve=True):
    store = SecurityIncidentStore(tmp_path / "sirp10-attack.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-attack", incident_ref=_ref("evidence", "a"), audit_ref=_ref("audit", "b"))
    action = store.create_action(action_id="act-attack", incident_id="inc-attack", action_type="crowdsec_temp_block", scope_fingerprint=_ref("scope", "c"), policy_revision=_ref("policy", "d"), idempotency_key="idem-attack", ttl_seconds=60, audit_ref=_ref("audit", "e"))
    prepared = store.transition(action_id=action.action_id, expected_version=action.version, target_state="prepared", audit_ref=_ref("audit", "f"))
    if approve:
        store.approve(action_id=action.action_id, expected_version=prepared.version, approval_id="approval-attack", approval_ref=_ref("approval", "1"), scope_fingerprint=prepared.scope_fingerprint, policy_revision=prepared.policy_revision, audit_ref=_ref("audit", "2"))
    return store


def _kernel(store, clock, calls):
    def effect(_request): calls.append(True)
    effect.security_executor_test_fake = True
    return SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": effect}, clock=lambda: clock[0])


@pytest.mark.parametrize("changes", [
    {"action_version": 2},  # stale version
    {"scope_fingerprint": _ref("scope", "9")},  # target substitution
    {"scope_fingerprint": _ref("scope", "8"), "timeout_seconds": 31},  # scope expansion attempt
    {"policy_revision": _ref("policy", "7")},
])
def test_bound_request_substitution_and_staleness_never_call_effects(tmp_path, changes):
    clock, calls = [100.0], []
    store = _store(tmp_path, clock)
    result = _kernel(store, clock, calls).execute(_request(**changes))
    assert result["status"] == "blocked" and result["verified"] is False and calls == []
    assert store.get_action("act-attack").state == "approved"


def test_execution_replay_is_single_effect_and_never_closes_success(tmp_path):
    clock, calls = [100.0], []
    store, request = _store(tmp_path, clock), _request()
    kernel = _kernel(store, clock, calls)
    first, replay = kernel.execute(request), kernel.execute(request)
    assert first["receipt_ref"] == replay["receipt_ref"] and replay["idempotent_replay"] is True
    assert first["verified"] is replay["verified"] is False and len(calls) == 1
    assert len(calls) == 1 and store.get_action("act-attack").state == "executed"


class _Auth:
    def validate_token(self, token): return token == "session"
    def get_username_for_token(self, token): return "admin" if token == "session" else None
    def is_admin(self, username): return username == "admin"
    def totp_enabled(self, username): return True
    def security_action_revision(self): return 0
    def verify_password(self, username, password): return username == "admin" and password == "correct"
    def totp_verify_live(self, username, code): return username == "admin" and code == "123456"


def test_step_up_and_durable_approval_reuse_are_cross_action_fail_closed(tmp_path):
    clock, effects = [100.0], []
    store = SecurityIncidentStore(tmp_path / "cross-action.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-cross", incident_ref=_ref("evidence", "a"), audit_ref=_ref("audit", "b"))
    prepared = []
    for action_id, scope, key in (("act-cross-a", "c", "idem-cross-a"), ("act-cross-b", "d", "idem-cross-b")):
        action = store.create_action(action_id=action_id, incident_id="inc-cross", action_type="crowdsec_temp_block", scope_fingerprint=_ref("scope", scope), policy_revision=_ref("policy", "e"), idempotency_key=key, ttl_seconds=60, audit_ref=_ref("audit", scope))
        prepared.append(store.transition(action_id=action.action_id, expected_version=action.version, target_state="prepared", audit_ref=_ref("audit", "f")))
    gate = SecurityActionAuthorization(_Auth(), clock=lambda: clock[0])
    commands = SecurityIncidentCommands(store, gate)
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=prepared[0], auth_kind="browser_cookie")
    with pytest.raises(SecurityIncidentCommandError):
        commands.approve(action_id="act-cross-b", session_token="session", username="admin")
    assert store.get_action("act-cross-a").state == store.get_action("act-cross-b").state == "prepared" and effects == []
    approved = commands.approve(action_id="act-cross-a", session_token="session", username="admin")
    assert approved["action_state"] == "approved" and store.get_approval("act-cross-a").consumed_at is None
    with pytest.raises(SecurityIncidentCommandError):
        commands.approve(action_id="act-cross-a", session_token="session", username="admin")
    approval = store.get_approval("act-cross-a")
    with pytest.raises(ConflictError):
        store.approve(action_id="act-cross-b", expected_version=prepared[1].version, approval_id=approval.approval_id, approval_ref=approval.approval_ref, scope_fingerprint=prepared[1].scope_fingerprint, policy_revision=prepared[1].policy_revision, audit_ref=_ref("audit", "9"))
    assert store.get_action("act-cross-a").state == "approved" and store.get_action("act-cross-b").state == "prepared" and effects == []


def test_missing_step_up_and_delivery_failure_are_no_send_no_effect_boundaries(tmp_path):
    clock, calls = [100.0], []
    store = _store(tmp_path, clock, approve=False)
    action = store.get_action("act-attack")
    with pytest.raises(SecurityActionAuthorizationError):
        SecurityActionAuthorization(_Auth(), clock=lambda: clock[0]).consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")
    blocked = _kernel(store, clock, calls).execute(_request(action_version=action.version))
    assert blocked["status"] == "blocked" and blocked["verified"] is False and calls == []
    # The only accepted delivery contract is immutable dry-run/no-send; malformed retry cannot schedule an effect.
    receipt = record_dry_run_delivery(action_id="act-attack")
    assert receipt.delivery_performed is False and receipt.retry_scheduled is False and calls == []
    with pytest.raises(ValueError): record_dry_run_delivery(action_id="act-attack", retry_count=2)


@pytest.mark.parametrize(("source_outcome", "expected_outcome"), [("failed", "failed"), ("unknown", "unknown"), (None, "unknown")])
def test_verifier_failure_unknown_or_unavailable_never_sets_verified_or_closure_success(tmp_path, source_outcome, expected_outcome):
    clock, calls = [100.0], []
    store, request = _store(tmp_path, clock), _request()
    assert _kernel(store, clock, calls).execute(request)["status"] == "success"
    failed = PostActionVerifier(store, issue_test_verification_source(source_ref=_ref("source", "4"), outcome=source_outcome, test_issuer=VERIFICATION_TEST_ONLY_ISSUER)).verify(request)
    # A source that is unavailable is deliberately normalized to the same fail-closed unknown terminal evidence.
    assert failed["outcome"] == expected_outcome and failed["verified"] is False and failed["closure_blocked"] is True and store.get_action("act-attack").state == "failed"
    assert len(calls) == 1


def test_sealed_issuers_and_receipts_cannot_be_forged_copied_serialized_or_rewrapped():
    source = issue_test_verification_source(source_ref=_ref("source", "5"), outcome="success", test_issuer=VERIFICATION_TEST_ONLY_ISSUER)
    adapter = issue_test_rollback_adapter(lambda _operation: None, test_issuer=ROLLBACK_TEST_ONLY_ISSUER)
    operation = RollbackOperation(action_ref=_ref("action", "7"), action_type="crowdsec_temp_block", scope_fingerprint=_ref("scope", "c"), receipt_ref=_ref("receipt", "6"), rollback_descriptor=_ref("rollback", "8"), mode="manual")
    acknowledgement = issue_test_rollback_acknowledgement(adapter, operation, test_issuer=ROLLBACK_TEST_ONLY_ISSUER)
    for value in (source, acknowledgement, object.__new__(IssuedVerificationSource), object.__new__(VerificationObservation), object.__new__(RollbackAcknowledgement), object.__new__(IssuedRollbackAdapter)):
        for operation in (lambda: copy.copy(value), lambda: copy.deepcopy(value), lambda: pickle.dumps(value)):
            with pytest.raises(ValueError): operation()
    with pytest.raises(ValueError): IssuedVerificationSource()
    with pytest.raises(ValueError): RollbackAcknowledgement()
    with pytest.raises(SecurityExecutorContractError):
        SecurityExecutionReceipt(action_id="act-attack", action_version=3, action_type="crowdsec_temp_block", idempotency_key="idem-attack", receipt_ref=_ref("receipt", "6"), verification_state="verified", verified=True)
    encoded = json.dumps({"source_type": type(source).__name__})
    assert "sha256" not in encoded and "provider" not in encoded
