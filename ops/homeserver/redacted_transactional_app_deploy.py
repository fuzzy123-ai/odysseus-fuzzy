#!/usr/bin/env python3
"""Default-disabled, app-only transactional deploy executor.

This module is deliberately not an updater.  It has no command-line switch
that can enable it and only models the later, owner-bound host action through
an injected runner.  Results are fixed-key, digest-bound redacted envelopes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit
from ops.homeserver import redacted_backup_snapshot_observation as snapshot_observer
from ops.homeserver import redacted_transactional_app_deploy_readback as deploy_readback

SCHEMA_ID = "odysseus.redacted_transactional_app_deploy.v1"
PACKET_SCHEMA_ID = "odysseus.transactional_app_deploy_packet.v1"
TARGET_ROOT = "/opt/odysseus"
RELEASE_WORKTREE = "/opt/odysseus-release-transactional-app"
LOCK_PATH = "/tmp/odysseus-auto-update.lock"
PROJECT = "odysseus"
APP_SERVICE = "odysseus"
APP_CONTAINER = "odysseus_odysseus_1"
APP_IMAGE = "odysseus_odysseus"
COMPOSE_FILE = "docker-compose.yml"
PRODUCTION_ENV_FILE = TARGET_ROOT + "/.env"
RUNTIME_PYTHON = "/home/homebase/.local/share/odysseus-compose-1.6.0/bin/python"
COMPOSE_COMMAND = (RUNTIME_PYTHON, "-m", "podman_compose")
MANIFEST_PATH = "runtime/release-manifest.json"
MANIFEST_GENERATOR_NAME = "scripts/generate_release_manifest.py"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"not_executed", "blocked", "succeeded", "rolled_back", "unknown"})
_PHASES = frozenset({"not_run", "preflight", "runtime_switched", "post_health", "rollback_attempted", "rollback_verified"})
_OUTCOMES = frozenset({"not_run", "failed", "succeeded", "rolled_back", "unknown"})
_ENVELOPE_KEYS = frozenset({"schema_id", "status", "effect_phase", "outcome", "rollback_attempted", "retry_permitted", "evidence_sha256"})
_TUPLES = frozenset({
    ("not_executed", "not_run", "not_run", False),
    ("blocked", "preflight", "failed", False),
    ("succeeded", "post_health", "succeeded", False),
    ("unknown", "post_health", "unknown", False),
    ("rolled_back", "rollback_verified", "rolled_back", True),
    ("unknown", "rollback_attempted", "unknown", True),
})


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_envelope(payload: Any) -> bool:
    return (
        type(payload) is dict and set(payload) == _ENVELOPE_KEYS
        and payload.get("schema_id") == SCHEMA_ID and payload.get("status") in _STATUSES
        and payload.get("effect_phase") in _PHASES and payload.get("outcome") in _OUTCOMES
        and (payload.get("status"), payload.get("effect_phase"), payload.get("outcome"), payload.get("rollback_attempted")) in _TUPLES and payload.get("retry_permitted") is False
        and type(payload.get("evidence_sha256")) is str and _HEX64.fullmatch(payload["evidence_sha256"]) is not None
        and payload["evidence_sha256"] == _digest(payload)
    )


def _envelope(status: str, phase: str, outcome: str, rollback: bool) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": status if status in _STATUSES else "unknown",
               "effect_phase": phase if phase in _PHASES else "not_run",
               "outcome": outcome if outcome in _OUTCOMES else "unknown",
               "rollback_attempted": rollback, "retry_permitted": False}
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _validate_snapshot(packet: "DeployPacket") -> bool:
    """Require a fresh, complete, independently digest-validated SEC129 proof."""
    try:
        payload = snapshot_observer.collect_backup_snapshot_observation()
        expected = snapshot_observer._OK_KEYS
        visibility = {key for key in expected if key.endswith("_visible")}
        digest = snapshot_observer._digest(payload)
        return (
            type(payload) is dict and set(payload) == expected and payload.get("schema_id") == snapshot_observer.SCHEMA_ID
            and payload.get("status") == "ok" and payload.get("repository_identity") == "restic_homeserver_backup_v1"
            and payload.get("protected_source_identity") == "odysseus_protected_source_v1"
            and payload.get("source_included") is True and payload.get("snapshot_fresh") is True
            and type(payload.get("snapshot_id")) is str and bool(_HEX64.fullmatch(payload["snapshot_id"]))
            and type(payload.get("snapshot_age_seconds")) is int and 0 <= payload["snapshot_age_seconds"] <= snapshot_observer.MAX_SNAPSHOT_AGE_SECONDS
            and all(payload.get(key) is False for key in visibility)
            and payload.get("evidence_sha256") == digest and payload.get("snapshot_id") == packet.snapshot_id
        )
    except Exception:
        return False


def _safe_fetch_url(value: Any) -> bool:
    """Validate the exact source identity without returning or logging its URL."""
    if type(value) is not str or len(value) > 512 or not value.endswith("\n") or value.count("\n") != 1 or "\r" in value:
        return False
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and parsed.hostname == "github.com" and parsed.port is None and parsed.query == ""
            and parsed.fragment == "" and parsed.path == "/fuzzy123-ai/odysseus-fuzzy.git")


def production_entrypoint(packet_value: Any, *, execute: bool = False, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Concrete host path used only by the fixed packet-gated transport."""
    packet = packet_value if isinstance(packet_value, DeployPacket) else DeployPacket.from_mapping(packet_value)
    if not execute or packet is None:
        return _envelope("not_executed", "not_run", "not_run", False)
    baseline: list[deploy_readback.RuntimeBaseline | None] = [None]
    def capture() -> object | None:
        candidate = deploy_readback.capture_runtime_baseline(runner=runner)
        if candidate is None or candidate.revision != packet.old_revision or candidate.manifest_sha256 != packet.old_manifest_sha256 or not candidate.expected_mounts:
            return None
        baseline[0] = candidate
        return candidate
    def readback(bound: DeployPacket, revision: str) -> bool:
        payload = deploy_readback.collect_host_readback(
            deploy_readback.ReadbackExpectation(revision, bound.manifest_sha256 if revision == bound.new_revision else bound.old_manifest_sha256), (baseline[0].dependency_digests if baseline[0] else ()), checkout_required=(revision != bound.new_revision), runner=runner,
        )
        return deploy_readback.validate_envelope(payload) and payload["status"] == "ok"
    return TransactionalAppDeployExecutor(runner=runner, readback=readback, baseline_factory=capture).run(packet, execute=True)


