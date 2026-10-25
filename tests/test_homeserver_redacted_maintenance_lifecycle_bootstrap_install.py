from __future__ import annotations

import hashlib
import itertools
import json
import os
import stat
import subprocess
import tempfile
import types
from pathlib import Path

import pytest

from ops.homeserver import redacted_maintenance_lifecycle_bootstrap_install as installer
from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle


def _result(raw: bytes, code: int = 0) -> object:
    return type("Result", (), {"stdout": raw, "returncode": code})()


def test_installer_is_inert_without_explicit_execute() -> None:
    value = installer.install()
    assert installer.validate_envelope(value)
    assert value["status"] == "blocked"
    assert value["publication_attempted"] is False


def test_installer_envelope_accepts_only_the_explicit_status_code_flag_matrix() -> None:
    flag_names = (
        "publication_attempted",
        "published_assets",
        "unit_published",
        "arm_created",
        "sudoers_attempted",
        "sudoers_published",
    )
    matrix = {
        ("ready", "installed"): {"111111"},
        **{
            ("blocked", code): {"000000"}
            for code in (
                "execution_disabled",
                "identity_unavailable",
                "unit_not_quiescent",
                "invalid_installer_identity",
                "unit_validation_failed",
                "sudoers_validation_failed",
            )
        },
        ("blocked", "install_failed_before_exposure"): {"000000", "100000"},
        ("unknown", "stage_cleanup_ambiguous"): {"000000"},
        ("unknown", "publication_ambiguous"): {"100000", "110000", "111000"},
        ("unknown", "rollback_ambiguous"): {"100000", "110000", "111000", "111100"},
        ("unknown", "sudoers_publication_ambiguous"): {"111110"},
    }

    def make(status: str, code: str, bits: tuple[bool, ...]) -> dict[str, object]:
        value = installer._envelope(status, code, **dict(zip(("attempted", "assets", "unit", "arm", "sudoers_attempted", "sudoers"), bits)))
        return value

    for (status, code), allowed in matrix.items():
        for bits in itertools.product((False, True), repeat=6):
            value = make(status, code, bits)
            bit_string = "".join("1" if flag else "0" for flag in bits)
            assert installer.validate_envelope(value) is (bit_string in allowed), (status, code, bit_string)

        accepted_bits = tuple(bit == "1" for bit in sorted(allowed)[0])
        valid = make(status, code, accepted_bits)
        for wrong_status in {"ready", "blocked", "unknown"} - {status}:
            changed = {**valid, "status": wrong_status}
            changed["evidence_sha256"] = installer._digest(changed)
            assert not installer.validate_envelope(changed)
        changed_code = {**valid, "error_code": "arbitrary"}
        changed_code["evidence_sha256"] = installer._digest(changed_code)
        assert not installer.validate_envelope(changed_code)

        for name in flag_names:
            changed_type = {**valid, name: 1}
            changed_type["evidence_sha256"] = installer._digest(changed_type)
            assert not installer.validate_envelope(changed_type)
        for name in ("manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible"):
            changed_type = {**valid, name: 1}
            changed_type["evidence_sha256"] = installer._digest(changed_type)
            assert not installer.validate_envelope(changed_type)
        for name, wrong_value in (("status", 1), ("error_code", 1), ("evidence_sha256", 1)):
            changed_type = {**valid, name: wrong_value}
            if name != "evidence_sha256":
                changed_type["evidence_sha256"] = installer._digest(changed_type)
            assert not installer.validate_envelope(changed_type)
        changed_digest = {**valid, "evidence_sha256": "0" * 64}
        assert not installer.validate_envelope(changed_digest)

    sample = make("blocked", "execution_disabled", (False,) * 6)
    assert not installer.validate_envelope({**sample, "extra": False})
    assert not installer.validate_envelope({key: value for key, value in sample.items() if key != "error_code"})


