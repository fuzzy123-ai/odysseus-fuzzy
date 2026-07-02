"""Redacted website research packet for later memory write intents."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable


_SECRET_RE = re.compile(r"(authorization|cookie|api[_-]?key|password|bearer\s+[A-Za-z0-9._-]{8,})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class WebResearchSourceRef:
    url: str
    title: str
    evidence_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "evidence_hash": self.evidence_hash}


@dataclass(frozen=True, slots=True)
class WebResearchMemoryPacket:
    topic: str
    summary: str
    source_refs: tuple[WebResearchSourceRef, ...]
    confidence: float
    gaps: tuple[str, ...]
    raw_content_visible: bool = False

    @classmethod
    def create(
        cls,
        *,
        topic: Any,
        summary: Any,
        source_refs: Iterable[dict[str, Any] | WebResearchSourceRef],
        confidence: Any = 0.0,
        gaps: Iterable[Any] = (),
    ) -> "WebResearchMemoryPacket":
        refs = tuple(ref if isinstance(ref, WebResearchSourceRef) else _source_ref(ref) for ref in source_refs)
        if not refs:
            raise ValueError("source_refs must not be empty")
        return cls(
            topic=_safe_text(topic, max_len=160),
            summary=_safe_text(summary, max_len=1200),
            source_refs=refs[:100],
            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
            gaps=tuple(_safe_text(gap, max_len=160) for gap in gaps)[:50],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.web_research.memory_packet.v1",
            "topic": self.topic,
            "summary": self.summary,
            "source_refs": tuple(ref.to_dict() for ref in self.source_refs),
            "confidence": self.confidence,
            "gaps": self.gaps,
            "raw_content_visible": False,
            "packet_hash": _hash_text(self.topic + "|" + self.summary),
        }


def _source_ref(payload: dict[str, Any]) -> WebResearchSourceRef:
    url = _safe_text(payload.get("url"), max_len=300)
    if not url.startswith(("http://", "https://")):
        raise ValueError("source url must be http(s)")
    title = _safe_text(payload.get("title") or url, max_len=160)
    evidence_hash = str(payload.get("evidence_hash") or _hash_text(url)).strip()
    if not evidence_hash.startswith("sha256:"):
        raise ValueError("evidence_hash must be sha256-prefixed")
    return WebResearchSourceRef(url=url, title=title, evidence_hash=evidence_hash)


def _safe_text(value: Any, *, max_len: int) -> str:
    text = " ".join(str(value or "").split())[:max_len]
    if not text:
        raise ValueError("text must not be empty")
    if _SECRET_RE.search(text):
        raise ValueError("text appears to contain secrets")
    return text


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
