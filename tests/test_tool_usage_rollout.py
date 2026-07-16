from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


from scripts.verify_tool_usage_rollout import (
    AGGREGATE_RETENTION_DAYS,
    CAPTURE_DEFAULT_ENABLED,
    EVENT_RETENTION_DAYS,
    LEGACY_BACKFILL_DEFAULT,
    LIVE_GATE_ID,
    ROLLOUT_SCHEMA,
    build_synthetic_rollout_acceptance,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_tool_usage_rollout.py"
PACKET = ROOT / "docs" / "plans" / "tool-usage-live-activation-packet.md"


def _acceptance():
    return build_synthetic_rollout_acceptance()


def test_capture_and_real_backfill_are_default_off():
    report = _acceptance()

    assert CAPTURE_DEFAULT_ENABLED is False
    assert LEGACY_BACKFILL_DEFAULT == "no"
    assert report["checks"]["capture_default_off"] is True
    assert report["checks"]["real_backfill_default_no"] is True
    assert report["live_contract"]["capture_enabled"] is False
    assert report["live_contract"]["real_backfill_authorized"] is False


def test_synthetic_off_on_incognito_off_sequence_is_exact():
    report = _acceptance()

    assert report["status"] == "passed"
    assert report["sequence"] == ("off", "on", "incognito", "off")
    assert report["counts"] == {
        "synthetic_invocations": 6,
        "captured_invocations": 1,
        "captured_events": 2,
        "incognito_writes": 0,
        "post_rollback_writes": 0,
        "production_writes": 0,
        "external_exports": 0,
    }
    assert report["checks"]["off_before_writes_zero"] is True
    assert report["checks"]["synthetic_on_emits_one_pair"] is True
    assert report["checks"]["incognito_no_write"] is True
    assert report["checks"]["rollback_stops_new_writes"] is True


def test_writer_store_failure_and_rollback_preserve_tool_and_safe_statistics():
    report = _acceptance()

    assert report["checks"]["tool_result_identity_preserved"] is True
    assert report["checks"]["writer_failure_isolated"] is True
    assert report["checks"]["store_failure_isolated"] is True
    assert report["checks"]["safe_statistics_preserved"] is True
    assert report["safe_statistics"] == {
        "invocations": 1,
        "events": 2,
        "terminal_invocations": 1,
        "coverage_percent": 100,
    }


def test_retention_and_aggregate_simulation_is_non_mutating():
    report = _acceptance()

    assert EVENT_RETENTION_DAYS == 90
    assert AGGREGATE_RETENTION_DAYS == 400
    assert report["checks"]["retention_is_dry_run"] is True
    assert report["retention_simulation"] == {
        "dry_run": True,
        "event_retention_days": 90,
        "aggregate_retention_days": 400,
        "eligible_event_count": 0,
        "eligible_aggregate_count": 0,
        "deleted_event_count": 0,
        "deleted_aggregate_count": 0,
    }


def test_machine_report_is_redacted_and_does_not_authorize_live_actions():
    report = _acceptance()
    rendered = json.dumps(report, sort_keys=True).casefold()

    assert report["schema_version"] == ROLLOUT_SCHEMA
    assert report["checks"]["diagnostics_redacted"] is True
    assert report["diagnostics"] == {
        "aggregate_only": True,
        "raw_content_visible": False,
        "direct_identifiers_visible": False,
        "exception_details_visible": False,
        "private_paths_visible": False,
    }
    assert report["live_contract"] == {
        "gate_id": LIVE_GATE_ID,
        "materialization_ready": True,
        "activation_authorized": False,
        "capture_enabled": False,
        "real_backfill_authorized": False,
        "legacy_backfill_default": "no",
        "external_export_enabled": False,
        "deployment_performed": False,
        "restart_performed": False,
    }
    for marker in (
        "c:\\",
        "/home/",
        "/users/",
        "bearer ",
        "authorization:",
        "token=",
        "password=",
        "h1_owner_",
        "h1_session_",
    ):
        assert marker not in rendered


def test_required_cli_emits_machine_readable_pass():
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "synthetic",
            "--assert-default-off",
            "--assert-incognito-no-write",
            "--assert-rollback",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert report["live_contract"]["activation_authorized"] is False
    assert report["counts"]["production_writes"] == 0


def test_cli_rejects_incomplete_or_live_shaped_invocations():
    missing_assertion = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "synthetic",
            "--assert-default-off",
            "--assert-rollback",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    live_attempt = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "live",
            "--assert-default-off",
            "--assert-incognito-no-write",
            "--assert-rollback",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert missing_assertion.returncode != 0
    assert live_attempt.returncode != 0


def test_activation_packet_is_complete_default_off_and_secret_free():
    packet = PACKET.read_text(encoding="utf-8")
    lowered = packet.casefold()

    for heading in (
        "## Version And Environment Template",
        "## Retention And Admin Scope",
        "## Optional Legacy Backfill",
        "## Pre-Activation Checks",
        "## Monitoring And Budgets",
        "## Abort Criteria",
        "## Rollback",
        "## Dormant GO Template",
    ):
        assert heading in packet
    assert "capture default: `off`" in lowered
    assert "historical backfill default: `no`" in lowered
    assert LIVE_GATE_ID.casefold() in lowered
    assert "activation_authorized=false" in lowered
    for marker in (
        "c:\\",
        "/home/",
        "/users/",
        "bearer ",
        "authorization:",
        "token=",
        "password=",
        "sk-",
    ):
        assert marker not in lowered
