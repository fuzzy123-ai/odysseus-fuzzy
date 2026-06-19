"""Offline-safe ledger models for Nextcloud inbox intake.

The ledger stores only reconstructable file metadata and redacted diagnostics.
It never reads file contents from disk and never persists raw private payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

ALLOWED_STATUSES = {
    "pending",
    "extracting",
    "analyzed",
    "needs_review",
    "routed",
    "metadata_written",
    "indexed",
    "routed_indexed",
    "failed",
    "duplicate",
    "permission_denied",
}
ALLOWED_PROVIDERS = {"nextcloud_inbox"}
_SAFE_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SAFE_ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|chat[_-]?id|authorization)\b\s*[:=]\s*[^\s,;]+"
)
_CONTENT_KEY_RE = re.compile(r"(?i)(content|text|body|payload|raw|bytes|data)$")
_SECRET_KEY_RE = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|chat[_-]?id|authorization)$")
_MAX_ERROR_LEN = 200
_MAX_METADATA_STR_LEN = 120


class NextcloudIntakeLedgerError(ValueError):
    """Raised when an intake ledger record is invalid."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_content_hash(value: bytes | str, *, algorithm: str = "sha256") -> str:
    """Hash provided bytes or text without touching the filesystem."""

    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    else:
        raise NextcloudIntakeLedgerError("hash input must be bytes or str")
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise NextcloudIntakeLedgerError(f"unsupported hash algorithm: {algorithm}") from exc
    digest.update(raw)
    return digest.hexdigest()


def _normalize_status(value: str) -> str:
    status = str(value or "").strip()
    if status not in ALLOWED_STATUSES:
        raise NextcloudIntakeLedgerError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
    return status


def _normalize_provider(value: str) -> str:
    provider = str(value or "").strip()
    if provider not in ALLOWED_PROVIDERS:
        raise NextcloudIntakeLedgerError("provider must be nextcloud_inbox")
    return provider


def _normalize_actor(value: str) -> str:
    actor = str(value or "").strip()
    if not actor or not _SAFE_ACTOR_RE.fullmatch(actor):
        raise NextcloudIntakeLedgerError("actor must be a compact safe identifier")
    return actor


def _normalize_permission_scope(value: str) -> str:
    scope = str(value or "").strip().lower().replace(" ", "_")
    parts = [part for part in scope.split(":") if part]
    if not parts or any(not _SAFE_SEGMENT_RE.fullmatch(part) for part in parts):
        raise NextcloudIntakeLedgerError("permission_scope must use safe colon-delimited segments")
    return ":".join(parts)


def _normalize_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise NextcloudIntakeLedgerError("path is required")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise NextcloudIntakeLedgerError("path must be relative to the inbox scope")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise NextcloudIntakeLedgerError("path must not contain traversal segments")
    if any(any(ord(ch) < 32 for ch in part) for part in parts):
        raise NextcloudIntakeLedgerError("path contains control characters")
    return "/".join(parts)


def _normalize_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise NextcloudIntakeLedgerError("size must be an integer") from exc
    if size < 0:
        raise NextcloudIntakeLedgerError("size must be >= 0")
    return size


