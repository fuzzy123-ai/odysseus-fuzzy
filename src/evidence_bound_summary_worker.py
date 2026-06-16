"""Small backend contract for evidence-bound summary worker payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_MAX_MEMORY_MB = 2048
_FORBIDDEN_TERMS = (
    "truth-write",
    "truth write",
    "authoritative",
    "global-summary",
    "global summary",
    "research",
    "accelerator",
)
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class EvidenceBoundSummaryError(ValueError):
    """Raised when an evidence-bound summary payload is invalid or unsafe."""


class SummaryTaskStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FALLBACK_REQUIRED = "fallback_required"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SummaryScope:
    summary_scope_ref: str

    @classmethod
    def create(cls, *, summary_scope_ref: Any) -> "SummaryScope":
        return cls(summary_scope_ref=_normalize_slug(summary_scope_ref, field_name="summary_scope_ref"))


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise EvidenceBoundSummaryError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise EvidenceBoundSummaryError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise EvidenceBoundSummaryError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise EvidenceBoundSummaryError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise EvidenceBoundSummaryError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise EvidenceBoundSummaryError(f"{field_name} must be > 0")
    return normalized


def _normalize_status(value: Any) -> SummaryTaskStatus:
    if isinstance(value, SummaryTaskStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return SummaryTaskStatus(normalized)
    except ValueError as exc:
        raise EvidenceBoundSummaryError("status is not supported") from exc


def _normalize_confidence(value: Any) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise EvidenceBoundSummaryError("confidence must be numeric") from None
    if math.isnan(normalized) or normalized < 0 or normalized > 1:
        raise EvidenceBoundSummaryError("confidence must be between 0 and 1")
    return normalized


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise EvidenceBoundSummaryError("truth/global/research/accelerator claims are out of scope")


@dataclass(frozen=True, slots=True)
class SummaryBudget:
    max_chunks: int
    max_tokens: int
    max_source_refs: int
    max_memory_mb: int

    @classmethod
    def create(
        cls,
        *,
        max_chunks: Any,
        max_tokens: Any,
        max_source_refs: Any,
        max_memory_mb: Any,
    ) -> "SummaryBudget":
        memory_mb = _normalize_positive_int(max_memory_mb, field_name="max_memory_mb")
        if memory_mb > _MAX_MEMORY_MB:
            raise EvidenceBoundSummaryError(f"max_memory_mb must not exceed {_MAX_MEMORY_MB}")
        return cls(
            max_chunks=_normalize_positive_int(max_chunks, field_name="max_chunks"),
            max_tokens=_normalize_positive_int(max_tokens, field_name="max_tokens"),
            max_source_refs=_normalize_positive_int(max_source_refs, field_name="max_source_refs"),
            max_memory_mb=memory_mb,
        )


@dataclass(frozen=True, slots=True)
class SummarySourceRef:
    source_ref: str
    chunk_ref: str
    evidence_ref: str

    @classmethod
    def create(
        cls,
        *,
        source_ref: Any,
        chunk_ref: Any,
        evidence_ref: Any,
    ) -> "SummarySourceRef":
        evidence = _normalize_slug(evidence_ref, field_name="evidence_ref")
        _reject_forbidden_terms(evidence)
        return cls(
            source_ref=_normalize_slug(source_ref, field_name="source_ref"),
            chunk_ref=_normalize_slug(chunk_ref, field_name="chunk_ref"),
            evidence_ref=evidence,
        )


@dataclass(frozen=True, slots=True)
class SummaryReviewGate:
    review_item_ref: str

    @classmethod
    def create(cls, *, review_item_ref: Any) -> "SummaryReviewGate":
        return cls(review_item_ref=_normalize_slug(review_item_ref, field_name="review_item_ref"))


@dataclass(frozen=True, slots=True)
class EvidenceBoundSummaryTask:
    summary_task_id: str
    summary_scope: SummaryScope
    model_profile_ref: str
    prompt_template_ref: str
    citation_policy: str
    budget: SummaryBudget
    source_refs: tuple[SummarySourceRef, ...]
    fallback_model_ref: str

    @classmethod
    def create(
        cls,
        *,
        summary_task_id: Any,
        summary_scope_ref: Any,
        model_profile_ref: Any,
        prompt_template_ref: Any,
        citation_policy: Any,
        budget: SummaryBudget,
        source_refs: Iterable[SummarySourceRef],
        fallback_model_ref: Any,
    ) -> "EvidenceBoundSummaryTask":
        if not isinstance(budget, SummaryBudget):
            raise EvidenceBoundSummaryError("budget must be a SummaryBudget")
        normalized_sources = tuple(source_refs)
        if not normalized_sources or any(not isinstance(item, SummarySourceRef) for item in normalized_sources):
            raise EvidenceBoundSummaryError("source_refs must contain SummarySourceRef items")
        if len(normalized_sources) > budget.max_source_refs or len(normalized_sources) > budget.max_chunks:
            raise EvidenceBoundSummaryError("source_refs must not exceed max_source_refs or max_chunks")
        model_ref = _normalize_slug(model_profile_ref, field_name="model_profile_ref")
        prompt_ref = _normalize_slug(prompt_template_ref, field_name="prompt_template_ref")
        citation_policy_text = _normalize_text(citation_policy, field_name="citation_policy", allow_empty=False)
        fallback_ref = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
        _reject_forbidden_terms(model_ref, prompt_ref, citation_policy_text, fallback_ref)
        return cls(
            summary_task_id=_normalize_slug(summary_task_id, field_name="summary_task_id"),
            summary_scope=SummaryScope.create(summary_scope_ref=summary_scope_ref),
            model_profile_ref=model_ref,
            prompt_template_ref=prompt_ref,
            citation_policy=citation_policy_text,
            budget=budget,
            source_refs=normalized_sources,
            fallback_model_ref=fallback_ref,
        )


@dataclass(frozen=True, slots=True)
class EvidenceBoundSummaryResult:
    task: EvidenceBoundSummaryTask
    summary_ref: str
    status: SummaryTaskStatus
    summary_text: str
    confidence: float
    uncertainty_reason: str
    needs_review: bool
    review_gate: SummaryReviewGate | None
    fallback_model_ref: str
    drift_check_ref: str
    risk_evidence_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        task: EvidenceBoundSummaryTask,
        summary_ref: Any = "",
        status: SummaryTaskStatus | str,
        summary_text: Any = "",
        confidence: Any = 0,
        uncertainty_reason: Any = "",
        needs_review: Any = False,
        review_item_ref: Any = "",
        fallback_model_ref: Any = "",
        drift_check_ref: Any = "",
        risk_evidence_ref: Any = "",
        reason: Any = "",
        next_action: Any = "",
    ) -> "EvidenceBoundSummaryResult":
        if not isinstance(task, EvidenceBoundSummaryTask):
            raise EvidenceBoundSummaryError("task must be an EvidenceBoundSummaryTask")
        normalized_status = _normalize_status(status)
        normalized_summary_ref = _normalize_slug(summary_ref, field_name="summary_ref") if str(summary_ref or "").strip() else ""
        normalized_text = _normalize_text(summary_text, field_name="summary_text", allow_empty=True, limit=_MAX_LONG_TEXT * 4)
        normalized_confidence = _normalize_confidence(confidence)
        normalized_uncertainty = _normalize_text(uncertainty_reason, field_name="uncertainty_reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_needs_review = bool(needs_review)
        normalized_review = SummaryReviewGate.create(review_item_ref=review_item_ref) if str(review_item_ref or "").strip() else None
        normalized_fallback = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref") if str(fallback_model_ref or "").strip() else ""
        normalized_drift = _normalize_slug(drift_check_ref, field_name="drift_check_ref") if str(drift_check_ref or "").strip() else ""
        normalized_risk = _normalize_text(risk_evidence_ref, field_name="risk_evidence_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_forbidden_terms(
            normalized_text,
            normalized_uncertainty,
            normalized_fallback,
            normalized_risk,
            normalized_reason,
            normalized_next_action,
            task.citation_policy,
        )
        if normalized_status == SummaryTaskStatus.COMPLETED:
            if not (normalized_summary_ref and normalized_text and normalized_risk and task.citation_policy and normalized_confidence >= 0):
                raise EvidenceBoundSummaryError("completed requires summary_ref, summary_text, risk_evidence_ref, citation_policy, and confidence")
            if len(normalized_text.split()) > task.budget.max_tokens:
                raise EvidenceBoundSummaryError("summary_text must stay within max_tokens approximation")
        if normalized_confidence < 0.5 or normalized_uncertainty:
            if normalized_status not in {SummaryTaskStatus.NEEDS_REVIEW, SummaryTaskStatus.FALLBACK_REQUIRED}:
                raise EvidenceBoundSummaryError("low confidence or uncertainty requires needs_review or fallback_required")
        if normalized_status in {SummaryTaskStatus.NEEDS_REVIEW, SummaryTaskStatus.FALLBACK_REQUIRED} and not (
            normalized_review or normalized_fallback
        ):
            raise EvidenceBoundSummaryError("needs_review and fallback_required require review_item_ref or fallback_model_ref")
        if normalized_status in {SummaryTaskStatus.BLOCKED, SummaryTaskStatus.FAILED} and not (
            normalized_reason or normalized_next_action
        ):
            raise EvidenceBoundSummaryError("blocked and failed require reason or next_action")
        return cls(
            task=task,
            summary_ref=normalized_summary_ref,
            status=normalized_status,
            summary_text=normalized_text,
            confidence=normalized_confidence,
            uncertainty_reason=normalized_uncertainty,
            needs_review=normalized_needs_review,
            review_gate=normalized_review,
            fallback_model_ref=normalized_fallback,
            drift_check_ref=normalized_drift,
            risk_evidence_ref=normalized_risk,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )


def audit_summary(result: EvidenceBoundSummaryResult) -> dict[str, Any]:
    if not isinstance(result, EvidenceBoundSummaryResult):
        raise EvidenceBoundSummaryError("result must be an EvidenceBoundSummaryResult")
    return {
        "summary_task_id": result.task.summary_task_id,
        "summary_scope_ref": result.task.summary_scope.summary_scope_ref,
        "status": result.status.value,
        "confidence": result.confidence,
        "max_chunks": result.task.budget.max_chunks,
        "max_tokens": result.task.budget.max_tokens,
        "max_source_refs": result.task.budget.max_source_refs,
        "max_memory_mb": result.task.budget.max_memory_mb,
        "source_ref_count": len(result.task.source_refs),
        "has_summary_ref": bool(result.summary_ref),
        "has_review_item_ref": bool(result.review_gate),
        "has_fallback_model_ref": bool(result.fallback_model_ref),
        "has_drift_check_ref": bool(result.drift_check_ref),
        "has_risk_evidence_ref": bool(result.risk_evidence_ref),
    }
