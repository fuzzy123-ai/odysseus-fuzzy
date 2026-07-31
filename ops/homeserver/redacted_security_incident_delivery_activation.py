#!/usr/bin/env python3
"""One-use, default-disabled transaction to enable only security-alert delivery."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ops.homeserver import redacted_backup_snapshot_observation as snapshot_observer
from ops.homeserver import redacted_security_incident_delivery_activation_readback as readback

SCHEMA_ID = "odysseus.redacted_security_incident_delivery_activation.v1"
PACKET_SCHEMA_ID = "odysseus.security_incident_delivery_activation_packet.v1"
TARGET_ROOT = "/opt/odysseus"
PRODUCTION_ENV_FILE = TARGET_ROOT + "/.env"
LOCK_PATH = "/tmp/odysseus-auto-update.lock"
APP_SERVICE = "odysseus"
RUNTIME_PYTHON = "/home/homebase/.local/share/odysseus-compose-1.6.0/bin/python"
COMPOSE_COMMAND = (RUNTIME_PYTHON, "-m", "podman_compose")
_FLAG = b"ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED"
_HEX40, _HEX64 = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$")
_MAX_ENV_BYTES, _MAX_PACKET_LIFETIME = 1024 * 1024, 900
_ENVELOPE_KEYS = frozenset({"schema_id", "status", "effect_phase", "outcome", "rollback_attempted", "retry_permitted", "evidence_sha256"})
_TUPLES = frozenset({("not_executed", "not_run", "not_run", False), ("blocked", "preflight", "failed", False), ("succeeded", "post_health", "succeeded", False), ("rolled_back", "rollback_verified", "rolled_back", True), ("unknown", "rollback_attempted", "unknown", True)})


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _envelope(status: str, phase: str, outcome: str, rollback: bool) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": status, "effect_phase": phase, "outcome": outcome, "rollback_attempted": rollback, "retry_permitted": False}; payload["evidence_sha256"] = _digest(payload); return payload


def validate_envelope(value: Any) -> bool:
    return type(value) is dict and set(value) == _ENVELOPE_KEYS and value.get("schema_id") == SCHEMA_ID and (value.get("status"), value.get("effect_phase"), value.get("outcome"), value.get("rollback_attempted")) in _TUPLES and value.get("retry_permitted") is False and type(value.get("evidence_sha256")) is str and _HEX64.fullmatch(value["evidence_sha256"]) is not None and value["evidence_sha256"] == _digest(value)


@dataclass(frozen=True, slots=True)
class ActivationPacket:
    expected_revision: str
    manifest_sha256: str
    snapshot_id: str
    prior_snapshot_evidence_sha256: str
    expires_at: int
    enable: bool
    def valid(self, now: int) -> bool:
        return bool(_HEX40.fullmatch(self.expected_revision) and _HEX64.fullmatch(self.manifest_sha256) and _HEX64.fullmatch(self.snapshot_id) and _HEX64.fullmatch(self.prior_snapshot_evidence_sha256) and self.enable is True and type(self.expires_at) is int and now <= self.expires_at <= now + _MAX_PACKET_LIFETIME)
    @classmethod
    def from_mapping(cls, value: Any) -> "ActivationPacket | None":
        fields = ("expected_revision", "manifest_sha256", "snapshot_id", "prior_snapshot_evidence_sha256", "expires_at", "enable")
        if type(value) is not dict or set(value) != {"schema_id", *fields} or value.get("schema_id") != PACKET_SCHEMA_ID or any(type(value.get(key)) is not str for key in fields[:4]) or type(value.get("expires_at")) is not int or value.get("enable") is not True:
            return None
        return cls(*(value[key] for key in fields))


class _Lock(Protocol):
    def __enter__(self) -> object: ...
    def __exit__(self, exc_type: object, exc: object, trace: object) -> None: ...


class _HostLock:
    def __init__(self) -> None: self._fd: int | None = None
    def __enter__(self) -> object:
        import fcntl
        self._fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        try: fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception: os.close(self._fd); self._fd = None; raise
        return self
    def __exit__(self, exc_type: object, exc: object, trace: object) -> None:
        if self._fd is not None:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN); os.close(self._fd); self._fd = None


def _replacement(original: bytes) -> bytes | None:
    if len(original) > _MAX_ENV_BYTES or b"\x00" in original:
        return None
    try: original.decode("utf-8")
    except UnicodeDecodeError: return None
    lines = original.splitlines(keepends=True); found = []
    for index, line in enumerate(lines):
        bare = line.rstrip(b"\r\n")
        candidate = bare.lstrip(b" \t")
        if candidate.startswith(b"export") and len(candidate) > len(b"export") and candidate[len(b"export"):len(b"export") + 1] in b" \t":
            candidate = candidate[len(b"export"):].lstrip(b" \t")
        target_like = candidate.startswith(_FLAG) and (len(candidate) == len(_FLAG) or candidate[len(_FLAG):len(_FLAG) + 1] in b"= \t")
        if target_like:
            if bare != candidate or not bare.startswith(_FLAG + b"="): return None
            found.append(index)
    if len(found) > 1: return None
    replacement = _FLAG + b"=true"
    if found:
        old = lines[found[0]]; value = old.rstrip(b"\r\n")[len(_FLAG) + 1:]
        if value not in {b"", b"false", b"0", b"no", b"off"}: return None
        lines[found[0]] = replacement + (b"\r\n" if old.endswith(b"\r\n") else b"\n" if old.endswith(b"\n") else b"")
        return b"".join(lines)
    return original + (b"" if not original or original.endswith((b"\n", b"\r")) else b"\n") + replacement + b"\n"


def _atomic_write(path: str, payload: bytes, original_stat: os.stat_result) -> str:
    directory = os.path.dirname(path)
    fd = -1; temporary = ""; replaced = False
    try:
        fd, temporary = tempfile.mkstemp(prefix=".odysseus-delivery-", dir=directory)
        if os.name == "posix":
            os.fchmod(fd, stat.S_IMODE(original_stat.st_mode))
            os.fchown(fd, original_stat.st_uid, original_stat.st_gid)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0: return "not_replaced"
            view = view[written:]
        os.fsync(fd); os.close(fd); fd = -1
        os.replace(temporary, path); temporary = ""; replaced = True
        directory_fd = os.open(directory, os.O_RDONLY); os.fsync(directory_fd); os.close(directory_fd)
        return "complete"
    except Exception:
        return "ambiguous" if replaced else "not_replaced"
    finally:
        if fd >= 0: os.close(fd)
        if temporary:
            try: os.unlink(temporary)
            except Exception: pass


@dataclass(frozen=True, slots=True)
class EnvMutation:
    original: bytes
    original_stat: os.stat_result
    confirmed: bool


def replace_delivery_flag() -> EnvMutation | None:
    """Replace only the fixed key; original secret-bearing bytes stay in memory."""
    try:
        before = os.lstat(PRODUCTION_ENV_FILE)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_size > _MAX_ENV_BYTES: return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0); fd = os.open(PRODUCTION_ENV_FILE, flags)
        try:
            current = os.fstat(fd); original = os.read(fd, _MAX_ENV_BYTES + 1)
        finally: os.close(fd)
        if not stat.S_ISREG(current.st_mode) or current.st_ino != before.st_ino or current.st_dev != before.st_dev or len(original) > _MAX_ENV_BYTES: return None
        changed = _replacement(original)
        if changed is None: return None
        outcome = _atomic_write(PRODUCTION_ENV_FILE, changed, before)
        return None if outcome == "not_replaced" else EnvMutation(original, before, outcome == "complete")
    except Exception: return None


def _exact_file_matches(expected: bytes, original_stat: os.stat_result) -> bool:
    try:
        before = os.lstat(PRODUCTION_ENV_FILE)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or stat.S_IMODE(before.st_mode) != stat.S_IMODE(original_stat.st_mode) or before.st_uid != original_stat.st_uid or before.st_gid != original_stat.st_gid or before.st_size != len(expected): return False
        fd = os.open(PRODUCTION_ENV_FILE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try: current, actual = os.fstat(fd), os.read(fd, _MAX_ENV_BYTES + 1)
        finally: os.close(fd)
        return current.st_ino == before.st_ino and current.st_dev == before.st_dev and actual == expected
    except Exception: return False


def restore_delivery_flag(mutation: EnvMutation) -> bool:
    outcome = _atomic_write(PRODUCTION_ENV_FILE, mutation.original, mutation.original_stat)
    return outcome == "complete" or (outcome == "ambiguous" and _exact_file_matches(mutation.original, mutation.original_stat))


class DeliveryActivationExecutor:
    def __init__(self, *, runner: Callable[..., Any] = subprocess.run, now: Callable[[], float] = time.time, snapshot_validator: Callable[[ActivationPacket], bool] | None = None, baseline_factory: Callable[[], readback.RuntimeBaseline | None] = readback.capture_runtime_baseline, readback_factory: Callable[[readback.ReadbackExpectation, readback.RuntimeBaseline], Mapping[str, Any]] = readback.collect_host_readback, lock_factory: Callable[[], _Lock] = _HostLock) -> None:
        self._runner, self._now, self._snapshot_validator, self._baseline_factory, self._readback_factory, self._lock_factory = runner, now, snapshot_validator or _validate_snapshot, baseline_factory, readback_factory, lock_factory; self._consumed = False
    def _recreate(self) -> bool:
        command = (*COMPOSE_COMMAND, "--project-name", "odysseus", "--env-file", PRODUCTION_ENV_FILE, "-f", TARGET_ROOT + "/docker-compose.yml", "up", "-d", "--no-deps", "--no-build", "--force-recreate", APP_SERVICE)
        try: result = self._runner(list(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, timeout=240, check=False, shell=False)
        except Exception: return False
        return getattr(result, "returncode", None) == 0
    def _readback(self, packet: ActivationPacket, baseline: readback.RuntimeBaseline, enabled: bool) -> bool:
        try: observed = self._readback_factory(readback.ReadbackExpectation(packet.expected_revision, packet.manifest_sha256, enabled), baseline)
        except Exception: return False
        return readback.validate_envelope(observed) and observed.get("status") == "ok"
    def run(self, value: Any, *, execute: bool = False) -> dict[str, Any]:
        packet = value if isinstance(value, ActivationPacket) else ActivationPacket.from_mapping(value)
        if not execute or self._consumed: return _envelope("not_executed", "not_run", "not_run", False)
        self._consumed = True
        try: now = self._now()
        except Exception: return _envelope("blocked", "preflight", "failed", False)
        if type(now) not in {int, float} or isinstance(now, bool) or not math.isfinite(now) or now < 0: return _envelope("blocked", "preflight", "failed", False)
        if packet is None or not packet.valid(int(now)): return _envelope("blocked", "preflight", "failed", False)
        replaced: EnvMutation | None = None
        rollback_started = False
        baseline: readback.RuntimeBaseline | None = None

        def rollback() -> bool:
            nonlocal rollback_started
            rollback_started = True
            restored_file = restore_delivery_flag(replaced)  # type: ignore[arg-type]
            if not restored_file: return False
            recreated = self._recreate()
            return recreated and baseline is not None and self._readback(packet, baseline, False)
        try:
            with self._lock_factory():
                baseline = self._baseline_factory()
                if baseline is None or baseline.revision != packet.expected_revision or baseline.manifest_sha256 != packet.manifest_sha256 or not baseline.mounts_intact or not baseline.delivery_disabled or self._snapshot_validator(packet) is not True:
                    return _envelope("blocked", "preflight", "failed", False)
                replaced = replace_delivery_flag()
                if replaced is not None and replaced.confirmed and self._recreate() and self._readback(packet, baseline, True): return _envelope("succeeded", "post_health", "succeeded", False)
                if replaced is None: return _envelope("blocked", "preflight", "failed", False)
                restored = rollback()
                return _envelope("rolled_back" if restored else "unknown", "rollback_verified" if restored else "rollback_attempted", "rolled_back" if restored else "unknown", True)
        except Exception:
            if replaced is None: return _envelope("blocked", "preflight", "failed", False)
            if rollback_started: return _envelope("unknown", "rollback_attempted", "unknown", True)
            try: restored = rollback()
            except Exception: restored = False
            return _envelope("rolled_back" if restored else "unknown", "rollback_verified" if restored else "rollback_attempted", "rolled_back" if restored else "unknown", True)


def _validate_snapshot(packet: ActivationPacket) -> bool:
    try:
        value = snapshot_observer.collect_backup_snapshot_observation(); keys = snapshot_observer._OK_KEYS; visible = {key for key in keys if key.endswith("_visible")}
        return type(value) is dict and set(value) == keys and value.get("schema_id") == snapshot_observer.SCHEMA_ID and value.get("status") == "ok" and value.get("repository_identity") == "restic_homeserver_backup_v1" and value.get("protected_source_identity") == "odysseus_protected_source_v1" and value.get("source_included") is True and value.get("snapshot_fresh") is True and type(value.get("snapshot_age_seconds")) is int and 0 <= value["snapshot_age_seconds"] <= snapshot_observer.MAX_SNAPSHOT_AGE_SECONDS and value.get("snapshot_id") == packet.snapshot_id and type(value.get("snapshot_id")) is str and _HEX64.fullmatch(value["snapshot_id"]) is not None and all(value.get(key) is False for key in visible) and value.get("evidence_sha256") == snapshot_observer._digest(value) and value.get("evidence_sha256") == packet.prior_snapshot_evidence_sha256
    except Exception: return False


def production_entrypoint(packet: Any, *, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    return DeliveryActivationExecutor(runner=runner).run(packet, execute=execute)


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(_envelope("not_executed", "not_run", "not_run", False), ensure_ascii=True, sort_keys=True, separators=(",", ":"))); return 1
