from src.manual_release_evidence import GO, NO_GO, PENDING, ManualEvidenceEntry, current_manual_evidence_entries
from src.manual_release_gap_report import (
    build_current_manual_evidence_gap_report,
    build_manual_evidence_gap_report,
)


def test_current_manual_evidence_has_expected_release_gaps():
    report = build_current_manual_evidence_gap_report()

    assert report.ok is False
    assert tuple(gap.gate_id for gap in report.gaps) == ("provider-proof", "export-import-rebuild")
    assert tuple(gap.status for gap in report.gaps) == ("partial", "partial")
    assert "query-index readiness" in report.gaps[0].next_action
    assert "small disposable test vault" in report.gaps[1].next_action


def test_missing_required_gate_creates_missing_gap():
    report = build_manual_evidence_gap_report([_entry("fresh-install")])

    assert report.ok is False
    assert any(gap.gate_id == "provider-proof" and gap.status == "missing" for gap in report.gaps)


def test_pending_gate_creates_pending_gap():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof", result=PENDING),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    gap = next(gap for gap in report.gaps if gap.gate_id == "provider-proof")
    assert gap.status == "pending"


def test_no_go_wins_for_gap_status():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof"),
            _entry("export-import-rebuild", result=NO_GO, blocker="rebuild lost chunks"),
            _entry("known-limits-review"),
        ]
    )

    gap = next(gap for gap in report.gaps if gap.gate_id == "export-import-rebuild")
    assert gap.status == "no_go"
    assert report.status == "no_go"


def test_all_go_evidence_produces_ok_report_without_gaps():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    assert report.ok is True
    assert report.status == "ok"
    assert report.gaps == ()


def test_to_dict_is_stable():
    report = build_current_manual_evidence_gap_report()

    assert report.to_dict() == {
        "ok": False,
        "status": "partial_no_go",
        "gaps": (
            {
                "gate_id": "provider-proof",
                "status": "partial",
                "label": "Provider Proof",
                "blocker": "query layer not ready for model-backed answer",
                "next_action": "Run provider-proof operator runbook, verify query-index readiness, and avoid logging secrets or provider credentials.",
                "owner": "Charlie/Alice",
                "evidence_ref": "authenticated browser read-only run",
            },
            {
                "gate_id": "export-import-rebuild",
                "status": "partial",
                "label": "Export / Import / Rebuild Proof",
                "blocker": "controlled write run with small test vault is still open",
                "next_action": "Prepare a small disposable test vault with no user artifacts, then run the manual export/import/rebuild proof end-to-end.",
                "owner": "Charlie/Bob",
                "evidence_ref": "authenticated read-only proof plus REL1 tests",
            },
        ),
        "summary": {
            "external_go": False,
            "status": "partial_no_go",
            "missing_gate_ids": (),
            "pending_gate_ids": (),
            "partial_gate_ids": ("provider-proof", "export-import-rebuild"),
            "no_go_gate_ids": (),
            "evidence_refs": (
                r"C:\tmp\odysseus-rel3-fresh-2cea25f",
                r"C:\tmp\odysseus-rel3-upgrade-proof",
                "authenticated browser read-only run",
                "authenticated read-only proof plus REL1 tests",
                "docs/plans/1.0-evidence-release-checklist.md",
            ),
        },
    }


def _entry(gate_id: str, *, result: str = GO, blocker: str = "") -> ManualEvidenceEntry:
    return ManualEvidenceEntry(
        gate_id=gate_id,
        label=gate_id.replace("-", " ").title(),
        result=result,
        commit="abc123",
        evidence_ref=f"evidence:{gate_id}",
        blocker=blocker,
    )
