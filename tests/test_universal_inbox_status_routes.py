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
            "uploaded_at": row.get("uploaded_at", "2026-07-03T12:00:00"),
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


def test_owner_browse_is_paginated_capable_and_allowlist_redacted(tmp_path):
    first_id = "1" * 32 + ".pdf"
    second_id = "2" * 32 + ".docx"
    foreign_id = "3" * 32 + ".pdf"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": first_id,
                "name": "first-private-invoice.pdf",
                "mime": "application/pdf",
                "owner": "alice",
                "hash": "FIRST-SECRET-HASH",
                "uploaded_at": "2026-07-03T12:02:00",
            },
            {
                "id": second_id,
                "name": "second-private-contract.docx",
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "owner": "alice",
                "hash": "SECOND-SECRET-HASH",
                "uploaded_at": "2026-07-03T12:01:00",
            },
            {
                "id": foreign_id,
                "name": "bob-private.pdf",
                "mime": "application/pdf",
                "owner": "bob",
            },
        ],
    )
    client = TestClient(_app(handler))

    first_response = client.get("/api/universal-inbox/items", params={"limit": 1})
    first_page = first_response.json()
    first_encoded = json.dumps(first_page, sort_keys=True)

    assert first_response.status_code == 200
    assert first_page["schema"] == "odysseus.universal_inbox.items.v1"
    assert first_page["scope"] == {
        "source_kind": "upload",
        "owner_scoped": True,
        "admin_override": False,
        "owner_identifier_visible": False,
    }
    assert first_page["page"]["returned_count"] == 1
    assert first_page["page"]["has_more"] is True
    assert first_page["page"]["next_cursor"]
    assert first_page["items"][0]["source_ref"] == f"upload:{first_id}"
    assert first_page["items"][0]["display_name"] == "first-private-invoice.pdf"
    assert first_page["items"][0]["status"] == "uploaded"
    assert first_page["items"][0]["metadata"]["family"] == "document"
    assert first_page["items"][0]["capability"]["owner_authorized"] is True
    assert first_page["items"][0]["capability"]["server_authoritative"] is True
    assert first_page["items"][0]["absolute_path_visible"] is False
    assert first_page["items"][0]["raw_content_visible"] is False
    assert str(tmp_path) not in first_encoded
    assert "FIRST-SECRET-HASH" not in first_encoded
    assert "SECOND-SECRET-HASH" not in first_encoded
    assert "bob-private" not in first_encoded

    second_response = client.get(
        "/api/universal-inbox/items",
        params={"limit": 1, "cursor": first_page["page"]["next_cursor"]},
    )
    second_page = second_response.json()

    assert second_response.status_code == 200
    assert second_page["items"][0]["source_ref"] == f"upload:{second_id}"
    assert second_page["page"]["has_more"] is False
    assert second_page["page"]["next_cursor"] is None


def test_admin_browse_requires_explicit_owner_scope_and_foreign_user_is_denied(tmp_path):
    bob_id = "4" * 32 + ".pdf"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": bob_id,
                "name": "bob-report.pdf",
                "mime": "application/pdf",
                "owner": "bob",
            }
        ],
    )

    denied = TestClient(_app(handler, user="alice")).get(
        "/api/universal-inbox/items",
        params={"owner": "bob"},
    )
    allowed = TestClient(_app(handler, user="admin", admins={"admin"})).get(
        "/api/universal-inbox/items",
        params={"owner": "bob"},
    )

    assert denied.status_code == 404
    assert bob_id not in denied.text
    assert allowed.status_code == 200
    assert allowed.json()["scope"]["admin_override"] is True
    assert [item["display_name"] for item in allowed.json()["items"]] == [
        "bob-report.pdf"
    ]


