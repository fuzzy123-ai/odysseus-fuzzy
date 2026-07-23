"""Compatibility contract for controlling Gemma 3 maintenance via Cookbook.

This module does not start or stop model servers. It translates manual/UI/agent
intent into a small auditable plan that must be executed through the existing
Cookbook tools or routes, never through ad-hoc shell/tmux/ssh commands.

The ``gemma4_*`` module and symbol names remain stable for existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping

from src.maintenance_model_policy import (
    DEFAULT_MAINTENANCE_MODEL,
    DEFAULT_MAINTENANCE_PROVIDER,
    default_maintenance_model_profile,
)


COOKBOOK_CONTROL_SCHEMA = "odysseus.gemma4_cookbook_control.v1"
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,160}$")


class Gemma4CookbookControlError(ValueError):
    """Raised when a maintenance-model Cookbook control plan is unsafe."""


class Gemma4CookbookAction(StrEnum):
    STATUS = "status"
    SERVE = "serve"
    STOP = "stop"
    ADOPT = "adopt"


_ACTION_TOOL = {
    Gemma4CookbookAction.STATUS: "list_served_models",
    Gemma4CookbookAction.SERVE: "serve_preset",
    Gemma4CookbookAction.STOP: "stop_served_model",
    Gemma4CookbookAction.ADOPT: "adopt_served_model",
}


@dataclass(frozen=True, slots=True)
class Gemma4CookbookControlPlan:
    action: Gemma4CookbookAction
    model_ref: str
    provider: str
    tool_name: str
    args: Mapping[str, Any]
    operator_confirmation_required: bool
    live_go_required: bool
    ui_placement_required: bool
    reason: str
    schema: str = COOKBOOK_CONTROL_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action": self.action.value,
            "model_ref": self.model_ref,
            "provider": self.provider,
            "tool_name": self.tool_name,
            "args": dict(self.args),
            "operator_confirmation_required": self.operator_confirmation_required,
            "live_go_required": self.live_go_required,
            "ui_placement_required": self.ui_placement_required,
            "reason": self.reason,
            "allowed_execution_surface": "cookbook_native_tool",
            "shell_allowed": False,
            "ssh_tmux_bypass_allowed": False,
            "raw_secret_allowed": False,
        }


def plan_gemma4_cookbook_control(
    *,
    action: Gemma4CookbookAction | str,
    model_ref: str = DEFAULT_MAINTENANCE_MODEL,
    provider: str = DEFAULT_MAINTENANCE_PROVIDER,
    preset_name: str = "gemma3-4b-maintenance",
    session_id: str = "",
    host: str = "",
    port: int | None = None,
    tmux_session: str = "",
    operator_go: bool = False,
    live_go: bool = False,
) -> Gemma4CookbookControlPlan:
    normalized_action = _normalize_action(action)
    profile = default_maintenance_model_profile()
    safe_model = _safe_label(model_ref or profile.model_ref, field="model_ref")
    safe_provider = _safe_label(provider or profile.provider, field="provider")
    tool_name = _ACTION_TOOL[normalized_action]
    confirmation_required = normalized_action in {
        Gemma4CookbookAction.SERVE,
        Gemma4CookbookAction.STOP,
        Gemma4CookbookAction.ADOPT,
    }
    live_required = confirmation_required
    args = _args_for_action(
        normalized_action,
        model_ref=safe_model,
        preset_name=preset_name,
        session_id=session_id,
        host=host,
        port=port,
        tmux_session=tmux_session,
        confirmed=bool(operator_go and (live_go or not live_required)),
    )
    reason = "read_only_status" if normalized_action is Gemma4CookbookAction.STATUS else (
        "ready_for_cookbook_tool" if operator_go and live_go else "awaiting_operator_live_go"
    )
    return Gemma4CookbookControlPlan(
        action=normalized_action,
        model_ref=safe_model,
        provider=safe_provider,
        tool_name=tool_name,
        args=args,
        operator_confirmation_required=confirmation_required,
        live_go_required=live_required,
        ui_placement_required=False,
        reason=reason,
    )


def _args_for_action(
    action: Gemma4CookbookAction,
    *,
    model_ref: str,
    preset_name: str,
    session_id: str,
    host: str,
    port: int | None,
    tmux_session: str,
    confirmed: bool,
) -> dict[str, Any]:
    if action is Gemma4CookbookAction.STATUS:
        return {}
    if action is Gemma4CookbookAction.SERVE:
        return {
            "name": _safe_label(preset_name, field="preset_name"),
            "confirmed": confirmed,
            "operator_go": confirmed,
            "maintenance_model_ref": model_ref,
        }
    if action is Gemma4CookbookAction.STOP:
        return {
            "session_id": _safe_label(session_id, field="session_id"),
            "confirmed": confirmed,
            "operator_go": confirmed,
        }
    if action is Gemma4CookbookAction.ADOPT:
        if not port or int(port) <= 0:
            raise Gemma4CookbookControlError("adopt requires a positive port")
        return {
            "host": _safe_label(host, field="host"),
            "tmux_session": _safe_label(tmux_session, field="tmux_session"),
            "model": model_ref,
            "port": int(port),
            "confirmed": confirmed,
            "operator_go": confirmed,
        }
    raise Gemma4CookbookControlError("unsupported action")


def _normalize_action(action: Gemma4CookbookAction | str) -> Gemma4CookbookAction:
    if isinstance(action, Gemma4CookbookAction):
        return action
    token = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return Gemma4CookbookAction(token)
    except ValueError as exc:
        raise Gemma4CookbookControlError("unsupported action") from exc


def _safe_label(value: Any, *, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise Gemma4CookbookControlError(f"{field} is required")
    lowered = text.lower()
    if any(marker in lowered for marker in ("password", "token", "api_key", "authorization", "cookie")):
        raise Gemma4CookbookControlError(f"{field} contains forbidden marker")
    if not _SAFE_LABEL_RE.fullmatch(text):
        raise Gemma4CookbookControlError(f"{field} must be a safe label")
    return text[:160]
