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


def normalize_task_create_args(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Normalize weekday recurring task args into one canonical cron schedule.

    This is intentionally pure so both manage_tasks and future MCP tools can use
    the same contract. It accepts the shapes models already tend to produce:
    weekly scheduled_day, explicit weekdays, or a five-field cron expression.
    """

    normalized = dict(args)
    if str(normalized.get("trigger_type", "schedule") or "schedule").lower() != "schedule":
        return normalized, None

    schedule = str(normalized.get("schedule", "daily") or "daily").strip().lower()
    scheduled_time = str(normalized.get("scheduled_time") or "09:00").strip()
    weekdays: tuple[int, ...] | None = None
    hour_minute: tuple[int, int] | None = None

    if normalized.get("weekdays") is not None:
        weekdays = _normalize_weekdays(_iter_weekday_values(normalized.get("weekdays")))
        hour_minute = _parse_hhmm(scheduled_time)
    elif schedule == "weekly" and normalized.get("scheduled_day") is not None:
        weekdays = _normalize_weekdays([normalized.get("scheduled_day")])
        hour_minute = _parse_hhmm(scheduled_time)
    elif schedule == "cron" and normalized.get("cron_expression"):
        cron_info = _parse_simple_weekday_cron(str(normalized.get("cron_expression") or ""))
        if cron_info:
            hour_minute = (cron_info["hour"], cron_info["minute"])
            weekdays = tuple(cron_info["weekdays"])
            scheduled_time = f"{hour_minute[0]:02d}:{hour_minute[1]:02d}"

    if weekdays is None or hour_minute is None:
        return normalized, None

    cron_expression = _weekday_cron_expression(hour_minute[0], hour_minute[1], weekdays)
    normalized["schedule"] = "cron"
    normalized["scheduled_time"] = scheduled_time
    normalized["scheduled_day"] = None
    normalized["cron_expression"] = cron_expression
    normalized.pop("weekdays", None)
    merge = {
        "weekdays": weekdays,
        "scheduled_time": scheduled_time,
        "cron_expression": cron_expression,
    }
    return normalized, merge


def find_compatible_weekday_task(db: Any, scheduled_task_model: Any, *, owner: str | None, args: dict[str, Any]) -> Any | None:
    """Find an existing scheduled task that should absorb a weekday create."""

    _, merge = normalize_task_create_args(args)
    if not merge:
        return None

    query = db.query(scheduled_task_model).filter(
        scheduled_task_model.trigger_type == "schedule",
        scheduled_task_model.status.in_(("active", "paused")),
        scheduled_task_model.scheduled_time == merge["scheduled_time"],
    )
    if owner:
        query = query.filter(scheduled_task_model.owner == owner)
    else:
        query = query.filter(scheduled_task_model.owner.is_(None))

    task_type = str(args.get("task_type", "llm") or "llm")
    output_target = str(args.get("output_target", "session") or "session")
    query = query.filter(
        scheduled_task_model.task_type == task_type,
        scheduled_task_model.output_target == output_target,
    )

    if task_type == "action":
        action_name = args.get("action_name")
        if not action_name:
            return None
        query = query.filter(scheduled_task_model.action == action_name)
    else:
        prompt = _safe_text(args.get("prompt") or "", max_len=2000)
        if prompt:
            query = query.filter(scheduled_task_model.prompt == prompt)
        else:
            query = query.filter(scheduled_task_model.name == (args.get("name") or "Task"))

    for task in query.order_by(scheduled_task_model.created_at.asc()).all():
        if _task_weekdays(task):
            return task
    return None


def merge_weekday_task_args(task: Any, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return normalized args merged with an existing task's weekday schedule."""

    normalized, merge = normalize_task_create_args(args)
    if not merge:
        return normalized, {"merged": False}
    existing_days = set(_task_weekdays(task))
    incoming_days = set(merge["weekdays"])
    merged_days = tuple(sorted(existing_days | incoming_days))
    hour, minute = _parse_hhmm(merge["scheduled_time"])
    cron_expression = _weekday_cron_expression(hour, minute, merged_days)
    normalized["schedule"] = "cron"
    normalized["scheduled_day"] = None
    normalized["scheduled_time"] = merge["scheduled_time"]
    normalized["cron_expression"] = cron_expression
    return normalized, {
        "merged": bool(existing_days),
        "existing_weekdays": sorted(existing_days),
        "incoming_weekdays": sorted(incoming_days),
        "merged_weekdays": list(merged_days),
        "cron_expression": cron_expression,
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


def _iter_weekday_values(values: Any) -> Iterable[int]:
    if isinstance(values, str):
        parts = [part.strip() for part in values.replace(";", ",").split(",")]
        return [_weekday_value(part) for part in parts if part]
    try:
        return [_weekday_value(value) for value in values]
    except TypeError:
        return [_weekday_value(values)]


def _weekday_value(value: Any) -> int:
    text = str(value).strip().lower()
    names = {
        "mon": 0,
        "monday": 0,
        "mo": 0,
        "montag": 0,
        "tue": 1,
        "tuesday": 1,
        "di": 1,
        "dienstag": 1,
        "wed": 2,
        "wednesday": 2,
        "mi": 2,
        "mittwoch": 2,
        "thu": 3,
        "thursday": 3,
        "do": 3,
        "donnerstag": 3,
        "fri": 4,
        "friday": 4,
        "fr": 4,
        "freitag": 4,
        "sat": 5,
        "saturday": 5,
        "sa": 5,
        "samstag": 5,
        "sun": 6,
        "sunday": 6,
        "so": 6,
        "sonntag": 6,
    }
    if text in names:
        return names[text]
    return int(value)


def _weekday_cron_expression(hour: int, minute: int, weekdays: Iterable[int]) -> str:
    normalized = _normalize_weekdays(weekdays)
    cron_days = ",".join(str(day + 1) for day in normalized)
    return f"{minute} {hour} * * {cron_days}"


def _parse_simple_weekday_cron(expression: str) -> dict[str, Any] | None:
    fields = expression.strip().split()
    if len(fields) != 5:
        return None
    minute_text, hour_text, day_month, month, day_week = fields
    if day_month != "*" or month != "*":
        return None
    try:
        minute = int(minute_text)
        hour = int(hour_text)
    except ValueError:
        return None
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        return None
    weekdays = _parse_cron_weekday_field(day_week)
    if weekdays is None:
        return None
    return {"hour": hour, "minute": minute, "weekdays": weekdays}


def _parse_cron_weekday_field(field: str) -> tuple[int, ...] | None:
    days: set[int] = set()
    for raw_part in field.split(","):
        part = raw_part.strip()
        if not part:
            return None
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = _cron_day_to_weekday(start_text)
            end = _cron_day_to_weekday(end_text)
            if start is None or end is None or end < start:
                return None
            days.update(range(start, end + 1))
        else:
            day = _cron_day_to_weekday(part)
            if day is None:
                return None
            days.add(day)
    if not days:
        return None
    return tuple(sorted(days))


def _cron_day_to_weekday(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed == 0 or parsed == 7:
        return 6
    if 1 <= parsed <= 6:
        return parsed - 1
    return None


def _task_weekdays(task: Any) -> tuple[int, ...]:
    schedule = str(getattr(task, "schedule", "") or "").lower()
    if schedule == "weekly" and getattr(task, "scheduled_day", None) is not None:
        try:
            return _normalize_weekdays([getattr(task, "scheduled_day")])
        except CalendarCapabilityError:
            return ()
    if schedule == "cron" and getattr(task, "cron_expression", None):
        parsed = _parse_simple_weekday_cron(str(getattr(task, "cron_expression") or ""))
        if parsed:
            return tuple(parsed["weekdays"])
    return ()


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
