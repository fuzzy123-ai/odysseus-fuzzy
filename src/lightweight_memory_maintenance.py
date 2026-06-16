"""Small backend contract for bounded lightweight memory maintenance workers."""

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
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_TERMS = ("qdrant", "kuzu", "umap", "gmm", "accelerator", "research")


class LightweightMaintenanceError(ValueError):
    """Raised when a lightweight maintenance payload is invalid or unsafe."""


class MaintenanceTaskType(StrEnum):
    CLUSTER_LABELING = "cluster_labeling"
    EVIDENCE_SUMMARY = "evidence_summary"
    ENTITY_EDGE_CANDIDATE = "entity_edge_candidate"
    DEDUPE_CANDIDATE = "dedupe_candidate"
    DRIFT_CHECK = "drift_check"
    REVIEW_PREPARATION = "review_preparation"


class MaintenanceTaskStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FALLBACK_REQUIRED = "fallback_required"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise LightweightMaintenanceError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise LightweightMaintenanceError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise LightweightMaintenanceError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise LightweightMaintenanceError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_text_list(values: Iterable[Any], *, field_name: str, limit: int = _MAX_TEXT) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True, limit=limit)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise LightweightMaintenanceError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise LightweightMaintenanceError(f"{field_name} must be > 0")
    if normalized > _MAX_MEMORY_MB and "memory" in field_name:
        raise LightweightMaintenanceError(f"{field_name} must not exceed {_MAX_MEMORY_MB} MB")
    return normalized


