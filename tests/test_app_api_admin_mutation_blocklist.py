import json

import pytest

from src.tool_implementations import do_app_api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "tool_name"),
    [
        ("POST", "/api/model-endpoints", "manage_endpoints"),
        ("PATCH", "/api/model-endpoints/ep1", "manage_endpoints"),
        ("DELETE", "/api/model-endpoints/ep1", "manage_endpoints"),
        ("POST", "/api/webhooks", "manage_webhooks"),
        ("PATCH", "/api/webhooks/wh1", "manage_webhooks"),
        ("DELETE", "/api/webhooks/wh1", "manage_webhooks"),
        ("POST", "/api/mcp/servers", "manage_mcp"),
        ("PUT", "/api/mcp/servers/srv1", "manage_mcp"),
        ("PATCH", "/api/mcp/servers/srv1", "manage_mcp"),
        ("DELETE", "/api/mcp/servers/srv1", "manage_mcp"),
    ],
)
async def test_app_api_blocks_admin_mutations_before_loopback(method, path, tool_name, monkeypatch):
    import httpx

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("app_api should block this route before loopback")

    monkeypatch.setattr(httpx, "AsyncClient", ForbiddenClient)

    result = await do_app_api(
        json.dumps({"method": method, "path": path, "body": {"unsafe": True}}),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert tool_name in result["error"]
