import json

from pypdf import PdfWriter

from src.pdf_extraction import (
    PDF_STATUS_COMPLETED,
    PDF_STATUS_FAILED,
    PDF_STATUS_METADATA_ONLY,
    PDF_STATUS_NEEDS_REVIEW,
    PDF_STATUS_PARTIAL,
    PDF_WARNING_CHAR_LIMIT_EXCEEDED,
    PDF_WARNING_OCR_REQUIRED,
    PDF_WARNING_PAGE_EXTRACT_FAILED,
    PDF_WARNING_PAGE_LIMIT_EXCEEDED,
    PDF_WARNING_PARSER_FAILED,
    PDF_WARNING_SIZE_LIMIT_EXCEEDED,
    PDF_WARNING_TEXT_EMPTY,
    PDF_STATUSES,
    PDF_WARNING_CODES,
    PdfExtractionBudget,
    extract_pdf_pages,
)
from src.universal_export_executor import _write_simple_text_pdf


def test_status_and_warning_contracts_are_stable():
    assert {
        "completed",
        "partial",
        "metadata_only",
        "needs_review",
        "failed",
    }.issubset(PDF_STATUSES)
    assert {
        "pdf_size_limit_exceeded",
        "pdf_page_limit_exceeded",
        "pdf_char_limit_exceeded",
        "pdf_page_extract_failed",
        "pdf_text_empty",
        "pdf_ocr_required",
        "pdf_parser_failed",
    }.issubset(PDF_WARNING_CODES)


def test_extracts_normal_text_pdf_and_report_excludes_raw_text(tmp_path):
    pdf_path = tmp_path / "notes.pdf"
    _write_simple_text_pdf("Quarterly invoice notes", pdf_path)

    result = extract_pdf_pages(pdf_path)

    assert result.status == PDF_STATUS_COMPLETED
    assert "Quarterly invoice notes" in result.text
    report = result.to_dict()
    encoded = json.dumps(report, sort_keys=True)
    assert report["text_available"] is True
    assert "Quarterly invoice notes" not in encoded
    assert "text" not in report["pages"][0]


def test_blank_pdf_needs_review_instead_of_empty_success(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    result = extract_pdf_pages(pdf_path)

    assert result.status == PDF_STATUS_NEEDS_REVIEW
    assert result.text == ""
    assert PDF_WARNING_TEXT_EMPTY in result.warning_codes
    assert PDF_WARNING_OCR_REQUIRED in result.warning_codes


def test_invalid_pdf_returns_failed_parser_status(tmp_path):
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 not really a pdf")

    result = extract_pdf_pages(pdf_path)

    assert result.status == PDF_STATUS_FAILED
    assert result.text == ""
    assert PDF_WARNING_PARSER_FAILED in result.warning_codes


def test_file_size_budget_returns_metadata_only_without_parsing(tmp_path):
    pdf_path = tmp_path / "huge.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nprivate bytes")

    result = extract_pdf_pages(pdf_path, PdfExtractionBudget(max_file_bytes=3))

    assert result.status == PDF_STATUS_METADATA_ONLY
    assert result.text == ""
    assert result.warning_codes == (PDF_WARNING_SIZE_LIMIT_EXCEEDED,)
    assert result.to_dict()["metadata"]["size_limited"] is True


def test_page_exception_keeps_prior_and_later_page_text(monkeypatch, tmp_path):
    pdf_path = tmp_path / "partial.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

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
            self.pages = [
                FakePage("first page"),
                FakePage(exc=ValueError("bad page")),
                FakePage("third page"),
            ]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    result = extract_pdf_pages(pdf_path)

    assert result.status == PDF_STATUS_PARTIAL
    assert "first page" in result.text
    assert "third page" in result.text
    assert PDF_WARNING_PAGE_EXTRACT_FAILED in result.warning_codes
    assert result.pages[1].status == PDF_STATUS_FAILED


def test_page_and_char_budgets_are_deterministic(monkeypatch, tmp_path):
    pdf_path = tmp_path / "budget.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    class FakePage:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class FakeReader:
        is_encrypted = False

        def __init__(self, _path):
            self.pages = [
                FakePage("a" * 20),
                FakePage("second page should not be processed"),
            ]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    result = extract_pdf_pages(
        pdf_path,
        PdfExtractionBudget(max_pages=1, max_chars=8, max_chars_per_page=10),
    )

    assert result.status == PDF_STATUS_PARTIAL
    assert result.processed_pages == 1
    assert result.text == "a" * 8
    assert PDF_WARNING_PAGE_LIMIT_EXCEEDED in result.warning_codes
    assert PDF_WARNING_CHAR_LIMIT_EXCEEDED in result.warning_codes
