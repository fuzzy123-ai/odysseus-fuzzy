"""Concurrency stress tests for AuthManager._config_lock.

Verifies that concurrent create/delete/rename operations don't lose data
or corrupt auth.json. If someone removes the lock, these tests should fail
with missing users or assertion errors.
"""

import json
import threading
import time
import contextlib
import sys
import types
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from tests.helpers.import_state import clear_module


class _OwnerColumn:
    def __eq__(self, other):
        return ("owner ==", other)


class _FakeApiToken:
    owner = _OwnerColumn()


class _FakeQuery:
    def filter(self, *_conds):
        return self

    def delete(self, *args, **kwargs):
        return 0


class _FakeSession:
    def query(self, model):
        assert model is _FakeApiToken
        return _FakeQuery()


@pytest.fixture(autouse=True)
def _stub_api_token_purge(monkeypatch):
    @contextlib.contextmanager
    def _fake_db_session():
        yield _FakeSession()

    db_stub = types.ModuleType("core.database")
    db_stub.get_db_session = _fake_db_session
    db_stub.ApiToken = _FakeApiToken
    monkeypatch.setitem(sys.modules, "core.database", db_stub)


def _fresh_auth_manager(tmp_path):
    clear_module("core.auth")
    from core.auth import AuthManager

    return AuthManager(str(tmp_path / "auth.json"))


