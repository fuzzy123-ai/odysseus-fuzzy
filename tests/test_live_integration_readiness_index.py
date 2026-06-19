from src.live_integration_readiness_index import build_live_integration_readiness_index


def test_default_builder_is_conservative_and_needs_manual_evidence():
    plan = build_live_integration_readiness_index()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_manual_evidence"
    assert plan.decision.external_release_ready is False
    assert gate_status == {
        "live_slices_recorded": "needs_manual_evidence",
        "network_actions_disabled": "go",
        "operator_review_required": "go",
        "plugin_imports_disabled": "go",
        "provider_proof_manual_gate_recorded": "needs_manual_evidence",
        "runtime_enablement_disabled": "go",
        "test_vault_rebuild_manual_gate_recorded": "go",
    }


def test_integration_readiness_ready_is_internal_only_not_external_go():
    plan = build_live_integration_readiness_index(
        live_slices_recorded=True,
        provider_proof_manual_gate_recorded=True,
        test_vault_rebuild_manual_gate_recorded=True,
        runtime_enablement_disabled=True,
        network_actions_disabled=True,
        plugin_imports_disabled=True,
        operator_review_required=True,
    )

    assert plan.decision.decision == "integration_readiness_ready"
    assert plan.decision.external_release_ready is False


def test_runtime_network_or_import_claims_block_the_index():
    plan = build_live_integration_readiness_index(
        live_slices_recorded=True,
        provider_proof_manual_gate_recorded=True,
        test_vault_rebuild_manual_gate_recorded=True,
        runtime_enablement_disabled=False,
        network_actions_disabled=False,
        plugin_imports_disabled=False,
        operator_review_required=True,
        network_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_integration_readiness_index()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "live_slices_recorded",
                "status": "needs_manual_evidence",
                "summary": "manual recording of live integration slices is still required",
            },
            {
                "gate_id": "network_actions_disabled",
                "status": "go",
                "summary": "network actions remain disabled during readiness review",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any live integration follow-up",
            },
            {
                "gate_id": "plugin_imports_disabled",
                "status": "go",
                "summary": "plugin imports remain disabled during readiness review",
            },
            {
                "gate_id": "provider_proof_manual_gate_recorded",
                "status": "needs_manual_evidence",
                "summary": "manual provider-proof evidence is still required",
            },
            {
                "gate_id": "runtime_enablement_disabled",
                "status": "go",
                "summary": "runtime enablement remains disabled during readiness review",
            },
            {
                "gate_id": "test_vault_rebuild_manual_gate_recorded",
                "status": "go",
                "summary": "test-vault rebuild manual gate is recorded for internal readiness review",
            },
        ),
        "decision": {
            "decision": "needs_manual_evidence",
            "next_action": "complete the remaining manual provider-proof evidence gate",
            "external_release_ready": False,
        },
        "next_allowed_actions": (
            "review recorded live-integration slices and manual evidence gates",
            "complete provider-proof evidence manually",
            "keep runtime, network, and plugin-import paths disabled during readiness review",
            "record operator notes without claiming external 1.0.0 release go",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_integration_readiness_index()
    markdown = plan.to_markdown()

    assert "# Live Integration Readiness Index" in markdown
    assert "needs_manual_evidence" in markdown
    assert "Next Allowed Actions" in markdown
