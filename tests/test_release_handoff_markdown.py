from src.release_handoff_markdown import render_current_release_handoff_markdown


def test_render_current_release_handoff_markdown_combines_status_and_followups():
    markdown = render_current_release_handoff_markdown()

    assert markdown.startswith("# Release Orchestration Status\n\n")
    assert "# Release Followups" in markdown
    assert "- Status: `blocked`" in markdown
    assert "- External release go: `false`" in markdown
    assert "| `REL-mvp-version-1-gate` | Charlie | no |" in markdown
    assert "| `REL-provider-proof-evidence` |" not in markdown
    assert "| `REL-test-vault-rebuild-evidence` |" not in markdown


def test_render_current_release_handoff_markdown_has_single_separator_between_sections():
    markdown = render_current_release_handoff_markdown()

    assert "\n\n# Release Followups\n\n" in markdown
