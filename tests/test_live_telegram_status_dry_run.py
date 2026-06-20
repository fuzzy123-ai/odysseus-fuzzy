from src.live_telegram_status_dry_run import build_live_telegram_status_dry_run_plan


def test_default_builder_is_conservative_and_needs_operator_review():
    plan = build_live_telegram_status_dry_run_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_review"
    assert gate_status == {
        "offline_preview_fixture_available": "needs_operator_review",
        "operator_review_required": "go",
        "redaction_policy_reviewed": "needs_operator_review",
        "send_path_disabled": "go",
        "status_payload_selected": "needs_operator_review",
    }


def test_dry_run_plan_ready_requires_all_inputs_and_send_paths_disabled():
    plan = build_live_telegram_status_dry_run_plan(
        status_payload_selected=True,
        offline_preview_fixture_available=True,
        redaction_policy_reviewed=True,
        operator_review_required=True,
        send_path_disabled=True,
    )

    assert plan.decision.decision == "dry_run_plan_ready"


def test_dry_run_plan_ready_still_blocks_live_telegram_actions():
    plan = build_live_telegram_status_dry_run_plan(
        status_payload_selected=True,
        offline_preview_fixture_available=True,
        redaction_policy_reviewed=True,
        operator_review_required=True,
        send_path_disabled=True,
    )

    assert plan.blocked_live_actions == (
        "telegram_send",
        "telegram_token_capture",
        "network_request",
        "scheduler_start",
        "runtime_hook_enablement",
        "unsafe_payload_logging",
    )


def test_runtime_or_send_claims_block_the_plan():
    plan = build_live_telegram_status_dry_run_plan(
        status_payload_selected=True,
        offline_preview_fixture_available=True,
        redaction_policy_reviewed=True,
        operator_review_required=True,
        send_path_disabled=False,
        token_present=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_telegram_status_dry_run_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "offline_preview_fixture_available",
                "status": "needs_operator_review",
                "summary": "manual preparation of an offline preview fixture is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any Telegram follow-up",
            },
            {
                "gate_id": "redaction_policy_reviewed",
                "status": "needs_operator_review",
                "summary": "manual redaction-policy review is still required",
            },
            {
                "gate_id": "send_path_disabled",
                "status": "go",
                "summary": "Telegram send path remains disabled during dry-run review",
            },
            {
                "gate_id": "status_payload_selected",
                "status": "needs_operator_review",
                "summary": "manual review of the status payload is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_review",
            "next_action": "complete the remaining Telegram dry-run review inputs before operator signoff",
        },
        "next_allowed_actions": (
            "review the Telegram status payload manually",
            "prepare an offline preview fixture for operator inspection",
            "confirm redaction policy before any Telegram follow-up",
            "keep send, scheduler, network, and runtime hooks disabled during dry-run planning",
        ),
        "blocked_live_actions": (
            "telegram_send",
            "telegram_token_capture",
            "network_request",
            "scheduler_start",
            "runtime_hook_enablement",
            "unsafe_payload_logging",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_telegram_status_dry_run_plan()
    markdown = plan.to_markdown()

    assert "# Live Telegram Status Dry Run Plan" in markdown
    assert "needs_operator_review" in markdown
    assert "Next Allowed Actions" in markdown
    assert "Blocked Live Actions" in markdown
    assert "telegram_send" in markdown
