"""Helpers for document library display metadata."""

from typing import Any, Iterable


def _aggregate_language_facets(lang_rows: Iterable[tuple[Any, int]]) -> dict[str, int]:
    """Sum document counts per display language for the library facet.

    NULL-language and explicit "text" rows share the "text" bucket (the
    language filter treats them as one), so they must be ADDED. A plain dict
    comprehension would key both to "text" and undercount the facet versus
    what the filter actually returns.
    """
    out: dict[str, int] = {}
    for lang, cnt in lang_rows:
        key = lang or "text"
        out[key] = out.get(key, 0) + cnt
    return out


def _library_language_for_document(doc: Any) -> str:
    """Return the display language used by the document library.

    PDF documents are stored as markdown wrappers so the editor can preserve
    extracted text, form fields, and annotations. The library should still
    identify them as PDFs instead of exposing that internal wrapper format.
    """
    from src.pdf_form_doc import find_source_upload_id

    if find_source_upload_id(getattr(doc, "current_content", None) or ""):
        return "pdf"
    return getattr(doc, "language", None) or "text"
