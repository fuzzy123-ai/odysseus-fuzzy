from __future__ import annotations

from fastapi import FastAPI, HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, sessionmaker
from starlette.requests import Request

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion, Session as DbSession
import routes.email_attachment_helpers as attachment_helpers
from src.document_subject_scope import capture_document_request_authority
import src.universal_inbox_working_copy as working_copy
import src.database as src_db


def _bind_src_database(monkeypatch, factory) -> None:
    monkeypatch.setattr(src_db, "SessionLocal", factory)
    monkeypatch.setattr(src_db, "Document", Document, raising=False)
    monkeypatch.setattr(src_db, "DocumentVersion", DocumentVersion, raising=False)
    monkeypatch.setattr(src_db, "Session", DbSession, raising=False)


def _request(manager: AuthManager, token: str) -> Request:
    app = FastAPI()
    app.state.auth_manager = manager
    request = Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/attachment-as-doc",
        "raw_path": b"/attachment-as-doc", "query_string": b"",
        "headers": [(b"cookie", f"odysseus_session={token}".encode("ascii"))],
        "client": ("127.0.0.1", 43100), "server": ("testserver", 80), "app": app,
    })
    request.state.api_token = False
    return request


def test_attachment_creation_scopes_global_deactivation_and_keeps_new_row_legacy(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'ingress.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _bind_src_database(monkeypatch, factory)

    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    assert manager.create_user("bob", "password-123")
    alice_token = manager.create_session("alice", "password-123")
    bob_token = manager.create_session("bob", "password-123")
    alice_sid = manager.strict_session_identity(alice_token)[1]
    bob_sid = manager.strict_session_identity(bob_token)[1]
    db = factory()
    try:
        db.add_all([
            Document(id="alice-bound", title="a", current_content="a", version_count=1,
                     is_active=True, owner="alice", creator_subject_id=alice_sid),
            Document(id="bob-bound", title="b", current_content="b", version_count=1,
                     is_active=True, owner="alice", creator_subject_id=bob_sid),
            Document(id="alice-legacy", title="l", current_content="l", version_count=1,
                     is_active=True, owner="alice", creator_subject_id=None),
        ])
        db.commit()
    finally:
        db.close()

    authority = capture_document_request_authority(_request(manager, alice_token))
    created_id = attachment_helpers.create_markdown_attachment_document(
        title="mail", content="bounded fixture", session_id=None, summary="mail",
        document_authority=authority, owner="alice",
        source_email={"uid": "7", "folder": "INBOX", "account_id": "", "message_id": "m"},
    )
    db = factory()
    try:
        created = db.get(Document, created_id)
        assert created.creator_subject_id == alice_sid
        assert created.owner == "alice"
        assert created.source_email_uid == "7"
        assert db.get(Document, "alice-bound").is_active is False
        assert db.get(Document, "alice-legacy").is_active is False
        assert db.get(Document, "bob-bound").is_active is True
    finally:
        db.close()


def test_noauthority_attachment_path_can_only_deactivate_null_sid_alias(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _bind_src_database(monkeypatch, factory)
    db = factory()
    try:
        db.add_all([
            Document(id="bound", title="b", current_content="b", version_count=1,
                     is_active=True, owner="alice", creator_subject_id="owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            Document(id="legacy", title="l", current_content="l", version_count=1,
                     is_active=True, owner="alice", creator_subject_id=None),
        ])
        db.commit()
    finally:
        db.close()
    attachment_helpers.create_markdown_attachment_document(
        title="legacy-new", content="fixture", session_id=None, summary="legacy",
        document_authority=None, owner="alice",
    )
    db = factory()
    try:
        assert db.get(Document, "bound").is_active is True
        assert db.get(Document, "legacy").is_active is False
    finally:
        db.close()


def test_working_copy_filework_releases_guard_and_revalidates_revision(tmp_path, monkeypatch):
    from contextlib import contextmanager

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'working-copy.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "working-copy-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    sid = manager.strict_session_identity(token)[1]
    authority = capture_document_request_authority(_request(manager, token))
    from src import document_subject_scope
    original_guard = document_subject_scope.guard_document_request_authority
    guard_state = {"depth": 0}

    @contextmanager
    def tracked_guard(sealed_authority):
        with original_guard(sealed_authority) as lease:
            guard_state["depth"] += 1
            try:
                yield lease
            finally:
                guard_state["depth"] -= 1

    monkeypatch.setattr(
        document_subject_scope, "guard_document_request_authority", tracked_guard
    )
    upload_id = "a" * 32 + ".txt"
    source_hash = working_copy._source_ref_hash("upload", upload_id)
    db = factory()
    try:
        db.add(Document(
            id="working", title="old", current_content="old", version_count=1,
            is_active=True, owner="alice", creator_subject_id=sid,
            source_kind="upload", source_ref_hash=source_hash,
        ))
        db.commit()
    finally:
        db.close()

    active_sessions = set()

    def tracked_factory():
        session = factory()
        active_sessions.add(session)
        original_close = session.close

        def close():
            try:
                return original_close()
            finally:
                active_sessions.discard(session)

        session.close = close
        return session

    def mutate_during_filework(*_args, **_kwargs):
        assert guard_state["depth"] == 0
        assert active_sessions == set()
        other = factory()
        try:
            row = other.get(Document, "working")
            row.version_count = 2
            other.commit()
        finally:
            other.close()
        return "new", "text", "new-content"

    monkeypatch.setattr(working_copy, "_build_working_copy_content", mutate_during_filework)
    try:
        working_copy.create_or_get_universal_inbox_working_copy(
            tracked_factory, object(), f"upload:{upload_id}", owner="alice",
            new_revision=True, document_authority=authority,
        )
    except working_copy.UniversalInboxWorkingCopyError as exc:
        assert exc.reason_code == "working_copy_changed"
    else:
        raise AssertionError("stale working-copy revision was accepted")
    check = factory()
    try:
        row = check.get(Document, "working")
        assert row.current_content == "old"
        assert row.version_count == 2
    finally:
        check.close()

    result = working_copy.create_or_get_universal_inbox_working_copy(
        factory, object(), f"upload:{upload_id}", owner="alice",
        document_authority=authority,
    )
    assert guard_state["depth"] == 0
    assert result.status_dict()["working_copy_id"] == "working"
    assert result.status_dict()["version"] == 2
    assert not hasattr(result, "document")


def test_working_copy_unique_race_recovers_same_sid_winner(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'working-copy-race.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "working-copy-race-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    sid = manager.strict_session_identity(token)[1]
    authority = capture_document_request_authority(_request(manager, token))
    upload_id = "e" * 32 + ".txt"
    source_hash = working_copy._source_ref_hash("upload", upload_id)
    monkeypatch.setattr(
        working_copy, "_build_working_copy_content", lambda *_args, **_kwargs: (
            "candidate", "text", "candidate-content"
        )
    )

    factory_calls = 0

    def racing_factory():
        nonlocal factory_calls
        factory_calls += 1
        db = factory()
        if factory_calls != 2:
            return db

        original_commit = db.commit
        injected = False

        def commit_after_competing_winner():
            nonlocal injected
            if injected:
                return original_commit()
            injected = True
            db.rollback()
            db.add(Document(
                id="race-winner", title="winner", language="text",
                current_content="winner-content", version_count=1, is_active=True,
                owner="alice", creator_subject_id=sid, source_kind="upload",
                source_ref_hash=source_hash,
            ))
            db.add(DocumentVersion(
                id="race-winner-version", document_id="race-winner", version_number=1,
                content="winner-content", summary="winner", source="upload",
            ))
            original_commit()
            raise IntegrityError("bounded race", {}, None)

        db.commit = commit_after_competing_winner
        return db

    result = working_copy.create_or_get_universal_inbox_working_copy(
        racing_factory, object(), f"upload:{upload_id}", owner="alice",
        document_authority=authority,
    )
    assert result.created is False
    assert result.status_dict()["working_copy_id"] == "race-winner"
    check = factory()
    try:
        assert check.query(Document).count() == 1
        assert check.query(DocumentVersion).count() == 1
        assert check.get(Document, "race-winner").creator_subject_id == sid
    finally:
        check.close()


def test_noauthority_working_copy_keeps_null_sid_owner_partitions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy-working-copy.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    upload_id = "f" * 32 + ".txt"
    monkeypatch.setattr(
        working_copy, "_build_working_copy_content", lambda *_args, **_kwargs: (
            "legacy", "text", "legacy-content"
        )
    )

    ids = []
    for owner in ("alice", "bob"):
        result = working_copy.create_or_get_universal_inbox_working_copy(
            factory, object(), f"upload:{upload_id}", owner=owner,
            document_authority=None,
        )
        ids.append(result.status_dict()["working_copy_id"])

    assert ids[0] != ids[1]
    check = factory()
    try:
        rows = check.query(Document).order_by(Document.owner).all()
        assert [(row.owner, row.creator_subject_id) for row in rows] == [
            ("alice", None), ("bob", None)
        ]
        assert check.query(DocumentVersion).count() == 2
    finally:
        check.close()


def test_working_copy_rejects_revoked_browser_session_before_atomic_write(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'working-copy-revoked.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "working-copy-revoked-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    sid = manager.strict_session_identity(token)[1]
    authority = capture_document_request_authority(_request(manager, token))
    upload_id = "b" * 32 + ".txt"
    source_hash = working_copy._source_ref_hash("upload", upload_id)
    seed = factory()
    try:
        seed.add(Document(
            id="revoked-working", title="old", current_content="old", version_count=1,
            is_active=True, owner="alice", creator_subject_id=sid,
            source_kind="upload", source_ref_hash=source_hash,
        ))
        seed.add(DocumentVersion(
            id="revoked-version", document_id="revoked-working", version_number=1,
            content="old", summary="seed", source="upload",
        ))
        seed.commit()
    finally:
        seed.close()

    def revoke_during_filework(*_args, **_kwargs):
        manager.revoke_token(token)
        return "new", "text", "new-content"

    monkeypatch.setattr(working_copy, "_build_working_copy_content", revoke_during_filework)
    with pytest.raises(HTTPException) as denied:
        working_copy.create_or_get_universal_inbox_working_copy(
            factory, object(), f"upload:{upload_id}", owner="alice",
            new_revision=True, document_authority=authority,
        )
    assert getattr(denied.value, "status_code", None) == 401

    check = factory()
    try:
        row = check.get(Document, "revoked-working")
        assert row.current_content == "old"
        assert row.version_count == 1
        assert check.query(DocumentVersion).count() == 1
    finally:
        check.close()


def test_working_copy_conditional_update_miss_rolls_back_without_version(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'working-copy-cas-miss.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "working-copy-cas-miss-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    sid = manager.strict_session_identity(token)[1]
    authority = capture_document_request_authority(_request(manager, token))
    upload_id = "c" * 32 + ".txt"
    source_hash = working_copy._source_ref_hash("upload", upload_id)
    seed = factory()
    try:
        seed.add(Document(
            id="cas-working", title="old", current_content="old", version_count=1,
            is_active=True, owner="alice", creator_subject_id=sid,
            source_kind="upload", source_ref_hash=source_hash,
        ))
        seed.add(DocumentVersion(
            id="cas-version", document_id="cas-working", version_number=1,
            content="old", summary="seed", source="upload",
        ))
        seed.commit()
    finally:
        seed.close()

    monkeypatch.setattr(
        working_copy, "_build_working_copy_content",
        lambda *_args, **_kwargs: ("new", "text", "new-content"),
    )
    update_calls = []

    def miss_conditional_update(_query, _values, **_kwargs):
        update_calls.append(True)
        return 0

    monkeypatch.setattr(Query, "update", miss_conditional_update)
    with pytest.raises(working_copy.UniversalInboxWorkingCopyError) as conflict:
        working_copy.create_or_get_universal_inbox_working_copy(
            factory, object(), f"upload:{upload_id}", owner="alice",
            new_revision=True, document_authority=authority,
        )
    assert conflict.value.reason_code == "working_copy_changed"
    assert update_calls == [True]

    check = factory()
    try:
        row = check.get(Document, "cas-working")
        assert row.current_content == "old"
        assert row.version_count == 1
        assert check.query(DocumentVersion).count() == 1
    finally:
        check.close()


def test_working_copy_session_close_failure_still_releases_lease(tmp_path, monkeypatch):
    from contextlib import contextmanager

    engine = create_engine(f"sqlite:///{(tmp_path / 'working-copy-close.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    manager = AuthManager(str(tmp_path / "working-copy-close-auth.json"))
    assert manager.create_user("alice", "password-123", is_admin=True)
    token = manager.create_session("alice", "password-123")
    authority = capture_document_request_authority(_request(manager, token))
    from src import document_subject_scope

    original_guard = document_subject_scope.guard_document_request_authority
    guard_depth = {"value": 0}

    @contextmanager
    def tracked_guard(sealed_authority):
        with original_guard(sealed_authority) as lease:
            guard_depth["value"] += 1
            try:
                yield lease
            finally:
                guard_depth["value"] -= 1

    monkeypatch.setattr(
        document_subject_scope, "guard_document_request_authority", tracked_guard
    )

    def broken_close_factory():
        session = factory()
        original_close = session.close

        def fail_after_close():
            original_close()
            raise RuntimeError("injected close failure")

        session.close = fail_after_close
        return session

    monkeypatch.setattr(
        working_copy, "_build_working_copy_content",
        lambda *_args, **_kwargs: pytest.fail("close failure must stop before extraction"),
    )
    with pytest.raises(RuntimeError):
        working_copy.create_or_get_universal_inbox_working_copy(
            broken_close_factory, object(), f"upload:{'d' * 32}.txt",
            owner="alice", document_authority=authority,
        )
    assert guard_depth["value"] == 0
