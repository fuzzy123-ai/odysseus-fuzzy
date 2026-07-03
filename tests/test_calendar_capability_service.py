from datetime import datetime
import sys

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarCal, CalendarEvent, CrewMember, Note, ScheduledTask

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


def _reset_db():
    db = _TS()
    try:
        for model in (ScheduledTask, Note, CalendarEvent, CalendarCal, CrewMember):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


def test_agenda_packet_is_owner_scoped_and_combines_events_notes_and_tasks():
    from src.calendar_capability_service import build_agenda_packet

    _reset_db()
    db = _TS()
    try:
        alice_cal = CalendarCal(id="cal-alice", owner="alice", name="Alice")
        bob_cal = CalendarCal(id="cal-bob", owner="bob", name="Bob")
        db.add_all([alice_cal, bob_cal])
        db.add_all([
            CalendarEvent(
                uid="evt-alice",
                calendar_id="cal-alice",
                summary="Alice appointment",
                dtstart=datetime(2026, 7, 4, 9, 0),
                dtend=datetime(2026, 7, 4, 10, 0),
            ),
            CalendarEvent(
                uid="evt-bob",
                calendar_id="cal-bob",
                summary="Bob appointment",
                dtstart=datetime(2026, 7, 4, 9, 0),
                dtend=datetime(2026, 7, 4, 10, 0),
            ),
            Note(
                id="note-alice",
                owner="alice",
                title="Pay invoice",
                note_type="note",
                due_date="2026-07-04T08:30:00",
            ),
            ScheduledTask(
                id="task-alice",
                owner="alice",
                name="Todo digest",
                task_type="action",
                action="todo_digest",
                schedule="cron",
                cron_expression="0 9 * * 1-5",
                status="active",
                output_target="telegram",
                next_run=datetime(2026, 7, 4, 7, 0),
            ),
        ])
        db.commit()
    finally:
        db.close()

    packet = build_agenda_packet(
        owner="alice",
        start=datetime(2026, 7, 4, 0, 0),
        end=datetime(2026, 7, 5, 0, 0),
    )

    assert packet["status"] == "success"
    assert packet["owner_scoped"] is True
    assert packet["counts"] == {"events": 1, "due_notes": 1, "scheduled_tasks": 1}
    assert packet["events"][0]["summary"] == "Alice appointment"
    assert packet["due_notes"][0]["title"] == "Pay invoice"
    assert packet["scheduled_tasks"][0]["output_target"] == "telegram"
    assert "Bob appointment" not in str(packet)
    assert packet["raw_content_visible"] is False


def test_todo_digest_schedule_plan_uses_one_weekday_cron_task():
    from src.calendar_capability_service import build_todo_digest_schedule_plan

    _reset_db()
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

    plan = build_todo_digest_schedule_plan(owner="alice", scheduled_time="09:00")

    assert plan["status"] == "plan_ready"
    assert plan["single_task"] is True
    assert plan["cron_expression"] == "0 9 * * 1,2,3,4,5"
    assert plan["task_payload"]["task_type"] == "action"
    assert plan["task_payload"]["action_name"] == "todo_digest"
    assert plan["task_payload"]["output_target"] == "telegram"
    assert plan["next_run"]
    assert plan["raw_content_visible"] is False


def test_calendar_readiness_reports_redacted_counts():
    from src.calendar_capability_service import build_calendar_readiness

    _reset_db()
    db = _TS()
    try:
        db.add(CalendarCal(id="cal-alice", owner="alice", name="Alice", source="caldav"))
        db.add(CalendarEvent(
            uid="evt-pending",
            calendar_id="cal-alice",
            summary="Pending writeback",
            dtstart=datetime(2026, 7, 4, 9, 0),
            dtend=datetime(2026, 7, 4, 10, 0),
            caldav_sync_pending="update",
        ))
        db.add(Note(id="note-due", owner="alice", title="Due", due_date="2026-07-04T08:30:00"))
        db.add(ScheduledTask(
            id="task-telegram",
            owner="alice",
            name="Telegram digest",
            task_type="action",
            status="active",
            output_target="telegram",
        ))
        db.commit()
    finally:
        db.close()

    readiness = build_calendar_readiness(owner="alice")

    assert readiness["status"] == "ready"
    assert readiness["calendars"] == 1
    assert readiness["events"] == 1
    assert readiness["due_notes"] == 1
    assert readiness["active_telegram_tasks"] == 1
    assert readiness["pending_caldav_writebacks"] == 1
    assert readiness["raw_content_visible"] is False
