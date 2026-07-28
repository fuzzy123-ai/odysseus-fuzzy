import copy
import threading
from dataclasses import replace

import pytest

from src.security_executor_contracts import SECURITY_EXECUTION_REQUEST_SCHEMA, SecurityExecutionRequest, build_rollback_descriptor
from src.security_executor_kernel import SecurityExecutorKernel
from src.security_incident_audit import SecurityIncidentAuditChain
from src.security_incident_store import SecurityIncidentStore
from src.security_rollback import IssuedRollbackAdapter, ROLLBACK_TEST_ONLY_ISSUER, RollbackAcknowledgement, SecurityRollbackError, SecurityRollbackManager, issue_test_rollback_acknowledgement, issue_test_rollback_adapter


def _request(**overrides):
    value = {"schema": SECURITY_EXECUTION_REQUEST_SCHEMA, "action_id": "act-rollback", "action_version": 3, "action_type": "crowdsec_temp_block", "scope_fingerprint": "scope:sha256:" + "a" * 64, "policy_revision": "policy:sha256:" + "b" * 64, "policy_gate": "crowdsec-remediation-go", "timeout_seconds": 30, "idempotency_key": "idem-rollback", "rollback_descriptor": "rollback:sha256:" + "0" * 64, "expires_at": 150.0}
    value.update(overrides)
    if "rollback_descriptor" not in overrides:
        value["rollback_descriptor"] = build_rollback_descriptor(SecurityExecutionRequest.from_mapping(value))
    return value


def _executed_store(tmp_path, clock):
    store = SecurityIncidentStore(tmp_path / "rollback.sqlite", clock=lambda: clock[0])
    store.create_incident(incident_id="inc-rollback", incident_ref="evidence:sha256:" + "1" * 64, audit_ref="audit:sha256:" + "2" * 64)
    store.create_action(action_id="act-rollback", incident_id="inc-rollback", action_type="crowdsec_temp_block", scope_fingerprint="scope:sha256:" + "a" * 64, policy_revision="policy:sha256:" + "b" * 64, idempotency_key="idem-rollback", ttl_seconds=60, audit_ref="audit:sha256:" + "3" * 64)
    prepared = store.transition(action_id="act-rollback", expected_version=1, target_state="prepared", audit_ref="audit:sha256:" + "4" * 64)
    store.approve(action_id="act-rollback", expected_version=prepared.version, approval_id="approval-rollback", approval_ref="approval:sha256:" + "5" * 64, scope_fingerprint="scope:sha256:" + "a" * 64, policy_revision="policy:sha256:" + "b" * 64, audit_ref="audit:sha256:" + "6" * 64)
    def executor(request): return {"raw_provider_response": "discarded"}
    executor.security_executor_test_fake = True
    assert SecurityExecutorKernel(store, fake_executors={"crowdsec_temp_block": executor}, clock=lambda: clock[0]).execute(_request())["status"] == "success"
    return store


def _adapter(*, mode="good", entered=None, release=None, store=None):
    holder = {"calls": []}
    def invoke(operation):
        holder["calls"].append(operation)
        if entered is not None: entered.set()
        if release is not None: release.wait(timeout=2)
        if mode == "none": return None
        if mode == "false": return {"success": False, "raw": "provider response"}
        if mode == "raise": raise RuntimeError("raw provider exception secret")
        if mode == "race":
            action = store.get_action("act-rollback")
            store.transition(action_id="act-rollback", expected_version=action.version, target_state="failed", audit_ref="audit:sha256:" + "7" * 64, failure_ref="failure:sha256:" + "8" * 64)
        return issue_test_rollback_acknowledgement(holder["adapter"], operation, test_issuer=ROLLBACK_TEST_ONLY_ISSUER)
    holder["adapter"] = issue_test_rollback_adapter(invoke, test_issuer=ROLLBACK_TEST_ONLY_ISSUER)
    return holder


def test_sealed_exact_acknowledgement_is_required_before_rolled_back(tmp_path):
    clock = [100.0]
    for index, mode in enumerate(("none", "false", "raise")):
        folder = tmp_path / str(index); folder.mkdir(); store = _executed_store(folder, clock); adapter = _adapter(mode=mode)
        result = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0]).rollback(_request())
        assert result["reason"] == "rollback_acknowledgement_rejected" and store.get_action("act-rollback").state == "executed" and len(adapter["calls"]) == 1


