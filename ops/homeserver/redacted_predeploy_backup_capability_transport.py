#!/usr/bin/env python3
"""Exact published-blob stdin transport for the read-only namespace probe."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from ops.homeserver import redacted_predeploy_backup_capability as capability


SCHEMA_ID = "odysseus.redacted_predeploy_backup_capability_transport.v1"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
CAPABILITY_PATH = "ops/homeserver/redacted_predeploy_backup_capability.py"
PUBLISHED_CAPABILITY_SHA256 = "50a77606af9b5a127ada57ca44533a9821612fd6e71ddeca17564c1586a8562e"
MAX_SOURCE_BYTES = 300_000
MAX_BUNDLE_BYTES = 500_000
SSH_COMMAND_PREFIX = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
_BOOTSTRAP = """import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(BUNDLE_READ_LIMIT)
if len(raw)>BUNDLE_MAXIMUM: raise SystemExit(2)
bundle=json.loads(raw.decode('ascii'))
expected='PLACEHOLDER_PIN'
if type(bundle) is not dict or set(bundle)!={'execute','sha256','source'} or bundle['execute'] is not True or bundle['sha256']!=expected: raise SystemExit(2)
source=base64.b64decode(bundle['source'],validate=True)
if hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(2)
name='ops.homeserver.redacted_predeploy_backup_capability';module=types.ModuleType(name);module.__file__='<published>';sys.modules[name]=module
exec(compile(source,module.__file__,'exec'),module.__dict__)
result=module.collect_predeploy_backup_capability(execute=True)
print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':')))""".replace(
    "PLACEHOLDER_PIN", PUBLISHED_CAPABILITY_SHA256,
).replace("BUNDLE_READ_LIMIT", str(MAX_BUNDLE_BYTES + 1)).replace("BUNDLE_MAXIMUM", str(MAX_BUNDLE_BYTES))
SSH_COMMAND = (*SSH_COMMAND_PREFIX, "exec /usr/bin/timeout --signal=KILL 25s /usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP))
_CODES = frozenset({"invalid_invocation", "published_blob_mismatch", "transport_ambiguous"})
_KEYS = frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "evidence_sha256"})


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _packet(status: str, code: str, effect: bool) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": status, "error_code": code, "effect_may_have_occurred": effect, "retry_permitted": False}
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_transport_envelope(value: Any) -> bool:
    return bool(type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID
                and ((value.get("status") == "blocked" and value.get("error_code") in {"invalid_invocation", "published_blob_mismatch"})
                     or (value.get("status") == "unknown" and value.get("error_code") == "transport_ambiguous"))
                and value.get("effect_may_have_occurred") is (value.get("status") == "unknown")
                and value.get("retry_permitted") is False and value.get("evidence_sha256") == _digest(value))


class TransportProcessUnreaped(RuntimeError):
    """The child could not be proven exited and reaped inside the fixed bound."""


def _bounded_process(command: tuple[str, ...], input_bytes: bytes, timeout: int, maximum: int) -> Any:
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    output = bytearray(); oversized = threading.Event(); kill_attempted = threading.Event(); kill_failed = threading.Event()
    def write() -> None:
        try:
            assert process.stdin is not None; process.stdin.write(input_bytes)
        except Exception: pass
        finally:
            try:
                if process.stdin is not None: process.stdin.close()
            except Exception: pass
    def read() -> None:
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(min(4096, maximum + 1 - len(output)))
                if not chunk: return
                output.extend(chunk)
                if len(output) > maximum:
                    oversized.set()
                    kill_attempted.set()
                    try: process.kill()
                    except Exception: kill_failed.set()
                    return
        except Exception:
            kill_attempted.set()
            try: process.kill()
            except Exception: kill_failed.set()
        finally:
            try:
                if process.stdout is not None: process.stdout.close()
            except Exception: pass
    writer = threading.Thread(target=write, daemon=True); reader = threading.Thread(target=read, daemon=True)
    writer.start(); reader.start()
    timed_out = False; reaped = False; wait_failed = False; returncode = -1

    def bounded_reap() -> None:
        nonlocal reaped, returncode
        if reaped: return
        try:
            returncode = process.wait(timeout=1); reaped = True; return
        except Exception: pass
        try:
            polled = process.poll()
            if isinstance(polled, int): returncode = polled; reaped = True
        except Exception: pass

    try:
        returncode = process.wait(timeout=timeout); reaped = True
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_attempted.set()
        try: process.kill()
        except Exception: kill_failed.set()
        bounded_reap()
    except Exception:
        wait_failed = True
        kill_attempted.set()
        try: process.kill()
        except Exception: kill_failed.set()
        bounded_reap()
    finally:
        writer.join(timeout=1); reader.join(timeout=1)
        if writer.is_alive() or reader.is_alive():
            try:
                if process.stdin is not None: process.stdin.close()
            except Exception: pass
            try:
                if process.stdout is not None: process.stdout.close()
            except Exception: pass
            kill_attempted.set()
            try: process.kill()
            except Exception: kill_failed.set()
            bounded_reap()
            writer.join(timeout=1); reader.join(timeout=1)
        try:
            if process.stdin is not None: process.stdin.close()
        except Exception: pass
        try:
            if process.stdout is not None: process.stdout.close()
        except Exception: pass
        if kill_attempted.is_set():
            try:
                polled = process.poll()
                if isinstance(polled, int): returncode = polled; reaped = True
            except Exception: pass
        bounded_reap()
    if writer.is_alive() or reader.is_alive(): raise RuntimeError("transport thread did not terminate")
    if not reaped: raise TransportProcessUnreaped("transport child exit could not be proven")
    if timed_out: raise subprocess.TimeoutExpired(command, timeout)
    if wait_failed: raise RuntimeError("transport child wait failed")
    return SimpleNamespace(returncode=returncode, stdout=bytes(output), stdout_oversized=oversized.is_set())


def _published_blob(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{CAPABILITY_PATH}"], stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=False, timeout=5, check=False, shell=False)
    except Exception: return None
    source = getattr(result, "stdout", None)
    return source if getattr(result, "returncode", None) == 0 and type(source) is bytes and 0 < len(source) <= MAX_SOURCE_BYTES and hashlib.sha256(source).hexdigest() == PUBLISHED_CAPABILITY_SHA256 else None


def collect_published_predeploy_backup_capability(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if execute is not True: return _packet("blocked", "invalid_invocation", False)
    source = _published_blob(runner)
    if source is None: return _packet("blocked", "published_blob_mismatch", False)
    serialized = json.dumps({"execute": True, "sha256": PUBLISHED_CAPABILITY_SHA256, "source": base64.b64encode(source).decode("ascii")}, separators=(",", ":")).encode("ascii")
    if len(serialized) > MAX_BUNDLE_BYTES: return _packet("blocked", "published_blob_mismatch", False)
    try:
        result = _bounded_process(SSH_COMMAND, serialized, 30, 8192) if runner is subprocess.run else runner(
            list(SSH_COMMAND), input=serialized, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=False, timeout=30, check=False, shell=False)
    except Exception: return _packet("unknown", "transport_ambiguous", True)
    raw = getattr(result, "stdout", None)
    try:
        if getattr(result, "returncode", None) != 0 or getattr(result, "stdout_oversized", False) is not False or type(raw) is not bytes or len(raw) > 8192 or raw.count(b"\n") != 1 or not raw.endswith(b"\n"): raise ValueError
        payload = json.loads(raw.decode("utf-8"))
    except Exception: return _packet("unknown", "transport_ambiguous", True)
    return dict(payload) if capability.validate_envelope(payload) else _packet("unknown", "transport_ambiguous", True)


def main() -> int:
    payload = collect_published_predeploy_backup_capability()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
