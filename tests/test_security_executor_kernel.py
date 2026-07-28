import json
from dataclasses import replace

import pytest

from src.security_executor_contracts import (
    SECURITY_EXECUTION_REQUEST_SCHEMA,
    build_rollback_descriptor,
    SecurityExecutionReceipt,
    SecurityExecutionRequest,
    SecurityExecutorContractError,
)
from src.security_executor_kernel import SecurityExecutorKernel, TYPED_EXECUTOR_ACTION_TYPES
from src.security_incident_store import SecurityIncidentStore
from src.security_response_policy import typed_executor_action_types


def _store(tmp_path, clock):
    store = SecurityIncidentStore(tmp_path / "kernel.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-kernel", incident_ref="evidence:sha256:" + "a" * 64, audit_ref="audit:sha256:" + "b" * 64)
    store.create_action(
        action_id="act-kernel", incident_id="inc-kernel", action_type="crowdsec_temp_block",
        scope_fingerprint="scope:sha256:" + "c" * 64, policy_revision="policy:sha256:" + "d" * 64,
        idempotency_key="idem-kernel", ttl_seconds=60, audit_ref="audit:sha256:" + "e" * 64,
    )
    prepared = store.transition(action_id="act-kernel", expected_version=1, target_state="prepared", audit_ref="audit:sha256:" + "f" * 64)
    store.approve(
        action_id="act-kernel", expected_version=prepared.version, approval_id="approval-kernel",
        approval_ref="approval:sha256:" + "1" * 64, scope_fingerprint="scope:sha256:" + "c" * 64,
        policy_revision="policy:sha256:" + "d" * 64, audit_ref="audit:sha256:" + "2" * 64,
    )
    return store


def _request(**overrides):
    request = {
        "schema": SECURITY_EXECUTION_REQUEST_SCHEMA, "action_id": "act-kernel", "action_version": 3,
        "action_type": "crowdsec_temp_block", "scope_fingerprint": "scope:sha256:" + "c" * 64,
        "policy_revision": "policy:sha256:" + "d" * 64, "policy_gate": "crowdsec-remediation-go",
        "timeout_seconds": 30, "idempotency_key": "idem-kernel",
        "rollback_descriptor": "rollback:sha256:" + "3" * 64, "expires_at": 150.0,
    }
    request.update(overrides)
    if "rollback_descriptor" not in overrides and set(request) == {
        "schema", "action_id", "action_version", "action_type", "scope_fingerprint",
        "policy_revision", "policy_gate", "timeout_seconds", "idempotency_key",
        "rollback_descriptor", "expires_at",
    }:
        try:
            request["rollback_descriptor"] = build_rollback_descriptor(SecurityExecutionRequest.from_mapping(request))
        except SecurityExecutorContractError:
            pass
    return request


def _fake(calls):
    def execute(request):
        calls.append(request)
        return {"provider_response": "authorization: secret"}
    execute.security_executor_test_fake = True
    return execute


