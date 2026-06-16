from src.derived_cluster_runs import (
    ClusterAlgorithmRef,
    ClusterMembership,
    ClusterNode,
    ClusterRunBudget,
    DerivedClusterRun,
    DerivedClusterRunError,
)


def _make_algorithm(**overrides) -> ClusterAlgorithmRef:
    payload = {
        "algorithm_ref": "hier-cluster",
        "algorithm_version": "v1",
        "embedding_snapshot_ref": "embed-snap-1",
    }
    payload.update(overrides)
    return ClusterAlgorithmRef.create(**payload)


def _make_budget(**overrides) -> ClusterRunBudget:
    payload = {
        "input_count": 3,
        "max_input_count": 10,
        "cluster_count": 2,
        "min_cluster_size": 1,
        "max_cluster_size": 5,
        "max_depth": 2,
    }
    payload.update(overrides)
    return ClusterRunBudget.create(**payload)


def _make_node(**overrides) -> ClusterNode:
    payload = {
        "cluster_node_ref": "cluster-node-1",
        "cluster_run_id": "cluster-run-1",
        "depth": 1,
        "parent_cluster_ref": "",
        "child_cluster_refs": (),
        "derived_output_ref": "derived-out-1",
        "label_review_item_ref": "",
    }
    payload.update(overrides)
    return ClusterNode.create(**payload)


def _make_membership(**overrides) -> ClusterMembership:
    payload = {
        "cluster_membership_ref": "membership-1",
        "cluster_node_ref": "cluster-node-1",
        "source_ref": "source-1",
        "chunk_ref": "chunk-1",
        "embedding_ref": "embedding-1",
        "score": 0.8,
    }
    payload.update(overrides)
    return ClusterMembership.create(**payload)


def _make_run(**overrides) -> DerivedClusterRun:
    payload = {
        "cluster_run_id": "cluster-run-1",
        "source_scope_ref": "scope-a",
        "algorithm": _make_algorithm(),
        "budget": _make_budget(),
        "nodes": (_make_node(),),
        "memberships": (_make_membership(),),
        "rebuild_ref": "rebuild-1",
        "quality_gate_ref": "quality-gate-1",
        "risk_evidence_ref": "risk-proof-1",
        "status": "completed",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return DerivedClusterRun.create(**payload)


def test_valid_derived_cluster_run_normalizes_stably() -> None:
    run = _make_run(status="ready_for_review", rebuild_ref="", quality_gate_ref="", risk_evidence_ref="")

    assert run.cluster_run_id == "cluster-run-1"
    assert run.algorithm.algorithm_version == "v1"
    assert run.status.value == "ready_for_review"


def test_missing_algorithm_version_or_embedding_snapshot_is_rejected() -> None:
    try:
        _make_algorithm(algorithm_version=" ")
    except DerivedClusterRunError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected algorithm version validation to fail")

    try:
        _make_algorithm(embedding_snapshot_ref=" ")
    except DerivedClusterRunError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected embedding snapshot validation to fail")


def test_unbounded_or_input_count_exceeds_max_is_rejected() -> None:
    try:
        _make_budget(input_count=11, max_input_count=10)
    except DerivedClusterRunError as exc:
        assert "must not exceed max_input_count" in str(exc)
    else:
        raise AssertionError("expected input bound validation to fail")


def test_node_depth_exceeds_max_depth_is_rejected() -> None:
    try:
        _make_run(nodes=(_make_node(depth=3),), budget=_make_budget(max_depth=2))
    except DerivedClusterRunError as exc:
        assert "must not exceed max_depth" in str(exc)
    else:
        raise AssertionError("expected max depth validation to fail")


def test_membership_without_source_chunk_or_embedding_is_rejected() -> None:
    try:
        _make_membership(source_ref=" ")
    except DerivedClusterRunError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected source ref validation to fail")

    try:
        _make_membership(chunk_ref=" ")
    except DerivedClusterRunError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected chunk ref validation to fail")

    try:
        _make_membership(embedding_ref=" ")
    except DerivedClusterRunError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected embedding ref validation to fail")


def test_score_outside_range_is_rejected() -> None:
    try:
        _make_membership(score=1.5)
    except DerivedClusterRunError as exc:
        assert "between 0 and 1" in str(exc)
    else:
        raise AssertionError("expected score validation to fail")


def test_truth_store_claim_is_rejected() -> None:
    try:
        _make_run(risk_evidence_ref="truth-store promotion planned")
    except DerivedClusterRunError as exc:
        assert "forbidden accelerator, research, or truth-store claim" in str(exc)
    else:
        raise AssertionError("expected truth-store claim validation to fail")


def test_completed_without_rebuild_quality_or_risk_is_rejected() -> None:
    try:
        _make_run(rebuild_ref="", quality_gate_ref="", risk_evidence_ref="", status="completed")
    except DerivedClusterRunError as exc:
        assert "completed runs require rebuild_ref" in str(exc)
    else:
        raise AssertionError("expected completed evidence validation to fail")


def test_audit_summary_contains_counts_status_algorithm_snapshot_and_signals_without_dumps() -> None:
    run = _make_run()
    summary = run.audit_summary()

    assert summary["cluster_run_id"] == "cluster-run-1"
    assert summary["status"] == "completed"
    assert summary["algorithm_ref"] == "hier-cluster"
    assert summary["embedding_snapshot_ref"] == "embed-snap-1"
    assert summary["node_count"] == 1
    assert summary["membership_count"] == 1
    assert summary["has_rebuild_ref"] is True
    assert summary["has_quality_gate_ref"] is True
    assert summary["has_risk_evidence_ref"] is True
    assert "membership-1" not in str(summary)
