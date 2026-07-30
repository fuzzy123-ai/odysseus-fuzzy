#!/usr/bin/env python3
"""Read-only, redacted readback for the selected Compose candidate host target.

The program accepts no arguments.  It may be piped to ``/usr/bin/python3 -``
on the Debian host, where it performs only filesystem metadata checks and one
bounded metadata query through the exact target venv interpreter.  It never
mutates files, invokes a package manager, contacts a network, or emits child
output, paths, versions, environment values, or exception text.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_compose_candidate_host_readback.v1"
TARGET_PATH = "/home/homebase/.local/share/odysseus-compose-1.6.0"
TEMP_TARGET_PATH = TARGET_PATH + ".tmp"
EXPECTED_USER = "homebase"
EXPECTED_VERSION = "1.6.0"
METADATA_TIMEOUT_SECONDS = 5
MAX_METADATA_OUTPUT_CHARS = 64
_MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
_METADATA_PROGRAM = (
    "import importlib.metadata as m"
    "\ntry:\n d=m.distribution('podman-compose'); print('present-exact' if d.version=='1.6.0' else 'present-other')"
    "\nexcept m.PackageNotFoundError: print('absent')"
    "\nexcept Exception: print('invalid')"
)
_STATES = frozenset({
    "target_ready", "target_absent", "target_unsafe", "temp_present",
    "target_incomplete", "metadata_unavailable", "version_not_exact",
    "host_precondition_failed", "invalid_invocation", "internal_error",
})
_ENVELOPE_KEYS = frozenset({
    "schema_id", "status", "state", "expected_user", "target_exists",
    "target_is_directory", "target_is_symlink", "temp_exists",
    "temp_is_directory", "temp_is_symlink", "venv_python_regular",
    "venv_python_executable", "podman_compose_distribution_present",
    "exact_version_1_6_0", "evidence_sha256",
})


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_envelope(payload: Any) -> bool:
    if type(payload) is not dict or set(payload) != _ENVELOPE_KEYS:
        return False
    if payload.get("schema_id") != SCHEMA_ID or type(payload.get("state")) is not str or payload["state"] not in _STATES:
        return False
    if type(payload.get("status")) is not str or payload.get("status") not in {"ok", "observed"}:
        return False
    if (payload["status"] == "ok") != (payload["state"] == "target_ready"):
        return False
    bool_keys = _ENVELOPE_KEYS - {"schema_id", "status", "state", "evidence_sha256"}
    if any(type(payload.get(key)) is not bool for key in bool_keys):
        return False
    digest = payload.get("evidence_sha256")
    return type(digest) is str and len(digest) == 64 and digest == _digest(payload)


def _envelope(state: str, flags: Mapping[str, bool]) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": "ok" if state == "target_ready" else "observed",
        "state": state if state in _STATES else "internal_error",
        **{key: bool(flags.get(key, False)) for key in _ENVELOPE_KEYS - {"schema_id", "status", "state", "evidence_sha256"}},
    }
    payload["evidence_sha256"] = _digest(payload)
    if not validate_envelope(payload):
        raise RuntimeError("fixed envelope construction failed")
    return payload


class ReadbackFilesystem:
    """Read-only adapter; tests may supply a synthetic equivalent."""

    def current_user(self) -> str:
        return getpass.getuser()

    def exists(self, path: str) -> bool:
        return Path(path).exists() or Path(path).is_symlink()

    def is_directory(self, path: str) -> bool:
        return Path(path).is_dir() and not Path(path).is_symlink()

    def is_symlink(self, path: str) -> bool:
        return Path(path).is_symlink()

    def is_regular_file(self, path: str) -> bool:
        return Path(path).is_file() and not Path(path).is_symlink()

    def is_executable(self, path: str) -> bool:
        return os.access(path, os.X_OK) and not Path(path).is_symlink()


Runner = Callable[..., Any]


def _venv_python(root: str) -> str:
    return root + "/bin/python"


def _metadata_state(runner: Runner) -> str:
    """Return only a fixed local classification; child output is discarded."""
    try:
        result = runner(
            [_venv_python(TARGET_PATH), "-I", "-c", _METADATA_PROGRAM],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=METADATA_TIMEOUT_SECONDS, check=False, shell=False, env=dict(_MINIMAL_ENV),
        )
    except Exception:
        return "invalid"
    output = getattr(result, "stdout", None)
    if getattr(result, "returncode", None) != 0 or type(output) is not str or len(output) > MAX_METADATA_OUTPUT_CHARS:
        return "invalid"
    return output if output in {"present-exact\n", "present-other\n", "absent\n"} else "invalid"


def collect_readback(*, filesystem: ReadbackFilesystem | None = None,
                     runner: Runner = subprocess.run) -> dict[str, Any]:
    """Read fixed metadata once and return a validated redacted envelope."""
    flags = {key: False for key in _ENVELOPE_KEYS - {"schema_id", "status", "state", "evidence_sha256"}}
    filesystem = filesystem or ReadbackFilesystem()
    try:
        flags["expected_user"] = filesystem.current_user() == EXPECTED_USER
        flags["target_exists"] = filesystem.exists(TARGET_PATH)
        flags["target_is_directory"] = filesystem.is_directory(TARGET_PATH)
        flags["target_is_symlink"] = filesystem.is_symlink(TARGET_PATH)
        flags["temp_exists"] = filesystem.exists(TEMP_TARGET_PATH)
        flags["temp_is_directory"] = filesystem.is_directory(TEMP_TARGET_PATH)
        flags["temp_is_symlink"] = filesystem.is_symlink(TEMP_TARGET_PATH)
        python_path = _venv_python(TARGET_PATH)
        flags["venv_python_regular"] = filesystem.is_regular_file(python_path)
        flags["venv_python_executable"] = filesystem.is_executable(python_path)
    except Exception:
        return _envelope("internal_error", flags)

    if not flags["expected_user"]:
        return _envelope("host_precondition_failed", flags)
    if flags["temp_exists"] or flags["temp_is_directory"] or flags["temp_is_symlink"]:
        return _envelope("temp_present", flags)
    if not flags["target_exists"]:
        return _envelope("target_absent", flags)
    if flags["target_is_symlink"] or not flags["target_is_directory"]:
        return _envelope("target_unsafe", flags)
    if not flags["venv_python_regular"] or not flags["venv_python_executable"]:
        return _envelope("target_incomplete", flags)

    metadata = _metadata_state(runner)
    flags["podman_compose_distribution_present"] = metadata in {"present-exact\n", "present-other\n"}
    flags["exact_version_1_6_0"] = metadata == "present-exact\n"
    if metadata == "present-exact\n":
        return _envelope("target_ready", flags)
    if metadata == "present-other\n":
        return _envelope("version_not_exact", flags)
    return _envelope("metadata_unavailable", flags)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    payload = _envelope("invalid_invocation", {}) if arguments else collect_readback()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if validate_envelope(payload) else 1


if __name__ == "__main__":
    raise SystemExit(main())