def test_owner_browse_denies_anonymous_when_auth_is_configured(tmp_path):
    handler = _make_handler(tmp_path, [])

    response = TestClient(_app(handler, user=None)).get(
        "/api/universal-inbox/items"
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authenticated"


def test_owner_browse_rejects_malformed_cursor_and_out_of_range_limit(tmp_path):
    handler = _make_handler(tmp_path, [])
    client = TestClient(_app(handler))

    malformed = client.get(
        "/api/universal-inbox/items",
        params={"cursor": "not-base64*"},
    )
    zero = client.get("/api/universal-inbox/items", params={"limit": 0})
    too_large = client.get("/api/universal-inbox/items", params={"limit": 101})

    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "Invalid Universal Inbox cursor"
    assert zero.status_code == 422
    assert too_large.status_code == 422


def test_owner_snapshot_contains_counts_and_readiness_without_item_identity(tmp_path):
    ready_id = "5" * 32 + ".pdf"
    review_id = "6" * 32 + ".jpg"
    blocked_id = "7" * 32 + ".exe"
    foreign_id = "8" * 32 + ".pdf"
    handler = _make_handler(
        tmp_path,
        [
            {
                "id": ready_id,
                "name": "alice-ready.pdf",
                "mime": "application/pdf",
                "owner": "alice",
            },
            {
                "id": review_id,
                "name": "alice-review.jpg",
                "mime": "image/jpeg",
                "owner": "alice",
            },
            {
                "id": blocked_id,
                "name": "alice-blocked.exe",
                "mime": "application/x-msdownload",
                "owner": "alice",
            },
            {
                "id": foreign_id,
                "name": "bob-ready.pdf",
                "mime": "application/pdf",
                "owner": "bob",
            },
        ],
    )

    response = TestClient(_app(handler)).get("/api/universal-inbox/snapshot")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.universal_inbox.snapshot.v1"
    assert payload["total_count"] == 3
    assert payload["counts"] == {
        "uploaded": 1,
        "needs_review": 1,
        "blocked": 1,
        "unsupported": 0,
    }
    assert payload["family_counts"] == {
        "dangerous": 1,
        "document": 1,
        "image": 1,
    }
    assert payload["readiness"] == {
        "state": "blocked_items_present",
        "ready_count": 1,
        "attention_count": 1,
        "blocked_count": 1,
    }
    assert payload["item_names_visible"] is False
    assert payload["source_refs_visible"] is False
    assert str(tmp_path) not in encoded
    for secret in (
        ready_id,
        review_id,
        blocked_id,
        foreign_id,
        "alice-ready",
        "alice-review",
        "alice-blocked",
        "bob-ready",
    ):
        assert secret not in encoded


def test_browse_uses_metadata_source_without_resolving_or_reading_upload_bytes():
    upload_id = "9" * 32 + ".md"

    class _MetadataOnlyHandler:
        def list_upload_metadata(self):
            return (
                {
                    "id": upload_id,
                    "name": "safe-note.md",
                    "original_name": "safe-note.md",
                    "mime": "text/markdown",
                    "size": 12,
                    "uploaded_at": "2026-07-03T12:00:00",
                    "owner": "alice",
                    "path": "C:/private/must-not-leak.md",
                    "content": "must not be read or returned",
                },
            )

        def resolve_upload(self, *args, **kwargs):
            raise AssertionError("browse must not resolve or read upload bytes")

    response = TestClient(_app(_MetadataOnlyHandler())).get(
        "/api/universal-inbox/items"
    )
    encoded = json.dumps(response.json(), sort_keys=True)

    assert response.status_code == 200
    assert response.json()["items"][0]["display_name"] == "safe-note.md"
    assert "C:/private" not in encoded
    assert "must not be read" not in encoded


def test_browse_and_snapshot_report_unavailable_metadata_backend():
    client = TestClient(_app(None))

    browse = client.get("/api/universal-inbox/items")
    snapshot = client.get("/api/universal-inbox/snapshot")

    assert browse.status_code == 503
    assert snapshot.status_code == 503


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
