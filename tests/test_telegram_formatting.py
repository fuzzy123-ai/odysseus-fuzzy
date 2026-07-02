import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.plugin import (
    TelegramInboxStore,
    build_telegram_draft_id,
    build_telegram_readiness,
    send_telegram_rich_draft,
    send_telegram_rich_message,
    send_telegram_text,
    setup,
)
from src.telegram_formatting import chunk_telegram_html, render_telegram_markdown, validate_telegram_html
from tests.test_telegram_plugin import _PluginContext


def test_telegram_markdown_renderer_outputs_safe_html():
    rendered = render_telegram_markdown(
        "# Title\n\n**Bold** *italic* __under__ ~~strike~~ ||secret|| `code`\n"
        "[OpenAI](https://openai.com)\n> quote\n<script>alert(1)</script>"
    )

    assert rendered.parse_mode == "HTML"
    assert rendered.formatting_mode == "html"
    assert "<b>Title</b>" in rendered.html
    assert "<b>Bold</b>" in rendered.html
    assert "<i>italic</i>" in rendered.html
    assert "<u>under</u>" in rendered.html
    assert "<s>strike</s>" in rendered.html
    assert "<tg-spoiler>secret</tg-spoiler>" in rendered.html
    assert '<a href="https://openai.com">OpenAI</a>' in rendered.html
    assert "<blockquote>quote</blockquote>" in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert validate_telegram_html(rendered.html) is True


def test_telegram_renderer_rejects_unsafe_link_targets():
    rendered = render_telegram_markdown("[bad](javascript:alert(1)) and <b raw>")

    assert "javascript:alert" in rendered.html
    assert "<a href" not in rendered.html
    assert "&lt;b raw&gt;" in rendered.html
    assert validate_telegram_html(rendered.html) is True


def test_telegram_tables_and_fenced_code_are_safe_pre_blocks():
    rendered = render_telegram_markdown("| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('<x>')\n```")

    assert "<pre>| A | B |" in rendered.html
    assert "<pre><code>print(&#x27;&lt;x&gt;&#x27;)</code></pre>" in rendered.html
    assert validate_telegram_html(rendered.html) is True


def test_telegram_chunking_respects_classic_message_limit():
    chunks = chunk_telegram_html(("word " * 1200).strip(), max_chars=4096)

    assert len(chunks) == 2
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_send_telegram_text_uses_html_parse_mode_for_single_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    result = send_telegram_text("chat-1", "**Hello**", http_post=_post)

    assert result["ok"] is True
    assert result["telegram_message_id"] == 1
    assert result["message_count"] == 1
    assert result["delivery_mode"] == "classic_html"
    assert result["formatting_mode"] == "html"
    assert all(call[1]["parse_mode"] == "HTML" for call in calls)


