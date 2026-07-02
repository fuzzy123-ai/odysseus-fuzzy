"""Bridge visual evidence into OCR/caption work requests."""

from __future__ import annotations

import re
from typing import Any, Mapping


class VisualOcrBridgeError(ValueError):
    """Raised when OCR bridge input is unsafe."""


def build_visual_ocr_request(
    screenshot: Mapping[str, Any],
    *,
    prefer_local: bool = True,
    language_hint: Any = "deu+eng",
    reason: Any = "visible_text_not_in_dom",
) -> dict[str, Any]:
    if not isinstance(screenshot, Mapping):
        raise VisualOcrBridgeError("screenshot must be a mapping")
    artifact_ref = str(screenshot.get("artifact_ref") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}", artifact_ref) or ".." in artifact_ref.split("/") or artifact_ref.startswith("/"):
        raise VisualOcrBridgeError("artifact ref is unsafe")
    return {
        "schema": "odysseus.visual_ocr_request.v1",
        "artifact_ref": artifact_ref,
        "image_hash": str(screenshot.get("image_hash") or "")[:80],
        "engine_preference": "local_ocr_first" if prefer_local else "policy_routed",
        "language_hint": _safe_language(language_hint),
        "reason": _safe_reason(reason),
        "raw_content_visible": False,
    }


def _safe_language(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z+]{2,32}", text):
        raise VisualOcrBridgeError("language hint is invalid")
    return text


def _safe_reason(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    if not re.fullmatch(r"[a-z0-9_.:-]{1,80}", text):
        raise VisualOcrBridgeError("reason is invalid")
    return text
