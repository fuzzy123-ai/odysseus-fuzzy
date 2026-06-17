from src.graph_maintenance_review_gate import build_graph_maintenance_review_gate


def test_default_builder_is_conservative_and_needs_review_gate_evidence():
    gate = build_graph_maintenance_review_gate()

    assert gate.gate_id == "graph_maintenance_review_gate"
    assert gate.decision == "needs_review_gate_evidence"
    assert gate.status == "needs_review_gate_evidence"


def test_gate_ready_requires_all_positive_review_gates():
    gate = build_graph_maintenance_review_gate(
        maintenance_job_recorded=True,
        candidate_count_recorded=True,
        provenance_recorded=True,
        review_required=True,
        truth_write_disabled=True,
        bounded_batch_enforced=True,
        rollback_plan_recorded=True,
        operator_next_action_recorded=True,
        candidate_count=42,
    )

    assert gate.decision == "graph_review_gate_ready"
    assert gate.status == "go"


def test_blocked_when_runtime_or_truth_write_paths_enabled():
    gate = build_graph_maintenance_review_gate(
        maintenance_job_recorded=True,
        candidate_count_recorded=True,
        provenance_recorded=True,
        review_required=True,
        truth_write_enabled=True,
        bounded_batch_enforced=True,
        rollback_plan_recorded=True,
        operator_next_action_recorded=True,
        candidate_count=42,
    )

    assert gate.decision == "blocked"
    assert gate.status == "blocked"


def test_to_dict_is_compact_and_stable():
    gate = build_graph_maintenance_review_gate(
        maintenance_job_recorded=True,
        candidate_count_recorded=True,
        provenance_recorded=False,
        review_required=True,
        truth_write_disabled=True,
        bounded_batch_enforced=False,
        rollback_plan_recorded=True,
        operator_next_action_recorded=False,
        candidate_count=17,
    )

    assert gate.to_dict() == {
        "gate_id": "graph_maintenance_review_gate",
        "decision": "needs_review_gate_evidence",
        "status": "needs_review_gate_evidence",
        "summary": "graph maintenance review evidence still needs maintenance, provenance, rollback, or bounded-batch proof",
        "candidate_count": 17,
        "next_allowed_actions": (
            "review graph-maintenance evidence, provenance, and rollback notes manually",
            "confirm bounded batch and operator next-action evidence offline",
            "keep truth-write, rebuild, fullbuild, migration, and accelerator runtime paths disabled",
        ),
    }


def test_markdown_is_operator_friendly_and_compact():
    gate = build_graph_maintenance_review_gate(candidate_count=17)

    markdown = gate.to_markdown()
    assert "# Graph Maintenance Review Gate" in markdown
    assert "needs_review_gate_evidence" in markdown
    assert "Candidate count" in markdown
