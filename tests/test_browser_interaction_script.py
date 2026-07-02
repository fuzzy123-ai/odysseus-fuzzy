import pytest

from src.browser_interaction_script import BrowserInteractionScriptError, build_interaction_script


def test_interaction_script_counts_type_chars_without_text():
    script = build_interaction_script(
        [
            {"action": "navigate", "target": "https://www.asv-bw.de/"},
            {"action": "click", "selector": "button.search"},
            {"action": "type", "selector": "input.search", "text": "hilfe", "timeout_ms": 1000},
            {"action": "screenshot"},
        ]
    )

    assert script["step_count"] == 4
    assert script["steps"][2]["text_chars"] == 5
    assert "text" not in script["steps"][2]
    assert script["raw_content_visible"] is False


def test_interaction_script_rejects_secrets_or_unsafe_navigation():
    with pytest.raises(BrowserInteractionScriptError):
        build_interaction_script([{"action": "type", "selector": "input", "text": "token=secret"}])
    with pytest.raises(BrowserInteractionScriptError):
        build_interaction_script([{"action": "navigate", "target": "https://example.test/?token=secret"}])
