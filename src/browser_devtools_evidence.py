"""Redacted DevTools-style browser evidence summaries."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


class BrowserDevtoolsEvidenceError(ValueError):
    """Raised when DevTools evidence would expose unsafe data."""


def summarize_console_events(events: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise BrowserDevtoolsEvidenceError("console event must be a mapping")
        text = _safe_message(event.get("message") or "")
        item = {
            "level": _safe_token(event.get("level") or "log", field="level"),
            "message_hash": _hash(text),
            "message_chars": len(text),
            "error_class": _error_class(text),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(item)
        result.append(item)
        if len(result) >= 100:
            break
    return tuple(result)


def summarize_network_events(events: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise BrowserDevtoolsEvidenceError("network event must be a mapping")
        host, path_class = _url_parts(event.get("url") or "")
        status = _safe_status(event.get("status"))
        item = {
            "host": host,
            "path_class": path_class,
            "method": _safe_token(event.get("method") or "GET", field="method").upper(),
            "status": status,
            "status_class": f"{status // 100}xx" if status else "unknown",
            "mime_family": _mime_family(event.get("mime_type") or event.get("resource_type") or ""),
            "duration_ms": _safe_duration(event.get("duration_ms")),
            "failed": bool(event.get("failed") or status >= 400),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(item)
        result.append(item)
        if len(result) >= 200:
            break
    return tuple(result)


def build_devtools_summary(*, console_events: Iterable[Mapping[str, Any]] = (), network_events: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    console = summarize_console_events(console_events)
    network = summarize_network_events(network_events)
    failed = tuple(item for item in network if item["failed"])
    errors = tuple(item for item in console if item["level"] in {"error", "warning"})
    payload = {
        "schema": "odysseus.browser_devtools_evidence.v1",
        "console_events": console,
        "network_events": network,
        "console_error_count": len(errors),
        "failed_request_count": len(failed),
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _url_parts(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserDevtoolsEvidenceError("url must be http(s)")
    if parsed.username or parsed.password:
        raise BrowserDevtoolsEvidenceError("url must not contain credentials")
    host = parsed.hostname.lower()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        raise BrowserDevtoolsEvidenceError("host is invalid")
    path = parsed.path.lower()
    if path.endswith((".js", ".mjs")):
        path_class = "script"
    elif path.endswith((".css",)):
        path_class = "style"
    elif path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")):
        path_class = "image"
    elif path.endswith((".woff", ".woff2", ".ttf", ".otf")):
        path_class = "font"
    elif path.endswith((".json", ".xml")):
        path_class = "data"
    else:
        path_class = "document"
    return host, path_class


def _safe_message(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise BrowserDevtoolsEvidenceError("console message contains forbidden marker")
    return text[:500]


def _safe_token(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise BrowserDevtoolsEvidenceError(f"{field} is invalid")
    return text


def _safe_status(value: Any) -> int:
    try:
        status = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(status, 999))


def _safe_duration(value: Any) -> int:
    try:
        duration = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(duration, 3_600_000))


def _mime_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "javascript" in text or text == "script":
        return "script"
    if "css" in text or text == "stylesheet":
        return "style"
    if text.startswith("image/") or text == "image":
        return "image"
    if "json" in text or text in {"xhr", "fetch"}:
        return "data"
    if "html" in text or text == "document":
        return "document"
    return "other"


def _error_class(message: str) -> str:
    lowered = message.lower()
    if "typeerror" in lowered:
        return "type_error"
    if "referenceerror" in lowered:
        return "reference_error"
    if "timeout" in lowered:
        return "timeout"
    if "network" in lowered or "failed to fetch" in lowered:
        return "network"
    return "unknown"


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise BrowserDevtoolsEvidenceError("payload contains forbidden marker")
