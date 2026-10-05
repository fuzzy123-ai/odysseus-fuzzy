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
