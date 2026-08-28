#!/usr/bin/env python3
"""Fixed-stage, read-only diagnostic for the installed root-helper child setup."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
import types
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_preexec_stage_diagnostic.v1"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
HELPER_SHA256 = "56119595274556615a3e83e1f637bd2035232180a0a0005aa3938d08ca3efb81"
MAX_HELPER_BYTES = 400_000
TIMEOUT_SECONDS = 20
STAGES = frozenset({"none", "namespace_or_private_propagation", "private_view_setup", "credential_directory_or_source_open_tree", "source_move_mount", "repository_open_tree", "repository_move_mount", "canonical_identity_or_source_readonly_remount", "credential_materialization", "credential_readonly_remount", "mount_postflight", "descriptor_close", "identity_drop", "execveat", "restic_read"})
_CODES = frozenset({"none", "execution_disabled", "preflight_failed", "stage_failed", "restic_read_failed", "timeout"})
_KEYS = frozenset({"schema_id", "status", "error_code", "first_failed_stage", "execution_lock_held", "identity_bound", "read_command_invoked", "repository_write_invoked", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
_TOKEN_STAGE = {
    b"": "namespace_or_private_propagation",
    b"P": "private_view_setup",
    b"V": "credential_directory_or_source_open_tree",
    b"S": "source_move_mount",
    b"s": "repository_open_tree",
    b"T": "repository_move_mount",
    b"t": "canonical_identity_or_source_readonly_remount",
    b"R": "credential_materialization",
    b"K": "mount_postflight",
    b"M": "descriptor_close",
    b"C": "identity_drop",
    b"I": "execveat",
}


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def envelope(status: str, code: str, stage: str, *, locked: bool = False, bound: bool = False, invoked: bool = False, effect: bool = False, recovery: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status if status in {"blocked", "observed", "unknown"} else "unknown", "error_code": code if code in _CODES else "preflight_failed", "first_failed_stage": stage if stage in STAGES else "none", "execution_lock_held": locked is True, "identity_bound": bound is True, "read_command_invoked": invoked is True, "repository_write_invoked": False, "effect_may_have_occurred": effect is True, "manual_recovery_required": recovery is True, "retry_permitted": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "environment_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if not (type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("error_code") in _CODES and value.get("first_failed_stage") in STAGES and value.get("repository_write_invoked") is False and value.get("retry_permitted") is False and all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible")) and value.get("evidence_sha256") == _digest(value)): return False
    if value["status"] == "blocked": return bool(value["error_code"] in {"execution_disabled", "preflight_failed"} and value["first_failed_stage"] == "none" and all(value[key] is False for key in ("execution_lock_held", "identity_bound", "read_command_invoked", "effect_may_have_occurred", "manual_recovery_required")))
    if value["status"] == "unknown": return bool(value["error_code"] == "timeout" and value["first_failed_stage"] == "none" and value["execution_lock_held"] is True and value["identity_bound"] is True and value["read_command_invoked"] is True and value["effect_may_have_occurred"] is False and value["manual_recovery_required"] is False)
    observed = value["status"] == "observed" and value["execution_lock_held"] is True and value["identity_bound"] is True and value["read_command_invoked"] is True and value["effect_may_have_occurred"] is False and value["manual_recovery_required"] is False
    if value["error_code"] == "none": return bool(observed and value["first_failed_stage"] == "none")
    if value["error_code"] == "restic_read_failed": return bool(observed and value["first_failed_stage"] == "restic_read")
    return bool(observed and value["error_code"] == "stage_failed" and value["first_failed_stage"] not in {"none", "restic_read"})


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


def _child_result(helper: Mapping[str, Any], bound: Any, timeout: int = TIMEOUT_SECONDS) -> tuple[int | None, bytes]:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 1); os.dup2(null, 2); os.close(null)
            def mark(token: bytes) -> None:
                if token not in _TOKEN_STAGE: os._exit(125)
                if os.write(write_fd, token) != 1: os._exit(125)
            native = helper["_syscalls"]()
            if native is None: os._exit(125)
            syscall = native[0]; syscall_count = 0; mount_count = 0
            libc = helper["ctypes"].CDLL(None, use_errno=True); native_mount = libc.mount
            def staged_syscall(number: int, *args: Any) -> int:
                nonlocal syscall_count
                result = syscall(number, *args); syscall_count += 1
                tokens = (b"S", b"s", b"T", b"t")
                success = result >= 0 if syscall_count in {1, 3} else result == 0
                if success and syscall_count <= len(tokens): mark(tokens[syscall_count - 1])
                return result
            def staged_mount(*args: Any) -> int:
                nonlocal mount_count
                result = native_mount(*args); mount_count += 1
                tokens = (b"P", b"V", b"R", b"K")
                if result == 0 and mount_count <= len(tokens): mark(tokens[mount_count - 1])
                return result
            helper["_mount_setup"](bound, syscall=staged_syscall, mount_call=staged_mount); mark(b"M")
            for descriptor in (bound.source_fd, bound.repository_fd, bound.credential_fd): helper["_close_verified"](descriptor)
            mark(b"C"); helper["_drop_identity"](bound); mark(b"I")
            command = (helper["RESTIC_BINARY"], "-r", helper["REPOSITORY"], "--no-lock", "snapshots", "--latest", "1", "--json")
            ctypes = helper["ctypes"]
            argv = (ctypes.c_char_p * (len(command) + 1))(*[part.encode("ascii") for part in command], None)
            env = (ctypes.c_char_p * 3)(b"PATH=/usr/bin:/bin", ("RESTIC_PASSWORD_FILE=" + helper["VIEW_CREDENTIAL"]).encode("ascii"), None)
            syscall(native[3], bound.restic_fd, ctypes.c_char_p(b""), argv, env, helper["EXECVEAT_EMPTY_PATH"])
        except Exception: pass
        os._exit(125)
    os.close(write_fd)
    try:
        deadline = time.monotonic() + timeout
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                raw = os.read(read_fd, 32)
                return os.waitstatus_to_exitcode(status), raw
            if time.monotonic() >= deadline:
                os.kill(pid, 9); os.waitpid(pid, 0); return None, b""
            time.sleep(0.01)
    finally:
        try: os.close(read_fd)
        except Exception: pass


def _run_read_only() -> dict[str, Any]:
    source = _helper_source()
    if os.geteuid() != 0 or source is None: return envelope("blocked", "preflight_failed", "none")
    name = "_odysseus_pinned_installed_root_helper_stage_diagnostic_"; module = types.ModuleType(name); module.__file__ = "<verified-installed-root-helper>"; sys.modules[name] = module
    try: exec(compile(source, module.__file__, "exec"), module.__dict__, module.__dict__)
    except Exception:
        sys.modules.pop(name, None); return envelope("blocked", "preflight_failed", "none")
    helper = module.__dict__; lock = bound = None
    try:
        lock = helper["_acquire_run_lock"](); bound = helper["_bind_identities"]()
        code, raw = _child_result(helper, bound)
        if code is None: return envelope("unknown", "timeout", "none", locked=True, bound=True, invoked=True)
        last = raw[-1:] if raw else b""
        if code == 125: return envelope("observed", "stage_failed", _TOKEN_STAGE.get(last, "namespace_or_private_propagation"), locked=True, bound=True, invoked=True)
        if code != 0: return envelope("observed", "restic_read_failed", "restic_read", locked=True, bound=True, invoked=True)
        return envelope("observed", "none", "none", locked=True, bound=True, invoked=True)
    except Exception: return envelope("blocked", "preflight_failed", "none")
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
    if execute is not True: return envelope("blocked", "execution_disabled", "none")
    try: value = runner()
    except Exception: return envelope("blocked", "preflight_failed", "none")
    return dict(value) if validate_envelope(value) else envelope("blocked", "preflight_failed", "none")


def main() -> int:
    print(json.dumps(envelope("blocked", "execution_disabled", "none"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
