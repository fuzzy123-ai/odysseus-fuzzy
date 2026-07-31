#!/usr/bin/env python3
"""Published-blob stdin transport for one fixed backup-contract repair."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, Callable

from ops.homeserver import redacted_backup_configuration_repair as repair


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
REPAIR_PATH = "ops/homeserver/redacted_backup_configuration_repair.py"
PUBLISHED_REPAIR_SHA256 = "c003ccfe3777db7535c91be33958d8a76a5b8f27ecf591d8142577001adaf333"
_BOOTSTRAP = """import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(400001)
if len(raw)>400000: raise SystemExit(2)
bundle=json.loads(raw.decode('utf-8'))
expected='c003ccfe3777db7535c91be33958d8a76a5b8f27ecf591d8142577001adaf333'
if type(bundle) is not dict or set(bundle)!={'execute','sha256','source'} or bundle['execute'] is not True or bundle['sha256']!=expected: raise SystemExit(2)
source=base64.b64decode(bundle['source'],validate=True)
if hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(2)
name='ops.homeserver.redacted_backup_configuration_repair'
module=types.ModuleType(name);module.__file__='<published>';sys.modules[name]=module
exec(compile(source,module.__file__,'exec'),module.__dict__)
result=module.repair_backup_configuration(execute=True)
print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':')))"""
SSH_COMMAND = (
    "ssh",
    "-F",
    "ops/homeserver/ssh_config",
    "odysseus-homeserver",
    "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 25s "
    "/usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP),
)


def _bounded_subprocess(
    command: list[str],
    *,
    input_bytes: bytes,
    timeout: int,
    maximum_stdout: int,
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
                chunk = process.stdout.read(
                    min(4096, maximum_stdout + 1 - len(output))
                )
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
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        writer.join(timeout=1)
        reader.join(timeout=1)
    return SimpleNamespace(
        returncode=return_code,
        stdout=bytes(output),
        stdout_oversized=oversized.is_set(),
    )


def _published_blob(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(
            ["git", "cat-file", "blob", f"{PUBLISHED_REF}:{REPAIR_PATH}"],
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
        and 0 < len(source) <= 300_000
        and hashlib.sha256(source).hexdigest() == PUBLISHED_REPAIR_SHA256
        else None
    )


def collect_published_backup_configuration_repair(
    *,
    execute: bool = False,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if execute is not True:
        return repair.envelope("blocked", "invalid_invocation")
    source = _published_blob(runner)
    if source is None:
        return repair.envelope("blocked", "published_blob_mismatch")
    bundle = {
        "execute": True,
        "sha256": PUBLISHED_REPAIR_SHA256,
        "source": base64.b64encode(source).decode("ascii"),
    }
    try:
        serialized = json.dumps(
            bundle,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        result = (
            _bounded_subprocess(
                list(SSH_COMMAND),
                input_bytes=serialized,
                timeout=30,
                maximum_stdout=8192,
            )
            if runner is subprocess.run
            else runner(
                list(SSH_COMMAND),
                input=serialized,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                timeout=30,
                check=False,
                shell=False,
            )
        )
    except subprocess.TimeoutExpired:
        return repair.envelope("unknown", "mutation_ambiguous", effect=True)
    except Exception:
        return repair.envelope("unknown", "mutation_ambiguous", effect=True)
    raw = getattr(result, "stdout", None)
    try:
        if (
            getattr(result, "returncode", None) not in {0, 1}
            or getattr(result, "stdout_oversized", False) is not False
            or type(raw) is not bytes
            or len(raw) > 8192
            or raw.count(b"\n") != 1
            or not raw.endswith(b"\n")
        ):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return repair.envelope("unknown", "mutation_ambiguous", effect=True)
    return (
        dict(payload)
        if repair.validate_envelope(payload)
        else repair.envelope("unknown", "mutation_ambiguous", effect=True)
    )


def main() -> int:
    payload = repair.envelope("blocked", "invalid_invocation")
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
