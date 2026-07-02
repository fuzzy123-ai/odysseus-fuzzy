"""Structured browser evidence for Odysseus agents.

Browser evidence is a compact, redacted summary of what a worker observed.
Artifacts such as screenshots are referenced by repo-relative ids/paths, while
raw page dumps, cookies, authorization headers and private host paths are kept
out of the packet.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


BROWSER_EVIDENCE_SCHEMA = "odysseus.agent.browser_evidence.v1"

_MAX_TEXT = 400
_MAX_ITEMS = 50
_SAFE_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")
_SECRET_RE = re.compile(
    r"(authorization\s*[:=]|cookie\s*[:=]|api[_-]?key\s*[:=]|password\s*[:=]|bearer\s+[A-Za-z0-9._-]{8,})",
    re.IGNORECASE,
)


class BrowserEvidenceError(ValueError):
    """Raised when browser evidence would be unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class BrowserConsoleEvent:
    level: str
    message: str
    source_hash: str

    @classmethod
    def create(cls, *, level: Any, message: Any) -> "BrowserConsoleEvent":
        safe_message = _redacted_text(message, field_name="message")
        return cls(
            level=_token(level, field_name="level", default="log"),
            message=safe_message,
            source_hash=_hash_text(safe_message),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "message": self.message, "source_hash": self.source_hash}


@dataclass(frozen=True, slots=True)
class BrowserNetworkEvent:
    url: str
    method: str
    status: int
    resource_type: str
    failed: bool

    @classmethod
    def create(
        cls,
        *,
        url: Any,
        method: Any = "GET",
        status: Any = 0,
        resource_type: Any = "other",
        failed: bool = False,
    ) -> "BrowserNetworkEvent":
        parsed_url = _safe_url(url)
        return cls(
            url=parsed_url,
            method=_token(method, field_name="method", default="GET").upper(),
            status=max(0, min(999, int(status or 0))),
            resource_type=_token(resource_type, field_name="resource_type", default="other"),
            failed=bool(failed),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "status": self.status,
            "resource_type": self.resource_type,
            "failed": self.failed,
        }


