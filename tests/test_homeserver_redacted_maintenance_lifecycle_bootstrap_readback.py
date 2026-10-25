from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

from ops.homeserver import redacted_maintenance_lifecycle_bootstrap_readback as readback


def _quiet_unit() -> dict[str, object]:
    return {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True}


def _install_quiet_readback_fakes(monkeypatch: object, *, state: object = None, metadata: object = None) -> None:
    quiet = _quiet_unit()
    monkeypatch.setattr(readback, "_unit", lambda _unit, _runner: quiet)
    monkeypatch.setattr(readback, "_state", lambda: (state or {"arm": None, "consumed": None, "receipt": None}, True))
    monkeypatch.setattr(readback, "_asset_pins", lambda: (_ for _ in ()).throw(OSError("no armed asset set")))
    monkeypatch.setattr(readback, "_metadata", lambda: metadata)


def test_unit_parser_requires_exact_inactive_dead_pid_zero_properties() -> None:
    good = b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
    parsed = readback._parse_unit(good, 0)
    assert parsed is not None and parsed["inactive_dead"] is True and parsed["main_pid"] == 0
    for raw, code in (
        (good + b"Extra=x\n", 0),
        (good, 1),
        (b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=False\nControlPID=0\n", 0),
    ):
        assert readback._parse_unit(raw, code) is None
    for raw in (
        good.replace(b"MainPID=0", b"MainPID=1"),
        good.replace(b"ControlPID=0", b"ControlPID=3"),
        good.replace(b"ActiveState=inactive", b"ActiveState=active"),
        good.replace(b"SubState=dead", b"SubState=failed"),
    ):
        parsed = readback._parse_unit(raw, 0)
        assert parsed is not None and parsed["inactive_dead"] is False
    assert readback._parse_unit(good.replace(b"ActiveState=inactive", b"ActiveState=untrusted"), 0) is None


def test_live_or_failed_unit_projection_never_becomes_ready(monkeypatch: object) -> None:
    now = int(time.time())
    pins = {name: "a" * 64 for name in readback.ASSET_NAMES}
    arm = {"schema_id": readback.ARM_SCHEMA_ID, "grant_id": "1" * 64, "invocation_id": "2" * 64, "expires_at": now + 60, "asset_pins": pins}
    quiet = {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True}
    active = {"loaded": True, "active_state": "active", "sub_state": "running", "main_pid": 7, "control_pid": 0, "inactive_dead": False}
    current = {readback.SERVICE_UNIT: active, readback.BACKUP_UNIT: quiet}
    monkeypatch.setattr(readback, "_unit", lambda unit, _runner: current[unit])
    monkeypatch.setattr(readback, "_state", lambda: ({"arm": arm, "consumed": None, "receipt": None}, True))
    monkeypatch.setattr(readback, "_asset_pins", lambda: pins)
    monkeypatch.setattr(readback, "_metadata", lambda: None)
    value = readback.readback()
    assert value["status"] == "manual_recovery_required"
    assert value["units"][readback.SERVICE_UNIT]["inactive_dead"] is False
    assert readback.validate_envelope(value)


def test_arm_readback_requires_exact_schema_pins_and_expiry() -> None:
    now = int(time.time())
    valid = {
        "schema_id": readback.ARM_SCHEMA_ID,
        "grant_id": "1" * 64,
        "invocation_id": "2" * 64,
        "expires_at": now + 600,
        "asset_pins": {name: "a" * 64 for name in readback.ASSET_NAMES},
    }
    assert readback._valid_arm(valid)
    assert not readback._valid_arm({**valid, "expires_at": now + 601})
    assert not readback._valid_arm({**valid, "expires_at": True})
    assert not readback._valid_arm({**valid, "asset_pins": {"supervisor.py": "a" * 64}})
    assert not readback._valid_arm({**valid, "unknown": 1})


def test_receipt_must_bind_consumed_grant_invocation_and_exact_digest() -> None:
    pins = {name: "a" * 64 for name in readback.ASSET_NAMES}
    consumed = {"schema_id": readback.ARM_SCHEMA_ID, "grant_id": "1" * 64, "invocation_id": "2" * 64, "expires_at": int(time.time()), "asset_pins": pins}
    metadata = {"directory_uid": 0, "directory_gid": 1001, "directory_mode": 0o750, "lock_uid": 0, "lock_gid": 1001, "lock_mode": 0o660, "lock_nlink": 1}
    receipt = {"schema_id": readback.RECEIPT_SCHEMA_ID, "grant_id": consumed["grant_id"], "invocation_id": consumed["invocation_id"], "asset_pins": pins, "outcome": "ready", "error_code": "none", "shared_metadata": metadata, "recorded_at": int(time.time())}
    receipt["receipt_sha256"] = hashlib.sha256(readback._canonical(receipt)).hexdigest()
    assert readback._valid_receipt(receipt, consumed)
    assert not readback._valid_receipt({**receipt, "grant_id": "3" * 64}, consumed)
    assert not readback._valid_receipt({**receipt, "receipt_sha256": "0" * 64}, consumed)


def test_readback_envelope_detects_state_or_digest_tampering() -> None:
    units = {
        readback.SERVICE_UNIT: {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True},
        readback.BACKUP_UNIT: {"loaded": True, "active_state": "active", "sub_state": "running", "main_pid": 1, "control_pid": 0, "inactive_dead": False},
    }
    value = readback._envelope("manual_recovery_required", "unit_not_quiescent", units=units, pins_valid=False, state={"arm": None, "consumed": None, "receipt": None}, metadata=None, temporary_absent=False)
    assert readback.validate_envelope(value)
    changed = {**value, "retry_permitted": True}
    assert not readback.validate_envelope(changed)


def test_armed_and_completed_require_exact_state_validity_maps() -> None:
    now = int(time.time())
    pins = {name: "a" * 64 for name in readback.ASSET_NAMES}
    arm = {"schema_id": readback.ARM_SCHEMA_ID, "grant_id": "1" * 64, "invocation_id": "2" * 64, "expires_at": now + 60, "asset_pins": pins}
    consumed = {**arm, "expires_at": now}
    metadata = {"directory_uid": 0, "directory_gid": 1001, "directory_mode": 0o750, "lock_uid": 0, "lock_gid": 1001, "lock_mode": 0o660, "lock_nlink": 1}
    receipt = {"schema_id": readback.RECEIPT_SCHEMA_ID, "grant_id": consumed["grant_id"], "invocation_id": consumed["invocation_id"], "asset_pins": pins, "outcome": "ready", "error_code": "none", "shared_metadata": metadata, "recorded_at": now}
    receipt["receipt_sha256"] = hashlib.sha256(readback._canonical(receipt)).hexdigest()
    quiet = {readback.SERVICE_UNIT: {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True}, readback.BACKUP_UNIT: {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True}}
    armed = readback._envelope("armed", "ready_to_start", units=quiet, pins_valid=True, state={"arm": arm, "consumed": None, "receipt": None}, metadata=None, temporary_absent=True)
    assert readback.validate_envelope(armed)
    invalid_armed = {**armed, "state_validity": {**armed["state_validity"], "consumed_present": True, "consumed_valid": False}}
    invalid_armed["evidence_sha256"] = readback._digest(invalid_armed)
    assert not readback.validate_envelope(invalid_armed)
    completed = readback._envelope("completed", "bootstrap_completed", units=quiet, pins_valid=True, state={"arm": None, "consumed": consumed, "receipt": receipt}, metadata={"valid": True, **metadata}, temporary_absent=True)
    assert readback.validate_envelope(completed)
    invalid_completed = {**completed, "state_validity": {**completed["state_validity"], "receipt_valid": False}}
    invalid_completed["evidence_sha256"] = readback._digest(invalid_completed)
    assert not readback.validate_envelope(invalid_completed)


def test_readback_never_exposes_subprocess_output() -> None:
    def runner(command: tuple[str, ...], **kwargs: object) -> object:
        assert command[0] == "/usr/bin/systemctl"
        assert kwargs["stderr"] == __import__("subprocess").DEVNULL
        return type("Result", (), {"returncode": 1, "stdout": b"secret-looking output"})()

    result = readback.readback(runner=runner)
    assert result["status"] == "manual_recovery_required"
    assert result["raw_output_visible"] is False
    assert readback.validate_envelope(result)


def test_readback_projects_only_validated_nested_state(monkeypatch: object) -> None:
    sentinel = "hostile-nested-value-must-never-escape"
    quiet = {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True}
    monkeypatch.setattr(readback, "_unit", lambda _unit, _runner: quiet)
    monkeypatch.setattr(readback, "_state", lambda: ({"arm": {"schema_id": sentinel, "extra": sentinel}, "consumed": None, "receipt": None}, True))
    monkeypatch.setattr(readback, "_asset_pins", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(readback, "_metadata", lambda: {"valid": False, "untrusted": sentinel})

    value = readback.readback()
    serialized = json.dumps(value, sort_keys=True)
    assert value["status"] == "manual_recovery_required"
    assert value["arm"] is None and value["consumed"] is None and value["receipt"] is None
    assert value["state_validity"] == {"arm_present": True, "arm_valid": False, "consumed_present": False, "consumed_valid": True, "receipt_present": False, "receipt_valid": True}
    assert value["shared_metadata"] == {"valid": False}
    assert sentinel not in serialized
    assert readback.validate_envelope(value)


def test_envelope_validator_rejects_hostile_nested_shapes() -> None:
    quiet = {"loaded": True, "active_state": "inactive", "sub_state": "dead", "main_pid": 0, "control_pid": 0, "inactive_dead": True}
    value = readback._envelope(
        "blocked",
        "not_armed",
        units={readback.SERVICE_UNIT: quiet, readback.BACKUP_UNIT: quiet},
        pins_valid=False,
        state={"arm": None, "consumed": None, "receipt": None},
        metadata=None,
        temporary_absent=True,
    )
    hostile_unit = {**value, "units": {**value["units"], readback.SERVICE_UNIT: {**quiet, "active_state": "unbounded-" + "x" * 400}}}
    hostile_unit["evidence_sha256"] = readback._digest(hostile_unit)
    assert not readback.validate_envelope(hostile_unit)
    unhashable_state = {**value, "units": {**value["units"], readback.SERVICE_UNIT: {**quiet, "active_state": []}}}
    unhashable_state["evidence_sha256"] = readback._digest(unhashable_state)
    assert not readback.validate_envelope(unhashable_state)
    hostile_arm = {**value, "arm": {"schema_id": readback.ARM_SCHEMA_ID, "grant_id": "a" * 64, "invocation_id": "b" * 64, "expires_at": int(time.time()) + 60, "asset_pins": {name: "c" * 64 for name in readback.ASSET_NAMES}, "unknown": "untrusted"}}
    hostile_arm["evidence_sha256"] = readback._digest(hostile_arm)
    assert not readback.validate_envelope(hostile_arm)


def test_clean_absence_is_the_only_blocked_not_armed_projection(monkeypatch: object) -> None:
    _install_quiet_readback_fakes(monkeypatch)
    value = readback.readback()
    assert value["status"] == "blocked"
    assert value["error_code"] == "not_armed"
    assert value["state_validity"] == {
        "arm_present": False,
        "arm_valid": True,
        "consumed_present": False,
        "consumed_valid": True,
        "receipt_present": False,
        "receipt_valid": True,
    }
    assert value["temporary_absent"] is True
    assert value["asset_pins_valid"] is False
    assert value["shared_metadata"] is None
    assert readback.validate_envelope(value)


@pytest.mark.parametrize("failure", [ValueError("malformed"), OSError("oversize"), PermissionError("unsafe or unreadable")])
def test_state_read_failures_are_manual_recovery_and_never_clean_blocked(monkeypatch: object, failure: BaseException) -> None:
    _install_quiet_readback_fakes(monkeypatch)
    monkeypatch.setattr(readback, "_state", lambda: (_ for _ in ()).throw(failure))
    value = readback.readback()
    assert value["status"] == "manual_recovery_required"
    assert value["error_code"] == "state_mismatch_or_consumed"
    assert value["state_validity"] == {
        "arm_present": False,
        "arm_valid": True,
        "consumed_present": False,
        "consumed_valid": True,
        "receipt_present": False,
        "receipt_valid": True,
    }
    assert value["temporary_absent"] is False
    assert readback.validate_envelope(value)


@pytest.mark.parametrize("metadata", [
    {"valid": False},
    {"valid": True, "directory_uid": 0, "directory_gid": 1001, "directory_mode": 0o750, "lock_uid": 0, "lock_gid": 1001, "lock_mode": 0o660, "lock_nlink": 1},
])
def test_shared_metadata_without_transaction_state_is_manual_recovery(monkeypatch: object, metadata: dict[str, object]) -> None:
    _install_quiet_readback_fakes(monkeypatch, metadata=metadata)
    value = readback.readback()
    assert value["status"] == "manual_recovery_required"
    assert value["error_code"] == "state_mismatch_or_consumed"
    assert value["shared_metadata"] is not None
    assert readback.validate_envelope(value)


class _MetadataAPI:
    O_RDONLY = os.O_RDONLY
    O_DIRECTORY = getattr(os, "O_DIRECTORY", 1 << 20)
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 1 << 21)
    O_CLOEXEC = getattr(os, "O_CLOEXEC", 1 << 22)

    def __init__(self, invalid: str | None = None) -> None:
        self.invalid = invalid
        self.directories: dict[int, Path] = {}
        self.next_fd = 200_000

    def open(self, name: str, flags: int, *, dir_fd: int | None = None) -> int:
        path = Path(name) if dir_fd is None else self.directories[dir_fd] / name
        if flags & self.O_DIRECTORY:
            descriptor = self.next_fd
            self.next_fd += 1
            self.directories[descriptor] = path
            return descriptor
        raise AssertionError("metadata readback opens directories only")

    def close(self, fd: int) -> None:
        del self.directories[fd]

    def _info(self, path: Path) -> object:
        mode = 0o750 if path.name == "odysseus-maintenance" else 0o755 if path.is_dir() else 0o660
        uid, gid, links = 0, 1002 if path.name == "odysseus-maintenance" or path.name == "lifecycle.lock" else 0, 1
        if self.invalid == "directory-owner" and path.name == "odysseus-maintenance":
            uid = 1001
        if self.invalid == "directory-group" and path.name == "odysseus-maintenance":
            gid = 1003
        if self.invalid == "directory-mode" and path.name == "odysseus-maintenance":
            mode = 0o770
        if self.invalid == "lock-owner" and path.name == "lifecycle.lock":
            uid = 1001
        if self.invalid == "lock-group" and path.name == "lifecycle.lock":
            gid = 1003
        if self.invalid == "lock-mode" and path.name == "lifecycle.lock":
            mode = 0o600
        if self.invalid == "lock-links" and path.name == "lifecycle.lock":
            links = 2
        actual = path.stat()
        return types.SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino, st_mode=(stat.S_IFDIR if path.is_dir() else stat.S_IFREG) | mode, st_uid=uid, st_gid=gid, st_nlink=links)

    def fstat(self, fd: int) -> object:
        return self._info(self.directories[fd])

    def stat(self, name: str, *, dir_fd: int, follow_symlinks: bool = False) -> object:
        return self._info(self.directories[dir_fd] / name)


