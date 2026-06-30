"""Settings and feature-flag admin agent tool implementation."""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from src.tool_domains.common import _parse_tool_args
from src.tool_domains.admin_common import _INTERNAL_BASE, _internal_headers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings/preferences management tool
# ---------------------------------------------------------------------------


def _manage_settings_v2(args: Dict[str, Any], owner: Optional[str] = None) -> Dict:
    """Service-backed manage_settings implementation.

    The public tool response stays legacy-friendly (`response`, `value`,
    `exit_code`) while the new `setting` payload carries machine-readable
    policy and scope details for agent self-control flows.
    """

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _scope() -> str:
        return str(args.get("scope") or "auto")

    def _store() -> str:
        raw = str(args.get("store") or args.get("source") or "").strip().lower()
        return "feature" if raw in {"feature", "features", "flag", "flags"} else "setting"

    def _display_value(value: Any, visible: bool = True) -> Any:
        return value if visible else "***** (secure handoff required)"

    def _policy_response(result: Dict[str, Any]) -> Dict:
        status = str(result.get("status") or "blocked")
        return {
            "response": str(result.get("reason") or status),
            "status": status,
            "requires_confirmation": bool(result.get("requires_confirmation")),
            "secret_handoff_required": status == "secret_handoff_required",
            "setting": result,
            "exit_code": 0,
        }

    def _feature_items() -> Dict:
        from src.settings_service import list_settings

        snapshot = list_settings(scope="global", store="feature", include_secrets=False)
        features = {item["key"]: item.get("value") for item in snapshot["settings"]}
        return {
            "response": f"{len(features)} feature flags",
            "features": features,
            "settings": features,
            "exit_code": 0,
        }

    def _model_slug(value: str) -> str:
        import re as _re

        return _re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    def _endpoint_model_from_cache(model_query: str) -> Dict[str, Any] | None:
        import json as _json
        import re as _re

        try:
            from core.database import ModelEndpoint, SessionLocal
        except Exception:
            return None

        wanted = (model_query or "").strip()
        wanted_slug = _model_slug(wanted)
        wanted_tokens = [_model_slug(t) for t in _re.findall(r"[A-Za-z0-9]+", wanted)]
        wanted_tokens = [t for t in wanted_tokens if t]
        if not wanted_slug:
            return None
        try:
            db = SessionLocal()
        except Exception:
            return None
        try:
            best = None
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():
                try:
                    raw_models = _json.loads(ep.cached_models or "[]") or []
                except Exception:
                    raw_models = []
                for mid in raw_models:
                    mid = str(mid)
                    mid_slug = _model_slug(mid)
                    if not mid_slug:
                        continue
                    exact = mid.lower() == wanted.lower()
                    compact_match = wanted_slug in mid_slug or mid_slug in wanted_slug
                    token_match = bool(wanted_tokens) and all(tok in mid_slug for tok in wanted_tokens)
                    if exact or compact_match or token_match:
                        score = 3 if exact else (2 if compact_match else 1)
                        if not best or score > best[0]:
                            best = (score, ep.id, mid)
            if best:
                return {"endpoint_id": best[1], "model": best[2]}
            return None
        except Exception:
            return None
        finally:
            db.close()

    try:
        from src.settings import get_setting as load_setting_value, load_settings, save_settings
        from src.settings_service import (
            SettingsServiceError,
            explain_setting,
            get_setting as service_get_setting,
            list_settings,
            patch_setting,
            reset_setting,
            set_setting,
        )
        from src.settings_registry import resolve_setting_alias

        if action == "list":
            store = _store()
            if store == "feature":
                return _feature_items()
            snapshot = list_settings(owner=owner, scope=_scope(), store="setting", include_secrets=False)
            shown = {
                item["key"]: _display_value(item.get("value"), bool(item.get("value_visible")))
                for item in snapshot["settings"]
                if item.get("value_type") != "object"
            }
            return {
                "response": f"{len(shown)} settings (use get/set/patch/explain with a key)",
                "settings": shown,
                "exit_code": 0,
            }

        if action == "request_secret":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            try:
                from src.secret_handoff import create_secret_handoff

                handoff = create_secret_handoff(
                    key,
                    owner=owner,
                    scope=_scope(),
                    requested_by="agent",
                    ttl_seconds=int(args.get("ttl_seconds") or 3600),
                )
            except SettingsServiceError as exc:
                return {"error": str(exc), "status": exc.code, "exit_code": 1}
            return {
                "response": (
                    f"Secure input requested for {handoff['key']}. "
                    "Open the secure settings handoff UI to enter the value."
                ),
                "secret_handoff": handoff,
                "ui_event": {
                    "type": "odysseus:secret-handoff-requested",
                    "request_id": handoff["id"],
                    "key": handoff["key"],
                },
                "exit_code": 0,
            }

        if action == "secret_handoffs":
            from src.secret_handoff import list_secret_handoffs

            status = str(args.get("status") or "pending").strip().lower()
            handoffs = list_secret_handoffs(status=status or None)
            return {
                "response": f"{handoffs['count']} secret handoff request(s)",
                "secret_handoffs": handoffs["requests"],
                "exit_code": 0,
            }

        if action == "features":
            key = str(args.get("key") or "").strip()
            if not key:
                return _feature_items()
            if "value" not in args:
                result = service_get_setting(key, owner=owner, scope="global", store="feature")
                return {
                    "response": f"{result['key']} = {result.get('value')}",
                    "value": result.get("value"),
                    "setting": result,
                    "exit_code": 0,
                }
            result = set_setting(
                key,
                args.get("value"),
                owner=owner,
                scope="global",
                store="feature",
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            return {
                "response": f"Set feature {result['key']} = {result.get('value')}.",
                "value": result.get("value"),
                "setting": result,
                "exit_code": 0,
            }

        if action == "get":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            result = service_get_setting(key, owner=owner, scope=_scope(), store=_store(), include_secret=False)
            value = _display_value(result.get("value"), bool(result.get("value_visible")))
            return {
                "response": f"{result['key']} = {value}",
                "value": value,
                "setting": result,
                "exit_code": 0,
            }

        if action == "set":
            raw = str(args.get("key") or "").strip()
            if not raw:
                return {"error": "key is required", "exit_code": 1}
            if "value" not in args:
                return {"error": "value is required", "exit_code": 1}
            store = _store()
            key = resolve_setting_alias(raw) if store == "setting" else raw
            value = args.get("value")
            endpoint_result = None
            if store == "setting" and key in {
                "default_model",
                "research_model",
                "utility_model",
                "task_model",
                "vision_model",
                "image_model",
            }:
                resolved = _endpoint_model_from_cache(str(value))
                if resolved:
                    prefix = key[:-6]
                    endpoint_result = set_setting(
                        f"{prefix}_endpoint_id",
                        resolved["endpoint_id"],
                        owner=owner,
                        scope=_scope(),
                        store="setting",
                        actor="agent",
                        confirmed=_confirmed(),
                    )
                    value = resolved["model"]
            result = set_setting(
                key,
                value,
                owner=owner,
                scope=_scope(),
                store=store,
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            display_value = _display_value(result.get("value"), bool(result.get("value_visible")))
            response = f"Set {result['key']} = {display_value}."
            if endpoint_result and endpoint_result.get("ok"):
                response = f"Set {result['key']} = {display_value} (endpoint {endpoint_result.get('value')})."
            return {
                "response": response,
                "value": display_value,
                "setting": result,
                "endpoint_setting": endpoint_result,
                "exit_code": 0,
            }

        if action in ("delete", "reset"):
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            result = reset_setting(
                key,
                owner=owner,
                scope=_scope(),
                store=_store(),
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            return {
                "response": f"Reset {result['key']} to default ({result.get('value')}).",
                "value": result.get("value"),
                "setting": result,
                "exit_code": 0,
            }

        if action == "patch":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            patch = args.get("patch")
            if not isinstance(patch, dict):
                patch = {
                    "op": args.get("op"),
                    "path": args.get("path") or args.get("patch_key"),
                    "key": args.get("patch_key"),
                    "value": args.get("value"),
                }
            result = patch_setting(
                key,
                patch,
                owner=owner,
                scope=_scope(),
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            return {
                "response": f"Patched {result['key']}.",
                "value": result.get("value"),
                "setting": result,
                "exit_code": 0,
            }

        if action == "explain":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            result = explain_setting(key, owner=owner, scope=_scope(), store=_store())
            bits = [result["key"], f"scope={result['entry']['scope']}", f"agent_access={result['agent_access']}"]
            if result.get("requires_confirmation"):
                bits.append("requires_confirmation")
            if result.get("secret_handoff_required"):
                bits.append("secret_handoff_required")
            return {"response": "; ".join(bits), "setting": result, "exit_code": 0}

        if action in ("disable_tool", "enable_tool", "list_tools"):
            _ALIASES = {
                "shell": ["bash"],
                "terminal": ["bash"],
                "search": ["web_search"],
                "web": ["web_search"],
                "browser": ["builtin_browser"],
                "documents": ["create_document", "edit_document", "update_document", "suggest_document"],
                "doc": ["create_document", "edit_document", "update_document", "suggest_document"],
                "memory": ["manage_memory"],
                "skills": ["manage_skills"],
                "images": ["generate_image"],
                "image": ["generate_image"],
                "tasks": ["manage_tasks"],
                "notes": ["manage_notes"],
                "calendar": ["manage_calendar"],
                "email": ["mcp__email__list_emails", "mcp__email__read_email", "mcp__email__send_email"],
                "research": ["web_search"],
            }
            if action == "list_tools":
                current = load_setting_value("disabled_tools", []) or []
                return {
                    "response": (
                        f"Currently disabled: {', '.join(current) if current else '(none)'}.\n"
                        "Common toggles: shell (bash), search (web_search), browser, documents, "
                        "memory, skills, images, tasks, notes, calendar, email."
                    ),
                    "disabled": list(current),
                    "exit_code": 0,
                }
            tool_name = (args.get("tool") or args.get("name") or "").strip().lower()
            if not tool_name:
                return {"error": "tool name required (e.g. 'shell', 'search', 'bash')", "exit_code": 1}
            targets = _ALIASES.get(tool_name, [tool_name])
            settings = load_settings()
            current = list(settings.get("disabled_tools") or [])
            before = set(current)
            if action == "disable_tool":
                for target in targets:
                    if target not in current:
                        current.append(target)
            else:
                current = [target for target in current if target not in targets]
            after = set(current)
            settings["disabled_tools"] = current
            save_settings(settings)
            verb = "Disabled" if action == "disable_tool" else "Enabled"
            changed = sorted(after.symmetric_difference(before))
            return {
                "response": (
                    f"{verb} {tool_name} ({', '.join(targets)}). "
                    f"Now disabled: {', '.join(current) if current else '(none)'}."
                ),
                "changed": changed,
                "disabled": list(current),
                "exit_code": 0,
            }

        return {"error": f"Unknown action: {action}", "exit_code": 1}
    except SettingsServiceError as exc:
        return {"error": str(exc), "status": exc.code, "exit_code": 1}
    except Exception as exc:
        logger.error("manage_settings v2 error: %s", exc)
        return {"error": str(exc), "exit_code": 1}


async def do_manage_settings(content: str, owner: Optional[str] = None) -> Dict:
    """Manage user settings and preferences."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    return _manage_settings_v2(args, owner=owner)

    action = args.get("action", "list")

    from core.database import SessionLocal
    db = SessionLocal()
    try:
        # set/get/list/delete operate on the REAL app settings (the same store
        # the Settings panel writes), so changing a model / voice / search
        # engine / reminder channel from chat actually takes effect.
        from src.settings import load_settings, save_settings, DEFAULT_SETTINGS

        # Secrets/credentials the agent must NOT write — kept read-only (masked)
        # so API keys never flow through chat. User sets these in the panel.
        _SECRET_KEYS = {
            "brave_api_key", "google_pse_key", "google_pse_cx",
            "tavily_api_key", "serper_api_key", "app_public_url",
        }
        def _is_secret(k):
            # `token` must be a suffix, not a substring: otherwise the int
            # setting `agent_input_token_budget` (which even has a "token budget"
            # alias to set it from chat) is wrongly classified as a credential.
            return (
                k in _SECRET_KEYS
                or k.endswith("token")
                or any(t in k for t in ("api_key", "_key", "secret", "password"))
            )

        # Friendly aliases → real keys, so natural phrasing resolves.
        _ALIASES_SET = {
            "voice": "tts_voice", "tts voice": "tts_voice", "tts": "tts_enabled",
            "text to speech": "tts_enabled", "tts provider": "tts_provider",
            "speech speed": "tts_speed", "voice speed": "tts_speed",
            "stt": "stt_enabled", "speech to text": "stt_enabled", "transcription": "stt_enabled",
            "search engine": "search_provider", "search provider": "search_provider",
            "search results": "search_result_count", "result count": "search_result_count",
            "default model": "default_model", "chat model": "default_model",
            "default endpoint": "default_endpoint_id",
            "task model": "task_model", "background model": "task_model",
            "teacher model": "teacher_model", "teacher": "teacher_enabled",
            "utility model": "utility_model", "research model": "research_model",
            "research max tokens": "research_max_tokens",
            "vision model": "vision_model", "vision": "vision_enabled",
            "image model": "image_model", "image quality": "image_quality",
            "image gen": "image_gen_enabled", "image generation": "image_gen_enabled",
            "reminder channel": "reminder_channel", "reminders": "reminder_channel",
            "ntfy topic": "reminder_ntfy_topic",
            "webhook integration": "reminder_webhook_integration_id",
            "webhook template": "reminder_webhook_payload_template", "webhook payload": "reminder_webhook_payload_template",
            "agent tool calls": "agent_max_tool_calls", "max tool calls": "agent_max_tool_calls",
            "agent timeout": "agent_stream_timeout_seconds", "stream timeout": "agent_stream_timeout_seconds",
            "token budget": "agent_input_token_budget", "input budget": "agent_input_token_budget",
            "hard max": "agent_input_token_hard_max",
            "token budget cap": "agent_input_token_hard_max",
            "input budget cap": "agent_input_token_hard_max",
        }
        def _resolve(k):
            k2 = (k or "").strip().lower()
            if k2 in DEFAULT_SETTINGS:
                return k2
            return _ALIASES_SET.get(k2, (k or "").strip())

        _ENUMS = {
            "image_quality": ["low", "medium", "high"],
            "reminder_channel": ["browser", "email", "ntfy", "webhook"],
        }
        def _coerce(value, default):
            if isinstance(default, bool):
                return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "on", "yes", "1", "enable", "enabled")
            if isinstance(default, int):
                return int(value)
            return value

        def _model_slug(value: str) -> str:
            import re as _re
            return _re.sub(r"[^a-z0-9]+", "", (value or "").lower())

        def _endpoint_model_from_cache(model_query: str):
            """Resolve friendly model text to an enabled endpoint + real model id.

            The Settings UI stores both `<prefix>_endpoint_id` and
            `<prefix>_model`; writing only the model leaves the runtime on the
            old endpoint. Prefer cached model lists so this stays fast/offline.
            """
            import json as _json
            import re as _re
            from core.database import ModelEndpoint

            wanted = (model_query or "").strip()
            wanted_slug = _model_slug(wanted)
            wanted_tokens = [_model_slug(t) for t in _re.findall(r"[A-Za-z0-9]+", wanted)]
            wanted_tokens = [t for t in wanted_tokens if t]
            if not wanted_slug:
                return None
            best = None
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():
                raw_models = []
                try:
                    raw_models = _json.loads(ep.cached_models or "[]") or []
                except Exception:
                    raw_models = []
                # If cache is empty, still allow matching against endpoint name
                # for callers using model@endpoint elsewhere later.
                for mid in raw_models:
                    mid = str(mid)
                    mid_slug = _model_slug(mid)
                    if not mid_slug:
                        continue
                    exact = mid.lower() == wanted.lower()
                    compact_match = wanted_slug in mid_slug or mid_slug in wanted_slug
                    token_match = bool(wanted_tokens) and all(tok in mid_slug for tok in wanted_tokens)
                    if exact or compact_match or token_match:
                        score = 3 if exact else (2 if compact_match else 1)
                        if not best or score > best[0]:
                            best = (score, ep.id, mid)
            if best:
                return {"endpoint_id": best[1], "model": best[2]}
            return None

        def _mask(k, v):
            return "••••• (set in panel)" if _is_secret(k) and v else v

        if action == "list":
            s = load_settings()
            shown = {k: _mask(k, v) for k, v in s.items() if k in DEFAULT_SETTINGS and not isinstance(v, dict)}
            return {"response": f"{len(shown)} settings (use get/set with a key)", "settings": shown, "exit_code": 0}

        elif action == "get":
            key = _resolve(args.get("key", ""))
            if not key:
                return {"error": "key is required", "exit_code": 1}
            if key not in DEFAULT_SETTINGS:
                return {"error": f"Unknown setting '{args.get('key')}'. Use action='list' to see them.", "exit_code": 1}
            val = load_settings().get(key, DEFAULT_SETTINGS.get(key))
            return {"response": f"{key} = {_mask(key, val)}", "value": _mask(key, val), "exit_code": 0}

        elif action == "set":
            raw = args.get("key", "")
            value = args.get("value")
            if not raw:
                return {"error": "key is required", "exit_code": 1}
            key = _resolve(raw)
            if key not in DEFAULT_SETTINGS:
                return {"error": f"Unknown setting '{raw}'. Use action='list' to see available settings.", "exit_code": 1}
            if _is_secret(key):
                return {"response": f"'{key}' is a credential/secret — for security I can't set it from chat. Open Settings and set it there.", "exit_code": 0}
            # Structured settings (dicts/lists like keybinds, default_model_fallbacks)
            # have no safe scalar coercion — _coerce would pass a bare string
            # straight through and clobber the structure. Refuse them here; they're
            # edited in their dedicated panels. (reset/delete still restore the
            # default structure, which is safe.)
            if isinstance(DEFAULT_SETTINGS[key], (dict, list)):
                return {"response": f"'{key}' is a structured setting — edit it in its panel, not from chat. (You can reset it to default here.)", "exit_code": 0}
            try:
                value = _coerce(value, DEFAULT_SETTINGS[key])
            except (ValueError, TypeError):
                return {"error": f"'{value}' isn't a valid value for {key} (expected {type(DEFAULT_SETTINGS[key]).__name__}).", "exit_code": 1}
            if key in _ENUMS and str(value).lower() not in _ENUMS[key]:
                return {"error": f"{key} must be one of: {', '.join(_ENUMS[key])}.", "exit_code": 1}
            s = load_settings()
            s[key] = value
            if key in {"default_model", "research_model", "utility_model", "task_model", "vision_model", "image_model"}:
                resolved = _endpoint_model_from_cache(str(value))
                if resolved:
                    prefix = key[:-6]
                    s[f"{prefix}_endpoint_id"] = resolved["endpoint_id"]
                    s[key] = resolved["model"]
                    value = resolved["model"]
            save_settings(s)
            if key.endswith("_model") and s.get(f"{key[:-6]}_endpoint_id"):
                return {"response": f"Set {key} = {value} (endpoint {s.get(f'{key[:-6]}_endpoint_id')}).", "exit_code": 0}
            return {"response": f"Set {key} = {value}.", "exit_code": 0}

        elif action == "delete" or action == "reset":
            key = _resolve(args.get("key", ""))
            if key not in DEFAULT_SETTINGS:
                return {"error": f"Unknown setting '{args.get('key')}'.", "exit_code": 1}
            if _is_secret(key):
                return {"response": f"'{key}' is a credential — reset it in the panel.", "exit_code": 0}
            s = load_settings()
            s[key] = DEFAULT_SETTINGS[key]
            save_settings(s)
            return {"response": f"Reset {key} to default ({DEFAULT_SETTINGS[key]}).", "exit_code": 0}

        elif action in ("disable_tool", "enable_tool", "list_tools"):
            # Tool-toggle actions. These edit settings.json:disabled_tools
            # (the global list read on every chat request) rather than
            # prefs.json. Friendly aliases accepted: "shell" -> "bash",
            # "search" -> "web_search", "browser" -> "builtin_browser",
            # "documents" -> the document tool set, "memory" ->
            # manage_memory, etc.
            from src.settings import get_setting, save_settings, load_settings
            _ALIASES = {
                "shell": ["bash"],
                "terminal": ["bash"],
                "search": ["web_search"],
                "web": ["web_search"],
                "browser": ["builtin_browser"],
                "documents": ["create_document", "edit_document", "update_document", "suggest_document"],
                "doc": ["create_document", "edit_document", "update_document", "suggest_document"],
                "memory": ["manage_memory"],
                "skills": ["manage_skills"],
                "images": ["generate_image"],
                "image": ["generate_image"],
                "tasks": ["manage_tasks"],
                "notes": ["manage_notes"],
                "calendar": ["manage_calendar"],
                "email": ["mcp__email__list_emails", "mcp__email__read_email", "mcp__email__send_email"],
                "research": ["web_search"],  # research is a per-request flag, not a tool — closest analog
            }

            if action == "list_tools":
                current = get_setting("disabled_tools", []) or []
                return {
                    "response": (
                        f"Currently disabled: {', '.join(current) if current else '(none)'}.\n"
                        "Common toggles: shell (bash), search (web_search), browser, documents, "
                        "memory, skills, images, tasks, notes, calendar, email."
                    ),
                    "disabled": list(current),
                    "exit_code": 0,
                }

            tool_name = (args.get("tool") or args.get("name") or "").strip().lower()
            if not tool_name:
                return {"error": "tool name required (e.g. 'shell', 'search', 'bash')", "exit_code": 1}
            targets = _ALIASES.get(tool_name, [tool_name])

            settings = load_settings()
            current = list(settings.get("disabled_tools") or [])
            before = set(current)
            if action == "disable_tool":
                for t in targets:
                    if t not in current:
                        current.append(t)
            else:  # enable_tool
                current = [t for t in current if t not in targets]
            after = set(current)
            settings["disabled_tools"] = current
            save_settings(settings)

            verb = "Disabled" if action == "disable_tool" else "Enabled"
            changed = sorted(after.symmetric_difference(before))
            return {
                "response": (
                    f"{verb} {tool_name} ({', '.join(targets)}). "
                    f"Now disabled: {', '.join(current) if current else '(none)'}."
                ),
                "changed": changed,
                "disabled": list(current),
                "exit_code": 0,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_settings error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()


