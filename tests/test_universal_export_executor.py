from pathlib import Path

from src.universal_export_executor import execute_universal_export


def test_executes_markdown_to_pdf_without_persisting_raw_content(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("# Project Alpha\n\nBitte als PDF exportieren.", encoding="utf-8")

    result = execute_universal_export(source, "pdf", tmp_path / "exports")
    payload = result.to_dict()

    assert result.ok is True
    assert result.delivery_ready is True
    assert Path(result.output_path).read_bytes().startswith(b"%PDF-")
    assert result.output_filename == "export.pdf"
    assert payload["status"] == "exported"
    assert payload["required_tool"] == "builtin_text_pdf"
    assert payload["raw_content_visible"] is False
    assert payload["host_paths_visible"] is False
    assert payload["filename_visible"] is False
    assert "Project Alpha" not in str(payload)
    assert str(source) not in str(payload)


def test_export_executor_does_not_overwrite_existing_pdf(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    out = tmp_path / "exports"
    out.mkdir()
    (out / "export.pdf").write_bytes(b"old")

    result = execute_universal_export(source, "pdf", out)

    assert result.ok is True
    assert result.output_filename == "export-1.pdf"
    assert (out / "export.pdf").read_bytes() == b"old"


def test_executor_blocks_docx_to_pdf_until_external_converter_exists(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"not a real docx")

    result = execute_universal_export(source, "pdf", tmp_path / "exports")

    assert result.ok is False
    assert result.status == "blocked"
    assert result.required_tool == "libreoffice_or_pandoc"
    assert result.delivery_ready is False


def test_executor_blocks_symlink_source(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(source)
    except (OSError, NotImplementedError):
        return

    result = execute_universal_export(link, "pdf", tmp_path / "exports")

    assert result.status == "blocked"
    assert result.reason == "source_symlink_blocked"
