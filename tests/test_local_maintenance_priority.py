import pytest

from src.local_maintenance_priority import (
    LocalMaintenancePriorityError,
    LocalMaintenancePreflightEvidence,
    build_foreground_aware_maintenance_plan,
    build_guarded_maintenance_launcher_plan,
    build_low_priority_maintenance_plan,
    main,
)


def test_builds_nice_ionice_plan_without_executing():
    plan = build_low_priority_maintenance_plan(
        ("podman", "exec", "odysseus_odysseus_1", "python", "scripts/gemma_memory_benchmark.py"),
        priority_class="P2",
    )

    assert plan.execution_argv[:8] == ("nice", "-n", "10", "ionice", "-c", "2", "-n", "7")
    assert plan.execution_argv[8:] == plan.maintenance_argv
    assert plan.to_dict()["executes"] is False


def test_builds_systemd_scope_plan():
    plan = build_low_priority_maintenance_plan(
        ("podman", "exec", "odysseus_odysseus_1", "python", "scripts/gemma_memory_benchmark.py"),
        method="systemd_scope",
        cpu_weight=25,
        io_weight=15,
    )

    assert plan.execution_argv[:7] == (
        "systemd-run",
        "--user",
        "--scope",
        "-p",
        "CPUWeight=25",
        "-p",
        "IOWeight=15",
    )


def test_p3_defaults_to_stronger_low_priority():
    plan = build_low_priority_maintenance_plan(
        ("podman", "exec", "odysseus_odysseus_1", "python", "scripts/gemma_memory_benchmark.py"),
        priority_class="P3",
    )

    assert plan.execution_argv[:6] == ("nice", "-n", "19", "ionice", "-c", "3")
    assert "-n" not in plan.execution_argv[6:8]
    assert plan.nice_value == 19
    assert plan.ionice_class == 3
    assert plan.ionice_level is None


def test_rejects_shell_string_and_control_syntax():
    with pytest.raises(LocalMaintenancePriorityError):
        build_low_priority_maintenance_plan("podman exec app python script.py")  # type: ignore[arg-type]

    with pytest.raises(LocalMaintenancePriorityError):
        build_low_priority_maintenance_plan(("podman", "exec", "app", "python", "x.py", ";"))


def test_rejects_destructive_commands():
    with pytest.raises(LocalMaintenancePriorityError):
        build_low_priority_maintenance_plan(("rm", "-rf", "/tmp/example"))

    with pytest.raises(LocalMaintenancePriorityError):
        build_low_priority_maintenance_plan(("podman", "rm", "-f", "odysseus_odysseus_1"))


def test_redacts_private_paths_in_report_but_keeps_execution_argv():
    plan = build_low_priority_maintenance_plan(
        ("python", "/home/homebase/private/report.json", "C:\\Users\\nkatz\\secret.txt")
    )
    payload = plan.to_dict()
    encoded = repr(payload)

    assert "/home/homebase/private/report.json" in plan.execution_argv
    assert "C:\\Users\\nkatz\\secret.txt" in plan.execution_argv
    assert "/home/homebase/private/report.json" not in encoded
    assert "C:\\Users\\nkatz\\secret.txt" not in encoded
    assert "<private-path>/report.json" in encoded
    assert "<private-path>/secret.txt" in encoded


def test_foreground_aware_plan_inserts_guard_inside_podman_exec():
    plan = build_foreground_aware_maintenance_plan(
        (
            "podman",
            "exec",
            "odysseus_odysseus_1",
            "python",
            "scripts/gemma_multihop_chunk_benchmark.py",
        ),
        priority_class="P3",
        wait_timeout_seconds=120,
    )

    assert plan.execution_argv[:6] == ("nice", "-n", "19", "ionice", "-c", "3")
    assert plan.maintenance_argv[:3] == ("podman", "exec", "odysseus_odysseus_1")
    assert plan.maintenance_argv[3:10] == (
        "python",
        "-m",
        "src.local_maintenance_priority",
        "--wait-foreground-clear",
        "--timeout",
        "120",
        "--",
    )
    assert plan.maintenance_argv[10:] == ("python", "scripts/gemma_multihop_chunk_benchmark.py")


