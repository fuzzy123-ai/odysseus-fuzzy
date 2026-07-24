from pathlib import Path

import pytest

from src.server_project_runner import build_server_project_runner_plan


def test_default_plan_is_hold_until_live_gates_are_supplied():
    plan = build_server_project_runner_plan()

    assert plan.decision == "hold"
    assert plan.live_execution_allowed is False
    assert plan.to_dict()["operator_gate"]["mutation_allowed"] is False
    assert plan.to_dict()["raw_content_visible"] is False
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
    assert plan.to_dict()["operator_gate"]["commit_push_deploy_requires_gate"] is True
    assert plan.to_dict()["evidence_summary"]["quality_gate_count"] == 1
    assert plan.to_dict()["evidence_summary"]["planned_step_count"] == len(plan.planned_steps)
    assert plan.blockers == ()
    assert [step["step_id"] for step in plan.planned_steps] == [
        "project_intake",
        "project_chat_scope",
        "repo_creation_plan",
        "workspace_preflight",
        "git_remote_gate",
        "branch_plan",
        "quality_gate_1",
        "backup_gate",
        "deploy_handoff",
        "smoke_gate",
        "cloudflare_tunnel_gate",
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
    assert plan.live_execution_allowed is False
    assert plan.operator_live_go_ready is True
    assert plan.to_dict()["live_execution_allowed"] is False
    assert plan.to_dict()["operator_gate"]["mutation_allowed"] is False
    assert plan.to_dict()["operator_gate"]["operator_decision"] == "go"
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


def test_project_title_creates_universal_project_repo_workspace_and_chat_scope():
    plan = build_server_project_runner_plan(
        project_title="Kundenportal MVP",
        project_type="app",
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold deployment and keep previous release",
    )
    spec = plan.project_spec

    assert spec.project_title == "Kundenportal MVP"
    assert spec.project_slug == "kundenportal-mvp"
    assert spec.repo_name == "kundenportal-mvp"
    assert spec.workspace_root == "projects/kundenportal-mvp"
    assert spec.chat_scope == "project:kundenportal-mvp"
    assert spec.project_type == "app"
    assert "repo_creation_plan" in [step["step_id"] for step in plan.planned_steps]
    assert "project_chat_scope" in [step["step_id"] for step in plan.planned_steps]


def test_cloudflare_tunnel_is_a_gate_not_execution():
    plan = build_server_project_runner_plan(
        project_title="Public Demo",
        cloudflare_tunnel_requested=True,
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold",
    )

    assert plan.project_spec.cloudflare_tunnel_requested is True
    assert "Cloudflare Tunnel requested" in plan.project_spec.cloudflare_tunnel_gate
    tunnel_step = [step for step in plan.planned_steps if step["step_id"] == "cloudflare_tunnel_gate"][0]
    assert tunnel_step["executes"] is False
    assert "operator Go" in tunnel_step["summary"]


def test_odysseus_repo_name_is_not_the_universal_default():
    plan = build_server_project_runner_plan(
        project_title="Some Tool",
        repo_name="odysseus",
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold",
    )

    assert plan.decision == "hold"
    assert "universal project runner must not default to the Odysseus repository" in plan.blockers


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


@pytest.mark.parametrize(
    "command",
    (
        "git push --force fuzzy dev",
        "git rebase main",
        "git update-ref refs/heads/main HEAD",
        "git branch -D main",
        "python -m pytest tests/test_server_project_runner.py | git status",
    ),
)
def test_quality_gate_commands_use_structured_safe_argv_only(command):
    plan = build_server_project_runner_plan(
        quality_gate_commands=(command,),
        backup_evidence_green=True,
        smoke_target="tests/test_server_project_runner.py",
        rollback_plan="hold",
    )

    assert plan.decision == "blocked"
    assert any("unsafe argv" in blocker for blocker in plan.blockers)


@pytest.mark.parametrize(
    "field_name",
    ("live_go", "backup_evidence_green", "ui_scope_requested", "cloudflare_tunnel_requested"),
)
def test_server_boolean_gates_never_coerce_string_false(field_name):
    kwargs = {
        "backup_evidence_green": True,
        "smoke_target": "tests/test_server_project_runner.py",
        "rollback_plan": "hold",
        "operator_decision": "go",
        "live_go": True,
    }
    kwargs[field_name] = "false"

    plan = build_server_project_runner_plan(**kwargs)

    assert plan.operator_live_go_ready is False
    assert f"{field_name} must be a boolean" in plan.blockers


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
