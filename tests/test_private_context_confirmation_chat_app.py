from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth import AuthManager
from core.database import Base, Session
from routes import chat_routes
from routes.auth_routes import SESSION_COOKIE
from src.mcp_private_context_confirmation import DisclosureReceipt
from src.private_context_confirmation import ContextManifest, ManifestItem
from src.private_context_confirmation_app import (
    ContextSelection,
    PrivateContextConfirmationApp,
    SourceSelection,
)


_VALID_CHALLENGE = "pc1." + ("A" * 43) + "." + ("0" * 64)


class _SessionManager:
    def get_session(self, session_id: str):
        if session_id != "chat-owned":
            raise KeyError(session_id)
        return SimpleNamespace(id=session_id, owner="alice", model="test", history=[])

    def reload_session_after_external_commit(self, session_id: str):
        if session_id != "chat-owned":
            raise RuntimeError("session_reload_failed")
        return self.get_session(session_id)


class _SyntheticProducer:
    def capture(self, binding, selection):
        return ContextManifest(
            tuple(
                ManifestItem(
                    item.source,
                    item.selection_ref,
                    item.revision_ref,
                    "1" * 64,
                    "2" * 64,
                    "3" * 64,
                    "private",
                    "synthetic.test",
                )
                for item in selection.items
            )
        )


@pytest.fixture
def route_context(tmp_path, monkeypatch):
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("alice", "Password-123", is_admin=True)
    token = auth.create_session("alice", "Password-123")
    assert token

    application = FastAPI()
    application.state.auth_manager = auth
    application.state.session_manager = _SessionManager()
    application.state.private_context_confirmation = PrivateContextConfirmationApp(
        lambda: None,
        auth,
        manifest_producer=None,
    )
    application.state.private_context_confirmation_auth_manager = auth
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session: None)
    application.include_router(
        chat_routes.setup_chat_routes(
            application.state.session_manager,
            object(),
            object(),
            object(),
            object(),
            object(),
        )
    )
    client = TestClient(application)
    client.cookies.set(SESSION_COOKIE, token)
    return client, token


def _json_payload(token: str = _VALID_CHALLENGE, **extra):
    value = {"message": f"/context-disclose {token}", "session": "chat-owned"}
    value.update(extra)
    return value


