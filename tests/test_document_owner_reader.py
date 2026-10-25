import copy

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session as SqlAlchemySession
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion
from src.document_owner_reader import (
    DOCUMENT_OWNER_READER_SCHEMA,
    DocumentOwnerDescriptor,
    DocumentOwnerReader,
    DocumentOwnerReaderError,
)
from src.document_subject_scope import capture_document_request_authority


def _request(manager: AuthManager, token: str) -> Request:
    app = FastAPI()
    app.state.auth_manager = manager
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"cookie", f"odysseus_session={token}".encode("ascii"))],
        "client": ("127.0.0.1", 43200),
        "server": ("testserver", 80),
        "app": app,
    }
    request = Request(scope)
    request.state.api_token = False
    return request


def _local_request() -> Request:
    app = FastAPI()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 43200),
        "server": ("testserver", 80),
        "app": app,
    }
    request = Request(scope)
    request.state.api_token = False
    return request


def _authority(tmp_path, username="alice"):
    manager = AuthManager(str(tmp_path / f"{username}-auth.json"))
    assert manager.create_user("admin", "password-123", is_admin=True)
    if username != "admin":
        assert manager.create_user(username, "password-123")
    token = manager.create_session(username, "password-123")
    assert token
    identity = manager.strict_session_identity(token)
    assert identity
    return manager, token, identity[1], capture_document_request_authority(_request(manager, token))


def _db(tmp_path, name="reader.db"):
    engine = create_engine(
        f"sqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, factory


def _seed(factory, *, doc_id, owner, subject, content="content", version=1, active=True, archived=False):
    with factory() as db:
        doc = Document(
            id=doc_id,
            session_id=None,
            title=f"title-{doc_id}",
            language="markdown",
            current_content=content,
            version_count=version,
            is_active=active,
            archived=archived,
            owner=owner,
            creator_subject_id=subject,
        )
        db.add(doc)
        for number in range(1, version + 1):
            db.add(
                DocumentVersion(
                    id=f"{doc_id}-v{number}",
                    document_id=doc_id,
                    version_number=number,
                    content=content if number == version else f"older-{number}",
                    summary="version",
                    source="user",
                )
            )
        db.commit()


def _code(captured):
    return captured.value.code


def test_reader_requires_real_pinned_sessionmaker_and_engine(tmp_path):
    _, _, _, authority = _authority(tmp_path)
    engine, factory = _db(tmp_path)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    assert repr(reader) == "DocumentOwnerReader(<redacted>)"

    with pytest.raises(DocumentOwnerReaderError) as captured:
        DocumentOwnerReader(authority=authority, session_factory=lambda: None)
    assert _code(captured) == "invalid_request"
    with pytest.raises(DocumentOwnerReaderError):
        DocumentOwnerReader(authority=authority, session_factory=factory())
    with pytest.raises(DocumentOwnerReaderError):
        DocumentOwnerReader(authority=authority, session_factory=sessionmaker())

    class CustomSession(SqlAlchemySession):
        pass

    custom = sessionmaker(bind=engine, class_=CustomSession, autocommit=False, autoflush=False)
    with pytest.raises(DocumentOwnerReaderError) as captured:
        DocumentOwnerReader(authority=authority, session_factory=custom)
    assert _code(captured) == "invalid_request"
    engine.dispose()


def test_capture_is_sid_only_complete_and_identity_redacted(tmp_path):
    engine, factory = _db(tmp_path)
    _, token, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid, content="owned-secret")
    _seed(factory, doc_id="legacy", owner="alice", subject=None, content="legacy-secret")
    _seed(factory, doc_id="foreign-same-alias", owner="alice", subject="owner_" + "b" * 32, content="foreign-secret")

    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    snapshot = reader.capture()
    assert snapshot.schema_id == DOCUMENT_OWNER_READER_SCHEMA
    assert len(snapshot.descriptors) == 1
    descriptor = snapshot.descriptors[0]
    assert descriptor.title == "title-owned"
    assert descriptor.source_ref.startswith("document:sha256:")
    assert descriptor.revision_ref.startswith("sha256:")
    assert descriptor.content_hash.startswith("sha256:")
    assert "owned" not in descriptor.source_ref
    representations = repr(snapshot) + repr(descriptor) + repr(reader)
    for private in (sid, token, "alice", "owned-secret", "legacy-secret", "foreign-secret"):
        assert private not in representations
    assert not hasattr(descriptor, "creator_subject_id")
    assert not hasattr(descriptor, "document_id")
    assert not hasattr(descriptor, "session_id")
    engine.dispose()


