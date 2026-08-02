import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.security_routes as security_routes
from routes.security_routes import setup_security_routes
import src.settings as settings_store
from src.security_incident_delivery import (
    SecurityIncidentDeliveryAdapter,
    TrustedTelegramDeliveryReadiness,
    build_server_owned_delivery_request,
    delivery_idempotency_key,
    issue_test_delivery_transport,
)
from src.security_incident_notifications import (
    canonical_operator_notification_body_ref,
    canonical_operator_notification_target_class_ref,
)
from src.security_incident_store import SecurityIncidentStore


def _app(
    *, is_admin: bool = True, user: str | None = None, store=None, owner_resolver=None,
    delivery_transport=None, delivery_readiness=None, delivery_timeout_seconds=None,
) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: bool(is_admin),
    )
    if store is not None:
        app.state.security_incident_store = store
    if owner_resolver is not None:
        app.state.security_incident_owner_resolver = owner_resolver

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = user if user is not None else ("admin" if is_admin else "user")
        return await call_next(request)

    app.include_router(setup_security_routes(
        incident_store=store if store is not None else object(),
        incident_delivery_transport=delivery_transport,
        incident_delivery_readiness=delivery_readiness,
        incident_delivery_timeout_seconds=delivery_timeout_seconds,
    ))
    return app


def _incident_store(tmp_path, *, clock=lambda: 100.0):
    store = SecurityIncidentStore(tmp_path / "security.sqlite", clock=clock)
    store.create_incident(incident_id="inc-owner", incident_ref="evidence:sha256:" + "a" * 64, audit_ref="audit:sha256:" + "b" * 64)
    store.create_incident(incident_id="inc-other", incident_ref="evidence:sha256:" + "c" * 64, audit_ref="audit:sha256:" + "d" * 64)
    store.create_action(
        action_id="act-owner", incident_id="inc-owner", action_type="service_restart",
        scope_fingerprint="scope:sha256:" + "e" * 64, policy_revision="policy:sha256:" + "f" * 64,
        idempotency_key="idem-owner", ttl_seconds=60, audit_ref="audit:sha256:" + "1" * 64,
    )
    store.create_action(
        action_id="act-other", incident_id="inc-other", action_type="service_restart",
        scope_fingerprint="scope:sha256:" + "2" * 64, policy_revision="policy:sha256:" + "3" * 64,
        idempotency_key="idem-other", ttl_seconds=60, audit_ref="audit:sha256:" + "4" * 64,
    )
    return store


def _trusted_delivery_readiness():
    credential_keys = {
        "DATA_BRAVE_API_KEY", "EMBEDDING_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
        "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
        "NEXTCLOUD_WEBDAV_APP_PASSWORD", "ODYSSEUS_ADMIN_PASSWORD",
        "ODYSSEUS_INTERNAL_TOKEN", "OPENAI_API_KEY", "SERPER_API_KEY",
        "TAVILY_API_KEY", "TELEGRAM_BOT_TOKEN",
    }
    return TrustedTelegramDeliveryReadiness.from_redacted_probe({
        "schema_id": "odysseus.homeserver.redacted_runtime_probe.v1", "status": "ok",
        "container": "odysseus_odysseus_1", "container_running": True,
        "environment_entry_count": len(credential_keys),
        "credential_presence": {key: False for key in credential_keys},
        "unknown_sensitive_key_count": 0, "raw_environment_visible": False,
        "secret_values_visible": False,
        "telegram_delivery_readiness": {
            "opaque_target_configured": True, "agent_reply_enabled": True,
            "send_ready": True, "raw_target_visible": False,
            "secret_values_visible": False,
        },
    })


def _approved_delivery_store(tmp_path, *, clock=None):
    now = time.time() if clock is None else clock()
    clock = (lambda: now)
    store = SecurityIncidentStore(tmp_path / "delivery.sqlite", clock=clock)
    store.create_incident(
        incident_id="inc-delivery", incident_ref="evidence:sha256:" + "a" * 64,
        audit_ref="audit:sha256:" + "b" * 64,
    )
    readiness = _trusted_delivery_readiness()
    action_values = {
        "schema": "odysseus.security_incident_delivery_request.v1",
        "incident_id": "inc-delivery", "action_id": "act-delivery", "action_version": 3,
        "scope_fingerprint": "scope:sha256:" + "c" * 64,
        "policy_revision": "policy:sha256:" + "d" * 64,
        "body_ref": canonical_operator_notification_body_ref(),
        "approved_target_class_ref": canonical_operator_notification_target_class_ref(),
        "channel": "telegram", "grant_expires_at": now + 60.0, "timeout_seconds": 15,
        "telegram_delivery_readiness": readiness, "policy_gate": "OPS-ALERT-DELIVERY-GO",
    }
    store.create_action(
        action_id=action_values["action_id"], incident_id=action_values["incident_id"],
        action_type="operator_notification", scope_fingerprint=action_values["scope_fingerprint"],
        policy_revision=action_values["policy_revision"],
        idempotency_key=delivery_idempotency_key(action_values), ttl_seconds=60,
        audit_ref="audit:sha256:" + "e" * 64,
    )
    prepared = store.transition(
        action_id="act-delivery", expected_version=1, target_state="prepared",
        audit_ref="audit:sha256:" + "f" * 64,
    )
    approved = store.approve(
        action_id="act-delivery", expected_version=prepared.version,
        approval_id="approval-delivery", approval_ref="approval:sha256:" + "1" * 64,
        scope_fingerprint=prepared.scope_fingerprint, policy_revision=prepared.policy_revision,
        audit_ref="audit:sha256:" + "2" * 64,
    )
    assert approved.version == 3
    return store, readiness


