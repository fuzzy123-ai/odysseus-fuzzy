import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.system_update_routes import setup_system_update_routes
from src.system_update_status import (
    StatusCommandResult,
    collect_system_update_status,
    parse_restic_snapshots_json,
    parse_systemctl_show,
    start_system_update_action,
)


def test_parse_systemctl_show_ignores_non_key_value_lines():
    assert parse_systemctl_show("ActiveState=active\njunk\nResult=success\n") == {
        "ActiveState": "active",
        "Result": "success",
    }


def test_parse_restic_snapshots_json_is_bounded_and_sorted():
    raw = json.dumps(
        [
            {
                "short_id": "older12345678",
                "time": "2026-06-19T10:00:00Z",
                "hostname": "homebase",
                "paths": ["/srv/old"],
            },
            {
                "id": "newer1234567890",
                "time": "2026-06-20T10:00:00Z",
                "hostname": "homebase",
                "paths": ["/srv/one", "/srv/two", "/srv/three", "/srv/four", "/srv/five", "/srv/six"],
                "tags": ["pre-update"],
            },
        ]
    )

    snapshots = parse_restic_snapshots_json(raw, limit=1)

    assert snapshots == [
        {
            "id": "newer1234567",
            "time": "2026-06-20T10:00:00Z",
            "hostname": "homebase",
            "paths": ["/srv/one", "/srv/two", "/srv/three", "/srv/four", "/srv/five"],
            "tags": ["pre-update"],
        }
    ]


def test_collect_status_degrades_without_host_tools():
    status = collect_system_update_status(
        env={},
        runner=lambda argv, timeout: StatusCommandResult(exit_code=1, stderr="should not run"),
        tool_resolver=lambda name: None,
        version_provider=lambda **_: {"status": "current", "commit": "abc123"},
        recent_changes_provider=lambda force: {"available": True, "latest": None, "history": []},
    )

    assert status["status"] == "success"
    assert status["version"]["status"] == "current"
    assert status["capabilities"]["systemctl"] is False
    assert status["schedule"]["timer"]["available"] is False
    assert status["backups"]["available"] is False
    assert status["recent_changes"]["available"] is True
    assert status["actions"]["update_now_enabled"] is False


def test_collect_status_links_recent_changes_and_forces_on_update_check():
    calls = []

    def fake_recent(force_collect):
        calls.append(force_collect)
        return {
            "available": True,
            "latest": {
                "id": "snap-1",
                "generated_at": "2026-06-23T10:00:00Z",
                "since": "2026-06-22T22:00:00Z",
                "hours": 12,
                "summary": ["local changes found"],
                "persisted": True,
                "patch_notes": "Patch notes snapshot",
                "repo_root": "/secret/repo",
                "tracked_changes": [{"path": "src/x.py"}],
            },
            "history": [{"id": "snap-1", "summary": ["local changes found"]}],
        }

    status = collect_system_update_status(
        env={},
        runner=lambda argv, timeout: StatusCommandResult(exit_code=1, stderr="should not run"),
        tool_resolver=lambda name: None,
        version_provider=lambda **_: {"status": "current"},
        recent_changes_provider=fake_recent,
        force_version_refresh=True,
    )

    assert calls == [True]
    assert status["recent_changes"]["latest"]["id"] == "snap-1"
    assert status["recent_changes"]["latest"]["patch_notes"] == "Patch notes snapshot"
    assert "repo_root" not in status["recent_changes"]["latest"]
    assert "tracked_changes" not in status["recent_changes"]["latest"]


