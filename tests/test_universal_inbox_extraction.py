import json
import zipfile

import pytest

from src.universal_inbox_extraction import (
    UniversalInboxExtractionError,
    extract_universal_inbox_content,
)


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


def test_pdf_is_metadata_only_with_structured_warning(tmp_path):
    source = tmp_path / "file.pdf"
    source.write_bytes(b"%PDF-1.4\nprivate bytes")

    packet = extract_universal_inbox_content(source, relative_path="file.pdf")

    assert packet.status == "metadata_only"
    assert packet.raw_text == ""
    assert packet.warnings[0].code == "pdf_metadata_only"
    assert packet.to_dict()["metadata"]["extractor"] == "pdf_metadata_only"


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
