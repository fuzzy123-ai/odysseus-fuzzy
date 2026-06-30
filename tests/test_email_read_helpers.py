import json
import sqlite3
import asyncio

from routes.email_read_helpers import (
    load_read_cached_extras,
    schedule_recent_email_warm,
    select_recent_warm_reads,
)
from src.email_thread_parser import THREAD_PARSER_VERSION


def _owner_clause(owner: str):
    return "owner = ?", [owner]


def _create_read_cache_tables(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE email_summaries (message_id TEXT, owner TEXT, summary TEXT)")
    conn.execute("CREATE TABLE email_ai_replies (message_id TEXT, owner TEXT, reply TEXT)")
    conn.execute("CREATE TABLE email_boundaries (message_id TEXT, sig_start INTEGER, quote_start INTEGER, turns_json TEXT)")
    conn.execute("CREATE TABLE sender_signatures (from_address TEXT, owner TEXT, signature_text TEXT)")
    return conn


def test_load_read_cached_extras_is_owner_scoped_and_uses_versioned_turn_cache(tmp_path):
    db_path = tmp_path / "scheduled_emails.db"
    conn = _create_read_cache_tables(db_path)
    turns = [{"author": "bob", "text": "hello"}]
    conn.execute("INSERT INTO email_summaries VALUES (?, ?, ?)", ("<m1>", "alice", "alice summary"))
    conn.execute("INSERT INTO email_summaries VALUES (?, ?, ?)", ("<m1>", "bob", "bob summary"))
    conn.execute("INSERT INTO email_ai_replies VALUES (?, ?, ?)", ("<m1>", "bob", "raw reply"))
    conn.execute(
        "INSERT INTO email_boundaries VALUES (?, ?, ?, ?)",
        ("<m1>", 10, 20, json.dumps({"v": THREAD_PARSER_VERSION, "turns": turns})),
    )
    conn.execute("INSERT INTO sender_signatures VALUES (?, ?, ?)", ("sender@example.com", "alice", "alice sig"))
    conn.execute("INSERT INTO sender_signatures VALUES (?, ?, ?)", ("sender@example.com", "bob", "bob sig"))
    conn.commit()
    conn.close()

    result = load_read_cached_extras(
        db_path,
        "bob",
        " <m1> ",
        "Sender@Example.com",
        "<p>ignored</p>",
        "ignored",
        email_cache_owner_clause=_owner_clause,
        apply_email_style_mechanics=lambda value: f"styled:{value}",
        extract_reply=lambda value: value.replace("raw", "clean"),
        thread_parser=lambda _html, _text: [{"author": "fallback"}],
    )

    assert result == {
        "cached_summary": "bob summary",
        "cached_ai_reply": "styled:clean reply",
        "boundaries": {"sig_start": 10, "quote_start": 20},
        "thread_turns": turns,
        "sender_signature": "bob sig",
    }


def test_load_read_cached_extras_parses_turns_when_cache_is_missing(tmp_path):
    db_path = tmp_path / "scheduled_emails.db"
    conn = _create_read_cache_tables(db_path)
    conn.commit()
    conn.close()

    result = load_read_cached_extras(
        db_path,
        "bob",
        "<m1>",
        "",
        "<p>hi</p>",
        "hi",
        email_cache_owner_clause=_owner_clause,
        apply_email_style_mechanics=lambda value: value,
        extract_reply=lambda value: value,
        thread_parser=lambda html, text: [{"html": html, "text": text}],
    )

    assert result["cached_summary"] is None
    assert result["cached_ai_reply"] is None
    assert result["boundaries"] is None
    assert result["sender_signature"] is None
    assert result["thread_turns"] == [{"html": "<p>hi</p>", "text": "hi"}]


def test_select_recent_warm_reads_filters_and_marks_selected_keys():
    warming = {"ck-existing-warm"}
    cached = {"ck-cached"}

    def cache_key(_account_id, _folder, uid, *, owner=""):
        return f"ck-{uid}"

    selected = select_recent_warm_reads(
        [
            {"uid": "", "date_epoch": 1000, "size": 1},
            {"uid": "old", "date_epoch": 100, "size": 1},
            {"uid": "large", "date_epoch": 1000, "size": 99},
            {"uid": "cached", "date_epoch": 1000, "size": 1},
            {"uid": "existing-warm", "date_epoch": 1000, "size": 1},
            {"uid": "fresh", "date_epoch": 1000, "size": 1},
            {"uid": "second", "date_epoch": 1000, "size": 1},
        ],
        folder="INBOX",
        account_id="acct",
        owner="bob",
        now=1000,
        recent_seconds=50,
        max_bytes=10,
        read_limit=1,
        read_cache_key=cache_key,
        read_cache_get=lambda key: {"hit": True} if key in cached else None,
        warming_reads=warming,
    )

    assert selected == [("fresh", "ck-fresh")]
    assert "ck-fresh" in warming
    assert "ck-second" not in warming


def test_select_recent_warm_reads_skips_scheduled_folder():
    warming = set()

    selected = select_recent_warm_reads(
        [{"uid": "fresh", "date_epoch": 1000, "size": 1}],
        folder="__scheduled__",
        account_id=None,
        owner="bob",
        now=1000,
        recent_seconds=50,
        max_bytes=10,
        read_limit=1,
        read_cache_key=lambda *_args, **_kwargs: "ck",
        read_cache_get=lambda _key: None,
        warming_reads=warming,
    )

    assert selected == []
    assert warming == set()


def test_schedule_recent_email_warm_reads_and_clears_warming_set():
    warming = set()
    cached = {}
    created = []

    def cache_key(_account_id, _folder, uid, *, owner=""):
        return f"ck-{owner}-{uid}"

    async def fake_to_thread(func, *args):
        return func(*args)

    def fake_read(uid, folder, account_id, owner, mark_seen):
        assert (uid, folder, account_id, owner, mark_seen) == ("fresh", "INBOX", "acct", "bob", False)
        return {"uid": uid, "body": "warm"}

    scheduled = schedule_recent_email_warm(
        [{"uid": "fresh", "date_epoch": 1000, "size": 1}],
        folder="INBOX",
        account_id="acct",
        owner="bob",
        recent_seconds=50,
        max_bytes=10,
        read_limit=1,
        read_cache_key=cache_key,
        read_cache_get=lambda key: cached.get(key),
        read_cache_put=lambda key, value: cached.__setitem__(key, value),
        read_email_sync=fake_read,
        warming_reads=warming,
        now=lambda: 1000,
        create_task=lambda coro: created.append(coro),
        to_thread=fake_to_thread,
        sleep=lambda _seconds: asyncio.sleep(0),
    )

    assert scheduled is True
    assert len(created) == 1
    assert warming == {"ck-bob-fresh"}

    asyncio.run(created[0])

    assert cached == {"ck-bob-fresh": {"uid": "fresh", "body": "warm"}}
    assert warming == set()


def test_schedule_recent_email_warm_noops_without_selected_reads():
    created = []

    scheduled = schedule_recent_email_warm(
        [{"uid": "fresh", "date_epoch": 1000, "size": 1}],
        folder="__scheduled__",
        account_id=None,
        owner="bob",
        recent_seconds=50,
        max_bytes=10,
        read_limit=1,
        read_cache_key=lambda *_args, **_kwargs: "ck",
        read_cache_get=lambda _key: None,
        read_cache_put=lambda _key, _value: None,
        read_email_sync=lambda *_args: {"ok": True},
        warming_reads=set(),
        now=lambda: 1000,
        create_task=lambda coro: created.append(coro),
    )

    assert scheduled is False
    assert created == []
