from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from core.models import ChatMessage, Session
from src.context_compactor import trim_for_context
from src.telegram_context_policy import (
    TELEGRAM_CONTEXT_POLICY_SCHEMA,
    build_telegram_turn_context,
)


def test_long_chat_keeps_latest_user_turn_and_domain_policy():
    history = []
    for index in range(40):
        history.extend(
            [
                {"role": "user", "content": f"old user {index} " + ("u" * 80)},
                {
                    "role": "assistant",
                    "content": f"old assistant {index}: todo saved " + ("a" * 80),
                },
            ]
        )

    window = build_telegram_turn_context(
        history,
        "Liste jetzt meine offenen To-dos.",
        max_history_messages=8,
        max_history_characters=800,
    )
    messages = list(window.messages)

    assert messages[0]["role"] == "system"
    assert messages[0]["_protected"] is True
    assert "call `manage_todos`" in messages[0]["content"]
    assert "summaries, Memory and retrieved context" in messages[0]["content"]
    assert messages[-1] == {
        "role": "user",
        "content": "Liste jetzt meine offenen To-dos.",
        "_protected": True,
    }
    assert window.evidence["retained_history_message_count"] <= 8
    assert window.evidence["retained_history_character_count"] <= 800

    trimmed = trim_for_context(messages, context_length=700, reserve_tokens=0)
    assert any(
        message.get("role") == "system" and "call `manage_todos`" in message.get("content", "")
        for message in trimmed
    )
    assert any(
        message.get("role") == "user"
        and message.get("content") == "Liste jetzt meine offenen To-dos."
        for message in trimmed
    )


def test_persisted_summaries_are_omitted_and_never_domain_authority():
    history = [
        {
            "role": "system",
            "content": "[Conversation summary] The fabricated todo is open.",
            "metadata": {"compacted": True},
        },
        {
            "role": "system",
            "content": "CURRENT TASK STATE: todo completed",
            "metadata": {"task_state": True},
        },
        {"role": "user", "content": "Was hatten wir besprochen?"},
        {"role": "assistant", "content": "A continuity answer."},
    ]

    window = build_telegram_turn_context(history, "Welche To-dos sind offen?")
    rendered = "\n".join(str(message.get("content") or "") for message in window.messages)

    assert "fabricated todo" not in rendered
    assert "CURRENT TASK STATE" not in rendered
    assert window.evidence["omitted_system_message_count"] == 2
    assert window.evidence["todo_state_authority"] == "manage_todos"
    assert window.evidence["summary_authoritative"] is False
    assert window.evidence["memory_authoritative"] is False


def test_builder_does_not_rewrite_or_append_to_existing_session():
    session = Session(
        id="telegram-session",
        name="Telegram",
        endpoint_url="http://localhost:1234/v1",
        model="local-model",
        history=[
            ChatMessage("user", "first"),
            ChatMessage("assistant", "second"),
            ChatMessage("system", "[Conversation summary] stale", {"compacted": True}),
        ],
    )
    history_identity = id(session.history)
    original_history = deepcopy(session.history)
    context = session.get_context_messages()

    window = build_telegram_turn_context(context, "current")

    assert id(session.history) == history_identity
    assert session.history == original_history
    assert session.message_count == 0
    assert window.evidence["session_mutated"] is False


def test_telegram_handler_uses_bounded_copy_without_persisting_compaction():
    source = Path("app.py").read_text(encoding="utf-8-sig")
    start = source.index("def _telegram_agent_turn_handler")
    end = source.index("\n\napp.state.telegram_session_bridge", start)
    body = source[start:end]

    assert "build_telegram_turn_context" in body
    assert "context = session.get_context_messages()" in body
    assert "messages = list(context_window.messages)" in body
    assert "maybe_compact" not in body
    assert "replace_messages" not in body


def test_supplemental_context_stays_untrusted_and_precedes_current_turn():
    supplemental = {
        "role": "user",
        "content": "retrieved narrative",
        "metadata": {"trusted": False, "source": "rag"},
    }
    window = build_telegram_turn_context(
        [{"role": "assistant", "content": "previous answer"}],
        "current user turn",
        supplemental_messages=[supplemental],
    )

    assert window.messages[-2]["content"] == "retrieved narrative"
    assert window.messages[-2]["metadata"]["trusted"] is False
    assert window.messages[-1]["content"] == "current user turn"
    assert window.evidence["supplemental_message_count"] == 1


def test_evidence_is_deterministic_and_contains_no_raw_conversation_text():
    secret = "private todo text that must not appear in evidence"
    first = build_telegram_turn_context(
        [{"role": "user", "content": secret}],
        "current",
    )
    second = build_telegram_turn_context(
        [{"role": "user", "content": secret}],
        "current",
    )

    assert first.evidence == second.evidence
    assert first.evidence["schema"] == TELEGRAM_CONTEXT_POLICY_SCHEMA
    assert first.evidence["history_fingerprint"].startswith("sha256:")
    assert secret not in json.dumps(first.evidence, sort_keys=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_history_messages", 0), ("max_history_characters", -1)],
)
def test_limits_fail_closed(field, value):
    kwargs = {field: value}
    with pytest.raises(ValueError, match="positive integer"):
        build_telegram_turn_context([], "current", **kwargs)


def test_empty_current_user_turn_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        build_telegram_turn_context([], "   ")
