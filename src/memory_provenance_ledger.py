"""Redacted provenance ledger for memory and RaptorGraph activity.

This ledger answers "why does Odysseus know/use this?" without storing raw
memory text, document content, chat messages, e-mail bodies, provider output,
chat ids, tokens, or host paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from src.constants import DATA_DIR


MEMORY_PROVENANCE_LEDGER_DIR = os.path.join(DATA_DIR, "memory_provenance_ledger")
MEMORY_PROVENANCE_SCHEMA = "odysseus.memory_provenance.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_SECRET_KEY_HINTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api_key",
    "credential",
    "chat_id",
)
_ALLOWED_EVENT_TYPES = {
    "memory_write_intent",
    "raptorgraph_mutation",
    "memory_maintenance",
    "memory_retrieval",
    "memory_user_interaction",
}


class MemoryProvenanceLedgerError(ValueError):
    """Raised when a provenance record would be unsafe or invalid."""


def record_memory_provenance(
    event_type: str,
    *,
    owner: str | None = None,
    surface: str | None = None,
    source: str | None = None,
    action: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    document_ref: str | None = None,
    source_hash: str | None = None,
    memory_record_ids: Iterable[Any] | None = None,
    graph_event_id: str | None = None,
    node_count: int | None = None,
    edge_count: int | None = None,
    before_count: int | None = None,
    after_count: int | None = None,
    retrieval_count: int | None = None,
    used_in_context: bool | None = None,
    dsgvo_mode: bool | None = None,
    local_only: bool | None = None,
    classification: str | None = None,
    model_id: str | None = None,
    agent_id: str | None = None,
    review_required: bool | None = None,
    dry_run: bool | None = None,
    writes_performed: bool | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one redacted provenance event."""

    record = build_memory_provenance_record(
        event_type,
        owner=owner,
        surface=surface,
        source=source,
        action=action,
        status=status,
        reason=reason,
        session_id=session_id,
        task_id=task_id,
        document_ref=document_ref,
        source_hash=source_hash,
        memory_record_ids=memory_record_ids,
        graph_event_id=graph_event_id,
        node_count=node_count,
        edge_count=edge_count,
        before_count=before_count,
        after_count=after_count,
        retrieval_count=retrieval_count,
        used_in_context=used_in_context,
        dsgvo_mode=dsgvo_mode,
        local_only=local_only,
        classification=classification,
        model_id=model_id,
        agent_id=agent_id,
        review_required=review_required,
        dry_run=dry_run,
        writes_performed=writes_performed,
        metadata=metadata,
    )
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def build_memory_provenance_record(event_type: str, **kwargs: Any) -> dict[str, Any]:
    normalized_type = _safe_label(event_type or "", field="event_type")
    if normalized_type not in _ALLOWED_EVENT_TYPES:
        raise MemoryProvenanceLedgerError("unsupported memory provenance event_type")

    source_hash = _safe_hash_label(kwargs.get("source_hash"), field="source_hash")
    ids = tuple(_safe_label(value, field="memory_record_id") for value in (kwargs.get("memory_record_ids") or ()))
    record: dict[str, Any] = {
        "schema": MEMORY_PROVENANCE_SCHEMA,
        "ts": _now_iso(),
        "event_type": normalized_type,
        "owner": _safe_label(kwargs.get("owner") or "unknown", field="owner"),
        "surface": _safe_label(kwargs.get("surface") or "unknown", field="surface"),
        "source": _safe_label(kwargs.get("source") or "", field="source"),
        "action": _safe_label(kwargs.get("action") or "", field="action"),
        "status": _safe_label(kwargs.get("status") or "unknown", field="status"),
        "reason": _safe_label(kwargs.get("reason") or "", field="reason"),
        "session_id": _safe_label(kwargs.get("session_id") or "", field="session_id"),
        "task_id": _safe_label(kwargs.get("task_id") or "", field="task_id"),
        "document_ref": _safe_reference(kwargs.get("document_ref")),
        "source_hash": source_hash,
        "memory_record_ids": ids,
        "memory_record_count": len(ids),
        "graph_event_id": _safe_label(kwargs.get("graph_event_id") or "", field="graph_event_id"),
        "node_count": _safe_count(kwargs.get("node_count")),
        "edge_count": _safe_count(kwargs.get("edge_count")),
        "before_count": _safe_count(kwargs.get("before_count")),
        "after_count": _safe_count(kwargs.get("after_count")),
        "retrieval_count": _safe_count(kwargs.get("retrieval_count")),
        "used_in_context": _safe_bool(kwargs.get("used_in_context")),
        "dsgvo_mode": _safe_bool(kwargs.get("dsgvo_mode")),
        "local_only": _safe_bool(kwargs.get("local_only")),
        "classification": _safe_label(kwargs.get("classification") or "", field="classification"),
        "model_id": _safe_label(kwargs.get("model_id") or "", field="model_id"),
        "agent_id": _safe_label(kwargs.get("agent_id") or "", field="agent_id"),
        "review_required": _safe_bool(kwargs.get("review_required")),
        "dry_run": _safe_bool(kwargs.get("dry_run")),
        "writes_performed": _safe_bool(kwargs.get("writes_performed")),
        "metadata": _safe_metadata(kwargs.get("metadata") or {}),
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(record)
    return record


def read_memory_provenance(
    *,
    day: str | None = None,
    limit: int = 100,
    event_type: str | None = None,
    owner: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    capped_limit = max(1, min(int(limit or 100), 1000))
    filters = {
        "event_type": _safe_label(event_type, field="event_type_filter") if event_type else "",
        "owner": _safe_label(owner, field="owner_filter") if owner else "",
        "status": _safe_label(status, field="status_filter") if status else "",
    }
    path = ledger_path(day)
    if not path.exists():
        return _read_result(path, capped_limit, filters, [], 0)

    records: list[dict[str, Any]] = []
    skipped = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                record = json.loads(line)
                _reject_forbidden_payload(record)
            except Exception:
                skipped += 1
                continue
            if not isinstance(record, dict):
                skipped += 1
                continue
            if any(value and str(record.get(key, "")) != value for key, value in filters.items()):
                continue
            records.append(record)
    return _read_result(path, capped_limit, filters, records, skipped)


def ledger_path(day: str | None = None) -> Path:
    day_text = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text):
        raise MemoryProvenanceLedgerError("ledger day must be YYYY-MM-DD")
    return Path(MEMORY_PROVENANCE_LEDGER_DIR) / f"{day_text}.jsonl"


def _read_result(
    path: Path,
    limit: int,
    filters: Mapping[str, str],
    records: list[dict[str, Any]],
    skipped: int,
) -> dict[str, Any]:
    recent = list(reversed(records[-limit:]))
    return {
        "status": "success",
        "day": path.stem,
        "limit": limit,
        "filters": {key: value for key, value in filters.items() if value},
        "count": len(recent),
        "total_matches": len(records),
        "records": recent,
        "summary": _summary(records, skipped=skipped),
    }


def _summary(records: Iterable[Mapping[str, Any]], *, skipped: int) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for record in records:
        event_type = str(record.get("event_type") or "unknown")
        status = str(record.get("status") or "unknown")
        by_type[event_type] = by_type.get(event_type, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "total": sum(by_type.values()),
        "by_event_type": by_type,
        "by_status": by_status,
        "skipped": skipped,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, count)


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _safe_hash_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{16,128}", text):
        return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return text


def _safe_reference(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:24]
    return _safe_label(text, field="document_ref")


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        raise MemoryProvenanceLedgerError(f"{field} contains forbidden secret marker")
    if any(ord(ch) < 32 for ch in text):
        raise MemoryProvenanceLedgerError(f"{field} contains control characters")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise MemoryProvenanceLedgerError(f"{field} must not contain host paths")
    if not _SAFE_TOKEN_RE.fullmatch(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text[:180]


def _safe_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key, value in list(metadata.items())[:40]:
        safe_key = _safe_label(key, field="metadata_key")
        if isinstance(value, bool):
            safe[safe_key] = value
        elif isinstance(value, (int, float)):
            safe[safe_key] = value
        elif isinstance(value, (tuple, list)):
            safe[safe_key] = tuple(_safe_label(item, field="metadata_value") for item in value[:20])
        elif value is None:
            safe[safe_key] = None
        else:
            safe[safe_key] = _safe_label(value, field="metadata_value")
    return safe


def _reject_forbidden_payload(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).lower()
    forbidden = (
        "authorization",
        "bearer ",
        "api_key",
        "password",
        "cookie",
        "data:image",
        "private raw text",
        "begin private key",
    )
    if any(marker in encoded for marker in forbidden):
        raise MemoryProvenanceLedgerError("memory provenance payload contains forbidden content marker")
