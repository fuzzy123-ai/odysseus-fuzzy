import asyncio
import json
from pathlib import Path

import src.agent_loop as agent_loop
from src.agent_loop_intent import _classify_agent_request
from src.agent_loop_orchestration import _empty_response_fallback
from src.telegram_turn_diagnostics import (
    build_telegram_turn_diagnostic,
    filter_telegram_fallback_candidates,
    parse_terminal_provider_error_sse,
    provider_error_sse,
    telegram_provider_error_reply,
)


def test_exact_german_followup_inherits_recent_changes_topic():
    messages = [
        {"role": "user", "content": "Was gibt's Neues?"},
        {
            "role": "assistant",
            "content": (
                "Die Compose-Sicherheitsinfrastruktur ist auf dem Weg in die "
                "Produktion. Soll ich dir eins der Detail-Dokumente zeigen?"
            ),
        },
        {"role": "user", "content": "Welche Punkte sind das?"},
    ]

    intent = _classify_agent_request(messages, "Welche Punkte sind das?")

    assert intent["continuation"] is True
    assert intent["low_signal"] is False
    assert intent["domains"] == {"changes"}
    assert "Was gibt's Neues?" in intent["retrieval_query"]
    assert "Welche Punkte sind das?" in intent["retrieval_query"]


def test_german_assistant_question_keeps_todo_domain_for_plain_answer():
    messages = [
        {"role": "user", "content": "Neue Aufgabe"},
        {
            "role": "assistant",
            "content": "Was möchtest du auf die Todo-Liste setzen?",
        },
        {"role": "user", "content": "Videos speichern und Tobi schicken"},
    ]

    intent = _classify_agent_request(
        messages,
        "Videos speichern und Tobi schicken",
    )

    assert intent["continuation"] is True
    assert intent["domains"] == {"todos"}
    assert "Neue Aufgabe" in intent["retrieval_query"]


def test_terminal_provider_error_is_closed_and_never_becomes_empty_response():
    raw = (
        'event: error\n'
        'data: {"status": 400, "text": "private provider payload", '
        '"request_id": "private-id"}\n\n'
    )

    failure = parse_terminal_provider_error_sse(raw)

    assert failure == {
        "schema": "odysseus.provider_failure.v1",
        "type": "provider_error",
        "status": 400,
        "error_class": "invalid_request",
        "retryable": False,
    }
    projected = provider_error_sse(failure)
    assert "private provider payload" not in projected
    assert "private-id" not in projected
    assert json.loads(projected.split("data: ", 1)[1]) == failure

    response, chunk = _empty_response_fallback(
        "",
        "",
        [],
        terminal_provider_error=failure,
    )
    assert response == ""
    assert chunk is None
    assert "empty response" not in response.lower()

    reply = telegram_provider_error_reply(failure)
    assert "HTTP 400" in reply
    assert "keine Antwort erzeugt" in reply
    assert "private" not in reply


def test_local_only_fallback_filter_never_returns_external_provider():
    candidates = [
        ("https://api.example.invalid/v1/chat/completions", "external", {"Authorization": "secret"}),
        ("http://127.0.0.1:11434/v1/chat/completions", "local", {}),
        ("http://odysseus.local/v1/chat/completions", "lan", {}),
    ]

    filtered = filter_telegram_fallback_candidates(
        candidates,
        local_only_required=True,
    )

    assert [(item[0], item[1]) for item in filtered] == [
        ("http://127.0.0.1:11434/v1/chat/completions", "local"),
        ("http://odysseus.local/v1/chat/completions", "lan"),
    ]
    assert filtered[0][2] == {}
    assert all("example.invalid" not in item[0] for item in filtered)


def test_turn_diagnostic_projection_is_bounded_and_content_free():
    diagnostic = build_telegram_turn_diagnostic(
        context_evidence={
            "retained_history_message_count": 2,
            "retained_history_character_count": 1375,
            "omitted_history_message_count": 0,
        },
        agent_metrics={
            "continuation": True,
            "inherited_domain_count": 1,
            "selected_tool_count": 3,
            "provider_failure": {
                "status": 400,
                "error_class": "invalid_request",
                "private": "must-not-pass",
            },
            "fallback_attempted": True,
            "fallback_succeeded": False,
            "raw_prompt": "must-not-pass",
        },
    )

    assert diagnostic == {
        "schema": "odysseus.telegram_turn_diagnostic.v1",
        "binding_reused": True,
        "retained_history_message_count": 2,
        "retained_history_character_count": 1375,
        "omitted_history_message_count": 0,
        "continuation": True,
        "inherited_domain_count": 1,
        "selected_tool_count": 3,
        "provider_status": 400,
        "provider_error_class": "invalid_request",
        "fallback_attempted": True,
        "fallback_succeeded": False,
    }
    serialized = json.dumps(diagnostic, sort_keys=True)
    assert "must-not-pass" not in serialized
    assert "raw_prompt" not in serialized


def test_real_agent_loop_projects_terminal_error_and_skips_generic_empty(monkeypatch):
    monkeypatch.setattr(
        agent_loop,
        "get_setting",
        lambda key, default=None: default,
        raising=False,
    )
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(
        agent_loop,
        "get_function_tool_schemas",
        lambda: [],
        raising=False,
    )

    async def fake_stream(_candidates, messages, **kwargs):
        yield (
            'event: error\n'
            'data: {"status": 400, "text": "private provider payload", '
            '"request_id": "private-id"}\n\n'
        )

    monkeypatch.setattr(
        agent_loop,
        "stream_llm_with_fallback",
        fake_stream,
        raising=False,
    )

    async def collect():
        return [
            chunk
            async for chunk in agent_loop.stream_agent_loop(
                "https://api.deepseek.com/v1/chat/completions",
                "deepseek-v4-flash",
                [{"role": "user", "content": "Welche Punkte sind das?"}],
                max_rounds=2,
                relevant_tools={"ask_user"},
            )
        ]

    chunks = asyncio.run(collect())

    assert not any("empty response" in chunk.lower() for chunk in chunks)
    assert not any("private provider payload" in chunk for chunk in chunks)
    assert not any("private-id" in chunk for chunk in chunks)
    errors = [chunk for chunk in chunks if chunk.startswith("event: error")]
    assert len(errors) == 1
    assert '"error_class": "invalid_request"' in errors[0]
    metrics = [
        json.loads(chunk[6:])["data"]["telegram_turn"]
        for chunk in chunks
        if chunk.startswith("data: ") and '"type": "metrics"' in chunk
    ]
    assert metrics == [
        {
            "continuation": True,
            "inherited_domain_count": 0,
            "selected_tool_count": 0,
            "provider_failure": {
                "schema": "odysseus.provider_failure.v1",
                "type": "provider_error",
                "status": 400,
                "error_class": "invalid_request",
                "retryable": False,
            },
            "fallback_attempted": False,
            "fallback_succeeded": False,
        }
    ]


def test_telegram_handler_binds_fallback_error_and_closed_diagnostics():
    source = Path("app.py").read_text(encoding="utf-8-sig")
    start = source.index("def _telegram_agent_turn_handler")
    end = source.index("\n\napp.state.telegram_session_bridge", start)
    body = source[start:end]

    assert "resolve_chat_fallback_candidates(owner=owner)" in body
    assert "filter_telegram_fallback_candidates(" in body
    assert "local_only_required=telegram_local_only_required" in body
    assert "fallbacks=fallback_candidates" in body
    assert 'if chunk.startswith("event: error")' in body
    assert "telegram_provider_error_reply(" in body
    assert "build_telegram_turn_diagnostic(" in body
    assert "context_evidence=context_window.evidence" not in body