def test_injected_synthetic_producer_round_trip_reloads_cache(route_context) -> None:
    client, token = route_context
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    db.add(
        Session(
            id="chat-owned",
            name="Owned",
            endpoint_url="local",
            model="test",
            owner="alice",
            message_count=0,
        )
    )
    db.commit()
    db.close()
    service = PrivateContextConfirmationApp(
        factory,
        client.app.state.auth_manager,
        _SyntheticProducer(),
    )
    selection = ContextSelection((SourceSelection("forge", "selection.1", "revision.1"),))
    challenge = service.issue_for_selection(token, "chat-owned", selection)
    client.app.state.private_context_confirmation = service
    response = client.post(
        "/api/chat",
        json={"message": f"/context-disclose {challenge.token}", "session": "chat-owned"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["response"] == "Private context disclosure confirmed."
    assert type(payload["disclosure_message_id"]) is int
    assert type(payload["acknowledgement_message_id"]) is int
    assert payload["audience"] == "authenticated_context_download"
    assert payload["mode"] == "read_only"
    assert challenge.token not in response.text
    db = factory()
    assert db.query(Session).filter(Session.id == "chat-owned").one().message_count == 2
    db.close()
    engine.dispose()


def test_nonstream_reserved_malformed_command_is_terminal(route_context) -> None:
    client, _token = route_context
    response = client.post(
        "/api/chat",
        json={"message": "/context-disclose", "session": "chat-owned"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_context_challenge"}


def test_stream_reserved_malformed_command_is_terminal(route_context) -> None:
    client, _token = route_context
    response = client.post(
        "/api/chat_stream",
        json={"message": "/context-disclose extra words", "session": "chat-owned"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_context_challenge"}


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat_stream"])
def test_default_producer_fails_closed_before_model_or_provider(route_context, path: str) -> None:
    client, _token = route_context
    response = client.post(
        path,
        json=_json_payload(),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "private_context_unavailable"}


@pytest.mark.parametrize(
    "headers, status",
    [
        ({}, 403),
        ({"Authorization": "Bearer ody_external"}, 401),
        ({"X-Forwarded-For": "198.51.100.4"}, 401),
        ({"X-Odysseus-Internal-Token": "tool"}, 401),
    ],
)
def test_private_command_requires_cookie_same_origin_and_direct_transport(route_context, headers, status) -> None:
    client, _token = route_context
    response = client.post("/api/chat", json=_json_payload(), headers=headers)
    assert response.status_code == status


def test_incognito_option_is_rejected_even_when_pydantic_would_ignore_it(route_context) -> None:
    client, _token = route_context
    response = client.post(
        "/api/chat",
        json=_json_payload(incognito=True),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "private_context_requires_authenticated_chat"}


def test_missing_cookie_cannot_use_local_bypass(route_context, monkeypatch) -> None:
    client, _token = route_context
    client.cookies.clear()
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")
    response = client.post(
        "/api/chat",
        json=_json_payload(),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/api/chat", "/mcp-context-disclose"),
        ("/api/chat", "/mcp-context-disclose short"),
        ("/api/chat_stream", "/mcp-context-disclose " + "A" * 42),
        ("/api/chat_stream", " /mcp-context-disclose " + "A" * 43),
        ("/api/chat_stream", "/mcp-context-disclose " + "A" * 43 + " "),
    ],
    ids=["missing-token", "short-token", "token-42", "leading-space", "trailing-space"],
)
def test_mcp_reserved_malformed_variants_are_terminal_before_chat(route_context, path, message) -> None:
    client, _token = route_context
    response = client.post(
        path,
        json={"message": message, "session": "chat-owned"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_mcp_context_challenge"}


@pytest.mark.parametrize(
    "path",
    ["/api/chat", "/api/chat_stream"],
    ids=["nonstream", "stream"],
)
def test_exact_mcp_command_fails_closed_without_composition_before_model(route_context, path) -> None:
    client, _token = route_context
    response = client.post(
        path,
        json={
            "message": "/mcp-context-disclose " + "A" * 43,
            "session": "chat-owned",
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "mcp_context_unavailable"}


@pytest.mark.parametrize("stream", (False, True), ids=("nonstream", "stream"))
def test_mcp_disclosure_worker_uses_captured_entry_after_mutable_descriptor_replacement(
    monkeypatch, stream,
) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    principal = chat_routes.SessionPrincipal(
        owner="alice",
        creator_subject_id="usr_" + "a" * 32,
        authority="browser_session",
    )
    session_manager = _SessionManager()
    fake_calls = []
    captured_calls = []

    def fake_disclosure(*_args, **_kwargs):
        fake_calls.append(True)
        raise AssertionError("replacement disclosure executed")

    composition = SimpleNamespace(_disclose_browser_descriptor=fake_disclosure)

    def captured_disclosure(
        received_composition,
        received_request,
        challenge,
        owner_sid,
        chat_session_id,
        raw_command,
    ):
        captured_calls.append((received_composition, received_request, owner_sid))
        composition._disclose_browser_descriptor = fake_disclosure
        assert challenge == "A" * 43
        assert chat_session_id == "chat-owned"
        assert raw_command == "/mcp-context-disclose " + challenge
        return "request-captured", DisclosureReceipt("request-captured", 11, 12)

    monkeypatch.setattr(
        chat_routes,
        "_private_principal",
        lambda received_request, *, expected=None: expected,
    )
    encoded, media_type = chat_routes._mcp_disclose_sync(
        request,
        composition,
        session_manager,
        principal,
        "chat-owned",
        "A" * 43,
        stream,
        captured_disclosure,
    )
    assert captured_calls == [(composition, request, principal.creator_subject_id)]
    assert fake_calls == []
    assert b'"request_id":"request-captured"' in encoded
    assert media_type == ("text/event-stream" if stream else "application/json")


def test_mcp_route_entry_rejects_coordinated_module_and_class_capture_replacement(
    monkeypatch,
) -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    auth = object()
    composition = object.__new__(chat_routes._MCP_CONTEXT_APP_TYPE)
    composition._app = app
    composition._auth = auth
    app.state.auth_manager = auth
    app.state.mcp_context_app = composition
    request = SimpleNamespace(app=app)
    fake_calls = []

    def fake_capture(*_args, **_kwargs):
        fake_calls.append(True)
        raise AssertionError("replacement route capture executed")

    monkeypatch.setattr(chat_routes, "_MCP_CAPTURE_DISCLOSURE", fake_capture)
    monkeypatch.setattr(
        chat_routes._MCP_CONTEXT_APP_TYPE,
        "capture_browser_disclosure_entry",
        fake_capture,
    )
    with pytest.raises(Exception):
        chat_routes._mcp_context_composition(request)
    assert fake_calls == []
