"""Metadata-only ledger contract for large private-data ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, Mapping, Optional


LEDGER_SCHEMA = "odysseus.bigdata_ledger.v1"
EVENT_SCHEMA = "odysseus.bigdata_ledger.event.v1"
ALLOWED_STAGES = frozenset({"inventory", "transfer", "extraction", "analysis", "memory"})
ALLOWED_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "retryable", "skipped", "needs_review", "blocked"}
)
FORBIDDEN_KEYS = frozenset(
    {
        "raw_text",
        "content",
        "body",
        "payload",
        "bytes",
        "binary",
        "ocr_dump",
        "transcript",
        "full_text",
        "page_text",
        "email_body",
        "attachment_bytes",
        "secret",
        "token",
        "password",
        "api_key",
        "credential",
        "chat_id",
        "authorization",
    }
)
SAFE_FIELD_KEYS = frozenset({"content_hash", "source_hash", "sha256", "etag", "size_bytes"})
_FORBIDDEN_KEY_RE = re.compile(r"(?i)(raw|content|body|payload|bytes|text|secret|token|password|api[_-]?key|chat[_-]?id|authorization)")
_SECRET_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*[^\s,;]+")
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_HEX_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{32,128}$")


class BigDataLedgerError(ValueError):
    """Raised when a big-data ledger payload is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class BigDataLedgerItem:
    provider: str
    source_id: str
    relative_path: str
    size_bytes: int
    mtime: str
    content_hash: str = ""
    etag: str = ""
    item_id: str = ""

    def __post_init__(self) -> None:
        provider = _normalize_token(self.provider, field="provider")
        source_id = _normalize_source_id(self.source_id)
        relative_path = _normalize_relative_path(self.relative_path)
        size_bytes = _nonnegative_int(self.size_bytes, field="size_bytes")
        mtime = _normalize_time(self.mtime)
        content_hash = _normalize_optional_hash(self.content_hash, field="content_hash")
        etag = _compact_text(self.etag, limit=160)
        item_id = self.item_id or _stable_item_id(provider, source_id, relative_path)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "relative_path", relative_path)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "mtime", mtime)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "etag", etag)
        object.__setattr__(self, "item_id", _normalize_source_id(item_id))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BigDataLedgerItem":
        _assert_no_forbidden_fields(payload)
        return cls(
            provider=str(payload.get("provider") or ""),
            source_id=str(payload.get("source_id") or ""),
            relative_path=str(payload.get("relative_path") or payload.get("path") or ""),
            size_bytes=payload.get("size_bytes", payload.get("size", 0)),
            mtime=str(payload.get("mtime") or ""),
            content_hash=str(payload.get("content_hash") or payload.get("sha256") or ""),
            etag=str(payload.get("etag") or ""),
            item_id=str(payload.get("item_id") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "provider": self.provider,
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "content_hash": self.content_hash,
            "etag": self.etag,
        }

    def version_digest(self) -> str:
        return _digest(
            {
                "provider": self.provider,
                "source_id": self.source_id,
                "relative_path": self.relative_path,
                "size_bytes": self.size_bytes,
                "mtime": self.mtime,
                "content_hash": self.content_hash,
                "etag": self.etag,
            }
        )


@dataclass(frozen=True, slots=True)
class BigDataLedgerRecord:
    item: BigDataLedgerItem
    stage: str
    status: str
    attempt_count: int = 0
    last_error: str = ""
    next_retry_at: str = ""
    locked_by: str = ""
    locked_at: str = ""
    updated_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.item, BigDataLedgerItem):
            raise BigDataLedgerError("item must be BigDataLedgerItem")
        stage = _normalize_choice(self.stage, ALLOWED_STAGES, field="stage")
        status = _normalize_choice(self.status, ALLOWED_STATUSES, field="status")
        attempt_count = _nonnegative_int(self.attempt_count, field="attempt_count")
        metadata = _sanitize_metadata(self.metadata)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "attempt_count", attempt_count)
        object.__setattr__(self, "last_error", _redact_text(self.last_error, limit=240))
        object.__setattr__(self, "next_retry_at", _normalize_optional_time(self.next_retry_at))
        object.__setattr__(self, "locked_by", _compact_text(self.locked_by, limit=120))
        object.__setattr__(self, "locked_at", _normalize_optional_time(self.locked_at))
        object.__setattr__(self, "updated_at", _normalize_optional_time(self.updated_at) or _now_iso())
        object.__setattr__(self, "metadata", metadata)

    @classmethod
    def create(
        cls,
        item: BigDataLedgerItem | Mapping[str, Any],
        *,
        stage: str,
        status: str,
        attempt_count: int = 0,
        last_error: str = "",
        next_retry_at: str = "",
        locked_by: str = "",
        locked_at: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "BigDataLedgerRecord":
        parsed_item = item if isinstance(item, BigDataLedgerItem) else BigDataLedgerItem.from_mapping(item)
        return cls(
            item=parsed_item,
            stage=stage,
            status=status,
            attempt_count=attempt_count,
            last_error=last_error,
            next_retry_at=next_retry_at,
            locked_by=locked_by,
            locked_at=locked_at,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BigDataLedgerRecord":
        _assert_no_forbidden_fields(payload)
        return cls(
            item=BigDataLedgerItem.from_mapping(_mapping(payload.get("item"), field="item")),
            stage=str(payload.get("stage") or ""),
            status=str(payload.get("status") or ""),
            attempt_count=payload.get("attempt_count", 0),
            last_error=str(payload.get("last_error") or ""),
            next_retry_at=str(payload.get("next_retry_at") or ""),
            locked_by=str(payload.get("locked_by") or ""),
            locked_at=str(payload.get("locked_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            metadata=_mapping(payload.get("metadata") or {}, field="metadata"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": LEDGER_SCHEMA,
            "item": self.item.to_dict(),
            "stage": self.stage,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "last_error": self.last_error,
            "next_retry_at": self.next_retry_at,
            "locked_by": self.locked_by,
            "locked_at": self.locked_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    def state_key(self) -> tuple[str, str]:
        return (self.item.item_id, self.stage)


@dataclass(frozen=True, slots=True)
class BigDataLedgerEvent:
    sequence: int
    event_type: str
    record: BigDataLedgerRecord
    transaction_id: str
    previous_digest: str
    digest: str
    schema: str = EVENT_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        event_type: str,
        record: BigDataLedgerRecord,
        transaction_id: str,
        previous_digest: str,
    ) -> "BigDataLedgerEvent":
        event_type = _normalize_choice(event_type, {"intent", "commit"}, field="event_type")
        sequence = _nonnegative_int(sequence, field="sequence")
        transaction_id = _normalize_source_id(transaction_id)
        previous_digest = str(previous_digest or "ROOT")
        data = {
            "schema": EVENT_SCHEMA,
            "sequence": sequence,
            "event_type": event_type,
            "transaction_id": transaction_id,
            "record": record.to_dict(),
            "previous_digest": previous_digest,
        }
        return cls(
            sequence=sequence,
            event_type=event_type,
            record=record,
            transaction_id=transaction_id,
            previous_digest=previous_digest,
            digest=_digest(data),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BigDataLedgerEvent":
        parsed = cls(
            sequence=_nonnegative_int(payload.get("sequence"), field="sequence"),
            event_type=_normalize_choice(payload.get("event_type"), {"intent", "commit"}, field="event_type"),
            record=BigDataLedgerRecord.from_mapping(_mapping(payload.get("record"), field="record")),
            transaction_id=_normalize_source_id(payload.get("transaction_id")),
            previous_digest=str(payload.get("previous_digest") or "ROOT"),
            digest=str(payload.get("digest") or ""),
            schema=str(payload.get("schema") or EVENT_SCHEMA),
        )
        expected = cls.create(
            sequence=parsed.sequence,
            event_type=parsed.event_type,
            record=parsed.record,
            transaction_id=parsed.transaction_id,
            previous_digest=parsed.previous_digest,
        )
        if parsed.digest != expected.digest:
            raise BigDataLedgerError("event digest mismatch")
        return parsed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "transaction_id": self.transaction_id,
            "record": self.record.to_dict(),
            "previous_digest": self.previous_digest,
            "digest": self.digest,
        }


class AppendOnlyBigDataLedger:
    """Append-only intent/commit ledger that replays only committed records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists() and self.path.is_dir():
            raise BigDataLedgerError("ledger path must be a file")
        self._events: list[BigDataLedgerEvent] = []
        self._state: Dict[tuple[str, str], BigDataLedgerRecord] = {}
        if self.path.exists():
            self._load()

    @property
    def events(self) -> tuple[BigDataLedgerEvent, ...]:
        return tuple(self._events)

    def latest_state(self) -> Dict[tuple[str, str], BigDataLedgerRecord]:
        return dict(self._state)

    def append_intent(self, record: BigDataLedgerRecord) -> BigDataLedgerEvent:
        event = self._build_event("intent", record)
        self._append(event, apply=False)
        self._write(event)
        return event

    def append_commit(self, record: BigDataLedgerRecord, *, transaction_id: str = "") -> BigDataLedgerEvent:
        event = self._build_event("commit", record, transaction_id=transaction_id)
        self._append(event, apply=True)
        self._write(event)
        return event

    def append_record(self, record: BigDataLedgerRecord) -> BigDataLedgerEvent:
        intent = self.append_intent(record)
        return self.append_commit(record, transaction_id=intent.transaction_id)

    def retry_record(self, record: BigDataLedgerRecord, *, last_error: str = "", next_retry_at: str = "") -> BigDataLedgerRecord:
        return BigDataLedgerRecord(
            item=record.item,
            stage=record.stage,
            status="retryable",
            attempt_count=record.attempt_count + 1,
            last_error=last_error or record.last_error,
            next_retry_at=next_retry_at,
            metadata=record.metadata,
        )

    def summary(self) -> Dict[str, Any]:
        by_stage: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        retryable = stuck = 0
        for record in self._state.values():
            by_stage[record.stage] = by_stage.get(record.stage, 0) + 1
            by_status[record.status] = by_status.get(record.status, 0) + 1
            if record.status == "retryable":
                retryable += 1
            if record.status in {"failed", "blocked", "needs_review"}:
                stuck += 1
        return {
            "schema": "odysseus.bigdata_ledger.summary.v1",
            "events": len(self._events),
            "committed_records": sum(1 for event in self._events if event.event_type == "commit"),
            "latest_records": len(self._state),
            "by_stage": dict(sorted(by_stage.items())),
            "by_status": dict(sorted(by_status.items())),
            "retryable": retryable,
            "stuck": stuck,
            "latest_digest": self._events[-1].digest if self._events else "ROOT",
        }

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    raise BigDataLedgerError(f"invalid JSONL record at line {line_number}") from None
                event = BigDataLedgerEvent.from_mapping(_mapping(payload, field="event"))
                self._append(event, apply=event.event_type == "commit")

    def _build_event(self, event_type: str, record: BigDataLedgerRecord, *, transaction_id: str = "") -> BigDataLedgerEvent:
        previous_digest = self._events[-1].digest if self._events else "ROOT"
        tx = transaction_id or _digest(
            {
                "record": record.to_dict(),
                "sequence": len(self._events),
                "previous_digest": previous_digest,
            }
        )[:24]
        return BigDataLedgerEvent.create(
            sequence=len(self._events),
            event_type=event_type,
            record=record,
            transaction_id=tx,
            previous_digest=previous_digest,
        )

    def _append(self, event: BigDataLedgerEvent, *, apply: bool) -> None:
        expected_previous = self._events[-1].digest if self._events else "ROOT"
        if event.sequence != len(self._events):
            raise BigDataLedgerError("ledger sequence is not append-only")
        if event.previous_digest != expected_previous:
            raise BigDataLedgerError("ledger digest chain is broken")
        self._events.append(event)
        if apply:
            self._state[event.record.state_key()] = event.record

    def _write(self, event: BigDataLedgerEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_stable_json(event.to_dict()) + "\n")


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _stable_item_id(provider: str, source_id: str, relative_path: str) -> str:
    return "bigdata:" + hashlib.sha256(f"{provider}\0{source_id}\0{relative_path}".encode("utf-8")).hexdigest()[:24]


def _assert_no_forbidden_fields(value: Any, *, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            normalized = key_text.strip().lower()
            if normalized in SAFE_FIELD_KEYS:
                _assert_no_forbidden_fields(child, path=f"{path}{key_text}.")
                continue
            if normalized in FORBIDDEN_KEYS or _FORBIDDEN_KEY_RE.search(normalized):
                raise BigDataLedgerError(f"raw or secret field is not allowed: {path + key_text}")
            _assert_no_forbidden_fields(child, path=f"{path}{key_text}.")
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_forbidden_fields(child, path=path)


def _sanitize_metadata(value: Mapping[str, Any]) -> Dict[str, Any]:
    _assert_no_forbidden_fields(value)

    def scrub(item: Any) -> Any:
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, str):
            return _redact_text(item, limit=180)
        if isinstance(item, Mapping):
            return {str(key)[:80]: scrub(child) for key, child in list(item.items())[:40]}
        if isinstance(item, (list, tuple, set)):
            return [scrub(child) for child in list(item)[:40]]
        return _compact_text(repr(item), limit=120)

    return scrub(dict(value or {}))


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BigDataLedgerError(f"{field} must be a mapping")
    return value


def _normalize_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower()
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise BigDataLedgerError(f"{field} must be a safe token")
    return token


def _normalize_source_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160 or any(ord(ch) < 32 for ch in text):
        raise BigDataLedgerError("source id must be compact text")
    return text


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise BigDataLedgerError("relative_path must not be absolute")
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise BigDataLedgerError("relative_path must not contain traversal")
    if any(any(ord(ch) < 32 for ch in part) for part in parts):
        raise BigDataLedgerError("relative_path contains control characters")
    return "/".join(parts)


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise BigDataLedgerError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise BigDataLedgerError(f"{field} must be a non-negative integer") from None
    if parsed < 0:
        raise BigDataLedgerError(f"{field} must be a non-negative integer")
    return parsed


def _normalize_choice(value: Any, allowed: Iterable[str], *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    allowed_set = set(allowed)
    if normalized not in allowed_set:
        raise BigDataLedgerError(f"{field} must be one of {sorted(allowed_set)}")
    return normalized


def _normalize_optional_hash(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not _HEX_RE.fullmatch(text):
        raise BigDataLedgerError(f"{field} must be sha256-like")
    return text


def _normalize_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise BigDataLedgerError("time is required")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise BigDataLedgerError("time must be ISO-8601") from None
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_optional_time(value: Any) -> str:
    text = str(value or "").strip()
    return _normalize_time(text) if text else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compact_text(value: Any, *, limit: int) -> str:
    text = "".join(ch if ord(ch) >= 32 else " " for ch in str(value or "")).strip()
    return text[:limit]


def _redact_text(value: Any, *, limit: int) -> str:
    return _compact_text(_SECRET_VALUE_RE.sub("[redacted]", value or ""), limit=limit)
