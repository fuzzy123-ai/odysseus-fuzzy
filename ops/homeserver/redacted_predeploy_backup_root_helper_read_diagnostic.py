#!/usr/bin/env python3
"""Read-only diagnostic for the installed root-helper mount/Restic corridor."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import types
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_read_diagnostic.v1"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
HELPER_SHA256 = "56119595274556615a3e83e1f637bd2035232180a0a0005aa3938d08ca3efb81"
MAX_HELPER_BYTES = 400_000
MAX_OUTPUT_BYTES = 65_536
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CODES = frozenset({"none", "execution_disabled", "preflight_failed", "identity_failed", "preexec_failed", "restic_read_failed", "read_timeout", "read_output_invalid"})
_KEYS = frozenset({"schema_id", "status", "error_code", "execution_lock_held", "identity_bound", "read_command_invoked", "snapshot_shape_valid", "repository_write_invoked", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def envelope(status: str, code: str, *, locked: bool = False, bound: bool = False, invoked: bool = False, shape: bool = False, effect: bool = False, recovery: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status if status in {"blocked", "observed", "unknown"} else "unknown", "error_code": code if code in _CODES else "preflight_failed", "execution_lock_held": locked is True, "identity_bound": bound is True, "read_command_invoked": invoked is True, "snapshot_shape_valid": shape is True, "repository_write_invoked": False, "effect_may_have_occurred": effect is True, "manual_recovery_required": recovery is True, "retry_permitted": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "environment_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if not (type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("error_code") in _CODES and value.get("repository_write_invoked") is False and value.get("retry_permitted") is False and all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible")) and value.get("evidence_sha256") == _digest(value)): return False
    if value["status"] == "blocked": return bool(value["error_code"] in {"execution_disabled", "preflight_failed", "identity_failed"} and value["read_command_invoked"] is False and value["snapshot_shape_valid"] is False and value["effect_may_have_occurred"] is False and value["manual_recovery_required"] is False)
    if value["status"] == "observed": return bool(value["error_code"] in {"none", "preexec_failed", "restic_read_failed", "read_output_invalid"} and value["execution_lock_held"] is True and value["identity_bound"] is True and value["read_command_invoked"] is True and value["snapshot_shape_valid"] is (value["error_code"] == "none") and value["effect_may_have_occurred"] is False and value["manual_recovery_required"] is False)
    return bool(value["status"] == "unknown" and value["error_code"] == "read_timeout" and value["execution_lock_held"] is True and value["identity_bound"] is True and value["read_command_invoked"] is True and value["effect_may_have_occurred"] is True and value["manual_recovery_required"] is True)


def _helper_source(path: str = HELPER_PATH) -> bytes | None:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700 and info.st_nlink == 1 and 0 < info.st_size <= MAX_HELPER_BYTES): return None
        raw = bytearray()
        while len(raw) <= MAX_HELPER_BYTES:
            piece = os.read(descriptor, min(8192, MAX_HELPER_BYTES + 1 - len(raw)))
            if not piece: break
            raw.extend(piece)
        return bytes(raw) if len(raw) == info.st_size and len(raw) <= MAX_HELPER_BYTES and os.read(descriptor, 1) == b"" and hashlib.sha256(raw).hexdigest() == HELPER_SHA256 else None
    except Exception: return None
    finally:
        if isinstance(descriptor, int):
            try: os.close(descriptor)
            except Exception: pass


def _snapshot_shape(raw: bytes) -> bool:
    try:
        value = json.loads(raw.decode("utf-8"))
        return bool(type(value) is list and len(value) == 1 and type(value[0]) is dict and isinstance(value[0].get("id"), str) and _HEX64.fullmatch(value[0]["id"]) and isinstance(value[0].get("paths"), list) and isinstance(value[0].get("tags"), list))
    except Exception: return False


def _run_read_only() -> dict[str, Any]:
    source = _helper_source()
    if os.geteuid() != 0 or source is None: return envelope("blocked", "preflight_failed")
    name = "_odysseus_pinned_installed_root_helper_diagnostic_"; module = types.ModuleType(name); module.__file__ = "<verified-installed-root-helper>"; sys.modules[name] = module
    try: exec(compile(source, module.__file__, "exec"), module.__dict__, module.__dict__)
    except Exception:
        sys.modules.pop(name, None); return envelope("blocked", "preflight_failed")
    helper = module.__dict__; lock = bound = None
    try:
        lock = helper["_acquire_run_lock"]()
        try: bound = helper["_bind_identities"]()
        except Exception: return envelope("blocked", "identity_failed", locked=True)
        command = (helper["RESTIC_BINARY"], "-r", helper["REPOSITORY"], "--no-lock", "snapshots", "--latest", "1", "--json")
        code, raw, overflow = helper["_run_child"](bound, command, 20, True)
        if code is None: return envelope("unknown", "read_timeout", locked=True, bound=True, invoked=True, effect=True, recovery=True)
        if code == 125: return envelope("observed", "preexec_failed", locked=True, bound=True, invoked=True)
        if code != 0: return envelope("observed", "restic_read_failed", locked=True, bound=True, invoked=True)
        shape = not overflow and type(raw) is bytes and len(raw) <= MAX_OUTPUT_BYTES and _snapshot_shape(raw)
        return envelope("observed", "none" if shape else "read_output_invalid", locked=True, bound=True, invoked=True, shape=shape)
    except Exception:
        return envelope("blocked", "preflight_failed") if lock is None else envelope("observed", "read_output_invalid", locked=True, bound=bound is not None, invoked=bound is not None)
    finally:
        if bound is not None:
            for descriptor in (bound.source_fd, bound.repository_fd, bound.credential_fd, bound.restic_fd):
                try: os.close(descriptor)
                except Exception: pass
        if isinstance(lock, int):
            try: os.close(lock)
            except Exception: pass
        sys.modules.pop(name, None)


def collect(*, execute: bool = False, runner: Callable[[], dict[str, Any]] = _run_read_only) -> dict[str, Any]:
    if execute is not True: return envelope("blocked", "execution_disabled")
    try: value = runner()
    except Exception: return envelope("blocked", "preflight_failed")
    return dict(value) if validate_envelope(value) else envelope("blocked", "preflight_failed")


def main() -> int:
    print(json.dumps(envelope("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
