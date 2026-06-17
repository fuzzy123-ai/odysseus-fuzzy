from src.large_graph_budget_proof import build_large_graph_budget_proof


def test_default_builder_is_conservative_and_needs_budget_review():
    plan = build_large_graph_budget_proof()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_budget_review"
    assert gate_status == {
        "accelerator_not_required": "go",
        "clipping_explained": "needs_budget_review",
        "cursor_or_aggregate_available": "needs_budget_review",
        "edge_count_recorded": "needs_budget_review",
        "large_graph_input_recorded": "needs_budget_review",
        "no_full_payload_dump": "go",
        "node_count_at_least_100k": "needs_budget_review",
        "output_budget_enforced": "needs_budget_review",
        "returned_edges_within_budget": "needs_budget_review",
        "returned_nodes_within_budget": "needs_budget_review",
    }


def test_budget_proof_ready_requires_large_counts_budget_and_clipping_evidence():
    plan = build_large_graph_budget_proof(
        node_count=100_000,
        edge_count=250_000,
        output_budget_enforced=True,
        returned_nodes_within_budget=True,
        returned_edges_within_budget=True,
        clipping_explained=True,
        cursor_or_aggregate_available=True,
        no_full_payload_dump=True,
        accelerator_not_required=True,
    )

    assert plan.decision.decision == "budget_proof_ready"


def test_full_dump_rebuild_migration_or_accelerator_claims_block_the_proof():
    plan = build_large_graph_budget_proof(
        node_count=100_000,
        edge_count=250_000,
        output_budget_enforced=True,
        returned_nodes_within_budget=True,
        returned_edges_within_budget=True,
        clipping_explained=True,
        cursor_or_aggregate_available=True,
        full_payload_dump_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable_and_compact():
    plan = build_large_graph_budget_proof(node_count=123_456, edge_count=456_789)

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "accelerator_not_required",
                "status": "go",
                "summary": "accelerator and research runtime are not required for this proof",
            },
            {
                "gate_id": "clipping_explained",
                "status": "needs_budget_review",
                "summary": "budget review still needs clipping explanation evidence",
            },
            {
                "gate_id": "cursor_or_aggregate_available",
                "status": "needs_budget_review",
                "summary": "budget review still needs cursor or aggregate evidence",
            },
            {
                "gate_id": "edge_count_recorded",
                "status": "go",
                "summary": "edge-count evidence is recorded for budget review",
            },
            {
                "gate_id": "large_graph_input_recorded",
                "status": "go",
                "summary": "large-graph input metadata is recorded for budget review",
            },
            {
                "gate_id": "no_full_payload_dump",
                "status": "go",
                "summary": "full payload dumps remain disabled for the large-graph proof",
            },
            {
                "gate_id": "node_count_at_least_100k",
                "status": "go",
                "summary": "node-count evidence shows a 100,000+ graph input",
            },
            {
                "gate_id": "output_budget_enforced",
                "status": "needs_budget_review",
                "summary": "budget review still needs output-budget enforcement evidence",
            },
            {
                "gate_id": "returned_edges_within_budget",
                "status": "needs_budget_review",
                "summary": "budget review still needs proof that returned edges stay within budget",
            },
            {
                "gate_id": "returned_nodes_within_budget",
                "status": "needs_budget_review",
                "summary": "budget review still needs proof that returned nodes stay within budget",
            },
        ),
        "decision": {
            "decision": "needs_budget_review",
            "next_action": "complete the remaining large-graph budget-proof evidence before review",
            "node_count": 123456,
            "edge_count": 456789,
        },
        "next_allowed_actions": (
            "review large-graph count and clipping evidence manually",
            "confirm output budgets and cursor or aggregate evidence offline",
            "keep full payload dumps, rebuilds, migrations, and accelerator runtime paths disabled",
            "record budget-proof notes without enabling plugin or runtime integrations",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_large_graph_budget_proof(node_count=123_456, edge_count=456_789)
    markdown = plan.to_markdown()

    assert "# Large Graph Budget Proof" in markdown
    assert "needs_budget_review" in markdown
    assert "Next Allowed Actions" in markdown
