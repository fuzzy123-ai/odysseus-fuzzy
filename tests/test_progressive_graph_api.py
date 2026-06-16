from src.progressive_graph_api import (
    GraphAggregate,
    GraphEdgeSummary,
    GraphNodeSummary,
    GraphQueryBudget,
    GraphViewport,
    ProgressiveGraphApiError,
    ProgressiveGraphPage,
    ProgressiveGraphStatus,
)


def _make_budget(**overrides) -> GraphQueryBudget:
    payload = {
        "limit": 5,
        "max_nodes": 5,
        "max_edges": 6,
        "depth": 2,
        "max_hops": 0,
        "time_budget_ms": 200,
        "payload_budget_bytes": 4096,
    }
    payload.update(overrides)
    return GraphQueryBudget.create(**payload)


def _make_viewport(**overrides) -> GraphViewport:
    payload = {
        "viewport_ref": "main-view",
        "node_ref": "",
    }
    payload.update(overrides)
    return GraphViewport.create(**payload)


def _make_node(node_id: str = "node-1") -> GraphNodeSummary:
    return GraphNodeSummary.create(node_id=node_id, label="Node", node_type="entity", score=0.9)


def _make_edge(edge_id: str = "edge-1") -> GraphEdgeSummary:
    return GraphEdgeSummary.create(edge_id=edge_id, source_id="node-1", target_id="node-2", edge_type="rel", score=0.5)


def _make_aggregate() -> GraphAggregate:
    return GraphAggregate.create(aggregate_id="type-entity", label="Entities", count=2)


def _make_page(**overrides) -> ProgressiveGraphPage:
    payload = {
        "graph_query_id": "graph-query-1",
        "graph_ref": "graph-main",
        "viewport": _make_viewport(),
        "query_kind": "overview",
        "budget": _make_budget(),
        "nodes": (_make_node("node-1"), _make_node("node-2")),
        "edges": (_make_edge(),),
        "aggregates": (_make_aggregate(),),
        "node_count": 2,
        "edge_count": 1,
        "status": "complete",
        "partial": False,
        "clipped": False,
        "next_cursor": "",
        "reason": "",
        "next_action": "",
        "evidence_ref": "graph page proof",
    }
    payload.update(overrides)
    return ProgressiveGraphPage.create(**payload)


def test_valid_progressive_graph_page_normalizes_stably() -> None:
    page = _make_page(graph_query_id=" Graph Query 1 ", status="complete")

    assert page.graph_query_id == "graph-query-1"
    assert page.status is ProgressiveGraphStatus.COMPLETE
    assert page.node_count == 2
    assert page.edge_count == 1


def test_unbounded_or_missing_budgets_are_rejected() -> None:
    try:
        _make_budget(limit=0)
    except ProgressiveGraphApiError as exc:
        assert "must be bounded" in str(exc)
    else:
        raise AssertionError("expected bounded budget validation to fail")


def test_nodes_or_edges_over_budget_are_rejected() -> None:
    try:
        _make_page(node_count=6, nodes=tuple(_make_node(f"node-{idx}") for idx in range(6)))
    except ProgressiveGraphApiError as exc:
        assert "node_count must not exceed" in str(exc)
    else:
        raise AssertionError("expected node budget validation to fail")

    try:
        _make_page(edge_count=7, edges=tuple(_make_edge(f"edge-{idx}") for idx in range(7)))
    except ProgressiveGraphApiError as exc:
        assert "edge_count must not exceed" in str(exc)
    else:
        raise AssertionError("expected edge budget validation to fail")


def test_clipped_or_partial_without_reason_or_next_action_is_rejected() -> None:
    for overrides in (
        {"partial": True, "status": "partial", "reason": " ", "next_action": " ", "next_cursor": "cursor-2"},
        {"clipped": True, "status": "clipped", "reason": " ", "next_action": " "},
    ):
        try:
            _make_page(**overrides)
        except ProgressiveGraphApiError as exc:
            assert "require reason or next_action" in str(exc)
        else:
            raise AssertionError("expected clipped/partial validation to fail")


def test_blocked_or_failed_without_reason_or_next_action_are_rejected() -> None:
    for status in ("blocked", "failed"):
        try:
            _make_page(status=status, reason=" ", next_action=" ")
        except ProgressiveGraphApiError as exc:
            assert "require reason and next_action" in str(exc)
        else:
            raise AssertionError("expected blocked/failed validation to fail")


def test_path_without_max_hops_is_rejected() -> None:
    try:
        _make_page(query_kind="path", budget=_make_budget(max_hops=0))
    except ProgressiveGraphApiError as exc:
        assert "path queries require max_hops" in str(exc)
    else:
        raise AssertionError("expected path max_hops validation to fail")


def test_neighborhood_without_depth_or_node_ref_is_rejected() -> None:
    try:
        _make_page(query_kind="neighborhood", viewport=_make_viewport(node_ref=""), budget=_make_budget(depth=0))
    except ProgressiveGraphApiError as exc:
        assert "neighborhood queries require depth and node_ref" in str(exc)
    else:
        raise AssertionError("expected neighborhood validation to fail")


def test_audit_summary_contains_counts_kind_status_clipping_cursor_without_long_dumps() -> None:
    page = _make_page(
        status="clipped",
        clipped=True,
        reason="payload clipped " + ("x" * 500),
        next_action="request next page",
        next_cursor="cursor-2",
    )

    summary = page.audit_summary()

    assert summary["graph_query_id"] == "graph-query-1"
    assert summary["query_kind"] == "overview"
    assert summary["status"] == "clipped"
    assert summary["node_count"] == 2
    assert summary["edge_count"] == 1
    assert summary["clipped"] is True
    assert summary["has_next_cursor"] is True
    assert "reason" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
