from src.manual_release_evidence import GO, NO_GO, PENDING, ManualEvidenceEntry
from src.manual_release_gap_report import build_current_manual_evidence_gap_report, build_manual_evidence_gap_report
from src.manual_release_gap_report_markdown import render_manual_release_gap_report_markdown


def test_current_manual_evidence_markdown_contains_expected_open_gaps():
    markdown = render_manual_release_gap_report_markdown(build_current_manual_evidence_gap_report())

    assert markdown.startswith("# Manual Release Evidence Gaps")
    assert "Status: PARTIAL" in markdown
    assert "provider-proof" in markdown
    assert "Provider Proof" in markdown
    assert "export-import-rebuild" not in markdown
    assert "Export / Import / Rebuild Proof" not in markdown


def test_all_go_markdown_contains_no_gaps_message():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    markdown = render_manual_release_gap_report_markdown(report)

    assert "Status: OK" in markdown
    assert "No manual release evidence gaps are currently open." in markdown
    assert "All required manual 1.0 evidence gates are marked as go." in markdown


def test_pending_missing_and_no_go_are_visible_in_markdown():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof", result=PENDING),
            _entry("export-import-rebuild", result=NO_GO, blocker="rebuild failed"),
        ]
    )

    markdown = render_manual_release_gap_report_markdown(report)

    assert "Status: BLOCKED" in markdown
    assert "| provider-proof | PENDING |" in markdown
    assert "| export-import-rebuild | NO_GO |" in markdown
    assert "| known-limits-review | MISSING |" in markdown


def test_markdown_snapshot_is_stable():
    markdown = render_manual_release_gap_report_markdown(build_current_manual_evidence_gap_report())

    assert markdown == (
        "# Manual Release Evidence Gaps\n"
        "\n"
        "Status: PARTIAL\n"
        "\n"
        "Open manual evidence gaps: 1\n"
        "\n"
        "| Gate | Status | Label | Blocker | Next Action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| provider-proof | PARTIAL | Provider Proof | DeepSeek cloud route returned provider_error; no fallback chain recorded | Run provider-proof operator runbook, verify query-index readiness, and avoid logging secrets or provider credentials. | Charlie/Alice |"
    )


def _entry(gate_id: str, *, result: str = GO, blocker: str = "") -> ManualEvidenceEntry:
    return ManualEvidenceEntry(
        gate_id=gate_id,
        label=gate_id.replace("-", " ").title(),
        result=result,
        commit="abc123",
        evidence_ref=f"evidence:{gate_id}",
        blocker=blocker,
    )
