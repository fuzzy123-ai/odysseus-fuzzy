from src.live_plugin_capability_preview_index import build_live_plugin_capability_preview_index


def test_default_builder_is_conservative_and_needs_operator_review():
    plan = build_live_plugin_capability_preview_index()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_review"
    assert gate_status == {
        "audit_policy_selected": "needs_operator_review",
        "capability_metadata_available": "needs_operator_review",
        "import_path_disabled": "go",
        "manifest_metadata_available": "needs_operator_review",
        "operator_review_required": "go",
        "runtime_enablement_disabled": "go",
    }


def test_preview_index_ready_requires_all_inputs_and_runtime_paths_disabled():
    plan = build_live_plugin_capability_preview_index(
        manifest_metadata_available=True,
        capability_metadata_available=True,
        audit_policy_selected=True,
        operator_review_required=True,
        runtime_enablement_disabled=True,
        import_path_disabled=True,
    )

    assert plan.decision.decision == "preview_index_ready"


def test_runtime_or_import_claims_block_the_plan():
    plan = build_live_plugin_capability_preview_index(
        manifest_metadata_available=True,
        capability_metadata_available=True,
        audit_policy_selected=True,
        operator_review_required=True,
        runtime_enablement_disabled=False,
        import_path_disabled=False,
        plugin_import_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_plugin_capability_preview_index()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "audit_policy_selected",
                "status": "needs_operator_review",
                "summary": "manual selection of an audit policy is still required",
            },
            {
                "gate_id": "capability_metadata_available",
                "status": "needs_operator_review",
                "summary": "manual review of capability metadata is still required",
            },
            {
                "gate_id": "import_path_disabled",
                "status": "go",
                "summary": "import paths remain disabled during capability preview review",
            },
            {
                "gate_id": "manifest_metadata_available",
                "status": "needs_operator_review",
                "summary": "manual review of manifest metadata is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any preview follow-up",
            },
            {
                "gate_id": "runtime_enablement_disabled",
                "status": "go",
                "summary": "runtime enablement remains disabled during capability preview review",
            },
        ),
        "decision": {
            "decision": "needs_operator_review",
            "next_action": "complete the remaining capability preview review inputs before operator signoff",
        },
        "next_allowed_actions": (
            "review manifest and capability metadata manually",
            "confirm audit policy selection offline",
            "keep import, setup, dynamic import, exec, and runtime enablement disabled during preview review",
            "record operator notes without enabling plugin runtime behavior",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_plugin_capability_preview_index()
    markdown = plan.to_markdown()

    assert "# Live Plugin Capability Preview Index" in markdown
    assert "needs_operator_review" in markdown
    assert "Next Allowed Actions" in markdown
