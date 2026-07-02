"""Durable, redacted evidence report storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from src.constants import DATA_DIR


EVIDENCE_STORAGE_SCHEMA = "odysseus.evidence_storage.v1"

_SECRET_RE = re.compile(r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})")
_HOST_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])")


class EvidenceStorageError(ValueError):
    """Raised when an evidence report would be unsafe."""


@dataclass(frozen=True, slots=True)
class EvidenceReportRecord:
    report_ref: str
    content_hash: str
    size_bytes: int
    written: bool
    raw_content_visible: bool = False
    schema: str = EVIDENCE_STORAGE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "report_ref": self.report_ref,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "written": self.written,
            "raw_content_visible": False,
        }


def write_evidence_report(
    *,
    report_ref: Any,
    payload: Mapping[str, Any],
    root: str | Path | None = None,
) -> EvidenceReportRecord:
    safe_ref = _safe_report_ref(report_ref)
    clean_payload = _sanitize_payload(payload)
    encoded = json.dumps(clean_payload, ensure_ascii=False, indent=2, sort_keys=True)
    content_hash = "sha256:" + hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()
    base = Path(root) if root is not None else Path(DATA_DIR) / "reports"
    target = _resolve_under(base, safe_ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(encoded + "\n", encoding="utf-8")
    return EvidenceReportRecord(
        report_ref=safe_ref,
        content_hash=content_hash,
        size_bytes=len(encoded.encode("utf-8")),
        written=True,
    )


def build_evidence_readiness(*, report_ref: Any, root: str | Path | None = None) -> dict[str, Any]:
    safe_ref = _safe_report_ref(report_ref)
    base = Path(root) if root is not None else Path(DATA_DIR) / "reports"
    target = _resolve_under(base, safe_ref)
    return {
        "schema": "odysseus.evidence_storage_readiness.v1",
        "report_ref": safe_ref,
        "exists": target.exists(),
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "ready": target.exists() and target.stat().st_size > 0,
        "raw_content_visible": False,
    }


def _safe_report_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    parts = PurePosixPath(text).parts
    if not text or text.startswith("/") or ".." in parts or text.endswith("/"):
        raise EvidenceStorageError("report_ref must be safe relative path")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,220}\.json", text):
        raise EvidenceStorageError("report_ref must be a safe json ref")
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        raise EvidenceStorageError("report_ref contains forbidden material")
    return text


def _resolve_under(root: Path, report_ref: str) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / report_ref).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise EvidenceStorageError("report_ref escapes evidence root") from exc
    return target


def _sanitize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise EvidenceStorageError("payload must be a mapping")
    clean = _sanitize_value(payload)
    if not isinstance(clean, dict):
        raise EvidenceStorageError("payload must sanitize to an object")
    clean.setdefault("generated_at", datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    clean.setdefault("raw_content_visible", False)
    _reject_unsafe(clean)
    return clean


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in {"raw", "raw_text", "content", "html", "body", "bytes"}:
                continue
            result[key_text] = _sanitize_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value[:200]]
    text = str(value)
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return "[redacted]"
    return value


def _reject_unsafe(value: Any) -> None:
    _walk_forbidden(value)


def _walk_forbidden(value: Any, *, key: str = "") -> None:
    lowered_key = key.lower()
    if lowered_key == "raw_content_visible" and bool(value):
        raise EvidenceStorageError("payload exposes raw content")
    if lowered_key in {"authorization", "cookie", "password", "passwd", "api_key", "api-key", "token", "secret"} and value:
        raise EvidenceStorageError("payload contains forbidden material")
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            _walk_forbidden(child_value, key=str(child_key))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_forbidden(item, key=key)
        return
    text = str(value or "")
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        raise EvidenceStorageError("payload contains forbidden material")
