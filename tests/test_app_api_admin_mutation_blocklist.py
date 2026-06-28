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
        ("POST", "/api/gallery/upload", "Gallery UI"),
        ("POST", "/api/gallery/img1/replace", "Gallery UI"),
        ("POST", "/api/gallery/img1/rename", "Gallery UI"),
        ("PATCH", "/api/gallery/img1", "Gallery UI"),
        ("PUT", "/api/gallery/albums/album1", "Gallery UI"),
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
        ("POST", "/api/session", "manage_session"),
        ("PATCH", "/api/session/s1", "manage_session"),
        ("DELETE", "/api/session/s1", "manage_session"),
        ("POST", "/api/session/s1/archive", "manage_session"),
        ("POST", "/api/session/s1/compact", "manage_session"),
        ("POST", "/api/sessions/bulk-delete", "manage_session"),
        ("DELETE", "/api/sessions/all", "manage_session"),
        ("POST", "/api/sessions/auto-sort", "manage_session"),
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
                    "/api/gallery/upload": {"post": {"summary": "Upload Image"}},
                    "/api/gallery/{image_id}": {
                        "get": {"summary": "Read Image"},
                        "patch": {"summary": "Patch Image"},
                        "delete": {"summary": "Delete Image"},
                    },
                    "/api/gallery/{image_id}/replace": {"post": {"summary": "Replace Image"}},
                    "/api/gallery/albums/{album_id}": {"put": {"summary": "Update Album"}},
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
                    "/api/sessions": {
                        "get": {"summary": "List Sessions"},
                    },
                    "/api/session/{sid}": {
                        "get": {"summary": "Read Session"},
                        "patch": {"summary": "Patch Session"},
                        "delete": {"summary": "Delete Session"},
                    },
                    "/api/session/{sid}/archive": {"post": {"summary": "Archive Session"}},
                    "/api/session/{sid}/compact": {"post": {"summary": "Compact Session"}},
                    "/api/sessions/bulk-delete": {"post": {"summary": "Bulk Delete Sessions"}},
                    "/api/sessions/all": {"delete": {"summary": "Delete All Sessions"}},
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
    assert ("GET", "/api/sessions") in paths
    assert ("GET", "/api/session/{sid}") in paths
    assert ("POST", "/api/gallery/upload") not in paths
    assert ("PATCH", "/api/gallery/{image_id}") not in paths
    assert ("DELETE", "/api/gallery/{image_id}") not in paths
    assert ("POST", "/api/gallery/{image_id}/replace") not in paths
    assert ("PUT", "/api/gallery/albums/{album_id}") not in paths
    assert ("POST", "/api/document/{doc_id}/archive") not in paths
    assert ("PUT", "/api/document/{doc_id}") not in paths
    assert ("PATCH", "/api/document/{doc_id}") not in paths
    assert ("DELETE", "/api/document/{doc_id}") not in paths
    assert ("POST", "/api/documents/tidy") not in paths
    assert ("DELETE", "/api/research/{session_id}") not in paths
    assert ("POST", "/api/tasks") not in paths
    assert ("PUT", "/api/tasks/{task_id}") not in paths
    assert ("DELETE", "/api/tasks/{task_id}") not in paths
    assert ("PATCH", "/api/session/{sid}") not in paths
    assert ("DELETE", "/api/session/{sid}") not in paths
    assert ("POST", "/api/session/{sid}/archive") not in paths
    assert ("POST", "/api/session/{sid}/compact") not in paths
    assert ("POST", "/api/sessions/bulk-delete") not in paths
    assert ("DELETE", "/api/sessions/all") not in paths
