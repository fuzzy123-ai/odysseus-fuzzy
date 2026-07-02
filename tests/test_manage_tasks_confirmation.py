import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ScheduledTask
from src.tool_implementations import do_manage_tasks


def _isolated_task_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", session_factory)
    return session_factory


def _seed_task(session_factory, task_id="task-1", owner="alice"):
    db = session_factory()
    try:
        db.add(ScheduledTask(
            id=task_id,
            owner=owner,
            name="Daily summary",
            prompt="Summarize my inbox",
            task_type="llm",
            trigger_type="schedule",
            status="active",
            output_target="session",
        ))
        db.commit()
    finally:
        db.close()


def _task_exists(session_factory, task_id="task-1"):
    db = session_factory()
    try:
        return db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first() is not None
    finally:
        db.close()


def _get_task(session_factory, task_id):
    db = session_factory()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            return None
        return {
            "schedule": task.schedule,
            "cron_expression": task.cron_expression,
            "next_run": task.next_run,
        }
    finally:
        db.close()


def test_manage_tasks_delete_requires_confirmation(tmp_path, monkeypatch):
    session_factory = _isolated_task_db(tmp_path, monkeypatch)
    _seed_task(session_factory)

    result = asyncio.run(do_manage_tasks(
        json.dumps({"action": "delete", "task_id": "task-1"}),
        owner="alice",
    ))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert _task_exists(session_factory)


def test_manage_tasks_delete_runs_after_confirmation(tmp_path, monkeypatch):
    session_factory = _isolated_task_db(tmp_path, monkeypatch)
    _seed_task(session_factory)

    result = asyncio.run(do_manage_tasks(
        json.dumps({"action": "delete", "task_id": "task-1", "confirmed": True}),
        owner="alice",
    ))

    assert result["exit_code"] == 0
    assert "Deleted task" in result["response"]
    assert not _task_exists(session_factory)


def test_manage_tasks_creates_weekday_reminder_as_single_cron_task(tmp_path, monkeypatch):
    session_factory = _isolated_task_db(tmp_path, monkeypatch)

    result = asyncio.run(do_manage_tasks(
        json.dumps({
            "action": "create",
            "name": "Weekday reminder",
            "prompt": "Remind me to check the planning board.",
            "task_type": "llm",
            "trigger_type": "schedule",
            "schedule": "cron",
            "cron_expression": "0 9 * * 1-5",
            "output_target": "telegram",
        }),
        owner="alice",
    ))

    assert result["exit_code"] == 0
    task = _get_task(session_factory, result["task_id"])
    assert task["schedule"] == "cron"
    assert task["cron_expression"] == "0 9 * * 1-5"
    assert task["next_run"] is not None
