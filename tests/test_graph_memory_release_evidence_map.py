from src.graph_memory_release_evidence_map import build_graph_memory_release_evidence_map


def test_default_builder_is_conservative_and_needs_release_review():
    plan = build_graph_memory_release_evidence_map()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_release_review"
    assert gate_status == {
        "accelerator_optional_post_release": "go",
        "derived_cluster_runs_recorded": "needs_release_review",
        "fallback_routing_recorded": "needs_release_review",
        "graph_maintenance_worker_recorded": "needs_release_review",
        "progressive_graph_api_recorded": "needs_release_review",
        "provenance_required": "go",
        "query_budgets_recorded": "needs_release_review",
        "review_required": "go",
        "small_model_evaluation_gates_recorded": "needs_release_review",
        "summary_worker_recorded": "needs_release_review",
        "truth_write_disabled": "go",
        "unbounded_fullbuild_disabled": "go",
    }


def test_evidence_map_ready_requires_all_foundation_records_and_safety_guards():
    plan = build_graph_memory_release_evidence_map(
        progressive_graph_api_recorded=True,
        query_budgets_recorded=True,
        derived_cluster_runs_recorded=True,
        summary_worker_recorded=True,
        graph_maintenance_worker_recorded=True,
        small_model_evaluation_gates_recorded=True,
        fallback_routing_recorded=True,
        provenance_required=True,
        review_required=True,
        truth_write_disabled=True,
        unbounded_fullbuild_disabled=True,
        accelerator_optional_post_release=True,
    )

    assert plan.decision.decision == "evidence_map_ready"


def test_truth_write_rebuild_migration_or_accelerator_claims_block_the_map():
    plan = build_graph_memory_release_evidence_map(
        progressive_graph_api_recorded=True,
        query_budgets_recorded=True,
        derived_cluster_runs_recorded=True,
        summary_worker_recorded=True,
        graph_maintenance_worker_recorded=True,
        small_model_evaluation_gates_recorded=True,
        fallback_routing_recorded=True,
        truth_write_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_graph_memory_release_evidence_map()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "accelerator_optional_post_release",
                "status": "go",
                "summary": "accelerator tracks remain optional and post-release",
            },
            {
                "gate_id": "derived_cluster_runs_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the derived cluster run evidence record",
            },
            {
                "gate_id": "fallback_routing_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the fallback routing evidence record",
            },
            {
                "gate_id": "graph_maintenance_worker_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the graph maintenance worker evidence record",
            },
            {
                "gate_id": "progressive_graph_api_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the progressive graph API evidence record",
            },
            {
                "gate_id": "provenance_required",
                "status": "go",
                "summary": "provenance remains required for graph-memory release review",
            },
            {
                "gate_id": "query_budgets_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the query budget evidence record",
            },
            {
                "gate_id": "review_required",
                "status": "go",
                "summary": "review remains required for graph-memory release review",
            },
            {
                "gate_id": "small_model_evaluation_gates_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the small-model evaluation gate evidence record",
            },
            {
                "gate_id": "summary_worker_recorded",
                "status": "needs_release_review",
                "summary": "release review still needs the summary worker evidence record",
            },
            {
                "gate_id": "truth_write_disabled",
                "status": "go",
                "summary": "truth-write remains disabled during graph-memory release review",
            },
            {
                "gate_id": "unbounded_fullbuild_disabled",
                "status": "go",
                "summary": "unbounded fullbuild remains disabled during graph-memory release review",
            },
        ),
        "decision": {
            "decision": "needs_release_review",
            "next_action": "complete the remaining graph-memory release evidence records before review",
        },
        "next_allowed_actions": (
            "review graph-memory evidence records and provenance requirements manually",
            "confirm budget, worker, and fallback proofs offline",
            "keep truth-write, fullbuild, rebuild, migration, and accelerator runtime paths disabled",
            "record release-review notes without enabling plugin or runtime integrations",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_graph_memory_release_evidence_map()
    markdown = plan.to_markdown()

    assert "# Graph Memory Release Evidence Map" in markdown
    assert "needs_release_review" in markdown
    assert "Next Allowed Actions" in markdown
