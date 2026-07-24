"""Crash-recoverable, default-off daily Telegram session rollover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import threading
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TELEGRAM_ROLLOVER_SCHEMA = "odysseus.telegram_session_rollover.v1"
DEFAULT_ROLLOVER_TIMEZONE = "Europe/Berlin"
DEFAULT_ROLLOVER_HOUR = 4

_ACTIVE_TURNS_LOCK = threading.Lock()
_ACTIVE_TURNS: dict[str, int] = {}


@dataclass(frozen=True)
class TelegramRolloverConfig:
    enabled: bool = False
    timezone_name: str = DEFAULT_ROLLOVER_TIMEZONE
    boundary_hour: int = DEFAULT_ROLLOVER_HOUR
    error: str = ""

    @classmethod
    def from_environment(cls) -> "TelegramRolloverConfig":
        enabled = str(os.getenv("TELEGRAM_SESSION_ROLLOVER_ENABLED") or "").strip().lower()
        is_enabled = enabled in {"1", "true", "yes", "on"}
        timezone_name = str(
            os.getenv("TELEGRAM_SESSION_ROLLOVER_TIMEZONE") or DEFAULT_ROLLOVER_TIMEZONE
        ).strip()
        raw_hour = str(os.getenv("TELEGRAM_SESSION_ROLLOVER_HOUR") or DEFAULT_ROLLOVER_HOUR)
        try:
            boundary_hour = int(raw_hour)
            if boundary_hour < 0 or boundary_hour > 23:
                raise ValueError
            ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError):
            return cls(
                enabled=False,
                timezone_name=timezone_name,
                boundary_hour=DEFAULT_ROLLOVER_HOUR,
                error="invalid_rollover_configuration",
            )
        return cls(
            enabled=is_enabled,
            timezone_name=timezone_name,
            boundary_hour=boundary_hour,
        )

    def local_rollover_day(self, now: datetime | None = None) -> date:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("rollover clock must be timezone-aware")
        local = current.astimezone(ZoneInfo(self.timezone_name))
        return (local - timedelta(hours=self.boundary_hour)).date()


def deterministic_rollover_session_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(
        b"odysseus.telegram-rollover-session.v1\0" + idempotency_key.encode("utf-8")
    ).hexdigest()[:14]
    return f"tg-{digest}"


def begin_telegram_turn(data_dir: str | Path, chat_id: str, scope: str) -> bool:
    key = _turn_key(data_dir, chat_id, scope)
    with _ACTIVE_TURNS_LOCK:
        was_idle = _ACTIVE_TURNS.get(key, 0) == 0
        _ACTIVE_TURNS[key] = _ACTIVE_TURNS.get(key, 0) + 1
        return was_idle


def end_telegram_turn(data_dir: str | Path, chat_id: str, scope: str) -> None:
    key = _turn_key(data_dir, chat_id, scope)
    with _ACTIVE_TURNS_LOCK:
        remaining = _ACTIVE_TURNS.get(key, 0) - 1
        if remaining > 0:
            _ACTIVE_TURNS[key] = remaining
        else:
            _ACTIVE_TURNS.pop(key, None)


def telegram_turn_active(data_dir: str | Path, chat_id: str, scope: str) -> bool:
    with _ACTIVE_TURNS_LOCK:
        return _ACTIVE_TURNS.get(_turn_key(data_dir, chat_id, scope), 0) > 0


def execute_telegram_session_rollover(
    *,
    store: Any,
    chat_id: str,
    scope: str,
    creator: Callable[..., Any] | None,
    archiver: Callable[[str], Any] | None = None,
    config: TelegramRolloverConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Roll one chat/scope at most once for the configured local day."""

    policy = config or TelegramRolloverConfig.from_environment()
    selected_scope = _normalize_scope(scope)
    if policy.error:
        return _result("invalid_config", selected_scope, reason=policy.error)
    if not policy.enabled:
        return _result("disabled", selected_scope)
    target_day = policy.local_rollover_day(now).isoformat()
    if not callable(creator):
        return _result("blocked", selected_scope, rollover_day=target_day, reason="creator_missing")

    with store.exclusive():
        mapping = store.get(chat_id)
        slot_key = f"{selected_scope}_session_id"
        day_key = f"{selected_scope}_rollover_day"
        previous_session_id = str((mapping or {}).get(slot_key) or "").strip()
        if not mapping or not previous_session_id:
            return _result("not_bound", selected_scope, rollover_day=target_day)

        stored_day = str(mapping.get(day_key) or "").strip()
        rollovers = dict(mapping.get("rollovers") or {})
        existing_rollover = dict(rollovers.get(selected_scope) or {})
        if not stored_day:
            mapping[day_key] = target_day
            store.replace_mapping(chat_id, mapping)
            return _result(
                "initialized",
                selected_scope,
                rollover_day=target_day,
                session_id=previous_session_id,
            )
        if stored_day == target_day:
            archive_status = _retry_archive_if_needed(
                store=store,
                chat_id=chat_id,
                mapping=mapping,
                scope=selected_scope,
                archiver=archiver,
            )
            return _result(
                "already_current",
                selected_scope,
                rollover_day=target_day,
                session_id=previous_session_id,
                archive_status=archive_status,
            )
        if telegram_turn_active(store.data_dir, chat_id, selected_scope):
            return _result(
                "deferred_active_turn",
                selected_scope,
                rollover_day=target_day,
                session_id=previous_session_id,
            )

        chat_handle = str(mapping.get("chat_handle") or "")
        idempotency_key = _rollover_idempotency_key(chat_handle, selected_scope, target_day)
        expected_session_id = deterministic_rollover_session_id(idempotency_key)
        pending = {
            "schema": TELEGRAM_ROLLOVER_SCHEMA,
            "status": "preparing",
            "rollover_day": target_day,
            "idempotency_key": idempotency_key,
            "previous_session_id": previous_session_id,
            "new_session_id": expected_session_id,
            "archive_status": "not_started",
            "continuity_consumed": False,
        }
        if existing_rollover.get("idempotency_key") == idempotency_key:
            pending.update(existing_rollover)
            pending["status"] = "recovering"
        rollovers[selected_scope] = pending
        mapping["rollovers"] = rollovers
        store.replace_mapping(chat_id, mapping)

        try:
            created = creator(
                chat_id=str(chat_id),
                session_alias=str(mapping.get("session_alias") or ""),
                recommended_session_name=str(
                    mapping.get("recommended_session_name") or "Telegram Bot"
                ),
                session_scope=selected_scope,
                local_only_required=selected_scope == "secure",
                rollover_idempotency_key=idempotency_key,
                rollover_session_id=expected_session_id,
                rollover_day=target_day,
                previous_session_id=previous_session_id,
            )
            new_session_id = _created_session_id(created)
        except Exception:
            new_session_id = ""
        if new_session_id != expected_session_id:
            pending["status"] = "create_failed"
            pending["failure_reason"] = (
                "deterministic_session_id_mismatch" if new_session_id else "session_create_failed"
            )
            rollovers[selected_scope] = pending
            mapping["rollovers"] = rollovers
            store.replace_mapping(chat_id, mapping)
            return _result(
                "create_failed",
                selected_scope,
                rollover_day=target_day,
                session_id=previous_session_id,
                reason=pending["failure_reason"],
            )

        mapping[slot_key] = new_session_id
        mapping[day_key] = target_day
        if str(mapping.get("last_selected_scope") or "normal") == selected_scope:
            mapping["active_session_id"] = new_session_id
            mapping["session_id"] = new_session_id
        pending.update(
            {
                "status": "bound",
                "new_session_id": new_session_id,
                "archive_status": "pending",
                "continuity_consumed": False,
            }
        )
        pending.pop("failure_reason", None)
        rollovers[selected_scope] = pending
        mapping["rollovers"] = rollovers
        mapping = store.replace_mapping(chat_id, mapping)

        archive_status = _retry_archive_if_needed(
            store=store,
            chat_id=chat_id,
            mapping=mapping,
            scope=selected_scope,
            archiver=archiver,
        )
        return _result(
            "rolled_over" if archive_status == "archived" else "rolled_over_archive_pending",
            selected_scope,
            rollover_day=target_day,
            session_id=new_session_id,
            previous_session_id=previous_session_id,
            archive_status=archive_status,
        )


