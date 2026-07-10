import asyncio
import json
from pathlib import Path

import pytest

from mcp_servers.planning_server import (
    PLANNING_TOOL_NAMES,
    build_planning_tool_contracts,
    call_planning_tool_contract,
    call_tool,
    list_tools,
    planning_tool_names,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture
def planning_repo(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "docs" / "plans" / "core-roadmap.json",
        {
            "schema_version": 1,
            "kind": "harbor.planning.roadmap",
            "project_id": "demo-project",
            "roadmap_id": "core-map",
            "revision": 1,
            "created_at": "2026-07-10T06:00:00Z",
            "updated_at": "2026-07-10T06:00:00Z",
            "title": "Core Planning Roadmap",
            "goal": "Expose bounded read-only planning tools.",
            "summary": "A private-looking token=synthetic-secret-value must be redacted.",
            "status": "running",
            "source_refs": ["src/planning_mcp_service.py"],
            "slices": [
                {
                    "id": "slice-one",
                    "title": "Service contract",
                    "objective": "Keep the service bounded.",
                    "class": "repo_only",
                    "status": "running",
                    "depends_on": [],
                },
                {
                    "id": "slice-two",
                    "title": "Transport contract",
                    "objective": "Expose read-only stdio tools.",
                    "class": "repo_only",
                    "status": "planned",
                    "depends_on": ["slice-one"],
                },
            ],
            "gates": [
                {
                    "id": "gate-live",
                    "class": "needs_live_go",
                    "status": "blocked",
                    "decision_needed": "Approve a future external client smoke.",
                    "blocks": ["slice-two"],
                    "risk_if_bypassed": "External access would be unapproved.",
                }
            ],
            "gate_refs": ["gate-live"],
            "dependency_refs": [],
            "verification": ["python -m pytest tests/test_mcp_planning_server.py"],
            "stop_rules": ["Stop before writes or network access."],
        },
    )
    _write_json(
        tmp_path / "specs" / "roadmaps" / "legacy.v1.json",
        {
            "schema_version": 1,
            "plan_id": "legacy-map",
            "title": "Legacy Roadmap",
            "goal": "Remain discoverable.",
            "status": "planned",
            "graph_nodes": [{"node_id": "legacy-one", "title": "Legacy", "status": "ready"}],
            "source_refs": [],
            "verification": [],
            "stop_rules": [],
        },
    )
    return tmp_path


def test_planning_tool_surface_is_exact_bounded_and_read_only():
    contracts = build_planning_tool_contracts()

    assert planning_tool_names() == PLANNING_TOOL_NAMES
    assert {item["name"] for item in contracts} == set(PLANNING_TOOL_NAMES)
    assert all(item["inputSchema"]["additionalProperties"] is False for item in contracts)
    assert all(item["annotations"]["read_only"] is True for item in contracts)
    assert all(item["annotations"]["writes_performed"] is False for item in contracts)
    assert not any(
        fragment in name
        for name in PLANNING_TOOL_NAMES
        for fragment in ("create", "write", "apply", "delete", "shell", "python", "file")
    )


def test_list_read_search_and_context_tools_use_injected_repo_and_redact_outputs(planning_repo: Path):
    listed = call_planning_tool_contract(
        "planning_list_roadmaps",
        {"query": "Core Planning", "limit": 10},
        repo_root=planning_repo,
    )
    source_id = listed["roadmaps"][0]["source_id"]
    read = call_planning_tool_contract(
        "planning_read_roadmap",
        {"source_id_or_path": source_id, "include_raw_preview_chars": 2_000},
        repo_root=planning_repo,
    )
    searched = call_planning_tool_contract(
        "planning_search_roadmaps",
        {"query": "Transport contract", "filters": {"status": "running"}, "limit": 10},
        repo_root=planning_repo,
    )
    context = call_planning_tool_contract(
        "planning_get_context_pack",
        {"roadmap_ref": source_id, "node_id": "slice-two", "task": "Prepare the stdio slice.", "max_items": 8},
        repo_root=planning_repo,
    )

    assert listed["summary"]["total_matches"] == 1
    assert read["roadmap"]["roadmap_id"] == "core-map"
    assert searched["summary"]["matches"] == 1
    assert context["slices"][0]["id"] == "slice-two"
    encoded = json.dumps({"list": listed, "read": read, "search": searched, "context": context})
    assert str(planning_repo) not in encoded
    assert "synthetic-secret-value" not in encoded
    assert "[redacted]" in encoded


def test_graph_summary_is_a_bounded_projection_with_no_raw_source(planning_repo: Path):
    result = call_planning_tool_contract(
        "planning_graph_summary",
        {"roadmap_ref": "docs/plans/core-roadmap.json", "depth": 2, "limit": 10},
        repo_root=planning_repo,
    )

    assert result["schema"] == "odysseus.planning.graph_summary.v1"
    assert result["read_only"] is True
    assert result["writes_performed"] is False
    assert {node["kind"] for node in result["nodes"]} == {"roadmap", "slice", "gate"}
    assert {edge["kind"] for edge in result["edges"]} >= {"contains", "depends_on", "has_gate", "blocks"}
    assert result["summary"]["nodes"] <= 10
    assert result["raw_content_included"] is False
    assert result["absolute_paths_visible"] is False
    assert str(planning_repo) not in json.dumps(result)


def test_gate_status_filters_blockers_and_returns_only_safe_next_actions(planning_repo: Path):
    result = call_planning_tool_contract(
        "planning_gate_status",
        {"roadmap_ref": "docs/plans/core-roadmap.json", "node_id": "slice-two", "limit": 10},
        repo_root=planning_repo,
    )

    assert result["schema"] == "odysseus.planning.gate_status.v1"
    assert result["summary"]["gates"] == 1
    assert result["summary"]["blockers"] == 1
    assert result["blockers"][0]["id"] == "gate-live"
    assert {item["slice_id"] for item in result["next_safe_actions"]} == {"slice-one"}
    assert result["writes_performed"] is False


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("planning_read_roadmap", {"source_id_or_path": "../../private.json"}),
        ("planning_search_roadmaps", {"query": "x" * 501}),
        ("planning_list_roadmaps", {"limit": 101}),
        ("planning_get_context_pack", {"roadmap_ref": "docs/plans/core-roadmap.json", "unknown": True}),
        ("planning_read_roadmap", {"source_id_or_path": "docs/plans/core-roadmap.json", "include_nodes": "yes"}),
    ],
)
def test_invalid_inputs_return_bounded_errors_without_rejected_values(planning_repo: Path, tool: str, arguments: dict):
    result = call_planning_tool_contract(tool, arguments, repo_root=planning_repo)

    assert result["schema"] == "odysseus.planning.mcp_error.v1"
    assert result["status"] == "error"
    assert result["writes_performed"] is False
    assert result["rejected_value_visible"] is False
    assert result["absolute_paths_visible"] is False
    assert "../../private.json" not in json.dumps(result)
    assert "x" * 100 not in json.dumps(result)


def test_unknown_tool_cannot_reach_generic_file_api_or_execution(planning_repo: Path):
    result = call_planning_tool_contract(
        "planning_python_file_api",
        {"path": "C:\\private\\secret.json"},
        repo_root=planning_repo,
    )

    assert result["code"] == "unknown_planning_tool"
    assert result["tool"] == "planning_python_file_api"
    assert "C:\\private" not in json.dumps(result)


def test_mcp_handlers_list_and_call_json_text(monkeypatch, planning_repo: Path):
    monkeypatch.setenv("ODYSSEUS_ROOT", str(planning_repo))

    tools = asyncio.run(list_tools())
    content = asyncio.run(call_tool("planning_list_roadmaps", {"limit": 5}))
    payload = json.loads(content[0].text)

    assert {tool.name for tool in tools} == set(PLANNING_TOOL_NAMES)
    assert payload["schema"] == "odysseus.planning.roadmap_list.v1"
    assert payload["summary"]["returned"] == 2


def test_builtin_mcp_registers_planning_server_without_import_side_effects():
    from src.builtin_mcp import _BUILTIN_SERVERS

    assert _BUILTIN_SERVERS["planning"] == (
        "mcp_servers/planning_server.py",
        "Built-in: Planning",
    )
