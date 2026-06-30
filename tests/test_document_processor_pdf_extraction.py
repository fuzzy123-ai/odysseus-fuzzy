from src.document_processor import _process_pdf, strip_pdf_content_marker
from src.universal_export_executor import _write_simple_text_pdf


class _FakePage:
    def __init__(self, text=None, exc=None):
        self.text = text
        self.exc = exc

    def extract_text(self):
        if self.exc:
            raise self.exc
        return self.text


def test_process_pdf_preserves_content_and_page_markers(tmp_path):
    pdf_path = tmp_path / "notes.pdf"
    _write_simple_text_pdf("Meeting notes for the board", pdf_path)

    processed = _process_pdf(str(pdf_path), owner="alice")

    assert processed.startswith("\n\n[PDF content]:")
    body = strip_pdf_content_marker(processed)
    assert "[Page 1 text]:" in body
    assert "Meeting notes for the board" in body


def test_process_pdf_keeps_partial_page_text_and_warning(monkeypatch, tmp_path):
    pdf_path = tmp_path / "partial.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    class FakeReader:
        is_encrypted = False

        def __init__(self, _path):
            self.pages = [
                _FakePage("first page"),
                _FakePage(exc=ValueError("bad page")),
                _FakePage("third page"),
            ]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    processed = _process_pdf(str(pdf_path), owner="alice")

    assert "[Page 1 text]:" in processed
    assert "first page" in processed
    assert "[Page 2 warning]: pdf_page_extract_failed" in processed
    assert "[Page 3 text]:" in processed
    assert "third page" in processed


def test_process_pdf_maps_parser_failure(monkeypatch, tmp_path):
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    class FakeReader:
        def __init__(self, _path):
            raise ValueError("not parseable")

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    processed = _process_pdf(str(pdf_path), owner="alice")

    assert processed.startswith("\n\n[PDF processing failed:")
    assert "ValueError" in processed


def test_process_pdf_keeps_existing_inline_truncation_marker(monkeypatch, tmp_path):
    pdf_path = tmp_path / "long.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    class FakeReader:
        is_encrypted = False

        def __init__(self, _path):
            self.pages = [_FakePage("a" * 20_000)]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    processed = _process_pdf(str(pdf_path), owner="alice")

    assert processed.startswith("\n\n[PDF content]:")
    assert processed.endswith("[PDF content truncated]")
    assert len(processed) < 16_000
