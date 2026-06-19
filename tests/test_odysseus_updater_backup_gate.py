from pathlib import Path

import pytest

from src.odysseus_updater_backup_gate import build_odysseus_updater_backup_gate


def _full_green_evidence():
    return (
        {
            "evidence_id": "pre_update_snapshot",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-19T08:00:00Z",
            "summary": "Pre-update snapshot metadata is present and structurally validated.",
        },
        {
            "evidence_id": "repository_check",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-19T08:05:00Z",
            "summary": "Repository check evidence confirms an offline-validated clean result.",
        },
        {
            "evidence_id": "restore_smoke",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-19T08:10:00Z",
            "summary": "Restore smoke evidence shows a successful offline dry validation record.",
        },
    )


def test_ready_when_all_required_evidence_is_green():
    report = build_odysseus_updater_backup_gate(
        risk_level="medium",
        evaluated_at="2026-06-19T08:30:00Z",
        evidence_inputs=_full_green_evidence(),
    )

    assert report.status == "ready"
    assert report.deployment_decision == "go"
    assert report.blockers == ()
    assert report.to_compact_report() == {
        "risk_level": "medium",
        "status": "ready",
        "deployment_decision": "go",
        "evidence_labels": {
            "pre_update_snapshot": "pass",
            "repository_check": "pass",
            "restore_smoke": "pass",
        },
        "evidence_states": {
            "pre_update_snapshot": "green",
            "repository_check": "green",
            "restore_smoke": "green",
        },
        "blockers": [],
        "next_actions": [
            "Proceed with deployment review because all required backup evidence is green."
        ],
    }
    packet = report.to_evidence_packet()
    assert packet["feature"] == "homeserver_backup_gate"
    assert packet["secret_values_visible"] is False
    assert packet["host_output_visible"] is False
    assert "blocker_reason" not in packet["required_evidence"][0]


def test_missing_restore_smoke_is_partial_for_low_risk():
    report = build_odysseus_updater_backup_gate(
        risk_level="low",
        evaluated_at="2026-06-19T09:00:00Z",
        evidence_inputs=_full_green_evidence()[:2],
    )

    assert report.status == "partial"
    assert report.deployment_decision == "deferred"
    assert report.evidence[-1].evidence_id == "restore_smoke"
    assert report.evidence[-1].result_label == "partial"
    assert "restore smoke evidence still needs a structured offline record" not in report.blockers
    assert any("restore_smoke" in action for action in report.next_actions)


def test_missing_restore_smoke_is_blocked_for_high_risk():
    report = build_odysseus_updater_backup_gate(
        risk_level="critical",
        evaluated_at="2026-06-19T09:30:00Z",
        evidence_inputs=_full_green_evidence()[:2],
    )

    assert report.status == "blocked"
    assert report.deployment_decision == "no_go"
    assert report.evidence[-1].result_label == "blocked"
    assert report.blockers == (
        "restore smoke evidence is required before high-risk deployment review",
    )


def test_failed_repository_check_blocks_deployment():
    report = build_odysseus_updater_backup_gate(
        risk_level="medium",
        evaluated_at="2026-06-19T10:00:00Z",
        evidence_inputs=(
            _full_green_evidence()[0],
            {
                "evidence_id": "repository_check",
                "state": "red",
                "result_label": "fail",
                "checked_at": "2026-06-19T09:55:00Z",
                "summary": "Repository check evidence captured a failed integrity validation result.",
                "blocker_reason": "repository check evidence must be green before updater promotion",
            },
            _full_green_evidence()[2],
        ),
    )

    assert report.status == "blocked"
    assert report.deployment_decision == "no_go"
    assert "repository check evidence must be green before updater promotion" in report.blockers


def test_pending_evidence_defers_review():
    report = build_odysseus_updater_backup_gate(
        risk_level="medium",
        evaluated_at="2026-06-19T10:30:00Z",
        evidence_inputs=(
            _full_green_evidence()[0],
            {
                "evidence_id": "repository_check",
                "state": "pending",
                "result_label": "pending",
                "checked_at": "2026-06-19T10:25:00Z",
                "summary": "Repository check evidence has been requested but not yet validated.",
            },
            _full_green_evidence()[2],
        ),
    )

    assert report.status == "deferred"
    assert report.deployment_decision == "deferred"
    assert report.blockers == ()


def test_rejects_duplicate_evidence_ids():
    with pytest.raises(ValueError, match="duplicate evidence_id"):
        build_odysseus_updater_backup_gate(
            risk_level="medium",
            evaluated_at="2026-06-19T11:00:00Z",
            evidence_inputs=(
                _full_green_evidence()[0],
                _full_green_evidence()[0],
                _full_green_evidence()[2],
            ),
        )


def test_rejects_invalid_timestamp():
    with pytest.raises(ValueError, match="evaluated_at must be a valid ISO-8601 timestamp"):
        build_odysseus_updater_backup_gate(
            risk_level="medium",
            evaluated_at="not-a-timestamp",
            evidence_inputs=_full_green_evidence(),
        )


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_backup_gate.py").read_text(encoding="utf-8")

    forbidden_fragments = (
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import telegram",
        "from telegram",
        "import nextcloud",
        "from nextcloud",
        "import git",
        "from git",
        ".run(",
        "os.system",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_evidence_packet_is_safe_to_persist_when_blocked():
    report = build_odysseus_updater_backup_gate(
        risk_level="critical",
        evaluated_at="2026-06-19T12:00:00Z",
        evidence_inputs=_full_green_evidence()[:2],
    )

    packet = report.to_evidence_packet()
    encoded = str(packet).lower()

    assert packet["deployment_decision"] == "no_go"
    assert packet["secret_values_visible"] is False
    assert packet["host_output_visible"] is False
    assert "password" not in encoded
    assert "token" not in encoded
    assert "chat_id" not in encoded
