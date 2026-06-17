from src.release_orchestration_status import ReleaseOrchestrationStatus
from src.release_status_markdown import render_current_release_status_markdown, render_release_status_markdown


def test_render_release_status_markdown_for_blocked_status():
    status = ReleaseOrchestrationStatus(
        status="blocked",
        external_release_go=False,
        active_owners=("Alice", "Bob", "Charlie"),
        parallel_candidate_ids=("REL-test-vault-rebuild-evidence",),
        sequential_gate_ids=("REL-provider-proof-evidence", "REL-partial-manual-evidence-closeout"),
        next_action_ids=(
            "REL-provider-proof-evidence",
            "REL-test-vault-rebuild-evidence",
            "REL-partial-manual-evidence-closeout",
        ),
    )

    markdown = render_release_status_markdown(status)

    assert markdown == "\n".join(
        [
            "# Release Orchestration Status",
            "",
            "- Status: `blocked`",
            "- External release go: `false`",
            "- Active owners: `Alice`, `Bob`, `Charlie`",
            "- Parallel candidates: `REL-test-vault-rebuild-evidence`",
            "- Sequential gates: `REL-provider-proof-evidence`, `REL-partial-manual-evidence-closeout`",
            "- Next actions: `REL-provider-proof-evidence`, `REL-test-vault-rebuild-evidence`, `REL-partial-manual-evidence-closeout`",
        ]
    )


def test_render_release_status_markdown_for_empty_lists():
    status = ReleaseOrchestrationStatus(
        status="go",
        external_release_go=True,
        active_owners=(),
        parallel_candidate_ids=(),
        sequential_gate_ids=(),
        next_action_ids=(),
    )

    markdown = render_release_status_markdown(status)

    assert "- Active owners: `none`" in markdown
    assert "- Parallel candidates: `none`" in markdown
    assert "- Sequential gates: `none`" in markdown
    assert "- Next actions: `none`" in markdown


def test_render_current_release_status_markdown_uses_documented_no_go_state():
    markdown = render_current_release_status_markdown()

    assert "- Status: `blocked`" in markdown
    assert "- External release go: `false`" in markdown
    assert "- Active owners: `Alice`, `Bob`, `Charlie`" in markdown
    assert "- Parallel candidates: `REL-test-vault-rebuild-evidence`" in markdown
