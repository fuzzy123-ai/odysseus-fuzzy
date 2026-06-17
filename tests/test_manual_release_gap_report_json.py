from src.manual_release_evidence import GO, ManualEvidenceEntry
from src.manual_release_gap_report import build_current_manual_evidence_gap_report, build_manual_evidence_gap_report
from src.manual_release_gap_report_json import render_manual_release_gap_report_json


def test_current_report_json_contains_open_gate_ids():
    payload = render_manual_release_gap_report_json(build_current_manual_evidence_gap_report())

    assert '"gate_id": "provider-proof"' in payload
    assert '"gate_id": "export-import-rebuild"' in payload


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
  "gaps": [
    {
      "blocker": "query layer not ready for model-backed answer",
      "evidence_ref": "authenticated browser read-only run",
      "gate_id": "provider-proof",
      "label": "Provider Proof",
      "next_action": "Run provider-proof operator runbook, verify query-index readiness, and avoid logging secrets or provider credentials.",
      "owner": "Charlie/Alice",
      "status": "partial"
    },
    {
      "blocker": "controlled write run with small test vault is still open",
      "evidence_ref": "authenticated read-only proof plus REL1 tests",
      "gate_id": "export-import-rebuild",
      "label": "Export / Import / Rebuild Proof",
      "next_action": "Prepare a small disposable test vault with no user artifacts, then run the manual export/import/rebuild proof end-to-end.",
      "owner": "Charlie/Bob",
      "status": "partial"
    }
  ],
  "ok": false,
  "status": "partial_no_go",
  "summary": {
    "evidence_refs": [
      "C:\\\\tmp\\\\odysseus-rel3-fresh-2cea25f",
      "C:\\\\tmp\\\\odysseus-rel3-upgrade-proof",
      "authenticated browser read-only run",
      "authenticated read-only proof plus REL1 tests",
      "docs/plans/1.0-evidence-release-checklist.md"
    ],
    "external_go": false,
    "missing_gate_ids": [],
    "no_go_gate_ids": [],
    "partial_gate_ids": [
      "provider-proof",
      "export-import-rebuild"
    ],
    "pending_gate_ids": [],
    "status": "partial_no_go"
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
