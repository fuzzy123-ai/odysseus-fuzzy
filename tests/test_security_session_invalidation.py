import dataclasses
import copy
import json
import pickle
import threading

import pytest

from core.auth import AuthManager
import src.security_session_contracts as session_contracts
from src.security_executor_contracts import SecurityExecutionRequest, build_rollback_descriptor
from src.security_executors.session_invalidation import SessionInvalidationAcknowledgement, SessionInvalidationExecutor
from src.security_session_contracts import (
    IsolatedSessionStore, IsolatedSessionTestAuthManager, MAX_ACCOUNT_SESSION_SET,
    OperatorSessionProtection, SESSION_INVALIDATION_ACTION_TYPE, SESSION_INVALIDATION_GATE,
    SecuritySessionContractError, SessionInvalidationExecutionAuthority, SessionInvalidationScope,
    SESSION_INVALIDATION_TEST_ONLY_ISSUER, issue_test_session_invalidation_authority,
)


def _trusted_session(manager, username):
    fence = manager.capture_login_identity(username)
    assert fence is not None
    return manager.create_session_trusted(username, identity_fence=fence)


def _manager(tmp_path):
    manager = IsolatedSessionTestAuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("operator", "safe-password", is_admin=True)
    assert manager.create_user("target", "safe-password")
    operator = _trusted_session(manager, "operator")
    target_one = _trusted_session(manager, "target")
    target_two = _trusted_session(manager, "target")
    assert operator and target_one and target_two
    return manager, operator, target_one, target_two


def _request(scope, **overrides):
    values = {
        "action_id": "act-session", "action_version": 3,
        "action_type": SESSION_INVALIDATION_ACTION_TYPE, "scope_fingerprint": scope.scope_fingerprint,
        "policy_revision": "policy:sha256:" + "d" * 64, "policy_gate": SESSION_INVALIDATION_GATE,
        "timeout_seconds": 30, "idempotency_key": "idem-session", "expires_at": 150.0,
    }
    values.update(overrides)
    provisional = SecurityExecutionRequest(rollback_descriptor="rollback:sha256:" + "0" * 64, **values)
    return SecurityExecutionRequest(rollback_descriptor=build_rollback_descriptor(provisional), **values)


def _protection(operator):
    return OperatorSessionProtection.current_session(account_id="operator", session_handle=operator)


def _isolated_executor(manager, *, store=None, enabled=True):
    return SessionInvalidationExecutor(store or IsolatedSessionStore.from_isolated_test_manager(manager), enabled=enabled)


def _authority(request, scope, store):
    return issue_test_session_invalidation_authority(request, scope, store, test_issuer=SESSION_INVALIDATION_TEST_ONLY_ISSUER)


def test_single_session_is_exact_and_acknowledgement_is_not_independent_verification(tmp_path):
    manager, operator, target_one, target_two = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope = SessionInvalidationScope.single_session(account_id="target", session_handle=target_one)
    request = _request(scope)
    executor = _isolated_executor(manager, store=store)

    readiness = executor.readiness(request=request, scope=scope, operator_protection=_protection(operator))
    assert readiness["status"] == "ready" and readiness["target_count"] == 1 and readiness["mutation_performed"] is False
    acknowledgement = executor.execute(request=request, scope=scope, operator_protection=_protection(operator), capability=_authority(request, scope, store))

    assert acknowledgement["status"] == "success" and acknowledgement["invalidated_count"] == 1
    assert acknowledgement["acknowledgement_received"] is True and acknowledgement["verification_state"] == "not_verified"
    assert not manager.validate_token(target_one) and manager.validate_token(target_two) and manager.validate_token(operator)
    assert executor.readback(receipt=dict(acknowledgement), scope=scope, operator_protection=_protection(operator))["status"] == "blocked"


def test_account_set_is_bounded_exact_account_and_readback_is_bound_to_the_durable_receipt(tmp_path):
    manager, operator, target_one, target_two = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope = SessionInvalidationScope.account_session_set(account_id="target", max_sessions=2)
    request = _request(scope)
    executor = _isolated_executor(manager, store=store)
    acknowledgement = executor.execute(request=request, scope=scope, operator_protection=_protection(operator), capability=_authority(request, scope, store))

    readback = executor.readback(receipt=acknowledgement, scope=scope, operator_protection=_protection(operator))
    assert acknowledgement["verification_state"] == "not_verified" and readback["verified"] is True
    assert not manager.validate_token(target_one) and not manager.validate_token(target_two) and manager.validate_token(operator)


