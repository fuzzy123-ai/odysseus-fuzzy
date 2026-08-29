#!/usr/bin/env python3
"""Fail-closed compatibility boundary for the retired direct backup executor.

Production backup execution is owned exclusively by the separately versioned,
root-owned helper. This historical module retains only its redacted result
schema so obsolete checkouts and packets stop safely before any host action.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation.v1"
_ERROR = "legacy_executor_retired"
_KEYS = frozenset(
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
            {key: value for key, value in payload.items() if key != "evidence_sha256"},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def blocked(_code: str = _ERROR) -> dict[str, Any]:
    """Return the sole permitted direct-executor result."""
    payload: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "status": "blocked",
        "error_code": _ERROR,
        "backup_invoked": False,
        "retry_permitted": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    return bool(
        type(value) is dict
        and set(value) == _KEYS
        and value.get("schema_id") == SCHEMA_ID
        and value.get("status") == "blocked"
        and value.get("error_code") == _ERROR
        and value.get("backup_invoked") is False
        and value.get("retry_permitted") is False
        and type(value.get("evidence_sha256")) is str
        and value["evidence_sha256"] == _digest(value)
    )


def collect_predeploy_backup_creation(**_ignored: Any) -> dict[str, Any]:
    """Stop obsolete callers before configuration, process, or host access."""
    return blocked()


def main() -> int:
    payload = collect_predeploy_backup_creation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
