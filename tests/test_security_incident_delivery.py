from dataclasses import replace
import concurrent.futures

import pytest

from src.security_incident_delivery import (
    MAX_DRY_RUN_RETRIES, SecurityIncidentDeliveryAdapter, SecurityIncidentDeliveryRequest,
    TrustedTelegramDeliveryReadiness, delivery_idempotency_key, issue_test_delivery_transport,
    record_dry_run_delivery,
)
from src.security_incident_notifications import build_incident_delivery_preview
from src.security_incident_store import SecurityIncidentStore


def _ref(kind, char): return f"{kind}:sha256:{char * 64}"


def _probe(**changes):
    values = {
        "schema_id": "odysseus.homeserver.redacted_runtime_probe.v1", "status": "ok",
        "container": "odysseus_odysseus_1", "container_running": True,
        "environment_entry_count": 14,
        "credential_presence": {key: False for key in {"DATA_BRAVE_API_KEY", "EMBEDDING_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "NEXTCLOUD_WEBDAV_APP_PASSWORD", "ODYSSEUS_ADMIN_PASSWORD", "ODYSSEUS_INTERNAL_TOKEN", "OPENAI_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "TELEGRAM_BOT_TOKEN"}},
        "unknown_sensitive_key_count": 0, "raw_environment_visible": False, "secret_values_visible": False,
        "telegram_delivery_readiness": {"opaque_target_configured": True, "agent_reply_enabled": True, "send_ready": True, "raw_target_visible": False, "secret_values_visible": False},
    }
    values.update(changes)
    return values


def _trusted(): return TrustedTelegramDeliveryReadiness.from_redacted_probe(_probe())


def _request(**changes):
    values = {
        "schema": "odysseus.security_incident_delivery_request.v1", "incident_id": "inc-delivery",
        "action_id": "act-delivery", "action_version": 3, "scope_fingerprint": _ref("scope", "c"),
        "policy_revision": _ref("policy", "d"), "body_ref": _ref("body", "e"),
        "approved_target_class_ref": _ref("target_class", "f"), "channel": "telegram",
        "grant_expires_at": 160.0, "timeout_seconds": 30, "telegram_delivery_readiness": _trusted(),
        "policy_gate": "OPS-ALERT-DELIVERY-GO",
    }
    values.update(changes)
    return values


def _fake(effect):
    effect.security_incident_delivery_test_fake = True
    return issue_test_delivery_transport(effect)


def _approved_store(tmp_path, clock, request, name="delivery.sqlite"):
    store = SecurityIncidentStore(tmp_path / name, clock=lambda: clock[0])
    store.create_incident(incident_id=request["incident_id"], incident_ref=_ref("incident", "a"), audit_ref=_ref("audit", "b"))
    action = store.create_action(action_id=request["action_id"], incident_id=request["incident_id"], action_type="operator_notification", scope_fingerprint=request["scope_fingerprint"], policy_revision=request["policy_revision"], idempotency_key=delivery_idempotency_key(request), ttl_seconds=60, audit_ref=_ref("audit", "c"))
    prepared = store.transition(action_id=action.action_id, expected_version=action.version, target_state="prepared", audit_ref=_ref("audit", "d"))
    approved = store.approve(action_id=prepared.action_id, expected_version=prepared.version, approval_id="approval-delivery", approval_ref=_ref("approval", "e"), scope_fingerprint=prepared.scope_fingerprint, policy_revision=prepared.policy_revision, audit_ref=_ref("audit", "f"))
    assert approved.version == request["action_version"]
    return store


def test_dry_run_compatibility_is_no_send_and_bounded():
    assert record_dry_run_delivery(action_id="action-one").delivery_performed is False
    assert build_incident_delivery_preview(action_id="action-one")["outcome"] == "blocked_no_send"
    with pytest.raises(ValueError): record_dry_run_delivery(action_id="action-one", retry_count=MAX_DRY_RUN_RETRIES + 1)


