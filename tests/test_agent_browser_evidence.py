import pytest

from src.agent_browser_evidence import (
    BrowserEvidenceError,
    BrowserEvidencePacket,
    build_browser_evidence_report,
)


def test_browser_evidence_counts_errors_and_failed_assets():
    packet = BrowserEvidencePacket.create(
        target_url="https://example.test/help",
        captured_at="2026-07-02T12:00:00Z",
        page_title="Help",
        text_summary="A bounded summary.",
        dom_summary="main landmark and navigation",
        accessibility_summary="heading level one is present",
        screenshot_artifact="reports/browser/help.png",
        console_events=[{"level": "error", "message": "asset failed"}],
        network_events=[{"url": "https://example.test/app.js", "status": 404, "resource_type": "script"}],
        performance={"load_ms": 123.4},
    )

    payload = packet.to_dict()

    assert payload["console_error_count"] == 1
    assert payload["failed_asset_count"] == 1
    assert payload["raw_content_visible"] is False
    assert build_browser_evidence_report(packet)["summary_hash"].startswith("sha256:")


def test_browser_evidence_rejects_secrets_and_host_paths():
    with pytest.raises(BrowserEvidenceError):
        BrowserEvidencePacket.create(
            target_url="https://example.test",
            captured_at="now",
            text_summary="Authorization: bearer abcdefghijk",
        )

    with pytest.raises(BrowserEvidenceError):
        BrowserEvidencePacket.create(
            target_url="https://example.test",
            captured_at="now",
            screenshot_artifact="C:/Users/private/screen.png",
        )
