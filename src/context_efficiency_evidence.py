"""Redacted evidence records for context-efficiency decisions.

The records in this module summarize tool-schema selection, cache-boundary and
simple routing decisions without storing raw prompts, schemas, provider output,
private content, host paths, or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import Any, Mapping


_MAX_ID = 100
_MAX_REASON = 120
_MAX_REASONS = 16
_MAX_METRICS = 32
_FORBIDDEN = (
    "api_key",
    "authorization:",
    "bearer ",
    "cookie:",
    "password",
    "secret=",
    "token=",
)
_NON_TOKEN_RE = re.compile(r"[^a-z0-9_.:@/+~-]+")


class ContextEfficiencyEvidenceError(ValueError):
    """Raised when a context-efficiency evidence record is unsafe."""


class ContextEfficiencyEvidenceKind(StrEnum):
    TOOL_SCHEMA_SELECTION = "tool_schema_selection"
    CACHE_BOUNDARY = "cache_boundary"
    TASK_ROUTING = "task_routing"


class ContextEfficiencyEvidenceStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ContextEfficiencyEvidenceRecord:
    evidence_id: str
    kind: ContextEfficiencyEvidenceKind
    status: ContextEfficiencyEvidenceStatus
    decision_ref: str
    reason_codes: tuple[str, ...]
    metrics: Mapping[str, Any]
    raw_prompt_visible: bool = False
    raw_schema_visible: bool = False
    raw_content_visible: bool = False
    token_value_visible: bool = False

    @classmethod
    def create(
        cls,
        *,
        evidence_id: Any,
        kind: ContextEfficiencyEvidenceKind | str,
        status: ContextEfficiencyEvidenceStatus | str,
        decision_ref: Any,
        reason_codes: tuple[Any, ...] | list[Any],
        metrics: Mapping[str, Any],
    ) -> "ContextEfficiencyEvidenceRecord":
        normalized_kind = kind if isinstance(kind, ContextEfficiencyEvidenceKind) else ContextEfficiencyEvidenceKind(str(kind))
        normalized_status = (
            status if isinstance(status, ContextEfficiencyEvidenceStatus) else ContextEfficiencyEvidenceStatus(str(status))
        )
        safe_metrics = _safe_metrics(metrics)
        return cls(
            evidence_id=_safe_ref(evidence_id, field_name="evidence_id"),
            kind=normalized_kind,
            status=normalized_status,
            decision_ref=_safe_ref(decision_ref, field_name="decision_ref"),
            reason_codes=_safe_reason_codes(reason_codes),
            metrics=safe_metrics,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "decision_ref": self.decision_ref,
            "reason_codes": self.reason_codes,
            "metrics": dict(self.metrics),
            "raw_prompt_visible": self.raw_prompt_visible,
            "raw_schema_visible": self.raw_schema_visible,
            "raw_content_visible": self.raw_content_visible,
            "token_value_visible": self.token_value_visible,
        }


def evidence_from_tool_schema_selection(evidence_id: Any, selection: Any) -> ContextEfficiencyEvidenceRecord:
    summary = _audit_summary(selection)
    selected = _int(summary.get("selected_schema_count"))
    deferred = _int(summary.get("deferred_schema_count"))
    blocked = _int(summary.get("blocked_schema_count"))
    warnings = tuple(summary.get("warnings") or ())
    status = ContextEfficiencyEvidenceStatus.SUCCESS
    if blocked:
        status = ContextEfficiencyEvidenceStatus.PARTIAL
    if selected == 0 and deferred == 0 and not blocked:
        status = ContextEfficiencyEvidenceStatus.SKIPPED
    return ContextEfficiencyEvidenceRecord.create(
        evidence_id=evidence_id,
        kind=ContextEfficiencyEvidenceKind.TOOL_SCHEMA_SELECTION,
        status=status,
        decision_ref="tool-schema-selection",
        reason_codes=tuple(warnings) or ("manifest_first_schema_selection",),
        metrics={
            "manifest_count": _int(summary.get("manifest_count")),
            "selected_schema_count": selected,
            "deferred_schema_count": deferred,
            "blocked_schema_count": blocked,
            "warning_count": len(warnings),
            "prompt_budget_estimate": _int(summary.get("prompt_budget_estimate")),
            "raw_schema_visible": bool(summary.get("raw_schema_visible")),
        },
    )


def evidence_from_cache_boundary_decision(evidence_id: Any, decision: Any) -> ContextEfficiencyEvidenceRecord:
    summary = _audit_summary(decision)
    allowed = bool(summary.get("allowed"))
    changed = bool(summary.get("changed"))
    status = ContextEfficiencyEvidenceStatus.SUCCESS if allowed else ContextEfficiencyEvidenceStatus.BLOCKED
    reasons = tuple(summary.get("reasons") or ())
    decision_ref = summary.get("decision") or "cache-boundary"
    return ContextEfficiencyEvidenceRecord.create(
        evidence_id=evidence_id,
        kind=ContextEfficiencyEvidenceKind.CACHE_BOUNDARY,
        status=status,
        decision_ref=decision_ref,
        reason_codes=reasons or (decision_ref,),
        metrics={
            "allowed": allowed,
            "changed": changed,
            "requires_new_session": bool(summary.get("requires_new_session")),
            "requires_operator_go": bool(summary.get("requires_operator_go")),
        },
    )


def evidence_from_task_routing_decision(evidence_id: Any, decision: Any) -> ContextEfficiencyEvidenceRecord:
    summary = _audit_summary(decision)
    route = str(summary.get("route") or "task_routing")
    status = ContextEfficiencyEvidenceStatus.SUCCESS
    if bool(summary.get("requires_review")):
        status = ContextEfficiencyEvidenceStatus.PARTIAL
    return ContextEfficiencyEvidenceRecord.create(
        evidence_id=evidence_id,
        kind=ContextEfficiencyEvidenceKind.TASK_ROUTING,
        status=status,
        decision_ref=route,
        reason_codes=tuple(summary.get("reason_codes") or (route,)),
        metrics={
            "eligible_for_small_model": bool(summary.get("eligible_for_small_model")),
            "requires_tool_orchestration": bool(summary.get("requires_tool_orchestration")),
            "requires_strong_reasoning": bool(summary.get("requires_strong_reasoning")),
            "requires_review": bool(summary.get("requires_review")),
            "local_only_required": bool(summary.get("local_only_required")),
            "token_budget": _int(summary.get("token_budget")),
            "prompt_chars": _int(summary.get("prompt_chars")),
        },
    )


def _audit_summary(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "audit_summary"):
        payload = value.audit_summary()
    else:
        payload = value
    if not isinstance(payload, Mapping):
        raise ContextEfficiencyEvidenceError("decision must provide a mapping audit summary")
    return payload


def _safe_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metrics, Mapping):
        raise ContextEfficiencyEvidenceError("metrics must be a mapping")
    safe: dict[str, Any] = {}
    for key in sorted(metrics, key=lambda item: str(item))[:_MAX_METRICS]:
        safe_key = _safe_ref(key, field_name="metric_key")
        value = metrics[key]
        if isinstance(value, bool):
            safe[safe_key] = value
        elif isinstance(value, int):
            safe[safe_key] = value
        elif isinstance(value, float):
            safe[safe_key] = round(value, 6)
        elif value is None:
            safe[safe_key] = None
        else:
            safe[safe_key] = _safe_ref(value, field_name=f"metric:{safe_key}")
    return safe


def _safe_reason_codes(values: tuple[Any, ...] | list[Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    seen: set[str] = set()
    for value in values[:_MAX_REASONS]:
        reason = _safe_ref(value, field_name="reason_code", limit=_MAX_REASON)
        if reason in seen:
            continue
        seen.add(reason)
        reasons.append(reason)
    if not reasons:
        raise ContextEfficiencyEvidenceError("reason_codes must not be empty")
    return tuple(reasons)


def _safe_ref(value: Any, *, field_name: str, limit: int = _MAX_ID) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContextEfficiencyEvidenceError(f"{field_name} must not be empty")
    lowered = text.lower()
    if any(marker in lowered for marker in _FORBIDDEN):
        raise ContextEfficiencyEvidenceError(f"{field_name} contains forbidden material")
    pathish = "\\" in text or lowered.startswith("/") or re.match(r"^[a-z]:/", lowered) or "/users/" in lowered or "/home/" in lowered
    if pathish:
        return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    token = _NON_TOKEN_RE.sub("-", lowered).strip("-")
    if not token:
        raise ContextEfficiencyEvidenceError(f"{field_name} must contain safe characters")
    return token[:limit]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
