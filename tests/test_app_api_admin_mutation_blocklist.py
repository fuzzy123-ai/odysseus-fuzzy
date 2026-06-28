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
        ("DELETE", "/api/gallery/img1", "Gallery UI"),
        ("POST", "/api/document/doc1/archive", "manage_documents"),
        ("PUT", "/api/document/doc1", "manage_documents"),
        ("PATCH", "/api/document/doc1", "manage_documents"),
        ("DELETE", "/api/document/doc1", "manage_documents"),
        ("POST", "/api/documents/tidy", "manage_documents"),
        ("POST", "/api/documents/ai-tidy", "manage_documents"),
        ("DELETE", "/api/research/report1", "manage_research"),
        ("POST", "/api/tasks", "manage_tasks"),
        ("PUT", "/api/tasks/task1", "manage_tasks"),
        ("PATCH", "/api/tasks/task1", "manage_tasks"),
        ("DELETE", "/api/tasks/task1", "manage_tasks"),
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


@pytest.mark.asyncio
async def test_app_api_discovery_hides_destructive_data_mutations(monkeypatch):
    import httpx

    class FakeResponse:
        def json(self):
            return {
                "paths": {
                    "/api/gallery/library": {"get": {"summary": "Gallery Library"}},
                    "/api/gallery/{image_id}": {
                        "get": {"summary": "Read Image"},
                        "delete": {"summary": "Delete Image"},
                    },
                    "/api/document/{doc_id}": {
                        "get": {"summary": "Read Document"},
                        "put": {"summary": "Update Document"},
                        "patch": {"summary": "Patch Document"},
                        "delete": {"summary": "Delete Document"},
                    },
                    "/api/document/{doc_id}/archive": {"post": {"summary": "Archive Document"}},
                    "/api/documents/tidy": {"post": {"summary": "Tidy Documents"}},
                    "/api/research/{session_id}": {"delete": {"summary": "Delete Research"}},
                    "/api/tasks/notifications": {"get": {"summary": "Task Notifications"}},
                    "/api/tasks": {"post": {"summary": "Create Task"}},
                    "/api/tasks/{task_id}": {
                        "put": {"summary": "Update Task"},
                        "delete": {"summary": "Delete Task"},
                    },
                }
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await do_app_api(json.dumps({"action": "endpoints"}), owner="admin")

    assert result["exit_code"] == 0
    paths = {(endpoint["method"], endpoint["path"]) for endpoint in result["endpoints"]}
    assert ("GET", "/api/gallery/library") in paths
    assert ("GET", "/api/gallery/{image_id}") in paths
    assert ("GET", "/api/document/{doc_id}") in paths
    assert ("GET", "/api/tasks/notifications") in paths
    assert ("DELETE", "/api/gallery/{image_id}") not in paths
    assert ("POST", "/api/document/{doc_id}/archive") not in paths
    assert ("PUT", "/api/document/{doc_id}") not in paths
    assert ("PATCH", "/api/document/{doc_id}") not in paths
    assert ("DELETE", "/api/document/{doc_id}") not in paths
    assert ("POST", "/api/documents/tidy") not in paths
    assert ("DELETE", "/api/research/{session_id}") not in paths
    assert ("POST", "/api/tasks") not in paths
    assert ("PUT", "/api/tasks/{task_id}") not in paths
    assert ("DELETE", "/api/tasks/{task_id}") not in paths
