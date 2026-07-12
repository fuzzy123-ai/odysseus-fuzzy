"""Canonical redacted lifecycle state for Memory and RaptorGraph flows.

This module is a repo-only contract layer. It builds status payloads from
already-produced dry-run, intent, provenance, graph and diagnostics data. It
does not write memories, mutate graph stores, reindex, rebuild or migrate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from src.runtime_event_envelope import build_runtime_event, stable_payload_hash


MEMORY_LIFECYCLE_SCHEMA = "odysseus.memory_lifecycle.v1"
CANONICAL_MEMORY_LIFECYCLE_STAGES = (
    "source_metadata",
    "extracted_abstraction",
    "policy_review",
    "memory_write_intent",
    "memory_record",
    "provenance_event",
    "graph_event",
    "diagnostics_budget",
    "rebuild_dry_run",
)
MEMORY_LIFECYCLE_STATUSES = (
    "pending",
    "completed",
    "review",
    "blocked",
    "dry_run_ready",
    "written",
    "duplicate",
)

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_SOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_HEX_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{32,128}$")
_FORBIDDEN_KEY_PATTERNS = (
    "raw",
    "content",
    "body",
    "text",
    "ocr",
    "path",
    "filename",
    "file_name",
    "webdav",
    "url",
    "chat_id",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "cookie",
)
_FORBIDDEN_EXACT_KEYS = frozenset({"bytes", "raw_bytes", "file_bytes"})
_FORBIDDEN_VALUE_RE = re.compile(
    r"([A-Za-z]:[\\/]|/(home|Users|var/lib|mnt|srv)/|https?://|PRIVATE RAW TEXT|BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,})",
    re.IGNORECASE,
)


class MemoryLifecycleError(ValueError):
    """Raised when a memory lifecycle payload would be invalid or unsafe."""


@dataclass(frozen=True)
class MemoryLifecycleStage:
    stage: str
    status: str
    reason_codes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metadata, blocked_field_count = _sanitize_mapping(self.metadata)
        payload: dict[str, Any] = {
            "stage": _stage(self.stage),
            "status": _status(self.status),
            "reason_codes": tuple(_safe_token(value, field="reason_code") for value in self.reason_codes),
            "metadata": metadata,
        }
        if blocked_field_count:
            payload["blocked_field_count"] = blocked_field_count
        return payload


@dataclass(frozen=True)
class MemoryLifecycleState:
    source_hash: str
    source_kind: str
    stages: tuple[MemoryLifecycleStage, ...]
    live_reindex_allowed: bool = False
    storage_migration_allowed: bool = False
    schema: str = MEMORY_LIFECYCLE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        stages = tuple(stage.to_dict() for stage in self.stages)
        blocked_field_count = sum(int(stage.get("blocked_field_count") or 0) for stage in stages)
        overall_status = _overall_status(stages)
        correlation_id = stable_payload_hash(
            {
                "schema": self.schema,
                "source_hash": self.source_hash,
                "overall_status": overall_status,
            }
        )
        payload: dict[str, Any] = {
            "schema": self.schema,
            "source_kind": _source_kind(self.source_kind),
            "source_hash": _source_hash(self.source_hash),
            "source_ref_visible": False,
            "source_path_visible": False,
            "raw_content_visible": False,
            "secret_values_visible": False,
            "chat_id_visible": False,
            "live_reindex_allowed": bool(self.live_reindex_allowed),
            "storage_migration_allowed": bool(self.storage_migration_allowed),
            "overall_status": overall_status,
            "next_action": _next_action(
                overall_status,
                stages,
                live_reindex_allowed=self.live_reindex_allowed,
                storage_migration_allowed=self.storage_migration_allowed,
            ),
            "stages": stages,
            "correlation_id": correlation_id,
            "runtime_event": _runtime_event(
                correlation_id=correlation_id,
                overall_status=overall_status,
                blocked_field_count=blocked_field_count,
                live_reindex_allowed=self.live_reindex_allowed,
                storage_migration_allowed=self.storage_migration_allowed,
            ),
        }
        if blocked_field_count:
            payload["blocked_field_count"] = blocked_field_count
        return payload


def build_memory_lifecycle_state(
    *,
    source_ref: str = "",
    source_hash: str = "",
    source_kind: str = "unknown",
    source_metadata: Mapping[str, Any] | None = None,
    extracted_abstraction: Mapping[str, Any] | None = None,
    policy_review: Mapping[str, Any] | None = None,
    memory_write_intent: Mapping[str, Any] | None = None,
    memory_record: Mapping[str, Any] | None = None,
    provenance_event: Mapping[str, Any] | None = None,
    graph_event: Mapping[str, Any] | None = None,
    diagnostics_budget: Mapping[str, Any] | None = None,
    rebuild_dry_run: Mapping[str, Any] | None = None,
    live_reindex_allowed: bool = False,
    storage_migration_allowed: bool = False,
) -> MemoryLifecycleState:
    """Build a redacted canonical lifecycle state without side effects."""

    metadata = _mapping(source_metadata)
    abstraction = _mapping(extracted_abstraction)
    policy = _mapping(policy_review)
    intent = _mapping(memory_write_intent)
    record = _mapping(memory_record)
    provenance = _mapping(provenance_event)
    graph = _mapping(graph_event)
    diagnostics = _mapping(diagnostics_budget)
    rebuild = _mapping(rebuild_dry_run)

    normalized_hash = _source_hash(
        source_hash
        or metadata.get("source_hash")
        or abstraction.get("source_hash")
        or intent.get("source_hash")
        or _hash_from_record(record)
        or graph.get("source_hash")
        or stable_payload_hash({"source_ref": source_ref, "source_kind": source_kind})
    )
    normalized_kind = _source_kind(
        source_kind
        or metadata.get("source_kind")
        or metadata.get("source")
        or intent.get("source")
        or "unknown"
    )

    stages = (
        MemoryLifecycleStage(
            "source_metadata",
            _source_metadata_status(metadata, source_ref=source_ref, source_hash=normalized_hash),
            metadata={
                "source_kind": normalized_kind,
                "classification": metadata.get("classification"),
                "privacy_level": metadata.get("privacy_level"),
                "local_only": metadata.get("local_only"),
                "dsgvo_mode": metadata.get("dsgvo_mode"),
                "source_payload": metadata,
            },
        ),
        MemoryLifecycleStage(
            "extracted_abstraction",
            _payload_status(abstraction, fallback="pending"),
            reason_codes=_reason_codes(abstraction.get("review_reasons") or abstraction.get("reason")),
            metadata={
                "summary_available": bool(abstraction.get("summary") or abstraction.get("abstract")),
                "source_material_stored": bool(abstraction.get("source_material_stored")),
                "blocked_field_count": abstraction.get("blocked_field_count"),
                "abstraction_payload": abstraction,
            },
        ),
        MemoryLifecycleStage(
            "policy_review",
            _policy_status(policy, intent),
            reason_codes=_reason_codes(
                _extend(
                    policy.get("review_reasons"),
                    policy.get("no_go_reasons"),
                    intent.get("review_reasons"),
                    intent.get("no_go_reasons"),
                    intent.get("reason") if str(intent.get("status") or "") in {"review", "blocked"} else (),
                )
            ),
            metadata={
                "classification": policy.get("classification") or intent.get("classification"),
                "memory_write_allowed": policy.get("memory_write_allowed"),
                "raptor_write_allowed": policy.get("raptor_write_allowed"),
                "review_required": policy.get("review_required"),
                "policy_payload": policy,
            },
        ),
        MemoryLifecycleStage(
            "memory_write_intent",
            _intent_status(intent),
            reason_codes=_reason_codes(intent.get("reason")),
            metadata={
                "dry_run": intent.get("dry_run", True) if intent else None,
                "writes_performed": bool(intent.get("writes_performed")),
                "ready_to_write": bool(intent.get("ready_to_write")),
                "memory_record_count": _count(intent.get("memory_records")),
                "intent_payload": intent,
            },
        ),
        MemoryLifecycleStage(
            "memory_record",
            _memory_record_status(record, intent),
            metadata={
                "memory_record_count": _memory_record_count(record, intent),
                "category": record.get("category"),
                "source": record.get("source"),
                "writes_performed": bool(record.get("writes_performed") or intent.get("writes_performed")),
                "record_payload": record,
            },
        ),
        MemoryLifecycleStage(
            "provenance_event",
            _provenance_status(provenance),
            reason_codes=_reason_codes(provenance.get("reason")),
            metadata={
                "event_type": provenance.get("event_type"),
                "status": provenance.get("status"),
                "memory_record_count": provenance.get("memory_record_count"),
                "dry_run": provenance.get("dry_run"),
                "writes_performed": provenance.get("writes_performed"),
                "provenance_payload": provenance,
            },
        ),
        MemoryLifecycleStage(
            "graph_event",
            _graph_status(graph, intent),
            reason_codes=_reason_codes(_extend(graph.get("review_reasons"), graph.get("no_go_reasons"))),
            metadata={
                "event": graph.get("event") or _nested_event(intent),
                "node_count": graph.get("node_count"),
                "edge_count": graph.get("edge_count"),
                "duplicate": graph.get("duplicate"),
                "raw_content_stored": False,
                "graph_payload": graph,
            },
        ),
        MemoryLifecycleStage(
            "diagnostics_budget",
            _diagnostics_status(diagnostics),
            reason_codes=_reason_codes(diagnostics.get("gaps") or diagnostics.get("readiness_gap_names")),
            metadata={
                "ready": diagnostics.get("ready"),
                "gap_count": diagnostics.get("gap_count"),
                "budget_family_count": diagnostics.get("budget_family_count"),
                "max_nodes": diagnostics.get("max_nodes"),
                "max_edges": diagnostics.get("max_edges"),
                "max_depth": diagnostics.get("max_depth"),
                "diagnostics_payload": diagnostics,
            },
        ),
        MemoryLifecycleStage(
            "rebuild_dry_run",
            _rebuild_status(rebuild),
            reason_codes=_reason_codes(rebuild.get("reasons") or rebuild.get("reason")),
            metadata={
                "dry_run": rebuild.get("dry_run", True) if rebuild else None,
                "rollback_available": rebuild.get("rollback_available"),
                "before_count": rebuild.get("before_count"),
                "after_count": rebuild.get("after_count"),
                "writes_performed": bool(rebuild.get("writes_performed")),
                "rebuild_payload": rebuild,
            },
        ),
    )
    return MemoryLifecycleState(
        source_hash=normalized_hash,
        source_kind=normalized_kind,
        stages=stages,
        live_reindex_allowed=live_reindex_allowed,
        storage_migration_allowed=storage_migration_allowed,
    )


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_metadata_status(metadata: Mapping[str, Any], *, source_ref: str, source_hash: str) -> str:
    if metadata or source_ref or source_hash:
        return "completed"
    return "pending"


def _policy_status(policy: Mapping[str, Any], intent: Mapping[str, Any]) -> str:
    status = str(policy.get("status") or intent.get("status") or "")
    if status in {"no_go", "failed", "blocked"} or policy.get("no_go_reasons"):
        return "blocked"
    if status in {"review", "needs_review"} or policy.get("review_reasons"):
        return "review"
    if status in {"go", "ready", "completed"} or bool(policy.get("memory_write_allowed")):
        return "completed"
    return "pending"


def _intent_status(intent: Mapping[str, Any]) -> str:
    status = str(intent.get("status") or "")
    if not intent:
        return "pending"
    if status in {"blocked", "no_go", "failed"}:
        return "blocked"
    if status in {"review", "needs_review"}:
        return "review"
    if bool(intent.get("writes_performed")):
        return "written"
    if status in {"ready", "completed", "go"} or bool(intent.get("ready_to_write")):
        return "dry_run_ready"
    return _payload_status(intent, fallback="pending")


def _memory_record_status(record: Mapping[str, Any], intent: Mapping[str, Any]) -> str:
    status = str(record.get("status") or "")
    if status in {"written", "duplicate"}:
        return status
    if bool(record.get("writes_performed") or intent.get("writes_performed")):
        return "written"
    if record or _count(intent.get("memory_records")):
        return "dry_run_ready"
    return "pending"


def _provenance_status(provenance: Mapping[str, Any]) -> str:
    status = str(provenance.get("status") or "")
    if status in {"written", "duplicate"}:
        return status
    if status in {"blocked", "failed", "no_go"}:
        return "blocked"
    if provenance:
        return "completed"
    return "pending"


def _graph_status(graph: Mapping[str, Any], intent: Mapping[str, Any]) -> str:
    status = str(graph.get("status") or "")
    if status in {"written", "duplicate"}:
        return status
    if status in {"blocked", "failed", "no_go"}:
        return "blocked"
    if graph:
        return "completed" if bool(graph.get("writes_performed")) else "dry_run_ready"
    raptor = intent.get("raptorgraph_event")
    if isinstance(raptor, Mapping) and raptor:
        return "dry_run_ready"
    return "pending"


def _diagnostics_status(diagnostics: Mapping[str, Any]) -> str:
    if not diagnostics:
        return "pending"
    if diagnostics.get("ready") is False or int(diagnostics.get("gap_count") or 0) > 0:
        return "review"
    return "completed"


def _rebuild_status(rebuild: Mapping[str, Any]) -> str:
    if not rebuild:
        return "pending"
    status = str(rebuild.get("status") or "")
    if status in {"blocked", "failed", "no_go"}:
        return "blocked"
    if status in {"review", "needs_review"}:
        return "review"
    if bool(rebuild.get("writes_performed")):
        return "written"
    return "dry_run_ready"


def _payload_status(payload: Mapping[str, Any], *, fallback: str) -> str:
    if not payload:
        return fallback
    status = str(payload.get("status") or fallback)
    if status in {"ready", "go", "ok", "success"}:
        return "completed"
    if status in {"needs_review", "review_required"}:
        return "review"
    if status in {"no_go", "failed"}:
        return "blocked"
    return _status(status)


def _overall_status(stages: tuple[Mapping[str, Any], ...]) -> str:
    statuses = {str(stage.get("status") or "") for stage in stages}
    if "blocked" in statuses:
        return "blocked"
    if "review" in statuses:
        return "review"
    if "pending" in statuses:
        return "partial"
    return "ready"


def _next_action(
    overall_status: str,
    stages: tuple[Mapping[str, Any], ...],
    *,
    live_reindex_allowed: bool,
    storage_migration_allowed: bool,
) -> str:
    if overall_status == "blocked":
        return "fix_blocker"
    if overall_status == "review":
        return "operator_review"
    for stage in stages:
        if stage.get("status") == "pending":
            return str(stage.get("stage") or "continue")
    if not live_reindex_allowed:
        return "hold_for_reindex_go"
    if not storage_migration_allowed:
        return "hold_for_storage_migration_go"
    return "ready_for_bounded_live_action"


def _runtime_event(
    *,
    correlation_id: str,
    overall_status: str,
    blocked_field_count: int,
    live_reindex_allowed: bool,
    storage_migration_allowed: bool,
) -> dict[str, Any]:
    event_status = {
        "ready": "queued",
        "review": "warn",
        "blocked": "blocked",
        "partial": "unknown",
    }.get(overall_status, "unknown")
    return build_runtime_event(
        surface="memory",
        component="lifecycle",
        event_type="memory_lifecycle_state",
        status=event_status,
        severity="warn" if overall_status in {"review", "blocked"} else "info",
        correlation_id=correlation_id,
        privacy_level="private_metadata",
        raw_content_visible=False,
        side_effects=("none",),
        metadata={
            "overall_status": overall_status,
            "blocked_field_count": blocked_field_count,
            "live_reindex_allowed": bool(live_reindex_allowed),
            "storage_migration_allowed": bool(storage_migration_allowed),
        },
    )


def _sanitize_mapping(payload: Mapping[str, Any] | Any) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, Mapping):
        return {}, 0
    sanitized: dict[str, Any] = {}
    blocked_field_count = 0
    for key, value in payload.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            blocked_field_count += 1
            continue
        clean_value, nested_blocked = _sanitize_value(value)
        sanitized[key_text] = clean_value
        blocked_field_count += nested_blocked
    return sanitized, blocked_field_count


def _sanitize_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (tuple, list)):
        sanitized = []
        blocked_field_count = 0
        for item in value:
            clean_item, nested_blocked = _sanitize_value(item)
            sanitized.append(clean_item)
            blocked_field_count += nested_blocked
        return tuple(sanitized), blocked_field_count
    if isinstance(value, str):
        if _FORBIDDEN_VALUE_RE.search(value):
            return stable_payload_hash({"redacted_value": value}), 1
        return value, 0
    if isinstance(value, (int, float, bool)) or value is None:
        return value, 0
    return str(value), 0


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in _FORBIDDEN_EXACT_KEYS or any(
        pattern in normalized for pattern in _FORBIDDEN_KEY_PATTERNS
    )


def _memory_record_count(record: Mapping[str, Any], intent: Mapping[str, Any]) -> int:
    if record:
        return 1
    return _count(intent.get("memory_records"))


def _hash_from_record(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    return str(metadata.get("source_hash") or "")


def _nested_event(intent: Mapping[str, Any]) -> str:
    graph = intent.get("raptorgraph_event")
    if isinstance(graph, Mapping):
        return str(graph.get("event") or "")
    return ""


def _reason_codes(values: Any) -> tuple[str, ...]:
    result = []
    seen = set()
    for value in _extend(values):
        token = _safe_token(value, field="reason_code")
        if token not in seen:
            seen.add(token)
            result.append(token)
    return tuple(result)


def _extend(*values: Any) -> tuple[Any, ...]:
    output: list[Any] = []
    for value in values:
        if isinstance(value, str):
            output.append(value)
        elif isinstance(value, (tuple, list)):
            output.extend(value)
        elif value:
            output.append(value)
    return tuple(output)


def _count(values: Any) -> int:
    if isinstance(values, (tuple, list)):
        return len(values)
    return 0


def _source_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise MemoryLifecycleError("source_hash must not be empty")
    if text.startswith("sha256:") and _HEX_HASH_RE.fullmatch(text):
        return text
    if _HEX_HASH_RE.fullmatch(text):
        return f"sha256:{text.removeprefix('sha256:')}"
    raise MemoryLifecycleError("source_hash must be sha256-like")


def _source_kind(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    if not _SAFE_SOURCE_KIND_RE.fullmatch(text):
        return "unknown"
    return text


def _stage(value: Any) -> str:
    token = _safe_token(value, field="stage")
    if token not in CANONICAL_MEMORY_LIFECYCLE_STAGES:
        raise MemoryLifecycleError("stage is not part of the canonical memory lifecycle")
    return token


def _status(value: Any) -> str:
    token = _safe_token(value, field="status")
    if token not in MEMORY_LIFECYCLE_STATUSES:
        raise MemoryLifecycleError("status is not part of the canonical memory lifecycle")
    return token


def _safe_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise MemoryLifecycleError(f"{field} must be a safe token")
    return token
