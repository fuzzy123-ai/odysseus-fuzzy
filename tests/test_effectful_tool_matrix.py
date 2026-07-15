from src.agent_loop_orchestration import _VERIFIER_EFFECTFUL_TOOLS, _build_actions_snapshot
from src.effectful_tool_matrix import build_effectful_action_snapshot, effectful_tool_names, tool_effect_category


def test_effectful_matrix_includes_modern_side_effects():
    names = set(effectful_tool_names())

    assert {"telegram_document_reply", "browser_screenshot", "git_commit", "sandbox_submit", "bash"} <= names
    assert _VERIFIER_EFFECTFUL_TOOLS >= names
    assert tool_effect_category("telegram_photo") == "telegram_outbound"


def test_effectful_matrix_covers_sensitive_catalog_control_families():
    assert tool_effect_category("manage_plugins") == "plugin_supply_chain_control"
    assert tool_effect_category("manage_settings") == "settings_control"
    assert tool_effect_category("manage_tokens") == "credential_control"
    assert tool_effect_category("manage_repos") == "repository_state_control"
    assert tool_effect_category("manage_embeddings") == "embedding_runtime_control"
    assert tool_effect_category("manage_personal_docs") == "document_source_control"
    for tool_id in (
        "download_model",
        "serve_model",
        "serve_preset",
        "stop_served_model",
        "cancel_download",
        "adopt_served_model",
    ):
        assert tool_effect_category(tool_id) == "model_runtime_control"
    assert tool_effect_category("tail_serve_output") == ""


def test_effectful_snapshot_keeps_failed_transaction_failed_and_redacted():
    snapshot = build_effectful_action_snapshot(
        [
            {
                "tool": "browser_screenshot",
                "command": "screenshot",
                "output": "failed to capture data/reports/pong/screen.png",
                "exit_code": 1,
                "artifact_refs": ["data/reports/pong/screen.png"],
            }
        ]
    )

    tx = snapshot["transactions"][0]
    assert snapshot["effectful_count"] == 1
    assert "browser_automation" in snapshot["categories"]
    assert tx["status"] == "failed"
    assert tx["verified_done"] is False
    assert snapshot["raw_content_visible"] is False
    assert "failed to capture" not in repr(snapshot)


def test_actions_snapshot_contains_machine_evidence_for_external_review():
    text = _build_actions_snapshot(
        [
            {
                "tool": "telegram_document_reply",
                "command": "send photo",
                "output": "sent ok data/reports/pong/screen.png",
                "exit_code": 0,
                "artifact_refs": ["data/reports/pong/screen.png"],
            }
        ]
    )

    assert "[machine_evidence]" in text
    assert '"transaction_status"' in text
    assert "data/reports/pong/screen.png" in text
    assert '"raw_content_visible": false' in text
