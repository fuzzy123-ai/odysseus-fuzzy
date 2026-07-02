import json
import zipfile

import pytest
from pypdf import PdfWriter

from src.universal_inbox_extraction import (
    UniversalInboxExtractionError,
    extract_universal_inbox_content,
)
from src.universal_inbox_ocr import UniversalInboxOcrSettings
from src.universal_export_executor import _write_simple_text_pdf


def test_text_extraction_packet_is_ephemeral_and_report_excludes_raw_text(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("# Private Note\nDo not persist this body.", encoding="utf-8")

    packet = extract_universal_inbox_content(source, relative_path="note.md")

    assert packet.status == "completed"
    assert packet.ephemeral is True
    assert packet.raw_text == "# Private Note\nDo not persist this body."
    report = packet.to_dict()
    encoded = json.dumps(report, sort_keys=True)
    assert report["persisted"] is False
    assert report["text_available"] is True
    assert "Do not persist this body" not in encoded
    assert "raw_text" not in encoded


@pytest.mark.parametrize(
    ("filename", "body", "expected"),
    [
        ("data.json", '{"b": 2, "a": 1}', '"a": 1'),
        ("rows.csv", "name,value\nalpha,1\n", "alpha,1"),
        ("rows.tsv", "name\tvalue\nalpha\t1\n", "alpha\t1"),
        ("page.html", "<html><script>hidden()</script><body><h1>Visible</h1></body></html>", "Visible"),
        ("feed.xml", "<root><title>Visible XML</title></root>", "Visible XML"),
    ],
)
def test_supported_text_like_formats_extract_runtime_text(tmp_path, filename, body, expected):
    source = tmp_path / filename
    source.write_text(body, encoding="utf-8")

    packet = extract_universal_inbox_content(source, relative_path=filename)

    assert packet.status == "completed"
    assert expected in packet.raw_text
    assert expected not in json.dumps(packet.to_dict(), sort_keys=True)


def test_docx_best_effort_extracts_document_xml_text(tmp_path):
    source = tmp_path / "sample.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    packet = extract_universal_inbox_content(source, relative_path="sample.docx")

    assert packet.status == "completed"
    assert packet.raw_text == "First paragraph\nSecond paragraph"
    assert "First paragraph" not in json.dumps(packet.to_dict(), sort_keys=True)


def test_pdf_extracts_runtime_text_with_pypdf_adapter(tmp_path):
    source = tmp_path / "file.pdf"
    _write_simple_text_pdf("Invoice text from PDF", source)

    packet = extract_universal_inbox_content(source, relative_path="file.pdf")

    assert packet.status == "completed"
    assert packet.raw_text == "Invoice text from PDF"
    assert packet.warnings == ()
    assert packet.to_dict()["metadata"]["extractor"] == "pypdf_page_stream"
    assert packet.to_dict()["metadata"]["pdf_status"] == "completed"
    assert "Invoice text from PDF" not in json.dumps(packet.to_dict(), sort_keys=True)


def test_pdf_without_extractable_text_needs_review(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with source.open("wb") as handle:
        writer.write(handle)

    packet = extract_universal_inbox_content(source, relative_path="scan.pdf")

    assert packet.status == "needs_review"
    assert packet.raw_text == ""
    assert [warning.code for warning in packet.warnings] == ["pdf_text_empty", "pdf_ocr_required"]
    assert packet.to_dict()["metadata"]["pdf_status"] == "needs_review"


def test_pdf_without_extractable_text_uses_configured_local_ocr_adapter(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with source.open("wb") as handle:
        writer.write(handle)
    calls = []

    def fake_ocr(path, page_number, context):
        calls.append((path, page_number, context["local_only"]))
        return "OCR text from local adapter"

    packet = extract_universal_inbox_content(
        source,
        relative_path="scan.pdf",
        ocr_adapter=fake_ocr,
        ocr_settings=UniversalInboxOcrSettings(enabled=True, max_pdf_pages=1),
    )

    assert packet.status == "completed"
    assert packet.raw_text == "OCR text from local adapter"
    assert packet.warnings == ()
    assert packet.to_dict()["metadata"]["ocr_pages_processed"] == 1
    assert calls == [(source, 1, True)]
    assert "OCR text from local adapter" not in json.dumps(packet.to_dict(), sort_keys=True)


def test_image_without_ocr_adapter_needs_review(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"\xff\xd8\xff bytes")

    packet = extract_universal_inbox_content(source, relative_path="photo.jpg")

    assert packet.status == "needs_review"
    assert packet.raw_text == ""
    assert packet.warnings[0].code == "image_ocr_required"
    assert packet.to_dict()["metadata"]["ocr_enabled"] is False


def test_image_uses_configured_local_ocr_adapter_without_persisting_text(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"\xff\xd8\xff bytes")
    calls = []

    def fake_ocr(path, page_number, context):
        calls.append((path, page_number, context["local_only"], context["source"]))
        return "Text aus dem Foto"

    packet = extract_universal_inbox_content(
        source,
        relative_path="photo.jpg",
        ocr_adapter=fake_ocr,
        ocr_settings=UniversalInboxOcrSettings(enabled=True),
    )

    assert packet.status == "completed"
    assert packet.raw_text == "Text aus dem Foto"
    assert packet.warnings == ()
    assert packet.to_dict()["metadata"]["ocr_text_available"] is True
    assert calls == [(source, None, True, "universal_inbox_image")]
    assert "Text aus dem Foto" not in json.dumps(packet.to_dict(), sort_keys=True)


def test_partial_pdf_maps_shared_warning_metadata(tmp_path, monkeypatch):
    source = tmp_path / "partial.pdf"
    source.write_bytes(b"%PDF-1.4 fake")

    class FakePage:
        def __init__(self, text=None, exc=None):
            self.text = text
            self.exc = exc

        def extract_text(self):
            if self.exc:
                raise self.exc
            return self.text

    class FakeReader:
        is_encrypted = False

        def __init__(self, _path):
            self.pages = [FakePage("first"), FakePage(exc=ValueError("bad")), FakePage("third")]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    packet = extract_universal_inbox_content(source, relative_path="partial.pdf")

    assert packet.status == "partial"
    assert "first" in packet.raw_text
    assert "third" in packet.raw_text
    assert [warning.code for warning in packet.warnings] == ["pdf_page_extract_failed", "pdf_partial_text"]
    assert packet.warnings[0].detail == "page=2:ValueError"
    assert packet.to_dict()["metadata"]["pdf_warning_codes"] == (
        "pdf_page_extract_failed",
        "pdf_partial_text",
    )
    assert "first" not in json.dumps(packet.to_dict(), sort_keys=True)


def test_failed_pdf_maps_parser_status(tmp_path, monkeypatch):
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-1.4 fake")

    class FakeReader:
        def __init__(self, _path):
            raise ValueError("not parseable")

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    packet = extract_universal_inbox_content(source, relative_path="broken.pdf")

    assert packet.status == "failed"
    assert packet.raw_text == ""
    assert [warning.code for warning in packet.warnings] == ["pdf_parser_failed"]


def test_size_limit_returns_metadata_only_without_raw_text(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("abcdef", encoding="utf-8")

    packet = extract_universal_inbox_content(
        source,
        relative_path="large.txt",
        max_extract_bytes=3,
    )

    assert packet.status == "metadata_only"
    assert packet.raw_text == ""
    assert packet.warnings[0].to_dict() == {
        "code": "size_limit_exceeded",
        "relative_path": "large.txt",
        "detail": "size>3",
    }


def test_extraction_rejects_absolute_serialized_relative_path(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")

    with pytest.raises(UniversalInboxExtractionError):
        extract_universal_inbox_content(source, relative_path=str(source))


@pytest.mark.parametrize(
    ("filename", "body", "status", "warning"),
    [
        ("photo.jpg", b"\xff\xd8\xff bytes", "needs_review", "image_ocr_required"),
        ("voice.ogg", b"OggS bytes", "metadata_only", "audio_transcription_required"),
        ("bundle.zip", b"PK\x03\x04 bytes", "metadata_only", "archive_needs_review"),
        ("mail.eml", b"Subject: hi\n\nbody", "metadata_only", "structured_message_needs_parser"),
        ("sheet.xlsx", b"PK\x03\x04 bytes", "metadata_only", "document_extractor_pending"),
        ("payload.unknown", b"opaque", "unsupported", "unsupported_type"),
        ("setup.exe", b"MZ bytes", "blocked", "dangerous_type_blocked"),
    ],
)
def test_non_text_types_get_structured_review_decisions(tmp_path, filename, body, status, warning):
    source = tmp_path / filename
    source.write_bytes(body)

    packet = extract_universal_inbox_content(source, relative_path=filename)

    assert packet.status == status
    assert packet.raw_text == ""
    assert packet.warnings[0].code == warning
    assert packet.to_dict()["metadata"]["file_type"]["review_required"] is True
