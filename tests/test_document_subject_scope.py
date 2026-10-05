from contextlib import nullcontext
from copy import copy
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from core.auth import AuthManager
from core.database import Base, Document, DocumentVersion
from src.auth_helpers import SessionPrincipal
import src.document_subject_scope as document_subject_scope
from src.document_subject_scope import (
    DocumentAuthorityLease,
    DocumentRequestAuthority,
    capture_document_request_authority,
    current_document_owner_alias,
    guard_document_request_authority,
    scope_document_bound_query,
    scope_document_ingress_key_query,
    scope_document_web_query,
    stamp_new_document,
)


def _browser_request(manager: AuthManager, token: str) -> Request:
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
        "client": ("127.0.0.1", 43100),
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
        "client": ("127.0.0.1", 43100),
        "server": ("testserver", 80),
        "app": app,
    }
    request = Request(scope)
    request.state.api_token = False
    return request


def _browser(tmp_path, username="alice"):
    manager = AuthManager(str(tmp_path / f"{username}-auth.json"))
    assert manager.create_user("admin", "password-123", is_admin=True)
    if username != "admin":
        assert manager.create_user(username, "password-123")
    token = manager.create_session(username, "password-123")
    assert token
    identity = manager.strict_session_identity(token)
    assert identity is not None
    return manager, token, identity[1], _browser_request(manager, token)


