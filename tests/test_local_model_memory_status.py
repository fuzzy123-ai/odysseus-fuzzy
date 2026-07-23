import json

from src.local_maintenance_priority import LocalMaintenancePreflightEvidence, build_guarded_maintenance_launcher_plan
from src.local_model_memory_status import LOCAL_MODEL_MEMORY_STATUS_SCHEMA, build_local_model_memory_status


def test_local_model_memory_status_marks_foreground_and_slow_latency():
    status = build_local_model_memory_status(
        gate_snapshot={"active": 1, "active_foreground": 1, "waiting_foreground": 2, "max_concurrency": 1},
        foreground_marker={"model": "gemma3:4b", "reason": "active"},
        benchmark_summary={"model": "gemma3:4b", "latency_seconds": 81, "tokens": 27, "tokens_per_second": 0.333},
    )

    assert status["schema"] == LOCAL_MODEL_MEMORY_STATUS_SCHEMA
    assert status["status"] == "pending"
    assert status["summary"] == "local model foreground work active; maintenance must yield"
    assert status["queue"]["waiting_foreground"] == 2
    assert status["foreground"]["active"] is True
    assert status["maintenance_guard"]["executes"] is False
    assert status["known_cpu_constraint"] == "slow_local_model_latency_observed"
    assert status["raw_content_visible"] is False


def test_local_model_memory_status_surfaces_blocked_maintenance_preflight():
    plan = build_guarded_maintenance_launcher_plan(
        ("podman", "exec", "odysseus_odysseus_1", "python", "script.py"),
        evidence=LocalMaintenancePreflightEvidence(
            load_average_1m=6.0,
            available_ram_mb=1024,
            warm_models=("llama3:8b",),
            active_maintenance=True,
        ),
    )
    status = build_local_model_memory_status(maintenance_plan=plan)

    assert status["status"] == "blocked"
    assert status["blocked_count"] == 1
    assert status["maintenance_guard"]["preflight_status"] == "blocked"
    assert status["maintenance_guard"]["failure_count"] == 4
    assert status["maintenance_guard"]["executes"] is False


def test_local_model_memory_status_redacts_unsafe_inputs():
    status = build_local_model_memory_status(
        required_model="token=SECRET",
        foreground_marker={"model": "C:/Users/private/model", "reason": "token=SECRET"},
        benchmark_summary={"model": "C:/Users/private/bench", "result": "token=SECRET"},
    )
    encoded = json.dumps(status, sort_keys=True)

    assert status["required_model"] == "gemma3:4b"
    assert status["foreground"]["model"] == ""
    assert status["foreground"]["reason"] == ""
    assert "SECRET" not in encoded
    assert "C:/Users" not in encoded
