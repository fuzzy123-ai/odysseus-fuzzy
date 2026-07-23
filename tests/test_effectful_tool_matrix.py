from src.agent_loop_orchestration import _VERIFIER_EFFECTFUL_TOOLS, _build_actions_snapshot
from src.effectful_tool_matrix import build_effectful_action_snapshot, effectful_tool_names, tool_effect_category


def test_effectful_matrix_includes_modern_side_effects():
    names = set(effectful_tool_names())

    assert {"telegram_document_reply", "browser_screenshot", "git_commit", "commit_project", "sandbox_submit", "bash"} <= names
    assert _VERIFIER_EFFECTFUL_TOOLS >= names
    assert tool_effect_category("telegram_photo") == "telegram_outbound"
    assert tool_effect_category("commit_project") == "project_versioning"


def test_mixed_admin_tools_classify_read_and_mutation_actions_separately():
    assert tool_effect_category("manage_plugins", "list") == ""
    assert tool_effect_category("manage_plugins", "enable") == "plugin_state"
    assert tool_effect_category("manage_settings", "get") == ""
    assert tool_effect_category("manage_settings", "set") == "settings_write"
    assert tool_effect_category("manage_tokens", "list") == ""
    assert tool_effect_category("manage_tokens", "revoke") == "token_state"
    assert tool_effect_category("manage_repos", "status") == ""
    assert tool_effect_category("manage_repos", "update_policy") == "repo_registry_write"
    assert tool_effect_category("manage_repos", "unknown_future_action") == "repo_registry_write"


def test_snapshot_uses_content_free_action_for_mixed_tool_effects():
    snapshot = build_effectful_action_snapshot(
        [
            {"tool": "manage_settings", "action": "get", "exit_code": 0},
            {"tool": "manage_settings", "action": "set", "exit_code": 0},
        ]
    )

    assert snapshot["effectful_count"] == 1
    assert snapshot["categories"] == ("settings_write",)


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
