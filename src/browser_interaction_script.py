"""Auditable bounded browser interaction scripts."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


class BrowserInteractionScriptError(ValueError):
    """Raised when an interaction script step is unsafe."""


_ALLOWED_ACTIONS = {"click", "type", "wait", "screenshot", "navigate"}


def build_interaction_script(steps: Iterable[Mapping[str, Any]], *, max_steps: int = 25) -> dict[str, Any]:
    capped = max(1, min(int(max_steps or 25), 100))
    normalized: list[dict[str, Any]] = []
    for step in steps:
        if len(normalized) >= capped:
            break
        normalized.append(_normalize_step(step))
    if not normalized:
        raise BrowserInteractionScriptError("interaction script must contain at least one step")
    return {
        "schema": "odysseus.browser_interaction_script.v1",
        "steps": tuple(normalized),
        "step_count": len(normalized),
        "raw_content_visible": False,
    }


def _normalize_step(step: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(step, Mapping):
        raise BrowserInteractionScriptError("step must be a mapping")
    action = _safe_token(step.get("action") or "")
    if action not in _ALLOWED_ACTIONS:
        raise BrowserInteractionScriptError("action is not allowed")
    selector = _safe_selector(step.get("selector") or "", allow_empty=action in {"wait", "screenshot", "navigate"})
    item = {"action": action, "selector": selector, "timeout_ms": _timeout(step.get("timeout_ms")), "raw_content_visible": False}
    if action == "type":
        item["text_chars"] = len(str(step.get("text") or ""))
        if item["text_chars"] <= 0:
            raise BrowserInteractionScriptError("type step needs text")
        if _looks_secret(step.get("text")):
            raise BrowserInteractionScriptError("type text appears sensitive")
    if action == "navigate":
        item["target"] = _safe_navigation_target(step.get("target") or "")
    return item


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", text):
        raise BrowserInteractionScriptError("token is invalid")
    return text


def _safe_selector(value: Any, *, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise BrowserInteractionScriptError("selector must not be empty")
    if len(text) > 160 or any(marker in text.lower() for marker in ("password", "token", "secret", "cookie")):
        raise BrowserInteractionScriptError("selector is unsafe")
    if re.search(r"[\r\n<>]", text):
        raise BrowserInteractionScriptError("selector contains unsafe characters")
    return text


def _safe_navigation_target(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"https?://[a-z0-9.-]{1,253}(/[a-z0-9._~:/@!$&'()*+,;=%-]*)?", text.lower()) or "?" in text:
        raise BrowserInteractionScriptError("navigation target is unsafe")
    return text


def _timeout(value: Any) -> int:
    try:
        parsed = int(value or 5000)
    except (TypeError, ValueError):
        parsed = 5000
    return max(100, min(parsed, 60_000))


def _looks_secret(value: Any) -> bool:
    lowered = str(value or "").lower()
    return any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "token="))
