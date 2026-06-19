from src.manual_release_evidence import GO, NO_GO, PENDING, ManualEvidenceEntry, current_manual_evidence_entries
from src.manual_release_gap_report import (
    build_current_manual_evidence_gap_report,
    build_manual_evidence_gap_report,
)


def test_current_manual_evidence_has_expected_release_gaps():
    report = build_current_manual_evidence_gap_report()

    assert report.ok is False
    assert tuple(gap.gate_id for gap in report.gaps) == ("provider-proof",)
    assert tuple(gap.status for gap in report.gaps) == ("partial",)
    assert "query-index readiness" in report.gaps[0].next_action


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
                "blocker": "DeepSeek cloud route returned provider_error; no fallback chain recorded",
                "next_action": "Run provider-proof operator runbook, verify query-index readiness, and avoid logging secrets or provider credentials.",
                "owner": "Charlie/Alice",
                "evidence_ref": "P1 isolated provider proof attempt run-7dyxtze_",
            },
        ),
        "summary": {
            "external_go": False,
            "status": "partial_no_go",
            "missing_gate_ids": (),
            "pending_gate_ids": (),
            "partial_gate_ids": ("provider-proof",),
            "no_go_gate_ids": (),
            "evidence_refs": (
                r"C:\tmp\odysseus-rel3-fresh-2cea25f",
                r"C:\tmp\odysseus-rel3-upgrade-proof",
                "P1 isolated provider proof attempt run-7dyxtze_",
                "P1 isolated test-vault evidence run-7dyxtze_",
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
