import pytest
import json
import threading
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security_action_authorization import SecurityActionAuthorization
from src.security_incident_commands import SecurityIncidentCommandError, SecurityIncidentCommands
from src.security_incident_store import SecurityIncidentStore
from routes.auth_routes import SESSION_COOKIE, setup_auth_routes
import routes.auth_routes as auth_routes


def _contains_exact(value, needle):
    if isinstance(value, dict): return any(_contains_exact(item, needle) for item in value.values())
    if isinstance(value, list): return any(_contains_exact(item, needle) for item in value)
    return value == needle


def _ref(kind, value="a"):
    return f"{kind}:sha256:{value * 64}"


class _Auth:
    def __init__(self): self.admin = True; self.live = True; self.valid = True; self.principal = "admin"; self.revision = 0
    def validate_token(self, token): return self.valid and token == "session"
    def get_username_for_token(self, token): return self.principal if self.valid and token == "session" else None
    def is_admin(self, username): return self.admin and username == "admin"
    def verify_password(self, username, password): return username == "admin" and password == "correct"
    def totp_verify_live(self, username, code): return self.live and username == "admin" and code == "123456"
    def totp_verify(self, username, code): return self.live and username == "admin" and code == "123456"
    def totp_enabled(self, username): return self.live and username == "admin"
    def security_action_revision(self): return self.revision
    def create_session_trusted(self, username): return "session" if username == "admin" else None
    signup_enabled = False
    def status(self, token): return {"authenticated": self.valid and token == "session", "username": self.principal if self.valid and token == "session" else None, "is_admin": self.admin}
    def get_privileges(self, username): return {}
    def revoke_token(self, token): self.valid = False


def _prepared_store(tmp_path):
    store = SecurityIncidentStore(tmp_path / "incident.sqlite3", clock=lambda: 100.0)
    store.create_incident(incident_id="inc-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    action = store.create_action(
        action_id="action-one", incident_id="inc-one", action_type="operator_notification",
        scope_fingerprint="scope:sha256:" + "b" * 64, policy_revision=_ref("policy"),
        idempotency_key="key-one", ttl_seconds=60, audit_ref=_ref("audit", "c"),
    )
    store.transition(action_id=action.action_id, expected_version=action.version, target_state="prepared", audit_ref=_ref("audit", "d"))
    return store


def test_approve_is_action_id_only_and_never_executes(tmp_path):
    auth = _Auth()
    gate = SecurityActionAuthorization(auth)
    action_store = _prepared_store(tmp_path)
    commands = SecurityIncidentCommands(action_store, gate)
    action = action_store.get_action("action-one")
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    result = commands.approve(action_id=action.action_id, session_token="session", username="admin")
    assert result["status"] == "approved" and result["executed"] is False and result["delivery_performed"] is False
    with pytest.raises(SecurityIncidentCommandError): commands.approve(action_id=action.action_id, session_token="session", username="admin")
def test_deny_and_manual_expire_require_fresh_step_up_and_fail_closed(tmp_path):
    store = _prepared_store(tmp_path)
    auth = _Auth(); gate = SecurityActionAuthorization(auth); commands = SecurityIncidentCommands(store, gate)
    action = store.get_action("action-one")
    with pytest.raises(SecurityIncidentCommandError): commands.deny(action_id="unknown-action", session_token="session", username="admin")
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    assert commands.deny(action_id="action-one", session_token="session", username="admin")["action_state"] == "denied"


def test_manual_expire_and_registered_routes_remain_action_id_only(tmp_path):
    expire_dir = tmp_path / "expire"; expire_dir.mkdir()
    store = _prepared_store(expire_dir)
    auth = _Auth(); gate = SecurityActionAuthorization(auth); commands = SecurityIncidentCommands(store, gate)
    action = store.get_action("action-one")
    gate.step_up(session_token="session", username="admin", password="correct", totp_code="123456", action=action, auth_kind="browser_cookie")
    assert commands.expire(action_id="action-one", session_token="session", username="admin")["action_state"] == "expired"
    paths = {route.path for route in setup_auth_routes(auth).routes}
    assert "/api/auth/security-actions/{action_id}/step-up" in paths
    assert "/api/auth/security-actions/{action_id}/approve" in paths
    assert "/api/auth/security-actions/{action_id}/deny" in paths
    assert "/api/auth/security-actions/{action_id}/expire" in paths


def _route_client(store, auth, *, factory=False):
    app = FastAPI()
    if factory:
        calls = []
        app.state.security_incident_store_factory = lambda: calls.append(True) or store
    else:
        calls = None
        app.state.security_incident_store = store
    app.include_router(setup_auth_routes(auth))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "session")
    return client, calls


