import json

import pytest

from src.memory_perf_suite_reports import (
    MemoryPerfSuiteReportError,
    archive_suite_report,
    build_performance_summary,
    compare_performance_summaries,
    render_suite_report_markdown,
)
from src.memory_perf_suite_runner import run_memory_durability_scenario


def test_archive_suite_report_writes_safe_relative_artifacts(tmp_path):
    result = run_memory_durability_scenario("quick", run_dir=tmp_path / "run", seed=12, event_count=2)

    archive = archive_suite_report(result, tmp_path / "archive")

    archive_dir = tmp_path / "archive" / archive.archive_dir
    assert (archive_dir / archive.scenario_path).exists()
    assert (archive_dir / archive.report_json_path).exists()
    assert (archive_dir / archive.report_md_path).exists()
    assert (archive_dir / archive.metrics_jsonl_path).exists()
    assert (archive_dir / archive.performance_summary_path).exists()
    assert "\\" not in archive.to_dict()["report_json_path"]

    encoded = (archive_dir / archive.report_json_path).read_text(encoding="utf-8").lower()
    assert "raw_text" not in encoded
    assert "chat_id" not in encoded
    assert "password" not in encoded
    assert json.loads((archive_dir / archive.scenario_path).read_text(encoding="utf-8"))["name"] == "quick"
    summary = json.loads((archive_dir / archive.performance_summary_path).read_text(encoding="utf-8"))
    assert summary["performance_gate"]["status"] == "passed"
    assert "runtime_seconds" in summary["metrics"]


def test_markdown_report_contains_gate_summary_without_raw_content(tmp_path):
    result = run_memory_durability_scenario("quick", run_dir=tmp_path, seed=13, event_count=1)

    markdown = render_suite_report_markdown(result)

    assert "Status: `passed`" in markdown
    assert "Synthetic data only" in markdown
    assert "raw_text" not in markdown.lower()


def test_archive_suite_report_refuses_overwrite(tmp_path):
    result = run_memory_durability_scenario("quick", run_dir=tmp_path / "run", seed=14, event_count=1)
    archive_root = tmp_path / "archive"

    archive_suite_report(result, archive_root)

    with pytest.raises(MemoryPerfSuiteReportError, match="already exists"):
        archive_suite_report(result, archive_root)


def test_performance_summary_comparison_flags_regression(tmp_path):
    baseline_result = run_memory_durability_scenario("quick", run_dir=tmp_path / "base", seed=15, event_count=1)
    current_result = run_memory_durability_scenario("quick", run_dir=tmp_path / "current", seed=16, event_count=1)
    baseline = build_performance_summary(baseline_result)
    current = build_performance_summary(current_result)
    baseline["metrics"]["runtime_seconds"] = 1.0
    current["metrics"]["runtime_seconds"] = 2.0

    comparison = compare_performance_summaries(current, baseline, max_regression_ratio=0.25)

    assert comparison.passed is False
    assert comparison.regressions == ("runtime_seconds_regressed",)
    assert comparison.to_dict()["deltas"]["runtime_seconds"] == 1.0
