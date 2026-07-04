"""Post-update tool capability knowledge refresh.

This module turns the current built-in tool registry into a redacted,
versioned knowledge packet. It is intentionally about Odysseus system
capabilities, not private user data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.atomic_io import atomic_write_json
from src.constants import TOOL_CAPABILITY_KNOWLEDGE_DIR, TOOL_CAPABILITY_RAPTORGRAPH_DIR
from src.tool_index import ALWAYS_AVAILABLE, ASSISTANT_ALWAYS_AVAILABLE, BUILTIN_TOOL_DESCRIPTIONS
from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS


TOOL_CAPABILITY_SNAPSHOT_SCHEMA = "odysseus.tool_capability_snapshot.v1"
TOOL_CAPABILITY_MEMORY_RECORD_SCHEMA = "odysseus.tool_capability_memory_record.v1"
TOOL_CAPABILITY_RAPTORGRAPH_EVENT_SCHEMA = "odysseus.tool_capability_raptorgraph_event.v1"
TOOL_CAPABILITY_MEMORY_WRITE_INTENT_SCHEMA = "odysseus.tool_capability_memory_write_intent.v1"
TOOL_CAPABILITY_MEMORY_WRITE_EXECUTION_SCHEMA = "odysseus.tool_capability_memory_write_execution.v1"
HISTORY_FILE = "history.jsonl"
LATEST_FILE = "latest.json"

_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|credential)\b\s*[:=]\s*\S+")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\t]+")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9._-])/(?:[^\s/]+/)*[^\s]+")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{32,128}$")


class ToolCapabilityMaintenanceError(ValueError):
    """Raised when a tool capability knowledge packet is unsafe."""


@dataclass(frozen=True, slots=True)
class ToolCapabilityRefreshReport:
    status: str
    snapshot: dict[str, Any]
    memory_records: tuple[dict[str, Any], ...]
    raptorgraph_event: dict[str, Any]
    persisted: bool
    index_status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "snapshot": dict(self.snapshot),
            "memory_records": [dict(record) for record in self.memory_records],
            "raptorgraph_event": dict(self.raptorgraph_event),
            "persisted": self.persisted,
            "index_status": dict(self.index_status),
        }


@dataclass(frozen=True, slots=True)
class ToolCapabilityMemoryWriteReport:
    status: str
    reason: str
    memory_records_planned: int
    memory_records_written: int = 0
    memory_records_skipped: int = 0
    dry_run: bool = True
    writes_performed: bool = False
    raw_content_visible: bool = False
    schema: str = TOOL_CAPABILITY_MEMORY_WRITE_EXECUTION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "memory_records_planned": self.memory_records_planned,
            "memory_records_written": self.memory_records_written,
            "memory_records_skipped": self.memory_records_skipped,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolCapabilityRaptorGraphAppendResult:
    status: str
    event_id: str
    duplicate: bool
    path: str
    raw_content_visible: bool = False
    schema: str = TOOL_CAPABILITY_RAPTORGRAPH_EVENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "event_id": self.event_id,
            "duplicate": self.duplicate,
            "path": self.path,
            "raw_content_visible": False,
        }


def refresh_tool_capability_knowledge(
    *,
    reason: str = "manual",
    commit: str = "",
    data_dir: str | Path | None = None,
    persist: bool = True,
    refresh_index: bool = True,
) -> ToolCapabilityRefreshReport:
    """Refresh the Chroma tool index and persist a safe knowledge packet."""

    index_status = refresh_runtime_tool_index(enabled=refresh_index)
    snapshot = build_tool_capability_snapshot(reason=reason, commit=commit, index_status=index_status)
    memory_records = build_tool_memory_records(snapshot)
    raptorgraph_event = build_tool_raptorgraph_event(snapshot, memory_records=memory_records)
    persisted = False
    if persist:
        persist_tool_capability_knowledge(
            snapshot=snapshot,
            memory_records=memory_records,
            raptorgraph_event=raptorgraph_event,
            data_dir=data_dir,
        )
        persisted = True
    return ToolCapabilityRefreshReport(
        status="refreshed",
        snapshot=snapshot,
        memory_records=memory_records,
        raptorgraph_event=raptorgraph_event,
        persisted=persisted,
        index_status=index_status,
    )


def build_tool_capability_memory_write_intent(
    *,
    snapshot: Mapping[str, Any],
    memory_records: Iterable[Mapping[str, Any]],
    raptorgraph_event: Mapping[str, Any],
    owner: str = "system",
) -> dict[str, Any]:
    """Build a ready-to-write intent for non-private system capability memory."""

    _assert_snapshot(snapshot)
    records = tuple(dict(record) for record in memory_records)
    for record in records:
        _assert_safe_payload(record)
    event = dict(raptorgraph_event)
    _assert_safe_payload(event)
    intent = {
        "schema": TOOL_CAPABILITY_MEMORY_WRITE_INTENT_SCHEMA,
        "status": "ready",
        "ready_to_write": True,
        "owner": _safe_label(owner or "system"),
        "source": "tool_capability_maintenance",
        "snapshot_id": snapshot["id"],
        "source_fingerprint": snapshot["fingerprint"],
        "classification": "system_capability",
        "raw_content_stored": False,
        "private_content_stored": False,
        "memory_records": records,
        "raptorgraph_event": event,
        "analysis_policy": {
            "owner": _safe_label(owner or "system"),
            "surface": "tool_capability_maintenance",
            "classification": "system_capability",
            "dsgvo_mode": False,
            "local_only": False,
            "raw_content_stored": False,
            "write_gate_required": True,
        },
    }
    _assert_safe_payload(intent)
    return intent


def execute_tool_capability_memory_write(
    intent: Mapping[str, Any],
    *,
    write_gate_open: bool = False,
    dry_run: bool = True,
    memory_manager: Any = None,
    memory_vector: Any = None,
    owner: str = "system",
    confirmation_source: str = "system_capability_policy",
) -> ToolCapabilityMemoryWriteReport:
    """Plan or execute idempotent writes of tool capability chunks to Memory."""

    if not isinstance(intent, Mapping):
        raise ToolCapabilityMaintenanceError("intent must be a mapping")
    records = tuple(record for record in (intent.get("memory_records") or ()) if isinstance(record, Mapping))
    if str(intent.get("status") or "") != "ready" or not bool(intent.get("ready_to_write")):
        report = ToolCapabilityMemoryWriteReport(
            status="blocked",
            reason="intent_not_ready",
            memory_records_planned=len(records),
            dry_run=True,
        )
        _record_tool_capability_memory_write(intent, report, owner=owner, confirmation_source=confirmation_source)
        return report
    if not write_gate_open:
        report = ToolCapabilityMemoryWriteReport(
            status="blocked",
            reason="write_gate_closed",
            memory_records_planned=len(records),
            dry_run=True,
        )
        _record_tool_capability_memory_write(intent, report, owner=owner, confirmation_source=confirmation_source)
        return report
    if dry_run:
        report = ToolCapabilityMemoryWriteReport(
            status="planned",
            reason="dry_run_only",
            memory_records_planned=len(records),
            dry_run=True,
        )
        _record_tool_capability_memory_write(intent, report, owner=owner, confirmation_source=confirmation_source)
        return report
    if memory_manager is None:
        raise ToolCapabilityMaintenanceError("memory_manager is required for live execution")

    written = 0
    skipped = 0
    for record in records:
        result = _upsert_tool_memory_record(record, memory_manager=memory_manager, memory_vector=memory_vector, owner=owner)
        if result == "written":
            written += 1
        else:
            skipped += 1

    report = ToolCapabilityMemoryWriteReport(
        status="written",
        reason="write_gate_open_and_records_upserted",
        memory_records_planned=len(records),
        memory_records_written=written,
        memory_records_skipped=skipped,
        dry_run=False,
        writes_performed=bool(written),
    )
    _record_tool_capability_memory_write(intent, report, owner=owner, confirmation_source=confirmation_source)
    return report


def load_tool_capability_provider_payload(*, query: str = "", budget: int = 0) -> dict[str, Any]:
    """Return compact provider context for capability/self-knowledge questions."""

    payload = _load_latest_payload()
    if payload:
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        records = tuple(payload.get("memory_records") or ())
    else:
        snapshot = build_tool_capability_snapshot(reason="provider-fallback", index_status={"status": "unknown"})
        records = build_tool_memory_records(snapshot)
    _assert_snapshot(snapshot)
    available = set(snapshot.get("tool_names") or ())
    key_tools = tuple(
        name
        for name in ("read_file", "write_file", "edit_file", "grep", "glob", "ls", "bash", "python", "manage_repos", "manage_tasks")
        if name in available
    )
    snippets = []
    max_records = 2 if int(budget or 0) < 600 else 4
    for record in records[:max_records]:
        if not isinstance(record, Mapping):
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        snippets.append({
            "source": record.get("source") or "tool_capability_maintenance",
            "chunk": metadata.get("chunk") or "",
            "text": _safe_text(record.get("text") or "", limit=900),
        })
    readiness = "ready" if (snapshot.get("index_status") or {}).get("status") == "ok" else "degraded"
    return {
        "structured_state": {
            "tool_capability_snapshot": {
                "id": snapshot.get("id"),
                "generated_at": snapshot.get("generated_at"),
                "commit": snapshot.get("commit") or "",
                "builtin_tool_count": snapshot.get("builtin_tool_count"),
                "schema_tool_count": snapshot.get("schema_tool_count"),
                "key_tools_available": key_tools,
                "domains": dict(snapshot.get("domains") or {}),
                "index_status": dict(snapshot.get("index_status") or {}),
                "schema_parity": dict(snapshot.get("schema_parity") or {}),
            }
        },
        "snippets": snippets or [{"source": "tool_capability_maintenance", "chunk": "summary", "text": "\n".join(snapshot.get("summary") or ())}],
        "sources": [{"source": "tool_capability_knowledge", "snapshot_id": snapshot.get("id"), "score": 1.0}],
        "memory": {
            "summary": {
                "readiness_state": readiness,
                "readiness_gaps": 0 if readiness == "ready" else 1,
                "readiness_gap_names": () if readiness == "ready" else ("tool_index_refresh_not_confirmed",),
            }
        },
        "cache_key": snapshot.get("fingerprint"),
    }


def read_tool_capability_diagnostics(
    *,
    data_dir: str | Path | None = None,
    raptorgraph_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a compact, redacted readback of current tool capability knowledge."""

    payload = _load_latest_payload(data_dir)
    if not payload:
        result = {
            "status": "no_data",
            "snapshot": None,
            "memory_records": {
                "count": 0,
                "ids": (),
                "written_status": "unknown",
            },
            "raptorgraph": {
                "event_present": False,
                "event": "",
                "store_event_count": 0,
                "latest_event_id": "",
                "latest_event": "",
            },
            "raw_content_visible": False,
        }
        _assert_safe_payload(result)
        return result

    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
    _assert_snapshot(snapshot)
    records = tuple(record for record in (payload.get("memory_records") or ()) if isinstance(record, Mapping))
    for record in records:
        _assert_safe_payload(record)
    raptor_event = payload.get("raptorgraph_event") if isinstance(payload.get("raptorgraph_event"), Mapping) else {}
    if raptor_event:
        _assert_safe_payload(raptor_event)
    graph_rows = _read_tool_raptorgraph_rows(raptorgraph_dir)
    latest_graph = graph_rows[-1] if graph_rows else {}
    result = {
        "status": "success",
        "snapshot": {
            "id": snapshot.get("id"),
            "generated_at": snapshot.get("generated_at"),
            "commit": snapshot.get("commit") or "",
            "builtin_tool_count": snapshot.get("builtin_tool_count"),
            "schema_tool_count": snapshot.get("schema_tool_count"),
            "fingerprint": snapshot.get("fingerprint"),
            "index_status": dict(snapshot.get("index_status") or {}),
            "domains": dict(snapshot.get("domains") or {}),
        },
        "memory_records": {
            "count": len(records),
            "ids": tuple(str(record.get("memory_id") or "") for record in records if record.get("memory_id")),
            "written_status": "generated",
        },
        "raptorgraph": {
            "event_present": bool(raptor_event),
            "event": raptor_event.get("event") if raptor_event else "",
            "store_event_count": len(graph_rows),
            "latest_event_id": latest_graph.get("event_id") or "",
            "latest_event": latest_graph.get("event") or "",
        },
        "raw_content_visible": False,
    }
    _assert_safe_payload(result)
    return result


