"""Legacy email aliases never certify or reuse a subject-bound document."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import database
from core.database import Base, Document, DocumentVersion
from mcp_servers.email_draft_document_utils import create_email_draft_document
from src import event_bus


@pytest.fixture
def draft_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'drafts.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(event_bus, "fire_event", lambda *_args, **_kwargs: None)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed(factory, *, document_id, subject_id=None, newer=False):
    content = "To: recipient\nSubject: draft\n---\nOriginal draft"
    with factory() as db:
        db.add(Document(
            id=document_id, owner="same-alias", creator_subject_id=subject_id,
            title="Draft", language="email", current_content=content,
            version_count=1, is_active=True, source_email_uid="42",
            source_email_folder="INBOX",
            updated_at=datetime(2026, 1, 1) + timedelta(days=1 if newer else 0),
        ))
        db.add(DocumentVersion(
            id=f"version-{document_id}", document_id=document_id,
            version_number=1, content=content, source="ai",
        ))
        db.commit()
    return content


def _create():
    return create_email_draft_document(
        to="recipient", subject="draft", body="New reply",
        source_uid="42", source_folder="INBOX",
        load_config_func=lambda _account: {"account_name": "offline"},
        current_owner_func=lambda: "same-alias",
        default_document_owner_func=lambda: "unused",
    )


def test_same_alias_bound_draft_is_not_read_reused_or_changed(draft_database):
    original = _seed(
        draft_database, document_id="bound", subject_id="owner_" + "a" * 32,
    )
    result = _create()
    assert result["doc_id"] != "bound"
    assert "updated" not in result
    assert "creator_subject_id" not in result and "owner" not in result
    with draft_database() as db:
        bound = db.get(Document, "bound")
        assert bound.current_content == original and bound.version_count == 1
        assert db.query(DocumentVersion).filter_by(document_id="bound").count() == 1
        created = db.get(Document, result["doc_id"])
        assert created.creator_subject_id is None and created.owner == "same-alias"
        assert "New reply" in created.current_content
        assert db.query(DocumentVersion).filter_by(document_id=created.id).count() == 1


def test_newer_bound_draft_cannot_shadow_reusable_legacy_draft(draft_database):
    _seed(draft_database, document_id="legacy")
    original = _seed(
        draft_database, document_id="bound", subject_id="owner_" + "b" * 32,
        newer=True,
    )
    result = _create()
    assert result["updated"] is True and result["doc_id"] == "legacy"
    with draft_database() as db:
        legacy = db.get(Document, "legacy")
        assert legacy.creator_subject_id is None and legacy.version_count == 2
        assert "New reply" in legacy.current_content
        assert db.query(DocumentVersion).filter_by(document_id="legacy").count() == 2
        bound = db.get(Document, "bound")
        assert bound.current_content == original and bound.version_count == 1
        assert db.query(Document).count() == 2
