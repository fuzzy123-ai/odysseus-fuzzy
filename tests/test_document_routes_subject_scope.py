from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion, Session as DbSession
import routes.document_routes as document_routes
import src.database as src_db


def _auth(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    assert manager.create_user("bob", "password-123")
    alice_token = manager.create_session("alice", "password-123")
    bob_token = manager.create_session("bob", "password-123")
    assert alice_token and bob_token
    alice_sid = manager.strict_session_identity(alice_token)[1]
    bob_sid = manager.strict_session_identity(bob_token)[1]
    return manager, alice_token, bob_token, alice_sid, bob_sid


def _document(doc_id: str, owner: str, sid: str | None):
    return Document(
        id=doc_id,
        title=doc_id,
        language="markdown",
        current_content=f"content-{doc_id}",
        version_count=1,
        is_active=True,
        archived=False,
        owner=owner,
        creator_subject_id=sid,
    )


def _bind_src_database(monkeypatch, factory) -> None:
    monkeypatch.setattr(src_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "Document", Document, raising=False)
    monkeypatch.setattr(src_db, "DocumentVersion", DocumentVersion, raising=False)
    monkeypatch.setattr(src_db, "Session", DbSession, raising=False)


def _track_route_guards(monkeypatch):
    from contextlib import contextmanager

    original = document_routes.guard_document_request_authority
    state = {"depth": 0}

    @contextmanager
    def tracked(authority):
        with original(authority) as lease:
            state["depth"] += 1
            try:
                yield lease
            finally:
                state["depth"] -= 1

    monkeypatch.setattr(document_routes, "guard_document_request_authority", tracked)
    return state


def _app(tmp_path, monkeypatch, *, upload_handler=None):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'documents.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(document_routes, "SessionLocal", factory)
    _bind_src_database(monkeypatch, factory)
    manager, alice_token, bob_token, alice_sid, bob_sid = _auth(tmp_path)
    db = factory()
    try:
        for doc in (
            _document("alice-bound", "alice", alice_sid),
            _document("bob-bound", "alice", bob_sid),
            _document("alice-legacy", "alice", None),
            _document("bob-legacy", "bob", None),
        ):
            db.add(doc)
            db.add(DocumentVersion(
                id=f"version-{doc.id}", document_id=doc.id, version_number=1,
                content=doc.current_content, summary="initial", source="user",
            ))
        db.commit()
    finally:
        db.close()
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

    app.include_router(document_routes.setup_document_routes(None, upload_handler=upload_handler))
    return app, factory, manager, alice_token, bob_token


def test_real_cookie_routes_scope_same_alias_subjects_and_legacy_rows(tmp_path, monkeypatch):
    app, factory, _, alice_token, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        library = client.get("/api/documents/library")
        assert library.status_code == 200
        assert {row["id"] for row in library.json()["documents"]} == {
            "alice-bound", "alice-legacy"
        }
        assert client.get("/api/document/bob-bound").status_code == 404
        assert client.put(
            "/api/document/bob-bound", json={"content": "forbidden"}
        ).status_code == 404
        updated = client.put(
            "/api/document/alice-bound", json={"content": "allowed"}
        )
        assert updated.status_code == 200
    db = factory()
    try:
        assert db.get(Document, "bob-bound").current_content == "content-bob-bound"
        assert db.get(Document, "alice-bound").current_content == "allowed"
    finally:
        db.close()


def test_invalid_present_cookie_never_falls_back_to_bearer(tmp_path, monkeypatch):
    app, _, _, _, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.clear()
        client.cookies.set("odysseus_session", "invalid-cookie")
        response = client.get(
            "/api/document/alice-legacy",
            headers={"Authorization": "Bearer legacy-token"},
        )
        assert response.status_code == 401


def test_direct_rest_create_stamps_current_sid_and_invalid_cookie_creates_nothing(
    tmp_path, monkeypatch
):
    app, factory, manager, alice_token, _ = _app(tmp_path, monkeypatch)
    alice_sid = manager.strict_session_identity(alice_token)[1]
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        created = client.post(
            "/api/document",
            json={"title": "SID document", "language": "markdown", "content": "body"},
        )
        assert created.status_code == 200
        created_id = created.json()["id"]

        client.cookies.clear()
        client.cookies.set("odysseus_session", "invalid-cookie")
        denied = client.post(
            "/api/document",
            json={"title": "Denied", "language": "markdown", "content": "body"},
        )
        assert denied.status_code == 401

    db = factory()
    try:
        row = db.get(Document, created_id)
        assert row.creator_subject_id == alice_sid
        assert row.owner == "alice"
        assert db.query(DocumentVersion).filter(
            DocumentVersion.document_id == created_id
        ).count() == 1
        assert db.query(Document).filter(Document.title == "Denied").count() == 0
    finally:
        db.close()


def test_pdf_import_passes_same_authority_to_factory_and_reads_bound_result(
    tmp_path, monkeypatch
):
    upload_id = "d" * 32 + ".pdf"
    stored_pdf = tmp_path / upload_id
    stored_pdf.write_bytes(b"%PDF fixture")

    class Uploads:
        def save_upload(self, *_args, **_kwargs):
            return {"id": upload_id, "name": "form.pdf", "original_name": "form.pdf"}

        def resolve_upload(self, *_args, **_kwargs):
            return {"path": str(stored_pdf)}

    app, factory, manager, alice_token, _ = _app(
        tmp_path, monkeypatch, upload_handler=Uploads()
    )
    alice_sid = manager.strict_session_identity(alice_token)[1]
    observed = {"authority": None}

    def create_bound_pdf(*, session_id, upload_id, title, body_text, document_authority):
        from src.document_subject_scope import (
            guard_document_request_authority,
            stamp_new_document,
        )

        observed["authority"] = document_authority
        with guard_document_request_authority(document_authority) as lease:
            db = factory()
            try:
                document = Document(
                    id="imported-pdf", session_id=session_id, title=title,
                    language="markdown", current_content="pdf", version_count=1,
                    is_active=True, owner=None,
                )
                stamp_new_document(document, lease, local_owner="alice")
                db.add(document)
                db.add(DocumentVersion(
                    id="imported-pdf-version", document_id=document.id,
                    version_number=1, content="pdf", summary="initial", source="upload",
                ))
                db.commit()
                return document.id
            finally:
                db.close()

    monkeypatch.setattr("src.pdf_forms.has_form_fields", lambda _path: False)
    monkeypatch.setattr("src.document_processor._process_pdf", lambda *_args, **_kwargs: "body")
    monkeypatch.setattr("src.document_processor.strip_pdf_content_marker", lambda value: value)
    monkeypatch.setattr("src.pdf_form_doc.create_plain_pdf_document", create_bound_pdf)
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        response = client.post(
            "/api/documents/import-pdf",
            files={"file": ("form.pdf", b"%PDF fixture", "application/pdf")},
        )
    assert response.status_code == 200
    assert response.json()["id"] == "imported-pdf"
    assert observed["authority"] is not None
    db = factory()
    try:
        imported = db.get(Document, "imported-pdf")
        assert imported.owner == "alice"
        assert imported.creator_subject_id == alice_sid
    finally:
        db.close()


def test_ai_tidy_revalidates_revision_after_await(tmp_path, monkeypatch):
    app, factory, _, alice_token, _ = _app(tmp_path, monkeypatch)
    guard_state = _track_route_guards(monkeypatch)
    monkeypatch.setattr(
        "src.task_endpoint.resolve_task_endpoint",
        lambda **_: ("http://offline.invalid", "offline", {}),
    )

    async def changed_during_await(*_args, **_kwargs):
        assert guard_state["depth"] == 0
        db = factory()
        try:
            row = db.get(Document, "alice-bound")
            row.version_count += 1
            db.commit()
        finally:
            db.close()
        return '["keep","keep"]'

    monkeypatch.setattr("src.llm_core.llm_call_async", changed_during_await)
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        response = client.post("/api/documents/ai-tidy")
        assert response.status_code == 409


def test_zip_build_runs_without_held_cookie_guard_and_revalidates(tmp_path, monkeypatch):
    import zipfile

    app, _, manager, alice_token, _ = _app(tmp_path, monkeypatch)
    guard_state = _track_route_guards(monkeypatch)
    original = zipfile.ZipFile.writestr
    revoked = False

    def revoke_during_write(self, *args, **kwargs):
        nonlocal revoked
        assert guard_state["depth"] == 0
        if not revoked:
            revoked = True
            manager.revoke_token(alice_token)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", revoke_during_write)
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        response = client.post(
            "/api/documents/export-zip", json={"ids": ["alice-bound"]}
        )
        assert response.status_code == 401
        assert revoked is True


@pytest.mark.parametrize("endpoint", ["render-pdf", "export-pdf"])
def test_pdf_temp_files_are_cleaned_when_final_cookie_guard_fails(
    tmp_path, monkeypatch, endpoint
):
    upload_id = "a" * 32 + ".pdf"
    source_pdf = tmp_path / upload_id
    source_pdf.write_bytes(b"pdf-fixture")

    class Uploads:
        upload_dir = str(tmp_path)

        def resolve_upload(self, *_args, **_kwargs):
            return {"path": str(source_pdf)}

    app, factory, manager, alice_token, _ = _app(
        tmp_path, monkeypatch, upload_handler=Uploads()
    )
    guard_state = _track_route_guards(monkeypatch)
    db = factory()
    try:
        row = db.get(Document, "alice-bound")
        row.current_content = f'<!-- pdf_source upload_id="{upload_id}" -->\n# PDF\n'
        db.commit()
    finally:
        db.close()

    created: list[str] = []

    def fill_and_revoke(_source, destination, _values):
        assert guard_state["depth"] == 0
        created.append(destination)
        with open(destination, "wb") as handle:
            handle.write(b"rendered")
        manager.revoke_token(alice_token)

    monkeypatch.setattr("src.pdf_forms.fill_fields", fill_and_revoke)
    monkeypatch.setattr("src.pdf_runtime.load_pymupdf_for_pdf_viewer", lambda: object())
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        response = client.get(f"/api/document/alice-bound/{endpoint}")
        assert response.status_code == 401
    assert created
    assert all(not __import__("os").path.exists(path) for path in created)


def test_signed_reply_removes_destination_when_final_guard_fails(tmp_path, monkeypatch):
    upload_id = "b" * 32 + ".pdf"
    source_pdf = tmp_path / upload_id
    source_pdf.write_bytes(b"pdf-fixture")
    compose_root = tmp_path / "mail"
    monkeypatch.setattr(document_routes, "MAIL_ATTACHMENTS_DIR", str(compose_root))

    class Uploads:
        upload_dir = str(tmp_path)

        def resolve_upload(self, *_args, **_kwargs):
            return {"path": str(source_pdf)}

    app, factory, manager, alice_token, _ = _app(
        tmp_path, monkeypatch, upload_handler=Uploads()
    )
    guard_state = _track_route_guards(monkeypatch)
    db = factory()
    try:
        row = db.get(Document, "alice-bound")
        row.current_content = f'<!-- pdf_source upload_id="{upload_id}" -->\n# PDF\n'
        row.source_email_uid = "7"
        row.source_email_folder = "INBOX"
        row.source_email_message_id = "message"
        db.commit()
    finally:
        db.close()

    def fill_and_revoke(_source, destination, _values):
        assert guard_state["depth"] == 0
        with open(destination, "wb") as handle:
            handle.write(b"rendered")
        manager.revoke_token(alice_token)

    monkeypatch.setattr("src.pdf_forms.fill_fields", fill_and_revoke)
    monkeypatch.setattr("routes.email_routes._imap", None)
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", alice_token)
        response = client.post("/api/document/alice-bound/prepare-signed-reply")
        assert response.status_code == 401
    compose_dir = compose_root / "_compose"
    assert list(compose_dir.iterdir()) == []


def test_signed_reply_cleans_partial_copy_and_intermediate_file(tmp_path, monkeypatch):
    upload_id = "c" * 32 + ".pdf"
    source_pdf = tmp_path / upload_id
    source_pdf.write_bytes(b"pdf-fixture")
    compose_root = tmp_path / "mail-copy-failure"
    monkeypatch.setattr(document_routes, "MAIL_ATTACHMENTS_DIR", str(compose_root))

    class Uploads:
        upload_dir = str(tmp_path)

        def resolve_upload(self, *_args, **_kwargs):
            return {"path": str(source_pdf)}

    app, factory, _, alice_token, _ = _app(
        tmp_path, monkeypatch, upload_handler=Uploads()
    )
    guard_state = _track_route_guards(monkeypatch)
    db = factory()
    try:
        row = db.get(Document, "alice-bound")
        row.current_content = f'<!-- pdf_source upload_id="{upload_id}" -->\n# PDF\n'
        row.source_email_uid = "7"
        row.source_email_folder = "INBOX"
        db.commit()
    finally:
        db.close()

    intermediates: list[str] = []

    def fill(_source, destination, _values):
        assert guard_state["depth"] == 0
        intermediates.append(destination)
        with open(destination, "wb") as handle:
            handle.write(b"rendered")

    def partial_copy(_source, destination):
        assert guard_state["depth"] == 0
        with open(destination, "wb") as handle:
            handle.write(b"partial")
        raise OSError("bounded fixture copy failure")

    monkeypatch.setattr("src.pdf_forms.fill_fields", fill)
    monkeypatch.setattr("shutil.copyfile", partial_copy)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set("odysseus_session", alice_token)
        response = client.post("/api/document/alice-bound/prepare-signed-reply")
        assert response.status_code == 500
    assert intermediates
    assert all(not __import__("os").path.exists(path) for path in intermediates)
    assert list((compose_root / "_compose").iterdir()) == []
