import hashlib
import json
import sqlite3
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion
from routes import universal_inbox_routes
from routes.universal_inbox_routes import setup_universal_inbox_routes
from src.upload_handler import UploadHandler
import src.universal_inbox_working_copy as universal_inbox_working_copy


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


def _auth(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password-123", is_admin=True)
    assert manager.create_user("alice", "password-123")
    assert manager.create_user("bob", "password-123")
    return manager


def _client(monkeypatch, handler, sessions, manager, *, user="alice"):
    monkeypatch.setattr(universal_inbox_routes, "SessionLocal", sessions)
    app = FastAPI()
    app.state.auth_manager = manager

    @app.middleware("http")
    async def _resolve_real_cookie(request, call_next):
        identity = manager.strict_session_identity(
            request.cookies.get("odysseus_session")
        )
        if identity is not None:
            request.state.current_user = identity[0]
        return await call_next(request)

    app.include_router(setup_universal_inbox_routes(handler))
    token = manager.create_session(user, "password-123")
    assert token
    client = TestClient(app)
    client.cookies.set("odysseus_session", token)
    return client


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
    source_ref_hash = "sha256:" + hashlib.sha256(
        f"upload:{upload_id}".encode("utf-8")
    ).hexdigest()
    db = sessions()
    try:
        db.add(Document(
            id="legacy-copy", title="legacy", language="markdown",
            current_content="legacy", version_count=1, is_active=True,
            owner="alice", creator_subject_id=None, source_kind="upload",
            source_ref_hash=source_ref_hash,
        ))
        db.add(DocumentVersion(
            id="legacy-version", document_id="legacy-copy", version_number=1,
            content="legacy", summary="legacy", source="upload",
        ))
        db.commit()
    finally:
        db.close()
    manager = _auth(tmp_path)
    alice_sid = manager.subject_id_for_username("alice")
    alice = _client(monkeypatch, handler, sessions, manager)

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
        source_ref_hash
    )
    assert upload_id not in provenance["source_ref_hash"]

    db = sessions()
    try:
        assert db.query(Document).count() == 2
        assert db.query(DocumentVersion).count() == 2
        bound = db.get(Document, first_payload["id"])
        assert bound.owner == "alice"
        assert bound.creator_subject_id == alice_sid
        assert db.get(Document, "legacy-copy").creator_subject_id is None
    finally:
        db.close()

    assert manager.rename_user("alice", "renamed", "admin")
    renamed = _client(monkeypatch, handler, sessions, manager, user="renamed")
    renamed_cached = renamed.post(_working_copy_url(upload_id), json={})
    assert renamed_cached.status_code == 200
    assert renamed_cached.json()["id"] == first_payload["id"]
    renamed_revision = renamed.post(
        _working_copy_url(upload_id), json={"new_revision": True}
    )
    assert renamed_revision.status_code == 404
    assert renamed_revision.json()["reason_code"] == "source_not_found"

    bob = _client(monkeypatch, handler, sessions, manager, user="bob")
    denied = bob.post(_working_copy_url(upload_id), json={})
    assert denied.status_code == 404
    assert denied.json()["source_ref_visible"] is False
    assert upload_id not in denied.text


def test_invalid_cookie_creates_no_working_copy_or_version(tmp_path, monkeypatch):
    upload_id = "9" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [_row(upload_id, name="denied.txt", mime="text/plain", body=b"private")],
    )
    sessions = _session_factory()
    manager = _auth(tmp_path)
    client = _client(monkeypatch, handler, sessions, manager)
    client.cookies.clear()
    client.cookies.set("odysseus_session", "invalid-cookie")

    response = client.post(_working_copy_url(upload_id), json={})
    assert response.status_code == 401
    db = sessions()
    try:
        assert db.query(Document).count() == 0
        assert db.query(DocumentVersion).count() == 0
    finally:
        db.close()


def test_revoked_cookie_during_extraction_returns_401_without_writes_or_sessions(
    tmp_path, monkeypatch
):
    upload_id = "7" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [_row(upload_id, name="revoked.txt", mime="text/plain", body=b"private")],
    )
    raw_factory = _session_factory()
    active_sessions = set()

    def tracked_factory():
        session = raw_factory()
        active_sessions.add(session)
        original_close = session.close

        def close():
            try:
                return original_close()
            finally:
                active_sessions.discard(session)

        session.close = close
        return session

    manager = _auth(tmp_path)
    client = _client(monkeypatch, handler, tracked_factory, manager)
    token = client.cookies.get("odysseus_session")
    assert token
    original_build = universal_inbox_working_copy._build_working_copy_content

    def revoke_during_extraction(*args, **kwargs):
        assert active_sessions == set()
        manager.revoke_token(token)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(
        universal_inbox_working_copy,
        "_build_working_copy_content",
        revoke_during_extraction,
    )

    response = client.post(_working_copy_url(upload_id), json={})

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert manager.strict_session_identity(token) is None
    assert active_sessions == set()
    db = raw_factory()
    try:
        assert db.query(Document).count() == 0
        assert db.query(DocumentVersion).count() == 0
    finally:
        db.close()


def test_working_copy_extracts_without_open_session_and_serializes_under_fresh_lease(
    tmp_path, monkeypatch
):
    upload_id = "8" * 32 + ".txt"
    handler = _make_handler(
        tmp_path,
        [_row(upload_id, name="fresh.txt", mime="text/plain", body=b"fresh content")],
    )
    raw_factory = _session_factory()
    active_sessions = set()

    def tracked_factory():
        session = raw_factory()
        active_sessions.add(session)
        original_close = session.close

        def close():
            try:
                return original_close()
            finally:
                active_sessions.discard(session)

        session.close = close
        return session

    original_build = universal_inbox_working_copy._build_working_copy_content

    def checked_build(*args, **kwargs):
        assert active_sessions == set()
        return original_build(*args, **kwargs)

    monkeypatch.setattr(
        universal_inbox_working_copy, "_build_working_copy_content", checked_build
    )
    original_query = universal_inbox_routes._document_by_id_query
    final_queries = []

    def checked_final_query(db, document_id, *, lease=None, user=None):
        assert lease is not None
        assert db in active_sessions
        final_queries.append(document_id)
        return original_query(db, document_id, lease=lease, user=user)

    monkeypatch.setattr(
        universal_inbox_routes, "_document_by_id_query", checked_final_query
    )
    manager = _auth(tmp_path)
    client = _client(monkeypatch, handler, tracked_factory, manager)

    response = client.post(_working_copy_url(upload_id), json={})

    assert response.status_code == 201
    assert response.json()["current_content"] == "fresh content"
    assert len(final_queries) == 1
    assert active_sessions == set()


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
    manager = _auth(tmp_path)
    client = _client(monkeypatch, handler, sessions, manager)
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
    manager = _auth(tmp_path)

    response = _client(monkeypatch, handler, sessions, manager).post(
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
    manager = _auth(tmp_path)

    response = _client(monkeypatch, handler, sessions, manager).post(
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
    manager = _auth(tmp_path)

    response = _client(monkeypatch, handler, sessions, manager).post(
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
        assert {"source_kind", "source_ref_hash", "creator_subject_id"}.issubset(columns)
        assert "ux_documents_universal_inbox_source" not in indexes
        assert indexes["ux_documents_universal_inbox_subject_source"] == 1
        assert indexes["ux_documents_universal_inbox_legacy_source"] == 1
    finally:
        conn.close()
