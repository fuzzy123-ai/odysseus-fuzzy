import asyncio
import json
from pathlib import Path

import pytest

from mcp_servers.planning_server import (
    PLANNING_COMPATIBILITY_TOOL_NAMES,
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


@pytest.fixture
def definition_store():
    from src.planning_revision_store import PlanningRevisionStore

    legacy_definition = {
        "schema_version": 1,
        "kind": "harbor.planning.roadmap",
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "revision": 7,
        "created_at": "2026-07-15T06:00:00Z",
        "updated_at": "2026-07-15T06:00:00Z",
        "title": "Core Planning Roadmap",
        "goal": "Expose the Definition v2 MCP boundary.",
        "status": "approved",
        "slice_queue": [
            {
                "id": "definition-transport",
                "title": "Definition transport",
                "objective": "Expose immutable Planning intent.",
                "status": "running",
                "depends_on": [],
                "gate_ids": ["definition-go"],
            }
        ],
        "gate_queue": [
            {
                "id": "definition-go",
                "class": "repo",
                "state": "blocked",
                "decision": "synthetic-runtime-decision",
                "decision_needed": "Confirm the definition-only transport.",
                "safe_default": "Keep the transport read-only.",
                "blocks": ["definition-transport"],
            }
        ],
        "source_refs": ["mcp_servers/planning_server.py"],
    }
    return PlanningRevisionStore(
        [("owner-1", legacy_definition, "core-map.json")],
        cursor_secret=b"planning-mcp-transport-test-secret",
    )


def test_planning_tool_surface_is_exact_bounded_and_read_only():
    contracts = build_planning_tool_contracts()

    assert planning_tool_names() == PLANNING_TOOL_NAMES
    assert {item["name"] for item in contracts} == set(PLANNING_TOOL_NAMES)
    assert all(item["inputSchema"]["additionalProperties"] is False for item in contracts)
    assert all(item["annotations"]["read_only"] is True for item in contracts)
    assert all(item["annotations"]["writes_performed"] is False for item in contracts)
    assert PLANNING_TOOL_NAMES == (
        "planning_list_roadmaps",
        "planning_read_roadmap",
        "planning_search_roadmaps",
        "planning_get_context_pack",
        "planning_graph_summary",
        "planning_read_gate_definitions",
        "planning_create_agent_handoff",
    )
    assert not any(
        fragment in name
        for name in PLANNING_TOOL_NAMES
        for fragment in ("write", "apply", "delete", "shell", "python", "file")
    )
    assert "planning_gate_status" not in PLANNING_TOOL_NAMES
    assert "planning_mark_status" not in PLANNING_TOOL_NAMES


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
    assert '"status"' not in json.dumps(result, sort_keys=True)


def test_gate_definition_tool_drops_legacy_runtime_fields(
    planning_repo: Path,
    definition_store,
):
    result = call_planning_tool_contract(
        "planning_read_gate_definitions",
        {
            "project_id": "demo-project",
            "roadmap_id": "core-map",
            "revision_or_latest_approved": 7,
            "node_id": "definition-transport",
        },
        repo_root=planning_repo,
        definition_store=definition_store,
        definition_owner="owner-1",
    )

    assert result["schema_id"] == "odysseus.planning.gate_definitions.v2"
    assert result["revision"] == 7
    assert result["gate_definitions"][0]["gate_id"] == "definition-go"
    encoded = json.dumps(result, sort_keys=True)
    assert "synthetic-runtime-decision" not in encoded
    assert '"state"' not in encoded
    assert '"decision"' not in encoded
    assert '"status"' not in encoded
    assert "blockers" not in result
    assert "next_safe_actions" not in result
    assert result["writes_performed"] is False


def test_agent_handoff_tool_is_hash_pinned_and_cannot_launch(
    planning_repo: Path,
    definition_store,
):
    result = call_planning_tool_contract(
        "planning_create_agent_handoff",
        {
            "project_id": "demo-project",
            "roadmap_id": "core-map",
            "revision_or_latest_approved": "latest_approved",
        },
        repo_root=planning_repo,
        definition_store=definition_store,
        definition_owner="owner-1",
    )

    assert result["schema_id"] == "odysseus.agent.plan_handoff.v1"
    assert result["revision"] == 7
    assert result["content_hash"].startswith("sha256:")
    assert result["composer_text"] == (
        f"/abc run roadmap:core-map@7 hash:{result['content_hash']}"
    )
    assert result["launch_authorized"] is False
    assert result["read_only"] is True
    assert set(result).isdisjoint(
        {"skill", "skills", "model", "models", "run_id", "workflow_id", "command", "auto_submit"}
    )


@pytest.mark.parametrize("tool", PLANNING_COMPATIBILITY_TOOL_NAMES)
def test_deprecated_runtime_tool_dispatch_is_hidden_and_performs_zero_reads_or_writes(
    planning_repo: Path,
    tool: str,
):
    class ExplodingDefinitionStore:
        def get_roadmap(self, *args, **kwargs):
            raise AssertionError("deprecated compatibility dispatch read the definition store")

    result = call_planning_tool_contract(
        tool,
        {
            "runtime_status": "running",
            "gate_decision": "go",
            "unknown": "ignored",
        },
        repo_root=planning_repo,
        definition_store=ExplodingDefinitionStore(),
        definition_owner="owner-1",
    )

    assert result["error"] == "deprecated_tool"
    assert result["tool"] == tool
    assert result["read_only"] is True
    assert result["writes_performed"] is False


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("planning_read_roadmap", {"source_id_or_path": "../../private.json"}),
        ("planning_search_roadmaps", {"query": "x" * 501}),
        ("planning_list_roadmaps", {"limit": 101}),
        ("planning_get_context_pack", {"roadmap_ref": "docs/plans/core-roadmap.json", "unknown": True}),
        ("planning_read_roadmap", {"source_id_or_path": "docs/plans/core-roadmap.json", "include_nodes": "yes"}),
        ("planning_read_gate_definitions", {"project_id": "demo-project", "roadmap_id": "core-map", "revision_or_latest_approved": 0}),
        ("planning_create_agent_handoff", {"project_id": "demo-project"}),
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


def test_planning_transport_has_no_agent_or_temporal_execution_import():
    source = Path("mcp_servers/planning_server.py").read_text(encoding="utf-8")

    assert "routes.coding_agent_routes" not in source
    assert "src.agent_loop" not in source
    assert "temporalio" not in source
    assert "start_workflow" not in source
    assert "/api/agent/runs" not in source