@dataclass(frozen=True, slots=True)
class DeployPacket:
    """The exact immutable inputs which must be owner-bound before execution."""
    old_revision: str
    new_revision: str
    snapshot_evidence_sha256: str
    snapshot_id: str
    manifest_sha256: str
    old_manifest_sha256: str
    delivery_disabled: bool

    def valid(self) -> bool:
        return (
            bool(_HEX40.fullmatch(self.old_revision)) and bool(_HEX40.fullmatch(self.new_revision))
            and self.old_revision != self.new_revision and bool(_HEX64.fullmatch(self.snapshot_evidence_sha256)) and bool(_HEX64.fullmatch(self.snapshot_id))
            and bool(_HEX64.fullmatch(self.manifest_sha256)) and bool(_HEX64.fullmatch(self.old_manifest_sha256)) and self.delivery_disabled is True
        )

    @classmethod
    def from_mapping(cls, value: Any) -> "DeployPacket | None":
        if type(value) is not dict or set(value) != {"schema_id", "old_revision", "new_revision", "snapshot_evidence_sha256", "snapshot_id", "manifest_sha256", "old_manifest_sha256", "delivery_disabled"}:
            return None
        if value.get("schema_id") != PACKET_SCHEMA_ID or value.get("delivery_disabled") is not True:
            return None
        fields = ("old_revision", "new_revision", "snapshot_evidence_sha256", "snapshot_id", "manifest_sha256", "old_manifest_sha256")
        if any(type(value[name]) is not str for name in fields):
            return None
        if not _HEX40.fullmatch(value["old_revision"]) or not _HEX40.fullmatch(value["new_revision"]):
            return None
        if value["old_revision"] == value["new_revision"] or any(not _HEX64.fullmatch(value[name]) for name in fields[2:]):
            return None
        packet = cls(*(value[name] for name in fields), delivery_disabled=True)
        return packet if packet.valid() else None


class _Lock(Protocol):
    def __enter__(self) -> object: ...
    def __exit__(self, exc_type: object, exc: object, trace: object) -> None: ...


class _HostLock:
    """One non-blocking advisory lock held across the whole transaction."""
    def __init__(self) -> None:
        self._fd: int | None = None
    def __enter__(self) -> object:
        import fcntl
        self._fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(self._fd); self._fd = None
            raise
        return self
    def __exit__(self, exc_type: object, exc: object, trace: object) -> None:
        if self._fd is not None:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN); os.close(self._fd); self._fd = None


Runner = Callable[..., Any]
Readback = Callable[[DeployPacket, str], bool]
SnapshotValidator = Callable[[DeployPacket], bool]