class TestConcurrentCreateUser:
    """Concurrent create_user calls must not lose accounts."""

    @pytest.mark.slow
    def test_parallel_creates_no_lost_users(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        num_users = 50

        def create(i):
            return mgr.create_user(f"user{i}", f"password{i}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(create, i) for i in range(num_users)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results), "Some create_user calls returned False unexpectedly"
        assert len(mgr.users) == num_users

        mgr2 = _fresh_auth_manager(tmp_path)
        mgr2.auth_path = mgr.auth_path
        mgr2._load()
        assert len(mgr2.users) == num_users

    def test_parallel_creates_same_username_only_one_wins(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        num_attempts = 20

        def create(_):
            return mgr.create_user("contested", "password123")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(create, i) for i in range(num_attempts)]
            results = [f.result() for f in as_completed(futures)]

        assert results.count(True) == 1
        assert results.count(False) == num_attempts - 1
        assert len(mgr.users) == 1


class TestConcurrentDeleteUser:
    """Concurrent deletes must not corrupt state."""

    @pytest.mark.slow
    def test_parallel_deletes_no_corruption(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        num_users = 30
        for i in range(num_users):
            mgr.create_user(f"target{i}", f"pw{i}")

        assert len(mgr.users) == num_users + 1

        def delete(i):
            return mgr.delete_user(f"target{i}", "admin")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(delete, i) for i in range(num_users)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results)
        assert len(mgr.users) == 1
        with open(mgr.auth_path, "r") as f:
            data = json.load(f)
        assert len(data["users"]) == 1
        assert "admin" in data["users"]


class TestConcurrentRenameUser:
    """Concurrent renames must not lose or duplicate users."""

    @pytest.mark.slow
    def test_parallel_renames_no_lost_users(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        num_users = 20
        for i in range(num_users):
            mgr.create_user(f"old{i}", f"pw{i}")

        def rename(i):
            return mgr.rename_user(f"old{i}", f"new{i}", "admin")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(rename, i) for i in range(num_users)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results)
        for i in range(num_users):
            assert f"new{i}" in mgr.users
            assert f"old{i}" not in mgr.users

        assert len(mgr.users) == num_users + 1


class TestConcurrentMixedOperations:
    """Mixed create/delete/rename at the same time."""

    @pytest.mark.slow
    def test_mixed_operations_no_corruption(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)

        for i in range(20):
            mgr.create_user(f"existing{i}", f"pw{i}")

        def create_batch():
            for i in range(20):
                mgr.create_user(f"newuser{i}", f"pw{i}")

        def delete_batch():
            for i in range(10):
                mgr.delete_user(f"existing{i}", "admin")

        def rename_batch():
            for i in range(10, 20):
                mgr.rename_user(f"existing{i}", f"renamed{i}", "admin")

        threads = [
            threading.Thread(target=create_batch),
            threading.Thread(target=delete_batch),
            threading.Thread(target=rename_batch),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert "admin" in mgr.users
        for i in range(10):
            assert f"existing{i}" not in mgr.users
        for i in range(10, 20):
            assert f"renamed{i}" in mgr.users
            assert f"existing{i}" not in mgr.users
        for i in range(20):
            assert f"newuser{i}" in mgr.users

        with open(mgr.auth_path, "r") as f:
            data = json.load(f)
        assert set(data["users"].keys()) == set(mgr.users.keys())


class TestDiskConsistency:
    """Verify auth.json is never in a corrupt state during concurrent writes."""

    @pytest.mark.slow
    def test_file_always_valid_json_during_concurrent_ops(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)

        stop_event = threading.Event()
        corruption_found = []
        successful_reads = []
        reader_errors = []
        writer_errors = []

        def read_snapshot():
            # Windows does not permit os.replace while an unlocked reader has
            # the file open.  POSIX atomic replacement deliberately remains
            # lock-free for readers; the writer FILE lock serializes both ends
            # only on Windows.
            if sys.platform == "win32":
                import core.auth as auth_module
                with auth_module._AUTH_FILE_WRITE_LOCK:
                    with open(mgr.auth_path, "r") as f:
                        return f.read()
            with open(mgr.auth_path, "r") as f:
                return f.read()

        def reader():
            while not stop_event.is_set():
                try:
                    content = read_snapshot()
                    json.loads(content)
                    successful_reads.append(True)
                except json.JSONDecodeError as e:
                    corruption_found.append(str(e))
                    break
                except Exception as exc:
                    reader_errors.append(type(exc))
                    break
                time.sleep(0.001)

        def writer():
            try:
                for i in range(50):
                    if not mgr.create_user(f"stress{i}", f"pw{i}"):
                        raise AssertionError("writer create failed")
            except Exception as exc:
                writer_errors.append(type(exc))

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)

        started_threads = []
        try:
            reader_thread.start()
            started_threads.append(reader_thread)
            writer_thread.start()
            started_threads.append(writer_thread)
            # bcrypt work is intentionally real here.  On Windows, fifty
            # creates can take materially longer than the former ten seconds.
            writer_thread.join(60)
        finally:
            stop_event.set()
            for thread in started_threads:
                thread.join(60)

        assert not reader_thread.is_alive() and not writer_thread.is_alive()
        assert reader_errors == [] and writer_errors == []
        assert successful_reads, "The concurrent reader never obtained a valid JSON snapshot"
        assert not corruption_found, f"Corrupt JSON detected: {corruption_found[0]}"
        persisted = json.loads(read_snapshot())
        assert set(persisted["users"]) == set(mgr.users) == {"admin", *(f"stress{i}" for i in range(50))}


class _ObservedRLock:
    """A test wrapper which preserves actual RLock blocking behavior."""

    def __init__(self, real, first_thread, second_attempt, writer_attempt=None):
        self._real = real
        self._first_thread = first_thread
        self._second_attempt = second_attempt
        self._writer_attempt = writer_attempt

    def __enter__(self):
        if threading.current_thread() is not self._first_thread:
            self._second_attempt.set()
            if self._writer_attempt is not None:
                self._writer_attempt.set()
        self._real.acquire()
        return self

    def __exit__(self, *args):
        self._real.release()


def test_second_constructor_attempts_file_lock_but_cannot_read_before_first_releases(tmp_path, monkeypatch):
    from core.auth import AuthManager
    import core.auth as auth_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"users": {"alice": {"password_hash": "legacy", "role": "admin"}}}), encoding="utf-8")
    first_read, release_first, second_attempt, second_read = (threading.Event() for _ in range(4))
    first_thread = []
    original_read = AuthManager._read_auth_config_locked
    errors, managers = [], []

    def paused_read(manager):
        if threading.current_thread() is first_thread[0]:
            first_read.set()
            assert release_first.wait(3)
        else:
            second_read.set()
        return original_read(manager)

    monkeypatch.setattr(AuthManager, "_read_auth_config_locked", paused_read)
    original_lock = auth_module._AUTH_FILE_WRITE_LOCK

    def construct():
        try:
            managers.append(AuthManager(str(auth_path)))
        except Exception as exc:
            errors.append(type(exc))

    first = threading.Thread(target=construct)
    first_thread.append(first)
    auth_module._AUTH_FILE_WRITE_LOCK = _ObservedRLock(original_lock, first, second_attempt)
    second = None
    started_threads = []
    try:
        first.start()
        started_threads.append(first)
        assert first_read.wait(3)
        second = threading.Thread(target=construct)
        second.start()
        started_threads.append(second)
        assert second_attempt.wait(3)
        assert not second_read.is_set()
        release_first.set()
    finally:
        release_first.set()
        for thread in started_threads:
            thread.join(5)
        auth_module._AUTH_FILE_WRITE_LOCK = original_lock
    assert not first.is_alive() and not second.is_alive() and errors == []
    durable = json.loads(auth_path.read_text(encoding="utf-8"))["users"]
    assert len(managers) == 2
    assert {manager.subject_id_for_username("alice") for manager in managers} == {durable["alice"]["subject_id"]}


