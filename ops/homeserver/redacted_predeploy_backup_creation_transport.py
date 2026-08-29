#!/usr/bin/env python3
"""Fail-closed compatibility boundary for the retired direct backup executor.

Normal application revisions must never turn into backup-capable server code.
The only production executor is the separately versioned installed root helper
reached through ``redacted_predeploy_backup_root_helper_action_transport``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation_transport.v1"
_CODES = frozenset(
    {
        "invalid_invocation",
        "legacy_executor_retired",
    }
)
_BLOCKED_KEYS = frozenset(
    {
        "schema_id",
        "status",
        "error_code",
        "backup_invoked",
        "retry_permitted",
        "evidence_sha256",
    }
)


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in payload.items()
                if key != "evidence_sha256"
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": "blocked",
        "error_code": code if code in _CODES else "transport_invalid",
        "backup_invoked": False,
        "retry_permitted": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_transport_envelope(value: Any) -> bool:
    return bool(
        type(value) is dict
        and value.get("schema_id") == SCHEMA_ID
        and value.get("retry_permitted") is False
        and type(value.get("evidence_sha256")) is str
        and value["evidence_sha256"] == _digest(value)
        and (
            (
                set(value) == _BLOCKED_KEYS
                and value.get("status") == "blocked"
                and value.get("error_code") in _CODES
                and value.get("backup_invoked") is False
            )
        )
    )


def collect_published_predeploy_backup_creation(
    *,
    execute: bool = False,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if execute is not True:
        return blocked("invalid_invocation")
    # `runner` remains an unused compatibility parameter so callers cannot
    # accidentally reintroduce an SSH capability by changing a default.
    _ = runner
    return blocked("legacy_executor_retired")


def main() -> int:
    payload = blocked("invalid_invocation")
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
