from pathlib import Path

import pytest

from src.telegram_screenshot_delivery import build_telegram_screenshot_delivery_packet
from src.visual_observer_evidence import build_screenshot_evidence


def test_screenshot_delivery_packet_verifies_artifact_and_redacts_payload(tmp_path: Path):
    artifact = tmp_path / "data" / "reports" / "autonomous_coding_agent" / "pong" / "screen.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\x89PNG\r\n\x1a\npayload")
    visual = build_screenshot_evidence(
        artifact_ref="data/reports/autonomous_coding_agent/pong/screen.png",
        width=1280,
        height=720,
        image_hash="a" * 64,
    )

    packet = build_telegram_screenshot_delivery_packet(
        "data/reports/autonomous_coding_agent/pong/screen.png",
        repo_root=tmp_path,
        filename="Pong Screenshot!!.png",
        caption="Pong screenshot ready",
        reply_enabled=True,
        target_configured=True,
        visual_evidence=visual,
    )
    payload = packet.to_dict()

    assert payload["dispatch_allowed"] is True
    assert payload["integrity_status"] == "verified"
    assert payload["mime_type"] == "image/png"
    assert payload["content_hash"].startswith("sha256:")
    assert payload["filename"] == "Pong-Screenshot-.png"
    assert payload["visual_evidence"]["raw_content_visible"] is False
    assert "payload" not in repr(payload)


def test_screenshot_delivery_packet_blocks_bad_png_without_transport(tmp_path: Path):
    artifact = tmp_path / "reports" / "screen.png"
    artifact.parent.mkdir()
    artifact.write_text("not an image", encoding="utf-8")

    packet = build_telegram_screenshot_delivery_packet(
        "reports/screen.png",
        repo_root=tmp_path,
        reply_enabled=True,
        target_configured=True,
    )

    assert packet.dispatch_allowed is False
    assert packet.integrity_status == "blocked"
    assert "supported image" in packet.blocker
    assert packet.content_hash == ""


def test_screenshot_delivery_packet_respects_live_reply_gate(tmp_path: Path):
    artifact = tmp_path / "reports" / "screen.png"
    artifact.parent.mkdir()
    artifact.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    packet = build_telegram_screenshot_delivery_packet(
        "reports/screen.png",
        repo_root=tmp_path,
        reply_enabled=False,
        target_configured=True,
    )

    assert packet.integrity_status == "verified"
    assert packet.dispatch_allowed is False
    assert packet.blocker == "reply_gate_disabled"


def test_screenshot_delivery_packet_rejects_outside_roots(tmp_path: Path):
    with pytest.raises(ValueError):
        build_telegram_screenshot_delivery_packet(
            "src/private.png",
            repo_root=tmp_path,
            reply_enabled=True,
            target_configured=True,
        )
