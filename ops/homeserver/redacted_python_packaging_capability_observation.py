#!/usr/bin/env python3
"""Fixed-key, read-only Debian Python packaging capability observation."""

from __future__ import annotations

import getpass
import hashlib
import importlib.util
import json
import sys
from typing import Any, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_python_packaging_capability_observation.v1"
EXPECTED_USER = "homebase"
_CAPABILITIES = {
    "venv_module_present": "venv",
    "ensurepip_module_present": "ensurepip",
    "pip_module_present": "pip",
    "setuptools_module_present": "setuptools",
    "wheel_module_present": "wheel",
}
_STATES = frozenset({"observed", "invalid_invocation", "internal_error"})
_BOOL_KEYS = frozenset({"expected_user", *_CAPABILITIES})
_ENVELOPE_KEYS = frozenset(
    {"schema_id", "status", "state", "evidence_sha256", *_BOOL_KEYS}
)


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(
        body, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_envelope(payload: Any) -> bool:
    if type(payload) is not dict or set(payload) != _ENVELOPE_KEYS:
        return False
    if payload.get("schema_id") != SCHEMA_ID:
        return False
    if payload.get("status") != "observed":
        return False
    if type(payload.get("state")) is not str or payload["state"] not in _STATES:
        return False
    if any(type(payload.get(key)) is not bool for key in _BOOL_KEYS):
        return False
    digest = payload.get("evidence_sha256")
    return (
        type(digest) is str
        and len(digest) == 64
        and digest == _digest(payload)
    )


def _envelope(state: str, flags: Mapping[str, bool]) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": "observed",
        "state": state if state in _STATES else "internal_error",
        **{key: bool(flags.get(key, False)) for key in _BOOL_KEYS},
    }
    payload["evidence_sha256"] = _digest(payload)
    if not validate_envelope(payload):
        raise RuntimeError("fixed envelope construction failed")
    return payload


def collect_observation() -> dict[str, Any]:
    flags = {key: False for key in _BOOL_KEYS}
    try:
        flags["expected_user"] = getpass.getuser() == EXPECTED_USER
        for key, module_name in _CAPABILITIES.items():
            flags[key] = importlib.util.find_spec(module_name) is not None
    except Exception:
        return _envelope("internal_error", flags)
    return _envelope("observed", flags)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    payload = _envelope("invalid_invocation", {}) if arguments else collect_observation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if validate_envelope(payload) else 1


if __name__ == "__main__":
    raise SystemExit(main())
