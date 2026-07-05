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
    assert "playwright" in snapshot["sandbox"]
    assert "browser_gui" in snapshot["sandbox"]
    assert snapshot["delegate"] == "read_only_analysis_only"
    assert snapshot["claim_evidence_gate"] == "active_post_stream"
    assert snapshot["artifact_integrity"] == "active_sandbox_logs_and_telegram_images"
    assert snapshot["tool_transaction_ledger"] == "active_agent_metrics_claim_gate"
    assert snapshot["capability_first_gate"] == "active_repo_side_program_screenshot"
    assert snapshot["telegram_run_state"] == "active_repo_side_contract"
    assert snapshot["telegram_screenshot_delivery"] == "active_preview_live_gate_integrity_packet_photo_artifacts"
    assert "live_telegram_smoke_requires_operator_go" in snapshot["limits"]


def test_runtime_snapshot_context_is_small_user_context(tmp_path: Path):
    message = runtime_snapshot_context_message(repo_root=tmp_path)

    assert message["role"] == "user"
    assert message["metadata"] == {"source": "runtime_snapshot", "trusted": True}
    assert "## Odysseus runtime snapshot" in message["content"]
    assert len(message["content"].split()) < 130


def test_render_runtime_snapshot_mentions_recent_changes_source():
    text = render_runtime_snapshot(
        {
            "commit": "abc12345",
            "sandbox": ("python", "playwright"),
            "telegram": ("photo_artifact_reply",),
            "delegate": "read_only_analysis_only",
            "claim_evidence_gate": "active_post_stream",
            "artifact_integrity": "active_sandbox_logs_and_telegram_images",
            "tool_transaction_ledger": "active_agent_metrics_claim_gate",
            "capability_first_gate": "active_repo_side_program_screenshot",
            "telegram_run_state": "active_repo_side_contract",
            "telegram_screenshot_delivery": "active_preview_live_gate_integrity_packet_photo_artifacts",
            "limits": ("live_actions_still_operator_gated",),
        }
    )

    assert "abc12345" in text
    assert "photo_artifact_reply" in text
    assert "active_sandbox_logs_and_telegram_images" in text
    assert "active_repo_side_program_screenshot" in text
    assert "active_repo_side_contract" in text
    assert "active_preview_live_gate_integrity_packet_photo_artifacts" in text
    assert "use recent_changes" in text
