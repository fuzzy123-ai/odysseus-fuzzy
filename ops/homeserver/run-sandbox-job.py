#!/usr/bin/env python3
"""Narrow host runner for approved Odysseus sandbox Podman commands.

The Odysseus app container may call this script over SSH with a small JSON
payload. This is not a general shell bridge: only the Podman argv shapes
rendered by src.agent_sandbox_podman_plan are accepted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT_TEXT = str(os.environ.get("ODYSSEUS_SANDBOX_HOST_ROOT") or "/opt/odysseus").rstrip("/")
ROOT = Path(ROOT_TEXT).resolve()
MAX_TIMEOUT_SECONDS = 7200
MAX_OUTPUT_CHARS = 2000
_POD_NAME_RE = re.compile(r"^odysseus-agent-[A-Za-z0-9_.-]{1,80}$")
_FORBIDDEN_PARTS = (
    "--privileged",
    "docker.sock",
    "podman.sock",
    "/var/run/",
    "/run/podman/",
    "&&",
    "||",
    ";",
    "`",
    "$(",
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        argv = _validated_argv(payload.get("argv"))
        timeout = _validated_timeout(payload.get("timeout_seconds"))
        result = _run(argv, timeout)
    except Exception as exc:
        result = {
            "exit_code": 126,
            "stdout": "",
            "stderr": f"host_runner_rejected:{type(exc).__name__}",
            "timed_out": False,
            "duration_seconds": 0.0,
        }
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _validated_argv(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("argv must be a non-empty list")
    argv = [str(item) for item in value]
    if argv[0] != "podman":
        raise ValueError("only podman argv is accepted")
    lowered = " ".join(argv).lower()
    if any(part in lowered for part in _FORBIDDEN_PARTS):
        raise ValueError("forbidden podman argument")
    if len(argv) >= 3 and argv[1:3] == ["pod", "create"]:
        _validate_pod_create(argv)
        return argv
    if len(argv) >= 3 and argv[1:3] == ["pod", "rm"]:
        _validate_pod_rm(argv)
        return argv
    if len(argv) >= 2 and argv[1] == "run":
        return _validated_podman_run(argv)
    raise ValueError("unsupported podman command")


def _validate_pod_create(argv: list[str]) -> None:
    if len(argv) != 7 or argv[3] != "--name" or argv[5] != "--network":
        raise ValueError("unsupported pod create shape")
    if not _POD_NAME_RE.fullmatch(argv[4]):
        raise ValueError("unsafe pod name")
    if argv[6] != "none" and not argv[6].startswith("slirp4netns:"):
        raise ValueError("unsafe pod network")


def _validate_pod_rm(argv: list[str]) -> None:
    if len(argv) != 5 or argv[3] != "-f" or not _POD_NAME_RE.fullmatch(argv[4]):
        raise ValueError("unsupported pod rm shape")


def _validated_podman_run(argv: list[str]) -> list[str]:
    if "--rm" not in argv or "--pod" not in argv:
        raise ValueError("podman run must use disposable pod mode")
    if "--security-opt" not in argv or "no-new-privileges" not in argv:
        raise ValueError("podman run must include no-new-privileges")
    normalized = list(argv)
    index = 0
    while index < len(normalized):
        if normalized[index] == "--pod":
            pod_name = normalized[index + 1]
            if not _POD_NAME_RE.fullmatch(pod_name):
                raise ValueError("unsafe pod name")
            index += 2
            continue
        if normalized[index] == "--mount":
            normalized[index + 1] = _normalize_mount(normalized[index + 1])
            index += 2
            continue
        index += 1
    return normalized


def _normalize_mount(spec: str) -> str:
    parts = str(spec).split(",")
    updated: list[str] = []
    seen_src = False
    seen_dst = False
    for part in parts:
        if part.startswith("src="):
            seen_src = True
            source = part[4:].replace("\\", "/").strip()
            if source.startswith("/") or source.startswith("./") or ".." in source.split("/"):
                raise ValueError("mount source must be repo-relative")
            resolved = (ROOT / source).resolve()
            if ROOT not in (resolved, *resolved.parents):
                raise ValueError("mount source escapes repo root")
            updated.append(f"src={ROOT_TEXT}/{source}")
        elif part.startswith("dst="):
            seen_dst = True
            if not part[4:].startswith("/workspace/"):
                raise ValueError("mount target must be under /workspace")
            updated.append(part)
        else:
            updated.append(part)
    if not seen_src or not seen_dst:
        raise ValueError("mount must include src and dst")
    return ",".join(updated)


def _validated_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc
    if timeout < 1 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds out of range")
    return timeout


def _run(argv: list[str], timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        return {
            "exit_code": int(completed.returncode),
            "stdout": _redact(completed.stdout),
            "stderr": _redact(completed.stderr),
            "timed_out": False,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "stdout": _redact(exc.stdout or ""),
            "stderr": _redact(exc.stderr or "command timed out"),
            "timed_out": True,
            "duration_seconds": round(time.monotonic() - started, 3),
        }


def _redact(value: Any) -> str:
    text = str(value or "")
    lowered = text.lower()
    if any(marker in lowered for marker in ("token", "secret", "password", "api_key", "bearer ")):
        return "[redacted]"
    return text[:MAX_OUTPUT_CHARS]


if __name__ == "__main__":
    raise SystemExit(main())