def test_adapter_and_acknowledgement_are_unforgeable_and_nonserializable(tmp_path):
    clock = [100.0]; store = _executed_store(tmp_path, clock); adapter = _adapter()
    with pytest.raises(SecurityRollbackError): IssuedRollbackAdapter()
    with pytest.raises(SecurityRollbackError): RollbackAcknowledgement()
    with pytest.raises(SecurityRollbackError): SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": object()})
    with pytest.raises(SecurityRollbackError): copy.copy(adapter["adapter"])
    with pytest.raises(SecurityRollbackError): adapter["adapter"].__reduce_ex__(4)


def test_action_specific_rollback_is_idempotent_across_manager_restart(tmp_path):
    clock = [100.0]; store = _executed_store(tmp_path, clock); adapter = _adapter()
    first = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0]).rollback(_request())
    replay = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0]).rollback(_request())
    assert first["status"] == replay["status"] == "success" and first["idempotent_replay"] is False and replay["idempotent_replay"] is True and len(adapter["calls"]) == 1


def test_two_managers_in_process_invoke_exact_descriptor_once(tmp_path):
    clock = [100.0]; store = _executed_store(tmp_path, clock); entered = threading.Event(); release = threading.Event(); adapter = _adapter(entered=entered, release=release)
    first = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0]); second = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0])
    results = []
    worker = threading.Thread(target=lambda: results.append(first.rollback(_request()))); worker.start(); assert entered.wait(timeout=2)
    follower = threading.Thread(target=lambda: results.append(second.rollback(_request()))); follower.start(); release.set(); worker.join(timeout=3); follower.join(timeout=3)
    assert len(adapter["calls"]) == 1 and sorted(item["idempotent_replay"] for item in results) == [False, True] and store.get_action("act-rollback").state == "rolled_back"


def test_preapproved_expiry_rollback_is_due_only_and_evidence_chain_is_bound(tmp_path):
    clock = [100.0]; store = _executed_store(tmp_path, clock); adapter = _adapter(); manager = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0])
    assert manager.expire(_request())["reason"] == "expiry_not_due"; clock[0] = 150.0
    result = manager.expire(_request()); assert result["status"] == "success" and manager.expire(_request())["idempotent_replay"] is True
    snapshot = SecurityIncidentAuditChain.snapshot(store, action_id="act-rollback")
    executed = next(item for item in snapshot.entries if item.event_type == "action_executed"); rolled = next(item for item in snapshot.entries if item.event_type == "action_rolled_back")
    assert executed.receipt_ref and rolled.rollback_ref == result["rollback_ref"] and not rolled.receipt_ref


def test_concurrent_durable_write_and_tampered_audit_never_claim_rollback(tmp_path):
    clock = [100.0]; store = _executed_store(tmp_path, clock); racing = _adapter(mode="race", store=store)
    result = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": racing["adapter"]}, clock=lambda: clock[0]).rollback(_request())
    assert result["reason"] == "concurrent_rollback_conflict" and store.get_action("act-rollback").state == "failed"
    other = tmp_path / "other"; other.mkdir(); store2 = _executed_store(other, clock); good = _adapter()
    SecurityRollbackManager(store2, fake_adapters={"crowdsec_temp_block": good["adapter"]}, clock=lambda: clock[0]).rollback(_request())
    class TamperedStore:
        def __getattr__(self, name): return getattr(store2, name)
        def audit_events(self, action_id=None): return tuple(replace(event, reference="audit:sha256:" + "f" * 64) if event.event_type == "action_rolled_back" else event for event in store2.audit_events(action_id))
    replay = SecurityRollbackManager(TamperedStore(), fake_adapters={"crowdsec_temp_block": good["adapter"]}, clock=lambda: clock[0]).rollback(_request())
    assert replay["reason"] == "durable_rollback_mismatch" and len(good["calls"]) == 1


def test_rollback_rejects_request_binding_tamper_before_adapter(tmp_path):
    clock = [100.0]; store = _executed_store(tmp_path, clock); adapter = _adapter()
    result = SecurityRollbackManager(store, fake_adapters={"crowdsec_temp_block": adapter["adapter"]}, clock=lambda: clock[0]).rollback(_request(timeout_seconds=31))
    assert result["reason"] == "durable_receipt_mismatch" and adapter["calls"] == []
