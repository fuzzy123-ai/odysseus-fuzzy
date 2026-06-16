from src.fallback_routing import (
    FallbackRoutingDecision,
    FallbackRoutingError,
    FallbackRoutingResult,
    RetryBackoffPolicy,
    RoutingBudget,
    RoutingDecisionStatus,
    audit_summary,
)


def _make_budget(**overrides) -> RoutingBudget:
    payload = {
        "latency_budget_ms": 1200,
        "token_budget": 500,
        "cost_budget_units": 100,
        "max_retries": 2,
    }
    payload.update(overrides)
    return RoutingBudget.create(**payload)


def _make_retry_policy(**overrides) -> RetryBackoffPolicy:
    payload = {
        "retry_policy_ref": "retry-linear",
        "backoff_policy_ref": "backoff-30s",
        "retry_count": 0,
        "backoff_seconds": 30,
    }
    payload.update(overrides)
    return RetryBackoffPolicy.create(**payload)


def _make_decision(**overrides) -> FallbackRoutingDecision:
    payload = {
        "routing_decision_id": "route-1",
        "maintenance_model_ref": "mini-model-a",
        "fallback_model_ref": "mini-model-b",
        "task_type": "evidence_summary",
        "go_no_go_status": "go",
        "budget": _make_budget(),
        "retry_policy": _make_retry_policy(),
        "gate_reason_ref": "gate-pass-1",
        "risk_evidence_ref": "risk-evidence-1",
    }
    payload.update(overrides)
    return FallbackRoutingDecision.create(**payload)


def _make_result(**overrides) -> FallbackRoutingResult:
    payload = {
        "decision": _make_decision(),
        "status": "use_maintenance_model",
        "estimated_cost_units": 40,
        "estimated_latency_ms": 700,
        "estimated_tokens": 250,
        "review_item_ref": "",
        "fallback_model_ref": "",
        "failure_reason": "",
        "next_action": "",
        "audit_refs": ("audit-1", "audit-2"),
    }
    payload.update(overrides)
    return FallbackRoutingResult.create(**payload)


def test_valid_maintenance_decision_normalizes_stably() -> None:
    result = _make_result()

    assert result.status is RoutingDecisionStatus.USE_MAINTENANCE_MODEL
    assert result.decision.routing_decision_id == "route-1"
    assert result.decision.task_type.value == "evidence_summary"


def test_retry_without_policy_or_over_max_retries_is_rejected() -> None:
    try:
        _make_result(
            status="retry_maintenance",
            decision=_make_decision(retry_policy=_make_retry_policy(retry_count=3), budget=_make_budget(max_retries=2)),
            next_action="retry after backoff",
        )
    except FallbackRoutingError as exc:
        assert "retry_count must not exceed max_retries" in str(exc)
    else:
        raise AssertionError("expected retry limit validation to fail")

    try:
        _make_result(status="retry_maintenance", next_action="")
    except FallbackRoutingError as exc:
        assert "retry_maintenance requires next_action" in str(exc)
    else:
        raise AssertionError("expected retry next_action validation to fail")


def test_fallback_without_gate_risk_or_fallback_ref_is_rejected() -> None:
    try:
        _make_result(status="use_fallback_model", fallback_model_ref="")
    except FallbackRoutingError as exc:
        assert "use_fallback_model requires fallback_model_ref" in str(exc)
    else:
        raise AssertionError("expected fallback ref validation to fail")

    try:
        _make_result(
            status="use_fallback_model",
            decision=_make_decision(risk_evidence_ref=" "),
            fallback_model_ref="mini-model-b",
        )
    except FallbackRoutingError as exc:
        assert "risk_evidence_ref must not be empty" in str(exc)
    else:
        raise AssertionError("expected risk evidence validation to fail")


def test_fallback_default_for_all_tasks_is_rejected() -> None:
    try:
        _make_decision(gate_reason_ref="default fallback for all tasks")
    except FallbackRoutingError as exc:
        assert "out of scope" in str(exc)
    else:
        raise AssertionError("expected forbidden fallback default validation to fail")


def test_budget_overruns_prevent_automatic_execution() -> None:
    try:
        _make_result(estimated_tokens=800)
    except FallbackRoutingError as exc:
        assert "budget exceedance prevents automatic execution" in str(exc)
    else:
        raise AssertionError("expected maintenance budget validation to fail")

    try:
        _make_result(
            status="retry_maintenance",
            estimated_cost_units=200,
            next_action="retry once budget is replenished",
        )
    except FallbackRoutingError as exc:
        assert "retry_maintenance requires budget reserve" in str(exc)
    else:
        raise AssertionError("expected retry budget validation to fail")


def test_needs_review_without_review_or_next_action_is_rejected() -> None:
    try:
        _make_result(status="needs_review")
    except FallbackRoutingError as exc:
        assert "needs_review requires review_item_ref or next_action" in str(exc)
    else:
        raise AssertionError("expected review validation to fail")


def test_blocked_and_failed_without_failure_reason_or_next_action_are_rejected() -> None:
    for status in ("blocked", "failed"):
        try:
            _make_result(status=status)
        except FallbackRoutingError as exc:
            assert "blocked and failed require failure_reason or next_action" in str(exc)
        else:
            raise AssertionError(f"expected {status} validation to fail")


def test_truth_global_or_research_claims_are_rejected() -> None:
    try:
        _make_result(failure_reason="authoritative global accelerator route", status="failed")
    except FallbackRoutingError as exc:
        assert "out of scope" in str(exc)
    else:
        raise AssertionError("expected forbidden claim validation to fail")


def test_audit_summary_stays_compact() -> None:
    summary = audit_summary(
        _make_result(
            status="needs_review",
            review_item_ref="review-1",
            next_action="handoff to reviewer",
        )
    )

    assert summary["routing_decision_id"] == "route-1"
    assert summary["status"] == "needs_review"
    assert summary["audit_ref_count"] == 2
    assert summary["has_review_item_ref"] is True
    assert "mini-model-a" not in str(summary)
    assert "handoff to reviewer" not in str(summary)
