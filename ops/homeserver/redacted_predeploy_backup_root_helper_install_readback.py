#!/usr/bin/python3
"""Self-contained, descriptor-rooted readback for root-helper installation."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from typing import Any, Mapping

SCHEMA_ID = "odysseus.predeploy_backup_root_helper_install_readback.v2"
DIAGNOSTIC_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_install_diagnostic_readback.v1"
UNIT = "odysseus-predeploy-backup-root-helper.service"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_PATH = STATE_DIR + "/arm.json"
MAX_ASSET_BYTES = 400_000
SYSTEMCTL_TIMEOUT_SECONDS = 5
SYSTEMCTL_OUTPUT_BYTES = 32
ASSETS = (
    ("/usr/local/libexec/odysseus-predeploy-backup-root-helper.py", "f5e6f90169c59daee07dbb82880912b8c959d2bf991748e3363b6dbbc7b57952", 0o700),
    ("/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py", "8d01a6e5ec7593f57136b7bbd2af8b35a9a94f428ec623a14dd2a0e870ab516e", 0o700),
    ("/etc/systemd/system/odysseus-predeploy-backup-root-helper.service", "faf4e165de61d15c033908f0c526bed5af0ab0a15afdb38aa9021a3a9e229090", 0o644),
    ("/etc/sudoers.d/odysseus-predeploy-backup-root-helper", "b64a8eb4e5d71236c2343967379e05731e06b35e2ffce22c6f29021eb026f652", 0o440),
)
_KEYS = frozenset({"schema_id", "status", "assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "unit_inactive", "arm_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})
_DIAGNOSTIC_KEYS = frozenset({"schema_id", "status", "assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "arm_present", "unit_terminal_state", "main_pid_present", "control_pid_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _result(*, assets: bool, parents: bool, state: bool, disabled: bool, inactive: bool, arm: bool) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": "available" if assets and parents and state and disabled and inactive and not arm else "unknown", "assets_valid": assets, "safe_parents": parents, "state_dir_safe": state, "unit_disabled": disabled, "unit_inactive": inactive, "arm_present": arm, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate(value: Any) -> bool:
    boolean_fields = ("assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "unit_inactive", "arm_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")
    try:
        if type(value) is not dict or set(value) != _KEYS: return False
        if type(value.get("schema_id")) is not str or value["schema_id"] != SCHEMA_ID: return False
        if type(value.get("status")) is not str or value["status"] not in {"available", "unknown"}: return False
        if any(type(value.get(key)) is not bool for key in boolean_fields): return False
        if type(value.get("evidence_sha256")) is not str: return False
        available = all(value.get(key) is True for key in ("assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "unit_inactive")) and value.get("arm_present") is False
        return all(value.get(key) is False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and value["status"] == ("available" if available else "unknown") and value["evidence_sha256"] == _digest(value)
    except Exception:
        return False


def _directory_ok(info: Any, mode: int | tuple[int, ...] = (0o755, 0o700)) -> bool:
    return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) in mode and info.st_nlink >= 1)


def _open_parent(path: str, *, api: Any = os) -> tuple[int, str]:
    parts = path.split("/")[1:]
    if not parts or any(not part or part in {".", ".."} for part in parts): raise OSError("path")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    current = api.open("/", flags)
    try:
        if not _directory_ok(api.fstat(current), (0o755,)): raise OSError("root")
        for part in parts[:-1]:
            nxt = api.open(part, flags, dir_fd=current)
            api.close(current); current = nxt
            if not _directory_ok(api.fstat(current)): raise OSError("parent")
        return current, parts[-1]
    except Exception:
        try: api.close(current)
        except Exception: pass
        raise


def _asset_valid(path: str, digest: str, mode: int, *, api: Any = os) -> bool:
    parent = fd = None
    try:
        parent, name = _open_parent(path, api=api)
        fd = api.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
        before = api.fstat(fd)
        identity = tuple(getattr(before, key, None) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
        if not (stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_gid == 0 and stat.S_IMODE(before.st_mode) == mode and before.st_nlink == 1 and 0 < before.st_size <= MAX_ASSET_BYTES): return False
        remaining = before.st_size; hasher = hashlib.sha256()
        while remaining:
            data = api.read(fd, min(8192, remaining))
            if not data or len(data) > remaining: return False
            hasher.update(data); remaining -= len(data)
        after = api.fstat(fd)
        return bool(api.read(fd, 1) == b"" and tuple(getattr(after, key, None) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")) == identity and after.st_nlink == 1 and hasher.hexdigest() == digest)
    except Exception:
        return False
    finally:
        for descriptor in (fd, parent):
            if isinstance(descriptor, int):
                try: api.close(descriptor)
                except Exception: pass


def _parents_valid(*, api: Any = os) -> bool:
    """Independently validate every asset's descriptor-rooted parent walk."""
    for path, _, _ in ASSETS:
        descriptor = None
        try:
            descriptor, _ = _open_parent(path, api=api)
        except Exception:
            return False
        finally:
            if isinstance(descriptor, int):
                try: api.close(descriptor)
                except Exception: return False
    return True


