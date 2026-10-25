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
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit
from ops.homeserver import redacted_backup_snapshot_observation as snapshot_observer
from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle_lock
from ops.homeserver import redacted_transactional_app_deploy_readback as deploy_readback

SCHEMA_ID = "odysseus.redacted_transactional_app_deploy.v1"
PACKET_SCHEMA_ID = "odysseus.transactional_app_deploy_packet.v1"
TARGET_ROOT = "/opt/odysseus"
RELEASE_WORKTREE = "/opt/odysseus-release-transactional-app"
LOCK_PATH = lifecycle_lock.SHARED_LOCK
TRANSACTION_DEADLINE_SECONDS = 1140
CLEANUP_RESERVE_SECONDS = 30
_TERMINATE_GRACE_SECONDS = 2
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
    ("unknown", "preflight", "unknown", False),
})


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_envelope(payload: Any) -> bool:
    if type(payload) is not dict or set(payload) != _ENVELOPE_KEYS:
        return False
    if payload.get("schema_id") != SCHEMA_ID:
        return False
    status = payload.get("status")
    phase = payload.get("effect_phase")
    outcome = payload.get("outcome")
    rollback = payload.get("rollback_attempted")
    retry = payload.get("retry_permitted")
    if type(status) is not str or type(phase) is not str or type(outcome) is not str:
        return False
    if type(rollback) is not bool or retry is not False:
        return False
    if status not in _STATUSES or phase not in _PHASES or outcome not in _OUTCOMES:
        return False
    if (status, phase, outcome, rollback) not in _TUPLES:
        return False
    digest = payload.get("evidence_sha256")
    return type(digest) is str and _HEX64.fullmatch(digest) is not None and digest == _digest(payload)


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
    bounded_runner = _DeadlineProcessRunner() if runner is subprocess.run else runner
    baseline: list[deploy_readback.RuntimeBaseline | None] = [None]
    def capture() -> object | None:
        candidate = deploy_readback.capture_runtime_baseline(runner=bounded_runner)
        if candidate is None or candidate.revision != packet.old_revision or candidate.manifest_sha256 != packet.old_manifest_sha256 or not candidate.expected_mounts:
            return None
        baseline[0] = candidate
        return candidate
    def readback(bound: DeployPacket, revision: str) -> bool:
        payload = deploy_readback.collect_host_readback(
            deploy_readback.ReadbackExpectation(revision, bound.manifest_sha256 if revision == bound.new_revision else bound.old_manifest_sha256), (baseline[0].dependency_digests if baseline[0] else ()), checkout_required=(revision != bound.new_revision), runner=bounded_runner,
        )
        return deploy_readback.validate_envelope(payload) and payload["status"] == "ok"
    return TransactionalAppDeployExecutor(runner=bounded_runner, readback=readback, baseline_factory=capture).run(packet, execute=True)


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
        self._fd = lifecycle_lock.acquire_shared_lock()
        return self
    def __exit__(self, exc_type: object, exc: object, trace: object) -> None:
        if self._fd is not None:
            lifecycle_lock.close_lock(self._fd)
            self._fd = None


class ProcessTimedOut(TimeoutError):
    """A child reached its bounded deadline and was killed and reaped."""


