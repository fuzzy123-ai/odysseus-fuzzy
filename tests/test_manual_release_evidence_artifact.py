from src.manual_release_evidence import GO, ManualEvidenceEntry
from src.manual_release_evidence_artifact import build_manual_release_evidence_artifact
from src.manual_release_gap_report import build_current_manual_evidence_gap_report, build_manual_evidence_gap_report
from src.manual_release_gap_report_digest import manual_release_gap_report_sha256


def test_current_report_artifact_contains_open_gates_in_markdown_and_json():
    report = build_current_manual_evidence_gap_report()
    artifact = build_manual_release_evidence_artifact(report, generated_at="2026-06-17T08:00:00Z")

    assert "provider-proof" in artifact.markdown
    assert '"gate_id": "provider-proof"' in artifact.json
    assert '"gate_id": "export-import-rebuild"' not in artifact.json
    assert artifact.ok is False


def test_sha256_matches_digest_helper():
    report = build_current_manual_evidence_gap_report()
    artifact = build_manual_release_evidence_artifact(report)

    assert artifact.sha256 == manual_release_gap_report_sha256(report)


def test_all_go_artifact_is_ok():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )
    artifact = build_manual_release_evidence_artifact(report, label="All Go Evidence", generated_at="2026-06-17")

    assert artifact.ok is True
    assert "No manual release evidence gaps are currently open." in artifact.markdown
    assert '"ok": true' in artifact.json
    assert artifact.label == "All Go Evidence"
    assert artifact.generated_at == "2026-06-17"


def test_to_dict_is_stable_for_automation():
    artifact = build_manual_release_evidence_artifact(
        build_current_manual_evidence_gap_report(),
        label="Manual Gap Artifact",
        generated_at="2026-06-17T08:30:00Z",
    )

    payload = artifact.to_dict()

    assert payload["label"] == "Manual Gap Artifact"
    assert payload["generated_at"] == "2026-06-17T08:30:00Z"
    assert payload["ok"] is False
    assert payload["sha256"] == artifact.sha256
    assert payload["markdown"] == artifact.markdown
    assert payload["json"] == artifact.json


def _entry(gate_id: str) -> ManualEvidenceEntry:
    return ManualEvidenceEntry(
        gate_id=gate_id,
        label=gate_id.replace("-", " ").title(),
        result=GO,
        commit="abc123",
        evidence_ref=f"evidence:{gate_id}",
    )
