#!/usr/bin/python3
"""Self-contained, descriptor-rooted readback for root-helper installation."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
from typing import Any, Mapping

SCHEMA_ID = "odysseus.predeploy_backup_root_helper_install_readback.v2"
UNIT = "odysseus-predeploy-backup-root-helper.service"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_PATH = STATE_DIR + "/arm.json"
MAX_ASSET_BYTES = 400_000
SYSTEMCTL_TIMEOUT_SECONDS = 5
SYSTEMCTL_OUTPUT_BYTES = 32
ASSETS = (
    ("/usr/local/libexec/odysseus-predeploy-backup-root-helper.py", "56119595274556615a3e83e1f637bd2035232180a0a0005aa3938d08ca3efb81", 0o700),
    ("/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py", "e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046", 0o700),
    ("/etc/systemd/system/odysseus-predeploy-backup-root-helper.service", "466de2f889a00ee2759bd06380ddb213f8c0f4cee5644e3a2e27083863c1ab98", 0o644),
    ("/etc/sudoers.d/odysseus-predeploy-backup-root-helper", "1a6a7f1ec4d328c9fed20b758a87bb9905b68145122f6751fd5eeea748d5847d", 0o440),
)
_KEYS = frozenset({"schema_id", "status", "assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "unit_inactive", "arm_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _result(*, assets: bool, parents: bool, state: bool, disabled: bool, inactive: bool, arm: bool) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": "available" if assets and parents and state and disabled and inactive and not arm else "unknown", "assets_valid": assets, "safe_parents": parents, "state_dir_safe": state, "unit_disabled": disabled, "unit_inactive": inactive, "arm_present": arm, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate(value: Any) -> bool:
    return bool(type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("status") in {"available", "unknown"} and all(value.get(k) is False for k in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and value.get("status") == ("available" if all(value.get(k) is True for k in ("assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "unit_inactive")) and value.get("arm_present") is False else "unknown") and value.get("evidence_sha256") == _digest(value))


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


def _systemctl_state(argument: str, *, popen: Any = subprocess.Popen) -> bool:
    try:
        process = popen(["/usr/bin/systemctl", argument, UNIT], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
        output = bytearray()
        def reader() -> None:
            try:
                while len(output) <= SYSTEMCTL_OUTPUT_BYTES:
                    part = process.stdout.read(min(16, SYSTEMCTL_OUTPUT_BYTES + 1 - len(output)))
                    if not part: break
                    output.extend(part)
            except Exception: output.clear(); output.extend(b"?")
        thread = threading.Thread(target=reader, daemon=True); thread.start()
        try: process.wait(timeout=SYSTEMCTL_TIMEOUT_SECONDS)
        except Exception:
            try: process.kill(); process.wait(timeout=1)
            except Exception: pass
            return False
        thread.join(1)
        if thread.is_alive() or len(output) > SYSTEMCTL_OUTPUT_BYTES: return False
        expected = {"is-enabled": {b"disabled\n", b"static\n"}, "is-active": {b"inactive\n"}}[argument]
        return getattr(process, "returncode", None) in {0, 1, 3} and bytes(output) in expected
    except Exception:
        return False


def collect(*, api: Any = os, popen: Any = subprocess.Popen) -> dict[str, Any]:
    assets = all(_asset_valid(path, digest, mode, api=api) for path, digest, mode in ASSETS)
    parents = _parents_valid(api=api)
    state, arm = _state_safe_and_arm_absent(api=api)
    return _result(assets=assets, parents=parents, state=state, disabled=_systemctl_state("is-enabled", popen=popen), inactive=_systemctl_state("is-active", popen=popen), arm=arm)


def main() -> int:
    print(json.dumps(collect(), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__": raise SystemExit(main())
