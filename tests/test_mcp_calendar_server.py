import asyncio
import json
import sys
from datetime import datetime

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarCal, CalendarEvent, CrewMember, Note, ScheduledTask

import mcp_servers.calendar_server as calendar_server

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    for key in calendar_server._OWNER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _reset_db():
    db = _TS()
    try:
        for model in (ScheduledTask, Note, CalendarEvent, CalendarCal, CrewMember):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


def _seed_calendar_data():
    _reset_db()
    db = _TS()
    try:
        db.add_all([
            CalendarCal(id="cal-alice", owner="alice", name="Alice"),
            CalendarCal(id="cal-bob", owner="bob", name="Bob"),
        ])
        db.add_all([
            CalendarEvent(
                uid="evt-alice",
                calendar_id="cal-alice",
                summary="Alice standup",
                dtstart=datetime(2026, 7, 4, 9, 0),
                dtend=datetime(2026, 7, 4, 10, 0),
            ),
            CalendarEvent(
                uid="evt-bob",
                calendar_id="cal-bob",
                summary="Bob standup",
                dtstart=datetime(2026, 7, 4, 9, 0),
                dtend=datetime(2026, 7, 4, 10, 0),
            ),
            Note(id="note-alice", owner="alice", title="Review invoice", due_date="2026-07-04T08:30:00"),
            ScheduledTask(
                id="task-alice",
                owner="alice",
                name="Todo digest",
                task_type="action",
                action="todo_digest",
                schedule="cron",
                cron_expression="0 9 * * 1,2,3,4,5",
                status="active",
                output_target="telegram",
                next_run=datetime(2026, 7, 4, 7, 0),
            ),
        ])
        db.commit()
    finally:
        db.close()


def _tool_json(name, args):
    result = asyncio.run(calendar_server.call_tool(name, args))
    return json.loads(result[0].text)


def test_calendar_mcp_lists_readonly_tools_and_resources():
    tools = asyncio.run(calendar_server.list_tools())
    resources = asyncio.run(calendar_server.list_resources())

    assert {tool.name for tool in tools} == {
        "calendar_agenda",
        "calendar_reminders",
        "calendar_readiness",
    }
    assert all("write" not in tool.name for tool in tools)
    assert {resource.name for resource in resources} == {
        "calendar_agenda",
        "calendar_reminders",
        "calendar_readiness",
    }


def test_calendar_mcp_agenda_is_owner_scoped_and_redacted(monkeypatch):
    _seed_calendar_data()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")

    payload = _tool_json("calendar_agenda", {
        "start": "2026-07-04T00:00:00",
        "end": "2026-07-05T00:00:00",
    })
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["status"] == "success"
    assert payload["owner_scoped"] is True
    assert payload["counts"] == {"events": 1, "due_notes": 1, "scheduled_tasks": 1}
    assert "Alice standup" in encoded
    assert "Bob standup" not in encoded
    assert payload["raw_content_visible"] is False


def test_calendar_mcp_fails_closed_without_owner_for_owner_scoped_data():
    _seed_calendar_data()

    result = asyncio.run(calendar_server.call_tool("calendar_readiness", {}))

    assert result[0].text.startswith("Error: Calendar MCP owner is not configured")


def test_calendar_mcp_resources_return_json(monkeypatch):
    _seed_calendar_data()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")

    raw = asyncio.run(calendar_server.read_resource(
        "odysseus://calendar/reminders?start=2026-07-04T00:00:00&end=2026-07-05T00:00:00"
    ))
    payload = json.loads(raw)

    assert payload["status"] == "success"
    assert payload["counts"] == {"due_notes": 1, "scheduled_tasks": 1}
    assert payload["raw_content_visible"] is False


def test_builtin_mcp_registers_calendar_server():
    from src.builtin_mcp import _BUILTIN_SERVERS

    assert _BUILTIN_SERVERS["calendar"] == (
        "mcp_servers/calendar_server.py",
        "Built-in: Calendar",
    )
