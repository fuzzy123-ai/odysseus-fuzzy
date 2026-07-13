"""Regressions for preserving follow-up context during agent soft trimming."""

import logging

from src.context_budget import resolve_input_token_budget
from src.context_compactor import (
    latest_dialog_pair_preserved,
    trim_for_context,
)


def _content(message):
    value = message.get("content", "")
    return value if isinstance(value, str) else ""


def _assert_valid_tool_protocol(messages):
    """Every emitted tool result must answer exactly one emitted call."""
    index = 0
    while index < len(messages):
        message = messages[index]
        assert message.get("role") != "tool", "orphaned tool result"
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            index += 1
            continue

        call_ids = {str(call["id"]) for call in calls}
        result_ids = []
        index += 1
        while index < len(messages) and messages[index].get("role") == "tool":
            result_ids.append(str(messages[index].get("tool_call_id") or ""))
            index += 1
        assert set(result_ids) == call_ids
        assert len(result_ids) == len(set(result_ids))


def _production_history():
    messages = [
        {"role": "system", "content": "You are Odysseus."},
        {"role": "system", "content": "retrieved context " + "r" * 5000},
    ]
    for number in range(5):
        messages.extend([
            {"role": "user", "content": f"OLD-USER-{number} " + "u" * 1200},
            {"role": "assistant", "content": f"OLD-ANSWER-{number} " + "a" * 1200},
        ])
    messages.extend([
        {"role": "user", "content": "Build the pygame game."},
        {"role": "assistant", "content": "LATEST-ASSISTANT-MARKER The pygame game is ready."},
        {"role": "user", "content": "FOLLOW-UP-MARKER Why did you create HTML?"},
    ])
    return messages


def test_production_900_1024_followup_keeps_previous_answer_and_current_user():
    original = _production_history()
    decision = resolve_input_token_budget(
        900,
        1_000_000,
        True,
        endpoint_url="https://api.deepseek.com/v1/chat/completions",
        model="deepseek-v4-flash",
        output_reserve=1024,
    )

    assert decision.input_budget == 900
    trimmed = trim_for_context(original, decision.input_budget, reserve_tokens=0)
    joined = "\n".join(_content(message) for message in trimmed)

    assert "LATEST-ASSISTANT-MARKER" in joined
    assert "FOLLOW-UP-MARKER" in joined
    assert "OLD-USER-0" not in joined
    assert latest_dialog_pair_preserved(original, trimmed) is True
    assistant_index = next(i for i, m in enumerate(trimmed) if "LATEST-ASSISTANT-MARKER" in _content(m))
    user_index = next(i for i, m in enumerate(trimmed) if "FOLLOW-UP-MARKER" in _content(m))
    assert assistant_index < user_index


def test_oversized_previous_assistant_response_remains_visibly_truncated():
    original = [
        {"role": "system", "content": "You are Odysseus."},
        {"role": "user", "content": "Create a detailed implementation."},
        {
            "role": "assistant",
            "content": "ASSISTANT-HEAD " + "x" * 12_000 + " ASSISTANT-TAIL",
        },
        {"role": "user", "content": "CURRENT-USER-FOLLOW-UP"},
    ]

    trimmed = trim_for_context(original, context_length=300, reserve_tokens=0)
    assistant = next(message for message in trimmed if message.get("role") == "assistant")

    assert "ASSISTANT-HEAD" in _content(assistant)
    assert "previous assistant response was too large" in _content(assistant)
    assert trimmed[-1]["content"] == "CURRENT-USER-FOLLOW-UP"
    assert latest_dialog_pair_preserved(original, trimmed) is True


def test_tool_calls_and_results_are_id_complete_after_trim():
    original = [
        {"role": "system", "content": "You are Odysseus."},
        {"role": "system", "content": "discardable memory " + "m" * 5000},
        {"role": "tool", "tool_call_id": "orphan", "content": "orphan result"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "call-2", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "valid result"},
        {"role": "tool", "tool_call_id": "missing-call", "content": "invalid result"},
        {"role": "tool", "tool_call_id": "call-1", "content": "duplicate result"},
        {"role": "user", "content": "Continue from the tool result."},
    ]

    trimmed = trim_for_context(original, context_length=500, reserve_tokens=0)
    _assert_valid_tool_protocol(trimmed)

    assistant = next(message for message in trimmed if message.get("tool_calls"))
    assert [call["id"] for call in assistant["tool_calls"]] == ["call-1"]
    tool_results = [message for message in trimmed if message.get("role") == "tool"]
    assert [message["tool_call_id"] for message in tool_results] == ["call-1"]
    assert tool_results[0]["content"] == "valid result"


def test_trim_logging_reports_pair_status_without_message_contents(caplog):
    secret_marker = "PRIVATE-MESSAGE-CONTENT-MUST-NOT-BE-LOGGED"
    original = _production_history()
    original[2]["content"] += secret_marker

    caplog.set_level(logging.INFO, logger="src.context_compactor")
    trimmed = trim_for_context(original, context_length=500, reserve_tokens=0)

    assert latest_dialog_pair_preserved(original, trimmed) is True
    assert "latest_pair_preserved=True" in caplog.text
    assert secret_marker not in caplog.text
    assert "LATEST-ASSISTANT-MARKER" not in caplog.text
    assert "FOLLOW-UP-MARKER" not in caplog.text
