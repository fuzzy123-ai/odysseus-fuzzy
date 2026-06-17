from src.progressive_graph_api_release_gate import build_progressive_graph_api_release_gate


def test_default_builder_is_conservative_and_needs_gate_review():
    gate = build_progressive_graph_api_release_gate()

    assert gate.gate_id == "progressive_graph_api_release_gate"
    assert gate.decision == "needs_gate_review"
    assert gate.status == "needs_gate_review"


def test_gate_ready_requires_budget_clipping_and_continuation_evidence():
    gate = build_progressive_graph_api_release_gate(
        graph_budget_required=True,
        max_nodes_enforced=True,
        max_edges_enforced=True,
        clipped_status_explained=True,
        partial_status_explained=True,
        cursor_or_next_action_present=True,
        aggregate_view_supported=True,
        full_payload_dump_disabled=True,
        api_runtime_activation_disabled=True,
        node_budget=500,
        edge_budget=1000,
        returned_nodes=250,
        returned_edges=900,
    )

    assert gate.decision == "progressive_graph_gate_ready"
    assert gate.status == "go"


def test_blocked_when_runtime_or_full_dump_paths_enabled():
    gate = build_progressive_graph_api_release_gate(
        graph_budget_required=True,
        max_nodes_enforced=True,
        max_edges_enforced=True,
        clipped_status_explained=True,
        partial_status_explained=True,
        cursor_or_next_action_present=True,
        full_payload_dump_enabled=True,
        node_budget=500,
        edge_budget=1000,
        returned_nodes=100,
        returned_edges=200,
    )

    assert gate.decision == "blocked"
    assert gate.status == "blocked"


def test_to_dict_is_compact_and_stable():
    gate = build_progressive_graph_api_release_gate(
        graph_budget_required=True,
        max_nodes_enforced=True,
        max_edges_enforced=False,
        clipped_status_explained=True,
        partial_status_explained=False,
        cursor_or_next_action_present=False,
        aggregate_view_supported=True,
        node_budget=400,
        edge_budget=900,
        returned_nodes=300,
        returned_edges=800,
    )

    assert gate.to_dict() == {
        "gate_id": "progressive_graph_api_release_gate",
        "decision": "needs_gate_review",
        "status": "needs_gate_review",
        "summary": "progressive graph API release evidence still needs budget, clipping, continuation, or compact output review",
        "node_count": 300,
        "edge_count": 800,
        "node_budget": 400,
        "edge_budget": 900,
        "next_allowed_actions": (
            "review progressive graph budget and clipping evidence manually",
            "confirm cursor, aggregate, or next-action continuation evidence offline",
            "keep full-dump, full-render, runtime activation, rebuild, migration, and accelerator paths disabled",
        ),
    }


def test_markdown_is_operator_friendly_and_compact():
    gate = build_progressive_graph_api_release_gate(
        node_budget=400,
        edge_budget=900,
        returned_nodes=300,
        returned_edges=800,
    )

    markdown = gate.to_markdown()
    assert "# Progressive Graph API Release Gate" in markdown
    assert "needs_gate_review" in markdown
    assert "Node count / budget" in markdown