def _state_safe_and_arm_absent(*, api: Any = os) -> tuple[bool, bool]:
    parent = state_directory = None
    try:
        parent, name = _open_parent(STATE_DIR, api=api)
        state_directory = api.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
        info = api.fstat(state_directory)
        if not _directory_ok(info, (0o700,)): return False, True
        identity = tuple(getattr(info, key, None) for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_mtime_ns", "st_ctime_ns"))
        try:
            arm = api.stat("arm.json", dir_fd=state_directory, follow_symlinks=False)
            return True, bool(arm)
        except FileNotFoundError:
            after = api.fstat(state_directory)
            return tuple(getattr(after, key, None) for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_mtime_ns", "st_ctime_ns")) == identity, False
        except Exception:
            return False, True
    except Exception:
        return False, True
    finally:
        for descriptor in (state_directory, parent):
            if isinstance(descriptor, int):
                try: api.close(descriptor)
                except Exception: pass


def _terminate_and_reap(process: Any) -> None:
    try: process.kill()
    except Exception: pass
    try: process.wait(timeout=1)
    except Exception: pass


def _close_streams(process: Any) -> None:
    for stream in (getattr(process, "stdin", None), getattr(process, "stdout", None)):
        try: stream.close()
        except Exception: pass


def _bounded_stdout(process: Any, limit: int) -> tuple[bytes | None, int | None]:
    """Read a child pipe without workers; ``None`` means fail closed."""
    def failed() -> tuple[None, None]:
        _terminate_and_reap(process)
        return None, None
    try:
        stream = process.stdout
        descriptor = stream.fileno()
        if type(descriptor) is not int: return failed()
        os.set_blocking(descriptor, False)
    except Exception:
        return failed()
    output = bytearray(); eof = False; deadline = time.monotonic() + SYSTEMCTL_TIMEOUT_SECONDS
    while True:
        while not eof:
            if len(output) >= limit + 1: return failed()
            try:
                piece = stream.read(min(32, limit + 1 - len(output)))
                if piece is None: break
                if type(piece) is not bytes: return failed()
                if piece == b"": eof = True; break
                output.extend(piece)
            except (BlockingIOError, InterruptedError):
                break
            except Exception:
                return failed()
        if len(output) > limit: return failed()
        try: code = process.poll()
        except Exception: return failed()
        if code is not None:
            return (bytes(output), code) if eof and type(code) is int else failed()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return failed()
        time.sleep(min(0.01, remaining))


def _systemctl_state(argument: str, *, popen: Any = subprocess.Popen) -> bool:
    process = None
    try:
        process = popen(["/usr/bin/systemctl", argument, UNIT], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, bufsize=0)
        output, code = _bounded_stdout(process, SYSTEMCTL_OUTPUT_BYTES)
        expected = {"is-enabled": {b"disabled\n", b"static\n"}, "is-active": {b"inactive\n"}}[argument]
        return code in {0, 1, 3} and output in expected
    except Exception:
        return False
    finally:
        if process is not None:
            _close_streams(process)


def collect(*, api: Any = os, popen: Any = subprocess.Popen) -> dict[str, Any]:
    assets = all(_asset_valid(path, digest, mode, api=api) for path, digest, mode in ASSETS)
    parents = _parents_valid(api=api)
    state, arm = _state_safe_and_arm_absent(api=api)
    return _result(assets=assets, parents=parents, state=state, disabled=_systemctl_state("is-enabled", popen=popen), inactive=_systemctl_state("is-active", popen=popen), arm=arm)


_NONTERMINAL_UNIT_STATES = frozenset({
    ("active", "running"), ("active", "exited"),
    ("activating", "start-pre"), ("activating", "start"), ("activating", "start-post"), ("activating", "auto-restart"),
    ("deactivating", "stop"), ("deactivating", "stop-sigterm"), ("deactivating", "stop-sigkill"),
    ("deactivating", "final-sigterm"), ("deactivating", "final-sigkill"),
    ("reloading", "reload"), ("reloading", "reload-signal"),
})


def _show_unit_state(*, popen: Any = subprocess.Popen) -> tuple[str, bool, bool, bool]:
    process = None
    try:
        process = popen(["/usr/bin/systemctl", "show", "--property=LoadState,UnitFileState,ActiveState,SubState,MainPID,ControlPID", UNIT], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, bufsize=0)
        output, code = _bounded_stdout(process, 256)
        if output is None or code != 0 or not output.endswith(b"\n"): return "unavailable", False, False, False
        try:
            lines = output.decode("ascii").splitlines()
            pairs = [line.split("=", 1) for line in lines]
            values = {key: item for key, item in pairs if key in {"LoadState", "UnitFileState", "ActiveState", "SubState", "MainPID", "ControlPID"}}
            if len(lines) != 6 or len(values) != 6 or any(len(pair) != 2 for pair in pairs) or {pair[0] for pair in pairs} != set(values): return "unavailable", False, False, False
            if values["LoadState"] != "loaded" or values["UnitFileState"] not in {"disabled", "static"}: return "unavailable", False, False, False
            if not values["MainPID"].isdigit() or not values["ControlPID"].isdigit(): return "unavailable", False, False, False
            main, control = int(values["MainPID"]) != 0, int(values["ControlPID"]) != 0
            if values["ActiveState"] == "inactive" and values["SubState"] == "dead": return "inactive", True, main, control
            if values["ActiveState"] == "failed" and values["SubState"] == "failed": return "failed", True, main, control
            if (values["ActiveState"], values["SubState"]) in _NONTERMINAL_UNIT_STATES: return "nonterminal", True, main, control
        except Exception: pass
        return "unavailable", False, False, False
    except Exception:
        return "unavailable", False, False, False
    finally:
        if process is not None:
            _close_streams(process)


def _diagnostic_result(*, assets: bool, parents: bool, state: bool, disabled: bool, arm: bool, terminal: str, main: bool, control: bool) -> dict[str, Any]:
    available = assets and parents and state and disabled and not arm and terminal in {"inactive", "failed"} and not main and not control
    value = {"schema_id": DIAGNOSTIC_SCHEMA_ID, "status": "available" if available else "unknown", "assets_valid": assets, "safe_parents": parents, "state_dir_safe": state, "unit_disabled": disabled, "arm_present": arm, "unit_terminal_state": terminal, "main_pid_present": main, "control_pid_present": control, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_diagnostic(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _DIAGNOSTIC_KEYS or value.get("schema_id") != DIAGNOSTIC_SCHEMA_ID or value.get("status") not in {"available", "unknown"} or value.get("unit_terminal_state") not in {"inactive", "failed", "nonterminal", "unavailable"}: return False
        if any(type(value.get(key)) is not bool for key in ("assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "arm_present", "main_pid_present", "control_pid_present")): return False
        available = all(value.get(key) is True for key in ("assets_valid", "safe_parents", "state_dir_safe", "unit_disabled")) and value.get("arm_present") is False and value.get("unit_terminal_state") in {"inactive", "failed"} and value.get("main_pid_present") is False and value.get("control_pid_present") is False
        return value.get("status") == ("available" if available else "unknown") and all(value.get(key) is False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and type(value.get("evidence_sha256")) is str and value["evidence_sha256"] == _digest(value)
    except Exception: return False


def collect_diagnostic_readback_v2(*, api: Any = os, popen: Any = subprocess.Popen) -> dict[str, Any]:
    assets = all(_asset_valid(path, digest, mode, api=api) for path, digest, mode in ASSETS)
    parents = _parents_valid(api=api)
    state, arm = _state_safe_and_arm_absent(api=api)
    terminal, disabled, main, control = _show_unit_state(popen=popen)
    return _diagnostic_result(assets=assets, parents=parents, state=state, disabled=disabled, arm=arm, terminal=terminal, main=main, control=control)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == []: value = collect()
    elif args == ["--diagnostic-v2"]: value = collect_diagnostic_readback_v2()
    else: return 2
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__": raise SystemExit(main())
