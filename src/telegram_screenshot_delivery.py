"""Safe Telegram screenshot artifact delivery packets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from src.artifact_integrity import ArtifactIntegrityError, inspect_image_artifact, safe_artifact_ref


TELEGRAM_SCREENSHOT_DELIVERY_SCHEMA = "odysseus.telegram_screenshot_delivery.v1"
DEFAULT_SCREENSHOT_ARTIFACT_ROOTS = ("data/reports/autonomous_coding_agent/", "reports/")

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SECRET_RE = re.compile(r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})")
_HOST_PATH_RE = re.compile(r"(?i)(^[a-z]:[\\/]|^/|/home/|/opt/|/users/|~[\\/])")


@dataclass(frozen=True, slots=True)
class TelegramScreenshotDeliveryPacket:
    artifact_ref: str
    filename: str
    caption: str
    mime_type: str
    content_hash: str
    size_bytes: int
    integrity_status: str
    delivery_mode: str
    dispatch_allowed: bool
    blocker: str = ""
    visual_evidence: Mapping[str, Any] | None = None
    raw_content_visible: bool = False
    schema: str = TELEGRAM_SCREENSHOT_DELIVERY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "artifact_ref": self.artifact_ref,
            "filename": self.filename,
            "caption": self.caption,
            "mime_type": self.mime_type,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "integrity_status": self.integrity_status,
            "delivery_mode": self.delivery_mode,
            "dispatch_allowed": self.dispatch_allowed,
            "blocker": self.blocker,
            "visual_evidence": dict(self.visual_evidence or {}),
            "raw_content_visible": False,
        }


def build_telegram_screenshot_live_gate_packet(
    delivery_packet: TelegramScreenshotDeliveryPacket | Mapping[str, Any],
    *,
    operator_go_phrase: str = "GO telegram_screenshot_delivery bounded smoke",
) -> dict[str, Any]:
    """Return a redacted operator gate packet without performing live dispatch."""

    packet = delivery_packet.to_dict() if isinstance(delivery_packet, TelegramScreenshotDeliveryPacket) else dict(delivery_packet)
    blocker = str(packet.get("blocker") or "")
    integrity_status = str(packet.get("integrity_status") or "")
    if integrity_status != "verified":
        status = "blocked"
        decision = "fix_or_regenerate_artifact"
    elif blocker == "reply_gate_disabled":
        status = "needs_reply_gate"
        decision = "enable_reply_gate_before_operator_go"
    elif blocker == "telegram_target_not_configured":
        status = "needs_target"
        decision = "configure_allowed_telegram_target"
    elif packet.get("dispatch_allowed") is True:
        status = "ready_for_operator_go"
        decision = "operator_go_required"
    else:
        status = "blocked"
        decision = "review_delivery_packet"
    return {
        "schema": "odysseus.telegram_screenshot_live_gate.v1",
        "kind": "telegram_screenshot_delivery_live_gate",
        "status": status,
        "decision": decision,
        "operator_live_go_required": True,
        "operator_go_phrase": operator_go_phrase,
        "live_actions_performed": False,
        "delivery_packet": packet,
        "raw_content_visible": False,
        "token_value_visible": False,
        "chat_target_value_visible": False,
    }


def build_telegram_screenshot_delivery_packet(
    artifact_ref: Any,
    *,
    repo_root: Path | str | None,
    filename: Any = "",
    caption: Any = "",
    reply_enabled: bool = False,
    target_configured: bool = False,
    allowed_artifact_roots: tuple[str, ...] = DEFAULT_SCREENSHOT_ARTIFACT_ROOTS,
    visual_evidence: Mapping[str, Any] | None = None,
) -> TelegramScreenshotDeliveryPacket:
    """Validate a screenshot artifact before it can be dispatched to Telegram."""

    ref = safe_artifact_ref(artifact_ref)
    _ensure_allowed_root(ref, allowed_artifact_roots)
    visual = _safe_visual_evidence(visual_evidence)
    try:
        integrity = inspect_image_artifact(ref, repo_root=repo_root)
    except ArtifactIntegrityError as exc:
        return TelegramScreenshotDeliveryPacket(
            artifact_ref=ref,
            filename=_safe_filename(filename, fallback=Path(ref).name),
            caption=_safe_caption(caption),
            mime_type="application/octet-stream",
            content_hash="",
            size_bytes=0,
            integrity_status="blocked",
            delivery_mode="photo",
            dispatch_allowed=False,
            blocker=str(exc),
            visual_evidence=visual,
        )

    blocker = ""
    if not reply_enabled:
        blocker = "reply_gate_disabled"
    elif not target_configured:
        blocker = "telegram_target_not_configured"
    return TelegramScreenshotDeliveryPacket(
        artifact_ref=integrity.artifact_ref,
        filename=_safe_filename(filename, fallback=Path(integrity.artifact_ref).name),
        caption=_safe_caption(caption),
        mime_type=integrity.mime_hint,
        content_hash=integrity.content_hash,
        size_bytes=integrity.size_bytes,
        integrity_status=integrity.status,
        delivery_mode="photo",
        dispatch_allowed=not blocker and integrity.status == "verified",
        blocker=blocker,
        visual_evidence=visual,
    )


def _ensure_allowed_root(ref: str, roots: tuple[str, ...]) -> None:
    normalized_roots = tuple(root.replace("\\", "/").strip("/") + "/" for root in roots if str(root).strip())
    if not normalized_roots or not any(ref.startswith(root) for root in normalized_roots):
        raise ValueError("telegram screenshot artifact_ref is outside allowed roots")


def _safe_filename(value: Any, *, fallback: str) -> str:
    text = str(value or fallback or "screenshot.png").strip()
    text = _SAFE_FILENAME_RE.sub("-", text).strip(".-")
    suffix = Path(text).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        text = f"{Path(text).stem or 'screenshot'}.png"
    return text[:120] or "screenshot.png"


def _safe_caption(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) > 240:
        text = text[:237].rstrip() + "..."
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return ""
    return text


def _safe_visual_evidence(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    allowed = {}
    for key in ("schema", "artifact_ref", "width", "height", "viewport", "image_hash", "redaction_policy", "raw_content_visible"):
        if key in value:
            allowed[key] = value[key]
    encoded = repr(allowed)
    if _SECRET_RE.search(encoded) or _HOST_PATH_RE.search(encoded):
        return {}
    allowed["raw_content_visible"] = False
    return allowed
