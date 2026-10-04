#!/usr/bin/env python3
"""Emit a fixed, presence-only facts envelope from the Debian probe."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
FACTS_SCHEMA_ID = "odysseus.homeserver.runtime_facts.v1"
HOST_SCHEMA_ID = "odysseus.homeserver.redacted_runtime_probe.v1"
TIMEOUT_SECONDS = 20
MAX_STDOUT_BYTES = 32768
MAX_COUNT = 4096

_BASE_CREDENTIAL_KEYS = (
    "DATA_BRAVE_API_KEY", "EMBEDDING_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
    "NEXTCLOUD_WEBDAV_APP_PASSWORD", "ODYSSEUS_ADMIN_PASSWORD",
    "ODYSSEUS_INTERNAL_TOKEN", "OPENAI_API_KEY", "SERPER_API_KEY",
    "TAVILY_API_KEY", "TELEGRAM_BOT_TOKEN",
)
_PC_CREDENTIAL_KEY = "TELEGRAM_PC_AGENT_SECRET"
_TELEGRAM_KEYS = ("opaque_target_configured", "agent_reply_enabled", "send_ready")
_PC_KEYS = ("enabled", "wake_enabled", "wake_target_configured", "agent_secret_configured", "wake_ready", "agent_ready")
_COMMON_FACT_KEYS = frozenset({
    "schema_id", "status", "error_code", "credential_presence",
    "environment_entry_count", "unknown_sensitive_key_count", "container_running",
    "telegram_delivery_readiness",
    "credential_validity_verified", "runtime_readiness_verified", "external_effects_verified",
})


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("module_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_transport = _load("redacted_runtime_probe_transport_facts", ROOT / "ops" / "homeserver" / "redacted_runtime_probe_transport.py")


def _blocked(error_code: str) -> dict[str, Any]:
    result = {
        "schema_id": FACTS_SCHEMA_ID,
        "status": "blocked",
        "error_code": error_code if isinstance(error_code, str) and error_code in _ERROR_CODES else "facts_unavailable",
        "credential_validity_verified": False,
        "runtime_readiness_verified": False,
        "external_effects_verified": False,
    }
    return result


_ERROR_CODES = frozenset({
    "facts_unavailable", "transport_invocation_failed", "transport_timeout",
    "output_too_large", "stdout_unavailable", "ssh_transport_failed",
    "invalid_probe_payload", "invalid_facts_shape", "host_projection_blocked",
})


def _bools(value: Any, keys: tuple[str, ...]) -> dict[str, bool] | None:
    if type(value) is not dict or set(value) != set(keys):
        return None
    if any(type(value[key]) is not bool for key in keys):
        return None
    return {key: value[key] for key in keys}


def _count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNT


def _facts_from_payload(payload: Any) -> dict[str, Any] | None:
    if type(payload) is not dict or payload.get("schema_id") != HOST_SCHEMA_ID or payload.get("status") != "ok":
        return None
    pc_extended = "pc_control_readiness" in payload
    expected = {
        "schema_id", "status", "container", "container_running",
        "environment_entry_count", "credential_presence", "unknown_sensitive_key_count",
        "raw_environment_visible", "secret_values_visible", "telegram_delivery_readiness",
    }
    if pc_extended:
        expected.add("pc_control_readiness")
    if set(payload) != expected:
        return None
    if type(payload.get("container")) is not str or type(payload.get("container_running")) is not bool or payload["container_running"] is not True:
        return None
    if type(payload.get("raw_environment_visible")) is not bool or type(payload.get("secret_values_visible")) is not bool:
        return None
    if payload["raw_environment_visible"] or payload["secret_values_visible"]:
        return None
    if not _count(payload.get("environment_entry_count")) or not _count(payload.get("unknown_sensitive_key_count")):
        return None
    presence_keys = _BASE_CREDENTIAL_KEYS + ((_PC_CREDENTIAL_KEY,) if pc_extended else ())
    presence = _bools(payload.get("credential_presence"), presence_keys)
    telegram = _bools(payload.get("telegram_delivery_readiness"), _TELEGRAM_KEYS + ("raw_target_visible", "secret_values_visible"))
    pc = _bools(payload.get("pc_control_readiness"), _PC_KEYS + ("raw_target_visible", "secret_values_visible")) if pc_extended else None
    if presence is None or telegram is None or (pc_extended and pc is None):
        return None
    if telegram["raw_target_visible"] or telegram["secret_values_visible"]:
        return None
    if telegram["send_ready"] != (telegram["opaque_target_configured"] and telegram["agent_reply_enabled"] and presence["TELEGRAM_BOT_TOKEN"]):
        return None
    if pc_extended:
        assert pc is not None
        if pc["raw_target_visible"] or pc["secret_values_visible"] or pc["agent_secret_configured"] != presence[_PC_CREDENTIAL_KEY]:
            return None
        if pc["wake_ready"] != (pc["enabled"] and pc["wake_enabled"] and pc["wake_target_configured"]):
            return None
        if pc["agent_ready"] != (pc["enabled"] and pc["agent_secret_configured"]):
            return None
    result = {
        "schema_id": FACTS_SCHEMA_ID,
        "status": "ok",
        "credential_presence": presence,
        "environment_entry_count": payload["environment_entry_count"],
        "unknown_sensitive_key_count": payload["unknown_sensitive_key_count"],
        "container_running": True,
        "telegram_delivery_readiness": {key: telegram[key] for key in _TELEGRAM_KEYS},
        "credential_validity_verified": False,
        "runtime_readiness_verified": False,
        "external_effects_verified": False,
    }
    if pc is not None:
        result["pc_control_readiness"] = {key: pc[key] for key in _PC_KEYS}
    return result


def _validate_facts(value: Any) -> bool:
    if type(value) is not dict or set(value) < {"schema_id", "status"} or value.get("schema_id") != FACTS_SCHEMA_ID or type(value.get("status")) is not str:
        return False
    if value.get("status") == "blocked":
        return (set(value) == {"schema_id", "status", "error_code", "credential_validity_verified", "runtime_readiness_verified", "external_effects_verified"}
                and type(value.get("error_code")) is str and value["error_code"] in _ERROR_CODES
                and all(type(value[key]) is bool and value[key] is False for key in ("credential_validity_verified", "runtime_readiness_verified", "external_effects_verified")))
    if value.get("status") != "ok":
        return False
    required = {"schema_id", "status", "credential_presence", "environment_entry_count", "unknown_sensitive_key_count", "container_running", "telegram_delivery_readiness", "credential_validity_verified", "runtime_readiness_verified", "external_effects_verified"}
    if not required.issubset(value) or set(value) - required not in ({"pc_control_readiness"}, set()):
        return False
    if any(type(value[key]) is not bool or value[key] is not False for key in ("credential_validity_verified", "runtime_readiness_verified", "external_effects_verified")) or value["container_running"] is not True:
        return False
    expected_presence = _BASE_CREDENTIAL_KEYS + ((_PC_CREDENTIAL_KEY,) if "pc_control_readiness" in value else ())
    if (_bools(value.get("credential_presence"), expected_presence) is None
            or not _count(value.get("environment_entry_count"))
            or not _count(value.get("unknown_sensitive_key_count"))
            or value["unknown_sensitive_key_count"] > value["environment_entry_count"]):
        return False
    telegram = _bools(value.get("telegram_delivery_readiness"), _TELEGRAM_KEYS)
    if telegram is None or telegram["send_ready"] != (telegram["opaque_target_configured"] and telegram["agent_reply_enabled"] and value["credential_presence"]["TELEGRAM_BOT_TOKEN"]):
        return False
    if "pc_control_readiness" in value:
        pc = _bools(value.get("pc_control_readiness"), _PC_KEYS)
        if pc is None or pc["wake_ready"] != (pc["enabled"] and pc["wake_enabled"] and pc["wake_target_configured"]) or pc["agent_ready"] != (pc["enabled"] and pc["agent_secret_configured"]) or pc["agent_secret_configured"] != value["credential_presence"][_PC_CREDENTIAL_KEY]:
            return False
    return True


def validate_facts(value: Any) -> bool:
    """Validate without allowing hostile objects to escape the redaction boundary."""
    try:
        if type(value) is not dict:
            return False
        schema_id = value.get("schema_id")
        if type(schema_id) is not str or schema_id != FACTS_SCHEMA_ID:
            return False
        status = value.get("status")
        if type(status) is not str:
            return False
        return _validate_facts(value)
    except Exception:
        return False


def collect_runtime_facts(*, runner: Callable[..., Any] = _transport._bounded_local_runner) -> dict[str, Any]:
    try:
        result = runner(_transport.SSH_COMMAND.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, shell=False, cwd=str(ROOT))
        timed_out = getattr(result, "timed_out")
        too_large = getattr(result, "too_large")
        raw = getattr(result, "stdout")
        returncode = getattr(result, "returncode")
    except Exception:
        return _blocked("transport_invocation_failed")
    if type(timed_out) is not bool or type(too_large) is not bool or type(raw) is not bytes or type(returncode) is not int:
        return _blocked("transport_invocation_failed")
    if timed_out:
        return _blocked("transport_timeout")
    if too_large or len(raw) > MAX_STDOUT_BYTES:
        return _blocked("output_too_large")
    try:
        diagnostic = _transport.classify_runtime_probe_payload(raw, returncode=returncode)
    except Exception:
        return _blocked("transport_invocation_failed")
    if not (type(diagnostic) is dict and diagnostic.get("diagnostic_code") == "host_projection_valid" and diagnostic.get("ssh_exit_class") in {"zero", "one"}):
        return _blocked("host_projection_blocked")
    try:
        payload, error = _transport._parse(raw)
    except Exception:
        return _blocked("invalid_probe_payload")
    if error is not None:
        return _blocked("invalid_probe_payload")
    facts = _facts_from_payload(payload)
    return facts if facts is not None else _blocked("invalid_facts_shape")


def _reserialize(value: Any) -> dict[str, Any]:
    if not validate_facts(value):
        return _blocked("facts_unavailable")
    if value["status"] == "blocked":
        return {"schema_id": FACTS_SCHEMA_ID, "status": "blocked", "error_code": value["error_code"], "credential_validity_verified": False, "runtime_readiness_verified": False, "external_effects_verified": False}
    output = {
        "schema_id": FACTS_SCHEMA_ID, "status": "ok",
        "credential_presence": {key: value["credential_presence"][key] for key in (_BASE_CREDENTIAL_KEYS + ((_PC_CREDENTIAL_KEY,) if "pc_control_readiness" in value else ()))},
        "environment_entry_count": value["environment_entry_count"], "unknown_sensitive_key_count": value["unknown_sensitive_key_count"],
        "container_running": True,
        "telegram_delivery_readiness": {key: value["telegram_delivery_readiness"][key] for key in _TELEGRAM_KEYS},
        "credential_validity_verified": False, "runtime_readiness_verified": False, "external_effects_verified": False,
    }
    if "pc_control_readiness" in value:
        output["pc_control_readiness"] = {key: value["pc_control_readiness"][key] for key in _PC_KEYS}
    return output


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    result = _blocked("transport_invocation_failed") if args else collect_runtime_facts()
    output = _reserialize(result)
    print(json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if output["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
