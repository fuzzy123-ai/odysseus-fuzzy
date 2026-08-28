#!/usr/bin/env python3
"""Published transport for the root-helper's read-only Restic diagnostic."""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, Callable

from ops.homeserver import redacted_predeploy_backup_root_helper_read_diagnostic as diagnostic


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
DIAGNOSTIC_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_read_diagnostic.py"
PUBLISHED_DIAGNOSTIC_SHA256 = "9426dfa06edff0927e216b95530e2312257998b513b764dfa4a6d6675e7f7820"
MAX_SOURCE_BYTES = 400_000
MAX_STDIN_BYTES = 600_000
MAX_STDOUT_BYTES = 8_192
TRANSPORT_TIMEOUT_SECONDS = 45
SSH_PREFIX = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")

_BOOTSTRAP = r'''import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(600001)
if not raw or len(raw)>600000: raise SystemExit(70)
try: bundle=json.loads(raw.decode("ascii"))
except Exception: raise SystemExit(70)
expected="9426dfa06edff0927e216b95530e2312257998b513b764dfa4a6d6675e7f7820"
if type(bundle) is not dict or set(bundle)!={"execute","sha256","source"} or bundle.get("execute") is not True or bundle.get("sha256")!=expected: raise SystemExit(70)
try: source=base64.b64decode(bundle["source"],validate=True)
except Exception: raise SystemExit(70)
if not source or len(source)>400000 or hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(70)
name="_odysseus_pinned_root_helper_read_diagnostic_"; module=types.ModuleType(name); module.__file__="<pinned-root-helper-read-diagnostic>"; sys.modules[name]=module
try:
    exec(compile(source,module.__file__,"exec"),module.__dict__,module.__dict__)
    result=module.collect(execute=True)
    if module.validate_envelope(result) is not True: raise SystemExit(70)
    print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(",",":")))
finally: sys.modules.pop(name,None)
'''
REMOTE_COMMAND = "/usr/bin/timeout --signal=KILL 40s /usr/bin/sudo -n /usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP)
SSH_COMMAND = SSH_PREFIX + (REMOTE_COMMAND,)


def _published_blob(runner: Callable[..., Any]) -> bytes | None:
    try: result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{DIAGNOSTIC_PATH}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=5, check=False, shell=False)
    except Exception: return None
    source = getattr(result, "stdout", None)
    return source if getattr(result, "returncode", None) == 0 and type(source) is bytes and 0 < len(source) <= MAX_SOURCE_BYTES and hashlib.sha256(source).hexdigest() == PUBLISHED_DIAGNOSTIC_SHA256 else None


def _bounded_process(command: tuple[str, ...], payload: bytes, timeout: int) -> Any:
    process = subprocess.Popen(list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    output = bytearray(); oversized = threading.Event(); failed = threading.Event()
    def writer() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(payload); process.stdin.close()
        except Exception: failed.set()
    def reader() -> None:
        try:
            assert process.stdout is not None
            while True:
                piece = process.stdout.read(min(4096, MAX_STDOUT_BYTES + 1 - len(output)))
                if not piece: return
                output.extend(piece)
                if len(output) > MAX_STDOUT_BYTES: oversized.set(); process.kill(); return
        except Exception:
            failed.set()
            try: process.kill()
            except Exception: pass
    writers = threading.Thread(target=writer, daemon=True); readers = threading.Thread(target=reader, daemon=True); writers.start(); readers.start()
    try: returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired: process.kill(); process.wait(); raise
    finally:
        writers.join(1); readers.join(1)
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None: stream.close()
            except Exception: pass
    if writers.is_alive() or readers.is_alive() or failed.is_set(): raise RuntimeError("transport ambiguous")
    return SimpleNamespace(returncode=returncode, stdout=bytes(output), stdout_oversized=oversized.is_set())


def _unknown() -> dict[str, Any]:
    return diagnostic.envelope("unknown", "read_timeout", locked=True, bound=True, invoked=True, effect=True, recovery=True)


def collect_published_root_helper_read_diagnostic(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if execute is not True: return diagnostic.envelope("blocked", "execution_disabled")
    source = _published_blob(runner)
    if source is None: return diagnostic.envelope("blocked", "preflight_failed")
    payload = json.dumps({"execute": True, "sha256": PUBLISHED_DIAGNOSTIC_SHA256, "source": base64.b64encode(source).decode("ascii")}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(payload) > MAX_STDIN_BYTES: return diagnostic.envelope("blocked", "preflight_failed")
    try: result = _bounded_process(SSH_COMMAND, payload, TRANSPORT_TIMEOUT_SECONDS) if runner is subprocess.run else runner(list(SSH_COMMAND), input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=TRANSPORT_TIMEOUT_SECONDS, check=False, shell=False)
    except Exception: return _unknown()
    raw = getattr(result, "stdout", None)
    try:
        if getattr(result, "returncode", None) not in {0, 1} or getattr(result, "stdout_oversized", False) is not False or type(raw) is not bytes or len(raw) > MAX_STDOUT_BYTES or raw.count(b"\n") != 1 or not raw.endswith(b"\n") or b"\r" in raw: raise ValueError
        value = json.loads(raw[:-1].decode("ascii"))
    except Exception: return _unknown()
    return dict(value) if diagnostic.validate_envelope(value) else _unknown()


def main() -> int:
    print(json.dumps(diagnostic.envelope("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
