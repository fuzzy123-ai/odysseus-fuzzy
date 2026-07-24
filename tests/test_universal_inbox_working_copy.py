import hashlib
import json
import sqlite3
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, Document, DocumentVersion
from routes import universal_inbox_routes
from routes.universal_inbox_routes import setup_universal_inbox_routes
from src.upload_handler import UploadHandler


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins

    def get_privileges(self, _user):
        return {"can_use_documents": True}


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_handler(tmp_path, rows):
    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "07" / "23"
    dated.mkdir(parents=True)
    index = {}
    for row in rows:
        path = dated / row["id"]
        path.write_bytes(row["bytes"])
        stored = {
            "id": row["id"],
            "path": str(path),
            "mime": row["mime"],
            "size": path.stat().st_size,
            "name": row["name"],
            "original_name": row["name"],
            "uploaded_at": "2026-07-23T12:00:00",
            "hash": row["id"],
            "owner": row.get("owner", "alice"),
        }
        index[f"{stored['owner']}:{stored['hash']}"] = stored
    (upload_dir / "uploads.json").write_text(
        json.dumps(index, sort_keys=True),
        encoding="utf-8",
    )
    return UploadHandler(str(tmp_path), str(upload_dir))


def _app(monkeypatch, handler, sessions, *, user="alice", admins=()):
    monkeypatch.setattr(universal_inbox_routes, "SessionLocal", sessions)
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_universal_inbox_routes(handler))
    return app


def _row(upload_id, *, name, mime, body, owner="alice"):
    return {
        "id": upload_id,
        "name": name,
        "mime": mime,
        "bytes": body,
        "owner": owner,
    }


def _working_copy_url(upload_id):
    return f"/api/universal-inbox/items/upload:{upload_id}/working-copy"


def test_markdown_working_copy_is_owner_scoped_idempotent_and_version_one(
    tmp_path,
    monkeypatch,
):
    upload_id = "1" * 32 + ".md"
    body = b"# Private note\r\nKeep the original line endings.\r\n"
    handler = _make_handler(
        tmp_path,
        [_row(upload_id, name="private-note.md", mime="text/markdown", body=body)],
    )
    sessions = _session_factory()
    alice = TestClient(_app(monkeypatch, handler, sessions))

    first = alice.post(_working_copy_url(upload_id), json={})
    repeated = alice.post(_working_copy_url(upload_id), json={})

    assert first.status_code == 201
    assert repeated.status_code == 200
    first_payload = first.json()
    repeated_payload = repeated.json()
    assert first_payload["id"] == repeated_payload["id"]
    assert first_payload["current_content"] == body.decode("utf-8")
    assert first_payload["language"] == "markdown"
    assert first_payload["version_count"] == 1
    assert first_payload["working_copy"]["created"] is True
    assert repeated_payload["working_copy"]["created"] is False
    provenance = first_payload["working_copy_provenance"]
    assert provenance["working_copy_id"] == first_payload["id"]
    assert provenance["source_kind"] == "upload"
    assert provenance["source_ref_hash"] == (
        "sha256:"
        + hashlib.sha256(f"upload:{upload_id}".encode("utf-8")).hexdigest()
    )
    assert upload_id not in provenance["source_ref_hash"]

    db = sessions()
    try:
        assert db.query(Document).count() == 1
        assert db.query(DocumentVersion).count() == 1
        assert db.query(Document).one().owner == "alice"
    finally:
        db.close()

    bob = TestClient(_app(monkeypatch, handler, sessions, user="bob"))
    denied = bob.post(_working_copy_url(upload_id), json={})
    assert denied.status_code == 404
    assert denied.json()["source_ref_visible"] is False
    assert upload_id not in denied.text