@pytest.mark.parametrize("mutator", [
    lambda value: value.pop("container"), lambda value: value.__setitem__("status", "blocked"),
    lambda value: value.__setitem__("container", "C:\\unsafe"), lambda value: value.__setitem__("container", "192.0.2.1"), lambda value: value.__setitem__("container", "127.0.0.1"), lambda value: value.__setitem__("environment_entry_count", True),
    lambda value: value["credential_presence"].pop("OPENAI_API_KEY"), lambda value: value["credential_presence"].__setitem__("OPENAI_API_KEY", "yes"),
    lambda value: value.__setitem__("unknown_sensitive_key_count", 4097), lambda value: value["telegram_delivery_readiness"].__setitem__("send_ready", False),
    lambda value: (value["credential_presence"].update({key: True for key in value["credential_presence"]}) or value.__setitem__("unknown_sensitive_key_count", 1)),
    lambda value: value.__setitem__("unexpected", False),
])
def test_trusted_readiness_rejects_incomplete_or_forged_ok_probe(mutator):
    probe = _probe(); mutator(probe)
    with pytest.raises(ValueError): TrustedTelegramDeliveryReadiness.from_redacted_probe(probe)


def test_caller_supplied_readiness_mapping_and_free_callable_transport_never_dispatch(tmp_path):
    clock, calls, request = [100.0], [], _request()
    store = _approved_store(tmp_path, clock, request)
    untrusted = _request(telegram_delivery_readiness=_probe()["telegram_delivery_readiness"])
    def arbitrary(_value): calls.append(True); return {"status": "acknowledged", "receipt_ref": _ref("receipt", "a")}
    assert SecurityIncidentDeliveryAdapter(store, transport=arbitrary, clock=lambda: clock[0]).attempt(request)["reason"] == "delivery_transport_unavailable"
    assert SecurityIncidentDeliveryAdapter(store, transport=_fake(arbitrary), clock=lambda: clock[0]).attempt(untrusted)["reason"] == "invalid_delivery_request"
    assert calls == [] and store.get_action(request["action_id"]).state == "approved"


def test_exactly_once_bound_delivery_target_class_and_independent_readback(tmp_path):
    clock, calls, request = [100.0], [], _request()
    store = _approved_store(tmp_path, clock, request)
    def transport(value):
        calls.append(value)
        assert set(value) == {"schema", "action_id", "action_version", "body_ref", "channel", "approved_target_class_ref", "timeout_seconds", "raw_content_visible"}
        return {"status": "acknowledged", "receipt_ref": _ref("receipt", "a")}
    adapter = SecurityIncidentDeliveryAdapter(store, transport=_fake(transport), clock=lambda: clock[0])
    assert adapter.attempt(request)["status"] == "acknowledged"
    assert adapter.attempt(request)["status"] == "blocked" and len(calls) == 1
    assert SecurityIncidentDeliveryAdapter(store).readback(request)["status"] == "acknowledged"


@pytest.mark.parametrize("changes", [{"approved_target_class_ref": _ref("target_class", "a")}, {"body_ref": _ref("body", "b")}, {"timeout_seconds": 61}, {"grant_expires_at": 159.0}, {"telegram_delivery_readiness": _probe()["telegram_delivery_readiness"]}])
def test_bound_substitution_rejects_before_transport(tmp_path, changes):
    clock, calls, request = [100.0], [], _request(); store = _approved_store(tmp_path, clock, request)
    def transport(_value): calls.append(True); return {"status": "acknowledged", "receipt_ref": _ref("receipt", "b")}
    assert SecurityIncidentDeliveryAdapter(store, transport=_fake(transport), clock=lambda: clock[0]).attempt(_request(**changes))["status"] == "blocked"
    assert calls == [] and store.get_action(request["action_id"]).state == "approved"


