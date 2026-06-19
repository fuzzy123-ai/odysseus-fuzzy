from pathlib import Path

import pytest

from src.odysseus_updater_audit import (
    build_odysseus_updater_audit_record,
    redact_audit_text,
)


def test_audit_record_is_persistable_and_handoff_safe():
    record = build_odysseus_updater_audit_record(
        plan_id="upd6.audit.01",
        source_ref="origin/main",
        current_ref="aabb234",
        target_ref="def5678",
        gate_statuses={
            "scope_confirmed": "pass",
            "offline_slice_confirmed": "pass",
            "tests_defined": "pending",
        },
        operator_decision="deferred",
        started_at="2026-06-19T10:00:00Z",
        completed_at="2026-06-19T10:05:00Z",
        result="pending",
        rollback_or_hold_note="Hold until operator review confirms next safe step.",
        redacted_notes=(
            "Bearer <redacted-test-sentinel>",
            "Absolute path C:\\Users\\nkatz\\odysseus\\secrets.txt must not persist.",
        ),
    )

    payload = record.to_dict()

    assert payload["plan_id"] == "upd6.audit.01"
    assert payload["operator_decision"] == "deferred"
    assert payload["result"] == "pending"
    assert payload["gate_statuses"]["tests_defined"] == "pending"
    assert payload["started_at"] == "2026-06-19T10:00:00+00:00"
    assert payload["completed_at"] == "2026-06-19T10:05:00+00:00"
    assert "<redacted-test-sentinel>" not in record.to_json()
    assert "C:\\Users\\nkatz\\odysseus\\secrets.txt" not in record.to_json()
    assert "[redacted]" in payload["redacted_notes"][0]
    assert "[redacted-path]" in payload["redacted_notes"][1]
    assert "decision=deferred" in record.to_handoff_quote()
    assert "gates=offline_slice_confirmed=pass, scope_confirmed=pass, tests_defined=pending" in record.to_handoff_quote()


def test_audit_record_redacts_secret_url_and_path_patterns():
    note = (
        "api_key=<redacted-test-sentinel> authorization=Bearer <redacted-test-sentinel> "
        "Bearer <redacted-test-sentinel> https://private.example.invalid/x "
        "/var/lib/private/config.json C:\\secure\\private\\file.txt"
    )

    redacted = redact_audit_text(note)

    assert "<redacted-test-sentinel>" not in redacted
    assert "private.example.invalid" not in redacted
    assert "/var/lib/private/config.json" not in redacted
    assert "C:\\secure\\private\\file.txt" not in redacted
    assert redacted.count("[redacted]") >= 3
    assert "[redacted-url]" in redacted
    assert "[redacted-path]" in redacted


def test_audit_record_sanitizes_gate_status_values_and_notes():
    record = build_odysseus_updater_audit_record(
        plan_id="upd6.audit.02",
        source_ref="origin/release",
        current_ref="111aaaa",
        target_ref="222bbbb",
        gate_statuses={
            "provider_output_reviewed": "fail because bearer secret-token appeared in https://internal.invalid/report",
            "tests_defined": "pass",
        },
        operator_decision="hold",
        result="held",
        rollback_or_hold_note="See /srv/private/logs/update.log before resume.",
        redacted_notes=["api_key=<redacted-test-sentinel>", "api_key=<redacted-test-sentinel>"],
    )

    assert record.gate_statuses["provider_output_reviewed"] == "fail because Bearer [redacted] appeared in [redacted-url]"
    assert record.rollback_or_hold_note == "See [redacted-path] before resume."
    assert record.redacted_notes == ("api_key=[redacted]",)


def test_audit_record_rejects_invalid_decisions_and_empty_gate_statuses():
    with pytest.raises(ValueError, match="unsupported operator_decision"):
        build_odysseus_updater_audit_record(
            plan_id="upd6.audit.03",
            source_ref="origin/main",
            current_ref="333cccc",
            target_ref="444dddd",
            gate_statuses={"scope_confirmed": "pass"},
            operator_decision="approve",
            result="applied",
        )

    with pytest.raises(ValueError, match="gate_statuses must not be empty"):
        build_odysseus_updater_audit_record(
            plan_id="upd6.audit.04",
            source_ref="origin/main",
            current_ref="333cccc",
            target_ref="444dddd",
            gate_statuses={},
            operator_decision="go",
            result="applied",
        )


def test_audit_record_round_trips_from_dict_without_secrets():
    payload = {
        "plan_id": "upd6.audit.05",
        "source_ref": "origin/main",
        "current_ref": "555eeee",
        "target_ref": "666ffff",
        "gate_statuses": {
            "scope_confirmed": "pass",
            "tests_defined": "pass",
        },
        "operator_decision": "go",
        "started_at": "2026-06-19T12:00:00+00:00",
        "completed_at": "2026-06-19T12:03:00+00:00",
        "result": "applied",
        "rollback_or_hold_note": "No rollback needed.",
        "redacted_notes": [
            "authorization=Bearer <redacted-test-sentinel>",
        ],
    }

    record = build_odysseus_updater_audit_record(**payload)
    cloned = type(record).from_dict(record.to_dict())

    assert cloned.to_dict() == record.to_dict()
    assert "<redacted-test-sentinel>" not in cloned.to_json()
    assert cloned.redacted_notes == ("authorization=[redacted]",)


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_audit.py").read_text(encoding="utf-8")

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
