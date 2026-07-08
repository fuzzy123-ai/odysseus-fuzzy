"""Read-only alignment plan for memory, chunk, provenance and graph IDs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from src.memory_provenance_ledger import build_memory_provenance_record
from src.runtime_event_envelope import build_runtime_event, stable_payload_hash
from src.universal_inbox_raptorgraph_store import normalize_universal_inbox_raptorgraph_event


MEMORY_PROVENANCE_ALIGNMENT_SCHEMA = "odysseus.memory_provenance_alignment.v1"

_HEX_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{32,128}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_FORBIDDEN_VALUE_RE = re.compile(
    r"([A-Za-z]:[\\/]|/(home|Users|var/lib|mnt|srv)/|https?://|PRIVATE RAW TEXT|BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,})",
    re.IGNORECASE,
)


class MemoryProvenanceAlignmentError(ValueError):
    """Raised when provenance alignment evidence is unsafe or inconsistent."""


@dataclass(frozen=True)
class MemoryProvenanceAlignmentPlan:
    source_hash: str
    memory_record_ids: tuple[str, ...]
    chunk_refs: tuple[dict[str, Any], ...]
    provenance_record: dict[str, Any]
    graph_event: dict[str, Any]
    lifecycle_correlation_id: str = ""
    schema: str = MEMORY_PROVENANCE_ALIGNMENT_SCHEMA
    raw_content_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        graph_event_id = str(self.graph_event.get("event_id") or "")
        correlation_id = stable_payload_hash(
            {
                "schema": self.schema,
                "source_hash": self.source_hash,
                "memory_record_ids": self.memory_record_ids,
                "chunk_count": len(self.chunk_refs),
                "graph_event_id": graph_event_id,
            }
        )
        payload = {
            "schema": self.schema,
            "source_hash": self.source_hash,
            "memory_record_ids": self.memory_record_ids,
            "memory_record_count": len(self.memory_record_ids),
            "chunk_refs": self.chunk_refs,
            "chunk_count": len(self.chunk_refs),
            "provenance_record": self.provenance_record,
            "graph_event": self.graph_event,
            "graph_event_id": graph_event_id,
            "lifecycle_correlation_id": self.lifecycle_correlation_id,
            "alignment_status": "aligned",
            "raw_content_visible": False,
            "source_path_visible": False,
            "secret_values_visible": False,
            "runtime_event": build_runtime_event(
                surface="memory",
                component="provenance_alignment",
                event_type="memory_provenance_alignment",
                status="queued",
                severity="info",
                correlation_id=correlation_id,
                privacy_level="private_metadata",
                raw_content_visible=False,
                side_effects=("none",),
                metadata={
                    "memory_record_count": len(self.memory_record_ids),
                    "chunk_count": len(self.chunk_refs),
                    "graph_event_present": bool(graph_event_id),
                },
            ),
            "correlation_id": correlation_id,
        }
        _reject_forbidden_payload(payload)
        return payload


def build_memory_provenance_alignment_plan(
    *,
    source_hash: str,
    memory_record_ids: Iterable[Any],
    chunk_metadata: Iterable[Mapping[str, Any]] | None = None,
    provenance_event: Mapping[str, Any] | None = None,
    graph_event: Mapping[str, Any] | None = None,
    lifecycle_state: Mapping[str, Any] | None = None,
) -> MemoryProvenanceAlignmentPlan:
    """Build a redacted ID alignment plan without writing any stores."""

    normalized_source_hash = _source_hash(source_hash)
    ids = _memory_ids(memory_record_ids)
    chunks = tuple(_chunk_ref(item, source_hash=normalized_source_hash) for item in (chunk_metadata or ()))
    graph = _graph_event(
        graph_event or {},
        source_hash=normalized_source_hash,
        memory_record_ids=ids,
    )
    provenance = _provenance_record(
        provenance_event or {},
        source_hash=normalized_source_hash,
        memory_record_ids=ids,
        graph_event_id=str(graph.get("event_id") or ""),
    )
    lifecycle_correlation_id = _lifecycle_correlation_id(lifecycle_state or {})
    return MemoryProvenanceAlignmentPlan(
        source_hash=normalized_source_hash,
        memory_record_ids=ids,
        chunk_refs=chunks,
        provenance_record=provenance,
        graph_event=graph,
        lifecycle_correlation_id=lifecycle_correlation_id,
    )


def _chunk_ref(item: Mapping[str, Any], *, source_hash: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise MemoryProvenanceAlignmentError("chunk metadata must be mappings")
    item_source_hash = _source_hash(item.get("source_hash") or source_hash)
    if item_source_hash != source_hash:
        raise MemoryProvenanceAlignmentError("chunk source_hash does not match alignment source_hash")
    chunk_index = _safe_int(item.get("chunk_index"), field="chunk_index")
    char_start = _safe_int(item.get("char_start"), field="char_start")
    char_end = _safe_int(item.get("char_end"), field="char_end")
    token_start = _safe_int(item.get("token_start_est"), field="token_start_est")
    token_end = _safe_int(item.get("token_end_est"), field="token_end_est")
    splitter_version = _safe_text(item.get("splitter_version") or "unknown", field="splitter_version")
    section_hash = stable_payload_hash({"section_path": item.get("section_path") or ""})
    chunk_id = "memchunk-" + stable_payload_hash(
        {
            "source_hash": source_hash,
            "splitter_version": splitter_version,
            "chunk_index": chunk_index,
            "char_start": char_start,
            "char_end": char_end,
        }
    ).removeprefix("sha256:")[:24]
    return {
        "chunk_id": chunk_id,
        "source_hash": source_hash,
        "chunk_index": chunk_index,
        "splitter_version": splitter_version,
        "section_ref_hash": section_hash,
        "page_start": _safe_int(item.get("page_start"), field="page_start"),
        "page_end": _safe_int(item.get("page_end"), field="page_end"),
        "char_start": char_start,
        "char_end": char_end,
        "token_start_est": token_start,
        "token_end_est": token_end,
        "raw_content_visible": False,
    }


def _graph_event(
    event: Mapping[str, Any],
    *,
    source_hash: str,
    memory_record_ids: tuple[str, ...],
) -> dict[str, Any]:
    payload = dict(event or {})
    if payload.get("source_hash") and _source_hash(payload.get("source_hash")) != source_hash:
        raise MemoryProvenanceAlignmentError("graph source_hash does not match alignment source_hash")
    payload["source_hash"] = source_hash
    payload["memory_record_ids"] = tuple(payload.get("memory_record_ids") or memory_record_ids)
    normalized = normalize_universal_inbox_raptorgraph_event(payload)
    if tuple(normalized.get("memory_record_ids") or ()) != memory_record_ids:
        raise MemoryProvenanceAlignmentError("graph memory_record_ids do not match alignment ids")
    return normalized


def _provenance_record(
    event: Mapping[str, Any],
    *,
    source_hash: str,
    memory_record_ids: tuple[str, ...],
    graph_event_id: str,
) -> dict[str, Any]:
    if event.get("source_hash") and _source_hash(event.get("source_hash")) != source_hash:
        raise MemoryProvenanceAlignmentError("provenance source_hash does not match alignment source_hash")
    event_ids = tuple(event.get("memory_record_ids") or memory_record_ids)
    if _memory_ids(event_ids) != memory_record_ids:
        raise MemoryProvenanceAlignmentError("provenance memory_record_ids do not match alignment ids")
    return build_memory_provenance_record(
        str(event.get("event_type") or "memory_write_intent"),
        owner=str(event.get("owner") or "unknown"),
        surface=str(event.get("surface") or "memory_lifecycle"),
        source=str(event.get("source") or "memory_lifecycle"),
        action=str(event.get("action") or "align_provenance"),
        status=str(event.get("status") or "dry_run"),
        reason=str(event.get("reason") or "memory_provenance_alignment"),
        source_hash=source_hash,
        memory_record_ids=memory_record_ids,
        graph_event_id=graph_event_id,
        dry_run=True,
        writes_performed=False,
        metadata={
            "alignment_schema": MEMORY_PROVENANCE_ALIGNMENT_SCHEMA,
            "raw_content_visible": False,
        },
    )


def _lifecycle_correlation_id(state: Mapping[str, Any]) -> str:
    if not state:
        return ""
    value = str(state.get("correlation_id") or "")
    if not value:
        return ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise MemoryProvenanceAlignmentError("lifecycle correlation_id is invalid")
    return value


def _source_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _HEX_HASH_RE.fullmatch(text):
        raise MemoryProvenanceAlignmentError("source_hash must be sha256-like")
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _memory_ids(values: Iterable[Any]) -> tuple[str, ...]:
    ids = tuple(str(value or "").strip() for value in values or ())
    if not ids:
        raise MemoryProvenanceAlignmentError("memory_record_ids must not be empty")
    if any(not _SAFE_ID_RE.fullmatch(value) for value in ids):
        raise MemoryProvenanceAlignmentError("memory_record_ids contain unsafe values")
    return ids


def _safe_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise MemoryProvenanceAlignmentError(f"{field} must be an int") from exc
    if parsed < 0:
        raise MemoryProvenanceAlignmentError(f"{field} must be >= 0")
    return parsed


def _safe_text(value: Any, *, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise MemoryProvenanceAlignmentError(f"{field} must not be empty")
    if _FORBIDDEN_VALUE_RE.search(text):
        raise MemoryProvenanceAlignmentError(f"{field} contains unsafe material")
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,120}", text):
        raise MemoryProvenanceAlignmentError(f"{field} is invalid")
    return text


def _reject_forbidden_payload(value: Any) -> None:
    encoded = repr(value)
    if _FORBIDDEN_VALUE_RE.search(encoded):
        raise MemoryProvenanceAlignmentError("alignment payload contains unsafe material")
