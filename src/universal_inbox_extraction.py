"""Local content extraction MVP for Universal Inbox dry runs.

Extraction packets may hold raw text at runtime, but serialized reports contain
metadata and structured warnings only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree
import zipfile


EXTRACTION_SCHEMA = "odysseus.universal_inbox.local_extraction_packet.v1"
DEFAULT_MAX_EXTRACT_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
STRUCTURED_TEXT_SUFFIXES = {".json", ".csv", ".tsv", ".html", ".htm"}
DOCX_SUFFIX = ".docx"
PDF_SUFFIX = ".pdf"


class UniversalInboxExtractionError(ValueError):
    """Raised when a local extraction request is invalid or unsafe."""


@dataclass(frozen=True)
class UniversalInboxExtractionWarning:
    code: str
    relative_path: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "relative_path": self.relative_path}
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class UniversalInboxExtractionPacket:
    relative_path: str
    suffix: str
    status: str
    raw_text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[UniversalInboxExtractionWarning, ...] = ()
    schema: str = EXTRACTION_SCHEMA
    ephemeral: bool = True

    @property
    def text_available(self) -> bool:
        return bool(self.raw_text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "relative_path": self.relative_path,
            "suffix": self.suffix,
            "status": self.status,
            "ephemeral": True,
            "persisted": False,
            "text_available": self.text_available,
            "metadata": dict(self.metadata),
            "warnings": tuple(warning.to_dict() for warning in self.warnings),
        }


def extract_universal_inbox_content(
    source: str | Path | Mapping[str, Any],
    *,
    root: str | Path | None = None,
    relative_path: str | None = None,
    max_extract_bytes: int = DEFAULT_MAX_EXTRACT_BYTES,
) -> UniversalInboxExtractionPacket:
    """Extract supported local content types without persisting raw text."""

    path, normalized_relative_path = _resolve_source(source, root=root, relative_path=relative_path)
    if max_extract_bytes < 0:
        raise UniversalInboxExtractionError("max_extract_bytes must be non-negative")
    if not path.exists() or not path.is_file():
        raise UniversalInboxExtractionError("source must be an existing file")
    if path.is_symlink():
        raise UniversalInboxExtractionError("source must not be a symlink")

    suffix = path.suffix.lower()
    size = path.stat().st_size
    base_metadata = {
        "filename": path.name,
        "size": size,
        "char_count": 0,
        "line_count": 0,
        "extractor": _extractor_name(suffix),
    }
    warnings: list[UniversalInboxExtractionWarning] = []
    if size > max_extract_bytes:
        warnings.append(
            UniversalInboxExtractionWarning(
                "size_limit_exceeded",
                normalized_relative_path,
                f"size>{max_extract_bytes}",
            )
        )
        return UniversalInboxExtractionPacket(
            relative_path=normalized_relative_path,
            suffix=suffix,
            status="metadata_only",
            metadata={**base_metadata, "size_limited": True},
            warnings=tuple(warnings),
        )

    if suffix in TEXT_SUFFIXES:
        raw_text, warning = _read_text(path, normalized_relative_path)
    elif suffix == ".json":
        raw_text, warning = _read_json(path, normalized_relative_path)
    elif suffix in {".csv", ".tsv"}:
        raw_text, warning = _read_delimited(path, normalized_relative_path, delimiter="\t" if suffix == ".tsv" else ",")
    elif suffix in {".html", ".htm"}:
        raw_text, warning = _read_html(path, normalized_relative_path)
    elif suffix == DOCX_SUFFIX:
        raw_text, warning = _read_docx(path, normalized_relative_path)
    elif suffix == PDF_SUFFIX:
        warnings.append(
            UniversalInboxExtractionWarning("pdf_metadata_only", normalized_relative_path)
        )
        return UniversalInboxExtractionPacket(
            relative_path=normalized_relative_path,
            suffix=suffix,
            status="metadata_only",
            metadata=base_metadata,
            warnings=tuple(warnings),
        )
    else:
        warnings.append(
            UniversalInboxExtractionWarning("unsupported_type", normalized_relative_path)
        )
        return UniversalInboxExtractionPacket(
            relative_path=normalized_relative_path,
            suffix=suffix,
            status="unsupported",
            metadata=base_metadata,
            warnings=tuple(warnings),
        )

    if warning is not None:
        warnings.append(warning)
    status = "completed" if raw_text else "partial"
    if warning is not None and warning.code.endswith("_failed"):
        status = "failed"
    return UniversalInboxExtractionPacket(
        relative_path=normalized_relative_path,
        suffix=suffix,
        status=status,
        raw_text=raw_text,
        metadata={
            **base_metadata,
            "char_count": len(raw_text),
            "line_count": len(raw_text.splitlines()),
        },
        warnings=tuple(warnings),
    )


def build_universal_inbox_extraction_packet(
    source: str | Path | Mapping[str, Any],
    *,
    root: str | Path | None = None,
    relative_path: str | None = None,
    max_extract_bytes: int = DEFAULT_MAX_EXTRACT_BYTES,
) -> UniversalInboxExtractionPacket:
    """Compatibility wrapper for pipeline callers."""

    return extract_universal_inbox_content(
        source,
        root=root,
        relative_path=relative_path,
        max_extract_bytes=max_extract_bytes,
    )


def _resolve_source(
    source: str | Path | Mapping[str, Any],
    *,
    root: str | Path | None,
    relative_path: str | None,
) -> tuple[Path, str]:
    if isinstance(source, Mapping):
        if root is None:
            raise UniversalInboxExtractionError("root is required for discovery metadata input")
        rel = _normalize_relative_path(relative_path or source.get("relative_path") or "")
        return Path(root) / rel, rel

    path = Path(source)
    if relative_path is not None:
        rel = _normalize_relative_path(relative_path)
    elif root is not None:
        try:
            rel = _normalize_relative_path(path.relative_to(Path(root)).as_posix())
        except ValueError as exc:
            raise UniversalInboxExtractionError("source must stay under root") from exc
    else:
        rel = _normalize_relative_path(path.name)
    return path, rel


def _read_text(
    path: Path,
    relative_path: str,
) -> tuple[str, UniversalInboxExtractionWarning | None]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1"), UniversalInboxExtractionWarning(
                "encoding_fallback_used",
                relative_path,
                "latin-1",
            )
        except OSError:
            return "", UniversalInboxExtractionWarning("text_read_failed", relative_path)
    except OSError:
        return "", UniversalInboxExtractionWarning("text_read_failed", relative_path)


def _read_json(
    path: Path,
    relative_path: str,
) -> tuple[str, UniversalInboxExtractionWarning | None]:
    raw_text, warning = _read_text(path, relative_path)
    if warning is not None and warning.code.endswith("_failed"):
        return raw_text, warning
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text, UniversalInboxExtractionWarning("json_parse_failed", relative_path)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), warning


def _read_delimited(
    path: Path,
    relative_path: str,
    *,
    delimiter: str,
) -> tuple[str, UniversalInboxExtractionWarning | None]:
    raw_text, warning = _read_text(path, relative_path)
    if warning is not None and warning.code.endswith("_failed"):
        return raw_text, warning
    try:
        rows = list(csv.reader(raw_text.splitlines(), delimiter=delimiter))
    except csv.Error:
        return raw_text, UniversalInboxExtractionWarning("delimited_parse_failed", relative_path)
    return "\n".join(delimiter.join(row) for row in rows), warning


def _read_html(
    path: Path,
    relative_path: str,
) -> tuple[str, UniversalInboxExtractionWarning | None]:
    raw_text, warning = _read_text(path, relative_path)
    if warning is not None and warning.code.endswith("_failed"):
        return raw_text, warning
    parser = _VisibleTextParser()
    try:
        parser.feed(raw_text)
    except Exception:
        return raw_text, UniversalInboxExtractionWarning("html_parse_failed", relative_path)
    return parser.text(), warning


def _read_docx(
    path: Path,
    relative_path: str,
) -> tuple[str, UniversalInboxExtractionWarning | None]:
    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile):
        return "", UniversalInboxExtractionWarning("docx_parse_failed", relative_path)

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError:
        return "", UniversalInboxExtractionWarning("docx_parse_failed", relative_path)

    paragraphs: list[str] = []
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for paragraph in root.iter(f"{namespace}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{namespace}tab":
                parts.append("\t")
            elif node.tag == f"{namespace}br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs), None


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise UniversalInboxExtractionError("relative_path is required")
    if raw.startswith(("/", "~")) or (len(raw) > 2 and raw[1:3] == ":/"):
        raise UniversalInboxExtractionError("relative_path must be relative")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise UniversalInboxExtractionError("relative_path must not contain traversal segments")
    return "/".join(parts)


def _extractor_name(suffix: str) -> str:
    if suffix in TEXT_SUFFIXES:
        return "plain_text"
    if suffix in STRUCTURED_TEXT_SUFFIXES:
        return suffix.lstrip(".")
    if suffix == DOCX_SUFFIX:
        return "docx_zip_xml"
    if suffix == PDF_SUFFIX:
        return "pdf_metadata_only"
    return "unsupported"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._hidden_depth += 1
        elif tag.lower() in {"br", "p", "div", "li", "tr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1
        elif tag.lower() in {"p", "div", "li", "tr"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            text = data.strip()
            if text:
                self._parts.append(text)

    def text(self) -> str:
        lines = (" ".join(part.split()) for part in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)
