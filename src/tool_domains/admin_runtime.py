"""Task and endpoint admin agent tool implementations."""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from src.tool_domains.common import _parse_tool_args
from src.tool_domains.admin_common import _INTERNAL_BASE, _internal_headers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task management tool
# ---------------------------------------------------------------------------

async def do_manage_tasks(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_tasks tool calls: CRUD on scheduled tasks."""
    import uuid as _uuid
    from core.database import SessionLocal, ScheduledTask
    from src.task_scheduler import compute_next_run

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Task {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    db = SessionLocal()
    try:
        if action == "list":
            q = db.query(ScheduledTask)
            if owner:
                q = q.filter(ScheduledTask.owner == owner)
            tasks = q.order_by(ScheduledTask.created_at.desc()).all()
            task_list = []
            for t in tasks:
                task_list.append({
                    "id": t.id, "name": t.name, "status": t.status,
                    "task_type": t.task_type or "llm",
                    "action": t.action,
                    "trigger_type": t.trigger_type or "schedule",
                    "schedule": t.schedule,
                    "cron_expression": t.cron_expression,
                    "trigger_event": t.trigger_event,
                    "trigger_count": t.trigger_count,
                    "next_run": t.next_run.isoformat() + "Z" if t.next_run else None,
                    "last_run": t.last_run.isoformat() + "Z" if t.last_run else None,
                    "run_count": t.run_count or 0,
                })
            return {"response": f"Found {len(task_list)} tasks", "tasks": task_list, "exit_code": 0}

        elif action == "create":
            task_type = args.get("task_type", "llm")
            trigger_type = args.get("trigger_type", "schedule")

            if task_type in ("llm", "research") and not args.get("prompt"):
                return {"error": "Prompt is required for llm/research tasks", "exit_code": 1}
            if task_type == "action" and not args.get("action_name"):
                return {"error": "action_name is required for action tasks", "exit_code": 1}

            # Compute next_run for schedule triggers
            next_run = None
            if trigger_type == "schedule":
                schedule = args.get("schedule", "daily")
                next_run = compute_next_run(
                    schedule, args.get("scheduled_time", "09:00"),
                    args.get("scheduled_day"),
                    cron_expression=args.get("cron_expression"),
                )

            task_id = str(_uuid.uuid4())
            # Guard each fallback with `or`: args.get("prompt", default) returns
            # None when the key is present but null, and None[:50] raises.
            name = args.get("name") or (args.get("prompt") or args.get("action_name") or "Task")[:50]

            task = ScheduledTask(
                id=task_id,
                owner=owner,
                name=name,
                prompt=args.get("prompt"),
                task_type=task_type,
                action=args.get("action_name"),
                schedule=args.get("schedule") if trigger_type == "schedule" else None,
                scheduled_time=args.get("scheduled_time", "09:00") if trigger_type == "schedule" else None,
                scheduled_day=args.get("scheduled_day"),
                cron_expression=args.get("cron_expression"),
                trigger_type=trigger_type,
                trigger_event=args.get("trigger_event"),
                trigger_count=args.get("trigger_count"),
                trigger_counter=0,
                next_run=next_run,
                status="active",
                output_target=args.get("output_target", "session"),
            )
            db.add(task)
            db.commit()
            return {"response": f"Created task '{name}' (id: {task_id})", "task_id": task_id, "exit_code": 0}

        elif action == "edit":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for edit", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            changed = []
            for field in ("name", "prompt", "output_target"):
                if args.get(field) is not None:
                    setattr(task, field, args[field])
                    changed.append(field)
            if args.get("task_type") is not None:
                task.task_type = args["task_type"]
                changed.append("task_type")
            if args.get("action_name") is not None:
                task.action = args["action_name"]
                changed.append("action")
            if args.get("trigger_type") is not None:
                task.trigger_type = args["trigger_type"]
                changed.append("trigger_type")
            if args.get("trigger_event") is not None:
                task.trigger_event = args["trigger_event"]
                changed.append("trigger_event")
            if args.get("trigger_count") is not None:
                task.trigger_count = args["trigger_count"]
                changed.append("trigger_count")

            schedule_changed = False
            for field in ("schedule", "scheduled_time", "scheduled_day", "cron_expression"):
                if args.get(field) is not None:
                    setattr(task, field, args[field])
                    changed.append(field)
                    schedule_changed = True

            if schedule_changed and (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time, task.scheduled_day,
                    cron_expression=task.cron_expression,
                )

            db.commit()
            return {"response": f"Updated task '{task.name}': {', '.join(changed)}", "exit_code": 0}

        elif action == "delete":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for delete", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required("delete")
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}
            name = task.name
            db.delete(task)
            db.commit()
            return {"response": f"Deleted task '{name}'", "exit_code": 0}

        elif action in ("pause", "resume"):
            task_id = args.get("task_id")
            if not task_id:
                return {"error": f"task_id is required for {action}", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            if action == "pause":
                task.status = "paused"
            else:
                task.status = "active"
                if (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(
                        task.schedule, task.scheduled_time, task.scheduled_day,
                        cron_expression=task.cron_expression,
                    )
            db.commit()
            return {"response": f"Task '{task.name}' {action}d", "exit_code": 0}

        elif action == "run":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for run", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            from src.event_bus import get_task_scheduler
            scheduler = get_task_scheduler()
            if scheduler:
                started = await scheduler.run_task_now(task_id)
                if started:
                    return {"response": f"Task '{task.name}' triggered", "exit_code": 0}
                else:
                    return {"error": "Task is already running", "exit_code": 1}
            return {"error": "Task scheduler not available", "exit_code": 1}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}

    except Exception as e:
        logger.error(f"manage_tasks error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoint management tool
# ---------------------------------------------------------------------------

async def do_manage_endpoints(content: str, owner: Optional[str] = None) -> Dict:
    """Manage model endpoints through the same admin routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Confirmation required before endpoint {target}. Repeat with confirmed=true after explicit user confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "action": action,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Endpoint route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/model-endpoints", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            items = resp.json() or []
            return {"response": f"{len(items)} endpoints", "endpoints": items, "exit_code": 0}

        elif action == "add":
            name = args.get("name", "")
            base_url = args.get("base_url", "")
            if not base_url:
                return {"error": "base_url is required", "exit_code": 1}
            if args.get("api_key"):
                return {
                    "response": "Endpoint API keys must be entered through secure UI handoff, not chat text.",
                    "status": "secret_handoff_required",
                    "secret_handoff_required": True,
                    "exit_code": 0,
                }
            if not _confirmed():
                return _confirmation_required("add")
            pinned_models = args.get("pinned_models", "")
            if isinstance(pinned_models, (list, dict)):
                pinned_models = json.dumps(pinned_models)
            data = {
                "name": name,
                "base_url": base_url,
                "skip_probe": str(args.get("skip_probe", "false")).lower(),
                "require_models": str(args.get("require_models", "false")).lower(),
                "model_type": args.get("model_type", "llm"),
                "endpoint_kind": args.get("endpoint_kind", "auto"),
                "model_refresh_mode": args.get("model_refresh_mode", ""),
                "model_refresh_interval": str(args.get("model_refresh_interval", "")),
                "model_refresh_timeout": str(args.get("model_refresh_timeout", "")),
                "supports_tools": "" if args.get("supports_tools") is None else str(args.get("supports_tools")).lower(),
                "pinned_models": pinned_models,
                "container_local": str(args.get("container_local", "false")).lower(),
                "shared": str(args.get("shared", "true")).lower(),
            }
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/model-endpoints", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            endpoint = resp.json() or {}
            return {
                "response": f"Added endpoint '{endpoint.get('name') or name or base_url}' (id: {endpoint.get('id')}).",
                "endpoint": endpoint,
                "exit_code": 0,
            }

        elif action == "delete":
            eid = args.get("endpoint_id", "")
            if not eid:
                return {"error": "endpoint_id is required", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required("delete")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/model-endpoints/{eid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Deleted endpoint {eid}", "result": result, "exit_code": 0}

        elif action in ("enable", "disable", "update"):
            eid = args.get("endpoint_id", "")
            if not eid:
                return {"error": "endpoint_id is required", "exit_code": 1}
            body: Dict[str, Any] = {}
            if action in ("enable", "disable"):
                body["is_enabled"] = action == "enable"
            else:
                for field in (
                    "name",
                    "base_url",
                    "model_type",
                    "pinned_models",
                    "endpoint_kind",
                    "model_refresh_mode",
                    "model_refresh_interval",
                    "model_refresh_timeout",
                    "supports_tools",
                ):
                    if field in args:
                        body[field] = args[field]
                if args.get("api_key"):
                    return {
                        "response": "Endpoint API keys must be rotated through secure UI handoff, not chat text.",
                        "status": "secret_handoff_required",
                        "secret_handoff_required": True,
                        "exit_code": 0,
                    }
                if not body:
                    return {"error": "No update fields supplied", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required(action)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(f"{_INTERNAL_BASE}/api/model-endpoints/{eid}", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            endpoint = resp.json() or {}
            return {
                "response": f"Endpoint '{endpoint.get('name') or eid}' updated.",
                "endpoint": endpoint,
                "exit_code": 0,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_endpoints error: {e}")
        return {"error": str(e), "exit_code": 1}


