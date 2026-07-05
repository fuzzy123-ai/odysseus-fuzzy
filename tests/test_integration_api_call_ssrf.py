import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import integrations


BASE_INTEGRATION = {
    "id": "test_integ",
    "name": "TestInteg",
    "enabled": True,
    "base_url": "http://api.example.com",
    "auth_type": "none",
    "api_key": "",
    "auth_header": "",
    "auth_param": "",
    "description": "",
    "preset": "",
}


def _response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = {"ok": True}
    resp.text = json.dumps({"ok": True})
    return resp


async def _call(integration):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=_response())
    with (
        patch.object(integrations, "_find_integration", return_value=integration),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await integrations.execute_api_call("test_integ", "GET", "/items")
    return result, mock_client


@pytest.mark.asyncio
async def test_api_call_rejects_private_final_url_before_request():
    integration = {**BASE_INTEGRATION, "base_url": "http://127.0.0.1:8080/api"}

    result, mock_client = await _call(integration)

    assert result["exit_code"] == 1
    assert "Integration URL rejected" in result["error"]
    mock_client.request.assert_not_called()


@pytest.mark.asyncio
async def test_api_call_validates_joined_final_url_not_only_base(monkeypatch):
    seen = []
    monkeypatch.setattr(integrations, "_validate_api_call_url", lambda url: seen.append(url) or url)

    result, mock_client = await _call({**BASE_INTEGRATION, "base_url": "http://api.example.com/root"})

    assert result["exit_code"] == 0
    assert seen == ["http://api.example.com/root/items"]
    mock_client.request.assert_called_once()
