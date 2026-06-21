import json

from plugins.telegram.plugin import TelegramInboxStore, build_telegram_readiness, send_telegram_text
from src.telegram_formatting import chunk_telegram_html, render_telegram_markdown, validate_telegram_html


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


def test_send_telegram_text_uses_html_parse_mode_and_chunks(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    result = send_telegram_text("chat-1", "**Hello** " + ("x " * 2200), http_post=_post)

    assert result["ok"] is True
    assert result["telegram_message_id"] == 1
    assert result["message_count"] == len(calls)
    assert result["delivery_mode"] == "classic_html"
    assert result["formatting_mode"] == "html"
    assert all(call[1]["parse_mode"] == "HTML" for call in calls)


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
