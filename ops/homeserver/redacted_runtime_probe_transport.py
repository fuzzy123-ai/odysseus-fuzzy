#!/usr/bin/env python3
"""Bounded, presence-only diagnosis of the fixed Debian probe projection.

This module classifies only the shape of the fixed probe's JSON response. It
never returns probe values, unknown field names, subprocess text, or readiness.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


SCHEMA_ID = "odysseus.homeserver.runtime_projection_schema_diagnostic.v1"
HOST_SCHEMA_ID = "odysseus.homeserver.redacted_runtime_probe.v1"
CONTAINER_SCHEMA_ID = "odysseus.homeserver.credential_presence.v1"
SSH_COMMAND = ["ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver-probe"]
TIMEOUT_SECONDS = 20
CLEANUP_GRACE_SECONDS = 1.0
MAX_STDOUT_BYTES = 32768
MAX_COUNT = 4096
DEFAULT_CONTAINER = "odysseus_odysseus_1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_CREDENTIAL_KEYS = frozenset({
    "DATA_BRAVE_API_KEY", "EMBEDDING_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
    "NEXTCLOUD_WEBDAV_APP_PASSWORD", "ODYSSEUS_ADMIN_PASSWORD",
    "ODYSSEUS_INTERNAL_TOKEN", "OPENAI_API_KEY", "SERPER_API_KEY",
    "TAVILY_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PC_AGENT_SECRET",
})
_CREDENTIAL_BASE_KEYS = _CREDENTIAL_KEYS - {"TELEGRAM_PC_AGENT_SECRET"}
_TELEGRAM_KEYS = frozenset({
    "opaque_target_configured", "agent_reply_enabled", "send_ready",
    "raw_target_visible", "secret_values_visible",
})
_PC_KEYS = frozenset({
    "enabled", "wake_enabled", "wake_target_configured", "agent_secret_configured",
    "wake_ready", "agent_ready", "raw_target_visible", "secret_values_visible",
})
_HOST_OK_BASE = frozenset({
    "schema_id", "status", "container", "container_running",
    "environment_entry_count", "credential_presence", "unknown_sensitive_key_count",
    "raw_environment_visible", "secret_values_visible", "telegram_delivery_readiness",
})
_HOST_BLOCKED_BASE = frozenset({
    "schema_id", "status", "error_code", "raw_environment_visible",
    "secret_values_visible", "telegram_delivery_readiness",
})
_CONTAINER_BASE = frozenset({
    "schema_id", "environment_entry_count", "credential_presence",
    "unknown_sensitive_key_count", "telegram_delivery_readiness",
})
_PC_FIELD = "pc_control_readiness"
_BLOCKED_CODES = frozenset({
    "invalid_container_name", "podman_unavailable", "container_probe_timeout",
    "container_probe_failed", "invalid_probe_payload", "container_probe_internal_error",
})
_DIAGNOSTIC_CODES = (
    "transport_timeout", "transport_invocation_failed", "stdout_unavailable",
    "output_too_large", "ssh_transport_failed", "invalid_framing", "invalid_utf8",
    "invalid_json", "duplicate_json_key", "top_level_not_object",
    "schema_discriminator_missing", "schema_discriminator_type_invalid",
    "schema_discriminator_unknown", "host_exact_shape_invalid",
    "host_nested_shape_invalid", "host_leaf_type_invalid", "host_count_invalid",
    "host_invariant_invalid", "container_exact_shape_invalid",
    "container_nested_shape_invalid", "container_leaf_type_invalid",
    "container_count_invalid", "container_invariant_invalid", "returncode_mismatch",
    "host_projection_valid", "unexpected_container_projection",
)
_DIAGNOSTIC_CODE_SET = frozenset(_DIAGNOSTIC_CODES)
_SSH_EXIT_CLASSES = frozenset({"zero", "one", "two_five_five", "other", "unavailable"})
_PROJECTION_KINDS = frozenset({"host", "container", "unknown", "unavailable"})
_SHAPE_PROFILES = frozenset({
    "host_v1_base", "host_v1_pc_extended", "container_v1_base",
    "container_v1_pc_extended", "none",
})
_BOOL_FIELDS = (
    "json_valid", "top_level_object", "schema_discriminator_valid", "exact_known_key_shape",
    "nested_shape_valid", "leaf_types_valid", "bounded_counts_valid",
    "cross_field_invariants_valid", "host_projection_valid", "container_projection_valid",
    "fixed_alias_contract_valid", "runtime_readiness_verified", "credential_readiness_verified",
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible",
    "unknown_key_names_visible", "secret_values_visible",
)
_OUTPUT_KEYS = frozenset({
    "schema_id", "status", "diagnostic_code", "ssh_exit_class", "projection_kind",
    "shape_profile", *_BOOL_FIELDS,
})


def _envelope(code: str, *, ssh_exit_class: str = "unavailable", projection_kind: str = "unavailable",
              shape_profile: str = "none", flags: dict[str, bool] | None = None) -> dict[str, Any]:
    if code not in _DIAGNOSTIC_CODE_SET:
        code = "transport_invocation_failed"
    if ssh_exit_class not in _SSH_EXIT_CLASSES:
        ssh_exit_class = "unavailable"
    if projection_kind not in _PROJECTION_KINDS:
        projection_kind = "unavailable"
    if shape_profile not in _SHAPE_PROFILES:
        shape_profile = "none"
    values = {key: False for key in _BOOL_FIELDS}
    if flags:
        for key in _BOOL_FIELDS[:11]:
            if type(flags.get(key)) is bool:
                values[key] = flags[key]
    values.update({
        "schema_id": SCHEMA_ID,
        "status": "diagnosed" if code in {"host_projection_valid", "unexpected_container_projection"} else "blocked",
        "diagnostic_code": code,
        "ssh_exit_class": ssh_exit_class,
        "projection_kind": projection_kind,
        "shape_profile": shape_profile,
        "runtime_readiness_verified": False,
        "credential_readiness_verified": False,
        "raw_stdout_visible": False,
        "raw_stderr_visible": False,
        "exception_text_visible": False,
        "unknown_key_names_visible": False,
        "secret_values_visible": False,
    })
    return values


def validate_diagnostic_envelope(value: Any) -> bool:
    """Return whether value is exactly the fixed, non-sensitive envelope."""
    if type(value) is not dict or set(value) != _OUTPUT_KEYS:
        return False
    if value.get("schema_id") != SCHEMA_ID or type(value.get("schema_id")) is not str:
        return False
    status, code = value.get("status"), value.get("diagnostic_code")
    if type(status) is not str or status not in {"diagnosed", "blocked"}:
        return False
    if type(code) is not str or code not in _DIAGNOSTIC_CODE_SET:
        return False
    if status != ("diagnosed" if code in {"host_projection_valid", "unexpected_container_projection"} else "blocked"):
        return False
    for key, allowed in (("ssh_exit_class", _SSH_EXIT_CLASSES), ("projection_kind", _PROJECTION_KINDS), ("shape_profile", _SHAPE_PROFILES)):
        if type(value.get(key)) is not str or value[key] not in allowed:
            return False
    if any(type(value.get(key)) is not bool for key in _BOOL_FIELDS):
        return False
    if any(value[key] is not False for key in _BOOL_FIELDS[-7:]):
        return False
    if value["fixed_alias_contract_valid"]:
        if not (
            code == "host_projection_valid"
            and value["projection_kind"] == "host"
            and value["shape_profile"] in {"host_v1_base", "host_v1_pc_extended"}
            and value["ssh_exit_class"] in {"zero", "one"}
            and value["host_projection_valid"]
            and not value["container_projection_valid"]
            and all(value[key] for key in (
                "json_valid", "top_level_object", "schema_discriminator_valid",
                "exact_known_key_shape", "nested_shape_valid", "leaf_types_valid",
                "bounded_counts_valid", "cross_field_invariants_valid",
            ))
        ):
            return False
    if code == "host_projection_valid" and not value["fixed_alias_contract_valid"]:
        return False
    if value["host_projection_valid"] and code != "host_projection_valid":
        return False
    if value["container_projection_valid"]:
        if not (
            code == "unexpected_container_projection"
            and value["projection_kind"] == "container"
            and value["shape_profile"] in {"container_v1_base", "container_v1_pc_extended"}
            and not value["fixed_alias_contract_valid"]
            and all(value[key] for key in (
                "json_valid", "top_level_object", "schema_discriminator_valid",
                "exact_known_key_shape", "nested_shape_valid", "leaf_types_valid",
                "bounded_counts_valid", "cross_field_invariants_valid",
            ))
        ):
            return False
    if code == "unexpected_container_projection" and not value["container_projection_valid"]:
        return False
    return True


def _reserialize_diagnostic_envelope(value: Any) -> dict[str, Any] | None:
    if not validate_diagnostic_envelope(value):
        return None
    return {
        "schema_id": SCHEMA_ID,
        "status": value["status"],
        "diagnostic_code": value["diagnostic_code"],
        "ssh_exit_class": value["ssh_exit_class"],
        "projection_kind": value["projection_kind"],
        "shape_profile": value["shape_profile"],
        **{key: value[key] for key in _BOOL_FIELDS},
    }


def _ssh_exit_class(returncode: Any) -> str:
    if type(returncode) is not int:
        return "unavailable"
    return {0: "zero", 1: "one", 255: "two_five_five"}.get(returncode, "other")


def _count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNT


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


class _DuplicateKey(ValueError):
    pass


class _InvalidJSONConstant(ValueError):
    pass


def _reject_nonfinite_json_constant(_constant: str) -> Any:
    raise _InvalidJSONConstant("non_finite_json_constant")


def _parse(raw: bytes) -> tuple[Any, str | None]:
    if type(raw) is not bytes:
        return None, "stdout_unavailable"
    if len(raw) > MAX_STDOUT_BYTES:
        return None, "output_too_large"
    if not raw.endswith(b"\n") or raw[:-1].find(b"\n") >= 0 or b"\r" in raw:
        return None, "invalid_framing"
    try:
        text = raw[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, "invalid_utf8"
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except _DuplicateKey:
        return None, "duplicate_json_key"
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, "invalid_json"
    if type(value) is not dict:
        return None, "top_level_not_object"
    return value, None


def _nested(value: Any, keys: frozenset[str]) -> tuple[bool, bool]:
    if type(value) is not dict or set(value) != keys:
        return False, False
    return True, all(type(value[key]) is bool for key in keys)


def classify_runtime_probe_payload(raw: bytes, *, returncode: int) -> dict[str, Any]:
    """Classify fixed probe bytes and process status without exposing payload data."""
    if type(raw) is not bytes:
        return _envelope("stdout_unavailable", ssh_exit_class=_ssh_exit_class(returncode))
    if len(raw) > MAX_STDOUT_BYTES:
        return _envelope("output_too_large", ssh_exit_class=_ssh_exit_class(returncode))
    if type(returncode) is not int:
        return _envelope("ssh_transport_failed", ssh_exit_class="unavailable")
    if returncode not in {0, 1}:
        return _envelope("ssh_transport_failed", ssh_exit_class=_ssh_exit_class(returncode))
    payload, framing_error = _parse(raw)
    exit_class = _ssh_exit_class(returncode)
    if framing_error:
        return _envelope(framing_error, ssh_exit_class=exit_class, projection_kind="unknown")
    schema = payload.get("schema_id", object())
    if "schema_id" not in payload:
        return _envelope("schema_discriminator_missing", ssh_exit_class=exit_class, projection_kind="unknown",
                         flags={"json_valid": True, "top_level_object": True})
    if type(schema) is not str:
        return _envelope("schema_discriminator_type_invalid", ssh_exit_class=exit_class, projection_kind="unknown",
                         flags={"json_valid": True, "top_level_object": True})
    if schema not in {HOST_SCHEMA_ID, CONTAINER_SCHEMA_ID}:
        return _envelope("schema_discriminator_unknown", ssh_exit_class=exit_class, projection_kind="unknown",
                         flags={"json_valid": True, "top_level_object": True})

    kind = "host" if schema == HOST_SCHEMA_ID else "container"
    status = payload.get("status") if kind == "host" else "ok"
    pc_extended = _PC_FIELD in payload
    profile = f"{kind}_v1_{'pc_extended' if pc_extended else 'base'}"
    base = (_HOST_OK_BASE if status == "ok" else _HOST_BLOCKED_BASE) if kind == "host" else _CONTAINER_BASE
    expected = base | ({_PC_FIELD} if pc_extended else set())
    flags: dict[str, bool] = {
        "json_valid": True, "top_level_object": True,
        "schema_discriminator_valid": True,
        "exact_known_key_shape": set(payload) == expected,
    }
    if not flags["exact_known_key_shape"]:
        return _envelope(f"{kind}_exact_shape_invalid", ssh_exit_class=exit_class, projection_kind=kind,
                         shape_profile=profile, flags=flags)

    telegram_shape, telegram_types = _nested(payload.get("telegram_delivery_readiness"), _TELEGRAM_KEYS)
    pc_shape, pc_types = (True, True)
    if pc_extended:
        pc_shape, pc_types = _nested(payload.get(_PC_FIELD), _PC_KEYS)
    presence = payload.get("credential_presence") if status == "ok" or kind == "container" else None
    expected_presence = _CREDENTIAL_KEYS if pc_extended else _CREDENTIAL_BASE_KEYS
    presence_shape = (type(presence) is dict and set(presence) == expected_presence) if presence is not None else True
    nested_shape = telegram_shape and pc_shape and presence_shape
    flags["nested_shape_valid"] = nested_shape
    if not nested_shape:
        return _envelope(f"{kind}_nested_shape_invalid", ssh_exit_class=exit_class, projection_kind=kind,
                         shape_profile=profile, flags=flags)

    leaf_types = telegram_types and pc_types
    if presence is not None:
        leaf_types = leaf_types and all(type(presence[key]) is bool for key in expected_presence)
    visibility_objects = [payload["telegram_delivery_readiness"]]
    if pc_extended:
        visibility_objects.append(payload[_PC_FIELD])
    visibility_objects.extend([payload])
    for obj in visibility_objects:
        for key in ("raw_environment_visible", "secret_values_visible", "raw_target_visible"):
            if key in obj and type(obj[key]) is not bool:
                leaf_types = False
    if kind == "host":
        leaf_types = leaf_types and type(payload.get("status")) is str
        if status == "ok":
            leaf_types = leaf_types and type(payload.get("container")) is str and type(payload.get("container_running")) is bool
            leaf_types = leaf_types and type(payload.get("raw_environment_visible")) is bool and type(payload.get("secret_values_visible")) is bool
        else:
            leaf_types = leaf_types and type(payload.get("error_code")) is str
            leaf_types = leaf_types and type(payload.get("raw_environment_visible")) is bool and type(payload.get("secret_values_visible")) is bool
    flags["leaf_types_valid"] = leaf_types
    if not leaf_types:
        return _envelope(f"{kind}_leaf_type_invalid", ssh_exit_class=exit_class, projection_kind=kind,
                         shape_profile=profile, flags=flags)

    bounded = True
    if status == "ok" or kind == "container":
        bounded = _count(payload.get("environment_entry_count")) and _count(payload.get("unknown_sensitive_key_count"))
    flags["bounded_counts_valid"] = bounded
    if not bounded:
        return _envelope(f"{kind}_count_invalid", ssh_exit_class=exit_class, projection_kind=kind,
                         shape_profile=profile, flags=flags)

    invariant = True
    if kind == "host":
        invariant = payload["raw_environment_visible"] is False and payload["secret_values_visible"] is False
        invariant = invariant and payload["telegram_delivery_readiness"]["raw_target_visible"] is False and payload["telegram_delivery_readiness"]["secret_values_visible"] is False
        if status == "ok":
            invariant = invariant and payload["container"] == DEFAULT_CONTAINER and payload["container_running"] is True
            invariant = invariant and payload["unknown_sensitive_key_count"] <= payload["environment_entry_count"]
            invariant = invariant and payload["telegram_delivery_readiness"]["send_ready"] == (
                payload["telegram_delivery_readiness"]["opaque_target_configured"]
                and payload["telegram_delivery_readiness"]["agent_reply_enabled"]
                and payload["credential_presence"]["TELEGRAM_BOT_TOKEN"]
            )
        else:
            invariant = invariant and payload["status"] == "blocked" and payload["error_code"] in _BLOCKED_CODES
            invariant = invariant and all(value is False for value in payload["telegram_delivery_readiness"].values())
        if pc_extended:
            pc = payload[_PC_FIELD]
            invariant = invariant and pc["raw_target_visible"] is False and pc["secret_values_visible"] is False
            if status == "ok":
                invariant = invariant and pc["agent_secret_configured"] == payload["credential_presence"]["TELEGRAM_PC_AGENT_SECRET"]
                invariant = invariant and pc["wake_ready"] == (pc["enabled"] and pc["wake_enabled"] and pc["wake_target_configured"])
                invariant = invariant and pc["agent_ready"] == (pc["enabled"] and pc["agent_secret_configured"])
            else:
                invariant = invariant and all(value is False for value in pc.values())
    else:
        invariant = payload["unknown_sensitive_key_count"] <= payload["environment_entry_count"]
        invariant = invariant and payload["telegram_delivery_readiness"]["raw_target_visible"] is False and payload["telegram_delivery_readiness"]["secret_values_visible"] is False
        invariant = invariant and payload["telegram_delivery_readiness"]["send_ready"] == (
            payload["telegram_delivery_readiness"]["opaque_target_configured"]
            and payload["telegram_delivery_readiness"]["agent_reply_enabled"]
            and payload["credential_presence"]["TELEGRAM_BOT_TOKEN"]
        )
        if pc_extended:
            pc = payload[_PC_FIELD]
            invariant = invariant and pc["raw_target_visible"] is False and pc["secret_values_visible"] is False
            invariant = invariant and pc["agent_secret_configured"] == payload["credential_presence"]["TELEGRAM_PC_AGENT_SECRET"]
            invariant = invariant and pc["wake_ready"] == (pc["enabled"] and pc["wake_enabled"] and pc["wake_target_configured"])
            invariant = invariant and pc["agent_ready"] == (pc["enabled"] and pc["agent_secret_configured"])
    flags["cross_field_invariants_valid"] = invariant
    if not invariant:
        return _envelope(f"{kind}_invariant_invalid", ssh_exit_class=exit_class, projection_kind=kind,
                         shape_profile=profile, flags=flags)

    if kind == "container":
        flags["container_projection_valid"] = True
        return _envelope("unexpected_container_projection", ssh_exit_class=exit_class,
                         projection_kind=kind, shape_profile=profile, flags=flags)
    expected_rc = 0 if status == "ok" else 1
    if returncode != expected_rc:
        return _envelope("returncode_mismatch", ssh_exit_class=exit_class, projection_kind=kind,
                         shape_profile=profile, flags=flags)
    flags["host_projection_valid"] = True
    flags["fixed_alias_contract_valid"] = True
    return _envelope("host_projection_valid", ssh_exit_class=exit_class, projection_kind=kind,
                     shape_profile=profile, flags=flags)


class _BoundedResult:
    def __init__(self, stdout: bytes, returncode: int, *, timed_out: bool = False, too_large: bool = False):
        self.stdout = stdout
        self.returncode = returncode
        self.timed_out = timed_out
        self.too_large = too_large


class _BoundedProcessFailure(RuntimeError):
    """Fixed internal failure with only a literal child-reaped indicator."""

    def __init__(self, *, child_reaped: bool) -> None:
        super().__init__("bounded_process_failed")
        self.child_reaped = child_reaped


def _pipe_available(stream: Any) -> int | None:
    """Return immediately available bytes, or None when the pipe reached EOF."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel32.PeekNamedPipe
        peek.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                         wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
        peek.restype = wintypes.BOOL
        ok = peek(wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno())), None, 0,
                  None, ctypes.byref(available), None)
        if not ok:
            error = ctypes.get_last_error()
            if error == 109:  # ERROR_BROKEN_PIPE
                return None
            raise OSError(error, "pipe_read_failed")
        return int(available.value)
    ready, _, _ = select.select([stream], [], [], 0)
    return 1 if ready else 0


