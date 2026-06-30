"""Shared, budgeted PDF text extraction primitives.

Runtime callers may use page text from ``PdfExtractionResult.pages``. Serialized
reports intentionally omit extracted raw text so ledgers and diagnostics can
store extraction evidence without storing private document contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Callable, Mapping


PDF_STATUS_COMPLETED = "completed"
PDF_STATUS_PARTIAL = "partial"
PDF_STATUS_METADATA_ONLY = "metadata_only"
PDF_STATUS_NEEDS_REVIEW = "needs_review"
PDF_STATUS_FAILED = "failed"

PDF_WARNING_SIZE_LIMIT_EXCEEDED = "pdf_size_limit_exceeded"
PDF_WARNING_PAGE_LIMIT_EXCEEDED = "pdf_page_limit_exceeded"
PDF_WARNING_CHAR_LIMIT_EXCEEDED = "pdf_char_limit_exceeded"
PDF_WARNING_PAGE_EXTRACT_FAILED = "pdf_page_extract_failed"
PDF_WARNING_TEXT_EMPTY = "pdf_text_empty"
PDF_WARNING_OCR_REQUIRED = "pdf_ocr_required"
PDF_WARNING_OCR_BLOCKED_BY_POLICY = "pdf_ocr_blocked_by_policy"
PDF_WARNING_OCR_BUDGET_EXCEEDED = "pdf_ocr_budget_exceeded"
PDF_WARNING_OCR_FAILED = "pdf_ocr_failed"
PDF_WARNING_PARSER_FAILED = "pdf_parser_failed"
PDF_WARNING_ENCRYPTED = "pdf_encrypted"
PDF_WARNING_PARTIAL_TEXT = "pdf_partial_text"

PDF_STATUSES = frozenset(
    {
        PDF_STATUS_COMPLETED,
        PDF_STATUS_PARTIAL,
        PDF_STATUS_METADATA_ONLY,
        PDF_STATUS_NEEDS_REVIEW,
        PDF_STATUS_FAILED,
    }
)

PDF_WARNING_CODES = frozenset(
    {
        PDF_WARNING_SIZE_LIMIT_EXCEEDED,
        PDF_WARNING_PAGE_LIMIT_EXCEEDED,
        PDF_WARNING_CHAR_LIMIT_EXCEEDED,
        PDF_WARNING_PAGE_EXTRACT_FAILED,
        PDF_WARNING_TEXT_EMPTY,
        PDF_WARNING_OCR_REQUIRED,
        PDF_WARNING_OCR_BLOCKED_BY_POLICY,
        PDF_WARNING_OCR_BUDGET_EXCEEDED,
        PDF_WARNING_OCR_FAILED,
        PDF_WARNING_PARSER_FAILED,
        PDF_WARNING_ENCRYPTED,
        PDF_WARNING_PARTIAL_TEXT,
    }
)


@dataclass(frozen=True)
class PdfExtractionBudget:
    max_file_bytes: int = 25 * 1024 * 1024
    max_pages: int = 500
    max_chars: int = 300_000
    max_chars_per_page: int = 20_000
    max_seconds: float = 30.0
    max_images_per_page: int = 0
    ocr_enabled: bool = False
    ocr_max_pages: int = 0


@dataclass(frozen=True)
class PdfExtractionWarning:
    code: str
    page_number: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code}
        if self.page_number is not None:
            payload["page_number"] = self.page_number
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class PdfPageExtraction:
    page_number: int
    status: str
    text: str = ""
    char_count: int = 0
    warnings: tuple[PdfExtractionWarning, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "status": self.status,
            "char_count": self.char_count,
            "text_available": bool(self.text),
            "warnings": tuple(warning.to_dict() for warning in self.warnings),
        }


@dataclass(frozen=True)
class PdfExtractionResult:
    status: str
    page_count: int = 0
    processed_pages: int = 0
    char_count: int = 0
    warnings: tuple[PdfExtractionWarning, ...] = ()
    pages: tuple[PdfPageExtraction, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text).strip()

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(warning.code for warning in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "page_count": self.page_count,
            "processed_pages": self.processed_pages,
            "char_count": self.char_count,
            "text_available": bool(self.text),
            "warnings": tuple(warning.to_dict() for warning in self.warnings),
            "pages": tuple(page.to_dict() for page in self.pages),
            "metadata": dict(self.metadata),
        }


def extract_pdf_pages(
    path: str | Path,
    budget: PdfExtractionBudget | None = None,
    *,
    owner: str | None = None,
    policy_context: Mapping[str, Any] | None = None,
    ocr_adapter: Callable[[Path, int, Mapping[str, Any]], str] | None = None,
) -> PdfExtractionResult:
    """Extract PDF text page by page with explicit partial/failure status."""

    del owner  # Reserved for later OCR/Vision and policy-aware adapters.
    budget = budget or PdfExtractionBudget()
    source = Path(path)
    metadata = {
        "extractor": "pypdf_page_stream",
        "ocr_enabled": bool(budget.ocr_enabled),
        "ocr_max_pages": budget.ocr_max_pages,
        "max_images_per_page": budget.max_images_per_page,
        "ocr_pages_processed": 0,
    }

    try:
        size = source.stat().st_size
    except OSError as exc:
        return _failed(PDF_WARNING_PARSER_FAILED, f"stat_failed:{type(exc).__name__}", metadata)

    metadata = {**metadata, "size": size}
    if budget.max_file_bytes >= 0 and size > budget.max_file_bytes:
        warning = PdfExtractionWarning(
            PDF_WARNING_SIZE_LIMIT_EXCEEDED,
            detail=f"size>{budget.max_file_bytes}",
        )
        return PdfExtractionResult(
            status=PDF_STATUS_METADATA_ONLY,
            warnings=(warning,),
            metadata={**metadata, "size_limited": True},
        )

    try:
        from pypdf import PdfReader
    except Exception as exc:
        return _failed(PDF_WARNING_PARSER_FAILED, f"pypdf_unavailable:{type(exc).__name__}", metadata)

    try:
        reader = PdfReader(str(source))
    except Exception as exc:
        return _failed(PDF_WARNING_PARSER_FAILED, type(exc).__name__, metadata)

    warnings: list[PdfExtractionWarning] = []
    try:
        encrypted = bool(getattr(reader, "is_encrypted", False))
    except Exception:
        encrypted = False
    if encrypted:
        warnings.append(PdfExtractionWarning(PDF_WARNING_ENCRYPTED))
        try:
            reader.decrypt("")
        except Exception:
            return PdfExtractionResult(
                status=PDF_STATUS_FAILED,
                warnings=tuple(warnings + [PdfExtractionWarning(PDF_WARNING_PARSER_FAILED, detail="encrypted")]),
                metadata=metadata,
            )

    try:
        pages_obj = reader.pages
        page_count = len(pages_obj)
    except Exception as exc:
        return _failed(PDF_WARNING_PARSER_FAILED, type(exc).__name__, metadata, warnings=tuple(warnings))

    pages: list[PdfPageExtraction] = []
    start = time.monotonic()
    max_pages = max(0, budget.max_pages)
    pages_to_process = min(page_count, max_pages)
    if page_count > pages_to_process:
        warnings.append(PdfExtractionWarning(PDF_WARNING_PAGE_LIMIT_EXCEEDED, detail=f"pages>{max_pages}"))

    chars_remaining = max(0, budget.max_chars)
    stopped_for_budget = False
    ocr_pages_processed = 0
    for index in range(pages_to_process):
        if budget.max_seconds >= 0 and (time.monotonic() - start) > budget.max_seconds:
            warnings.append(PdfExtractionWarning(PDF_WARNING_PARTIAL_TEXT, detail="max_seconds_exceeded"))
            stopped_for_budget = True
            break
        if chars_remaining <= 0:
            warnings.append(PdfExtractionWarning(PDF_WARNING_CHAR_LIMIT_EXCEEDED, detail=f"chars>{budget.max_chars}"))
            stopped_for_budget = True
            break

        page_number = index + 1
        page_warnings: list[PdfExtractionWarning] = []
        try:
            raw_text = pages_obj[index].extract_text() or ""
        except Exception as exc:
            warning = PdfExtractionWarning(
                PDF_WARNING_PAGE_EXTRACT_FAILED,
                page_number=page_number,
                detail=type(exc).__name__,
            )
            warnings.append(warning)
            pages.append(PdfPageExtraction(page_number=page_number, status=PDF_STATUS_FAILED, warnings=(warning,)))
            continue

        text = _normalize_pdf_text(raw_text)
        if not text:
            ocr_text, ocr_warnings, ocr_used = _try_ocr_page(
                source,
                page_number,
                budget=budget,
                policy_context=policy_context,
                ocr_adapter=ocr_adapter,
                ocr_pages_processed=ocr_pages_processed,
            )
            if ocr_used:
                ocr_pages_processed += 1
            if ocr_warnings:
                warnings.extend(ocr_warnings)
                page_warnings.extend(ocr_warnings)
            if ocr_text:
                text = _normalize_pdf_text(ocr_text)
        if text and len(text) > budget.max_chars_per_page:
            text = text[: budget.max_chars_per_page].rstrip()
            warning = PdfExtractionWarning(
                PDF_WARNING_CHAR_LIMIT_EXCEEDED,
                page_number=page_number,
                detail=f"page_chars>{budget.max_chars_per_page}",
            )
            warnings.append(warning)
            page_warnings.append(warning)

        if text and len(text) > chars_remaining:
            text = text[:chars_remaining].rstrip()
            warning = PdfExtractionWarning(
                PDF_WARNING_CHAR_LIMIT_EXCEEDED,
                page_number=page_number,
                detail=f"chars>{budget.max_chars}",
            )
            warnings.append(warning)
            page_warnings.append(warning)
            stopped_for_budget = True

        pages.append(
            PdfPageExtraction(
                page_number=page_number,
                status=PDF_STATUS_COMPLETED if text else "empty",
                text=text,
                char_count=len(text),
                warnings=tuple(page_warnings),
            )
        )
        chars_remaining -= len(text)
        if stopped_for_budget:
            break

    char_count = sum(page.char_count for page in pages)
    if char_count > 0:
        status = PDF_STATUS_PARTIAL if warnings or len(pages) < page_count else PDF_STATUS_COMPLETED
        if status == PDF_STATUS_PARTIAL and PDF_WARNING_PARTIAL_TEXT not in [warning.code for warning in warnings]:
            warnings.append(PdfExtractionWarning(PDF_WARNING_PARTIAL_TEXT))
    else:
        has_only_empty_pages = bool(pages) and all(page.status == "empty" for page in pages)
        if has_only_empty_pages or page_count > 0:
            warnings.extend(_empty_text_warnings(budget, policy_context))
            status = PDF_STATUS_NEEDS_REVIEW
        else:
            warnings.append(PdfExtractionWarning(PDF_WARNING_TEXT_EMPTY))
            status = PDF_STATUS_FAILED

    return PdfExtractionResult(
        status=status,
        page_count=page_count,
        processed_pages=len(pages),
        char_count=char_count,
        warnings=tuple(_dedupe_warnings(warnings)),
        pages=tuple(pages),
        metadata={**metadata, "ocr_pages_processed": ocr_pages_processed},
    )


def extract_pdf_text(
    path: str | Path,
    budget: PdfExtractionBudget | None = None,
    *,
    owner: str | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> str:
    """Compatibility helper returning only extracted runtime text."""

    return extract_pdf_pages(path, budget, owner=owner, policy_context=policy_context).text


def _try_ocr_page(
    source: Path,
    page_number: int,
    *,
    budget: PdfExtractionBudget,
    policy_context: Mapping[str, Any] | None,
    ocr_adapter: Callable[[Path, int, Mapping[str, Any]], str] | None,
    ocr_pages_processed: int,
) -> tuple[str, list[PdfExtractionWarning], bool]:
    if not budget.ocr_enabled:
        return "", [], False
    if policy_context and policy_context.get("local_only") and policy_context.get("external_ocr_requested"):
        return "", [PdfExtractionWarning(PDF_WARNING_OCR_BLOCKED_BY_POLICY, page_number=page_number)], False
    if ocr_pages_processed >= max(0, budget.ocr_max_pages):
        return "", [PdfExtractionWarning(PDF_WARNING_OCR_BUDGET_EXCEEDED, page_number=page_number)], False
    if ocr_adapter is None:
        return "", [PdfExtractionWarning(PDF_WARNING_OCR_REQUIRED, page_number=page_number)], False
    try:
        text = ocr_adapter(
            source,
            page_number,
            {
                "local_only": bool(policy_context.get("local_only")) if policy_context else False,
                "max_images_per_page": budget.max_images_per_page,
            },
        )
    except Exception as exc:
        return "", [PdfExtractionWarning(PDF_WARNING_OCR_FAILED, page_number=page_number, detail=type(exc).__name__)], True
    return str(text or ""), [], True


def _failed(
    code: str,
    detail: str,
    metadata: Mapping[str, Any],
    *,
    warnings: tuple[PdfExtractionWarning, ...] = (),
) -> PdfExtractionResult:
    return PdfExtractionResult(
        status=PDF_STATUS_FAILED,
        warnings=tuple(warnings + (PdfExtractionWarning(code, detail=detail),)),
        metadata=metadata,
    )


def _empty_text_warnings(
    budget: PdfExtractionBudget,
    policy_context: Mapping[str, Any] | None,
) -> list[PdfExtractionWarning]:
    warnings = [PdfExtractionWarning(PDF_WARNING_TEXT_EMPTY)]
    if budget.ocr_enabled:
        if budget.ocr_max_pages <= 0:
            warnings.append(PdfExtractionWarning(PDF_WARNING_OCR_BUDGET_EXCEEDED))
    else:
        warnings.append(PdfExtractionWarning(PDF_WARNING_OCR_REQUIRED))
    if policy_context and policy_context.get("local_only") and policy_context.get("external_ocr_requested"):
        warnings.append(PdfExtractionWarning(PDF_WARNING_OCR_BLOCKED_BY_POLICY))
    return warnings


def _normalize_pdf_text(value: str) -> str:
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = [line.rstrip() for line in lines]
    return "\n".join(normalized).strip()


def _dedupe_warnings(warnings: list[PdfExtractionWarning]) -> list[PdfExtractionWarning]:
    seen: set[tuple[str, int | None, str]] = set()
    result: list[PdfExtractionWarning] = []
    for warning in warnings:
        key = (warning.code, warning.page_number, warning.detail)
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result