def continuity_binding(store: Any, chat_id: str, scope: str) -> dict[str, Any] | None:
    selected_scope = _normalize_scope(scope)
    mapping = store.get(chat_id)
    if not mapping:
        return None
    rollover = dict((mapping.get("rollovers") or {}).get(selected_scope) or {})
    if rollover.get("status") != "bound" or rollover.get("continuity_consumed") is True:
        return None
    current_session_id = str(mapping.get(f"{selected_scope}_session_id") or "")
    if current_session_id != str(rollover.get("new_session_id") or ""):
        return None
    previous_session_id = str(rollover.get("previous_session_id") or "")
    if not previous_session_id or previous_session_id == current_session_id:
        return None
    return {
        "schema": TELEGRAM_ROLLOVER_SCHEMA,
        "previous_session_id": previous_session_id,
        "rollover_day": str(rollover.get("rollover_day") or ""),
        "trusted": False,
    }


def consume_continuity(store: Any, chat_id: str, scope: str) -> bool:
    selected_scope = _normalize_scope(scope)
    with store.exclusive():
        mapping = store.get(chat_id)
        if not mapping:
            return False
        rollovers = dict(mapping.get("rollovers") or {})
        rollover = dict(rollovers.get(selected_scope) or {})
        if not rollover or rollover.get("continuity_consumed") is True:
            return False
        rollover["continuity_consumed"] = True
        rollovers[selected_scope] = rollover
        mapping["rollovers"] = rollovers
        store.replace_mapping(chat_id, mapping)
        return True


