from src.manual_release_evidence_readiness_summary import build_current_manual_release_evidence_readiness_summary
from src.manual_release_evidence_readiness_summary_markdown import (
    render_manual_release_evidence_readiness_summary_markdown,
)


def test_current_summary_markdown_shows_ok_and_filenames():
    summary = build_current_manual_release_evidence_readiness_summary(
        label="Morning Evidence",
        generated_at="2026-06-17T11:30:00Z",
    )

    markdown = render_manual_release_evidence_readiness_summary_markdown(summary)

    assert "# Manual Release Evidence Readiness Summary" in markdown
    assert "Status: OK" in markdown
    assert "OK: yes" in markdown
    assert "Open Gate Count: 0" in markdown
    assert f"SHA-256: {summary.sha256}" in markdown
    assert f"Suggested Markdown Filename: {summary.suggested_markdown_filename}" in markdown
    assert f"Suggested JSON Filename: {summary.suggested_json_filename}" in markdown


def test_current_summary_markdown_contains_ok_hint():
    summary = build_current_manual_release_evidence_readiness_summary()

    markdown = render_manual_release_evidence_readiness_summary_markdown(summary)

    assert "All required manual release evidence gates are currently closed." in markdown
    assert "Provider Proof" not in markdown
    assert "Export/Import/Rebuild" not in markdown


def test_readiness_summary_markdown_snapshot_is_stable():
    summary = build_current_manual_release_evidence_readiness_summary(
        label="Morning Evidence",
        generated_at="2026-06-17T11:30:00Z",
    )

    markdown = render_manual_release_evidence_readiness_summary_markdown(summary)

    assert markdown == (
        "# Manual Release Evidence Readiness Summary\n"
        "\n"
            "Status: OK\n"
            "OK: yes\n"
            "Open Gate Count: 0\n"
        f"SHA-256: {summary.sha256}\n"
        f"Suggested Markdown Filename: {summary.suggested_markdown_filename}\n"
        f"Suggested JSON Filename: {summary.suggested_json_filename}\n"
        "\n"
            "All required manual release evidence gates are currently closed."
    )
