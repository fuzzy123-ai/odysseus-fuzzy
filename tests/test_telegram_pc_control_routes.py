import json
import asyncio
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.pc_control import (
    TelegramPcControlService,
    build_agent_request_signature,
    build_agent_response_signature,
)
from plugins.telegram.routes_pc_control import register_telegram_pc_control_routes
import plugins.telegram.routes_pc_control as pc_routes


def _app(tmp_path):
    env = {
        "TELEGRAM_PC_CONTROL_ENABLED": "true",
        "TELEGRAM_PC_AGENT_SECRET": "route-test-agent-secret-with-enough-entropy",
    }
    service = TelegramPcControlService(tmp_path, environ=env, id_factory=lambda: "route-action")
    router = APIRouter(prefix="/api/plugins/telegram")
    register_telegram_pc_control_routes(router, service=service)
    app = FastAPI()
    app.include_router(router)
    return app, service, env["TELEGRAM_PC_AGENT_SECRET"]


def _signed_post(client, secret, path, payload, *, timestamp, nonce):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = build_agent_request_signature(
        secret,
        timestamp=str(timestamp),
        nonce=nonce,
        method="POST",
        path=path,
        body=body,
    )
    return client.post(
        path,
        content=body,
        headers={
            "content-type": "application/json",
            "x-odysseus-pc-timestamp": str(timestamp),
            "x-odysseus-pc-nonce": nonce,
            "x-odysseus-pc-signature": signature,
        },
    )


def test_agent_routes_reject_unsigned_requests_and_sign_exact_responses(tmp_path, monkeypatch):
    app, service, secret = _app(tmp_path)
    now = 1_800_000_000
    monkeypatch.setattr(service, "now", lambda: float(now))
    service.store.now = service.now
    client = TestClient(app)

    unsigned = client.post("/api/plugins/telegram/pc-agent/poll", json={})
    assert unsigned.status_code == 403
    assert unsigned.json() == {"error": "agent_auth_required", "raw_content_visible": False}

    service.store.enqueue("start_codex", ttl_seconds=120)
    nonce = "route-nonce-abcdefghijkl"
    response = _signed_post(
        client,
        secret,
        "/api/plugins/telegram/pc-agent/poll",
        {"agent_version": "1.0", "codex_running": False, "interactive_session": True},
        timestamp=now,
        nonce=nonce,
    )
    assert response.status_code == 200
    assert response.json()["command"]["action"] == "start_codex"
    assert response.headers["x-odysseus-pc-response-signature"] == build_agent_response_signature(
        secret,
        request_nonce=nonce,
        status_code=200,
        body=response.content,
    )


def test_agent_result_route_rejects_replay_and_private_error_fields(tmp_path, monkeypatch):
    app, service, secret = _app(tmp_path)
    now = 1_800_000_000
    monkeypatch.setattr(service, "now", lambda: float(now))
    service.store.now = service.now
    client = TestClient(app)
    service.store.enqueue("lock", ttl_seconds=120)

    nonce = "route-result-abcdefghijk"
    payload = {"action_id": "route-action", "outcome": "completed", "code": "locked"}
    first = _signed_post(
        client,
        secret,
        "/api/plugins/telegram/pc-agent/result",
        payload,
        timestamp=now,
        nonce=nonce,
    )
    assert first.status_code == 200
    assert first.json() == {"raw_content_visible": False, "status": "completed"}

    replay = _signed_post(
        client,
        secret,
        "/api/plugins/telegram/pc-agent/result",
        payload,
        timestamp=now,
        nonce=nonce,
    )
    assert replay.status_code == 403
    assert replay.json()["error"] == "agent_request_replayed"

    invalid = _signed_post(
        client,
        secret,
        "/api/plugins/telegram/pc-agent/result",
        {"action_id": "route-action", "outcome": "failed", "code": "private stack trace"},
        timestamp=now,
        nonce="route-result-zyxwvutsrqpo",
    )
    assert invalid.status_code == 400
    assert "private stack trace" not in invalid.text


class _UnreadableAgentRequest:
    def __init__(self, chunks, *, content_length=None, delay_seconds=0.0):
        self.headers = {} if content_length is None else {"content-length": content_length}
        self.method = "POST"
        self.url = SimpleNamespace(path="/api/plugins/telegram/pc-agent/poll")
        self._chunks = chunks
        self._delay_seconds = delay_seconds

    async def stream(self):
        for chunk in self._chunks:
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            yield chunk


class _NoAuthenticationService:
    def __init__(self):
        self.authentication_calls = 0

    def authenticate_agent_request(self, **_kwargs):
        self.authentication_calls += 1
        raise AssertionError("authentication must not run for rejected ingress")


def test_agent_ingress_rejects_declared_and_chunked_oversize_before_authentication():
    service = _NoAuthenticationService()
    declared = _UnreadableAgentRequest((b"x",), content_length="8193")
    declared_response = asyncio.run(pc_routes._handle(declared, service=service, operation="poll"))
    assert declared_response.status_code == 413
    assert json.loads(declared_response.body) == {
        "error": "agent_body_too_large",
        "raw_content_visible": False,
    }

    chunked = _UnreadableAgentRequest((b"x" * 4096, b"y" * 4097))
    chunked_response = asyncio.run(pc_routes._handle(chunked, service=service, operation="poll"))
    assert chunked_response.status_code == 413
    assert json.loads(chunked_response.body) == {
        "error": "agent_body_too_large",
        "raw_content_visible": False,
    }
    assert service.authentication_calls == 0


def test_agent_ingress_timeout_rejects_before_authentication(monkeypatch):
    service = _NoAuthenticationService()
    monkeypatch.setattr(pc_routes, "_BODY_READ_TIMEOUT_SECONDS", 0.001)
    request = _UnreadableAgentRequest((b"{}",), delay_seconds=0.01)

    response = asyncio.run(pc_routes._handle(request, service=service, operation="poll"))

    assert response.status_code == 408
    assert json.loads(response.body) == {
        "error": "agent_body_timeout",
        "raw_content_visible": False,
    }
    assert service.authentication_calls == 0
