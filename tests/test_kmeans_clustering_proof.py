from src.kmeans_clustering_proof import (
    ClusterAssignment,
    ClusteringFixture,
    ClusteringProofBudget,
    ClusteringProofResult,
    ClusteringProofRun,
    KmeansClusteringProofError,
    run_kmeans_proof,
)


def _make_budget(**overrides) -> ClusteringProofBudget:
    payload = {
        "max_input_count": 10,
        "max_dimension_count": 4,
        "max_k": 3,
        "max_iterations": 10,
        "max_memory_mb": 512,
    }
    payload.update(overrides)
    return ClusteringProofBudget.create(**payload)


def _make_fixture(**overrides) -> ClusteringFixture:
    payload = {
        "fixture_ref": "fixture-a",
        "embedding_snapshot_ref": "snapshot-1",
        "vectors": ((0.0, 0.0), (0.1, 0.0), (9.0, 9.0), (9.1, 9.0)),
        "source_refs": ("source-1", "source-2", "source-3", "source-4"),
        "chunk_refs": ("chunk-1", "chunk-2", "chunk-3", "chunk-4"),
    }
    payload.update(overrides)
    return ClusteringFixture.create(**payload)


def _make_run(**overrides) -> ClusteringProofRun:
    payload = {
        "clustering_proof_id": "proof-1",
        "algorithm_ref": "kmeans",
        "algorithm_version": "v1",
        "seed": 7,
        "k": 2,
        "budget": _make_budget(),
        "fixture": _make_fixture(),
        "quality_gate_ref": "quality-1",
        "rebuild_ref": "rebuild-1",
        "risk_evidence_ref": "risk-1",
    }
    payload.update(overrides)
    return ClusteringProofRun.create(**payload)


def _make_result(**overrides) -> ClusteringProofResult:
    payload = {
        "run": _make_run(),
        "status": "completed",
        "input_count": 4,
        "dimension_count": 2,
        "cluster_count": 2,
        "assignments": (
            ClusterAssignment.create(assignment_ref="a-0", input_index=0, cluster_index=0),
            ClusterAssignment.create(assignment_ref="a-1", input_index=1, cluster_index=0),
            ClusterAssignment.create(assignment_ref="a-2", input_index=2, cluster_index=1),
            ClusterAssignment.create(assignment_ref="a-3", input_index=3, cluster_index=1),
        ),
        "inertia": 0.02,
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return ClusteringProofResult.create(**payload)


def test_valid_kmeans_proof_normalizes_stably() -> None:
    result = _make_result(status="ready_for_review", assignments=(), reason="review output")

    assert result.run.clustering_proof_id == "proof-1"
    assert result.status.value == "ready_for_review"
    assert result.dimension_count == 2


def test_missing_seed_or_snapshot_is_rejected() -> None:
    try:
        _make_run(seed="")
    except KmeansClusteringProofError as exc:
        assert "seed must be an int" in str(exc)
    else:
        raise AssertionError("expected seed validation to fail")

    try:
        _make_fixture(embedding_snapshot_ref=" ")
    except KmeansClusteringProofError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected snapshot validation to fail")


def test_unbounded_or_too_large_budgets_are_rejected() -> None:
    try:
        _make_budget(max_memory_mb=4096)
    except KmeansClusteringProofError as exc:
        assert "must not exceed 2048" in str(exc)
    else:
        raise AssertionError("expected max memory validation to fail")


def test_k_greater_than_input_count_or_max_k_is_rejected() -> None:
    try:
        _make_run(k=5)
    except KmeansClusteringProofError as exc:
        assert "must not exceed max_k or input_count" in str(exc)
    else:
        raise AssertionError("expected k bound validation to fail")


def test_inconsistent_vector_dimensions_are_rejected() -> None:
    try:
        _make_fixture(vectors=((0.0, 0.0), (1.0,)))
    except KmeansClusteringProofError as exc:
        assert "same dimension" in str(exc)
    else:
        raise AssertionError("expected vector dimension validation to fail")


def test_missing_source_or_chunk_refs_are_rejected() -> None:
    try:
        _make_fixture(source_refs=("source-1",))
    except KmeansClusteringProofError as exc:
        assert "must match vector count" in str(exc)
    else:
        raise AssertionError("expected source ref validation to fail")

    try:
        _make_fixture(chunk_refs=("chunk-1",))
    except KmeansClusteringProofError as exc:
        assert "must match vector count" in str(exc)
    else:
        raise AssertionError("expected chunk ref validation to fail")


def test_completed_without_assignments_quality_rebuild_or_risk_is_rejected() -> None:
    try:
        _make_result(run=_make_run(quality_gate_ref="", rebuild_ref="", risk_evidence_ref=""))
    except KmeansClusteringProofError as exc:
        assert "completed runs require assignments" in str(exc)
    else:
        raise AssertionError("expected completed evidence validation to fail")


def test_umap_gmm_or_research_claim_is_rejected() -> None:
    try:
        _make_run(risk_evidence_ref="research path via umap")
    except KmeansClusteringProofError as exc:
        assert "out of scope" in str(exc)
    else:
        raise AssertionError("expected forbidden term validation to fail")


def test_deterministic_tiny_kmeans_returns_same_assignments_for_same_seed() -> None:
    first = run_kmeans_proof(_make_run(seed=11))
    second = run_kmeans_proof(_make_run(seed=11))

    assert tuple((item.input_index, item.cluster_index) for item in first.assignments) == tuple(
        (item.input_index, item.cluster_index) for item in second.assignments
    )
    assert first.inertia == second.inertia


def test_audit_summary_contains_counts_algorithm_status_inertia_and_signals_without_vector_dumps() -> None:
    summary = _make_result().audit_summary()

    assert summary["clustering_proof_id"] == "proof-1"
    assert summary["algorithm_ref"] == "kmeans"
    assert summary["status"] == "completed"
    assert summary["input_count"] == 4
    assert summary["cluster_count"] == 2
    assert summary["has_quality_gate_ref"] is True
    assert summary["has_rebuild_ref"] is True
    assert summary["has_risk_evidence_ref"] is True
    assert "(0.0, 0.0)" not in str(summary)
