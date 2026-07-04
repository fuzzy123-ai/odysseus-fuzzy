from datetime import datetime
import sys

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import CalendarCal, CalendarDeletedEvent, CalendarEvent, CrewMember, Note, ScheduledTask

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
        for model in (ScheduledTask, Note, CalendarEvent, CalendarDeletedEvent, CalendarCal, CrewMember):
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


def test_todo_digest_schedule_plan_accepts_weekday_ranges():
    from src.calendar_capability_service import build_todo_digest_schedule_plan

    _reset_db()

    plan = build_todo_digest_schedule_plan(owner="alice", scheduled_time="09:00", weekdays="mo-fr")

    assert plan["single_task"] is True
    assert plan["weekdays"] == [0, 1, 2, 3, 4]
    assert plan["cron_expression"] == "0 9 * * 1,2,3,4,5"


def test_telegram_todo_digest_live_gate_reports_ready_without_live_action():
    from src.calendar_capability_service import build_telegram_todo_digest_live_gate

    _reset_db()
    db = _TS()
    try:
        db.add(ScheduledTask(
            id="private-task-id",
            owner="alice",
            name="Private task name must not leak",
            prompt="private prompt must not leak",
            task_type="action",
            action="todo_digest",
            schedule="cron",
            scheduled_time="09:00",
            cron_expression="0 9 * * 1,2,3,4,5",
            status="active",
            output_target="telegram",
            next_run=datetime(2026, 7, 6, 7, 0),
            run_count=1,
            webhook_token="secret-token",
        ))
        db.commit()
    finally:
        db.close()

    packet = build_telegram_todo_digest_live_gate(owner="alice")
    encoded = str(packet)

    assert packet["status"] == "ready_for_live_smoke"
    assert packet["counts"] == {
        "telegram_todo_digest_tasks": 1,
        "matching_schedule_tasks": 1,
        "active_matching_schedule_tasks": 1,
    }
    assert packet["gates"]["canonical_single_task"] == "go"
    assert packet["gates"]["live_evidence_recorded"] == "needs_live_go"
    assert packet["operator_live_go_required"] is True
    assert packet["live_actions_performed"] is False
    assert packet["matching_tasks"][0]["id_hash"]
    assert "Private task name" not in encoded
    assert "private prompt" not in encoded
    assert "secret-token" not in encoded
    assert "private-task-id" not in encoded


def test_telegram_todo_digest_live_gate_detects_missing_and_duplicates():
    from src.calendar_capability_service import build_telegram_todo_digest_live_gate

    _reset_db()

    missing = build_telegram_todo_digest_live_gate(owner="alice")
    assert missing["status"] == "missing_task"
    assert missing["next_action"].startswith("Create the canonical task")

    db = _TS()
    try:
        for task_id in ("task-a", "task-b"):
            db.add(ScheduledTask(
                id=task_id,
                owner="alice",
                name=f"Duplicate {task_id}",
                task_type="action",
                action="todo_digest",
                schedule="cron",
                scheduled_time="09:00",
                cron_expression="0 9 * * 1,2,3,4,5",
                status="active",
                output_target="telegram",
            ))
        db.commit()
    finally:
        db.close()

    duplicate = build_telegram_todo_digest_live_gate(owner="alice")

    assert duplicate["status"] == "duplicate_tasks_need_cleanup"
    assert duplicate["counts"]["matching_schedule_tasks"] == 2
    assert duplicate["gates"]["canonical_single_task"] == "blocked"


def test_calendar_readiness_reports_redacted_counts(monkeypatch):
    from src.calendar_capability_service import build_calendar_readiness

    _reset_db()
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
            summary="Pending writeback",
            dtstart=datetime(2026, 7, 4, 9, 0),
            dtend=datetime(2026, 7, 4, 10, 0),
            caldav_sync_pending="update",
        ))
        db.add(CalendarDeletedEvent(
            uid="evt-deleted",
            owner="alice",
            calendar_id="cal-alice",
            summary="Private deleted event",
            last_error="AuthorizationError: private-password rejected",
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
    assert readiness["caldav_accounts_configured"] == 1
    assert readiness["caldav_accounts"][0]["password_configured"] is True
    assert readiness["caldav_accounts"][0]["url_host"] == "nextcloud.example.test"
    assert readiness["pending_caldav_writebacks"] == 1
    assert readiness["pending_caldav_writebacks_by_action"] == {"update": 1}
    assert readiness["pending_caldav_writeback_samples"][0]["uid_hash"]
    assert readiness["pending_caldav_delete_tombstones"] == 1
    assert readiness["caldav_delete_tombstone_errors"][0]["error_hash"]
    assert readiness["caldav_sync_window"]["lookahead_days"] >= 1
    assert readiness["raw_content_visible"] is False
    encoded = str(readiness)
    assert "private-password" not in encoded
    assert "evt-pending" not in encoded
    assert "evt-deleted" not in encoded