def test_exact_read_revalidates_latest_version_content_and_state(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid, content="current", version=2)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    descriptor = reader.capture().descriptors[0]
    exact = reader.read_exact(descriptor)
    assert exact.content == "current"
    assert exact.source_ref == descriptor.source_ref
    assert exact.revision_ref == descriptor.revision_ref
    assert repr(exact) == "DocumentExactRead(<redacted>)"

    with factory() as db:
        row = db.query(Document).filter(Document.id == "owned").one()
        row.archived = True
        db.commit()
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(descriptor)
    assert _code(captured) == "changed"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(descriptor)
    assert _code(captured) == "authority_unavailable"
    engine.dispose()


@pytest.mark.parametrize("mode", ["missing", "duplicate", "ahead", "content_mismatch"])
def test_capture_rejects_non_exact_latest_version_state(tmp_path, mode):
    engine, factory = _db(tmp_path, f"{mode}.db")
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id=mode, owner="alice", subject=sid, content="current", version=1)
    with factory() as db:
        versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == mode)
        if mode == "missing":
            versions.delete(synchronize_session=False)
        elif mode == "duplicate":
            db.add(DocumentVersion(id=f"{mode}-duplicate", document_id=mode, version_number=1, content="current"))
        elif mode == "ahead":
            db.add(DocumentVersion(id=f"{mode}-ahead", document_id=mode, version_number=2, content="future"))
        else:
            versions.one().content = "different"
        db.commit()
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture()
    assert _code(captured) == "changed"
    engine.dispose()


def test_capture_limit_plus_one_fails_without_partial_snapshot(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="one", owner="alice", subject=sid)
    _seed(factory, doc_id="two", owner="alice", subject=sid)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture(limit=1)
    assert _code(captured) == "limit_exceeded"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture(limit=True)
    assert _code(captured) == "invalid_request"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture(limit=0)
    assert _code(captured) == "invalid_request"
    engine.dispose()


