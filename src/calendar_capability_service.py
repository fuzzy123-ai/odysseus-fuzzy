"""Unified calendar/reminder capability service.

This module is intentionally backend-only and storage-neutral from the caller's
viewpoint. It reads the existing SQLite calendar, notes and scheduled task
tables, and returns structured owner-scoped packets that Telegram, native tools,
future UI, and a calendar MCP server can share.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


CALENDAR_CAPABILITY_SCHEMA = "odysseus.calendar_capability.v1"


class CalendarCapabilityError(ValueError):
    """Raised when calendar capability input is invalid."""


def build_agenda_packet(
    *,
    owner: str | None = None,
    start: Any | None = None,
    end: Any | None = None,
    include_due_notes: bool = True,
    include_scheduled_tasks: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Return owner-scoped agenda context for events, due notes and tasks."""

    start_dt, end_dt = _window(start, end)
    capped_limit = _safe_limit(limit)
    from core.database import CalendarCal, CalendarEvent, Note, ScheduledTask, SessionLocal

    db = SessionLocal()
    try:
        event_query = (
            db.query(CalendarEvent)
            .join(CalendarCal)
            .filter(
                CalendarEvent.dtstart < end_dt,
                CalendarEvent.dtend > start_dt,
                CalendarEvent.status != "cancelled",
            )
        )
        if owner:
            event_query = event_query.filter(CalendarCal.owner == owner)
        events = event_query.order_by(CalendarEvent.dtstart.asc()).limit(capped_limit).all()

        due_notes: list[dict[str, Any]] = []
        if include_due_notes:
            note_query = db.query(Note).filter(
                Note.archived == False,  # noqa: E712
                Note.due_date.isnot(None),
                Note.due_date != "",
            )
            if owner:
                note_query = note_query.filter(Note.owner == owner)
            for note in note_query.order_by(Note.due_date.asc()).limit(capped_limit).all():
                due = _parse_datetime(getattr(note, "due_date", None))
                if due is not None and start_dt <= due < end_dt:
                    due_notes.append(_note_to_packet(note, due))

        scheduled_tasks: list[dict[str, Any]] = []
        if include_scheduled_tasks:
            task_query = db.query(ScheduledTask).filter(
                ScheduledTask.status.in_(("active", "paused")),
                ScheduledTask.next_run.isnot(None),
                ScheduledTask.next_run >= start_dt,
                ScheduledTask.next_run < end_dt,
            )
            if owner:
                task_query = task_query.filter(ScheduledTask.owner == owner)
            tasks = task_query.order_by(ScheduledTask.next_run.asc()).limit(capped_limit).all()
            scheduled_tasks = [_task_to_packet(task) for task in tasks]

        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "status": "success",
            "owner_scoped": bool(owner),
            "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
            "events": [_event_to_packet(event) for event in events],
            "due_notes": due_notes,
            "scheduled_tasks": scheduled_tasks,
            "counts": {
                "events": len(events),
                "due_notes": len(due_notes),
                "scheduled_tasks": len(scheduled_tasks),
            },
            "raw_content_visible": False,
            "content_visibility": "owner_scoped_titles",
        }
    finally:
        db.close()


def build_calendar_readiness(*, owner: str | None = None) -> dict[str, Any]:
    """Return redacted calendar/reminder readiness diagnostics."""

    from core.database import CalendarCal, CalendarDeletedEvent, CalendarEvent, Note, ScheduledTask, SessionLocal

    db = SessionLocal()
    try:
        calendar_query = db.query(CalendarCal)
        event_query = db.query(CalendarEvent).join(CalendarCal)
        note_query = db.query(Note).filter(Note.archived == False)  # noqa: E712
        task_query = db.query(ScheduledTask)
        tombstone_query = db.query(CalendarDeletedEvent)
        if owner:
            calendar_query = calendar_query.filter(CalendarCal.owner == owner)
            event_query = event_query.filter(CalendarCal.owner == owner)
            note_query = note_query.filter(Note.owner == owner)
            task_query = task_query.filter(ScheduledTask.owner == owner)
            tombstone_query = tombstone_query.filter(CalendarDeletedEvent.owner == owner)

        pending_writebacks = event_query.filter(CalendarEvent.caldav_sync_pending.isnot(None)).count()
        caldav_accounts = _count_caldav_accounts(owner)
        active_telegram_tasks = task_query.filter(
            ScheduledTask.status == "active",
            ScheduledTask.output_target.in_(("telegram", "notification:telegram")),
        ).count()
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "status": "ready",
            "owner_scoped": bool(owner),
            "calendars": calendar_query.count(),
            "events": event_query.count(),
            "due_notes": note_query.filter(Note.due_date.isnot(None), Note.due_date != "").count(),
            "scheduled_tasks": task_query.count(),
            "active_telegram_tasks": active_telegram_tasks,
            "caldav_accounts_configured": caldav_accounts,
            "pending_caldav_writebacks": pending_writebacks,
            "pending_caldav_delete_tombstones": tombstone_query.count(),
            "raw_content_visible": False,
        }
    finally:
        db.close()


