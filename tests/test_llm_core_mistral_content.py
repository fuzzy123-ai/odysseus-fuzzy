from src.llm_core import _detect_provider, _normalize_mistral_content, _supports_thinking


def test_detects_mistral_provider():
    assert _detect_provider("https://api.mistral.ai/v1/chat/completions") == "mistral"


def test_mistral_thinking_models_are_marked_thinking_capable():
    assert _supports_thinking("mistral-small-latest")
    assert _supports_thinking("magistral-medium-latest")


def test_string_passthrough_returns_text_with_empty_thinking():
    assert _normalize_mistral_content("hello") == ("hello", "")


def test_array_with_thinking_and_text_blocks():
    content = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "think "}], "closed": True},
        {"type": "text", "text": "answer"},
    ]
    assert _normalize_mistral_content(content) == ("answer", "think ")


def test_array_accepts_string_inner_thinking():
    content = [
        {"type": "thinking", "thinking": "inline"},
        {"type": "text", "text": "final"},
    ]
    assert _normalize_mistral_content(content) == ("final", "inline")


def test_garbage_content_returns_empty_strings():
    assert _normalize_mistral_content(None) == ("", "")
    assert _normalize_mistral_content([None, {"type": "missing"}]) == ("", "")
