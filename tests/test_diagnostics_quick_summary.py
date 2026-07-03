import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import diagnostics_routes
from src import ai_activity_ledger, memory_provenance_ledger, tool_capability_maintenance
from src.diagnostics_quick_summary import build_diagnostics_quick_summary
from src.tool_capability_maintenance import (
    append_tool_capability_raptorgraph_event,
    build_tool_capability_snapshot,
    build_tool_memory_records,
    build_tool_raptorgraph_event,
    persist_tool_capability_knowledge,
)


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _admin_app(*, user="admin", admins=("admin",)) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))
    return app


def test_diagnostics_quick_summary_compacts_sources_without_records():
    payload = build_diagnostics_quick_summary(
        ai_activity={
            "status": "success",
            "day": "2026-07-03",
            "count": 2,
            "total_matches": 2,
            "records": [{"prompt_type": "private"}],
            "summary": {"by_status": {"success": 1, "error": 1}, "by_surface": {"memory": 2}, "skipped": 1},
        },
        memory_provenance={
            "status": "success",
            "day": "2026-07-03",
            "count": 1,
            "summary": {"by_status": {"written": 1}, "by_event_type": {"raptorgraph_mutation": 1}},
            "records": [{"metadata": "private"}],
        },
        tool_capabilities={
            "status": "success",
            "snapshot": {
                "id": "tool-capability-latest",
                "generated_at": "2026-07-03T00:00:00+00:00",
                "builtin_tool_count": 12,
                "schema_tool_count": 10,
                "index_status": {"status": "ok", "healthy": True},
                "domains": {"filesystem_code": 4},
            },
            "memory_records": {"count": 2, "ids": ("memory-a",)},
            "raptorgraph": {"event_present": True, "store_event_count": 1},
        },
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.diagnostics_quick_summary.v1"
    assert payload["status"] == "warn"
    assert payload["ai_activity"]["records_included"] is False
    assert payload["memory_provenance"]["records_included"] is False
    assert payload["tool_capabilities"]["record_ids_included"] is False
    assert payload["ai_activity"]["error_count"] == 1
    assert payload["tool_capabilities"]["runtime_index_status"] == "ok"
    assert "prompt_type" not in encoded
    assert "memory-a" not in encoded
    assert "private" not in encoded


def test_diagnostics_quick_summary_route_requires_admin():
    response = TestClient(_admin_app(user="alice", admins=("admin",))).get("/api/diagnostics/quick-summary")

    assert response.status_code == 403


def test_diagnostics_quick_summary_route_is_admin_gated_and_redacted(tmp_path, monkeypatch):
    ai_dir = tmp_path / "ai"
    memory_dir = tmp_path / "memory"
    tool_dir = tmp_path / "tools"
    graph_dir = tmp_path / "graph"
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(ai_dir))
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(memory_dir))
    monkeypatch.setattr(tool_capability_maintenance, "TOOL_CAPABILITY_KNOWLEDGE_DIR", str(tool_dir))
    monkeypatch.setattr(tool_capability_maintenance, "TOOL_CAPABILITY_RAPTORGRAPH_DIR", str(graph_dir))
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)

    ai_activity_ledger.record_ai_activity(
        owner="admin",
        surface="memory",
        prompt_type="memory_file_extract",
        provider="local",
        endpoint_url="http://127.0.0.1:11434/v1/chat/completions",
        model="gemma",
        messages=[{"role": "user", "content": "private uploaded document"}],
        status="success",
    )
    memory_provenance_ledger.record_memory_provenance(
        "raptorgraph_mutation",
        owner="system",
        surface="tool_capability_maintenance",
        source="unit",
        action="append_event",
        status="written",
        reason="ok",
        source_hash="sha256:abc",
        memory_record_ids=("tool-capability-test",),
        graph_event_id="tool-rg-test",
    )
    snapshot = build_tool_capability_snapshot(
        reason="route-test",
        commit="def456",
        index_status={"status": "ok", "healthy": True},
        generated_at="2026-07-03T00:00:00+00:00",
    )
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)
    persist_tool_capability_knowledge(snapshot=snapshot, memory_records=records, raptorgraph_event=event, data_dir=tool_dir)
    append_tool_capability_raptorgraph_event(event, root=graph_dir)

    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))
    response = TestClient(app).get("/api/diagnostics/quick-summary")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["ai_activity"]["recent_count"] == 1
    assert payload["memory_provenance"]["recent_count"] >= 1
    assert payload["tool_capabilities"]["snapshot_available"] is True
    assert payload["raw_records_included"] is False
    assert "private uploaded document" not in encoded
    assert "/v1/chat/completions" not in encoded
    assert "tool-capability-test" not in encoded
