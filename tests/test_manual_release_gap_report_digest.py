from src.manual_release_evidence import GO, NO_GO, ManualEvidenceEntry
from src.manual_release_gap_report import build_current_manual_evidence_gap_report, build_manual_evidence_gap_report
from src.manual_release_gap_report_digest import (
    canonical_manual_release_gap_report_bytes,
    canonical_manual_release_gap_report_json,
    manual_release_gap_report_sha256,
)


def test_digest_is_deterministic_for_current_report():
    report = build_current_manual_evidence_gap_report()

    digest_a = manual_release_gap_report_sha256(report)
    digest_b = manual_release_gap_report_sha256(report)

    assert digest_a == digest_b
    assert len(digest_a) == 64


def test_digest_changes_when_report_content_changes():
    current_report = build_current_manual_evidence_gap_report()
    all_go_report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    assert manual_release_gap_report_sha256(current_report) != manual_release_gap_report_sha256(all_go_report)


def test_canonical_json_is_stably_sorted():
    report = build_manual_evidence_gap_report(
        [
            _entry("fresh-install"),
            _entry("upgrade-path"),
            _entry("provider-proof", result=NO_GO, blocker="provider call failed"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    payload = canonical_manual_release_gap_report_json(report)

    assert payload.startswith('{"gaps":')
    assert '"ok":false' in payload
    assert '"status":"no_go"' in payload


def test_canonical_bytes_are_utf8_of_canonical_json():
    report = build_current_manual_evidence_gap_report()

    assert canonical_manual_release_gap_report_bytes(report) == canonical_manual_release_gap_report_json(report).encode(
        "utf-8"
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
