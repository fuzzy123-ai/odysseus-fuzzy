from src.live_system_health_host_agent_plan import build_live_system_health_host_agent_plan


def test_default_builder_is_conservative_and_needs_operator_input():
    plan = build_live_system_health_host_agent_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_input"
    assert gate_status == {
        "install_method_reviewed": "needs_operator_input",
        "operator_host_scope_selected": "needs_operator_input",
        "permissions_reviewed": "needs_operator_input",
        "rollback_plan_ready": "needs_operator_input",
        "secrets_policy_ready": "needs_operator_input",
        "snapshot_api_contract_ready": "needs_operator_input",
    }


def test_host_agent_plan_ready_requires_all_inputs_and_runtime_execution_disabled():
    plan = build_live_system_health_host_agent_plan(
        operator_host_scope_selected=True,
        install_method_reviewed=True,
        snapshot_api_contract_ready=True,
        permissions_reviewed=True,
        rollback_plan_ready=True,
        secrets_policy_ready=True,
        runtime_execution_enabled=False,
    )

    assert plan.decision.decision == "host_agent_plan_ready"


def test_host_agent_plan_ready_still_blocks_host_runtime_actions():
    plan = build_live_system_health_host_agent_plan(
        operator_host_scope_selected=True,
        install_method_reviewed=True,
        snapshot_api_contract_ready=True,
        permissions_reviewed=True,
        rollback_plan_ready=True,
        secrets_policy_ready=True,
        runtime_execution_enabled=False,
    )

    assert plan.decision.decision == "host_agent_plan_ready"
    assert "host_agent_start" in plan.blocked_live_actions
    assert "host_command_execution" in plan.blocked_live_actions
    assert "systemd_mutation" in plan.blocked_live_actions
    assert "socket_access" in plan.blocked_live_actions
    assert "secret_or_token_capture" in plan.blocked_live_actions


def test_runtime_or_host_enablement_claims_block_the_plan():
    plan = build_live_system_health_host_agent_plan(
        operator_host_scope_selected=True,
        install_method_reviewed=True,
        snapshot_api_contract_ready=True,
        permissions_reviewed=True,
        rollback_plan_ready=True,
        secrets_policy_ready=True,
        host_command_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_system_health_host_agent_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "install_method_reviewed",
                "status": "needs_operator_input",
                "summary": "manual review of the install method is still required",
            },
            {
                "gate_id": "operator_host_scope_selected",
                "status": "needs_operator_input",
                "summary": "manual host-scope selection is still required",
            },
            {
                "gate_id": "permissions_reviewed",
                "status": "needs_operator_input",
                "summary": "manual permissions review is still required",
            },
            {
                "gate_id": "rollback_plan_ready",
                "status": "needs_operator_input",
                "summary": "manual rollback-plan review is still required",
            },
            {
                "gate_id": "secrets_policy_ready",
                "status": "needs_operator_input",
                "summary": "manual secrets-policy review is still required",
            },
            {
                "gate_id": "snapshot_api_contract_ready",
                "status": "needs_operator_input",
                "summary": "manual review of the snapshot API contract is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_input",
            "next_action": "complete the remaining host-agent MVP planning inputs before operator review",
        },
        "next_allowed_actions": (
            "review the host scope and installation method manually",
            "confirm the snapshot API contract and permissions review offline",
            "prepare rollback and secrets-policy notes before any host-agent follow-up",
            "keep runtime execution disabled during operator planning",
        ),
        "blocked_live_actions": (
            "host_agent_start",
            "host_command_execution",
            "systemd_mutation",
            "socket_access",
            "network_action",
            "secret_or_token_capture",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_system_health_host_agent_plan()
    markdown = plan.to_markdown()

    assert "# Live System Health Host Agent MVP Plan" in markdown
    assert "needs_operator_input" in markdown
    assert "Next Allowed Actions" in markdown
    assert "Blocked Live Actions" in markdown
    assert "host_agent_start" in markdown
