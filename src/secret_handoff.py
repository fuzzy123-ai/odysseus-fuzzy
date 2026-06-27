"""Pending secret handoff contract for agent-initiated setup flows.

The agent may request that a secret setting be filled, but it never receives
or persists the value. UI/admin routes complete the request with the secret
value and receive only a stored/visible=false result.
"""

from __future__ import annotations

import os
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from core.atomic_io import atomic_write_json
from src.constants import DATA_DIR
from src.settings_registry import get_registry_entry, resolve_setting_alias
from src.settings_service import SettingsServiceError, set_setting


SECRET_HANDOFFS_FILE = os.path.join(DATA_DIR, "secret_handoffs.json")
DEFAULT_TTL_SECONDS = 60 * 60
MAX_TTL_SECONDS = 24 * 60 * 60


class SecretHandoffError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "status": self.code, "error": self.message}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_records() -> list[dict[str, Any]]:
    try:
        import json

        with open(SECRET_HANDOFFS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = raw.get("requests") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _save_records(records: list[dict[str, Any]]) -> None:
    atomic_write_json(SECRET_HANDOFFS_FILE, {"requests": records}, indent=2)


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "key",
        "scope",
        "owner",
        "requested_by",
        "status",
        "created_at",
        "expires_at",
        "completed_at",
        "completed_by",
        "cancelled_at",
        "cancelled_by",
    }
    return {key: deepcopy(record.get(key)) for key in allowed if key in record}


def _expire_stale(records: list[dict[str, Any]]) -> bool:
    changed = False
    current = _now()
    for record in records:
        if record.get("status") != "pending":
            continue
        expires_at = _parse_iso(str(record.get("expires_at") or ""))
        if expires_at and expires_at <= current:
            record["status"] = "expired"
            record["expired_at"] = _iso(current)
            changed = True
    return changed


def _find_pending(records: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    for record in records:
        if record.get("id") == request_id:
            if record.get("status") != "pending":
                raise SecretHandoffError("not_pending", "Secret handoff is no longer pending.")
            return record
    raise SecretHandoffError("not_found", "Secret handoff not found.")


def create_secret_handoff(
    key: str,
    *,
    owner: str | None = None,
    scope: str = "global",
    requested_by: str = "agent",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    resolved = resolve_setting_alias(key)
    try:
        entry = get_registry_entry(resolved, source="setting")
    except KeyError as exc:
        raise SettingsServiceError("unknown_key", f"Unknown setting key '{key}'.", {"key": key}) from exc
    if not entry.secret or entry.agent_access != "secret_handoff":
        raise SettingsServiceError(
            "not_secret_handoff",
            f"{entry.key} is not a secret-handoff setting.",
            {"key": entry.key},
        )
    ttl = max(60, min(int(ttl_seconds or DEFAULT_TTL_SECONDS), MAX_TTL_SECONDS))
    created_at = _now()
    request_id = "sh_" + secrets.token_urlsafe(12).replace("-", "_")
    record = {
        "id": request_id,
        "key": entry.key,
        "scope": scope or "global",
        "owner": owner or "",
        "requested_by": requested_by or "agent",
        "status": "pending",
        "created_at": _iso(created_at),
        "expires_at": _iso(created_at + timedelta(seconds=ttl)),
    }
    records = _load_records()
    if _expire_stale(records):
        _save_records(records)
        records = _load_records()
    records.append(record)
    _save_records(records)
    result = _safe_record(record)
    result.update({
        "ok": True,
        "status": "pending",
        "value_visible": False,
        "secret_value_stored": False,
    })
    return result


def list_secret_handoffs(*, status: str | None = None) -> dict[str, Any]:
    records = _load_records()
    if _expire_stale(records):
        _save_records(records)
    wanted = (status or "").strip().lower()
    items = [
        _safe_record(record)
        for record in records
        if not wanted or str(record.get("status") or "").lower() == wanted
    ]
    return {"ok": True, "status": "listed", "count": len(items), "requests": items}


def complete_secret_handoff(request_id: str, secret_value: str, *, actor: str = "ui") -> dict[str, Any]:
    if not request_id:
        raise SecretHandoffError("missing_request_id", "Secret handoff id is required.")
    if not secret_value:
        raise SecretHandoffError("missing_secret", "Secret value is required.")
    records = _load_records()
    changed = _expire_stale(records)
    record = _find_pending(records, request_id)
    result = set_setting(
        str(record["key"]),
        secret_value,
        owner=str(record.get("owner") or "") or None,
        scope=str(record.get("scope") or "global"),
        store="setting",
        actor="ui",
        confirmed=True,
    )
    current = _now()
    record["status"] = "completed"
    record["completed_at"] = _iso(current)
    record["completed_by"] = actor or "ui"
    changed = True
    if changed:
        _save_records(records)
    safe = _safe_record(record)
    safe.update({
        "ok": True,
        "status": "completed",
        "stored": bool(result.get("ok")),
        "setting": {
            "key": result.get("key"),
            "source": result.get("source"),
            "scope": result.get("scope"),
            "effective_scope": result.get("effective_scope"),
            "value_visible": False,
        },
    })
    return safe


def cancel_secret_handoff(request_id: str, *, actor: str = "ui") -> dict[str, Any]:
    if not request_id:
        raise SecretHandoffError("missing_request_id", "Secret handoff id is required.")
    records = _load_records()
    changed = _expire_stale(records)
    record = _find_pending(records, request_id)
    current = _now()
    record["status"] = "cancelled"
    record["cancelled_at"] = _iso(current)
    record["cancelled_by"] = actor or "ui"
    changed = True
    if changed:
        _save_records(records)
    safe = _safe_record(record)
    safe.update({"ok": True, "status": "cancelled"})
    return safe
