#!/usr/bin/env python3
"""Published-blob transport for one root-helper arm/start/readback action."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shlex
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable

from ops.homeserver import redacted_predeploy_backup_root_helper_action as action


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
ACTION_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_action.py"
PUBLISHED_ACTION_SHA256 = "403dab90f8f6c38a7e34ec1a04c8421bfa0edab2914642677707923bda5e103e"
MAX_SOURCE_BYTES = 400_000
MAX_BUNDLE_BYTES = 600_000
MAX_STDOUT_BYTES = 8_192
TRANSPORT_TIMEOUT_SECONDS = 1_930
SSH_PREFIX = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")

_BOOTSTRAP = r'''import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(600001)
if not raw or len(raw)>600000: raise SystemExit(70)
try: bundle=json.loads(raw.decode("ascii"))
except Exception: raise SystemExit(70)
expected="403dab90f8f6c38a7e34ec1a04c8421bfa0edab2914642677707923bda5e103e"
if type(bundle) is not dict or set(bundle)!={"execute","packet","sha256","source"} or bundle.get("execute") is not True or bundle.get("sha256")!=expected: raise SystemExit(70)
try: source=base64.b64decode(bundle["source"],validate=True)
except Exception: raise SystemExit(70)
if not source or len(source)>400000 or hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(70)
name="_odysseus_pinned_root_helper_action_"; module=types.ModuleType(name); module.__file__="<pinned-root-helper-action>"; sys.modules[name]=module
try:
    exec(compile(source,module.__file__,"exec"),module.__dict__,module.__dict__)
    result=module.perform(bundle["packet"],execute=True)
    if module.validate_envelope(result) is not True: raise SystemExit(70)
    print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(",",":")))
finally: sys.modules.pop(name,None)
'''
REMOTE_COMMAND = "/usr/bin/timeout --signal=KILL 1920s /usr/bin/sudo -n /usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP)
SSH_COMMAND = SSH_PREFIX + (REMOTE_COMMAND,)


def _published_blob(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(
            ["git", "cat-file", "blob", f"{PUBLISHED_REF}:{ACTION_PATH}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=5,
            check=False,
            shell=False,
        )
    except Exception:
        return None
    source = getattr(result, "stdout", None)
    return (
        source
        if getattr(result, "returncode", None) == 0
        and type(source) is bytes
        and 0 < len(source) <= MAX_SOURCE_BYTES
        and hashlib.sha256(source).hexdigest() == PUBLISHED_ACTION_SHA256
        else None
    )


def _bounded_process(command: tuple[str, ...], payload: bytes, timeout: int) -> Any:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    output = bytearray()
    oversized = threading.Event()
    failed = threading.Event()

    def writer() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(payload)
            process.stdin.close()
        except Exception:
            failed.set()

    def reader() -> None:
        try:
            assert process.stdout is not None
            while True:
                piece = process.stdout.read(min(4_096, MAX_STDOUT_BYTES + 1 - len(output)))
                if not piece:
                    return
                output.extend(piece)
                if len(output) > MAX_STDOUT_BYTES:
                    oversized.set()
                    process.kill()
                    return
        except Exception:
            failed.set()
            try:
                process.kill()
            except Exception:
                pass

    writer_thread = threading.Thread(target=writer, daemon=True)
    reader_thread = threading.Thread(target=reader, daemon=True)
    writer_thread.start()
    reader_thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        writer_thread.join(timeout=1)
        reader_thread.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
    if writer_thread.is_alive() or reader_thread.is_alive() or failed.is_set():
        raise RuntimeError("transport cleanup ambiguous")
    return SimpleNamespace(
        returncode=returncode,
        stdout=bytes(output),
        stdout_oversized=oversized.is_set(),
    )


def _unknown() -> dict[str, Any]:
    return action.envelope(
        "unknown",
        "transport_ambiguous",
        arm_created=True,
        unit_invoked=True,
        manual_recovery_required=True,
    )


def collect_published_root_helper_action(
    *,
    execute: bool = False,
    runner: Callable[..., Any] = subprocess.run,
    grant_id: str | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    if execute is not True:
        return action.envelope("blocked", "execution_disabled")
    source = _published_blob(runner)
    if source is None:
        return action.envelope("blocked", "published_blob_mismatch")
    current = int(time.time()) if now_epoch is None else now_epoch
    selected_grant = secrets.token_hex(32) if grant_id is None else grant_id
    packet = {
        "schema_id": action.PACKET_SCHEMA_ID,
        "grant_id": selected_grant,
        "expires_at_epoch": current + 300,
        "helper_sha256": action.HELPER_SHA256,
    }
    bundle = {
        "execute": True,
        "packet": packet,
        "sha256": PUBLISHED_ACTION_SHA256,
        "source": base64.b64encode(source).decode("ascii"),
    }
    try:
        payload = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(payload) > MAX_BUNDLE_BYTES:
            return action.envelope("blocked", "published_blob_mismatch")
        result = (
            _bounded_process(SSH_COMMAND, payload, TRANSPORT_TIMEOUT_SECONDS)
            if runner is subprocess.run
            else runner(
                list(SSH_COMMAND),
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                timeout=TRANSPORT_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        )
    except Exception:
        return _unknown()
    raw = getattr(result, "stdout", None)
    try:
        if (
            getattr(result, "returncode", None) not in {0, 1}
            or getattr(result, "stdout_oversized", False) is not False
            or type(raw) is not bytes
            or len(raw) > MAX_STDOUT_BYTES
            or raw.count(b"\n") != 1
            or not raw.endswith(b"\n")
            or b"\r" in raw
        ):
            raise ValueError
        value = json.loads(raw[:-1].decode("ascii"))
    except Exception:
        return _unknown()
    return dict(value) if action.validate_envelope(value) else _unknown()


def main() -> int:
    value = action.envelope("blocked", "execution_disabled")
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
