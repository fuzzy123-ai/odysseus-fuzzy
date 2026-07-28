"""Telegram parsing boundary for non-executing security incident controls."""

from __future__ import annotations

import re
from typing import Any


_ACTION_ID = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")


def parse_security_incident_command(text: Any) -> dict[str, str] | None:
    """Accept exactly ``/incident <approve|deny|expire> <action-id>``.

    The Telegram surface has no browser session or TOTP step-up.  Parsing a
    command therefore never authorizes a lifecycle write or a reply dispatch.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split()
    if len(parts) != 3 or parts[0].split("@", 1)[0].lower() != "/incident":
        return None
    operation, action_id = parts[1].lower(), parts[2].lower()
    if operation not in {"approve", "deny", "expire"} or not _ACTION_ID.fullmatch(action_id):
        return None
    return {"operation": operation, "action_id": action_id}


def command_key(command: dict[str, str] | None) -> str:
    if not isinstance(command, dict):
        return ""
    operation = command.get("operation")
    action_id = command.get("action_id")
    if operation not in {"approve", "deny", "expire"} or not isinstance(action_id, str) or not _ACTION_ID.fullmatch(action_id):
        return ""
    return f"security_incident_{operation}:{action_id}"


def handle_security_incident_command(command: str) -> dict[str, Any] | None:
    """Return an action-ID-only block; Telegram cannot satisfy browser step-up."""
    raw = str(command or "")
    if raw == "security_incident_control":
        return {
            "status": "security_action_browser_step_up_required",
            "delivery_performed": False,
            "executed": False,
            "raw_content_visible": False,
        }
    prefix, separator, action_id = raw.partition(":")
    if not separator or prefix not in {"security_incident_approve", "security_incident_deny", "security_incident_expire"} or not _ACTION_ID.fullmatch(action_id):
        return None
    return {
        "status": "security_action_browser_step_up_required",
        "action_id": action_id,
        "delivery_performed": False,
        "executed": False,
        "raw_content_visible": False,
    }


__all__ = ["command_key", "handle_security_incident_command", "parse_security_incident_command"]