def test_kernel_binds_durable_approval_before_fake_side_effect_and_acknowledges_only(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    result = kernel.execute(_request())

    assert result["status"] == "success" and result["acknowledgement_received"] is True
    assert result["verification_state"] == "not_verified" and result["verified"] is False
    assert len(calls) == 1 and calls[0].action_id == "act-kernel" and calls[0].timeout_seconds == 30
    assert store.get_approval("act-kernel").consumed_at == 100.0
    assert store.get_action("act-kernel").state == "executed"
    assert store.get_action_evidence("act-kernel").verification_ref == ""
    encoded = json.dumps(result, sort_keys=True)
    assert "provider_response" not in encoded and "secret" not in encoded and len(encoded) < 1200


def test_kernel_dispatch_set_is_exactly_the_policy_set_and_unknowns_are_closed(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    assert TYPED_EXECUTOR_ACTION_TYPES == typed_executor_action_types()
    assert kernel.execute(_request(action_type="service_restart"))["reason"] == "unknown_action_type"
    assert calls == []


def test_kernel_consumes_the_single_use_approval_before_the_fake_is_called(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); observed = []
    def fake(_request):
        observed.append(store.get_approval("act-kernel").consumed_at)
    fake.security_executor_test_fake = True
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": fake}, clock=lambda: clock[0])

    assert kernel.execute(_request())["status"] == "success"
    assert observed == [100.0]


def test_timeout_completion_boundary_is_inclusive_but_never_exceeds_request_or_action_expiry(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    result = kernel.execute(_request(timeout_seconds=30, expires_at=130.0))

    assert result["status"] == "success" and len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("action_version", 2), ("action_type", "service_restart"),
    ("scope_fingerprint", "scope:sha256:" + "4" * 64),
    ("policy_revision", "policy:sha256:" + "5" * 64),
    ("policy_gate", "wrong-gate"), ("idempotency_key", "idem-other"),
    ("expires_at", 100.0),
])
def test_kernel_rejects_binding_mismatch_before_fake_side_effect(tmp_path, field, value):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    result = kernel.execute(_request(**{field: value}))

    assert result["status"] == "blocked" and calls == []
    assert store.get_action("act-kernel").state == "approved"
    assert store.get_approval("act-kernel").consumed_at is None


@pytest.mark.parametrize("overrides", [
    {"rollback_descriptor": "rollback:sha256:" + "8" * 64},
    {"expires_at": 161.0},
    {"timeout_seconds": 51, "expires_at": 150.0},
])
def test_kernel_rejects_rollback_or_expiry_authority_expansion_before_fake_side_effect(tmp_path, overrides):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    result = kernel.execute(_request(**overrides))

    assert result["reason"] == "durable_preflight_rejected" and calls == []
    assert store.get_action("act-kernel").state == "approved"
    assert store.get_approval("act-kernel").consumed_at is None


@pytest.mark.parametrize("request_data", [
    {"action_id": "act-kernel"}, _request(timeout_seconds=301),
    _request(rollback_descriptor="rollback:unsafe"), _request(extra="forbidden"),
])
def test_kernel_rejects_malformed_or_unbounded_requests_before_fake_side_effect(tmp_path, request_data):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    result = kernel.execute(request_data)

    assert result["reason"] == "invalid_execution_request" and calls == []


def test_kernel_requires_a_registered_marker_gated_fake_executor(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock)
    with pytest.raises(Exception):
        SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": lambda request: None})
    kernel = SecurityExecutorKernel(store, fake_executors={}, clock=lambda: clock[0])
    assert kernel.execute(_request())["reason"] == "missing_fake_executor"


def test_kernel_replay_is_immutable_and_conflicting_idempotency_reuse_fails_closed(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])
    first = kernel.execute(_request())
    replay = kernel.execute(_request())
    conflict = kernel.execute(_request(timeout_seconds=31))

    assert first["receipt_ref"] == replay["receipt_ref"] and replay["idempotent_replay"] is True
    assert len(calls) == 1
    assert conflict["status"] == "blocked" and conflict["reason"] == "idempotency_key_conflict"
    assert "receipt_ref" not in conflict


def test_durable_restart_replay_never_calls_the_fake_again_and_rejects_changed_fingerprint(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    first_kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])
    first = first_kernel.execute(_request())
    restarted_kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0])

    replay = restarted_kernel.execute(_request())
    conflict = restarted_kernel.execute(_request(timeout_seconds=31))

    assert replay["status"] == "success" and replay["idempotent_replay"] is True
    assert replay["receipt_ref"] == first["receipt_ref"]
    assert conflict["reason"] == "idempotency_key_conflict"
    assert len(calls) == 1


def test_durable_receipt_tamper_never_replays_or_reinvokes_the_fake(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())

    class TamperedStore:
        def get_action(self, action_id):
            return replace(store.get_action(action_id), receipt_ref="receipt:sha256:" + "9" * 64)
        def get_approval(self, action_id):
            return store.get_approval(action_id)
        def audit_events(self, action_id):
            return store.audit_events(action_id)

    result = SecurityExecutorKernel(TamperedStore(), fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())

    assert result["reason"] == "durable_receipt_mismatch"
    assert len(calls) == 1


def test_durable_inflight_or_failed_state_never_replays_or_reinvokes(tmp_path):
    clock = [100.0]; calls = []
    inflight_dir = tmp_path / "inflight"; inflight_dir.mkdir()
    inflight = _store(inflight_dir, clock)
    inflight.transition(action_id="act-kernel", expected_version=3, target_state="executing", audit_ref="audit:sha256:" + "7" * 64)
    inflight_result = SecurityExecutorKernel(inflight, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())

    failed_dir = tmp_path / "failed"; failed_dir.mkdir()
    failed = _store(failed_dir, clock)
    executing = failed.transition(action_id="act-kernel", expected_version=3, target_state="executing", audit_ref="audit:sha256:" + "7" * 64)
    failed.transition(action_id="act-kernel", expected_version=executing.version, target_state="failed", audit_ref="audit:sha256:" + "8" * 64, failure_ref="failure:sha256:" + "9" * 64)
    failed_result = SecurityExecutorKernel(failed, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())

    assert inflight_result["reason"] == failed_result["reason"] == "durable_nonreplayable_state"
    assert calls == []


