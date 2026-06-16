"""Bounded backend contract for small-model evaluation gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_MAX_MEMORY_MB = 2048
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_TERMS = (
    "truth-write",
    "truth write",
    "authoritative",
    "global",
    "research",
    "accelerator",
    "default-fallback-for-all-tasks",
    "default fallback for all tasks",
)


class SmallModelEvaluationError(ValueError):
    """Raised when a small-model evaluation gate payload is invalid or unsafe."""


class EvaluationTaskType(StrEnum):
    CLUSTER_LABELING = "cluster_labeling"
    EVIDENCE_SUMMARY = "evidence_summary"
    ENTITY_CANDIDATE = "entity_candidate"
    EDGE_CANDIDATE = "edge_candidate"
    DEDUPE_CANDIDATE = "dedupe_candidate"
    DRIFT_CHECK = "drift_check"
    REVIEW_PREPARATION = "review_preparation"


class EvaluationGateStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    GO = "go"
    NO_GO = "no_go"
    NEEDS_REVIEW = "needs_review"
    FALLBACK_REQUIRED = "fallback_required"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise SmallModelEvaluationError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise SmallModelEvaluationError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise SmallModelEvaluationError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise SmallModelEvaluationError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise SmallModelEvaluationError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise SmallModelEvaluationError(f"{field_name} must be > 0")
    return normalized


def _normalize_ratio(value: Any, *, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise SmallModelEvaluationError(f"{field_name} must be numeric") from None
    if math.isnan(normalized) or normalized < 0 or normalized > 1:
        raise SmallModelEvaluationError(f"{field_name} must be between 0 and 1")
    return normalized


def _normalize_status(value: Any) -> EvaluationGateStatus:
    if isinstance(value, EvaluationGateStatus):
        return value
    normalized = _normalize_slug(value, field_name="go_no_go_status").replace("-", "_")
    try:
        return EvaluationGateStatus(normalized)
    except ValueError as exc:
        raise SmallModelEvaluationError("go_no_go_status is not supported") from exc


def _normalize_task_type(value: Any) -> EvaluationTaskType:
    if isinstance(value, EvaluationTaskType):
        return value
    normalized = _normalize_slug(value, field_name="task_type").replace("-", "_")
    try:
        return EvaluationTaskType(normalized)
    except ValueError as exc:
        raise SmallModelEvaluationError("task_type is not supported") from exc


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise SmallModelEvaluationError("truth/global/research/accelerator claims are out of scope")


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    latency_budget_ms: int
    memory_budget_mb: int
    token_budget: int
    chunk_budget: int

    @classmethod
    def create(
        cls,
        *,
        latency_budget_ms: Any,
        memory_budget_mb: Any,
        token_budget: Any,
        chunk_budget: Any,
    ) -> "EvaluationBudget":
        memory_mb = _normalize_positive_int(memory_budget_mb, field_name="memory_budget_mb")
        if memory_mb > _MAX_MEMORY_MB:
            raise SmallModelEvaluationError(f"memory_budget_mb must not exceed {_MAX_MEMORY_MB}")
        return cls(
            latency_budget_ms=_normalize_positive_int(latency_budget_ms, field_name="latency_budget_ms"),
            memory_budget_mb=memory_mb,
            token_budget=_normalize_positive_int(token_budget, field_name="token_budget"),
            chunk_budget=_normalize_positive_int(chunk_budget, field_name="chunk_budget"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationThresholds:
    min_source_coverage: float
    min_evidence_coverage: float
    min_citation_accuracy: float
    min_confidence: float
    max_drift_score: float
    max_hallucination_risk: float

    @classmethod
    def create(
        cls,
        *,
        min_source_coverage: Any,
        min_evidence_coverage: Any,
        min_citation_accuracy: Any,
        min_confidence: Any,
        max_drift_score: Any,
        max_hallucination_risk: Any,
    ) -> "EvaluationThresholds":
        return cls(
            min_source_coverage=_normalize_ratio(min_source_coverage, field_name="min_source_coverage"),
            min_evidence_coverage=_normalize_ratio(min_evidence_coverage, field_name="min_evidence_coverage"),
            min_citation_accuracy=_normalize_ratio(min_citation_accuracy, field_name="min_citation_accuracy"),
            min_confidence=_normalize_ratio(min_confidence, field_name="min_confidence"),
            max_drift_score=_normalize_ratio(max_drift_score, field_name="max_drift_score"),
            max_hallucination_risk=_normalize_ratio(
                max_hallucination_risk, field_name="max_hallucination_risk"
            ),
        )


@dataclass(frozen=True, slots=True)
class EvaluationReviewGate:
    review_item_ref: str

    @classmethod
    def create(cls, *, review_item_ref: Any) -> "EvaluationReviewGate":
        return cls(review_item_ref=_normalize_slug(review_item_ref, field_name="review_item_ref"))


@dataclass(frozen=True, slots=True)
class SmallModelEvaluationGate:
    evaluation_gate_id: str
    model_profile_ref: str
    task_type: EvaluationTaskType
    fixture_ref: str
    expected_schema_ref: str
    budget: EvaluationBudget
    thresholds: EvaluationThresholds
    fallback_model_ref: str

    @classmethod
    def create(
        cls,
        *,
        evaluation_gate_id: Any,
        model_profile_ref: Any,
        task_type: EvaluationTaskType | str,
        fixture_ref: Any,
        expected_schema_ref: Any,
        budget: EvaluationBudget,
        thresholds: EvaluationThresholds,
        fallback_model_ref: Any,
    ) -> "SmallModelEvaluationGate":
        if not isinstance(budget, EvaluationBudget):
            raise SmallModelEvaluationError("budget must be an EvaluationBudget")
        if not isinstance(thresholds, EvaluationThresholds):
            raise SmallModelEvaluationError("thresholds must be an EvaluationThresholds")
        model_ref = _normalize_slug(model_profile_ref, field_name="model_profile_ref")
        fixture = _normalize_slug(fixture_ref, field_name="fixture_ref")
        schema_ref = _normalize_slug(expected_schema_ref, field_name="expected_schema_ref")
        fallback_ref = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
        _reject_forbidden_terms(model_ref, fixture, schema_ref, fallback_ref)
        return cls(
            evaluation_gate_id=_normalize_slug(evaluation_gate_id, field_name="evaluation_gate_id"),
            model_profile_ref=model_ref,
            task_type=_normalize_task_type(task_type),
            fixture_ref=fixture,
            expected_schema_ref=schema_ref,
            budget=budget,
            thresholds=thresholds,
            fallback_model_ref=fallback_ref,
        )


@dataclass(frozen=True, slots=True)
class SmallModelEvaluationResult:
    gate: SmallModelEvaluationGate
    json_valid: bool
    source_coverage: float
    evidence_coverage: float
    citation_accuracy: float
    confidence: float
    uncertainty_reason: str
    drift_score: float
    hallucination_risk: float
    go_no_go_status: EvaluationGateStatus
    review_gate: EvaluationReviewGate | None
    fallback_model_ref: str
    risk_evidence_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        gate: SmallModelEvaluationGate,
        json_valid: Any,
        source_coverage: Any,
        evidence_coverage: Any,
        citation_accuracy: Any,
        confidence: Any,
        uncertainty_reason: Any = "",
        drift_score: Any,
        hallucination_risk: Any,
        go_no_go_status: EvaluationGateStatus | str,
        review_item_ref: Any = "",
        fallback_model_ref: Any = "",
        risk_evidence_ref: Any = "",
        reason: Any = "",
        next_action: Any = "",
    ) -> "SmallModelEvaluationResult":
        if not isinstance(gate, SmallModelEvaluationGate):
            raise SmallModelEvaluationError("gate must be a SmallModelEvaluationGate")
        normalized_status = _normalize_status(go_no_go_status)
        normalized_uncertainty = _normalize_text(
            uncertainty_reason,
            field_name="uncertainty_reason",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_review = (
            EvaluationReviewGate.create(review_item_ref=review_item_ref)
            if str(review_item_ref or "").strip()
            else None
        )
        normalized_fallback = (
            _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
            if str(fallback_model_ref or "").strip()
            else ""
        )
        normalized_risk_evidence = _normalize_text(
            risk_evidence_ref,
            field_name="risk_evidence_ref",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(
            next_action,
            field_name="next_action",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        source_coverage_value = _normalize_ratio(source_coverage, field_name="source_coverage")
        evidence_coverage_value = _normalize_ratio(evidence_coverage, field_name="evidence_coverage")
        citation_accuracy_value = _normalize_ratio(citation_accuracy, field_name="citation_accuracy")
        confidence_value = _normalize_ratio(confidence, field_name="confidence")
        drift_score_value = _normalize_ratio(drift_score, field_name="drift_score")
        hallucination_risk_value = _normalize_ratio(
            hallucination_risk,
            field_name="hallucination_risk",
        )
        json_is_valid = bool(json_valid)
        _reject_forbidden_terms(
            gate.model_profile_ref,
            gate.fixture_ref,
            gate.expected_schema_ref,
            gate.fallback_model_ref,
            normalized_uncertainty,
            normalized_fallback,
            normalized_risk_evidence,
            normalized_reason,
            normalized_next_action,
        )
        if normalized_status == EvaluationGateStatus.GO:
            if not json_is_valid:
                raise SmallModelEvaluationError("go requires json_valid=True")
            if not gate.expected_schema_ref:
                raise SmallModelEvaluationError("go requires expected_schema_ref")
            if normalized_uncertainty:
                raise SmallModelEvaluationError("go cannot include uncertainty_reason")
            if not normalized_risk_evidence:
                raise SmallModelEvaluationError("go requires risk_evidence_ref")
            thresholds = gate.thresholds
            if source_coverage_value < thresholds.min_source_coverage:
                raise SmallModelEvaluationError("go requires source_coverage to meet thresholds")
            if evidence_coverage_value < thresholds.min_evidence_coverage:
                raise SmallModelEvaluationError("go requires evidence_coverage to meet thresholds")
            if citation_accuracy_value < thresholds.min_citation_accuracy:
                raise SmallModelEvaluationError("go requires citation_accuracy to meet thresholds")
            if confidence_value < thresholds.min_confidence:
                raise SmallModelEvaluationError("go requires confidence to meet thresholds")
            if drift_score_value > thresholds.max_drift_score:
                raise SmallModelEvaluationError("go requires drift_score to stay within thresholds")
            if hallucination_risk_value > thresholds.max_hallucination_risk:
                raise SmallModelEvaluationError("go requires hallucination_risk to stay within thresholds")
        if normalized_status in {EvaluationGateStatus.NEEDS_REVIEW, EvaluationGateStatus.FALLBACK_REQUIRED} and not (
            normalized_review or normalized_fallback
        ):
            raise SmallModelEvaluationError(
                "needs_review and fallback_required require review_item_ref or fallback_model_ref"
            )
        if normalized_status in {
            EvaluationGateStatus.NO_GO,
            EvaluationGateStatus.BLOCKED,
            EvaluationGateStatus.FAILED,
        } and not (normalized_reason or normalized_next_action):
            raise SmallModelEvaluationError("no_go, blocked, and failed require reason or next_action")
        return cls(
            gate=gate,
            json_valid=json_is_valid,
            source_coverage=source_coverage_value,
            evidence_coverage=evidence_coverage_value,
            citation_accuracy=citation_accuracy_value,
            confidence=confidence_value,
            uncertainty_reason=normalized_uncertainty,
            drift_score=drift_score_value,
            hallucination_risk=hallucination_risk_value,
            go_no_go_status=normalized_status,
            review_gate=normalized_review,
            fallback_model_ref=normalized_fallback,
            risk_evidence_ref=normalized_risk_evidence,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )


def audit_summary(result: SmallModelEvaluationResult) -> dict[str, Any]:
    if not isinstance(result, SmallModelEvaluationResult):
        raise SmallModelEvaluationError("result must be a SmallModelEvaluationResult")
    return {
        "evaluation_gate_id": result.gate.evaluation_gate_id,
        "task_type": result.gate.task_type.value,
        "status": result.go_no_go_status.value,
        "json_valid": result.json_valid,
        "source_coverage": result.source_coverage,
        "evidence_coverage": result.evidence_coverage,
        "citation_accuracy": result.citation_accuracy,
        "confidence": result.confidence,
        "drift_score": result.drift_score,
        "hallucination_risk": result.hallucination_risk,
        "latency_budget_ms": result.gate.budget.latency_budget_ms,
        "memory_budget_mb": result.gate.budget.memory_budget_mb,
        "token_budget": result.gate.budget.token_budget,
        "chunk_budget": result.gate.budget.chunk_budget,
        "has_review_item_ref": bool(result.review_gate),
        "has_fallback_model_ref": bool(result.fallback_model_ref),
        "has_risk_evidence_ref": bool(result.risk_evidence_ref),
    }
