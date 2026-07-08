"""Canonical Universal Inbox flow state.

This module is an additive, metadata-only contract for operator status views.
It accepts already-produced dry-run/status payloads and never performs
Nextcloud, filesystem, Telegram, provider, memory, or graph writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from src.runtime_event_envelope import build_runtime_event, stable_payload_hash
from src.universal_inbox_review_reasons import (
    normalize_universal_inbox_review_reasons,
    universal_inbox_review_reason_dicts,
)


FLOW_STATE_SCHEMA = "odysseus.universal_inbox.flow_state.v1"
CANONICAL_FLOW_STAGES = (
    "received",
    "classified",
    "extracted",
    "abstracted",
    "reviewed",
    "routed",
    "copied_exported",
    "memory_intent",
    "graph_provenance",
)

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_SOURCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_SENSITIVE_KEY_PATTERNS = (
    "raw_text",
    "content",
    "body",
    "payload",
    "ocr_dump",
    "full_text",
    "email_body",
    "raw_body",
    "message_body",
    "document_text",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "chat_id",
    "path",
    "filename",
    "file_name",
    "webdav",
    "url",
)
_SENSITIVE_EXACT_KEYS = frozenset({"bytes", "raw_bytes", "file_bytes"})


class UniversalInboxFlowStateError(ValueError):
    """Raised when a flow state payload would be invalid or unsafe."""


@dataclass(frozen=True)
class UniversalInboxFlowStep:
    stage: str
    status: str
    reason_codes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        metadata, blocked_count = _sanitize_mapping(self.metadata)
        payload: dict[str, Any] = {
            "stage": _token(self.stage, field="stage"),
            "status": _token(self.status, field="status"),
            "reason_codes": self.reason_codes,
            "metadata": metadata,
        }
        if blocked_count:
            payload["blocked_field_count"] = blocked_count
        return payload


@dataclass(frozen=True)
class UniversalInboxFlowState:
    source_ref: str
    source_kind: str
    steps: tuple[UniversalInboxFlowStep, ...]
    review_reasons: tuple[str, ...] = ()
    no_go_reasons: tuple[str, ...] = ()
    live_write_allowed: bool = False
    schema: str = FLOW_STATE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        steps = tuple(step.to_dict() for step in self.steps)
        blocked_field_count = sum(int(step.get("blocked_field_count") or 0) for step in steps)
        overall_status = _overall_status(steps, self.review_reasons, self.no_go_reasons)
        source_ref_hash = stable_payload_hash({"source_ref": self.source_ref})
        correlation_id = stable_payload_hash(
            {
                "schema": self.schema,
                "source": source_ref_hash,
                "overall_status": overall_status,
            }
        )
        payload: dict[str, Any] = {
            "schema": self.schema,
            "source_kind": _source_kind(self.source_kind),
            "source_ref_hash": source_ref_hash,
            "source_ref_visible": False,
            "source_path_visible": False,
            "raw_content_visible": False,
            "secret_values_visible": False,
            "chat_id_visible": False,
            "live_write_allowed": bool(self.live_write_allowed),
            "overall_status": overall_status,
            "next_action": _next_action(overall_status, steps, self.live_write_allowed),
            "review_reasons": self.review_reasons,
            "no_go_reasons": self.no_go_reasons,
            "review_reason_details": universal_inbox_review_reason_dicts(
                self.review_reasons,
                no_go_reasons=self.no_go_reasons,
            ),
            "no_go_reason_details": universal_inbox_review_reason_dicts(
                self.no_go_reasons,
                no_go_reasons=self.no_go_reasons,
            ),
            "steps": steps,
            "correlation_id": correlation_id,
            "runtime_event": _runtime_event(
                correlation_id=correlation_id,
                overall_status=overall_status,
                review_reasons=self.review_reasons,
                no_go_reasons=self.no_go_reasons,
                blocked_field_count=blocked_field_count,
                live_write_allowed=self.live_write_allowed,
            ),
        }
        if blocked_field_count:
            payload["blocked_field_count"] = blocked_field_count
        return payload


def build_universal_inbox_flow_state(
    *,
    source_ref: str,
    item_status: Mapping[str, Any] | None = None,
    pipeline_run: Mapping[str, Any] | None = None,
    nextcloud_report: Mapping[str, Any] | None = None,
    copy_result: Mapping[str, Any] | None = None,
    memory_intent: Mapping[str, Any] | None = None,
    graph_event: Mapping[str, Any] | None = None,
    live_write_allowed: bool = False,
) -> UniversalInboxFlowState:
    """Build a redacted canonical flow state from existing dry-run payloads."""

    source = str(source_ref or "").strip()
    if not source:
        raise UniversalInboxFlowStateError("source_ref must not be empty")
    item = _mapping(item_status)
    pipeline = _mapping(pipeline_run)
    report = _mapping(nextcloud_report)
    copy = _mapping(copy_result)
    intent = _mapping(memory_intent)
    graph = _mapping(graph_event)

    pipeline_stages = pipeline.get("stages") if isinstance(pipeline.get("stages"), Mapping) else {}
    policy_gate = pipeline.get("policy_gate") if isinstance(pipeline.get("policy_gate"), Mapping) else {}
    routing_decision = pipeline.get("routing_decision") if isinstance(pipeline.get("routing_decision"), Mapping) else {}
    memory_event = (
        pipeline.get("memory_abstraction_event")
        if isinstance(pipeline.get("memory_abstraction_event"), Mapping)
        else {}
    )

    review_reasons = _reason_codes(
        _extend(
            item.get("reason_codes"),
            pipeline.get("review_reasons"),
            policy_gate.get("review_reasons"),
            intent.get("review_reasons"),
            (intent.get("reason"),) if str(intent.get("status") or "") == "review" else (),
        )
    )
    no_go_reasons = _reason_codes(
        _extend(
            pipeline.get("no_go_reasons"),
            policy_gate.get("no_go_reasons"),
            intent.get("no_go_reasons"),
            (intent.get("reason"),) if str(intent.get("status") or "") == "blocked" else (),
        )
    )
    source_kind = _source_kind_from_ref(source, item)

    steps = (
        UniversalInboxFlowStep(
            "received",
            _received_status(item, report),
            metadata={
                "source_kind": source_kind,
                "inventory_total": report.get("inventory_total"),
                "size_bytes": item.get("size_bytes"),
            },
        ),
        UniversalInboxFlowStep(
            "classified",
            _classified_status(item, report),
            reason_codes=_reason_codes(item.get("reason_codes")),
            metadata={
                "family": item.get("family"),
                "category": item.get("category"),
                "extractable_now": item.get("extractable_now"),
                "review_required": item.get("review_required"),
                "by_file_category": report.get("by_file_category"),
                "by_privacy_class": report.get("by_privacy_class"),
            },
        ),
        _pipeline_step("extracted", pipeline_stages.get("extraction")),
        _flow_step_from_payload(
            "abstracted",
            pipeline_stages.get("memory_abstraction") or memory_event,
            fallback_status="pending",
            metadata={
                "memory_event": memory_event.get("event"),
                "blocked_field_count": memory_event.get("blocked_field_count"),
            },
        ),
        UniversalInboxFlowStep(
            "reviewed",
            _reviewed_status(policy_gate, review_reasons, no_go_reasons),
            reason_codes=_reason_codes(_extend(review_reasons, no_go_reasons)),
            metadata={
                "policy_gate": policy_gate.get("status"),
                "review_required": bool(review_reasons),
                "no_go": bool(no_go_reasons),
            },
        ),
        _flow_step_from_payload(
            "routed",
            pipeline_stages.get("routing") or routing_decision,
            fallback_status="pending",
            reason_codes=_reason_codes(routing_decision.get("review_reasons")),
            metadata={
                "decision": routing_decision.get("decision"),
                "safe_operation": routing_decision.get("safe_operation"),
                "copy_only": routing_decision.get("copy_only", True),
                "delete_original": routing_decision.get("delete_original", False),
            },
        ),
        UniversalInboxFlowStep(
            "copied_exported",
            _copy_status(copy, report),
            reason_codes=_reason_codes(copy.get("reasons") or copy.get("reason")),
            metadata={
                "dry_run": True,
                "document_candidates": report.get("document_candidates"),
                "metadata_only_candidates": report.get("metadata_only_candidates"),
                "review_candidates": report.get("review_candidates"),
                "copy_only": copy.get("copy_only", True),
                "writes_performed": bool(copy.get("writes_performed")),
            },
        ),
        _flow_step_from_payload(
            "memory_intent",
            intent,
            fallback_status="pending",
            reason_codes=_reason_codes(intent.get("reason")),
            metadata={
                "ready_to_write": intent.get("ready_to_write"),
                "dry_run": intent.get("dry_run", True),
                "writes_performed": intent.get("writes_performed", False),
                "memory_records_planned": _count(intent.get("memory_records")),
            },
        ),
        _flow_step_from_payload(
            "graph_provenance",
            _graph_payload(graph, intent),
            fallback_status="pending",
            metadata={
                "event": (graph or {}).get("event") if graph else "",
                "dry_run": True,
                "raw_content_stored": False,
            },
        ),
    )
    return UniversalInboxFlowState(
        source_ref=source,
        source_kind=source_kind,
        steps=steps,
        review_reasons=review_reasons,
        no_go_reasons=no_go_reasons,
        live_write_allowed=live_write_allowed,
    )


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pipeline_step(stage: str, payload: Any) -> UniversalInboxFlowStep:
    return _flow_step_from_payload(stage, payload, fallback_status="pending")


def _flow_step_from_payload(
    stage: str,
    payload: Any,
    *,
    fallback_status: str,
    reason_codes: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> UniversalInboxFlowStep:
    data = payload if isinstance(payload, Mapping) else {}
    return UniversalInboxFlowStep(
        stage=stage,
        status=_status_from_payload(data, fallback=fallback_status),
        reason_codes=reason_codes or _reason_codes(data.get("reasons") or data.get("reason_codes")),
        metadata={**dict(data.get("metadata") or {}), **dict(metadata or {})},
    )


def _received_status(item: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    if item or int(report.get("inventory_total") or 0) > 0:
        return "completed"
    return "pending"


def _classified_status(item: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    status = str(item.get("status") or "")
    if status in {"blocked", "unsupported"}:
        return status
    if bool(item.get("review_required")) or int(report.get("review_candidates") or 0) > 0:
        return "review"
    if item.get("category") or report.get("by_file_category"):
        return "completed"
    return "pending"


def _reviewed_status(
    policy_gate: Mapping[str, Any],
    review_reasons: tuple[str, ...],
    no_go_reasons: tuple[str, ...],
) -> str:
    gate_status = str(policy_gate.get("status") or "")
    if gate_status == "no_go" or no_go_reasons:
        return "blocked"
    if gate_status == "review" or review_reasons:
        return "review"
    if gate_status == "go":
        return "completed"
    return "pending"


def _copy_status(copy: Mapping[str, Any], report: Mapping[str, Any]) -> str:
    status = str(copy.get("status") or "")
    if status:
        normalized = _public_status(status)
        return "completed" if normalized == "exported" else normalized
    if report:
        return "dry_run_ready"
    return "pending"


def _status_from_payload(payload: Mapping[str, Any], *, fallback: str) -> str:
    return _public_status(str(payload.get("status") or fallback))


def _public_status(status: str) -> str:
    normalized = _token(status or "unknown", field="status")
    if normalized in {"go", "ready", "accepted", "completed", "processed", "sent"}:
        return "completed"
    if normalized in {"needs_review", "review_required"}:
        return "review"
    if normalized in {"no_go", "failed", "blocked"}:
        return "blocked"
    return normalized


def _overall_status(
    steps: tuple[Mapping[str, Any], ...],
    review_reasons: tuple[str, ...],
    no_go_reasons: tuple[str, ...],
) -> str:
    statuses = {str(step.get("status") or "") for step in steps}
    if "blocked" in statuses or no_go_reasons:
        return "blocked"
    if "review" in statuses or review_reasons:
        return "review"
    if statuses and statuses <= {"completed", "dry_run_ready", "skipped"}:
        return "ready"
    return "partial"


def _next_action(
    overall_status: str,
    steps: tuple[Mapping[str, Any], ...],
    live_write_allowed: bool,
) -> str:
    if overall_status == "blocked":
        return "fix_blocker"
    if overall_status == "review":
        return "operator_review"
    for step in steps:
        if step.get("status") in {"pending", "partial", "unknown"}:
            return str(step.get("stage") or "continue")
    if not live_write_allowed:
        return "hold_for_live_go"
    return "ready_for_bounded_live_action"


def _source_kind_from_ref(source_ref: str, item_status: Mapping[str, Any]) -> str:
    if item_status.get("source_kind"):
        return _source_kind(item_status.get("source_kind"))
    if ":" in source_ref:
        return _source_kind(source_ref.split(":", 1)[0])
    return "unknown"


def _source_kind(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    if not _SAFE_SOURCE_KIND_RE.fullmatch(text):
        return "unknown"
    return text


def _sanitize_mapping(payload: Mapping[str, Any] | Any) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, Mapping):
        return {}, 0
    sanitized: dict[str, Any] = {}
    blocked_count = 0
    for key, value in payload.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            blocked_count += 1
            continue
        clean_value, value_blocked_count = _sanitize_value(value)
        sanitized[key_text] = clean_value
        blocked_count += value_blocked_count
    return sanitized, blocked_count


def _sanitize_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (tuple, list)):
        sanitized = []
        blocked_count = 0
        for item in value:
            clean, item_blocked = _sanitize_value(item)
            sanitized.append(clean)
            blocked_count += item_blocked
        return tuple(sanitized), blocked_count
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value, 0
    return str(value), 0


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        pattern in normalized for pattern in _SENSITIVE_KEY_PATTERNS
    )


def _graph_payload(graph_event: Mapping[str, Any], memory_intent: Mapping[str, Any]) -> Mapping[str, Any]:
    if graph_event:
        return graph_event
    raptor_event = memory_intent.get("raptorgraph_event")
    return raptor_event if isinstance(raptor_event, Mapping) else {}


def _reason_codes(values: Any) -> tuple[str, ...]:
    return normalize_universal_inbox_review_reasons(values)


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


def _token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise UniversalInboxFlowStateError(f"{field} must be a safe token")
    return token


def _runtime_event(
    *,
    correlation_id: str,
    overall_status: str,
    review_reasons: tuple[str, ...],
    no_go_reasons: tuple[str, ...],
    blocked_field_count: int,
    live_write_allowed: bool,
) -> dict[str, Any]:
    event_status = {
        "ready": "queued",
        "review": "warn",
        "blocked": "blocked",
        "partial": "unknown",
    }.get(overall_status, "unknown")
    return build_runtime_event(
        surface="universal_inbox",
        component="flow_state",
        event_type="flow_state",
        status=event_status,
        severity="warn" if event_status in {"warn", "blocked"} else "info",
        owner_scope="universal_inbox",
        correlation_id=correlation_id,
        privacy_level="private_metadata",
        side_effects=("none",),
        metadata={
            "overall_status": overall_status,
            "review_reason_count": len(review_reasons),
            "no_go_reason_count": len(no_go_reasons),
            "blocked_field_count": blocked_field_count,
            "live_write_allowed": bool(live_write_allowed),
        },
    )