def test_unknown_durable_executed_action_is_rejected_before_replay_lookup(tmp_path):
    clock = [100.0]
    store = SecurityIncidentStore(tmp_path / "unknown.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-unknown", incident_ref="evidence:sha256:" + "a" * 64, audit_ref="audit:sha256:" + "b" * 64)
    store.create_action(
        action_id="act-unknown", incident_id="inc-unknown", action_type="service_restart",
        scope_fingerprint="scope:sha256:" + "c" * 64, policy_revision="policy:sha256:" + "d" * 64,
        idempotency_key="idem-unknown", ttl_seconds=60, audit_ref="audit:sha256:" + "e" * 64,
    )
    prepared = store.transition(action_id="act-unknown", expected_version=1, target_state="prepared", audit_ref="audit:sha256:" + "f" * 64)
    approved = store.approve(
        action_id="act-unknown", expected_version=prepared.version, approval_id="approval-unknown",
        approval_ref="approval:sha256:" + "1" * 64, scope_fingerprint="scope:sha256:" + "c" * 64,
        policy_revision="policy:sha256:" + "d" * 64, audit_ref="audit:sha256:" + "2" * 64,
    )
    request = _request(action_id="act-unknown", action_version=approved.version, action_type="service_restart", idempotency_key="idem-unknown", policy_gate="unknown-gate")
    typed = SecurityExecutionRequest.from_mapping(request)
    executing = store.transition(action_id="act-unknown", expected_version=approved.version, target_state="executing", audit_ref=SecurityExecutorKernel._audit_ref("executing", typed))
    store.transition(action_id="act-unknown", expected_version=executing.version, target_state="executed", audit_ref=SecurityExecutorKernel._audit_ref("acknowledged", typed), receipt_ref=SecurityExecutorKernel._receipt(typed).receipt_ref)
    calls = []

    result = SecurityExecutorKernel(store, fake_executors={}, clock=lambda: clock[0]).execute(request)

    assert result["reason"] == "unknown_action_type" and calls == []


def test_durable_ack_replay_survives_verification_and_rollback_without_reinvoking(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock); calls = []
    first = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())
    executed = store.get_action("act-kernel")
    verified = store.transition(action_id="act-kernel", expected_version=executed.version, target_state="verified", audit_ref="audit:sha256:" + "a" * 64, verification_ref="verification:sha256:" + "b" * 64)
    after_verified = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())
    store.transition(action_id="act-kernel", expected_version=verified.version, target_state="rolled_back", audit_ref="audit:sha256:" + "c" * 64, rollback_ref="rollback:sha256:" + "d" * 64)
    after_rollback = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())

    assert after_verified["receipt_ref"] == after_rollback["receipt_ref"] == first["receipt_ref"]
    assert after_verified["idempotent_replay"] is after_rollback["idempotent_replay"] is True
    assert after_verified["verified"] is after_rollback["verified"] is False
    assert len(calls) == 1


def test_post_execution_failed_ack_replays_but_pre_execution_failure_does_not(tmp_path):
    clock = [100.0]; calls = []
    completed_dir = tmp_path / "completed"; completed_dir.mkdir()
    completed = _store(completed_dir, clock)
    first = SecurityExecutorKernel(completed, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())
    executed = completed.get_action("act-kernel")
    completed.transition(action_id="act-kernel", expected_version=executed.version, target_state="failed", audit_ref="audit:sha256:" + "a" * 64, failure_ref="failure:sha256:" + "b" * 64)
    replay = SecurityExecutorKernel(completed, fake_executors={"crowdsec_temp_block": _fake(calls)}, clock=lambda: clock[0]).execute(_request())

    assert replay["receipt_ref"] == first["receipt_ref"] and replay["verified"] is False
    assert replay["idempotent_replay"] is True and len(calls) == 1


def test_kernel_failed_fake_never_acknowledges_or_marks_verified(tmp_path):
    clock = [100.0]; store = _store(tmp_path, clock)
    def broken(_request):
        raise RuntimeError("provider secret response")
    broken.security_executor_test_fake = True
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": broken}, clock=lambda: clock[0])

    result = kernel.execute(_request())

    assert result == {"status": "blocked", "reason": "fake_executor_failed", "executed": False, "verified": False, "raw_content_visible": False}
    assert store.get_action("act-kernel").state == "failed"


def test_receipt_contract_cannot_be_constructed_as_independently_verified():
    with pytest.raises(SecurityExecutorContractError):
        SecurityExecutionReceipt(
            action_id="act-kernel", action_version=3, action_type="crowdsec_temp_block",
            idempotency_key="idem-kernel", receipt_ref="receipt:sha256:" + "a" * 64,
            verification_state="verified", verified=True,
        )
