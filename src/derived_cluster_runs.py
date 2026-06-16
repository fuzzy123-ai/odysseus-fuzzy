"""Small backend contract for derived cluster run models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_TERMS = ("umap", "gmm", "qdrant", "kuzu", "accelerator", "research", "truth-store", "truth store")


class DerivedClusterRunError(ValueError):
    """Raised when a derived cluster run payload is invalid or unsafe."""


class ClusterRunStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise DerivedClusterRunError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise DerivedClusterRunError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise DerivedClusterRunError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise DerivedClusterRunError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise DerivedClusterRunError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise DerivedClusterRunError(f"{field_name} must be > 0")
    return normalized


def _normalize_status(value: Any) -> ClusterRunStatus:
    if isinstance(value, ClusterRunStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return ClusterRunStatus(normalized)
    except ValueError as exc:
        raise DerivedClusterRunError("status is not supported") from exc


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise DerivedClusterRunError("forbidden accelerator, research, or truth-store claim detected")


@dataclass(frozen=True, slots=True)
class ClusterAlgorithmRef:
    algorithm_ref: str
    algorithm_version: str
    embedding_snapshot_ref: str

    @classmethod
    def create(
        cls,
        *,
        algorithm_ref: Any,
        algorithm_version: Any,
        embedding_snapshot_ref: Any,
    ) -> "ClusterAlgorithmRef":
        algorithm = _normalize_slug(algorithm_ref, field_name="algorithm_ref")
        version = _normalize_slug(algorithm_version, field_name="algorithm_version")
        snapshot = _normalize_slug(embedding_snapshot_ref, field_name="embedding_snapshot_ref")
        _reject_forbidden_terms(algorithm, version, snapshot)
        return cls(
            algorithm_ref=algorithm,
            algorithm_version=version,
            embedding_snapshot_ref=snapshot,
        )


@dataclass(frozen=True, slots=True)
class ClusterRunBudget:
    input_count: int
    max_input_count: int
    cluster_count: int
    min_cluster_size: int
    max_cluster_size: int
    max_depth: int

    @classmethod
    def create(
        cls,
        *,
        input_count: Any,
        max_input_count: Any,
        cluster_count: Any,
        min_cluster_size: Any,
        max_cluster_size: Any,
        max_depth: Any,
    ) -> "ClusterRunBudget":
        input_count_value = _normalize_positive_int(input_count, field_name="input_count")
        max_input_count_value = _normalize_positive_int(max_input_count, field_name="max_input_count")
        cluster_count_value = _normalize_positive_int(cluster_count, field_name="cluster_count")
        min_cluster_size_value = _normalize_positive_int(min_cluster_size, field_name="min_cluster_size")
        max_cluster_size_value = _normalize_positive_int(max_cluster_size, field_name="max_cluster_size")
        max_depth_value = _normalize_positive_int(max_depth, field_name="max_depth")
        if input_count_value > max_input_count_value:
            raise DerivedClusterRunError("input_count must not exceed max_input_count")
        if min_cluster_size_value > max_cluster_size_value:
            raise DerivedClusterRunError("min_cluster_size must not exceed max_cluster_size")
        return cls(
            input_count=input_count_value,
            max_input_count=max_input_count_value,
            cluster_count=cluster_count_value,
            min_cluster_size=min_cluster_size_value,
            max_cluster_size=max_cluster_size_value,
            max_depth=max_depth_value,
        )


@dataclass(frozen=True, slots=True)
class ClusterNode:
    cluster_node_ref: str
    cluster_run_id: str
    depth: int
    parent_cluster_ref: str
    child_cluster_refs: tuple[str, ...]
    derived_output_ref: str
    label_review_item_ref: str

    @classmethod
    def create(
        cls,
        *,
        cluster_node_ref: Any,
        cluster_run_id: Any,
        depth: Any,
        parent_cluster_ref: Any = "",
        child_cluster_refs: Iterable[Any] = (),
        derived_output_ref: Any = "",
        label_review_item_ref: Any = "",
    ) -> "ClusterNode":
        child_refs = tuple(sorted({_normalize_slug(ref, field_name="child_cluster_ref") for ref in child_cluster_refs}))
        return cls(
            cluster_node_ref=_normalize_slug(cluster_node_ref, field_name="cluster_node_ref"),
            cluster_run_id=_normalize_slug(cluster_run_id, field_name="cluster_run_id"),
            depth=int(depth),
            parent_cluster_ref=_normalize_slug(parent_cluster_ref, field_name="parent_cluster_ref") if str(parent_cluster_ref or "").strip() else "",
            child_cluster_refs=child_refs,
            derived_output_ref=_normalize_slug(derived_output_ref, field_name="derived_output_ref") if str(derived_output_ref or "").strip() else "",
            label_review_item_ref=_normalize_slug(label_review_item_ref, field_name="label_review_item_ref") if str(label_review_item_ref or "").strip() else "",
        )


@dataclass(frozen=True, slots=True)
class ClusterMembership:
    cluster_membership_ref: str
    cluster_node_ref: str
    source_ref: str
    chunk_ref: str
    embedding_ref: str
    score: float

    @classmethod
    def create(
        cls,
        *,
        cluster_membership_ref: Any,
        cluster_node_ref: Any,
        source_ref: Any,
        chunk_ref: Any,
        embedding_ref: Any,
        score: Any,
    ) -> "ClusterMembership":
        try:
            normalized_score = float(score)
        except (TypeError, ValueError):
            raise DerivedClusterRunError("score must be numeric") from None
        if math.isnan(normalized_score) or normalized_score < 0 or normalized_score > 1:
            raise DerivedClusterRunError("score must be between 0 and 1")
        return cls(
            cluster_membership_ref=_normalize_slug(cluster_membership_ref, field_name="cluster_membership_ref"),
            cluster_node_ref=_normalize_slug(cluster_node_ref, field_name="cluster_node_ref"),
            source_ref=_normalize_slug(source_ref, field_name="source_ref"),
            chunk_ref=_normalize_slug(chunk_ref, field_name="chunk_ref"),
            embedding_ref=_normalize_slug(embedding_ref, field_name="embedding_ref"),
            score=normalized_score,
        )


@dataclass(frozen=True, slots=True)
class ClusterQualityGate:
    quality_gate_ref: str

    @classmethod
    def create(cls, *, quality_gate_ref: Any) -> "ClusterQualityGate":
        return cls(quality_gate_ref=_normalize_slug(quality_gate_ref, field_name="quality_gate_ref"))


@dataclass(frozen=True, slots=True)
class DerivedClusterRun:
    cluster_run_id: str
    source_scope_ref: str
    algorithm: ClusterAlgorithmRef
    budget: ClusterRunBudget
    nodes: tuple[ClusterNode, ...]
    memberships: tuple[ClusterMembership, ...]
    rebuild_ref: str
    quality_gate: ClusterQualityGate | None
    risk_evidence_ref: str
    status: ClusterRunStatus
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        cluster_run_id: Any,
        source_scope_ref: Any,
        algorithm: ClusterAlgorithmRef,
        budget: ClusterRunBudget,
        nodes: Iterable[ClusterNode],
        memberships: Iterable[ClusterMembership],
        rebuild_ref: Any = "",
        quality_gate_ref: Any = "",
        risk_evidence_ref: Any = "",
        status: ClusterRunStatus | str,
        reason: Any = "",
        next_action: Any = "",
    ) -> "DerivedClusterRun":
        if not isinstance(algorithm, ClusterAlgorithmRef):
            raise DerivedClusterRunError("algorithm must be a ClusterAlgorithmRef")
        if not isinstance(budget, ClusterRunBudget):
            raise DerivedClusterRunError("budget must be a ClusterRunBudget")
        normalized_nodes = tuple(nodes)
        normalized_memberships = tuple(memberships)
        if any(not isinstance(node, ClusterNode) for node in normalized_nodes):
            raise DerivedClusterRunError("nodes must contain ClusterNode items")
        if any(not isinstance(item, ClusterMembership) for item in normalized_memberships):
            raise DerivedClusterRunError("memberships must contain ClusterMembership items")
        normalized_status = _normalize_status(status)
        normalized_rebuild = _normalize_slug(rebuild_ref, field_name="rebuild_ref") if str(rebuild_ref or "").strip() else ""
        normalized_quality = ClusterQualityGate.create(quality_gate_ref=quality_gate_ref) if str(quality_gate_ref or "").strip() else None
        normalized_risk = _normalize_text(risk_evidence_ref, field_name="risk_evidence_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_forbidden_terms(normalized_risk, normalized_reason, normalized_next_action)

        if len(normalized_memberships) > budget.input_count:
            raise DerivedClusterRunError("memberships must not exceed input_count")
        for node in normalized_nodes:
            if node.depth > budget.max_depth:
                raise DerivedClusterRunError("node depth must not exceed max_depth")
            if node.cluster_run_id != _normalize_slug(cluster_run_id, field_name="cluster_run_id"):
                raise DerivedClusterRunError("cluster nodes must belong to the same cluster_run_id")

        if normalized_status == ClusterRunStatus.COMPLETED and not (
            normalized_rebuild and normalized_quality and normalized_risk
        ):
            raise DerivedClusterRunError("completed runs require rebuild_ref, quality_gate_ref, and risk_evidence_ref")
        if normalized_status in {ClusterRunStatus.BLOCKED, ClusterRunStatus.FAILED, ClusterRunStatus.NEEDS_REVIEW} and not (
            normalized_reason or normalized_next_action
        ):
            raise DerivedClusterRunError("blocked, failed, and needs_review runs require reason or next_action")

        return cls(
            cluster_run_id=_normalize_slug(cluster_run_id, field_name="cluster_run_id"),
            source_scope_ref=_normalize_slug(source_scope_ref, field_name="source_scope_ref"),
            algorithm=algorithm,
            budget=budget,
            nodes=tuple(sorted(normalized_nodes, key=lambda node: node.cluster_node_ref)),
            memberships=tuple(sorted(normalized_memberships, key=lambda item: item.cluster_membership_ref)),
            rebuild_ref=normalized_rebuild,
            quality_gate=normalized_quality,
            risk_evidence_ref=normalized_risk,
            status=normalized_status,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "cluster_run_id": self.cluster_run_id,
            "source_scope_ref": self.source_scope_ref,
            "status": self.status.value,
            "algorithm_ref": self.algorithm.algorithm_ref,
            "algorithm_version": self.algorithm.algorithm_version,
            "embedding_snapshot_ref": self.algorithm.embedding_snapshot_ref,
            "node_count": len(self.nodes),
            "membership_count": len(self.memberships),
            "max_depth": self.budget.max_depth,
            "has_rebuild_ref": bool(self.rebuild_ref),
            "has_quality_gate_ref": bool(self.quality_gate),
            "has_risk_evidence_ref": bool(self.risk_evidence_ref),
        }
