"""Small backend contract for deterministic tiny k-means clustering proofs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import random
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_MAX_MEMORY_MB = 2048
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_TERMS = ("umap", "gmm", "qdrant", "kuzu", "accelerator", "research")


class KmeansClusteringProofError(ValueError):
    """Raised when a k-means clustering proof payload is invalid or unsafe."""


class ClusteringAlgorithm(StrEnum):
    KMEANS = "kmeans"
    BISECTING_KMEANS = "bisecting_kmeans"


class ClusteringProofStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise KmeansClusteringProofError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise KmeansClusteringProofError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise KmeansClusteringProofError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise KmeansClusteringProofError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_positive_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise KmeansClusteringProofError(f"{field_name} must be an int") from None
    if normalized <= 0:
        raise KmeansClusteringProofError(f"{field_name} must be > 0")
    return normalized


def _normalize_status(value: Any) -> ClusteringProofStatus:
    if isinstance(value, ClusteringProofStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return ClusteringProofStatus(normalized)
    except ValueError as exc:
        raise KmeansClusteringProofError("status is not supported") from exc


def _normalize_algorithm(value: Any) -> ClusteringAlgorithm:
    if isinstance(value, ClusteringAlgorithm):
        return value
    normalized = _normalize_slug(value, field_name="algorithm_ref").replace("-", "_")
    try:
        return ClusteringAlgorithm(normalized)
    except ValueError as exc:
        raise KmeansClusteringProofError("algorithm_ref is not supported") from exc


def _reject_forbidden_terms(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_TERMS):
        raise KmeansClusteringProofError("umap, gmm, accelerator, or research claims are out of scope")


def _euclidean_squared(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum((left - right) ** 2 for left, right in zip(a, b))


@dataclass(frozen=True, slots=True)
class ClusteringProofBudget:
    max_input_count: int
    max_dimension_count: int
    max_k: int
    max_iterations: int
    max_memory_mb: int

    @classmethod
    def create(
        cls,
        *,
        max_input_count: Any,
        max_dimension_count: Any,
        max_k: Any,
        max_iterations: Any,
        max_memory_mb: Any,
    ) -> "ClusteringProofBudget":
        memory_mb = _normalize_positive_int(max_memory_mb, field_name="max_memory_mb")
        if memory_mb > _MAX_MEMORY_MB:
            raise KmeansClusteringProofError(f"max_memory_mb must not exceed {_MAX_MEMORY_MB}")
        return cls(
            max_input_count=_normalize_positive_int(max_input_count, field_name="max_input_count"),
            max_dimension_count=_normalize_positive_int(max_dimension_count, field_name="max_dimension_count"),
            max_k=_normalize_positive_int(max_k, field_name="max_k"),
            max_iterations=_normalize_positive_int(max_iterations, field_name="max_iterations"),
            max_memory_mb=memory_mb,
        )


@dataclass(frozen=True, slots=True)
class ClusteringFixture:
    fixture_ref: str
    embedding_snapshot_ref: str
    vectors: tuple[tuple[float, ...], ...]
    source_refs: tuple[str, ...]
    chunk_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        fixture_ref: Any,
        embedding_snapshot_ref: Any,
        vectors: Iterable[Iterable[Any]],
        source_refs: Iterable[Any],
        chunk_refs: Iterable[Any],
    ) -> "ClusteringFixture":
        normalized_vectors: list[tuple[float, ...]] = []
        dimension = None
        for vector in vectors:
            current = tuple(float(value) for value in vector)
            if not current:
                raise KmeansClusteringProofError("vectors must not contain empty embeddings")
            if dimension is None:
                dimension = len(current)
            elif len(current) != dimension:
                raise KmeansClusteringProofError("vectors must share the same dimension")
            normalized_vectors.append(current)
        normalized_sources = tuple(_normalize_slug(ref, field_name="source_ref") for ref in source_refs)
        normalized_chunks = tuple(_normalize_slug(ref, field_name="chunk_ref") for ref in chunk_refs)
        if not normalized_vectors:
            raise KmeansClusteringProofError("vectors must not be empty")
        if len(normalized_sources) != len(normalized_vectors) or len(normalized_chunks) != len(normalized_vectors):
            raise KmeansClusteringProofError("source_refs and chunk_refs must match vector count")
        snapshot_ref = _normalize_slug(embedding_snapshot_ref, field_name="embedding_snapshot_ref")
        _reject_forbidden_terms(snapshot_ref)
        return cls(
            fixture_ref=_normalize_slug(fixture_ref, field_name="fixture_ref"),
            embedding_snapshot_ref=snapshot_ref,
            vectors=tuple(normalized_vectors),
            source_refs=normalized_sources,
            chunk_refs=normalized_chunks,
        )


@dataclass(frozen=True, slots=True)
class ClusterAssignment:
    assignment_ref: str
    input_index: int
    cluster_index: int

    @classmethod
    def create(
        cls,
        *,
        assignment_ref: Any,
        input_index: Any,
        cluster_index: Any,
    ) -> "ClusterAssignment":
        input_idx = int(input_index)
        cluster_idx = int(cluster_index)
        if input_idx < 0 or cluster_idx < 0:
            raise KmeansClusteringProofError("assignment indexes must be >= 0")
        return cls(
            assignment_ref=_normalize_slug(assignment_ref, field_name="assignment_ref"),
            input_index=input_idx,
            cluster_index=cluster_idx,
        )


@dataclass(frozen=True, slots=True)
class ClusteringProofRun:
    clustering_proof_id: str
    algorithm_ref: ClusteringAlgorithm
    algorithm_version: str
    seed: int
    k: int
    budget: ClusteringProofBudget
    fixture: ClusteringFixture
    quality_gate_ref: str
    rebuild_ref: str
    risk_evidence_ref: str

    @classmethod
    def create(
        cls,
        *,
        clustering_proof_id: Any,
        algorithm_ref: ClusteringAlgorithm | str,
        algorithm_version: Any,
        seed: Any,
        k: Any,
        budget: ClusteringProofBudget,
        fixture: ClusteringFixture,
        quality_gate_ref: Any = "",
        rebuild_ref: Any = "",
        risk_evidence_ref: Any = "",
    ) -> "ClusteringProofRun":
        if not isinstance(budget, ClusteringProofBudget):
            raise KmeansClusteringProofError("budget must be a ClusteringProofBudget")
        if not isinstance(fixture, ClusteringFixture):
            raise KmeansClusteringProofError("fixture must be a ClusteringFixture")
        try:
            normalized_seed = int(seed)
        except (TypeError, ValueError):
            raise KmeansClusteringProofError("seed must be an int") from None
        normalized_k = _normalize_positive_int(k, field_name="k")
        dimension_count = len(fixture.vectors[0])
        input_count = len(fixture.vectors)
        if input_count > budget.max_input_count:
            raise KmeansClusteringProofError("input_count must not exceed max_input_count")
        if dimension_count > budget.max_dimension_count:
            raise KmeansClusteringProofError("dimension_count must not exceed max_dimension_count")
        if normalized_k > budget.max_k or normalized_k > input_count:
            raise KmeansClusteringProofError("k must not exceed max_k or input_count")
        algorithm_version_text = _normalize_slug(algorithm_version, field_name="algorithm_version")
        normalized_risk = _normalize_text(risk_evidence_ref, field_name="risk_evidence_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_forbidden_terms(algorithm_version_text, normalized_risk)
        return cls(
            clustering_proof_id=_normalize_slug(clustering_proof_id, field_name="clustering_proof_id"),
            algorithm_ref=_normalize_algorithm(algorithm_ref),
            algorithm_version=algorithm_version_text,
            seed=normalized_seed,
            k=normalized_k,
            budget=budget,
            fixture=fixture,
            quality_gate_ref=_normalize_slug(quality_gate_ref, field_name="quality_gate_ref") if str(quality_gate_ref or "").strip() else "",
            rebuild_ref=_normalize_slug(rebuild_ref, field_name="rebuild_ref") if str(rebuild_ref or "").strip() else "",
            risk_evidence_ref=normalized_risk,
        )


@dataclass(frozen=True, slots=True)
class ClusteringProofResult:
    run: ClusteringProofRun
    status: ClusteringProofStatus
    input_count: int
    dimension_count: int
    cluster_count: int
    assignments: tuple[ClusterAssignment, ...]
    inertia: float
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        run: ClusteringProofRun,
        status: ClusteringProofStatus | str,
        input_count: Any,
        dimension_count: Any,
        cluster_count: Any,
        assignments: Iterable[ClusterAssignment],
        inertia: Any,
        reason: Any = "",
        next_action: Any = "",
    ) -> "ClusteringProofResult":
        if not isinstance(run, ClusteringProofRun):
            raise KmeansClusteringProofError("run must be a ClusteringProofRun")
        normalized_status = _normalize_status(status)
        input_count_value = _normalize_positive_int(input_count, field_name="input_count")
        dimension_count_value = _normalize_positive_int(dimension_count, field_name="dimension_count")
        cluster_count_value = _normalize_positive_int(cluster_count, field_name="cluster_count")
        normalized_assignments = tuple(assignments)
        if any(not isinstance(item, ClusterAssignment) for item in normalized_assignments):
            raise KmeansClusteringProofError("assignments must contain ClusterAssignment items")
        try:
            inertia_value = float(inertia)
        except (TypeError, ValueError):
            raise KmeansClusteringProofError("inertia must be numeric") from None
        if math.isnan(inertia_value) or math.isinf(inertia_value) or inertia_value < 0:
            raise KmeansClusteringProofError("inertia must be finite and >= 0")
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_forbidden_terms(normalized_reason, normalized_next_action)
        if normalized_status == ClusteringProofStatus.COMPLETED:
            if not (normalized_assignments and run.quality_gate_ref and run.rebuild_ref and run.risk_evidence_ref):
                raise KmeansClusteringProofError("completed runs require assignments, quality_gate_ref, rebuild_ref, and risk_evidence_ref")
            if len(normalized_assignments) != input_count_value:
                raise KmeansClusteringProofError("completed results require assignments for all inputs")
        if normalized_status in {ClusteringProofStatus.BLOCKED, ClusteringProofStatus.FAILED, ClusteringProofStatus.NEEDS_REVIEW} and not (
            normalized_reason or normalized_next_action
        ):
            raise KmeansClusteringProofError("blocked, failed, and needs_review results require reason or next_action")
        return cls(
            run=run,
            status=normalized_status,
            input_count=input_count_value,
            dimension_count=dimension_count_value,
            cluster_count=cluster_count_value,
            assignments=tuple(sorted(normalized_assignments, key=lambda item: item.input_index)),
            inertia=inertia_value,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "clustering_proof_id": self.run.clustering_proof_id,
            "algorithm_ref": self.run.algorithm_ref.value,
            "algorithm_version": self.run.algorithm_version,
            "status": self.status.value,
            "embedding_snapshot_ref": self.run.fixture.embedding_snapshot_ref,
            "input_count": self.input_count,
            "dimension_count": self.dimension_count,
            "cluster_count": self.cluster_count,
            "inertia": self.inertia,
            "has_quality_gate_ref": bool(self.run.quality_gate_ref),
            "has_rebuild_ref": bool(self.run.rebuild_ref),
            "has_risk_evidence_ref": bool(self.run.risk_evidence_ref),
        }


def run_kmeans_proof(run: ClusteringProofRun) -> ClusteringProofResult:
    if not isinstance(run, ClusteringProofRun):
        raise KmeansClusteringProofError("run must be a ClusteringProofRun")
    vectors = list(run.fixture.vectors)
    rng = random.Random(run.seed)
    initial_indexes = sorted(rng.sample(range(len(vectors)), run.k))
    centroids = [vectors[index] for index in initial_indexes]
    assignments: list[int] = [0] * len(vectors)

    for _ in range(run.budget.max_iterations):
        changed = False
        for idx, vector in enumerate(vectors):
            distances = [_euclidean_squared(vector, centroid) for centroid in centroids]
            best_cluster = min(range(len(distances)), key=lambda cluster_idx: (distances[cluster_idx], cluster_idx))
            if assignments[idx] != best_cluster:
                assignments[idx] = best_cluster
                changed = True
        new_centroids: list[tuple[float, ...]] = []
        for cluster_idx in range(run.k):
            members = [vectors[idx] for idx, assigned in enumerate(assignments) if assigned == cluster_idx]
            if not members:
                new_centroids.append(centroids[cluster_idx])
                continue
            dimension = len(members[0])
            centroid = tuple(
                sum(member[dim] for member in members) / len(members)
                for dim in range(dimension)
            )
            new_centroids.append(centroid)
        if not changed and new_centroids == centroids:
            break
        centroids = new_centroids

    cluster_assignments = tuple(
        ClusterAssignment.create(
            assignment_ref=f"assignment-{idx}",
            input_index=idx,
            cluster_index=cluster_idx,
        )
        for idx, cluster_idx in enumerate(assignments)
    )
    inertia = sum(
        _euclidean_squared(vectors[idx], centroids[cluster_idx])
        for idx, cluster_idx in enumerate(assignments)
    )
    return ClusteringProofResult.create(
        run=run,
        status=ClusteringProofStatus.COMPLETED,
        input_count=len(vectors),
        dimension_count=len(vectors[0]),
        cluster_count=run.k,
        assignments=cluster_assignments,
        inertia=inertia,
    )
