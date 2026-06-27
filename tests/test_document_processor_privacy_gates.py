from types import SimpleNamespace

import src.document_processor as dp


class _UploadHandler:
    def __init__(self, uploads):
        self.uploads = uploads

    def resolve_upload(self, fid, owner=None):
        return self.uploads.get(fid)

    def _inside_upload_dir(self, path):
        return True

    def is_image_file(self, display_name, mime):
        return mime.startswith("image/")

    def is_audio_file(self, display_name, mime):
        return mime.startswith("audio/")

    def is_document_file(self, display_name, mime):
        return True


def test_dsgvo_blocks_raw_attachment_content_for_external_session(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    secret_doc = tmp_path / "note.txt"
    secret_doc.write_text("private attachment body", encoding="utf-8")

    content = dp.build_user_content(
        "please inspect",
        ["doc-1"],
        str(tmp_path),
        _UploadHandler({"doc-1": {"path": str(secret_doc), "mime": "text/plain", "name": "note.txt"}}),
        owner="alice",
        session_endpoint_url="https://api.openai.com/v1/chat/completions",
        session_model="gpt-4o",
    )

    assert "Attachments omitted by DSGVO" in content
    assert "private attachment body" not in content
    assert "note.txt" not in content


def test_dsgvo_allows_raw_attachment_content_for_local_session(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    doc = tmp_path / "note.txt"
    doc.write_text("local attachment body", encoding="utf-8")

    content = dp.build_user_content(
        "please inspect",
        ["doc-1"],
        str(tmp_path),
        _UploadHandler({"doc-1": {"path": str(doc), "mime": "text/plain", "name": "note.txt"}}),
        owner="alice",
        session_endpoint_url="http://localhost:11434/v1/chat/completions",
        session_model="local-chat",
    )

    assert "local attachment body" in content
    assert "Attachments omitted by DSGVO" not in content


def test_dsgvo_blocks_external_vision_before_image_bytes_are_read(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    image = tmp_path / "image.png"
    image.write_bytes(b"image-bytes")

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "gpt-4o"})
    monkeypatch.setattr(
        dp,
        "_resolve_vl_model",
        lambda configured, owner=None: ("https://api.openai.com/v1/chat/completions", "gpt-4o", {}),
    )
    monkeypatch.setattr(
        dp,
        "llm_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("external vision must not be called")),
    )

    result = dp.analyze_image_with_vl_result(str(image), owner="alice")

    assert result["blocked_by_policy"] is True
    assert "Vision analysis blocked by DSGVO" in result["text"]


def test_dsgvo_allows_local_vision_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    image = tmp_path / "image.png"
    image.write_bytes(b"image-bytes")
    seen = {}

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "llava"})
    monkeypatch.setattr(
        dp,
        "_resolve_vl_model",
        lambda configured, owner=None: ("http://localhost:11434/v1/chat/completions", "llava", {"X-Test": "1"}),
    )

    def fake_llm_call(url, model, messages, headers=None, timeout=None):
        seen["call"] = SimpleNamespace(url=url, model=model, headers=headers, timeout=timeout, messages=messages)
        return "local description"

    monkeypatch.setattr(dp, "llm_call", fake_llm_call)

    result = dp.analyze_image_with_vl_result(str(image), owner="alice")

    assert result == {"text": "local description", "model": "llava"}
    assert seen["call"].url.startswith("http://localhost")
    assert seen["call"].headers == {"X-Test": "1"}


def test_attachment_policy_reports_external_block_in_dsgvo(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")

    decision = dp.attachment_content_allowed_for_model(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
    )

    assert decision.allowed is False
    assert decision.local_only_required is True