def _bounded_process(argv: list[str], timeout: int) -> _BoundedResult:
    """Capture a bounded child result without a background reader thread."""
    started_at = time.monotonic()
    deadline = started_at + timeout
    stream: Any = None
    chunks = bytearray()
    too_large = False
    timed_out = False
    operation_failed = False
    final_returncode: int | None = None
    child_reaped = False
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        shell=False, close_fds=True, cwd=str(REPOSITORY_ROOT), bufsize=0,
    )
    # Process ownership begins as soon as Popen returns. Keep every operation
    # that can inspect the handle or touch its pipe inside this cleanup scope.
    try:
        try:
            stream = proc.stdout
        except Exception:
            operation_failed = True
            # Popen stores its already-created pipe on the handle. If a test
            # double or wrapper faults on the public getter, recover only that
            # local handle reference so the acquired pipe can still be closed.
            try:
                attributes = object.__getattribute__(proc, "__dict__")
                stream = attributes.get("stdout", attributes.get("_stdout"))
            except Exception:
                operation_failed = True
        if not operation_failed:
            while True:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if stream is None:
                    break
                available = _pipe_available(stream)
                if available is None:
                    break
                if type(available) is not int or available < 0:
                    operation_failed = True
                    break
                if available:
                    allowance = MAX_STDOUT_BYTES + 1 - len(chunks)
                    if allowance <= 0:
                        too_large = True
                        break
                    read_size = min(available, allowance, 4096) if os.name == "nt" else min(allowance, 4096)
                    chunk = os.read(stream.fileno(), read_size)
                    if not chunk:
                        break
                    chunks.extend(chunk)
                    if len(chunks) > MAX_STDOUT_BYTES:
                        too_large = True
                        break
                    continue
                time.sleep(0.01)
        if not operation_failed and not timed_out and not too_large:
            try:
                waited_returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                if type(waited_returncode) is not int:
                    operation_failed = True
                else:
                    child_reaped = True
            except subprocess.TimeoutExpired:
                timed_out = True
            except Exception:
                operation_failed = True
    except Exception:
        # Do not retain exception text, traceback, or input objects. Cleanup
        # below still owns the process and any pipe already acquired.
        operation_failed = True
    finally:
        cleanup_deadline = deadline + CLEANUP_GRACE_SECONDS
        try:
            current = proc.poll()
            if current is not None and type(current) is not int:
                operation_failed = True
            if type(current) is int:
                child_reaped = True
        except Exception:
            operation_failed = True
        if not child_reaped:
            try:
                proc.kill()
            except Exception:
                operation_failed = True
            try:
                remaining = max(0.0, cleanup_deadline - time.monotonic())
                waited_returncode = proc.wait(timeout=remaining)
                if type(waited_returncode) is not int:
                    operation_failed = True
                else:
                    child_reaped = True
            except Exception:
                operation_failed = True
        if stream is not None:
            try:
                stream.close()
            except Exception:
                operation_failed = True
        try:
            final_returncode = proc.poll()
            if final_returncode is not None and type(final_returncode) is not int:
                operation_failed = True
        except Exception:
            operation_failed = True
        if type(final_returncode) is int:
            child_reaped = True
    if operation_failed or type(final_returncode) is not int:
        raise _BoundedProcessFailure(child_reaped=child_reaped) from None
    return _BoundedResult(bytes(chunks), final_returncode, timed_out=timed_out, too_large=too_large)


