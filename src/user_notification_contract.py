"""Safe request contract for Odysseus user notifications.

The contract deliberately keeps delivery secrets and channel targets outside
agent/MCP inputs. Callers may request a notification; Odysseus decides whether
and how to dispatch it from server-side configuration.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


ALLOWED_SEVERITIES = {"info", "success", "warning", "error"}
ALLOWED_CHANNELS = {"auto", "telegram"}
FORBIDDEN_KEY_TOKENS = {
    "apikey",
    "authorization",
    "bearer",
    "botsecret",
    "bottoken",
    "chatid",
    "credential",
    "credentials",
    "destination",
    "password",
    "recipient",
    "secret",
    "target",
    "telegramchat",
    "telegramchatid",
    "telegramtoken",
    "token",
}
MAX_MESSAGE_CHARS = 1200
MAX_EVENT_CHARS = 80
MAX_METADATA_ITEMS = 12


class NotificationContractError(ValueError):
    """Raised when a notification request violates the safe boundary."""


@dataclass(frozen=True)
class UserNotificationRequest:
    event: str
    message: str
    severity: str = "info"
    channel: str = "auto"
    dry_run: bool = True
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UserNotificationDecision:
    status: str
    dispatch_allowed: bool
    reason: str
    request: UserNotificationRequest
    resolved_channel: str
    rendered_text: str

    def as_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["token_value_visible"] = False
        payload["chat_target_value_visible"] = False
        return payload


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _assert_no_forbidden_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = _normalized_key(key)
            if normalized in FORBIDDEN_KEY_TOKENS:
                raise NotificationContractError(f"Forbidden notification key at {path}.{key}")
            _assert_no_forbidden_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_forbidden_keys(nested, path=f"{path}[{index}]")


def _clean_text(value: Any, *, fallback: str = "", max_chars: int = MAX_MESSAGE_CHARS) -> str:
    text = " ".join(str(value if value is not None else fallback).split())
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _clean_slug(value: Any, *, fallback: str = "odysseus_notification") -> str:
    raw = _clean_text(value, fallback=fallback, max_chars=MAX_EVENT_CHARS).lower()
    slug = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._-")
    return slug or fallback


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _metadata_to_public_strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, item in list(value.items())[:MAX_METADATA_ITEMS]:
        clean_key = _clean_slug(key, fallback="metadata")[:40]
        if isinstance(item, (dict, list, tuple)):
            rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(item)
        out[clean_key] = _clean_text(rendered, max_chars=180)
    return out


def build_user_notification_request(payload: Mapping[str, Any] | str) -> UserNotificationRequest:
    if isinstance(payload, str):
        payload = {"message": payload}
    if not isinstance(payload, Mapping):
        raise NotificationContractError("Notification payload must be an object or text")
    _assert_no_forbidden_keys(payload)

    event = _clean_slug(
        payload.get("event")
        or payload.get("event_type")
        or payload.get("run_label")
        or "odysseus_notification"
    )
    message = _clean_text(payload.get("message") or payload.get("summary") or payload.get("text") or "")
    if not message:
        raise NotificationContractError("Notification message is required")

    severity = _clean_slug(payload.get("severity") or "info", fallback="info")
    if severity not in ALLOWED_SEVERITIES:
        severity = "info"

    channel = _clean_slug(
        payload.get("channel") or payload.get("requested_channel_class") or "auto",
        fallback="auto",
    )
    if channel in {"completion_notice", "completion"}:
        channel = "auto"
    if channel not in ALLOWED_CHANNELS:
        channel = "auto"

    return UserNotificationRequest(
        event=event,
        message=message,
        severity=severity,
        channel=channel,
        dry_run=_coerce_bool(payload.get("dry_run"), default=True),
        metadata=_metadata_to_public_strings(payload.get("metadata") or {}),
    )


def render_user_notification_text(request: UserNotificationRequest) -> str:
    prefix = f"[Odysseus][{request.severity}] {request.event}"
    if not request.metadata:
        return f"{prefix}: {request.message}"
    metadata = " ".join(f"{key}={value}" for key, value in sorted(request.metadata.items()))
    return f"{prefix}: {request.message}\n{metadata}"


def build_user_notification_decision(
    payload: Mapping[str, Any] | str,
    *,
    configured_channels: Sequence[str] = ("telegram",),
    live_dispatch_enabled: bool = False,
    target_configured: bool = False,
) -> UserNotificationDecision:
    request = build_user_notification_request(payload)
    configured = tuple(channel for channel in configured_channels if channel in ALLOWED_CHANNELS - {"auto"})
    resolved_channel = configured[0] if request.channel == "auto" and configured else request.channel
    rendered_text = render_user_notification_text(request)

    if request.dry_run:
        return UserNotificationDecision(
            status="dry_run",
            dispatch_allowed=False,
            reason="dry_run_requested",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    if resolved_channel not in configured:
        return UserNotificationDecision(
            status="blocked",
            dispatch_allowed=False,
            reason="channel_not_configured",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    if not live_dispatch_enabled:
        return UserNotificationDecision(
            status="blocked",
            dispatch_allowed=False,
            reason="live_dispatch_disabled",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    if not target_configured:
        return UserNotificationDecision(
            status="blocked",
            dispatch_allowed=False,
            reason="notification_target_missing",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    return UserNotificationDecision(
        status="accepted",
        dispatch_allowed=True,
        reason="ready_for_server_side_dispatch",
        request=request,
        resolved_channel=resolved_channel,
        rendered_text=rendered_text,
    )
