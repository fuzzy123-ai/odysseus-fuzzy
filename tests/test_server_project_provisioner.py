import json
from pathlib import Path

import pytest

from src.server_project_provisioner import (
    ServerProjectProvisioningError,
    build_project_workspace_provisioning_plan,
    provision_project_workspace,
)
from src.server_project_registry import ServerProjectRegistry


def _record(title: str = "Kundenportal MVP") -> ServerProjectRecord:
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title=title,
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def test_workspace_provisioning_plan_blocks_without_live_go():
    record = _record()

    plan = build_project_workspace_provisioning_plan(
        record=record,
        created_at="2026-06-27T10:05:00Z",
    )

    assert plan.decision == "hold"
    assert plan.can_execute is False
    assert "operator decision is not go" in plan.blockers
    assert plan.workspace_root == "projects/kundenportal-mvp"
    assert plan.repo_directory == "projects/kundenportal-mvp/repo"
    assert plan.assignment.audit_summary()["worker_workspace_root"] == "projects/kundenportal-mvp"
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_workspace_provisioning_creates_local_workspace_under_projects_root(tmp_path: Path):
    record = _record()

    report = provision_project_workspace(
        record=record,
        projects_root=tmp_path,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )

    assert report.status == "provisioned"
    assert report.executed is True
    assert report.blockers == ()
    assert set(report.created_paths) == {
        "projects/kundenportal-mvp",
        "projects/kundenportal-mvp/repo",
        "projects/kundenportal-mvp/.odysseus",
    }
    assert (tmp_path / "kundenportal-mvp" / "repo").is_dir()
    marker = json.loads((tmp_path / "kundenportal-mvp" / ".odysseus" / "project.json").read_text(encoding="utf-8"))
    assert marker["project_slug"] == "kundenportal-mvp"
    assert marker["chat_scope"] == "project:kundenportal-mvp"
    assert marker["repo_directory"] == "projects/kundenportal-mvp/repo"
    assert str(tmp_path) not in json.dumps(marker)


def test_workspace_provisioning_is_idempotent(tmp_path: Path):
    record = _record()
    kwargs = {
        "record": record,
        "projects_root": tmp_path,
        "created_at": "2026-06-27T10:05:00Z",
        "live_enabled": True,
        "operator_decision": "go",
    }

    first = provision_project_workspace(**kwargs)
    second = provision_project_workspace(**kwargs)

    assert first.status == "provisioned"
    assert second.status == "provisioned"
    assert second.created_paths == ()
    assert set(second.reused_paths) == {
        "projects/kundenportal-mvp",
        "projects/kundenportal-mvp/repo",
        "projects/kundenportal-mvp/.odysseus",
    }


def test_workspace_provisioning_rejects_bad_projects_root_and_record_type():
    record = _record()

    with pytest.raises(ServerProjectProvisioningError, match="explicit directory"):
        provision_project_workspace(
            record=record,
            projects_root=".",
            created_at="2026-06-27T10:05:00Z",
            live_enabled=True,
            operator_decision="go",
        )

    with pytest.raises(ServerProjectProvisioningError, match="record"):
        provision_project_workspace(
            record=object(),
            projects_root="server-projects",
            created_at="2026-06-27T10:05:00Z",
            live_enabled=True,
            operator_decision="go",
        )


def test_workspace_provisioning_supports_long_project_titles():
    record = _record("Sehr Langes Kundenportal Mit Reporting Und Dashboard Und Export Und Monitoring Und Mehr")

    plan = build_project_workspace_provisioning_plan(
        record=record,
        created_at="2026-06-27T10:05:00Z",
        live_enabled=True,
        operator_decision="go",
    )

    assert plan.can_execute is True
    assert len(plan.assignment.agent_identity.run_id) <= 80


def test_source_has_no_provider_or_shell_runtime():
    source = Path("src/server_project_provisioner.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "systemctl", "cloudflared", "shell=True")
    for fragment in forbidden:
        assert fragment not in source
