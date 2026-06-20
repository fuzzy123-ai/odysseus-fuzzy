from pathlib import Path

import pytest

from src.odysseus_updater import build_odysseus_updater
from src.odysseus_updater_executor import (
    UpdaterCommandResult,
    UpdaterExecutionStep,
    build_default_odysseus_update_steps,
    command_is_allowed,
    execute_odysseus_update,
)


def _green_bundle(**overrides):
    kwargs = {
        "plan_input": {
            "source_ref": "origin/main",
            "current_ref": "main",
            "target_ref": "origin/main",
            "reason": "active updater execution",
            "risk_level": "medium",
            "required_gates": (
                {
                    "gate_id": "scope_confirmed",
                    "status": "pass",
                    "summary": "scope is confirmed",
                },
                {
                    "gate_id": "offline_slice_confirmed",
                    "status": "pass",
                    "summary": "updater slice is confirmed",
                },
                {
                    "gate_id": "tests_defined",
                    "status": "pass",
                    "summary": "tests are defined",
                },
            ),
            "optional_gates": (),
            "planned_commands": (
                {
                    "argv": ("git", "pull", "--ff-only"),
                    "summary": "fast-forward checkout",
                },
            ),
        },
        "preflight_input": {
            "worktree_snapshot": {
                "dirty": False,
                "staged_files": (),
                "allowed_staged_files": (),
                "hotfile_conflict": False,
            },
            "branch_snapshot": {
                "current_branch": "main",
                "expected_branch": "main",
                "branch_candidates": ("main",),
                "detached": False,
                "ahead": 0,
                "behind": 0,
            },
            "env_snapshot": {
                "required_names": ("ODYSSEUS_UPDATE_CHANNEL",),
                "present_names": ("ODYSSEUS_UPDATE_CHANNEL",),
            },
            "backup_snapshot": {
                "mount_ready": True,
            },
        },
        "backup_gate_input": {
            "risk_level": "medium",
            "evaluated_at": "2026-06-20T00:00:00Z",
            "evidence_inputs": (
                {
                    "evidence_id": "pre_update_snapshot",
                    "state": "green",
                    "result_label": "pass",
                    "checked_at": "2026-06-20T00:00:00Z",
                    "summary": "pre-update snapshot is green",
                },
                {
                    "evidence_id": "repository_check",
                    "state": "green",
                    "result_label": "pass",
                    "checked_at": "2026-06-20T00:01:00Z",
                    "summary": "repository check is green",
                },
                {
                    "evidence_id": "restore_smoke",
                    "state": "green",
                    "result_label": "pass",
                    "checked_at": "2026-06-20T00:02:00Z",
                    "summary": "restore smoke is green",
                },
            ),
        },
        "test_gate_input": {
            "allowed_suites": (
                {
                    "suite_id": "updater_executor",
                    "required": True,
                    "timeout_seconds": 300,
                    "summary": "executor tests",
                },
            ),
            "result_snapshots": (
                {
                    "suite_id": "updater_executor",
                    "execution_status": "completed",
                    "result_label": "pass",
                    "summary": "executor tests passed",
                },
            ),
        },
        "command_plan_inputs": (),
        "include_audit_summary": False,
        "live_update_enabled": True,
        "operator_decision": "go",
    }
    kwargs.update(overrides)
    return build_odysseus_updater(**kwargs)


def test_default_steps_match_podman_safe_update_flow():
    steps = build_default_odysseus_update_steps(
        reason="release update",
        smoke_tests=("tests/test_odysseus_updater_executor.py",),
    )

    assert [step.step_id for step in steps] == [
        "pre_update_snapshot",
        "git_pull_ff_only",
        "podman_compose_up",
        "podman_image_prune",
        "smoke_test_1",
    ]
    assert steps[0].env == {"ODYSSEUS_UPDATE_REASON": "release update"}
    assert steps[2].argv == ("podman", "compose", "up", "-d", "--build")
    assert steps[3].argv == ("podman", "image", "prune", "-f")
    assert all(command_is_allowed(step.argv) for step in steps)


def test_default_steps_can_use_explicit_docker_fallback():
    steps = build_default_odysseus_update_steps(
        container_runtime="docker",
        include_pre_update_hook=False,
    )

    assert [step.step_id for step in steps] == [
        "git_pull_ff_only",
        "docker_compose_up",
        "docker_image_prune",
    ]
    assert steps[1].argv == ("docker", "compose", "up", "-d", "--build")
    assert steps[2].argv == ("docker", "image", "prune", "-f")


