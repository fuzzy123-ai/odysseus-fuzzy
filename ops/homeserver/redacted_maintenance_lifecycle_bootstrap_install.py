#!/usr/bin/env python3
"""Inert-by-default root installer for the fixed one-use bootstrap service."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import fcntl
except ImportError:
    fcntl = None

from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle
from ops.homeserver import redacted_maintenance_lifecycle_bootstrap_supervisor as supervisor


SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_install.v1"
ASSET_DIRECTORY = "/usr/local/libexec/odysseus-maintenance-bootstrap"
UNIT_PATH = "/etc/systemd/system/odysseus-maintenance-lifecycle-bootstrap.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-maintenance-lifecycle-bootstrap"
STATE_DIRECTORY = "/var/lib/odysseus-maintenance-bootstrap"
SHARED_DIRECTORY = "/var/lib/odysseus-maintenance"
SERVICE_UNIT = "odysseus-maintenance-lifecycle-bootstrap.service"
MAX_SOURCE_BYTES = 350_000
MAX_OUTPUT_BYTES = 512

# Filled from the reviewed package bytes. The installer itself is deliberately
# excluded, avoiding a circular self-hash while pinning every installed asset.
EXPECTED_SOURCE_SHA256: dict[str, str] = {
    "lifecycle.py": "82f5deae8288c94434f3728c7e0791d5af5bb08e3dbaf9f1d7591447ed72a766",
    "bootstrap.py": "60cbd0cb37b30f237f4d23146912112eb1db0055f0e4981cc31f90a75448ce28",
    "supervisor.py": "3ee6f2be7d60dda0c2a2b0a1d430ab0d7e8571fdea08b81aebea5f4821c574f7",
    "readback.py": "0b2a21344f7242ddbf81dc4478aeee05adc0d986261823a1817fe0a427c90b05",
    "unit": "97fe4766f3902bcfee3e6f081b9fc75305993208c11f555ceb30dcb950954496",
    "sudoers": "a4d2aaea7a777d3eb7b105bd01ce6053cff605276dc93c18ff88a2a3c0658652",
}

PACKAGE_SOURCES = {
    "lifecycle.py": "ops/homeserver/redacted_maintenance_lifecycle_lock.py",
    "bootstrap.py": "ops/homeserver/redacted_maintenance_lifecycle_bootstrap.py",
    "supervisor.py": "ops/homeserver/redacted_maintenance_lifecycle_bootstrap_supervisor.py",
    "readback.py": "ops/homeserver/redacted_maintenance_lifecycle_bootstrap_readback.py",
    "unit": "ops/homeserver/root-helper/odysseus-maintenance-lifecycle-bootstrap.service",
    "sudoers": "ops/homeserver/root-helper/odysseus-maintenance-lifecycle-bootstrap.sudoers",
}
ASSET_MODES = {"lifecycle.py": 0o700, "bootstrap.py": 0o700, "supervisor.py": 0o700, "readback.py": 0o700}
_KEYS = frozenset({"schema_id", "status", "error_code", "publication_attempted", "published_assets", "unit_published", "arm_created", "sudoers_attempted", "sudoers_published", "manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible", "evidence_sha256"})
_ALLOWED_ENVELOPE_FLAGS = {
    ("ready", "installed"): frozenset({(True, True, True, True, True, True)}),
    **{
        ("blocked", code): frozenset({(False, False, False, False, False, False)})
        for code in (
            "execution_disabled",
            "identity_unavailable",
            "unit_not_quiescent",
            "invalid_installer_identity",
            "unit_validation_failed",
            "sudoers_validation_failed",
        )
    },
    ("blocked", "install_failed_before_exposure"): frozenset({
        (False, False, False, False, False, False),
        (True, False, False, False, False, False),
    }),
    ("unknown", "stage_cleanup_ambiguous"): frozenset({(False, False, False, False, False, False)}),
    ("unknown", "publication_ambiguous"): frozenset({
        (True, False, False, False, False, False),
        (True, True, False, False, False, False),
        (True, True, True, False, False, False),
    }),
    ("unknown", "rollback_ambiguous"): frozenset({
        (True, False, False, False, False, False),
        (True, True, False, False, False, False),
        (True, True, True, False, False, False),
        (True, True, True, True, False, False),
    }),
    ("unknown", "sudoers_publication_ambiguous"): frozenset({
        (True, True, True, True, True, False),
    }),
}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({key: item for key, item in value.items() if key != "evidence_sha256"})).hexdigest()


def _envelope(status: str, code: str, *, attempted: bool = False, assets: bool = False, unit: bool = False, arm: bool = False, sudoers_attempted: bool = False, sudoers: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status, "error_code": code, "publication_attempted": attempted, "published_assets": assets, "unit_published": unit, "arm_created": arm, "sudoers_attempted": sudoers_attempted, "sudoers_published": sudoers, "manual_recovery_required": status == "unknown", "retry_permitted": False, "raw_output_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID:
        return False
    if type(value.get("status")) is not str or value["status"] not in {"ready", "blocked", "unknown"} or type(value.get("error_code")) is not str:
        return False
    for key in _KEYS - {"schema_id", "status", "error_code", "evidence_sha256"}:
        if type(value.get(key)) is not bool:
            return False
    if type(value.get("evidence_sha256")) is not str:
        return False
    if value["retry_permitted"] or value["manual_recovery_required"] != (value["status"] == "unknown") or value["raw_output_visible"] or value["secret_values_visible"]:
        return False
    code = value["error_code"]
    flags = (value["publication_attempted"], value["published_assets"], value["unit_published"], value["arm_created"], value["sudoers_attempted"], value["sudoers_published"])
    return flags in _ALLOWED_ENVELOPE_FLAGS.get((value["status"], code), ()) and value["evidence_sha256"] == _digest(value)


def _entry_absent(parent_fd: int, name: str, *, api: Any) -> bool:
    try:
        api.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return False
    except FileNotFoundError:
        return True


def _same_inode(a: Any, b: Any) -> bool:
    return a.st_dev == b.st_dev and a.st_ino == b.st_ino


def _safe_dir(info: Any) -> bool:
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and info.st_nlink >= 1 and stat.S_IMODE(info.st_mode) & 0o022 == 0


def _open_dir(root: str, path: str, *, api: Any) -> int:
    if type(path) is not str or not path.startswith("/") or "//" in path or ".." in path.split("/"):
        raise OSError(errno.EINVAL, "invalid fixed path")
    root_fd = api.open(root, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC)
    fd = root_fd
    try:
        for component in filter(None, path.split("/")):
            child = api.open(component, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=fd)
            if fd != root_fd:
                api.close(fd)
            fd = child
            if not _safe_dir(api.fstat(fd)):
                raise OSError(errno.EPERM, "unsafe fixed parent")
        if fd == root_fd:
            root_fd = -1
        return fd
    except BaseException:
        if fd >= 0:
            api.close(fd)
        if root_fd >= 0 and root_fd != fd:
            api.close(root_fd)
        raise


def _target(root: str, path: str) -> str:
    return os.path.join(root, path.lstrip("/")) if root != "/" else path


def _parents_absent_check(root: str, path: str, *, api: Any) -> tuple[int, str]:
    parent, _, name = path.rpartition("/")
    fd = _open_dir(root, parent or "/", api=api)
    try:
        api.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return fd, name
    api.close(fd)
    raise FileExistsError(errno.EEXIST, "fixed target already exists")


def _source_bytes(source_root: str) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for name, relative in PACKAGE_SOURCES.items():
        candidate = Path(source_root, *relative.split("/"))
        raw = candidate.read_bytes().replace(b"\r\n", b"\n")
        if not raw or len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("invalid fixed package source")
        expected = EXPECTED_SOURCE_SHA256.get(name)
        if type(expected) is not str or len(expected) != 64 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("fixed package pin mismatch")
        values[name] = raw
    return values


def _run_suppressed(runner: Callable[..., Any], command: tuple[str, ...], *, timeout: int) -> bool:
    try:
        result = runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, timeout=timeout, check=False)
        return type(getattr(result, "returncode", None)) is int and result.returncode == 0
    except BaseException:
        return False


def _unit_state(runner: Callable[..., Any], unit: str) -> dict[str, str] | None:
    command = ("/usr/bin/systemctl", "show", "--no-pager", "--property=LoadState", "--property=ActiveState", "--property=SubState", "--property=MainPID", "--property=ControlPID", unit)
    try:
        result = runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, timeout=5, check=False)
        raw = getattr(result, "stdout", None)
        if type(raw) is not bytes or len(raw) > MAX_OUTPUT_BYTES or type(getattr(result, "returncode", None)) is not int or result.returncode != 0 or not raw.isascii():
            return False
        lines = raw.splitlines()
        if len(lines) != 5 or any(line.count(b"=") != 1 for line in lines):
            return None
        pairs = [line.decode("ascii").split("=", 1) for line in lines]
        fields = dict(pairs)
        if len(fields) != len(pairs):
            return None
        if set(fields) != {"LoadState", "ActiveState", "SubState", "MainPID", "ControlPID"} or not fields["MainPID"].isdigit() or not fields["ControlPID"].isdigit() or int(fields["MainPID"]) > 2_147_483_647 or int(fields["ControlPID"]) > 2_147_483_647:
            return None
        return fields
    except BaseException:
        return None


def _backup_quiescent(runner: Callable[..., Any]) -> bool:
    state = _unit_state(runner, "odysseus-predeploy-backup-root-helper.service")
    return state == {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "MainPID": "0", "ControlPID": "0"}


def _backup_arm_absent(root: str, *, api: Any) -> bool:
    try:
        fd = _open_dir(root, "/var/lib/odysseus-predeploy-backup-root-helper", api=api)
    except BaseException:
        return False
    try:
        try:
            api.stat("arm.json", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    except BaseException:
        return False
    finally:
        api.close(fd)


def _acquire_legacy_root(root: str, *, api: Any) -> int:
    """Root-side installer lock acquisition with the legacy owner contract."""
    uid, gid = lifecycle.homebase_private_gid()
    if fcntl is None:
        raise OSError(errno.ENOTSUP, "POSIX flock unavailable")
    parent = lifecycle._open_tmp_parent(root=root, api=api)
    descriptor: int | None = None
    try:
        try:
            descriptor = lifecycle._acquire_existing(parent, "odysseus-auto-update.lock", uid=uid, gid=gid, mode=lifecycle.LEGACY_LOCK_MODE, api=api)
        except FileNotFoundError:
            descriptor = api.open("odysseus-auto-update.lock", api.O_RDWR | api.O_CREAT | api.O_EXCL | api.O_NOFOLLOW | api.O_CLOEXEC, lifecycle.LEGACY_LOCK_MODE, dir_fd=parent)
            api.fchown(descriptor, uid, gid)
            api.fchmod(descriptor, lifecycle.LEGACY_LOCK_MODE)
            api.fsync(descriptor)
            api.fsync(parent)
            lifecycle._check_named_descriptor(parent, "odysseus-auto-update.lock", descriptor, uid=uid, gid=gid, mode=lifecycle.LEGACY_LOCK_MODE, api=api)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lifecycle._check_named_descriptor(parent, "odysseus-auto-update.lock", descriptor, uid=uid, gid=gid, mode=lifecycle.LEGACY_LOCK_MODE, api=api)
        result = descriptor
        descriptor = None
        return result
    finally:
        if descriptor is not None:
            api.close(descriptor)
        api.close(parent)


def _initial_unit_absent(runner: Callable[..., Any]) -> bool:
    state = _unit_state(runner, SERVICE_UNIT)
    # An absent installed unit is the only acceptable initial state. A loaded,
    # masked, failed, activating, or otherwise unknown unit may be a conflict.
    return state == {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead", "MainPID": "0", "ControlPID": "0"}


def _write_fd(fd: int, data: bytes, *, api: Any) -> None:
    view = memoryview(data)
    while view:
        count = api.write(fd, view)
        if count <= 0:
            raise OSError(errno.EIO, "short fixed write")
        view = view[count:]


class _TrackingAPI:
    def __init__(self, api: Any, on_fsync_error: Callable[[], None]) -> None:
        self._api = api
        self._on_fsync_error = on_fsync_error

    def __getattr__(self, name: str) -> Any:
        return getattr(self._api, name)

    def fsync(self, fd: int) -> None:
        try:
            self._api.fsync(fd)
        except BaseException:
            self._on_fsync_error()
            raise


def _create_file(parent_fd: int, name: str, data: bytes, mode: int, *, api: Any, on_created: Callable[[Any], None] | None = None, on_identity_uncertain: Callable[[], None] | None = None, sync: Callable[[int], None] | None = None) -> Any:
    fd = api.open(name, api.O_WRONLY | api.O_CREAT | api.O_EXCL | api.O_NOFOLLOW | api.O_CLOEXEC, mode, dir_fd=parent_fd)
    try:
        try:
            info = api.fstat(fd)
        except BaseException:
            if on_identity_uncertain is not None:
                on_identity_uncertain()
            raise
        if on_created is not None:
            on_created(info)
        api.fchown(fd, 0, 0)
        api.fchmod(fd, mode)
        _write_fd(fd, data, api=api)
        (sync or api.fsync)(fd)
        held = api.fstat(fd)
        named = api.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_inode(held, named) or not stat.S_ISREG(held.st_mode) or held.st_uid != 0 or held.st_gid != 0 or stat.S_IMODE(held.st_mode) != mode or held.st_nlink != 1:
            raise OSError(errno.EPERM, "published file identity mismatch")
        verify_fd = api.open(name, api.O_RDONLY | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=parent_fd)
        try:
            verify_info = api.fstat(verify_fd)
            verify_named = api.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not _same_inode(held, verify_info) or not _same_inode(verify_info, verify_named) or not stat.S_ISREG(verify_info.st_mode) or verify_info.st_nlink != 1:
                raise OSError(errno.ESTALE, "published file changed during verification")
            copied = bytearray()
            while len(copied) <= len(data):
                part = api.read(verify_fd, min(65536, len(data) + 1 - len(copied)))
                if not part:
                    break
                copied.extend(part)
            if bytes(copied) != data:
                raise OSError(errno.EBADMSG, "published file bytes mismatch")
        finally:
            api.close(verify_fd)
        return held
    finally:
        api.close(fd)


def _owned_remove(parent_fd: int, name: str, info: Any, *, directory: bool, api: Any) -> bool:
    try:
        current = api.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_inode(current, info):
            return False
        if directory:
            api.rmdir(name, dir_fd=parent_fd)
        else:
            api.unlink(name, dir_fd=parent_fd)
        api.fsync(parent_fd)
        return True
    except FileNotFoundError:
        return True
    except BaseException:
        return False


@dataclass
class InstallOperations:
    api: Any = os
    root: str = "/"
    source_root: str = "/opt/odysseus"
    euid: int | None = None
    runner: Callable[..., Any] = subprocess.run
    clock: Callable[[], float] = time.time
    token_bytes: Callable[[int], bytes] = secrets.token_bytes

    def current_euid(self) -> int:
        return int(self.api.geteuid() if self.euid is None else self.euid)


def install(*, execute: bool = False, operations: InstallOperations | None = None) -> dict[str, Any]:
    if execute is not True:
        return _envelope("blocked", "execution_disabled")
    ops = operations or InstallOperations()
    held: list[int] = []
    parents: list[tuple[int, str, Any, bool]] = []
    opened_parents: list[int] = []
    publication_attempted = False
    fsync_ambiguous = False
    reload_ambiguous = False
    reload_succeeded = False
    stage_cleanup_ambiguous = False
    stage_identity_ambiguous = False
    untracked_create_ambiguous = False
    published_assets = published_unit = arm_created = sudoers_attempted = sudoers_published = False
    state_fd = None
    def mark_fsync_error() -> None:
        nonlocal fsync_ambiguous
        fsync_ambiguous = True
    api = _TrackingAPI(ops.api, mark_fsync_error)
    root = ops.root
    if os.name != "posix" or ops.current_euid() != 0:
        return _envelope("blocked", "identity_unavailable")
    try:
        if not _initial_unit_absent(ops.runner) or not _backup_quiescent(ops.runner) or not _backup_arm_absent(root, api=api):
            return _envelope("blocked", "unit_not_quiescent")
        sources = _source_bytes(ops.source_root)
        asset_names = tuple(supervisor.ASSET_NAMES)
        # Check every exact target before acquiring or creating anything.
        asset_parent, asset_name = _parents_absent_check(root, ASSET_DIRECTORY, api=api)
        opened_parents.append(asset_parent)
        unit_parent, unit_name = _parents_absent_check(root, UNIT_PATH, api=api)
        opened_parents.append(unit_parent)
        sudo_parent, sudo_name = _parents_absent_check(root, SUDOERS_PATH, api=api)
        opened_parents.append(sudo_parent)
        state_parent, state_name = _parents_absent_check(root, STATE_DIRECTORY, api=api)
        opened_parents.append(state_parent)
        shared_parent, shared_name = _parents_absent_check(root, SHARED_DIRECTORY, api=api)
        opened_parents.append(shared_parent)
        for lock_path in (lifecycle.LEGACY_LOCK, lifecycle.BACKUP_LIFECYCLE_LOCK, lifecycle.BACKUP_EXECUTION_LOCK):
            if lock_path == lifecycle.LEGACY_LOCK:
                held.append(_acquire_legacy_root(root, api=api))
            else:
                held.append(lifecycle.acquire_root_state_lock(lock_path, root=root, api=api))
        if not _initial_unit_absent(ops.runner) or not _backup_quiescent(ops.runner) or not _backup_arm_absent(root, api=api):
            raise OSError(errno.EBUSY, "maintenance state changed during install preflight")
        for parent_fd, name in ((asset_parent, asset_name), (unit_parent, unit_name), (sudo_parent, sudo_name), (state_parent, state_name), (shared_parent, shared_name)):
            if not _entry_absent(parent_fd, name, api=api):
                raise FileExistsError(errno.EEXIST, "fixed target appeared during install")

        # Validate staged sudoers and unit before any authority-bearing target
        # is exposed. Stage files are hidden siblings with exclusive creation.
        token = ops.token_bytes(16).hex()
        if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
            return _envelope("blocked", "invalid_installer_identity")
        unit_stage = ".odysseus-bootstrap-unit-" + token + ".service"
        sudo_stage = ".odysseus-bootstrap-sudoers-" + token
        staged: list[tuple[int, str, Any]] = []
        validation_error: str | None = None
        try:
            def sync(fd: int) -> None:
                api.fsync(fd)

            def mark_stage_identity_uncertain() -> None:
                nonlocal stage_identity_ambiguous
                stage_identity_ambiguous = True

            unit_info = _create_file(unit_parent, unit_stage, sources["unit"], 0o600, api=api, on_created=lambda item: staged.append((unit_parent, unit_stage, item)), on_identity_uncertain=mark_stage_identity_uncertain, sync=sync)
            sudo_info = _create_file(sudo_parent, sudo_stage, sources["sudoers"], 0o600, api=api, on_created=lambda item: staged.append((sudo_parent, sudo_stage, item)), on_identity_uncertain=mark_stage_identity_uncertain, sync=sync)
            sync(unit_parent)
            sync(sudo_parent)
            unit_stage_path = _target(root, UNIT_PATH.rpartition("/")[0] + "/" + unit_stage)
            sudo_stage_path = _target(root, SUDOERS_PATH.rpartition("/")[0] + "/" + sudo_stage)
            if not _run_suppressed(ops.runner, ("/usr/bin/systemd-analyze", "verify", unit_stage_path), timeout=10):
                validation_error = "unit_validation_failed"
            elif not _run_suppressed(ops.runner, ("/usr/sbin/visudo", "-cf", sudo_stage_path), timeout=10):
                validation_error = "sudoers_validation_failed"
        finally:
            # Staged validator inputs must be gone before package publication
            # or sudoers authority exposure. Stop at the first ambiguous
            # inode, unlink, or directory-sync result and preserve the rest.
            if fsync_ambiguous or stage_identity_ambiguous:
                stage_cleanup_ambiguous = True
            else:
                for parent_fd, name, info in reversed(staged):
                    if not _owned_remove(parent_fd, name, info, directory=False, api=api):
                        stage_cleanup_ambiguous = True
                        break
        if stage_cleanup_ambiguous:
            raise OSError(errno.EIO, "staged validation cleanup is ambiguous")
        if validation_error is not None:
            return _envelope("blocked", validation_error)

        # Create the private asset directory first and populate it while
        # mode 0700 keeps it unavailable to homebase.
        publication_attempted = True
        api.mkdir(asset_name, 0o700, dir_fd=asset_parent)
        untracked_create_ambiguous = True
        asset_info = api.stat(asset_name, dir_fd=asset_parent, follow_symlinks=False)
        parents.append((asset_parent, asset_name, asset_info, True))
        untracked_create_ambiguous = False
        if not stat.S_ISDIR(asset_info.st_mode) or asset_info.st_uid != 0 or asset_info.st_gid != 0 or stat.S_IMODE(asset_info.st_mode) != 0o700:
            raise OSError(errno.EPERM, "unsafe new asset directory")
        sync(asset_parent)
        asset_fd = api.open(asset_name, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=asset_parent)
        opened_parents.append(asset_fd)
        def mark_untracked_create_ambiguous() -> None:
            nonlocal untracked_create_ambiguous
            untracked_create_ambiguous = True

        for name in asset_names:
            _create_file(asset_fd, name, sources[name], ASSET_MODES[name], api=api, on_created=lambda item, n=name: parents.append((asset_fd, n, item, False)), on_identity_uncertain=mark_untracked_create_ambiguous, sync=sync)
        sync(asset_fd)
        api.fchmod(asset_fd, 0o755)
        sync(asset_fd)
        sync(asset_parent)
        published_assets = True

        _create_file(unit_parent, unit_name, sources["unit"], 0o644, api=api, on_created=lambda item: parents.append((unit_parent, unit_name, item, False)), on_identity_uncertain=mark_untracked_create_ambiguous, sync=sync)
        sync(unit_parent)
        published_unit = True
        if not _run_suppressed(ops.runner, ("/usr/bin/systemctl", "daemon-reload"), timeout=10):
            reload_ambiguous = True
            raise OSError(errno.EIO, "daemon reload failed")
        reload_succeeded = True

        # State and arm are installed after the unit is ready and before
        # dropping the lock chain. This makes the grant permanently one-use.
        api.mkdir(state_name, 0o700, dir_fd=state_parent)
        untracked_create_ambiguous = True
        state_info = api.stat(state_name, dir_fd=state_parent, follow_symlinks=False)
        parents.append((state_parent, state_name, state_info, True))
        untracked_create_ambiguous = False
        if not stat.S_ISDIR(state_info.st_mode) or state_info.st_uid != 0 or state_info.st_gid != 0 or stat.S_IMODE(state_info.st_mode) != 0o700:
            raise OSError(errno.EPERM, "unsafe new state directory")
        sync(state_parent)
        state_fd = api.open(state_name, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=state_parent)
        opened_parents.append(state_fd)
        pins = {name: hashlib.sha256(sources[name]).hexdigest() for name in asset_names}
        arm = {"schema_id": supervisor.ARM_SCHEMA_ID, "grant_id": ops.token_bytes(32).hex(), "invocation_id": ops.token_bytes(32).hex(), "expires_at": int(ops.clock()) + 600, "asset_pins": pins}
        if not supervisor.validate_arm(arm, now=int(ops.clock())):
            raise OSError(errno.EINVAL, "invalid fixed arm")
        arm_raw = _canonical(arm)
        _create_file(state_fd, supervisor.ARM_NAME, arm_raw, 0o600, api=api, on_created=lambda item: parents.append((state_fd, supervisor.ARM_NAME, item, False)), on_identity_uncertain=mark_untracked_create_ambiguous, sync=sync)
        sync(state_fd)
        arm_created = True

        while held:
            descriptor = held.pop()
            lifecycle.close_lock(descriptor, api=api)

        # This is the first privilege-authority exposure. Any failure from
        # this point leaves all evidence intact for manual recovery.
        sudoers_attempted = True
        _create_file(sudo_parent, sudo_name, sources["sudoers"], 0o440, api=api, on_created=lambda item: parents.append((sudo_parent, sudo_name, item, False)), on_identity_uncertain=mark_untracked_create_ambiguous, sync=sync)
        sync(sudo_parent)
        sudoers_published = True
        return _envelope("ready", "installed", attempted=True, assets=True, unit=True, arm=True, sudoers_attempted=True, sudoers=True)
    except BaseException:
        if stage_cleanup_ambiguous:
            return _envelope("unknown", "stage_cleanup_ambiguous")
        if sudoers_attempted:
            return _envelope("unknown", "sudoers_publication_ambiguous", attempted=True, assets=published_assets, unit=published_unit, arm=arm_created, sudoers_attempted=True, sudoers=sudoers_published)
        if untracked_create_ambiguous:
            return _envelope("unknown", "publication_ambiguous", attempted=True, assets=published_assets, unit=published_unit, arm=arm_created)
        if fsync_ambiguous or reload_ambiguous:
            return _envelope("unknown", "publication_ambiguous", attempted=True, assets=published_assets, unit=published_unit, arm=arm_created)
        rollback_ok = True
        for parent_fd, name, info, is_directory in reversed(parents):
            if not _owned_remove(parent_fd, name, info, directory=is_directory, api=api):
                rollback_ok = False
                break
        if rollback_ok and reload_succeeded and published_unit:
            rollback_ok = _run_suppressed(ops.runner, ("/usr/bin/systemctl", "daemon-reload"), timeout=10)
        if publication_attempted and not rollback_ok:
            return _envelope("unknown", "rollback_ambiguous", attempted=True, assets=published_assets, unit=published_unit, arm=arm_created)
        return _envelope("blocked", "install_failed_before_exposure", attempted=publication_attempted, assets=False, unit=False, arm=False)
    finally:
        while held:
            descriptor = held.pop()
            try:
                lifecycle.close_lock(descriptor, api=api)
            except BaseException:
                pass
        for fd in opened_parents:
            try:
                api.close(fd)
            except BaseException:
                pass


def main() -> int:
    value = install()
    print(_canonical(value).decode("ascii"))
    return 0 if validate_envelope(value) and value["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