def build_todo_digest_schedule_plan(
    *,
    owner: str | None = None,
    scheduled_time: str = "09:00",
    weekdays: Iterable[int] = (0, 1, 2, 3, 4),
    output_target: str = "telegram",
    name: str = "Weekday todo digest",
    label: str = "",
    list_filter: str = "",
) -> dict[str, Any]:
    """Build the canonical single scheduled task payload for a todo digest."""

    hour, minute = _parse_hhmm(scheduled_time)
    weekday_tuple = _normalize_weekdays(weekdays)
    cron_days = ",".join(str(day + 1) for day in weekday_tuple)
    cron_expression = f"{minute} {hour} * * {cron_days}"
    task_payload = {
        "action": "create",
        "name": _safe_text(name, max_len=120) or "Weekday todo digest",
        "prompt": "Send my todo digest.",
        "task_type": "action",
        "action_name": "todo_digest",
        "trigger_type": "schedule",
        "schedule": "cron",
        "cron_expression": cron_expression,
        "scheduled_time": scheduled_time,
        "output_target": _safe_output_target(output_target),
    }
    if label:
        task_payload["label"] = _safe_text(label, max_len=80)
    if list_filter:
        task_payload["list_filter"] = _safe_text(list_filter, max_len=80)

    next_run = _compute_next_run_for_plan(
        owner=owner,
        scheduled_time=scheduled_time,
        cron_expression=cron_expression,
    )
    return {
        "schema": CALENDAR_CAPABILITY_SCHEMA,
        "status": "plan_ready",
        "kind": "todo_digest_schedule",
        "owner_scoped": bool(owner),
        "single_task": True,
        "weekdays": list(weekday_tuple),
        "cron_expression": cron_expression,
        "task_payload": task_payload,
        "next_run": next_run.isoformat() if next_run else None,
        "raw_content_visible": False,
    }


def _window(start: Any | None, end: Any | None) -> tuple[datetime, datetime]:
    start_dt = _parse_datetime(start) if start is not None else datetime.utcnow()
    if start_dt is None:
        raise CalendarCapabilityError("start must be an ISO datetime")
    end_dt = _parse_datetime(end) if end is not None else start_dt + timedelta(days=1)
    if end_dt is None:
        raise CalendarCapabilityError("end must be an ISO datetime")
    if end_dt <= start_dt:
        raise CalendarCapabilityError("end must be after start")
    return start_dt, end_dt


def _parse_datetime(value: Any | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _event_to_packet(event: Any) -> dict[str, Any]:
    calendar = getattr(event, "calendar", None)
    return {
        "uid": str(getattr(event, "uid", "") or ""),
        "calendar_id": str(getattr(event, "calendar_id", "") or ""),
        "calendar": _safe_text(getattr(calendar, "name", "") if calendar else "", max_len=120),
        "summary": _safe_text(getattr(event, "summary", "") or "", max_len=180),
        "dtstart": _iso(getattr(event, "dtstart", None)),
        "dtend": _iso(getattr(event, "dtend", None)),
        "all_day": bool(getattr(event, "all_day", False)),
        "event_type": _safe_text(getattr(event, "event_type", "") or "", max_len=40),
        "importance": _safe_text(getattr(event, "importance", "") or "normal", max_len=40),
        "source": _safe_text(getattr(event, "origin", "") or "local", max_len=40),
        "caldav_sync_pending": bool(getattr(event, "caldav_sync_pending", None)),
    }


def _note_to_packet(note: Any, due: datetime) -> dict[str, Any]:
    return {
        "id": str(getattr(note, "id", "") or ""),
        "title": _safe_text(getattr(note, "title", "") or "Note", max_len=180),
        "note_type": _safe_text(getattr(note, "note_type", "") or "note", max_len=40),
        "label": _safe_text(getattr(note, "label", "") or "", max_len=80),
        "due_date": due.isoformat(),
        "pinned": bool(getattr(note, "pinned", False)),
    }


def _task_to_packet(task: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(task, "id", "") or ""),
        "name": _safe_text(getattr(task, "name", "") or "Task", max_len=180),
        "task_type": _safe_text(getattr(task, "task_type", "") or "", max_len=40),
        "action": _safe_text(getattr(task, "action", "") or "", max_len=80),
        "schedule": _safe_text(getattr(task, "schedule", "") or "", max_len=40),
        "cron_expression": _safe_text(getattr(task, "cron_expression", "") or "", max_len=80),
        "next_run": _iso(getattr(task, "next_run", None)),
        "status": _safe_text(getattr(task, "status", "") or "", max_len=40),
        "output_target": _safe_output_target(getattr(task, "output_target", "") or ""),
    }


def _safe_text(value: Any, *, max_len: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _safe_output_target(value: Any) -> str:
    text = str(value or "session").strip().lower()
    if text in {"telegram", "notification:telegram", "session", "notification"}:
        return text
    if text.startswith("email:"):
        return "email"
    if "@" in text:
        return "email"
    return "session"


def _safe_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 50
    return max(1, min(parsed, 200))


def _parse_hhmm(value: Any) -> tuple[int, int]:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise CalendarCapabilityError("scheduled_time must be HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise CalendarCapabilityError("scheduled_time must be HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CalendarCapabilityError("scheduled_time out of range")
    return hour, minute


def _normalize_weekdays(values: Iterable[int]) -> tuple[int, ...]:
    days: list[int] = []
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise CalendarCapabilityError("weekdays must be integers 0-6")
        if parsed < 0 or parsed > 6:
            raise CalendarCapabilityError("weekdays must be integers 0-6")
        days.append(parsed)
    normalized = tuple(sorted(dict.fromkeys(days)))
    if not normalized:
        raise CalendarCapabilityError("at least one weekday is required")
    return normalized


def _compute_next_run_for_plan(*, owner: str | None, scheduled_time: str, cron_expression: str) -> datetime | None:
    from core.database import SessionLocal
    from src.task_scheduler_helpers import compute_next_run, resolve_task_timezone

    db = SessionLocal()
    try:
        tz_name = resolve_task_timezone(db, owner=owner)
        return compute_next_run("cron", scheduled_time, cron_expression=cron_expression, tz_name=tz_name)
    finally:
        db.close()


def _count_caldav_accounts(owner: str | None) -> int:
    try:
        from src.caldav_sync import _load_caldav_accounts

        return len(_load_caldav_accounts(owner or ""))
    except Exception:
        return 0


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return ""