def test_collect_status_parses_systemd_and_restic(tmp_path):
    wrapper = tmp_path / "odysseus-auto-update.sh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    def fake_runner(argv, timeout):
        calls.append(argv)
        if argv[:4] == ("systemctl", "--user", "show", "odysseus-auto-update.timer"):
            return StatusCommandResult(
                exit_code=0,
                stdout=(
                    "LoadState=loaded\n"
                    "ActiveState=active\n"
                    "SubState=waiting\n"
                    "UnitFileState=enabled\n"
                    "NextElapseUSecRealtime=Sun 2026-06-21 04:29:00 CEST\n"
                ),
            )
        if argv[:4] == ("systemctl", "--user", "show", "odysseus-auto-update.service"):
            return StatusCommandResult(
                exit_code=0,
                stdout=(
                    "LoadState=loaded\n"
                    "ActiveState=inactive\n"
                    "SubState=dead\n"
                    "Result=success\n"
                    "ExecMainStatus=0\n"
                    "ExecMainExitTimestamp=Sat 2026-06-20 22:10:00 CEST\n"
                ),
            )
        if argv[:4] == ("systemctl", "--user", "show", "odysseus-homeserver-backup.timer"):
            return StatusCommandResult(
                exit_code=0,
                stdout="LoadState=loaded\nActiveState=active\nSubState=waiting\n",
            )
        if argv[:4] == ("systemctl", "--user", "show", "odysseus-homeserver-backup.service"):
            return StatusCommandResult(
                exit_code=0,
                stdout="LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n",
            )
        if argv[:3] == ("restic", "-r", "/repo"):
            return StatusCommandResult(
                exit_code=0,
                stdout=json.dumps(
                    [
                        {
                            "short_id": "351b0d54abcd",
                            "time": "2026-06-20T20:00:00Z",
                            "hostname": "homebase",
                            "paths": ["/opt/odysseus"],
                            "tags": ["pre-update"],
                        }
                    ]
                ),
            )
        raise AssertionError(f"unexpected argv: {argv!r}")

    status = collect_system_update_status(
        env={
            "ODYSSEUS_AUTO_UPDATE_WRAPPER": str(wrapper),
            "ODYSSEUS_UPDATER_LIVE_ENABLED": "1",
            "RESTIC_REPOSITORY": "/repo",
        },
        runner=fake_runner,
        tool_resolver=lambda name: f"/usr/bin/{name}",
        version_provider=lambda **_: {"status": "outdated", "commit": "abc123"},
    )

    assert status["updater"]["runner_available"] is True
    assert status["schedule"]["timer"]["active_state"] == "active"
    assert status["updater"]["service"]["result"] == "success"
    assert status["backups"]["latest_snapshot"]["id"] == "351b0d54abcd"
    assert status["actions"]["backup_now_enabled"] is True
    assert status["actions"]["update_now_enabled"] is True
    assert ("restic", "-r", "/repo", "snapshots", "--json", "--latest", "10") in calls


def test_collect_status_redacts_secret_bearing_errors():
    def fake_runner(argv, timeout):
        if argv[0] == "systemctl":
            return StatusCommandResult(exit_code=0, stdout="ActiveState=inactive\n")
        return StatusCommandResult(exit_code=1, stderr="password hunter2 failed")

    status = collect_system_update_status(
        env={"RESTIC_REPOSITORY": "/repo"},
        runner=fake_runner,
        tool_resolver=lambda name: f"/usr/bin/{name}",
        version_provider=lambda **_: {"status": "current"},
        recent_changes_provider=lambda force: {"available": True, "latest": None, "history": []},
    )

    assert status["backups"]["reason"] == "[redacted]"


def test_update_status_route_requires_admin_by_default():
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: False)
    app.include_router(setup_system_update_routes())

    response = TestClient(app).get("/api/admin/system/update-status")

    assert response.status_code == 403


def test_update_status_route_allows_auth_disabled(monkeypatch):
    import routes.system_update_routes as system_update_routes

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        system_update_routes,
        "collect_system_update_status",
        lambda: {"status": "success", "version": {"status": "current"}},
    )
    app = FastAPI()
    app.include_router(system_update_routes.setup_system_update_routes())

    response = TestClient(app).get("/api/admin/system/update-status")

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_update_check_route_forces_version_refresh(monkeypatch):
    import routes.system_update_routes as system_update_routes

    calls = []

    def fake_collect(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "version": {"status": "current"}}

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(system_update_routes, "collect_system_update_status", fake_collect)
    app = FastAPI()
    app.include_router(system_update_routes.setup_system_update_routes())

    response = TestClient(app).post("/api/admin/system/update-check")

    assert response.status_code == 200
    assert calls == [{"force_version_refresh": True}]