def test_execute_blocks_until_live_flag_and_operator_go_are_present():
    bundle = _green_bundle(live_update_enabled=False, operator_decision="missing")
    calls = []

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append(argv)
        return UpdaterCommandResult(exit_code=0)

    report = execute_odysseus_update(
        bundle=bundle,
        steps=(UpdaterExecutionStep.create(step_id="pull", argv=("git", "pull", "--ff-only"), summary="pull"),),
        command_runner=runner,
        live_enabled=False,
        operator_decision="hold",
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert calls == []
    assert "ODYSSEUS_UPDATER_LIVE_ENABLED is not enabled" in report.blockers
    assert "operator decision is not go" in report.blockers


def test_execute_runs_whitelisted_steps_in_order_with_fake_runner(tmp_path: Path):
    bundle = _green_bundle()
    calls = []
    steps = (
        UpdaterExecutionStep.create(
            step_id="pull",
            argv=("git", "pull", "--ff-only"),
            summary="pull latest code",
        ),
        UpdaterExecutionStep.create(
            step_id="podman",
            argv=("podman", "compose", "up", "-d", "--build"),
            summary="restart deployment",
        ),
    )

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append((argv, cwd, timeout_seconds, dict(env)))
        return UpdaterCommandResult(exit_code=0, stdout="ok")

    report = execute_odysseus_update(
        bundle=bundle,
        steps=steps,
        cwd=tmp_path,
        command_runner=runner,
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "completed"
    assert report.succeeded is True
    assert report.executed is True
    assert [call[0] for call in calls] == [
        ("git", "pull", "--ff-only"),
        ("podman", "compose", "up", "-d", "--build"),
    ]
    assert all(call[1] == tmp_path.resolve() for call in calls)


def test_execute_stops_on_first_failed_step():
    bundle = _green_bundle()
    steps = (
        UpdaterExecutionStep.create(
            step_id="pre_update_snapshot",
            argv=("ops/homeserver/pre-update-snapshot.sh",),
            summary="snapshot",
        ),
        UpdaterExecutionStep.create(
            step_id="pull",
            argv=("git", "pull", "--ff-only"),
            summary="pull latest code",
        ),
    )
    calls = []

    def runner(argv, *, cwd, timeout_seconds, env):
        calls.append(argv)
        return UpdaterCommandResult(exit_code=1, stderr="backup gate failed")

    report = execute_odysseus_update(
        bundle=bundle,
        steps=steps,
        command_runner=runner,
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "failed"
    assert report.executed is True
    assert calls == [("ops/homeserver/pre-update-snapshot.sh",)]
    assert report.blockers == ("step pre_update_snapshot failed",)


def test_execute_rejects_unwhitelisted_commands_before_running():
    bundle = _green_bundle()

    def runner(argv, *, cwd, timeout_seconds, env):  # pragma: no cover
        raise AssertionError("unsafe command must not run")

    report = execute_odysseus_update(
        bundle=bundle,
        steps=(
            UpdaterExecutionStep.create(
                step_id="danger",
                argv=("rm", "-rf", "/"),
                summary="unsafe",
            ),
        ),
        command_runner=runner,
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "step danger is not in the updater command whitelist" in report.blockers


def test_result_output_redacts_secret_like_text():
    result = UpdaterCommandResult(exit_code=1, stderr="Bearer super-secret-token")

    assert result.to_dict()["stderr"] == "[redacted]"


def test_step_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="timeout_seconds"):
        UpdaterExecutionStep.create(
            step_id="pull",
            argv=("git", "pull", "--ff-only"),
            summary="pull",
            timeout_seconds=0,
        )


def test_executor_source_uses_bounded_subprocess_without_shell():
    source = Path("src/odysseus_updater_executor.py").read_text(encoding="utf-8")

    assert "subprocess.run(" in source
    assert "shell=False" in source
    assert "capture_output=True" in source
    assert "timeout=timeout_seconds" in source

    forbidden_fragments = (
        "shell=True",
        "os.system",
        "subprocess.Popen",
        "requests",
        "telegram",
        "nextcloud",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source