class _MetadataAccounts:
    def getpwnam(self, _name: str) -> object:
        return types.SimpleNamespace(pw_uid=1001, pw_gid=1002)

    def getpwall(self) -> list[object]:
        return [types.SimpleNamespace(pw_name="homebase", pw_gid=1002)]


class _MetadataGroups:
    def getgrgid(self, _gid: int) -> object:
        return types.SimpleNamespace(gr_mem=["homebase"])


@pytest.mark.parametrize("invalid", [None, "directory-owner", "directory-group", "directory-mode", "lock-owner", "lock-group", "lock-mode", "lock-links"])
def test_real_shared_metadata_accepts_private_gid_and_rejects_wrong_identities(tmp_path: Path, invalid: str | None) -> None:
    shared = tmp_path / "var/lib/odysseus-maintenance"
    shared.mkdir(parents=True)
    (shared / "lifecycle.lock").write_bytes(b"")
    metadata = readback._metadata(root=str(tmp_path), api=_MetadataAPI(invalid), users=_MetadataAccounts(), groups=_MetadataGroups())
    if invalid is None:
        assert metadata == {"valid": True, "directory_uid": 0, "directory_gid": 1002, "directory_mode": 0o750, "lock_uid": 0, "lock_gid": 1002, "lock_mode": 0o660, "lock_nlink": 1}
        assert readback._valid_metadata({key: value for key, value in metadata.items() if key != "valid"}, include_valid=False)
    else:
        assert metadata == {"valid": False}


