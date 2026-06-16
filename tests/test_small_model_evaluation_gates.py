from src.small_model_evaluation_gates import (
    EvaluationBudget,
    EvaluationGateStatus,
    EvaluationThresholds,
    SmallModelEvaluationError,
    SmallModelEvaluationGate,
    SmallModelEvaluationResult,
    audit_summary,
)


def _make_budget(**overrides) -> EvaluationBudget:
    payload = {
        "latency_budget_ms": 1200,
        "memory_budget_mb": 512,
        "token_budget": 400,
        "chunk_budget": 6,
    }
    payload.update(overrides)
    return EvaluationBudget.create(**payload)


def _make_thresholds(**overrides) -> EvaluationThresholds:
    payload = {
        "min_source_coverage": 0.7,
        "min_evidence_coverage": 0.7,
        "min_citation_accuracy": 0.8,
        "min_confidence": 0.75,
        "max_drift_score": 0.25,
        "max_hallucination_risk": 0.15,
    }
    payload.update(overrides)
    return EvaluationThresholds.create(**payload)


def _make_gate(**overrides) -> SmallModelEvaluationGate:
    payload = {
        "evaluation_gate_id": "eval-gate-1",
        "model_profile_ref": "tiny-model-a",
        "task_type": "evidence_summary",
        "fixture_ref": "fixture-a",
        "expected_schema_ref": "schema-v1",
        "budget": _make_budget(),
        "thresholds": _make_thresholds(),
        "fallback_model_ref": "tiny-model-b",
    }
    payload.update(overrides)
    return SmallModelEvaluationGate.create(**payload)


def _make_result(**overrides) -> SmallModelEvaluationResult:
    payload = {
        "gate": _make_gate(),
        "json_valid": True,
        "source_coverage": 0.9,
        "evidence_coverage": 0.9,
        "citation_accuracy": 0.95,
        "confidence": 0.9,
        "uncertainty_reason": "",
        "drift_score": 0.05,
        "hallucination_risk": 0.05,
        "go_no_go_status": "go",
        "review_item_ref": "",
        "fallback_model_ref": "",
        "risk_evidence_ref": "risk-evidence-1",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return SmallModelEvaluationResult.create(**payload)


def test_valid_go_normalizes_stably() -> None:
    result = _make_result()

    assert result.go_no_go_status is EvaluationGateStatus.GO
    assert result.gate.evaluation_gate_id == "eval-gate-1"
    assert result.gate.task_type.value == "evidence_summary"


def test_missing_schema_or_json_invalid_prevents_go() -> None:
    try:
        _make_gate(expected_schema_ref=" ")
    except SmallModelEvaluationError as exc:
        assert "expected_schema_ref must not be empty" in str(exc)
    else:
        raise AssertionError("expected schema validation to fail")

    try:
        _make_result(json_valid=False)
    except SmallModelEvaluationError as exc:
        assert "go requires json_valid=True" in str(exc)
    else:
        raise AssertionError("expected json validity validation to fail")


def test_unbounded_or_too_large_budgets_are_rejected() -> None:
    try:
        _make_budget(token_budget=0)
    except SmallModelEvaluationError as exc:
        assert "must be > 0" in str(exc)
    else:
        raise AssertionError("expected zero budget validation to fail")

    try:
        _make_budget(memory_budget_mb=4096)
    except SmallModelEvaluationError as exc:
        assert "must not exceed 2048" in str(exc)
    else:
        raise AssertionError("expected memory budget validation to fail")


def test_poor_evidence_citation_or_confidence_prevents_go() -> None:
    try:
        _make_result(evidence_coverage=0.4)
    except SmallModelEvaluationError as exc:
        assert "go requires evidence_coverage to meet thresholds" in str(exc)
    else:
        raise AssertionError("expected evidence coverage validation to fail")

    try:
        _make_result(citation_accuracy=0.4)
    except SmallModelEvaluationError as exc:
        assert "go requires citation_accuracy to meet thresholds" in str(exc)
    else:
        raise AssertionError("expected citation accuracy validation to fail")

    try:
        _make_result(confidence=0.4)
    except SmallModelEvaluationError as exc:
        assert "go requires confidence to meet thresholds" in str(exc)
    else:
        raise AssertionError("expected confidence validation to fail")


def test_high_drift_or_hallucination_risk_prevents_go() -> None:
    try:
        _make_result(drift_score=0.8)
    except SmallModelEvaluationError as exc:
        assert "go requires drift_score to stay within thresholds" in str(exc)
    else:
        raise AssertionError("expected drift score validation to fail")

    try:
        _make_result(hallucination_risk=0.8)
    except SmallModelEvaluationError as exc:
        assert "go requires hallucination_risk to stay within thresholds" in str(exc)
    else:
        raise AssertionError("expected hallucination risk validation to fail")


def test_review_or_fallback_without_refs_is_rejected() -> None:
    try:
        _make_result(go_no_go_status="needs_review")
    except SmallModelEvaluationError as exc:
        assert "needs_review and fallback_required require review_item_ref or fallback_model_ref" in str(exc)
    else:
        raise AssertionError("expected review gate validation to fail")

    try:
        _make_result(go_no_go_status="fallback_required")
    except SmallModelEvaluationError as exc:
        assert "needs_review and fallback_required require review_item_ref or fallback_model_ref" in str(exc)
    else:
        raise AssertionError("expected fallback gate validation to fail")


def test_no_go_blocked_and_failed_require_reason_or_next_action() -> None:
    for status in ("no_go", "blocked", "failed"):
        try:
            _make_result(go_no_go_status=status, risk_evidence_ref="", reason="", next_action="")
        except SmallModelEvaluationError as exc:
            assert "require reason or next_action" in str(exc)
        else:
            raise AssertionError(f"expected {status} validation to fail")


def test_truth_global_or_research_claims_are_rejected() -> None:
    try:
        _make_result(reason="authoritative global answer with accelerator support", go_no_go_status="no_go")
    except SmallModelEvaluationError as exc:
        assert "out of scope" in str(exc)
    else:
        raise AssertionError("expected forbidden claim validation to fail")


def test_audit_summary_stays_compact() -> None:
    summary = audit_summary(
        _make_result(
            go_no_go_status="needs_review",
            review_item_ref="review-item-1",
            risk_evidence_ref="",
            reason="needs human review",
        )
    )

    assert summary["evaluation_gate_id"] == "eval-gate-1"
    assert summary["status"] == "needs_review"
    assert summary["json_valid"] is True
    assert summary["has_review_item_ref"] is True
    assert "tiny-model-a" not in str(summary)
    assert "needs human review" not in str(summary)
