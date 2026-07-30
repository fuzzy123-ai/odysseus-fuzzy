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
HISTORICAL_TRANSPORT_SCHEMA_ID = "odysseus.redacted_podman_compose_capability_transport.v1"
TRANSPORT_SCHEMA_ID = "odysseus.redacted_podman_compose_capability_transport.v2"
OBSERVER_PATH = "ops/homeserver/redacted_podman_compose_capability_observation.py"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
PUBLISHED_OBJECT = f"{PUBLISHED_REF}:{OBSERVER_PATH}"
PUBLISHED_OBSERVER_SHA256 = "c4a48afb4d6c92e94f96ce3c13cf200cfadfadaf6b8710e1ce8977791c713f09"
EXPECTED_VERSION = "1.6.0"
GIT_READ_TIMEOUT_SECONDS = 5
WORKSTATION_TIMEOUT_SECONDS = 20
MAX_OBSERVER_BYTES = 200_000
MAX_RESPONSE_BYTES = 8_192
REMOTE_OBSERVER_INTERPRETER = "/home/homebase/.local/share/odysseus-compose-1.6.0/bin/python"
REMOTE_COMMAND = f"cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s {REMOTE_OBSERVER_INTERPRETER} -"
SSH_COMMAND = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver", REMOTE_COMMAND)

