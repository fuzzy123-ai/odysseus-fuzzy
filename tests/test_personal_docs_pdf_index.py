from pypdf import PdfWriter

from src import personal_docs
from src.universal_export_executor import _write_simple_text_pdf


def test_personal_index_includes_pdf_uploads(tmp_path):
    pdf_path = tmp_path / "notes.pdf"
    _write_simple_text_pdf("readable pdf text", pdf_path)

    files = personal_docs.load_personal_index(str(tmp_path))

    assert [item["name"] for item in files] == ["notes.pdf"]
    assert files[0]["path"] == str(pdf_path)
    assert files[0]["chunks"] == ["readable pdf text"]
    assert files[0]["pdf_status"] == "completed"
    assert files[0]["pdf_warning_codes"] == []


def test_personal_index_reports_pdf_review_status(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    files = personal_docs.load_personal_index(str(tmp_path))

    assert [item["name"] for item in files] == ["scan.pdf"]
    assert files[0]["chunks"] == []
    assert files[0]["pdf_status"] == "needs_review"
    assert "pdf_text_empty" in files[0]["pdf_warning_codes"]


def test_personal_index_default_extensions_advertise_pdf_support():
    assert ".pdf" in personal_docs.config.DEFAULT_EXTENSIONS
