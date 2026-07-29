#!/usr/bin/env python3
"""Run one immutable, source-redacted Podman Compose observation over SSH.

The observer is read from the already-published Git object, not from a remote
checkout.  Its verified bytes are piped to one fixed Python-stdin invocation.
This module never exposes SSH output, stderr, or exception text.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from typing import Any, Callable, Mapping


OBSERVER_SCHEMA_ID = "odysseus.redacted_podman_compose_capability_observation.v1"
TRANSPORT_SCHEMA_ID = "odysseus.redacted_podman_compose_capability_transport.v1"
OBSERVER_PATH = "ops/homeserver/redacted_podman_compose_capability_observation.py"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
PUBLISHED_OBJECT = f"{PUBLISHED_REF}:{OBSERVER_PATH}"
PUBLISHED_OBSERVER_SHA256 = "01e648a9a861cee1b3ff446e1807b8bc840d3ffc5338208fe80d1209e49fd82e"
EXPECTED_VERSION = "1.3.0"
GIT_READ_TIMEOUT_SECONDS = 5
WORKSTATION_TIMEOUT_SECONDS = 20
MAX_OBSERVER_BYTES = 200_000
MAX_RESPONSE_BYTES = 8_192
REMOTE_COMMAND = "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s /usr/bin/python3 -"
SSH_COMMAND = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver", REMOTE_COMMAND)

_SHA256 = "0123456789abcdef"
_TRANSPORT_CODES = frozenset({
    "published_blob_unavailable", "published_blob_mismatch", "transport_timeout",
    "transport_failed", "transport_invalid", "invalid_invocation",
})
_VISIBILITY_KEYS = frozenset({
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
    "source_text_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
})
_OK_KEYS = frozenset({
    "schema_id", "status", "podman_compose_version", "global_env_file_parser_present",
    "global_project_name_parser_present", "service_scoped_build_parser_present",
    "service_scoped_up_parser_present", "no_deps_parser_present", "no_build_parser_present",
    "rollback_force_recreate_parser_present", "service_scoped_dependency_exclusion_proven",
    "rollback_force_recreate_proven", "deployment_capability_supported", *_VISIBILITY_KEYS,
    "evidence_sha256",
})
_NEEDS_KEYS = frozenset({"schema_id", "status", "reason_code", "retry_permitted", "evidence_sha256"})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "retry_permitted", "evidence_sha256"})
_NEEDS_REASONS = frozenset({"semantic_proof_insufficient"})
_OBSERVER_ERRORS = frozenset({
    "version_unavailable", "version_mismatch", "help_unavailable", "source_audit_unavailable",
    "source_audit_invalid", "malformed_output", "output_too_large", "timeout", "internal_error",
})
_VERSION_DIAGNOSTIC_CODES = frozenset({
    "version_output_empty", "version_output_controls", "version_output_multiline",
    "version_output_line_shape", "version_output_version_mismatch",
})
_VERSION_BLOCKED_KEYS = _BLOCKED_KEYS | {"diagnostic_code"}


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def transport_blocked(code: str) -> dict[str, Any]:
    payload = {
        "schema_id": TRANSPORT_SCHEMA_ID,
        "status": "blocked",
        "error_code": code if code in _TRANSPORT_CODES else "transport_invalid",
        "retry_permitted": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _result_bytes(result: Any) -> bytes | None:
    value = getattr(result, "stdout", None)
    return value if isinstance(value, bytes) else None


def _load_published_observer(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(
            ["git", "cat-file", "blob", PUBLISHED_OBJECT], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=False, timeout=GIT_READ_TIMEOUT_SECONDS, check=False, shell=False,
        )
    except Exception:
        return None
    source = _result_bytes(result)
    if getattr(result, "returncode", None) != 0 or not source or len(source) > MAX_OBSERVER_BYTES:
        return None
    return source


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in _SHA256 for character in value)


def _validate_observer_payload(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
        line = text.strip()
        if not line or "\n" in line or "\r" in line:
            return None
        payload = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if type(payload) is not dict or payload.get("schema_id") != OBSERVER_SCHEMA_ID:
        return None
    status = payload.get("status")
    if status == "ok":
        if set(payload) != _OK_KEYS or payload.get("podman_compose_version") != EXPECTED_VERSION:
            return None
        required_true = _OK_KEYS - {"schema_id", "status", "podman_compose_version", "evidence_sha256", *_VISIBILITY_KEYS}
        if any(payload.get(key) is not True for key in required_true) or any(payload.get(key) is not False for key in _VISIBILITY_KEYS):
            return None
    elif status == "needs_live_observation":
        if set(payload) != _NEEDS_KEYS or payload.get("reason_code") not in _NEEDS_REASONS or payload.get("retry_permitted") is not False:
            return None
    elif status == "blocked":
        keys = set(payload)
        is_generic = keys == _BLOCKED_KEYS
        is_version_diagnostic = (
            keys == _VERSION_BLOCKED_KEYS
            and payload.get("error_code") in {"malformed_output", "version_mismatch"}
            and payload.get("diagnostic_code") in _VERSION_DIAGNOSTIC_CODES
        )
        if (not (is_generic or is_version_diagnostic) or payload.get("error_code") not in _OBSERVER_ERRORS
                or payload.get("retry_permitted") is not False):
            return None
    else:
        return None
    if not _valid_sha256(payload.get("evidence_sha256")) or payload["evidence_sha256"] != _digest(payload):
        return None
    return {key: payload[key] for key in sorted(payload)}


def collect_published_podman_compose_capability_observation(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Return one validated observer envelope, or a fixed terminal transport envelope."""
    source = _load_published_observer(runner)
    if source is None:
        return transport_blocked("published_blob_unavailable")
    if hashlib.sha256(source).hexdigest() != PUBLISHED_OBSERVER_SHA256:
        return transport_blocked("published_blob_mismatch")
    try:
        result = runner(
            list(SSH_COMMAND), input=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=False, timeout=WORKSTATION_TIMEOUT_SECONDS, check=False, shell=False,
        )
    except subprocess.TimeoutExpired:
        return transport_blocked("transport_timeout")
    except Exception:
        return transport_blocked("transport_failed")
    response = _result_bytes(result)
    if response is None:
        return transport_blocked("transport_failed")
    if getattr(result, "returncode", None) not in (0, 1):
        return transport_blocked("transport_failed")
    validated = _validate_observer_payload(response)
    if validated is None:
        return transport_blocked("transport_invalid")
    expected_returncode = 0 if validated["status"] == "ok" else 1
    if getattr(result, "returncode", None) != expected_returncode:
        return transport_blocked("transport_failed")
    return validated


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    payload = transport_blocked("invalid_invocation") if argv else collect_published_podman_compose_capability_observation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
