import email

from routes.email_message_shapes import (
    fetch_flags_from_meta,
    fetch_size_from_meta,
    list_email_row_from_header,
    message_has_attachments,
    parse_email_datetime,
    read_email_response_base,
)


def _msg(raw: bytes):
    return email.message_from_bytes(raw)


def _decode(value: str) -> str:
    return value


def test_parse_email_datetime_normalizes_naive_dates_for_list_sorting():
    parsed = parse_email_datetime("01 Jan 2026 12:00:00")

    assert parsed.isoformat() == "2026-01-01T12:00:00+00:00"
    assert parsed.timestamp() == 1767268800.0


def test_fetch_meta_flags_and_size_are_stable_for_gmail_or_dovecot_shapes():
    meta = rb"10779 (UID 18723 RFC822.SIZE 54308 RFC822.HEADER {24} FLAGS (\Seen \Flagged))"

    assert fetch_flags_from_meta(meta) == r"\Seen \Flagged"
    assert fetch_size_from_meta(meta) == 54308


def test_message_has_attachments_uses_header_only_hint():
    msg = _msg(
        b"Subject: File\r\n"
        b"Content-Type: multipart/mixed; boundary=x\r\n"
        b"\r\n"
    )

    assert message_has_attachments(msg) is True


def test_list_email_row_from_header_matches_route_shape():
    msg = _msg(
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Cc: Carol <carol@example.com>\r\n"
        b"Subject: Hello\r\n"
        b"Message-ID: <m1@example.com>\r\n"
        b"Date: Thu, 01 Jan 2026 12:00:00 +0000\r\n"
        b"Content-Type: multipart/related; boundary=x\r\n"
        b"\r\n"
    )

    row = list_email_row_from_header(
        "18723",
        msg,
        flags=r"\Seen \Answered",
        size=99,
        tag_entry={"tags": ["urgent"], "spam": True},
        decode_header=_decode,
    )

    assert row == {
        "uid": "18723",
        "message_id": "<m1@example.com>",
        "subject": "Hello",
        "from_name": "Alice",
        "from_address": "alice@example.com",
        "to": "Bob <bob@example.com>",
        "cc": "Carol <carol@example.com>",
        "date": "2026-01-01T12:00:00+00:00",
        "date_display": "Thu, 01 Jan 2026 12:00:00 +0000",
        "date_epoch": 1767268800.0,
        "is_read": True,
        "is_answered": True,
        "is_flagged": False,
        "flags": r"\Seen \Answered",
        "has_attachments": True,
        "size": 99,
        "tags": ["urgent"],
        "is_spam_verdict": True,
    }


def test_read_email_response_base_preserves_read_route_shape():
    msg = _msg(
        b"From: Writer <writer@example.com>\r\n"
        b"To: Reader <reader@example.com>\r\n"
        b"Subject: Read me\r\n"
        b"Message-ID: <m2@example.com>\r\n"
        b"In-Reply-To: <m1@example.com>\r\n"
        b"References: <m0@example.com> <m1@example.com>\r\n"
        b"Date: 01 Jan 2026 12:00:00\r\n"
        b"\r\n"
    )

    response = read_email_response_base(
        "5",
        "INBOX",
        msg,
        body="plain",
        body_html="<p>plain</p>",
        attachments=[{"filename": "a.pdf"}],
        decode_header=_decode,
    )

    assert response == {
        "uid": "5",
        "folder": "INBOX",
        "message_id": "<m2@example.com>",
        "subject": "Read me",
        "from_name": "Writer",
        "from_address": "writer@example.com",
        "to": "Reader <reader@example.com>",
        "cc": "",
        "date": "2026-01-01T12:00:00",
        "in_reply_to": "<m1@example.com>",
        "references": "<m0@example.com> <m1@example.com>",
        "body": "plain",
        "body_html": "<p>plain</p>",
        "attachments": [{"filename": "a.pdf"}],
    }
