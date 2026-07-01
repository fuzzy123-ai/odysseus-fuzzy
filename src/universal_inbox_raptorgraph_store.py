"""Append-only RaptorGraph provenance events for Universal Inbox.

This is a bounded local event store, not a global graph rebuild. It persists
only redacted provenance fields from reviewed Universal Inbox write intents.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import time
from typing import Any, Mapping


RAPTORGRAPH_EVENT_STORE_SCHEMA = "odysseus.universal_inbox.raptorgraph_event_store.v1"
DEFAULT_EVENT_LOG = "events.jsonl"

_HEX_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{32,128}$")
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_TEXT_RE = re.compile(
    r"(PRIVATE RAW TEXT|BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,})",
    re.IGNORECASE,
)


class UniversalInboxRaptorGraphStoreError(ValueError):
    """Raised when a graph provenance event is unsafe or invalid."""


@dataclass(frozen=True)
class UniversalInboxRaptorGraphAppendResult:
    status: str
    event_id: str
    duplicate: bool
    path: str
    raw_content_visible: bool = False
    schema: str = RAPTORGRAPH_EVENT_STORE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "event_id": self.event_id,
            "duplicate": self.duplicate,
            "path": self.path,
            "raw_content_visible": False,
        }


class UniversalInboxRaptorGraphEventStore:
    """Small JSONL store for reviewed Universal Inbox graph provenance."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / DEFAULT_EVENT_LOG

    def append(self, event: Mapping[str, Any]) -> UniversalInboxRaptorGraphAppendResult:
        normalized = normalize_universal_inbox_raptorgraph_event(event)
        event_id = str(normalized["event_id"])
        self.root.mkdir(parents=True, exist_ok=True)
        if self._contains(event_id):
            _record_raptorgraph_mutation(normalized, status="duplicate", duplicate=True)
            return UniversalInboxRaptorGraphAppendResult(
                status="duplicate",
                event_id=event_id,
                duplicate=True,
                path=str(self.path),
            )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        _record_raptorgraph_mutation(normalized, status="written", duplicate=False)
        return UniversalInboxRaptorGraphAppendResult(
            status="written",
            event_id=event_id,
            duplicate=False,
            path=str(self.path),
        )

    def _contains(self, event_id: str) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("event_id") == event_id:
                    return True
        return False


def build_universal_inbox_raptorgraph_writer(root: str | Path):
    store = UniversalInboxRaptorGraphEventStore(root)

    def _writer(event: Mapping[str, Any]) -> UniversalInboxRaptorGraphAppendResult:
        return store.append(event)

    return _writer


def normalize_universal_inbox_raptorgraph_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise UniversalInboxRaptorGraphStoreError("event must be a mapping")
    source_hash = _source_hash(event.get("source_hash"))
    memory_record_ids = _safe_id_tuple(event.get("memory_record_ids") or ())
    if not memory_record_ids:
        raise UniversalInboxRaptorGraphStoreError("memory_record_ids are required")
    normalized = {
        "schema": RAPTORGRAPH_EVENT_STORE_SCHEMA,
        "event_id": _event_id(source_hash=source_hash, memory_record_ids=memory_record_ids),
        "event": "universal_inbox_memory_write",
        "source_provider": "universal_inbox",
        "source_hash": source_hash,
        "memory_record_ids": memory_record_ids,
        "classification": _safe_token(event.get("classification") or "private", field="classification"),
        "document_type": _safe_token(event.get("document_type") or "unknown", field="document_type"),
        "domain": _safe_token(event.get("domain") or "private", field="domain"),
        "local_only": bool(event.get("local_only")),
        "dsgvo_mode": bool(event.get("dsgvo_mode")),
        "review_reasons": _safe_string_tuple(event.get("review_reasons") or ()),
        "no_go_reasons": _safe_string_tuple(event.get("no_go_reasons") or ()),
        "raw_content_stored": False,
        "raw_content_visible": False,
        "author_stamp": _safe_author_stamp(event.get("author_stamp")),
        "created_at": int(time.time()),
    }
    encoded = json.dumps(normalized, ensure_ascii=False)
    if _FORBIDDEN_TEXT_RE.search(encoded):
        raise UniversalInboxRaptorGraphStoreError("event appears to contain raw or secret material")
    if len(encoded) > 4000:
        raise UniversalInboxRaptorGraphStoreError("event exceeds safe length")
    return normalized


def _event_id(*, source_hash: str, memory_record_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"source_hash": source_hash, "memory_record_ids": memory_record_ids},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"uix-rg-{digest}"


def _source_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _HEX_RE.fullmatch(text):
        raise UniversalInboxRaptorGraphStoreError("source_hash must be sha256-like")
    return text


def _safe_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise UniversalInboxRaptorGraphStoreError(f"{field} must be a safe token")
    return token


def _safe_id_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (tuple, list)):
        raise UniversalInboxRaptorGraphStoreError("memory_record_ids must be a list")
    result = tuple(str(value or "").strip() for value in values)
    if not result or any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) for value in result):
        raise UniversalInboxRaptorGraphStoreError("memory_record_ids contain unsafe values")
    return result


def _safe_string_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (tuple, list)):
        return ()
    result = []
    for value in values[:12]:
        text = " ".join(str(value or "").strip().split())
        if text and not _FORBIDDEN_TEXT_RE.search(text):
            result.append(text[:120])
    return tuple(result)


def _safe_author_stamp(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "schema",
        "actor",
        "model_id",
        "model_provider",
        "model_scope",
        "action",
        "created_at",
        "source_material_stored",
    }
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        raw = value.get(key)
        if isinstance(raw, bool):
            result[key] = raw
            continue
        text = " ".join(str(raw or "").strip().split())
        if not text or _FORBIDDEN_TEXT_RE.search(text):
            continue
        result[key] = text[:120]
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded) > 1000:
        raise UniversalInboxRaptorGraphStoreError("author_stamp exceeds safe length")
    return result


def _record_raptorgraph_mutation(event: Mapping[str, Any], *, status: str, duplicate: bool) -> None:
    try:
        from src.memory_provenance_ledger import record_memory_provenance

        record_memory_provenance(
            "raptorgraph_mutation",
            owner="unknown",
            surface="universal_inbox",
            source="raptorgraph_event_store",
            action="append_event",
            status=status,
            reason="duplicate" if duplicate else "event_appended",
            source_hash=str(event.get("source_hash") or ""),
            memory_record_ids=event.get("memory_record_ids") or (),
            graph_event_id=str(event.get("event_id") or ""),
            node_count=len(tuple(event.get("memory_record_ids") or ())),
            edge_count=1 if event.get("memory_record_ids") else 0,
            dsgvo_mode=bool(event.get("dsgvo_mode")),
            local_only=bool(event.get("local_only")),
            classification=str(event.get("classification") or ""),
            writes_performed=status == "written" and not duplicate,
        )
    except Exception:
        pass
