#!/usr/bin/env python3
"""Published-blob stdin transport for one value-free backup diagnostic."""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any, Callable

from ops.homeserver import redacted_backup_configuration_diagnostic as diagnostic


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
DIAGNOSTIC_PATH = "ops/homeserver/redacted_backup_configuration_diagnostic.py"
PUBLISHED_DIAGNOSTIC_SHA256 = "c5c5fa09d77bab0d08999814171b8d3c2a48f140b4e6e872eacdebc447b1d72e"
SSH_COMMAND = (
    "ssh",
    "-F",
    "ops/homeserver/ssh_config",
    "odysseus-homeserver",
    "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 20s /usr/bin/python3 -I -",
)


def _blob(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(
            ["git", "cat-file", "blob", f"{PUBLISHED_REF}:{DIAGNOSTIC_PATH}"],
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
        and 0 < len(source) <= 200000
        and hashlib.sha256(source).hexdigest() == PUBLISHED_DIAGNOSTIC_SHA256
        else None
    )


def collect_published_backup_configuration_diagnostic(
    *,
    execute: bool = False,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if execute is not True:
        return diagnostic.envelope("blocked", "invalid_invocation")
    source = _blob(runner)
    if source is None:
        return diagnostic.envelope("blocked", "published_blob_mismatch")
    try:
        result = runner(
            list(SSH_COMMAND),
            input=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=30,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return diagnostic.envelope("blocked", "transport_timeout")
    except Exception:
        return diagnostic.envelope("blocked", "transport_failed")
    raw = getattr(result, "stdout", None)
    try:
        if (
            getattr(result, "returncode", None) not in {0, 1}
            or type(raw) is not bytes
            or len(raw) > 8192
            or raw.count(b"\n") != 1
            or not raw.endswith(b"\n")
        ):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return diagnostic.envelope("blocked", "transport_invalid")
    return (
        dict(payload)
        if diagnostic.validate_envelope(payload)
        else diagnostic.envelope("blocked", "transport_invalid")
    )


def main() -> int:
    payload = diagnostic.envelope("blocked", "invalid_invocation")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
