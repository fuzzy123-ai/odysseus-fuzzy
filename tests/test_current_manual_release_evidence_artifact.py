from src.current_manual_release_evidence_artifact import (
    build_current_manual_release_evidence_artifact,
    render_current_manual_release_evidence_artifact_markdown,
)


def test_current_manual_release_evidence_artifact_stays_not_ok_with_open_gates():
    artifact = build_current_manual_release_evidence_artifact(
        label="Morning Evidence",
        generated_at="2026-06-17T10:00:00Z",
    )

    assert artifact.ok is False
    assert "provider-proof" in artifact.markdown
    assert '"gate_id": "provider-proof"' in artifact.json
    assert '"gate_id": "export-import-rebuild"' not in artifact.json


def test_current_manual_release_evidence_artifact_has_stable_sha256():
    artifact_a = build_current_manual_release_evidence_artifact()
    artifact_b = build_current_manual_release_evidence_artifact()

    assert artifact_a.sha256 == artifact_b.sha256
    assert len(artifact_a.sha256) == 64


def test_current_manual_release_evidence_artifact_markdown_renderer_includes_metadata():
    markdown = render_current_manual_release_evidence_artifact_markdown(
        label="Morning Evidence",
        generated_at="2026-06-17T10:00:00Z",
    )

    assert "# Manual Release Evidence Artifact" in markdown
    assert "Label: Morning Evidence" in markdown
    assert "Generated At: 2026-06-17T10:00:00Z" in markdown
    assert "Status: NO_GO" in markdown
    assert "provider-proof" in markdown
    assert "export-import-rebuild" not in markdown