def test_dsgvo_status_is_browser_safe_and_redacted(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_store._invalidate_caches()
    settings_store.save_settings({**settings_store.DEFAULT_SETTINGS, "dsgvo_mode": True, "brave_api_key": "sk-secret"})

    response = TestClient(_app()).get("/api/security/dsgvo/status")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["dsgvo_mode"] is True
    assert payload["local_only_required"] is True
    assert payload["effective_security_mode"] == "secure"
    assert payload["required_provider_scope"] == "local_only"
    assert payload["external_io_allowed"] is False
    assert payload["settings_values_visible"] is False
    assert "sk-secret" not in encoded


def test_dsgvo_toggle_requires_admin_and_updates_global_setting(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_store._invalidate_caches()
    settings_store.save_settings({**settings_store.DEFAULT_SETTINGS, "dsgvo_mode": False})

    denied = TestClient(_app(is_admin=False)).post("/api/security/dsgvo/toggle")
    assert denied.status_code == 403

    response = TestClient(_app(is_admin=True)).post("/api/security/dsgvo/toggle")
    payload = response.json()

    assert response.status_code == 200
    assert payload["requested"] is True
    assert payload["before"] is False
    assert payload["changed"] is True
    assert payload["dsgvo_mode"] is True
    assert settings_store.load_settings()["dsgvo_mode"] is True


def test_dsgvo_disable_reports_forced_active_when_env_requires_it(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "true")
    settings_store._invalidate_caches()
    settings_store.save_settings({**settings_store.DEFAULT_SETTINGS, "dsgvo_mode": True})

    response = TestClient(_app(is_admin=True)).post("/api/security/dsgvo", json={"enabled": False})
    payload = response.json()

    assert response.status_code == 200
    assert payload["requested"] is False
    assert payload["dsgvo_mode"] is True
    assert payload["setting_enabled"] is False
    assert payload["forced_active"] is True
    assert payload["changed"] is False


def test_registered_security_routes_read_owner_scoped_persisted_incident(tmp_path):
    store = _incident_store(tmp_path)
    client = TestClient(_app(is_admin=False, user="alice", store=store, owner_resolver=lambda _request, incident_id: "alice" if incident_id == "inc-owner" else "bob"))

    allowed = client.get("/api/security/incidents/inc-owner")
    denied = client.get("/api/security/incidents/inc-other")
    encoded = json.dumps(allowed.json(), sort_keys=True)

    assert allowed.status_code == 200
    assert allowed.json()["incident"]["action_count"] == 1
    assert denied.status_code == 403
    assert "evidence:sha256:" not in encoded
    assert "scope:sha256:" not in encoded


def test_application_registers_existing_security_route_module_without_orphan_wiring():
    source = Path("app.py").read_text(encoding="utf-8")

    assert "from routes.security_routes import setup_security_routes" in source
    assert source.count("app.include_router(setup_security_routes())") == 1
    assert "from routes.ops_console_routes import setup_ops_console_routes" in source
    assert source.count("app.include_router(setup_ops_console_routes())") == 1


def test_security_action_prepare_is_incident_bound_versioned_and_no_execute(tmp_path):
    store = _incident_store(tmp_path)
    client = TestClient(_app(is_admin=True, store=store))

    wrong_incident = client.post("/api/security/incidents/inc-owner/actions/act-other/prepare", json={"expected_version": 1})
    prepared = client.post("/api/security/incidents/inc-owner/actions/act-owner/prepare", json={"expected_version": 1})
    stale = client.post("/api/security/incidents/inc-owner/actions/act-owner/prepare", json={"expected_version": 1})

    assert wrong_incident.status_code == 404
    assert prepared.status_code == 200
    assert prepared.json()["action"]["state"] == "prepared"
    assert prepared.json()["approved"] is False and prepared.json()["executed"] is False
    assert stale.status_code == 409
    assert not any(getattr(route, "path", "").endswith("/execute") for route in client.app.routes)


def test_security_action_prepare_unknown_and_expired_fail_closed(tmp_path):
    now = [100.0]
    store = _incident_store(tmp_path, clock=lambda: now[0])
    client = TestClient(_app(is_admin=True, store=store))

    unknown = client.post("/api/security/incidents/inc-owner/actions/act-missing/prepare", json={"expected_version": 1})
    now[0] = 200.0
    expired = client.post("/api/security/incidents/inc-owner/actions/act-owner/prepare", json={"expected_version": 1})

    assert unknown.status_code == 404
    assert expired.status_code == 409


def test_security_prepare_forbids_all_client_authority_fields_without_transition(tmp_path):
    store = _incident_store(tmp_path)
    client = TestClient(_app(is_admin=True, store=store))
    bodies = (
        {"expected_version": 1, "incident": {"incident_id": "inc-owner"}},
        {"expected_version": 1, "action": {"action_id": "act-owner"}},
        {"expected_version": 1, "action": {"raw_target": r"C:\\private\\secret-command"}},
        {"expected_version": 1, "owner": "admin"},
        {"expected_version": 1, "state": "prepared"},
        {"expected_version": {"value": 1}},
        {"expected_version": 1, "version": 2},
    )

    for body in bodies:
        response = client.post("/api/security/incidents/inc-owner/actions/act-owner/prepare", json=body)
        assert response.status_code == 422
        assert "private" not in response.text.lower()
        assert "secret-command" not in response.text.lower()
        assert store.get_action("act-owner").state == "proposed"
        assert store.get_action("act-owner").version == 1


def test_security_prepare_owner_and_resolver_fail_closed_while_admin_bypasses(tmp_path):
    store = _incident_store(tmp_path)
    denied = TestClient(_app(is_admin=False, user="bob", store=store, owner_resolver=lambda _request, _incident_id: "alice"))
    missing = TestClient(_app(is_admin=False, user="alice", store=store))
    broken = TestClient(_app(is_admin=False, user="alice", store=store, owner_resolver=lambda *_args: (_ for _ in ()).throw(RuntimeError("owner detail"))))
    admin = TestClient(_app(is_admin=True, user="admin", store=store, owner_resolver=lambda _request, _incident_id: "alice"))

    assert denied.post("/api/security/incidents/inc-owner/actions/act-owner/prepare", json={"expected_version": 1}).status_code == 403
    assert missing.get("/api/security/incidents/inc-owner").status_code == 403
    assert broken.get("/api/security/incidents/inc-owner").status_code == 403
    assert admin.get("/api/security/incidents/inc-owner").status_code == 200
    assert admin.post("/api/security/incidents/inc-owner/actions/act-owner/prepare", json={"expected_version": 1}).status_code == 200


def test_security_delivery_is_server_owned_exactly_once_and_readback_is_independent(tmp_path):
    store, readiness = _approved_delivery_store(tmp_path)
    calls = []

    def fake_transport(value):
        calls.append(dict(value))
        return {"status": "acknowledged", "receipt_ref": "receipt:sha256:" + "a" * 64}

    fake_transport.security_incident_delivery_test_fake = True
    delivery_request = build_server_owned_delivery_request(store.get_action("act-delivery"), readiness)
    client = TestClient(_app(
        store=store, delivery_transport=issue_test_delivery_transport(fake_transport),
        delivery_readiness=readiness,
    ))
    response = client.post(
        "/api/security/incidents/inc-delivery/actions/act-delivery/delivery",
        json={"expected_version": 3},
    )
    repeated = client.post(
        "/api/security/incidents/inc-delivery/actions/act-delivery/delivery",
        json={"expected_version": 3},
    )
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200 and payload["status"] == "acknowledged"
    assert repeated.status_code == 409 and len(calls) == 1
    assert set(calls[0]) == {
        "schema", "action_id", "action_version", "body_ref", "channel",
        "approved_target_class_ref", "timeout_seconds", "raw_content_visible",
    }
    assert calls[0]["body_ref"] == canonical_operator_notification_body_ref()
    assert calls[0]["approved_target_class_ref"] == canonical_operator_notification_target_class_ref()
    assert calls[0]["raw_content_visible"] is False
    assert "configured_telegram_operator_target_v1" not in encoded
    assert "Security incident operator notification" not in encoded
    assert SecurityIncidentDeliveryAdapter(store).readback(delivery_request)["status"] == "acknowledged"


def test_security_delivery_rejects_client_authority_and_defaults_to_disabled(tmp_path):
    store, _readiness = _approved_delivery_store(tmp_path)
    client = TestClient(_app(store=store))
    endpoint = "/api/security/incidents/inc-delivery/actions/act-delivery/delivery"

    disabled = client.post(endpoint, json={"expected_version": 3})
    injected = client.post(endpoint, json={
        "expected_version": 3, "body": "private target", "target": "secret",
    })

    assert disabled.status_code == 503
    assert injected.status_code == 422
    assert "private target" not in injected.text.lower()
    assert store.get_action("act-delivery").state == "approved"


def test_security_delivery_runtime_app_state_rejects_test_issuer(tmp_path):
    store, readiness = _approved_delivery_store(tmp_path)
    calls = []

    def fake_transport(_value):
        calls.append(True)
        return {"status": "acknowledged", "receipt_ref": "receipt:sha256:" + "a" * 64}

    fake_transport.security_incident_delivery_test_fake = True
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: user == "admin")
    app.state.security_incident_store = store
    app.state.security_incident_delivery_transport = issue_test_delivery_transport(fake_transport)
    app.state.security_incident_delivery_readiness = readiness

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = "admin"
        return await call_next(request)

    app.include_router(setup_security_routes())
    response = TestClient(app).post(
        "/api/security/incidents/inc-delivery/actions/act-delivery/delivery",
        json={"expected_version": 3},
    )

    assert response.status_code == 503
    assert calls == [] and store.get_action("act-delivery").state == "approved"


def test_security_delivery_owner_scope_and_unknown_outcome_never_retry(tmp_path):
    store, readiness = _approved_delivery_store(tmp_path)
    calls = []

    def fake_transport(_value):
        calls.append(True)
        raise RuntimeError("private provider failure")

    fake_transport.security_incident_delivery_test_fake = True
    endpoint = "/api/security/incidents/inc-delivery/actions/act-delivery/delivery"
    denied = TestClient(_app(
        is_admin=False, user="bob", store=store,
        owner_resolver=lambda _request, _incident_id: "alice",
        delivery_transport=issue_test_delivery_transport(fake_transport),
        delivery_readiness=readiness,
    ))
    assert denied.post(endpoint, json={"expected_version": 3}).status_code == 403
    assert calls == [] and store.get_action("act-delivery").state == "approved"

    allowed = TestClient(_app(
        is_admin=False, user="alice", store=store,
        owner_resolver=lambda _request, _incident_id: "alice",
        delivery_transport=issue_test_delivery_transport(fake_transport),
        delivery_readiness=readiness,
    ))
    unknown = allowed.post(endpoint, json={"expected_version": 3})
    retry = allowed.post(endpoint, json={"expected_version": 3})

    assert unknown.status_code == 200 and unknown.json()["status"] == "unknown"
    assert unknown.json()["retry_scheduled"] is False
    assert retry.status_code == 409 and len(calls) == 1
    assert SecurityIncidentDeliveryAdapter(store).readback(
        {"schema": "odysseus.security_incident_delivery_request.v1", "incident_id": "inc-delivery", "action_id": "act-delivery", "action_version": 3,
         "scope_fingerprint": "scope:sha256:" + "c" * 64, "policy_revision": "policy:sha256:" + "d" * 64,
         "body_ref": canonical_operator_notification_body_ref(), "approved_target_class_ref": canonical_operator_notification_target_class_ref(),
         "channel": "telegram", "grant_expires_at": store.get_action("act-delivery").expires_at, "timeout_seconds": 15,
         "telegram_delivery_readiness": readiness, "policy_gate": "OPS-ALERT-DELIVERY-GO"}
    )["status"] == "unknown"


def test_zero_arg_registered_security_factory_uses_server_provider(tmp_path, monkeypatch):
    store = _incident_store(tmp_path)
    monkeypatch.setattr(security_routes, "create_default_security_incident_store", lambda: store)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: user == "admin")

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = "admin"
        return await call_next(request)

    app.include_router(security_routes.setup_security_routes())
    response = TestClient(app).get("/api/security/incidents/inc-owner")

    assert response.status_code == 200


def test_security_read_reports_bounded_action_count_truthfully():
    events = tuple(
        SimpleNamespace(incident_id="inc-bounded", action_id=f"act-{index:03}")
        for index in range(101)
    )

    class _Store:
        def get_incident(self, incident_id):
            return SimpleNamespace(incident_id=incident_id, version=1)

        def get_action(self, _action_id):
            raise AssertionError("read endpoint must not get action")

        def transition(self, **_kwargs):
            raise AssertionError("read endpoint must not transition")

        def audit_events(self):
            return events

    response = TestClient(_app(is_admin=True, store=_Store())).get("/api/security/incidents/inc-bounded")

    assert response.status_code == 200
    assert response.json()["incident"]["action_count"] == 100
    assert response.json()["incident"]["action_count_truncated"] is True