class _DeadlineProcessRunner:
    """Run each command in a killable process group under one absolute budget."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic, popen: Any = subprocess.Popen) -> None:
        self._monotonic = monotonic
        self._popen = popen
        self._cutoff: float | None = None

    def begin(self) -> None:
        started = self._monotonic()
        self._cutoff = started + TRANSACTION_DEADLINE_SECONDS - CLEANUP_RESERVE_SECONDS

    def _remaining(self) -> float:
        if self._cutoff is None:
            self.begin()
        return max(0.0, float(self._cutoff) - self._monotonic())

    @staticmethod
    def _signal_group(process: Any, sig: int) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        elif sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    @staticmethod
    def _group_exists(process: Any) -> bool:
        """Return whether the owned POSIX process group still has members."""
        if os.name != "posix":
            return False
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _terminate_residual_group(self, process: Any, *, deadline: float) -> bool:
        """Remove all same-session descendants and verify the group disappeared."""
        if os.name != "posix":
            return True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        term_deadline = min(deadline, self._monotonic() + 0.1)
        try:
            process.wait(timeout=max(0.0, term_deadline - self._monotonic()))
        except subprocess.TimeoutExpired:
            pass
        while self._monotonic() < term_deadline and self._group_exists(process):
            time.sleep(0.01)
        if self._group_exists(process):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return True
        try:
            process.wait(timeout=max(0.0, deadline - self._monotonic()))
        except subprocess.TimeoutExpired:
            return False
        while self._monotonic() < deadline:
            if not self._group_exists(process):
                return True
            time.sleep(0.01)
        return not self._group_exists(process)

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        requested_timeout = kwargs.pop("timeout", 60)
        input_value = kwargs.pop("input", None)
        check = kwargs.pop("check", False)
        if check is not False:
            raise ValueError("bounded runner does not support check=True")
        try:
            finite_timeout = math.isfinite(float(requested_timeout))
        except (OverflowError, TypeError, ValueError):
            finite_timeout = False
        if type(requested_timeout) not in (int, float) or not finite_timeout or requested_timeout <= 0 or requested_timeout > 1230:
            raise ValueError("invalid child timeout")
        allowed = min(float(requested_timeout), self._remaining())
        if allowed <= 0:
            raise ProcessTimedOut("transaction deadline reached")
        if input_value is not None and type(input_value) is not bytes:
            raise ValueError("bounded runner accepts bytes input only")
        if input_value is not None and len(input_value) > 1_700_000:
            raise ValueError("child input exceeded the fixed bound")
        supplied_stdin = kwargs.pop("stdin", subprocess.DEVNULL)
        if input_value is not None:
            if supplied_stdin not in (None, subprocess.DEVNULL):
                raise ValueError("input and explicit stdin cannot both be set")
            stdin_value: Any = subprocess.PIPE
        else:
            if supplied_stdin not in (None, subprocess.DEVNULL):
                raise ValueError("bounded runner requires explicit byte input for piped stdin")
            stdin_value = subprocess.DEVNULL
        if kwargs.get("stderr") == subprocess.PIPE:
            raise ValueError("bounded runner does not capture child stderr")

        process = None
        selector = None
        timed_out = False
        stdout: Any = None
        stderr: Any = None
        output_too_large = False
        input_closed_early = False
        input_offset = 0
        captured = bytearray()
        stdout_eof = kwargs.get("stdout") != subprocess.PIPE
        deadline = self._monotonic() + allowed
        cleanup_deadline = deadline + _TERMINATE_GRACE_SECONDS
        cleanup_verified = True

        def read_stdout() -> None:
            nonlocal stdout_eof, output_too_large
            assert process is not None and process.stdout is not None and selector is not None
            while not stdout_eof:
                try:
                    chunk = os.read(process.stdout.fileno(), 257)
                except BlockingIOError:
                    return
                if not chunk:
                    stdout_eof = True
                    try:
                        selector.unregister(process.stdout)
                    except KeyError:
                        pass
                    return
                remaining = 257 - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])
                if len(captured) > 256:
                    output_too_large = True
                    return

        try:
            if self._monotonic() >= deadline:
                raise ProcessTimedOut("transaction deadline reached before process start")
            process = self._popen(
                command,
                stdin=stdin_value,
                close_fds=True,
                start_new_session=(os.name == "posix"),
                **kwargs,
            )
            if process.stdin is not None and input_value is not None and input_value:
                os.set_blocking(process.stdin.fileno(), False)
            elif process.stdin is not None and input_value is not None:
                process.stdin.close()

            if process.stdout is not None:
                os.set_blocking(process.stdout.fileno(), False)
            selector = selectors.DefaultSelector()
            if process.stdout is not None:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            if process.stdin is not None and input_value:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")

            while True:
                now = self._monotonic()
                if now >= deadline:
                    timed_out = process.poll() is None or not stdout_eof or input_offset < len(input_value or b"")
                    break
                if process.poll() is not None:
                    if input_offset < len(input_value or b""):
                        input_closed_early = True
                    if stdout_eof or selector is None or not any(key.data == "stdout" for key in selector.get_map().values()):
                        break
                    events = selector.select(0)
                    if not events:
                        break
                else:
                    remaining = deadline - now
                    events = selector.select(min(remaining, 0.05)) if selector is not None and selector.get_map() else []
                    if not events:
                        time.sleep(min(0.01, remaining))

                for key, _mask in events:
                    if key.data == "stdout":
                        read_stdout()
                        if output_too_large:
                            break
                        continue
                    try:
                        count = os.write(process.stdin.fileno(), input_value[input_offset : input_offset + 65536])
                    except BrokenPipeError:
                        input_closed_early = input_offset < len(input_value)
                        try:
                            selector.unregister(process.stdin)
                        except KeyError:
                            pass
                        process.stdin.close()
                        continue
                    if count <= 0:
                        input_closed_early = True
                        selector.unregister(process.stdin)
                        process.stdin.close()
                        continue
                    input_offset += count
                    if input_offset == len(input_value):
                        selector.unregister(process.stdin)
                        process.stdin.close()

                if output_too_large:
                    break

            if process.poll() is not None and input_offset < len(input_value or b""):
                input_closed_early = True
            if process.poll() is None and (timed_out or output_too_large or input_closed_early):
                self._signal_group(process, signal.SIGTERM)
            cleanup_verified = self._terminate_residual_group(process, deadline=cleanup_deadline)
            try:
                process.wait(timeout=max(0.0, cleanup_deadline - self._monotonic()))
            except subprocess.TimeoutExpired:
                cleanup_verified = False

            if process.stdout is not None and not stdout_eof and selector is not None:
                drain_deadline = min(cleanup_deadline, self._monotonic() + 0.25)
                while self._monotonic() < drain_deadline and not stdout_eof and not output_too_large:
                    events = selector.select(min(0.02, drain_deadline - self._monotonic())) if selector.get_map() else []
                    for key, _mask in events:
                        if key.data == "stdout":
                            read_stdout()

            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if selector is not None:
                selector.close()
                selector = None
            # Oversized output deliberately stops pipe draining.  Cleanup is
            # established by process-group termination and leader reaping.
            if not stdout_eof and not output_too_large:
                cleanup_verified = False
            if not cleanup_verified:
                raise RuntimeError("child process-group cleanup was not verified")
            if output_too_large:
                raise ValueError("child output exceeded the fixed capture bound")
            if input_closed_early:
                raise BrokenPipeError("child closed bounded input early")

            returncode = process.returncode
            stdout = bytes(captured) if kwargs.get("stdout") == subprocess.PIPE else None
            if kwargs.get("text") is True and stdout is not None:
                stdout = stdout.decode(kwargs.get("encoding") or "utf-8", errors=kwargs.get("errors") or "strict")
            stderr = None
            result = subprocess.CompletedProcess(command, returncode, stdout, stderr)
            result.timed_out = timed_out
            return result
        except BaseException:
            if process is not None:
                try:
                    self._signal_group(process, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    process.wait(timeout=max(0.0, cleanup_deadline - self._monotonic()))
                except subprocess.TimeoutExpired:
                    pass
                for stream_name in ("stdin", "stdout", "stderr"):
                    stream = getattr(process, stream_name, None)
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
            raise
        finally:
            if selector is not None:
                selector.close()


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
        self._effect_may_have_started = False
        self._runtime_switch_attempted = False

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
        if self._command_may_mutate(command):
            self._effect_may_have_started = True
        try:
            result = self._runner(list(command), stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, text=True, check=False, shell=False,
                                  env=self._environment(getattr(self, "_revision", "")), timeout=self._timeout(command))
        except ProcessTimedOut:
            raise
        except Exception:
            return None
        if getattr(result, "timed_out", False) is True:
            raise ProcessTimedOut("child reached its bounded deadline")
        if getattr(result, "returncode", None) != 0:
            return None
        output = getattr(result, "stdout", "")
        return output if capture and type(output) is str and len(output) <= 256 else ("" if not capture else None)

    @staticmethod
    def _command_may_mutate(command: tuple[str, ...]) -> bool:
        if command[:2] == ("git", "-C"):
            return any(
                marker in command
                for marker in ("fetch", "merge", "worktree")
            )
        if command[:2] == ("podman", "tag"):
            return True
        return "build" in command or "up" in command

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
        self._runtime_switch_attempted = True
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
        self._effect_may_have_started = False
        self._runtime_switch_attempted = False
        begin = getattr(self._runner, "begin", None)
        if callable(begin):
            try:
                begin()
            except Exception:
                return _envelope("blocked", "preflight", "failed", False)
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
                if not preflight or image is None:
                    if self._effect_may_have_started:
                        return _envelope("unknown", "preflight", "unknown", False)
                    return _envelope("blocked", "preflight", "failed", False)
                switch_state = self._switch(image, packet)
                if switch_state == "not_started":
                    if self._effect_may_have_started:
                        return _envelope("unknown", "preflight", "unknown", False)
                    return _envelope("blocked", "preflight", "failed", False)
                switched = switch_state in {"switched", "ambiguous"} or self._runtime_switch_attempted
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
            if switched or self._runtime_switch_attempted:
                try:
                    rolled = self._rollback(packet)
                    restored = rolled and self._readback is not None and self._readback(packet, packet.old_revision)
                except Exception: pass
                else:
                    if restored:
                        return _envelope("rolled_back", "rollback_verified", "rolled_back", True)
                return _envelope("unknown", "rollback_attempted", "unknown", True)
            if self._effect_may_have_started:
                return _envelope("unknown", "preflight", "unknown", False)
            return _envelope("blocked", "preflight", "failed", False)


def main(argv: list[str] | None = None) -> int:
    # No caller-supplied packet or enable flag can cross the program boundary.
    arguments = sys.argv[1:] if argv is None else argv
    payload = _envelope("not_executed", "not_run", "not_run", False) if not arguments else _envelope("blocked", "preflight", "failed", False)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