def test_explicit_revision_updates_same_document_and_cached_copy_survives_source_loss(
    tmp_path,
    monkeypatch,
):
    upload_id = "2" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [_row(upload_id, name="notes.txt", mime="text/plain", body=b"version one")],
    )
    sessions = _session_factory()
    client = TestClient(_app(monkeypatch, handler, sessions))
    url = _working_copy_url(upload_id)

    first = client.post(url, json={})
    source_path = next(
        path for path in (tmp_path / "uploads").rglob(upload_id) if path.is_file()
    )
    source_path.write_bytes(b"version two")
    revised = client.post(url, json={"new_revision": True})

    assert revised.status_code == 200
    assert revised.json()["id"] == first.json()["id"]
    assert revised.json()["current_content"] == "version two"
    assert revised.json()["version_count"] == 2
    assert revised.json()["working_copy"]["revision_created"] is True

    source_path.unlink()
    cached = client.post(url, json={})
    unavailable_revision = client.post(url, json={"new_revision": True})

    assert cached.status_code == 200
    assert cached.json()["id"] == first.json()["id"]
    assert cached.json()["version_count"] == 2
    assert unavailable_revision.status_code == 404
    assert unavailable_revision.json()["reason_code"] == "source_not_found"

    db = sessions()
    try:
        assert db.query(Document).count() == 1
        assert [
            row.version_number
            for row in db.query(DocumentVersion)
            .order_by(DocumentVersion.version_number)
            .all()
        ] == [1, 2]
    finally:
        db.close()


def test_docx_becomes_markdown_without_mutating_source_bytes_or_metadata(
    tmp_path,
    monkeypatch,
):
    upload_id = "3" * 32 + ".docx"
    source = tmp_path / "source.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    original_bytes = source.read_bytes()
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="proposal.docx",
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                body=original_bytes,
            )
        ],
    )
    stored_path = next(
        path for path in (tmp_path / "uploads").rglob(upload_id) if path.is_file()
    )
    metadata_path = tmp_path / "uploads" / "uploads.json"
    metadata_before = metadata_path.read_bytes()
    sessions = _session_factory()

    response = TestClient(_app(monkeypatch, handler, sessions)).post(
        _working_copy_url(upload_id),
        json={},
    )

    assert response.status_code == 201
    assert response.json()["language"] == "markdown"
    assert response.json()["current_content"] == "First paragraph\nSecond paragraph"
    assert response.json()["version_count"] == 1
    assert stored_path.read_bytes() == original_bytes
    assert metadata_path.read_bytes() == metadata_before


def test_pdf_uses_existing_pdf_markdown_path_without_mutating_original(
    tmp_path,
    monkeypatch,
):
    upload_id = "4" * 32 + ".pdf"
    original = b"%PDF-1.7\nminimal local fixture"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="report.pdf",
                mime="application/pdf",
                body=original,
            )
        ],
    )
    stored_path = next(
        path for path in (tmp_path / "uploads").rglob(upload_id) if path.is_file()
    )
    sessions = _session_factory()

    response = TestClient(_app(monkeypatch, handler, sessions)).post(
        _working_copy_url(upload_id),
        json={},
    )

    assert response.status_code == 201
    assert response.json()["language"] == "markdown"
    assert (
        f'<!-- pdf_source upload_id="{upload_id}" -->'
        in response.json()["current_content"]
    )
    assert stored_path.read_bytes() == original


def test_unsupported_source_is_rejected_without_document_or_content_leak(
    tmp_path,
    monkeypatch,
):
    upload_id = "5" * 32 + ".png"
    handler = _make_handler(
        tmp_path,
        [
            _row(
                upload_id,
                name="image.png",
                mime="image/png",
                body=b"\x89PNG\r\n\x1a\nfixture",
            )
        ],
    )
    sessions = _session_factory()

    response = TestClient(_app(monkeypatch, handler, sessions)).post(
        _working_copy_url(upload_id),
        json={},
    )

    assert response.status_code == 415
    assert response.json()["reason_code"] == "source_format_not_supported"
    assert response.json()["content_included"] is False
    assert upload_id not in response.text
    db = sessions()
    try:
        assert db.query(Document).count() == 0
    finally:
        db.close()


def test_working_copy_migration_adds_columns_and_unique_index_idempotently(
    tmp_path,
    monkeypatch,
):
    from core import database as database
    from core import database_migrations

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE documents ("
            "id VARCHAR PRIMARY KEY, owner VARCHAR, title VARCHAR NOT NULL"
            ")"
        )
        conn.commit()
    finally:
        conn.close()

    migration_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(database, "engine", migration_engine)

    database_migrations._migrate_add_universal_inbox_working_copy_cols()
    database_migrations._migrate_add_universal_inbox_working_copy_cols()

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        indexes = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA index_list(documents)").fetchall()
        }
        assert {"source_kind", "source_ref_hash"}.issubset(columns)
        assert indexes["ux_documents_universal_inbox_source"] == 1
    finally:
        conn.close()
