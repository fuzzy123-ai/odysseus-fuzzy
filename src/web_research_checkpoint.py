"""Resumable checkpoint model for bounded website research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse, urlunparse


WEB_RESEARCH_CHECKPOINT_SCHEMA = "odysseus.web_research_checkpoint.v1"


class WebResearchCheckpointError(ValueError):
    """Raised when a web research checkpoint is unsafe."""


@dataclass(frozen=True, slots=True)
class FrontierItem:
    url: str
    depth: int
    attempts: int = 0
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "url": self.url,
            "depth": self.depth,
            "attempts": self.attempts,
            "last_error": _safe_reason(self.last_error),
        }
        _reject_unsafe_payload(payload)
        return payload


@dataclass(frozen=True, slots=True)
class WebResearchCheckpoint:
    scope_id: str
    frontier: tuple[FrontierItem, ...]
    visited_urls: tuple[str, ...]
    content_hashes: tuple[str, ...]
    pages_processed: int
    max_pages: int
    status: str
    updated_at: str
    schema: str = WEB_RESEARCH_CHECKPOINT_SCHEMA

    @property
    def exhausted(self) -> bool:
        return self.pages_processed >= self.max_pages or (not self.frontier and self.status in {"running", "ready"})

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "scope_id": self.scope_id,
            "frontier": tuple(item.to_dict() for item in self.frontier),
            "visited_urls": self.visited_urls,
            "content_hashes": self.content_hashes,
            "pages_processed": self.pages_processed,
            "max_pages": self.max_pages,
            "status": self.status,
            "updated_at": self.updated_at,
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


def create_initial_checkpoint(scope: Mapping[str, Any]) -> WebResearchCheckpoint:
    if not isinstance(scope, Mapping):
        raise WebResearchCheckpointError("scope must be a mapping")
    _reject_unsafe_payload(scope)
    scope_id = _safe_label(scope.get("scope_id") or "", field="scope_id")
    seed_url = _safe_url(scope.get("seed_url") or "")
    max_pages = _bounded_int(scope.get("max_pages"), field="max_pages", minimum=1, maximum=500)
    return WebResearchCheckpoint(
        scope_id=scope_id,
        frontier=(FrontierItem(seed_url, depth=0),),
        visited_urls=(),
        content_hashes=(),
        pages_processed=0,
        max_pages=max_pages,
        status="ready",
        updated_at=_now_iso(),
    )


def advance_checkpoint(
    checkpoint: Mapping[str, Any] | WebResearchCheckpoint,
    *,
    visited_url: Any,
    content_hash: Any = "",
    discovered_urls: Iterable[Any] = (),
    max_depth: Any = 3,
) -> WebResearchCheckpoint:
    current = checkpoint if isinstance(checkpoint, WebResearchCheckpoint) else checkpoint_from_dict(checkpoint)
    visited = _safe_url(visited_url)
    max_depth_int = _bounded_int(max_depth, field="max_depth", minimum=0, maximum=5)
    visited_urls = list(current.visited_urls)
    if visited not in visited_urls:
        visited_urls.append(visited)
    content_hashes = list(current.content_hashes)
    safe_hash = _safe_content_hash(content_hash)
    if safe_hash and safe_hash not in content_hashes:
        content_hashes.append(safe_hash)
    remaining = [item for item in current.frontier if item.url != visited]
    known = set(visited_urls) | {item.url for item in remaining}
    next_depth = _depth_for_url(current.frontier, visited) + 1
    if next_depth <= max_depth_int:
        for url in discovered_urls:
            safe_url = _safe_url(url)
            if safe_url not in known and len(remaining) + len(visited_urls) < current.max_pages:
                remaining.append(FrontierItem(safe_url, depth=next_depth))
                known.add(safe_url)
    pages_processed = min(len(visited_urls), current.max_pages)
    status = "done" if pages_processed >= current.max_pages or not remaining else "running"
    return WebResearchCheckpoint(
        scope_id=current.scope_id,
        frontier=tuple(remaining),
        visited_urls=tuple(visited_urls),
        content_hashes=tuple(content_hashes),
        pages_processed=pages_processed,
        max_pages=current.max_pages,
        status=status,
        updated_at=_now_iso(),
    )


def checkpoint_from_dict(payload: Mapping[str, Any]) -> WebResearchCheckpoint:
    if not isinstance(payload, Mapping):
        raise WebResearchCheckpointError("checkpoint must be a mapping")
    _reject_unsafe_payload(payload)
    return WebResearchCheckpoint(
        scope_id=_safe_label(payload.get("scope_id") or "", field="scope_id"),
        frontier=tuple(
            FrontierItem(
                url=_safe_url(item.get("url") if isinstance(item, Mapping) else ""),
                depth=_bounded_int(item.get("depth") if isinstance(item, Mapping) else 0, field="depth", minimum=0, maximum=5),
                attempts=_bounded_int(item.get("attempts") if isinstance(item, Mapping) else 0, field="attempts", minimum=0, maximum=20),
                last_error=_safe_reason(item.get("last_error") if isinstance(item, Mapping) else ""),
            )
            for item in payload.get("frontier") or ()
        ),
        visited_urls=tuple(_safe_url(url) for url in payload.get("visited_urls") or ()),
        content_hashes=tuple(_safe_content_hash(value) for value in payload.get("content_hashes") or () if _safe_content_hash(value)),
        pages_processed=_bounded_int(payload.get("pages_processed"), field="pages_processed", minimum=0, maximum=500),
        max_pages=_bounded_int(payload.get("max_pages"), field="max_pages", minimum=1, maximum=500),
        status=_safe_label(payload.get("status") or "ready", field="status"),
        updated_at=_safe_timestamp(payload.get("updated_at") or _now_iso()),
    )


def write_checkpoint(path: str | Path, checkpoint: WebResearchCheckpoint) -> Path:
    target = Path(path)
    if target.is_absolute():
        raise WebResearchCheckpointError("checkpoint path must be relative")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return target


def read_checkpoint(path: str | Path) -> WebResearchCheckpoint:
    target = Path(path)
    if target.is_absolute():
        raise WebResearchCheckpointError("checkpoint path must be relative")
    return checkpoint_from_dict(json.loads(target.read_text(encoding="utf-8")))


def _depth_for_url(frontier: tuple[FrontierItem, ...], url: str) -> int:
    for item in frontier:
        if item.url == url:
            return item.depth
    return 0


def _safe_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebResearchCheckpointError("invalid_url")
    if parsed.username or parsed.password:
        raise WebResearchCheckpointError("url_contains_credentials")
    host = parsed.hostname.lower()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        raise WebResearchCheckpointError("invalid_host")
    return urlunparse((parsed.scheme, host, parsed.path or "/", "", "", ""))


def _safe_content_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.fullmatch(r"sha256:[a-f0-9]{16,64}", text):
        raise WebResearchCheckpointError("content hash is invalid")
    return text


def _safe_reason(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    return text if re.fullmatch(r"[a-z0-9_.:-]{0,80}", text) else "redacted"


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"^[A-Za-z0-9_.:-]{1,120}$", text):
        raise WebResearchCheckpointError(f"{field} is invalid")
    return text


def _safe_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text):
        raise WebResearchCheckpointError("timestamp is invalid")
    return text


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WebResearchCheckpointError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise WebResearchCheckpointError(f"{field} out of range")
    return parsed


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret", "raw_text"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise WebResearchCheckpointError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise WebResearchCheckpointError("payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise WebResearchCheckpointError("payload contains host path")
