"""Global privacy runtime helpers.

The existing secure-chat modules decide individual model/source/tool gates.
This module adds the app-wide DSGVO switch and turns it into the same
``ChatSecurityState`` shape those gates already understand.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from typing import Any, Mapping

from src.chat_security_state import ChatSecurityState, SecurityMode, normalize_security_mode

DSGVO_MODE_SETTING = "dsgvo_mode"
GDPR_MODE_SETTING = "gdpr_mode"
DSGVO_MODE_ENV = "ODYSSEUS_DSGVO_MODE"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "secure", "dsgvo", "gdpr"}


def truthy(value: Any) -> bool:
    """Return whether a setting/env value should be treated as enabled."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return False


def is_dsgvo_mode_enabled(settings: Mapping[str, Any] | None = None) -> bool:
    """Read the global DSGVO mode from settings or environment.

    ``settings`` is injectable so tests and hot paths do not need to touch
    ``data/settings.json``.
    """

    if settings is None:
        try:
            from src.settings import load_settings

            settings = load_settings()
        except Exception:
            settings = {}

    return (
        truthy(settings.get(DSGVO_MODE_SETTING))
        or truthy(settings.get(GDPR_MODE_SETTING))
        or truthy(os.environ.get(DSGVO_MODE_ENV))
    )


def runtime_requires_local_only(
    *,
    settings: Mapping[str, Any] | None = None,
    security_state: ChatSecurityState | None = None,
) -> bool:
    """Return whether current runtime policy requires local-only processing."""

    if is_dsgvo_mode_enabled(settings):
        return True
    return bool(security_state and security_state.security_mode == SecurityMode.SECURE)


def effective_security_mode(
    requested_mode: SecurityMode | str | None = None,
    *,
    settings: Mapping[str, Any] | None = None,
) -> SecurityMode:
    """Return the mode after applying the global DSGVO override."""

    if is_dsgvo_mode_enabled(settings):
        return SecurityMode.SECURE
    if requested_mode is None or str(requested_mode).strip() == "":
        return SecurityMode.NORMAL
    return normalize_security_mode(requested_mode)


def create_runtime_security_state(
    *,
    chat_id: Any = "runtime",
    thread_id: Any = "runtime",
    security_mode: SecurityMode | str | None = None,
    requested_by: Any = "privacy-runtime",
    settings: Mapping[str, Any] | None = None,
) -> ChatSecurityState:
    """Create a policy-compatible security state for the current runtime."""

    mode = effective_security_mode(security_mode, settings=settings)
    return ChatSecurityState.create(
        chat_id=str(chat_id or "runtime"),
        thread_id=str(thread_id or chat_id or "runtime"),
        security_mode=mode,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        requested_by=str(requested_by or "privacy-runtime"),
    )
