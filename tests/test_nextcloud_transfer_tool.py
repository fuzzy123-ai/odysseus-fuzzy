import json

import pytest

from src.agent_tools import TOOL_TAGS, ToolBlock
from src.mcp_server_tool_policy import classify_mcp_tool
from src.tool_execution import execute_tool_block
from src.tool_implementations import do_manage_nextcloud_transfer
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS, plan_mode_disabled_tools


def test_manage_nextcloud_transfer_schema_index_security_and_mcp_policy():
    schema_by_name = {(schema.get("function") or {}).get("name"): schema for schema in FUNCTION_TOOL_SCHEMAS}

    assert "manage_nextcloud_transfer" in schema_by_name
    actions = schema_by_name["manage_nextcloud_transfer"]["function"]["parameters"]["properties"]["action"]["enum"]
    assert {"readiness", "smoke_plan", "execute"}.issubset(set(actions))
    assert "target_path" in schema_by_name["manage_nextcloud_transfer"]["function"]["parameters"]["properties"]

    assert "manage_nextcloud_transfer" in TOOL_TAGS
    assert "manage_nextcloud_transfer" in BUILTIN_TOOL_DESCRIPTIONS
    assert "manage_nextcloud_transfer" in NON_ADMIN_BLOCKED_TOOLS
    assert "manage_nextcloud_transfer" not in PLAN_MODE_READONLY_TOOLS
    assert "manage_nextcloud_transfer" in plan_mode_disabled_tools()

    decision = classify_mcp_tool("manage_nextcloud_transfer")
    assert decision.exposed is False
    assert decision.category == "high_risk"


def test_manage_nextcloud_transfer_native_function_call_converts_to_tool_block():
    block = function_call_to_tool_block(
        "manage_nextcloud_transfer",
        json.dumps({"action": "readiness"}),
    )

    assert block is not None
    assert block.tool_type == "manage_nextcloud_transfer"
    assert json.loads(block.content)["action"] == "readiness"


@pytest.mark.asyncio
async def test_nextcloud_readiness_reports_env_gates_without_values(monkeypatch):
    for name in (
        "UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED",
        "UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO",
        "NEXTCLOUD_WEBDAV_BASE_URL",
        "NEXTCLOUD_WEBDAV_USERNAME",
        "NEXTCLOUD_WEBDAV_APP_PASSWORD",
    ):
        monkeypatch.setenv(name, "configured")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED", "true")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO", "true")

    result = await do_manage_nextcloud_transfer(
        json.dumps({"action": "readiness", "target_path": "Odysseus/Test/smoke.txt"}),
        owner="alice",
    )

    assert result["status"] == "ready"
    assert result["ready"] is True
    assert result["readiness_gap_names"] == []
    assert "NEXTCLOUD_WEBDAV_APP_PASSWORD" in result["expected_env_names"]
    assert result["password_alias_accepted"] is False
    encoded = json.dumps(result, sort_keys=True)
    assert "configured" not in encoded
    assert result["secret_values_visible"] is False


@pytest.mark.asyncio
async def test_nextcloud_smoke_plan_never_writes_or_probes_network():
    result = await do_manage_nextcloud_transfer(
        json.dumps({"action": "smoke_plan", "target_path": "Odysseus/Test/smoke.txt"}),
        owner="alice",
    )

    assert result["status"] == "dry_run_ready"
    assert result["writes_performed"] is False
    assert result["nextcloud_write_performed"] is False
    assert result["network_probe_performed"] is False
    assert result["target_path"] == "Odysseus/Test/smoke.txt"
    assert result["secret_values_visible"] is False
    assert result["host_paths_visible"] is False


@pytest.mark.asyncio
async def test_nextcloud_execute_dispatches_and_live_copy_uses_webdav_adapter(monkeypatch):
    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: True)
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED", "true")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO", "true")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_BASE_URL", "https://nextcloud.example/remote.php/dav/files/odysseus")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_USERNAME", "odysseus")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_APP_PASSWORD", "secret-app-password")

    class FakeClient:
        def __init__(self):
            self.files = {}
            self.sidecars = {}
            self.closed = False

        def stat(self, relative_path):
            if relative_path not in self.files:
                return None
            return {"size_bytes": len(self.files[relative_path]), "etag": "etag"}

        def put_file(self, source_path, relative_path):
            payload = source_path.read_bytes()
            self.files[relative_path] = payload
            return {"size_bytes": len(payload), "etag": "uploaded"}

        def put_text(self, relative_path, text):
            self.sidecars[relative_path] = text
            return {"size_bytes": len(text.encode("utf-8")), "etag": "sidecar"}

        def close(self):
            self.closed = True

    fake = FakeClient()
    monkeypatch.setattr("src.nextcloud_webdav_client.build_nextcloud_webdav_client_from_env", lambda: fake)

    desc, result = await execute_tool_block(
        ToolBlock(
            "manage_nextcloud_transfer",
            json.dumps(
                {
                    "action": "execute",
                    "target_path": "Odysseus/Test/smoke.txt",
                    "sidecar_path": "Odysseus/Test/smoke.odysseus.json",
                    "dry_run": False,
                    "review_approved": True,
                    "operator_live_go": True,
                    "smoke_text": "hello nextcloud\n",
                }
            ),
        ),
        owner="alice",
    )

    assert desc == "manage_nextcloud_transfer"
    assert result["status"] == "completed"
    assert result["writes_performed"] is True
    assert result["verified"] is True
    assert result["target_path"] == "Odysseus/Test/smoke.txt"
    assert fake.files["Odysseus/Test/smoke.txt"] == b"hello nextcloud\n"
    assert fake.sidecars
    assert fake.closed is True
    encoded = json.dumps(result, sort_keys=True)
    assert "secret-app-password" not in encoded
    assert result["secret_values_visible"] is False
    assert result["host_paths_visible"] is False