def test_initial_service_must_be_absent_and_quiet_but_loaded_conflicts_block() -> None:
    def run_for(raw: bytes, code: int = 0) -> bool:
        return installer._initial_unit_absent(lambda *_args, **_kwargs: _result(raw, code))

    absent = b"LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
    loaded = b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
    active = absent.replace(b"ActiveState=inactive", b"ActiveState=active")
    failed = absent.replace(b"SubState=dead", b"SubState=failed")
    nonzero = absent.replace(b"MainPID=0", b"MainPID=1")
    control_busy = absent.replace(b"ControlPID=0", b"ControlPID=1")
    assert run_for(absent)
    assert not run_for(loaded)
    assert not run_for(active)
    assert not run_for(failed)
    assert not run_for(nonzero)
    assert not run_for(control_busy)
    assert not run_for(absent, 1)


def test_backup_unit_must_be_loaded_inactive_and_dead() -> None:
    quiet = b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
    active = quiet.replace(b"ActiveState=inactive", b"ActiveState=activating")
    assert installer._backup_quiescent(lambda *_args, **_kwargs: _result(quiet))
    assert not installer._backup_quiescent(lambda *_args, **_kwargs: _result(active))


def test_installed_service_assets_are_exact_fixed_source_bytes() -> None:
    root = Path.cwd()
    unit = (root / installer.PACKAGE_SOURCES["unit"]).read_bytes()
    sudoers = (root / installer.PACKAGE_SOURCES["sudoers"]).read_bytes()
    assert b"TimeoutStartSec=30s" in unit
    assert b"TimeoutStopSec=5s" in unit
    assert b"KillMode=control-group" in unit
    assert b"SendSIGKILL=yes" in unit
    assert b"ExecStart=/usr/bin/python3 -I /usr/local/libexec/odysseus-maintenance-bootstrap/supervisor.py" in unit
    assert b"StandardOutput=null" in unit and b"StandardError=null" in unit
    assert b"PrivateTmp=no" in unit
    assert b"ProtectSystem=strict" in unit and b"ReadWritePaths=/var/lib" in unit
    assert b"--wait" in sudoers and b"/usr/bin/python3 -I /usr/local/libexec/odysseus-maintenance-bootstrap/readback.py" in sudoers
    assert b"NOSETENV" in sudoers
    assert b"python -c" not in sudoers and b"systemd-run" not in sudoers


@pytest.mark.skipif(os.name != "posix", reason="inode rollback uses POSIX descriptor operations")
def test_rollback_removes_only_the_exact_inode_created_by_this_installer() -> None:
    with tempfile.TemporaryDirectory(prefix="bootstrap-installer-") as temp:
        root = Path(temp)
        target = root / "target"
        target.write_bytes(b"owned")
        fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            owned = os.stat("target", dir_fd=fd, follow_symlinks=False)
            moved = root / "owned-moved"
            target.rename(moved)
            target.write_bytes(b"foreign")
            assert not installer._owned_remove(fd, "target", owned, directory=False, api=os)
            assert target.read_bytes() == b"foreign"
            assert moved.read_bytes() == b"owned"
        finally:
            os.close(fd)


def test_package_pin_manifest_covers_only_fixed_installed_assets() -> None:
    assert set(installer.PACKAGE_SOURCES) == set(installer.EXPECTED_SOURCE_SHA256)
    for name, relative in installer.PACKAGE_SOURCES.items():
        raw = Path.cwd().joinpath(*relative.split("/")).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == installer.EXPECTED_SOURCE_SHA256[name]


def _tree(root: Path) -> None:
    for relative, mode in (
        ("var", 0o755),
        ("var/lib", 0o755),
        ("var/lib/odysseus-predeploy-backup-root-helper", 0o700),
        ("tmp", 0o1777),
        ("usr", 0o755),
        ("usr/local", 0o755),
        ("usr/local/libexec", 0o755),
        ("etc", 0o755),
        ("etc/systemd", 0o755),
        ("etc/systemd/system", 0o755),
        ("etc/sudoers.d", 0o755),
    ):
        path = root / relative
        path.mkdir(exist_ok=True)
        os.chmod(path, mode)


