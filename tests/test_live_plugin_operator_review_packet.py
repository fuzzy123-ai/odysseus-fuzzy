from src.live_plugin_operator_review_packet import build_live_plugin_operator_review_packet


def test_default_builder_is_conservative_and_needs_operator_review():
    plan = build_live_plugin_operator_review_packet()
    gate_status = {gate.gate_id: gate.status for gate in plan.gates}

    assert plan.decision.decision == "needs_operator_review"
    assert gate_status == {
        "auto_approval_disabled": "go",
        "capability_preview_available": "needs_operator_review",
        "local_audit_summary_available": "needs_operator_review",
        "manifest_summary_available": "needs_operator_review",
        "operator_review_required": "go",
        "runtime_enablement_disabled": "go",
        "safe_mode_gate_recorded": "needs_operator_review",
    }


def test_review_packet_ready_requires_all_inputs_and_runtime_paths_disabled():
    plan = build_live_plugin_operator_review_packet(
        manifest_summary_available=True,
        capability_preview_available=True,
        local_audit_summary_available=True,
        safe_mode_gate_recorded=True,
        operator_review_required=True,
        auto_approval_disabled=True,
        runtime_enablement_disabled=True,
    )

    assert plan.decision.decision == "review_packet_ready"


def test_runtime_or_auto_approval_claims_block_the_plan():
    plan = build_live_plugin_operator_review_packet(
        manifest_summary_available=True,
        capability_preview_available=True,
        local_audit_summary_available=True,
        safe_mode_gate_recorded=True,
        operator_review_required=True,
        auto_approval_disabled=False,
        runtime_enablement_disabled=False,
        auto_approval_enabled=True,
    )

    assert plan.decision.decision == "blocked"


def test_to_dict_is_stable():
    plan = build_live_plugin_operator_review_packet()

    assert plan.to_dict() == {
        "gates": (
            {
                "gate_id": "auto_approval_disabled",
                "status": "go",
                "summary": "auto-approval remains disabled during operator review",
            },
            {
                "gate_id": "capability_preview_available",
                "status": "needs_operator_review",
                "summary": "manual review of the capability preview is still required",
            },
            {
                "gate_id": "local_audit_summary_available",
                "status": "needs_operator_review",
                "summary": "manual review of the local audit summary is still required",
            },
            {
                "gate_id": "manifest_summary_available",
                "status": "needs_operator_review",
                "summary": "manual review of the manifest summary is still required",
            },
            {
                "gate_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any packet follow-up",
            },
            {
                "gate_id": "runtime_enablement_disabled",
                "status": "go",
                "summary": "runtime enablement remains disabled during operator review",
            },
            {
                "gate_id": "safe_mode_gate_recorded",
                "status": "needs_operator_review",
                "summary": "manual confirmation of the safe-mode gate is still required",
            },
        ),
        "decision": {
            "decision": "needs_operator_review",
            "next_action": "complete the remaining operator review packet inputs before signoff",
        },
        "next_allowed_actions": (
            "review manifest, capability, and audit summaries manually",
            "confirm safe-mode gate recording offline",
            "keep auto-approval, import, setup, and runtime enablement disabled during operator review",
            "record operator notes without enabling plugin runtime behavior",
        ),
    }


def test_markdown_is_operator_friendly():
    plan = build_live_plugin_operator_review_packet()
    markdown = plan.to_markdown()

    assert "# Live Plugin Operator Review Packet" in markdown
    assert "needs_operator_review" in markdown
    assert "Next Allowed Actions" in markdown
