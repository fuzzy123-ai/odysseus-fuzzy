from pathlib import Path

import pytest

from plugins.telegram.outbound import send_telegram_photo


def _post(*_args, **_kwargs):
    return {"ok": True, "result": {"message_id": 42}}


@pytest.mark.parametrize(
    ("filename", "payload", "expected_mime"),
    [
        ("screen.png", b"\x89PNG\r\n\x1a\npayload", "image/png"),
        ("screen.jpg", b"\xff\xd8\xffpayload", "image/jpeg"),
        ("screen.webp", b"RIFFxxxxWEBPpayload", "image/webp"),
    ],
)
def test_send_telegram_photo_accepts_real_image_signatures(tmp_path: Path, filename: str, payload: bytes, expected_mime: str, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    path = tmp_path / filename
    path.write_bytes(payload)
    calls = []

    def post(url, fields, field_name, file_path, *, filename, mime_type):
        calls.append((url, fields, field_name, Path(file_path), filename, mime_type))
        return _post()

    result = send_telegram_photo("123", path, filename=filename, http_post_multipart=post)

    assert result["ok"] is True
    assert calls[0][5] == expected_mime


def test_send_telegram_photo_rejects_empty_image(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    path = tmp_path / "screen.png"
    path.write_bytes(b"")

    with pytest.raises(ValueError, match="integrity failed"):
        send_telegram_photo("123", path, filename="screen.png", http_post_multipart=_post)


def test_send_telegram_photo_rejects_text_with_png_extension(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    path = tmp_path / "screen.png"
    path.write_text("not a png", encoding="utf-8")

    with pytest.raises(ValueError, match="integrity failed"):
        send_telegram_photo("123", path, filename="screen.png", http_post_multipart=_post)
