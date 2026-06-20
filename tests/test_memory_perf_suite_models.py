import json

import pytest

from src.memory_perf_suite_models import (
    MemoryPerfSuiteModelError,
    MetricsEnvelope,
    ReportEnvelope,
    ResourceBudget,
    ScenarioPreset,
    SuiteMetric,
    build_scenario_preset,
)


def test_presets_are_budget_checked_and_stable():
    quick = build_scenario_preset("quick")
    standard = build_scenario_preset("standard")
    stress = build_scenario_preset("stress-local")

    assert quick.event_count == 25
    assert standard.event_count == 250
    assert stress.event_count == 5_000
    assert quick.estimate_budget().within_budget is True
    assert standard.estimate_budget().to_dict()["reasons"] == ("within_resource_budget",)


def test_preset_from_dict_round_trips():
    preset = build_scenario_preset("standard", seed=123)

    cloned = ScenarioPreset.from_dict(preset.to_dict())

    assert cloned == preset
    assert cloned.to_dict() == preset.to_dict()


def test_preset_rejects_budget_overrun():
    budget = ResourceBudget.create(
        max_events=10,
        max_event_bytes=100,
        max_log_bytes=1_000,
        max_runtime_seconds=1,
        max_memory_mb=1,
    )

    with pytest.raises(MemoryPerfSuiteModelError, match="exceeds budget"):
        ScenarioPreset(
            name="quick",
            event_count=25,
            seed=1,
            batch_size=5,
            checkpoint_interval=5,
            budget=budget,
        )


def test_metrics_and_report_envelopes_round_trip_without_private_fields():
    preset = build_scenario_preset("quick")
    metrics = MetricsEnvelope(
        scenario_id="scenario-quick",
        preset_name=preset.name,
        metrics=(
            SuiteMetric.create(name="append-p95-ms", value=4.2, unit="ms"),
            SuiteMetric.create(name="recovery-events", value=25, unit="count"),
        ),
    )
    report = ReportEnvelope(
        run_id="run-001",
        status="passed",
        preset=preset,
        budget_estimate=preset.estimate_budget(),
        metrics=metrics,
        warnings=("synthetic data only",),
    )

    cloned = ReportEnvelope.from_dict(report.to_dict())
    encoded = json.dumps(cloned.to_dict(), sort_keys=True).lower()

    assert cloned.to_dict() == report.to_dict()
    assert "raw_text" not in encoded
    assert "chat_id" not in encoded
    assert "password" not in encoded
