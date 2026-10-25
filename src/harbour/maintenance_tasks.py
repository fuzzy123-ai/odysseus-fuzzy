"""Harbour Maintenance Tasks.

Provides background loops and warm-up procedures decoupled from app.py:
- Tool index pre-warmup
- LLM endpoint warmup and keepalive ping loop
- Default tasks reconciliation
- Legacy skill owner backfill
- Null-owner periodic sweep
- Nightly skill audit
- Obsidian vault trash purge
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("uvicorn.error")


async def warmup_tool_index() -> None:
    """Pre-warm the RAG tool index off the request path."""
    try:
        from src.tool_index import get_tool_index
        idx = await asyncio.to_thread(get_tool_index)
        if idx:
            await asyncio.to_thread(idx.get_tools_for_query, "warmup", 8)
            logger.info("[startup] Tool index pre-warmed")
    except Exception as e:
        logger.warning(f"Tool index warmup failed (non-critical): {type(e).__name__}: {e}")


async def warmup_endpoints(model_discovery: Any = None) -> None:
    """Ping all known LLM endpoints to prime connections."""
    try:
        import httpx
        urls = (
            await asyncio.to_thread(model_discovery.warmup_ping_urls)
            if model_discovery else []
        )
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.get(url)
                logger.info(f"Warmup ping OK: {url}")
            except Exception as e:
                logger.debug(f"Warmup ping failed for endpoint: {e}")
    except Exception as e:
        logger.debug(f"Warmup ping skipped: {e}")


async def keepalive_loop(model_discovery: Any = None) -> None:
    """Ping endpoints periodically to prevent cold starts."""
    while True:
        try:
            await asyncio.sleep(60)
            await warmup_endpoints(model_discovery)
        except Exception as e:
            logger.warning(f"Keepalive loop error: {e}")
            await asyncio.sleep(300)


async def ensure_default_tasks(task_scheduler: Any, auth_file: str | Path | None = None) -> None:
    """Create/reconcile default automation tasks and personal assistant for every user."""
    if task_scheduler is None:
        return
    owners: set[str] = set()
    try:
        import json as _json
        from src.config import AUTH_FILE
        auth_path = auth_file or AUTH_FILE
        with open(auth_path, encoding="utf-8") as f:
            users = _json.load(f).get("users", {})
        owners.update(users.keys())
    except Exception as e:
        logger.debug(f"Default task auth-owner scan: {e}")

    try:
        from core.database import SessionLocal, ScheduledTask
        from src.task_scheduler import HOUSEKEEPING_DEFAULTS
        builtin_names = []
        for defs in HOUSEKEEPING_DEFAULTS.values():
            builtin_names.append(defs["name"])
            builtin_names.extend(defs.get("legacy_names") or [])
        db_seed = SessionLocal()
        try:
            rows = db_seed.query(ScheduledTask.owner).filter(
                (ScheduledTask.action.in_(list(HOUSEKEEPING_DEFAULTS.keys())))
                | (ScheduledTask.name.in_(builtin_names))
            ).distinct().all()
            owners.update(row[0] for row in rows if row[0])
        finally:
            db_seed.close()
    except Exception as e:
        logger.debug(f"Default task existing-owner scan: {e}")

    try:
        for uname in sorted(owners):
            try:
                await task_scheduler.ensure_defaults(uname)
            except Exception as e:
                logger.debug(f"ensure_defaults({uname}): {e}")
    except Exception as e:
        logger.debug(f"Default tasks: {e}")


def backfill_legacy_skill_owners(skills_manager: Any, auth_file: str | Path | None = None) -> None:
    """Repair ownerless or deleted/test-owner SKILL.md files on disk."""
    if skills_manager is None:
        return
    try:
        import json as _json
        from src.config import AUTH_FILE
        auth_path = auth_file or AUTH_FILE
        with open(auth_path, encoding="utf-8") as f:
            users = _json.load(f).get("users", {})
        primary_owner = None
        for uname, udata in users.items():
            if udata.get("is_admin") is True:
                primary_owner = uname
                break
        if not primary_owner and users:
            primary_owner = next(iter(users))
        if primary_owner:
            changed = skills_manager.backfill_owner(primary_owner, set(users.keys()))
            if changed:
                logger.info("Assigned %s legacy skill file(s) to %s", changed, primary_owner)
    except Exception as e:
        logger.debug(f"Skill owner backfill skipped: {e}")


async def null_owner_sweep_loop() -> None:
    """Periodic null-owner sweep — re-runs legacy-owner assignment hourly."""
    while True:
        try:
            await asyncio.sleep(3600)
            from core.database import _migrate_assign_legacy_owner
            await asyncio.to_thread(_migrate_assign_legacy_owner)
        except Exception as e:
            logger.debug(f"Null-owner sweep skipped: {e}")
            await asyncio.sleep(3600)


async def skill_audit_nightly_loop(skills_manager: Any = None) -> None:
    """Nightly skill audit rotating through library batches at configured hour."""
    while True:
        try:
            from src.settings import get_setting
            hour = int(get_setting("skill_audit_hour", 2) or 2)
        except Exception:
            hour = 2
        now = datetime.now()
        nxt = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep(max(60, (nxt - now).total_seconds()))
        try:
            from src.settings import get_setting
            if not get_setting("skill_audit_nightly", True):
                continue
            batch = int(get_setting("skill_audit_batch", 8) or 8)
            from routes.skills_routes import run_scheduled_skill_audit
            if skills_manager is not None:
                await run_scheduled_skill_audit(skills_manager, owner=None, max_skills=batch)
        except Exception as e:
            logger.warning(f"Nightly skill audit failed: {e}")


async def vault_trash_purge_loop() -> None:
    """Daily vault trash purge — deletes .trash/ entries older than TRASH_RETENTION_DAYS."""
    while True:
        await asyncio.sleep(86400)
        try:
            from plugins.obsidian.backend.vault_service import purge_all_vault_trash, TRASH_RETENTION_DAYS
            result = await asyncio.to_thread(purge_all_vault_trash)
            if result.get("purged", 0) > 0:
                logger.info(
                    f"Vault trash purge: {result['purged']} date-dirs removed "
                    f"({result.get('errors', 0)} errors) across {result.get('vaults', 0)} vaults "
                    f"(retention={TRASH_RETENTION_DAYS}d)."
                )
        except Exception as e:
            logger.debug(f"Vault trash purge skipped: {e}")


def schedule_background_maintenance_tasks(
    startup_tasks: list[asyncio.Task],
    *,
    startup_offline: bool,
    model_discovery: Any = None,
    skills_manager: Any = None,
) -> None:
    """Schedule ongoing background maintenance loops into startup_tasks."""
    if not startup_offline:
        startup_tasks.append(asyncio.create_task(warmup_tool_index()))
        startup_tasks.append(asyncio.create_task(warmup_endpoints(model_discovery)))
        startup_tasks.append(asyncio.create_task(keepalive_loop(model_discovery)))

    startup_tasks.append(asyncio.create_task(null_owner_sweep_loop()))
    startup_tasks.append(asyncio.create_task(skill_audit_nightly_loop(skills_manager)))
    startup_tasks.append(asyncio.create_task(vault_trash_purge_loop()))


__all__ = [
    "warmup_tool_index",
    "warmup_endpoints",
    "keepalive_loop",
    "ensure_default_tasks",
    "backfill_legacy_skill_owners",
    "null_owner_sweep_loop",
    "skill_audit_nightly_loop",
    "vault_trash_purge_loop",
    "schedule_background_maintenance_tasks",
]
