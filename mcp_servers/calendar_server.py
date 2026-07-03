"""
calendar_server.py

Read-only MCP server for Odysseus calendar, reminders and readiness.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("calendar")

_MCP_OWNER_ARG = "_odysseus_owner"
_OWNER_ENV_KEYS = ("ODYSSEUS_MCP_CALENDAR_OWNER", "ODYSSEUS_OWNER", "ODYSSEUS_FALLBACK_OWNER")
_RESOURCE_BASE = "odysseus://calendar"
_OWNER_SCOPE_ERROR = (
    "Error: Calendar MCP owner is not configured for owner-scoped calendar/reminder data. "
    "Set ODYSSEUS_MCP_CALENDAR_OWNER for this server or call through an owner-bound MCP context."
)


def _configured_owner() -> str | None:
    for key in _OWNER_ENV_KEYS:
        owner = os.environ.get(key, "").strip()
        if owner:
            return owner
    return None


def _resolve_owner(arguments: dict[str, Any] | None = None) -> str | None:
    arguments = arguments or {}
    owner = str(arguments.get(_MCP_OWNER_ARG, "") or "").strip()
    return owner or _configured_owner()


def _owner_scoped_data_exists() -> bool:
    try:
        from core.database import CalendarCal, Note, ScheduledTask, SessionLocal

        db = SessionLocal()
        try:
            calendar_owner = db.query(CalendarCal).filter(CalendarCal.owner.isnot(None), CalendarCal.owner != "").first()
            note_owner = db.query(Note).filter(Note.owner.isnot(None), Note.owner != "").first()
            task_owner = db.query(ScheduledTask).filter(ScheduledTask.owner.isnot(None), ScheduledTask.owner != "").first()
            return bool(calendar_owner or note_owner or task_owner)
        finally:
            db.close()
    except Exception:
        return True


def _scope_or_error(arguments: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
    owner = _resolve_owner(arguments)
    if owner is None and _owner_scoped_data_exists():
        return None, _OWNER_SCOPE_ERROR
    return owner, None


def _write_scope_or_error(arguments: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
    owner = _resolve_owner(arguments)
    if owner is None:
        return None, (
            "Error: Calendar MCP writes require an authenticated owner context. "
            "Set ODYSSEUS_MCP_CALENDAR_OWNER or call through an owner-bound MCP context."
        )
    return owner, None


def _text_result(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _json_text(payload: dict[str, Any]) -> list[TextContent]:
    return _text_result(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _confirmed(arguments: dict[str, Any]) -> bool:
    return bool(arguments.get("confirmed") or arguments.get("confirm"))


def _confirmation_required(tool: str, action: str) -> dict[str, Any]:
    return {
        "schema": "odysseus.calendar_mcp.write_result.v1",
        "status": "confirmation_required",
        "requires_confirmation": True,
        "tool": tool,
        "action": action,
        "response": f"{tool} {action} requires confirmed=true after explicit user confirmation.",
        "raw_content_visible": False,
    }


def _write_result(tool: str, action: str, result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema": "odysseus.calendar_mcp.write_result.v1",
        "status": "success" if not result.get("error") else "error",
        "tool": tool,
        "action": action,
        "exit_code": result.get("exit_code", 0 if not result.get("error") else 1),
        "response": result.get("response") or result.get("error") or "",
        "raw_content_visible": False,
    }
    for key in (
        "uid",
        "anchor",
        "note_id",
        "note_title",
        "open_url",
        "task_id",
        "duplicate",
        "deduplicated",
        "requires_confirmation",
        "reminder_note_id",
        "reminder_skipped_reason",
    ):
        if key in result:
            payload[key] = result[key]
    return payload


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _agenda_payload(arguments: dict[str, Any]) -> dict[str, Any] | str:
    owner, error = _scope_or_error(arguments)
    if error:
        return error
    from src.calendar_capability_service import build_agenda_packet

    return build_agenda_packet(
        owner=owner,
        start=arguments.get("start"),
        end=arguments.get("end"),
        include_due_notes=_parse_bool(arguments.get("include_due_notes"), True),
        include_scheduled_tasks=_parse_bool(arguments.get("include_scheduled_tasks"), True),
        limit=arguments.get("limit", 50),
    )


def _reminders_payload(arguments: dict[str, Any]) -> dict[str, Any] | str:
    owner, error = _scope_or_error(arguments)
    if error:
        return error
    from src.calendar_capability_service import build_agenda_packet

    start = arguments.get("start") or datetime.utcnow()
    end = arguments.get("end") or (datetime.utcnow() + timedelta(days=14))
    packet = build_agenda_packet(
        owner=owner,
        start=start,
        end=end,
        include_due_notes=True,
        include_scheduled_tasks=True,
        limit=arguments.get("limit", 50),
    )
    return {
        "schema": packet.get("schema"),
        "status": packet.get("status"),
        "owner_scoped": packet.get("owner_scoped"),
        "window": packet.get("window"),
        "due_notes": packet.get("due_notes", []),
        "scheduled_tasks": packet.get("scheduled_tasks", []),
        "counts": {
            "due_notes": len(packet.get("due_notes", [])),
            "scheduled_tasks": len(packet.get("scheduled_tasks", [])),
        },
        "raw_content_visible": False,
    }


def _readiness_payload(arguments: dict[str, Any]) -> dict[str, Any] | str:
    owner, error = _scope_or_error(arguments)
    if error:
        return error
    from src.calendar_capability_service import build_calendar_readiness

    return build_calendar_readiness(owner=owner)


async def _event_write_payload(arguments: dict[str, Any]) -> dict[str, Any] | str:
    owner, error = _write_scope_or_error(arguments)
    if error:
        return error
    action = str(arguments.get("action") or "create_event").replace("-", "_").strip().lower()
    aliases = {"create": "create_event", "update": "update_event", "delete": "delete_event"}
    action = aliases.get(action, action)
    if action not in {"create_event", "update_event", "delete_event"}:
        return {
            "schema": "odysseus.calendar_mcp.write_result.v1",
            "status": "error",
            "error": "Unsupported event write action. Use create_event, update_event, or delete_event.",
            "raw_content_visible": False,
        }
    if not _confirmed(arguments):
        return _confirmation_required("calendar_write_event", action)
    from src.tool_implementations import do_manage_calendar

    payload = _without_internal_owner(arguments)
    payload["action"] = action
    result = await do_manage_calendar(json.dumps(payload), owner=owner)
    return _write_result("calendar_write_event", action, result)


async def _reminder_write_payload(arguments: dict[str, Any]) -> dict[str, Any] | str:
    owner, error = _write_scope_or_error(arguments)
    if error:
        return error
    action = str(arguments.get("action") or "create").replace("-", "_").strip().lower()
    aliases = {"create": "add", "new": "add", "remind": "add", "remove": "delete"}
    action = aliases.get(action, action)
    if action not in {"add", "update", "delete"}:
        return {
            "schema": "odysseus.calendar_mcp.write_result.v1",
            "status": "error",
            "error": "Unsupported reminder write action. Use add, update, or delete.",
            "raw_content_visible": False,
        }
    if not _confirmed(arguments):
        return _confirmation_required("calendar_write_reminder", action)
    from src.tool_implementations import do_manage_notes

    payload = _without_internal_owner(arguments)
    payload["action"] = action
    if action == "add":
        payload.setdefault("note_type", "todo")
        payload.setdefault("label", "calendar")
    result = await do_manage_notes(json.dumps(payload), owner=owner)
    return _write_result("calendar_write_reminder", action, result)


async def _digest_schedule_write_payload(arguments: dict[str, Any]) -> dict[str, Any] | str:
    owner, error = _write_scope_or_error(arguments)
    if error:
        return error
    action = str(arguments.get("action") or "create").replace("-", "_").strip().lower()
    aliases = {"add": "create", "update": "edit", "remove": "delete"}
    action = aliases.get(action, action)
    if action not in {"create", "edit", "delete"}:
        return {
            "schema": "odysseus.calendar_mcp.write_result.v1",
            "status": "error",
            "error": "Unsupported digest schedule action. Use create, edit, or delete.",
            "raw_content_visible": False,
        }
    if not _confirmed(arguments):
        return _confirmation_required("calendar_write_todo_digest", action)
    from src.calendar_capability_service import build_todo_digest_schedule_plan
    from src.tool_implementations import do_manage_tasks

    if action == "create":
        plan = build_todo_digest_schedule_plan(
            owner=owner,
            scheduled_time=arguments.get("scheduled_time", "09:00"),
            weekdays=_digest_weekdays(arguments.get("weekdays")),
            output_target=arguments.get("output_target", "telegram"),
            name=arguments.get("name", "Weekday todo digest"),
            label=arguments.get("label", ""),
            list_filter=arguments.get("list_filter", ""),
        )
        payload = dict(plan["task_payload"])
    else:
        payload = _without_internal_owner(arguments)
        payload["action"] = action
    payload["confirmed"] = True
    result = await do_manage_tasks(json.dumps(payload), owner=owner)
    wrapped = _write_result("calendar_write_todo_digest", action, result)
    if action == "create":
        wrapped["cron_expression"] = payload.get("cron_expression")
        wrapped["single_task"] = True
    return wrapped


def _without_internal_owner(arguments: dict[str, Any]) -> dict[str, Any]:
    payload = dict(arguments)
    payload.pop(_MCP_OWNER_ARG, None)
    return payload


def _digest_weekdays(value: Any) -> Any:
    if value in (None, ""):
        return (0, 1, 2, 3, 4)
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return value


def _resource_args(uri: Any) -> tuple[str, dict[str, Any]]:
    parsed = urlparse(str(uri))
    path = parsed.path.strip("/")
    if not path and parsed.netloc:
        path = parsed.netloc
    args: dict[str, Any] = {
        key: values[-1] if values else ""
        for key, values in parse_qs(parsed.query).items()
    }
    return path, args


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            name="calendar_agenda",
            title="Odysseus Calendar Agenda",
            uri=f"{_RESOURCE_BASE}/agenda",
            description="Owner-scoped upcoming calendar events, due notes and scheduled tasks.",
            mimeType="application/json",
        ),
        Resource(
            name="calendar_reminders",
            title="Odysseus Calendar Reminders",
            uri=f"{_RESOURCE_BASE}/reminders",
            description="Owner-scoped due notes and scheduled task reminders without raw private content.",
            mimeType="application/json",
        ),
        Resource(
            name="calendar_readiness",
            title="Odysseus Calendar Readiness",
            uri=f"{_RESOURCE_BASE}/readiness",
            description="Redacted calendar, reminder and CalDAV readiness diagnostics.",
            mimeType="application/json",
        ),
    ]


@server.read_resource()
async def read_resource(uri) -> str:
    path, args = _resource_args(uri)
    if path == "agenda":
        payload = _agenda_payload(args)
    elif path == "reminders":
        payload = _reminders_payload(args)
    elif path == "readiness":
        payload = _readiness_payload(args)
    else:
        payload = {"status": "error", "error": f"Unknown calendar resource: {uri}", "raw_content_visible": False}
    if isinstance(payload, str):
        payload = {"status": "error", "error": payload, "raw_content_visible": False}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="calendar_agenda",
            description="Read owner-scoped calendar agenda context. This is read-only and returns events, due notes and scheduled tasks.",
            inputSchema={
                "type": "object",
                "properties": {
                    _MCP_OWNER_ARG: {"type": "string", "description": "Internal owner context; prefer server-bound env."},
                    "start": {"type": "string", "description": "ISO datetime start; defaults to now."},
                    "end": {"type": "string", "description": "ISO datetime end; defaults to start + 1 day."},
                    "limit": {"type": "integer", "description": "Maximum rows per category, capped by backend."},
                    "include_due_notes": {"type": "boolean", "description": "Include due notes. Defaults true."},
                    "include_scheduled_tasks": {"type": "boolean", "description": "Include scheduled tasks. Defaults true."},
                },
            },
        ),
        Tool(
            name="calendar_reminders",
            description="Read owner-scoped due notes and scheduled task reminders without calendar event detail.",
            inputSchema={
                "type": "object",
                "properties": {
                    _MCP_OWNER_ARG: {"type": "string", "description": "Internal owner context; prefer server-bound env."},
                    "start": {"type": "string", "description": "ISO datetime start; defaults to now."},
                    "end": {"type": "string", "description": "ISO datetime end; defaults to start + 14 days."},
                    "limit": {"type": "integer", "description": "Maximum rows per category, capped by backend."},
                },
            },
        ),
        Tool(
            name="calendar_readiness",
            description="Read redacted calendar/reminder/CalDAV readiness diagnostics. This tool never writes calendar state.",
            inputSchema={
                "type": "object",
                "properties": {
                    _MCP_OWNER_ARG: {"type": "string", "description": "Internal owner context; prefer server-bound env."},
                },
            },
        ),
        Tool(
            name="calendar_write_event",
            description="Create, update, or delete one owner-scoped calendar event. Requires confirmed=true for every write.",
            inputSchema={
                "type": "object",
                "properties": {
                    _MCP_OWNER_ARG: {"type": "string", "description": "Internal owner context; prefer server-bound env."},
                    "action": {"type": "string", "enum": ["create_event", "update_event", "delete_event", "create", "update", "delete"]},
                    "confirmed": {"type": "boolean", "description": "Required true after explicit user confirmation."},
                    "uid": {"type": "string", "description": "Event UID for update/delete."},
                    "summary": {"type": "string", "description": "Event title for create/update."},
                    "dtstart": {"type": "string", "description": "Start ISO/natural datetime for create/update."},
                    "dtend": {"type": "string", "description": "End ISO/natural datetime for create/update."},
                    "all_day": {"type": "boolean"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "reminder_minutes": {"type": "integer", "description": "Optional note reminder offset."},
                    "rrule": {"type": "string", "description": "Optional recurrence rule."},
                    "event_type": {"type": "string"},
                    "importance": {"type": "string"},
                },
                "required": ["action", "confirmed"],
            },
        ),
        Tool(
            name="calendar_write_reminder",
            description="Create, update, or delete one owner-scoped reminder note. Requires confirmed=true for every write.",
            inputSchema={
                "type": "object",
                "properties": {
                    _MCP_OWNER_ARG: {"type": "string", "description": "Internal owner context; prefer server-bound env."},
                    "action": {"type": "string", "enum": ["add", "update", "delete", "create", "remove"]},
                    "confirmed": {"type": "boolean", "description": "Required true after explicit user confirmation."},
                    "id": {"type": "string", "description": "Reminder/note id for update/delete."},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "due_date": {"type": "string", "description": "ISO or natural-language due date."},
                    "label": {"type": "string"},
                    "pinned": {"type": "boolean"},
                },
                "required": ["action", "confirmed"],
            },
        ),
        Tool(
            name="calendar_write_todo_digest",
            description="Create, update, or delete one owner-scoped todo digest schedule. Create uses one canonical cron task. Requires confirmed=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    _MCP_OWNER_ARG: {"type": "string", "description": "Internal owner context; prefer server-bound env."},
                    "action": {"type": "string", "enum": ["create", "edit", "delete", "add", "update", "remove"]},
                    "confirmed": {"type": "boolean", "description": "Required true after explicit user confirmation."},
                    "task_id": {"type": "string", "description": "Task id for edit/delete."},
                    "name": {"type": "string"},
                    "scheduled_time": {"type": "string", "description": "HH:MM in owner timezone; defaults 09:00."},
                    "weekdays": {"description": "Weekdays as integers 0=Mon or names; defaults Mo-Fr."},
                    "output_target": {"type": "string", "description": "Defaults telegram. Never pass chat ids/tokens."},
                    "label": {"type": "string"},
                    "list_filter": {"type": "string"},
                },
                "required": ["action", "confirmed"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    arguments = dict(arguments) if isinstance(arguments, dict) else {}
    if name == "calendar_agenda":
        payload = _agenda_payload(arguments)
    elif name == "calendar_reminders":
        payload = _reminders_payload(arguments)
    elif name == "calendar_readiness":
        payload = _readiness_payload(arguments)
    elif name == "calendar_write_event":
        payload = await _event_write_payload(arguments)
    elif name == "calendar_write_reminder":
        payload = await _reminder_write_payload(arguments)
    elif name == "calendar_write_todo_digest":
        payload = await _digest_schedule_write_payload(arguments)
    else:
        return _text_result(f"Unknown tool: {name}")
    if isinstance(payload, str):
        return _text_result(payload)
    return _json_text(payload)


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
