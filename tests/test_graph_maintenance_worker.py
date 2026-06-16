from src.graph_maintenance_worker import (
    EdgeCandidate,
    EntityCandidate,
    GraphMaintenanceBudget,
    GraphMaintenanceResult,
    GraphMaintenanceTask,
    GraphMaintenanceWorkerError,
    GraphSourceRef,
    audit_summary,
)


def _make_budget(**overrides) -> GraphMaintenanceBudget:
    payload = {
        "max_candidates": 4,
        "max_source_refs": 3,
        "max_edges": 2,
        "max_entities": 2,
        "max_memory_mb": 512,
    }
    payload.update(overrides)
    return GraphMaintenanceBudget.create(**payload)


def _make_source_ref(idx: int = 1, **overrides) -> GraphSourceRef:
    payload = {
        "source_ref": f"source-{idx}",
        "chunk_ref": f"chunk-{idx}",
        "evidence_ref": f"evidence-{idx}",
        "provenance_ref": f"prov-{idx}",
    }
    payload.update(overrides)
    return GraphSourceRef.create(**payload)


def _make_entity_candidate(**overrides) -> EntityCandidate:
    payload = {
        "entity_candidate_ref": "entity-cand-1",
        "label": "Alice",
        "entity_type": "person",
        "dedupe_key": "alice-person",
        "confidence": 0.9,
        "source_refs": (_make_source_ref(1),),
    }
    payload.update(overrides)
    return EntityCandidate.create(**payload)


def _make_edge_candidate(**overrides) -> EdgeCandidate:
    payload = {
        "edge_candidate_ref": "edge-cand-1",
        "source_entity_ref": "entity-a",
        "target_entity_ref": "entity-b",
        "relation_type": "knows",
        "dedupe_key": "entity-a-knows-entity-b",
        "confidence": 0.85,
        "source_refs": (_make_source_ref(2),),
    }
    payload.update(overrides)
    return EdgeCandidate.create(**payload)


def _make_task(**overrides) -> GraphMaintenanceTask:
    payload = {
        "graph_maintenance_task_id": "graph-task-1",
        "task_type": "entity_candidate",
        "budget": _make_budget(),
        "source_refs": (_make_source_ref(1), _make_source_ref(2)),
        "entity_candidates": (_make_entity_candidate(),),
        "edge_candidates": (_make_edge_candidate(),),
        "merge_policy_ref": "merge-policy-1",
        "truth_write_allowed": False,
        "fallback_model_ref": "mini-graph-fallback",
    }
    payload.update(overrides)
    return GraphMaintenanceTask.create(**payload)


def _make_result(**overrides) -> GraphMaintenanceResult:
    payload = {
        "task": _make_task(),
        "status": "completed",
        "confidence": 0.9,
        "uncertainty_reason": "",
        "needs_review": False,
        "review_item_ref": "",
        "fallback_model_ref": "",
        "drift_check_ref": "drift-check-1",
        "risk_evidence_ref": "risk-proof-1",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return GraphMaintenanceResult.create(**payload)


def test_valid_task_and_result_normalize_stably() -> None:
    result = _make_result(status="ready_for_review", risk_evidence_ref="")

    assert result.task.graph_maintenance_task_id == "graph-task-1"
    assert result.status.value == "ready_for_review"
    assert result.task.budget.max_candidates == 4


def test_missing_source_evidence_or_provenance_refs_are_rejected() -> None:
    try:
        _make_source_ref(evidence_ref=" ")
    except GraphMaintenanceWorkerError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected evidence ref validation to fail")

    try:
        _make_source_ref(provenance_ref=" ")
    except GraphMaintenanceWorkerError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected provenance ref validation to fail")


def test_unbounded_or_too_large_budgets_are_rejected() -> None:
    try:
        _make_budget(max_candidates=0)
    except GraphMaintenanceWorkerError as exc:
        assert "must be > 0" in str(exc)
    else:
        raise AssertionError("expected unbounded budget validation to fail")

    try:
        _make_budget(max_memory_mb=4096)
    except GraphMaintenanceWorkerError as exc:
        assert "must not exceed 2048" in str(exc)
    else:
        raise AssertionError("expected memory budget validation to fail")


def test_truth_write_claim_is_rejected() -> None:
    try:
        _make_task(truth_write_allowed=True)
    except GraphMaintenanceWorkerError as exc:
        assert "truth_write_allowed=True is forbidden" in str(exc)
    else:
        raise AssertionError("expected truth write validation to fail")


def test_edge_without_entity_refs_relation_or_dedupe_is_rejected() -> None:
    try:
        _make_edge_candidate(source_entity_ref=" ")
    except GraphMaintenanceWorkerError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected source entity validation to fail")

    try:
        _make_edge_candidate(relation_type=" ")
    except GraphMaintenanceWorkerError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected relation type validation to fail")


def test_self_loop_is_rejected() -> None:
    try:
        _make_edge_candidate(source_entity_ref="entity-a", target_entity_ref="entity-a")
    except GraphMaintenanceWorkerError as exc:
        assert "self-loop edge candidates" in str(exc)
    else:
        raise AssertionError("expected self-loop validation to fail")


def test_low_confidence_or_uncertainty_without_review_or_fallback_is_rejected() -> None:
    try:
        _make_result(confidence=0.2, status="completed")
    except GraphMaintenanceWorkerError as exc:
        assert "low confidence or uncertainty requires" in str(exc)
    else:
        raise AssertionError("expected low confidence validation to fail")

    try:
        _make_result(uncertainty_reason="weak provenance", status="completed")
    except GraphMaintenanceWorkerError as exc:
        assert "low confidence or uncertainty requires" in str(exc)
    else:
        raise AssertionError("expected uncertainty validation to fail")


def test_too_many_candidates_edges_entities_or_sources_are_rejected() -> None:
    try:
        _make_task(source_refs=tuple(_make_source_ref(idx) for idx in range(1, 5)))
    except GraphMaintenanceWorkerError as exc:
        assert "must not exceed max_source_refs" in str(exc)
    else:
        raise AssertionError("expected source budget validation to fail")

    try:
        _make_task(entity_candidates=(_make_entity_candidate(), _make_entity_candidate(entity_candidate_ref="entity-cand-2"), _make_entity_candidate(entity_candidate_ref="entity-cand-3")))
    except GraphMaintenanceWorkerError as exc:
        assert "must not exceed max_entities" in str(exc)
    else:
        raise AssertionError("expected entity budget validation to fail")


def test_truth_global_or_research_claims_are_rejected() -> None:
    try:
        _make_result(risk_evidence_ref="authoritative global graph research")
    except GraphMaintenanceWorkerError as exc:
        assert "out of scope" in str(exc)
    else:
        raise AssertionError("expected forbidden claim validation to fail")


def test_audit_summary_stays_compact() -> None:
    summary = audit_summary(
        _make_result(
            status="needs_review",
            confidence=0.4,
            uncertainty_reason="weak evidence",
            needs_review=True,
            review_item_ref="review-item-1",
            risk_evidence_ref="",
        )
    )

    assert summary["graph_maintenance_task_id"] == "graph-task-1"
    assert summary["status"] == "needs_review"
    assert summary["source_ref_count"] == 2
    assert summary["has_review_item_ref"] is True
    assert "source-1" not in str(summary)
    assert "Alice" not in str(summary)
