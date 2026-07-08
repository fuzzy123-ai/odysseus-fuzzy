import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.universal_inbox_routes import setup_universal_inbox_routes
from src.upload_handler import UploadHandler
from src.universal_inbox_flow_state import CANONICAL_FLOW_STAGES


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _make_handler(tmp_path, rows):
    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "07" / "03"
    dated.mkdir(parents=True)
    index = {}
    for row in rows:
        file_path = dated / row["id"]
        file_path.write_bytes(row.get("bytes", b"stored file bytes"))
        stored = {
            "id": row["id"],
            "path": str(file_path),
            "mime": row["mime"],
            "size": file_path.stat().st_size,
            "name": row["name"],
            "original_name": row["name"],
            "uploaded_at": "2026-07-03T12:00:00",
            "hash": row.get("hash", row["id"]),
            "owner": row.get("owner"),
        }
        index[f"{stored.get('owner')}:{stored['hash']}"] = stored
    (upload_dir / "uploads.json").write_text(json.dumps(index), encoding="utf-8")
    return UploadHandler(str(tmp_path), str(upload_dir))


def _app(handler, *, user="alice", admins=()) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_universal_inbox_routes(handler))
    return app


def test_upload_status_is_redacted_and_classifies_pdf(tmp_path):
    upload_id = "a" * 32 + ".pdf"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": upload_id,
                "name": "private-invoice-2026.pdf",
                "mime": "application/pdf",
                "owner": "alice",
            }
        ],
    )

    response = TestClient(_app(handler)).get(f"/api/universal-inbox/items/upload:{upload_id}/status")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "uploaded"
    assert payload["source_ref"] == f"upload:{upload_id}"
    assert payload["family"] == "document"
    assert payload["category"] == "document_extractable"
    assert payload["extractable_now"] is True
    assert payload["review_required"] is False
    assert payload["display_name_redacted"] is True
    assert payload["path_redacted"] is True
    assert "private-invoice" not in encoded
    assert str(tmp_path) not in encoded


def test_upload_status_denies_cross_owner_without_leaking_existence(tmp_path):
    upload_id = "b" * 32 + ".docx"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": upload_id,
                "name": "bob-contract.docx",
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "owner": "bob",
            }
        ],
    )

    response = TestClient(_app(handler, user="alice")).get(
        f"/api/universal-inbox/items/upload:{upload_id}/status"
    )

    assert response.status_code == 404


def test_upload_status_allows_admin_and_marks_image_review(tmp_path):
    upload_id = "c" * 32 + ".jpg"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": upload_id,
                "name": "wall-label.jpg",
                "mime": "image/jpeg",
                "owner": "bob",
            }
        ],
    )

    response = TestClient(_app(handler, user="admin", admins={"admin"})).get(
        f"/api/universal-inbox/items/upload:{upload_id}/status"
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "needs_review"
    assert payload["family"] == "image"
    assert payload["review_required"] is True
    assert payload["next_action"] == "review"
    assert "image_metadata_only" in payload["reason_codes"]


def test_upload_status_rejects_path_like_source_refs(tmp_path):
    handler = _make_handler(tmp_path, [])

    response = TestClient(_app(handler)).get("/api/universal-inbox/items/upload:../secret/status")

    assert response.status_code == 400


def test_upload_flow_state_is_redacted_and_metadata_only(tmp_path):
    upload_id = "d" * 32 + ".pdf"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": upload_id,
                "name": "private-invoice-2026.pdf",
                "mime": "application/pdf",
                "owner": "alice",
            }
        ],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/flow-state"
    )
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.universal_inbox.flow_state.v1"
    assert payload["source_kind"] == "upload"
    assert payload["source_ref_visible"] is False
    assert payload["source_path_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["secret_values_visible"] is False
    assert payload["chat_id_visible"] is False
    assert payload["live_write_allowed"] is False
    assert payload["overall_status"] == "partial"
    assert payload["next_action"] == "extracted"
    assert [step["stage"] for step in payload["steps"]] == list(CANONICAL_FLOW_STAGES)
    assert payload["steps"][0]["status"] == "completed"
    assert payload["steps"][1]["metadata"]["category"] == "document_extractable"
    assert payload["steps"][1]["metadata"]["extractable_now"] is True
    assert payload["runtime_event"]["component"] == "flow_state"
    assert payload["runtime_event"]["side_effects"] == ["none"]
    assert "private-invoice" not in encoded
    assert str(tmp_path) not in encoded
    assert upload_id not in encoded


def test_upload_flow_state_surfaces_review_without_raw_names(tmp_path):
    upload_id = "e" * 32 + ".jpg"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": upload_id,
                "name": "whiteboard-secret.jpg",
                "mime": "image/jpeg",
                "owner": "alice",
            }
        ],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/flow-state"
    )
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["overall_status"] == "review"
    assert payload["next_action"] == "operator_review"
    assert payload["review_reasons"] == ["image_metadata_only"]
    assert payload["review_reason_details"][0]["code"] == "image_metadata_only"
    assert payload["steps"][1]["status"] == "review"
    assert payload["steps"][4]["status"] == "review"
    assert "whiteboard-secret" not in encoded
    assert upload_id not in encoded


def test_upload_flow_state_denies_cross_owner_without_leaking_existence(tmp_path):
    upload_id = "f" * 32 + ".pdf"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": upload_id,
                "name": "bob-private.pdf",
                "mime": "application/pdf",
                "owner": "bob",
            }
        ],
    )

    response = TestClient(_app(handler, user="alice")).get(
        f"/api/universal-inbox/items/upload:{upload_id}/flow-state"
    )

    assert response.status_code == 404


def test_upload_flow_state_rejects_path_like_source_refs(tmp_path):
    handler = _make_handler(tmp_path, [])

    response = TestClient(_app(handler)).get("/api/universal-inbox/items/upload:../secret/flow-state")

    assert response.status_code == 400
