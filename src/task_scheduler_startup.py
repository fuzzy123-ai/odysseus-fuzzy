"""Startup housekeeping helpers for :mod:`src.task_scheduler`."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Dict

from src.task_scheduler_helpers import _utcnow

logger = logging.getLogger(__name__)


def clear_stale_task_runs_on_startup() -> None:
    """Abort queued/running task runs left behind by a prior process exit."""
    try:
        from core.database import SessionLocal, TaskRun

        db = SessionLocal()
        try:
            stale = db.query(TaskRun).filter(
                TaskRun.status.in_(("running", "queued"))
            ).all()
            if stale:
                now = _utcnow()
                for run in stale:
                    old_status = run.status or "running"
                    run.status = "aborted"
                    run.error = "Server restarted while task was " + old_status
                    run.finished_at = now
                db.commit()
                logger.info("Cleared %d stale task_runs from previous run", len(stale))
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Could not clear stale task_runs on startup: %s", exc)


def advance_overdue_tasks_on_startup() -> None:
    """Push active overdue tasks forward once so restart cannot double-fire them."""
    try:
        from core.database import SessionLocal, ScheduledTask

        db = SessionLocal()
        try:
            now = _utcnow()
            overdue = db.query(ScheduledTask).filter(
                ScheduledTask.status == "active",
                ScheduledTask.next_run.isnot(None),
                ScheduledTask.next_run < now,
            ).all()
            if overdue:
                for task in overdue:
                    task.next_run = now + timedelta(seconds=60)
                db.commit()
                logger.info(
                    "Pushed next_run forward by 60s for %d overdue active tasks on startup",
                    len(overdue),
                )
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Could not advance overdue next_run on startup: %s", exc)


def dedupe_default_assistants_on_startup() -> None:
    """Keep one default assistant per owner and remove orphaned duplicate tasks."""
    try:
        from core.database import SessionLocal, CrewMember, ScheduledTask
        from sqlalchemy import func

        db = SessionLocal()
        try:
            groups = db.query(CrewMember.owner, func.count(CrewMember.id).label("n")).filter(
                CrewMember.is_default_assistant == True,  # noqa: E712
            ).group_by(CrewMember.owner).having(func.count(CrewMember.id) > 1).all()
            for owner, count in groups:
                rows = db.query(CrewMember).filter(
                    CrewMember.owner == owner,
                    CrewMember.is_default_assistant == True,  # noqa: E712
                ).order_by(CrewMember.created_at.asc()).all()
                keep = rows[0]
                losers = rows[1:]
                loser_ids = [row.id for row in losers]
                orphan_tasks = db.query(ScheduledTask).filter(
                    ScheduledTask.crew_member_id.in_(loser_ids)
                ).delete(synchronize_session=False)
                for row in losers:
                    db.delete(row)
                db.commit()
                logger.warning(
                    "Default-assistant dedupe: owner=%r had %d rows, kept %s, "
                    "dropped %d crew + %d orphan tasks",
                    owner, count, keep.id, len(losers), orphan_tasks,
                )
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Could not dedupe default-assistant rows on startup: %s", exc)


def audit_schedule_clusters_on_startup() -> None:
    """Log active scheduled-task minute buckets with more than one task."""
    try:
        from core.database import SessionLocal, ScheduledTask

        db = SessionLocal()
        try:
            rows = db.query(ScheduledTask).filter(
                ScheduledTask.status == "active",
                ScheduledTask.trigger_type == "schedule",
                ScheduledTask.next_run.isnot(None),
            ).all()
            buckets: Dict[str, list] = {}
            for row in rows:
                if not row.next_run:
                    continue
                key = row.next_run.strftime("%H:%M")
                buckets.setdefault(key, []).append(row.name or row.id)
            clusters = {key: value for key, value in buckets.items() if len(value) > 1}
            if clusters:
                summary = ", ".join(
                    f"{key} ({len(value)})" for key, value in sorted(clusters.items())
                )
                logger.info("Task scheduling clusters (>1 task/minute): %s", summary)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("Cluster audit skipped: %s", exc)
