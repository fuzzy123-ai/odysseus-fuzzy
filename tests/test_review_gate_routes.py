import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.stores import TelegramInboxStore
from routes.review_gate_routes import setup_review_gate_routes


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _app(data_dir, *, user="admin", admins=("admin",)) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_review_gate_routes(data_dir))
    return app


def test_review_gate_status_empty_store_is_clear_and_redacted(tmp_path):
    response = TestClient(_app(tmp_path)).get("/api/review-gates/status")
    payload = response.json()

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.review_gate_state.v1"
    assert payload["status"] == "clear"
    assert payload["pending_count"] == 0
    assert payload["blocked_count"] == 0
    assert payload["gate_count"] == 4
    assert all(gate["state"] == "no_pending" for gate in payload["gates"])
    assert payload["raw_content_visible"] is False
    assert payload["path_values_visible"] is False
    assert payload["chat_id_value_visible"] is False
    assert payload["token_value_visible"] is False
    assert len(payload["canonical_gate_evidence"]) == 4
    assert payload["canonical_gate_evidence"][0]["schema"] == "gate_evidence_core.v1"
    assert payload["canonical_safe_now"]["schema"] == "gate_evidence_core.safe_now.v1"


def test_review_gate_status_summarizes_inbox_memory_and_raptor_without_raw_ids(tmp_path):
    TelegramInboxStore(tmp_path).append_event(
        kind="universal_inbox_attachment",
        status="processed",
        chat_id="raw-chat-123",
        message_id=42,
        universal_inbox_status="needs_review",
        attachment_family="document",
        attachment_suffix=".pdf",
        review_reason_count=2,
        maintenance_review_required=True,
        memory_write_intent_status="review",
        memory_records_planned=1,
        memory_records_written=0,
        raptorgraph_events_planned=1,
        raptorgraph_events_written=0,
        writes_performed=False,
    )

    response = TestClient(_app(tmp_path)).get("/api/review-gates/status")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)
    gates = {gate["id"]: gate for gate in payload["gates"]}

    assert response.status_code == 200
    assert payload["status"] == "pending"
    assert payload["pending_count"] == 3
    assert gates["nextcloud_copy"]["state"] == "pending_review"
    assert gates["memory_write"]["state"] == "pending_review"
    assert gates["raptorgraph_write"]["state"] == "pending_review"
    assert payload["canonical_safe_now"]["can_proceed"] is False
    assert payload["canonical_safe_now"]["operator_required_gate_ids"]
    assert gates["nextcloud_copy"]["metadata"]["attachment_suffix"] == ".pdf"
    assert "raw-chat-123" not in encoded
    assert "message_id" not in encoded


def test_review_gate_status_summarizes_export_plan_and_redacts_secretish_values(tmp_path):
    TelegramInboxStore(tmp_path).append_event(
        kind="universal_inbox_export_plan",
        status="ready",
        chat_id="export-chat",
        message_id=77,
        target_format="pdf",
        action="convert",
        required_tool="api_key=secret",
        delivery_ready=False,
    )

    response = TestClient(_app(tmp_path)).get("/api/review-gates/status")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)
    gates = {gate["id"]: gate for gate in payload["gates"]}

    assert response.status_code == 200
    assert payload["status"] == "pending"
    assert payload["pending_count"] == 1
    assert gates["file_export"]["state"] == "ready_to_execute"
    assert gates["file_export"]["metadata"]["target_format"] == "pdf"
    assert gates["file_export"]["metadata"]["required_tool"] == "redacted"
    assert "export-chat" not in encoded
    assert "secret" not in encoded


def test_review_gate_status_requires_admin(tmp_path):
    response = TestClient(_app(tmp_path, user="alice", admins=("admin",))).get("/api/review-gates/status")

    assert response.status_code == 403