def test_foreground_aware_plan_supports_podman_exec_options():
    plan = build_foreground_aware_maintenance_plan(
        (
            "podman",
            "exec",
            "-i",
            "-e",
            "PYTHONPATH=/app",
            "odysseus_odysseus_1",
            "python",
            "script.py",
        ),
        wait_timeout_seconds=90,
    )

    assert plan.maintenance_argv[:6] == (
        "podman",
        "exec",
        "-i",
        "-e",
        "PYTHONPATH=/app",
        "odysseus_odysseus_1",
    )
    assert plan.maintenance_argv[6:13] == (
        "python",
        "-m",
        "src.local_maintenance_priority",
        "--wait-foreground-clear",
        "--timeout",
        "90",
        "--",
    )


def test_wait_guard_cli_reports_clear_without_executing_command(tmp_path):
    marker_path = tmp_path / "missing.json"

    assert main(["--wait-foreground-clear", "--marker-path", str(marker_path)]) == 0


def test_guarded_launcher_plan_combines_priority_guard_preflight_and_timeout():
    plan = build_guarded_maintenance_launcher_plan(
        (
            "podman",
            "exec",
            "odysseus_odysseus_1",
            "python",
            "scripts/gemma_multihop_chunk_benchmark.py",
        ),
        priority_class="P3",
        command_timeout_seconds=1200,
        report_path="/tmp/gemma-maintenance-report.json",
        evidence=LocalMaintenancePreflightEvidence(
            load_average_1m=0.42,
            available_ram_mb=8192,
            warm_models=("gemma3:4b",),
            active_maintenance=False,
        ),
    )
    payload = plan.to_dict()

    assert plan.preflight_status == "ready"
    assert plan.preflight_failures == ()
    assert plan.preflight_warnings == ()
    assert plan.command_timeout_seconds == 1200
    assert plan.max_load_average_1m == 1.0
    assert plan.min_available_ram_mb == 4096
    assert plan.execution_argv[:6] == ("nice", "-n", "19", "ionice", "-c", "3")
    assert "src.local_maintenance_priority" in plan.priority_plan.maintenance_argv
    assert payload["executes"] is False
    assert payload["priority"]["executes"] is False


def test_guarded_launcher_plan_without_evidence_is_unknown_not_executed():
    plan = build_guarded_maintenance_launcher_plan(
        ("podman", "exec", "odysseus_odysseus_1", "python", "script.py"),
        priority_class="P2",
    )

    assert plan.preflight_status == "unknown"
    assert plan.max_load_average_1m == 2.0
    assert "preflight_evidence_missing" in plan.preflight_warnings
    assert plan.to_dict()["executes"] is False


def test_guarded_launcher_plan_blocks_bad_preflight_evidence():
    plan = build_guarded_maintenance_launcher_plan(
        ("podman", "exec", "odysseus_odysseus_1", "python", "script.py"),
        evidence={
            "load_average_1m": 4.2,
            "available_ram_mb": 2048,
            "warm_models": ("llama3:8b",),
            "active_maintenance": True,
        },
    )

    assert plan.preflight_status == "blocked"
    assert plan.preflight_failures == (
        "load_average_too_high",
        "available_ram_too_low",
        "required_model_not_warm",
        "maintenance_already_active",
    )


def test_guarded_launcher_plan_redacts_private_report_path():
    plan = build_guarded_maintenance_launcher_plan(
        ("python", "/home/homebase/private/script.py"),
        report_path="/home/homebase/private/report.json",
    )
    encoded = repr(plan.to_dict())

    assert "/home/homebase/private/report.json" == plan.report_path
    assert "/home/homebase/private/report.json" not in encoded
    assert "<private-path>/report.json" in encoded


def test_guarded_launcher_plan_rejects_unsafe_bounds_and_report_path():
    with pytest.raises(LocalMaintenancePriorityError):
        build_guarded_maintenance_launcher_plan(("python", "script.py"), wait_timeout_seconds=0)

    with pytest.raises(LocalMaintenancePriorityError):
        build_guarded_maintenance_launcher_plan(("python", "script.py"), report_path="/tmp/report.json;rm")