def test_completed_manager_writer_attempts_file_lock_before_paused_constructor_can_publish(tmp_path, monkeypatch):
    from core.auth import AuthManager
    import core.auth as auth_module

    auth_path = tmp_path / "auth.json"
    completed = AuthManager(str(auth_path))
    assert completed.create_user("admin", "adminpw", is_admin=True)
    original_sids = {name: row["subject_id"] for name, row in completed.users.items()}
    constructor_read, release_constructor, writer_attempt, writer_published = (threading.Event() for _ in range(4))
    original_read, original_write = AuthManager._read_auth_config_locked, auth_module._atomic_write_json
    errors, constructed = [], []
    constructor_thread = []

    def paused_read(manager):
        if threading.current_thread() is constructor_thread[0]:
            constructor_read.set()
            assert release_constructor.wait(3)
        return original_read(manager)

    def observing_write(*args, **kwargs):
        writer_published.set()
        return original_write(*args, **kwargs)

    monkeypatch.setattr(AuthManager, "_read_auth_config_locked", paused_read)
    monkeypatch.setattr(auth_module, "_atomic_write_json", observing_write)
    original_lock = auth_module._AUTH_FILE_WRITE_LOCK

    def construct():
        try:
            constructed.append(AuthManager(str(auth_path)))
        except Exception as exc:
            errors.append(type(exc))

    def write_public_config():
        try:
            completed.signup_enabled = True
        except Exception as exc:
            errors.append(type(exc))

    constructor = threading.Thread(target=construct)
    constructor_thread.append(constructor)
    auth_module._AUTH_FILE_WRITE_LOCK = _ObservedRLock(original_lock, constructor, writer_attempt, writer_attempt)
    writer = None
    started_threads = []
    try:
        constructor.start()
        started_threads.append(constructor)
        assert constructor_read.wait(3)
        writer = threading.Thread(target=write_public_config)
        writer.start()
        started_threads.append(writer)
        assert writer_attempt.wait(3)
        assert not writer_published.is_set()
        release_constructor.set()
    finally:
        release_constructor.set()
        for thread in started_threads:
            thread.join(5)
        auth_module._AUTH_FILE_WRITE_LOCK = original_lock
    assert not constructor.is_alive() and not writer.is_alive() and errors == []
    persisted = json.loads(auth_path.read_text(encoding="utf-8"))
    assert len(constructed) == 1 and persisted["signup_enabled"] is True
    assert {name: row["subject_id"] for name, row in persisted["users"].items()} == original_sids


def test_constructor_normalizes_all_legacy_rows_in_one_publication_or_exposes_no_config(tmp_path, monkeypatch):
    from core.auth import AuthManager, AuthSubjectIdentityError, RESERVED_USERNAMES, SUBJECT_ID_PATTERN
    import core.auth as auth_module

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"users": {
        "Admin": {"password_hash": "legacy", "role": "admin"},
        next(iter(RESERVED_USERNAMES)): {"password_hash": "legacy", "subject_id": "owner_" + "a" * 32},
        "member": {"password_hash": "legacy", "is_admin": False},
    }}), encoding="utf-8")
    original_write = auth_module._atomic_write_json
    shapes = []

    def inspect_then_publish(path, candidate, **kwargs):
        shapes.append({
            "has_only_users": set(candidate) == {"users"},
            "reserved_absent": not (set(candidate["users"]) & RESERVED_USERNAMES),
            "all_sids_valid": all(SUBJECT_ID_PATTERN.fullmatch(row.get("subject_id", "")) for row in candidate["users"].values()),
            "legacy_admin_normalized": candidate["users"]["admin"].get("is_admin") is True,
        })
        return original_write(path, candidate, **kwargs)

    monkeypatch.setattr(auth_module, "_atomic_write_json", inspect_then_publish)
    manager = AuthManager(str(auth_path))
    assert shapes == [{"has_only_users": True, "reserved_absent": True, "all_sids_valid": True, "legacy_admin_normalized": True}]
    assert set(manager.users) == {"admin", "member"}

    auth_path.write_text(json.dumps({"users": {"again": {"password_hash": "legacy"}}}), encoding="utf-8")
    monkeypatch.setattr(auth_module, "_atomic_write_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unavailable")))
    failed = None
    with pytest.raises(AuthSubjectIdentityError) as exc:
        failed = AuthManager(str(auth_path))
    assert str(exc.value) == "invalid account identity registry"
    assert failed is None
