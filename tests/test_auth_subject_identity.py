import json
import copy
import asyncio
import gc
import sqlite3
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from core.auth import AuthManager, AuthSubjectIdentityError, LoginIdentityFence, SUBJECT_ID_PATTERN
from src.auth_helpers import SessionPrincipal, guard_trusted_session_principal, require_trusted_session_principal, require_user, strict_session_principal


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


def test_public_rename_advances_security_snapshot_before_alias_changes(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("alice", "password")
    before = manager.security_action_revision()

    assert manager.rename_user("alice", "renamed", "admin")
    assert manager.security_action_revision() == before + 1
    assert manager.active_admin_subject_identity(username="alice") is None

    after = manager.security_action_revision()
    assert manager.rename_user("missing", "other", "admin") is False
    assert manager.security_action_revision() == after


def test_subject_resolution_rejects_duplicate_or_malformed_current_registry(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("alice", "password")
    assert manager.create_user("bob", "password")
    alice = manager.subject_id_for_username("alice")
    manager._config["users"]["bob"]["subject_id"] = alice
    with pytest.raises(AuthSubjectIdentityError):
        manager.subject_id_for_username("alice")
    with pytest.raises(AuthSubjectIdentityError):
        manager.subject_id_for_owner_scope("owner:invalid")


def test_owner_scope_resolution_requires_one_current_account(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("alice", "password")
    subject = manager.subject_id_for_username("alice")
    from src.unified_source_index_owner_scope import OwnerScope
    scope = OwnerScope.for_subject_id(subject).value
    assert manager.subject_id_for_owner_scope(scope) == subject
    assert manager.subject_id_for_owner_scope("owner:unknown") is None
    manager._config["users"].pop("alice")
    assert manager.subject_id_for_owner_scope(scope) is None


def test_active_admin_subject_snapshot_is_atomic_and_follows_only_same_subject_alias(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("member", "password", is_admin=False)
    admin_subject = manager.subject_id_for_username("admin")
    member_subject = manager.subject_id_for_username("member")

    assert manager.active_admin_subject_identity(username="admin") == ("admin", admin_subject)
    assert manager.active_admin_subject_identity(username="member") is None
    assert manager.active_admin_subject_identity(subject_id=member_subject) is None

    assert manager.rename_user("admin", "renamed-admin", "renamed-admin") is False
    assert manager.create_user("second-admin", "password", is_admin=True)
    assert manager.rename_user("admin", "renamed-admin", "second-admin")
    assert manager.active_admin_subject_identity(username="admin") is None
    assert manager.active_admin_subject_identity(subject_id=admin_subject) == (
        "renamed-admin", admin_subject,
    )
    assert manager.active_admin_subject_identity(username="renamed-admin") == (
        "renamed-admin", admin_subject,
    )
    assert manager.active_admin_subject_identity(username="renamed-admin", subject_id=admin_subject) is None


def test_active_admin_subject_snapshot_denies_recreated_account_subject(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    manager = AuthManager(str(path))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("member", "password")
    old_subject = manager.subject_id_for_username("member")

    class EmptyTokenQuery:
        def filter(self, *_args): return self
        def delete(self): return 0

    class EmptyTokenDb:
        def query(self, *_args): return EmptyTokenQuery()

    import core.database
    monkeypatch.setattr(core.database, "get_db_session", lambda: nullcontext(EmptyTokenDb()))
    assert manager.delete_user("member", "admin")
    assert manager.create_user("member", "password", is_admin=True)
    assert manager.active_admin_subject_identity(subject_id=old_subject) is None
    assert manager.active_admin_subject_identity(username="member")[1] != old_subject


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


def test_login_identity_fence_binds_session_to_immutable_subject_and_revision(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    fence = manager.capture_login_identity("admin")
    assert type(fence) is LoginIdentityFence
    token = manager.create_session_trusted("admin", identity_fence=fence)
    assert token and manager.get_username_for_token(token) == "admin"
    assert manager.change_password("admin", "password", "new-password")
    # Password mutation denies stale in-flight issuance, but an already-issued
    # SID-bound browser session remains resolvable until explicitly revoked.
    assert manager.get_username_for_token(token) == "admin"


def test_login_fence_is_manager_private_exact_and_single_use(tmp_path):
    primary = AuthManager(str(tmp_path / "primary.json"))
    other = AuthManager(str(tmp_path / "other.json"))
    assert primary.create_user("admin", "password", is_admin=True)
    assert other.create_user("admin", "password", is_admin=True)
    issued = primary.capture_login_identity("admin")
    assert issued is not None

    # The public value shape is not issuance authority: manually recreated,
    # copied, and cross-manager fences cannot enter the private registry.
    manual = LoginIdentityFence(issued.subject_id, issued.security_revision)
    assert primary.create_session_trusted("admin", identity_fence=manual) is None
    assert primary.create_session_trusted("admin", identity_fence=copy.copy(issued)) is None
    assert other.create_session_trusted("admin", identity_fence=issued) is None
    token = primary.create_session_trusted("admin", identity_fence=issued)
    assert token
    assert primary.create_session_trusted("admin", identity_fence=issued) is None


def test_login_fence_mutation_is_rejected_and_consumed_before_any_session_write(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("alice", "password", is_admin=True)
    assert manager.create_user("bob", "password")
    before_sessions = copy.deepcopy(manager._sessions)
    sessions_path = tmp_path / "sessions.json"
    before_durable = sessions_path.read_bytes() if sessions_path.exists() else None

    mutations = (
        ("subject_id", manager.subject_id_for_username("bob")),
        ("security_revision", 1),
        ("_issuer", object()),
        ("_nonce", "replaced"),
    )
    for field_name, replacement in mutations:
        fence = manager.capture_login_identity("alice")
        assert fence is not None
        original = getattr(fence, field_name)
        object.__setattr__(fence, field_name, replacement)
        assert manager.create_session_trusted("bob", identity_fence=fence) is None
        assert manager._sessions == before_sessions
        assert (sessions_path.read_bytes() if sessions_path.exists() else None) == before_durable
        # A failed consume remains consumed even when the public field is put
        # back to its original value.
        object.__setattr__(fence, field_name, original)
        assert manager.create_session_trusted("alice", identity_fence=fence) is None
        assert manager._sessions == before_sessions
        assert (sessions_path.read_bytes() if sessions_path.exists() else None) == before_durable


def test_trusted_principal_rejects_nonexact_manager_without_invoking_callbacks(monkeypatch):
    calls = []

    class FakeManager:
        def __init__(self, configured):
            self._configured = configured

        @property
        def is_configured(self):
            calls.append("is_configured")
            raise AssertionError("must not be read")

        def strict_session_identity(self, _token):
            calls.append("identity")
            raise AssertionError("must not be called")

        def guard_current_session(self, _token, _subject):
            calls.append("guard")
            raise AssertionError("must not be called")

    def request(manager):
        return SimpleNamespace(
            state=SimpleNamespace(api_token=False),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
            cookies={"odysseus_session": "cookie"}, headers={}, client=SimpleNamespace(host="127.0.0.1"),
        )

    monkeypatch.setenv("AUTH_ENABLED", "false")
    for fake in (FakeManager(True), FakeManager(False)):
        with pytest.raises(HTTPException) as denied:
            strict_session_principal(request(fake))
        assert denied.value.status_code == 401
        with pytest.raises(HTTPException) as denied:
            require_trusted_session_principal(request(fake))
        assert denied.value.status_code == 401
        with pytest.raises(HTTPException) as denied:
            with guard_trusted_session_principal(
                request(fake),
                SessionPrincipal("alice", "owner_" + "a" * 32, "browser_session"),
            ):
                pass
        assert denied.value.status_code == 401
    assert calls == []


@pytest.mark.parametrize("expiry", [True, float("nan"), float("inf"), -float("inf"), "tomorrow"])
def test_session_expiry_must_be_a_finite_exact_number(tmp_path, expiry):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    manager._sessions["bad"] = {"username": "admin", "expiry": expiry}
    assert manager.get_username_for_token("bad") is None


def test_malformed_present_sid_never_uses_legacy_alias_and_strict_legacy_is_unchanged(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    manager._sessions["bad-sid"] = {"username": "admin", "subject_id": "bad", "expiry": 10**12}
    assert manager.get_username_for_token("bad-sid") is None
    legacy = {"username": "admin", "expiry": 10**12}
    manager._sessions["legacy"] = copy.deepcopy(legacy)
    assert manager.strict_session_identity("legacy") is None
    assert manager._sessions["legacy"] == legacy


def test_require_trusted_principal_only_allows_explicit_direct_local(monkeypatch, tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    def request(*, host="127.0.0.1", headers=None):
        return SimpleNamespace(state=SimpleNamespace(api_token=False), app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)), cookies={}, headers=headers or {}, client=SimpleNamespace(host=host))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    direct = request()
    principal = require_trusted_session_principal(direct)
    assert principal == SessionPrincipal("local", None, "local")
    with pytest.raises(HTTPException):
        strict_session_principal(direct)
    with guard_trusted_session_principal(direct, principal) as guarded:
        assert guarded == principal
    # Explicit disabled-auth mode owns the local principal even with accounts
    # configured or a remote/proxied address.  Bearer remains forbidden.
    assert require_trusted_session_principal(request(host="10.0.0.2")) == principal
    assert require_trusted_session_principal(request(headers={"x-forwarded-for": "127.0.0.1"})) == principal
    with pytest.raises(HTTPException) as exc:
        require_trusted_session_principal(request(headers={"authorization": "Bearer x"}))
    assert exc.value.status_code == 401
    assert manager.create_user("admin", "password", is_admin=True)
    assert require_trusted_session_principal(direct) == principal
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")
    forwarded = tuple(
        request(headers={header: "127.0.0.1"})
        for header in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
    )
    for rejected in (direct, *forwarded, request(headers={"authorization": "Bearer x"})):
        with pytest.raises(HTTPException) as exc:
            require_trusted_session_principal(rejected)
        assert exc.value.status_code == 401


def test_unconfigured_trusted_principal_allows_only_unforwarded_numeric_loopback(tmp_path, monkeypatch):
    manager = AuthManager(str(tmp_path / "auth.json"))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "false")

    def request(host, headers=None):
        return SimpleNamespace(
            state=SimpleNamespace(api_token=False), app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
            cookies={}, headers=headers or {}, client=SimpleNamespace(host=host),
        )

    for host in ("127.0.0.1", "::1"):
        assert require_trusted_session_principal(request(host)) == SessionPrincipal("local", None, "local")
    for rejected in (request("localhost"), request("203.0.113.7"), *(
        request("127.0.0.1", {header: "203.0.113.7"})
        for header in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")
    )):
        with pytest.raises(HTTPException) as denied:
            require_trusted_session_principal(rejected)
        assert denied.value.status_code == 401


def test_legacy_require_user_keeps_configured_loopback_bypass_without_relaxing_strict_principal(tmp_path, monkeypatch):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    request = SimpleNamespace(
        state=SimpleNamespace(api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
        cookies={}, headers={"x-forwarded-for": "203.0.113.7"},
        client=SimpleNamespace(host="localhost"),
    )
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")
    assert require_user(request) == ""
    with pytest.raises(HTTPException) as denied:
        require_trusted_session_principal(request)
    assert denied.value.status_code == 401


def test_actual_login_denies_same_alias_recreated_after_password_verify(tmp_path, monkeypatch):
    from routes.auth_routes import LoginRequest, setup_auth_routes
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("target", "password")
    login = next(route.endpoint for route in setup_auth_routes(manager).routes if route.path == "/api/auth/login")
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), cookies={}, headers={}, app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace())
    original_verify = manager.verify_password
    class Query:
        def filter(self, *_args): return self
        def delete(self): return 0
    monkeypatch.setattr("core.database.get_db_session", lambda: nullcontext(SimpleNamespace(query=lambda *_args: Query())))
    def recreate_after_verify(username, password):
        assert original_verify(username, password)
        assert manager.delete_user(username, "admin")
        assert manager.create_user(username, password)
        return True
    monkeypatch.setattr(manager, "verify_password", recreate_after_verify)
    response = Response()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login(LoginRequest(username="target", password="password"), request, response))
    assert exc.value.status_code == 401
    assert "set-cookie" not in response.headers


def _login_endpoint_and_request(manager):
    from routes.auth_routes import setup_auth_routes
    endpoint = next(route.endpoint for route in setup_auth_routes(manager).routes if route.path == "/api/auth/login")
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), cookies={}, headers={}, app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace())
    return endpoint, request


def test_actual_login_totp_missing_invalid_and_backup_compatibility(tmp_path, monkeypatch):
    from routes.auth_routes import LoginRequest
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    manager._config["users"]["admin"].update({"totp_enabled": True, "totp_secret": "JBSWY3DPEHPK3PXP", "totp_backup_codes": ["backup"]})
    endpoint, request = _login_endpoint_and_request(manager)
    missing = asyncio.run(endpoint(LoginRequest(username="admin", password="password"), request, Response()))
    assert missing == {"ok": False, "requires_totp": True, "username": "admin"}
    monkeypatch.setattr(manager, "totp_verify", lambda *_args: False)
    with pytest.raises(HTTPException) as invalid:
        asyncio.run(endpoint(LoginRequest(username="admin", password="password", totp_code="bad"), request, Response()))
    assert invalid.value.status_code == 401
    monkeypatch.undo()
    assert manager.totp_verify("admin", "backup") is True
    assert "backup" not in manager._config["users"]["admin"]["totp_backup_codes"]


def test_actual_login_denies_password_or_totp_revision_change_after_verification(tmp_path, monkeypatch):
    from routes.auth_routes import LoginRequest
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    endpoint, request = _login_endpoint_and_request(manager)
    original_verify = manager.verify_password
    def password_change_after_verify(username, password):
        assert original_verify(username, password)
        # Exercise the production mutation rather than a private revision
        # helper, while keeping the outer login credential check successful.
        manager.verify_password = original_verify
        try:
            assert manager.change_password(username, password, "changed-password")
        finally:
            manager.verify_password = password_change_after_verify
        return True
    monkeypatch.setattr(manager, "verify_password", password_change_after_verify)
    with pytest.raises(HTTPException) as changed:
        asyncio.run(endpoint(LoginRequest(username="admin", password="password"), request, Response()))
    assert changed.value.status_code == 401


def test_actual_login_denies_totp_disable_after_second_factor_verification(tmp_path, monkeypatch):
    from routes.auth_routes import LoginRequest
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    manager._config["users"]["admin"].update({"totp_enabled": True, "totp_secret": "JBSWY3DPEHPK3PXP"})
    endpoint, request = _login_endpoint_and_request(manager)
    original_verify = manager.verify_password
    def disable_after_totp(_username, _code):
        manager.verify_password = original_verify
        try:
            assert manager.totp_disable("admin", "password")
        finally:
            manager.verify_password = original_verify
        return True
    monkeypatch.setattr(manager, "totp_verify", disable_after_totp)
    with pytest.raises(HTTPException) as changed:
        asyncio.run(endpoint(LoginRequest(username="admin", password="password", totp_code="accepted"), request, Response()))
    assert changed.value.status_code == 401


def test_guard_blocks_revoke_and_delete_from_other_threads_until_operation_exits(tmp_path, monkeypatch):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("target", "password")
    fence = manager.capture_login_identity("admin")
    token = manager.create_session_trusted("admin", identity_fence=fence)
    subject = manager.subject_id_for_username("admin")
    class Query:
        def filter(self, *_args): return self
        def delete(self): return 0
    monkeypatch.setattr("core.database.get_db_session", lambda: nullcontext(SimpleNamespace(query=lambda *_args: Query())))
    entered, release, revoke_done, delete_done = (threading.Event() for _ in range(4))
    def guarded():
        with manager.guard_current_session(token, subject):
            entered.set(); release.wait(2)
    def revoke():
        entered.wait(2); manager.revoke_token(token); revoke_done.set()
    def delete():
        entered.wait(2); manager.delete_user("target", "admin"); delete_done.set()
    holder = threading.Thread(target=guarded); revoker = threading.Thread(target=revoke); deleter = threading.Thread(target=delete)
    holder.start(); revoker.start(); deleter.start(); entered.wait(2)
    assert not revoke_done.wait(.05) and not delete_done.wait(.05)
    release.set()
    for worker in (holder, revoker, deleter): worker.join(3)
    assert revoke_done.is_set() and delete_done.is_set()


def test_session_persistence_restart_and_revocation_order(tmp_path):
    auth_path = tmp_path / "auth.json"
    first = AuthManager(str(auth_path))
    assert first.create_user("admin", "password", is_admin=True)
    fence = first.capture_login_identity("admin")
    token = first.create_session_trusted("admin", identity_fence=fence)
    sessions_path = tmp_path / "sessions.json"
    issued_file = sessions_path.read_bytes()
    assert AuthManager(str(auth_path)).strict_session_identity(token)
    assert first.revoke_token(token) is None
    assert AuthManager(str(auth_path)).strict_session_identity(token) is None
    # A restored pre-revocation file is evaluated by durable SID/current-owner
    # rules on restart, rather than a process-global revision counter.
    sessions_path.write_bytes(issued_file)
    assert AuthManager(str(auth_path)).strict_session_identity(token)


def test_restored_old_sid_file_is_denied_after_actual_delete_and_recreate(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    manager = AuthManager(str(auth_path))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("target", "password")
    target_fence = manager.capture_login_identity("target")
    target_token = manager.create_session_trusted("target", identity_fence=target_fence)
    old_file = (tmp_path / "sessions.json").read_bytes()
    class Query:
        def filter(self, *_args): return self
        def delete(self): return 0
    monkeypatch.setattr("core.database.get_db_session", lambda: nullcontext(SimpleNamespace(query=lambda *_args: Query())))
    assert manager.delete_user("target", "admin")
    assert manager.create_user("target", "password")
    (tmp_path / "sessions.json").write_bytes(old_file)
    assert AuthManager(str(auth_path)).strict_session_identity(target_token) is None


def test_two_auth_managers_serialize_shared_session_file_writes(tmp_path):
    auth_path = tmp_path / "auth.json"
    first = AuthManager(str(auth_path))
    assert first.create_user("admin", "password", is_admin=True)
    second = AuthManager(str(auth_path))
    first._sessions = {"first": {"username": "admin", "expiry": 10**12}}
    second._sessions = {"second": {"username": "admin", "expiry": 10**12}}
    start = threading.Barrier(2)
    done = []
    def write(manager):
        start.wait(2)
        done.append(manager._save_sessions())
    one, two = threading.Thread(target=write, args=(first,)), threading.Thread(target=write, args=(second,))
    one.start(); two.start(); one.join(3); two.join(3)
    assert done == [True, True]
    persisted = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert isinstance(persisted, dict) and set(persisted).issubset({"first", "second"})


def test_global_session_writer_blocks_second_same_process_writer_until_first_exits(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    first = AuthManager(str(auth_path))
    second = AuthManager(str(auth_path))
    first._sessions = {"one": {"username": "admin", "expiry": 10**12}}
    second._sessions = {"two": {"username": "admin", "expiry": 10**12}}
    import core.auth as auth_module
    original_writer = auth_module._atomic_write_json
    first_entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def barrier_writer(path, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            first_entered.set()
            assert release.wait(2)
        else:
            second_entered.set()
        return original_writer(path, payload, **kwargs)
    monkeypatch.setattr(auth_module, "_atomic_write_json", barrier_writer)
    one = threading.Thread(target=first._save_sessions)
    two = threading.Thread(target=second._save_sessions)
    one.start(); assert first_entered.wait(2)
    two.start()
    assert not second_entered.wait(.1)
    release.set(); one.join(3); two.join(3)
    assert second_entered.is_set()


def test_failed_revocation_stays_denied_and_logout_returns_503(tmp_path, monkeypatch):
    from core.auth import AuthSessionPersistenceError
    from routes.auth_routes import setup_auth_routes
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    fence = manager.capture_login_identity("admin")
    token = manager.create_session_trusted("admin", identity_fence=fence)
    def failed_writer(*_args, **_kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr("core.auth._atomic_write_json", failed_writer)
    with pytest.raises(AuthSessionPersistenceError):
        manager.revoke_token(token)
    assert manager.validate_token(token) is False

    # A fresh token gives the HTTP route an authenticated cookie; failure must
    # not produce a success audit or an ok response.
    monkeypatch.undo()
    fence = manager.capture_login_identity("admin")
    token = manager.create_session_trusted("admin", identity_fence=fence)
    monkeypatch.setattr("core.auth._atomic_write_json", failed_writer)
    logout = next(route.endpoint for route in setup_auth_routes(manager).routes if route.path == "/api/auth/logout")
    request = SimpleNamespace(cookies={"odysseus_session": token}, app=SimpleNamespace(state=SimpleNamespace(security_auth_events=[])))
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(logout(request, Response()))
    assert unavailable.value.status_code == 503
    assert manager.validate_token(token) is False
    assert request.app.state.security_auth_events == []


def test_trusted_session_requires_server_fence_and_ordinary_session_ignores_global_revision(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("backup-admin", "password", is_admin=True)
    assert manager.create_session_trusted("admin") is None
    fence = manager.capture_login_identity("admin")
    token = manager.create_session_trusted("admin", identity_fence=fence)
    assert token
    # Role changes invalidate in-flight issuance, but do not invalidate a
    # correctly bound, already-issued browser session.
    assert manager.set_admin("admin", False, "admin").value == "ok"
    assert manager.get_username_for_token(token) == "admin"


def test_strict_identity_follows_rename_but_denies_delete_recreate_and_legacy(tmp_path, monkeypatch):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("alice", "password")
    fence = manager.capture_login_identity("alice")
    token = manager.create_session_trusted("alice", identity_fence=fence)
    original = manager.strict_session_identity(token)
    assert original and original[0] == "alice"
    assert manager.rename_user("alice", "renamed", "admin")
    assert manager.strict_session_identity(token) == ("renamed", original[1])

    legacy = "legacy-cookie"
    manager._sessions[legacy] = {"username": "renamed", "expiry": 10**12}
    assert manager.get_username_for_token(legacy) == "renamed"
    assert manager.strict_session_identity(legacy) is None

    class EmptyTokenQuery:
        def filter(self, *_args): return self
        def delete(self): return 0
    class EmptyTokenDb:
        def query(self, *_args): return EmptyTokenQuery()
    import core.database
    monkeypatch.setattr(core.database, "get_db_session", lambda: nullcontext(EmptyTokenDb()))
    assert manager.delete_user("renamed", "admin")
    assert manager.create_user("renamed", "password")
    assert manager.strict_session_identity(token) is None


def test_guard_current_session_releases_authority_when_operation_fails(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    fence = manager.capture_login_identity("admin")
    token = manager.create_session_trusted("admin", identity_fence=fence)
    subject = manager.subject_id_for_username("admin")
    with pytest.raises(RuntimeError):
        with manager.guard_current_session(token, subject):
            raise RuntimeError("database failure")
    # A later mutation proves the outer authority lock was released.
    assert manager.revoke_token(token) is None
    assert manager.get_username_for_token(token) is None


def test_guard_exception_releases_authority_for_a_second_thread(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    fence = manager.capture_login_identity("admin")
    token = manager.create_session_trusted("admin", identity_fence=fence)
    subject = manager.subject_id_for_username("admin")
    entered, release, failed, revoked = (threading.Event() for _ in range(4))

    def failing_operation():
        try:
            with manager.guard_current_session(token, subject):
                entered.set()
                release.wait(2)
                raise RuntimeError("commit failed")
        except RuntimeError:
            failed.set()

    def independent_revoker():
        entered.wait(2)
        release.set()
        failed.wait(2)
        manager.revoke_token(token)
        revoked.set()

    first = threading.Thread(target=failing_operation)
    second = threading.Thread(target=independent_revoker)
    first.start(); second.start()
    first.join(3); second.join(3)
    assert failed.is_set() and revoked.is_set()
    assert manager.get_username_for_token(token) is None


def test_unknown_cookie_does_not_save_and_legacy_alias_ignores_other_missing_subjects(tmp_path, monkeypatch):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("legacy-user", "password")
    manager._config["users"]["legacy-user"].pop("subject_id")
    manager._sessions["legacy"] = {"username": "admin", "expiry": 10**12}
    saved = []
    monkeypatch.setattr(manager, "_save_sessions", lambda: saved.append(True) or True)
    assert manager.get_username_for_token("unknown-cookie") is None
    assert saved == []
    assert manager.get_username_for_token("legacy") == "admin"
    assert manager.strict_session_identity("legacy") is None
    assert manager._sessions["legacy"] == {"username": "admin", "expiry": 10**12}


def test_restart_revoke_uses_current_subject_when_rename_session_write_failed(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    first = AuthManager(str(auth_path))
    assert first.create_user("admin", "password", is_admin=True)
    assert first.create_user("alice", "password")
    token = first.create_session_trusted("alice", identity_fence=first.capture_login_identity("alice"))
    assert token
    monkeypatch.setattr(first, "_save_sessions", lambda: False)
    assert first.rename_user("alice", "renamed", "admin")
    restarted = AuthManager(str(auth_path))
    assert restarted.strict_session_identity(token)[0] == "renamed"
    assert restarted.revoke_user_sessions("renamed") == 1
    assert restarted.strict_session_identity(token) is None


def test_abandoned_login_fence_is_weakly_collected(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    fence = manager.capture_login_identity("admin")
    assert len(manager._issued_login_fences) == 1
    del fence
    gc.collect()
    assert len(manager._issued_login_fences) == 0


def test_legacy_identity_startup_is_serialized_to_one_durable_subject_set(tmp_path):
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, {"alice": _row(), "bob": _row()})
    start = threading.Barrier(2)
    managers = []
    def construct():
        start.wait(2)
        managers.append(AuthManager(str(auth_path)))
    first, second = threading.Thread(target=construct), threading.Thread(target=construct)
    first.start(); second.start(); first.join(3); second.join(3)
    assert len(managers) == 2
    persisted = json.loads(auth_path.read_text(encoding="utf-8"))["users"]
    assert all(SUBJECT_ID_PATTERN.fullmatch(row["subject_id"]) for row in persisted.values())
    assert {manager.subject_id_for_username("alice") for manager in managers} == {persisted["alice"]["subject_id"]}
    assert {manager.subject_id_for_username("bob") for manager in managers} == {persisted["bob"]["subject_id"]}


def test_legacy_single_user_constructors_reload_before_one_shared_conversion(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"username": "alice", "password_hash": "legacy-hash"}), encoding="utf-8")
    start = threading.Barrier(2)
    managers = []

    def construct():
        start.wait(2)
        managers.append(AuthManager(str(auth_path)))

    first, second = threading.Thread(target=construct), threading.Thread(target=construct)
    first.start(); second.start()
    first.join(3); second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert len(managers) == 2
    persisted = json.loads(auth_path.read_text(encoding="utf-8"))
    assert set(persisted) == {"users"}
    subject_id = persisted["users"]["alice"]["subject_id"]
    assert SUBJECT_ID_PATTERN.fullmatch(subject_id)
    assert {manager.subject_id_for_username("alice") for manager in managers} == {subject_id}


@pytest.mark.parametrize("identity", [
    None,
    "admin",
    ("admin",),
    ("Admin", "owner_" + "a" * 32),
    ("admin", "not-a-subject"),
    ("admin", "owner_" + "a" * 32, "extra"),
])
def test_strict_principal_rejects_malformed_manager_identity_without_request_state_proof(tmp_path, identity):
    class Manager:
        is_configured = True

        def strict_session_identity(self, _token):
            return identity

    request = SimpleNamespace(
        state=SimpleNamespace(api_token=False, current_user="admin", principal=SessionPrincipal("admin", "owner_" + "a" * 32, "browser_session")),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=Manager())),
        cookies={"odysseus_session": "cookie"}, headers={}, client=SimpleNamespace(host="127.0.0.1"),
    )
    with pytest.raises(HTTPException) as denied:
        strict_session_principal(request)
    assert denied.value.status_code == 401


def test_strict_principal_accepts_only_normalized_manager_identity_and_rejects_api_state(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    token = manager.create_session_trusted("admin", identity_fence=manager.capture_login_identity("admin"))
    request = SimpleNamespace(
        state=SimpleNamespace(api_token=False, current_user="forged"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
        cookies={"odysseus_session": token}, headers={}, client=SimpleNamespace(host="127.0.0.1"),
    )
    principal = strict_session_principal(request)
    assert principal.owner == "admin" and principal.creator_subject_id == manager.subject_id_for_username("admin")
    request.state.api_token = True
    with pytest.raises(HTTPException) as denied:
        strict_session_principal(request)
    assert denied.value.status_code == 401


def test_guard_rechecks_actual_cookie_and_propagates_yielded_database_failure(tmp_path, monkeypatch):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("other", "password")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")
    admin_token = manager.create_session_trusted("admin", identity_fence=manager.capture_login_identity("admin"))
    other_token = manager.create_session_trusted("other", identity_fence=manager.capture_login_identity("other"))
    request = SimpleNamespace(
        state=SimpleNamespace(api_token=False, current_user="forged"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
        cookies={"odysseus_session": admin_token}, headers={}, client=SimpleNamespace(host="127.0.0.1"),
    )
    # A configured localhost request still requires this SID cookie even with
    # the legacy route-level bypass flag enabled.
    principal = require_trusted_session_principal(request)
    with guard_trusted_session_principal(request, principal) as guarded:
        assert guarded == principal

    # The cookie is the proof at guard entry, rather than request state or the
    # principal passed by the caller.
    request.cookies["odysseus_session"] = other_token
    with pytest.raises(HTTPException) as changed_cookie:
        with guard_trusted_session_principal(request, principal):
            pass
    assert changed_cookie.value.status_code == 401
    request.cookies["odysseus_session"] = admin_token
    assert manager.revoke_token(admin_token) is None
    with pytest.raises(HTTPException) as revoked_cookie:
        with guard_trusted_session_principal(request, principal):
            pass
    assert revoked_cookie.value.status_code == 401

    fresh = manager.create_session_trusted("admin", identity_fence=manager.capture_login_identity("admin"))

    def current_valid_principal():
        request.state.api_token, request.headers, request.cookies["odysseus_session"] = False, {}, fresh
        current = require_trusted_session_principal(request)
        with guard_trusted_session_principal(request, current) as guarded:
            assert guarded == current
        return current

    other_subject = manager.subject_id_for_username("other")

    class ForgedSubclass(SessionPrincipal):
        pass

    for forge in (
        lambda current: SessionPrincipal("other", current.creator_subject_id, "browser_session"),
        lambda current: SessionPrincipal("admin", other_subject, "browser_session"),
        lambda current: SessionPrincipal("admin", current.creator_subject_id, "local"),
        lambda current: ForgedSubclass("admin", current.creator_subject_id, "browser_session"),
    ):
        current = current_valid_principal()
        with pytest.raises(HTTPException) as denied:
            with guard_trusted_session_principal(request, forge(current)):
                pass
        assert denied.value.status_code == 401

    for api_state, headers in ((True, {}), (False, {"authorization": "Bearer forged"}), (True, {"authorization": "Bearer forged"})):
        current = current_valid_principal()
        request.state.api_token, request.headers = api_state, headers
        with pytest.raises(HTTPException) as denied:
            with guard_trusted_session_principal(request, current):
                pass
        assert denied.value.status_code == 401

    current = current_valid_principal()
    database_failure = RuntimeError("database failure")
    seen, later_authority, thread_errors = [], threading.Event(), []
    try:
        with guard_trusted_session_principal(request, current):
            raise database_failure
    except RuntimeError as exc:
        seen.append(exc)
    def later_guard():
        try:
            with manager.guard_current_session(fresh, current.creator_subject_id):
                later_authority.set()
        except Exception as exc:
            thread_errors.append(type(exc))
    other_thread = threading.Thread(target=later_guard)
    try:
        other_thread.start()
    finally:
        if other_thread.ident is not None:
            other_thread.join(3)
    assert seen == [database_failure] and later_authority.is_set() and not other_thread.is_alive() and thread_errors == []


def test_guard_keeps_session_authority_through_sqlite_commit_without_holding_config_lock(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    token = manager.create_session_trusted("admin", identity_fence=manager.capture_login_identity("admin"))
    subject = manager.subject_id_for_username("admin")
    entered, read_config, release = threading.Event(), threading.Event(), threading.Event()
    db_path = tmp_path / "guard.sqlite"
    def guarded():
        with manager.guard_current_session(token, subject):
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS proof (id INTEGER)")
                db.execute("INSERT INTO proof VALUES (1)")
                db.commit()
            entered.set()
            release.wait(2)
    def config_reader():
        entered.wait(2)
        assert manager.subject_id_for_username("admin") == subject
        read_config.set()
    holder, reader = threading.Thread(target=guarded), threading.Thread(target=config_reader)
    holder.start(); reader.start()
    assert entered.wait(2) and read_config.wait(.5)
    release.set(); holder.join(3); reader.join(3)


def test_current_admin_subject_guard_blocks_demotion_and_delete_through_commit(tmp_path):
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    assert manager.create_user("guardian", "password", is_admin=True)
    subject = manager.subject_id_for_username("admin")
    entered, release = threading.Event(), threading.Event()
    outcomes, errors = [], []

    def guarded_commit():
        try:
            with manager.guard_current_admin_subject(subject) as alias:
                outcomes.append(alias)
                entered.set()
                release.wait(3)
        except BaseException as exc:
            errors.append(type(exc))

    def demote():
        try: outcomes.append(manager.set_admin("admin", False, "guardian"))
        except BaseException as exc: errors.append(type(exc))

    holder = threading.Thread(target=guarded_commit)
    mutator = threading.Thread(target=demote)
    try:
        holder.start(); assert entered.wait(2)
        mutator.start(); mutator.join(.2)
        assert mutator.is_alive()
        release.set(); holder.join(3); mutator.join(3)
    finally:
        release.set(); holder.join(1); mutator.join(1)
    assert not holder.is_alive() and not mutator.is_alive() and errors == []
    assert outcomes[0] == "admin" and manager.active_admin_subject_identity(subject_id=subject) is None

    with pytest.raises(AuthSubjectIdentityError):
        with manager.guard_current_admin_subject(subject):
            pass
