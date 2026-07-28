import json
import threading
import time
from types import SimpleNamespace

import pytest
import pyotp

from src.security_action_authorization import (
    STEP_UP_TTL_SECONDS,
    SecurityActionAuthorization,
    SecurityActionAuthorizationError,
    build_redacted_auth_event,
)
from src.security_incident_store import SecurityIncidentStore
from core.auth import AuthManager
import core.auth as auth_core


def _ref(kind, value="a"):
    return f"{kind}:sha256:{value * 64}"


class _Auth:
    def __init__(self):
        self.admin = True
        self.live = True
        self.valid = True
        self.revision = 0

    def validate_token(self, token): return self.valid and str(token).startswith("session")
    def get_username_for_token(self, token): return "admin" if self.valid and str(token).startswith("session") else None
    def is_admin(self, username): return self.admin and username == "admin"
    def verify_password(self, username, password): return username == "admin" and password == "correct"
    def totp_verify_live(self, username, code): return self.live and username == "admin" and code == "123456"
    def totp_enabled(self, username): return self.live and username == "admin"
    def security_action_revision(self): return self.revision


def _prepared(tmp_path):
    store = SecurityIncidentStore(tmp_path / "incident.sqlite3", clock=lambda: 100.0)
    store.create_incident(incident_id="inc-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    action = store.create_action(
        action_id="action-one", incident_id="inc-one", action_type="operator_notification",
        scope_fingerprint="scope:sha256:" + "b" * 64, policy_revision=_ref("policy"),
        idempotency_key="key-one", ttl_seconds=60, audit_ref=_ref("audit", "c"),
    )
    return store.transition(action_id=action.action_id, expected_version=action.version, target_state="prepared", audit_ref=_ref("audit", "d"))


def test_live_totp_step_up_is_single_use_exactly_300_seconds_and_redacted(tmp_path):
    action = _prepared(tmp_path)
    clock = [1000.0]
    auth = _Auth()
    gate = SecurityActionAuthorization(auth, clock=lambda: clock[0])
    event = gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    assert event.envelope.source == "auth_outcome"
    assert "correct" not in json.dumps(event.envelope.to_dict())
    gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")
    with pytest.raises(SecurityActionAuthorizationError):
        gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    clock[0] += STEP_UP_TTL_SECONDS
    with pytest.raises(SecurityActionAuthorizationError):
        gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")


def test_step_up_rejects_non_admin_synthetic_api_and_missing_live_totp(tmp_path):
    action = _prepared(tmp_path)
    auth = _Auth()
    gate = SecurityActionAuthorization(auth)
    for kwargs in (
        {"auth_kind": "bearer"},
        {"username": "api"},
    ):
        values = {"session_token": "session", "username": "admin", "password": "correct", "totp_code": "123456", "action": action, "auth_kind": "browser_cookie"}
        values.update(kwargs)
        with pytest.raises(SecurityActionAuthorizationError): gate.step_up(**values)
    auth.admin = False
    with pytest.raises(SecurityActionAuthorizationError):
        gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    auth.admin = True
    auth.live = False
    with pytest.raises(SecurityActionAuthorizationError):
        gate.step_up(session_token="session", username="admin", password="correct", totp_code="backup-code", action=action, auth_kind="browser_cookie")


def test_redacted_auth_source_contract_uses_unknown_without_secret_fields():
    projection = build_redacted_auth_event(username="admin", outcome="unknown").envelope.to_dict()
    assert projection["source"] == "auth_outcome"
    assert projection["dimensions"]["outcome"] == "unknown"
    assert projection["raw_content_visible"] is False
    assert "password" not in json.dumps(projection).lower()


def test_revision_totp_and_session_loss_invalidate_grants(tmp_path):
    action = _prepared(tmp_path); auth = _Auth(); gate = SecurityActionAuthorization(auth)
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    auth.revision += 1  # demote/re-promote is a security-authority change.
    with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    auth.live = False
    with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")
    auth.live = True
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    auth.valid = False
    with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")


