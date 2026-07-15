from __future__ import annotations

from core.session_manager import SessionManager


MODEL = "odysseus-utf8-byte-v1"


def _manager_with_messages(messages: list[dict]) -> SessionManager:
    manager = SessionManager.__new__(SessionManager)
    manager._context_message_limit = lambda: 100
    manager._recent_context_messages_from_cache = lambda session_id, limit: messages
    manager._recent_context_messages_from_db = lambda session_id, limit: (_ for _ in ()).throw(
        AssertionError("database fallback must not run")
    )
    return manager


def test_model_hint_bypasses_wrong_legacy_scalar_without_mutating_it() -> None:
    old = {
        "role": "user",
        "content": "界" * 200,
        "metadata": {"estimated_tokens": 1},
    }
    recent = {
        "role": "assistant",
        "content": "ok",
        "metadata": {"estimated_tokens": 1},
    }
    manager = _manager_with_messages([old, recent])

    selected = manager.get_recent_context_messages(
        "session-1",
        token_budget=256,
        reserve_tokens=0,
        model_hint=MODEL,
    )

    assert selected == [recent]
    assert old["metadata"]["estimated_tokens"] == 1
    assert recent["metadata"]["estimated_tokens"] == 1


def test_no_hint_reuses_legacy_cached_scalar() -> None:
    messages = [
        {"role": "user", "content": "界" * 200, "metadata": {"estimated_tokens": 1}},
        {"role": "assistant", "content": "ok", "metadata": {"estimated_tokens": 1}},
    ]
    manager = _manager_with_messages(messages)

    selected = manager.get_recent_context_messages(
        "session-1",
        token_budget=256,
        reserve_tokens=0,
    )

    assert selected == messages
    assert [item["metadata"]["estimated_tokens"] for item in messages] == [1, 1]
