"""Security and privacy runtime routes.

These routes expose a small browser-safe contract for global DSGVO/Secure Data
Mode state. They intentionally return policy metadata only, never raw settings,
tokens, chat ids, provider URLs, or private content.
"""

from __future__ import annotations

import os
import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.privacy_observability import privacy_runtime_health
from src.privacy_runtime import DSGVO_MODE_ENV, is_dsgvo_mode_enabled, truthy
from src.security_incident_delivery import (
    DEFAULT_DELIVERY_TIMEOUT_SECONDS,
    SecurityIncidentDeliveryAdapter,
    TrustedTelegramDeliveryReadiness,
    build_server_owned_delivery_request,
    is_sealed_security_incident_delivery_transport,
)
from src.security_incident_telegram_transport import is_production_security_incident_telegram_transport
from src.settings import load_settings
from src.settings_service import SettingsServiceError, set_setting
from src.ops_timeline_adapters import create_default_security_incident_store


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


def setup_security_routes(
    *,
    incident_store: Any | None = None,
    owner_resolver: Any | None = None,
    incident_delivery_transport: Any | None = None,
    incident_delivery_readiness: Any | None = None,
    incident_delivery_timeout_seconds: int | None = None,
) -> APIRouter:
    """Register DSGVO plus explicitly scoped durable incident surfaces.

    Ownership is intentionally supplied by server configuration (or app state),
    never by request JSON.  A missing store or resolver fails closed.
    """
    router = APIRouter(prefix="/api/security", tags=["security"])
    configured_store = incident_store if incident_store is not None else create_default_security_incident_store()

    def _store(request: Request) -> Any | None:
        candidate = incident_store if incident_store is not None else getattr(request.app.state, "security_incident_store", configured_store)
        required = ("get_incident", "get_action", "transition", "audit_events")
        return candidate if candidate is not None and all(callable(getattr(candidate, item, None)) for item in required) else None

    def _is_admin(request: Request) -> bool:
        manager = getattr(request.app.state, "auth_manager", None)
        user = getattr(request.state, "current_user", None)
        try:
            return bool(manager and getattr(manager, "is_configured", False) and user and manager.is_admin(user))
        except Exception:
            return False

    def _owner_allowed(request: Request, incident_id: str) -> bool:
        resolver = owner_resolver if owner_resolver is not None else getattr(request.app.state, "security_incident_owner_resolver", None)
        user = getattr(request.state, "current_user", None)
        if _is_admin(request):
            return True
        if not user or not callable(resolver):
            return False
        try:
            return resolver(request, incident_id) == user
        except Exception:
            return False

    def _incident_summary(store: Any, incident_id: str) -> dict[str, Any] | None:
        try:
            record = store.get_incident(incident_id)
            action_ids = {
                event.action_id for event in store.audit_events()
                if event.incident_id == record.incident_id and event.action_id
            }
        except Exception:
            return None
        action_limit = 100
        return {
            "incident_id": record.incident_id,
            "version": record.version,
            "action_count": min(len(action_ids), action_limit),
            "action_count_truncated": len(action_ids) > action_limit,
            "raw_content_visible": False,
        }

    def _delivery_dependencies(request: Request) -> tuple[Any, TrustedTelegramDeliveryReadiness, int] | None:
        runtime_transport = incident_delivery_transport is None
        transport = (
            getattr(request.app.state, "security_incident_delivery_transport", None)
            if runtime_transport else incident_delivery_transport
        )
        readiness = (
            incident_delivery_readiness
            if incident_delivery_readiness is not None
            else getattr(request.app.state, "security_incident_delivery_readiness", None)
        )
        timeout_seconds = (
            incident_delivery_timeout_seconds
            if incident_delivery_timeout_seconds is not None
            else getattr(request.app.state, "security_incident_delivery_timeout_seconds", DEFAULT_DELIVERY_TIMEOUT_SECONDS)
        )
        if (
            not (
                is_production_security_incident_telegram_transport(transport)
                if runtime_transport else is_sealed_security_incident_delivery_transport(transport)
            )
            or not isinstance(readiness, TrustedTelegramDeliveryReadiness)
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 60
        ):
            return None
        return transport, readiness, timeout_seconds

    @router.get("/incidents/{incident_id}")
    async def read_security_incident(request: Request, incident_id: str):
        store = _store(request)
        if store is None:
            raise HTTPException(503, "Security incident service unavailable")
        summary = _incident_summary(store, incident_id)
        if summary is None:
            raise HTTPException(404, "Security incident unavailable")
        if not _owner_allowed(request, summary["incident_id"]):
            raise HTTPException(403, "Security incident access denied")
        return {
            "status": "success", "read_only": True, "writes_performed": False,
            "raw_content_visible": False, "incident": summary,
        }

    @router.post("/incidents/{incident_id}/actions/{action_id}/prepare")
    async def prepare_security_action(request: Request, incident_id: str, action_id: str):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid security action prepare request") from None
        if not isinstance(body, dict) or set(body) != {"expected_version"}:
            raise HTTPException(422, "Invalid security action prepare request")
        expected_version = body.get("expected_version")
        if type(expected_version) is not int or expected_version < 1:
            raise HTTPException(422, "Invalid security action prepare request")
        store = _store(request)
        if store is None:
            raise HTTPException(503, "Security incident service unavailable")
        summary = _incident_summary(store, incident_id)
        if summary is None:
            raise HTTPException(404, "Security incident unavailable")
        if not _owner_allowed(request, summary["incident_id"]):
            raise HTTPException(403, "Security incident access denied")
        try:
            action = store.get_action(action_id)
        except Exception:
            raise HTTPException(404, "Security action unavailable") from None
        if action.incident_id != summary["incident_id"]:
            raise HTTPException(404, "Security action unavailable")
        audit_ref = "audit:sha256:" + hashlib.sha256(
            f"{action.action_id}:{expected_version}:{secrets.token_hex(16)}".encode("utf-8")
        ).hexdigest()
        try:
            prepared = store.transition(
                action_id=action.action_id,
                expected_version=expected_version,
                target_state="prepared",
                audit_ref=audit_ref,
            )
        except Exception:
            # Includes expiry, stale/replay and invalid state.  The durable
            # store owns the precise reason; clients only get a fail-closed response.
            raise HTTPException(409, "Security action preparation unavailable") from None
        return {
            "status": "prepared", "read_only": False, "writes_performed": True,
            "approved": False, "executed": False, "delivered": False,
            "action": {"action_id": prepared.action_id, "state": prepared.state, "version": prepared.version},
            "raw_content_visible": False,
        }

    @router.post("/incidents/{incident_id}/actions/{action_id}/delivery")
    async def deliver_security_action(request: Request, incident_id: str, action_id: str):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(422, "Invalid security incident delivery request") from None
        if not isinstance(body, dict) or set(body) != {"expected_version"}:
            raise HTTPException(422, "Invalid security incident delivery request")
        expected_version = body.get("expected_version")
        if type(expected_version) is not int or expected_version < 1:
            raise HTTPException(422, "Invalid security incident delivery request")
        store = _store(request)
        if store is None:
            raise HTTPException(503, "Security incident service unavailable")
        summary = _incident_summary(store, incident_id)
        if summary is None:
            raise HTTPException(404, "Security incident unavailable")
        if not _owner_allowed(request, summary["incident_id"]):
            raise HTTPException(403, "Security incident access denied")
        try:
            action = store.get_action(action_id)
        except Exception:
            raise HTTPException(404, "Security action unavailable") from None
        if action.incident_id != summary["incident_id"]:
            raise HTTPException(404, "Security action unavailable")
        if action.state != "approved" or action.version != expected_version:
            raise HTTPException(409, "Security action delivery unavailable")
        dependencies = _delivery_dependencies(request)
        if dependencies is None:
            raise HTTPException(503, "Security incident delivery unavailable")
        transport, readiness, timeout_seconds = dependencies
        try:
            delivery_request = build_server_owned_delivery_request(
                action, readiness, timeout_seconds=timeout_seconds,
            )
        except Exception:
            raise HTTPException(409, "Security action delivery unavailable") from None
        return SecurityIncidentDeliveryAdapter(store, transport=transport).attempt(delivery_request)

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