def refresh_runtime_tool_index(*, enabled: bool = True) -> dict[str, Any]:
    """Force the runtime tool index to pick up current built-ins/plugins."""

    if not enabled:
        return {"status": "skipped", "reason": "disabled"}
    try:
        from src.tool_index import get_tool_index, reset_tool_index

        reset_tool_index()
        tool_index = get_tool_index()
        if tool_index is None:
            return {"status": "unavailable", "reason": "tool_index_not_created"}
        tool_index.index_builtin_tools()
        tool_index.index_plugin_tools()
        return {
            "status": "ok",
            "healthy": bool(getattr(tool_index, "healthy", False)),
            "builtin_tools_indexed": len(BUILTIN_TOOL_DESCRIPTIONS),
        }
    except Exception as exc:
        return {"status": "failed", "error_class": exc.__class__.__name__, "message": _safe_text(str(exc), limit=240)}


def build_tool_capability_snapshot(
    *,
    reason: str = "manual",
    commit: str = "",
    index_status: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    tool_names = tuple(sorted(BUILTIN_TOOL_DESCRIPTIONS))
    schema_names = tuple(sorted(_schema_tool_names()))
    domains = _domain_counts(tool_names)
    snapshot: dict[str, Any] = {
        "schema": TOOL_CAPABILITY_SNAPSHOT_SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "reason": _safe_label(reason or "manual"),
        "commit": _safe_label(commit),
        "builtin_tool_count": len(tool_names),
        "schema_tool_count": len(schema_names),
        "tool_names": tool_names,
        "always_available": tuple(sorted(ALWAYS_AVAILABLE)),
        "assistant_always_available": tuple(sorted(ASSISTANT_ALWAYS_AVAILABLE)),
        "domains": domains,
        "schema_parity": {
            "missing_schema": tuple(sorted(set(tool_names) - set(schema_names))),
            "missing_description": tuple(sorted(set(schema_names) - set(tool_names))),
        },
        "index_status": dict(index_status or {"status": "unknown"}),
        "summary": _summary_lines(tool_names=tool_names, domains=domains, index_status=index_status or {}),
        "redaction_policy": {
            "stores_private_content": False,
            "stores_secrets": False,
            "stores_absolute_host_paths": False,
            "content": "tool names, descriptions categories, and registry parity only",
        },
    }
    snapshot["fingerprint"] = _fingerprint(snapshot)
    snapshot["id"] = f"tool-capabilities-{snapshot['fingerprint'][:12]}"
    _assert_safe_payload(snapshot)
    return snapshot


def build_tool_memory_records(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    _assert_snapshot(snapshot)
    records: list[dict[str, Any]] = []
    summary_text = "\n".join(
        [
            "Odysseus tool capability knowledge",
            f"Snapshot: {snapshot['id']}",
            f"Built-in tools: {snapshot['builtin_tool_count']}",
            f"Function schemas: {snapshot['schema_tool_count']}",
            *list(snapshot.get("summary") or ())[:10],
        ]
    )
    records.append(_memory_record(snapshot, chunk="summary", text=summary_text, tool_names=snapshot.get("tool_names") or ()))
    by_domain: dict[str, list[str]] = {}
    for name in snapshot.get("tool_names") or ():
        by_domain.setdefault(_tool_domain(str(name)), []).append(str(name))
    for domain, names in sorted(by_domain.items()):
        lines = [f"{name}: {_safe_text(BUILTIN_TOOL_DESCRIPTIONS.get(name, ''), limit=220)}" for name in sorted(names)]
        records.append(
            _memory_record(
                snapshot,
                chunk=f"domain-{domain}",
                text="\n".join([f"Odysseus tools for {domain}", *lines]),
                tool_names=names,
            )
        )
    return tuple(records)


def build_tool_raptorgraph_event(
    snapshot: Mapping[str, Any],
    *,
    memory_records: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    _assert_snapshot(snapshot)
    records = tuple(memory_records)
    event = {
        "schema": TOOL_CAPABILITY_RAPTORGRAPH_EVENT_SCHEMA,
        "event": "tool_capability_knowledge_refresh",
        "snapshot_id": snapshot["id"],
        "source_fingerprint": snapshot["fingerprint"],
        "commit": snapshot.get("commit") or "",
        "builtin_tool_count": snapshot.get("builtin_tool_count"),
        "schema_tool_count": snapshot.get("schema_tool_count"),
        "domains": dict(snapshot.get("domains") or {}),
        "index_status": dict(snapshot.get("index_status") or {}),
        "memory_record_ids": tuple(str(record.get("memory_id") or "") for record in records if record.get("memory_id")),
        "private_content_stored": False,
    }
    _assert_safe_payload(event)
    return event


def append_tool_capability_raptorgraph_event(
    event: Mapping[str, Any],
    *,
    root: str | Path | None = None,
) -> ToolCapabilityRaptorGraphAppendResult:
    """Append a deduped redacted RaptorGraph maintenance event."""

    normalized = normalize_tool_capability_raptorgraph_event(event)
    event_id = str(normalized["event_id"])
    base = Path(root or TOOL_CAPABILITY_RAPTORGRAPH_DIR)
    path = base / "events.jsonl"
    base.mkdir(parents=True, exist_ok=True)
    if _jsonl_contains_event(path, event_id):
        _record_tool_raptorgraph_mutation(normalized, status="duplicate", duplicate=True)
        return ToolCapabilityRaptorGraphAppendResult(
            status="duplicate",
            event_id=event_id,
            duplicate=True,
            path=str(path),
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    _record_tool_raptorgraph_mutation(normalized, status="written", duplicate=False)
    return ToolCapabilityRaptorGraphAppendResult(
        status="written",
        event_id=event_id,
        duplicate=False,
        path=str(path),
    )


def normalize_tool_capability_raptorgraph_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise ToolCapabilityMaintenanceError("raptorgraph event must be a mapping")
    source_fingerprint = _safe_hash(event.get("source_fingerprint") or event.get("source_hash"))
    memory_record_ids = _safe_id_tuple(event.get("memory_record_ids") or ())
    if not memory_record_ids:
        raise ToolCapabilityMaintenanceError("memory_record_ids are required")
    normalized = {
        "schema": TOOL_CAPABILITY_RAPTORGRAPH_EVENT_SCHEMA,
        "event_id": _tool_raptorgraph_event_id(source_fingerprint=source_fingerprint, memory_record_ids=memory_record_ids),
        "event": "tool_capability_knowledge_refresh",
        "source_provider": "tool_capability_maintenance",
        "source_fingerprint": source_fingerprint,
        "commit": _safe_label(event.get("commit") or ""),
        "snapshot_id": _safe_label(event.get("snapshot_id") or ""),
        "memory_record_ids": memory_record_ids,
        "builtin_tool_count": int(event.get("builtin_tool_count") or 0),
        "schema_tool_count": int(event.get("schema_tool_count") or 0),
        "domains": _safe_domain_counts(event.get("domains") or {}),
        "index_status": _safe_index_status(event.get("index_status") or {}),
        "classification": "system_capability",
        "local_only": False,
        "dsgvo_mode": False,
        "raw_content_stored": False,
        "raw_content_visible": False,
        "created_at": int(time.time()),
    }
    _assert_safe_payload(normalized)
    encoded = json.dumps(normalized, ensure_ascii=False)
    if len(encoded) > 5000:
        raise ToolCapabilityMaintenanceError("raptorgraph event exceeds safe length")
    return normalized


def persist_tool_capability_knowledge(
    *,
    snapshot: Mapping[str, Any],
    memory_records: Iterable[Mapping[str, Any]],
    raptorgraph_event: Mapping[str, Any],
    data_dir: str | Path | None = None,
) -> None:
    _assert_snapshot(snapshot)
    base = Path(data_dir or TOOL_CAPABILITY_KNOWLEDGE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot": dict(snapshot),
        "memory_records": [dict(record) for record in memory_records],
        "raptorgraph_event": dict(raptorgraph_event),
    }
    _assert_safe_payload(payload)
    atomic_write_json(str(base / LATEST_FILE), payload, indent=2)
    with open(base / HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def _load_latest_payload(data_dir: str | Path | None = None) -> dict[str, Any]:
    path = Path(data_dir or TOOL_CAPABILITY_KNOWLEDGE_DIR) / LATEST_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    _assert_safe_payload(payload)
    return payload


def _read_tool_raptorgraph_rows(root: str | Path | None = None) -> list[dict[str, Any]]:
    path = Path(root or TOOL_CAPABILITY_RAPTORGRAPH_DIR) / "events.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            _assert_safe_payload(row)
            rows.append(row)
    return rows


def _upsert_tool_memory_record(
    record: Mapping[str, Any],
    *,
    memory_manager: Any,
    memory_vector: Any = None,
    owner: str = "system",
) -> str:
    _assert_safe_payload(record)
    memory_id = _safe_label(record.get("memory_id") or "", field="memory_id")
    if not memory_id:
        raise ToolCapabilityMaintenanceError("memory record id is required")
    text = _safe_text(record.get("text") or "", limit=5000).strip()
    if not text:
        raise ToolCapabilityMaintenanceError("memory record text is required")
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    entry = {
        "id": memory_id,
        "text": text,
        "timestamp": int(time.time()),
        "source": _safe_label(record.get("source") or "tool_capability_maintenance"),
        "category": _safe_label(record.get("category") or "system_capability"),
        "uses": 0,
        "owner": _safe_label(owner or "system"),
        "metadata": dict(metadata),
    }
    _assert_safe_payload(entry)
    entries = list(memory_manager.load_all())
    existing_index = next((idx for idx, item in enumerate(entries) if item.get("id") == memory_id), None)
    if existing_index is not None:
        current = entries[existing_index]
        if current.get("text") == entry["text"] and current.get("metadata") == entry["metadata"] and current.get("owner") == entry["owner"]:
            return "skipped"
        entry["uses"] = int(current.get("uses") or 0)
        entries[existing_index] = entry
    else:
        entries.append(entry)
    memory_manager.save(entries)
    if memory_vector is not None:
        try:
            if existing_index is not None and hasattr(memory_vector, "remove"):
                memory_vector.remove(memory_id)
            memory_vector.add(memory_id, text)
        except Exception:
            pass
    return "written"


def _record_tool_capability_memory_write(
    intent: Mapping[str, Any],
    report: ToolCapabilityMemoryWriteReport,
    *,
    owner: str,
    confirmation_source: str,
) -> None:
    try:
        from src.memory_provenance_ledger import record_memory_provenance

        record_memory_provenance(
            "memory_maintenance",
            owner=owner,
            surface="tool_capability_maintenance",
            source="tool_capability_maintenance",
            action="execute_tool_capability_memory_write",
            status=report.status,
            reason=report.reason,
            source_hash=str(intent.get("source_fingerprint") or ""),
            memory_record_ids=tuple(record.get("memory_id") for record in (intent.get("memory_records") or ()) if isinstance(record, Mapping)),
            classification=str(intent.get("classification") or "system_capability"),
            review_required=not bool(intent.get("ready_to_write")),
            dry_run=report.dry_run,
            writes_performed=report.writes_performed,
            metadata={
                "confirmation_source": _safe_label(confirmation_source or "system_capability_policy"),
                "memory_records_planned": report.memory_records_planned,
                "memory_records_written": report.memory_records_written,
                "memory_records_skipped": report.memory_records_skipped,
                "snapshot_id": _safe_label(intent.get("snapshot_id") or ""),
            },
        )
    except Exception:
        pass


def _memory_record(snapshot: Mapping[str, Any], *, chunk: str, text: str, tool_names: Iterable[str]) -> dict[str, Any]:
    safe_chunk = _safe_label(chunk)
    safe_text = _safe_text(text, limit=5000)
    record = {
        "schema": TOOL_CAPABILITY_MEMORY_RECORD_SCHEMA,
        "memory_id": f"tool-capability-{snapshot['fingerprint'][:12]}-{safe_chunk}",
        "source": "tool_capability_maintenance",
        "category": "system_capability",
        "text": safe_text,
        "metadata": {
            "schema": TOOL_CAPABILITY_MEMORY_RECORD_SCHEMA,
            "snapshot_id": snapshot["id"],
            "source_fingerprint": snapshot["fingerprint"],
            "chunk": safe_chunk,
            "tool_names": tuple(sorted(str(name) for name in tool_names)),
            "commit": snapshot.get("commit") or "",
            "private_content_stored": False,
        },
    }
    _assert_safe_payload(record)
    return record


def _jsonl_contains_event(path: Path, event_id: str) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping) and row.get("event_id") == event_id:
                return True
    return False


def _record_tool_raptorgraph_mutation(event: Mapping[str, Any], *, status: str, duplicate: bool) -> None:
    try:
        from src.memory_provenance_ledger import record_memory_provenance

        record_memory_provenance(
            "raptorgraph_mutation",
            owner="system",
            surface="tool_capability_maintenance",
            source="tool_capability_raptorgraph_store",
            action="append_event",
            status=status,
            reason="duplicate" if duplicate else "event_appended",
            source_hash=str(event.get("source_fingerprint") or ""),
            memory_record_ids=event.get("memory_record_ids") or (),
            graph_event_id=str(event.get("event_id") or ""),
            node_count=len(tuple(event.get("memory_record_ids") or ())),
            edge_count=1 if event.get("memory_record_ids") else 0,
            dsgvo_mode=False,
            local_only=False,
            classification="system_capability",
            writes_performed=status == "written" and not duplicate,
        )
    except Exception:
        pass


def _tool_raptorgraph_event_id(*, source_fingerprint: str, memory_record_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"source_fingerprint": source_fingerprint, "memory_record_ids": memory_record_ids},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"tool-rg-{digest}"


def _safe_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _HEX_RE.fullmatch(text):
        raise ToolCapabilityMaintenanceError("source_fingerprint must be sha-like")
    return text


def _safe_id_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (tuple, list)):
        raise ToolCapabilityMaintenanceError("memory_record_ids must be a list")
    result = tuple(str(value or "").strip() for value in values)
    if not result or any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) for value in result):
        raise ToolCapabilityMaintenanceError("memory_record_ids contain unsafe values")
    return result


def _safe_domain_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    domains: dict[str, int] = {}
    for key, raw_count in value.items():
        label = _safe_label(key)
        if not label or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", label):
            continue
        try:
            count = max(0, min(int(raw_count or 0), 10000))
        except (TypeError, ValueError):
            continue
        domains[label] = count
    return domains


def _safe_index_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {"status", "reason", "error_class", "message", "healthy", "builtin_tools_indexed"}
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        raw = value.get(key)
        if isinstance(raw, bool):
            result[key] = raw
        elif isinstance(raw, int):
            result[key] = max(0, min(raw, 10000))
        else:
            result[key] = _safe_label(raw)
    return result


def _schema_tool_names() -> set[str]:
    names: set[str] = set()
    for item in FUNCTION_TOOL_SCHEMAS:
        function = item.get("function") if isinstance(item, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        if name:
            names.add(str(name))
    return names


def _summary_lines(*, tool_names: Iterable[str], domains: Mapping[str, int], index_status: Mapping[str, Any]) -> tuple[str, ...]:
    names = tuple(tool_names)
    parity = set(BUILTIN_TOOL_DESCRIPTIONS) - _schema_tool_names()
    return (
        f"Runtime tool index refresh status: {index_status.get('status', 'unknown')}.",
        f"Tool registry exposes {len(names)} described built-in tools.",
        "Key file/code tools include read_file, write_file, edit_file, grep, glob, ls, bash, and python.",
        "Autonomous coding must use sandbox-bound file and terminal capabilities with owned paths, resource limits, review gates, and no unrestricted host shell.",
        "Nextcloud writes are available only as review-gated copy-only Universal Inbox transfers with server-side Nextcloud Zugangsdaten; chat-supplied access data must never be requested, and delete, move, rename, overwrite, and broad push semantics stay blocked.",
        "Repository automation tools include manage_repos, recent_changes, and gated commit/push planning.",
        "System capability memory is generated from trusted registry metadata only, never private chat or document content.",
        f"Domains: {', '.join(f'{name}={count}' for name, count in sorted(domains.items()))}.",
        f"Tools without function schemas: {len(parity)}.",
    )


def _domain_counts(tool_names: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in tool_names:
        domain = _tool_domain(str(name))
        counts[domain] = counts.get(domain, 0) + 1
    return counts


def _tool_domain(name: str) -> str:
    if name in {"read_file", "write_file", "edit_file", "grep", "glob", "ls", "bash", "python", "get_workspace"}:
        return "filesystem_code"
    if name in {"manage_repos", "recent_changes", "manage_bg_jobs", "spawn_subagent", "manage_subagents", "delegate"}:
        return "agent_development"
    if "memory" in name or name in {"manage_skills"}:
        return "memory_skills"
    if name.startswith(("list_email", "read_email", "send_email", "reply_to_email", "archive_email", "delete_email", "mark_email", "bulk_email")):
        return "email"
    if "calendar" in name or "notes" in name or "tasks" in name or "contact" in name:
        return "personal_organizer"
    if "web" in name or "research" in name:
        return "web_research"
    if "document" in name or "image" in name or "gallery" in name:
        return "documents_media"
    if name.startswith(("manage_", "list_", "serve_", "stop_", "download_", "tail_", "adopt_", "search_hf")):
        return "admin_runtime"
    return "core_chat"


def _fingerprint(payload: Mapping[str, Any]) -> str:
    filtered = {key: value for key, value in payload.items() if key not in {"fingerprint", "id", "generated_at"}}
    raw = json.dumps(filtered, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_label(value: Any, *, field: str | None = None) -> str:
    raw = str(value or "").strip()
    raw = _SECRET_RE.sub("[redacted-secret]", raw)
    raw = _WINDOWS_PATH_RE.sub("[redacted-path]", raw)
    raw = _ABSOLUTE_PATH_RE.sub("[redacted-path]", raw)
    return re.sub(r"\s+", " ", raw)[:160]


def _safe_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    text = _ABSOLUTE_PATH_RE.sub("[redacted-path]", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _assert_snapshot(snapshot: Mapping[str, Any]) -> None:
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != TOOL_CAPABILITY_SNAPSHOT_SCHEMA:
        raise ToolCapabilityMaintenanceError("snapshot schema is invalid")
    if not snapshot.get("fingerprint") or not snapshot.get("id"):
        raise ToolCapabilityMaintenanceError("snapshot identity is incomplete")
    _assert_safe_payload(snapshot)


def _assert_safe_payload(payload: Any) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if _SECRET_RE.search(encoded):
        raise ToolCapabilityMaintenanceError("payload contains a secret-like value")
    if _WINDOWS_PATH_RE.search(encoded) or _ABSOLUTE_PATH_RE.search(encoded):
        raise ToolCapabilityMaintenanceError("payload contains an absolute host path")
