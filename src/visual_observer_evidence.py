"""Screenshot evidence metadata for no-GPU visual observation."""

from __future__ import annotations

import re
from typing import Any, Mapping


class VisualObserverEvidenceError(ValueError):
    """Raised when screenshot evidence metadata is unsafe."""


def build_screenshot_evidence(
    *,
    artifact_ref: Any,
    width: Any,
    height: Any,
    viewport: Mapping[str, Any] | None = None,
    image_hash: Any = "",
    selector_focus: Any = "",
    redaction_policy: Any = "public_page",
) -> dict[str, Any]:
    payload = {
        "schema": "odysseus.visual_observer.screenshot_evidence.v1",
        "artifact_ref": _artifact_ref(artifact_ref),
        "width": _dimension(width),
        "height": _dimension(height),
        "viewport": {
            "width": _dimension((viewport or {}).get("width") or width),
            "height": _dimension((viewport or {}).get("height") or height),
        },
        "image_hash": _safe_hash(image_hash),
        "selector_focus": _safe_selector(selector_focus),
        "redaction_policy": _safe_token(redaction_policy),
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _artifact_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[a-z]:", text.lower()) or ".." in text.split("/"):
        raise VisualObserverEvidenceError("artifact ref is unsafe")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}", text):
        raise VisualObserverEvidenceError("artifact ref is invalid")
    return text


def _dimension(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise VisualObserverEvidenceError("dimension must be an integer") from exc
    if parsed < 1 or parsed > 20000:
        raise VisualObserverEvidenceError("dimension out of range")
    return parsed


def _safe_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.fullmatch(r"(sha256:)?[a-f0-9]{16,64}", text):
        raise VisualObserverEvidenceError("image hash is invalid")
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _safe_selector(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 160 or re.search(r"[\r\n<>]", text) or any(marker in text.lower() for marker in ("password", "token", "secret", "cookie")):
        raise VisualObserverEvidenceError("selector is unsafe")
    return text


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise VisualObserverEvidenceError("token is invalid")
    return text


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise VisualObserverEvidenceError("payload contains forbidden marker")
