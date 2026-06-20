from src.live_system_health_local_api_consumer import build_live_system_health_local_api_consumer_plan


def test_default_builder_is_conservative_and_needs_operator_input():
    plan = build_live_system_health_local_api_consumer_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_input"
    assert gate_status == {
        "offline_fixture_available": "needs_operator_input",
        "operator_review_required": "go",
        "sanitized_payload_reviewed": "needs_operator_input",
        "snapshot_api_contract_selected": "needs_operator_input",
        "timeout_policy_reviewed": "needs_operator_input",
    }


def test_consumer_plan_ready_requires_all_inputs_and_runtime_off():
    plan = build_live_system_health_local_api_consumer_plan(
        snapshot_api_contract_selected=True,
        offline_fixture_available=True,
        timeout_policy_reviewed=True,
        sanitized_payload_reviewed=True,
        operator_review_required=True,
    )

    assert plan.decision.decision == "consumer_plan_ready"


def test_consumer_plan_ready_still_blocks_live_runtime_actions():
    plan = build_live_system_health_local_api_consumer_plan(
        snapshot_api_contract_selected=True,
        offline_fixture_available=True,
        timeout_policy_reviewed=True,
        sanitized_payload_reviewed=True,
        operator_review_required=True,
    )

    assert plan.blocked_live_actions == (
        "network_request",
        "host_access",
        "runtime_polling",
        "token_or_secret_capture",
        "unsafe_payload_logging",
        "automatic_consumer_start",
    )


def test_runtime_or_unsafe_logging_claims_block_the_plan():
    plan = build_live_system_health_local_api_consumer_plan(
        snapshot_api_contract_selected=True,
        offline_fixture_available=True,
        timeout_policy_reviewed=True,
        sanitized_payload_reviewed=True,
        operator_review_required=True,
        network_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_system_health_local_api_consumer_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "offline_fixture_available",
                "status": "needs_operator_input",
                "summary": "manual preparation of an offline fixture is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any consumer follow-up",
            },
            {
                "gate_id": "sanitized_payload_reviewed",
                "status": "needs_operator_input",
                "summary": "manual sanitized-payload review is still required",
            },
            {
                "gate_id": "snapshot_api_contract_selected",
                "status": "needs_operator_input",
                "summary": "manual review of the snapshot API contract is still required",
            },
            {
                "gate_id": "timeout_policy_reviewed",
                "status": "needs_operator_input",
                "summary": "manual timeout-policy review is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_input",
            "next_action": "complete the remaining local API consumer planning inputs before operator review",
        },
        "next_allowed_actions": (
            "review the local snapshot API contract manually",
            "prepare an offline sanitized fixture for consumer review",
            "confirm timeout and payload-redaction policy before any follow-up",
            "keep runtime polling and network access disabled during operator planning",
        ),
        "blocked_live_actions": (
            "network_request",
            "host_access",
            "runtime_polling",
            "token_or_secret_capture",
            "unsafe_payload_logging",
            "automatic_consumer_start",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_system_health_local_api_consumer_plan()
    markdown = plan.to_markdown()

    assert "# Live System Health Local API Consumer Plan" in markdown
    assert "needs_operator_input" in markdown
    assert "Next Allowed Actions" in markdown
    assert "Blocked Live Actions" in markdown
    assert "network_request" in markdown