def _retry_archive_if_needed(
    *,
    store: Any,
    chat_id: str,
    mapping: Mapping[str, Any],
    scope: str,
    archiver: Callable[[str], Any] | None,
) -> str:
    rollovers = dict(mapping.get("rollovers") or {})
    rollover = dict(rollovers.get(scope) or {})
    status = str(rollover.get("archive_status") or "not_applicable")
    if rollover.get("status") != "bound" or status == "archived":
        return status
    previous = str(rollover.get("previous_session_id") or "")
    if not previous or not callable(archiver):
        return "pending"
    try:
        result = archiver(previous)
        archived = bool(result.get("archived")) if isinstance(result, Mapping) else bool(result)
    except Exception:
        archived = False
    rollover["archive_status"] = "archived" if archived else "pending"
    rollovers[scope] = rollover
    updated = dict(mapping)
    updated["rollovers"] = rollovers
    store.replace_mapping(chat_id, updated)
    return str(rollover["archive_status"])


def _created_session_id(created: Any) -> str:
    if isinstance(created, Mapping):
        if created.get("error"):
            return ""
        return str(created.get("session_id") or created.get("id") or "").strip()
    return str(created or "").strip()


def _rollover_idempotency_key(chat_handle: str, scope: str, rollover_day: str) -> str:
    digest = hashlib.sha256(
        f"odysseus.telegram-rollover-key.v1\0{chat_handle}\0{scope}\0{rollover_day}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"rollover:{digest}"


def _turn_key(data_dir: str | Path, chat_id: str, scope: str) -> str:
    digest = hashlib.sha256(
        f"{Path(data_dir).resolve()}\0{chat_id}\0{_normalize_scope(scope)}".encode("utf-8")
    ).hexdigest()
    return digest


def _normalize_scope(scope: str) -> str:
    return "secure" if str(scope or "").strip().lower() == "secure" else "normal"


def _session_ref(session_id: str) -> str:
    if not session_id:
        return ""
    digest = hashlib.sha256(
        b"odysseus.telegram-rollover-session-ref.v1\0" + session_id.encode("utf-8")
    ).hexdigest()[:16]
    return f"session_ref:{digest}"


def _result(status: str, scope: str, **values: Any) -> dict[str, Any]:
    session_id = str(values.pop("session_id", "") or "")
    previous_session_id = str(values.pop("previous_session_id", "") or "")
    result = {
        "schema": TELEGRAM_ROLLOVER_SCHEMA,
        "status": status,
        "scope": scope,
        **values,
        "session_ref": _session_ref(session_id),
        "previous_session_ref": _session_ref(previous_session_id),
        "raw_chat_id_visible": False,
        "raw_content_visible": False,
    }
    return result
