import email

from routes.email_attachment_helpers import (
    attachment_as_document_response,
    attachment_basename,
)


class DummyRequest:
    pass


def _msg():
    return email.message_from_bytes(
        b"From: Sender <sender@example.com>\r\n"
        b"Message-ID: <m1@example.com>\r\n"
        b"\r\n"
    )


def test_attachment_basename_rejects_hidden_files(tmp_path):
    path = tmp_path / ".secret.txt"
    path.write_text("secret", encoding="utf-8")

    assert attachment_basename(path) == {"error": "Invalid filename", "filename": ".secret.txt"}


def test_attachment_basename_returns_base_extension_and_title(tmp_path):
    path = tmp_path / "invoice.final.pdf"
    path.write_bytes(b"%PDF")

    assert attachment_basename(path) == ("invoice.final.pdf", ".pdf", "invoice.final")


def test_attachment_as_document_response_rejects_unsupported_type_before_db(tmp_path):
    path = tmp_path / "payload.exe"
    path.write_bytes(b"nope")

    result = attachment_as_document_response(
        path,
        _msg(),
        uid="7",
        folder="INBOX",
        account_id="acct",
        request=DummyRequest(),
    )

    assert result == {"error": "Unsupported attachment type: .exe", "filename": "payload.exe"}


def test_attachment_as_document_response_rejects_hidden_file_before_db(tmp_path):
    path = tmp_path / ".payload.txt"
    path.write_text("hidden", encoding="utf-8")

    result = attachment_as_document_response(
        path,
        _msg(),
        uid="7",
        folder="INBOX",
        account_id="acct",
        request=DummyRequest(),
    )

    assert result == {"error": "Invalid filename", "filename": ".payload.txt"}
