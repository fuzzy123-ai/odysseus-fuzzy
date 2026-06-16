from src.lightweight_memory_maintenance import (
    MaintenanceEvidenceRef,
    MaintenanceTask,
    MaintenanceTaskBudget,
    MaintenanceTaskResult,
    MaintenanceTaskStatus,
    MaintenanceWorkerProfile,
    LightweightMaintenanceError,
    audit_summary,
)


def _make_profile(**overrides) -> MaintenanceWorkerProfile:
    payload = {
        "model_profile_ref": "mini-llm-local",
        "max_memory_mb": 1024,
        "supports_json": True,
        "supports_source_citations": True,
        "fallback_model_ref": "mini-llm-fallback",
    }
    payload.update(overrides)
    return MaintenanceWorkerProfile.create(**payload)


def _make_budget(**overrides) -> MaintenanceTaskBudget:
    payload = {
        "memory_budget_mb": 512,
        "token_budget": 800,
        "chunk_budget": 8,
        "source_ref_budget": 4,
        "time_budget_ms": 1500,
    }
    payload.update(overrides)
    return MaintenanceTaskBudget.create(**payload)


def _make_evidence_ref(value: str = "evidence-1") -> MaintenanceEvidenceRef:
    return MaintenanceEvidenceRef.create(evidence_ref=value)


def _make_task(**overrides) -> MaintenanceTask:
    payload = {
        "task_ref": "task-1",
        "task_type": "cluster_labeling",
        "source_refs": ["source-a", "source-b"],
        "evidence_refs": (_make_evidence_ref(),),
        "budget": _make_budget(),
        "worker_profile": _make_profile(),
        "truth_write_allowed": False,
        "derived_output_ref": "derived-output-1",
        "review_item_ref": "",
    }
    payload.update(overrides)
    return MaintenanceTask.create(**payload)


def _make_result(**overrides) -> MaintenanceTaskResult:
    payload = {
        "task": _make_task(),
        "status": "completed",
        "confidence": 0.92,
        "uncertainty_reason": "",
        "needs_review": False,
        "fallback_model_ref": "",
        "risk_evidence_ref": "risk-proof-1",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return MaintenanceTaskResult.create(**payload)


def test_valid_maintenance_task_and_result_normalize_stably() -> None:
    result = _make_result(status="ready_for_review")

    assert result.status is MaintenanceTaskStatus.READY_FOR_REVIEW
    assert result.task.task_ref == "task-1"
    assert result.task.worker_profile.max_memory_mb == 1024


def test_unbounded_or_over_2048_budget_is_rejected() -> None:
    try:
        _make_budget(memory_budget_mb=4096)
    except LightweightMaintenanceError as exc:
        assert "must not exceed 2048 MB" in str(exc)
    else:
        raise AssertionError("expected memory budget validation to fail")


def test_missing_source_or_evidence_refs_are_rejected() -> None:
    try:
        _make_task(source_refs=[])
    except LightweightMaintenanceError as exc:
        assert "source_refs must not be empty" in str(exc)
    else:
        raise AssertionError("expected source refs validation to fail")

    try:
        _make_task(evidence_refs=())
    except LightweightMaintenanceError as exc:
        assert "evidence_refs must contain" in str(exc)
    else:
        raise AssertionError("expected evidence refs validation to fail")


def test_truth_write_claim_is_rejected() -> None:
    try:
        _make_task(truth_write_allowed=True)
    except LightweightMaintenanceError as exc:
        assert "truth_write_allowed=True is forbidden" in str(exc)
    else:
        raise AssertionError("expected truth write validation to fail")


def test_low_confidence_or_uncertainty_without_review_or_fallback_is_rejected() -> None:
    try:
        _make_result(confidence=0.3, status="completed")
    except LightweightMaintenanceError as exc:
        assert "low confidence or uncertainty requires" in str(exc)
    else:
        raise AssertionError("expected low confidence validation to fail")

    try:
        _make_result(uncertainty_reason="ambiguous evidence", status="completed")
    except LightweightMaintenanceError as exc:
        assert "low confidence or uncertainty requires" in str(exc)
    else:
        raise AssertionError("expected uncertainty validation to fail")


def test_completed_without_derived_output_or_review_item_is_rejected() -> None:
    try:
        _make_result(task=_make_task(derived_output_ref="", review_item_ref=""), status="completed")
    except LightweightMaintenanceError as exc:
        assert "require derived_output_ref or review_item_ref" in str(exc)
    else:
        raise AssertionError("expected derived/review validation to fail")


def test_blocked_or_failed_without_reason_or_next_action_are_rejected() -> None:
    for status in ("blocked", "failed"):
        try:
            _make_result(status=status, reason=" ", next_action=" ")
        except LightweightMaintenanceError as exc:
            assert "require reason or next_action" in str(exc)
        else:
            raise AssertionError("expected blocked/failed validation to fail")


def test_accelerator_or_research_claim_is_rejected() -> None:
    try:
        _make_profile(fallback_model_ref="qdrant-research-mode")
    except LightweightMaintenanceError as exc:
        assert "out of scope" in str(exc)
    else:
        raise AssertionError("expected forbidden term validation to fail")


def test_audit_summary_contains_task_status_budgets_evidence_and_review_fallback_without_long_dumps() -> None:
    result = _make_result(
        status="needs_review",
        confidence=0.4,
        uncertainty_reason="weak evidence",
        needs_review=True,
        task=_make_task(review_item_ref="review-item-1"),
    )

    summary = audit_summary(result)

    assert summary["task_ref"] == "task-1"
    assert summary["status"] == "needs_review"
    assert summary["memory_budget_mb"] == 512
    assert summary["evidence_ref_count"] == 1
    assert summary["needs_review"] is True
    assert "source" not in str(summary).lower()
    assert "chunk dump" not in str(summary).lower()
