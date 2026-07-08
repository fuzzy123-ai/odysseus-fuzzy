import json

import pytest

from src.ops_tabletop_packet import (
    OPS_TABLETOP_PACKET_SCHEMA,
    OpsTabletopPacketError,
    build_ops_tabletop_packet,
    validate_ops_tabletop_packet,
)


def test_build_ops_tabletop_packet_is_synthetic_readonly_and_gate_oriented():
    packet = build_ops_tabletop_packet()
    encoded = json.dumps(packet, sort_keys=True)

    assert packet["schema"] == OPS_TABLETOP_PACKET_SCHEMA
    assert packet["mode"] == "synthetic_tabletop"
    assert packet["status"] == "contain"
    assert packet["snapshot"]["schema"] == "odysseus.ops_console.snapshot.v1"
    assert packet["snapshot"]["timeline"]["schema"] == "odysseus.ops_timeline.v1"
    assert "service_restart-operator-go" in packet["operator_gates"]
    assert packet["live_go_required"] == packet["operator_gates"]
    assert all(assertion["passed"] is True for assertion in packet["assertions"])
    assert packet["live_actions_performed"] is False
    assert packet["host_commands_performed"] is False
    assert packet["writes_performed"] is False
    assert packet["remediation_performed"] is False
    assert packet["raw_content_visible"] is False
    assert packet["host_paths_visible"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "chat_id" not in encoded.lower()
    assert "C:\\\\" not in encoded


def test_validate_ops_tabletop_packet_reports_counts_without_live_actions():
    packet = build_ops_tabletop_packet()

    validation = validate_ops_tabletop_packet(packet)

    assert validation["schema"] == "odysseus.ops_tabletop_packet.validation.v1"
    assert validation["status"] == "valid"
    assert validation["operator_gate_count"] >= 1
    assert validation["timeline_event_count"] >= 1
    assert validation["raw_content_visible"] is False
    assert validation["live_actions_performed"] is False


def test_tabletop_packet_rejects_unsafe_scenario_text_and_payload_flags():
    with pytest.raises(OpsTabletopPacketError, match="raw private identifier"):
        build_ops_tabletop_packet(title=r"Inspect C:\Users\nkatz\secret.log")

    packet = dict(build_ops_tabletop_packet())
    packet["writes_performed"] = True

    with pytest.raises(OpsTabletopPacketError, match="writes_performed"):
        validate_ops_tabletop_packet(packet)


def test_tabletop_packet_validation_rejects_missing_gate_or_unsafe_payload():
    packet = dict(build_ops_tabletop_packet())
    packet["operator_gates"] = ()

    with pytest.raises(OpsTabletopPacketError, match="operator gate"):
        validate_ops_tabletop_packet(packet)

    packet = dict(build_ops_tabletop_packet())
    packet["notes"] = "chat_id=12345"

    with pytest.raises(OpsTabletopPacketError, match="forbidden marker"):
        validate_ops_tabletop_packet(packet)


def test_tabletop_packet_with_approved_gate_remains_prepare_only():
    packet = build_ops_tabletop_packet(approved_gates=("service_restart-operator-go",))
    encoded = json.dumps(packet, sort_keys=True)
    reasons = {
        event.get("summary")
        for event in packet["snapshot"]["timeline"]["events"]
        if event.get("surface") == "remediation"
    }

    assert packet["status"] == "contain"
    assert packet["remediation_performed"] is False
    assert packet["snapshot"]["remediation_performed"] is False
    assert any("act-service-restart" in summary for summary in reasons)
    assert "operator_gate_approved_prepare_only" in encoded
