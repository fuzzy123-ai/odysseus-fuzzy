"""Webhook, preset, personal-docs, embedding and assistant admin tools."""

import logging
from typing import Any, Dict, Optional

from src.tool_domains.common import _parse_tool_args
from src.tool_domains.admin_common import _INTERNAL_BASE, _internal_headers
from src.tool_domains.admin_plugin_token_services import do_manage_plugins, do_manage_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Webhook management tool
# ---------------------------------------------------------------------------

async def do_manage_webhooks(content: str, owner: Optional[str] = None) -> Dict:
    """Manage webhooks through admin routes with confirmation for mutations."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Webhook {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Webhook route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _mask_webhook(item: Dict[str, Any]) -> Dict[str, Any]:
        safe = dict(item)
        url = str(safe.get("url") or "")
        if url:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(url)
                safe["url"] = f"{parsed.scheme}://{parsed.netloc}/..." if parsed.scheme and parsed.netloc else "(configured)"
            except Exception:
                safe["url"] = "(configured)"
        safe["has_url"] = bool(url)
        return safe

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/webhooks", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            hooks = [_mask_webhook(item) for item in (resp.json() or [])]
            return {"response": f"{len(hooks)} webhooks", "webhooks": hooks, "exit_code": 0}

        elif action == "add":
            if not _confirmed():
                return _confirmation_required("add")
            name = args.get("name", "")
            url = args.get("url", "")
            events = args.get("events", "chat.completed")
            if not url:
                return {"error": "url is required", "exit_code": 1}
            data = {"name": name or "Webhook", "url": url, "events": events}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/webhooks", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            webhook = resp.json() or {}
            return {
                "response": f"Added webhook '{webhook.get('name') or name or 'Webhook'}'.",
                "webhook": webhook,
                "exit_code": 0,
            }

        elif action == "delete":
            if not _confirmed():
                return _confirmation_required("delete")
            wid = args.get("webhook_id", "")
            if not wid:
                return {"error": "webhook_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/webhooks/{wid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted webhook {wid}", "result": resp.json() or {}, "exit_code": 0}

        elif action == "test":
            if not _confirmed():
                return _confirmation_required("test")
            wid = args.get("webhook_id", "")
            if not wid:
                return {"error": "webhook_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/webhooks/{wid}/test", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {
                "response": f"Sent test event for webhook {wid}.",
                "result": resp.json() or {},
                "exit_code": 0,
            }

        elif action in ("enable", "disable"):
            if not _confirmed():
                return _confirmation_required(action)
            wid = args.get("webhook_id", "")
            if not wid:
                return {"error": "webhook_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                listed = await client.get(f"{_INTERNAL_BASE}/api/webhooks", headers=headers)
                if listed.status_code >= 400:
                    return _error_from_response(listed)
                hooks = listed.json() or []
                current = next((item for item in hooks if item.get("id") == wid), None)
                if not current:
                    return {"error": f"Webhook {wid} not found", "exit_code": 1}
                desired = action == "enable"
                if bool(current.get("is_active")) != desired:
                    resp = await client.patch(f"{_INTERNAL_BASE}/api/webhooks/{wid}", headers=headers)
                    if resp.status_code >= 400:
                        return _error_from_response(resp)
                    current = {**current, **(resp.json() or {})}
            return {
                "response": f"Webhook '{current.get('name') or wid}' {'enabled' if desired else 'disabled'}.",
                "webhook": _mask_webhook(current),
                "exit_code": 0,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_webhooks error: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Preset management tool
# ---------------------------------------------------------------------------

async def do_manage_presets(content: str, owner: Optional[str] = None) -> Dict:
    """Manage chat/persona presets through the same routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Preset {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Preset route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/presets", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            presets = resp.json() or {}
            return {"response": f"{len(presets)} presets", "presets": presets, "exit_code": 0}

        elif action in ("templates", "list_templates"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/presets/templates", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            templates = resp.json() or []
            return {"response": f"{len(templates)} preset templates", "templates": templates, "exit_code": 0}

        elif action in ("groups", "list_groups"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/presets/groups", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            groups = resp.json() or {}
            count = len(groups.get("groups") or []) if isinstance(groups, dict) else 0
            return {"response": f"{count} preset groups", "groups": groups, "exit_code": 0}

        elif action in ("update_custom", "custom"):
            if not _confirmed():
                return _confirmation_required("update_custom")
            body = {
                "name": args.get("name", ""),
                "enabled": bool(args.get("enabled", True)),
                "temperature": args.get("temperature", 1.0),
                "max_tokens": args.get("max_tokens", 0),
                "system_prompt": args.get("system_prompt", ""),
                "inject_prefix": args.get("inject_prefix", ""),
                "inject_suffix": args.get("inject_suffix", ""),
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/presets/custom", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": "Updated custom preset.", "result": resp.json() or {}, "exit_code": 0}

        elif action in ("save_template", "template"):
            if not _confirmed():
                return _confirmation_required("save_template")
            name = str(args.get("name") or "").strip()
            if not name:
                return {"error": "name is required", "exit_code": 1}
            body = {
                "id": args.get("template_id") or args.get("id") or "",
                "name": name,
                "system_prompt": args.get("system_prompt", ""),
                "temperature": args.get("temperature", 1.0),
                "max_tokens": args.get("max_tokens", 0),
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/presets/templates", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Saved preset template '{name}'.", "result": resp.json() or {}, "exit_code": 0}

        elif action == "delete_template":
            if not _confirmed():
                return _confirmation_required("delete_template")
            template_id = str(args.get("template_id") or args.get("id") or "").strip()
            if not template_id:
                return {"error": "template_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/presets/templates/{template_id}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted preset template {template_id}.", "result": resp.json() or {}, "exit_code": 0}

        elif action == "save_groups":
            if not _confirmed():
                return _confirmation_required("save_groups")
            groups = args.get("groups")
            if not isinstance(groups, list):
                return {"error": "groups must be a list", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/presets/groups", json={"groups": groups}, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Saved {len(groups)} preset group(s).", "result": resp.json() or {}, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_presets error: {e}")
        return {"error": str(e), "exit_code": 1}

# ---------------------------------------------------------------------------
# Personal document/RAG source management tool
# ---------------------------------------------------------------------------

async def do_manage_personal_docs(content: str, owner: Optional[str] = None) -> Dict:
    """Manage personal document/RAG sources through the same routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Personal document {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Personal docs route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _directory_arg() -> str:
        return str(args.get("directory") or args.get("path") or "").strip()

    def _filepath_arg() -> str:
        return str(args.get("filepath") or args.get("file_path") or args.get("path") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/personal", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            files = data.get("files") or []
            directories = data.get("directories") or []
            return {
                "response": f"{len(files)} personal document(s), {len(directories)} indexed source dir(s)",
                "personal_docs": data,
                "exit_code": 0,
            }

        elif action == "reload":
            if not _confirmed():
                return _confirmation_required("reload")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/personal/reload", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": f"Reloaded personal documents ({result.get('count', 0)} indexed item(s)).",
                "result": result,
                "exit_code": 0,
            }

        elif action in ("add_directory", "add"):
            if not _confirmed():
                return _confirmation_required("add_directory")
            directory = _directory_arg()
            if not directory:
                return {"error": "directory is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/personal/add_directory",
                    json={"directory": directory},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": result.get("message") or f"Added personal directory {directory}.",
                "result": result,
                "exit_code": 0,
            }

        elif action in ("remove_directory", "remove"):
            if not _confirmed():
                return _confirmation_required("remove_directory")
            directory = _directory_arg()
            if not directory:
                return {"error": "directory is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(
                    f"{_INTERNAL_BASE}/api/personal/remove_directory",
                    params={"directory": directory},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": result.get("message") or f"Removed personal directory {directory}.",
                "result": result,
                "exit_code": 0,
            }

        elif action in ("delete_file", "delete"):
            if not _confirmed():
                return _confirmation_required("delete_file")
            filepath = _filepath_arg()
            if not filepath:
                return {"error": "filepath is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(
                    f"{_INTERNAL_BASE}/api/personal/file",
                    params={"filepath": filepath},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": f"Deleted/excluded personal document {filepath}.",
                "result": result,
                "exit_code": 0,
            }

        elif action == "upload":
            return {
                "error": "Uploading files stays UI-only for now; use the Personal Docs UI so multipart bytes and owner scope stay bounded.",
                "exit_code": 1,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_personal_docs error: {e}")
        return {"error": str(e), "exit_code": 1}

# ---------------------------------------------------------------------------
# Embedding model/endpoint management tool
# ---------------------------------------------------------------------------

async def do_manage_embeddings(content: str, owner: Optional[str] = None) -> Dict:
    """Manage embedding models through admin routes with confirmation for mutations."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Embedding {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Embedding route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _model_name() -> str:
        return str(args.get("model_name") or args.get("model") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/embeddings/models", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            models = resp.json() or []
            return {"response": f"{len(models)} embedding model(s)", "models": models, "exit_code": 0}

        elif action == "status":
            model_name = _model_name()
            if not model_name:
                return {"error": "model_name is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{_INTERNAL_BASE}/api/embeddings/models/{model_name}/status",
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            status = resp.json() or {}
            return {"response": f"Embedding model {model_name}: {status}", "status": status, "exit_code": 0}

        elif action in ("endpoint", "get_endpoint"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/embeddings/endpoint", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            endpoint = resp.json() or {}
            return {"response": f"Embedding endpoint active={bool(endpoint.get('active'))}", "endpoint": endpoint, "exit_code": 0}

        elif action in ("download", "download_model"):
            if not _confirmed():
                return _confirmation_required("download")
            model_name = _model_name()
            if not model_name:
                return {"error": "model_name is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/embeddings/models/{model_name}/download",
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Embedding model download requested for {model_name}.", "result": result, "exit_code": 0}

        elif action in ("delete", "delete_model"):
            if not _confirmed():
                return _confirmation_required("delete")
            model_name = _model_name()
            if not model_name:
                return {"error": "model_name is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.delete(
                    f"{_INTERNAL_BASE}/api/embeddings/models/{model_name}",
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Deleted embedding model cache for {model_name}.", "result": result, "exit_code": 0}

        elif action in ("clear_endpoint", "delete_endpoint"):
            if not _confirmed():
                return _confirmation_required("clear_endpoint")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/embeddings/endpoint", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": "Cleared embedding endpoint; local FastEmbed will be used.", "result": result, "exit_code": 0}

        elif action in ("set_endpoint", "update_endpoint"):
            return {
                "error": "Setting embedding endpoints stays UI/secure-handoff-only for now because it performs a live health check and may require an API key.",
                "exit_code": 1,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_embeddings error: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Personal assistant management tool
# ---------------------------------------------------------------------------

async def do_manage_assistant(content: str, owner: Optional[str] = None) -> Dict:
    """Manage the per-user assistant through the same routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "settings") or "settings").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Assistant {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Assistant route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _task_id() -> str:
        return str(args.get("task_id") or args.get("id") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)

        if action == "session":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/session", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            session = resp.json() or {}
            return {
                "response": f"Assistant session {session.get('session_id') or '(unknown)'}",
                "session": session,
                "exit_code": 0,
            }

        elif action in ("settings", "get", "list"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/settings", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            settings = resp.json() or {}
            check_ins = settings.get("check_ins") or []
            crew = settings.get("crew") or {}
            return {
                "response": f"Assistant settings for {crew.get('name') or 'assistant'} with {len(check_ins)} check-in(s)",
                "assistant": settings,
                "exit_code": 0,
            }

        elif action == "timezones":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/available-timezones", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            zones = resp.json() or {}
            return {
                "response": f"{len(zones.get('timezones') or [])} available timezones",
                "timezones": zones.get("timezones") or [],
                "exit_code": 0,
            }

        elif action in ("run_status", "status"):
            task_id = _task_id()
            if not task_id:
                return {"error": "task_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/run-status/{task_id}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            status = resp.json() or {}
            return {"response": f"Assistant task {task_id}: {status.get('status')}", "status": status, "exit_code": 0}

        elif action == "update":
            if not _confirmed():
                return _confirmation_required("update")
            if "endpoint_url" in args:
                return {
                    "error": (
                        "endpoint_url stays UI/manage_endpoints-only for now; "
                        "assistant endpoint changes must use the UI or endpoint management flow."
                    ),
                    "exit_code": 1,
                }
            allowed = (
                "name", "avatar", "personality", "model", "enabled_tools",
                "allow_autonomous_email", "timezone", "check_ins",
            )
            body = {key: args[key] for key in allowed if key in args}
            if "check_ins" in body and not isinstance(body["check_ins"], list):
                return {"error": "check_ins must be a list", "exit_code": 1}
            if "enabled_tools" in body and not isinstance(body["enabled_tools"], list):
                return {"error": "enabled_tools must be a list", "exit_code": 1}
            if not body:
                return {"error": "No assistant settings fields supplied", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{_INTERNAL_BASE}/api/assistant/settings",
                    json=body,
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            settings = resp.json() or {}
            return {"response": "Updated assistant settings.", "assistant": settings, "exit_code": 0}

        elif action == "run":
            if not _confirmed():
                return _confirmation_required("run")
            task_id = _task_id()
            if not task_id:
                return {"error": "task_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/assistant/run/{task_id}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Assistant check-in {task_id} started={bool(result.get('started'))}.", "result": result, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_assistant error: {e}")
        return {"error": str(e), "exit_code": 1}
