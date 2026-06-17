from src.live_plugin_manifest_discovery_dry_run import (
    build_live_plugin_manifest_discovery_dry_run_plan,
)


def test_default_builder_is_conservative_and_needs_operator_review():
    plan = build_live_plugin_manifest_discovery_dry_run_plan()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_review"
    assert gate_status == {
        "capability_metadata_present": "needs_operator_review",
        "import_path_disabled": "go",
        "local_audit_policy_selected": "needs_operator_review",
        "manifest_path_selected": "needs_operator_review",
        "manifest_schema_reviewed": "needs_operator_review",
        "operator_review_required": "go",
    }


def test_discovery_plan_ready_requires_all_inputs_and_import_path_disabled():
    plan = build_live_plugin_manifest_discovery_dry_run_plan(
        manifest_path_selected=True,
        manifest_schema_reviewed=True,
        capability_metadata_present=True,
        local_audit_policy_selected=True,
        operator_review_required=True,
        import_path_disabled=True,
    )

    assert plan.decision.decision == "discovery_plan_ready"


def test_runtime_or_import_claims_block_the_plan():
    plan = build_live_plugin_manifest_discovery_dry_run_plan(
        manifest_path_selected=True,
        manifest_schema_reviewed=True,
        capability_metadata_present=True,
        local_audit_policy_selected=True,
        operator_review_required=True,
        import_path_disabled=False,
        plugin_import_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_plugin_manifest_discovery_dry_run_plan()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "capability_metadata_present",
                "status": "needs_operator_review",
                "summary": "manual review of capability metadata is still required",
            },
            {
                "gate_id": "import_path_disabled",
                "status": "go",
                "summary": "import and runtime-affecting paths remain disabled during manifest discovery review",
            },
            {
                "gate_id": "local_audit_policy_selected",
                "status": "needs_operator_review",
                "summary": "manual selection of a local audit policy is still required",
            },
            {
                "gate_id": "manifest_path_selected",
                "status": "needs_operator_review",
                "summary": "manual review of the manifest path is still required",
            },
            {
                "gate_id": "manifest_schema_reviewed",
                "status": "needs_operator_review",
                "summary": "manual manifest-schema review is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any discovery follow-up",
            },
        ),
        "decision": {
            "decision": "needs_operator_review",
            "next_action": "complete the remaining manifest discovery review inputs before operator signoff",
        },
        "next_allowed_actions": (
            "review the manifest path and schema manually",
            "confirm capability metadata and local audit policy offline",
            "keep import, setup, dynamic import, and exec paths disabled during dry-run review",
            "record operator notes without enabling plugin runtime behavior",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_plugin_manifest_discovery_dry_run_plan()
    markdown = plan.to_markdown()

    assert "# Live Plugin Manifest Discovery Dry Run Plan" in markdown
    assert "needs_operator_review" in markdown
    assert "Next Allowed Actions" in markdown