@pytest.mark.parametrize("scope", [
    lambda operator: SessionInvalidationScope.single_session(account_id="operator", session_handle=operator),
    lambda operator: SessionInvalidationScope.account_session_set(account_id="operator", max_sessions=2),
])
def test_operator_protection_rejects_before_any_store_mutation(tmp_path, scope):
    manager, operator, target_one, target_two = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    target_scope = scope(operator)
    request = _request(target_scope)
    result = _isolated_executor(manager, store=store).execute(request=request, scope=target_scope, operator_protection=_protection(operator), capability=_authority(request, target_scope, store))

    assert result["status"] == "blocked"
    assert manager.validate_token(operator) and manager.validate_token(target_one) and manager.validate_token(target_two)


def test_authority_is_issuer_and_adapter_identity_bound_single_use_and_not_request_reconstructable(tmp_path):
    manager, operator, target_one, target_two = _manager(tmp_path)
    primary_store = IsolatedSessionStore.from_isolated_test_manager(manager)
    other_store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope, request = SessionInvalidationScope.single_session(account_id="target", session_handle=target_one), None
    request = _request(scope)
    authority = _authority(request, scope, primary_store)

    assert not hasattr(SessionInvalidationExecutionAuthority, "for_execution_request")
    assert not hasattr(session_contracts, "_AUTHORITY_CONSTRUCTION_SEAL")
    class MaliciousIssuer:
        def _consume_authority(self, *_args):
            raise AssertionError("must never be called")

    with pytest.raises(SecuritySessionContractError):
        SessionInvalidationExecutionAuthority(MaliciousIssuer())
    with pytest.raises(SecuritySessionContractError):
        issue_test_session_invalidation_authority(request, scope, primary_store, test_issuer=MaliciousIssuer())
    forged_authority = object.__new__(SessionInvalidationExecutionAuthority)
    assert _isolated_executor(manager, store=primary_store).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=forged_authority)["status"] == "blocked"
    assert manager.validate_token(target_one) and manager.validate_token(target_two)
    with pytest.raises(SecuritySessionContractError):
        copy.copy(authority)
    with pytest.raises(SecuritySessionContractError):
        pickle.dumps(authority)
    copied_fields = {"request_binding": "a" * 64, "policy_gate": SESSION_INVALIDATION_GATE}
    assert _isolated_executor(manager, store=other_store).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=authority)["status"] == "blocked"
    assert manager.validate_token(target_one) and manager.validate_token(target_two)
    assert _isolated_executor(manager, store=primary_store).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=authority)["status"] == "success"
    assert _isolated_executor(manager, store=primary_store).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=copied_fields)["status"] == "blocked"
    assert not manager.validate_token(target_one) and manager.validate_token(target_two)


def test_live_or_general_auth_manager_cannot_be_mislabeled_as_an_isolated_store(tmp_path):
    manager = AuthManager(str(tmp_path / "general-auth.json"))
    manager.isolated_test_store = True
    manager._IsolatedSessionTestAuthManager__session_invalidation_test_store_marker = object()
    assert manager.create_user("operator", "safe-password", is_admin=True)
    assert manager.create_user("target", "safe-password")
    operator, target = _trusted_session(manager, "operator"), _trusted_session(manager, "target")
    assert operator and target
    scope, request = SessionInvalidationScope.single_session(account_id="target", session_handle=target), None
    request = _request(scope)
    with pytest.raises(SecuritySessionContractError):
        IsolatedSessionStore.from_isolated_test_manager(manager)
    result = SessionInvalidationExecutor(manager, enabled=True).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=None)
    assert result["status"] == "blocked" and manager.validate_token(target) and manager.validate_token(operator)


def test_fabricated_or_cross_action_version_scope_receipts_fail_readback(tmp_path):
    manager, operator, target_one, target_two = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope = SessionInvalidationScope.single_session(account_id="target", session_handle=target_one)
    request = _request(scope)
    executor = _isolated_executor(manager, store=store)
    acknowledgement = executor.execute(request=request, scope=scope, operator_protection=_protection(operator), capability=_authority(request, scope, store))
    durable = acknowledgement._receipt_for_readback()
    other_scope = SessionInvalidationScope.single_session(account_id="target", session_handle=target_two)
    forged = [
        dataclasses.replace(durable, action_id="act-other"),
        dataclasses.replace(durable, action_version=4),
        dataclasses.replace(durable, request_binding="a" * 64),
        dataclasses.replace(durable, scope_fingerprint=other_scope.scope_fingerprint),
        dataclasses.replace(durable, invalidated_count=2),
        copy.copy(durable),
        copy.deepcopy(durable),
        pickle.loads(pickle.dumps(durable)),
    ]
    for fake in forged:
        assert executor.readback(receipt=SessionInvalidationAcknowledgement(fake), scope=scope, operator_protection=_protection(operator))["status"] == "blocked"
    assert executor.readback(receipt=SessionInvalidationAcknowledgement(durable), scope=scope, operator_protection=_protection(operator))["status"] == "blocked"
    assert executor.readback(receipt=copy.copy(acknowledgement), scope=scope, operator_protection=_protection(operator))["status"] == "blocked"
    assert executor.readback(receipt=acknowledgement, scope=other_scope, operator_protection=_protection(operator))["status"] == "blocked"
    assert executor.readback(receipt=acknowledgement, scope=scope, operator_protection=_protection(operator))["verified"] is True


