"""Compact redacted task summaries for chat/status surfaces."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from core.database import ScheduledTask, SessionLocal
from src.auth_helpers import require_user
from src.calendar_capability_service import build_telegram_todo_digest_live_gate
from src.task_summary import summarize_tasks


def setup_task_summary_routes() -> APIRouter:
    router = APIRouter(prefix="/api/tasks", tags=["tasks"])

    @router.get("/summary")
    async def task_summary(
        request: Request,
        status: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        user = require_user(request)
        db = SessionLocal()
        try:
            query = db.query(ScheduledTask)
            if user:
                query = query.filter(ScheduledTask.owner == user)
            if status:
                query = query.filter(ScheduledTask.status == status)
            tasks = query.order_by(ScheduledTask.created_at.desc()).limit(limit).all()
            payload = summarize_tasks(tasks)
            payload["owner_scoped"] = bool(user)
            payload["limit"] = limit
            return payload
        finally:
            db.close()

    @router.get("/reminder-live-gate")
    async def reminder_live_gate(
        request: Request,
        scheduled_time: str = Query("09:00", pattern=r"^\d{1,2}:\d{2}$"),
        weekdays: str = Query("mo-fr", max_length=80),
    ):
        user = require_user(request)
        return build_telegram_todo_digest_live_gate(
            owner=user,
            scheduled_time=scheduled_time,
            weekdays=weekdays,
        )

    return router
