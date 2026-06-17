from src.live_test_vault_rebuild_plan import build_live_test_vault_rebuild_plan


def test_default_builder_keeps_all_test_vault_gates_open_for_manual_input():
    plan = build_live_test_vault_rebuild_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert gate_status == {
        "evidence_redaction_review": "needs_operator_input",
        "export_artifact_plan": "needs_operator_input",
        "isolated_import_target": "needs_operator_input",
        "rebuild_verification_plan": "needs_operator_input",
        "test_vault_selected": "needs_operator_input",
    }


def test_default_builder_returns_needs_operator_input():
    plan = build_live_test_vault_rebuild_plan()

    assert plan.decision.decision == "needs_operator_input"
    assert "manual test-vault proof preparation inputs" in plan.decision.next_action


def test_ready_for_manual_operator_run_requires_all_inputs_ready():
    plan = build_live_test_vault_rebuild_plan(
        test_vault_selected=True,
        export_artifact_plan_ready=True,
        isolated_import_target_ready=True,
        rebuild_verification_plan_ready=True,
        evidence_redaction_ready=True,
    )

    assert plan.decision.decision == "ready_for_manual_operator_run"


def test_to_dict_is_stable():
    plan = build_live_test_vault_rebuild_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "evidence_redaction_review",
                "status": "needs_operator_input",
                "summary": "manual evidence-redaction review is still required",
            },
            {
                "gate_id": "export_artifact_plan",
                "status": "needs_operator_input",
                "summary": "manual preparation of the export artifact plan is still required",
            },
            {
                "gate_id": "isolated_import_target",
                "status": "needs_operator_input",
                "summary": "manual preparation of an isolated import target is still required",
            },
            {
                "gate_id": "rebuild_verification_plan",
                "status": "needs_operator_input",
                "summary": "manual preparation of the rebuild verification plan is still required",
            },
            {
                "gate_id": "test_vault_selected",
                "status": "needs_operator_input",
                "summary": "manual selection of a small test vault is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_input",
            "next_action": "complete the remaining manual test-vault proof preparation inputs",
        },
        "next_allowed_actions": (
            "select a minimal test vault manually",
            "define the export artifact capture plan",
            "prepare an isolated import target manually",
            "review rebuild verification evidence before recording results",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_test_vault_rebuild_plan()
    markdown = plan.to_markdown()

    assert "# Live Test Vault Rebuild Plan" in markdown
    assert "needs_operator_input" in markdown
    assert "Next Allowed Actions" in markdown
