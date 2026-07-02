"""Policy decisions for Memory/RaptorGraph candidate writes."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


MEMORY_WRITE_POLICY_SCHEMA = "odysseus.memory_write_policy.v1"


class MemoryWritePolicyError(ValueError):
    """Raised when memory write policy input is unsafe."""


@dataclass(frozen=True, slots=True)
class MemoryWritePolicyDecision:
    action: str
    reasons: tuple[str, ...]
    auto_write_allowed: bool
    review_required: bool
    blocked: bool
    raw_content_visible: bool = False
    schema: str = MEMORY_WRITE_POLICY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "action": self.action,
            "reasons": self.reasons,
            "auto_write_allowed": self.auto_write_allowed,
            "review_required": self.review_required,
            "blocked": self.blocked,
            "raw_content_visible": self.raw_content_visible,
        }
        _reject_unsafe_payload(payload)
        return payload


def decide_memory_write_policy(
    candidates: Iterable[Mapping[str, Any]],
    *,
    dsgvo_mode: bool = False,
    model_route: str = "api_or_local",
    operator_auto_write_enabled: bool = False,
    min_confidence: float = 0.75,
) -> MemoryWritePolicyDecision:
    normalized = tuple(candidates)
    if not normalized:
        return _decision("blocked", ("no_candidates",))
    reasons: list[str] = []
    route = _safe_label(model_route, field="model_route").lower()
    if dsgvo_mode and route != "local_only":
        reasons.append("dsgvo_requires_local_only")
    for candidate in normalized:
        if not isinstance(candidate, Mapping):
            raise MemoryWritePolicyError("candidate must be a mapping")
        _reject_unsafe_payload(candidate)
        if bool(candidate.get("raw_content_visible")):
            reasons.append("raw_content_visible")
        sensitivity = _safe_label(candidate.get("sensitivity") or "public", field="sensitivity").lower()
        if sensitivity in {"private", "sensitive", "confidential", "personal"} and route != "local_only":
            reasons.append("sensitive_candidate_requires_local_only")
        confidence = _safe_confidence(candidate.get("confidence"))
        if confidence < min_confidence:
            reasons.append("confidence_below_threshold")
        if not candidate.get("source_refs"):
            reasons.append("source_refs_missing")
        stamp = candidate.get("author_stamp") if isinstance(candidate.get("author_stamp"), Mapping) else {}
        if not stamp.get("model") or not stamp.get("created_at"):
            reasons.append("author_stamp_incomplete")
    reasons = list(dict.fromkeys(reasons))
    if any(reason in reasons for reason in ("dsgvo_requires_local_only", "sensitive_candidate_requires_local_only", "raw_content_visible", "source_refs_missing", "author_stamp_incomplete")):
        return _decision("blocked", tuple(reasons))
    if not operator_auto_write_enabled:
        return _decision("review_required", tuple(reasons or ["operator_auto_write_disabled"]))
    if "confidence_below_threshold" in reasons:
        return _decision("review_required", tuple(reasons))
    return _decision("auto_write", ("policy_passed",))


def _decision(action: str, reasons: tuple[str, ...]) -> MemoryWritePolicyDecision:
    safe_action = _safe_label(action, field="action").lower()
    safe_reasons = tuple(_safe_label(reason, field="reason").lower() for reason in reasons)
    return MemoryWritePolicyDecision(
        action=safe_action,
        reasons=safe_reasons,
        auto_write_allowed=safe_action == "auto_write",
        review_required=safe_action == "review_required",
        blocked=safe_action == "blocked",
    )


def _safe_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoryWritePolicyError("confidence must be numeric") from exc
    if parsed < 0 or parsed > 1:
        raise MemoryWritePolicyError("confidence must be between 0 and 1")
    return parsed


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"^[A-Za-z0-9_.:-]{1,120}$", text):
        raise MemoryWritePolicyError(f"{field} is invalid")
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        raise MemoryWritePolicyError(f"{field} contains forbidden marker")
    return text


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret", "raw_text"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise MemoryWritePolicyError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise MemoryWritePolicyError("payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise MemoryWritePolicyError("payload contains host path")
