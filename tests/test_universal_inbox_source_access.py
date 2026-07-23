import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.universal_inbox_routes import setup_universal_inbox_routes
from src.upload_handler import UploadHandler
from src.universal_inbox_source_access import (
    UniversalInboxSourceAccessError,
    read_selected_universal_inbox_source,
)


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _make_handler(tmp_path, rows):
    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "07" / "23"
    dated.mkdir(parents=True)
    index = {}
    for row in rows:
        file_path = dated / row["id"]
        file_path.write_bytes(row["bytes"])
        stored = {
            "id": row["id"],
            "path": str(file_path),
            "mime": row["mime"],
            "size": file_path.stat().st_size,
            "name": row["name"],
            "original_name": row["name"],
            "uploaded_at": "2026-07-23T11:00:00",
            "hash": row["id"],
            "owner": row.get("owner"),
        }
        index[f"{stored.get('owner')}:{stored['hash']}"] = stored
    (upload_dir / "uploads.json").write_text(json.dumps(index), encoding="utf-8")
    return UploadHandler(str(tmp_path), str(upload_dir))


def _app(handler, *, user="alice", admins=()):
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_universal_inbox_routes(handler))
    return app


def _row(
    upload_id,
    *,
    name,
    mime,
    body,
    owner="alice",
):
    return {
        "id": upload_id,
        "name": name,
        "mime": mime,
        "bytes": body,
        "owner": owner,
    }


def test_selected_markdown_read_is_owner_scoped_bounded_and_nosniff(tmp_path):
    upload_id = "1" * 32 + ".md"
    body = b"# Private note\nselected content"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="private-note.md",
                mime="text/markdown",
                body=body,
            )
        ],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content"
    )

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["x-odysseus-content-schema"] == (
        "odysseus.universal_inbox.source_content.v1"
    )
    assert response.headers["x-odysseus-content-state"] == "complete"
    assert response.headers["x-odysseus-magic-diagnostic"] == "text_or_unknown"
    assert response.headers["x-odysseus-mime-diagnostic"] == "match"
    assert 'inline; filename="private-note.md"' in response.headers[
        "content-disposition"
    ]
    assert str(tmp_path) not in json.dumps(dict(response.headers))


def test_selected_pdf_supports_single_bounded_range(tmp_path):
    upload_id = "2" * 32 + ".pdf"
    body = b"%PDF-1.7\n0123456789"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="report.pdf",
                mime="application/pdf",
                body=body,
            )
        ],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content",
        headers={"Range": "bytes=5-9"},
    )

    assert response.status_code == 206
    assert response.content == body[5:10]
    assert response.headers["content-range"] == f"bytes 5-9/{len(body)}"
    assert response.headers["x-odysseus-content-state"] == "partial"
    assert response.headers["content-type"] == "application/pdf"


def test_docx_is_attachment_and_uses_server_media_type(tmp_path):
    upload_id = "3" * 32 + ".docx"
    body = b"PK\x03\x04fake docx container"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="draft contract.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                body=body,
            )
        ],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content"
    )

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="draft_contract.docx"'
    )


def test_content_read_denies_foreign_owner_and_anonymous_without_leak(tmp_path):
    upload_id = "4" * 32 + ".txt"
    body = b"bob private content"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="bob-secret.txt",
                mime="text/plain",
                body=body,
                owner="bob",
            )
        ],
    )

    foreign = TestClient(_app(handler, user="alice")).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content"
    )
    anonymous = TestClient(_app(handler, user=None)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content"
    )

    assert foreign.status_code == 404
    assert anonymous.status_code == 403
    for response in (foreign, anonymous):
        payload = response.json()
        encoded = json.dumps(payload)
        assert payload["content_included"] is False
        assert payload["source_ref_visible"] is False
        assert body.decode() not in encoded
        assert upload_id not in encoded
        assert str(tmp_path) not in encoded


def test_content_read_allows_explicit_admin_owner_override(tmp_path):
    upload_id = "5" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="bob-note.txt",
                mime="text/plain",
                body=b"admin authorized read",
                owner="bob",
            )
        ],
    )

    response = TestClient(
        _app(handler, user="admin", admins={"admin"})
    ).get(f"/api/universal-inbox/items/upload:{upload_id}/content")

    assert response.status_code == 200
    assert response.content == b"admin authorized read"