def _bounded_local_runner(argv: list[str], *, stdout: Any, stderr: Any, stdin: Any,
                          timeout: int, shell: bool, cwd: str) -> _BoundedResult:
    if (argv != SSH_COMMAND or stdout is not subprocess.PIPE or stderr is not subprocess.DEVNULL
            or stdin is not subprocess.DEVNULL or timeout != TIMEOUT_SECONDS or shell is not False
            or cwd != str(REPOSITORY_ROOT)):
        raise ValueError("fixed_invocation_required")
    return _bounded_process(argv, timeout)


def collect_runtime_probe_schema_diagnostic(*, runner: Callable[..., Any] = _bounded_local_runner) -> dict[str, Any]:
    try:
        result = runner(SSH_COMMAND.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, shell=False,
                        cwd=str(REPOSITORY_ROOT))
    except _BoundedProcessFailure:
        return _envelope("transport_invocation_failed")
    except subprocess.TimeoutExpired:
        return _envelope("transport_timeout")
    except Exception:
        return _envelope("transport_invocation_failed")
    try:
        timed_out = getattr(result, "timed_out", False)
        too_large = getattr(result, "too_large", False)
        raw = getattr(result, "stdout", None)
        rc = getattr(result, "returncode", None)
    except Exception:
        return _envelope("transport_invocation_failed")
    if type(timed_out) is not bool or type(too_large) is not bool:
        return _envelope("transport_invocation_failed")
    if timed_out:
        return _envelope("transport_timeout")
    if type(rc) is not int:
        return _envelope("transport_invocation_failed")
    if too_large:
        return _envelope("output_too_large", ssh_exit_class=_ssh_exit_class(rc))
    if type(raw) is not bytes:
        return _envelope("stdout_unavailable", ssh_exit_class=_ssh_exit_class(rc))
    if len(raw) > MAX_STDOUT_BYTES:
        return _envelope("output_too_large", ssh_exit_class=_ssh_exit_class(rc))
    # A nonzero SSH status takes precedence over response interpretation, while
    # retaining only its fixed exit class.
    if rc not in {0, 1}:
        return _envelope("ssh_transport_failed", ssh_exit_class=_ssh_exit_class(rc))
    return classify_runtime_probe_payload(raw, returncode=rc)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    result = _envelope("transport_invocation_failed") if args else collect_runtime_probe_schema_diagnostic()
    validated = _reserialize_diagnostic_envelope(result)
    if validated is None:
        validated = _envelope("transport_invocation_failed")
    print(json.dumps(validated, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if validated["status"] == "diagnosed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
