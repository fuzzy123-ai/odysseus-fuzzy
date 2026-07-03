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


def _text_result(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _json_text(payload: dict[str, Any]) -> list[TextContent]:
    return _text_result(json.dumps(payload, ensure_ascii=False, sort_keys=True))


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
