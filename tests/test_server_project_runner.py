from pathlib import Path

import pytest

from src.server_project_runner import build_server_project_runner_plan


def test_default_plan_is_hold_until_live_gates_are_supplied():
    plan = build_server_project_runner_plan()

    assert plan.decision == "hold"
    assert plan.live_execution_allowed is False
    assert "backup evidence is not green" in plan.blockers
    assert "smoke target is missing" in plan.blockers
    assert "rollback or hold plan is missing" in plan.blockers
    assert all(step["executes"] is False for step in plan.planned_steps)


def test_complete_dry_run_plan_is_plan_ready_but_not_live_execution():
    plan = build_server_project_runner_plan(
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold deployment and keep previous container until operator review",
        operator_decision="hold",
        live_go=False,
    )

    assert plan.decision == "plan_ready"
    assert plan.live_execution_allowed is False
    assert plan.blockers == ()
    assert [step["step_id"] for step in plan.planned_steps] == [
        "workspace_preflight",
        "git_remote_gate",
        "branch_plan",
        "quality_gate_1",
        "backup_gate",
        "deploy_handoff",
        "smoke_gate",
        "rollback_or_hold",
    ]


def test_ready_for_operator_go_requires_all_gates_and_operator_go():
    plan = build_server_project_runner_plan(
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="rollback to previous verified image if smoke fails",
        operator_decision="go",
        live_go=True,
    )

    assert plan.decision == "ready_for_operator_go"
    assert plan.live_execution_allowed is True
    assert plan.blockers == ()


def test_origin_remote_is_blocked():
    plan = build_server_project_runner_plan(
        push_remote="origin",
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold",
    )

    assert plan.decision == "blocked"
    assert "push remote must be fuzzy" in plan.blockers[0]


def test_ui_scope_is_held_out_of_backend_runner():
    plan = build_server_project_runner_plan(
        ui_scope_requested=True,
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold",
    )

    assert plan.decision == "hold"
    assert "UI scope is excluded" in plan.blockers[0]


def test_blocked_commands_do_not_become_quality_gates():
    plan = build_server_project_runner_plan(
        quality_gate_commands=("curl https://example.invalid",),
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold",
    )

    assert plan.decision == "blocked"
    assert any("quality gate commands include blocked" in blocker for blocker in plan.blockers)


def test_private_paths_and_secrets_are_redacted_in_serialized_plan():
    plan = build_server_project_runner_plan(
        project_id=r"C:\Users\nkatz\secret-project",
        quality_gate_commands=("python -m pytest TOKEN=abc123 C:\\Users\\nkatz\\private\\test.py",),
        backup_evidence_green=True,
        smoke_target=r"C:\Users\nkatz\private\smoke.py",
        rollback_plan="use password=hunter2 then hold",
    )
    payload = plan.to_dict()
    markdown = plan.to_markdown()

    assert "C:\\Users" not in str(payload)
    assert "TOKEN=abc123" not in str(payload)
    assert "password=hunter2" not in str(payload)
    assert "[redacted-path]" in markdown
    assert "[redacted-secret]" in markdown


def test_rejects_empty_quality_gate_list():
    with pytest.raises(ValueError, match="quality_gate_command"):
        build_server_project_runner_plan(quality_gate_commands=())


def test_source_has_no_subprocess_or_network_runtime():
    source = Path("src/server_project_runner.py").read_text(encoding="utf-8")

    forbidden = (
        "subprocess",
        "requests",
        "httpx",
        "paramiko",
        "podman exec",
        "docker exec",
        "ssh ",
    )
    for fragment in forbidden:
        assert fragment not in source