@dataclass(frozen=True, slots=True)
class BrowserEvidencePacket:
    target_url: str
    captured_at: str
    page_title: str
    text_summary: str
    dom_summary: str
    accessibility_summary: str
    screenshot_artifact: str
    console_events: tuple[BrowserConsoleEvent, ...]
    network_events: tuple[BrowserNetworkEvent, ...]
    performance: Mapping[str, float]
    raw_content_visible: bool = False
    schema: str = BROWSER_EVIDENCE_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        target_url: Any,
        captured_at: Any,
        page_title: Any = "",
        text_summary: Any = "",
        dom_summary: Any = "",
        accessibility_summary: Any = "",
        screenshot_artifact: Any = "",
        console_events: Iterable[BrowserConsoleEvent | Mapping[str, Any]] = (),
        network_events: Iterable[BrowserNetworkEvent | Mapping[str, Any]] = (),
        performance: Mapping[str, Any] | None = None,
    ) -> "BrowserEvidencePacket":
        return cls(
            target_url=_safe_url(target_url),
            captured_at=_required_text(captured_at, field_name="captured_at", max_len=80),
            page_title=_redacted_text(page_title, field_name="page_title", allow_empty=True),
            text_summary=_redacted_text(text_summary, field_name="text_summary", allow_empty=True),
            dom_summary=_redacted_text(dom_summary, field_name="dom_summary", allow_empty=True),
            accessibility_summary=_redacted_text(
                accessibility_summary,
                field_name="accessibility_summary",
                allow_empty=True,
            ),
            screenshot_artifact=_artifact_ref(screenshot_artifact, allow_empty=True),
            console_events=_console_events(console_events),
            network_events=_network_events(network_events),
            performance=_performance(performance or {}),
        )

    @property
    def failed_asset_count(self) -> int:
        return sum(1 for event in self.network_events if event.failed or event.status >= 400)

    @property
    def console_error_count(self) -> int:
        return sum(1 for event in self.console_events if event.level in {"error", "warning"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "target_url": self.target_url,
            "captured_at": self.captured_at,
            "page_title": self.page_title,
            "text_summary": self.text_summary,
            "dom_summary": self.dom_summary,
            "accessibility_summary": self.accessibility_summary,
            "screenshot_artifact": self.screenshot_artifact,
            "console_events": tuple(event.to_dict() for event in self.console_events),
            "network_events": tuple(event.to_dict() for event in self.network_events),
            "performance": dict(self.performance),
            "console_error_count": self.console_error_count,
            "failed_asset_count": self.failed_asset_count,
            "raw_content_visible": False,
        }


def build_browser_evidence_report(packet: BrowserEvidencePacket | Mapping[str, Any]) -> dict[str, Any]:
    payload = packet.to_dict() if isinstance(packet, BrowserEvidencePacket) else dict(packet)
    return {
        "schema": "odysseus.agent.browser_evidence_report.v1",
        "target_url": payload.get("target_url", ""),
        "page_title": payload.get("page_title", ""),
        "artifact_count": 1 if payload.get("screenshot_artifact") else 0,
        "console_error_count": int(payload.get("console_error_count") or 0),
        "failed_asset_count": int(payload.get("failed_asset_count") or 0),
        "raw_content_visible": False,
        "summary_hash": _hash_text(
            "|".join(
                str(payload.get(key, ""))
                for key in ("text_summary", "dom_summary", "accessibility_summary")
            )
        ),
    }


def _console_events(values: Iterable[BrowserConsoleEvent | Mapping[str, Any]]) -> tuple[BrowserConsoleEvent, ...]:
    events: list[BrowserConsoleEvent] = []
    for value in values:
        events.append(value if isinstance(value, BrowserConsoleEvent) else BrowserConsoleEvent.create(**dict(value)))
        if len(events) >= _MAX_ITEMS:
            break
    return tuple(events)


def _network_events(values: Iterable[BrowserNetworkEvent | Mapping[str, Any]]) -> tuple[BrowserNetworkEvent, ...]:
    events: list[BrowserNetworkEvent] = []
    for value in values:
        events.append(value if isinstance(value, BrowserNetworkEvent) else BrowserNetworkEvent.create(**dict(value)))
        if len(events) >= _MAX_ITEMS:
            break
    return tuple(events)


def _performance(values: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in values.items():
        name = _token(key, field_name="performance_key", default="metric")
        try:
            metric = float(value)
        except (TypeError, ValueError):
            continue
        result[name] = max(0.0, metric)
    return result


def _safe_url(value: Any) -> str:
    url = _required_text(value, field_name="url", max_len=300)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BrowserEvidenceError("url must be http(s) with a host")
    if parsed.username or parsed.password:
        raise BrowserEvidenceError("url must not contain credentials")
    return parsed.geturl()


def _artifact_ref(value: Any, *, allow_empty: bool) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        if allow_empty:
            return ""
        raise BrowserEvidenceError("artifact ref must not be empty")
    lowered = text.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise BrowserEvidenceError("artifact ref must be repo-relative")
    if ".." in text.split("/") or not _SAFE_ARTIFACT_RE.fullmatch(text):
        raise BrowserEvidenceError("artifact ref is unsafe")
    return text


def _redacted_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = _required_text(value, field_name=field_name, max_len=_MAX_TEXT, allow_empty=allow_empty)
    if _SECRET_RE.search(text):
        raise BrowserEvidenceError(f"{field_name} appears to contain secrets")
    return text


def _required_text(value: Any, *, field_name: str, max_len: int, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not text and not allow_empty:
        raise BrowserEvidenceError(f"{field_name} must not be empty")
    return text[:max_len]


def _token(value: Any, *, field_name: str, default: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise BrowserEvidenceError(f"{field_name} must be a safe token")
    return text


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
