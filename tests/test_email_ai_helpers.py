import sqlite3

from routes.email_ai_helpers import (
    build_ai_reply_prompt,
    cache_ai_reply,
    cache_email_summary,
    cached_ai_reply,
    extract_summary_content,
    summary_payload,
)


def _owner_clause(owner: str):
    return "owner = ?", [owner]


def _extract_reply(text: str) -> str:
    return text.strip()


def _style(text: str) -> str:
    return text.strip()


def test_cached_ai_reply_is_owner_scoped(tmp_path):
    db_path = tmp_path / "email.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE email_ai_replies (
            message_id TEXT,
            owner TEXT,
            uid TEXT,
            folder TEXT,
            reply TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO email_ai_replies
        (message_id, owner, uid, folder, reply, model_used, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("<m1>", "alice", "1", "INBOX", "alice reply", "ma", "2026-01-01"),
            ("<m1>", "bob", "2", "INBOX", "bob reply", "mb", "2026-01-02"),
        ],
    )
    conn.commit()
    conn.close()

    result = cached_ai_reply(
        db_path=db_path,
        message_id="<m1>",
        owner="bob",
        email_cache_owner_clause=_owner_clause,
        extract_reply=_extract_reply,
        apply_email_style_mechanics=_style,
    )

    assert result == {
        "success": True,
        "reply": "bob reply",
        "model_used": "mb",
        "cached": True,
    }


def test_cache_helpers_write_owner_scoped_rows(tmp_path):
    db_path = tmp_path / "email.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE email_summaries (
            message_id TEXT,
            owner TEXT,
            uid TEXT,
            folder TEXT,
            subject TEXT,
            sender TEXT,
            summary TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE email_ai_replies (
            message_id TEXT,
            owner TEXT,
            uid TEXT,
            folder TEXT,
            reply TEXT NOT NULL,
            model_used TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, owner)
        )
        """
    )
    conn.close()

    cache_email_summary(
        db_path=db_path,
        message_id="<m1>",
        owner="alice",
        uid="11",
        folder="Archive",
        subject="Subject",
        sender="sender@example.com",
        summary="- one",
        model="model-s",
    )
    cache_ai_reply(
        db_path=db_path,
        message_id="<m1>",
        owner="bob",
        uid="12",
        folder="INBOX",
        reply="reply",
        model="model-r",
    )

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT owner, uid, folder, summary, model_used FROM email_summaries"
        ).fetchone() == ("alice", "11", "Archive", "- one", "model-s")
        assert conn.execute(
            "SELECT owner, uid, folder, reply, model_used FROM email_ai_replies"
        ).fetchone() == ("bob", "12", "INBOX", "reply", "model-r")
    finally:
        conn.close()


def test_summary_payload_uses_attachment_instruction_and_token_key():
    payload = summary_payload(
        model="m",
        sender="sender@example.com",
        subject="Invoice",
        body_for_llm="Body\n\n--- ATTACHMENTS ---\n\nTotal 12.34",
        token_key="max_completion_tokens",
    )

    assert payload["max_completion_tokens"] == 8192
    assert payload["temperature"] == 0.3
    system = payload["messages"][0]["content"]
    user = payload["messages"][1]["content"]
    assert "USE THEIR CONTENTS" in system
    assert "Total 12.34" in user


def test_extract_summary_content_prefers_fenced_content_then_reasoning_bullets():
    assert extract_summary_content(
        {"content": "<<<SUMMARY>>>\n- final\n<<<END>>>"},
        extract_reply=lambda text: "- final",
    ) == "- final"

    assert extract_summary_content(
        {"content": "", "reasoning_content": "thinking\n- bullet\n1. second"},
        extract_reply=lambda text: "",
    ) == "- bullet\n1. second"


def test_build_ai_reply_prompt_includes_style_context_reference_and_hint():
    system, user = build_ai_reply_prompt(
        to="Sender <sender@example.com>",
        subject="Re: Question",
        original_body="Original body",
        user_hint="Keep it short",
        style="Warm and direct",
        context_snippets=["past context"],
        referenced="referenced docs",
        base_prompt="BASE",
    )

    assert "WRITING STYLE TO MATCH" in system
    assert "Warm and direct" in system
    assert "past context" in system
    assert "referenced docs" in system
    assert "Keep it short" in user
    assert "Original body" in user
