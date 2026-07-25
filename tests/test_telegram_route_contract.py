import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from plugins.telegram.plugin import _telegram_control_command, setup


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _PluginContext:
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.telegram.contract"))
    registered_tools: list[Any] = field(default_factory=list)
    registered_routers: list[Any] = field(default_factory=list)
    require_admin: Callable[[Any], None] = lambda _request: None

    def add_router(self, router):
        self.registered_routers.append(router)

    def register_tool(self, spec):
        self.registered_tools.append(spec)


def _setup_contract_context(tmp_path: Path) -> _PluginContext:
    ctx = _PluginContext(data_dir=tmp_path)
    setup(ctx)
    return ctx


def test_telegram_setup_keeps_route_surface_stable(tmp_path):
    ctx = _setup_contract_context(tmp_path)
    expected_route_methods = {
        ("/api/plugins/telegram/status", "GET"),
        ("/api/plugins/telegram/history", "GET"),
        ("/api/plugins/telegram/poll", "POST"),
        ("/api/plugins/telegram/webhook", "POST"),
        ("/api/plugins/telegram/reply", "POST"),
        ("/api/plugins/telegram/document-reply", "POST"),
        ("/api/plugins/telegram/document-reply/preview", "POST"),
        ("/api/plugins/telegram/document-reply/live-gate", "POST"),
        ("/api/plugins/telegram/app", "GET"),
    }
    assert len(ctx.registered_routers) == 1

    plugin_route_methods = {
        (route.path, method)
        for route in ctx.registered_routers[0].routes
        for method in getattr(route, "methods", set())
    }
    assert plugin_route_methods >= expected_route_methods


def test_telegram_setup_keeps_registered_tool_contracts(tmp_path):
    ctx = _setup_contract_context(tmp_path)
    tools = {tool.name: tool for tool in ctx.registered_tools}

    assert set(tools) == {"telegram_reply", "telegram_document_reply", "odysseus_notify_user"}
    assert tools["telegram_reply"].permission == "admin"
    assert tools["telegram_reply"].parameters["required"] == ["chat_id", "text"]
    assert tools["telegram_document_reply"].permission == "admin"
    assert tools["telegram_document_reply"].parameters["required"] == ["chat_id"]
    assert tools["odysseus_notify_user"].permission == "admin"
    assert tools["odysseus_notify_user"].parameters["required"] == ["message"]


def test_telegram_plugin_setup_delegates_route_registration():
    source = (ROOT / "plugins" / "telegram" / "plugin.py").read_text(encoding="utf-8")

    assert "register_telegram_admin_routes(" in source
    assert "register_telegram_polling_routes(" in source
    assert "register_telegram_webhook_routes(" in source
    assert "register_telegram_outbound_routes(" in source
    assert "@router.get(" not in source
    assert "@router.post(" not in source


def test_telegram_control_command_alias_contract():
    cases = {
        "/new": "new_chat",
        "/inbox": "universal_inbox_status",
        "/review": "universal_inbox_review_status",
        "/review ok": "universal_inbox_review_confirm",
        "/review memory": "universal_inbox_memory_review_status",
        "/review memory ok": "universal_inbox_memory_review_confirm",
        "/project ok": "project_intake_review_confirm",
        "/project hold": "project_intake_review_hold",
        "/task pause": "agent_task_pause",
        "/task weiter": "agent_task_resume",
        "/task cancel": "agent_task_cancel",
        "/calendar": "calendar_readiness",
        "/agenda": "calendar_agenda",
        "/remind status": "calendar_reminders_status",
        "/remind update": "calendar_reminder_update",
        "/remind buy milk": "calendar_reminder_create",
        "/todo status": "calendar_todo_status",
        "/todo daily digest": "calendar_todo_digest_create",
        "/dsgvo on": "dsgvo_enable",
        "/privacy off": "dsgvo_disable",
        "/gdpr status": "dsgvo_status",
        "/datenschutz something": "dsgvo_help",
    }

    for text, expected in cases.items():
        assert _telegram_control_command({"kind": "text", "text": text}) == expected


def test_telegram_control_command_ignores_non_text_messages():
    assert _telegram_control_command({"kind": "voice", "text": "/task pause"}) == ""
