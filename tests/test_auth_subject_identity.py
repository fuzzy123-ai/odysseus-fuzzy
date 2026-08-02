import json
from contextlib import nullcontext

import pytest

from core.auth import AuthManager, AuthSubjectIdentityError, SUBJECT_ID_PATTERN


def _write_auth(path, users):
    path.write_text(json.dumps({"users": users}), encoding="utf-8")


def _row(subject_id=None):
    row = {"password_hash": "unused", "created": 1, "is_admin": False}
    if subject_id is not None:
        row["subject_id"] = subject_id
    return row


def test_legacy_subject_migration_is_atomic_and_not_public(tmp_path):
    path = tmp_path / "auth.json"
    _write_auth(path, {"alice": _row(), "bob": _row()})

    manager = AuthManager(str(path))
    alice = manager.subject_id_for_username("alice")
    bob = manager.subject_id_for_username("bob")

    assert SUBJECT_ID_PATTERN.fullmatch(alice or "")
    assert SUBJECT_ID_PATTERN.fullmatch(bob or "")
    assert alice != bob
    assert all("subject_id" not in item for item in manager.list_users())
    assert "subject_id" not in manager.policy()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["users"]["alice"]["subject_id"] == alice


def test_subject_survives_rename_and_new_account_gets_new_subject(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    manager = AuthManager(str(path))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("alice", "password")
    original = manager.subject_id_for_username("alice")

    assert manager.rename_user("alice", "renamed", "admin")
    assert manager.subject_id_for_username("renamed") == original

    class EmptyTokenQuery:
        def filter(self, *_args): return self
        def delete(self): return 0

    class EmptyTokenDb:
        def query(self, *_args): return EmptyTokenQuery()

    import core.database
    monkeypatch.setattr(core.database, "get_db_session", lambda: nullcontext(EmptyTokenDb()))
    assert manager.delete_user("renamed", "admin")
    assert manager.create_user("renamed", "password")
    assert manager.subject_id_for_username("renamed") != original


@pytest.mark.parametrize(
    "users",
    [
        {"alice": _row("owner_bad")},
        {
            "alice": _row("owner_0123456789abcdef0123456789abcdef"),
            "bob": _row("owner_0123456789abcdef0123456789abcdef"),
        },
        {
            "Alice": _row("owner_0123456789abcdef0123456789abcdef"),
            " alice ": _row("owner_fedcba9876543210fedcba9876543210"),
        },
    ],
)
def test_invalid_or_duplicate_subject_ids_fail_closed(tmp_path, users):
    path = tmp_path / "auth.json"
    _write_auth(path, users)
    before = path.read_bytes()

    with pytest.raises(AuthSubjectIdentityError):
        AuthManager(str(path))

    assert path.read_bytes() == before