def test_backup_action_starts_only_the_backup_systemd_service(tmp_path):
    wrapper = tmp_path / "odysseus-auto-update.sh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    def fake_runner(argv, timeout):
        calls.append(argv)
        if argv[:3] == ("systemctl", "--user", "show"):
            return StatusCommandResult(exit_code=0, stdout="LoadState=loaded\nActiveState=inactive\nResult=success\n")
        if argv[:3] == ("restic", "-r", "/repo"):
            return StatusCommandResult(exit_code=0, stdout="[]")
        if argv == ("systemctl", "--user", "start", "--no-block", "odysseus-homeserver-backup.service"):
            return StatusCommandResult(exit_code=0)
        raise AssertionError(f"unexpected argv: {argv!r}")

    result = start_system_update_action(
        "backup_now",
        env={
            "ODYSSEUS_AUTO_UPDATE_WRAPPER": str(wrapper),
            "ODYSSEUS_UPDATER_LIVE_ENABLED": "1",
            "RESTIC_REPOSITORY": "/repo",
        },
        runner=fake_runner,
        tool_resolver=lambda name: f"/usr/bin/{name}",
        version_provider=lambda **_: {"status": "current"},
    )

    assert result["status"] == "started"
    assert result["started"] is True
    assert ("systemctl", "--user", "start", "--no-block", "odysseus-homeserver-backup.service") in calls


def test_update_action_is_blocked_when_live_gate_is_missing(tmp_path):
    wrapper = tmp_path / "odysseus-auto-update.sh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_runner(argv, timeout):
        if argv[:3] == ("systemctl", "--user", "show"):
            return StatusCommandResult(exit_code=0, stdout="LoadState=loaded\nActiveState=inactive\nResult=success\n")
        if argv[:3] == ("restic", "-r", "/repo"):
            return StatusCommandResult(exit_code=0, stdout="[]")
        raise AssertionError(f"unexpected argv: {argv!r}")

    result = start_system_update_action(
        "update_now",
        env={
            "ODYSSEUS_AUTO_UPDATE_WRAPPER": str(wrapper),
            "RESTIC_REPOSITORY": "/repo",
        },
        runner=fake_runner,
        tool_resolver=lambda name: f"/usr/bin/{name}",
        version_provider=lambda **_: {"status": "outdated"},
        recent_changes_provider=lambda force: {"available": True, "latest": None, "history": []},
    )

    assert result["status"] == "blocked"
    assert result["started"] is False
    assert "ODYSSEUS_UPDATER_LIVE_ENABLED is not enabled" in result["blockers"]


def test_update_action_starts_auto_update_service_when_gates_are_green(tmp_path):
    wrapper = tmp_path / "odysseus-auto-update.sh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    def fake_runner(argv, timeout):
        calls.append(argv)
        if argv[:3] == ("systemctl", "--user", "show"):
            return StatusCommandResult(exit_code=0, stdout="LoadState=loaded\nActiveState=inactive\nResult=success\n")
        if argv[:3] == ("restic", "-r", "/repo"):
            return StatusCommandResult(exit_code=0, stdout='[{"short_id":"abc123","time":"2026-06-20T20:00:00Z"}]')
        if argv == ("systemctl", "--user", "start", "--no-block", "odysseus-auto-update.service"):
            return StatusCommandResult(exit_code=0)
        raise AssertionError(f"unexpected argv: {argv!r}")

    result = start_system_update_action(
        "update_now",
        env={
            "ODYSSEUS_AUTO_UPDATE_WRAPPER": str(wrapper),
            "ODYSSEUS_UPDATER_LIVE_ENABLED": "true",
            "RESTIC_REPOSITORY": "/repo",
        },
        runner=fake_runner,
        tool_resolver=lambda name: f"/usr/bin/{name}",
        version_provider=lambda **_: {"status": "outdated"},
        recent_changes_provider=lambda force: {"available": True, "latest": None, "history": []},
    )

    assert result["status"] == "started"
    assert ("systemctl", "--user", "start", "--no-block", "odysseus-auto-update.service") in calls


def test_action_routes_delegate_to_safe_action_runner(monkeypatch):
    import routes.system_update_routes as system_update_routes

    calls = []

    def fake_start(action):
        calls.append(action)
        return {"status": "started", "action": action, "started": True}

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(system_update_routes, "start_system_update_action", fake_start)
    app = FastAPI()
    app.include_router(system_update_routes.setup_system_update_routes())

    backup_response = TestClient(app).post("/api/admin/system/backup-now")
    update_response = TestClient(app).post("/api/admin/system/update-now")

    assert backup_response.status_code == 200
    assert update_response.status_code == 200
    assert calls == ["backup_now", "update_now"]
