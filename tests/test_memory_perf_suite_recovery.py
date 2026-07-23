from src.memory_perf_suite_eventlog import AppendOnlyInMemoryEventLog
from src.memory_perf_suite_invariants import check_recovery_invariants
from src.memory_perf_suite_models import ResourceBudget, ScenarioPreset
from src.memory_perf_suite_runner import (
    CRASH_POINTS,
    run_memory_durability_crash_matrix,
    run_memory_durability_scenario,
)
from src.memory_perf_suite_data import generate_synthetic_memory_events


def test_recovery_invariants_ignore_uncommitted_intent():
    events = generate_synthetic_memory_events("quick", seed=42, count=2)
    log = AppendOnlyInMemoryEventLog()
    log.append_intent(events[0])
    log.append_event(events[1])

    result = check_recovery_invariants((events[1],), log)

    assert result.passed is True
    assert result.recovered_event_count == 1
    assert result.recovered_state.committed_event_ids == (events[1].event_id,)


def test_runner_passes_every_mvp_crash_point(tmp_path):
    results = run_memory_durability_crash_matrix("quick", run_root=tmp_path, seed=99, event_count=3)

    assert tuple(result.crash_point for result in results) == CRASH_POINTS
    assert all(result.status == "passed" for result in results)
    assert all(result.recovery.passed for result in results)
    assert results[0].committed_event_count == 0
    assert results[2].committed_event_count == 1


def test_complete_runner_rebuilds_from_event_log(tmp_path):
    result = run_memory_durability_scenario("quick", run_dir=tmp_path, seed=7, event_count=4)

    assert result.status == "passed"
    assert result.committed_event_count == 4
    assert result.recovery.recovered_event_count == 4
    assert result.recovery.recovered_state.duplicate_event_ids == ()
    assert "events.jsonl" == result.event_log_path


def test_runner_calls_maintenance_yield_checkpoint(tmp_path):
    calls = 0

    def checkpoint() -> None:
        nonlocal calls
        calls += 1

    result = run_memory_durability_scenario(
        "quick",
        run_dir=tmp_path,
        seed=7,
        event_count=4,
        maintenance_yield_func=checkpoint,
    )

    assert result.status == "passed"
    assert calls >= 5


def test_runner_marks_performance_budget_exceeded(tmp_path):
    preset = ScenarioPreset(
        name="quick",
        event_count=1,
        seed=1,
        batch_size=1,
        checkpoint_interval=1,
        budget=ResourceBudget.create(
            max_events=10,
            max_event_bytes=4096,
            max_log_bytes=1500,
            max_runtime_seconds=60,
            max_memory_mb=1024,
        ),
    )

    result = run_memory_durability_scenario(preset, run_dir=tmp_path, event_count=1)

    assert result.recovery.passed is True
    assert result.performance_gate.passed is False
    assert result.status == "failed"
    assert result.performance_gate.failures == ("temp_disk_budget_exceeded",)
    assert result.warnings == ("performance_budget_exceeded",)
