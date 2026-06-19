import pytest

from src.manual_release_evidence import (
    GO,
    NO_GO,
    PARTIAL,
    PENDING,
    ManualEvidenceEntry,
    current_manual_evidence_entries,
    summarize_manual_evidence,
)


def test_current_manual_evidence_is_external_go():
    summary = summarize_manual_evidence(current_manual_evidence_entries())

    assert summary.external_go is True
    assert summary.status == "go"
    assert summary.partial_gate_ids == ()
    assert summary.no_go_gate_ids == ()
    assert summary.missing_gate_ids == ()


def test_all_required_go_entries_allow_external_go():
    entries = [
        _entry("fresh-install"),
        _entry("upgrade-path"),
        _entry("provider-proof"),
        _entry("export-import-rebuild"),
        _entry("known-limits-review"),
    ]

    summary = summarize_manual_evidence(entries)

    assert summary.external_go is True
    assert summary.status == "go"


def test_missing_required_gate_keeps_summary_pending():
    summary = summarize_manual_evidence([_entry("fresh-install")])

    assert summary.external_go is False
    assert summary.status == "pending"
    assert "provider-proof" in summary.missing_gate_ids


def test_no_go_wins_over_partial_status():
    summary = summarize_manual_evidence(
        [
            _entry("fresh-install"),
            _entry("provider-proof", result=PARTIAL, blocker="query layer not ready"),
            _entry("export-import-rebuild", result=NO_GO, blocker="data loss observed"),
        ]
    )

    assert summary.status == "no_go"
    assert summary.no_go_gate_ids == ("export-import-rebuild",)
    assert summary.partial_gate_ids == ("provider-proof",)


def test_pending_gate_is_not_external_go():
    summary = summarize_manual_evidence(
        [
            _entry("fresh-install"),
            _entry("provider-proof", result=PENDING),
            _entry("upgrade-path"),
            _entry("export-import-rebuild"),
            _entry("known-limits-review"),
        ]
    )

    assert summary.external_go is False
    assert summary.status == "pending"
    assert summary.pending_gate_ids == ("provider-proof",)


def test_partial_or_no_go_requires_blocker():
    with pytest.raises(ValueError, match="blocker"):
        _entry("provider-proof", result=PARTIAL)

    with pytest.raises(ValueError, match="blocker"):
        _entry("provider-proof", result=NO_GO)


def test_go_requires_commit_and_evidence_ref():
    with pytest.raises(ValueError, match="commit"):
        ManualEvidenceEntry("fresh-install", "Fresh Install", GO, "", "evidence")

    with pytest.raises(ValueError, match="evidence_ref"):
        ManualEvidenceEntry("fresh-install", "Fresh Install", GO, "abc123", "")


def test_summary_to_dict_is_stable():
    summary = summarize_manual_evidence(
        [_entry("fresh-install"), _entry("provider-proof", result=PENDING)],
        required_gate_ids=("fresh-install", "provider-proof"),
    )

    assert summary.to_dict() == {
        "external_go": False,
        "status": "pending",
        "missing_gate_ids": (),
        "pending_gate_ids": ("provider-proof",),
        "partial_gate_ids": (),
        "no_go_gate_ids": (),
        "evidence_refs": ("evidence:fresh-install", "evidence:provider-proof"),
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
