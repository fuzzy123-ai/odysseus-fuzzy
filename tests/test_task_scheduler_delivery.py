from types import SimpleNamespace

import pytest

from src.task_scheduler import TaskScheduler
from src.task_scheduler_delivery import (
    deliver_via_mcp,
    format_email_output,
    is_email_output_target,
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
