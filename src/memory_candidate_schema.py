"""Generic source-linked memory candidate schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable, Mapping

from src.internal_references import build_internal_reference_dict


MEMORY_CANDIDATE_SCHEMA = "odysseus.memory_candidate.v1"


class MemoryCandidateError(ValueError):
    """Raised when a memory candidate would be unsafe."""


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    title: str
    abstract: str
    source_refs: tuple[str, ...]
    confidence: float
    sensitivity: str
    author_stamp: dict[str, str]
    recheck_hint: str
    internal_ref: dict[str, Any]
    raw_content_visible: bool = False
    schema: str = MEMORY_CANDIDATE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "candidate_id": self.candidate_id,
            "title": self.title,
            "abstract": self.abstract,
            "source_refs": self.source_refs,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "author_stamp": self.author_stamp,
            "recheck_hint": self.recheck_hint,
            "internal_ref": self.internal_ref,
            "raw_content_visible": self.raw_content_visible,
        }
        _reject_unsafe_payload(payload)
        return payload


def build_memory_candidates_from_synthesis(
    synthesis: Mapping[str, Any],
    *,
    model: str,
    created_by: str = "web_research_synthesis",
    sensitivity: str = "public",
    recheck_hint: str = "periodic",
) -> tuple[MemoryCandidate, ...]:
    if not isinstance(synthesis, Mapping):
        raise MemoryCandidateError("synthesis must be a mapping")
    _reject_unsafe_payload(synthesis)
    source_refs = tuple(_safe_source_ref(ref) for ref in synthesis.get("source_refs") or ())
    if not source_refs:
        raise MemoryCandidateError("memory candidate needs at least one source ref")
    confidence = _safe_confidence(synthesis.get("confidence"))
    stamp = {
        "model": _safe_label(model, field="model"),
        "created_by": _safe_label(created_by, field="created_by"),
        "created_at": _now_iso(),
    }
    sensitivity_token = _safe_label(sensitivity, field="sensitivity").lower()
    candidates: list[MemoryCandidate] = []
    for topic in synthesis.get("topics") or ():
        if not isinstance(topic, Mapping):
            continue
        title = _safe_text(topic.get("name") or "", field="topic")
        abstract = _safe_text(topic.get("summary") or title, field="abstract", max_len=500)
        if not title or not abstract:
            continue
        candidate_id = _candidate_id(title, source_refs)
        candidates.append(
            MemoryCandidate(
                candidate_id=candidate_id,
                title=title,
                abstract=abstract,
                source_refs=source_refs,
                confidence=confidence,
                sensitivity=sensitivity_token,
                author_stamp=stamp,
                recheck_hint=_safe_label(recheck_hint, field="recheck_hint").lower(),
                internal_ref=build_internal_reference_dict("memory", candidate_id, label="Memory-Kandidat oeffnen"),
            )
        )
    if not candidates:
        raise MemoryCandidateError("synthesis did not contain memory-worthy topics")
    return tuple(candidates)


def _candidate_id(title: str, source_refs: Iterable[str]) -> str:
    encoded = f"{title}|{','.join(source_refs)}".encode("utf-8", errors="replace")
    return "memcand_" + hashlib.sha256(encoded).hexdigest()[:16]


def _safe_source_ref(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        if re.fullmatch(r"sha256:[a-f0-9]{16,64}", text):
            return text
        raise MemoryCandidateError("source hash ref is invalid")
    if re.fullmatch(r"https?://[a-z0-9.-]{1,253}(/[a-z0-9._~:/@!$&'()*+,;=%-]*)?", text) and "?" not in text:
        return text[:240]
    raise MemoryCandidateError("source ref is invalid")


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"^[A-Za-z0-9_.:@/-]{1,120}$", text):
        raise MemoryCandidateError(f"{field} is invalid")
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        raise MemoryCandidateError(f"{field} contains forbidden marker")
    return text[:120]


def _safe_text(value: Any, *, field: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise MemoryCandidateError(f"{field} contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", lowered):
        raise MemoryCandidateError(f"{field} contains host path")
    return text[:max_len]


def _safe_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoryCandidateError("confidence must be numeric") from exc
    if parsed < 0 or parsed > 1:
        raise MemoryCandidateError("confidence must be between 0 and 1")
    return round(parsed, 3)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret", "raw_text"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise MemoryCandidateError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise MemoryCandidateError("payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise MemoryCandidateError("payload contains host path")
