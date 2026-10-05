import email
import types

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion, Session
from routes.email_attachment_helpers import (
    attachment_as_document_response,
    attachment_basename,
    create_markdown_attachment_document,
    create_pdf_attachment_document,
)
from src.document_subject_scope import (
    DocumentRequestAuthority,
    capture_document_request_authority,
)
from src.office_doc import create_office_document
from src.pdf_form_doc import create_form_markdown_document, create_plain_pdf_document


class DummyRequest:
    pass


def _request(*, manager=None, token=None) -> Request:
    app = FastAPI()
    if manager is not None:
        app.state.auth_manager = manager
    headers = []
    if token is not None:
        headers.append((b"cookie", f"odysseus_session={token}".encode("ascii")))
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 43100),
            "server": ("testserver", 80),
            "app": app,
        }
    )
    request.state.api_token = False
    return request


def _browser_authority(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password-123", is_admin=True)
    assert manager.create_user("alice", "password-123")
    token = manager.create_session("alice", "password-123")
    assert token
    identity = manager.strict_session_identity(token)
    assert identity is not None
    authority = capture_document_request_authority(
        _request(manager=manager, token=token)
    )
    return manager, token, identity[1], authority


@pytest.fixture
def document_database(tmp_path, monkeypatch):
    import src.database as src_db
    import src.agent_tools.document_tools as document_tools

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'documents.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(src_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "Document", Document, raising=False)
    monkeypatch.setattr(src_db, "DocumentVersion", DocumentVersion, raising=False)
    monkeypatch.setattr(src_db, "Session", Session, raising=False)
    monkeypatch.setattr(document_tools, "set_active_document", lambda _doc_id: None)
    yield factory
    engine.dispose()


def _add_session(factory, session_id: str, owner: str | None) -> None:
    with factory() as db:
        db.add(
            Session(
                id=session_id,
                name=session_id,
                endpoint_url="http://model.invalid",
                model="test-model",
                owner=owner,
            )
        )
        db.commit()


def _assert_documents(factory, document_ids, *, owner, subject_id) -> None:
    with factory() as db:
        documents = (
            db.query(Document)
            .filter(Document.id.in_(document_ids))
            .order_by(Document.id)
            .all()
        )
        assert len(documents) == len(document_ids)
        for document in documents:
            assert document.owner == owner
            assert document.creator_subject_id == subject_id
            assert document.version_count == 1
            versions = (
                db.query(DocumentVersion)
                .filter(DocumentVersion.document_id == document.id)
                .all()
            )
            assert len(versions) == 1
            assert versions[0].version_number == 1
            assert versions[0].content == document.current_content


def _create_factory_documents(session_id, *, authority, owner=None):
    return [
        create_plain_pdf_document(
            session_id,
            "plain.pdf",
            "Plain PDF",
            "plain body",
            document_authority=authority,
        ),
        create_form_markdown_document(
            session_id,
            [{"name": "field", "type": "text", "page": 1, "value": "answer"}],
            "form.pdf",
            "Form PDF",
            document_authority=authority,
        ),
        create_office_document(
            session_id,
            "office.docx",
            "Office",
            "office body",
            document_authority=authority,
        ),
        create_markdown_attachment_document(
            title="Email attachment",
            content="email body",
            session_id=session_id,
            summary="Imported from email",
            document_authority=authority,
            owner=owner,
            source_email={
                "uid": "7",
                "folder": "INBOX",
                "account_id": "acct",
                "message_id": "message-1",
            },
        ),
    ]


def _msg():
    return email.message_from_bytes(
        b"From: Sender <sender@example.com>\r\n"
        b"Message-ID: <m1@example.com>\r\n"
        b"\r\n"
    )


def test_attachment_basename_rejects_hidden_files(tmp_path):
    path = tmp_path / ".secret.txt"
    path.write_text("secret", encoding="utf-8")

    assert attachment_basename(path) == {"error": "Invalid filename", "filename": ".secret.txt"}


def test_attachment_basename_returns_base_extension_and_title(tmp_path):
    path = tmp_path / "invoice.final.pdf"
    path.write_bytes(b"%PDF")

    assert attachment_basename(path) == ("invoice.final.pdf", ".pdf", "invoice.final")


def test_attachment_as_document_response_rejects_unsupported_type_before_db(tmp_path):
    path = tmp_path / "payload.exe"
    path.write_bytes(b"nope")

    result = attachment_as_document_response(
        path,
        _msg(),
        uid="7",
        folder="INBOX",
        account_id="acct",
        request=DummyRequest(),
    )

    assert result == {"error": "Unsupported attachment type: .exe", "filename": "payload.exe"}


def test_attachment_as_document_response_rejects_hidden_file_before_db(tmp_path):
    path = tmp_path / ".payload.txt"
    path.write_text("hidden", encoding="utf-8")

    result = attachment_as_document_response(
        path,
        _msg(),
        uid="7",
        folder="INBOX",
        account_id="acct",
        request=DummyRequest(),
    )

    assert result == {"error": "Invalid filename", "filename": ".payload.txt"}


def test_shared_factories_stamp_browser_subject_before_insert(
    tmp_path, document_database
):
    factory = document_database
    _add_session(factory, "browser-session", "alice")
    _, _, subject_id, authority = _browser_authority(tmp_path)

    document_ids = _create_factory_documents(
        "browser-session", authority=authority, owner="forged-owner"
    )

    assert all(document_ids)
    _assert_documents(
        factory, document_ids, owner="alice", subject_id=subject_id
    )


def test_shared_factories_preserve_legacy_null_sid_and_existing_owners(
    document_database,
):
    factory = document_database
    _add_session(factory, "legacy-session", "legacy-owner")

    document_ids = _create_factory_documents(
        "legacy-session", authority=None, owner="legacy-owner"
    )

    assert all(document_ids)
    _assert_documents(
        factory, document_ids, owner="legacy-owner", subject_id=None
    )


def test_shared_factories_preserve_explicit_local_owner(
    document_database, monkeypatch
):
    factory = document_database
    _add_session(factory, "local-session", "local")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    authority = capture_document_request_authority(_request())

    document_ids = _create_factory_documents(
        "local-session", authority=authority, owner="ignored-owner"
    )

    assert all(document_ids)
    _assert_documents(factory, document_ids, owner="local", subject_id=None)


def test_shared_factories_reject_invalid_or_revoked_authority_without_rows(
    tmp_path, document_database, monkeypatch
):
    import src.document_subject_scope as document_subject_scope

    factory = document_database
    _add_session(factory, "revoked-session", "alice")
    manager, token, _, authority = _browser_authority(tmp_path)

    calls = (
        lambda candidate: create_plain_pdf_document(
            "revoked-session",
            "plain.pdf",
            "Plain",
            document_authority=candidate,
        ),
        lambda candidate: create_form_markdown_document(
            "revoked-session",
            [{"name": "field", "type": "text", "page": 1}],
            "form.pdf",
            "Form",
            document_authority=candidate,
        ),
        lambda candidate: create_office_document(
            "revoked-session",
            "office.docx",
            "Office",
            "office body",
            document_authority=candidate,
        ),
        lambda candidate: create_markdown_attachment_document(
            title="Email",
            content="email body",
            session_id="revoked-session",
            summary="email",
            document_authority=candidate,
            owner="alice",
        ),
    )

    def deny_stamp(*_args, **_kwargs):
        raise HTTPException(status_code=401, detail="Not authenticated")

    with monkeypatch.context() as scoped:
        scoped.setattr(document_subject_scope, "stamp_new_document", deny_stamp)
        for call in calls:
            with pytest.raises(HTTPException) as captured:
                call(authority)
            assert captured.value.status_code == 401
            assert captured.value.detail == "Not authenticated"

    manager.revoke_token(token)
    for call in calls:
        with pytest.raises(HTTPException) as captured:
            call(authority)
        assert captured.value.status_code == 401
        assert captured.value.detail == "Not authenticated"
    with pytest.raises(HTTPException) as captured:
        calls[-1](object.__new__(DocumentRequestAuthority))
    assert captured.value.status_code == 401
    assert captured.value.detail == "Not authenticated"

    with factory() as db:
        assert db.query(Document).count() == 0
        assert db.query(DocumentVersion).count() == 0


def test_email_pdf_delegates_same_authority_without_outer_stamp(
    tmp_path, monkeypatch
):
    import src.constants as constants
    import src.document_subject_scope as document_subject_scope
    import src.pdf_form_doc as pdf_form_doc
    import src.pdf_forms as pdf_forms

    _, _, _, authority = _browser_authority(tmp_path)
    source_email = {"uid": "7", "folder": "INBOX"}
    source = tmp_path / "attachment.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    calls = []

    def create_plain(*, document_authority, source_email, **kwargs):
        calls.append(
            types.SimpleNamespace(
                authority=document_authority,
                source_email=source_email,
                kwargs=kwargs,
            )
        )
        return "document-1"

    monkeypatch.setattr(constants, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(pdf_forms, "has_form_fields", lambda _path: False)
    monkeypatch.setattr(pdf_form_doc, "create_plain_pdf_document", create_plain)
    monkeypatch.setattr(
        document_subject_scope,
        "stamp_new_document",
        lambda *_args, **_kwargs: pytest.fail("outer email PDF stamp attempted"),
    )

    result = create_pdf_attachment_document(
        source,
        title="Attachment",
        session_id="session-1",
        document_authority=authority,
        source_email=source_email,
    )

    assert result == "document-1"
    assert len(calls) == 1
    assert calls[0].authority is authority
    assert calls[0].source_email is source_email
