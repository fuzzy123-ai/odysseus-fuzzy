#!/usr/bin/env python3
"""Default-disabled, one-use host-change transport for the selected Compose candidate.

The module performs no import-time work and ``run()`` is inert unless an exact,
unexpired future grant and ``execute=True`` are supplied.  Its result is always
a fixed-key redacted envelope; subprocess output, exception text, paths,
package metadata, and provider data are never returned or printed.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import getpass
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_compose_candidate_host_change.v1"
FUTURE_GRANT_ID = "SEC162-COMPOSE-CANDIDATE-HOST-CHANGE-GO"
TARGET_PATH = "/home/homebase/.local/share/odysseus-compose-1.6.0"
PARENT_PATH = "/home/homebase/.local/share"
TEMP_TARGET_PATH = TARGET_PATH + ".tmp"
REQUIREMENTS_PATH = TEMP_TARGET_PATH + "/requirements.txt"
SELECTED_PACKAGE = "podman-compose==1.6.0"
SELECTED_SDIST_SHA256 = "c83fd9bcbaa635100d581ce52a7a4b712ee0d457481232aff392efe3ebc5a217"
REQUIREMENTS_TEXT = SELECTED_PACKAGE + " --hash=sha256:" + SELECTED_SDIST_SHA256 + "\n"
EXPECTED_USER = "homebase"
EXPECTED_OS_ID = "debian"
EXPECTED_VERSION = "1.6.0"
MAX_GRANT_SECONDS = 600
COMMAND_TIMEOUT_SECONDS = 120
OFFICIAL_PYPI_SIMPLE_INDEX = "https://pypi.org/simple"
_MINIMAL_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PIP_CONFIG_FILE": os.devnull,
}

_STATUSES = frozenset({"not_executed", "blocked", "completed"})
_PHASES = frozenset({
    "execution_disabled", "attempt_already_consumed", "invalid_or_expired_grant",
    "unexpected_user", "unexpected_os", "unsafe_parent", "target_not_absent", "temp_not_absent",
    "venv_creation_failed", "temp_identity_unavailable", "requirements_write_failed", "pip_install_failed",
    "prepublish_identity_failed", "target_race_detected", "atomic_publish_failed", "publish_outcome_unknown",
    "postpublish_readback_failed",
    "completed", "internal_error",
})
_ENVELOPE_KEYS = frozenset({
    "schema_id", "status", "phase", "attempt_consumed", "retry_permitted",
    "rollback_performed", "target_published", "evidence_sha256",
})
_IDENTITY_PATH_EXPRESSION = (
    "root in module.parents and root in distribution_root.parents "
    "and distribution_root in module.parents"
)
_IDENTITY_PROGRAM = (
    "import importlib.metadata as metadata,pathlib,sys,podman_compose;"
    "root=pathlib.Path(sys.prefix).resolve();"
    "module=pathlib.Path(podman_compose.__file__).resolve();"
    "distribution=metadata.distribution('podman-compose');"
    "distribution_root=pathlib.Path(distribution.locate_file('')).resolve();"
    "version=distribution.version;"
    f"print('identity-ok' if {_IDENTITY_PATH_EXPRESSION} and version=='1.6.0' else 'identity-bad')"
)


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_envelope(payload: Any) -> bool:
    if type(payload) is not dict or set(payload) != _ENVELOPE_KEYS:
        return False
    if payload.get("schema_id") != SCHEMA_ID or type(payload.get("status")) is not str or payload.get("status") not in _STATUSES:
        return False
    if type(payload.get("phase")) is not str or payload.get("phase") not in _PHASES:
        return False
    if any(type(payload.get(key)) is not bool for key in (
        "attempt_consumed", "retry_permitted", "rollback_performed", "target_published",
    )):
        return False
    if payload["retry_permitted"] is not False:
        return False
    status, phase = payload["status"], payload["phase"]
    if status == "completed":
        if phase != "completed" or not payload["attempt_consumed"] or payload["rollback_performed"] or not payload["target_published"]:
            return False
    elif status == "not_executed":
        if phase not in {"execution_disabled", "attempt_already_consumed"}:
            return False
        if payload["target_published"] or payload["rollback_performed"]:
            return False
        if payload["attempt_consumed"] != (phase == "attempt_already_consumed"):
            return False
    else:
        if phase in {"execution_disabled", "attempt_already_consumed", "completed"}:
            return False
        if not payload["attempt_consumed"] or payload["target_published"]:
            return False
    digest = payload.get("evidence_sha256")
    return type(digest) is str and len(digest) == 64 and digest == _digest(payload)


def _envelope(status: str, phase: str, *, attempt: bool, rollback: bool = False,
              published: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": status if status in _STATUSES else "blocked",
        "phase": phase if phase in _PHASES else "internal_error",
        "attempt_consumed": bool(attempt),
        "retry_permitted": False,
        "rollback_performed": bool(rollback),
        "target_published": bool(published),
    }
    payload["evidence_sha256"] = _digest(payload)
    if not validate_envelope(payload):
        raise RuntimeError("fixed envelope construction failed")
    return payload


def _parse_expiry(value: Any) -> dt.datetime | None:
    if type(value) is not str or len(value) > 64:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def valid_execution_grant(grant_id: Any, expires_at: Any, *, now: dt.datetime | None = None) -> bool:
    if grant_id != FUTURE_GRANT_ID:
        return False
    expiry = _parse_expiry(expires_at)
    current = now or dt.datetime.now(dt.timezone.utc)
    if expiry is None or current.tzinfo is None:
        return False
    seconds_remaining = (expiry - current).total_seconds()
    return 0 < seconds_remaining <= MAX_GRANT_SECONDS


class HostFilesystem:
    """Small real filesystem adapter; tests provide a synthetic replacement."""

    def current_user(self) -> str:
        return getpass.getuser()

    def is_expected_debian(self) -> bool:
        if platform.system() != "Linux":
            return False
        try:
            lines = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        return "ID=" + EXPECTED_OS_ID in {line.strip() for line in lines}

    def exists(self, path: str) -> bool:
        return Path(path).exists() or Path(path).is_symlink()

    def is_symlink(self, path: str) -> bool:
        return Path(path).is_symlink()

    def is_directory(self, path: str) -> bool:
        return Path(path).is_dir() and not Path(path).is_symlink()

    def is_safe_parent(self, path: str) -> bool:
        candidate = Path(path)
        try:
            return (
                path == PARENT_PATH and candidate.is_dir() and not candidate.is_symlink()
                and str(candidate.resolve(strict=True)) == PARENT_PATH
            )
        except OSError:
            return False

    def write_requirements(self, path: str) -> None:
        Path(path).write_text(REQUIREMENTS_TEXT, encoding="utf-8", newline="\n")

    def ownership_token(self, path: str) -> tuple[int, int] | None:
        try:
            details = os.lstat(path)
        except OSError:
            return None
        if stat.S_ISLNK(details.st_mode):
            return None
        return details.st_dev, details.st_ino

    def publish_no_clobber(self, source: str, target: str, expected_token: object) -> tuple[int, int]:
        if source != TEMP_TARGET_PATH or target != TARGET_PATH or self.exists(target) or self.is_symlink(target):
            raise OSError("publish boundary rejected")
        if platform.system() != "Linux":
            raise OSError("no-clobber rename unavailable")
        renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        if renameat2 is None:
            raise OSError("no-clobber rename unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            raise OSError(ctypes.get_errno(), "no-clobber rename failed")
        token = self.ownership_token(target)
        if token is None or token != expected_token:
            raise OSError("published target identity unavailable")
        return token

    def remove_exact_owned_tree(self, path: str, token: object) -> bool:
        if path not in {TEMP_TARGET_PATH, TARGET_PATH}:
            raise ValueError("outside exact rollback boundary")
        candidate = Path(path)
        if token is None or self.ownership_token(path) != token:
            return False
        shutil.rmtree(candidate)
        return not candidate.exists() and not candidate.is_symlink()


Runner = Callable[..., Any]


def _run(command: Sequence[str], runner: Runner, *, capture: bool = False) -> str | None:
    try:
        result = runner(
            list(command), stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, timeout=COMMAND_TIMEOUT_SECONDS,
            check=False, shell=False, env=dict(_MINIMAL_ENV),
        )
    except Exception:
        return None
    if getattr(result, "returncode", None) != 0:
        return None
    if not capture:
        return ""
    output = getattr(result, "stdout", None)
    return output if type(output) is str and len(output) <= 128 else None


def _venv_python(root: str) -> str:
    return root + "/bin/python"


def _identity_matches(root: str, runner: Runner) -> bool:
    identity = _run((_venv_python(root), "-I", "-c", _IDENTITY_PROGRAM), runner, capture=True)
    return identity == "identity-ok\n"


class ComposeCandidateHostChange:
    """One-process, one-use executor.  Disabled calls never touch an adapter."""

    def __init__(self, *, filesystem: HostFilesystem | None = None, runner: Runner = subprocess.run) -> None:
        self._filesystem = filesystem
        self._runner = runner
        self._attempt_consumed = False

    def _result(self, status: str, phase: str, *, rollback: bool = False,
                published: bool = False) -> dict[str, Any]:
        return _envelope(status, phase, attempt=self._attempt_consumed, rollback=rollback, published=published)

    def _cleanup(self, filesystem: HostFilesystem, path: str, token: object) -> bool:
        if token is None:
            return False
        try:
            return filesystem.remove_exact_owned_tree(path, token)
        except Exception:
            return False

    def _publish_failure(self, filesystem: HostFilesystem, temp_token: object) -> dict[str, Any]:
        """Recover only a target whose opaque identity proves this attempt owns it."""
        try:
            target_token = filesystem.ownership_token(TARGET_PATH)
        except Exception:
            return self._result("blocked", "publish_outcome_unknown")
        if temp_token is not None and target_token == temp_token:
            return self._result(
                "blocked", "atomic_publish_failed",
                rollback=self._cleanup(filesystem, TARGET_PATH, target_token),
            )
        if filesystem.exists(TARGET_PATH) or filesystem.is_symlink(TARGET_PATH):
            # A target exists but is not provably ours.  It is deliberately not
            # removed and the result must not claim successful recovery.
            self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token)
            return self._result("blocked", "publish_outcome_unknown")
        return self._result(
            "blocked", "atomic_publish_failed",
            rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token),
        )

    def run(self, *, execute: bool = False, grant_id: str | None = None,
            expires_at: str | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
        if not execute:
            return self._result("not_executed", "execution_disabled")
        if self._attempt_consumed:
            return self._result("not_executed", "attempt_already_consumed")
        self._attempt_consumed = True
        if not valid_execution_grant(grant_id, expires_at, now=now):
            return self._result("blocked", "invalid_or_expired_grant")

        filesystem = self._filesystem or HostFilesystem()
        temp_created = False
        published = False
        temp_token: object = None
        target_token: object = None
        try:
            if filesystem.current_user() != EXPECTED_USER:
                return self._result("blocked", "unexpected_user")
            if not filesystem.is_expected_debian():
                return self._result("blocked", "unexpected_os")
            if not filesystem.is_safe_parent(PARENT_PATH):
                return self._result("blocked", "unsafe_parent")
            if filesystem.exists(TARGET_PATH) or filesystem.is_symlink(TARGET_PATH):
                return self._result("blocked", "target_not_absent")
            if filesystem.exists(TEMP_TARGET_PATH) or filesystem.is_symlink(TEMP_TARGET_PATH):
                return self._result("blocked", "temp_not_absent")

            venv_created = _run((sys.executable, "-m", "venv", "--system-site-packages", TEMP_TARGET_PATH), self._runner)
            temp_created = filesystem.exists(TEMP_TARGET_PATH)
            temp_token = filesystem.ownership_token(TEMP_TARGET_PATH) if temp_created else None
            if venv_created is None or not filesystem.is_directory(TEMP_TARGET_PATH) or filesystem.is_symlink(TEMP_TARGET_PATH):
                return self._result(
                    "blocked", "venv_creation_failed",
                    rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token) if temp_created else False,
                )
            if temp_token is None:
                return self._result("blocked", "temp_identity_unavailable")
            try:
                filesystem.write_requirements(REQUIREMENTS_PATH)
            except Exception:
                return self._result("blocked", "requirements_write_failed", rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token))
            pip_command = (
                _venv_python(TEMP_TARGET_PATH), "-m", "pip", "install", "--no-deps", "--no-binary", ":all:",
                "--no-build-isolation", "--require-hashes", "--isolated", "--no-input",
                "--disable-pip-version-check", "--no-cache-dir", "--index-url", OFFICIAL_PYPI_SIMPLE_INDEX,
                "-r", REQUIREMENTS_PATH,
            )
            if _run(pip_command, self._runner) is None:
                return self._result("blocked", "pip_install_failed", rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token))
            if not _identity_matches(TEMP_TARGET_PATH, self._runner):
                return self._result("blocked", "prepublish_identity_failed", rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token))
            if filesystem.exists(TARGET_PATH) or filesystem.is_symlink(TARGET_PATH):
                return self._result("blocked", "target_race_detected", rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token))
            try:
                target_token = filesystem.publish_no_clobber(TEMP_TARGET_PATH, TARGET_PATH, temp_token)
            except Exception:
                return self._publish_failure(filesystem, temp_token)
            temp_created = False
            published = True
            if not filesystem.is_directory(TARGET_PATH) or filesystem.is_symlink(TARGET_PATH) or not _identity_matches(TARGET_PATH, self._runner):
                return self._result("blocked", "postpublish_readback_failed", rollback=self._cleanup(filesystem, TARGET_PATH, target_token))
            return self._result("completed", "completed", published=True)
        except Exception:
            if published:
                return self._result("blocked", "internal_error", rollback=self._cleanup(filesystem, TARGET_PATH, target_token))
            if temp_created:
                return self._result("blocked", "internal_error", rollback=self._cleanup(filesystem, TEMP_TARGET_PATH, temp_token))
            return self._result("blocked", "internal_error")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one explicitly granted redacted Compose host change.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--grant-id")
    parser.add_argument("--expires-at")
    args = parser.parse_args(argv)
    payload = ComposeCandidateHostChange().run(
        execute=args.execute, grant_id=args.grant_id, expires_at=args.expires_at,
    )
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
