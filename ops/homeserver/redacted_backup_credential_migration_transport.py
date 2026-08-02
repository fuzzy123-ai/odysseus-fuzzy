#!/usr/bin/env python3
"""Fail-closed published-blob transport for credential migration.

This module intentionally has no command-line execution path.  It only sends a
single, SHA-pinned published source blob to the fixed homeserver service when
the caller supplies ``execute=True``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, Callable

from ops.homeserver import redacted_backup_credential_migration as migration


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
MIGRATION_PATH = "ops/homeserver/redacted_backup_credential_migration.py"
PUBLISHED_MIGRATION_SHA256 = (
    "2c5cc84b79cfa2339e768073e138c646f7353567276eb041b7b43a8b9d5ede08"
)
_UNBOUND_PIN = "0" * 64
_MAX_SOURCE = 400_000
_MAX_BUNDLE = 500_000
_MAX_OUTPUT = 8192
_PIN_RE = re.compile(r"[0-9a-f]{64}\Z")
_BOOTSTRAP = """import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(500001)
if len(raw)>500000: raise SystemExit(2)
bundle=json.loads(raw.decode('utf-8'))
expected='2c5cc84b79cfa2339e768073e138c646f7353567276eb041b7b43a8b9d5ede08'
if type(bundle) is not dict or set(bundle)!={'execute','sha256','source'} or bundle['execute'] is not True or bundle['sha256']!=expected: raise SystemExit(2)
source=base64.b64decode(bundle['source'],validate=True)
if hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(2)
name='ops.homeserver.redacted_backup_credential_migration'
module=types.ModuleType(name);module.__file__='<published>';sys.modules[name]=module
exec(compile(source,module.__file__,'exec'),module.__dict__)
result=module.migrate(execute=True)
if module.validate(result) is not True: raise SystemExit(2)
print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False))"""
SSH_COMMAND = (
    "ssh",
    "-F",
    "ops/homeserver/ssh_config",
    "odysseus-homeserver",
    "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 55s "
    "/usr/bin/systemd-run --user --wait --pipe --collect --quiet "
    "--unit=odysseus-backup-credential-migration "
    "--service-type=oneshot "
    "--property=RuntimeMaxSec=45s "
    "--property=TimeoutStopSec=5s "
    "--property=KillMode=control-group "
    "--property=SendSIGKILL=yes "
    "--property=EnvironmentFile=/home/homebase/.config/odysseus-backup/env "
    "/usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP),
)


def blocked(error_code: str) -> dict[str, Any]:
    if error_code == "invalid_invocation":
        return migration.envelope("blocked", "invalid_invocation")
    return migration.envelope("blocked", "published_blob_mismatch")


def unknown() -> dict[str, Any]:
    return migration.envelope("unknown", "mutation_ambiguous", effect=True)


def _pin_is_bound() -> bool:
    return bool(
        type(PUBLISHED_MIGRATION_SHA256) is str
        and _PIN_RE.fullmatch(PUBLISHED_MIGRATION_SHA256)
        and PUBLISHED_MIGRATION_SHA256 != _UNBOUND_PIN
    )


def _published_blob(runner: Callable[..., Any]) -> bytes | None:
    if not _pin_is_bound():
        return None
    try:
        command = ["git", "cat-file", "blob", f"{PUBLISHED_REF}:{MIGRATION_PATH}"]
        result = (
            _bounded_subprocess(
                command,
                input_bytes=b"",
                timeout=5,
                maximum_stdout=_MAX_SOURCE,
            )
            if runner is subprocess.run
            else runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                timeout=5,
                check=False,
                shell=False,
            )
        )
    except Exception:
        return None
    source = getattr(result, "stdout", None)
    return (
        source
        if getattr(result, "returncode", None) == 0
        and getattr(result, "stdout_oversized", False) is False
        and type(source) is bytes
        and 0 < len(source) <= _MAX_SOURCE
        and hashlib.sha256(source).hexdigest() == PUBLISHED_MIGRATION_SHA256
        else None
    )


def _bounded_subprocess(
    command: list[str], *, input_bytes: bytes, timeout: int, maximum_stdout: int
) -> Any:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    output = bytearray()
    oversized = threading.Event()

    def write_input() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(input_bytes)
            process.stdin.close()
        except Exception:
            pass

    def read_output() -> None:
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(min(4096, maximum_stdout + 1 - len(output)))
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > maximum_stdout:
                    oversized.set()
                    process.kill()
                    return
        except Exception:
            process.kill()

    writer = threading.Thread(target=write_input, daemon=True)
    reader = threading.Thread(target=read_output, daemon=True)
    writer.start()
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        writer.join(timeout=1)
        reader.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
    return SimpleNamespace(
        returncode=returncode,
        stdout=bytes(output),
        stdout_oversized=oversized.is_set(),
    )


def _canonical_core_envelope(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict or not migration.validate(value):
        return None
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return canonical if migration.validate(canonical) else None


def collect_published_backup_credential_migration(
    *, execute: bool = False, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any]:
    if execute is not True:
        return blocked("invalid_invocation")
    source = _published_blob(runner)
    if source is None:
        return blocked("published_blob_mismatch")
    bundle = {
        "execute": True,
        "sha256": PUBLISHED_MIGRATION_SHA256,
        "source": base64.b64encode(source).decode("ascii"),
    }
    try:
        serialized = json.dumps(
            bundle, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("ascii")
        if len(serialized) > _MAX_BUNDLE:
            return blocked("published_blob_mismatch")
        result = (
            _bounded_subprocess(
                list(SSH_COMMAND),
                input_bytes=serialized,
                timeout=60,
                maximum_stdout=_MAX_OUTPUT,
            )
            if runner is subprocess.run
            else runner(
                list(SSH_COMMAND),
                input=serialized,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                timeout=60,
                check=False,
                shell=False,
            )
        )
    except subprocess.TimeoutExpired:
        return unknown()
    except Exception:
        return unknown()
    raw = getattr(result, "stdout", None)
    try:
        if (
            getattr(result, "returncode", None) != 0
            or getattr(result, "stdout_oversized", False) is not False
            or type(raw) is not bytes
            or len(raw) > _MAX_OUTPUT
            or raw.count(b"\n") != 1
            or not raw.endswith(b"\n")
        ):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return unknown()
    canonical = _canonical_core_envelope(payload)
    return (
        canonical
        if canonical is not None
        and canonical.get("status") in {"succeeded", "rolled_back", "unknown"}
        else unknown()
    )


def main() -> int:
    print(json.dumps(blocked("invalid_invocation"), sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