def _database(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'documents.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, factory


def _document(doc_id, owner, subject_id, content="body"):
    doc = Document(
        id=doc_id,
        session_id=None,
        title=doc_id,
        language="markdown",
        current_content=content,
        version_count=1,
        is_active=True,
        archived=False,
        owner=owner,
        creator_subject_id=subject_id,
    )
    version = DocumentVersion(
        id=f"version-{doc_id}",
        document_id=doc_id,
        version_number=1,
        content=content,
        summary="initial",
        source="user",
    )
    return doc, version


def test_authority_and_lease_are_opaque_and_expire(tmp_path):
    _, _, sid, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    assert type(authority) is DocumentRequestAuthority
    assert repr(authority) == "<DocumentRequestAuthority redacted>"
    assert sid not in repr(authority)
    with pytest.raises(TypeError):
        DocumentRequestAuthority()
    with pytest.raises(TypeError):
        DocumentAuthorityLease()

    with guard_document_request_authority(authority) as lease:
        assert type(lease) is DocumentAuthorityLease
        assert repr(lease) == "<DocumentAuthorityLease redacted>"
        assert current_document_owner_alias(lease) == "alice"
    with pytest.raises(HTTPException) as captured:
        current_document_owner_alias(lease)
    assert captured.value.status_code == 401
    assert captured.value.detail == "Not authenticated"


def test_active_lease_rejects_every_cross_thread_use(tmp_path):
    engine, factory = _database(tmp_path)
    _, _, _, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    outcomes = []

    with guard_document_request_authority(authority) as lease:
        transient, _ = _document("cross-thread", "forged", None)

        def attempt_cross_thread_use():
            with factory() as db:
                actions = (
                    ("private_binding", lambda: document_subject_scope._document_subject_binding(lease)),
                    ("owner_alias", lambda: current_document_owner_alias(lease)),
                    ("bound_query", lambda: scope_document_bound_query(db.query(Document), lease)),
                    (
                        "ingress_key",
                        lambda: scope_document_ingress_key_query(
                            db.query(Document), lease, local_owner="alice"
                        ),
                    ),
                    ("stamp", lambda: stamp_new_document(transient, lease, local_owner=None)),
                )
                for name, action in actions:
                    try:
                        action()
                    except HTTPException as error:
                        outcomes.append((name, error.status_code, error.detail))
                    except Exception:
                        outcomes.append((name, "unexpected", "unexpected"))
                    else:
                        outcomes.append((name, "accepted", "accepted"))

        worker = threading.Thread(target=attempt_cross_thread_use, daemon=True)
        worker.start()
        worker.join(timeout=2.0)
        assert not worker.is_alive()

    assert outcomes == [
        ("private_binding", 401, "Not authenticated"),
        ("owner_alias", 401, "Not authenticated"),
        ("bound_query", 401, "Not authenticated"),
        ("ingress_key", 401, "Not authenticated"),
        ("stamp", 401, "Not authenticated"),
    ]
    assert transient.owner == "forged"
    assert transient.creator_subject_id is None
    engine.dispose()


def test_only_exact_real_request_can_issue_authority(tmp_path):
    manager, token, _, request = _browser(tmp_path)
    request.state.current_user = "forged"
    authority = capture_document_request_authority(request)
    with guard_document_request_authority(authority) as lease:
        assert current_document_owner_alias(lease) == "alice"

    forged = SimpleNamespace(
        app=request.app,
        state=SimpleNamespace(api_token=False, current_user="alice"),
        cookies={"odysseus_session": token},
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    with pytest.raises(HTTPException):
        capture_document_request_authority(forged)
    with pytest.raises(HTTPException):
        guard = guard_document_request_authority(object.__new__(DocumentRequestAuthority))
        with guard:
            pass
    with pytest.raises(HTTPException):
        guard = guard_document_request_authority(
            SessionPrincipal("alice", manager.subject_id_for_username("alice"), "browser_session")
        )
        with guard:
            pass

    class ForgedManager:
        def strict_session_identity(self, _token):
            return "alice", manager.subject_id_for_username("alice")

    forged_manager_request = _browser_request(manager, token)
    forged_manager_request.app.state.auth_manager = ForgedManager()
    with pytest.raises(HTTPException):
        capture_document_request_authority(forged_manager_request)

    request.app.state.auth_manager = ForgedManager()
    with pytest.raises(HTTPException):
        with guard_document_request_authority(authority):
            pass


def test_bound_and_web_queries_partition_sid_alias_and_local(tmp_path, monkeypatch):
    engine, factory = _database(tmp_path)
    manager, _, sid_a, request = _browser(tmp_path)
    sid_b = "owner_" + "b" * 32
    with factory() as db:
        for doc, version in (
            _document("bound-a", "alice", sid_a),
            _document("bound-b-same-alias", "alice", sid_b),
            _document("legacy-alice", "alice", None),
            _document("legacy-bob", "bob", None),
        ):
            db.add(doc)
            db.add(version)
        db.commit()

    authority = capture_document_request_authority(request)
    with guard_document_request_authority(authority) as lease, factory() as db:
        bound = scope_document_bound_query(db.query(Document), lease).order_by(Document.id).all()
        web = scope_document_web_query(db.query(Document), lease).order_by(Document.id).all()
    assert [row.id for row in bound] == ["bound-a"]
    assert [row.id for row in web] == ["bound-a", "legacy-alice"]

    monkeypatch.setenv("AUTH_ENABLED", "false")
    local_request = _local_request()
    local_authority = capture_document_request_authority(local_request)
    with guard_document_request_authority(local_authority) as lease, factory() as db:
        assert scope_document_bound_query(db.query(Document), lease).count() == 0
        local_ids = [row.id for row in scope_document_web_query(db.query(Document), lease).order_by(Document.id)]
    assert local_ids == ["legacy-alice", "legacy-bob"]
    engine.dispose()


def test_ingress_key_browser_matches_only_current_sid(tmp_path):
    engine, factory = _database(tmp_path)
    _, _, sid_a, request = _browser(tmp_path)
    sid_b = "owner_" + "b" * 32
    with factory() as db:
        for doc, version in (
            _document("bound-a", "stale-alias", sid_a),
            _document("bound-b-same-alias", "alice", sid_b),
            _document("legacy-alice", "alice", None),
        ):
            db.add(doc)
            db.add(version)
        db.commit()

    authority = capture_document_request_authority(request)
    with guard_document_request_authority(authority) as lease, factory() as db:
        rows = scope_document_ingress_key_query(
            db.query(Document), lease, local_owner="ignored"
        ).all()
    assert [row.id for row in rows] == ["bound-a"]
    engine.dispose()


def test_ingress_key_browser_preserves_same_sid_across_alias_rename(tmp_path):
    engine, factory = _database(tmp_path)
    manager, _, sid, _ = _browser(tmp_path)
    with factory() as db:
        doc, version = _document("bound-before-rename", "alice", sid)
        db.add(doc)
        db.add(version)
        db.commit()

    assert manager.rename_user("alice", "renamed", "admin")
    token = manager.create_session("renamed", "password-123")
    assert token
    authority = capture_document_request_authority(_browser_request(manager, token))
    with guard_document_request_authority(authority) as lease, factory() as db:
        rows = scope_document_ingress_key_query(
            db.query(Document), lease, local_owner="renamed"
        ).all()
    assert [row.id for row in rows] == ["bound-before-rename"]
    engine.dispose()


def test_ingress_key_local_owner_matches_only_null_sid_partition(tmp_path, monkeypatch):
    engine, factory = _database(tmp_path)
    with factory() as db:
        for doc, version in (
            _document("local-null", "local-owner", None),
            _document("local-bound", "local-owner", "owner_" + "a" * 32),
            _document("other-null", "other-owner", None),
        ):
            db.add(doc)
            db.add(version)
        db.commit()

    monkeypatch.setenv("AUTH_ENABLED", "false")
    authority = capture_document_request_authority(_local_request())
    with guard_document_request_authority(authority) as lease, factory() as db:
        rows = scope_document_ingress_key_query(
            db.query(Document), lease, local_owner="local-owner"
        ).all()
    assert [row.id for row in rows] == ["local-null"]
    engine.dispose()


def test_ingress_key_local_none_matches_only_unowned_null_sid(tmp_path, monkeypatch):
    engine, factory = _database(tmp_path)
    with factory() as db:
        for doc, version in (
            _document("unowned-null", None, None),
            _document("owned-null", "local-owner", None),
            _document("unowned-bound", None, "owner_" + "a" * 32),
        ):
            db.add(doc)
            db.add(version)
        db.commit()

    monkeypatch.setenv("AUTH_ENABLED", "false")
    authority = capture_document_request_authority(_local_request())
    with guard_document_request_authority(authority) as lease, factory() as db:
        rows = scope_document_ingress_key_query(
            db.query(Document), lease, local_owner=None
        ).all()
    assert [row.id for row in rows] == ["unowned-null"]
    engine.dispose()


def test_ingress_key_rejects_fake_query_model_and_nonexact_owner(tmp_path):
    engine, factory = _database(tmp_path)
    _, _, _, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)

    class OwnerAlias(str):
        pass

    with guard_document_request_authority(authority) as lease, factory() as db:
        with pytest.raises(HTTPException):
            scope_document_ingress_key_query(
                SimpleNamespace(filter=lambda *_: None), lease, local_owner="alice"
            )
        with pytest.raises(HTTPException):
            scope_document_ingress_key_query(
                db.query(Document), lease, local_owner="alice", model=object
            )
        for invalid_owner in (OwnerAlias("alice"), 1, False):
            with pytest.raises(HTTPException):
                scope_document_ingress_key_query(
                    db.query(Document), lease, local_owner=invalid_owner
                )
    engine.dispose()


def test_ingress_key_rejects_copied_forged_and_expired_leases(tmp_path):
    engine, factory = _database(tmp_path)
    _, _, _, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    with guard_document_request_authority(authority) as lease, factory() as db:
        try:
            copied = copy(lease)
        except TypeError:
            pass
        else:
            with pytest.raises(HTTPException):
                scope_document_ingress_key_query(
                    db.query(Document), copied, local_owner="alice"
                )
        with pytest.raises(HTTPException):
            scope_document_ingress_key_query(
                db.query(Document),
                object.__new__(DocumentAuthorityLease),
                local_owner="alice",
            )

    with factory() as db, pytest.raises(HTTPException):
        scope_document_ingress_key_query(
            db.query(Document), lease, local_owner="alice"
        )
    engine.dispose()


def test_query_helpers_reject_fake_query_and_model(tmp_path):
    _, _, _, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    with guard_document_request_authority(authority) as lease:
        with pytest.raises(HTTPException):
            scope_document_bound_query(SimpleNamespace(filter=lambda *_: None), lease)
        with pytest.raises(HTTPException):
            scope_document_web_query(SimpleNamespace(), lease, model=object)


def test_stamp_is_transient_exact_and_single_use(tmp_path, monkeypatch):
    engine, factory = _database(tmp_path)
    _, _, sid, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    transient, version = _document("new-browser", "forged", None)
    with guard_document_request_authority(authority) as lease:
        stamp_new_document(transient, lease, local_owner="ignored")
        assert transient.owner == "alice"
        assert transient.creator_subject_id == sid
        with pytest.raises(HTTPException):
            stamp_new_document(transient, lease, local_owner=None)
        with factory() as db:
            db.add(transient)
            db.add(version)
            with pytest.raises(HTTPException):
                stamp_new_document(transient, lease, local_owner=None)
            db.commit()
        with pytest.raises(HTTPException):
            stamp_new_document(transient, lease, local_owner=None)

    monkeypatch.setenv("AUTH_ENABLED", "false")
    local_request = _local_request()
    local = capture_document_request_authority(local_request)
    local_doc, _ = _document("new-local", "before", None)
    with guard_document_request_authority(local) as lease:
        stamp_new_document(local_doc, lease, local_owner=None)
        assert local_doc.owner is None
        assert local_doc.creator_subject_id is None
        with pytest.raises(HTTPException):
            stamp_new_document(local_doc, lease, local_owner=None)
    engine.dispose()


def test_same_sid_rename_requires_new_capture_but_remains_authorized(tmp_path):
    manager, _, sid, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    assert manager.rename_user("alice", "renamed", "admin")
    with pytest.raises(HTTPException):
        with guard_document_request_authority(authority):
            pass
    renamed_token = manager.create_session("renamed", "password-123")
    assert renamed_token
    renamed = capture_document_request_authority(_browser_request(manager, renamed_token))
    with guard_document_request_authority(renamed) as lease:
        assert current_document_owner_alias(lease) == "renamed"
    assert manager.strict_session_identity(renamed_token) == ("renamed", sid)


def test_deleted_recreated_alias_cannot_reuse_old_authority(tmp_path, monkeypatch):
    manager, old_token, old_sid, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)

    class EmptyTokenQuery:
        def filter(self, *_args):
            return self

        def delete(self):
            return 0

    class EmptyTokenDb:
        def query(self, *_args):
            return EmptyTokenQuery()

    import core.database

    monkeypatch.setattr(core.database, "get_db_session", lambda: nullcontext(EmptyTokenDb()))
    assert manager.delete_user("alice", "admin")
    assert manager.create_user("alice", "password-123")
    new_token = manager.create_session("alice", "password-123")
    assert new_token
    new_identity = manager.strict_session_identity(new_token)
    assert new_identity and new_identity[1] != old_sid
    assert manager.strict_session_identity(old_token) is None
    with pytest.raises(HTTPException):
        with guard_document_request_authority(authority):
            pass


def test_repr_and_errors_do_not_expose_identity_or_document_text(tmp_path):
    _, token, sid, request = _browser(tmp_path)
    authority = capture_document_request_authority(request)
    rendered = repr(authority)
    assert token not in rendered
    assert sid not in rendered
    with pytest.raises(HTTPException) as captured:
        with guard_document_request_authority(object.__new__(DocumentRequestAuthority)):
            pass
    rendered_error = str(captured.value)
    assert token not in rendered_error
    assert sid not in rendered_error
    assert "secret-title" not in rendered_error
    assert "secret-content" not in rendered_error