class TransactionalAppDeployExecutor:
    """Single-use executor; failures after the switch make one rollback attempt."""
    def __init__(self, *, runner: Runner = subprocess.run, readback: Readback | None = None,
                 snapshot_validator: SnapshotValidator | None = None, baseline_factory: Callable[[], object | None] | None = None,
                 path_exists: Callable[[str], bool] = os.path.lexists,
                 lock_factory: Callable[[], _Lock] = _HostLock) -> None:
        self._runner, self._readback, self._snapshot_validator = runner, readback, snapshot_validator or _validate_snapshot
        self._path_exists, self._lock_factory = path_exists, lock_factory
        self._baseline_factory = baseline_factory
        self._consumed = False

    def _environment(self, revision: str) -> dict[str, str]:
        uid = getattr(os, "getuid", lambda: 1000)()
        return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "HOME": "/home/homebase", "USER": "homebase", "LOGNAME": "homebase",
                "XDG_RUNTIME_DIR": f"/run/user/{uid}", "ODYSSEUS_RELEASE_REVISION": revision,
                "ODYSSEUS_GIT_COMMIT": revision, "ODYSSEUS_GIT_SHORT_COMMIT": revision[:8],
                "ODYSSEUS_GIT_BRANCH": "dev", "APP_DATA_DIR": "/opt/odysseus/data",
                "APP_LOGS_DIR": "/opt/odysseus/logs", "UNIVERSAL_INBOX_HOST_PATH": "/opt/odysseus/data/universal-inbox",
                "UNIVERSAL_INBOX_PATH": "/app/universal-inbox"}

    @staticmethod
    def _timeout(command: tuple[str, ...]) -> int:
        if command and command[-2:] == ("build", APP_SERVICE): return 900
        if command and command[-1:] == ("fetch",): return 180
        if "fetch" in command: return 180
        if "up" in command: return 240
        return 60

    def _run(self, command: tuple[str, ...], *, capture: bool = False) -> str | None:
        try:
            result = self._runner(list(command), stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, text=True, check=False, shell=False,
                                  env=self._environment(getattr(self, "_revision", "")), timeout=self._timeout(command))
        except Exception:
            return None
        if getattr(result, "returncode", None) != 0:
            return None
        output = getattr(result, "stdout", "")
        return output if capture and type(output) is str and len(output) <= 256 else ("" if not capture else None)

    def _preflight(self, packet: DeployPacket) -> tuple[bool, str | None]:
        if self._path_exists(RELEASE_WORKTREE):
            return False, None
        if self._run(("git", "-C", TARGET_ROOT, "status", "--porcelain"), capture=True) != "": return False, None
        if self._run(("git", "-C", TARGET_ROOT, "rev-parse", "HEAD"), capture=True) != packet.old_revision + "\n": return False, None
        if not _safe_fetch_url(self._run(("git", "-C", TARGET_ROOT, "remote", "get-url", "fuzzy"), capture=True) or ""):
            return False, None
        # Exactly one bounded source update, while the transaction lock is held.
        if self._run(("git", "-C", TARGET_ROOT, "fetch", "fuzzy", "dev")) is None: return False, None
        if self._run(("git", "-C", TARGET_ROOT, "rev-parse", "fuzzy/dev"), capture=True) != packet.new_revision + "\n": return False, None
        if self._run(("git", "-C", TARGET_ROOT, "merge-base", "--is-ancestor", packet.old_revision, packet.new_revision)) is None: return False, None
        if self._run(("git", "-C", TARGET_ROOT, "worktree", "add", "--detach", RELEASE_WORKTREE, packet.new_revision)) is None: return False, None
        manifest_command = ("/usr/bin/python3", RELEASE_WORKTREE + "/" + MANIFEST_GENERATOR_NAME, "--repo", RELEASE_WORKTREE, "--output", RELEASE_WORKTREE + "/" + MANIFEST_PATH, "--revision", packet.new_revision, "--ref", "dev", "--max-commits", "100")
        if self._run(manifest_command) is None: return False, None
        manifest = self._run(("sha256sum", RELEASE_WORKTREE + "/" + MANIFEST_PATH), capture=True)
        if manifest is None or manifest.split(maxsplit=1)[0] != packet.manifest_sha256: return False, None
        baseline = getattr(self, "_runtime_baseline", None)
        image = getattr(baseline, "image_id", None)
        if image is None or not _HEX64.fullmatch(image.strip().removeprefix("sha256:")): return False, None
        return True, image.strip()

    def _switch(self, image: str, packet: DeployPacket) -> str:
        tag = f"{PROJECT}-transactional-rollback:{packet.old_revision}"
        if self._run(("podman", "tag", image, tag)) is None: return "not_started"
        prefix = (*COMPOSE_COMMAND, "--project-name", PROJECT, "--env-file", PRODUCTION_ENV_FILE, "-f", RELEASE_WORKTREE + "/" + COMPOSE_FILE)
        if self._run((*prefix, "build", APP_SERVICE)) is None: return "not_started"
        # A failed service switch is ambiguous: Compose may have recreated the
        # app before reporting its error.  Treat it as post-switch and rollback.
        return "switched" if self._run((*prefix, "up", "-d", "--no-deps", "--no-build", "--force-recreate", APP_SERVICE)) is not None else "ambiguous"

    def _rollback(self, packet: DeployPacket) -> bool:
        tag = f"{PROJECT}-transactional-rollback:{packet.old_revision}"
        self._revision = packet.old_revision
        if self._run(("podman", "tag", tag, APP_IMAGE)) is None: return False
        prefix = (*COMPOSE_COMMAND, "--project-name", PROJECT, "--env-file", PRODUCTION_ENV_FILE, "-f", TARGET_ROOT + "/" + COMPOSE_FILE)
        return self._run((*prefix, "up", "-d", "--no-deps", "--no-build", "--force-recreate", APP_SERVICE)) is not None

    def run(self, packet_value: Any, *, execute: bool = False) -> dict[str, Any]:
        packet = packet_value if isinstance(packet_value, DeployPacket) else DeployPacket.from_mapping(packet_value)
        if not execute: return _envelope("not_executed", "not_run", "not_run", False)
        if self._consumed: return _envelope("not_executed", "not_run", "not_run", False)
        self._consumed = True
        if packet is None or not packet.valid() or self._readback is None or self._snapshot_validator is None: return _envelope("blocked", "preflight", "failed", False)
        switched = False
        self._revision = packet.new_revision
        try:
            with self._lock_factory():
                baseline = self._baseline_factory() if self._baseline_factory is not None else None
                if self._baseline_factory is not None and baseline is None:
                    return _envelope("blocked", "preflight", "failed", False)
                self._runtime_baseline = baseline
                try: snapshot_valid = self._snapshot_validator(packet)
                except Exception: snapshot_valid = False
                if snapshot_valid is not True: return _envelope("blocked", "preflight", "failed", False)
                preflight, image = self._preflight(packet)
                if not preflight or image is None: return _envelope("blocked", "preflight", "failed", False)
                switch_state = self._switch(image, packet)
                if switch_state == "not_started": return _envelope("blocked", "preflight", "failed", False)
                switched = True
                if switch_state == "ambiguous":
                    rolled = self._rollback(packet)  # exactly one best-effort rollback; never retry it
                    try: restored = rolled and self._readback(packet, packet.old_revision)
                    except Exception: restored = False
                    return _envelope("rolled_back" if restored else "unknown", "rollback_verified" if restored else "rollback_attempted", "rolled_back" if restored else "unknown", True)
                try: healthy = self._readback(packet, packet.new_revision)
                except Exception: healthy = False
                # The production checkout advances only after independent app
                # and dependency readback; it is always exact and ff-only.
                if healthy:
                    merged = self._run(("git", "-C", TARGET_ROOT, "merge", "--ff-only", packet.new_revision)) is not None
                    checkout = self._run(("git", "-C", TARGET_ROOT, "rev-parse", "HEAD"), capture=True)
                    if checkout == packet.new_revision + "\n":
                        return _envelope("succeeded", "post_health", "succeeded", False)
                    if merged or checkout not in {packet.old_revision + "\n", None}:
                        return _envelope("unknown", "post_health", "unknown", False)
                rolled = self._rollback(packet)
                if not rolled: return _envelope("unknown", "rollback_attempted", "unknown", True)
                try: restored = self._readback(packet, packet.old_revision)
                except Exception: restored = False
                return _envelope("rolled_back" if restored else "unknown", "rollback_verified" if restored else "rollback_attempted", "rolled_back" if restored else "unknown", True)
        except Exception:
            # An exception after the switch is ambiguous.  Make precisely one
            # best-effort rollback and retain unknown rather than retrying.
            if switched:
                try:
                    rolled = self._rollback(packet)
                    restored = rolled and self._readback is not None and self._readback(packet, packet.old_revision)
                except Exception: pass
                else:
                    if restored:
                        return _envelope("rolled_back", "rollback_verified", "rolled_back", True)
                return _envelope("unknown", "rollback_attempted", "unknown", True)
            return _envelope("blocked", "preflight", "failed", False)


def main(argv: list[str] | None = None) -> int:
    # No caller-supplied packet or enable flag can cross the program boundary.
    arguments = sys.argv[1:] if argv is None else argv
    payload = _envelope("not_executed", "not_run", "not_run", False) if not arguments else _envelope("blocked", "preflight", "failed", False)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
