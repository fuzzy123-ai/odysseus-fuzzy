from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.builtin_tool_catalog import build_builtin_analytics_identity_contract
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_usage_backfill import (
    SYNTHETIC_PRIMARY_SOURCE,
    ToolUsageBackfillError,
    preview_tool_usage_backfill,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "tool_usage"
PRIMARY_FIXTURE = FIXTURE_ROOT / "synthetic_primary.jsonl"
SCRIPT = ROOT / "scripts" / "backfill_tool_usage.py"
UNSAFE_MARKERS = (
    "synthetic-secret-marker",
    "synthetic/private/path-marker",
    "synthetic@example.invalid",
    "synthetic-command-marker",
)


def _records():
    return tuple(
        json.loads(line)
        for line in PRIMARY_FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _record(**overrides):
    value = {
        "source_id": SYNTHETIC_PRIMARY_SOURCE,
        "legacy_event_id": "synthetic-event",
        "tool_name": "read_file",
        "occurred_at": "2026-06-01T08:00:00Z",
        "status": "success",
        "terminal": True,
    }
    value.update(overrides)
    return value


def test_fixture_dry_run_is_metadata_only_count_bounded_and_content_free():
    result = preview_tool_usage_backfill(
        _records(),
        agent_ledger_coverage_count=8,
    )
    report = result.to_safe_report()

    assert report["status"] == "dry_run"
    assert report["source"] == "synthetic-fixture"
    assert report["counts"] == {
        "imported": 2,
        "skipped": 1,
        "deduped": 1,
        "unsafe_rejected": 4,
        "unknown": 1,
    }
    assert set(report["counts"]) == {
        "imported",
        "skipped",
        "deduped",
        "unsafe_rejected",
        "unknown",
    }
    assert report["coverage_comparison"] == "equal"
    assert report["writes_performed"] is False
    assert report["apply_mode_available"] is False
    assert len(result.records) == 2
    assert {record.status for record in result.records} == {"succeeded", "failed"}
    assert all(record.duration_ms is None for record in result.records)
    assert all(record.historical is True for record in result.records)
    assert all(len(record.dedupe_key) == 64 for record in result.records)

    encoded = json.dumps(
        {
            "report": report,
            "records": [record.to_safe_dict() for record in result.records],
        },
        sort_keys=True,
    )
    assert "legacy_event_id" not in encoded
    assert "owner" not in encoded
    assert "session" not in encoded
    for marker in UNSAFE_MARKERS:
        assert marker not in encoded


def test_returned_checkpoint_makes_repeated_preview_idempotent():
    first = preview_tool_usage_backfill(_records())
    second = preview_tool_usage_backfill(
        _records(),
        checkpoint=first.checkpoint,
    )

    assert len(first.records) == 2
    assert second.records == ()
    assert second.to_safe_report()["counts"] == {
        "imported": 0,
        "skipped": 0,
        "deduped": 4,
        "unsafe_rejected": 4,
        "unknown": 1,
    }


def test_tax10_alias_resolution_uses_one_canonical_counting_identity():
    contract = build_builtin_analytics_identity_contract(
        BUILTIN_TOOL_DESCRIPTIONS,
        historical_alias_targets={"legacy_read_file": "read_file"},
    )
    result = preview_tool_usage_backfill(
        [_record(tool_name="legacy_read_file")],
        identity_contract=contract,
    )

    assert result.to_safe_report()["counts"]["imported"] == 1
    assert result.records[0].tool_analytics_id == "read-file"
    assert "legacy_read_file" not in json.dumps(result.records[0].to_safe_dict())


def test_agent_ledger_is_coverage_only_and_never_added_to_primary_counts():
    low_reference = preview_tool_usage_backfill(
        [_record()],
        agent_ledger_coverage_count=0,
    )
    high_reference = preview_tool_usage_backfill(
        [_record()],
        agent_ledger_coverage_count=100,
    )

    assert low_reference.to_safe_report()["counts"] == high_reference.to_safe_report()["counts"]
    assert low_reference.to_safe_report()["counts"]["imported"] == 1
    assert low_reference.coverage_comparison == "primary_higher"
    assert high_reference.coverage_comparison == "primary_lower"


@pytest.mark.parametrize(
    "record",
    [
        _record(source_id="another_source"),
        _record(command="synthetic-command-marker"),
        _record(occurred_at="not-a-timestamp"),
        _record(status="arbitrary"),
        _record(terminal="true"),
        _record(tool_name="unsafe tool name"),
    ],
)
def test_non_allowlisted_or_malformed_records_are_count_only_rejected(record):
    result = preview_tool_usage_backfill([record])

    assert result.records == ()
    assert result.to_safe_report()["counts"] == {
        "imported": 0,
        "skipped": 0,
        "deduped": 0,
        "unsafe_rejected": 1,
        "unknown": 0,
    }
    assert "synthetic-command-marker" not in json.dumps(result.to_safe_report())


def test_input_and_coverage_bounds_fail_closed():
    with pytest.raises(ToolUsageBackfillError, match="bounded iterable"):
        preview_tool_usage_backfill("not-records")
    with pytest.raises(ToolUsageBackfillError, match="record count"):
        preview_tool_usage_backfill([_record()] * 10_001)
    with pytest.raises(ToolUsageBackfillError, match="coverage count"):
        preview_tool_usage_backfill([], agent_ledger_coverage_count=10_001)


def test_cli_exposes_only_fixed_synthetic_dry_run_and_safe_report():
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            "synthetic-fixture",
            "--dry-run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["counts"] == {
        "imported": 2,
        "skipped": 1,
        "deduped": 1,
        "unsafe_rejected": 4,
        "unknown": 1,
    }
    assert report["writes_performed"] is False
    assert report["apply_mode_available"] is False
    for marker in UNSAFE_MARKERS:
        assert marker not in completed.stdout

    no_dry_run = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", "synthetic-fixture"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    apply_attempt = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            "synthetic-fixture",
            "--dry-run",
            "--apply",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert no_dry_run.returncode != 0
    assert apply_attempt.returncode != 0
