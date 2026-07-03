"""Redacted summaries for scheduled task chat/status surfaces."""

from __future__ import annotations

import re
from typing import Any, Iterable


TASK_SUMMARY_SCHEMA = "odysseus.task_summary.v1"

_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_CRON_PART_RE = re.compile(r"^[0-9*,/-]+$")


def summarize_tasks(tasks: Iterable[Any]) -> dict[str, Any]:
    items = [summarize_task(task) for task in tasks]
    active = sum(1 for item in items if item["status"] == "active")
    paused = sum(1 for item in items if item["status"] == "paused")
    return {
        "schema": TASK_SUMMARY_SCHEMA,
        "count": len(items),
        "active_count": active,
        "paused_count": paused,
        "items": items,
        "prompt_visible": False,
        "run_result_visible": False,
        "webhook_token_visible": False,
        "raw_content_visible": False,
    }


def summarize_task(task: Any) -> dict[str, Any]:
    trigger_type = _safe_token(getattr(task, "trigger_type", None) or "schedule")
    schedule = _safe_token(getattr(task, "schedule", None) or "")
    cron_expression = str(getattr(task, "cron_expression", None) or "").strip()
    recurrence = describe_recurrence(
        trigger_type=trigger_type,
        schedule=schedule,
        scheduled_time=getattr(task, "scheduled_time", None),
        scheduled_day=getattr(task, "scheduled_day", None),
        scheduled_date=getattr(task, "scheduled_date", None),
        cron_expression=cron_expression,
        trigger_event=getattr(task, "trigger_event", None),
        trigger_count=getattr(task, "trigger_count", None),
    )
    return {
        "id": _safe_id(getattr(task, "id", "")),
        "name": _safe_name(getattr(task, "name", "") or "Untitled Task"),
        "status": _safe_token(getattr(task, "status", None) or "unknown"),
        "task_type": _safe_token(getattr(task, "task_type", None) or "llm"),
        "action": _safe_token(getattr(task, "action", None) or ""),
        "trigger_type": trigger_type,
        "schedule": schedule,
        "recurrence": recurrence,
        "next_run": _iso(getattr(task, "next_run", None)),
        "last_run": _iso(getattr(task, "last_run", None)),
        "output_target": _safe_token(getattr(task, "output_target", None) or "session"),
        "run_count": _safe_count(getattr(task, "run_count", 0)),
        "notifications_enabled": bool(getattr(task, "notifications_enabled", True)),
        "prompt_visible": False,
        "run_result_visible": False,
        "webhook_token_visible": False,
        "raw_content_visible": False,
    }


def describe_recurrence(
    *,
    trigger_type: str,
    schedule: str,
    scheduled_time: Any = None,
    scheduled_day: Any = None,
    scheduled_date: Any = None,
    cron_expression: str = "",
    trigger_event: Any = None,
    trigger_count: Any = None,
) -> dict[str, Any]:
    if trigger_type == "event":
        count = _safe_count(trigger_count) or 1
        event = _safe_token(trigger_event or "event")
        return _recurrence("event", f"{event} every {count}", event=event, trigger_count=count)
    if trigger_type == "webhook":
        return _recurrence("webhook", "Webhook")
    if schedule == "cron":
        return _describe_cron(cron_expression)
    if schedule == "once":
        label = _iso(scheduled_date) or "Once"
        return _recurrence("once", label)
    if schedule == "daily":
        return _recurrence("daily", f"Daily {_safe_time(scheduled_time) or '--:--'}")
    if schedule == "weekly":
        day = _weekday_label(scheduled_day)
        return _recurrence("weekly", f"{day} {_safe_time(scheduled_time) or '--:--'}", weekdays=(day,))
    if schedule == "monthly":
        day = _safe_count(scheduled_day) or 1
        return _recurrence("monthly", f"Monthly day {day} {_safe_time(scheduled_time) or '--:--'}", month_day=day)
    return _recurrence("unknown", schedule or trigger_type or "unknown")


def _describe_cron(expression: str) -> dict[str, Any]:
    minute, hour, _dom, _month, dow = _split_cron(expression)
    times = _cron_times(minute, hour)
    weekdays = _cron_weekdays(dow)
    if times and weekdays == _WEEKDAYS[:5]:
        return _recurrence("cron", f"Mo-Fr {', '.join(times)}", cron_expression_visible=True, weekdays=weekdays)
    if times and weekdays == _WEEKDAYS:
        return _recurrence("cron", f"Daily {', '.join(times)}", cron_expression_visible=True, weekdays=weekdays)
    if times and weekdays:
        return _recurrence("cron", f"{', '.join(weekdays)} {', '.join(times)}", cron_expression_visible=True, weekdays=weekdays)
    if expression == "*/5 * * * *":
        return _recurrence("cron", "Every 5 minutes", cron_expression_visible=True)
    if expression == "*/15 * * * *":
        return _recurrence("cron", "Every 15 minutes", cron_expression_visible=True)
    return _recurrence("cron", "Cron schedule", cron_expression_visible=bool(expression))


def _split_cron(expression: str) -> tuple[str, str, str, str, str]:
    parts = str(expression or "").strip().split()
    if len(parts) != 5 or any(not _CRON_PART_RE.fullmatch(part) for part in parts):
        return "", "", "", "", ""
    return tuple(parts)  # type: ignore[return-value]


def _cron_times(minute: str, hour: str) -> tuple[str, ...]:
    if not minute.isdigit():
        return ()
    minute_value = int(minute)
    if not (0 <= minute_value <= 59):
        return ()
    hours = _expand_numbers(hour, 0, 23)
    return tuple(f"{h:02d}:{minute_value:02d}" for h in hours)


def _cron_weekdays(value: str) -> tuple[str, ...]:
    if value in {"*", "0-6", "1-7"}:
        return _WEEKDAYS
    days = []
    for raw in _expand_numbers(value, 0, 7):
        day = 6 if raw == 0 or raw == 7 else raw - 1
        if 0 <= day <= 6 and _WEEKDAYS[day] not in days:
            days.append(_WEEKDAYS[day])
    return tuple(days)


def _expand_numbers(value: str, minimum: int, maximum: int) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not text or "/" in text:
        return ()
    numbers: list[int] = []
    for part in text.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            if not start.isdigit() or not end.isdigit():
                return ()
            numbers.extend(range(int(start), int(end) + 1))
        elif part.isdigit():
            numbers.append(int(part))
        else:
            return ()
    return tuple(num for num in numbers if minimum <= num <= maximum)


def _recurrence(kind: str, label: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label[:120],
        **extra,
    }


def _weekday_label(value: Any) -> str:
    try:
        day = int(value)
    except (TypeError, ValueError):
        day = 0
    if 0 <= day <= 6:
        return _WEEKDAYS[day]
    return _WEEKDAYS[0]


def _safe_time(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = text.split(":", 1)
        if 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59:
            return f"{int(hour):02d}:{int(minute):02d}"
    return ""


def _safe_id(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "._:-")[:80]


def _safe_name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:120]


def _safe_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "._:-")[:80]


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat() + "Z"
    text = str(value or "").strip()
    return text[:80] if text else None
