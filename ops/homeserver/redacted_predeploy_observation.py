#!/usr/bin/env python3
"""Emit one fixed, redacted D0 predeploy observation from the local host.

This wrapper is deliberately host-local and read-only.  It never invokes SSH,
reads an environment, runs a shell, or forwards command output or exceptions.
Every command below is a fixed argv array; command output remains internal and
is reduced to the D0 allowlisted projection before it can reach stdout.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_predeploy_observation.v1"
IDENTITY = "odysseus-homeserver:/opt/odysseus:odysseus-podman.service:odysseus_odysseus_1"
REPOSITORY = "/opt/odysseus"
APPROVED_BRANCH = "dev"
COMMAND_TIMEOUT_SECONDS = 1
OUTER_OBSERVATION_TIMEOUT_SECONDS = 30
BACKUP_OBSERVATION_TIMEOUT_SECONDS = 20
BASE_COMMAND_COUNT = 9
MAX_SOURCE_OUTPUT_CHARS = 65_536
MAX_DIRTY_ENTRIES = 4096
BACKUP_FRESHNESS_LIMIT_SECONDS = 86_400

_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHORT_REVISION = re.compile(r"^[0-9a-f]{8}$")
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SERVICE_STATUSES = frozenset({"active", "inactive", "failed", "activating", "deactivating", "unknown"})
_CONTAINER_STATUSES = frozenset({"running", "created", "exited", "paused", "unknown"})
_ERRORS = frozenset({
    "wrapper_missing", "wrapper_integrity_unverified", "identity_mismatch",
    "repository_unavailable", "revision_unavailable", "branch_unallowed",
    "worktree_dirty", "dirty_count_out_of_range", "upstream_relation_unallowed",
    "service_status_unallowed", "container_status_unallowed",
    "api_version_unavailable", "api_revision_mismatch", "backup_readiness_unavailable",
    "rollback_snapshot_unavailable", "rollback_snapshot_unsafe", "rollback_snapshot_invalid",
    "timeout", "malformed_output", "unexpected_field", "source_redaction_failure",
    "internal_error",
})
_OK_KEYS = frozenset({
    "schema_id", "status", "identity", "repository_revision", "branch",
    "worktree_clean", "dirty_entry_count", "upstream_relation",
    "odysseus_podman_service_active", "odysseus_podman_service_status",
    "odysseus_container_running", "odysseus_container_status",
    "api_version_revision_matches", "backup_ready", "rollback_snapshot_available",
    "rollback_snapshot_id", "rollback_snapshot_source_identity",
    "rollback_snapshot_age_seconds", "rollback_snapshot_fresh",
    "rollback_snapshot_observation_evidence_sha256", "raw_environment_visible", "secret_values_visible",
    "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "evidence_sha256"})

BACKUP_OBSERVATION_SCHEMA_ID = "odysseus.redacted_backup_snapshot_observation.v1"
BACKUP_REPOSITORY_IDENTITY = "restic_homeserver_backup_v1"
BACKUP_SOURCE_IDENTITY = "odysseus_protected_source_v1"
_BACKUP_OBSERVATION_OK_KEYS = frozenset({
    "schema_id", "status", "repository_identity", "protected_source_identity",
    "snapshot_id", "source_included", "snapshot_age_seconds", "snapshot_fresh",
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible",
    "environment_visible", "file_contents_visible", "paths_visible",
    "hostnames_visible", "secret_values_visible", "evidence_sha256",
})
_BACKUP_VISIBILITY_KEYS = frozenset({
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible",
    "environment_visible", "file_contents_visible", "paths_visible",
    "hostnames_visible", "secret_values_visible",
})

PRINCIPAL_COMMAND = ("id", "-un")
HOSTNAME_COMMAND = ("hostname",)
REVISION_COMMAND = ("git", "-C", REPOSITORY, "rev-parse", "HEAD")
BRANCH_COMMAND = ("git", "-C", REPOSITORY, "branch", "--show-current")
STATUS_COMMAND = ("git", "-C", REPOSITORY, "status", "--porcelain=v1", "-z", "--untracked-files=all")
UPSTREAM_COMMAND = ("git", "-C", REPOSITORY, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
SERVICE_COMMAND = ("systemctl", "--user", "is-active", "odysseus-podman.service")
CONTAINER_COMMAND = ("podman", "inspect", "--format", "{{.State.Status}}", "odysseus_odysseus_1")
_API_VERSION_PROGRAM = (
    "import json,urllib.request; "
    "response=urllib.request.urlopen('http://127.0.0.1:7000/api/version',timeout=2); "
    "payload=json.load(response); "
    "commit=payload.get('commit') if isinstance(payload,dict) else None; "
    "print(commit if isinstance(commit,str) else '')"
)
API_VERSION_COMMAND = ("podman", "exec", "odysseus_odysseus_1", "python3", "-I", "-c", _API_VERSION_PROGRAM)


class ObservationFailure(ValueError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code if error_code in _ERRORS else "internal_error"


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def blocked(error_code: str) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": error_code if error_code in _ERRORS else "internal_error"}
    payload["evidence_sha256"] = _canonical_digest(payload)
    return payload


def _source_output(result: Any) -> tuple[int, str]:
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    if isinstance(returncode, bool) or not isinstance(returncode, int) or not isinstance(stdout, str):
        raise ObservationFailure("malformed_output")
    if len(stdout) > MAX_SOURCE_OUTPUT_CHARS:
        raise ObservationFailure("source_redaction_failure")
    return returncode, stdout


def _run(command: Sequence[str], runner: Callable[..., Any]) -> tuple[int, str]:
    try:
        result = runner(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise ObservationFailure("timeout") from None
    except Exception:
        raise ObservationFailure("internal_error") from None
    return _source_output(result)


def _single_line(raw: str, *, error_code: str) -> str:
    value = raw.strip()
    if not value or "\n" in value or "\r" in value:
        raise ObservationFailure(error_code)
    return value


def _dirty_count(raw: str) -> int:
    if not raw:
        return 0
    if not raw.endswith("\0"):
        raise ObservationFailure("malformed_output")
    tokens = raw.split("\0")[:-1]
    count = 0
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        if len(entry) < 4 or entry[2] != " " or any(char not in " MADRCU?!" for char in entry[:2]):
            raise ObservationFailure("malformed_output")
        count += 1
        if count > MAX_DIRTY_ENTRIES:
            raise ObservationFailure("dirty_count_out_of_range")
        index += 1
        if "R" in entry[:2] or "C" in entry[:2]:
            if index >= len(tokens):
                raise ObservationFailure("malformed_output")
            index += 1
    return count


def _upstream_relation(raw: str) -> str:
    value = _single_line(raw, error_code="malformed_output")
    match = re.fullmatch(r"(0|[1-9][0-9]*)\t(0|[1-9][0-9]*)", value)
    if match is None:
        raise ObservationFailure("malformed_output")
    left, right = (int(item) for item in match.groups())
    if left == 0 and right == 0:
        return "upstream_equal"
    if left and right:
        return "diverged"
    return "local_ahead" if left else "remote_ahead"


def _load_backup_observer() -> Callable[[], dict[str, Any]]:
    """Load SEC128 in package or direct-script mode without spawning a process."""
    try:
        from ops.homeserver.redacted_backup_snapshot_observation import collect_backup_snapshot_observation
        return collect_backup_snapshot_observation
    except ModuleNotFoundError:
        sibling = Path(__file__).with_name("redacted_backup_snapshot_observation.py")
        spec = importlib.util.spec_from_file_location("odysseus_sec128_backup_snapshot_observation", sibling)
        if spec is None or spec.loader is None:
            raise ObservationFailure("backup_readiness_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observer = getattr(module, "collect_backup_snapshot_observation", None)
        if not callable(observer):
            raise ObservationFailure("backup_readiness_unavailable")
        return observer


def _production_backup_observer() -> dict[str, Any]:
    """Use SEC128 in-process; it owns the sole fixed Restic query."""
    try:
        return _load_backup_observer()()
    except ObservationFailure:
        raise
    except Exception:
        # The outer redaction boundary maps this to a fixed D0 code.
        raise ObservationFailure("backup_readiness_unavailable") from None


def _validated_backup_snapshot(backup_observer: Callable[[], Any]) -> tuple[str, str, int, str]:
    """Independently validate the complete SEC128 public result once."""
    try:
        candidate = backup_observer()
    except ObservationFailure:
        raise
    except Exception:
        raise ObservationFailure("backup_readiness_unavailable") from None
    if type(candidate) is not dict:
        raise ObservationFailure("backup_readiness_unavailable")
    if candidate.get("status") != "ok":
        raise ObservationFailure("rollback_snapshot_unavailable")
    if set(candidate) != _BACKUP_OBSERVATION_OK_KEYS:
        raise ObservationFailure("rollback_snapshot_unsafe")
    if candidate.get("schema_id") != BACKUP_OBSERVATION_SCHEMA_ID:
        raise ObservationFailure("rollback_snapshot_unsafe")
    if candidate.get("repository_identity") != BACKUP_REPOSITORY_IDENTITY or candidate.get("protected_source_identity") != BACKUP_SOURCE_IDENTITY:
        raise ObservationFailure("rollback_snapshot_unsafe")
    if candidate.get("source_included") is not True or candidate.get("snapshot_fresh") is not True:
        raise ObservationFailure("rollback_snapshot_invalid")
    if any(candidate.get(key) is not False for key in _BACKUP_VISIBILITY_KEYS):
        raise ObservationFailure("source_redaction_failure")
    snapshot_id = candidate.get("snapshot_id")
    age_seconds = candidate.get("snapshot_age_seconds")
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ObservationFailure("rollback_snapshot_invalid")
    if isinstance(age_seconds, bool) or not isinstance(age_seconds, int) or not 0 <= age_seconds <= BACKUP_FRESHNESS_LIMIT_SECONDS:
        raise ObservationFailure("rollback_snapshot_invalid")
    digest = candidate.get("evidence_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or digest != _canonical_digest(candidate):
        raise ObservationFailure("rollback_snapshot_invalid")
    return snapshot_id, BACKUP_SOURCE_IDENTITY, age_seconds, digest


def collect_predeploy_observation(
    *, runner: Callable[..., Any] = subprocess.run,
    backup_observer: Callable[[], Any] = _production_backup_observer,
) -> dict[str, Any]:
    """Collect and redact D0 facts, returning one valid `ok` or blocked object."""
    try:
        code, principal = _run(PRINCIPAL_COMMAND, runner)
        if code != 0 or _single_line(principal, error_code="identity_mismatch") != "homebase":
            raise ObservationFailure("identity_mismatch")
        code, hostname = _run(HOSTNAME_COMMAND, runner)
        if code != 0 or _single_line(hostname, error_code="identity_mismatch") != "debian":
            raise ObservationFailure("identity_mismatch")

        code, revision_raw = _run(REVISION_COMMAND, runner)
        if code != 0:
            raise ObservationFailure("repository_unavailable")
        revision = _single_line(revision_raw, error_code="revision_unavailable")
        if not _REVISION.fullmatch(revision):
            raise ObservationFailure("revision_unavailable")
        code, branch_raw = _run(BRANCH_COMMAND, runner)
        if code != 0:
            raise ObservationFailure("repository_unavailable")
        branch = _single_line(branch_raw, error_code="branch_unallowed")
        if not _BRANCH.fullmatch(branch) or branch != APPROVED_BRANCH:
            raise ObservationFailure("branch_unallowed")
        code, dirty_raw = _run(STATUS_COMMAND, runner)
        if code != 0:
            raise ObservationFailure("repository_unavailable")
        dirty_entry_count = _dirty_count(dirty_raw)
        if dirty_entry_count:
            raise ObservationFailure("worktree_dirty")
        code, upstream_raw = _run(UPSTREAM_COMMAND, runner)
        relation = "no_upstream" if code != 0 else _upstream_relation(upstream_raw)
        if relation != "upstream_equal":
            raise ObservationFailure("upstream_relation_unallowed")

        service_code, service_raw = _run(SERVICE_COMMAND, runner)
        service_status = _single_line(service_raw, error_code="service_status_unallowed")
        if service_code != 0 or service_status not in _SERVICE_STATUSES or service_status != "active":
            raise ObservationFailure("service_status_unallowed")
        container_code, container_raw = _run(CONTAINER_COMMAND, runner)
        container_status = _single_line(container_raw, error_code="container_status_unallowed")
        if container_code != 0 or container_status not in _CONTAINER_STATUSES or container_status != "running":
            raise ObservationFailure("container_status_unallowed")

        code, api_raw = _run(API_VERSION_COMMAND, runner)
        if code != 0:
            raise ObservationFailure("api_version_unavailable")
        api_revision = _single_line(api_raw, error_code="api_version_unavailable")
        if not _SHORT_REVISION.fullmatch(api_revision):
            raise ObservationFailure("api_version_unavailable")
        if api_revision != revision[:8]:
            raise ObservationFailure("api_revision_mismatch")
        snapshot_id, source_identity, snapshot_age_seconds, snapshot_digest = _validated_backup_snapshot(backup_observer)

        payload = {
            "schema_id": SCHEMA_ID, "status": "ok", "identity": IDENTITY,
            "repository_revision": revision, "branch": branch, "worktree_clean": True,
            "dirty_entry_count": 0, "upstream_relation": relation,
            "odysseus_podman_service_active": True,
            "odysseus_podman_service_status": service_status,
            "odysseus_container_running": True,
            "odysseus_container_status": container_status,
            "api_version_revision_matches": True,
            "backup_ready": True, "rollback_snapshot_available": True,
            "rollback_snapshot_id": snapshot_id,
            "rollback_snapshot_source_identity": source_identity,
            "rollback_snapshot_age_seconds": snapshot_age_seconds,
            "rollback_snapshot_fresh": True,
            "rollback_snapshot_observation_evidence_sha256": snapshot_digest,
            "raw_environment_visible": False,
            "secret_values_visible": False,
        }
        payload["evidence_sha256"] = _canonical_digest(payload)
        if set(payload) != _OK_KEYS:
            raise ObservationFailure("unexpected_field")
        return payload
    except ObservationFailure as exc:
        return blocked(exc.error_code)
    except Exception:
        return blocked("internal_error")


def main() -> int:
    try:
        payload = collect_predeploy_observation()
    except Exception:
        payload = blocked("internal_error")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
