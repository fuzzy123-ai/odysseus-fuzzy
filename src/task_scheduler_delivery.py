"""Delivery helper functions for scheduled task outputs."""

import logging
import re

from src.runtime_event_envelope import build_runtime_event, stable_payload_hash
from src.todo_digest_formatting import format_todo_digest_notification_body

logger = logging.getLogger(__name__)

_MOJIBAKE_INBOX = "\u00f0\u0178\u201c\u00ac"
_MOJIBAKE_REPLY = "\u00e2\u2020\u00a9"
_MOJIBAKE_PAPERCLIP = "\u00f0\u0178\u201c\u017d"
_MOJIBAKE_BLUE = "\u00f0\u0178\u201d\u00b5"
_EMAIL_MARKER_RE = rf"(?:\u21a9\s*|\U0001f4ce\s*|\U0001f535\s*|{_MOJIBAKE_REPLY}\s*|{_MOJIBAKE_PAPERCLIP}\s*|{_MOJIBAKE_BLUE}\s*|\s*)"


def format_email_output(raw: str) -> str:
    """Clean up raw MCP email list output into readable format."""
    lines = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip header lines like "Inbox: 856 emails..."
        if line.startswith(("\U0001f4ec", _MOJIBAKE_INBOX, "No emails", "---", "Page ")):
            continue
        # Skip "more pages available" etc.
        if "page" in line.lower() and "/" in line:
            continue
        # Parse: [1778] Re: Subject From: Name | Date
        m = re.match(rf'\[?\d+\]?\s*{_EMAIL_MARKER_RE}?(.+?)(?:\s*From:\s*(.+?))?(?:\s*\|\s*(\S+))?$', line)
        if m:
            subject = m.group(1).strip().rstrip('|').strip()
            sender = (m.group(2) or "").strip().rstrip('|').strip()
            if sender:
                lines.append(f"- {sender} \u2014 {subject}")
            else:
                lines.append(f"- {subject}")
        elif line.startswith("[") or line.startswith("-"):
            cleaned = re.sub(rf'^\[?\d+\]?\s*(?:\u21a9\s*|\U0001f4ce\s*|{_MOJIBAKE_REPLY}\s*|{_MOJIBAKE_PAPERCLIP}\s*)?', '', line.lstrip('- '))
            if cleaned.strip():
                lines.append(f"- {cleaned.strip()}")
    if not lines:
        return "No unread emails"
    return "\n".join(lines[:10])


def is_email_output_target(output: str) -> bool:
    target = (output or "").strip()
    if target in {"email", "email:self"}:
        return True
    if target.startswith("email:"):
        return True
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target))


def is_user_notification_output_target(output: str) -> bool:
    return (output or "").strip().lower() in {"telegram", "notification:telegram"}


def _is_todo_digest_task(task, result: str) -> bool:
    markers = (
        getattr(task, "action", ""),
        getattr(task, "action_name", ""),
        getattr(task, "name", ""),
        result,
    )
    for marker in markers:
        normalized = " ".join(str(marker or "").strip().lower().split())
        if (
            normalized == "todo_digest"
            or normalized.startswith("todo digest")
            or "todo_digest: todo digest" in normalized
            or "scheduled_task: todo digest" in normalized
        ):
            return True
    return False


async def deliver_user_notification_for_task(task, result: str) -> dict:
    from src.user_notification_delivery import deliver_user_notification

    task_ref = _task_ref(task)
    is_todo_digest = _is_todo_digest_task(task, result or "")
    message = format_todo_digest_notification_body(result or "") if is_todo_digest else result
    payload = {
        "event": "todo_digest" if is_todo_digest else "scheduled_task",
        "message": message or f"Task {getattr(task, 'name', '')} completed.",
        "severity": "success",
        "channel": "telegram",
        "dry_run": False,
        "render_mode": "plain" if is_todo_digest else "standard",
        "metadata": {
            "task_id": task_ref,
        },
    }
    decision = await deliver_user_notification(payload)
    runtime_event = _delivery_runtime_event(
        task,
        delivery_target="telegram",
        delivery_status=str(decision.get("delivery_status") or decision.get("status") or "unknown"),
        reason=str(decision.get("reason") or ""),
    )
    decision = dict(decision)
    decision["correlation_id"] = runtime_event["correlation_id"]
    decision["runtime_event"] = runtime_event
    logger.info(
        "Task %s notification delivery via telegram: status=%s reason=%s",
        getattr(task, "id", ""),
        decision.get("delivery_status") or decision.get("status"),
        decision.get("reason"),
    )
    return decision


