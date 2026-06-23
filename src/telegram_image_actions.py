"""Default-off Telegram image actions backed by the image tools worker client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.image_tools_worker import ImageToolsWorkerClient, ImageToolsWorkerResult, ImageToolsWorkerSettings


class TelegramImageWorkerClient(Protocol):
    def remove_background(self, image_bytes: bytes, hint_mask_bytes: bytes | None = None) -> ImageToolsWorkerResult:
        ...


@dataclass(frozen=True, slots=True)
class TelegramImageActionPlan:
    allowed: bool
    status: str
    reason: str
    action: str
    file_handle: str
    file_unique_handle: str
    file_size: int
    raw_identifiers_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "reason": self.reason,
            "action": self.action,
            "file_handle_present": bool(self.file_handle),
            "file_unique_handle_present": bool(self.file_unique_handle),
            "file_size": self.file_size,
            "raw_identifiers_visible": self.raw_identifiers_visible,
        }


def select_telegram_photo_variant(photo: Any) -> dict[str, Any]:
    """Choose the largest Telegram photo variant without exposing identifiers."""

    if not isinstance(photo, list):
        return {}
    variants = [item for item in photo if isinstance(item, dict)]
    if not variants:
        return {}
    return max(
        variants,
        key=lambda item: (
            int(item.get("file_size") or 0),
            int(item.get("width") or 0) * int(item.get("height") or 0),
        ),
    )


def plan_telegram_image_action(
    message: dict[str, Any],
    *,
    enabled: bool,
    action: str = "remove_background",
    max_bytes: int = 10_000_000,
) -> TelegramImageActionPlan:
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    file_size = _to_int(media.get("file_size"))
    if message.get("kind") != "image":
        return _plan(False, "not_image", "message is not a Telegram image", action, media, file_size)
    if action != "remove_background":
        return _plan(False, "unsupported_action", "only remove_background is supported", action, media, file_size)
    if message.get("chat_allowed") is not True:
        return _plan(False, "blocked_chat", "chat is not allowlisted", action, media, file_size)
    if not enabled:
        return _plan(False, "disabled", "Telegram image actions are disabled by default", action, media, file_size)
    if not media.get("file_handle"):
        return _plan(False, "missing_file_handle", "redacted image file handle is missing", action, media, file_size)
    if file_size and file_size > max_bytes:
        return _plan(False, "payload_too_large", "Telegram image exceeds configured action size limit", action, media, file_size)
    return _plan(True, "ready", "Telegram image action is ready for an injected bytes provider", action, media, file_size)


def run_telegram_image_action(
    message: dict[str, Any],
    *,
    enabled: bool,
    image_bytes_provider: Callable[[str], bytes] | None = None,
    worker_client: TelegramImageWorkerClient | None = None,
    max_bytes: int = 10_000_000,
) -> dict[str, Any] | None:
    """Run a fakeable image action without Telegram download or live worker by default."""

    if message.get("kind") != "image":
        return None
    plan = plan_telegram_image_action(message, enabled=enabled, max_bytes=max_bytes)
    payload: dict[str, Any] = {
        "plan": plan.to_dict(),
        "worker": {
            "called": False,
            "ok": False,
            "status": "not_started",
            "output_image_present": False,
            "raw_image_visible": False,
        },
    }
    if not plan.allowed:
        return payload
    if image_bytes_provider is None:
        payload["worker"]["status"] = "waiting_image_bytes_provider"
        return payload
    try:
        image_bytes = image_bytes_provider(plan.file_handle)
    except Exception as exc:
        payload["worker"]["status"] = "image_bytes_failed"
        payload["worker"]["error"] = str(exc)[:160]
        return payload
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        payload["worker"]["status"] = "invalid_image_bytes"
        return payload
    client = worker_client or ImageToolsWorkerClient(ImageToolsWorkerSettings.from_env())
    result = client.remove_background(bytes(image_bytes))
    payload["worker"].update(
        {
            "called": True,
            "ok": result.ok,
            "status": "ok" if result.ok else "worker_error",
            "output_image_present": bool(result.image_bytes),
            "error_code": result.error_code or "",
            "raw_image_visible": False,
        }
    )
    return payload


def _plan(
    allowed: bool,
    status: str,
    reason: str,
    action: str,
    media: dict[str, Any],
    file_size: int,
) -> TelegramImageActionPlan:
    return TelegramImageActionPlan(
        allowed=allowed,
        status=status,
        reason=reason,
        action=action,
        file_handle=str(media.get("file_handle") or ""),
        file_unique_handle=str(media.get("file_unique_handle") or ""),
        file_size=file_size,
    )


def _to_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
