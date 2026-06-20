from src.live_plugin_loader_safe_mode import build_live_plugin_loader_safe_mode_plan


def test_default_builder_is_conservative_and_needs_operator_review():
    plan = build_live_plugin_loader_safe_mode_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_review"
    assert gate_status == {
        "capability_boundary_validated": "needs_operator_review",
        "local_audit_clean": "needs_operator_review",
        "manifest_validated": "needs_operator_review",
        "operator_review_required": "go",
        "top_level_import_blocked": "go",
    }


def test_safe_mode_plan_ready_requires_all_safe_inputs_and_import_still_blocked():
    plan = build_live_plugin_loader_safe_mode_plan(
        manifest_validated=True,
        capability_boundary_validated=True,
        local_audit_clean=True,
        top_level_import_blocked=True,
        operator_review_required=True,
    )

    assert plan.decision.decision == "safe_mode_plan_ready"


def test_safe_mode_plan_ready_still_blocks_plugin_runtime_actions():
    plan = build_live_plugin_loader_safe_mode_plan(
        manifest_validated=True,
        capability_boundary_validated=True,
        local_audit_clean=True,
        top_level_import_blocked=True,
        operator_review_required=True,
    )

    assert plan.decision.decision == "safe_mode_plan_ready"
    assert "plugin_import" in plan.blocked_live_actions
    assert "plugin_setup_execution" in plan.blocked_live_actions
    assert "host_access" in plan.blocked_live_actions
    assert "network_action" in plan.blocked_live_actions
    assert "secret_or_token_capture" in plan.blocked_live_actions


def test_runtime_enable_claims_block_safe_mode_plan():
    plan = build_live_plugin_loader_safe_mode_plan(
        manifest_validated=True,
        capability_boundary_validated=True,
        local_audit_clean=True,
        top_level_import_blocked=False,
        operator_review_required=True,
        plugin_import_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_plugin_loader_safe_mode_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "capability_boundary_validated",
                "status": "needs_operator_review",
                "summary": "manual review of capability-boundary validation is still required",
            },
            {
                "gate_id": "local_audit_clean",
                "status": "needs_operator_review",
                "summary": "manual review of local audit evidence is still required",
            },
            {
                "gate_id": "manifest_validated",
                "status": "needs_operator_review",
                "summary": "manual review of plugin manifest validation is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any safe-mode follow-up",
            },
            {
                "gate_id": "top_level_import_blocked",
                "status": "go",
                "summary": "top-level import remains blocked during safe-mode operator review",
            },
        ),
        "decision": {
            "decision": "needs_operator_review",
            "next_action": "complete the remaining safe-mode validation and audit review inputs",
        },
        "next_allowed_actions": (
            "review plugin manifest and capability boundary manually",
            "confirm local audit evidence before any safe-mode follow-up",
            "keep top-level plugin import blocked during operator review",
            "record operator notes without enabling runtime plugin loading",
        ),
        "blocked_live_actions": (
            "plugin_import",
            "plugin_setup_execution",
            "host_access",
            "network_action",
            "socket_access",
            "secret_or_token_capture",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_plugin_loader_safe_mode_plan()
    markdown = plan.to_markdown()

    assert "# Live Plugin Loader Safe Mode Plan" in markdown
    assert "needs_operator_review" in markdown
    assert "Next Allowed Actions" in markdown
    assert "Blocked Live Actions" in markdown
    assert "plugin_import" in markdown
