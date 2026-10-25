import asyncio
import json

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

import core.database as core_db
import src.database as src_db
from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion, Session
from src.agent_tools.document_tools import (
    CreateDocumentTool,
    EditDocumentTool,
    ManageDocumentTool,
    UpdateDocumentTool,
    finalize_document_tool_result,
    get_active_document,
    set_active_document,
)
from src.document_actions import run_document_tidy
from src.document_subject_scope import (
    DocumentRequestAuthority,
    capture_document_request_authority,
)


def _request(manager, token):
    app = FastAPI()
    app.state.auth_manager = manager
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat_stream",
        "raw_path": b"/api/chat_stream",
        "query_string": b"",
        "headers": [(b"cookie", f"odysseus_session={token}".encode("ascii"))],
        "client": ("127.0.0.1", 43100),
        "server": ("testserver", 80),
        "app": app,
    }
    request = Request(scope)
    request.state.api_token = False
    return request


def _local_request():
    app = FastAPI()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat_stream",
        "raw_path": b"/api/chat_stream",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 43100),
        "server": ("testserver", 80),
        "app": app,
    }
    request = Request(scope)
    request.state.api_token = False
    return request


@pytest.fixture
def document_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'chat-documents.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "Document", Document, raising=False)
    monkeypatch.setattr(src_db, "DocumentVersion", DocumentVersion, raising=False)
    monkeypatch.setattr(src_db, "Session", Session, raising=False)
    yield factory
    engine.dispose()


