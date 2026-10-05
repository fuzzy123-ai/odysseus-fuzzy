"""Created Office/PDF projections preserve real browser subject authority."""

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion, Session
from src.document_subject_scope import capture_document_request_authority
import src.database as database
import src.document_processor as processor
from src.office_doc import create_office_document
from src.pdf_form_doc import create_plain_pdf_document


def _real_database_exports(monkeypatch):
    # The shared conftest installs a minimal src.database shim.  Bind the exact
    # canonical SQL models so these cases exercise real creation and readback.
    for name, model in [("Document", Document), ("DocumentVersion", DocumentVersion), ("Session", Session)]:
        monkeypatch.setattr(database, name, model, raising=False)


def _authority(manager, token):
    app = FastAPI()
    app.state.auth_manager = manager
    request = Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/",
        "query_string": b"", "headers": [(b"cookie", ("odysseus_session=" + token).encode("ascii"))],
        "client": ("127.0.0.1", 40000), "server": ("testserver", 80), "app": app,
    })
    request.state.api_token = False
    return capture_document_request_authority(request)


@pytest.mark.parametrize("creator", [create_office_document, create_plain_pdf_document])
def test_actual_created_projection_is_sid_scoped_detached_and_rechecks_revocation(tmp_path, monkeypatch, creator):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    _real_database_exports(monkeypatch)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    reads = []
    def local_db():
        reads.append(True)
        return factory()
    monkeypatch.setattr(database, "SessionLocal", local_db)
    import src.agent_tools.document_tools as tools
    monkeypatch.setattr(tools, "set_active_document", lambda _doc_id: None)
    manager = AuthManager(str(tmp_path / "projection-auth.json"))
    assert manager.create_user("alice", "projection-password-123", is_admin=True)
    assert manager.create_user("bob", "projection-password-123")
    alice_token = manager.create_session("alice", "projection-password-123")
    bob_token = manager.create_session("bob", "projection-password-123")
    alice = _authority(manager, alice_token)
    bob = _authority(manager, bob_token)
    with factory() as db:
        db.add(Session(id="alice-session", owner="alice", name="projection", endpoint_url="http://127.0.0.1:9999", model="offline-model"))
        db.commit()
    doc_id = creator("alice-session", "fixture-upload", "Created document", "owned document body", document_authority=alice)
    assert isinstance(doc_id, str)
    with factory() as db:
        row = db.query(Document).filter(Document.id == doc_id).one()
        assert row.creator_subject_id == manager.strict_session_identity(alice_token)[1]
    projection = processor._load_created_document_projection(doc_id, owner="alice", document_authority=alice)
    assert set(projection) == {"doc_id", "title", "language", "content", "version"}
    assert "owned document body" in projection["content"]
    assert processor._load_created_document_projection(doc_id, owner="bob", document_authority=bob) is None
    assert processor._load_created_document_projection(doc_id, owner="alice") is None
    with factory() as db:
        db.query(Document).filter(Document.id == doc_id).update({"current_content": "later body"})
        db.commit()
    assert "owned document body" in projection["content"]
    assert manager.revoke_user_sessions("alice") >= 1
    reads.clear()
    with pytest.raises(HTTPException) as denied:
        processor._load_created_document_projection(doc_id, owner="alice", document_authority=alice)
    assert denied.value.status_code == 401
    assert reads == []
    engine.dispose()


def test_legacy_projection_excludes_subject_rows_and_other_owner(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    _real_database_exports(monkeypatch)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as db:
        db.add_all([
            Document(id="legacy-alice", owner="alice", current_content="alice legacy"),
            Document(id="legacy-bob", owner="bob", current_content="bob legacy"),
            Document(id="subject-alice", owner="alice", creator_subject_id="owner_" + "a" * 32, current_content="subject body"),
        ])
        db.commit()
    assert processor._load_created_document_projection("legacy-alice", owner="alice")["content"] == "alice legacy"
    assert processor._load_created_document_projection("legacy-bob", owner="alice") is None
    assert processor._load_created_document_projection("subject-alice", owner="alice") is None
    engine.dispose()
