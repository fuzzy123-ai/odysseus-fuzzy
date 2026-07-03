from types import SimpleNamespace

import pytest

from src.task_scheduler import TaskScheduler
from src.task_scheduler_delivery import (
    deliver_user_notification_for_task,
    deliver_via_mcp,
    format_email_output,
    is_email_output_target,
    is_user_notification_output_target,
)


def test_task_scheduler_delivery_wrappers_preserve_email_formatting():
    raw = (
        "\U0001f4ec [INBOX] 2 emails\n"
        "[1778] Re: Status From: Alice | Today\n"
        "[42] \U0001f4ce Attachment update\n"
    )

    formatted = format_email_output(raw)

    assert formatted == TaskScheduler._format_email_output(raw)
    assert "- Alice \u2014 Re: Status" in formatted
    assert "- Attachment update" in formatted


def test_task_scheduler_delivery_email_target_detection():
    for target in ("email", "email:self", "email:me@example.com", "me@example.com"):
        assert is_email_output_target(target)
        assert TaskScheduler._is_email_output_target(target)

    assert not is_email_output_target("session")
    assert not TaskScheduler._is_email_output_target("session")


def test_task_scheduler_delivery_telegram_target_detection():
    assert is_user_notification_output_target("telegram")
    assert is_user_notification_output_target("notification:telegram")
    assert TaskScheduler._is_user_notification_output_target("telegram")
    assert not is_user_notification_output_target("email")


@pytest.mark.asyncio
async def test_task_scheduler_delivery_mcp_args_are_bounded(monkeypatch):
    calls = []

    class _Mcp:
        async def call_tool(self, tool_name, args):
            calls.append((tool_name, args))
            return {"exit_code": 0, "stdout": "sent", "stderr": ""}

    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: _Mcp())
    monkeypatch.setattr("routes.email_helpers._get_email_config", lambda: {"from_address": "me@example.com"})

    task = SimpleNamespace(id="task-1", name="Daily Brief", owner="owner@example.com")

    await deliver_via_mcp("mcp__gmail__send_email", task, "body")

    assert calls == [
        (
            "mcp__gmail__send_email",
            {
                "subject": "[Task] Daily Brief",
                "body": "body",
                "headers": {
                    "X-Odysseus-Origin": "odysseus-ui",
                    "X-Odysseus-Kind": "task",
                    "X-Odysseus-Ref": "task-1",
                },
                "to": "me@example.com",
                "recipient": "me@example.com",
                "email": "me@example.com",
                "address": "me@example.com",
            },
        )
    ]


@pytest.mark.asyncio
async def test_task_scheduler_telegram_delivery_uses_safe_boundary(monkeypatch):
    calls = []

    async def _deliver(payload):
        calls.append(payload)
        return {"delivery_status": "dispatched", "reason": "ready_for_server_side_dispatch"}

    monkeypatch.setattr("src.user_notification_delivery.deliver_user_notification", _deliver)
    task = SimpleNamespace(id="task-telegram", name="Morning Todos", owner="alice")

    decision = await deliver_user_notification_for_task(task, "Todo digest body")

    assert decision["delivery_status"] == "dispatched"
    assert calls[0]["channel"] == "telegram"
    assert calls[0]["dry_run"] is False
    assert "chat_id" not in calls[0]
    assert "token" not in str(calls[0]).lower()