def test_new_capture_invalidates_old_descriptor_and_reconstruction(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    first = reader.capture()
    old = first.descriptors[0]
    copied = copy.copy(old)
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(copied)
    assert _code(captured) == "invalid_descriptor"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(old)
    assert _code(captured) == "authority_unavailable"

    current = reader.capture(expected_snapshot_digest=first.snapshot_digest)
    live = current.descriptors[0]
    rebuilt = DocumentOwnerDescriptor(*(
        live.schema_id,
        live.source_ref,
        live.revision_ref,
        live.title,
        live.language,
        live.version_count,
        live.is_active,
        live.archived,
        live.created_at,
        live.updated_at,
        live.byte_size,
        live.char_size,
        live.content_hash,
        live.descriptor_digest,
    ))
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(rebuilt)
    assert _code(captured) == "invalid_descriptor"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(live)
    assert _code(captured) == "authority_unavailable"
    second = reader.capture(expected_snapshot_digest=first.snapshot_digest)
    assert second.snapshot_digest == first.snapshot_digest
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(old)
    assert _code(captured) == "invalid_descriptor"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(second.descriptors[0])
    assert _code(captured) == "authority_unavailable"
    third = reader.capture(expected_snapshot_digest=first.snapshot_digest)
    assert reader.read_exact(third.descriptors[0]).content == "content"
    engine.dispose()


def test_unrelated_rows_do_not_change_owner_snapshot_digest(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    _seed(factory, doc_id="foreign", owner="bob", subject="owner_" + "b" * 32)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    first = reader.capture()
    with factory() as db:
        row = db.query(Document).filter(Document.id == "foreign").one()
        row.title = "changed-foreign-title"
        row.current_content = "changed-foreign-content"
        db.query(DocumentVersion).filter(DocumentVersion.document_id == "foreign").one().content = row.current_content
        db.commit()
    second = reader.capture(expected_snapshot_digest=first.snapshot_digest)
    assert second.snapshot_digest == first.snapshot_digest
    engine.dispose()


def test_provenance_change_invalidates_exact_read(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    with factory() as db:
        row = db.query(Document).filter(Document.id == "owned").one()
        row.source_kind = "email_attachment"
        row.source_ref_hash = "a" * 64
        db.commit()
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    descriptor = reader.capture().descriptors[0]
    with factory() as db:
        row = db.query(Document).filter(Document.id == "owned").one()
        row.source_ref_hash = "b" * 64
        db.commit()
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(descriptor)
    assert _code(captured) == "changed"
    engine.dispose()


def test_capture_rejects_oversized_metadata_and_snapshot_mismatch(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    snapshot = reader.capture()
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture(expected_snapshot_digest="sha256:" + "0" * 64)
    assert _code(captured) == "changed"
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(snapshot.descriptors[0])
    assert _code(captured) == "authority_unavailable"

    with factory() as db:
        db.query(Document).filter(Document.id == "owned").one().title = "x" * 4_097
        db.commit()
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture()
    assert _code(captured) == "limit_exceeded"
    engine.dispose()


def test_factory_reconfiguration_fails_closed(tmp_path):
    engine, factory = _db(tmp_path)
    other_engine, _ = _db(tmp_path, "other.db")
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    factory.configure(bind=other_engine)
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture()
    assert _code(captured) == "authority_unavailable"
    engine.dispose()
    other_engine.dispose()


def test_local_authority_cannot_capture_trusted_documents(tmp_path, monkeypatch):
    engine, factory = _db(tmp_path)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    authority = capture_document_request_authority(_local_request())
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture()
    assert _code(captured) == "unauthorized"
    engine.dispose()


def test_raw_database_failure_is_detached_content_free_and_unlogged(tmp_path, caplog):
    engine, factory = _db(tmp_path)
    _, token, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE document_versions"))
    caplog.clear()
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.capture()
    assert _code(captured) == "storage_failed"
    assert captured.value.__cause__ is None
    rendered = str(captured.value) + repr(captured.value) + caplog.text
    for private in (token, sid, "document_versions", "OperationalError", "no such table"):
        assert private not in rendered
    engine.dispose()


def test_owned_sessions_close_before_authority_guard_exits(tmp_path):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid)
    checkouts = 0

    def checkout(*_args):
        nonlocal checkouts
        checkouts += 1

    def checkin(*_args):
        nonlocal checkouts
        checkouts -= 1

    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    descriptor = reader.capture().descriptors[0]
    assert checkouts == 0
    assert reader.read_exact(descriptor).content == "content"
    assert checkouts == 0
    engine.dispose()


def test_title_and_content_never_enter_repr_or_fixed_failure(tmp_path, caplog):
    engine, factory = _db(tmp_path)
    _, _, sid, authority = _authority(tmp_path)
    _seed(factory, doc_id="owned", owner="alice", subject=sid, content="private-content-value")
    with factory() as db:
        db.query(Document).filter(Document.id == "owned").one().title = "private-title-value"
        db.commit()
    reader = DocumentOwnerReader(authority=authority, session_factory=factory)
    snapshot = reader.capture()
    descriptor = snapshot.descriptors[0]
    rendered = repr(reader) + repr(snapshot) + repr(descriptor) + caplog.text
    assert "private-title-value" not in rendered
    assert "private-content-value" not in rendered
    with factory() as db:
        db.query(Document).filter(Document.id == "owned").delete()
        db.commit()
    with pytest.raises(DocumentOwnerReaderError) as captured:
        reader.read_exact(descriptor)
    rendered = str(captured.value) + repr(captured.value) + caplog.text
    assert "private-title-value" not in rendered
    assert "private-content-value" not in rendered
    engine.dispose()
