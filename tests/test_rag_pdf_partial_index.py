from src.rag_vector import VectorRAG


class _FakePage:
    def __init__(self, text=None, exc=None):
        self.text = text
        self.exc = exc

    def extract_text(self):
        if self.exc:
            raise self.exc
        return self.text


def _rag_with_capture():
    captured = []
    rag = VectorRAG.__new__(VectorRAG)
    rag._split_into_chunks = lambda text: [text]

    def fake_add_document(text, metadata):
        captured.append((text, metadata))
        return True

    rag.add_document = fake_add_document
    return rag, captured


def test_partial_pdf_is_indexed_with_warning_metadata(monkeypatch, tmp_path):
    pdf_path = tmp_path / "packet.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    class FakeReader:
        is_encrypted = False

        def __init__(self, _path):
            self.pages = [
                _FakePage("first readable page"),
                _FakePage(exc=ValueError("broken page")),
                _FakePage("third readable page"),
            ]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)
    rag, captured = _rag_with_capture()

    result = rag.index_personal_documents(str(tmp_path), owner="alice")

    assert result["success"] is True
    assert result["indexed_count"] == 1
    assert result["partial_count"] == 1
    assert result["review_count"] == 0
    assert result["skipped_count"] == 0
    assert "packet.pdf" in result["warnings_by_file"]
    warning_codes = [warning["code"] for warning in result["warnings_by_file"]["packet.pdf"]]
    assert "pdf_page_extract_failed" in warning_codes
    assert "pdf_partial_text" in warning_codes
    text, metadata = captured[0]
    assert "first readable page" in text
    assert "third readable page" in text
    assert metadata["owner"] == "alice"
    assert metadata["pdf_status"] == "partial"
    assert metadata["pdf_page_start"] == 1
    assert metadata["pdf_page_end"] == 3
    assert "pdf_page_extract_failed" in metadata["pdf_warning_codes"]


def test_empty_pdf_is_reported_for_review_without_silent_skip(monkeypatch, tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    rag, captured = _rag_with_capture()

    class FakeReader:
        is_encrypted = False

        def __init__(self, _path):
            self.pages = [_FakePage(""), _FakePage(None)]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    result = rag.index_personal_documents(str(tmp_path), owner="alice")

    assert result["success"] is True
    assert result["indexed_count"] == 0
    assert result["failed_count"] == 0
    assert result["review_count"] == 1
    assert result["skipped_count"] == 1
    assert captured == []
    warning_codes = [warning["code"] for warning in result["warnings_by_file"]["scan.pdf"]]
    assert "pdf_text_empty" in warning_codes
    assert "pdf_ocr_required" in warning_codes


def test_broken_pdf_is_failed_and_reported(monkeypatch, tmp_path):
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    rag, captured = _rag_with_capture()

    class FakeReader:
        def __init__(self, _path):
            raise ValueError("not parseable")

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    result = rag.index_personal_documents(str(tmp_path), owner="alice")

    assert result["success"] is True
    assert result["indexed_count"] == 0
    assert result["failed_count"] == 1
    assert result["skipped_count"] == 0
    assert captured == []
    warning_codes = [warning["code"] for warning in result["warnings_by_file"]["broken.pdf"]]
    assert "pdf_parser_failed" in warning_codes
