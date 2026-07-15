import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.audit_tool_usage_sources import (
    build_snapshot,
    render_snapshot,
    validate_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "docs" / "plans" / "tool-usage-source-overlap.json"
SCRIPT_PATH = ROOT / "scripts" / "audit_tool_usage_sources.py"


def _by_id(snapshot):
    return {item["source_id"]: item for item in snapshot["sources"]}


def test_snapshot_reproduces_both_frozen_historical_baselines_without_a_total():
    snapshot = build_snapshot()
    sources = _by_id(snapshot)

    assert sources["chat_metadata"]["observed_counts"] == {
        "messages_with_tool_events": 84,
        "sessions": 32,
        "tool_events": 1104,
        "tool_names": 46,
    }
    assert sources["chat_metadata"]["time_bounds"] == {
        "start": "2026-06-06",
        "end": "2026-06-17",
    }
    assert sources["agent_run_ledger"]["observed_counts"] == {
        "runs": 111,
        "tool_names": 43,
        "tool_outputs": 606,
        "tool_starts": 607,
    }
    assert sources["agent_run_ledger"]["time_bounds"] == {
        "start": "2026-06-13",
        "end": "2026-07-05",
    }
    assert snapshot["counting_policy"]["independent_invocation_total"] is None
    assert snapshot["counting_policy"]["may_sum_across_sources"] is False


def test_all_six_sources_have_the_required_controlled_classification():
    snapshot = build_snapshot()
    assert {
        item["source_id"]: item["classification"] for item in snapshot["sources"]
    } == {
        "agent_run_ledger": "coverage_only",
        "ai_activity_ledger": "not_usage",
        "ai_lens": "primary_candidate",
        "chat_metadata": "coverage_only",
        "mcp_audit": "domain_audit",
        "tool_transaction_ledger": "domain_audit",
    }
    assert snapshot["classification_counts"] == {
        "coverage_only": 2,
        "domain_audit": 2,
        "not_usage": 1,
        "primary_candidate": 1,
    }


def test_every_source_explains_overlap_and_historical_schema_gaps():
    for source in build_snapshot()["sources"]:
        assert source["overlap"]["may_sum_with_other_sources"] is False
        assert source["overlap"]["risk"]
        assert source["scope_capabilities"]
        assert source["key_capabilities"]
        assert source["status_capabilities"]
        assert source["historically_missing_fields"]


def test_snapshot_is_aggregate_only_and_contains_no_raw_records_or_identifiers():
    snapshot = build_snapshot()
    validate_snapshot(snapshot)

    assert snapshot["aggregate_only"] is True
    assert snapshot["privacy"] == {
        "direct_identifiers_visible": False,
        "raw_content_visible": False,
        "raw_records_read": False,
    }
    for source in snapshot["sources"]:
        privacy = source["privacy_capabilities"]
        assert privacy["aggregate_only"] is True
        assert privacy["direct_identifiers_visible"] is False
        assert privacy["raw_content_visible"] is False
        assert set(source) == {
            "classification",
            "historically_missing_fields",
            "key_capabilities",
            "observed_counts",
            "overlap",
            "privacy_capabilities",
            "scope_capabilities",
            "source_id",
            "status_capabilities",
            "time_bounds",
        }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["counting_policy"].update(may_sum_across_sources=True),
        lambda value: value["counting_policy"].update(independent_invocation_total=1711),
        lambda value: value["sources"][0]["overlap"].update(
            may_sum_with_other_sources=True
        ),
        lambda value: value["sources"][0]["privacy_capabilities"].update(
            raw_content_visible=True
        ),
        lambda value: value["sources"][0].update(classification="other"),
    ],
)
def test_validation_fails_closed_on_additive_privacy_or_classification_drift(mutation):
    snapshot = copy.deepcopy(build_snapshot())
    mutation(snapshot)
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_rendering_is_byte_stable_sorted_json():
    first = render_snapshot(build_snapshot())
    second = render_snapshot(build_snapshot())

    assert first == second
    assert first.endswith("\n")
    assert json.loads(first) == build_snapshot()


def test_checked_in_snapshot_matches_the_deterministic_builder():
    assert SNAPSHOT_PATH.read_text(encoding="utf-8") == render_snapshot(build_snapshot())


def test_cli_requires_aggregate_only_guard(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--output", str(tmp_path / "snapshot.json")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--aggregate-only is required" in result.stderr


def test_cli_writes_only_the_deterministic_aggregate_snapshot(tmp_path):
    output = tmp_path / "snapshot.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--aggregate-only",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == render_snapshot(build_snapshot())
    assert "no additive total" in result.stdout