def test_concurrent_adapters_consume_one_durable_approval(tmp_path):
    clock, calls, request = [100.0], [], _request(); store = _approved_store(tmp_path, clock, request)
    def transport(_value): calls.append(True); return {"status": "acknowledged", "receipt_ref": _ref("receipt", "c")}
    wrapped = _fake(transport)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: SecurityIncidentDeliveryAdapter(store, transport=wrapped, clock=lambda: clock[0]).attempt(request), range(2)))
    assert [value["status"] for value in results].count("acknowledged") == 1 and len(calls) == 1


def test_transport_exception_or_malformed_response_is_durable_unknown_no_retry(tmp_path):
    clock, request = [100.0], _request()
    for index, effect in enumerate((lambda _value: (_ for _ in ()).throw(RuntimeError("raw failure")), lambda _value: {"status": "acknowledged", "receipt_ref": _ref("receipt", "d"), "raw": "x"})):
        store = _approved_store(tmp_path, clock, request, f"unknown-{index}.sqlite")
        result = SecurityIncidentDeliveryAdapter(store, transport=_fake(effect), clock=lambda: clock[0]).attempt(request)
        assert result["status"] == "unknown" and result["delivery_performed"] is None and store.get_action(request["action_id"]).state == "failed"
        assert SecurityIncidentDeliveryAdapter(store).readback(request)["status"] == "unknown"


def test_elapsed_timeout_and_receipt_persist_failure_are_unknown(tmp_path):
    clock, request = [100.0], _request(); store = _approved_store(tmp_path, clock, request)
    def slow(_value): clock[0] = 131.0; return {"status": "acknowledged", "receipt_ref": _ref("receipt", "e")}
    result = SecurityIncidentDeliveryAdapter(store, transport=_fake(slow), clock=lambda: clock[0]).attempt(request)
    assert result["status"] == "unknown" and result["delivery_performed"] is None and SecurityIncidentDeliveryAdapter(store).readback(request)["status"] == "unknown"
    receipt_store = _approved_store(tmp_path, [100.0], request, "receipt-persist.sqlite")
    class ReceiptPersistenceFailure:
        def transition(self, **kwargs):
            if kwargs.get("target_state") == "executed": raise RuntimeError("not forwarded")
            return receipt_store.transition(**kwargs)
        def get_action(self, action_id): return receipt_store.get_action(action_id)
        def get_approval(self, action_id): return receipt_store.get_approval(action_id)
        def audit_events(self, action_id): return receipt_store.audit_events(action_id)
    result = SecurityIncidentDeliveryAdapter(ReceiptPersistenceFailure(), transport=_fake(lambda _value: {"status": "acknowledged", "receipt_ref": _ref("receipt", "g")}), clock=lambda: 100.0).attempt(request)
    assert result["status"] == "unknown" and result["delivery_performed"] is None
    assert SecurityIncidentDeliveryAdapter(receipt_store).readback(request)["status"] == "unknown"


def test_invalid_clock_and_exact_readback_guards_fail_closed(tmp_path):
    clock, calls, request = [100.0], [], _request(); store = _approved_store(tmp_path, clock, request)
    def effect(_value): calls.append(True)
    assert SecurityIncidentDeliveryAdapter(store, transport=_fake(effect), clock=lambda: float("nan")).attempt(request)["reason"] == "delivery_clock_unavailable"
    assert calls == [] and store.get_approval(request["action_id"]).consumed_at is None
    def good(_value): return {"status": "acknowledged", "receipt_ref": _ref("receipt", "f")}
    assert SecurityIncidentDeliveryAdapter(store, transport=_fake(good), clock=lambda: clock[0]).attempt(request)["status"] == "acknowledged"
    class ForgedReadback:
        def get_action(self, action_id): return replace(store.get_action(action_id), version=4)
        def get_approval(self, action_id): return replace(store.get_approval(action_id), consumed_at=None)
        def audit_events(self, action_id): return store.audit_events(action_id)
    assert SecurityIncidentDeliveryAdapter(ForgedReadback()).readback(request)["status"] == "blocked"
