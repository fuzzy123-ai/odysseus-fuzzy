"""Safe MVP execution for Universal File IO exports.

This module intentionally supports only a narrow built-in path for now:
plain text / markdown to a simple PDF. Reports are redacted for history logs;
raw text and host paths stay runtime-only.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from src.universal_export import build_universal_export_plan
from src.universal_inbox_extraction import DEFAULT_MAX_EXTRACT_BYTES, extract_universal_inbox_content


EXPORT_EXECUTION_SCHEMA = "odysseus.universal_export.execution.v1"
TEXT_PDF_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
PDF_MIME_TYPE = "application/pdf"


@dataclass(frozen=True)
class UniversalExportExecutionResult:
    status: str
    reason: str
    target_format: str
    output_path: str = ""
    output_filename: str = ""
    mime_type: str = ""
    bytes_written: int = 0
    required_tool: str = ""
    delivery_ready: bool = False
    raw_content_visible: bool = False
    host_paths_visible: bool = False
    filename_visible: bool = False
    schema: str = EXPORT_EXECUTION_SCHEMA

    @property
    def ok(self) -> bool:
        return self.status == "exported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "ok": self.ok,
            "reason": self.reason,
            "target_format": self.target_format,
            "mime_type": self.mime_type,
            "bytes_written": self.bytes_written,
            "required_tool": self.required_tool,
            "delivery_ready": self.delivery_ready,
            "raw_content_visible": False,
            "host_paths_visible": False,
            "filename_visible": False,
        }


def execute_universal_export(
    source_path: str | Path,
    target_format: str,
    output_dir: str | Path,
    *,
    dsgvo_mode: bool = False,
    max_input_bytes: int = DEFAULT_MAX_EXTRACT_BYTES,
    output_basename: str = "export",
) -> UniversalExportExecutionResult:
    """Execute one safe local export and keep public reports redacted."""

    source = Path(source_path)
    target = str(target_format or "").strip().lower().lstrip(".")
    plan = build_universal_export_plan(source, target, dsgvo_mode=dsgvo_mode)
    if not source.exists() or not source.is_file():
        return _blocked("source_missing", target, required_tool=plan.required_tool)
    if source.is_symlink():
        return _blocked("source_symlink_blocked", target, required_tool=plan.required_tool)
    if not plan.executable_now:
        return _blocked(plan.reason or "export_not_executable", target, required_tool=plan.required_tool)
    if plan.required_tool != "builtin_text_pdf" or target != "pdf" or source.suffix.lower() not in TEXT_PDF_SUFFIXES:
        return _blocked("builtin_executor_unsupported", target, required_tool=plan.required_tool)
    if source.stat().st_size > max_input_bytes:
        return _blocked("source_size_limit_exceeded", target, required_tool=plan.required_tool)

    try:
        packet = extract_universal_inbox_content(source, max_extract_bytes=max_input_bytes)
    except Exception as exc:
        return _blocked(f"extract_failed:{str(exc)[:80]}", target, required_tool=plan.required_tool)
    if not packet.raw_text:
        return _blocked("no_text_to_export", target, required_tool=plan.required_tool)

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = _next_available_path(output_root / f"{_safe_output_basename(output_basename)}.pdf")
    try:
        _write_simple_text_pdf(packet.raw_text, output_path)
    except Exception as exc:
        return _blocked(f"pdf_write_failed:{str(exc)[:80]}", target, required_tool=plan.required_tool)

    size = output_path.stat().st_size
    return UniversalExportExecutionResult(
        status="exported",
        reason="builtin_text_pdf_exported",
        target_format="pdf",
        output_path=str(output_path),
        output_filename=output_path.name,
        mime_type=PDF_MIME_TYPE,
        bytes_written=size,
        required_tool=plan.required_tool,
        delivery_ready=size > 0,
    )


def _blocked(reason: str, target_format: str, *, required_tool: str = "") -> UniversalExportExecutionResult:
    return UniversalExportExecutionResult(
        status="blocked",
        reason=reason,
        target_format=target_format,
        required_tool=required_tool,
    )


def _safe_output_basename(value: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "export")).strip(".-")
    return base[:80] or "export"


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("no available export filename")


def _write_simple_text_pdf(text: str, output_path: Path) -> None:
    lines = _wrap_pdf_lines(_markdown_to_plain_text(text))
    pages = [lines[index : index + 46] for index in range(0, max(1, len(lines)), 46)] or [[""]]

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(f"{3 + i * 2} 0 R".encode("ascii") for i in range(len(pages))) + b"] /Count "
        + str(len(pages)).encode("ascii")
        + b" >>",
    ]
    for idx, page_lines in enumerate(pages):
        page_obj = 3 + idx * 2
        content_obj = page_obj + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> /Contents {content_obj} 0 R >>".encode(
                "ascii"
            )
        )
        stream = _page_stream(page_lines)
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    offsets: list[int] = []
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    output_path.write_bytes(bytes(pdf))


def _markdown_to_plain_text(text: str) -> str:
    cleaned = re.sub(r"`{1,3}", "", text)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    return cleaned


def _wrap_pdf_lines(text: str, *, width: int = 86) -> list[str]:
    result: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.expandtabs(4)
        if not line:
            result.append("")
            continue
        while len(line) > width:
            split_at = line.rfind(" ", 0, width + 1)
            if split_at <= 0:
                split_at = width
            result.append(line[:split_at])
            line = line[split_at:].lstrip()
        result.append(line)
    return result or [""]


def _page_stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "14 TL", "50 792 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({_escape_pdf_text(line)}) Tj")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def _escape_pdf_text(text: str) -> str:
    return str(text).encode("latin-1", errors="replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
