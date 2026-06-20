from src.live_dashboard_readiness import build_live_dashboard_readiness_summary
from src.live_integration_readiness_index import build_live_integration_readiness_index


def test_default_dashboard_summary_needs_manual_evidence():
    summary = build_live_dashboard_readiness_summary()

    assert summary.status == "needs_manual_evidence"
    assert summary.external_release_ready is False
    assert summary.tiles[0].tile_id == "live_slices_recorded"
    assert summary.tiles[0].status == "needs_manual_evidence"


def test_ready_index_maps_to_operator_review_not_external_release_go():
    index = build_live_integration_readiness_index(
        live_slices_recorded=True,
        provider_proof_manual_gate_recorded=True,
        test_vault_rebuild_manual_gate_recorded=True,
        runtime_enablement_disabled=True,
        network_actions_disabled=True,
        plugin_imports_disabled=True,
        operator_review_required=True,
    )

    summary = build_live_dashboard_readiness_summary(index)

    assert summary.status == "ready_for_operator_review"
    assert summary.external_release_ready is False
    assert summary.blocked_live_actions == (
        "host_command_execution",
        "network_request",
        "provider_call",
        "telegram_send",
        "plugin_import",
        "runtime_enablement",
        "secret_or_token_capture",
        "automatic_release_go",
    )


def test_blocked_index_has_no_dashboard_next_actions():
    index = build_live_integration_readiness_index(
        live_slices_recorded=True,
        runtime_enablement_disabled=False,
        network_actions_disabled=False,
        plugin_imports_disabled=False,
        network_enabled=True,
    )

    summary = build_live_dashboard_readiness_summary(index)

    assert summary.status == "blocked"
    assert summary.next_actions == ()


def test_rejects_wrong_index_type():
    try:
        build_live_dashboard_readiness_summary(index=object())  # type: ignore[arg-type]
    except ValueError as exc:
        assert str(exc) == "index must be a LiveIntegrationReadinessIndex"
    else:
        raise AssertionError("expected ValueError")


def test_to_dict_is_stable():
    summary = build_live_dashboard_readiness_summary()

    assert summary.to_dict() == {
        "status": "needs_manual_evidence",
        "external_release_ready": False,
        "tiles": (
            {
                "tile_id": "live_slices_recorded",
                "status": "needs_manual_evidence",
                "summary": "manual recording of live integration slices is still required",
            },
            {
                "tile_id": "network_actions_disabled",
                "status": "go",
                "summary": "network actions remain disabled during readiness review",
            },
            {
                "tile_id": "operator_review_required",
                "status": "go",
                "summary": "operator review remains explicitly required before any live integration follow-up",
            },
            {
                "tile_id": "plugin_imports_disabled",
                "status": "go",
                "summary": "plugin imports remain disabled during readiness review",
            },
            {
                "tile_id": "provider_proof_manual_gate_recorded",
                "status": "go",
                "summary": "provider-proof manual gate is recorded for internal readiness review",
            },
            {
                "tile_id": "runtime_enablement_disabled",
                "status": "go",
                "summary": "runtime enablement remains disabled during readiness review",
            },
            {
                "tile_id": "test_vault_rebuild_manual_gate_recorded",
                "status": "go",
                "summary": "test-vault rebuild manual gate is recorded for internal readiness review",
            },
        ),
        "next_actions": (
            "review recorded live-integration slices and manual evidence gates",
            "confirm live integration slices are recorded before runtime follow-up",
            "keep runtime, network, and plugin-import paths disabled during readiness review",
            "record operator notes without claiming external 1.0.0 release go",
        ),
        "blocked_live_actions": (
            "host_command_execution",
            "network_request",
            "provider_call",
            "telegram_send",
            "plugin_import",
            "runtime_enablement",
            "secret_or_token_capture",
            "automatic_release_go",
        ),
    }


def test_markdown_is_operator_friendly():
    summary = build_live_dashboard_readiness_summary()
    markdown = summary.to_markdown()

    assert "# Live Dashboard Readiness Summary" in markdown
    assert "needs_manual_evidence" in markdown
    assert "Blocked Live Actions" in markdown
    assert "host_command_execution" in markdown
