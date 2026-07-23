import json
import re
from pathlib import Path, PurePosixPath

from scripts.audit_tool_usage_sources import (
    CLASSIFICATIONS,
    SourceEvidence,
    UsageSource,
    _validate_sources,
    audit_usage_sources,
    main,
    render_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _source_map(report: dict) -> dict[str, dict]:
    return {item["source_id"]: item for item in report["sources"]}


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def test_repository_usage_source_baseline_is_clean_and_exact():
    report = audit_usage_sources(ROOT)
    summary = report["summary"]

    assert summary["clean"] is True
    assert summary["chat_baseline"] == {
        "tool_event_count": 1104,
        "distinct_tool_name_count": 46,
        "message_count": 84,
        "session_count": 32,
    }
    assert summary["agent_run_baseline"] == {
        "run_started_event_count": 607,
        "output_event_count": 606,
        "distinct_tool_name_count": 43,
        "run_count": 111,
    }
    assert report["violations"] == []


def test_every_source_has_one_allowed_role_and_one_primary_candidate():
    report = audit_usage_sources(ROOT)
    sources = report["sources"]

    assert {item["classification"] for item in sources} == CLASSIFICATIONS
    assert [
        item["source_id"]
        for item in sources
        if item["classification"] == "primary_candidate"
    ] == ["tool_execution_boundary"]
    assert report["summary"]["classification_counts"] == {
        "coverage_only": 3,
        "domain_audit": 2,
        "not_usage": 2,
        "primary_candidate": 1,
    }


def test_overlap_policy_explicitly_prevents_legacy_double_counting():
    report = audit_usage_sources(ROOT)

    assert report["summary"]["legacy_counts_additive"] is False
    assert report["summary"]["independent_legacy_invocation_total"] is None
    assert report["counting_policy"]["legacy_sources"] == "coverage_only_not_additive"
    chat_agent = next(
        item
        for item in report["overlaps"]
        if {item["left"], item["right"]} == {"chat_metadata", "agent_run_ledger"}
    )
    assert chat_agent["aggregation_rule"] == "never_sum"
    assert chat_agent["shared_key_capability"] == "no_reliable_common_invocation_key"


def test_source_rows_explain_scope_time_keys_status_duration_privacy_and_gaps():
    sources = _source_map(audit_usage_sources(ROOT))

    for item in sources.values():
        assert item["scope"]
        assert item["time_basis"]
        assert item["key_capabilities"]
        assert item["status_capabilities"]
        assert item["duration_capability"]
        assert item["privacy_posture"]
        assert item["historically_missing"]
        assert item["evidence"]
    assert sources["chat_metadata"]["time_bounds"] == {
        "start": "2026-06-06",
        "end": "2026-06-17",
    }
    assert sources["agent_run_ledger"]["time_bounds"] == {
        "start": "2026-06-13",
        "end": "2026-07-05",
    }


def test_aggregate_report_contains_no_runtime_or_private_values():
    report = audit_usage_sources(ROOT)
    summary = report["summary"]
    strings = list(_all_strings(report))

    assert summary["aggregate_only"] is True
    assert summary["runtime_or_private_data_read"] is False
    assert summary["raw_message_visible"] is False
    assert summary["command_visible"] is False
    assert summary["tool_output_visible"] is False
    assert summary["direct_identity_visible"] is False
    assert all(str(ROOT).replace("\\", "/") not in text.replace("\\", "/") for text in strings)
    for source in report["sources"]:
        for evidence in source["evidence"]:
            assert not PurePosixPath(evidence["path"]).is_absolute()
            assert re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"])


def test_report_is_byte_stable_and_sorted():
    first = audit_usage_sources(ROOT)
    second = audit_usage_sources(ROOT)

    assert first == second
    assert render_report(first).encode("utf-8") == render_report(second).encode("utf-8")
    assert first["sources"] == sorted(first["sources"], key=lambda item: item["source_id"])


def test_missing_declared_source_symbol_fails_closed(tmp_path):
    source_path = tmp_path / "source.py"
    source_path.write_text("def present():\n    return None\n", encoding="utf-8")
    source = UsageSource(
        "tool_execution_boundary",
        "Fixture",
        "primary_candidate",
        "fixture",
        "fixture",
        None,
        None,
        ("fixture_key",),
        ("fixture_status",),
        "fixture_duration",
        "content_free",
        (),
        ("fixture_gap",),
        (SourceEvidence("source.py", ("missing",)),),
    )

    violations = _validate_sources(tmp_path, (source,))

    assert {item["code"] for item in violations} == {"missing_source_symbol"}


def test_cli_requires_aggregate_only_and_detects_snapshot_drift(tmp_path):
    output = tmp_path / "overlap.json"
    assert main(["--root", str(ROOT), "--output", str(output)]) == 2
    assert main(
        ["--root", str(ROOT), "--aggregate-only", "--output", str(output)]
    ) == 0
    assert main(
        [
            "--root",
            str(ROOT),
            "--aggregate-only",
            "--check",
            "--output",
            str(output),
        ]
    ) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["summary"]["legacy_counts_additive"] = True
    output.write_text(json.dumps(payload), encoding="utf-8")

    assert main(
        [
            "--root",
            str(ROOT),
            "--aggregate-only",
            "--check",
            "--output",
            str(output),
        ]
    ) == 1