@pytest.fixture
def browser_authority(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password-123", is_admin=True)
    assert manager.create_user("alice", "password-123")
    token = manager.create_session("alice", "password-123")
    identity = manager.strict_session_identity(token)
    assert token and identity is not None
    request = _request(manager, token)
    return manager, request, identity[1], capture_document_request_authority(request)


def _add_document(db, doc_id, owner, subject_id, content="substantial body"):
    db.add(
        Document(
            id=doc_id,
            title=doc_id,
            language="markdown",
            current_content=content,
            version_count=1,
            is_active=True,
            archived=False,
            owner=owner,
            creator_subject_id=subject_id,
        )
    )
    db.add(
        DocumentVersion(
            id=f"version-{doc_id}",
            document_id=doc_id,
            version_number=1,
            content=content,
            summary="initial",
            source="user",
        )
    )


def _seed_partitions(factory, subject_id):
    with factory() as db:
        _add_document(db, "bound-own", "alice", subject_id)
        _add_document(db, "bound-other", "alice", "owner_" + "b" * 32)
        _add_document(db, "legacy-own", "alice", None)
        _add_document(db, "legacy-other", "bob", None)
        db.commit()


def _run(
    tool,
    content,
    *,
    owner="alice",
    authority=None,
    session_id=None,
    doc_id=None,
    document_revision_token=None,
):
    return asyncio.run(
        tool.execute(
            content,
            {
                "owner": owner,
                "session_id": session_id,
                "doc_id": doc_id,
                "document_authority": authority,
                "document_revision_token": document_revision_token,
            },
        )
    )


def test_browser_tools_include_own_sid_and_legacy_alias_only(document_db, browser_authority):
    _, _, subject_id, authority = browser_authority
    _seed_partitions(document_db, subject_id)

    result = _run(ManageDocumentTool(), json.dumps({"action": "list"}), authority=authority)

    assert {item["id"] for item in result["documents"]} == {"legacy-own", "bound-own"}
    rendered = result["response"]
    assert "bound-other" not in rendered and "legacy-other" not in rendered


def test_missing_authority_is_null_sid_only_and_invalid_never_falls_back(
    document_db, browser_authority
):
    _, _, subject_id, _ = browser_authority
    _seed_partitions(document_db, subject_id)

    legacy = _run(ManageDocumentTool(), json.dumps({"action": "list"}), authority=None)
    assert [item["id"] for item in legacy["documents"]] == ["legacy-own"]

    invalid = object.__new__(DocumentRequestAuthority)
    denied = _run(
        ManageDocumentTool(),
        json.dumps({"action": "read", "document_id": "legacy-own"}),
        authority=invalid,
    )
    assert denied == {"error": "Document operation failed", "exit_code": 1}


def test_explicit_local_authority_sees_all_null_sid_rows(
    document_db, browser_authority, monkeypatch
):
    _, _, subject_id, _ = browser_authority
    _seed_partitions(document_db, subject_id)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    authority = capture_document_request_authority(_local_request())

    result = _run(ManageDocumentTool(), json.dumps({"action": "list"}), owner=None, authority=authority)

    assert {item["id"] for item in result["documents"]} == {"legacy-own", "legacy-other"}


def test_update_cannot_cross_subject_and_revalidates_revoked_authority(
    document_db, browser_authority
):
    _, request, subject_id, authority = browser_authority
    _seed_partitions(document_db, subject_id)

    denied = _run(
        UpdateDocumentTool(),
        "replacement",
        authority=authority,
        owner="alice",
        doc_id="bound-other",
    )
    assert "error" in denied
    with document_db() as db:
        assert db.query(Document).filter(Document.id == "bound-other").one().current_content == "substantial body"

        own = db.query(Document).filter(Document.id == "bound-own").one()
        stale_token = (
            own.id,
            own.version_count,
            own.current_content,
            own.is_active,
            own.archived,
            own.session_id,
            own.title,
            own.language,
        )
        own.current_content = "concurrent change"
        own.version_count += 1
        db.commit()

    stale = _run(
        UpdateDocumentTool(),
        "must not overwrite concurrent change",
        authority=authority,
        document_revision_token=stale_token,
    )
    assert stale == {"error": "Document changed; retry the request"}
    with document_db() as db:
        assert db.query(Document).filter(Document.id == "bound-own").one().current_content == "concurrent change"

    request.app.state.auth_manager = object()
    revoked = _run(UpdateDocumentTool(), "must not land", authority=authority)
    assert "error" in revoked
    with document_db() as db:
        assert db.query(Document).filter(Document.current_content == "must not land").count() == 0


def test_create_stamps_browser_sid_and_commits_document_version(
    document_db, browser_authority
):
    _, _, subject_id, authority = browser_authority
    with document_db() as db:
        db.add(Session(id="session-1", name="chat", endpoint_url="http://model", model="m", owner="alice"))
        db.commit()

    result = _run(
        CreateDocumentTool(),
        "Title\nmarkdown\nBody",
        authority=authority,
        session_id="session-1",
    )

    with document_db() as db:
        created = db.query(Document).filter(Document.id == result["doc_id"]).one()
        assert created.owner == "alice"
        assert created.creator_subject_id == subject_id
        assert db.query(DocumentVersion).filter(
            DocumentVersion.document_id == created.id,
            DocumentVersion.version_number == 1,
        ).count() == 1


def test_create_keeps_null_sid_for_local_authority(document_db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    authority = capture_document_request_authority(_local_request())
    with document_db() as db:
        db.add(Session(id="session-1", name="chat", endpoint_url="http://model", model="m", owner="local"))
        db.commit()

    result = _run(
        CreateDocumentTool(),
        "Title\nmarkdown\nBody",
        owner="local",
        session_id="session-1",
        authority=authority,
    )

    with document_db() as db:
        created = db.query(Document).filter(Document.id == result["doc_id"]).one()
        assert created.owner == "local"
        assert created.creator_subject_id is None


def test_create_without_authority_keeps_legacy_null_sid_and_session_owner(document_db):
    with document_db() as db:
        db.add(Session(id="session-1", name="chat", endpoint_url="http://model", model="m", owner="alice"))
        db.commit()

    result = _run(
        CreateDocumentTool(),
        "Title\nmarkdown\nBody",
        owner="alice",
        session_id="session-1",
    )

    with document_db() as db:
        created = db.query(Document).filter(Document.id == result["doc_id"]).one()
        assert created.owner == "alice"
        assert created.creator_subject_id is None


def test_create_with_invalid_present_authority_fails_closed_without_rows(
    document_db, browser_authority
):
    _, _, _, authority = browser_authority
    invalid = object.__new__(DocumentRequestAuthority)
    result = _run(
        CreateDocumentTool(),
        "Title\nmarkdown\nBody",
        authority=invalid,
        owner="alice",
        session_id="session-1",
    )
    assert result["error"] == "Document operation failed"
    with document_db() as db:
        assert db.query(Document).count() == 0
        assert db.query(DocumentVersion).count() == 0


def test_tidy_is_scoped_for_browser_and_scheduler_compatibility(
    document_db, browser_authority
):
    _, _, subject_id, authority = browser_authority
    with document_db() as db:
        _add_document(db, "junk-bound-own", "alice", subject_id, content="")
        _add_document(db, "junk-bound-other", "alice", "owner_" + "b" * 32, content="")
        _add_document(db, "junk-legacy-own", "alice", None, content="")
        _add_document(db, "junk-legacy-other", "bob", None, content="")
        db.commit()

    asyncio.run(run_document_tidy("alice", document_authority=authority))
    with document_db() as db:
        assert {row.id for row in db.query(Document).all()} == {
            "junk-bound-other",
            "junk-legacy-other",
        }


def test_update_uses_atomic_full_revision_and_private_fresh_receipt(
    document_db, browser_authority, monkeypatch
):
    _, _, subject_id, authority = browser_authority
    _seed_partitions(document_db, subject_id)
    from sqlalchemy.orm import Query

    original_update = Query.update
    raced = {"done": False}

    def race_once(query, values, **kwargs):
        if not raced["done"]:
            raced["done"] = True
            with document_db() as rival:
                row = rival.query(Document).filter(Document.id == "bound-own").one()
                row.current_content = "concurrent body"
                row.version_count = 2
                rival.commit()
        return original_update(query, values, **kwargs)

    monkeypatch.setattr(Query, "update", race_once)
    result = _run(
        UpdateDocumentTool(),
        "must not land",
        authority=authority,
        doc_id="bound-own",
    )

    assert result == {"error": "Document operation failed"} or result == {
        "error": "Document changed; retry the request"
    }
    with document_db() as db:
        row = db.query(Document).filter(Document.id == "bound-own").one()
        assert row.current_content == "concurrent body"
        assert db.query(DocumentVersion).filter(
            DocumentVersion.document_id == "bound-own",
            DocumentVersion.version_number == 2,
        ).count() == 0


def test_receipt_is_not_serialized_and_postawait_revalidation_is_fail_closed(
    document_db, browser_authority
):
    _, request, subject_id, authority = browser_authority
    _seed_partitions(document_db, subject_id)

    result = _run(
        EditDocumentTool(),
        "<<<FIND>>>\nsubstantial\n<<<REPLACE>>>\nrevised\n<<<END>>>",
        authority=authority,
        doc_id="bound-own",
    )
    assert "_document_revision_receipt" not in result
    assert "document_revision_receipt" not in json.dumps(result)
    public, revision, replace = finalize_document_tool_result(
        result, authority, "alice"
    )
    assert public["action"] == "edit" and replace is True
    assert revision[0] == "bound-own" and revision[1] == 2

    request.app.state.auth_manager = object()
    with pytest.raises(Exception):
        finalize_document_tool_result(result, authority, "alice")


def test_delete_clears_inactive_document_pointer(document_db, browser_authority):
    _, _, subject_id, authority = browser_authority
    _seed_partitions(document_db, subject_id)
    set_active_document("bound-own")
    result = _run(
        ManageDocumentTool(),
        json.dumps({"action": "delete", "document_id": "bound-own", "confirmed": True}),
        authority=authority,
    )
    assert result.get("action") == "delete" or "Deleted document" in result.get("response", "")
    assert get_active_document() is None


def test_tidy_rolls_back_all_conditional_deletes_on_one_conflict(
    document_db, browser_authority, monkeypatch
):
    _, _, subject_id, authority = browser_authority
    with document_db() as db:
        _add_document(db, "junk-one", "alice", subject_id, content="")
        _add_document(db, "junk-two", "alice", subject_id, content="")
        db.commit()
    from sqlalchemy.orm import Query

    original_delete = Query.delete
    calls = {"count": 0}

    def conflict_second(query, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            return 0
        return original_delete(query, **kwargs)

    monkeypatch.setattr(Query, "delete", conflict_second)
    with pytest.raises(RuntimeError, match="Document tidy conflict"):
        asyncio.run(run_document_tidy("alice", document_authority=authority))
    with document_db() as db:
        assert {row.id for row in db.query(Document).all()} == {"junk-one", "junk-two"}
        assert {
            row.document_id for row in db.query(DocumentVersion).all()
        } == {"junk-one", "junk-two"}

