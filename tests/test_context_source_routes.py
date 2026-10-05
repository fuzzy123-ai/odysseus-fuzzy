"""Real standalone HTTP transport; confirmation consumption is component-level."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.database import ChatMessage
from routes.auth_routes import SESSION_COOKIE
from routes.context_source_routes import create_context_source_router
from src.forge_context_page import ForgeContextPageConsumer
from src.harbour_knowledge_active_runtime import KnowledgeContextPageConsumer
from src.planning_context_reader import PlanningContextPageConsumer
from src.private_context_confirmation import Challenge
from src.private_context_confirmation_app import PrivateContextConfirmationApp
from tests.test_context_manifest_producer import real_three_domain_context


def _selection(context):
    values = []
    for item in context["selection"].items:
        row = {"source": item.source, "selection_ref": item.selection_ref, "revision_ref": item.revision_ref}
        if item.forge_locator is not None:
            locator = item.forge_locator
            row["forge_locator"] = {"repo_id": locator.repo_id, "version_id": locator.version_id, "path": locator.path}
        values.append(row)
    return values


@pytest.fixture
def http_context(real_three_domain_context):
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
        confirmation_app=confirmation, forge=context["forge"], planning=context["planning_producer"],
        knowledge=context["knowledge_producer"], forge_pages=forge_pages, planning_pages=planning_pages, knowledge_pages=knowledge_pages,
    ))
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, context["cookie"])
        yield context, confirmation, client


def _metadata(context, client):
    body = {"chat_session_id": context["chat_id"], "selection": _selection(context)}
    response = client.post("/api/context/metadata", json=body, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    metadata = response.json()
    assert metadata["requires_private_confirmation"] is True
    assert {item["source"] for item in metadata["items"]} == {"forge", "knowledge", "planning"}
    assert metadata["selection"] == body["selection"]
    assert len({target["manifest_digest"] for target in metadata["targets"]}) == 3
    assert "owner_sid" not in metadata
    return body, metadata


def _page_body(body, metadata, index, disclosure_id=None):
    result = dict(body, target_index=index, expected_manifest_digest=metadata["targets"][index]["manifest_digest"], offset=0, limit=17)
    if disclosure_id is not None:
        result["disclosure_message_id"] = disclosure_id
    return result


def test_real_http_three_domain_metadata_challenge_pages_and_signed_row_tamper(http_context):
    context, confirmation, client = http_context
    body, metadata = _metadata(context, client)
    headers = {"Origin": "http://testserver"}
    for index in range(3):
        denied = client.post("/api/context/page", json=_page_body(body, metadata, index), headers=headers)
        assert denied.status_code in {400, 409}
        assert set(denied.json()) == {"error"}
    response = client.post("/api/context/challenge", json=body, headers=headers)
    assert response.status_code == 200
    challenge = response.json()
    assert challenge["command"] == "/context-disclose " + challenge["challenge_token"]
    expires = datetime.fromisoformat(challenge["expires_at"])
    # This fixture deliberately consumes through the real component, not an HTTP chat shim.
    verified = confirmation.consume_challenge(context["cookie"], context["chat_id"], Challenge(challenge["challenge_token"], expires - timedelta(seconds=300), expires))
    byteforms = {"forge": "git_blob", "planning": "durable_revision_document", "knowledge": "knowledge_payload"}
    for index, target in enumerate(context["selection"].items):
        response = client.post("/api/context/page", json=_page_body(body, metadata, index, verified.disclosure_message_id), headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-context-byteform"] == byteforms[target.source]
        assert int(response.headers["x-context-end"]) == len(response.content) <= 17
        assert int(response.headers["x-context-total"]) >= len(response.content)
        assert response.headers["x-context-page-sha256"] == hashlib.sha256(response.content).hexdigest()
        assert response.headers["x-context-manifest-sha256"] == metadata["targets"][index]["manifest_digest"].removeprefix("sha256:")
    db = context["session_factory"]()
    try:
        row = db.query(ChatMessage).filter(ChatMessage.id == str(verified.disclosure_message_id)).one()
        row.content = "tampered confirmation"
        db.commit()
    finally:
        db.close()
    denied = client.post("/api/context/page", json=_page_body(body, metadata, 0, verified.disclosure_message_id), headers=headers)
    assert denied.status_code == 409
    assert denied.json() == {"error": {"code": "context_unavailable"}}


def test_real_http_forge_bootstrap_derives_canonical_refs_without_private_classification(http_context):
    context, _confirmation, client = http_context
    locator = context["forge_selection"].forge_locator
    body = {"chat_session_id": context["chat_id"], "forge_locators": [{"repo_id": locator.repo_id, "version_id": locator.version_id, "path": locator.path}]}
    response = client.post("/api/context/metadata", json=body, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["requires_private_confirmation"] is False
    assert metadata["manifest_digest"] is None
    assert metadata["selection"][0]["selection_ref"] == context["forge_selection"].selection_ref
    assert metadata["selection"][0]["revision_ref"] == context["forge_selection"].revision_ref
    page = client.post("/api/context/page", json=_page_body({"chat_session_id": context["chat_id"], "selection": metadata["selection"]}, metadata, 0), headers={"Origin": "http://testserver"})
    assert page.status_code == 200
    assert page.headers["x-context-byteform"] == "git_blob"


def test_http_rejects_hostile_transport_and_closed_json_before_disclosure(http_context):
    context, _confirmation, client = http_context
    body = {"chat_session_id": context["chat_id"], "selection": _selection(context)}
    for headers in ({}, {"Origin": "https://foreign.invalid"}, {"Origin": "http://testserver", "Authorization": "Bearer rejected"},
                    {"Origin": "http://testserver", "X-Forwarded-For": "127.0.0.1"}, {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"}):
        response = client.post("/api/context/metadata", json=body, headers=headers)
        assert response.status_code in {401, 403}
        assert response.headers["cache-control"] == "no-store"
    headers = {"Origin": "http://testserver"}
    duplicate = json.dumps(body)[:-1] + ',"chat_session_id":"duplicate"}'
    assert client.post("/api/context/metadata", content=duplicate, headers=headers).status_code == 400
    assert client.post("/api/context/metadata", json=dict(body, incognito=True), headers=headers).status_code == 400
    assert client.post("/api/context/metadata?owner=fuzzy", json=body, headers=headers).status_code == 403
    assert client.post("/api/context/metadata", content=b" " * (256 * 1024 + 1), headers=headers).status_code == 413
    client.cookies.clear()
    assert client.post("/api/context/metadata", json=body, headers=headers).status_code == 401
