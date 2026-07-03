import asyncio
import json
import sys
from datetime import datetime

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarCal, CalendarDeletedEvent, CalendarEvent, CrewMember, Note, ScheduledTask

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
        for model in (ScheduledTask, Note, CalendarEvent, CalendarDeletedEvent, CalendarCal, CrewMember):
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


def _tool_text(name, args):
    result = asyncio.run(calendar_server.call_tool(name, args))
    return result[0].text


def _db_counts():
    db = _TS()
    try:
        return {
            "events": db.query(CalendarEvent).count(),
            "notes": db.query(Note).count(),
            "tasks": db.query(ScheduledTask).count(),
        }
    finally:
        db.close()


def _event_exists(uid):
    db = _TS()
    try:
        return db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first() is not None
    finally:
        db.close()


def test_calendar_mcp_lists_readonly_tools_and_resources():
    tools = asyncio.run(calendar_server.list_tools())
    resources = asyncio.run(calendar_server.list_resources())

    assert {tool.name for tool in tools} == {
        "calendar_agenda",
        "calendar_reminders",
        "calendar_readiness",
        "calendar_write_event",
        "calendar_write_reminder",
        "calendar_write_todo_digest",
    }
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


def test_calendar_mcp_readiness_exposes_caldav_diagnostics_redacted(monkeypatch):
    _reset_db()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")
    monkeypatch.setattr("src.caldav_sync._load_caldav_accounts", lambda owner: [{
        "id": "nextcloud-primary",
        "label": "Private Nextcloud",
        "url": "https://nextcloud.example.test/remote.php/dav",
        "username": "private-user",
        "password": "private-password",
    }])
    db = _TS()
    try:
        db.add(CalendarCal(id="cal-alice", owner="alice", name="Alice", source="caldav"))
        db.add(CalendarEvent(
            uid="evt-pending",
            calendar_id="cal-alice",
            summary="Pending private event",
            dtstart=datetime(2026, 7, 4, 9, 0),
            dtend=datetime(2026, 7, 4, 10, 0),
            caldav_sync_pending="create",
        ))
        db.add(CalendarDeletedEvent(
            uid="evt-deleted",
            owner="alice",
            calendar_id="cal-alice",
            summary="Private deleted event",
            last_error="AuthorizationError: private-password rejected",
        ))
        db.commit()
    finally:
        db.close()

    payload = _tool_json("calendar_readiness", {})
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["pending_caldav_writebacks"] == 1
    assert payload["pending_caldav_writebacks_by_action"] == {"create": 1}
    assert payload["pending_caldav_delete_tombstones"] == 1
    assert payload["caldav_accounts_configured"] == 1
    assert payload["caldav_accounts"][0]["password_configured"] is True
    assert payload["caldav_delete_tombstone_errors"][0]["error_hash"]
    assert "private-password" not in encoded
    assert "evt-pending" not in encoded
    assert "evt-deleted" not in encoded
    assert payload["raw_content_visible"] is False


def test_builtin_mcp_registers_calendar_server():
    from src.builtin_mcp import _BUILTIN_SERVERS

    assert _BUILTIN_SERVERS["calendar"] == (
        "mcp_servers/calendar_server.py",
        "Built-in: Calendar",
    )


def test_calendar_mcp_write_event_requires_confirmation(monkeypatch):
    _reset_db()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")

    payload = _tool_json("calendar_write_event", {
        "action": "create_event",
        "summary": "Blocked write",
        "dtstart": "2026-07-04T09:00:00",
    })

    assert payload["status"] == "confirmation_required"
    assert payload["requires_confirmation"] is True
    assert _db_counts()["events"] == 0


def test_calendar_mcp_write_event_confirmed_creates_owner_scoped_event(monkeypatch):
    _reset_db()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")

    payload = _tool_json("calendar_write_event", {
        "action": "create_event",
        "confirmed": True,
        "summary": "Confirmed planning",
        "dtstart": "2026-07-04T09:00:00",
        "dtend": "2026-07-04T10:00:00",
        "reminder_minutes": 15,
    })

    db = _TS()
    try:
        event = db.query(CalendarEvent).filter(CalendarEvent.uid == payload["uid"]).first()
        note = db.query(Note).filter(Note.owner == "alice").first()
        assert payload["status"] == "success"
        assert event is not None
        assert event.calendar.owner == "alice"
        assert note is not None
        assert note.owner == "alice"
        assert payload["raw_content_visible"] is False
    finally:
        db.close()


def test_calendar_mcp_delete_event_requires_confirmation_and_then_deletes(monkeypatch):
    _seed_calendar_data()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")

    blocked = _tool_json("calendar_write_event", {"action": "delete_event", "uid": "evt-alice"})
    deleted = _tool_json("calendar_write_event", {
        "action": "delete_event",
        "uid": "evt-alice",
        "confirmed": True,
    })

    assert blocked["status"] == "confirmation_required"
    assert deleted["status"] == "success"
    assert not _event_exists("evt-alice")
    assert _event_exists("evt-bob")


def test_calendar_mcp_write_requires_owner_even_without_existing_data():
    _reset_db()

    text = _tool_text("calendar_write_event", {
        "action": "create_event",
        "confirmed": True,
        "summary": "No owner",
        "dtstart": "2026-07-04T09:00:00",
    })

    assert text.startswith("Error: Calendar MCP writes require an authenticated owner context")
    assert _db_counts()["events"] == 0


def test_calendar_mcp_write_reminder_confirmed_creates_owner_scoped_note(monkeypatch):
    _reset_db()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")

    payload = _tool_json("calendar_write_reminder", {
        "action": "add",
        "confirmed": True,
        "title": "Call dentist",
        "due_date": "2026-07-04T09:00:00",
    })

    db = _TS()
    try:
        note = db.query(Note).filter(Note.id == payload["note_id"]).first()
        assert payload["status"] == "success"
        assert note is not None
        assert note.owner == "alice"
        assert note.label == "calendar"
        assert payload["raw_content_visible"] is False
    finally:
        db.close()


def test_calendar_mcp_write_todo_digest_confirmed_creates_single_cron_task(monkeypatch):
    _reset_db()
    monkeypatch.setenv("ODYSSEUS_MCP_CALENDAR_OWNER", "alice")
    db = _TS()
    try:
        db.add(CrewMember(
            id="assistant-alice",
            owner="alice",
            name="Assistant",
            is_default_assistant=True,
            timezone="Europe/Berlin",
        ))
        db.commit()
    finally:
        db.close()

    payload = _tool_json("calendar_write_todo_digest", {
        "action": "create",
        "confirmed": True,
        "scheduled_time": "09:00",
        "weekdays": "mo,di,mi,do,fr",
        "output_target": "telegram",
    })

    db = _TS()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == payload["task_id"]).first()
        encoded = json.dumps(payload, ensure_ascii=False)
        assert payload["status"] == "success"
        assert payload["single_task"] is True
        assert payload["cron_expression"] == "0 9 * * 1,2,3,4,5"
        assert task is not None
        assert task.owner == "alice"
        assert task.action == "todo_digest"
        assert task.output_target == "telegram"
        assert "chat_id" not in encoded.lower()
        assert "token" not in encoded.lower()
    finally:
        db.close()
