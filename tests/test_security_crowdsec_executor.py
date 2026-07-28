import json
import hashlib
import copy
import pickle

import pytest

from src.security_crowdsec_contracts import (
    CROWDSEC_POLICY_GATE,
    CrowdSecAcknowledgement,
    CrowdSecContractError,
    CrowdSecRemediation,
    CROWDSEC_TEST_ONLY_ISSUER,
    MAX_CROWDSEC_TTL_SECONDS,
    MIN_CROWDSEC_TTL_SECONDS,
    issue_test_crowdsec_execution_authority,
)
import src.security_crowdsec_contracts as crowdsec_contracts
from src.security_executor_contracts import (
    SECURITY_EXECUTION_REQUEST_SCHEMA,
    SecurityExecutionRequest,
    build_rollback_descriptor,
    request_fingerprint,
)
from src.security_executor_kernel import SecurityExecutorKernel
from src.security_executors.crowdsec import CrowdSecExecutorError, CrowdSecTypedExecutor
from src.security_incident_store import SecurityIncidentStore


def _request(**overrides):
    value = {
        "schema": SECURITY_EXECUTION_REQUEST_SCHEMA, "action_id": "act-crowdsec", "action_version": 3,
        "action_type": "crowdsec_temp_block", "scope_fingerprint": "scope:sha256:" + "a" * 64,
        "policy_revision": "policy:sha256:" + "b" * 64, "policy_gate": CROWDSEC_POLICY_GATE,
        "timeout_seconds": 120, "idempotency_key": "idem-crowdsec",
        "rollback_descriptor": "rollback:sha256:" + "c" * 64, "expires_at": 220.0,
    }
    value.update(overrides)
    if "rollback_descriptor" not in overrides:
        typed = SecurityExecutionRequest.from_mapping(value)
        value["rollback_descriptor"] = build_rollback_descriptor(typed)
    return SecurityExecutionRequest.from_mapping(value)


def _remediation(**overrides):
    action_type = overrides.get("action_type", "crowdsec_temp_block")
    request = _request(action_type=action_type)
    ttl = overrides.pop("ttl_seconds", 60)
    descriptor_kind = "expiry" if action_type == "crowdsec_temp_block" else "unban"
    digest = request_fingerprint(request)
    remediation = CrowdSecRemediation(
        action_id=request.action_id, action_type=action_type,
        scope_handle=overrides.pop("scope_handle", request.scope_fingerprint), ttl_seconds=ttl,
        expiry_descriptor=overrides.pop("expiry_descriptor", "crowdsec-" + descriptor_kind + ":sha256:" + hashlib.sha256((descriptor_kind + "|" + digest).encode()).hexdigest()),
        policy_gate=overrides.pop("policy_gate", request.policy_gate),
        execution_binding=overrides.pop("execution_binding", "execution:sha256:" + digest),
        false_positive_risk=overrides.pop("false_positive_risk", False),
        operator_lockout_risk=overrides.pop("operator_lockout_risk", False),
    )
    return request, CrowdSecRemediation(**{field: overrides.get(field, getattr(remediation, field)) for field in remediation.__dataclass_fields__})


def _authority(request, remediation):
    return issue_test_crowdsec_execution_authority(
        request, remediation, test_issuer=CROWDSEC_TEST_ONLY_ISSUER,
    )


def _mapping(request):
    return {
        "schema": request.schema, "action_id": request.action_id, "action_version": request.action_version,
        "action_type": request.action_type, "scope_fingerprint": request.scope_fingerprint,
        "policy_revision": request.policy_revision, "policy_gate": request.policy_gate,
        "timeout_seconds": request.timeout_seconds, "idempotency_key": request.idempotency_key,
        "rollback_descriptor": request.rollback_descriptor, "expires_at": request.expires_at,
    }


class _FakeTransport:
    security_crowdsec_test_fake = True

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def execute_typed(self, operation):
        self.calls.append(operation)
        if self.error is not None:
            raise self.error
        return self.result


