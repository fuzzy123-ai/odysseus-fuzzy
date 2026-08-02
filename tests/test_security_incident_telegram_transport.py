from types import SimpleNamespace

import pytest

from src.security_incident_notifications import (
    canonical_access_alert_body,
    canonical_access_alert_body_ref,
    canonical_operator_notification_body_ref,
    canonical_operator_notification_target_class_ref,
)
from src.security_incident_delivery import SecurityIncidentDeliveryAdapter
from src.security_incident_telegram_transport import (
    ProductionSecurityIncidentTelegramTransport,
    SecurityIncidentTelegramTransportError,
    build_production_security_incident_telegram_transport,
    is_production_security_incident_telegram_transport,
)


def _request(**changes):
    value = {
        "schema": "odysseus.security_incident_delivery_request.v1", "action_id": "act-telegram",
        "action_version": 3, "body_ref": canonical_operator_notification_body_ref(), "channel": "telegram",
        "approved_target_class_ref": canonical_operator_notification_target_class_ref(), "timeout_seconds": 15,
        "raw_content_visible": False,
    }
    value.update(changes)
    return value


def _ack(message_id=17):
    return {"ok": True, "telegram_message_id": message_id, "telegram_message_ids": [message_id], "delivery_mode": "classic_html", "formatting_mode": "telegram_html", "parse_mode": "HTML", "message_count": 1, "max_reply_chunks": 3, "truncated": False, "token_value_visible": False, "raw_rich_payload_visible": False}


def test_production_transport_is_disabled_by_default_and_cannot_be_directly_issued():
    assert build_production_security_incident_telegram_transport() is None
    with pytest.raises(SecurityIncidentTelegramTransportError): ProductionSecurityIncidentTelegramTransport(_issuer=object())
    issued = build_production_security_incident_telegram_transport(enabled=True)
    assert SecurityIncidentDeliveryAdapter(object(), transport=issued)._transport is issued
    assert is_production_security_incident_telegram_transport(issued) is True
    assert is_production_security_incident_telegram_transport(object()) is False


def test_sealed_transport_resolves_server_target_and_projects_only_opaque_receipt(monkeypatch):
    sent = []
    monkeypatch.setattr("src.user_notification_delivery._configured_telegram_target", lambda: "server-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda target: target == "server-target")
    monkeypatch.setattr("plugins.telegram.outbound.send_telegram_text", lambda target, body: sent.append((target, body)) or _ack())
    result = build_production_security_incident_telegram_transport(enabled=True).invoke(_request())
    assert result["status"] == "acknowledged" and result["receipt_ref"].startswith("receipt:sha256:")
    assert set(result) == {"status", "receipt_ref"}
    assert len(sent) == 1 and "server-target" not in str(result)


@pytest.mark.parametrize("changes", [
    {"body_ref": "body:sha256:" + "a" * 64},
    {"approved_target_class_ref": "target_class:sha256:" + "b" * 64},
    {"raw_content_visible": True},
])
def test_transport_rejects_request_substitution_without_send(monkeypatch, changes):
    monkeypatch.setattr("src.user_notification_delivery._configured_telegram_target", lambda: "server-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda _target: True)
    monkeypatch.setattr("plugins.telegram.outbound.send_telegram_text", lambda *_args: (_ for _ in ()).throw(AssertionError("must not send")))
    with pytest.raises(SecurityIncidentTelegramTransportError):
        build_production_security_incident_telegram_transport(enabled=True).invoke(_request(**changes))


def test_target_rejection_and_provider_ambiguity_are_content_free(monkeypatch):
    transport = build_production_security_incident_telegram_transport(enabled=True)
    monkeypatch.setattr("src.user_notification_delivery._configured_telegram_target", lambda: "server-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda _target: False)
    with pytest.raises(SecurityIncidentTelegramTransportError) as error:
        transport.invoke(_request())
    assert "server-target" not in str(error.value)
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda _target: True)
    monkeypatch.setattr("plugins.telegram.outbound.send_telegram_text", lambda *_args: {"ok": False, "raw_response": "forbidden"})
    with pytest.raises(SecurityIncidentTelegramTransportError): transport.invoke(_request())


@pytest.mark.parametrize("result", [
    {"ok": True}, _ack(None), _ack(True), _ack("17"),
    _ack(0), {**_ack(), "telegram_message_ids": [17, 18]},
    {**_ack(), "telegram_message_ids": [18]}, {**_ack(), "message_count": 2},
    {**_ack(), "token_value_visible": True}, {**_ack(), "raw_rich_payload_visible": True},
])
def test_ok_true_malformed_acknowledgements_are_rejected_without_provider_leak(monkeypatch, result):
    monkeypatch.setattr("src.user_notification_delivery._configured_telegram_target", lambda: "server-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda _target: True)
    monkeypatch.setattr("plugins.telegram.outbound.send_telegram_text", lambda *_args: result)
    with pytest.raises(SecurityIncidentTelegramTransportError) as error:
        build_production_security_incident_telegram_transport(enabled=True).invoke(_request())
    assert "server-target" not in str(error.value) and "17" not in str(error.value)


def test_receipt_binds_validated_message_id_without_exposing_it(monkeypatch):
    monkeypatch.setattr("src.user_notification_delivery._configured_telegram_target", lambda: "server-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda _target: True)
    monkeypatch.setattr("plugins.telegram.outbound.send_telegram_text", lambda *_args: _ack(17))
    first = build_production_security_incident_telegram_transport(enabled=True).invoke(_request())
    monkeypatch.setattr("plugins.telegram.outbound.send_telegram_text", lambda *_args: _ack(18))
    second = build_production_security_incident_telegram_transport(enabled=True).invoke(_request())
    assert first["receipt_ref"] != second["receipt_ref"] and "17" not in str(first) and "18" not in str(second)


def test_dynamic_access_alert_body_is_resolved_from_store_and_body_ref_verified(monkeypatch):
    context = SimpleNamespace(
        event_class="authentication_failure", accessing_ip="2606:4700:4700::1111",
        suppression_decision="notify",
    )
    store = SimpleNamespace(get_incident_context_for_action=lambda action_id: context)
    body_ref = canonical_access_alert_body_ref(
        event_class=context.event_class, accessing_ip=context.accessing_ip,
    )
    sent = []
    monkeypatch.setattr("src.user_notification_delivery._configured_telegram_target", lambda: "server-target")
    monkeypatch.setattr("plugins.telegram.plugin._chat_allowed", lambda _target: True)
    monkeypatch.setattr(
        "plugins.telegram.outbound.send_telegram_text",
        lambda target, body: sent.append((target, body)) or _ack(),
    )
    result = build_production_security_incident_telegram_transport(
        enabled=True, store=store,
    ).invoke(_request(body_ref=body_ref))
    assert result["status"] == "acknowledged"
    assert sent == [(
        "server-target",
        canonical_access_alert_body(
            event_class=context.event_class, accessing_ip=context.accessing_ip,
        ),
    )]
    with pytest.raises(SecurityIncidentTelegramTransportError):
        build_production_security_incident_telegram_transport(
            enabled=True, store=store,
        ).invoke(_request(body_ref="body:sha256:" + "a" * 64))
