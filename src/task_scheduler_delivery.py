"""Delivery helper functions for scheduled task outputs."""

import logging
import re

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


async def deliver_user_notification_for_task(task, result: str) -> dict:
    from src.user_notification_delivery import deliver_user_notification

    payload = {
        "event": "scheduled_task",
        "message": result or f"Task {getattr(task, 'name', '')} completed.",
        "severity": "success",
        "channel": "telegram",
        "dry_run": True,
        "metadata": {
            "task_id": str(getattr(task, "id", "") or ""),
            "task_name": str(getattr(task, "name", "") or "")[:80],
        },
    }
    decision = await deliver_user_notification(payload)
    logger.info(
        "Task %s notification delivery via telegram: status=%s reason=%s",
        getattr(task, "id", ""),
        decision.get("delivery_status") or decision.get("status"),
        decision.get("reason"),
    )
    return decision


async def deliver_via_mcp(tool_name: str, task, result: str) -> None:
    """Send a task result via an MCP tool such as Gmail send."""
    from src.tool_utils import get_mcp_manager

    mcp = get_mcp_manager()
    if not mcp:
        logger.warning(f"Task {task.id}: MCP manager not available for delivery")
        return

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
        else:
            logger.info(
                f"Task {task.id} delivered via MCP tool {tool_name} "
                f"(recipient_set={bool(recipient)}, body={body_len}b, reply={stdout[:200]!r})"
            )
    except Exception as exc:
        logger.error(f"Task {task.id} MCP delivery failed: {exc}")