def _normalize_status(value: Any) -> MaintenanceTaskStatus:
    if isinstance(value, MaintenanceTaskStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return MaintenanceTaskStatus(normalized)
    except ValueError as exc:
        raise LightweightMaintenanceError("status is not supported") from exc


def _normalize_task_type(value: Any) -> MaintenanceTaskType:
    if isinstance(value, MaintenanceTaskType):
        return value
    normalized = _normalize_slug(value, field_name="task_type").replace("-", "_")
    try:
        return MaintenanceTaskType(normalized)
    except ValueError as exc:
        raise LightweightMaintenanceError("task_type is not supported") from exc


def _normalize_confidence(value: Any) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise LightweightMaintenanceError("confidence must be numeric") from None
    if math.isnan(normalized) or normalized < 0 or normalized > 1:
        raise LightweightMaintenanceError("confidence must be between 0 and 1")
    return normalized


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise LightweightMaintenanceError("accelerator or research activation is out of scope")


@dataclass(frozen=True, slots=True)
class MaintenanceWorkerProfile:
    model_profile_ref: str
    max_memory_mb: int
    supports_json: bool
    supports_source_citations: bool
    fallback_model_ref: str

    @classmethod
    def create(
        cls,
        *,
        model_profile_ref: Any,
        max_memory_mb: Any,
        supports_json: Any,
        supports_source_citations: Any,
        fallback_model_ref: Any,
    ) -> "MaintenanceWorkerProfile":
        model_ref = _normalize_slug(model_profile_ref, field_name="model_profile_ref")
        fallback_ref = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
        _reject_forbidden_terms(model_ref, fallback_ref)
        return cls(
            model_profile_ref=model_ref,
            max_memory_mb=_normalize_positive_int(max_memory_mb, field_name="max_memory_mb"),
            supports_json=bool(supports_json),
            supports_source_citations=bool(supports_source_citations),
            fallback_model_ref=fallback_ref,
        )


@dataclass(frozen=True, slots=True)
class MaintenanceTaskBudget:
    memory_budget_mb: int
    token_budget: int
    chunk_budget: int
    source_ref_budget: int
    time_budget_ms: int

    @classmethod
    def create(
        cls,
        *,
        memory_budget_mb: Any,
        token_budget: Any,
        chunk_budget: Any,
        source_ref_budget: Any,
        time_budget_ms: Any,
    ) -> "MaintenanceTaskBudget":
        return cls(
            memory_budget_mb=_normalize_positive_int(memory_budget_mb, field_name="memory_budget_mb"),
            token_budget=_normalize_positive_int(token_budget, field_name="token_budget"),
            chunk_budget=_normalize_positive_int(chunk_budget, field_name="chunk_budget"),
            source_ref_budget=_normalize_positive_int(source_ref_budget, field_name="source_ref_budget"),
            time_budget_ms=_normalize_positive_int(time_budget_ms, field_name="time_budget_ms"),
        )


@dataclass(frozen=True, slots=True)
class MaintenanceEvidenceRef:
    evidence_ref: str

    @classmethod
    def create(cls, *, evidence_ref: Any) -> "MaintenanceEvidenceRef":
        normalized = _normalize_slug(evidence_ref, field_name="evidence_ref")
        _reject_forbidden_terms(normalized)
        return cls(evidence_ref=normalized)


@dataclass(frozen=True, slots=True)
class MaintenanceTask:
    task_ref: str
    task_type: MaintenanceTaskType
    source_refs: tuple[str, ...]
    evidence_refs: tuple[MaintenanceEvidenceRef, ...]
    budget: MaintenanceTaskBudget
    worker_profile: MaintenanceWorkerProfile
    truth_write_allowed: bool
    derived_output_ref: str
    review_item_ref: str

    @classmethod
    def create(
        cls,
        *,
        task_ref: Any,
        task_type: MaintenanceTaskType | str,
        source_refs: Iterable[Any],
        evidence_refs: Iterable[MaintenanceEvidenceRef],
        budget: MaintenanceTaskBudget,
        worker_profile: MaintenanceWorkerProfile,
        truth_write_allowed: Any,
        derived_output_ref: Any = "",
        review_item_ref: Any = "",
    ) -> "MaintenanceTask":
        if not isinstance(budget, MaintenanceTaskBudget):
            raise LightweightMaintenanceError("budget must be a MaintenanceTaskBudget")
        if not isinstance(worker_profile, MaintenanceWorkerProfile):
            raise LightweightMaintenanceError("worker_profile must be a MaintenanceWorkerProfile")
        normalized_sources = _normalize_text_list(source_refs, field_name="source_ref")
        if not normalized_sources:
            raise LightweightMaintenanceError("source_refs must not be empty")
        normalized_evidence = tuple(evidence_refs)
        if not normalized_evidence or any(not isinstance(item, MaintenanceEvidenceRef) for item in normalized_evidence):
            raise LightweightMaintenanceError("evidence_refs must contain MaintenanceEvidenceRef items")
        if len(normalized_sources) > budget.source_ref_budget:
            raise LightweightMaintenanceError("source_refs must not exceed source_ref_budget")
        truth_write = bool(truth_write_allowed)
        if truth_write:
            raise LightweightMaintenanceError("truth_write_allowed=True is forbidden")
        if budget.memory_budget_mb > worker_profile.max_memory_mb:
            raise LightweightMaintenanceError("memory_budget_mb must not exceed worker profile max_memory_mb")
        return cls(
            task_ref=_normalize_slug(task_ref, field_name="task_ref"),
            task_type=_normalize_task_type(task_type),
            source_refs=normalized_sources,
            evidence_refs=normalized_evidence,
            budget=budget,
            worker_profile=worker_profile,
            truth_write_allowed=truth_write,
            derived_output_ref=_normalize_slug(derived_output_ref, field_name="derived_output_ref") if str(derived_output_ref or "").strip() else "",
            review_item_ref=_normalize_slug(review_item_ref, field_name="review_item_ref") if str(review_item_ref or "").strip() else "",
        )


@dataclass(frozen=True, slots=True)
class MaintenanceTaskResult:
    task: MaintenanceTask
    status: MaintenanceTaskStatus
    confidence: float
    uncertainty_reason: str
    needs_review: bool
    fallback_model_ref: str
    risk_evidence_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        task: MaintenanceTask,
        status: MaintenanceTaskStatus | str,
        confidence: Any,
        uncertainty_reason: Any,
        needs_review: Any,
        fallback_model_ref: Any = "",
        risk_evidence_ref: Any = "",
        reason: Any = "",
        next_action: Any = "",
    ) -> "MaintenanceTaskResult":
        if not isinstance(task, MaintenanceTask):
            raise LightweightMaintenanceError("task must be a MaintenanceTask")
        normalized_status = _normalize_status(status)
        normalized_confidence = _normalize_confidence(confidence)
        normalized_uncertainty = _normalize_text(
            uncertainty_reason,
            field_name="uncertainty_reason",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_needs_review = bool(needs_review)
        normalized_fallback = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref") if str(fallback_model_ref or "").strip() else ""
        normalized_risk = _normalize_text(risk_evidence_ref, field_name="risk_evidence_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_forbidden_terms(
            normalized_uncertainty,
            normalized_fallback,
            normalized_risk,
            normalized_reason,
            normalized_next_action,
        )

        if normalized_confidence < 0.5 or normalized_uncertainty:
            if normalized_status not in {MaintenanceTaskStatus.NEEDS_REVIEW, MaintenanceTaskStatus.FALLBACK_REQUIRED}:
                raise LightweightMaintenanceError("low confidence or uncertainty requires needs_review or fallback_required")
        if normalized_status in {
            MaintenanceTaskStatus.COMPLETED,
            MaintenanceTaskStatus.NEEDS_REVIEW,
            MaintenanceTaskStatus.FALLBACK_REQUIRED,
        } and not (task.derived_output_ref or task.review_item_ref):
            raise LightweightMaintenanceError("completed, needs_review, and fallback_required require derived_output_ref or review_item_ref")
        if normalized_status in {MaintenanceTaskStatus.BLOCKED, MaintenanceTaskStatus.FAILED} and not (
            normalized_reason or normalized_next_action
        ):
            raise LightweightMaintenanceError("blocked and failed results require reason or next_action")
        if normalized_status == MaintenanceTaskStatus.FALLBACK_REQUIRED and not normalized_fallback:
            normalized_fallback = task.worker_profile.fallback_model_ref

        return cls(
            task=task,
            status=normalized_status,
            confidence=normalized_confidence,
            uncertainty_reason=normalized_uncertainty,
            needs_review=normalized_needs_review,
            fallback_model_ref=normalized_fallback,
            risk_evidence_ref=normalized_risk,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )


@dataclass(frozen=True, slots=True)
class MaintenanceWorkerReadiness:
    worker_profile: MaintenanceWorkerProfile
    ready: bool
    reason: str

    @classmethod
    def create(
        cls,
        *,
        worker_profile: MaintenanceWorkerProfile,
        ready: Any,
        reason: Any = "",
    ) -> "MaintenanceWorkerReadiness":
        if not isinstance(worker_profile, MaintenanceWorkerProfile):
            raise LightweightMaintenanceError("worker_profile must be a MaintenanceWorkerProfile")
        return cls(
            worker_profile=worker_profile,
            ready=bool(ready),
            reason=_normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT),
        )


def audit_summary(result: MaintenanceTaskResult) -> dict[str, Any]:
    if not isinstance(result, MaintenanceTaskResult):
        raise LightweightMaintenanceError("result must be a MaintenanceTaskResult")
    return {
        "task_ref": result.task.task_ref,
        "task_type": result.task.task_type.value,
        "status": result.status.value,
        "confidence": result.confidence,
        "memory_budget_mb": result.task.budget.memory_budget_mb,
        "token_budget": result.task.budget.token_budget,
        "chunk_budget": result.task.budget.chunk_budget,
        "ref_budget": result.task.budget.source_ref_budget,
        "evidence_ref_count": len(result.task.evidence_refs),
        "has_derived_output_ref": bool(result.task.derived_output_ref),
        "has_review_item_ref": bool(result.task.review_item_ref),
        "needs_review": result.needs_review,
        "has_fallback_model_ref": bool(result.fallback_model_ref),
    }
