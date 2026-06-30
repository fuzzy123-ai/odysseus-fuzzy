import sqlite3

from routes.email_list_helpers import (
    list_email_contacts_from_tags,
    list_email_rows_from_grouped_headers,
    load_email_tags_by_message_id,
    load_email_tags_by_uid,
    normalize_email_tags,
    search_email_row_from_fetch_data,
)


def _owner_clause(_account_id, owner):
    return "owner = ?", [owner]


def _uid_from_meta(meta: bytes) -> str:
    text = meta.decode(errors="replace")
    marker = "UID "
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].split()[0]


def _decode(value: str) -> str:
    return value


def _headers(uid: str, message_id: str, date: str, subject: str = "Hello"):
    meta = f"1 (UID {uid} FLAGS (\\Seen) RFC822.SIZE 42 RFC822.HEADER {{1}}".encode()
    raw = (
        f"From: Alice <alice@example.com>\r\n"
        f"To: Bob <bob@example.com>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Date: {date}\r\n"
        "\r\n"
    ).encode()
    return meta, raw


def _create_tag_table(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE email_tags (uid TEXT, message_id TEXT, folder TEXT, owner TEXT, tags TEXT, spam_verdict INTEGER, sender TEXT)"
    )
    return conn


def test_normalize_email_tags_maps_promo_alias_and_rejects_non_lists():
    assert normalize_email_tags('["promo", "urgent"]') == ["marketing", "urgent"]
    assert normalize_email_tags('{"not": "a-list"}') == []
    assert normalize_email_tags("not-json") == []


def test_load_email_tags_by_uid_is_owner_scoped(tmp_path):
    db_path = tmp_path / "scheduled.db"
    conn = _create_tag_table(db_path)
    conn.execute("INSERT INTO email_tags VALUES (?, ?, ?, ?, ?, ?, ?)", ("7", "", "INBOX", "alice", '["promo"]', 1, "Alice <alice@example.com>"))
    conn.execute("INSERT INTO email_tags VALUES (?, ?, ?, ?, ?, ?, ?)", ("7", "", "INBOX", "bob", '["urgent"]', 0, "Bob <bob@example.com>"))
    conn.commit()
    conn.close()

    rows = load_email_tags_by_uid(
        db_path,
        folder="INBOX",
        account_id="acct",
        owner="bob",
        uid_list=[b"7"],
        email_tag_owner_clause=_owner_clause,
    )

    assert rows == {"7": {"tags": ["urgent"], "spam": False}}


def test_load_email_tags_by_message_id_reads_ids_from_grouped_headers(tmp_path):
    db_path = tmp_path / "scheduled.db"
    conn = _create_tag_table(db_path)
    conn.execute(
        "INSERT INTO email_tags VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("", "<m1@example.com>", "INBOX", "bob", '["promo"]', 1, "Bob <bob@example.com>"),
    )
    conn.commit()
    conn.close()

    rows = load_email_tags_by_message_id(
        db_path,
        folder="INBOX",
        account_id=None,
        owner="bob",
        grouped=[_headers("1", "<m1@example.com>", "Thu, 01 Jan 2026 12:00:00 +0000")],
        email_tag_owner_clause=_owner_clause,
    )

    assert rows == {"<m1@example.com>": {"tags": ["marketing"], "spam": True}}


def test_list_email_rows_from_grouped_headers_shapes_and_sorts_rows():
    older = _headers("1", "<old@example.com>", "Thu, 01 Jan 2026 12:00:00 +0000", "Old")
    newer = _headers("2", "<new@example.com>", "Thu, 01 Jan 2026 13:00:00 +0000", "New")

    rows = list_email_rows_from_grouped_headers(
        [older, newer],
        tag_by_uid={"1": {"tags": ["uid-tag"], "spam": False}},
        tag_by_message_id={"<new@example.com>": {"tags": ["mid-tag"], "spam": True}},
        uid_from_fetch_meta=_uid_from_meta,
        decode_header=_decode,
    )

    assert [row["uid"] for row in rows] == ["2", "1"]
    assert rows[0]["subject"] == "New"
    assert rows[0]["tags"] == ["mid-tag"]
    assert rows[0]["is_spam_verdict"] is True
    assert rows[1]["tags"] == ["uid-tag"]


def test_search_email_row_from_fetch_data_keeps_effective_folder_and_flags():
    grouped = [_headers("9", "<search@example.com>", "Thu, 01 Jan 2026 12:00:00 +0000", "Search")]
    meta, raw = grouped[0]
    msg_data = [(meta, raw)]

    row = search_email_row_from_fetch_data(
        msg_data,
        effective_folder="[Gmail]/All Mail",
        group_uid_fetch_records=lambda data: data,
        uid_from_fetch_meta=_uid_from_meta,
        decode_header=_decode,
    )

    assert row is not None
    assert row["uid"] == "9"
    assert row["folder"] == "[Gmail]/All Mail"
    assert row["is_read"] is True
    assert row["subject"] == "Search"


def test_search_email_row_from_fetch_data_returns_none_without_uid_or_header():
    assert search_email_row_from_fetch_data(
        [(b"1 (FLAGS ())", b"Subject: Missing uid\r\n\r\n")],
        effective_folder="INBOX",
        group_uid_fetch_records=lambda data: data,
        uid_from_fetch_meta=_uid_from_meta,
        decode_header=_decode,
    ) is None


def test_list_email_contacts_from_tags_scopes_dedupes_and_filters(tmp_path):
    db_path = tmp_path / "scheduled.db"
    conn = _create_tag_table(db_path)
    conn.executemany(
        "INSERT INTO email_tags VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("1", "<m1>", "INBOX", "bob", "[]", 0, "Bob Beta <bob@example.com>"),
            ("2", "<m2>", "INBOX", "bob", "[]", 0, "Alice Alpha <alice@example.com>"),
            ("3", "<m3>", "INBOX", "bob", "[]", 0, "Duplicate <bob@example.com>"),
            ("4", "<m4>", "INBOX", "alice", "[]", 0, "Alice Other <private@example.com>"),
            ("5", "<m5>", "INBOX", "bob", "[]", 0, "No Address"),
        ],
    )
    conn.commit()
    conn.close()

    result = list_email_contacts_from_tags(
        db_path,
        owner="bob",
        query="ali",
        limit=10,
        email_tag_owner_clause=_owner_clause,
    )

    assert result == {"contacts": [{"name": "Alice Alpha", "address": "alice@example.com"}]}


def test_list_email_contacts_from_tags_sorts_query_prefix_first(tmp_path):
    db_path = tmp_path / "scheduled.db"
    conn = _create_tag_table(db_path)
    conn.executemany(
        "INSERT INTO email_tags VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("1", "<m1>", "INBOX", "bob", "[]", 0, "Team Alpha <team-alpha@example.com>"),
            ("2", "<m2>", "INBOX", "bob", "[]", 0, "Alice Team <alice@example.com>"),
        ],
    )
    conn.commit()
    conn.close()

    result = list_email_contacts_from_tags(
        db_path,
        owner="bob",
        query="team",
        limit=10,
        email_tag_owner_clause=_owner_clause,
    )

    assert result["contacts"] == [
        {"name": "Team Alpha", "address": "team-alpha@example.com"},
        {"name": "Alice Team", "address": "alice@example.com"},
    ]


def test_list_email_contacts_from_tags_returns_safe_error_on_db_failure(tmp_path):
    result = list_email_contacts_from_tags(
        tmp_path / "missing.db",
        owner="bob",
        email_tag_owner_clause=_owner_clause,
    )

    assert result == {"contacts": [], "error": "Mail operation failed"}
