from src.live_provider_proof_plan import build_live_provider_proof_plan


def test_default_builder_keeps_provider_answer_gates_open_for_manual_input():
    plan = build_live_provider_proof_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert gate_status == {
        "default_model_answer_run": "needs_operator_input",
        "evidence_redaction_review": "needs_operator_input",
        "fallback_model_answer_run": "needs_operator_input",
        "local_or_deepseek_availability": "needs_operator_input",
        "ready_query_index_precheck": "needs_operator_input",
    }


def test_default_builder_returns_needs_operator_input():
    plan = build_live_provider_proof_plan()

    assert plan.decision.decision == "needs_operator_input"
    assert "manual provider-proof preparation inputs" in plan.decision.next_action


def test_ready_for_manual_operator_run_requires_all_inputs_ready():
    plan = build_live_provider_proof_plan(
        query_index_ready=True,
        default_model_inputs_ready=True,
        fallback_model_inputs_ready=True,
        local_or_deepseek_available=True,
        evidence_redaction_ready=True,
    )

    assert plan.decision.decision == "ready_for_manual_operator_run"


def test_ready_plan_still_blocks_live_provider_actions():
    plan = build_live_provider_proof_plan(
        query_index_ready=True,
        default_model_inputs_ready=True,
        fallback_model_inputs_ready=True,
        local_or_deepseek_available=True,
        evidence_redaction_ready=True,
    )

    assert plan.decision.decision == "ready_for_manual_operator_run"
    assert "provider_call" in plan.blocked_live_actions
    assert "network_request" in plan.blocked_live_actions
    assert "secret_or_token_capture" in plan.blocked_live_actions
    assert "automatic_release_go" in plan.blocked_live_actions


def test_to_dict_is_stable():
    plan = build_live_provider_proof_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "default_model_answer_run",
                "status": "needs_operator_input",
                "summary": "manual preparation of default-model answer capture inputs is still required",
            },
            {
                "gate_id": "evidence_redaction_review",
                "status": "needs_operator_input",
                "summary": "manual evidence-redaction review is still required",
            },
            {
                "gate_id": "fallback_model_answer_run",
                "status": "needs_operator_input",
                "summary": "manual preparation of fallback-model answer capture inputs is still required",
            },
            {
                "gate_id": "local_or_deepseek_availability",
                "status": "needs_operator_input",
                "summary": "manual confirmation of local or DeepSeek availability is still required",
            },
            {
                "gate_id": "ready_query_index_precheck",
                "status": "needs_operator_input",
                "summary": "manual confirmation of query index readiness is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_input",
            "next_action": "complete the remaining manual provider-proof preparation inputs",
        },
        "next_allowed_actions": (
            "verify query-index readiness manually",
            "confirm default-model answer capture plan",
            "confirm fallback-model answer capture plan",
            "review evidence redaction before recording results",
        ),
        "blocked_live_actions": (
            "provider_call",
            "network_request",
            "raw_provider_log_capture",
            "secret_or_token_capture",
            "automatic_release_go",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_provider_proof_plan()
    markdown = plan.to_markdown()

    assert "# Live Provider Proof Plan" in markdown
    assert "needs_operator_input" in markdown
    assert "Next Allowed Actions" in markdown
    assert "Blocked Live Actions" in markdown
    assert "provider_call" in markdown
