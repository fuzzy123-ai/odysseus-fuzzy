"""Sanitized observability for DSGVO/privacy runtime decisions.

The diagnostics API should explain *why* the privacy gate allows, blocks,
requires local-only execution, or requires review without echoing user content,
paths, channel ids, provider URLs, or settings values.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Mapping

from src.chat_security_state import ChatSecurityState, ProviderScope
from src.privacy_runtime import (
    create_runtime_security_state,
    is_dsgvo_mode_enabled,
    runtime_requires_local_only,
)
from src.secure_channel_policy import ChannelContext, ChannelKind, decide_channel_access
from src.secure_policy_gate import decide_provider_gate, decide_source_access, decide_tool_gate


_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9_.:-]+")
_MAX_TOKEN = 96


def _token(value: Any) -> str:
    """Return a controlled diagnostic token, never raw arbitrary text."""

    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = _SAFE_TOKEN_RE.sub("_", text).strip("_")
    if len(text) > _MAX_TOKEN:
        return text[: _MAX_TOKEN - 3] + "..."
    return text


def _policy_probe(result: Any) -> dict[str, Any]:
    return {
        "decision": _token(getattr(result, "decision", "")),
        "allowed": bool(getattr(result, "allowed", False)),
        "block_reason": _token(getattr(result, "block_reason", "")),
        "classification": _token(getattr(result, "classification", "")) or None,
        "required_security_mode": _token(getattr(result, "required_security_mode", "")),
        "required_provider_scope": _token(getattr(result, "required_provider_scope", "")),
        "local_only_required": bool(getattr(result, "local_only_required", False)),
        "next_action": _token(getattr(result, "next_action", "")),
    }


def _channel_probe(result: Any, *, state: ChatSecurityState) -> dict[str, Any]:
    probe = _policy_probe(result)
    probe["channel_kind"] = _token(getattr(result, "channel_kind", ""))
    probe["local_only_required"] = (
        bool(state.local_only_required)
        or getattr(result, "required_provider_scope", None) == ProviderScope.LOCAL_ONLY
    )
    return probe


def privacy_runtime_health(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the admin diagnostics card for the privacy runtime.

    The returned structure intentionally uses synthetic probe inputs and omits
    raw config. It is a policy contract check, not a live data inspection.
    """

    state = create_runtime_security_state(settings=settings)
    dsgvo_mode = is_dsgvo_mode_enabled(settings=settings)

    local_ui = ChannelContext.create(
        channel_id="diagnostic-local-ui",
        channel_kind=ChannelKind.LOCAL_UI,
        secure_transport=True,
        user_allowlisted=True,
    )
    telegram = ChannelContext.create(
        channel_id="diagnostic-telegram",
        channel_kind=ChannelKind.TELEGRAM,
        secure_transport=True,
        user_allowlisted=True,
        can_start_secure_flow=False,
    )

    probes = {
        "data": {
            "sensitive_source": _policy_probe(
                decide_source_access(state=state, source_classifications=["sensitive"])
            ),
            "unknown_classification": _policy_probe(
                decide_source_access(state=state, source_classifications=[None])
            ),
        },
        "model": {
            "local_provider": _policy_probe(
                decide_provider_gate(state=state, provider_scope="local_only")
            ),
            "external_provider": _policy_probe(
                decide_provider_gate(state=state, provider_scope="default")
            ),
        },
        "tool": {
            "safe_local_tool": _policy_probe(
                decide_tool_gate(state=state, tool_safety_class="safe_local")
            ),
            "external_tool": _policy_probe(
                decide_tool_gate(state=state, tool_safety_class="external")
            ),
        },
        "channel": {
            "local_private": _channel_probe(
                decide_channel_access(
                    state=state,
                    channel=local_ui,
                    classification="private",
                ),
                state=state,
            ),
            "telegram_sensitive": _channel_probe(
                decide_channel_access(
                    state=state,
                    channel=telegram,
                    classification="sensitive",
                ),
                state=state,
            ),
        },
    }

    detail = (
        "DSGVO mode is active; privacy gates require local-only execution."
        if dsgvo_mode
        else "Privacy gates are ready; DSGVO mode is not active."
    )
    return {
        "name": "privacy_runtime",
        "status": "ok",
        "detail": detail,
        "meta": {
            "dsgvo_mode": dsgvo_mode,
            "effective_security_mode": _token(state.security_mode),
            "required_provider_scope": _token(state.allowed_provider_scope),
            "local_only_required": runtime_requires_local_only(
                settings=settings,
                security_state=state,
            ),
            "probes": probes,
        },
    }
