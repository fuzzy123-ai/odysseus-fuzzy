from unittest.mock import AsyncMock

import pytest

from routes import note_reminders


@pytest.mark.asyncio
async def test_ntfy_reminder_rejects_link_local_url_before_send(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings", lambda: {})
    monkeypatch.setattr(
        "src.integrations.load_integrations",
        lambda: [
            {
                "id": "ntfy",
                "preset": "ntfy",
                "enabled": True,
                "base_url": "http://169.254.169.254",
                "api_key": "",
            }
        ],
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: mock_client)

    result = await note_reminders.dispatch_reminder(
        "Title",
        "Body",
        "",
        queue_browser=False,
        settings_override={"reminder_channel": "ntfy"},
    )

    assert result["ntfy_sent"] is False
    assert "ntfy URL rejected" in result["ntfy_error"]
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_ntfy_reminder_validates_final_topic_url(monkeypatch):
    monkeypatch.setattr("src.settings.load_settings", lambda: {})
    monkeypatch.setattr(
        "src.integrations.load_integrations",
        lambda: [
            {
                "id": "ntfy",
                "preset": "ntfy",
                "enabled": True,
                "base_url": "http://ntfy.example.com",
                "api_key": "",
            }
        ],
    )
    seen = []
    monkeypatch.setattr(
        "src.url_safety.check_outbound_url",
        lambda url, *, block_private=False: seen.append((url, block_private)) or (True, "ok"),
    )
    resp = AsyncMock()
    resp.is_success = True
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: mock_client)

    result = await note_reminders.dispatch_reminder(
        "Title",
        "Body",
        "",
        queue_browser=False,
        settings_override={"reminder_channel": "ntfy", "reminder_ntfy_topic": "ops alerts"},
    )

    assert result["ntfy_sent"] is True
    assert seen == [("http://ntfy.example.com/ops%20alerts", False)]
    mock_client.post.assert_called_once()