def test_http_boundary_is_cookie_only_exact_json_and_never_echoes_credentials(tmp_path):
    store, auth = _prepared_store(tmp_path), _Auth()
    client, _ = _route_client(store, auth)
    url = "/api/auth/security-actions/action-one/step-up"
    bad_requests = [
        {"json": {"password": "correct", "totp_code": "123456", "extra": "free-form"}},
        {"json": {"password": "correct"}},
        {"data": "not-json"},
        {"json": {"password": "correct", "totp_code": "123456"}, "params": {"x": "1"}},
        {"json": {"password": "correct", "totp_code": "123456"}, "headers": {"Authorization": "Bearer opaque"}},
        {"json": {"password": "correct", "totp_code": "123456"}, "headers": {"X-Odysseus-Internal-Token": "opaque"}},
    ]
    for kwargs in bad_requests:
        response = client.post(url, **kwargs)
        assert response.status_code == 403
        assert "correct" not in response.text and "123456" not in response.text and "opaque" not in response.text
    auth.principal = "internal-tool"
    response = client.post(url, json={"password": "correct", "totp_code": "123456"})
    assert response.status_code == 403 and "correct" not in response.text
    auth.principal = "admin"; auth.admin = False
    assert client.post(url, json={"password": "correct", "totp_code": "123456"}).status_code == 403
    auth.admin = True
    assert client.post(url, json={"password": "correct", "totp_code": "123456"}).status_code == 200
    command = "/api/auth/security-actions/action-one/approve"
    assert client.post(command, json={}).status_code == 403
    assert client.post(command, data="credentials").status_code == 403
    assert client.post(command).status_code == 200


def test_http_store_factory_is_lazy_and_authenticated_before_default_fallback(tmp_path, monkeypatch):
    store, auth = _prepared_store(tmp_path), _Auth()
    app = FastAPI(); calls = []
    monkeypatch.setattr(auth_routes, "create_default_security_incident_store", lambda: calls.append(True) or store)
    app.include_router(setup_auth_routes(auth))
    assert calls == []
    client = TestClient(app); client.cookies.set(SESSION_COOKIE, "session")
    endpoint = "/api/auth/security-actions/action-one/step-up"
    for kwargs in (
        {"cookies": {}},
        {"headers": {"Authorization": "Bearer opaque"}, "json": {"password": "correct", "totp_code": "123456"}},
        {"params": {"x": "1"}, "json": {"password": "correct", "totp_code": "123456"}},
        {"data": "bad-json"},
        {"json": {"password": "wrong", "totp_code": "123456"}},
    ):
        response = client.post(endpoint, **kwargs)
        assert response.status_code == 403 and calls == []
    response = client.post("/api/auth/security-actions/action-one/step-up", json={"password": "correct", "totp_code": "123456"})
    assert response.status_code == 200 and calls == [True]


def test_auth_event_sink_is_canonical_redacted_and_step_up_sink_failure_fails_closed(tmp_path):
    store, auth = _prepared_store(tmp_path), _Auth(); events = []
    client, _ = _route_client(store, auth)
    client.app.state.security_auth_event_sink = events.append
    login = client.post("/api/auth/login", json={"username": "admin", "password": "correct", "totp_code": "123456"})
    assert login.status_code == 200
    failed = client.post("/api/auth/login", json={"username": "admin", "password": "wrong", "totp_code": "123456"})
    assert failed.status_code == 401
    assert not any(_contains_exact(events, value) for value in ("correct", "wrong", "123456", "session"))
    assert {event["source"] for event in events} == {"auth_outcome"}
    client.app.state.security_auth_event_sink = lambda _event: (_ for _ in ()).throw(RuntimeError())
    response = client.post("/api/auth/security-actions/action-one/step-up", json={"password": "correct", "totp_code": "123456"})
    assert response.status_code == 403


def test_step_up_sink_barrier_never_publishes_a_grant_before_sink_acceptance(tmp_path):
    store, auth = _prepared_store(tmp_path), _Auth(); client, _ = _route_client(store, auth)
    entered, release = threading.Event(), threading.Event()
    def sink(_event):
        entered.set(); release.wait(timeout=5); raise RuntimeError("sink rejected")
    client.app.state.security_auth_event_sink = sink
    endpoint = "/api/auth/security-actions/action-one/step-up"
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(client.post(endpoint, json={"password": "correct", "totp_code": "123456"}).status_code))
    worker.start(); assert entered.wait(timeout=5)
    assert client.post("/api/auth/security-actions/action-one/approve").status_code != 200
    release.set(); worker.join(timeout=5)
    assert outcome == [403]
    assert client.post("/api/auth/security-actions/action-one/approve").status_code != 200


def test_login_status_logout_and_rate_limit_emit_one_redacted_event_per_request(tmp_path):
    store, auth = _prepared_store(tmp_path), _Auth(); client, _ = _route_client(store, auth)
    events = []; client.app.state.security_auth_event_sink = events.append
    assert client.get("/api/auth/status").status_code == 200
    assert len(events) == 1
    assert client.post("/api/auth/logout").status_code == 200
    assert len(events) == 2
    for _ in range(15): client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    before = len(events)
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 429
    assert len(events) == before + 1
    client.app.state.security_auth_event_sink = None
    for _ in range(65): client.get("/api/auth/status")
    retained = client.app.state.security_auth_events
    assert len(retained) == 64 and all(event["source"] == "auth_outcome" for event in retained)
