import pytest

from src.release_evidence_snapshot import (
    AUTOMATED,
    BLOCKED,
    MANUAL,
    PASS,
    PENDING,
    WARN,
    ReleaseGate,
    build_release_evidence_snapshot,
    default_1_0_release_gates,
)


def test_default_1_0_snapshot_is_manual_pending_not_external_go():
    snapshot = build_release_evidence_snapshot(default_1_0_release_gates())

    assert snapshot.status == "manual_pending"
    assert snapshot.automated_ok is True
    assert snapshot.manual_ok is False
    assert snapshot.external_release_go is False
    assert snapshot.pending_manual_gate_ids == ("provider-proof", "export-import-rebuild")


def test_all_required_gates_pass_means_go():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("manual", "Manual", MANUAL, PASS, ("runbook",)),
        ]
    )

    assert snapshot.status == "go"
    assert snapshot.external_release_go is True
    assert snapshot.blocking_gate_ids == ()


def test_blocked_gate_wins_over_pending_manual():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, BLOCKED, risk="red tests"),
            ReleaseGate("manual", "Manual", MANUAL, PENDING, risk="needs user proof"),
        ]
    )

    assert snapshot.status == "blocked"
    assert snapshot.external_release_go is False
    assert snapshot.blocking_gate_ids == ("auto",)
    assert snapshot.pending_manual_gate_ids == ("manual",)


def test_warning_keeps_release_go_when_no_required_blocker():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("warn", "Known warning", AUTOMATED, WARN, required_for_external_release=False),
            ReleaseGate("manual", "Manual", MANUAL, PASS, ("manual log",)),
        ]
    )

    assert snapshot.status == "go_with_warnings"
    assert snapshot.external_release_go is True
    assert snapshot.warning_gate_ids == ("warn",)


def test_evidence_refs_are_deduplicated_in_order():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("a", "A", AUTOMATED, PASS, ("pytest", "runbook")),
            ReleaseGate("b", "B", MANUAL, PASS, ("pytest", "manual")),
        ]
    )

    assert snapshot.evidence_refs == ("pytest", "runbook", "manual")


def test_required_pass_gate_requires_evidence():
    with pytest.raises(ValueError, match="evidence_refs"):
        ReleaseGate("manual", "Manual", MANUAL, PASS)


def test_snapshot_to_dict_is_stable():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("manual", "Manual", MANUAL, PENDING, risk="needs evidence"),
        ]
    )

    assert snapshot.to_dict() == {
        "status": "manual_pending",
        "external_release_go": False,
        "automated_ok": True,
        "manual_ok": False,
        "blocking_gate_ids": (),
        "pending_manual_gate_ids": ("manual",),
        "warning_gate_ids": (),
        "evidence_refs": ("pytest",),
        "gate_count": 2,
    }