def _store(tmp_path, clock, *, action_type="crowdsec_temp_block"):
    store = SecurityIncidentStore(tmp_path / "crowdsec.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-crowdsec", incident_ref="evidence:sha256:" + "a" * 64, audit_ref="audit:sha256:" + "b" * 64)
    store.create_action(
        action_id="act-crowdsec", incident_id="inc-crowdsec", action_type=action_type,
        scope_fingerprint="scope:sha256:" + "a" * 64, policy_revision="policy:sha256:" + "b" * 64,
        idempotency_key="idem-crowdsec", ttl_seconds=120, audit_ref="audit:sha256:" + "c" * 64,
    )
    prepared = store.transition(action_id="act-crowdsec", expected_version=1, target_state="prepared", audit_ref="audit:sha256:" + "d" * 64)
    store.approve(
        action_id="act-crowdsec", expected_version=prepared.version, approval_id="approval-crowdsec",
        approval_ref="approval:sha256:" + "e" * 64, scope_fingerprint="scope:sha256:" + "a" * 64,
        policy_revision="policy:sha256:" + "b" * 64, audit_ref="audit:sha256:" + "f" * 64,
    )
    return store


def test_temporary_block_and_explicit_unblock_have_closed_descriptors():
    block_request, block = _remediation(ttl_seconds=MIN_CROWDSEC_TTL_SECONDS)
    unblock_request, unblock = _remediation(action_type="crowdsec_unblock", ttl_seconds=0)

    assert block.operation() == "temporary_block"
    assert block.expiry_descriptor.startswith("crowdsec-expiry:sha256:")
    assert unblock.operation() == "explicit_unblock"
    assert unblock.expiry_descriptor.startswith("crowdsec-unban:sha256:")
    assert block_request.policy_gate == unblock_request.policy_gate == CROWDSEC_POLICY_GATE


@pytest.mark.parametrize("ttl", [0, MIN_CROWDSEC_TTL_SECONDS - 1, MAX_CROWDSEC_TTL_SECONDS + 1, True])
def test_block_ttl_is_bounded_and_unblock_requires_zero(ttl):
    with pytest.raises(CrowdSecContractError):
        _remediation(ttl_seconds=ttl)[1].validate()
    with pytest.raises(CrowdSecContractError):
        _remediation(action_type="crowdsec_unblock", ttl_seconds=1)[1].validate()


def test_default_disabled_and_unmarked_transport_reject_before_calls():
    request, remediation = _remediation()
    disabled = CrowdSecTypedExecutor({request.action_id: remediation})
    with pytest.raises(CrowdSecExecutorError, match="disabled"):
        disabled.execute(request)
    with pytest.raises(CrowdSecExecutorError):
        CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=lambda _operation: None)


@pytest.mark.parametrize("override", [{"false_positive_risk": True}, {"operator_lockout_risk": True}])
def test_preflight_blockers_reject_before_fake_calls(override):
    request, remediation = _remediation(**override)
    fake = _FakeTransport()
    executor = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake)

    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        executor.execute(request)
    assert fake.calls == []


def test_gate_mismatch_and_scope_leak_are_rejected_by_contract_before_transport_exists():
    request = _request()
    with pytest.raises(CrowdSecContractError):
        CrowdSecRemediation.from_mapping({
            "schema": "odysseus.crowdsec_remediation.v1", "action_id": request.action_id,
            "action_type": request.action_type, "scope_handle": "203.0.113.9", "ttl_seconds": 60,
            "expiry_descriptor": "crowdsec-expiry:sha256:" + "a" * 64, "policy_gate": CROWDSEC_POLICY_GATE,
            "execution_binding": "execution:sha256:" + "a" * 64,
            "false_positive_risk": False, "operator_lockout_risk": False,
        })
    request, remediation = _remediation()
    with pytest.raises(CrowdSecContractError):
        CrowdSecRemediation(**{**{field: getattr(remediation, field) for field in remediation.__dataclass_fields__}, "policy_gate": "wrong-gate"}).validate()


def test_request_only_reconstruction_and_replayed_authority_reject_before_fake_calls():
    request, remediation = _remediation()
    fake = _FakeTransport()
    executor = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake)

    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        executor.execute(request)
    authority = _authority(request, remediation)
    first = executor.execute(request, authority)
    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        executor.execute(request, authority)

    assert first["acknowledgement_received"] is True
    assert len(fake.calls) == 1


def test_mismatched_authority_and_cross_adapter_replay_reject_before_fake_calls():
    request, remediation = _remediation()
    mismatched = CrowdSecRemediation(
        **{**{field: getattr(remediation, field) for field in remediation.__dataclass_fields__}, "ttl_seconds": 61}
    )
    authority = _authority(request, mismatched)
    first_fake = _FakeTransport()
    second_fake = _FakeTransport()
    first = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=first_fake)
    second = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=second_fake)

    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        first.execute(request, authority)
    assert first_fake.calls == []

    exact_authority = _authority(request, remediation)
    second.execute(request, exact_authority)
    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        first.execute(request, exact_authority)
    assert len(second_fake.calls) == 1 and first_fake.calls == []


