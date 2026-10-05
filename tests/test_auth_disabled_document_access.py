import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Document
from routes.document_helpers import _verify_doc_owner
from routes.document_helpers import _document_by_id_query, _owner_session_filter


class _Doc:
    owner = "alice"
    session_id = "session-1"


def test_verify_doc_owner_allows_none_user_when_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _verify_doc_owner(None, _Doc(), None)


def test_verify_doc_owner_rejects_none_user_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc_info:
        _verify_doc_owner(None, _Doc(), None)
    assert exc_info.value.status_code == 403


def test_verify_doc_owner_rejects_wrong_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc_info:
        _verify_doc_owner(None, _Doc(), "bob")
    assert exc_info.value.status_code == 404


def test_legacy_document_helpers_cannot_reach_subject_bound_rows(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'documents.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    alice_sid = "owner_" + "a" * 32
    bob_sid = "owner_" + "b" * 32
    db.add_all([
        Document(
            id="alice-bound", title="bound", language="markdown",
            current_content="bound", version_count=1, is_active=True,
            owner="alice", creator_subject_id=alice_sid,
        ),
        Document(
            id="alice-legacy", title="legacy", language="markdown",
            current_content="legacy", version_count=1, is_active=True,
            owner="alice", creator_subject_id=None,
        ),
        Document(
            id="bob-legacy", title="other legacy", language="markdown",
            current_content="other", version_count=1, is_active=True,
            owner="bob", creator_subject_id=None,
        ),
        Document(
            id="bob-bound-same-alias", title="other bound", language="markdown",
            current_content="other bound", version_count=1, is_active=True,
            owner="alice", creator_subject_id=bob_sid,
        ),
    ])
    db.commit()
    try:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        assert [row.id for row in _owner_session_filter(db.query(Document), "alice").all()] == ["alice-legacy"]
        assert _document_by_id_query(db, "alice-bound", user="alice").first() is None
        assert _document_by_id_query(db, "bob-bound-same-alias", user="alice").first() is None
        assert _document_by_id_query(db, "alice-legacy", user="alice").first().id == "alice-legacy"
        with pytest.raises(HTTPException) as bound_alias:
            _verify_doc_owner(db, db.get(Document, "alice-bound"), "alice")
        assert bound_alias.value.status_code == 404

        monkeypatch.setenv("AUTH_ENABLED", "false")
        local_rows = _owner_session_filter(db.query(Document), None).all()
        assert {row.id for row in local_rows} == {"alice-legacy", "bob-legacy"}
        assert _document_by_id_query(db, "alice-bound", user=None).first() is None
        assert _document_by_id_query(db, "alice-legacy", user=None).first().id == "alice-legacy"
        with pytest.raises(HTTPException) as bound_local:
            _verify_doc_owner(db, db.get(Document, "alice-bound"), None)
        assert bound_local.value.status_code == 404
        _verify_doc_owner(db, db.get(Document, "alice-legacy"), None)
    finally:
        db.close()
        engine.dispose()