def test_packaged_readback_starts_isolated_without_repository_imports(tmp_path: Path) -> None:
    import shutil

    packaged = tmp_path / "readback.py"
    shutil.copyfile(Path.cwd() / "ops/homeserver/redacted_maintenance_lifecycle_bootstrap_readback.py", packaged)
    result = subprocess.run([sys.executable, "-I", str(packaged)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15)
    assert result.returncode == 1
    assert result.stderr == b""
    value = json.loads(result.stdout)
    assert readback.validate_envelope(value)
    assert value["status"] == "manual_recovery_required"
    assert value["raw_output_visible"] is False and value["secret_values_visible"] is False


def test_status_matrix_rejects_contradictory_quiescence_and_absence(monkeypatch: object) -> None:
    _install_quiet_readback_fakes(monkeypatch)
    blocked = readback.readback()
    for changed in (
        {**blocked, "temporary_absent": False},
        {**blocked, "shared_metadata": {"valid": False}},
        {**blocked, "asset_pins_valid": True},
    ):
        changed["evidence_sha256"] = readback._digest(changed)
        assert not readback.validate_envelope(changed)

    active = {"loaded": True, "active_state": "active", "sub_state": "running", "main_pid": 4, "control_pid": 0, "inactive_dead": False}
    state_mismatch = readback._envelope(
        "manual_recovery_required",
        "state_mismatch_or_consumed",
        units={readback.SERVICE_UNIT: active, readback.BACKUP_UNIT: _quiet_unit()},
        pins_valid=False,
        state={"arm": None, "consumed": None, "receipt": None},
        metadata=None,
        temporary_absent=False,
    )
    assert not readback.validate_envelope(state_mismatch)


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0, reason="requires root-owned POSIX filesystem fixtures")
@pytest.mark.parametrize("content,mode", [(b"{", 0o600), (b"{} " + b" " * readback.MAX_JSON_BYTES, 0o600), (b"{}", 0o644)])
def test_state_rejects_malformed_oversize_and_unsafe_state_files(content: bytes, mode: int, monkeypatch: object) -> None:
    with tempfile.TemporaryDirectory(prefix="bootstrap-readback-state-") as temp:
        state = Path(temp, "state")
        state.mkdir(mode=0o700)
        os.chmod(state, 0o700)
        arm = state / readback.ARM_NAME
        arm.write_bytes(content)
        os.chmod(arm, mode)
        monkeypatch.setattr(readback, "STATE_DIRECTORY", str(state))
        monkeypatch.setattr(readback, "_open_dir", lambda _path: os.open(str(state), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
        with pytest.raises((OSError, ValueError, json.JSONDecodeError)):
            readback._state()

