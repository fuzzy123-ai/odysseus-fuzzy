"""Focused owner-scoped browser export coverage (no real source content logs)."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, Document
import routes.document_routes as document_routes
import routes.universal_inbox_routes as inbox_routes
from routes.document_routes import setup_document_routes
from routes.universal_inbox_routes import setup_universal_inbox_routes
from src.upload_handler import UploadHandler


class _AuthManager:
    is_configured = True

    def get_privileges(self, _user):
        return {"can_use_documents": True}

    def is_admin(self, _user):
        return False


def _sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _handler(tmp_path, upload_id, body=b"# private\n"):
    upload_dir = tmp_path / "uploads"
    source_dir = upload_dir / "2026" / "07" / "23"
    source_dir.mkdir(parents=True)
    source = source_dir / upload_id
    source.write_bytes(body)
    info = {
        "id": upload_id,
        "hash": upload_id,
        "owner": "alice",
        "path": str(source),
        "name": "private-note.md",
        "original_name": "private-note.md",
        "mime": "text/markdown",
        "size": len(body),
        "uploaded_at": "2026-07-23T12:00:00",
    }
    (upload_dir / "uploads.json").write_text(
        json.dumps({f"alice:{upload_id}": info}), encoding="utf-8"
    )
    return UploadHandler(str(tmp_path), str(upload_dir))


def _app(monkeypatch, sessions, handler, *, user="alice"):
    monkeypatch.setattr(document_routes, "SessionLocal", sessions)
    monkeypatch.setattr(inbox_routes, "SessionLocal", sessions)
    app = FastAPI()
    app.state.auth_manager = _AuthManager()

    @app.middleware("http")
    async def _identity(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_document_routes(None, handler))
    app.include_router(setup_universal_inbox_routes(handler))
    return app


def _document(sessions, *, owner="alice", content="alpha\n", language="markdown"):
    db = sessions()
    try:
        doc = Document(
            id="d" * 32,
            title="notes.md\r\n",
            language=language,
            current_content=content,
            version_count=7,
            is_active=True,
            owner=owner,
        )
        db.add(doc)
        db.commit()
        return doc.id
    finally:
        db.close()


def test_generic_export_returns_exact_current_bytes_and_safe_attachment_headers(
    tmp_path, monkeypatch
):
    sessions = _sessions()
    doc_id = _document(sessions, content="alpha\r\nβeta\n")
    client = TestClient(_app(monkeypatch, sessions, _handler(tmp_path, "a" * 32 + ".md")))

    response = client.get(f"/api/document/{doc_id}/export")

    assert response.status_code == 200
    assert response.content == "alpha\r\nβeta\n".encode("utf-8")
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert 'filename="notes-v7.md"' in disposition
    assert "filename*=UTF-8''notes-v7.md" in disposition

    db = sessions()
    try:
        assert db.get(Document, doc_id).current_content == "alpha\r\nβeta\n"
    finally:
        db.close()

    assert document_routes._pdf_export_filename("a" * 120) == (
        "a" * 96 + "_annotated.pdf"
    )


def test_generic_export_closes_foreign_owner_and_rejects_oversized_or_pdf_copy(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    sessions = _sessions()
    doc_id = _document(sessions)
    handler = _handler(tmp_path, "b" * 32 + ".md")
    assert TestClient(_app(monkeypatch, sessions, handler, user="bob")).get(
        f"/api/document/{doc_id}/export"
    ).status_code == 404

    db = sessions()
    try:
        db.get(Document, doc_id).current_content = "too-large"
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(document_routes, "_BROWSER_WORKING_COPY_EXPORT_MAX_BYTES", 4)
    client = TestClient(_app(monkeypatch, sessions, handler))
    assert client.get(f"/api/document/{doc_id}/export").status_code == 413
    assert TestClient(_app(monkeypatch, sessions, handler, user=None)).get(
        f"/api/document/{doc_id}/export"
    ).status_code == 403

    db = sessions()
    try:
        db.get(Document, doc_id).current_content = '<!-- pdf_source upload_id="' + "c" * 32 + '.pdf" -->'
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/document/{doc_id}/export").status_code == 409


def test_working_copy_response_has_authoritative_browser_capability_and_download_attachment(
    tmp_path, monkeypatch
):
    sessions = _sessions()
    upload_id = "e" * 32 + ".md"
    client = TestClient(_app(monkeypatch, sessions, _handler(tmp_path, upload_id)))

    created = client.post(f"/api/universal-inbox/items/upload:{upload_id}/working-copy", json={})

    assert created.status_code == 201
    payload = created.json()
    capability = payload["workbench_capability"]
    assert capability["owner_authorized"] is True
    assert capability["has_working_copy"] is True
    assert capability["browser_download_allowed"] is True
    assert capability["live_write_authorized"] is False
    assert capability["source_suffix"] == ".md"
    actions = {item["action"]: item for item in capability["actions"]}
    assert actions["download_original"]["state"] == "allowed"
    assert actions["export_working_copy"]["state"] == "allowed"
    assert all(item["mutates_original"] is False for item in actions.values())
    assert all(item["performs_live_write"] is False for item in actions.values())
    assert upload_id not in json.dumps(capability)

    original = client.get(
        f"/api/universal-inbox/items/upload:{upload_id}/content?download=true"
    )
    assert original.status_code == 200
    assert original.headers["content-disposition"].startswith("attachment;")
    assert original.headers["x-content-type-options"] == "nosniff"

    partial = client.get(
        f"/api/universal-inbox/items/upload:{upload_id}/content?download=true",
        headers={"Range": "bytes=0-1"},
    )
    assert partial.status_code == 409
    assert partial.headers["cache-control"] == "private, no-store"
    assert partial.headers["x-content-type-options"] == "nosniff"
    assert partial.json()["content_included"] is False
    assert partial.json()["reason_code"] == "complete_source_required"

    # Reuse must remain a database-only cached copy; the source can disappear
    # after the first explicit creation without changing the capability.
    next((tmp_path / "uploads").rglob(upload_id)).unlink()
    cached = client.post(f"/api/universal-inbox/items/upload:{upload_id}/working-copy", json={})
    assert cached.status_code == 200
    assert cached.json()["id"] == payload["id"]
    assert cached.json()["workbench_capability"] == capability
