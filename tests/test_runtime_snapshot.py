from pathlib import Path

from src.runtime_snapshot import build_runtime_snapshot, render_runtime_snapshot, runtime_snapshot_context_message


def test_runtime_snapshot_reads_git_head_and_lists_core_capabilities(tmp_path: Path):
    git = tmp_path / ".git"
    ref = git / "refs" / "heads"
    ref.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
    (ref / "dev").write_text("639017c3abcdef0123456789abcdef0123456789\n", encoding="utf-8")

    snapshot = build_runtime_snapshot(repo_root=tmp_path)

    assert snapshot["commit"] == "639017c3"
    assert snapshot["generated_at"].endswith("Z")
    assert "playwright" in snapshot["sandbox"]
    assert "browser_gui" in snapshot["sandbox"]
    assert snapshot["delegate"] == "read_only_analysis_only"
    assert snapshot["claim_evidence_gate"] == "active_post_stream"
    assert snapshot["artifact_integrity"] == "active_sandbox_logs_and_telegram_images"
    assert snapshot["tool_transaction_ledger"] == "active_agent_metrics_claim_gate"
    assert snapshot["capability_first_gate"] == "active_repo_side_program_screenshot"
    assert snapshot["telegram_run_state"] == "active_repo_side_contract"
    assert snapshot["telegram_screenshot_delivery"] == "active_preview_live_gate_integrity_packet_photo_artifacts"
    assert snapshot["self_knowledge"]["change_source"] == "recent_changes_not_memory"
    assert snapshot["self_knowledge"]["tool_inventory_source"] == "runtime_tool_status_tool_registry_and_tool_index"
    assert snapshot["self_knowledge"]["ask_user_policy"] == "one_clarification_then_act_or_block"
    assert snapshot["self_knowledge"]["memory_boundary"] == "memory_is_not_authoritative_for_runtime_capabilities"
    assert "live_telegram_smoke_requires_operator_go" in snapshot["limits"]


def test_runtime_snapshot_context_is_small_user_context(tmp_path: Path):
    message = runtime_snapshot_context_message(repo_root=tmp_path)

    assert message["role"] == "user"
    assert message["metadata"] == {"source": "runtime_snapshot", "trusted": True}
    assert "## Odysseus runtime snapshot" in message["content"]
    assert len(message["content"].split()) < 180


def test_render_runtime_snapshot_mentions_recent_changes_source():
    text = render_runtime_snapshot(
        {
            "commit": "abc12345",
            "generated_at": "2026-07-05T12:00:00Z",
            "sandbox": ("python", "playwright"),
            "telegram": ("photo_artifact_reply",),
            "delegate": "read_only_analysis_only",
            "claim_evidence_gate": "active_post_stream",
            "artifact_integrity": "active_sandbox_logs_and_telegram_images",
            "tool_transaction_ledger": "active_agent_metrics_claim_gate",
            "capability_first_gate": "active_repo_side_program_screenshot",
            "telegram_run_state": "active_repo_side_contract",
            "telegram_screenshot_delivery": "active_preview_live_gate_integrity_packet_photo_artifacts",
            "self_knowledge": {
                "version_source": "runtime_snapshot_git_commit",
                "change_source": "recent_changes_not_memory",
                "tool_inventory_source": "runtime_tool_status_tool_registry_and_tool_index",
                "capability_probe": "sandbox_and_telegram_partial_mcp_live_gated",
                "evidence_source": "tool_transaction_ledger_and_claim_gate",
                "ask_user_policy": "one_clarification_then_act_or_block",
                "memory_boundary": "memory_is_not_authoritative_for_runtime_capabilities",
            },
            "limits": ("live_actions_still_operator_gated",),
        }
    )

    assert "abc12345" in text
    assert "2026-07-05T12:00:00Z" in text
    assert "photo_artifact_reply" in text
    assert "active_sandbox_logs_and_telegram_images" in text
    assert "active_repo_side_program_screenshot" in text
    assert "active_repo_side_contract" in text
    assert "active_preview_live_gate_integrity_packet_photo_artifacts" in text
    assert "recent_changes_not_memory" in text
    assert "runtime_tool_status_tool_registry_and_tool_index" in text
    assert "one_clarification_then_act_or_block" in text
    assert "use recent_changes" in text
