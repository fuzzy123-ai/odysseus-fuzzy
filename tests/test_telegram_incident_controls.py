import json
import pytest

from plugins.telegram.parsing import _telegram_control_command, parse_telegram_update
from plugins.telegram.security_incident_commands import handle_security_incident_command, parse_security_incident_command
from plugins.telegram.stores import TelegramInboxStore
from plugins.telegram.plugin import _handle_telegram_control_command
from plugins.telegram.stores import TelegramSessionBridgeStore
from plugins.telegram.webhook_service import build_webhook_control_command_summary


def test_incident_command_is_action_id_only_and_cannot_authorize_from_telegram(tmp_path):
    raw = "/incident approve action-one"
    parsed = parse_security_incident_command(raw)
    assert parsed == {"operation": "approve", "action_id": "action-one"}
    message = parse_telegram_update({"update_id": 1, "message": {"message_id": 2, "chat": {"id": "raw-chat"}, "from": {"id": "raw-sender"}, "text": raw}}, chat_allowed=lambda _chat: True)
    assert _telegram_control_command(message) == "security_incident_approve:action-one"
    blocked = handle_security_incident_command(_telegram_control_command(message))
    assert blocked["status"] == "security_action_browser_step_up_required" and blocked["executed"] is False
    stored = TelegramInboxStore(tmp_path).append_inbound(message)["message"]
    encoded = json.dumps(stored, sort_keys=True)
    assert raw not in encoded and "action-one" not in encoded and "raw-chat" not in encoded


def test_incident_command_rejects_free_text_and_extra_arguments():
    assert parse_security_incident_command("/incident approve action-one now") is None
    assert parse_security_incident_command("/incident remediate anything") is None


@pytest.mark.parametrize("text", ["/incident approve action-one now", "/incident remediate arbitrary free text", "/incident@bot nope raw-attempt"])
def test_every_incident_attempt_is_quarantined_before_persistence(text, tmp_path):
    message = parse_telegram_update({"update_id": 7, "message": {"message_id": 8, "chat": {"id": "raw-chat"}, "from": {"id": "raw-sender"}, "text": text}}, chat_allowed=lambda _chat: True)
    assert message["text"] == "[security incident command]"
    assert _telegram_control_command(message) == "security_incident_control"
    blocked = handle_security_incident_command("security_incident_control")
    assert blocked["status"] == "security_action_browser_step_up_required" and "action_id" not in blocked
    stored = TelegramInboxStore(tmp_path).append_inbound(message)["message"]
    assert text not in json.dumps(stored, sort_keys=True)
    assert _telegram_control_command(stored) == "security_incident_control"


def test_persisted_and_public_telegram_control_surfaces_use_constant_label(tmp_path):
    message = parse_telegram_update({"update_id": 3, "message": {"message_id": 4, "chat": {"id": "raw-chat"}, "from": {"id": "raw-sender"}, "text": "/incident deny action-one"}}, chat_allowed=lambda _chat: True)
    ephemeral = _handle_telegram_control_command(
        _telegram_control_command(message), message=message, raw_chat_id="raw-chat",
        sessions=TelegramSessionBridgeStore(tmp_path), session_creator=None,
    )
    assert ephemeral["command"] == "security_incident_control"
    assert ephemeral["security_incident"]["action_id"] == "action-one"
    assert "action-one" not in json.dumps(build_webhook_control_command_summary(ephemeral), sort_keys=True)
    stored = TelegramInboxStore(tmp_path).append_inbound(message)["message"]
    assert _telegram_control_command(stored) == "security_incident_control"
    persisted = _handle_telegram_control_command(
        _telegram_control_command(stored), message=stored, raw_chat_id="raw-chat",
        sessions=TelegramSessionBridgeStore(tmp_path), session_creator=None,
    )
    assert "action-one" not in json.dumps(persisted, sort_keys=True)
