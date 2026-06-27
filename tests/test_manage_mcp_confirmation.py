import asyncio
import json

import pytest

from src.tool_implementations import do_manage_mcp


@pytest.mark.parametrize("action", ["delete", "enable", "disable", "reconnect"])
def test_manage_mcp_mutating_actions_require_confirmation(action):
    result = asyncio.run(do_manage_mcp(json.dumps({
        "action": action,
        "server_id": "srv-123",
    })))

    assert result["exit_code"] == 0
    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
