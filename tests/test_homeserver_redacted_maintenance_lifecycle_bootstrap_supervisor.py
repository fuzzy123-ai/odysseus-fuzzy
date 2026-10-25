from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import types
from pathlib import Path

import pytest

from ops.homeserver import redacted_maintenance_lifecycle_bootstrap_supervisor as supervisor
from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle


def _arm(now: int | None = None) -> dict[str, object]:
    current = int(time.time()) if now is None else now
    return {
        "schema_id": supervisor.ARM_SCHEMA_ID,
        "grant_id": "1" * 64,
        "invocation_id": "2" * 64,
        "expires_at": current + 600,
        "asset_pins": {name: "a" * 64 for name in supervisor.ASSET_NAMES},
    }


def test_arm_schema_has_exact_types_and_bounded_expiry() -> None:
    now = 1_800_000_000
    value = _arm(now)
    assert supervisor.validate_arm(value, now=now)
    for changed in (
        {**value, "extra": True},
        {**value, "expires_at": True},
        {**value, "expires_at": now + 601},
        {**value, "grant_id": "A" * 64},
        {**value, "asset_pins": {"supervisor.py": "a" * 64}},
    ):
        assert not supervisor.validate_arm(changed, now=now)


class _FixtureAPI:
    O_RDONLY = os.O_RDONLY
    O_WRONLY = os.O_WRONLY
    O_RDWR = os.O_RDWR
    O_CREAT = os.O_CREAT
    O_EXCL = os.O_EXCL
    O_DIRECTORY = getattr(os, "O_DIRECTORY", 1 << 20)
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 1 << 21)
    O_CLOEXEC = getattr(os, "O_CLOEXEC", 1 << 22)
    O_NONBLOCK = getattr(os, "O_NONBLOCK", 1 << 23)

    def __init__(self, root: Path, *, target: str = "valid") -> None:
        self.root = root
        self.target = target
        self.directories: dict[int, Path] = {}
        self.files: dict[int, Path] = {}
        self.metadata: dict[Path, tuple[int, int, int, int]] = {}
        self.next_fd = 100_000
        self.open_flags: list[tuple[Path, int]] = []

    def _path(self, name: str, dir_fd: int | None) -> Path:
        return (self.directories[dir_fd] if dir_fd is not None else Path()) / name

    def _info(self, path: Path) -> object:
        device, inode = path.stat().st_dev, path.stat().st_ino
        default_mode = 0o1777 if path.name == "tmp" else 0o755 if path.is_dir() else 0o600
        default_uid, default_gid, default_links, mode = self.metadata.get(path, (0, 0, 1, default_mode))
        kind = stat.S_IFDIR if path.is_dir() else stat.S_IFREG
        return types.SimpleNamespace(st_dev=device, st_ino=inode, st_mode=kind | mode, st_uid=default_uid, st_gid=default_gid, st_nlink=default_links)

    def open(self, name: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        path = Path(name) if dir_fd is None else self.directories[dir_fd] / name
        self.open_flags.append((path, flags))
        if flags & self.O_DIRECTORY:
            fd = self.next_fd
            self.next_fd += 1
            self.directories[fd] = path
            return fd
        stripped = flags & ~(self.O_DIRECTORY | self.O_NOFOLLOW | self.O_CLOEXEC | self.O_NONBLOCK)
        fd = os.open(str(path), stripped, mode)
        self.files[fd] = path
        return fd

    def close(self, fd: int) -> None:
        if fd in self.directories:
            del self.directories[fd]
        else:
            self.files.pop(fd, None)
            os.close(fd)

    def fstat(self, fd: int) -> object:
        return self._info(self.directories[fd] if fd in self.directories else self.files[fd])

    def stat(self, name: str, *, dir_fd: int, follow_symlinks: bool = False) -> object:
        return self._info(self.directories[dir_fd] / name)

    def geteuid(self) -> int:
        return 0


class _FixtureAccounts:
    def getpwnam(self, _name: str) -> object:
        return types.SimpleNamespace(pw_uid=1001, pw_gid=1002)

    def getpwall(self) -> list[object]:
        return [types.SimpleNamespace(pw_name="homebase", pw_gid=1002)]


class _FixtureGroups:
    def getgrgid(self, _gid: int) -> object:
        return types.SimpleNamespace(gr_mem=["homebase"])


class _FixtureFlock:
    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 8

    def __init__(self, *, contended: bool = False) -> None:
        self.contended = contended
        self.calls: list[tuple[int, int]] = []

    def flock(self, fd: int, operation: int) -> None:
        self.calls.append((fd, operation))
        if self.contended:
            raise BlockingIOError("fixture lock is held")


def _legacy_lock_fixture(root: Path, target: str = "valid") -> tuple[_FixtureAPI, _FixtureFlock, Path]:
    temporary = root / "tmp"
    temporary.mkdir()
    lock = temporary / "odysseus-auto-update.lock"
    if target != "absent":
        lock.write_bytes(b"")
    api = _FixtureAPI(root, target=target)
    owner, group, links, mode = (1001, 1002, 1, 0o600)
    if target == "wrong-owner":
        owner = 1003
    elif target == "wrong-mode":
        mode = 0o640
    elif target == "wrong-links":
        links = 2
    api.metadata[lock] = (owner, group, links, mode)
    return api, _FixtureFlock(contended=target == "contended"), lock


def test_root_legacy_bridge_uses_real_pinned_lifecycle_validators_and_never_creates(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in ("wrong-owner", "wrong-mode", "wrong-links", "contended", "absent"):
        with tempfile.TemporaryDirectory(prefix="bootstrap-root-bridge-") as temp:
            root = Path(temp)
            api, flock, lock = _legacy_lock_fixture(root, target)
            monkeypatch.setattr(lifecycle, "fcntl", flock)
            if target == "contended":
                with pytest.raises(BlockingIOError):
                    supervisor._acquire_existing_legacy_lock(lifecycle, root=temp, api=api, users=_FixtureAccounts(), groups=_FixtureGroups())
            else:
                with pytest.raises(OSError):
                    supervisor._acquire_existing_legacy_lock(lifecycle, root=temp, api=api, users=_FixtureAccounts(), groups=_FixtureGroups())
            assert lock.exists() is (target != "absent")
            assert not any(path == lock and flags & (os.O_CREAT | os.O_EXCL) for path, flags in api.open_flags)

    with tempfile.TemporaryDirectory(prefix="bootstrap-root-bridge-valid-") as temp:
        root = Path(temp)
        api, flock, lock = _legacy_lock_fixture(root)
        monkeypatch.setattr(lifecycle, "fcntl", flock)
        descriptor = supervisor._acquire_existing_legacy_lock(lifecycle, root=temp, api=api, users=_FixtureAccounts(), groups=_FixtureGroups())
        assert flock.calls == [(descriptor, flock.LOCK_EX | flock.LOCK_NB)]
        assert lock.exists()
        api.close(descriptor)

    with tempfile.TemporaryDirectory(prefix="bootstrap-root-bridge-unprivileged-") as temp:
        root = Path(temp)
        api, flock, lock = _legacy_lock_fixture(root)
        monkeypatch.setattr(lifecycle, "fcntl", flock)
        with pytest.raises(OSError):
            supervisor._acquire_existing_legacy_lock(lifecycle, root=temp, api=api, effective_uid=1001, users=_FixtureAccounts(), groups=_FixtureGroups())
        assert flock.calls == [] and lock.exists()


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0, reason="requires root-owned POSIX filesystem fixtures")
def test_supervisor_holds_only_legacy_lock_and_consumes_grant_once(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(prefix="bootstrap-supervisor-") as temp:
        state = Path(temp, "state")
        state.mkdir(mode=0o700)
        os.chmod(state, 0o700)
        payload = _arm()
        Path(state, supervisor.ARM_NAME).write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"))
        os.chmod(Path(state, supervisor.ARM_NAME), 0o600)
        events: list[str] = []
        lock_fd = os.open(os.devnull, os.O_RDONLY)

        class FakeBootstrap:
            @staticmethod
            def validate_envelope(value: object) -> bool:
                return value == {"status": "ready", "error_code": "none"}

            @staticmethod
            def bootstrap(*, execute: bool) -> dict[str, str]:
                assert events == ["legacy_lock"]
                assert execute is True
                events.append("bootstrap")
                return {"status": "ready", "error_code": "none"}

        loaded = {"lifecycle.py": lifecycle, "bootstrap.py": FakeBootstrap}
        def bridge(lifecycle_module: object) -> int:
            assert lifecycle_module is lifecycle
            events.append("legacy_lock")
            return lock_fd

        monkeypatch.setattr(supervisor, "_acquire_existing_legacy_lock", bridge)
        monkeypatch.setattr(supervisor, "STATE_DIRECTORY", str(state))
        monkeypatch.setattr(supervisor, "ASSET_DIRECTORY", str(Path(temp, "assets")))
        monkeypatch.setattr(supervisor, "_open_absolute_directory", lambda path: os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        monkeypatch.setattr(supervisor, "_load_assets", lambda _pins: ({name: b"pinned" for name in supervisor.ASSET_NAMES}, loaded))
        monkeypatch.setattr(supervisor, "_shared_metadata", lambda: {"directory_uid": 0})
        result = supervisor.run_supervisor()
        assert result == 0
        assert events == ["legacy_lock", "bootstrap"]
        assert not Path(state, supervisor.ARM_NAME).exists()
        assert Path(state, supervisor.CONSUMED_NAME).exists()
        receipt = json.loads(Path(state, supervisor.RECEIPT_NAME).read_text(encoding="ascii"))
        assert receipt["grant_id"] == payload["grant_id"]
        assert receipt["invocation_id"] == payload["invocation_id"]
        assert supervisor.run_supervisor() == 1
        assert events == ["legacy_lock", "bootstrap"]


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0, reason="requires root-owned POSIX filesystem fixtures")
def test_crash_after_consume_leaves_manual_recovery_marker_and_forbids_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(prefix="bootstrap-crash-") as temp:
        state = Path(temp, "state")
        state.mkdir(mode=0o700)
        os.chmod(state, 0o700)
        payload = _arm()
        Path(state, supervisor.ARM_NAME).write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"))
        os.chmod(Path(state, supervisor.ARM_NAME), 0o600)
        calls = 0
        lock_fd = os.open(os.devnull, os.O_RDONLY)

        class FakeBootstrap:
            @staticmethod
            def bootstrap(*, execute: bool) -> dict[str, str]:
                nonlocal calls
                calls += 1
                raise RuntimeError("fixture failure must not escape")

            @staticmethod
            def validate_envelope(_value: object) -> bool:
                return False

        monkeypatch.setattr(supervisor, "STATE_DIRECTORY", str(state))
        monkeypatch.setattr(supervisor, "_open_absolute_directory", lambda path: os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        monkeypatch.setattr(supervisor, "_acquire_existing_legacy_lock", lambda _module: lock_fd)
        monkeypatch.setattr(supervisor, "_load_assets", lambda _pins: ({name: b"pinned" for name in supervisor.ASSET_NAMES}, {"lifecycle.py": lifecycle, "bootstrap.py": FakeBootstrap}))
        # This failure is after consume completed: arm is gone and consumed
        # remains, so a later invocation cannot replay the bootstrap.
        assert supervisor.run_supervisor() == 1
        assert calls == 1
        assert not Path(state, supervisor.ARM_NAME).exists()
        assert Path(state, supervisor.CONSUMED_NAME).exists()
        assert not Path(state, supervisor.RECEIPT_NAME).exists()
        assert supervisor.run_supervisor() == 1
        assert calls == 1


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0, reason="requires root-owned POSIX filesystem fixtures")
def test_consumed_directory_sync_precedes_arm_unlink_and_failure_forbids_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(prefix="bootstrap-consume-fsync-") as temp:
        state = Path(temp, "state")
        state.mkdir(mode=0o700)
        os.chmod(state, 0o700)
        payload = _arm()
        Path(state, supervisor.ARM_NAME).write_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"))
        os.chmod(Path(state, supervisor.ARM_NAME), 0o600)
        calls = 0
        lock_fd = os.open(os.devnull, os.O_RDONLY)

        class FakeBootstrap:
            @staticmethod
            def bootstrap(*, execute: bool) -> dict[str, str]:
                nonlocal calls
                calls += 1
                return {"status": "ready", "error_code": "none"}

            @staticmethod
            def validate_envelope(value: object) -> bool:
                return value == {"status": "ready", "error_code": "none"}

        monkeypatch.setattr(supervisor, "STATE_DIRECTORY", str(state))
        monkeypatch.setattr(supervisor, "_open_absolute_directory", lambda path: os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        monkeypatch.setattr(supervisor, "_acquire_existing_legacy_lock", lambda _module: lock_fd)
        monkeypatch.setattr(supervisor, "_load_assets", lambda _pins: ({name: b"pinned" for name in supervisor.ASSET_NAMES}, {"lifecycle.py": lifecycle, "bootstrap.py": FakeBootstrap}))
        monkeypatch.setattr(supervisor, "_shared_metadata", lambda: None)
        real_fsync = os.fsync
        state_inode = state.stat().st_ino
        state_device = state.stat().st_dev
        events: list[str] = []

        def fsync(fd: int) -> None:
            info = os.fstat(fd)
            if info.st_dev == state_device and info.st_ino == state_inode:
                events.append("directory-fsync")
                raise OSError("injected state directory sync failure")
            events.append("file-fsync")
            real_fsync(fd)

        real_unlink = os.unlink

        def unlink(path: str, *args: object, **kwargs: object) -> None:
            if path == supervisor.ARM_NAME:
                events.append("arm-unlink")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(supervisor.os, "fsync", fsync)
        monkeypatch.setattr(supervisor.os, "unlink", unlink)
        assert supervisor.run_supervisor() == 1
        assert events.count("directory-fsync") == 1
        assert "arm-unlink" not in events
        assert Path(state, supervisor.ARM_NAME).exists()
        assert Path(state, supervisor.CONSUMED_NAME).exists()
        # The failed pre-unlink directory sync leaves the arm alongside the
        # durable consumed marker; that marker must deny replay.
        assert supervisor.run_supervisor() == 1
        assert calls == 0
        assert Path(state, supervisor.ARM_NAME).exists()
        assert Path(state, supervisor.CONSUMED_NAME).exists()
        assert not Path(state, supervisor.RECEIPT_NAME).exists()

