"""
tool_implementations.py

Extracted tool implementation functions (do_* and helpers) from agent_tools.py.
These handle the actual execution logic for each tool type.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from src.constants import MAX_READ_CHARS, DEEP_RESEARCH_DIR, VAULT_FILE
from src.tool_utils import get_mcp_manager
from core.constants import internal_api_base
from routes._validators import validate_remote_host, validate_ssh_port
from src.tool_domains.common import _parse_tool_args
from src.tool_domains.repo_skills import (
    do_manage_repos,
    do_manage_skills,
    do_recent_changes,
    do_search_chats,
)
from src.tool_domains.github_issues import do_manage_github_issues
from src.tool_domains.personal_workspace import (
    do_manage_calendar,
    do_manage_notes,
)
from src.tool_domains.admin_config import (
    _validate_mcp_command,
    do_manage_assistant,
    do_manage_embeddings,
    do_manage_endpoints,
    do_manage_mcp,
    do_manage_personal_docs,
    do_manage_plugins,
    do_manage_presets,
    do_manage_settings,
    do_manage_tasks,
    do_manage_tokens,
    do_manage_webhooks,
)
from src.tool_domains.app_api import (
    _APP_API_BLOCKLIST_PREFIXES,
    _APP_API_BLOCKLIST_METHOD_PATH,
    _INTERNAL_BASE,
    _internal_headers,
    do_app_api,
)
from src.tool_domains.cookbook_models import (
    _validate_cookbook_ssh_target,
    do_adopt_served_model,
    do_cancel_download,
    do_download_model,
    do_list_cached_models,
    do_list_cookbook_servers,
    do_list_downloads,
    do_list_serve_presets,
    do_list_served_models,
    do_search_hf_models,
    do_serve_model,
    do_serve_preset,
    do_stop_served_model,
    do_tail_serve_output,
)
from src.tool_domains.media_research_contacts import (
    do_edit_image,
    do_manage_contact,
    do_manage_research,
    do_resolve_contact,
    do_trigger_research,
)
from src.tool_domains.vault import (
    _load_vault_config,
    do_vault_get,
    do_vault_search,
    do_vault_unlock,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Active email state
# ---------------------------------------------------------------------------

# When the user has an email reader window open, the frontend tells the
# backend about it on each chat submit. Email tools can resolve "this email"
# without guessing a UID. Cleared between requests by chat_routes.
_active_email_ref: Optional[Dict[str, str]] = None


def set_active_email(uid: Optional[str], folder: Optional[str] = None, account: Optional[str] = None,
                     subject: Optional[str] = None, sender: Optional[str] = None) -> None:
    """Stash the email currently open in the UI. None clears it."""
    global _active_email_ref
    if not uid:
        _active_email_ref = None
        return
    _active_email_ref = {
        "uid": str(uid),
        "folder": str(folder or "INBOX"),
        "account": str(account or ""),
        "subject": str(subject or ""),
        "from": str(sender or ""),
    }


def get_active_email() -> Optional[Dict[str, str]]:
    return _active_email_ref


def clear_active_email() -> None:
    global _active_email_ref
    _active_email_ref = None

# ---------------------------------------------------------------------------
# Tool domains imported above; remaining domains stay local until their R7 slice.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Admin/config, App API and Cookbook tool domains imported above.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API call tool
# ---------------------------------------------------------------------------

async def do_api_call(content: str) -> Dict:
    """Execute an API call to a registered integration."""
    from src.integrations import execute_api_call, load_integrations
    try:
        args = json.loads(content)
    except json.JSONDecodeError:
        # Try line-based format: integration\nmethod path\nbody
        lines = content.strip().split("\n")
        args = {"integration": lines[0].strip() if lines else ""}
        if len(lines) > 1:
            parts = lines[1].strip().split(" ", 1)
            args["method"] = parts[0] if parts else "GET"
            args["path"] = parts[1] if len(parts) > 1 else "/"
        if len(lines) > 2:
            try:
                args["body"] = json.loads("\n".join(lines[2:]))
            except json.JSONDecodeError:
                pass

    integration_name = args.get("integration", "")
    integrations = load_integrations()
    intg = next((i for i in integrations if i["id"] == integration_name
                 or i["name"].lower() == integration_name.lower()), None)
    if not intg:
        available = ", ".join(i["name"] for i in integrations if i.get("enabled", True))
        return {"error": f"No integration matching '{integration_name}'. Available: {available or 'none configured'}", "exit_code": 1}

    return await execute_api_call(
        intg["id"],
        args.get("method", "GET"),
        args.get("path", "/"),
        params=args.get("params"),
        body=args.get("body"),
        extra_headers=args.get("headers"),
    )


# ---------------------------------------------------------------------------
# App API and Cookbook tool domains imported above; remaining tail domains stay local until R7F.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tail tool domains imported above.
# ---------------------------------------------------------------------------
