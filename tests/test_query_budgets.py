from src.query_budgets import (
    QueryBudget,
    QueryBudgetError,
    QueryBudgetKind,
    QueryBudgetResult,
    QueryBudgetStatus,
)


def _make_budget(**overrides) -> QueryBudget:
    payload = {
        "query_budget_id": "memory-page-1",
        "query_ref": "query-42",
        "kind": "memory",
        "limit": 25,
        "cursor": "",
        "time_budget_ms": 200,
        "token_budget": 0,
        "max_nodes": 0,
        "max_edges": 0,
        "depth": 0,
    }
    payload.update(overrides)
    return QueryBudget.create(**payload)


def _make_result(**overrides) -> QueryBudgetResult:
    payload = {
        "query_budget": _make_budget(),
        "status": "within_budget",
        "returned_count": 20,
        "scanned_count": 25,
        "partial": False,
        "clipped": False,
        "exhausted": False,
        "next_cursor": "",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return QueryBudgetResult.create(**payload)


def test_valid_query_budget_result_normalizes_stably() -> None:
    result = _make_result(
        query_budget=_make_budget(query_budget_id=" Memory Page 1 ", kind="vector"),
    )

    assert result.query_budget.query_budget_id == "memory-page-1"
    assert result.query_budget.kind is QueryBudgetKind.VECTOR
    assert result.status is QueryBudgetStatus.WITHIN_BUDGET
    assert result.returned_count == 20


def test_query_budget_without_budget_is_rejected() -> None:
    try:
        _make_budget(limit=0, time_budget_ms=0, token_budget=0, max_nodes=0, max_edges=0, depth=0)
    except QueryBudgetError as exc:
        assert "at least one hard budget" in str(exc)
    else:
        raise AssertionError("expected missing budget validation to fail")


def test_graph_budget_without_graph_limits_is_rejected() -> None:
    try:
        _make_budget(kind="graph", limit=10, time_budget_ms=100, max_nodes=0, max_edges=0, depth=0)
    except QueryBudgetError as exc:
        assert "graph budgets require" in str(exc)
    else:
        raise AssertionError("expected graph budget validation to fail")


def test_clipped_partial_exhausted_without_reason_or_next_action_are_rejected() -> None:
    for overrides in (
        {"partial": True, "reason": " ", "next_action": " "},
        {"clipped": True, "reason": " ", "next_action": " "},
        {"exhausted": True, "reason": " ", "next_action": " "},
    ):
        try:
            _make_result(**overrides)
        except QueryBudgetError as exc:
            assert "require reason or next_action" in str(exc)
        else:
            raise AssertionError("expected partial/clipped/exhausted validation to fail")


def test_partial_with_more_pages_without_next_cursor_is_rejected() -> None:
    try:
        _make_result(partial=True, returned_count=10, scanned_count=20, reason="more data available", next_cursor="")
    except QueryBudgetError as exc:
        assert "require next_cursor" in str(exc)
    else:
        raise AssertionError("expected partial cursor validation to fail")


def test_status_flags_must_match_partial_clipped_and_exhausted() -> None:
    for overrides, expected in (
        ({"status": "partial", "partial": False, "reason": "partial page"}, "partial=True"),
        ({"status": "clipped", "clipped": False, "reason": "payload clipped"}, "clipped=True"),
        ({"status": "exhausted", "exhausted": False, "reason": "time budget exhausted"}, "exhausted=True"),
    ):
        try:
            _make_result(**overrides)
        except QueryBudgetError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("expected status and flag mismatch validation to fail")


def test_next_cursor_requires_continuable_partial_result() -> None:
    for overrides in (
        {"next_cursor": "page-2", "partial": False},
        {"next_cursor": "page-2", "partial": True, "exhausted": True, "reason": "token budget exhausted"},
    ):
        try:
            _make_result(**overrides)
        except QueryBudgetError as exc:
            assert "next_cursor requires" in str(exc)
        else:
            raise AssertionError("expected invalid next_cursor validation to fail")


def test_returned_count_greater_than_limit_is_rejected() -> None:
    try:
        _make_result(returned_count=30)
    except QueryBudgetError as exc:
        assert "must not exceed limit" in str(exc)
    else:
        raise AssertionError("expected limit validation to fail")


def test_audit_summary_contains_budget_ids_status_counts_without_long_dumps() -> None:
    result = _make_result(
        status="clipped",
        clipped=True,
        reason="payload clipped " + ("x" * 500),
        next_action="request next page",
    )

    summary = result.audit_summary()

    assert summary["query_budget_id"] == "memory-page-1"
    assert summary["status"] == "clipped"
    assert summary["returned_count"] == 20
    assert summary["scanned_count"] == 25
    assert summary["clipped"] is True
    assert summary["partial"] is False
    assert summary["exhausted"] is False
    assert "reason" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
