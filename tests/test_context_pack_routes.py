"""Installed-router checks for the closed mixed context pack endpoint."""
from __future__ import annotations

from tests.test_context_manifest_producer import real_three_domain_context

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import ChatMessage
from routes.auth_routes import SESSION_COOKIE
from routes.context_source_routes import create_context_source_router
from src.forge_context_page import ForgeContextPageConsumer
from src.harbour_knowledge_active_runtime import KnowledgeContextPageConsumer
from src.planning_context_reader import PlanningContextPageConsumer
from src.private_context_confirmation_app import PrivateContextConfirmationApp


def _selection(context):
    rows = []
    for item in context["selection"].items:
        row = {"source": item.source, "selection_ref": item.selection_ref, "revision_ref": item.revision_ref}
        if item.forge_locator is not None:
            locator = item.forge_locator
            row["forge_locator"] = {"repo_id": locator.repo_id, "version_id": locator.version_id, "path": locator.path}
        rows.append(row)
    return rows


@pytest.fixture
def pack_http(real_three_domain_context):
    context = real_three_domain_context
    confirmation = PrivateContextConfirmationApp(context["session_factory"], context["auth"], context["combined"])
    forge_pages = ForgeContextPageConsumer(
        metadata_provider=context["forge"], local_forge=context["local_forge"], auth_manager=context["auth"],
        session_factory=context["session_factory"], confirmation_app=confirmation, manifest_producer=context["combined"],
    )
    planning_pages = PlanningContextPageConsumer(context["planning_producer"], context["auth"], confirmation, context["session_factory"])
    knowledge_pages = KnowledgeContextPageConsumer(context["knowledge_composition"], context["auth"], confirmation, context["session_factory"])
    app = FastAPI()
    app.state.auth_manager = context["auth"]
    app.include_router(create_context_source_router(
        auth_manager=context["auth"], session_factory=context["session_factory"], manifest_producer=context["combined"],
        confirmation_app=confirmation, forge=context["forge"], planning=context["planning_producer"], knowledge=context["knowledge_producer"],
        forge_pages=forge_pages, planning_pages=planning_pages, knowledge_pages=knowledge_pages,
    ))
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, context["cookie"])
        yield context, confirmation, client


def _pack_body(context, cursor=None, disclosure=None):
    body = {
        "chat_session_id": context["chat_id"], "selection": _selection(context),
        "page_bytes": 17, "cursor": cursor,
    }
    if disclosure is not None:
        body["disclosure_message_id"] = disclosure
    return body


def test_installed_router_pack_reads_one_page_and_closes_json(pack_http):
    context, confirmation, client = pack_http
    challenge = confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], challenge)
    response = client.post(
        "/api/context/pack",
        json=_pack_body(context, disclosure=verified.disclosure_message_id),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert set(payload) == {"schema", "pack_id", "items", "page", "next_cursor", "complete", "read_only", "authority_granted", "send_performed"}
    assert base64.b64decode(payload["page"]["content_b64"])
    assert payload["read_only"] is True and payload["authority_granted"] is False and payload["send_performed"] is False


def test_installed_router_rejects_empty_cursor_before_page_read(pack_http, monkeypatch):
    context, _confirmation, client = pack_http
    calls = []
    original = ForgeContextPageConsumer.read_page

    def unexpected_read(self, cookie, request):
        calls.append(request.target.selection_ref)
        return original(self, cookie, request)

    monkeypatch.setattr(ForgeContextPageConsumer, "read_page", unexpected_read)
    response = client.post(
        "/api/context/pack",
        json=_pack_body(context, cursor=""),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "context_unavailable"}}
    assert "content_b64" not in response.text
    assert calls == []


def test_installed_router_continuation_drains_private_three_domain_selection(pack_http):
    context, confirmation, client = pack_http
    challenge = confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], challenge)
    cursor = None
    seen = set()
    for _ in range(8):
        response = client.post(
            "/api/context/pack",
            json=dict(_pack_body(context, cursor=cursor, disclosure=verified.disclosure_message_id), page_bytes=1_000_000),
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200
        payload = response.json()
        seen.add(payload["page"]["target_index"])
        if payload["complete"]:
            assert payload["next_cursor"] == ""
            break
        cursor = payload["next_cursor"]
        assert cursor
    else:
        pytest.fail("installed route did not reach complete=True")
    assert seen == {0, 1, 2}


def test_pack_denies_missing_confirmation_without_content(pack_http):
    context, _confirmation, client = pack_http
    response = client.post(
        "/api/context/pack",
        json=_pack_body(context),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "context_unavailable"}}
    assert "content_b64" not in response.text


def test_pack_transport_rejects_duplicate_json(pack_http):
    context, _confirmation, client = pack_http
    body = json.dumps(_pack_body(context))[:-1] + ',"page_bytes":17}'
    response = client.post(
        "/api/context/pack", content=body,
        headers={"Origin": "http://testserver", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "invalid_request"}}
