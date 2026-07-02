import json

import pytest

from src.browser_devtools_evidence import BrowserDevtoolsEvidenceError, build_devtools_summary


def test_devtools_summary_redacts_console_and_network_details():
    summary = build_devtools_summary(
        console_events=[{"level": "error", "message": "TypeError: failed to fetch /api/private"}],
        network_events=[
            {
                "url": "https://www.asv-bw.de/app.js?token=secret",
                "method": "GET",
                "status": 404,
                "mime_type": "application/javascript",
                "duration_ms": 123.4,
            }
        ],
    )

    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True).lower()
    assert summary["console_error_count"] == 1
    assert summary["failed_request_count"] == 1
    assert summary["console_events"][0]["message_hash"].startswith("sha256:")
    assert summary["console_events"][0]["error_class"] == "type_error"
    assert summary["network_events"][0]["host"] == "www.asv-bw.de"
    assert summary["network_events"][0]["path_class"] == "script"
    assert summary["network_events"][0]["status_class"] == "4xx"
    assert "failed to fetch" not in encoded
    assert "token=secret" not in encoded
    assert summary["raw_content_visible"] is False


def test_devtools_summary_rejects_secrets_and_credentials():
    with pytest.raises(BrowserDevtoolsEvidenceError):
        build_devtools_summary(console_events=[{"level": "log", "message": "Authorization: Bearer abcdefghijk"}])
    with pytest.raises(BrowserDevtoolsEvidenceError):
        build_devtools_summary(network_events=[{"url": "https://user:pass@example.test/"}])
