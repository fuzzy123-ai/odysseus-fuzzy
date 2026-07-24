"""Unified calendar/reminder capability service.

This module is intentionally backend-only and storage-neutral from the caller's
viewpoint. It reads the existing SQLite calendar, notes and scheduled task
tables, and returns structured owner-scoped packets that Telegram, native tools,
future UI, and a calendar MCP server can share.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
import hashlib
import re
import uuid
from urllib.parse import urlparse


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

        pending_event_rows = event_query.filter(CalendarEvent.caldav_sync_pending.isnot(None)).all()
        pending_writebacks = len(pending_event_rows)
        tombstone_rows = tombstone_query.all()
        caldav_accounts = _caldav_account_readiness(owner)
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
            "caldav_accounts_configured": len(caldav_accounts),
            "caldav_accounts": caldav_accounts,
            "caldav_sync_window": _caldav_sync_window_packet(),
            "pending_caldav_writebacks": pending_writebacks,
            "pending_caldav_writebacks_by_action": _pending_writeback_counts(pending_event_rows),
            "pending_caldav_writeback_samples": _pending_writeback_samples(pending_event_rows),
            "pending_caldav_delete_tombstones": len(tombstone_rows),
            "caldav_delete_tombstone_errors": _tombstone_error_packets(tombstone_rows),
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
    weekday_tuple = _normalize_weekdays(_iter_weekday_values(weekdays))
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


def build_telegram_todo_digest_live_gate(
    *,
    owner: str | None = None,
    scheduled_time: str = "09:00",
    weekdays: Iterable[int] | str = "mo-fr",
) -> dict[str, Any]:
    """Return a read-only live-gate packet for the Telegram todo digest smoke."""

    try:
        plan = build_todo_digest_schedule_plan(
            owner=owner,
            scheduled_time=scheduled_time,
            weekdays=weekdays,
            output_target="telegram",
        )
    except CalendarCapabilityError as exc:
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "kind": "telegram_todo_digest_live_gate",
            "status": "blocked",
            "reason": _safe_text(str(exc), max_len=180),
            "operator_live_go_required": True,
            "live_actions_performed": False,
            "raw_content_visible": False,
        }

    from core.database import ScheduledTask, SessionLocal

    desired_cron = str(plan.get("cron_expression") or "")
    db = SessionLocal()
    try:
        query = db.query(ScheduledTask).filter(
            ScheduledTask.task_type == "action",
            ScheduledTask.action == "todo_digest",
            ScheduledTask.output_target.in_(("telegram", "notification:telegram")),
            ScheduledTask.status.in_(("active", "paused")),
        )
        if owner:
            query = query.filter(ScheduledTask.owner == owner)
        else:
            query = query.filter(ScheduledTask.owner.is_(None))

        candidates = query.order_by(ScheduledTask.created_at.asc()).all()
        exact = [task for task in candidates if str(getattr(task, "cron_expression", "") or "") == desired_cron]
        active_exact = [task for task in exact if str(getattr(task, "status", "") or "") == "active"]

        if len(active_exact) == 1 and len(exact) == 1:
            status = "ready_for_live_smoke"
            reason = "canonical_telegram_todo_digest_task_ready"
            next_action = "With explicit operator Go, observe one scheduled Telegram todo digest delivery and record redacted evidence."
        elif len(exact) > 1 or len(active_exact) > 1:
            status = "duplicate_tasks_need_cleanup"
            reason = "multiple_matching_todo_digest_tasks"
            next_action = "Pause or merge duplicate matching todo digest tasks before any live smoke."
        elif exact and not active_exact:
            status = "paused_task"
            reason = "matching_todo_digest_task_is_not_active"
            next_action = "Resume the matching task only after operator approval, then run the bounded live smoke."
        elif candidates:
            status = "schedule_mismatch"
            reason = "telegram_todo_digest_task_exists_but_not_for_requested_schedule"
            next_action = "Update the todo digest schedule through /todo HH:MM mo-fr or the task API before live smoke."
        else:
            status = "missing_task"
            reason = "no_telegram_todo_digest_task_found"
            next_action = "Create the canonical task first, for example /todo 09:00 mo-fr, then re-check this gate."

        gates = {
            "canonical_single_task": "go" if len(active_exact) == 1 and len(exact) == 1 else "blocked",
            "schedule_matches": "go" if exact else "blocked",
            "telegram_delivery_target": "go" if exact or candidates else "blocked",
            "live_evidence_recorded": "needs_live_go",
        }
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "kind": "telegram_todo_digest_live_gate",
            "status": status,
            "reason": reason,
            "owner_scoped": bool(owner),
            "desired": {
                "scheduled_time": str(plan["task_payload"].get("scheduled_time") or ""),
                "weekdays": plan.get("weekdays") or [],
                "cron_expression": desired_cron,
                "output_target": "telegram",
                "single_task": True,
                "next_run": plan.get("next_run"),
            },
            "counts": {
                "telegram_todo_digest_tasks": len(candidates),
                "matching_schedule_tasks": len(exact),
                "active_matching_schedule_tasks": len(active_exact),
            },
            "matching_tasks": [_task_gate_packet(task) for task in exact[:5]],
            "candidate_tasks": [_task_gate_packet(task) for task in candidates[:5]],
            "gates": gates,
            "operator_live_go_required": True,
            "live_actions_performed": False,
            "next_action": next_action,
            "raw_content_visible": False,
        }
    finally:
        db.close()


def build_todo_digest_schedule_postcondition(
    *,
    owner: str,
    session_factory=None,
) -> dict[str, Any]:
    """Prove one canonical active Telegram Todo digest schedule, read-only."""
    from core.database import ScheduledTask, SessionLocal

    if not isinstance(owner, str) or not owner.strip():
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "kind": "todo_digest_schedule_postcondition",
            "claim_type": "todo_digest_schedule_active",
            "status": "blocked",
            "reason": "owner_scope_required",
            "verified": False,
            "candidate_count": 0,
            "raw_content_visible": False,
        }
    db = (session_factory or SessionLocal)()
    try:
        candidates = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.owner == owner,
                ScheduledTask.task_type == "action",
                ScheduledTask.action == "todo_digest",
                ScheduledTask.trigger_type == "schedule",
                ScheduledTask.output_target.in_(("telegram", "notification:telegram")),
                ScheduledTask.status.in_(("active", "paused")),
            )
            .order_by(ScheduledTask.created_at.asc(), ScheduledTask.id.asc())
            .all()
        )
        active = [
            task
            for task in candidates
            if str(getattr(task, "status", "") or "") == "active"
            and getattr(task, "next_run", None) is not None
        ]
        canonical = active[0] if len(candidates) == 1 and len(active) == 1 else None
        if canonical is not None:
            task_hash = _stable_short_hash(getattr(canonical, "id", "") or "")
            schedule_ref = f"todo-digest-schedule:v1:{task_hash}"
            status = "active"
            reason = "single_active_owner_scoped_todo_digest_schedule"
            evidence_refs = [f"scheduled-task-readback:v1:{task_hash}"]
            next_run = _iso(getattr(canonical, "next_run", None))
        else:
            schedule_ref = ""
            next_run = ""
            evidence_refs = []
            if len(candidates) > 1:
                status = "ambiguous"
                reason = "multiple_matching_todo_digest_schedules"
            elif candidates:
                status = "inactive"
                reason = "matching_todo_digest_schedule_not_active_or_not_runnable"
            else:
                status = "missing"
                reason = "matching_todo_digest_schedule_missing"
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "kind": "todo_digest_schedule_postcondition",
            "claim_type": "todo_digest_schedule_active",
            "status": status,
            "reason": reason,
            "schedule_ref": schedule_ref,
            "next_run": next_run,
            "candidate_count": len(candidates),
            "verified": canonical is not None,
            "evidence_refs": evidence_refs,
            "owner_scoped": True,
            "live_actions_performed": False,
            "raw_content_visible": False,
        }
    finally:
        db.close()


def write_reminder_note(
    *,
    owner: str | None = None,
    action: str = "add",
    note_id: str = "",
    title: str = "",
    due_date: Any | None = None,
    label: str = "calendar",
) -> dict[str, Any]:
    """Create or update a due note reminder through the calendar capability."""

    normalized_action = str(action or "add").replace("-", "_").strip().lower()
    normalized_action = {"create": "add", "new": "add", "remind": "add", "edit": "update"}.get(
        normalized_action,
        normalized_action,
    )
    if normalized_action not in {"add", "update"}:
        return _write_error("Unsupported reminder action. Use add or update.")

    safe_title = _safe_text(title, max_len=180)
    due_iso = _normalize_due_date(due_date) if due_date not in (None, "") else ""
    if normalized_action == "add" and (not safe_title or not due_iso):
        return _write_error("Reminder create needs title and due_date.", status="clarification_required")
    if normalized_action == "update" and (not note_id or (not safe_title and not due_iso)):
        return _write_error(
            "Reminder update needs an id and at least a new title or due_date.",
            status="clarification_required",
        )

    from core.database import Note, SessionLocal

    db = SessionLocal()
    try:
        if normalized_action == "add":
            existing_q = db.query(Note).filter(
                Note.archived == False,  # noqa: E712
                Note.due_date == due_iso,
            )
            if owner is not None:
                existing_q = existing_q.filter(Note.owner == owner)
            target_title = _normalize_reminder_title(safe_title)
            for existing in existing_q.limit(25).all():
                if _normalize_reminder_title(getattr(existing, "title", "") or "") == target_title:
                    return {
                        "schema": CALENDAR_CAPABILITY_SCHEMA,
                        "status": "duplicate",
                        "action": "add",
                        "note_id": existing.id,
                        "title": _safe_text(existing.title or safe_title, max_len=180),
                        "due_date": due_iso,
                        "raw_content_visible": False,
                    }
            note = Note(
                id=str(uuid.uuid4()),
                owner=owner,
                title=safe_title,
                content=None,
                note_type="todo",
                label=_safe_text(label, max_len=80),
                due_date=due_iso,
                source="telegram",
            )
            db.add(note)
            db.commit()
            return {
                "schema": CALENDAR_CAPABILITY_SCHEMA,
                "status": "created",
                "action": "add",
                "note_id": note.id,
                "title": safe_title,
                "due_date": due_iso,
                "raw_content_visible": False,
            }

        note = _note_by_prefix(db, Note, note_id, owner=owner)
        if note is None:
            return _write_error("Reminder not found.", status="not_found")
        if safe_title:
            note.title = safe_title
        if due_iso:
            note.due_date = due_iso
        db.commit()
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "status": "updated",
            "action": "update",
            "note_id": note.id,
            "title": _safe_text(note.title or "", max_len=180),
            "due_date": str(note.due_date or ""),
            "raw_content_visible": False,
        }
    finally:
        db.close()


def write_todo_digest_schedule(
    *,
    owner: str | None = None,
    scheduled_time: str = "09:00",
    weekdays: Iterable[int] | str = (0, 1, 2, 3, 4),
    output_target: str = "telegram",
    name: str = "Weekday todo digest",
    label: str = "",
    list_filter: str = "",
) -> dict[str, Any]:
    """Create or update the canonical single Telegram todo-digest task."""

    from core.database import ScheduledTask, SessionLocal
    from src.task_scheduler import compute_next_run
    from src.task_scheduler_helpers import resolve_task_timezone

    try:
        plan = build_todo_digest_schedule_plan(
            owner=owner,
            scheduled_time=scheduled_time,
            weekdays=weekdays,
            output_target=output_target,
            name=name,
            label=label,
            list_filter=list_filter,
        )
    except CalendarCapabilityError as exc:
        return _write_error(str(exc), status="clarification_required")

    payload = dict(plan["task_payload"])
    db = SessionLocal()
    try:
        existing = find_compatible_weekday_task(db, ScheduledTask, owner=owner, args=payload)
        if existing is not None:
            payload, merge_info = merge_weekday_task_args(existing, payload)
            existing.name = payload.get("name") or existing.name
            existing.prompt = payload.get("prompt") or existing.prompt
            existing.output_target = payload.get("output_target") or existing.output_target
            existing.task_type = payload.get("task_type", "action")
            existing.action = payload.get("action_name")
            existing.trigger_type = payload.get("trigger_type", "schedule")
            existing.schedule = payload.get("schedule")
            existing.scheduled_time = payload.get("scheduled_time")
            existing.scheduled_day = payload.get("scheduled_day")
            existing.cron_expression = payload.get("cron_expression")
            existing.next_run = compute_next_run(
                existing.schedule,
                existing.scheduled_time,
                existing.scheduled_day,
                cron_expression=existing.cron_expression,
                tz_name=resolve_task_timezone(db, existing),
            )
            db.commit()
            return {
                "schema": CALENDAR_CAPABILITY_SCHEMA,
                "status": "updated",
                "kind": "todo_digest_schedule",
                "task_id": existing.id,
                "single_task": True,
                "cron_expression": existing.cron_expression,
                "merge_info": merge_info,
                "raw_content_visible": False,
            }

        task_id = str(uuid.uuid4())
        next_run = _parse_datetime(plan.get("next_run"))
        task = ScheduledTask(
            id=task_id,
            owner=owner,
            name=payload.get("name") or "Weekday todo digest",
            prompt=payload.get("prompt"),
            task_type=payload.get("task_type", "action"),
            action=payload.get("action_name"),
            schedule=payload.get("schedule"),
            scheduled_time=payload.get("scheduled_time"),
            scheduled_day=payload.get("scheduled_day"),
            cron_expression=payload.get("cron_expression"),
            trigger_type=payload.get("trigger_type", "schedule"),
            next_run=next_run,
            status="active",
            output_target=payload.get("output_target", "telegram"),
        )
        db.add(task)
        db.commit()
        return {
            "schema": CALENDAR_CAPABILITY_SCHEMA,
            "status": "created",
            "kind": "todo_digest_schedule",
            "task_id": task.id,
            "single_task": True,
            "cron_expression": task.cron_expression,
            "raw_content_visible": False,
        }
    finally:
        db.close()


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


def _task_gate_packet(task: Any) -> dict[str, Any]:
    task_id = str(getattr(task, "id", "") or "")
    return {
        "id_hash": _stable_short_hash(task_id),
        "status": _safe_text(getattr(task, "status", "") or "", max_len=40),
        "task_type": _safe_text(getattr(task, "task_type", "") or "", max_len=40),
        "action": _safe_text(getattr(task, "action", "") or "", max_len=80),
        "schedule": _safe_text(getattr(task, "schedule", "") or "", max_len=40),
        "scheduled_time": _safe_text(getattr(task, "scheduled_time", "") or "", max_len=20),
        "cron_expression": _safe_text(getattr(task, "cron_expression", "") or "", max_len=80),
        "next_run": _iso(getattr(task, "next_run", None)),
        "last_run": _iso(getattr(task, "last_run", None)),
        "output_target": _safe_output_target(getattr(task, "output_target", "") or ""),
        "run_count": _safe_int(getattr(task, "run_count", 0)),
        "raw_content_visible": False,
    }


def _safe_text(value: Any, *, max_len: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _write_error(message: str, *, status: str = "error") -> dict[str, Any]:
    return {
        "schema": CALENDAR_CAPABILITY_SCHEMA,
        "status": status,
        "error": _safe_text(message, max_len=180),
        "raw_content_visible": False,
    }


def _normalize_due_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from routes.calendar_routes import parse_due_for_user

        parsed = parse_due_for_user(text)
        return str(parsed or text)
    except Exception:
        return text


def _normalize_reminder_title(value: str) -> str:
    text = re.sub(r"^\s*reminder\s*:\s*", "", str(value or "").strip().lower())
    return re.sub(r"\s+", " ", text)


def _note_by_prefix(db: Any, note_model: Any, note_id: str, *, owner: str | None) -> Any | None:
    prefix = str(note_id or "").strip()
    if not prefix:
        return None
    query = db.query(note_model).filter(note_model.id.startswith(prefix))
    if owner is not None:
        query = query.filter(note_model.owner == owner)
    return query.first()


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


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


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
        expanded: list[int] = []
        for part in parts:
            if not part:
                continue
            expanded.extend(_expand_weekday_part(part))
        return expanded
    try:
        return [_weekday_value(value) for value in values]
    except TypeError:
        return [_weekday_value(values)]


def _expand_weekday_part(value: str) -> list[int]:
    text = str(value or "").strip()
    if "-" not in text:
        return [_weekday_value(text)]
    start_text, end_text = (part.strip() for part in text.split("-", 1))
    start = _weekday_value(start_text)
    end = _weekday_value(end_text)
    if end < start:
        raise CalendarCapabilityError("weekday ranges must be ascending")
    return list(range(start, end + 1))


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


def _caldav_account_readiness(owner: str | None) -> list[dict[str, Any]]:
    try:
        from src.caldav_sync import _load_caldav_accounts

        accounts = _load_caldav_accounts(owner or "")
    except Exception:
        return []
    return [_redacted_caldav_account(account) for account in accounts if isinstance(account, dict)]


def _redacted_caldav_account(account: dict[str, Any]) -> dict[str, Any]:
    url = str(account.get("url") or "")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    account_id = str(account.get("id") or account.get("label") or host or "account")
    return {
        "id_hash": _stable_short_hash(account_id),
        "label": _safe_text(account.get("label") or "", max_len=80),
        "url_scheme": parsed.scheme if parsed.scheme in {"http", "https"} else "",
        "url_host": _safe_text(host, max_len=120),
        "username_configured": bool(account.get("username")),
        "password_configured": bool(account.get("password")),
        "raw_content_visible": False,
    }


def _caldav_sync_window_packet() -> dict[str, Any]:
    try:
        from src import caldav_sync

        return {
            "lookback_days": int(getattr(caldav_sync, "_LOOKBACK_DAYS", 90)),
            "lookahead_days": int(getattr(caldav_sync, "_LOOKAHEAD_DAYS", 365)),
            "private_caldav_allowed": bool(caldav_sync._private_caldav_allowed()),
        }
    except Exception:
        return {"lookback_days": 90, "lookahead_days": 365, "private_caldav_allowed": False}


def _pending_writeback_counts(events: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        action = _safe_text(getattr(event, "caldav_sync_pending", "") or "unknown", max_len=24)
        counts[action] = counts.get(action, 0) + 1
    return counts


def _pending_writeback_samples(events: Iterable[Any]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for event in list(events)[:5]:
        samples.append({
            "uid_hash": _stable_short_hash(getattr(event, "uid", "") or ""),
            "calendar_id_hash": _stable_short_hash(getattr(event, "calendar_id", "") or ""),
            "action": _safe_text(getattr(event, "caldav_sync_pending", "") or "", max_len=24),
            "raw_content_visible": False,
        })
    return samples


def _tombstone_error_packets(tombstones: Iterable[Any]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for tombstone in list(tombstones)[:5]:
        error = str(getattr(tombstone, "last_error", "") or "")
        packets.append({
            "uid_hash": _stable_short_hash(getattr(tombstone, "uid", "") or ""),
            "calendar_id_hash": _stable_short_hash(getattr(tombstone, "calendar_id", "") or ""),
            "error_hash": _stable_short_hash(error) if error else "",
            "error_class": _safe_text(error.split(":", 1)[0], max_len=80) if error else "",
            "error_length": len(error),
            "raw_content_visible": False,
        })
    return packets


def _stable_short_hash(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return ""
