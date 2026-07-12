from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operator_dashboard_router_is_wired_after_mcp_manager_initialization():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    manager_index = source.index("mcp_manager = McpManager()")
    dashboard_index = source.index(
        "app.include_router(setup_operator_dashboard_routes(mcp_manager=mcp_manager))"
    )

    assert manager_index < dashboard_index
    assert source.count("setup_operator_dashboard_routes(mcp_manager=mcp_manager)") == 1