def test_authority_cannot_be_constructed_copied_or_serialized_for_revival():
    request, remediation = _remediation()
    authority = _authority(request, remediation)

    with pytest.raises(CrowdSecContractError):
        type(authority)()
    with pytest.raises(CrowdSecContractError):
        copy.copy(authority)
    with pytest.raises(CrowdSecContractError):
        copy.deepcopy(authority)
    with pytest.raises(CrowdSecContractError):
        pickle.dumps(authority)


def test_no_production_mint_path_exists_and_slot_cloned_forgery_rejects_before_fake():
    request, remediation = _remediation()
    fake = _FakeTransport()
    executor = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake)
    authority = _authority(request, remediation)
    forged = object.__new__(type(authority))

    assert not hasattr(crowdsec_contracts, "_SIRP06_COMPOSITION_ISSUER")
    assert not hasattr(crowdsec_contracts, "_seal_consumed_sirp06_authority")
    assert type(authority).__slots__ == ()
    for slot in type(authority).__slots__:
        setattr(forged, slot, getattr(authority, slot))
    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        executor.execute(request, forged)
    assert fake.calls == []

    assert executor.execute(request, authority)["acknowledgement_received"] is True
    assert len(fake.calls) == 1


def test_fixed_receipt_hides_scope_descriptors_and_raw_fake_response():
    request, remediation = _remediation()
    fake = _FakeTransport(result={"target": "203.0.113.9", "authorization": "secret", "endpoint": "/v1/decisions"})
    result = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake).execute(request, _authority(request, remediation))
    encoded = json.dumps(result, sort_keys=True)

    assert set(result) == {"schema", "action_id", "action_type", "receipt_ref", "acknowledgement_received", "verification_state", "verified", "raw_content_visible"}
    assert result["verification_state"] == "not_verified" and result["verified"] is False
    for forbidden in (request.scope_fingerprint, remediation.expiry_descriptor, "203.0.113.9", "secret", "endpoint"):
        assert forbidden not in encoded


def test_raw_transport_exception_is_fully_discarded_and_consumes_authority_first():
    request, remediation = _remediation()
    fake = _FakeTransport(error=RuntimeError("provider response target=203.0.113.9 authorization=secret"))
    authority = _authority(request, remediation)
    executor = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake)

    with pytest.raises(CrowdSecExecutorError) as exc_info:
        executor.execute(request, authority)

    assert str(exc_info.value) == "CrowdSec transport acknowledgement unavailable"
    assert "203.0.113.9" not in str(exc_info.value) and "secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None and exc_info.value.__context__ is None
    with pytest.raises(CrowdSecExecutorError, match="preflight"):
        executor.execute(request, authority)
    assert len(fake.calls) == 1


def test_kernel_gate_capability_and_preflight_reject_before_fake_transport(tmp_path):
    clock = [100.0]
    store = _store(tmp_path, clock)
    request, remediation = _remediation()
    fake = _FakeTransport()
    adapter = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake)
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": adapter}, clock=lambda: clock[0])

    blocked = kernel.execute(_mapping(_request(policy_gate="wrong-gate")))
    assert blocked["status"] == "blocked" and fake.calls == []


def test_current_one_argument_kernel_invocation_fails_closed_before_fake_transport(tmp_path):
    clock = [100.0]
    store = _store(tmp_path, clock)
    request, remediation = _remediation()
    fake = _FakeTransport(result={"raw": "ignored"})
    adapter = CrowdSecTypedExecutor({request.action_id: remediation}, fake_transport=fake)
    kernel = SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": adapter}, clock=lambda: clock[0])
    result = kernel.execute(_mapping(request))

    assert result == {"status": "blocked", "reason": "fake_executor_failed", "executed": False, "verified": False, "raw_content_visible": False}
    assert fake.calls == [] and store.get_approval("act-crowdsec").consumed_at == 100.0


def test_acknowledgement_contract_cannot_claim_independent_verification():
    with pytest.raises(CrowdSecContractError):
        CrowdSecAcknowledgement(
            action_id="act-crowdsec", action_type="crowdsec_temp_block",
            receipt_ref="crowdsec-receipt:sha256:" + "a" * 64, verification_state="verified", verified=True,
        )
