"""Helper contracts for Codex integration routes."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from routes._validators import validate_remote_host, validate_ssh_port
from src.auth_helpers import require_user

COOKBOOK_READ_SCOPES = {"cookbook:read", "cookbook:launch"}
COOKBOOK_LAUNCH_SCOPES = {"cookbook:launch"}
TODO_READ_SCOPES = {"todos:read", "todos:write"}
TODO_WRITE_SCOPES = {"todos:write"}
EMAIL_READ_SCOPES = {"email:read", "email:draft", "email:send"}
EMAIL_DRAFT_SCOPES = {"email:draft", "email:send"}
EMAIL_SEND_SCOPES = {"email:send"}
MEMORY_READ_SCOPES = {"memory:read", "memory:write"}
MEMORY_WRITE_SCOPES = {"memory:write"}
CALENDAR_READ_SCOPES = {"calendar:read", "calendar:write"}
CALENDAR_WRITE_SCOPES = {"calendar:write"}
DOCS_READ_SCOPES = {"documents:read", "documents:write"}
DOCS_WRITE_SCOPES = {"documents:write"}
WRITE_ACTIONS = {"add", "create", "new", "save", "remind", "update", "delete", "toggle_item", "remove", "remove_item"}


def ssh_prefix_for_task(task: dict) -> tuple[str, str]:
    """Resolve a cookbook task's stored SSH target into ``(host, port_flag)``."""
    raw_host = task.get("remoteHost")
    raw_port = task.get("sshPort")
    host_value = str(raw_host).strip() if raw_host is not None else None
    port_value = str(raw_port).strip() if raw_port is not None else None
    host = validate_remote_host(host_value or None) or ""
    ssh_port = validate_ssh_port(port_value or None) or ""
    port_flag = f"-p {ssh_port} " if ssh_port and ssh_port != "22" else ""
    return host, port_flag


async def as_owner(request: Request, owner: str, fn, *args, **kwargs):
    """Temporarily run an existing route handler as the scope-gated owner."""
    orig = getattr(request.state, "current_user", None)
    orig_api_token = getattr(request.state, "api_token", None)
    request.state.current_user = owner
    request.state.api_token = False
    try:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    finally:
        request.state.current_user = orig
        if orig_api_token is None:
            try:
                delattr(request.state, "api_token")
            except AttributeError:
                pass
        else:
            request.state.api_token = orig_api_token


def scope_owner(request: Request, allowed: set[str]) -> str:
    """Return the data owner if the caller is allowed for this Codex action."""
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(allowed):
            required = " or ".join(sorted(allowed))
            raise HTTPException(403, f"API token missing required scope: {required}")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    return require_user(request)


def scope_owner_all(request: Request, required: set[str]) -> str:
    """Return owner only when an API token has every required scope."""
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        missing = required - scopes
        if missing:
            raise HTTPException(403, f"API token missing required scope: {' and '.join(sorted(missing))}")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    return require_user(request)


def find_endpoint(router: APIRouter | None, method: str, path: str):
    if router is None:
        return None
    for route in getattr(router, "routes", []):
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    return None


def build_capabilities_payload(
    *,
    token_scopes: set[str],
    has_token: bool,
    memory_available: bool,
    calendar_available: bool,
    documents_available: bool,
) -> dict[str, Any]:
    def scoped(allowed: set[str]) -> bool:
        return bool(token_scopes.intersection(allowed)) if has_token else True

    return {
        "integration": "codex",
        "token_scopes": sorted(token_scopes),
        "tools": {
            "todos": {
                "read": scoped(TODO_READ_SCOPES),
                "write": scoped(TODO_WRITE_SCOPES),
                "actions": ["list", "add", "update", "delete", "toggle_item"],
            },
            "email": {
                "read": scoped(EMAIL_READ_SCOPES),
                "draft": scoped(EMAIL_DRAFT_SCOPES),
                "send": scoped(EMAIL_SEND_SCOPES),
                "actions": ["list", "read", "draft_document", "draft", "send"],
            },
            "memory": {
                "read": scoped(MEMORY_READ_SCOPES),
                "write": scoped(MEMORY_WRITE_SCOPES),
                "actions": ["list", "add", "delete"],
                "available": memory_available,
            },
            "calendar": {
                "read": scoped(CALENDAR_READ_SCOPES),
                "write": scoped(CALENDAR_WRITE_SCOPES),
                "actions": ["list_events", "create_event", "delete_event"],
                "available": calendar_available,
            },
            "documents": {
                "read": scoped(DOCS_READ_SCOPES),
                "write": scoped(DOCS_WRITE_SCOPES),
                "actions": ["library", "read", "create", "delete"],
                "available": documents_available,
            },
            "cookbook": {
                "read": scoped(COOKBOOK_READ_SCOPES),
                "launch": scoped(COOKBOOK_LAUNCH_SCOPES),
                "actions": ["tasks", "servers", "output", "serve", "stop"],
            },
        },
        "safety": {
            "email_send_requires_confirmation": True,
            "destructive_actions_should_confirm": True,
        },
    }