def test_default_disabled_bounds_recovery_and_store_exceptions_fail_closed_without_raw_values(tmp_path):
    manager, operator, target_one, _target_two = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope, request = SessionInvalidationScope.single_session(account_id="target", session_handle=target_one), None
    request = _request(scope)
    assert _isolated_executor(manager, store=store, enabled=False).readiness(request=request, scope=scope, operator_protection=_protection(operator))["status"] == "blocked"
    assert _isolated_executor(manager, store=store).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=_authority(request, scope, store), recovery={"untyped": "no"})["status"] == "blocked"
    with pytest.raises(SecuritySessionContractError):
        SessionInvalidationScope.account_session_set(account_id="target", max_sessions=MAX_ACCOUNT_SESSION_SET + 1)

    class BrokenSessions(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("provider response contains token=very-secret")

    manager._sessions = BrokenSessions(manager._sessions)
    result = _isolated_executor(manager, store=store).execute(request=request, scope=scope, operator_protection=_protection(operator), capability=_authority(request, scope, store))
    encoded = json.dumps(result, sort_keys=True)
    assert result == {"status": "blocked", "reason": "session_invalidation_unavailable", "executed": False, "verified": False, "raw_content_visible": False}
    assert target_one not in encoded and operator not in encoded and "very-secret" not in encoded and "provider response" not in encoded


def test_executor_waits_for_isolated_manager_guard_then_persists_durable_invalidation(tmp_path):
    manager, operator, target, _other = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope, request = SessionInvalidationScope.single_session(account_id="target", session_handle=target), None
    request = _request(scope)
    entered, release, executor_attempted, finished = (threading.Event() for _ in range(4))
    holder_thread = []
    real_authority = manager._session_authority_lock

    class ObservedAuthority:
        def __enter__(self):
            if threading.current_thread() is not holder_thread[0]:
                executor_attempted.set()
            real_authority.acquire()
            return self

        def __exit__(self, *args):
            real_authority.release()

    manager._session_authority_lock = ObservedAuthority()
    errors, result = [], []
    def guarded_work():
        try:
            with manager.guard_current_session(operator, manager.subject_id_for_username("operator")):
                entered.set()
                assert release.wait(3)
        except Exception as exc:
            errors.append(type(exc))

    def invalidate():
        try:
            assert entered.wait(3)
            result.append(_isolated_executor(manager, store=store).execute(
                request=request, scope=scope, operator_protection=_protection(operator),
                capability=_authority(request, scope, store),
            ))
            finished.set()
        except Exception as exc:
            errors.append(type(exc))

    holder, worker = threading.Thread(target=guarded_work), threading.Thread(target=invalidate)
    holder_thread.append(holder)
    started_threads = []
    try:
        holder.start()
        started_threads.append(holder)
        worker.start()
        started_threads.append(worker)
        assert entered.wait(3) and executor_attempted.wait(3)
        # The executor cannot have reached its session mutation while its
        # real authority acquisition remains blocked by the browser guard.
        assert not finished.is_set() and target in manager._sessions
        release.set()
    finally:
        release.set()
        for thread in started_threads:
            thread.join(5)
        manager._session_authority_lock = real_authority
    assert not holder.is_alive() and not worker.is_alive() and errors == []
    assert result[0]["status"] == "success" and manager.validate_token(target) is False
    reopened = IsolatedSessionTestAuthManager(str(tmp_path / "auth.json"))
    assert reopened.validate_token(target) is False


def test_failed_invalidator_save_consumes_authority_without_receipt_or_session_revival(tmp_path, monkeypatch):
    manager, operator, target, _other = _manager(tmp_path)
    store = IsolatedSessionStore.from_isolated_test_manager(manager)
    scope = SessionInvalidationScope.single_session(account_id="target", session_handle=target)
    request = _request(scope)
    capability = _authority(request, scope, store)
    monkeypatch.setattr(manager, "_save_sessions", lambda: False)
    result = _isolated_executor(manager, store=store).execute(
        request=request, scope=scope, operator_protection=_protection(operator), capability=capability,
    )
    assert result == {"status": "blocked", "reason": "session_invalidation_unavailable", "executed": False, "verified": False, "raw_content_visible": False}
    assert manager.validate_token(target) is False
    # The sealed capability was consumed before the failing durable write.
    repeat = _isolated_executor(manager, store=store).execute(
        request=request, scope=scope, operator_protection=_protection(operator), capability=capability,
    )
    assert repeat["status"] == "blocked"
