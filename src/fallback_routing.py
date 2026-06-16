"""Bounded backend contract for fallback routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_MAX_RETRIES = 5
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


class FallbackRoutingError(ValueError):
    """Raised when a fallback routing payload is invalid or unsafe."""


class RoutingTaskType(StrEnum):
    CLUSTER_LABELING = "cluster_labeling"
    EVIDENCE_SUMMARY = "evidence_summary"
    ENTITY_CANDIDATE = "entity_candidate"
    EDGE_CANDIDATE = "edge_candidate"
    DEDUPE_CANDIDATE = "dedupe_candidate"
    DRIFT_CHECK = "drift_check"
    REVIEW_PREPARATION = "review_preparation"


class RoutingDecisionStatus(StrEnum):
    USE_MAINTENANCE_MODEL = "use_maintenance_model"
    RETRY_MAINTENANCE = "retry_maintenance"
    USE_FALLBACK_MODEL = "use_fallback_model"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise FallbackRoutingError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise FallbackRoutingError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise FallbackRoutingError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise FallbackRoutingError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise FallbackRoutingError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise FallbackRoutingError(f"{field_name} must be > 0")
    return normalized


def _normalize_non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise FallbackRoutingError(f"{field_name} must be an int") from None
    if normalized < 0:
        raise FallbackRoutingError(f"{field_name} must be >= 0")
    return normalized


def _normalize_status(value: Any) -> RoutingDecisionStatus:
    if isinstance(value, RoutingDecisionStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return RoutingDecisionStatus(normalized)
    except ValueError as exc:
        raise FallbackRoutingError("status is not supported") from exc


def _normalize_task_type(value: Any) -> RoutingTaskType:
    if isinstance(value, RoutingTaskType):
        return value
    normalized = _normalize_slug(value, field_name="task_type").replace("-", "_")
    try:
        return RoutingTaskType(normalized)
    except ValueError as exc:
        raise FallbackRoutingError("task_type is not supported") from exc


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise FallbackRoutingError("truth/global/research/accelerator claims are out of scope")


@dataclass(frozen=True, slots=True)
class RoutingBudget:
    latency_budget_ms: int
    token_budget: int
    cost_budget_units: int
    max_retries: int

    @classmethod
    def create(
        cls,
        *,
        latency_budget_ms: Any,
        token_budget: Any,
        cost_budget_units: Any,
        max_retries: Any,
    ) -> "RoutingBudget":
        retries = _normalize_non_negative_int(max_retries, field_name="max_retries")
        if retries > _MAX_RETRIES:
            raise FallbackRoutingError(f"max_retries must not exceed {_MAX_RETRIES}")
        return cls(
            latency_budget_ms=_normalize_positive_int(latency_budget_ms, field_name="latency_budget_ms"),
            token_budget=_normalize_positive_int(token_budget, field_name="token_budget"),
            cost_budget_units=_normalize_positive_int(cost_budget_units, field_name="cost_budget_units"),
            max_retries=retries,
        )


@dataclass(frozen=True, slots=True)
class RetryBackoffPolicy:
    retry_policy_ref: str
    backoff_policy_ref: str
    retry_count: int
    backoff_seconds: int

    @classmethod
    def create(
        cls,
        *,
        retry_policy_ref: Any,
        backoff_policy_ref: Any,
        retry_count: Any,
        backoff_seconds: Any,
    ) -> "RetryBackoffPolicy":
        return cls(
            retry_policy_ref=_normalize_slug(retry_policy_ref, field_name="retry_policy_ref"),
            backoff_policy_ref=_normalize_slug(backoff_policy_ref, field_name="backoff_policy_ref"),
            retry_count=_normalize_non_negative_int(retry_count, field_name="retry_count"),
            backoff_seconds=_normalize_positive_int(backoff_seconds, field_name="backoff_seconds"),
        )


@dataclass(frozen=True, slots=True)
class RoutingReviewGate:
    review_item_ref: str

    @classmethod
    def create(cls, *, review_item_ref: Any) -> "RoutingReviewGate":
        return cls(review_item_ref=_normalize_slug(review_item_ref, field_name="review_item_ref"))


@dataclass(frozen=True, slots=True)
class FallbackRoutingDecision:
    routing_decision_id: str
    maintenance_model_ref: str
    fallback_model_ref: str
    task_type: RoutingTaskType
    go_no_go_status: str
    budget: RoutingBudget
    retry_policy: RetryBackoffPolicy
    gate_reason_ref: str
    risk_evidence_ref: str

    @classmethod
    def create(
        cls,
        *,
        routing_decision_id: Any,
        maintenance_model_ref: Any,
        fallback_model_ref: Any,
        task_type: RoutingTaskType | str,
        go_no_go_status: Any,
        budget: RoutingBudget,
        retry_policy: RetryBackoffPolicy,
        gate_reason_ref: Any,
        risk_evidence_ref: Any,
    ) -> "FallbackRoutingDecision":
        if not isinstance(budget, RoutingBudget):
            raise FallbackRoutingError("budget must be a RoutingBudget")
        if not isinstance(retry_policy, RetryBackoffPolicy):
            raise FallbackRoutingError("retry_policy must be a RetryBackoffPolicy")
        maintenance_ref = _normalize_slug(maintenance_model_ref, field_name="maintenance_model_ref")
        fallback_ref = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
        gate_ref = _normalize_slug(gate_reason_ref, field_name="gate_reason_ref")
        risk_ref = _normalize_text(
            risk_evidence_ref,
            field_name="risk_evidence_ref",
            allow_empty=False,
            limit=_MAX_LONG_TEXT,
        )
        status_text = _normalize_text(go_no_go_status, field_name="go_no_go_status", allow_empty=False)
        _reject_forbidden_terms(maintenance_ref, fallback_ref, gate_ref, risk_ref, status_text)
        return cls(
            routing_decision_id=_normalize_slug(routing_decision_id, field_name="routing_decision_id"),
            maintenance_model_ref=maintenance_ref,
            fallback_model_ref=fallback_ref,
            task_type=_normalize_task_type(task_type),
            go_no_go_status=status_text,
            budget=budget,
            retry_policy=retry_policy,
            gate_reason_ref=gate_ref,
            risk_evidence_ref=risk_ref,
        )


@dataclass(frozen=True, slots=True)
class FallbackRoutingResult:
    decision: FallbackRoutingDecision
    status: RoutingDecisionStatus
    estimated_cost_units: int
    estimated_latency_ms: int
    estimated_tokens: int
    review_gate: RoutingReviewGate | None
    fallback_model_ref: str
    failure_reason: str
    next_action: str
    audit_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        decision: FallbackRoutingDecision,
        status: RoutingDecisionStatus | str,
        estimated_cost_units: Any,
        estimated_latency_ms: Any,
        estimated_tokens: Any,
        review_item_ref: Any = "",
        fallback_model_ref: Any = "",
        failure_reason: Any = "",
        next_action: Any = "",
        audit_refs: tuple[Any, ...] | list[Any],
    ) -> "FallbackRoutingResult":
        if not isinstance(decision, FallbackRoutingDecision):
            raise FallbackRoutingError("decision must be a FallbackRoutingDecision")
        normalized_status = _normalize_status(status)
        cost_units = _normalize_positive_int(estimated_cost_units, field_name="estimated_cost_units")
        latency_ms = _normalize_positive_int(estimated_latency_ms, field_name="estimated_latency_ms")
        tokens = _normalize_positive_int(estimated_tokens, field_name="estimated_tokens")
        normalized_review = (
            RoutingReviewGate.create(review_item_ref=review_item_ref)
            if str(review_item_ref or "").strip()
            else None
        )
        normalized_fallback = (
            _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
            if str(fallback_model_ref or "").strip()
            else ""
        )
        normalized_failure = _normalize_text(
            failure_reason,
            field_name="failure_reason",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_next_action = _normalize_text(
            next_action,
            field_name="next_action",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_audit_refs = tuple(
            _normalize_slug(item, field_name="audit_ref") for item in audit_refs if str(item or "").strip()
        )
        if not normalized_audit_refs:
            raise FallbackRoutingError("audit_refs must not be empty")
        _reject_forbidden_terms(
            decision.maintenance_model_ref,
            decision.fallback_model_ref,
            decision.gate_reason_ref,
            decision.risk_evidence_ref,
            normalized_fallback,
            normalized_failure,
            normalized_next_action,
            " ".join(normalized_audit_refs),
        )
        over_budget = (
            cost_units > decision.budget.cost_budget_units
            or latency_ms > decision.budget.latency_budget_ms
            or tokens > decision.budget.token_budget
        )
        if normalized_status == RoutingDecisionStatus.USE_MAINTENANCE_MODEL:
            if "go" not in decision.go_no_go_status.lower():
                raise FallbackRoutingError("use_maintenance_model requires positive gate signal")
            if over_budget:
                raise FallbackRoutingError("budget exceedance prevents automatic execution")
        if normalized_status == RoutingDecisionStatus.RETRY_MAINTENANCE:
            if not decision.retry_policy.retry_policy_ref or not decision.retry_policy.backoff_policy_ref:
                raise FallbackRoutingError("retry_maintenance requires retry and backoff policy")
            if decision.retry_policy.retry_count > decision.budget.max_retries:
                raise FallbackRoutingError("retry_count must not exceed max_retries")
            if not normalized_next_action:
                raise FallbackRoutingError("retry_maintenance requires next_action")
            if over_budget:
                raise FallbackRoutingError("retry_maintenance requires budget reserve")
        if normalized_status == RoutingDecisionStatus.USE_FALLBACK_MODEL:
            if not (normalized_fallback and decision.gate_reason_ref and decision.risk_evidence_ref):
                raise FallbackRoutingError(
                    "use_fallback_model requires fallback_model_ref, gate_reason_ref, and risk_evidence_ref"
                )
        if normalized_status == RoutingDecisionStatus.NEEDS_REVIEW and not (
            normalized_review or normalized_next_action
        ):
            raise FallbackRoutingError("needs_review requires review_item_ref or next_action")
        if normalized_status in {RoutingDecisionStatus.BLOCKED, RoutingDecisionStatus.FAILED} and not (
            normalized_failure or normalized_next_action
        ):
            raise FallbackRoutingError("blocked and failed require failure_reason or next_action")
        if over_budget and normalized_status not in {
            RoutingDecisionStatus.NEEDS_REVIEW,
            RoutingDecisionStatus.BLOCKED,
            RoutingDecisionStatus.FAILED,
        }:
            raise FallbackRoutingError("budget exceedance prevents automatic execution")
        return cls(
            decision=decision,
            status=normalized_status,
            estimated_cost_units=cost_units,
            estimated_latency_ms=latency_ms,
            estimated_tokens=tokens,
            review_gate=normalized_review,
            fallback_model_ref=normalized_fallback,
            failure_reason=normalized_failure,
            next_action=normalized_next_action,
            audit_refs=normalized_audit_refs,
        )


def audit_summary(result: FallbackRoutingResult) -> dict[str, Any]:
    if not isinstance(result, FallbackRoutingResult):
        raise FallbackRoutingError("result must be a FallbackRoutingResult")
    return {
        "routing_decision_id": result.decision.routing_decision_id,
        "task_type": result.decision.task_type.value,
        "status": result.status.value,
        "estimated_cost_units": result.estimated_cost_units,
        "estimated_latency_ms": result.estimated_latency_ms,
        "estimated_tokens": result.estimated_tokens,
        "cost_budget_units": result.decision.budget.cost_budget_units,
        "latency_budget_ms": result.decision.budget.latency_budget_ms,
        "token_budget": result.decision.budget.token_budget,
        "max_retries": result.decision.budget.max_retries,
        "retry_count": result.decision.retry_policy.retry_count,
        "backoff_seconds": result.decision.retry_policy.backoff_seconds,
        "audit_ref_count": len(result.audit_refs),
        "has_review_item_ref": bool(result.review_gate),
        "has_fallback_model_ref": bool(result.fallback_model_ref),
        "has_failure_reason": bool(result.failure_reason),
    }
