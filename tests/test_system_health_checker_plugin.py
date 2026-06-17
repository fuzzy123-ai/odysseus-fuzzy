import logging
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.system_health_checker.health_model import (
    AlertSummary,
    CollectorStatus,
    HealthModelError,
    HealthSnapshot,
    HealthState,
    build_agent_offline_snapshot,
)
from plugins.system_health_checker.plugin import PLUGIN, setup
from src.plugin_capability_boundary import validate_plugin_capability_boundary


@dataclass
class _PluginContext:
    app: FastAPI
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.system_health_checker"))

    def add_router(self, router):
        self.app.include_router(router)


def test_manifest_keeps_health_checker_as_operations_plugin():
    assert PLUGIN["name"] == "System Health Checker"
    assert PLUGIN["category"] == "Operations"
    assert PLUGIN["permission"] == "admin"
    assert PLUGIN["kind"] == "ui"
    assert PLUGIN["capabilities"] == ["local_api"]
    assert PLUGIN["ui"]["open"] == "/api/plugins/system_health_checker/app"


def test_manifest_passes_plugin_capability_boundary():
    report = validate_plugin_capability_boundary(PLUGIN)

    assert report.ok
    assert report.error_codes == ()
    assert report.warning_codes == ()


def test_offline_snapshot_is_unknown_and_has_setup_hint():
    snapshot = build_agent_offline_snapshot(observed_at="2026-06-16T12:00:00Z")
    payload = snapshot.to_dict()

    assert payload["schema_version"] == 1
    assert payload["state"] == "unknown"
    assert payload["collectors"][0]["kind"] == "agent"
    assert payload["collectors"][0]["details"]["setup_hint"].startswith("Install and start")
    assert payload["alerts"]["highest_severity"] == "unknown"


def test_snapshot_state_uses_highest_collector_or_alert_severity():
    collector = CollectorStatus.create(
        kind="disk",
        state="warn",
        summary="Disk usage is high",
        observed_at="2026-06-16T12:00:00Z",
        details={"used_percent": 91},
    )
    alerts = AlertSummary.create(
        state="critical",
        active_count=1,
        highest_severity="critical",
        messages=["SMART critical"],
    )
    snapshot = HealthSnapshot.create(
        agent_id="agent-1",
        observed_at="2026-06-16T12:00:00Z",
        collectors=[collector],
        alerts=alerts,
    )

    assert snapshot.state == HealthState.CRITICAL
    assert snapshot.to_dict()["state"] == "critical"


def test_snapshot_requires_at_least_one_collector():
    alerts = AlertSummary.create(
        state="ok",
        active_count=0,
        highest_severity="ok",
        messages=[],
    )

    with pytest.raises(HealthModelError, match="collectors must not be empty"):
        HealthSnapshot.create(
            agent_id="agent-1",
            observed_at="2026-06-16T12:00:00Z",
            collectors=[],
            alerts=alerts,
        )


def test_health_route_returns_offline_snapshot(tmp_path):
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.get("/api/plugins/system_health_checker/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "unknown"
    assert payload["collectors"][0]["summary"] == "Health agent is not connected"


def test_plugin_app_route_renders_without_host_access(tmp_path):
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.get("/api/plugins/system_health_checker/app")

    assert response.status_code == 200
    assert "Host-agent integration is not configured yet" in response.text
    assert "does not run host commands" in response.text
    assert "/api/plugins/system_health_checker/health" in response.text
    assert "Health snapshot unavailable" in response.text


def test_plugin_file_loader_imports_health_model_without_package_context():
    plugin_path = Path("plugins/system_health_checker/plugin.py")
    spec = importlib.util.spec_from_file_location("odysseus_plugin_system_health_checker", plugin_path)
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module.PLUGIN["name"] == "System Health Checker"
