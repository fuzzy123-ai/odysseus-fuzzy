import json

import pytest

from src.browser_dom_observer import BrowserDomObserverError, summarize_dom_accessibility_diff


def test_dom_observer_summarizes_diffs_without_accessible_names():
    diff = summarize_dom_accessibility_diff(
        {"accessibility_roles": ["heading"], "tags": ["main"], "focusable_count": 1, "form_count": 0},
        {
            "accessibility_roles": ["heading", "button"],
            "tags": ["main", "button"],
            "focusable_count": 2,
            "form_count": 1,
            "accessible_names": ["Private Submit Text"],
        },
    )

    encoded = json.dumps(diff, ensure_ascii=False, sort_keys=True).lower()
    assert diff["role_delta"] == {"button": 1}
    assert diff["tag_delta"] == {"button": 1}
    assert diff["after_form_count"] == 1
    assert diff["accessible_name_hashes"][0].startswith("sha256:")
    assert "private submit text" not in encoded
    assert diff["raw_content_visible"] is False


def test_dom_observer_rejects_form_values():
    with pytest.raises(BrowserDomObserverError):
        summarize_dom_accessibility_diff({}, {"form_values": {"q": "secret"}})
