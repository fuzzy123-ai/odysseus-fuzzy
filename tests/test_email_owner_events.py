import sqlite3

from routes import email_owner_events


def test_email_tag_owner_clause_keeps_legacy_rows_only_for_ownerless_mode():
    clause, params = email_owner_events.email_tag_owner_clause_from_aliases(["", "alice"], "")

    assert clause == "(owner IN (?,?) OR owner IS NULL)"
    assert params == ["", "alice"]

    clause, params = email_owner_events.email_tag_owner_clause_from_aliases(["alice"], "alice")

    assert clause == "owner IN (?)"
    assert params == ["alice"]


def test_record_email_received_events_baselines_then_fires_for_new_inbox_messages(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr("src.event_bus.fire_event", lambda name, owner: events.append((name, owner)))
    db_path = tmp_path / "events.db"

    email_owner_events.record_email_received_events(
        "alice",
        "acct-a",
        "INBOX",
        [{"message_id": "<m1>"}, {"uid": "2"}],
        db_path=db_path,
    )

    assert events == []

    email_owner_events.record_email_received_events(
        "alice",
        "acct-a",
        "INBOX",
        [{"message_id": "<m1>"}, {"message_id": "<m3>"}],
        db_path=db_path,
    )

    assert events == [("email_received", "alice")]
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT owner, account_key, folder, message_key FROM email_event_seen ORDER BY message_key"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("alice", "acct-a", "INBOX", "2"),
        ("alice", "acct-a", "INBOX", "<m1>"),
        ("alice", "acct-a", "INBOX", "<m3>"),
    ]


def test_record_email_received_events_ignores_ownerless_or_non_inbox(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr("src.event_bus.fire_event", lambda name, owner: events.append((name, owner)))

    email_owner_events.record_email_received_events(
        "",
        "acct-a",
        "INBOX",
        [{"message_id": "<m1>"}],
        db_path=tmp_path / "events.db",
    )
    email_owner_events.record_email_received_events(
        "alice",
        "Sent",
        "Sent",
        [{"message_id": "<m1>"}],
        db_path=tmp_path / "events.db",
    )

    assert events == []
