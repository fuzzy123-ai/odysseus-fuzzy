from src.live_orchestration_runtime_bridge import build_live_orchestration_runtime_bridge_plan


def test_default_builder_is_conservative_and_needs_operator_review():
    plan = build_live_orchestration_runtime_bridge_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_review"
    assert gate_status == {
        "dry_run_payload_valid": "needs_operator_review",
        "live_send_disabled": "go",
        "mailbox_dispatch_ready": "needs_operator_review",
        "operator_review_required": "go",
        "thread_ref_resolved": "needs_operator_review",
    }


def test_dry_run_ready_requires_all_inputs_and_live_send_disabled():
    plan = build_live_orchestration_runtime_bridge_plan(
        thread_ref_resolved=True,
        mailbox_dispatch_ready=True,
        dry_run_payload_valid=True,
        operator_review_required=True,
        live_send_disabled=True,
    )

    assert plan.decision.decision == "dry_run_ready"


def test_live_send_claim_blocks_bridge_plan():
    plan = build_live_orchestration_runtime_bridge_plan(
        thread_ref_resolved=True,
        mailbox_dispatch_ready=True,
        dry_run_payload_valid=True,
        operator_review_required=True,
        live_send_disabled=False,
        live_send_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_orchestration_runtime_bridge_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "dry_run_payload_valid",
                "status": "needs_operator_review",
                "summary": "manual review of dry-run payload validity is still required",
            },
            {
                "gate_id": "live_send_disabled",
                "status": "go",
                "summary": "live send remains disabled during dry-run bridge planning",
            },
            {
                "gate_id": "mailbox_dispatch_ready",
                "status": "needs_operator_review",
                "summary": "manual review of mailbox dispatch planning is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any runtime bridge follow-up",
            },
            {
                "gate_id": "thread_ref_resolved",
                "status": "needs_operator_review",
                "summary": "manual review of thread reference resolution is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_review",
            "next_action": "complete the remaining dry-run bridge review inputs before operator signoff",
        },
        "next_allowed_actions": (
            "review thread reference resolution manually",
            "review mailbox dispatch payload in dry-run form",
            "keep live send disabled during operator review",
            "capture operator notes before any runtime bridge follow-up",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_orchestration_runtime_bridge_plan()
    markdown = plan.to_markdown()

    assert "# Live Orchestration Runtime Bridge Dry Run" in markdown
    assert "needs_operator_review" in markdown
    assert "Next Allowed Actions" in markdown
