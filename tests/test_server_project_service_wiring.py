from pathlib import Path

import pytest

from src.server_project_deploy_handoff import build_project_deploy_handoff
from src.server_project_git_review import build_project_git_review_plan
from src.server_project_quality_gate import build_project_quality_gate_bundle
from src.server_project_registry import ServerProjectRegistry
from src.server_project_service_wiring import (
    ServerProjectServiceWiringError,
    build_project_service_wiring_plan,
)


def _record(*, cloudflare: bool = False):
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        cloudflare_tunnel_requested=cloudflare,
        created_at="2026-06-27T10:00:00Z",
    )


def _green_backup_evidence():
    return (
        {"evidence_id": "pre_update_snapshot", "state": "green", "result_label": "pass", "checked_at": "2026-06-27T10:00:00Z", "summary": "green"},
        {"evidence_id": "repository_check", "state": "green", "result_label": "pass", "checked_at": "2026-06-27T10:01:00Z", "summary": "green"},
        {"evidence_id": "restore_smoke", "state": "green", "result_label": "pass", "checked_at": "2026-06-27T10:02:00Z", "summary": "green"},
    )


def _ready_handoff(record):
    return build_project_deploy_handoff(
        record=record,
        quality_bundle=build_project_quality_gate_bundle(record=record),
        git_review_plan=build_project_git_review_plan(record=record, changed_paths=("src/app.py",), operator_decision="go"),
        backup_evidence_inputs=_green_backup_evidence(),
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )


def test_service_wiring_plan_ready_with_operator_go():
    record = _record()
    plan = build_project_service_wiring_plan(
        record=record,
        deploy_handoff=_ready_handoff(record),
        operator_decision="go",
    )

    assert plan.decision == "plan_ready"
    assert plan.install_allowed is True
    assert plan.service_unit == "odysseus-project-kundenportal-mvp.service"
    assert plan.health_unit == "odysseus-project-kundenportal-mvp-health.service"
    assert plan.project_root_placeholder == "$ODYSSEUS_PROJECTS_ROOT/kundenportal-mvp"
    assert plan.wrapper_path_placeholder == "$ODYSSEUS_USER_BIN_DIR/odysseus-project-kundenportal-mvp.sh"
    assert all(step["executes"] is False for step in plan.planned_steps)


def test_unit_templates_are_reviewable_without_private_host_paths():
    record = _record()
    plan = build_project_service_wiring_plan(
        record=record,
        deploy_handoff=_ready_handoff(record),
        operator_decision="go",
    )
    encoded = str(plan.to_dict())

    assert "WorkingDirectory=$ODYSSEUS_PROJECTS_ROOT/kundenportal-mvp" in encoded
    assert "/home/" not in encoded
    assert "/root/" not in encoded
    assert "C:\\Users" not in encoded
    assert "journalctl --user" in encoded


def test_holds_without_operator_go_or_ready_deploy_handoff():
    record = _record()
    held = build_project_service_wiring_plan(
        record=record,
        deploy_handoff=_ready_handoff(record),
        operator_decision="hold",
    )
    not_ready_handoff = build_project_deploy_handoff(
        record=record,
        quality_bundle=build_project_quality_gate_bundle(record=record),
        git_review_plan=build_project_git_review_plan(record=record, changed_paths=("src/app.py",), operator_decision="hold"),
        backup_evidence_inputs=_green_backup_evidence(),
        evaluated_at="2026-06-27T10:05:00Z",
        operator_decision="go",
        command_plan_reviewed=True,
    )
    blocked_by_handoff = build_project_service_wiring_plan(
        record=record,
        deploy_handoff=not_ready_handoff,
        operator_decision="go",
    )

    assert held.decision == "hold"
    assert "operator decision is not go" in held.blockers
    assert blocked_by_handoff.decision == "hold"
    assert "deploy handoff decision is hold" in blocked_by_handoff.blockers


def test_cloudflare_request_adds_separate_exposure_gate():
    record = _record(cloudflare=True)
    plan = build_project_service_wiring_plan(
        record=record,
        deploy_handoff=_ready_handoff(record),
        operator_decision="go",
    )

    assert plan.decision == "hold"
    assert plan.cloudflare_tunnel_requested is True
    assert any("Cloudflare Tunnel exposure" in blocker for blocker in plan.blockers)
    assert "Cloudflare route" in plan.cloudflare_gate


def test_rejects_invalid_healthcheck_inputs():
    record = _record()
    with pytest.raises(ServerProjectServiceWiringError, match="healthcheck_port"):
        build_project_service_wiring_plan(
            record=record,
            deploy_handoff=_ready_handoff(record),
            operator_decision="go",
            healthcheck_port=70000,
        )
    with pytest.raises(ServerProjectServiceWiringError, match="healthcheck_path"):
        build_project_service_wiring_plan(
            record=record,
            deploy_handoff=_ready_handoff(record),
            operator_decision="go",
            healthcheck_path="../health",
        )


def test_source_has_no_live_install_runtime():
    source = Path("src/server_project_service_wiring.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "shell=True", "os.system")
    for fragment in forbidden:
        assert fragment not in source
