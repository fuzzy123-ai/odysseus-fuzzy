"""Small backend contract for bounded graph maintenance worker payloads."""

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
    "global-graph",
    "global graph",
    "research",
    "accelerator",
)
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class GraphMaintenanceWorkerError(ValueError):
    """Raised when a graph maintenance worker payload is invalid or unsafe."""


class GraphMaintenanceTaskType(StrEnum):
    ENTITY_CANDIDATE = "entity_candidate"
    EDGE_CANDIDATE = "edge_candidate"
    DEDUPE_CANDIDATE = "dedupe_candidate"
    MERGE_REVIEW = "merge_review"
    DRIFT_CHECK = "drift_check"


class GraphMaintenanceStatus(StrEnum):
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
        raise GraphMaintenanceWorkerError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise GraphMaintenanceWorkerError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise GraphMaintenanceWorkerError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise GraphMaintenanceWorkerError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise GraphMaintenanceWorkerError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise GraphMaintenanceWorkerError(f"{field_name} must be > 0")
    return normalized


def _normalize_status(value: Any) -> GraphMaintenanceStatus:
    if isinstance(value, GraphMaintenanceStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return GraphMaintenanceStatus(normalized)
    except ValueError as exc:
        raise GraphMaintenanceWorkerError("status is not supported") from exc


def _normalize_task_type(value: Any) -> GraphMaintenanceTaskType:
    if isinstance(value, GraphMaintenanceTaskType):
        return value
    normalized = _normalize_slug(value, field_name="task_type").replace("-", "_")
    try:
        return GraphMaintenanceTaskType(normalized)
    except ValueError as exc:
        raise GraphMaintenanceWorkerError("task_type is not supported") from exc


def _normalize_confidence(value: Any) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise GraphMaintenanceWorkerError("confidence must be numeric") from None
    if math.isnan(normalized) or normalized < 0 or normalized > 1:
        raise GraphMaintenanceWorkerError("confidence must be between 0 and 1")
    return normalized


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise GraphMaintenanceWorkerError("truth/global/research/accelerator claims are out of scope")


@dataclass(frozen=True, slots=True)
class GraphMaintenanceBudget:
    max_candidates: int
    max_source_refs: int
    max_edges: int
    max_entities: int
    max_memory_mb: int

    @classmethod
    def create(
        cls,
        *,
        max_candidates: Any,
        max_source_refs: Any,
        max_edges: Any,
        max_entities: Any,
        max_memory_mb: Any,
    ) -> "GraphMaintenanceBudget":
        memory_mb = _normalize_positive_int(max_memory_mb, field_name="max_memory_mb")
        if memory_mb > _MAX_MEMORY_MB:
            raise GraphMaintenanceWorkerError(f"max_memory_mb must not exceed {_MAX_MEMORY_MB}")
        return cls(
            max_candidates=_normalize_positive_int(max_candidates, field_name="max_candidates"),
            max_source_refs=_normalize_positive_int(max_source_refs, field_name="max_source_refs"),
            max_edges=_normalize_positive_int(max_edges, field_name="max_edges"),
            max_entities=_normalize_positive_int(max_entities, field_name="max_entities"),
            max_memory_mb=memory_mb,
        )


@dataclass(frozen=True, slots=True)
class GraphSourceRef:
    source_ref: str
    chunk_ref: str
    evidence_ref: str
    provenance_ref: str

    @classmethod
    def create(
        cls,
        *,
        source_ref: Any,
        chunk_ref: Any,
        evidence_ref: Any,
        provenance_ref: Any,
    ) -> "GraphSourceRef":
        evidence = _normalize_slug(evidence_ref, field_name="evidence_ref")
        provenance = _normalize_slug(provenance_ref, field_name="provenance_ref")
        _reject_forbidden_terms(evidence, provenance)
        return cls(
            source_ref=_normalize_slug(source_ref, field_name="source_ref"),
            chunk_ref=_normalize_slug(chunk_ref, field_name="chunk_ref"),
            evidence_ref=evidence,
            provenance_ref=provenance,
        )


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    entity_candidate_ref: str
    label: str
    entity_type: str
    dedupe_key: str
    confidence: float
    source_refs: tuple[GraphSourceRef, ...]

    @classmethod
    def create(
        cls,
        *,
        entity_candidate_ref: Any,
        label: Any,
        entity_type: Any,
        dedupe_key: Any,
        confidence: Any,
        source_refs: Iterable[GraphSourceRef],
    ) -> "EntityCandidate":
        normalized_sources = tuple(source_refs)
        if not normalized_sources or any(not isinstance(item, GraphSourceRef) for item in normalized_sources):
            raise GraphMaintenanceWorkerError("entity candidate source_refs must contain GraphSourceRef items")
        dedupe = _normalize_slug(dedupe_key, field_name="dedupe_key")
        label_text = _normalize_text(label, field_name="label", allow_empty=False, limit=80)
        entity_type_ref = _normalize_slug(entity_type, field_name="entity_type")
        _reject_forbidden_terms(label_text, entity_type_ref, dedupe)
        return cls(
            entity_candidate_ref=_normalize_slug(entity_candidate_ref, field_name="entity_candidate_ref"),
            label=label_text,
            entity_type=entity_type_ref,
            dedupe_key=dedupe,
            confidence=_normalize_confidence(confidence),
            source_refs=normalized_sources,
        )


@dataclass(frozen=True, slots=True)
class EdgeCandidate:
    edge_candidate_ref: str
    source_entity_ref: str
    target_entity_ref: str
    relation_type: str
    dedupe_key: str
    confidence: float
    source_refs: tuple[GraphSourceRef, ...]

    @classmethod
    def create(
        cls,
        *,
        edge_candidate_ref: Any,
        source_entity_ref: Any,
        target_entity_ref: Any,
        relation_type: Any,
        dedupe_key: Any,
        confidence: Any,
        source_refs: Iterable[GraphSourceRef],
    ) -> "EdgeCandidate":
        source_entity = _normalize_slug(source_entity_ref, field_name="source_entity_ref")
        target_entity = _normalize_slug(target_entity_ref, field_name="target_entity_ref")
        if source_entity == target_entity:
            raise GraphMaintenanceWorkerError("self-loop edge candidates are not silently accepted")
        normalized_sources = tuple(source_refs)
        if not normalized_sources or any(not isinstance(item, GraphSourceRef) for item in normalized_sources):
            raise GraphMaintenanceWorkerError("edge candidate source_refs must contain GraphSourceRef items")
        relation = _normalize_slug(relation_type, field_name="relation_type")
        dedupe = _normalize_slug(dedupe_key, field_name="dedupe_key")
        _reject_forbidden_terms(relation, dedupe)
        return cls(
            edge_candidate_ref=_normalize_slug(edge_candidate_ref, field_name="edge_candidate_ref"),
            source_entity_ref=source_entity,
            target_entity_ref=target_entity,
            relation_type=relation,
            dedupe_key=dedupe,
            confidence=_normalize_confidence(confidence),
            source_refs=normalized_sources,
        )


@dataclass(frozen=True, slots=True)
class GraphReviewGate:
    review_item_ref: str

    @classmethod
    def create(cls, *, review_item_ref: Any) -> "GraphReviewGate":
        return cls(review_item_ref=_normalize_slug(review_item_ref, field_name="review_item_ref"))


@dataclass(frozen=True, slots=True)
class GraphMaintenanceTask:
    graph_maintenance_task_id: str
    task_type: GraphMaintenanceTaskType
    budget: GraphMaintenanceBudget
    source_refs: tuple[GraphSourceRef, ...]
    entity_candidates: tuple[EntityCandidate, ...]
    edge_candidates: tuple[EdgeCandidate, ...]
    merge_policy_ref: str
    truth_write_allowed: bool
    fallback_model_ref: str

    @classmethod
    def create(
        cls,
        *,
        graph_maintenance_task_id: Any,
        task_type: GraphMaintenanceTaskType | str,
        budget: GraphMaintenanceBudget,
        source_refs: Iterable[GraphSourceRef],
        entity_candidates: Iterable[EntityCandidate],
        edge_candidates: Iterable[EdgeCandidate],
        merge_policy_ref: Any,
        truth_write_allowed: Any,
        fallback_model_ref: Any,
    ) -> "GraphMaintenanceTask":
        if not isinstance(budget, GraphMaintenanceBudget):
            raise GraphMaintenanceWorkerError("budget must be a GraphMaintenanceBudget")
        normalized_sources = tuple(source_refs)
        if not normalized_sources or any(not isinstance(item, GraphSourceRef) for item in normalized_sources):
            raise GraphMaintenanceWorkerError("source_refs must contain GraphSourceRef items")
        if len(normalized_sources) > budget.max_source_refs:
            raise GraphMaintenanceWorkerError("source_refs must not exceed max_source_refs")
        normalized_entities = tuple(entity_candidates)
        normalized_edges = tuple(edge_candidates)
        if any(not isinstance(item, EntityCandidate) for item in normalized_entities):
            raise GraphMaintenanceWorkerError("entity_candidates must contain EntityCandidate items")
        if any(not isinstance(item, EdgeCandidate) for item in normalized_edges):
            raise GraphMaintenanceWorkerError("edge_candidates must contain EdgeCandidate items")
        if len(normalized_entities) + len(normalized_edges) > budget.max_candidates:
            raise GraphMaintenanceWorkerError("candidate count must not exceed max_candidates")
        if len(normalized_entities) > budget.max_entities:
            raise GraphMaintenanceWorkerError("entity candidate count must not exceed max_entities")
        if len(normalized_edges) > budget.max_edges:
            raise GraphMaintenanceWorkerError("edge candidate count must not exceed max_edges")
        for candidate in normalized_entities:
            if len(candidate.source_refs) > budget.max_source_refs:
                raise GraphMaintenanceWorkerError("entity candidate source_refs must not exceed max_source_refs")
        for candidate in normalized_edges:
            if len(candidate.source_refs) > budget.max_source_refs:
                raise GraphMaintenanceWorkerError("edge candidate source_refs must not exceed max_source_refs")
        if bool(truth_write_allowed):
            raise GraphMaintenanceWorkerError("truth_write_allowed=True is forbidden")
        merge_policy = _normalize_slug(merge_policy_ref, field_name="merge_policy_ref")
        fallback_model = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref")
        _reject_forbidden_terms(merge_policy, fallback_model)
        return cls(
            graph_maintenance_task_id=_normalize_slug(graph_maintenance_task_id, field_name="graph_maintenance_task_id"),
            task_type=_normalize_task_type(task_type),
            budget=budget,
            source_refs=normalized_sources,
            entity_candidates=normalized_entities,
            edge_candidates=normalized_edges,
            merge_policy_ref=merge_policy,
            truth_write_allowed=False,
            fallback_model_ref=fallback_model,
        )


@dataclass(frozen=True, slots=True)
class GraphMaintenanceResult:
    task: GraphMaintenanceTask
    status: GraphMaintenanceStatus
    confidence: float
    uncertainty_reason: str
    needs_review: bool
    review_gate: GraphReviewGate | None
    fallback_model_ref: str
    drift_check_ref: str
    risk_evidence_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        task: GraphMaintenanceTask,
        status: GraphMaintenanceStatus | str,
        confidence: Any,
        uncertainty_reason: Any = "",
        needs_review: Any = False,
        review_item_ref: Any = "",
        fallback_model_ref: Any = "",
        drift_check_ref: Any = "",
        risk_evidence_ref: Any = "",
        reason: Any = "",
        next_action: Any = "",
    ) -> "GraphMaintenanceResult":
        if not isinstance(task, GraphMaintenanceTask):
            raise GraphMaintenanceWorkerError("task must be a GraphMaintenanceTask")
        normalized_status = _normalize_status(status)
        normalized_confidence = _normalize_confidence(confidence)
        normalized_uncertainty = _normalize_text(uncertainty_reason, field_name="uncertainty_reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_needs_review = bool(needs_review)
        normalized_review = GraphReviewGate.create(review_item_ref=review_item_ref) if str(review_item_ref or "").strip() else None
        normalized_fallback = _normalize_slug(fallback_model_ref, field_name="fallback_model_ref") if str(fallback_model_ref or "").strip() else ""
        normalized_drift = _normalize_slug(drift_check_ref, field_name="drift_check_ref") if str(drift_check_ref or "").strip() else ""
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
            if normalized_status not in {GraphMaintenanceStatus.NEEDS_REVIEW, GraphMaintenanceStatus.FALLBACK_REQUIRED}:
                raise GraphMaintenanceWorkerError("low confidence or uncertainty requires needs_review or fallback_required")
        if normalized_status == GraphMaintenanceStatus.COMPLETED and not (
            task.merge_policy_ref and normalized_risk and (normalized_confidence >= 0.8 or not normalized_needs_review)
        ):
            raise GraphMaintenanceWorkerError("completed requires merge_policy_ref, risk_evidence_ref, and clear confidence/review state")
        if normalized_status in {GraphMaintenanceStatus.NEEDS_REVIEW, GraphMaintenanceStatus.FALLBACK_REQUIRED} and not (
            normalized_review or normalized_fallback
        ):
            raise GraphMaintenanceWorkerError("needs_review and fallback_required require review_item_ref or fallback_model_ref")
        if normalized_status in {GraphMaintenanceStatus.BLOCKED, GraphMaintenanceStatus.FAILED} and not (
            normalized_reason or normalized_next_action
        ):
            raise GraphMaintenanceWorkerError("blocked and failed require reason or next_action")
        return cls(
            task=task,
            status=normalized_status,
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


def audit_summary(result: GraphMaintenanceResult) -> dict[str, Any]:
    if not isinstance(result, GraphMaintenanceResult):
        raise GraphMaintenanceWorkerError("result must be a GraphMaintenanceResult")
    return {
        "graph_maintenance_task_id": result.task.graph_maintenance_task_id,
        "task_type": result.task.task_type.value,
        "status": result.status.value,
        "confidence": result.confidence,
        "source_ref_count": len(result.task.source_refs),
        "entity_candidate_count": len(result.task.entity_candidates),
        "edge_candidate_count": len(result.task.edge_candidates),
        "max_candidates": result.task.budget.max_candidates,
        "max_source_refs": result.task.budget.max_source_refs,
        "has_merge_policy_ref": bool(result.task.merge_policy_ref),
        "has_review_item_ref": bool(result.review_gate),
        "has_fallback_model_ref": bool(result.fallback_model_ref),
        "has_drift_check_ref": bool(result.drift_check_ref),
        "has_risk_evidence_ref": bool(result.risk_evidence_ref),
    }
