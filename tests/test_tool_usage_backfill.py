from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from src.tool_usage_backfill import (
    BACKFILL_COUNT_FIELDS,
    PRIMARY_SOURCE,
    BackfillCheckpoint,
    ToolUsageBackfillError,
    _build_terminal_event,
    _candidate_from_record,
    dry_run_synthetic_fixture,
)
from src.tool_usage_store import ToolUsageStore


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "tool_usage" / "synthetic_chat_tool_events.json"
SCRIPT_PATH = ROOT / "scripts" / "backfill_tool_usage.py"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_bundled_fixture_returns_only_bounded_category_counts_and_coverage():
    result = dry_run_synthetic_fixture(_fixture())
    payload = result.report.to_dict()

    assert payload == {
        "schema": "odysseus.tool_usage_legacy_backfill.v1",
        "dry_run": True,
        "counts": {
            "imported": 3,
            "skipped": 1,
            "deduped": 1,
            "unsafe_rejected": 1,
            "unknown": 1,
        },
        "raw_content_visible": False,
    }
    assert tuple(payload["counts"]) == BACKFILL_COUNT_FIELDS
    assert result.coverage.primary_record_count == 6
    assert result.coverage.agent_ledger_start_count == 9
    assert result.coverage.agent_ledger_imported_count == 0
    assert result.coverage.additive is False
    encoded = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "read_file",
        "manage_rag",
        "custom_plugin_probe",
        "synthetic command preview",
        "SYNTHETIC_SECRET_MARKER",
        "example.invalid",
        "Z:/synthetic",
    ):
        assert forbidden not in encoded


def test_reusing_content_free_checkpoint_is_idempotent():
    checkpoint = BackfillCheckpoint()

    first = dry_run_synthetic_fixture(_fixture(), checkpoint=checkpoint)
    second = dry_run_synthetic_fixture(_fixture(), checkpoint=checkpoint)

    assert first.report.counts.imported == 3
    assert first.report.counts.deduped == 1
    assert second.report.counts.imported == 0
    assert second.report.counts.deduped == 4
    assert second.report.counts.skipped == 1
    assert second.report.counts.unsafe_rejected == 1
    assert second.report.counts.unknown == 0
    assert len(checkpoint) == 3
    assert all(re.fullmatch(r"bf1_[0-9a-f]{32}", item) for item in checkpoint.snapshot())


def test_tax_alias_unknown_bucket_and_null_duration_persist_no_legacy_content(tmp_path):
    fixture = _fixture()
    canonical_record, alias_record, unknown_record = fixture["records"][:3]
    canonical = _build_terminal_event(_candidate_from_record(canonical_record))
    alias = _build_terminal_event(_candidate_from_record(alias_record))
    unknown = _build_terminal_event(_candidate_from_record(unknown_record))

    assert canonical.tool_analytics_id == "read_file"
    assert alias.tool_analytics_id == "manage_personal_docs"
    assert alias.tool_source.value == "builtin"
    assert unknown.tool_analytics_id == "legacy.unclassified"
    assert unknown.tool_family.value == "unclassified_dynamic"
    assert unknown.tool_source.value == "legacy"
    for event in (canonical, alias, unknown):
        assert event.duration_ms is None
        assert event.owner_ref is None
        assert event.session_ref is None
        assert event.run_ref is None
        assert event.correlation_ref is None

    store = ToolUsageStore(tmp_path / "synthetic-target.sqlite3")
    store.migrate()
    write = store.append_events((canonical, alias, unknown))
    stored = repr(store._aggregation_event_rows("2026-07-01"))
    store.close()

    assert write.accepted_count == 3
    for forbidden in (
        "manage_rag",
        "custom_plugin_probe",
        "synthetic command preview",
        "SYNTHETIC_SECRET_MARKER",
        "example.invalid",
        "Z:/synthetic",
    ):
        assert forbidden not in stored


def test_fixture_contract_rejects_secondary_sources_and_non_count_coverage():
    fixture = _fixture()
    with pytest.raises(ToolUsageBackfillError, match="primary source"):
        dry_run_synthetic_fixture({**fixture, "source": "agent_run_ledger"})
    with pytest.raises(ToolUsageBackfillError, match="count-only"):
        dry_run_synthetic_fixture(
            {
                **fixture,
                "agent_ledger_coverage": {
                    "start_count": 9,
                    "records": [{"tool": "read_file"}],
                },
            }
        )
    with pytest.raises(ToolUsageBackfillError, match="checkpoint"):
        BackfillCheckpoint(["raw-session-id"])
    assert fixture["source"] == PRIMARY_SOURCE


def test_cli_is_fixed_to_bundled_synthetic_dry_run_and_has_no_apply_path():
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--source",
            "synthetic-fixture",
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["counts"]["imported"] == 3
    rejected = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--source",
            "synthetic-fixture",
            "--apply",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    script_source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "--apply" not in script_source
    assert "ToolUsageStore" not in script_source
    assert "sqlite" not in script_source.lower()
    assert "--fixture" not in script_source
    assert "--output" not in script_source