async def deliver_via_mcp(tool_name: str, task, result: str) -> dict:
    """Send a task result via an MCP tool such as Gmail send."""
    from src.tool_utils import get_mcp_manager

    safe_tool = _safe_tool_ref(tool_name)
    mcp = get_mcp_manager()
    if not mcp:
        logger.warning(f"Task {task.id}: MCP manager not available for delivery")
        return {
            "status": "blocked",
            "reason": "mcp_manager_unavailable",
            "correlation_id": _correlation_id(task),
            "runtime_event": _delivery_runtime_event(
                task,
                delivery_target="mcp",
                delivery_status="blocked",
                reason="mcp_manager_unavailable",
                tool_ref=safe_tool,
            ),
        }

    recipient = None
    try:
        from routes.email_helpers import _get_email_config

        cfg = _get_email_config() or {}
        recipient = cfg.get("from_address") or None
    except Exception as exc:
        logger.debug(f"_deliver_via_mcp: email config lookup failed: {exc}")
    if not recipient and task.owner and "@" in str(task.owner):
        recipient = task.owner

    args = {
        "subject": f"[Task] {task.name}",
        "body": result,
        "headers": {
            "X-Odysseus-Origin": "odysseus-ui",
            "X-Odysseus-Kind": "task",
            "X-Odysseus-Ref": str(task.id),
        },
    }
    if recipient:
        args["to"] = recipient
        args["recipient"] = recipient
        args["email"] = recipient
        args["address"] = recipient
    else:
        logger.warning(
            f"Task {task.id}: no recipient resolved for MCP delivery via {tool_name} - "
            "set an email From address in Settings or give the task an owner email."
        )
    try:
        mcp_result = await mcp.call_tool(tool_name, args)
        stderr = mcp_result.get("stderr", "")
        stdout = mcp_result.get("stdout", "")
        body_len = len(result or "")
        exit_code = mcp_result.get("exit_code", 0)
        if exit_code != 0:
            logger.warning(
                f"Task {task.id} MCP delivery FAILED via {tool_name}: "
                f"exit={exit_code} stderr={stderr[:400]!r} stdout={stdout[:400]!r}"
            )
            status = "failed"
            reason = "mcp_tool_exit_nonzero"
        else:
            logger.info(
                f"Task {task.id} delivered via MCP tool {tool_name} "
                f"(recipient_set={bool(recipient)}, body={body_len}b, reply={stdout[:200]!r})"
            )
            status = "success"
            reason = "mcp_tool_delivered"
        runtime_event = _delivery_runtime_event(
            task,
            delivery_target="mcp",
            delivery_status=status,
            reason=reason,
            tool_ref=safe_tool,
        )
        return {
            "status": status,
            "reason": reason,
            "exit_code": exit_code,
            "recipient_set": bool(recipient),
            "body_chars": body_len,
            "correlation_id": runtime_event["correlation_id"],
            "runtime_event": runtime_event,
        }
    except Exception as exc:
        logger.error(f"Task {task.id} MCP delivery failed: {exc}")
        runtime_event = _delivery_runtime_event(
            task,
            delivery_target="mcp",
            delivery_status="failed",
            reason=exc.__class__.__name__,
            tool_ref=safe_tool,
            error_class=exc.__class__.__name__,
        )
        return {
            "status": "failed",
            "reason": exc.__class__.__name__,
            "correlation_id": runtime_event["correlation_id"],
            "runtime_event": runtime_event,
        }


def _task_ref(task) -> str:
    return stable_payload_hash(str(getattr(task, "id", "") or "unknown"))


def _correlation_id(task) -> str:
    return _task_ref(task)


def _safe_tool_ref(tool_name: str) -> str:
    tool = re.sub(r"[^A-Za-z0-9_.:@/-]+", "_", str(tool_name or "mcp_tool"))[:120]
    return tool or "mcp_tool"


def _delivery_runtime_status(delivery_status: str) -> str:
    normalized = str(delivery_status or "").strip().lower()
    if normalized in {"success", "sent", "delivered", "dispatched", "ok"}:
        return "success"
    if normalized in {"queued", "pending"}:
        return "queued"
    if normalized in {"blocked", "denied"}:
        return "blocked"
    if normalized in {"failed", "failure", "error"}:
        return "failed"
    if normalized in {"dry_run", "dry-run"}:
        return "dry_run"
    return "unknown"


def _delivery_runtime_event(
    task,
    *,
    delivery_target: str,
    delivery_status: str,
    reason: str = "",
    tool_ref: str = "",
    error_class: str = "",
) -> dict:
    metadata = {
        "delivery_target": delivery_target,
        "delivery_status": _delivery_runtime_status(delivery_status),
        "reason": _safe_tool_ref(reason) if reason else "",
    }
    if tool_ref:
        metadata["tool_ref"] = tool_ref
    return build_runtime_event(
        surface="scheduler",
        component="task_delivery",
        event_type="scheduled_task_delivery",
        status=_delivery_runtime_status(delivery_status),
        severity="error" if _delivery_runtime_status(delivery_status) in {"failed", "blocked"} else "info",
        owner=getattr(task, "owner", None),
        correlation_id=_correlation_id(task),
        privacy_level="private_metadata",
        task_id=_task_ref(task),
        error_class=error_class,
        side_effects=(delivery_target,),
        metadata=metadata,
    )
