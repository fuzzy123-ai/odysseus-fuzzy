"""Bounded, owner-scoped reads for one explicitly selected Inbox source."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
from typing import Any
from urllib.parse import quote

from src.upload_handler import is_valid_upload_id, secure_filename
from src.universal_inbox_file_types import classify_universal_inbox_file


SOURCE_CONTENT_SCHEMA = "odysseus.universal_inbox.source_content.v1"
SOURCE_ERROR_SCHEMA = "odysseus.universal_inbox.source_content_error.v1"
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
SIGNATURE_SAMPLE_BYTES = 64 * 1024

_P0_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".pdf", ".docx"})
_MIME_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class UniversalInboxSourceAccessError(RuntimeError):
    """Structured, content-free source access failure."""

    def __init__(
        self,
        *,
        status_code: int,
        state: str,
        reason_code: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.status_code = status_code
        self.state = state
        self.reason_code = reason_code
        self.headers = dict(headers or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SOURCE_ERROR_SCHEMA,
            "state": self.state,
            "reason_code": self.reason_code,
            "content_included": False,
            "source_ref_visible": False,
            "absolute_path_visible": False,
        }


@dataclass(frozen=True, slots=True)
class UniversalInboxSourceContent:
    body: bytes
    media_type: str
    disposition: str
    status_code: int
    state: str
    total_size: int
    start: int
    end: int
    magic_diagnostic: str
    mime_diagnostic: str

    @property
    def truncated(self) -> bool:
        return self.state == "truncated"

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Disposition": self.disposition,
            "Content-Length": str(len(self.body)),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Odysseus-Content-Schema": SOURCE_CONTENT_SCHEMA,
            "X-Odysseus-Content-State": self.state,
            "X-Odysseus-Content-Truncated": str(self.truncated).lower(),
            "X-Odysseus-Magic-Diagnostic": self.magic_diagnostic,
            "X-Odysseus-Mime-Diagnostic": self.mime_diagnostic,
        }
        if self.status_code == 206:
            headers["Content-Range"] = (
                f"bytes {self.start}-{self.end}/{self.total_size}"
            )
        return headers


def read_selected_universal_inbox_source(
    upload_handler: Any,
    source_ref: str,
    *,
    owner: str | None,
    auth_manager: Any = None,
    range_header: str | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> UniversalInboxSourceContent:
    """Read one authorized upload source without exposing its storage path."""

    source_kind, upload_id = normalize_universal_inbox_source_ref(source_ref)
    if source_kind != "upload":
        _fail(415, "unsupported", "unsupported_source_kind")
    if not is_valid_upload_id(upload_id):
        _fail(400, "invalid", "invalid_upload_source_ref")

    if upload_handler is None or not callable(
        getattr(upload_handler, "resolve_upload", None)
    ):
        _fail(503, "unavailable", "upload_source_backend_unavailable")

    auth_configured = bool(
        auth_manager and getattr(auth_manager, "is_configured", False)
    )
    normalized_owner = str(owner or "").strip() or None
    if auth_configured and not normalized_owner:
        _fail(403, "unauthorized", "authentication_required")

    info = upload_handler.resolve_upload(
        upload_id,
        owner=normalized_owner,
        auth_manager=auth_manager,
        allow_admin=True,
    )
    if not isinstance(info, dict):
        _fail(404, "not_found", "source_not_found")

    source_path = _confined_source_path(upload_handler, info)
    try:
        source_stat = os.stat(source_path, follow_symlinks=True)
    except OSError:
        _fail(404, "not_found", "source_not_found")
    if not stat.S_ISREG(source_stat.st_mode):
        _fail(404, "not_found", "source_not_found")

    response_limit = _bounded_positive_int(
        max_response_bytes,
        field_name="max_response_bytes",
    )
    source_limit = _bounded_positive_int(
        max_source_bytes,
        field_name="max_source_bytes",
    )
    total_size = int(source_stat.st_size)
    if total_size > source_limit:
        _fail(413, "oversized", "source_size_limit_exceeded")

    display_name = _safe_display_name(
        info.get("original_name") or info.get("name") or upload_id
    )
    declared_mime = _safe_mime_type(info.get("mime"))
    decision = classify_universal_inbox_file(
        display_name,
        mime_type=declared_mime,
    )
    if decision.blocked:
        _fail(415, "blocked", "dangerous_source_type")
    if decision.suffix not in _P0_SUFFIXES:
        _fail(415, "unsupported", "source_format_not_supported")

    prefix, suffix = _read_signature_samples(source_path, total_size)
    magic_kind = _magic_kind(prefix)
    if decision.suffix == ".docx" and magic_kind == "ole_container":
        _fail(422, "password_required", "encrypted_office_container")
    if decision.suffix == ".pdf" and b"/Encrypt" in prefix + suffix:
        _fail(422, "password_required", "encrypted_pdf")

    mime_diagnostic = _validate_declared_mime(
        suffix=decision.suffix,
        declared_mime=declared_mime,
    )
    if not _magic_matches_suffix(decision.suffix, magic_kind):
        _fail(415, "mime_mismatch", "filename_magic_mismatch")

    start, end, status_code, state = _select_byte_window(
        range_header=range_header,
        total_size=total_size,
        max_response_bytes=response_limit,
    )
    body = _read_byte_window(source_path, start=start, end=end)
    if len(body) != max(0, end - start + 1):
        _fail(409, "changed", "source_changed_during_read")

    return UniversalInboxSourceContent(
        body=body,
        media_type=_response_media_type(decision.suffix),
        disposition=_content_disposition(
            display_name,
            inline=decision.suffix != ".docx",
        ),
        status_code=status_code,
        state=state,
        total_size=total_size,
        start=start,
        end=end,
        magic_diagnostic=magic_kind,
        mime_diagnostic=mime_diagnostic,
    )


def normalize_universal_inbox_source_ref(source_ref: str) -> tuple[str, str]:
    raw = str(source_ref or "").strip()
    if not raw or "/" in raw or "\\" in raw or raw in {".", ".."}:
        _fail(400, "invalid", "invalid_source_ref")
    if raw.startswith("inbox:upload:"):
        kind = "upload"
        value = raw.removeprefix("inbox:upload:")
    elif ":" in raw:
        kind, value = raw.split(":", 1)
    else:
        kind, value = "upload", raw
    kind = kind.strip().lower()
    value = value.strip()
    if not kind or not value:
        _fail(400, "invalid", "invalid_source_ref")
    return kind, value


def _confined_source_path(upload_handler: Any, info: dict[str, Any]) -> str:
    upload_root = str(getattr(upload_handler, "upload_dir", "") or "").strip()
    source_path = str(info.get("path") or "").strip()
    if not upload_root or not source_path:
        _fail(404, "not_found", "source_not_found")
    root = os.path.realpath(upload_root)
    path = os.path.realpath(source_path)
    try:
        confined = os.path.commonpath([root, path]) == root
    except (OSError, ValueError):
        confined = False
    if not confined:
        _fail(404, "not_found", "source_not_found")
    return path


def _read_signature_samples(path: str, total_size: int) -> tuple[bytes, bytes]:
    try:
        with open(path, "rb") as handle:
            prefix = handle.read(SIGNATURE_SAMPLE_BYTES)
            if total_size > SIGNATURE_SAMPLE_BYTES:
                handle.seek(max(0, total_size - SIGNATURE_SAMPLE_BYTES))
                suffix = handle.read(SIGNATURE_SAMPLE_BYTES)
            else:
                suffix = b""
    except OSError:
        _fail(404, "not_found", "source_not_found")
    return prefix, suffix


def _read_byte_window(path: str, *, start: int, end: int) -> bytes:
    length = max(0, end - start + 1)
    if not length:
        return b""
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            return handle.read(length)
    except OSError:
        _fail(404, "not_found", "source_not_found")
    return b""


def _select_byte_window(
    *,
    range_header: str | None,
    total_size: int,
    max_response_bytes: int,
) -> tuple[int, int, int, str]:
    if total_size == 0:
        if range_header:
            _range_error(total_size)
        return 0, -1, 200, "complete"

    if not range_header:
        start = 0
        end = min(total_size - 1, max_response_bytes - 1)
        if end < total_size - 1:
            return start, end, 206, "truncated"
        return start, end, 200, "complete"

    value = str(range_header).strip()
    if "," in value:
        _range_error(total_size)
    match = _RANGE_RE.fullmatch(value)
    if not match:
        _range_error(total_size)
    raw_start, raw_end = match.groups()
    if not raw_start and not raw_end:
        _range_error(total_size)

    if not raw_start:
        suffix_length = int(raw_end)
        if suffix_length < 1:
            _range_error(total_size)
        start = max(0, total_size - suffix_length)
        end = total_size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else total_size - 1
        if start >= total_size or end < start:
            _range_error(total_size)
        end = min(end, total_size - 1)

    requested_end = end
    end = min(end, start + max_response_bytes - 1)
    state = "truncated" if end < requested_end else "partial"
    return start, end, 206, state


def _range_error(total_size: int) -> None:
    _fail(
        416,
        "range_not_satisfiable",
        "invalid_byte_range",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes */{total_size}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _magic_kind(prefix: bytes) -> str:
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"PK\x03\x04"):
        return "zip_container"
    if prefix.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole_container"
    if prefix.startswith(b"MZ"):
        return "pe_executable"
    if prefix.startswith(b"\x7fELF"):
        return "elf_executable"
    if prefix.startswith(b"#!"):
        return "script"
    return "text_or_unknown"


def _magic_matches_suffix(suffix: str, magic_kind: str) -> bool:
    if suffix == ".pdf":
        return magic_kind == "pdf"
    if suffix == ".docx":
        return magic_kind == "zip_container"
    return magic_kind == "text_or_unknown"


def _validate_declared_mime(*, suffix: str, declared_mime: str) -> str:
    if not declared_mime or declared_mime == "application/octet-stream":
        return "missing_or_generic"
    if suffix == ".pdf" and declared_mime != "application/pdf":
        _fail(415, "mime_mismatch", "declared_mime_mismatch")
    if suffix == ".docx" and declared_mime not in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    }:
        _fail(415, "mime_mismatch", "declared_mime_mismatch")
    if suffix in {".md", ".markdown", ".txt"} and not declared_mime.startswith(
        "text/"
    ):
        _fail(415, "mime_mismatch", "declared_mime_mismatch")
    return "match"


def _response_media_type(suffix: str) -> str:
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".docx":
        return (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )
    return "text/plain; charset=utf-8"


def _content_disposition(display_name: str, *, inline: bool) -> str:
    kind = "inline" if inline else "attachment"
    ascii_name = secure_filename(display_name).replace('"', "")
    encoded_name = quote(display_name, safe="")
    return (
        f'{kind}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded_name}"
    )


def _safe_display_name(value: Any) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char >= " " and char != "\x7f")
    return (name.strip() or "unnamed")[:255]


def _safe_mime_type(value: Any) -> str:
    mime_type = str(value or "").strip().lower()
    return mime_type if _MIME_RE.fullmatch(mime_type) else ""


def _bounded_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _fail(
    status_code: int,
    state: str,
    reason_code: str,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    raise UniversalInboxSourceAccessError(
        status_code=status_code,
        state=state,
        reason_code=reason_code,
        headers=headers,
    )


__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_SOURCE_BYTES",
    "SOURCE_CONTENT_SCHEMA",
    "SOURCE_ERROR_SCHEMA",
    "UniversalInboxSourceAccessError",
    "UniversalInboxSourceContent",
    "normalize_universal_inbox_source_ref",
    "read_selected_universal_inbox_source",
]
