from src.manual_release_evidence import GO, ManualEvidenceEntry
from src.manual_release_gap_report import build_current_manual_evidence_gap_report, build_manual_evidence_gap_report
from src.manual_release_gap_report_json import render_manual_release_gap_report_json


def test_current_report_json_contains_no_open_gate_ids():
    payload = render_manual_release_gap_report_json(build_current_manual_evidence_gap_report())

    assert '"gaps": []' in payload
    assert '"gate_id": "provider-proof"' not in payload
    assert '"gate_id": "export-import-rebuild"' not in payload


def test_all_go_json_contains_ok_true_and_empty_gaps():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    payload = render_manual_release_gap_report_json(report)

    assert '"ok": true' in payload
    assert '"gaps": []' in payload


def test_json_snapshot_is_stable():
    payload = render_manual_release_gap_report_json(build_current_manual_evidence_gap_report())

    assert payload == """{
  "gaps": [],
  "ok": true,
  "status": "ok",
  "summary": {
    "evidence_refs": [
      "C:\\\\tmp\\\\odysseus-rel3-fresh-2cea25f",
      "C:\\\\tmp\\\\odysseus-rel3-upgrade-proof",
      "P1 isolated provider proof run-mpux1ei9",
      "P1 isolated test-vault evidence run-7dyxtze_",
      "docs/plans/1.0-evidence-release-checklist.md"
    ],
    "external_go": true,
    "missing_gate_ids": [],
    "no_go_gate_ids": [],
    "partial_gate_ids": [],
    "pending_gate_ids": [],
    "status": "go"
  }
}"""


def _entry(gate_id: str) -> ManualEvidenceEntry:
    return ManualEvidenceEntry(
        gate_id=gate_id,
        label=gate_id.replace("-", " ").title(),
        result=GO,
        commit="abc123",
        evidence_ref=f"evidence:{gate_id}",
    )
