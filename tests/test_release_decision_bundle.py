from src.release_decision_bundle import build_release_decision_bundle


def test_default_builder_is_conservative_and_not_release_go():
    result = build_release_decision_bundle()

    assert result.gate_id == "release_decision_bundle"
    assert result.decision == "release_deferred"


def test_release_go_requires_all_recorded_positive_gates():
    result = build_release_decision_bundle(
        provider_fallback_gate_recorded=True,
        test_vault_rebuild_gate_recorded=True,
        graph_memory_gate_recorded=True,
        large_graph_gate_recorded=True,
        telegram_boundary_recorded=True,
        plugin_freeze_recorded=True,
        known_limits_recorded=True,
        operator_decision_recorded=True,
    )

    assert result.decision == "release_go"


def test_release_no_go_when_blockers_are_claimed():
    result = build_release_decision_bundle(
        provider_fallback_gate_recorded=True,
        test_vault_rebuild_gate_recorded=True,
        plugin_runtime_enabled=True,
    )

    assert result.decision == "release_no_go"
    assert "no-go" in result.summary.lower()


def test_release_partial_when_required_gates_exist_but_supporting_gate_is_missing():
    result = build_release_decision_bundle(
        provider_fallback_gate_recorded=True,
        test_vault_rebuild_gate_recorded=True,
        graph_memory_gate_recorded=False,
        large_graph_gate_recorded=True,
        telegram_boundary_recorded=True,
        plugin_freeze_recorded=True,
        known_limits_recorded=True,
        operator_decision_recorded=True,
    )

    assert result.decision == "release_partial"


def test_to_dict_is_compact_and_stable():
    result = build_release_decision_bundle(
        provider_fallback_gate_recorded=True,
        test_vault_rebuild_gate_recorded=True,
        graph_memory_gate_recorded=False,
        large_graph_gate_recorded=True,
        telegram_boundary_recorded=True,
        plugin_freeze_recorded=True,
        known_limits_recorded=True,
        operator_decision_recorded=True,
    )

    assert result.to_dict() == {
        "gate_id": "release_decision_bundle",
        "decision": "release_partial",
        "summary": (
            "Mandatory release evidence is recorded, but one or more supporting release "
            "gates still need review before a full release go can be claimed."
        ),
        "next_allowed_actions": [
            "Review remaining release evidence and keep runtime activation disabled.",
            "Confirm operator release decision only after all mandatory gates are recorded.",
            "Keep secrets, payloads, and plugin runtime out of the release evidence bundle.",
        ],
    }


def test_markdown_is_operator_friendly_and_secret_safe():
    result = build_release_decision_bundle(
        provider_fallback_gate_recorded=True,
        test_vault_rebuild_gate_recorded=True,
        graph_memory_gate_recorded=True,
        large_graph_gate_recorded=True,
        telegram_boundary_recorded=True,
        plugin_freeze_recorded=True,
        known_limits_recorded=True,
        operator_decision_recorded=True,
    )

    markdown = result.to_markdown()

    assert "# Release Decision Bundle" in markdown
    assert "release_go" in markdown
    assert "secret" in markdown.lower()
    assert "payloads" in markdown.lower()