def test_clock_capacity_purge_and_concurrent_consumption_fail_closed(tmp_path):
    action = _prepared(tmp_path); auth = _Auth(); clock = [10.0]
    gate = SecurityActionAuthorization(auth, clock=lambda: clock[0], capacity=1)
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    other = SimpleNamespace(action_id="action-two", version=action.version, scope_fingerprint=action.scope_fingerprint, policy_revision=action.policy_revision)
    with pytest.raises(SecurityActionAuthorizationError): gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=other, auth_kind="browser_cookie")
    clock[0] += STEP_UP_TTL_SECONDS
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=other, auth_kind="browser_cookie")
    clock[0] -= 1
    with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=other, auth_kind="browser_cookie")
    for invalid in (float("nan"), float("inf"), -1.0):
        with pytest.raises(SecurityActionAuthorizationError): SecurityActionAuthorization(auth, clock=lambda value=invalid: value).step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    concurrent = SecurityActionAuthorization(auth)
    concurrent.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    results = []
    def consume():
        try: concurrent.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie"); results.append(True)
        except SecurityActionAuthorizationError: results.append(False)
    workers = [threading.Thread(target=consume) for _ in range(4)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert results.count(True) == 1 and results.count(False) == 3


def test_action_version_scope_and_policy_mismatches_cannot_consume(tmp_path):
    action = _prepared(tmp_path); auth = _Auth(); gate = SecurityActionAuthorization(auth)
    for field, value in (("version", action.version + 1), ("scope_fingerprint", "scope:sha256:" + "c" * 64), ("policy_revision", _ref("policy", "d"))):
        gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
        changed = SimpleNamespace(action_id=action.action_id, version=action.version, scope_fingerprint=action.scope_fingerprint, policy_revision=action.policy_revision)
        setattr(changed, field, value)
        with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=changed, auth_kind="browser_cookie")


def test_real_auth_manager_role_and_totp_changes_invalidate_existing_grants(tmp_path, monkeypatch):
    secret = pyotp.random_base32(); manager = AuthManager(str(tmp_path / "auth.json"))
    monkeypatch.setattr(auth_core, "_verify_password", lambda value, _hashed: value == "correct")
    manager._config = {"users": {
        "admin": {"password_hash": "hash", "is_admin": True, "totp_enabled": True, "totp_secret": secret, "privileges": {}},
        "other": {"password_hash": "hash", "is_admin": True, "totp_enabled": True, "totp_secret": secret, "privileges": {}},
    }}
    manager._sessions = {"session": {"username": "admin", "expiry": time.time() + 60}}
    action = _prepared(tmp_path); gate = SecurityActionAuthorization(manager)
    code = pyotp.TOTP(secret).now()
    gate.step_up(session_token="session", username="admin", password="correct", totp_code=code, action=action, auth_kind="browser_cookie")
    assert manager.set_admin("admin", False, "other").value == "ok"
    assert manager.set_admin("admin", True, "other").value == "ok"
    with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")
    gate.step_up(session_token="session", username="admin", password="correct", totp_code=code, action=action, auth_kind="browser_cookie")
    assert manager.totp_disable("admin", "correct") is True
    with pytest.raises(SecurityActionAuthorizationError): gate.consume(session_token="session", username="admin", action=action, auth_kind="browser_cookie")


def test_live_totp_verifier_rejects_backup_and_disabled_accounts(tmp_path):
    secret = pyotp.random_base32()
    manager = AuthManager(str(tmp_path / "auth.json"))
    manager._config = {"users": {"admin": {"totp_enabled": True, "totp_secret": secret, "totp_backup_codes": ["backup-code"]}}}
    assert manager.totp_verify_live("admin", pyotp.TOTP(secret).now()) is True
    assert manager.totp_verify_live("admin", "backup-code") is False
    manager._config["users"]["admin"]["totp_enabled"] = False
    assert manager.totp_verify_live("admin", pyotp.TOTP(secret).now()) is False