class _FaultAPI:
    def __init__(self, target: str | None, *, root: Path | None = None) -> None:
        self.target = target
        self.root = root
        self.names: dict[int, str] = {}
        self.paths: dict[int, Path] = {}
        self.failures = 0
        self.postcreate_failures = 0
        self.closed_postcreate: list[str] = []
        self.removed_names: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(os, name)

    def open(self, name: str, *args: object, **kwargs: object) -> int:
        fd = os.open(name, *args, **kwargs)
        self.names[fd] = str(name)
        parent_fd = kwargs.get("dir_fd")
        if type(parent_fd) is int and parent_fd in self.paths:
            self.paths[fd] = self.paths[parent_fd] / name
        elif self.root is not None and os.path.isabs(name):
            self.paths[fd] = Path(name)
        elif self.root is not None:
            self.paths[fd] = self.root / name
        return fd

    def close(self, fd: int) -> None:
        name = self.names.pop(fd, None)
        path = self.paths.pop(fd, None)
        os.close(fd)
        if self.target and self.target.endswith(":fstat") and name and path and self._matches(name):
            self.closed_postcreate.append(name)
            moved = path.with_name(path.name + ".created-unknown")
            if path.exists():
                path.rename(moved)
                if path.suffix == ".service" or path.name == "odysseus-maintenance-lifecycle-bootstrap":
                    path.write_bytes(b"foreign replacement")
                else:
                    path.write_bytes(b"foreign replacement")

    def _matches(self, name: str) -> bool:
        return {
            "stage-fstat": name.startswith(".odysseus-bootstrap-"),
            "asset-file-fstat": name == "lifecycle.py",
            "unit-fstat": name == "odysseus-maintenance-lifecycle-bootstrap.service",
            "arm-fstat": name == "arm.json",
            "sudoers-fstat": name == "odysseus-maintenance-lifecycle-bootstrap",
        }.get((self.target or "").split(":", 1)[0], False)

    def fstat(self, fd: int) -> object:
        name = self.names.get(fd, "")
        if self.target and self.target.endswith(":fstat") and self._matches(name) and self.postcreate_failures == 0:
            self.postcreate_failures += 1
            raise OSError("fixture post-create identity failure")
        return os.fstat(fd)

    def stat(self, name: str, *args: object, **kwargs: object) -> object:
        target = (self.target or "").split(":", 1)[0]
        parent_fd = kwargs.get("dir_fd")
        parent = self.paths.get(parent_fd) if type(parent_fd) is int else self.root
        parent_text = str(parent).replace("\\", "/") if parent is not None else ""
        matches_directory = (
            target == "asset-dir" and name == "odysseus-maintenance-bootstrap" and parent_text.endswith("/usr/local/libexec")
        ) or (
            target == "state-dir" and name == "odysseus-maintenance-bootstrap" and parent_text.endswith("/var/lib")
        )
        if self.target and self.target.endswith(":stat") and self.postcreate_failures == 0 and matches_directory:
            self.postcreate_failures += 1
            path = parent / name if parent is not None else None
            if path is not None and path.exists():
                moved = path.with_name(path.name + ".created-unknown")
                path.rename(moved)
                path.mkdir()
            raise OSError("fixture post-mkdir identity failure")
        return os.stat(name, *args, **kwargs)

    def unlink(self, name: str, *args: object, **kwargs: object) -> None:
        self.removed_names.append(name)
        os.unlink(name, *args, **kwargs)

    def rmdir(self, name: str, *args: object, **kwargs: object) -> None:
        self.removed_names.append(name)
        os.rmdir(name, *args, **kwargs)

    def mkdir(self, name: str, *args: object, **kwargs: object) -> None:
        if self.target == "asset-mkdir" and name == installer.ASSET_DIRECTORY.rsplit("/", 1)[-1]:
            raise OSError("fixture mkdir failure")
        os.mkdir(name, *args, **kwargs)

    def fsync(self, fd: int) -> None:
        name = self.names.get(fd)
        matches = self.target is not None and name == self.target
        if self.target == "stage-file-fsync" and type(name) is str and name.startswith(".odysseus-bootstrap-"):
            matches = True
        if self.target == "stage-dir-fsync" and name == "system":
            matches = True
        if matches:
            self.failures += 1
            raise OSError("fixture fsync failure")
        os.fsync(fd)


