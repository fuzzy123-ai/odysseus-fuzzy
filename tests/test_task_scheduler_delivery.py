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
from src.user_notification_delivery import deliver_user_notification


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
    assert decision["correlation_id"].startswith("sha256:")
    assert decision["runtime_event"]["surface"] == "scheduler"
    assert decision["runtime_event"]["component"] == "task_delivery"
    assert decision["runtime_event"]["status"] == "success"
    assert decision["runtime_event"]["raw_content_visible"] is False
    assert calls[0]["channel"] == "telegram"
    assert calls[0]["dry_run"] is False
    assert "chat_id" not in calls[0]
    assert calls[0]["metadata"]["task_id"].startswith("sha256:")
    assert "Morning Todos" not in str(decision["runtime_event"])
    assert "token" not in str(calls[0]).lower()


@pytest.mark.asyncio
async def test_todo_digest_telegram_delivery_is_plain_multiline(monkeypatch):
    calls = []

    async def _deliver(payload):
        calls.append(payload)
        return {"delivery_status": "dispatched", "reason": "ready_for_server_side_dispatch"}

    monkeypatch.setattr("src.user_notification_delivery.deliver_user_notification", _deliver)
    task = SimpleNamespace(id="task-telegram", name="Todo digest", owner="alice", action="todo_digest")
    body = "Todo digest\n\nOpen items:\n- Zentrale To-Do-Liste: Patchday kommunizieren"

    await deliver_user_notification_for_task(task, body)

    assert calls[0]["event"] == "todo_digest"
    assert calls[0]["render_mode"] == "plain"
    assert calls[0]["message"] == body
    assert calls[0]["metadata"]["task_id"].startswith("sha256:")


@pytest.mark.asyncio
async def test_legacy_named_todo_digest_telegram_delivery_is_plain(monkeypatch):
    sent = []
    body = (
        "Todo digest\n\n"
        "Open items:\n"
        "- Zentrale To-Do-Liste: Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
        "- Zentrale To-Do-Liste: ASV Noten ueberpruefen"
    )

    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_CHAT_ID", "server-side-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda target: target == "server-side-target")
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_text",
        lambda target, text: sent.append((target, text)) or {"ok": True},
    )
    task = SimpleNamespace(id="task-telegram", name="Todo digest", owner="alice")

    decision = await deliver_user_notification_for_task(task, body)

    assert decision["delivery_status"] == "dispatched"
    assert sent == [
        (
            "server-side-target",
            "Todo digest\n\n"
            "Open items:\n"
            "Zentrale To-Do-Liste:\n"
            "- Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
            "- ASV Noten ueberpruefen",
        )
    ]
    assert "[Odysseus]" not in sent[0][1]
    assert "scheduled_task" not in sent[0][1]
    assert "task_id" not in sent[0][1]


@pytest.mark.asyncio
async def test_legacy_flat_todo_digest_telegram_delivery_is_normalized(monkeypatch):
    sent = []
    body = (
        "[Odysseus][success] scheduled_task: Todo digest Open items: "
        "- Zentrale To-Do-Liste: Termin mit Herr Assel und Macro koordinieren per E-Mail "
        "- Zentrale To-Do-Liste: ASV Noten ueberpruefen "
        "task_id=sha256:eb50733f9dcd20d40431eeb07ba5ea317adf9d7f205425b6419e2fe6b12ae7f0"
    )

    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_CHAT_ID", "server-side-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda target: target == "server-side-target")
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_text",
        lambda target, text: sent.append((target, text)) or {"ok": True},
    )
    task = SimpleNamespace(id="task-telegram", name="Task", owner="alice")

    decision = await deliver_user_notification_for_task(task, body)

    assert decision["delivery_status"] == "dispatched"
    assert sent == [
        (
            "server-side-target",
            "Todo digest\n\n"
            "Open items:\n"
            "Zentrale To-Do-Liste:\n"
            "- Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
            "- ASV Noten ueberpruefen",
        )
    ]
    assert "[Odysseus]" not in sent[0][1]
    assert "scheduled_task" not in sent[0][1]
    assert "task_id" not in sent[0][1]


@pytest.mark.asyncio
async def test_todo_digest_live_notification_renders_plain_body(monkeypatch):
    sent = []
    body = "Todo digest\n\nOpen items:\n- Zentrale To-Do-Liste: Patchday kommunizieren"

    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_CHAT_ID", "server-side-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda target: target == "server-side-target")
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_text",
        lambda target, text: sent.append((target, text)) or {"ok": True},
    )

    decision = await deliver_user_notification({
        "event": "todo_digest",
        "message": body,
        "severity": "success",
        "channel": "telegram",
        "dry_run": False,
        "render_mode": "plain",
        "metadata": {"task_id": "sha256:abc"},
    })

    assert decision["delivery_status"] == "dispatched"
    assert sent == [("server-side-target", body)]
    assert "[Odysseus]" not in sent[0][1]
    assert "task_id" not in sent[0][1]


@pytest.mark.asyncio
async def test_task_scheduler_mcp_delivery_returns_redacted_runtime_event(monkeypatch):
    class _Mcp:
        async def call_tool(self, tool_name, args):
            return {"exit_code": 1, "stdout": "private delivery output", "stderr": "secret-ish error"}

    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: _Mcp())
    monkeypatch.setattr("routes.email_helpers._get_email_config", lambda: {"from_address": "me@example.com"})

    task = SimpleNamespace(id="task-mcp", name="Sensitive Task", owner="owner@example.com")

    decision = await deliver_via_mcp("mcp__gmail__send_email", task, "private body")

    encoded_event = str(decision["runtime_event"])
    assert decision["status"] == "failed"
    assert decision["runtime_event"]["status"] == "failed"
    assert decision["runtime_event"]["raw_content_visible"] is False
    assert decision["runtime_event"]["metadata"]["tool_ref"] == "mcp__gmail__send_email"
    assert "private body" not in encoded_event
    assert "private delivery output" not in encoded_event
    assert "Sensitive Task" not in encoded_event
