#!/usr/bin/env python3
"""Inert, single dispatch transport for the reviewed helper replacement.

Only immutable published blobs are eligible.  The default entry point never
opens SSH, and every transport failure is ambiguous with retries disabled.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import sys
import subprocess
import threading
import time
from typing import Any, Callable, Mapping

SCHEMA_ID = "odysseus.predeploy_backup_root_helper_replace_transport.v1"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
HELPER_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper.py"
REPLACEMENT_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_replace.py"
PUBLISHED_HELPER_SHA256 = "f5e6f90169c59daee07dbb82880912b8c959d2bf991748e3363b6dbbc7b57952"
PUBLISHED_REPLACEMENT_SHA256 = "0dd73f3c26f143f5df4a688b1b1d67659dedae9db10c12ebc54b249f803b5a4a"
MAX_BLOB_BYTES = 400_000
MAX_OUTPUT_BYTES = 8_192
TIMEOUT_SECONDS = 35
STATIC_BOOTSTRAP = r'''import base64,hashlib,json,sys,types,os
MAX=400000
def reject_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError()
        result[key]=value
    return result
try:
    if os.geteuid()!=0: raise ValueError()
    line=sys.stdin.buffer.readline(1200001)
    if not line.endswith(b"\n") or sys.stdin.buffer.read(1): raise ValueError()
    packet=json.loads(line[:-1].decode("ascii"),object_pairs_hook=reject_pairs)
    keys={"helper_sha256","helper_source","replacement_sha256","replacement_source"}
    if type(packet) is not dict or set(packet)!=keys: raise ValueError()
    if packet["helper_sha256"]!="f5e6f90169c59daee07dbb82880912b8c959d2bf991748e3363b6dbbc7b57952" or packet["replacement_sha256"]!="0dd73f3c26f143f5df4a688b1b1d67659dedae9db10c12ebc54b249f803b5a4a": raise ValueError()
    helper=base64.b64decode(packet["helper_source"],validate=True); replacement=base64.b64decode(packet["replacement_source"],validate=True)
    if not 0<len(helper)<=MAX or not 0<len(replacement)<=MAX or hashlib.sha256(helper).hexdigest()!=packet["helper_sha256"] or hashlib.sha256(replacement).hexdigest()!=packet["replacement_sha256"]: raise ValueError()
    module=types.ModuleType("_odysseus_pinned_replace"); sys.modules[module.__name__]=module; exec(compile(replacement,"<pinned-replacement>","exec"),module.__dict__,module.__dict__)
    result=module.replace(execute=True,new_source=helper)
    sys.stdout.write(json.dumps(result,sort_keys=True,separators=(",",":"))+"\n")
except Exception:
    raise SystemExit(70)
'''
REMOTE_COMMAND = "/usr/bin/timeout 35s /usr/bin/sudo -n /usr/bin/python3 -I -c " + shlex.quote(STATIC_BOOTSTRAP)
SSH_COMMAND = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver", REMOTE_COMMAND)
_KEYS = frozenset({"schema_id", "status", "error_code", "dispatch_invoked", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})
_CODES = frozenset({"execution_disabled", "published_blob_mismatch", "remote_blocked", "remote_rolled_back", "none", "transport_ambiguous", "transport_invalid", "transport_timeout", "transport_oversize", "transport_nonzero"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def envelope(status: str, code: str, *, invoked: bool = False, effect: bool = False, recovery: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status, "error_code": code, "dispatch_invoked": invoked is True, "effect_may_have_occurred": effect is True, "manual_recovery_required": recovery is True, "retry_permitted": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID or type(value.get("status")) is not str or type(value.get("error_code")) is not str or value.get("retry_permitted") is not False or any(value.get(k) is not False for k in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) or value.get("evidence_sha256") != _digest(value): return False
    except Exception: return False
    if any(type(value.get(key)) is not bool for key in ("dispatch_invoked", "effect_may_have_occurred", "manual_recovery_required")): return False
    if value.get("error_code") not in _CODES: return False
    if value["status"] == "blocked":
        if value["effect_may_have_occurred"] or value["manual_recovery_required"]: return False
        if value["error_code"] == "remote_blocked": return value["dispatch_invoked"]
        return value["error_code"] in {"execution_disabled", "published_blob_mismatch"} and not value["dispatch_invoked"]
    if value["status"] == "applied": return value["error_code"] == "none" and value["dispatch_invoked"] and value["effect_may_have_occurred"] and not value["manual_recovery_required"]
    if value["status"] == "rolled_back": return value["error_code"] == "remote_rolled_back" and value["dispatch_invoked"] and value["effect_may_have_occurred"] and not value["manual_recovery_required"]
    return value["status"] == "unknown" and value["dispatch_invoked"] and value["effect_may_have_occurred"] and value["manual_recovery_required"] and value["error_code"].startswith("transport_")


def _published_blob(path: str, expected: str, *, runner: Callable[..., Any] = subprocess.run) -> bytes | None:
    try:
        result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, timeout=5, check=False)
        data = getattr(result, "stdout", None)
        return data if getattr(result, "returncode", 1) == 0 and type(data) is bytes and 0 < len(data) <= MAX_BLOB_BYTES and hashlib.sha256(data).hexdigest() == expected else None
    except Exception: return None


def prepare_candidate(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, str] | None:
    helper = _published_blob(HELPER_PATH, PUBLISHED_HELPER_SHA256, runner=runner)
    replacement = _published_blob(REPLACEMENT_PATH, PUBLISHED_REPLACEMENT_SHA256, runner=runner)
    if helper is None or replacement is None: return None
    return {"helper_sha256": PUBLISHED_HELPER_SHA256, "helper_source": base64.b64encode(helper).decode("ascii"), "replacement_sha256": PUBLISHED_REPLACEMENT_SHA256, "replacement_source": base64.b64encode(replacement).decode("ascii")}


def _bounded_dispatch(payload: bytes, *, popen: Callable[..., Any]) -> tuple[str, bytes | None]:
    process = None
    threads: list[Any] = []
    started: list[Any] = []
    output = bytearray()
    failed = False
    oversized = False
    timed_out = False
    try:
        process = popen(list(SSH_COMMAND), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)

        def writer() -> None:
            nonlocal failed
            try:
                process.stdin.write(payload)
                process.stdin.flush()
                process.stdin.close()
            except Exception:
                failed = True

        def reader() -> None:
            nonlocal failed, oversized
            try:
                while len(output) <= MAX_OUTPUT_BYTES:
                    chunk = process.stdout.read(min(4096, MAX_OUTPUT_BYTES + 1 - len(output)))
                    if not chunk: break
                    output.extend(chunk)
                if len(output) > MAX_OUTPUT_BYTES:
                    oversized = True
                    try: process.kill()
                    except Exception: pass
            except Exception:
                failed = True

        writer_thread = threading.Thread(target=writer, daemon=True)
        threads.append(writer_thread)
        reader_thread = threading.Thread(target=reader, daemon=True)
        threads.append(reader_thread)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        def start_tracked(thread: Any) -> None:
            try:
                thread.start()
            except Exception:
                try:
                    actually_started = getattr(thread, "ident", None) is not None or thread.is_alive()
                except Exception:
                    actually_started = False
                if actually_started: started.append(thread)
                raise
            started.append(thread)

        start_tracked(writer_thread)
        start_tracked(reader_thread)
        while True:
            if failed or oversized:
                try: process.kill()
                except Exception: pass
                break
            try:
                process.wait(timeout=max(0.01, min(0.1, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    timed_out = True
                    try: process.kill()
                    except Exception: pass
                    break
            except Exception:
                failed = True
                break
        if oversized or failed:
            try: process.kill()
            except Exception: pass
        try: process.wait(timeout=1)
        except Exception: failed = True
    except Exception:
        # Once Popen returns, every construction/startup failure is treated as
        # an ambiguous dispatch and follows the same child cleanup path.
        failed = True
    finally:
        if process is not None:
            try:
                if process.poll() is None: process.kill()
            except Exception: pass
            try: process.wait(timeout=1)
            except Exception: failed = True
            for stream in (getattr(process, "stdin", None), getattr(process, "stdout", None)):
                if stream is not None:
                    try: stream.close()
                    except Exception: failed = True
        for thread in started:
            try: thread.join(1)
            except Exception: failed = True
    if process is None: return "ambiguous", None
    if timed_out: return "timeout", None
    if oversized: return "oversize", None
    if failed or any(thread.is_alive() for thread in started): return "ambiguous", None
    if getattr(process, "returncode", None) != 0: return "nonzero", None
    return "ok", bytes(output)


def _one_json_line(output: bytes | None) -> Any | None:
    if type(output) is not bytes or not output.endswith(b"\n") or output.count(b"\n") != 1 or b"\r" in output: return None
    def reject_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError("duplicate key")
            result[key] = value
        return result
    try: return json.loads(output[:-1].decode("ascii"), object_pairs_hook=reject_pairs)
    except Exception: return None


def _valid_result(value: Any) -> bool:
    required = {"schema_id", "status", "error_code", "backup_created", "replacement_published", "postflight_ok", "rollback_attempted", "rollback_succeeded", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"}
    try:
        if not (type(value) is dict and set(value) == required and value.get("schema_id") == "odysseus.predeploy_backup_root_helper_replace.v1" and type(value.get("status")) is str and type(value.get("error_code")) is str and isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()): return False
    except Exception: return False
    bools=("backup_created","replacement_published","postflight_ok","rollback_attempted","rollback_succeeded","effect_may_have_occurred","manual_recovery_required","retry_permitted","raw_output_visible","environment_visible","paths_visible","secret_values_visible")
    if any(type(value.get(k)) is not bool for k in bools) or any(value.get(k) is not False for k in ("retry_permitted","raw_output_visible","environment_visible","paths_visible","secret_values_visible")): return False
    status=value["status"]
    if status=="applied": return value["error_code"]=="none" and all(value[k] for k in ("backup_created","replacement_published","postflight_ok","effect_may_have_occurred")) and not any(value[k] for k in ("rollback_attempted","rollback_succeeded","manual_recovery_required"))
    if status=="blocked": return value["error_code"] in {"execution_disabled","invalid_source","preflight_failed","lock_busy","backup_conflict","foreign_target","write_failed"} and not any(value[k] for k in ("backup_created","replacement_published","postflight_ok","rollback_attempted","rollback_succeeded","effect_may_have_occurred","manual_recovery_required"))
    if status=="rolled_back": return value["error_code"] in {"write_failed","publish_ambiguous","postflight_failed"} and value["backup_created"] and value["replacement_published"] and not value["postflight_ok"] and value["rollback_attempted"] and value["rollback_succeeded"] and value["effect_may_have_occurred"] and not value["manual_recovery_required"]
    if status!="unknown" or value["error_code"] not in {"write_failed","publish_ambiguous","rollback_failed","foreign_target"} or not value["effect_may_have_occurred"] or not value["manual_recovery_required"] or value["postflight_ok"]: return False
    if value["rollback_succeeded"]: return False
    if value["error_code"] == "rollback_failed": return value["rollback_attempted"] and value["backup_created"] and value["replacement_published"]
    if value["rollback_attempted"]: return False
    if value["error_code"] == "publish_ambiguous": return value["backup_created"] and value["replacement_published"]
    if value["error_code"] == "foreign_target": return value["backup_created"]
    return not value["replacement_published"]


def request_replacement(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run, popen: Callable[..., Any] | None = None) -> dict[str, Any]:
    if execute is not True: return envelope("blocked", "execution_disabled")
    candidate = prepare_candidate(runner=runner)
    if candidate is None: return envelope("blocked", "published_blob_mismatch")
    payload = (json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    state, output = _bounded_dispatch(payload, popen=popen or subprocess.Popen)
    if state != "ok" or output is None: return envelope("unknown", "transport_" + state, invoked=True, effect=True, recovery=True)
    value = _one_json_line(output)
    if not _valid_result(value): return envelope("unknown", "transport_invalid", invoked=True, effect=True, recovery=True)
    if value["status"] == "applied": return envelope("applied", "none", invoked=True, effect=True, recovery=False)
    if value["status"] == "blocked": return envelope("blocked", "remote_blocked", invoked=True, effect=False, recovery=False)
    if value["status"] == "rolled_back": return envelope("rolled_back", "remote_rolled_back", invoked=True, effect=True, recovery=False)
    return envelope("unknown", "transport_ambiguous", invoked=True, effect=True, recovery=True)


def main() -> int:
    print(json.dumps(envelope("blocked", "execution_disabled"), sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