class _FakePOSIXInstallAPI:
    O_RDONLY = os.O_RDONLY
    O_WRONLY = os.O_WRONLY
    O_RDWR = os.O_RDWR
    O_CREAT = os.O_CREAT
    O_EXCL = os.O_EXCL
    O_DIRECTORY = 1 << 25
    O_NOFOLLOW = 1 << 26
    O_CLOEXEC = 1 << 27
    O_NONBLOCK = 1 << 28

    def __init__(self, root: Path, fault: str) -> None:
        self.root = root
        self.fault = fault
        self.directories: dict[int, Path] = {}
        self.files: dict[int, Path] = {}
        self.next_fd = 300_000
        self.metadata: dict[Path, tuple[int, int, int, int]] = {}
        self.created_fds: dict[int, str] = {}
        self.created_dirs: set[Path] = set()
        self.failed = False
        self.closed_create_names: list[str] = []
        self.uncertain_paths: list[Path] = []
        self.removed_names: list[str] = []
        self.open_flags: list[tuple[str, int]] = []

    def _path(self, name: str, dir_fd: int | None) -> Path:
        return Path(name) if dir_fd is None else self.directories[dir_fd] / name

    def _default_metadata(self, path: Path) -> tuple[int, int, int, int]:
        relative = path.relative_to(self.root) if path != self.root else Path()
        parts = relative.parts
        if not parts:
            return (0, 0, 1, 0o755)
        if parts == ("tmp",):
            return (0, 0, 1, 0o1777)
        if parts == ("var", "lib", "odysseus-predeploy-backup-root-helper"):
            return (0, 0, 1, 0o700)
        if path.is_dir():
            return (0, 0, 1, 0o755)
        return (0, 0, 1, 0o600)

    def _info(self, path: Path) -> object:
        actual = path.stat()
        uid, gid, links, mode = self.metadata.get(path, self._default_metadata(path))
        return types.SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino, st_mode=(stat.S_IFDIR if path.is_dir() else stat.S_IFREG) | mode, st_uid=uid, st_gid=gid, st_nlink=links)

    def open(self, name: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        path = self._path(name, dir_fd)
        self.open_flags.append((name, flags))
        if flags & self.O_DIRECTORY:
            descriptor = self.next_fd
            self.next_fd += 1
            self.directories[descriptor] = path
            return descriptor
        stripped = flags & ~(self.O_DIRECTORY | self.O_NOFOLLOW | self.O_CLOEXEC | self.O_NONBLOCK)
        descriptor = os.open(str(path), stripped, mode)
        self.files[descriptor] = path
        if flags & self.O_CREAT and flags & self.O_EXCL:
            self.created_fds[descriptor] = name
            self.metadata[path] = (0, 0, 1, mode)
        return descriptor

    def close(self, fd: int) -> None:
        if fd in self.directories:
            del self.directories[fd]
            return
        path = self.files.pop(fd)
        name = self.created_fds.pop(fd, None)
        os.close(fd)
        if name is not None and self.failed and self._matches(name) and self.fault.endswith(":fstat"):
            self.closed_create_names.append(name)
            self.uncertain_paths.append(path)
            moved = path.with_name(path.name + ".created-unknown")
            if path.exists():
                path.rename(moved)
                path.write_bytes(b"foreign replacement")
                self.metadata[path] = (0, 0, 1, 0o600)

    def _matches(self, name: str) -> bool:
        target = self.fault.split(":", 1)[0]
        return {
            "stage": name.startswith(".odysseus-bootstrap-"),
            "asset-file": name == "lifecycle.py",
            "unit": name == "odysseus-maintenance-lifecycle-bootstrap.service",
            "arm": name == "arm.json",
            "sudoers": name == "odysseus-maintenance-lifecycle-bootstrap",
        }.get(target, False)

    def fstat(self, fd: int) -> object:
        path = self.directories[fd] if fd in self.directories else self.files[fd]
        name = self.created_fds.get(fd)
        if name is not None and not self.failed and self.fault.endswith(":fstat") and self._matches(name):
            self.failed = True
            raise OSError("fixture post-create descriptor identity failure")
        return self._info(path)

    def stat(self, name: str, *, dir_fd: int, follow_symlinks: bool = False) -> object:
        path = self.directories[dir_fd] / name
        target = self.fault.split(":", 1)[0]
        if path in self.created_dirs and not self.failed and self.fault.endswith(":stat") and target in {"asset-dir", "state-dir"}:
            expected_parent = "/usr/local/libexec" if target == "asset-dir" else "/var/lib"
            if str(path.parent).replace("\\", "/").endswith(expected_parent):
                self.failed = True
                self.uncertain_paths.append(path)
                moved = path.with_name(path.name + ".created-unknown")
                path.rename(moved)
                path.mkdir()
                self.metadata[path] = (0, 0, 1, 0o755)
                raise OSError("fixture post-mkdir identity failure")
        return self._info(path)

    def mkdir(self, name: str, mode: int = 0o777, *, dir_fd: int) -> None:
        path = self.directories[dir_fd] / name
        path.mkdir()
        self.metadata[path] = (0, 0, 1, mode)
        self.created_dirs.add(path)

    def fchown(self, fd: int, uid: int, gid: int) -> None:
        path = self.directories[fd] if fd in self.directories else self.files[fd]
        _old_uid, _old_gid, links, mode = self.metadata.get(path, self._default_metadata(path))
        self.metadata[path] = (uid, gid, links, mode)

    def fchmod(self, fd: int, mode: int) -> None:
        path = self.directories[fd] if fd in self.directories else self.files[fd]
        uid, gid, links, _old_mode = self.metadata.get(path, self._default_metadata(path))
        self.metadata[path] = (uid, gid, links, mode)

    def fsync(self, _fd: int) -> None:
        return None

    def write(self, fd: int, data: object) -> int:
        return os.write(fd, data)

    def read(self, fd: int, size: int) -> bytes:
        return os.read(fd, size)

    def unlink(self, name: str, *, dir_fd: int) -> None:
        self.removed_names.append(name)
        (self.directories[dir_fd] / name).unlink()

    def rmdir(self, name: str, *, dir_fd: int) -> None:
        self.removed_names.append(name)
        (self.directories[dir_fd] / name).rmdir()

    def geteuid(self) -> int:
        return 0


class _FakeFlock:
    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 8

    def flock(self, _fd: int, _operation: int) -> None:
        return None


def _fake_posix_tree(root: Path) -> None:
    for relative in (
        "var/lib/odysseus-predeploy-backup-root-helper",
        "tmp",
        "usr/local/libexec",
        "etc/systemd/system",
        "etc/sudoers.d",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="uses a POSIX-shaped API adapter to execute the installer on Windows")
@pytest.mark.parametrize("fault,expected", [
    ("stage:fstat", ("stage_cleanup_ambiguous", "000000")),
    ("asset-dir:stat", ("publication_ambiguous", "100000")),
    ("asset-file:fstat", ("publication_ambiguous", "100000")),
    ("unit:fstat", ("publication_ambiguous", "110000")),
    ("state-dir:stat", ("publication_ambiguous", "111000")),
    ("arm:fstat", ("publication_ambiguous", "111000")),
    ("sudoers:fstat", ("sudoers_publication_ambiguous", "111110")),
])
def test_windows_fake_posix_postcreate_failures_preserve_unknown_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str, expected: tuple[str, str]) -> None:
    _fake_posix_tree(tmp_path)
    api = _FakePOSIXInstallAPI(tmp_path, fault)
    flock = _FakeFlock()
    monkeypatch.setattr(installer, "os", types.SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(installer, "fcntl", flock)
    monkeypatch.setattr(lifecycle, "fcntl", flock)
    monkeypatch.setattr(lifecycle, "homebase_private_gid", lambda: (1000, 1000))

    def runner(command: tuple[str, ...], **_kwargs: object) -> object:
        if command[:2] == ("/usr/bin/systemctl", "show"):
            unit = command[-1]
            raw = b"LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n" if unit == installer.SERVICE_UNIT else b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
            return _result(raw)
        if command[0] in {"/usr/bin/systemd-analyze", "/usr/sbin/visudo"} or command == ("/usr/bin/systemctl", "daemon-reload"):
            return _result(b"")
        raise AssertionError("unexpected fixed installer command")

    token = 0

    def token_bytes(size: int) -> bytes:
        nonlocal token
        token += 1
        return bytes([token]) * size

    result = installer.install(execute=True, operations=installer.InstallOperations(api=api, root=str(tmp_path), source_root=str(Path.cwd()), euid=0, runner=runner, clock=lambda: 1_800_000_000, token_bytes=token_bytes))
    assert installer.validate_envelope(result)
    flags = "".join("1" if result[name] else "0" for name in ("publication_attempted", "published_assets", "unit_published", "arm_created", "sudoers_attempted", "sudoers_published"))
    assert (result["error_code"], flags) == expected
    assert result["status"] == "unknown" and result["manual_recovery_required"] is True
    assert api.failed is True
    uncertain_names = {path.name for path in api.uncertain_paths}
    assert not uncertain_names.intersection(api.removed_names)
    if fault.endswith(":fstat"):
        assert api.closed_create_names
    assert api.uncertain_paths
    assert all(path.exists() and path.with_name(path.name + ".created-unknown").exists() for path in api.uncertain_paths)


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0, reason="requires a root-owned temporary POSIX install tree")
@pytest.mark.parametrize("fault", [None, "asset-mkdir", "supervisor.py", "daemon-reload", "sudoers", "stage-cleanup", "stage-swap", "stage-file-fsync", "stage-dir-fsync", "rollback-stop", "shared-before", "shared-after-lock", "stage-fstat", "asset-dir:stat", "asset-file-fstat", "unit-fstat", "state-dir:stat", "arm-fstat", "sudoers-fstat"])
def test_install_transaction_is_pinned_locked_and_exposes_sudoers_last(monkeypatch: pytest.MonkeyPatch, fault: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="bootstrap-install-e2e-") as temp:
        root = Path(temp)
        _tree(root)
        events: list[str] = []
        real_close_lock = lifecycle.close_lock

        def close_lock(fd: int, *, api: object = os) -> None:
            events.append("release-lock")
            real_close_lock(fd, api=api)

        monkeypatch.setattr(lifecycle, "homebase_private_gid", lambda: (1000, 1000))
        monkeypatch.setattr(lifecycle, "close_lock", close_lock)
        real_legacy_acquire = installer._acquire_legacy_root
        real_state_acquire = lifecycle.acquire_root_state_lock

        def legacy_acquire(*args: object, **kwargs: object) -> int:
            events.append("acquire-legacy")
            descriptor = real_legacy_acquire(*args, **kwargs)
            if fault == "shared-after-lock":
                (root / "var/lib/odysseus-maintenance").mkdir(mode=0o750)
            return descriptor

        def state_acquire(path: str, **kwargs: object) -> int:
            events.append("acquire-" + path.rsplit("/", 1)[-1])
            return real_state_acquire(path, **kwargs)

        monkeypatch.setattr(installer, "_acquire_legacy_root", legacy_acquire)
        monkeypatch.setattr(lifecycle, "acquire_root_state_lock", state_acquire)

        def runner(command: tuple[str, ...], **_kwargs: object) -> object:
            if command[:2] == ("/usr/bin/systemctl", "show"):
                unit = command[-1]
                raw = (b"LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n" if unit == installer.SERVICE_UNIT else b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n")
                return _result(raw)
            if command[0] == "/usr/bin/systemd-analyze":
                assert command[-1].endswith(".service")
                events.append("validate-unit")
                return _result(b"")
            if command[0] == "/usr/sbin/visudo":
                events.append("validate-sudoers")
                return _result(b"")
            if command == ("/usr/bin/systemctl", "daemon-reload"):
                events.append("daemon-reload")
                return _result(b"", 1 if fault == "daemon-reload" else 0)
            raise AssertionError("unexpected fixed installer command")

        original_create = installer._create_file
        removed: list[str] = []
        stage_cleanup_attempts: list[str] = []
        rollback_attempts: list[str] = []
        original_remove = installer._owned_remove

        def create_file(parent_fd: int, name: str, data: bytes, mode: int, **kwargs: object) -> object:
            if name == "odysseus-maintenance-lifecycle-bootstrap":
                events.append("publish-sudoers")
            if fault == "rollback-stop" and name == "arm.json":
                raise OSError("fixture failure after successful reload")
            return original_create(parent_fd, name, data, mode, **kwargs)

        def owned_remove(parent_fd: int, name: str, info: object, *, directory: bool, api: object) -> bool:
            removed.append(name)
            if name.startswith(".odysseus-bootstrap-"):
                stage_cleanup_attempts.append(name)
            else:
                rollback_attempts.append(name)
            if fault == "stage-cleanup" and name.startswith(".odysseus-bootstrap-"):
                return False
            if fault == "stage-swap" and name.startswith(".odysseus-bootstrap-"):
                parent = Path(os.readlink(f"/proc/self/fd/{parent_fd}"))
                moved = parent / (name + ".owned")
                (parent / name).rename(moved)
                (parent / name).write_bytes(b"foreign-stage")
                return original_remove(parent_fd, name, info, directory=directory, api=api)
            if fault == "rollback-stop" and not name.startswith(".odysseus-bootstrap-"):
                return False
            return original_remove(parent_fd, name, info, directory=directory, api=api)

        monkeypatch.setattr(installer, "_create_file", create_file)
        monkeypatch.setattr(installer, "_owned_remove", owned_remove)
        api_target = "odysseus-maintenance-lifecycle-bootstrap" if fault == "sudoers" else fault if fault in {"asset-mkdir", "supervisor.py", "stage-file-fsync", "stage-dir-fsync"} else None
        api_target = fault if fault in {"stage-fstat", "asset-dir:stat", "asset-file-fstat", "unit-fstat", "state-dir:stat", "arm-fstat", "sudoers-fstat"} else api_target
        api = _FaultAPI(api_target, root=root)
        if fault == "shared-before":
            (root / "var/lib/odysseus-maintenance").mkdir(mode=0o750)
        token_count = 0

        def tokens(size: int) -> bytes:
            nonlocal token_count
            token_count += 1
            return bytes([token_count]) * size

        result = installer.install(execute=True, operations=installer.InstallOperations(api=api, root=str(root), source_root=str(Path.cwd()), euid=0, runner=runner, clock=lambda: 1_800_000_000, token_bytes=tokens))
        assert installer.validate_envelope(result)
        if fault in {"stage-fstat", "asset-dir:stat", "asset-file-fstat", "unit-fstat", "state-dir:stat", "arm-fstat", "sudoers-fstat"}:
            assert api.postcreate_failures == 1
            assert api.closed_postcreate
            assert not api.removed_names
        assert api.failures == (1 if fault in {"supervisor.py", "sudoers", "stage-file-fsync", "stage-dir-fsync"} else 0)
        expected_results = {
            None: ("ready", "installed", "111111"),
            "asset-mkdir": ("blocked", "install_failed_before_exposure", "100000"),
            "supervisor.py": ("unknown", "publication_ambiguous", "100000"),
            "daemon-reload": ("unknown", "publication_ambiguous", "111000"),
            "sudoers": ("unknown", "sudoers_publication_ambiguous", "111110"),
            "stage-cleanup": ("unknown", "stage_cleanup_ambiguous", "000000"),
            "stage-swap": ("unknown", "stage_cleanup_ambiguous", "000000"),
            "stage-file-fsync": ("unknown", "stage_cleanup_ambiguous", "000000"),
            "stage-dir-fsync": ("unknown", "stage_cleanup_ambiguous", "000000"),
            "rollback-stop": ("unknown", "rollback_ambiguous", "111000"),
            "shared-before": ("blocked", "install_failed_before_exposure", "000000"),
            "shared-after-lock": ("blocked", "install_failed_before_exposure", "000000"),
            "stage-fstat": ("unknown", "stage_cleanup_ambiguous", "000000"),
            "asset-dir:stat": ("unknown", "publication_ambiguous", "100000"),
            "asset-file-fstat": ("unknown", "publication_ambiguous", "100000"),
            "unit-fstat": ("unknown", "publication_ambiguous", "110000"),
            "state-dir:stat": ("unknown", "publication_ambiguous", "111000"),
            "arm-fstat": ("unknown", "publication_ambiguous", "111000"),
            "sudoers-fstat": ("unknown", "sudoers_publication_ambiguous", "111110"),
        }
        expected_status, expected_code, expected_flags = expected_results[fault]
        actual_flags = "".join("1" if result[name] else "0" for name in ("publication_attempted", "published_assets", "unit_published", "arm_created", "sudoers_attempted", "sudoers_published"))
        assert (result["status"], result["error_code"], actual_flags) == (expected_status, expected_code, expected_flags)
        if fault is None:
            assert result["status"] == "ready"
            assert result["sudoers_published"] is True
            assert events[:3] == ["acquire-legacy", "acquire-lifecycle.lock", "acquire-execution.lock"]
            assert events.index("release-lock") < events.index("publish-sudoers")
            assets = root / "usr/local/libexec/odysseus-maintenance-bootstrap"
            for name in lifecycle_asset_names():
                source = Path.cwd() / installer.PACKAGE_SOURCES[name]
                assert (assets / name).read_bytes() == source.read_bytes().replace(b"\r\n", b"\n")
                assert stat.S_IMODE((assets / name).stat().st_mode) == 0o700
            arm = json.loads((root / "var/lib/odysseus-maintenance-bootstrap/arm.json").read_text(encoding="ascii"))
            assert set(arm["asset_pins"]) == set(lifecycle_asset_names())
            assert (root / "etc/systemd/system/odysseus-maintenance-lifecycle-bootstrap.service").exists()
            assert (root / "etc/sudoers.d/odysseus-maintenance-lifecycle-bootstrap").exists()
            assert not list(root.rglob(".odysseus-bootstrap-*-*"))
            reopened = installer._acquire_legacy_root(str(root), api=os)
            lifecycle.close_lock(reopened, api=os)
        elif fault == "supervisor.py":
            assert result["status"] == "unknown" and result["manual_recovery_required"] is True
            assert not (root / "etc/sudoers.d/odysseus-maintenance-lifecycle-bootstrap").exists()
            assert (root / "usr/local/libexec/odysseus-maintenance-bootstrap/supervisor.py").exists()
        elif fault == "daemon-reload":
            assert result["status"] == "unknown" and result["manual_recovery_required"] is True
            assert (root / "usr/local/libexec/odysseus-maintenance-bootstrap/supervisor.py").exists()
            assert (root / "etc/systemd/system/odysseus-maintenance-lifecycle-bootstrap.service").exists()
            assert not (root / "etc/sudoers.d/odysseus-maintenance-lifecycle-bootstrap").exists()
        else:
            if fault in {"shared-before", "shared-after-lock"}:
                assert result["status"] == "blocked" and result["error_code"] == "install_failed_before_exposure"
                assert result["publication_attempted"] is False
                assert not (root / "usr/local/libexec/odysseus-maintenance-bootstrap").exists()
            elif fault == "sudoers":
                assert result["manual_recovery_required"] is True
                assert result["status"] == "unknown" and result["sudoers_attempted"] is True
                assert (root / "var/lib/odysseus-maintenance-bootstrap/arm.json").exists()
                assert (root / "etc/sudoers.d/odysseus-maintenance-lifecycle-bootstrap").exists()
            elif fault in {"stage-cleanup", "stage-swap", "stage-file-fsync", "stage-dir-fsync"}:
                assert result["manual_recovery_required"] is True
                assert result["status"] == "unknown" and result["error_code"] == "stage_cleanup_ambiguous"
                assert not (root / "usr/local/libexec/odysseus-maintenance-bootstrap").exists()
                assert not (root / "etc/sudoers.d/odysseus-maintenance-lifecycle-bootstrap").exists()
                if fault in {"stage-cleanup", "stage-file-fsync", "stage-dir-fsync"}:
                    assert any(root.rglob(".odysseus-bootstrap-*.service"))
                else:
                    assert any(root.rglob("*.owned"))
                    assert any(root.rglob(".odysseus-bootstrap-*"))
            else:
                assert result["manual_recovery_required"] is True
                assert result["status"] == "unknown" and result["error_code"] == "rollback_ambiguous"
                assert len(stage_cleanup_attempts) == 2
                assert len(rollback_attempts) == 1
                assert len(removed) == len(stage_cleanup_attempts) + len(rollback_attempts)


def lifecycle_asset_names() -> tuple[str, ...]:
    return ("lifecycle.py", "bootstrap.py", "supervisor.py", "readback.py")

