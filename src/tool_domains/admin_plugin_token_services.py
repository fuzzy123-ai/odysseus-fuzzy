"""Plugin and API token admin tools."""

import logging
import re
from typing import Any, Dict, Optional

from src.tool_domains.admin_common import _INTERNAL_BASE, _internal_headers
from src.tool_domains.common import _parse_tool_args

logger = logging.getLogger(__name__)


async def do_manage_plugins(content: str, owner: Optional[str] = None) -> Dict:
    """Manage plugins through admin plugin routes with confirmation for mutations."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Plugin {target} requires explicit confirmation.",
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
            "error": detail or getattr(resp, "text", "") or f"Plugin route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _plugin_id() -> str:
        return str(args.get("plugin_id") or args.get("id") or "").strip()

    def _valid_plugin_id(plugin_id: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", plugin_id or ""))

    def _registry_url() -> str:
        return str(args.get("url") or args.get("registry_url") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)

        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            plugins = data.get("plugins") or []
            return {"response": f"{len(plugins)} plugin(s)", "plugins": plugins, "exit_code": 0}

        elif action == "registry":
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins/registry", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            plugins = data.get("plugins") or []
            return {"response": f"{len(plugins)} registry plugin(s)", "registry": data, "exit_code": 0}

        elif action in ("registries", "list_registries"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins/registries", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            registries = data.get("registries") or []
            return {"response": f"{len(registries)} plugin registries", "registries": data, "exit_code": 0}

        elif action == "status":
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins/{plugin_id}/status", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            status = resp.json() or {}
            return {"response": f"Plugin {plugin_id} status", "status": status, "exit_code": 0}

        elif action in ("enable", "disable", "reload"):
            if not _confirmed():
                return _confirmation_required(action)
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/plugins/{plugin_id}/{action}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Plugin {plugin_id} {action} complete.", "plugin": result, "exit_code": 0}

        elif action == "rescan":
            if not _confirmed():
                return _confirmation_required("rescan")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/plugins/rescan", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            plugins = data.get("plugins") or []
            return {"response": f"Rescanned plugins ({len(plugins)} discovered).", "plugins": plugins, "exit_code": 0}

        elif action == "install":
            if not _confirmed():
                return _confirmation_required("install")
            if args.get("url"):
                return {
                    "error": (
                        "Direct plugin ZIP URL installs stay Plugins UI-only for now. "
                        "Use manage_plugins install with a registry plugin id."
                    ),
                    "exit_code": 1,
                }
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id or id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/plugins/install",
                    json={"id": plugin_id},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Installed plugin {plugin_id}.", "plugin": result, "exit_code": 0}

        elif action == "uninstall":
            if not _confirmed():
                return _confirmation_required("uninstall")
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/plugins/{plugin_id}/uninstall", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Uninstalled plugin {plugin_id}.", "result": result, "exit_code": 0}

        elif action in ("add_registry", "add_registries"):
            if not _confirmed():
                return _confirmation_required("add_registry")
            url = _registry_url()
            if not url:
                return {"error": "url is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/plugins/registries",
                    json={"url": url},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            return {"response": "Added plugin registry.", "registries": data.get("registries") or data, "exit_code": 0}

        elif action in ("remove_registry", "delete_registry"):
            if not _confirmed():
                return _confirmation_required("remove_registry")
            url = _registry_url()
            if not url:
                return {"error": "url is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    "DELETE",
                    f"{_INTERNAL_BASE}/api/plugins/registries",
                    json={"url": url},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            return {"response": "Removed plugin registry.", "registries": data.get("registries") or data, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_plugins error: {e}")
        return {"error": str(e), "exit_code": 1}


async def do_manage_tokens(content: str, owner: Optional[str] = None) -> Dict:
    """Manage API tokens through the same admin routes as the UI/API."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"API token {target} requires explicit confirmation.",
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
            "error": detail or getattr(resp, "text", "") or f"Token route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/tokens", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            items = resp.json() or []
            return {"response": f"{len(items)} API tokens", "tokens": items, "exit_code": 0}

        elif action == "create":
            if not _confirmed():
                return _confirmation_required("create")
            name = args.get("name", "API Token")
            data = {"name": name}
            if args.get("scopes") is not None:
                scopes = args.get("scopes")
                data["scopes"] = ",".join(scopes) if isinstance(scopes, list) else str(scopes)
            if args.get("profile") is not None:
                data["profile"] = str(args.get("profile"))
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/tokens", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            token = resp.json() or {}
            return {
                "response": f"Created token '{token.get('name') or name}'. Store the token now; it will not be shown again.",
                "token": token.get("token"),
                "token_meta": {k: v for k, v in token.items() if k != "token"},
                "exit_code": 0,
            }

        elif action in ("update", "rename"):
            if not _confirmed():
                return _confirmation_required(action)
            tid = args.get("token_id", "")
            if not tid:
                return {"error": "token_id is required", "exit_code": 1}
            body: Dict[str, Any] = {}
            if "name" in args:
                body["name"] = args.get("name")
            if args.get("scopes") is not None:
                body["scopes"] = args.get("scopes")
            if not body:
                return {"error": "name or scopes is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(f"{_INTERNAL_BASE}/api/tokens/{tid}", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            token = resp.json() or {}
            return {
                "response": f"Updated token '{token.get('name') or tid}'.",
                "token_meta": token,
                "exit_code": 0,
            }

        elif action == "delete":
            if not _confirmed():
                return _confirmation_required("delete")
            tid = args.get("token_id", "")
            if not tid:
                return {"error": "token_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/tokens/{tid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted token {tid}", "result": resp.json() or {}, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_tokens error: {e}")
        return {"error": str(e), "exit_code": 1}