def test_content_read_rejects_traversal_and_non_upload_sources(tmp_path):
    handler = _make_handler(tmp_path, [])
    client = TestClient(_app(handler))

    traversal = client.get(
        "/api/universal-inbox/items/upload:../secret/content"
    )
    nextcloud = client.get(
        "/api/universal-inbox/items/nextcloud:opaque-id/content"
    )

    assert traversal.status_code == 400
    assert traversal.json()["state"] == "invalid"
    assert nextcloud.status_code == 415
    assert nextcloud.json()["state"] == "unsupported"
    assert nextcloud.json()["reason_code"] == "unsupported_source_kind"


@pytest.mark.parametrize(
    ("upload_id", "name", "mime", "body", "state", "reason"),
    [
        (
            "6" * 32 + ".exe",
            "danger.exe",
            "application/x-msdownload",
            b"MZ\x90\x00",
            "blocked",
            "dangerous_source_type",
        ),
        (
            "7" * 32 + ".pdf",
            "claimed.pdf",
            "application/pdf",
            b"PK\x03\x04not a pdf",
            "mime_mismatch",
            "filename_magic_mismatch",
        ),
        (
            "8" * 32 + ".pdf",
            "locked.pdf",
            "application/pdf",
            b"%PDF-1.7\nbody\n/Encrypt trailer",
            "password_required",
            "encrypted_pdf",
        ),
        (
            "9" * 32 + ".docx",
            "locked.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"\xd0\xcf\x11\xe0encrypted office",
            "password_required",
            "encrypted_office_container",
        ),
    ],
)
def test_content_read_surfaces_danger_mismatch_and_password_states(
    tmp_path,
    upload_id,
    name,
    mime,
    body,
    state,
    reason,
):
    handler = _make_handler(
        tmp_path,
        [_row(upload_id, name=name, mime=mime, body=body)],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content"
    )

    assert response.status_code in {415, 422}
    assert response.json()["state"] == state
    assert response.json()["reason_code"] == reason
    assert response.json()["content_included"] is False
    assert body not in response.content


def test_core_read_rejects_oversized_source_without_returning_content(tmp_path):
    upload_id = "a" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="large.txt",
                mime="text/plain",
                body=b"12345",
            )
        ],
    )

    with pytest.raises(UniversalInboxSourceAccessError) as exc:
        read_selected_universal_inbox_source(
            handler,
            f"upload:{upload_id}",
            owner="alice",
            auth_manager=_AuthManager(),
            max_source_bytes=4,
        )

    assert exc.value.status_code == 413
    assert exc.value.to_dict()["state"] == "oversized"
    assert exc.value.to_dict()["content_included"] is False


def test_core_read_truncates_full_response_at_explicit_byte_limit(tmp_path):
    upload_id = "b" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="bounded.txt",
                mime="text/plain",
                body=b"0123456789",
            )
        ],
    )

    content = read_selected_universal_inbox_source(
        handler,
        f"upload:{upload_id}",
        owner="alice",
        auth_manager=_AuthManager(),
        max_response_bytes=4,
    )

    assert content.status_code == 206
    assert content.body == b"0123"
    assert content.state == "truncated"
    assert content.headers()["Content-Range"] == "bytes 0-3/10"
    assert content.headers()["X-Odysseus-Content-Truncated"] == "true"


@pytest.mark.parametrize("range_header", ["bytes=99-100", "bytes=5-1", "bytes=0-1,3-4"])
def test_invalid_or_multiple_ranges_fail_with_content_free_416(
    tmp_path,
    range_header,
):
    upload_id = "c" * 32 + ".pdf"
    body = b"%PDF-1.7\n012345"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="range.pdf",
                mime="application/pdf",
                body=body,
            )
        ],
    )

    response = TestClient(_app(handler)).get(
        f"/api/universal-inbox/items/upload:{upload_id}/content",
        headers={"Range": range_header},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(body)}"
    assert response.json()["state"] == "range_not_satisfiable"
    assert response.json()["content_included"] is False
