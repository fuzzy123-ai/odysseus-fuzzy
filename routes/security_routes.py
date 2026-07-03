"""Security and privacy runtime routes.

These routes expose a small browser-safe contract for global DSGVO/Secure Data
Mode state. They intentionally return policy metadata only, never raw settings,
tokens, chat ids, provider URLs, or private content.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.privacy_observability import privacy_runtime_health
from src.privacy_runtime import DSGVO_MODE_ENV, is_dsgvo_mode_enabled, truthy
from src.settings import load_settings
from src.settings_service import SettingsServiceError, set_setting


class DsgvoModeRequest(BaseModel):
    enabled: bool | None = None


def _env_forces_dsgvo() -> bool:
    return truthy(os.environ.get(DSGVO_MODE_ENV))


def _settings_snapshot() -> dict[str, Any]:
    settings = dict(load_settings() or {})
    return {
        "dsgvo_mode": bool(settings.get("dsgvo_mode")),
        "gdpr_mode": truthy(settings.get("gdpr_mode")),
    }


def _status_payload(*, requested: bool | None = None, before: bool | None = None) -> dict[str, Any]:
    settings = _settings_snapshot()
    active = is_dsgvo_mode_enabled(settings=settings)
    health = privacy_runtime_health(settings=settings)
    meta = dict(health.get("meta") or {})
    forced_active = bool(_env_forces_dsgvo() or settings.get("gdpr_mode"))
    payload = {
        "ok": True,
        "status": "active" if active else "inactive",
        "dsgvo_mode": active,
        "setting_enabled": bool(settings.get("dsgvo_mode")),
        "forced_active": forced_active,
        "local_only_required": bool(meta.get("local_only_required")),
        "effective_security_mode": str(meta.get("effective_security_mode") or ("secure" if active else "normal")),
        "required_provider_scope": str(meta.get("required_provider_scope") or ("local_only" if active else "default")),
        "external_io_allowed": not active,
        "settings_values_visible": False,
        "telegram_command": "/dsgvo",
        "chat_feedback_modes": ["status_chip", "slash_reply", "telegram_pinned_status"],
        "reason": (
            "dsgvo_mode_forced_active"
            if forced_active and active and not settings.get("dsgvo_mode")
            else ("dsgvo_mode_enabled" if active else "dsgvo_mode_disabled")
        ),
    }
    if requested is not None:
        payload["requested"] = bool(requested)
    if before is not None:
        payload["before"] = bool(before)
        payload["changed"] = bool(before) != bool(active)
    return payload


def setup_security_routes() -> APIRouter:
    router = APIRouter(prefix="/api/security", tags=["security"])

    @router.get("/dsgvo/status")
    async def get_dsgvo_status(_request: Request):
        return _status_payload()

    @router.post("/dsgvo/toggle")
    async def toggle_dsgvo_mode(request: Request):
        require_admin(request)
        before = is_dsgvo_mode_enabled(settings=_settings_snapshot())
        return await set_dsgvo_mode(request, DsgvoModeRequest(enabled=not before))

    @router.post("/dsgvo")
    async def set_dsgvo_mode(request: Request, body: DsgvoModeRequest):
        require_admin(request)
        if body.enabled is None:
            raise HTTPException(400, "enabled is required")
        before = is_dsgvo_mode_enabled(settings=_settings_snapshot())
        try:
            result = set_setting(
                "dsgvo_mode",
                bool(body.enabled),
                scope="global",
                actor="ui",
                confirmed=True,
            )
        except SettingsServiceError as exc:
            raise HTTPException(400, exc.to_dict()) from exc
        if not result.get("ok"):
            raise HTTPException(400, result)
        return _status_payload(requested=bool(body.enabled), before=before)

    return router
