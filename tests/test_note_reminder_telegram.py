import pytest

from routes.note_reminders import dispatch_reminder


@pytest.mark.asyncio
async def test_note_reminder_telegram_uses_safe_notification_boundary(monkeypatch):
    calls = []

    async def _deliver(payload):
        calls.append(payload)
        return {"delivery_status": "dry_run", "reason": "dry_run_requested"}

    monkeypatch.setattr("src.user_notification_delivery.deliver_user_notification", _deliver)

    result = await dispatch_reminder(
        "Pay invoice",
        "Invoice due today",
        "note-1",
        owner="alice",
        queue_browser=False,
        settings_override={
            "reminder_channel": "telegram",
            "reminder_telegram_dry_run": True,
        },
    )

    assert result["channel"] == "telegram"
    assert result["telegram_sent"] is False
    assert result["telegram_status"] == "dry_run"
    assert calls[0]["channel"] == "telegram"
    assert calls[0]["dry_run"] is True
    assert "chat_id" not in calls[0]
    assert "token" not in str(calls[0]).lower()
