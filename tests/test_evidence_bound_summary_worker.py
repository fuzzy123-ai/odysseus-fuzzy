from src.evidence_bound_summary_worker import (
    EvidenceBoundSummaryError,
    EvidenceBoundSummaryResult,
    EvidenceBoundSummaryTask,
    SummaryBudget,
    SummarySourceRef,
    SummaryTaskStatus,
    audit_summary,
)


def _make_budget(**overrides) -> SummaryBudget:
    payload = {
        "max_chunks": 4,
        "max_tokens": 120,
        "max_source_refs": 4,
        "max_memory_mb": 512,
    }
    payload.update(overrides)
    return SummaryBudget.create(**payload)


def _make_source_ref(idx: int = 1, **overrides) -> SummarySourceRef:
    payload = {
        "source_ref": f"source-{idx}",
        "chunk_ref": f"chunk-{idx}",
        "evidence_ref": f"evidence-{idx}",
    }
    payload.update(overrides)
    return SummarySourceRef.create(**payload)


def _make_task(**overrides) -> EvidenceBoundSummaryTask:
    payload = {
        "summary_task_id": "summary-task-1",
        "summary_scope_ref": "scope-a",
        "model_profile_ref": "mini-llm",
        "prompt_template_ref": "summary-prompt-v1",
        "citation_policy": "cite all source refs",
        "budget": _make_budget(),
        "source_refs": (_make_source_ref(1), _make_source_ref(2)),
        "fallback_model_ref": "mini-llm-fallback",
    }
    payload.update(overrides)
    return EvidenceBoundSummaryTask.create(**payload)


def _make_result(**overrides) -> EvidenceBoundSummaryResult:
    payload = {
        "task": _make_task(),
        "summary_ref": "summary-ref-1",
        "status": "completed",
        "summary_text": "Bounded summary with citations for the provided evidence only.",
        "confidence": 0.85,
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
    return EvidenceBoundSummaryResult.create(**payload)


def test_valid_task_and_result_normalize_stably() -> None:
    result = _make_result(status="ready_for_review", summary_ref="", risk_evidence_ref="")

    assert result.status is SummaryTaskStatus.READY_FOR_REVIEW
    assert result.task.summary_task_id == "summary-task-1"
    assert result.task.summary_scope.summary_scope_ref == "scope-a"


def test_missing_source_or_evidence_refs_are_rejected() -> None:
    try:
        _make_task(source_refs=())
    except EvidenceBoundSummaryError as exc:
        assert "source_refs must contain SummarySourceRef items" in str(exc)
    else:
        raise AssertionError("expected missing source refs validation to fail")

    try:
        _make_source_ref(evidence_ref=" ")
    except EvidenceBoundSummaryError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected missing evidence ref validation to fail")


def test_unbounded_or_too_large_budgets_are_rejected() -> None:
    try:
        _make_budget(max_chunks=0)
    except EvidenceBoundSummaryError as exc:
        assert "must be > 0" in str(exc)
    else:
        raise AssertionError("expected unbounded budget validation to fail")

    try:
        _make_budget(max_memory_mb=4096)
    except EvidenceBoundSummaryError as exc:
        assert "must not exceed 2048" in str(exc)
    else:
        raise AssertionError("expected memory budget validation to fail")


def test_completed_without_summary_evidence_risk_or_citations_is_rejected() -> None:
    try:
        _make_result(summary_text="", risk_evidence_ref="")
    except EvidenceBoundSummaryError as exc:
        assert "completed requires summary_ref" in str(exc)
    else:
        raise AssertionError("expected completed validation to fail")


def test_low_confidence_or_uncertainty_without_review_or_fallback_is_rejected() -> None:
    try:
        _make_result(confidence=0.2, status="completed")
    except EvidenceBoundSummaryError as exc:
        assert "low confidence or uncertainty requires" in str(exc)
    else:
        raise AssertionError("expected low confidence validation to fail")

    try:
        _make_result(uncertainty_reason="missing citation alignment", status="completed")
    except EvidenceBoundSummaryError as exc:
        assert "low confidence or uncertainty requires" in str(exc)
    else:
        raise AssertionError("expected uncertainty validation to fail")


def test_too_many_sources_or_chunks_are_rejected() -> None:
    try:
        _make_task(source_refs=tuple(_make_source_ref(idx) for idx in range(1, 6)))
    except EvidenceBoundSummaryError as exc:
        assert "must not exceed max_source_refs or max_chunks" in str(exc)
    else:
        raise AssertionError("expected source count validation to fail")


def test_truth_global_or_research_claims_are_rejected() -> None:
    try:
        _make_result(summary_text="This is the authoritative global summary.")
    except EvidenceBoundSummaryError as exc:
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
            summary_ref="",
            risk_evidence_ref="",
        )
    )

    assert summary["summary_task_id"] == "summary-task-1"
    assert summary["status"] == "needs_review"
    assert summary["source_ref_count"] == 2
    assert summary["has_review_item_ref"] is True
    assert "source-1" not in str(summary)
    assert "Bounded summary with citations" not in str(summary)
