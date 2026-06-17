from src.release_morning_brief import render_current_release_morning_brief, render_release_morning_brief
from src.local_release_readiness_bundle import build_local_release_readiness_bundle


def test_release_morning_brief_combines_handoff_and_artifacts():
    brief = render_release_morning_brief(build_local_release_readiness_bundle())

    assert brief.startswith("# Odysseus Release Morning Brief")
    assert "## Handoff" in brief
    assert "# Release Orchestration Status" in brief
    assert "## Artifact Traceability" in brief
    assert "# Release Artifact Manifest" in brief


def test_current_release_morning_brief_uses_local_bundle():
    brief = render_current_release_morning_brief()

    assert "REL-provider-proof-evidence" in brief
    assert "REL-test-vault-rebuild-evidence" in brief
    assert "docs/plans/1.0-evidence-release-checklist.md" in brief
