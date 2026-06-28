"""Compact runtime self-control context for model calls.

The settings service/registry tells tools what they may change. This module
turns the current runtime policy into a short model-facing briefing so both API
and local models know which constraints are active before they answer.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.chat_security_state import ProviderScope
from src.privacy_runtime import create_runtime_security_state, is_dsgvo_mode_enabled
from src.secure_policy_gate import decide_provider_gate
from src.secure_provider_runtime import provider_scope_for_base_url


MAX_BLOCKED_TOOLS_SHOWN = 8
_TOOL_DISPLAY_PRIORITY = (
    "web_search",
    "web_fetch",
    "trigger_research",
    "ask_teacher",
    "chat_with_model",
    "api_call",
    "app_api",
    "bash",
    "python",
    "manage_settings",
)


def _load_settings_safely() -> dict[str, Any]:
    try:
        from src.settings import load_settings

        loaded = load_settings()
        return dict(loaded or {})
    except Exception:
        return {}


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _summarize_names(names: list[str], *, limit: int = MAX_BLOCKED_TOOLS_SHOWN) -> str:
    unique = sorted({name for name in names if name})
    if not unique:
        return "none"
    priority = [name for name in _TOOL_DISPLAY_PRIORITY if name in unique]
    rest = [name for name in unique if name not in priority]
    shown = [*priority, *rest][:limit]
    suffix = f", +{len(unique) - limit} more" if len(unique) > limit else ""
    return ", ".join(shown) + suffix


def _tool_policy_summary(settings: Mapping[str, Any], message: str) -> tuple[str, str]:
    disabled_tools = _as_list(settings.get("disabled_tools"))
    try:
        from src.tool_policy import build_effective_tool_policy

        policy = build_effective_tool_policy(
            disabled_tools=disabled_tools,
            last_user_message=message,
            settings=settings,
        )
        blocked = sorted(policy.hidden_tools | policy.disabled_tools)
        mode = policy.mode
    except Exception:
        blocked = disabled_tools
        mode = "unknown"
    return mode, _summarize_names(blocked)


def build_self_control_context(
    *,
    session: Any = None,
    owner: str | None = None,
    message: str = "",
    settings: Mapping[str, Any] | None = None,
) -> str:
    """Return a compact policy card for the active model call."""

    active_settings = dict(settings if settings is not None else _load_settings_safely())
    endpoint_url = str(_attr(session, "endpoint_url", "") or "")
    model_id = str(_attr(session, "model", "") or "")
    session_id = str(_attr(session, "id", "runtime") or "runtime")
    security_mode = _attr(session, "security_mode", None)

    state = create_runtime_security_state(
        chat_id=session_id,
        thread_id=session_id,
        security_mode=security_mode,
        requested_by=owner or "self-control-runtime",
        settings=active_settings,
    )
    provider_scope = provider_scope_for_base_url(endpoint_url)
    provider_gate = decide_provider_gate(state=state, provider_scope=provider_scope)
    dsgvo_on = is_dsgvo_mode_enabled(active_settings)
    model_profile = "local" if provider_scope == ProviderScope.LOCAL_ONLY else "api"
    tool_mode, blocked_tools = _tool_policy_summary(active_settings, message)

    if model_profile == "local":
        model_instruction = (
            "local model: smaller/slower context; keep plans compact, fetch only "
            "needed context, prefer summaries over bulk dumps."
        )
    else:
        model_instruction = (
            "API model: external provider; do not include sensitive or local-only "
            "content, and switch/use local processing when policy requires it."
        )

    gate_status = "allowed" if provider_gate.allowed else f"blocked:{provider_gate.block_reason}"
    model_part = f"; model={model_id}" if model_id else ""
    return "\n".join(
        [
            "## Odysseus Runtime Self-Control",
            (
                f"- security_mode={state.security_mode.value}; dsgvo_mode={'on' if dsgvo_on else 'off'}; "
                f"provider_scope={provider_scope.value}; model_profile={model_profile}{model_part}; "
                f"provider_gate={gate_status}."
            ),
            f"- tool_policy={tool_mode}; blocked_tools={blocked_tools}.",
            f"- {model_instruction}",
            (
                "- Settings changes use manage_settings; feature/global/risky changes need "
                "confirmed=true; secrets/API keys use request_secret and must never be echoed."
            ),
        ]
    )


def build_self_control_context_message(
    *,
    session: Any = None,
    owner: str | None = None,
    message: str = "",
    settings: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    return {
        "role": "system",
        "content": build_self_control_context(
            session=session,
            owner=owner,
            message=message,
            settings=settings,
        ),
    }
