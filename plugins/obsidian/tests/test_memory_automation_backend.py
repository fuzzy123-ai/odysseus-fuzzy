import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
from backend.memory_automation import (
    AUTOMATION_REPORT_PATH,
    JOB_ID,
    memory_automation_status,
    run_memory_automation,
)
from plugin import setup


def test_memory_automation_status_surfaces_pending_actions_and_cost_controller():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Source.md"), "w", encoding="utf-8") as f:
            f.write("# Source\n\nblob automation demo\n")

        status = memory_automation_status(tmpdir)

        assert status["job_id"] == JOB_ID
        assert "sync_memory_ledger" in status["pending_actions"]
        assert "build_derived_index" in status["pending_actions"]
        assert status["cost_controller"]["cooldown_seconds"] >= 0
        assert status["safety"] == {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "allowed_actions": ["sync_memory_ledger", "build_derived_index"],
        }


def test_memory_automation_run_executes_low_risk_actions_and_respects_cooldown(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Source.md"), "w", encoding="utf-8") as f:
            f.write("# Source\n\nblob automation demo\n")

        monkeypatch.setattr("backend.memory_automation._cooldown_seconds", lambda: 3600)
        monkeypatch.setattr("backend.memory_automation.vault_service.unlocked_vault_path_for_owner", lambda owner: tmpdir)

        first = run_memory_automation(owner=None, trigger="periodic", context={"event": "tick"}, force=False)
        report_path = os.path.join(tmpdir, *AUTOMATION_REPORT_PATH.split("/"))
        second = run_memory_automation(owner=None, trigger="periodic", context={"event": "tick"}, force=False)
        forced = run_memory_automation(owner=None, trigger="manual", context={}, force=True)

        assert first["skipped"] is False
        assert "sync_memory_ledger" in first["actions_executed"]
        assert "build_derived_index" in first["actions_executed"]
        assert os.path.exists(report_path)
        assert second == {"skipped": True, "reason": "cooldown_active", "actions_executed": []}
        assert forced["skipped"] is False
        assert forced["safety"]["source_note_writes"] is False
        assert forced["safety"]["derived_data_writes_only"] is True


@pytest.mark.asyncio
async def test_memory_automation_routes_expose_status_and_run(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Source.md"), "w", encoding="utf-8") as f:
            f.write("# Source\n\nroute automation demo\n")

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "_require_vault_scope", lambda request, required: "alice")
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")
        monkeypatch.setattr("backend.memory_automation.vault_service.unlocked_vault_path_for_owner", lambda owner: tmpdir)

        request = SimpleNamespace(state=SimpleNamespace(api_token=False))
        status = await obsidian_routes.memory_automation_status_route(request)
        result = await obsidian_routes.memory_automation_run_route(request, force=True)

        assert "sync_memory_ledger" in status["pending_actions"]
        assert result["skipped"] is False
        assert "build_derived_index" in result["actions_executed"]


def test_plugin_setup_registers_memory_automation_job():
    registered_jobs = []

    class MockContext:
        logger = SimpleNamespace(warning=lambda *args, **kwargs: None)

        def add_router(self, router):
            pass

        def register_tool(self, spec):
            pass

        def register_context_provider(self, spec):
            pass

        def register_consolidation_job(self, spec):
            registered_jobs.append(spec)

    setup(MockContext())

    job_ids = [spec["id"] for spec in registered_jobs]
    assert JOB_ID in job_ids
