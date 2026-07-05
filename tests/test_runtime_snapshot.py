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


def test_runtime_snapshot_context_is_small_user_context(tmp_path: Path):
    message = runtime_snapshot_context_message(repo_root=tmp_path)

    assert message["role"] == "user"
    assert message["metadata"] == {"source": "runtime_snapshot", "trusted": True}
    assert "## Odysseus runtime snapshot" in message["content"]
    assert len(message["content"].split()) < 90


def test_render_runtime_snapshot_mentions_recent_changes_source():
    text = render_runtime_snapshot(
        {
            "commit": "abc12345",
            "sandbox": ("python", "playwright"),
            "telegram": ("photo_artifact_reply",),
            "delegate": "read_only_analysis_only",
            "claim_evidence_gate": "active_post_stream",
            "artifact_integrity": "active_sandbox_logs_and_telegram_images",
            "limits": ("live_actions_still_operator_gated",),
        }
    )

    assert "abc12345" in text
    assert "photo_artifact_reply" in text
    assert "active_sandbox_logs_and_telegram_images" in text
    assert "use recent_changes" in text