_SHA256 = "0123456789abcdef"
_TRANSPORT_CODES = frozenset({
    "published_blob_unavailable", "published_blob_mismatch", "transport_timeout",
    "transport_failed", "transport_invalid", "invalid_invocation",
})
_TRANSPORT_PAIRS = frozenset({
    ("published_blob_unavailable", "published_blob_unavailable"),
    ("published_blob_mismatch", "published_blob_mismatch"),
    ("transport_timeout", "ssh_timeout"),
    ("transport_failed", "ssh_invocation_exception"),
    ("transport_failed", "ssh_stdout_unavailable"),
    ("transport_failed", "ssh_255_no_payload"),
    ("transport_failed", "ssh_255_invalid_payload"),
    ("transport_failed", "valid_payload_returncode_mismatch"),
    ("transport_invalid", "invalid_payload_expected_returncode"),
    ("transport_failed", "ssh_unexpected_returncode"),
    ("invalid_invocation", "invalid_invocation"),
    ("transport_invalid", "internal_contract_violation"),
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
_MISSING_PROOF_CODES = (
    "global_env_file_parser_missing", "global_project_name_parser_missing",
    "build_service_argument_missing", "up_service_argument_missing",
    "up_no_deps_parser_missing", "up_no_build_parser_missing",
    "up_force_recreate_parser_missing", "source_build_service_selection_missing",
    "source_up_service_selection_missing", "source_up_no_deps_guard_missing",
    "source_rollback_force_recreate_missing",
)
_RUNTIME_SHAPE_KEYS = frozenset({"help_grammar", "source_ast"})
_HELP_GRAMMAR_KEYS = frozenset({"build", "up"})
_USAGE_SHAPE_KEYS = frozenset({
    "usage_line_present", "uppercase_service_positional_grammar_present",
    "bracketed_lowercase_services_positional_grammar_present",
    "bare_lowercase_services_positional_grammar_present",
})
_SOURCE_AST_KEYS = frozenset({
    "compose_build_handler_present", "compose_up_handler_present", "get_excluded_handler_present",
    "exclusion_helper", "compose_up",
})
_EXCLUSION_HELPER_SHAPE_KEYS = frozenset({
    "exact_signature", "empty_set_initialization", "args_services_branch", "compose_services_set",
    "requested_service_loop", "dependency_lookup_subtraction", "selected_service_discard",
})
_COMPOSE_UP_SHAPE_KEYS = frozenset({
    "exact_exclusion_helper_assignment", "compose_containers_loop", "excluded_service_continue_guard",
    "no_deps_dependency_control_branch",
})
_NEEDS_KEYS = frozenset({
    "schema_id", "status", "reason_code", "missing_proofs", "retry_permitted",
    "runtime_shape_profile", "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "retry_permitted", "evidence_sha256"})
_TRANSPORT_BLOCKED_KEYS = _BLOCKED_KEYS | {"diagnostic_code"}
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


def transport_blocked(code: str, diagnostic_code: str) -> dict[str, Any]:
    pair = (code, diagnostic_code)
    if (
        type(code) is not str
        or type(diagnostic_code) is not str
        or pair not in _TRANSPORT_PAIRS
    ):
        pair = ("transport_invalid", "internal_contract_violation")
    payload = {
        "schema_id": TRANSPORT_SCHEMA_ID,
        "status": "blocked",
        "error_code": pair[0],
        "diagnostic_code": pair[1],
        "retry_permitted": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _result_bytes(result: Any) -> bytes | None:
    value = getattr(result, "stdout", None)
    return value if type(value) is bytes else None


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
    return type(value) is str and len(value) == 64 and all(character in _SHA256 for character in value)


def _validate_transport_evidence(payload: Any) -> dict[str, Any] | None:
    if type(payload) is not dict or payload.get("status") != "blocked":
        return None
    schema_id = payload.get("schema_id")
    if schema_id == HISTORICAL_TRANSPORT_SCHEMA_ID:
        error_code = payload.get("error_code")
        if (
            set(payload) != _BLOCKED_KEYS
            or type(error_code) is not str
            or error_code not in _TRANSPORT_CODES
            or payload.get("retry_permitted") is not False
        ):
            return None
    elif schema_id == TRANSPORT_SCHEMA_ID:
        error_code = payload.get("error_code")
        diagnostic_code = payload.get("diagnostic_code")
        if (
            set(payload) != _TRANSPORT_BLOCKED_KEYS
            or type(error_code) is not str
            or type(diagnostic_code) is not str
            or (error_code, diagnostic_code) not in _TRANSPORT_PAIRS
            or payload.get("retry_permitted") is not False
        ):
            return None
    else:
        return None
    if not _valid_sha256(payload.get("evidence_sha256")) or payload["evidence_sha256"] != _digest(payload):
        return None
    return {key: payload[key] for key in sorted(payload)}


def _all_literal_bools(payload: Mapping[str, Any], keys: frozenset[str]) -> bool:
    return set(payload) == keys and all(type(payload[key]) is bool for key in keys)


def _valid_runtime_shape_profile(value: Any) -> bool:
    if type(value) is not dict or set(value) != _RUNTIME_SHAPE_KEYS:
        return False
    help_grammar, source_ast = value["help_grammar"], value["source_ast"]
    if type(help_grammar) is not dict or set(help_grammar) != _HELP_GRAMMAR_KEYS:
        return False
    if any(type(help_grammar[name]) is not dict or not _all_literal_bools(help_grammar[name], _USAGE_SHAPE_KEYS)
           for name in _HELP_GRAMMAR_KEYS):
        return False
    if type(source_ast) is not dict or set(source_ast) != _SOURCE_AST_KEYS:
        return False
    if any(type(source_ast[key]) is not bool for key in {
        "compose_build_handler_present", "compose_up_handler_present", "get_excluded_handler_present",
    }):
        return False
    helper, compose_up = source_ast["exclusion_helper"], source_ast["compose_up"]
    return (
        type(helper) is dict and _all_literal_bools(helper, _EXCLUSION_HELPER_SHAPE_KEYS)
        and type(compose_up) is dict and _all_literal_bools(compose_up, _COMPOSE_UP_SHAPE_KEYS)
    )


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
        missing = payload.get("missing_proofs")
        reason_code = payload.get("reason_code")
        if (set(payload) != _NEEDS_KEYS or type(reason_code) is not str or reason_code not in _NEEDS_REASONS
                or payload.get("retry_permitted") is not False or type(missing) is not list
                or not missing or len(missing) > len(_MISSING_PROOF_CODES)
                or tuple(missing) != tuple(code for code in _MISSING_PROOF_CODES if code in missing)
                or not _valid_runtime_shape_profile(payload.get("runtime_shape_profile"))):
            return None
    elif status == "blocked":
        keys = set(payload)
        error_code = payload.get("error_code")
        diagnostic_code = payload.get("diagnostic_code")
        is_generic = keys == _BLOCKED_KEYS
        is_version_diagnostic = (
            keys == _VERSION_BLOCKED_KEYS
            and type(error_code) is str
            and error_code in {"malformed_output", "version_mismatch"}
            and type(diagnostic_code) is str
            and diagnostic_code in _VERSION_DIAGNOSTIC_CODES
        )
        if (not (is_generic or is_version_diagnostic) or type(error_code) is not str
                or error_code not in _OBSERVER_ERRORS
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
        return transport_blocked("published_blob_unavailable", "published_blob_unavailable")
    if hashlib.sha256(source).hexdigest() != PUBLISHED_OBSERVER_SHA256:
        return transport_blocked("published_blob_mismatch", "published_blob_mismatch")
    try:
        result = runner(
            list(SSH_COMMAND), input=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=False, timeout=WORKSTATION_TIMEOUT_SECONDS, check=False, shell=False,
        )
    except subprocess.TimeoutExpired:
        return transport_blocked("transport_timeout", "ssh_timeout")
    except Exception:
        return transport_blocked("transport_failed", "ssh_invocation_exception")
    response = _result_bytes(result)
    if response is None:
        return transport_blocked("transport_failed", "ssh_stdout_unavailable")
    validated = _validate_observer_payload(response)
    returncode = getattr(result, "returncode", None)
    if validated is not None:
        if validated["status"] == "ok" and type(returncode) is int and returncode == 0:
            return validated
        if (
            validated["status"] in {"needs_live_observation", "blocked"}
            and type(returncode) is int
            and returncode in {1, 255}
        ):
            return validated
        return transport_blocked("transport_failed", "valid_payload_returncode_mismatch")
    if type(returncode) is int and returncode == 255:
        diagnostic_code = "ssh_255_no_payload" if response == b"" else "ssh_255_invalid_payload"
        return transport_blocked("transport_failed", diagnostic_code)
    if type(returncode) is int and returncode in {0, 1}:
        return transport_blocked("transport_invalid", "invalid_payload_expected_returncode")
    return transport_blocked("transport_failed", "ssh_unexpected_returncode")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    payload = (
        transport_blocked("invalid_invocation", "invalid_invocation")
        if argv
        else collect_published_podman_compose_capability_observation()
    )
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
