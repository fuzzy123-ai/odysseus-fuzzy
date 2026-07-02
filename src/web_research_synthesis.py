"""Redacted synthesis contract for website research."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping


WEB_RESEARCH_SYNTHESIS_SCHEMA = "odysseus.web_research_synthesis.v1"


class WebResearchSynthesisError(ValueError):
    """Raised when website research synthesis input is unsafe."""


@dataclass(frozen=True, slots=True)
class WebResearchSynthesis:
    synthesis_id: str
    scope_id: str
    model_route: str
    topics: tuple[dict[str, Any], ...]
    processes: tuple[dict[str, Any], ...]
    faqs: tuple[dict[str, Any], ...]
    source_refs: tuple[str, ...]
    gaps: tuple[str, ...]
    confidence: float
    raw_content_visible: bool = False
    schema: str = WEB_RESEARCH_SYNTHESIS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "synthesis_id": self.synthesis_id,
            "scope_id": self.scope_id,
            "model_route": self.model_route,
            "topics": self.topics,
            "processes": self.processes,
            "faqs": self.faqs,
            "source_refs": self.source_refs,
            "gaps": self.gaps,
            "confidence": self.confidence,
            "raw_content_visible": self.raw_content_visible,
        }
        _reject_unsafe_payload(payload)
        return payload


def build_web_research_synthesis(
    inventory: Mapping[str, Any],
    source_summaries: Iterable[Mapping[str, Any]],
    *,
    dsgvo_mode: bool = False,
    sensitivity: str = "public",
    preferred_model_route: str = "api_or_local",
) -> WebResearchSynthesis:
    """Build a source-linked synthesis from redacted per-source summaries."""

    if not isinstance(inventory, Mapping):
        raise WebResearchSynthesisError("inventory must be a mapping")
    _reject_unsafe_payload(inventory)
    scope_id = _safe_label(inventory.get("scope_id") or "", field="scope_id")
    route = _model_route(dsgvo_mode=dsgvo_mode, sensitivity=sensitivity, preferred=preferred_model_route)
    topics: list[dict[str, Any]] = []
    processes: list[dict[str, Any]] = []
    faqs: list[dict[str, Any]] = []
    source_refs: list[str] = []
    gaps: list[str] = list(_safe_gap(gap) for gap in _inventory_gaps(inventory))
    for summary in source_summaries:
        if not isinstance(summary, Mapping):
            raise WebResearchSynthesisError("source summary must be a mapping")
        _reject_unsafe_payload(summary)
        source_ref = _source_ref(summary)
        if source_ref and source_ref not in source_refs:
            source_refs.append(source_ref)
        for topic in summary.get("topics") or ():
            item = _topic_item(topic, source_ref=source_ref)
            if item and item not in topics:
                topics.append(item)
        for process in summary.get("processes") or ():
            item = _named_item(process, source_ref=source_ref, kind="process")
            if item and item not in processes:
                processes.append(item)
        for faq in summary.get("faqs") or ():
            item = _faq_item(faq, source_ref=source_ref)
            if item and item not in faqs:
                faqs.append(item)
        for gap in summary.get("gaps") or ():
            safe_gap = _safe_gap(gap)
            if safe_gap not in gaps:
                gaps.append(safe_gap)
    confidence = _confidence(source_count=len(source_refs), topic_count=len(topics), gap_count=len(gaps))
    synthesis = WebResearchSynthesis(
        synthesis_id=_synthesis_id(scope_id, source_refs),
        scope_id=scope_id,
        model_route=route,
        topics=tuple(topics[:100]),
        processes=tuple(processes[:100]),
        faqs=tuple(faqs[:100]),
        source_refs=tuple(source_refs[:200]),
        gaps=tuple(gaps[:100]),
        confidence=confidence,
    )
    synthesis.to_dict()
    return synthesis


def _model_route(*, dsgvo_mode: bool, sensitivity: str, preferred: str) -> str:
    sensitivity_token = _safe_label(sensitivity or "public", field="sensitivity").lower()
    preferred_token = _safe_label(preferred or "api_or_local", field="preferred_model_route").lower()
    if dsgvo_mode or sensitivity_token in {"private", "sensitive", "confidential", "personal"}:
        return "local_only"
    if preferred_token in {"local_only", "api_or_local", "api_allowed"}:
        return preferred_token
    return "api_or_local"


def _inventory_gaps(inventory: Mapping[str, Any]) -> tuple[str, ...]:
    gaps: list[str] = []
    for skipped in inventory.get("skipped") or ():
        if isinstance(skipped, Mapping):
            reason = _safe_gap(skipped.get("reason") or "skipped")
            if reason not in gaps:
                gaps.append(reason)
    return tuple(gaps)


def _source_ref(summary: Mapping[str, Any]) -> str:
    ref = str(summary.get("source_ref") or summary.get("canonical_url") or "").strip()
    if ref.startswith("sha256:"):
        return _safe_hash_ref(ref)
    if ref.startswith(("http://", "https://")):
        return _safe_source_url(ref)
    return _safe_label(ref, field="source_ref") if ref else ""


def _topic_item(topic: Any, *, source_ref: str) -> dict[str, Any]:
    if isinstance(topic, Mapping):
        name = _safe_text(topic.get("name") or topic.get("title") or "", field="topic")
        summary = _safe_text(topic.get("summary") or "", field="topic_summary", max_len=280)
    else:
        name = _safe_text(topic, field="topic")
        summary = ""
    if not name:
        return {}
    return {"name": name, "summary": summary, "source_refs": (source_ref,) if source_ref else ()}


def _named_item(value: Any, *, source_ref: str, kind: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        name = _safe_text(value.get("name") or value.get("title") or "", field=kind)
        summary = _safe_text(value.get("summary") or value.get("description") or "", field=f"{kind}_summary", max_len=360)
    else:
        name = _safe_text(value, field=kind)
        summary = ""
    if not name:
        return {}
    return {"name": name, "summary": summary, "source_refs": (source_ref,) if source_ref else ()}


def _faq_item(value: Any, *, source_ref: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        question = _safe_text(value, field="faq_question")
        answer = ""
    else:
        question = _safe_text(value.get("question") or "", field="faq_question")
        answer = _safe_text(value.get("answer") or "", field="faq_answer", max_len=360)
    if not question:
        return {}
    return {"question": question, "answer": answer, "source_refs": (source_ref,) if source_ref else ()}


def _safe_source_url(value: str) -> str:
    text = str(value or "").strip().lower()
    match = re.fullmatch(r"https?://[a-z0-9.-]{1,253}(/[a-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?", text)
    if not match or "?" in text:
        return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text[:240]


def _safe_hash_ref(value: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"sha256:[a-f0-9]{16,64}", text):
        raise WebResearchSynthesisError("source hash ref is invalid")
    return text


def _safe_gap(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    return text if re.fullmatch(r"[a-z0-9_.:-]{1,80}", text) else "unknown"


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"^[A-Za-z0-9_.:-]{1,120}$", text):
        raise WebResearchSynthesisError(f"{field} is invalid")
    return text


def _safe_text(value: Any, *, field: str, max_len: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise WebResearchSynthesisError(f"{field} contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", lowered):
        raise WebResearchSynthesisError(f"{field} contains host path")
    return text[:max_len]


def _confidence(*, source_count: int, topic_count: int, gap_count: int) -> float:
    if source_count <= 0 or topic_count <= 0:
        return 0.2
    score = 0.55 + min(source_count, 10) * 0.03 + min(topic_count, 10) * 0.015 - min(gap_count, 10) * 0.02
    return round(max(0.2, min(score, 0.95)), 2)


def _synthesis_id(scope_id: str, source_refs: list[str]) -> str:
    encoded = f"{scope_id}|{','.join(source_refs)}".encode("utf-8", errors="replace")
    return "web_syn_" + hashlib.sha256(encoded).hexdigest()[:16]


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret", "raw_text"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise WebResearchSynthesisError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise WebResearchSynthesisError("payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise WebResearchSynthesisError("payload contains host path")
