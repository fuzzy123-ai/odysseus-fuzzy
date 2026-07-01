"""Telegram model selection helpers."""

from __future__ import annotations

import os
from typing import Callable, Any


def resolve_telegram_model_spec(
    *,
    env: dict[str, str] | None = None,
    get_setting: Callable[[str, Any], Any] | None = None,
) -> str:
    """Resolve the model spec used for new Telegram sessions.

    Precedence:
    1. ``TELEGRAM_MODEL_SPEC`` environment override.
    2. Dedicated ``telegram_model_spec`` setting.
    3. General ``default_model`` setting.
    """

    env_values = env if env is not None else os.environ
    env_spec = str(env_values.get("TELEGRAM_MODEL_SPEC") or "").strip()
    if env_spec:
        return env_spec

    if get_setting is None:
        from src.settings import get_setting as _get_setting

        get_setting = _get_setting

    return str(
        get_setting("telegram_model_spec", "")
        or get_setting("default_model", "")
        or ""
    ).strip()
