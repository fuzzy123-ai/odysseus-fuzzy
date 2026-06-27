from pathlib import Path

from src.server_project_deploy_handoff import build_project_deploy_handoff
from src.server_project_git_review import build_project_git_review_plan
from src.server_project_quality_gate import ProjectQualityGateSpec, build_project_quality_gate_bundle
from src.server_project_registry import ServerProjectRegistry


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def _green_backup_evidence():
    return (
        {
            "evidence_id": "pre_update_snapshot",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-27T10:00:00Z",
            "summary": "pre-update snapshot evidence is green",
        },
        {
            "evidence_id": "repository_check",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-27T10:01:00Z",
            "summary": "repository check evidence is green",
        },
        {
            "evidence_id": "restore_smoke",
            "state": "green",
            "result_label": "pass",
            "checked_at": "2026-06-27T10:02:00Z",
            "summary": "restore smoke evidence is green",
        },
    )


def _ready_quality(record):
    return build_project_quality_gate_bundle(
        record=record,
        gate_specs=(
            {"gate_id": "unit", "gate_type": "test", "command_text": "python -m pytest tests/test_server_project_deploy_handoff.py -q"},
            {"gate_id": "build", "gate_type": "build", "command_text": "evidence: build artifact recorded"},
            {"gate_id": "smoke", "gate_type": "smoke", "command_text": "python -m pytest tests/test_server_project_deploy_handoff.py -q"},
        ),
    )


def _ready_git(record):
    return build_project_git_review_plan(
        record=record,
        changed_paths=("src/app.py", "tests/test_app.py"),
        operator_decision="go",
    )


def test_deploy_handoff_ready_for_operator_go_when_all_evidence_is_green():
    record = _record()
    handoff = build_project_deploy_handoff(
        record=record,
        quality_bundle=_ready_quality(record),
        git_review_plan=_ready_git(record),
        backup_evidence_inputs=_green_backup_evidence(),
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )

    assert handoff.decision == "ready_for_operator_go"
    assert handoff.live_execution_allowed is False
    assert handoff.blockers == ()
    assert handoff.backup_gate.deployment_decision == "go"
    assert handoff.live_boundary.decision == "ready_for_operator_go"
    assert all(step["executes"] is False for step in handoff.planned_steps)


def test_missing_backup_evidence_holds_deploy_handoff():
    record = _record()
    handoff = build_project_deploy_handoff(
        record=record,
        quality_bundle=_ready_quality(record),
        git_review_plan=_ready_git(record),
        backup_evidence_inputs=_green_backup_evidence()[:1],
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )

    assert handoff.decision == "hold"
    assert any("backup gate deployment decision" in blocker for blocker in handoff.blockers)
    assert "restore smoke evidence is missing or not green" in handoff.blockers


def test_quality_or_git_not_ready_holds_handoff():
    record = _record()
    blocked_quality = build_project_quality_gate_bundle(
        record=record,
        gate_specs=(ProjectQualityGateSpec.create(gate_id="bad", gate_type="test", command_text="npm test"),),
    )
    held_git = build_project_git_review_plan(
        record=record,
        changed_paths=("src/app.py",),
        operator_decision="hold",
    )

    handoff = build_project_deploy_handoff(
        record=record,
        quality_bundle=blocked_quality,
        git_review_plan=held_git,
        backup_evidence_inputs=_green_backup_evidence(),
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )

    assert handoff.decision == "hold"
    assert "quality gates are blocked" in handoff.blockers
    assert "git review is hold" in handoff.blockers


def test_secret_risk_or_live_command_request_is_no_go():
    record = _record()
    kwargs = {
        "record": record,
        "quality_bundle": _ready_quality(record),
        "git_review_plan": _ready_git(record),
        "backup_evidence_inputs": _green_backup_evidence(),
        "evaluated_at": "2026-06-27T10:05:00Z",
        "operator_decision": "go",
        "command_plan_reviewed": True,
    }

    secret = build_project_deploy_handoff(**kwargs, secret_or_private_output_risk=True)
    live = build_project_deploy_handoff(**kwargs, live_command_requested=True)

    assert secret.decision == "no_go"
    assert live.decision == "no_go"
    assert secret.live_execution_allowed is False
    assert live.live_execution_allowed is False


def test_handoff_packet_is_safe_and_compact():
    record = _record()
    handoff = build_project_deploy_handoff(
        record=record,
        quality_bundle=_ready_quality(record),
        git_review_plan=_ready_git(record),
        backup_evidence_inputs=_green_backup_evidence(),
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )
    payload = handoff.to_dict()

    assert payload["project_slug"] == "kundenportal-mvp"
    assert payload["live_execution_allowed"] is False
    assert "backup_gate" in payload
    assert "live_boundary" in payload


def test_source_has_no_live_runtime():
    source = Path("src/server_project_deploy_handoff.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "podman exec", "docker exec", "shell=True")
    for fragment in forbidden:
        assert fragment not in source
