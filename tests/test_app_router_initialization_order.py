from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mcp_dependent_routers_are_wired_after_manager_initialization():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    manager_index = source.index("mcp_manager = McpManager()")
    router_calls = (
        "setup_operator_dashboard_routes(mcp_manager=mcp_manager)",
        "setup_workspace_snapshot_routes(mcp_manager=mcp_manager)",
    )

    for router_call in router_calls:
        assert manager_index < source.index(router_call)
        assert source.count(router_call) == 1
