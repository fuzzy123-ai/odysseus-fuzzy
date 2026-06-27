import asyncio
import json
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import CalendarCal, CalendarEvent
from src.tool_implementations import do_manage_calendar


def _isolated_calendar_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'calendar.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    monkeypatch.setattr(cdb, "SessionLocal", session_factory)
    return session_factory


def _seed_event(session_factory, uid="evt-1", owner="alice"):
    start = datetime(2026, 6, 28, 10, 0, 0)
    db = session_factory()
    try:
        db.add(CalendarCal(id="cal-1", owner=owner, name="Main", source="local"))
        db.add(CalendarEvent(
            uid=uid,
            calendar_id="cal-1",
            summary="Planning",
            dtstart=start,
            dtend=start + timedelta(hours=1),
            status="confirmed",
        ))
        db.commit()
    finally:
        db.close()


def _event_exists(session_factory, uid="evt-1"):
    db = session_factory()
    try:
        return db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first() is not None
    finally:
        db.close()


def test_manage_calendar_delete_requires_confirmation(tmp_path, monkeypatch):
    session_factory = _isolated_calendar_db(tmp_path, monkeypatch)
    _seed_event(session_factory)

    result = asyncio.run(do_manage_calendar(
        json.dumps({"action": "delete_event", "uid": "evt-1"}),
        owner="alice",
    ))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert _event_exists(session_factory)


def test_manage_calendar_delete_runs_after_confirmation(tmp_path, monkeypatch):
    session_factory = _isolated_calendar_db(tmp_path, monkeypatch)
    _seed_event(session_factory)

    result = asyncio.run(do_manage_calendar(
        json.dumps({"action": "delete_event", "uid": "evt-1", "confirmed": True}),
        owner="alice",
    ))

    assert result["exit_code"] == 0
    assert result["response"] == "Deleted event evt-1"
    assert not _event_exists(session_factory)
