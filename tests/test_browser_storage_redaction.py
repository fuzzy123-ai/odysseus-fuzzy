import json

import pytest

from src.browser_storage_redaction import BrowserStorageRedactionError, summarize_storage_metadata


def test_storage_summary_hashes_names_and_omits_values():
    payload = summarize_storage_metadata(
        cookies=[{"name": "sessionid", "domain": ".asv-bw.de", "secure": True, "httpOnly": True, "sameSite": "Lax"}],
        local_storage_keys=["theme"],
        session_storage_keys=["lastPage"],
    )

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["cookie_count"] == 1
    assert payload["cookies"][0]["name_hash"].startswith("sha256:")
    assert payload["cookies"][0]["domain"] == "asv-bw.de"
    assert payload["raw_values_visible"] is False
    assert "sessionid" not in encoded
    assert "theme" not in encoded


def test_storage_summary_rejects_cookie_values():
    with pytest.raises(BrowserStorageRedactionError):
        summarize_storage_metadata(cookies=[{"name": "session", "value": "secret", "domain": "asv-bw.de"}])
