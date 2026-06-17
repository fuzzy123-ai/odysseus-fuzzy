from src.manual_release_evidence import GO, ManualEvidenceEntry
from src.manual_release_evidence_artifact import build_manual_release_evidence_artifact
from src.manual_release_evidence_artifact_markdown import render_manual_release_evidence_artifact_markdown
from src.manual_release_gap_report import build_current_manual_evidence_gap_report, build_manual_evidence_gap_report


def test_current_artifact_markdown_contains_metadata_and_open_gaps():
    artifact = build_manual_release_evidence_artifact(
        build_current_manual_evidence_gap_report(),
        label="Morning Manual Evidence",
        generated_at="2026-06-17T09:00:00Z",
    )

    markdown = render_manual_release_evidence_artifact_markdown(artifact)

    assert "# Manual Release Evidence Artifact" in markdown
    assert "Label: Morning Manual Evidence" in markdown
    assert "Generated At: 2026-06-17T09:00:00Z" in markdown
    assert "Status: NO_GO" in markdown
    assert "SHA-256:" in markdown
    assert "provider-proof" in markdown
    assert "export-import-rebuild" in markdown


def test_all_go_artifact_markdown_contains_ok_hint():
    artifact = build_manual_release_evidence_artifact(
        build_manual_evidence_gap_report(
            [
                _entry("fresh-install"),
                _entry("upgrade-path"),
                _entry("provider-proof"),
                _entry("export-import-rebuild"),
                _entry("known-limits-review"),
            ]
        ),
        label="All Go",
        generated_at="2026-06-17",
    )

    markdown = render_manual_release_evidence_artifact_markdown(artifact)

    assert "Status: OK" in markdown
    assert "No manual release evidence gaps are currently open." in markdown


def test_artifact_markdown_snapshot_is_stable():
    artifact = build_manual_release_evidence_artifact(
        build_current_manual_evidence_gap_report(),
        label="Morning Manual Evidence",
        generated_at="2026-06-17T09:00:00Z",
    )

    markdown = render_manual_release_evidence_artifact_markdown(artifact)

    assert markdown == (
        "# Manual Release Evidence Artifact\n"
        "\n"
        "Label: Morning Manual Evidence\n"
        "Generated At: 2026-06-17T09:00:00Z\n"
        "Status: NO_GO\n"
        f"SHA-256: {artifact.sha256}\n"
        "\n"
        "## Gap Report\n"
        "\n"
        "# Manual Release Evidence Gaps\n"
        "\n"
        "Status: PARTIAL\n"
        "\n"
        "Open manual evidence gaps: 2\n"
        "\n"
        "| Gate | Status | Label | Blocker | Next Action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| provider-proof | PARTIAL | Provider Proof | query layer not ready for model-backed answer | Run provider-proof operator runbook, verify query-index readiness, and avoid logging secrets or provider credentials. | Charlie/Alice |\n"
        "| export-import-rebuild | PARTIAL | Export / Import / Rebuild Proof | controlled write run with small test vault is still open | Prepare a small disposable test vault with no user artifacts, then run the manual export/import/rebuild proof end-to-end. | Charlie/Bob |"
    )


def _entry(gate_id: str) -> ManualEvidenceEntry:
    return ManualEvidenceEntry(
        gate_id=gate_id,
        label=gate_id.replace("-", " ").title(),
        result=GO,
        commit="abc123",
        evidence_ref=f"evidence:{gate_id}",
    )