def test_send_telegram_text_uses_plaintext_chunks_for_long_html(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    long_code = "```python\n" + "\n".join(f"print({index})" for index in range(900)) + "\n```"
    result = send_telegram_text("chat-1", "**Plan**\n" + long_code, http_post=_post)

    assert result["ok"] is True
    assert result["message_count"] == len(calls)
    assert result["message_count"] > 1
    assert result["delivery_mode"] == "classic_plaintext_chunks"
    assert result["formatting_mode"] == "plaintext_chunk_fallback"
    assert result["parse_mode"] == ""
    assert result["truncated"] is False
    assert all("parse_mode" not in call[1] for call in calls)
    assert all(len(call[1]["text"]) <= 4096 for call in calls)
    assert calls[0][1]["text"].startswith("Teil 1/")


def test_send_telegram_text_caps_long_replies(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_MAX_REPLY_CHUNKS", "2")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    huge_reply = "```python\n" + "\n".join(f"print({index})" for index in range(2000)) + "\n```"
    result = send_telegram_text("chat-1", huge_reply, http_post=_post)

    assert result["ok"] is True
    assert result["message_count"] == 2
    assert result["max_reply_chunks"] == 2
    assert result["truncated"] is True
    assert result["delivery_mode"] == "classic_plaintext_chunks_truncated"
    assert calls[0][1]["text"].startswith("Teil 1/2")
    assert calls[1][1]["text"].startswith("Teil 2/2")
    assert "Weitere Teile wurden gekuerzt" in calls[1][1]["text"]
    assert all("parse_mode" not in call[1] for call in calls)
    assert all(len(call[1]["text"]) <= 4096 for call in calls)


def test_readiness_exposes_rich_status_without_raw_payloads(tmp_path):
    store = TelegramInboxStore(tmp_path)
    store.append_outbound(
        "chat-1",
        "**Hello**",
        delivery_status="sent",
        delivery_mode="classic_html",
        formatting_mode="html",
    )

    readiness = build_telegram_readiness(tmp_path)
    encoded = json.dumps(readiness, ensure_ascii=False)

    assert readiness["rich_messages_enabled"] is False
    assert readiness["rich_drafts_enabled"] is False
    assert readiness["formatting_mode"] == "html"
    assert readiness["last_delivery_mode"] == "classic_html"
    assert readiness["last_delivery_status"] == "sent"
    assert readiness["raw_rich_payload_visible"] is False
    assert "chat-1" not in encoded


def test_rich_draft_helpers_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_RICH_MESSAGES_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_RICH_DRAFTS_ENABLED", raising=False)
    calls = []

    with pytest.raises(ValueError, match="rich messages"):
        send_telegram_rich_draft("chat-1", "partial", http_post=lambda url, payload: calls.append(payload))

    assert calls == []


def test_rich_draft_uses_stable_nonzero_draft_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_RICH_DRAFTS_ENABLED", "true")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": 7}}

    first = build_telegram_draft_id(chat_id="chat-1", source_message_id=42)
    second = build_telegram_draft_id(chat_id="chat-1", source_message_id=42)
    result = send_telegram_rich_draft(
        "chat-1",
        "<tg-thinking>draft only</tg-thinking>\n**Partial**",
        source_message_id=42,
        http_post=_post,
    )

    assert first == second
    assert first > 0
    assert result["delivery_mode"] == "rich_draft"
    assert result["draft_id"] == first
    assert result["draft_id_value_visible"] is False
    payload = calls[0][1]
    assert payload["draft_id"] == first
    assert "rich_message" in payload
    assert "Partial" in payload["rich_message"]
    assert result["raw_rich_payload_visible"] is False


def test_final_rich_message_strips_draft_thinking(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "true")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": 10}}

    result = send_telegram_rich_message(
        "chat-1",
        "<tg-thinking>draft only</tg-thinking>\n**Final**",
        http_post=_post,
    )

    assert result["delivery_mode"] == "rich_final"
    assert result["telegram_message_id"] == 10
    assert "Final" in calls[0][1]["rich_message"]
    assert "draft only" not in calls[0][1]["rich_message"]
    assert result["raw_rich_payload_visible"] is False


def test_reply_route_falls_back_to_classic_html_when_final_rich_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "true")

    def _rich_fail(*_args, **_kwargs):
        raise ValueError("rich transport offline")

    classic_calls = []

    def _classic(chat_id, text):
        classic_calls.append((chat_id, text))
        return {
            "ok": True,
            "telegram_message_id": 77,
            "delivery_mode": "classic_html",
            "formatting_mode": "html",
            "token_value_visible": False,
            "raw_rich_payload_visible": False,
        }

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_rich_message", _rich_fail)
    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _classic)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/reply", json={"chat_id": "123", "text": "**Hallo**"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"]["delivery_mode"] == "classic_html_fallback"
    assert payload["sent"]["rich_fallback_reason"] == "rich transport offline"
    assert classic_calls == [("123", "**Hallo**")]
    history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert history[0]["delivery_mode"] == "classic_html_fallback"
    assert history[0]["formatting_mode"] == "html"