def _normalize_mtime(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    text = str(value or "").strip()
    if not text:
        raise NextcloudIntakeLedgerError("mtime is required")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NextcloudIntakeLedgerError("mtime must be an ISO-8601 datetime") from exc
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _redact_text(value: Any, *, limit: int = _MAX_METADATA_STR_LEN) -> str:
    text = _SECRET_RE.sub("[redacted]", str(value or ""))
    text = "".join(ch if ord(ch) >= 32 else " " for ch in text).strip()
    if len(text) > limit:
        return text[: limit - 12].rstrip() + "...[truncated]"
    return text


def _redact_error(value: Any) -> str:
    return _redact_text(value, limit=_MAX_ERROR_LEN)


def redact_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only compact, reconstructable metadata and redact risky fields."""

    def _scrub(item: Any, *, key: str | None = None) -> Any:
        if key and (_SECRET_KEY_RE.search(key) or _CONTENT_KEY_RE.search(key)):
            return "[redacted]"
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, str):
            return _redact_text(item)
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat() if item.tzinfo else item.replace(tzinfo=timezone.utc).isoformat()
        if isinstance(item, Mapping):
            scrubbed: dict[str, Any] = {}
            for child_key, child_value in item.items():
                child_name = _redact_text(child_key, limit=64)
                scrubbed[child_name] = _scrub(child_value, key=child_name)
            return scrubbed
        if isinstance(item, (list, tuple, set)):
            return [_scrub(child) for child in list(item)[:25]]
        return _redact_text(repr(item))

    return _scrub(dict(value or {}))


@dataclass(frozen=True)
class NextcloudIntakeLedgerEntry:
    """Compact offline ledger entry for a single inbox intake item."""

    digest: str
    path: str
    size: int
    mtime: str
    status: str
    actor: str
    permission_scope: str
    provider: str = "nextcloud_inbox"
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None
    recorded_at: str = ""

    def __post_init__(self) -> None:
        digest = str(self.digest or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32,128}", digest):
            raise NextcloudIntakeLedgerError("digest must be a lowercase hex hash")
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "path", _normalize_path(self.path))
        object.__setattr__(self, "size", _normalize_size(self.size))
        object.__setattr__(self, "mtime", _normalize_mtime(self.mtime))
        object.__setattr__(self, "status", _normalize_status(self.status))
        object.__setattr__(self, "actor", _normalize_actor(self.actor))
        object.__setattr__(self, "permission_scope", _normalize_permission_scope(self.permission_scope))
        object.__setattr__(self, "provider", _normalize_provider(self.provider))
        object.__setattr__(self, "errors", tuple(_redact_error(item) for item in self.errors if str(item or "").strip()))
        object.__setattr__(self, "metadata", redact_metadata(self.metadata))
        object.__setattr__(self, "recorded_at", _normalize_mtime(self.recorded_at or _now_iso()))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NextcloudIntakeLedgerEntry":
        return cls(
            digest=payload.get("digest", ""),
            path=payload.get("path", ""),
            size=payload.get("size", 0),
            mtime=payload.get("mtime", ""),
            status=payload.get("status", ""),
            actor=payload.get("actor", ""),
            permission_scope=payload.get("permission_scope", ""),
            provider=payload.get("provider", "nextcloud_inbox"),
            errors=tuple(payload.get("errors") or ()),
            metadata=dict(payload.get("metadata") or {}),
            recorded_at=payload.get("recorded_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "status": self.status,
            "actor": self.actor,
            "permission_scope": self.permission_scope,
            "provider": self.provider,
            "errors": list(self.errors),
            "metadata": dict(self.metadata or {}),
            "recorded_at": self.recorded_at,
        }

    def to_report(self) -> dict[str, Any]:
        review_required = self.status in {"needs_review", "failed", "permission_denied"} or bool(self.errors)
        route_ready = self.status in {"routed", "indexed", "routed_indexed", "metadata_written"}
        return {
            "digest": self.digest,
            "path": self.path,
            "status": self.status,
            "provider": self.provider,
            "actor": self.actor,
            "permission_scope": self.permission_scope,
            "error_count": len(self.errors),
            "review_required": review_required,
            "route_ready": route_ready,
            "metadata_keys": sorted((self.metadata or {}).keys()),
        }


def summarize_entries(entries: list[NextcloudIntakeLedgerEntry]) -> dict[str, Any]:
    """Build a compact report for routing/review without exposing contents."""

    counts: dict[str, int] = {}
    review_items = 0
    route_ready = 0
    for entry in entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1
        report = entry.to_report()
        if report["review_required"]:
            review_items += 1
        if report["route_ready"]:
            route_ready += 1
    return {
        "total": len(entries),
        "by_status": counts,
        "review_items": review_items,
        "route_ready": route_ready,
        "items": [entry.to_report() for entry in entries],
    }


def dumps_entry(entry: NextcloudIntakeLedgerEntry) -> str:
    """Serialize a compact entry snapshot suitable for append-only ledgers."""

    return json.dumps(entry.to_dict(), sort_keys=True, ensure_ascii=False)
