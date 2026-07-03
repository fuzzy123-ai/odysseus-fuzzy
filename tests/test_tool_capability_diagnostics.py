import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import diagnostics_routes
from src import tool_capability_maintenance
from src.tool_capability_maintenance import (
    append_tool_capability_raptorgraph_event,
    build_tool_capability_snapshot,
    build_tool_memory_records,
    build_tool_raptorgraph_event,
    persist_tool_capability_knowledge,
    read_tool_capability_diagnostics,
)


def test_tool_capability_diagnostics_reads_latest_and_graph_store(tmp_path):
    data_dir = tmp_path / "knowledge"
    graph_dir = tmp_path / "graph"
    snapshot = build_tool_capability_snapshot(
        reason="unit-test",
        commit="abc123",
        index_status={"status": "ok", "healthy": True},
        generated_at="2026-07-03T00:00:00+00:00",
    )
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)
    persist_tool_capability_knowledge(
        snapshot=snapshot,
        memory_records=records,
        raptorgraph_event=event,
        data_dir=data_dir,
    )
    graph_result = append_tool_capability_raptorgraph_event(event, root=graph_dir)

    result = read_tool_capability_diagnostics(data_dir=data_dir, raptorgraph_dir=graph_dir)
    encoded = json.dumps(result, sort_keys=True)

    assert result["status"] == "success"
    assert result["snapshot"]["id"] == snapshot["id"]
    assert result["snapshot"]["index_status"]["status"] == "ok"
    assert result["memory_records"]["count"] == len(records)
    assert result["raptorgraph"]["event_present"] is True
    assert result["raptorgraph"]["latest_event_id"] == graph_result.event_id
    assert result["raw_content_visible"] is False
    assert "C:\\\\" not in encoded
    assert "Authorization" not in encoded


def test_tool_capability_diagnostics_route_is_admin_gated_and_redacted(tmp_path, monkeypatch):
    data_dir = tmp_path / "knowledge"
    graph_dir = tmp_path / "graph"
    monkeypatch.setattr(tool_capability_maintenance, "TOOL_CAPABILITY_KNOWLEDGE_DIR", str(data_dir))
    monkeypatch.setattr(tool_capability_maintenance, "TOOL_CAPABILITY_RAPTORGRAPH_DIR", str(graph_dir))
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))
    snapshot = build_tool_capability_snapshot(
        reason="route-test",
        commit="def456",
        index_status={"status": "ok", "healthy": True},
        generated_at="2026-07-03T00:00:00+00:00",
    )
    records = build_tool_memory_records(snapshot)
    event = build_tool_raptorgraph_event(snapshot, memory_records=records)
    persist_tool_capability_knowledge(
        snapshot=snapshot,
        memory_records=records,
        raptorgraph_event=event,
        data_dir=data_dir,
    )
    append_tool_capability_raptorgraph_event(event, root=graph_dir)

    response = TestClient(app).get("/api/diagnostics/tool-capabilities")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["snapshot"]["id"] == snapshot["id"]
    assert payload["memory_records"]["count"] == len(records)
    assert payload["raptorgraph"]["event_present"] is True
    assert "Authorization" not in encoded
    assert "C:\\\\" not in encoded
