"""Sealed, server-owned Telegram transport for one fixed incident smoke body.

This module deliberately does not compose itself into application startup.  A
separate, explicitly authorized runtime layer must opt in to its factory.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from src.security_incident_notifications import (
    canonical_operator_notification_body_ref,
    canonical_operator_notification_smoke_body,
    canonical_operator_notification_target_class_ref,
)


_ISSUER = object()
_SAFE_MODE = re.compile(r"^[a-z_]{1,64}$")


class SecurityIncidentTelegramTransportError(RuntimeError):
    """Content-free rejection for target, transport, or provider ambiguity."""


class ProductionSecurityIncidentTelegramTransport:
    """A sealed issuer that resolves target and body only inside this module."""

    __slots__ = ()

    def __init__(self, *, _issuer: object) -> None:
        if _issuer is not _ISSUER:
            raise SecurityIncidentTelegramTransportError("security incident transport unavailable")

    def invoke(self, request: Mapping[str, Any]) -> dict[str, str]:
        if not _valid_request(request):
            raise SecurityIncidentTelegramTransportError("security incident transport unavailable")
        try:
            from src.user_notification_delivery import _configured_telegram_target
            from plugins.telegram.plugin import _chat_allowed
            from plugins.telegram.outbound import send_telegram_text

            target = _configured_telegram_target()
            if not target or not _chat_allowed(target):
                raise SecurityIncidentTelegramTransportError("security incident transport unavailable")
            # send_telegram_text owns its fixed 15-second low-level timeout.
            result = send_telegram_text(target, canonical_operator_notification_smoke_body())
        except SecurityIncidentTelegramTransportError:
            raise
        except Exception:
            raise SecurityIncidentTelegramTransportError("security incident transport unavailable") from None
        message_id = _validated_acknowledgement(result)
        if message_id is None:
            raise SecurityIncidentTelegramTransportError("security incident transport unavailable")
        return {"status": "acknowledged", "receipt_ref": _receipt_ref(request, message_id)}


def build_production_security_incident_telegram_transport(*, enabled: bool = False) -> ProductionSecurityIncidentTelegramTransport | None:
    """Return no transport unless a future server-only composition opts in."""
    if enabled is not True:
        return None
    return ProductionSecurityIncidentTelegramTransport(_issuer=_ISSUER)


def is_production_security_incident_telegram_transport(value: Any) -> bool:
    return isinstance(value, ProductionSecurityIncidentTelegramTransport)


def _valid_request(value: Any) -> bool:
    expected = {"schema", "action_id", "action_version", "body_ref", "channel", "approved_target_class_ref", "timeout_seconds", "raw_content_visible"}
    return bool(
        isinstance(value, Mapping) and set(value) == expected
        and value.get("schema") == "odysseus.security_incident_delivery_request.v1"
        and isinstance(value.get("action_id"), str) and isinstance(value.get("action_version"), int)
        and value.get("channel") == "telegram" and value.get("body_ref") == canonical_operator_notification_body_ref()
        and value.get("approved_target_class_ref") == canonical_operator_notification_target_class_ref()
        and type(value.get("timeout_seconds")) is int and 1 <= value["timeout_seconds"] <= 60
        and value.get("raw_content_visible") is False
    )


def _validated_acknowledgement(value: Any) -> int | None:
    expected = {"ok", "telegram_message_id", "telegram_message_ids", "delivery_mode", "formatting_mode", "parse_mode", "message_count", "max_reply_chunks", "truncated", "token_value_visible", "raw_rich_payload_visible"}
    if not isinstance(value, Mapping) or set(value) != expected or value.get("ok") is not True:
        return None
    message_id = value.get("telegram_message_id")
    message_ids = value.get("telegram_message_ids")
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        return None
    if not isinstance(message_ids, list) or len(message_ids) != 1 or message_ids[0] != message_id:
        return None
    if value.get("message_count") != 1 or value.get("truncated") is not False:
        return None
    if value.get("token_value_visible") is not False or value.get("raw_rich_payload_visible") is not False:
        return None
    if not all(isinstance(value.get(field), str) and _SAFE_MODE.fullmatch(value[field]) for field in ("delivery_mode", "formatting_mode")):
        return None
    if not isinstance(value.get("parse_mode"), str) or len(value["parse_mode"]) > 16:
        return None
    if isinstance(value.get("max_reply_chunks"), bool) or not isinstance(value.get("max_reply_chunks"), int) or not 1 <= value["max_reply_chunks"] <= 10:
        return None
    return message_id


def _receipt_ref(request: Mapping[str, Any], message_id: int) -> str:
    identity = "|".join((str(request["action_id"]), str(request["action_version"]), canonical_operator_notification_body_ref(), canonical_operator_notification_target_class_ref(), str(message_id)))
    return "receipt:sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


__all__ = [
    "ProductionSecurityIncidentTelegramTransport", "SecurityIncidentTelegramTransportError",
    "build_production_security_incident_telegram_transport", "is_production_security_incident_telegram_transport",
]
