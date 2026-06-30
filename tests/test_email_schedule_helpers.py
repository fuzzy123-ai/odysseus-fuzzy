import sqlite3
from datetime import datetime, timedelta, timezone

from routes import email_schedule_helpers


def _init_db(tmp_path):
    import routes.email_helpers as email_helpers

    db_path = tmp_path / "scheduled_emails.db"
    old_path = email_helpers.SCHEDULED_DB
    try:
        email_helpers.SCHEDULED_DB = db_path
        email_helpers._init_scheduled_db()
    finally:
        email_helpers.SCHEDULED_DB = old_path
    return db_path


def test_normalize_scheduled_send_at_converts_offsets_to_naive_utc():
    local = datetime.now(timezone(timedelta(hours=2))) + timedelta(hours=1)

    normalized = email_schedule_helpers.normalize_scheduled_send_at(local.isoformat())

    assert normalized == local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def test_normalize_scheduled_send_at_rejects_missing_invalid_or_past_values():
    for value, expected in [
        (None, "send_at required"),
        ("not a date", "send_at must be ISO8601"),
        ("1970-01-01T00:00:00", "send_at must be in the future"),
    ]:
        try:
            email_schedule_helpers.normalize_scheduled_send_at(value)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {value!r}")


def test_scheduled_email_rows_are_owner_scoped(tmp_path):
    db_path = _init_db(tmp_path)
    send_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    alice = email_schedule_helpers.schedule_email_row(
        {"to": "a@example.test", "body": "alice", "send_at": send_at},
        owner="alice",
        db_path=db_path,
    )
    bob = email_schedule_helpers.schedule_email_row(
        {"to": "b@example.test", "body": "bob", "send_at": send_at},
        owner="bob",
        db_path=db_path,
    )

    assert [row["id"] for row in email_schedule_helpers.list_scheduled_email_rows(owner="alice", db_path=db_path)] == [
        alice["id"]
    ]
    assert [row["id"] for row in email_schedule_helpers.list_scheduled_email_rows(owner="bob", db_path=db_path)] == [
        bob["id"]
    ]

    email_schedule_helpers.cancel_scheduled_email_row(bob["id"], owner="alice", db_path=db_path)
    assert [row["id"] for row in email_schedule_helpers.list_scheduled_email_rows(owner="bob", db_path=db_path)] == [
        bob["id"]
    ]


def test_pending_agent_draft_helpers_are_owner_scoped(tmp_path):
    db_path = _init_db(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO scheduled_emails
            (id, to_addr, subject, body, attachments, send_at, created_at, status, account_id, owner)
            VALUES (?, ?, ?, ?, '[]', '9999-12-31T00:00:00', ?, 'agent_draft', ?, ?)
            """,
            [
                ("draft-ownerless", "nobody@example.test", "Ownerless", "old", "2026-01-01", "acct-a", ""),
                ("draft-bob", "bob@example.test", "Bob", "bob body", "2026-01-02", "acct-b", "bob"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert email_schedule_helpers.list_pending_agent_draft_rows(owner="alice", db_path=db_path) == []
    assert [
        row["id"] for row in email_schedule_helpers.list_pending_agent_draft_rows(owner="bob", db_path=db_path)
    ] == ["draft-bob"]
    assert email_schedule_helpers.approve_agent_draft_row("draft-ownerless", owner="alice", db_path=db_path) is False
    assert email_schedule_helpers.cancel_agent_draft_row("draft-ownerless", owner="bob", db_path=db_path) is False
    assert email_schedule_helpers.approve_agent_draft_row("draft-bob", owner="bob", db_path=db_path) is True
