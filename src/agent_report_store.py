"""Offline AgentReport validation and reduction models.

This module treats agent reports as untrusted input. Reports can produce
structured reducer output, but they never mutate PlanGraph state directly and
never mark work verified done.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import PurePosixPath
import re
from typing import Any, Iterable


_MAX_ID = 96
_MAX_TEXT = 220
_MAX_SUMMARY = 360
_MAX_STRUCTURED_ITEMS = 80
_MAX_REFS = 120
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_FORBIDDEN_EVENT_TYPES = {
    "node_completed",
    "node_promoted_to_claimable",
    "node_claimed",
    "report_accepted",
}
_ALLOWED_REDUCER_EVENT_TYPES = {
    "gate_observed",
    "collision_observed",
    "node_proposed",
    "node_blocked",
    "frontier_refreshed",
    "context_summary_updated",
}
_FORBIDDEN_REPORT_TOKENS = {
    "verified_done",
    "promoted_to_claimable",
    "make_claimable",
    "mark_done",
}
_SECRET_MARKERS = {
    "-----BEGIN PRIVATE KEY-----",
    "TELEGRAM_BOT_TOKEN=",
    "OPENAI_API_KEY=",
    "PASSWORD=",
}


class AgentReportStoreError(ValueError):
    """Raised when an AgentReport payload is invalid or unsafe."""


class AgentReportConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentReportStatus(StrEnum):
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REDUCED_TO_EVENTS = "reduced_to_events"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise AgentReportStoreError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise AgentReportStoreError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise AgentReportStoreError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise AgentReportStoreError(f"{field_name} must not be empty")
    if _contains_secret_marker(text):
        raise AgentReportStoreError(f"{field_name} appears to contain raw secret material")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _contains_secret_marker(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise AgentReportStoreError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise AgentReportStoreError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise AgentReportStoreError(f"{field_name} must be repo-relative")
    path_part = raw.split(":", 1)[0]
    parts = PurePosixPath(path_part).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise AgentReportStoreError(f"{field_name} must not contain traversal segments")
    return raw


def _normalize_path_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value, field_name=field_name)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    if not normalized and not allow_empty:
        raise AgentReportStoreError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


def _normalize_text_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    if not normalized and not allow_empty:
        raise AgentReportStoreError(f"{field_name} must not be empty")
    return tuple(normalized)


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 40 or not _TIMESTAMP_RE.fullmatch(text):
        raise AgentReportStoreError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _normalize_confidence(value: Any) -> AgentReportConfidence:
    return value if isinstance(value, AgentReportConfidence) else AgentReportConfidence(str(value or "").strip().lower())


def _normalize_report_status(value: Any) -> AgentReportStatus:
    return value if isinstance(value, AgentReportStatus) else AgentReportStatus(str(value or "").strip().lower())


def _source_in_scope(source_ref: str, read_scope: tuple[str, ...]) -> bool:
    source_path = source_ref.split(":", 1)[0]
    for scope in read_scope:
        scope_path = scope.split(":", 1)[0]
        if any(token in scope_path for token in "*?[]"):
            if fnmatch(source_path, scope_path):
                return True
        elif scope_path.endswith("/"):
            if source_path.startswith(scope_path):
                return True
        elif source_path == scope_path:
            return True
        elif source_path.startswith(scope_path.rstrip("/") + "/"):
            return True
    return False


def _assert_sources_in_scope(source_refs: tuple[str, ...], read_scope: tuple[str, ...]) -> None:
    out_of_scope = [source for source in source_refs if not _source_in_scope(source, read_scope)]
    if out_of_scope:
        raise AgentReportStoreError(f"source_refs must stay inside read_scope: {', '.join(out_of_scope)}")


def _normalize_structured_items(values: Iterable[Any], *, field_name: str) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise AgentReportStoreError(f"{field_name} items must be structured objects")
        if len(normalized) >= _MAX_STRUCTURED_ITEMS:
            raise AgentReportStoreError(f"{field_name} exceeds max item count {_MAX_STRUCTURED_ITEMS}")
        normalized.append(_normalize_structured_item(value, field_name=field_name))
    return tuple(normalized)


def _normalize_structured_item(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for key in sorted(value):
        normalized_key = _normalize_slug(key, field_name=f"{field_name}_key").replace("-", "_")
        raw = value[key]
        if isinstance(raw, dict):
            item[normalized_key] = _normalize_structured_item(raw, field_name=field_name)
        elif isinstance(raw, list):
            item[normalized_key] = [
                _normalize_structured_item(entry, field_name=field_name)
                if isinstance(entry, dict)
                else _normalize_text(entry, field_name=normalized_key, allow_empty=True, limit=_MAX_SUMMARY)
                for entry in raw[:_MAX_STRUCTURED_ITEMS]
            ]
        else:
            item[normalized_key] = _normalize_text(raw, field_name=normalized_key, allow_empty=True, limit=_MAX_SUMMARY)
    if not any(item.values()):
        raise AgentReportStoreError(f"{field_name} items must not be empty")
    _reject_forbidden_report_tokens(item, field_name=field_name)
    return item


def _reject_forbidden_report_tokens(value: Any, *, field_name: str) -> None:
    text = repr(value).lower()
    if field_name == "proposed_plan_events" and any(
        token in text for token in ("claimable", "node_claimed", "node_completed", "report_accepted")
    ):
        raise AgentReportStoreError("proposed_plan_events must not complete, claim, promote, or accept work")
    for token in _FORBIDDEN_REPORT_TOKENS:
        if token in text:
            raise AgentReportStoreError(f"{field_name} must not claim verified done or claimability")


def _event_type(value: dict[str, Any]) -> str:
    event_type = _normalize_slug(value.get("event_type", ""), field_name="event_type").replace("-", "_")
    if event_type in _FORBIDDEN_EVENT_TYPES:
        raise AgentReportStoreError("proposed_plan_events must not complete, claim, promote, or accept work")
    if event_type not in _ALLOWED_REDUCER_EVENT_TYPES:
        raise AgentReportStoreError(f"unsupported proposed event_type: {event_type}")
    return event_type


@dataclass(frozen=True, slots=True)
class ReducedPlanEvent:
    event_type: str
    plan_id: str
    node_id: str
    writer: str
    reason: str
    evidence_refs: tuple[str, ...]
    confidence: AgentReportConfidence

    @classmethod
    def create(
        cls,
        *,
        event_type: Any,
        plan_id: Any,
        node_id: Any,
        writer: Any,
        reason: Any,
        evidence_refs: Iterable[Any],
        confidence: AgentReportConfidence | str,
    ) -> "ReducedPlanEvent":
        normalized_event_type = _normalize_slug(event_type, field_name="event_type").replace("-", "_")
        if normalized_event_type not in _ALLOWED_REDUCER_EVENT_TYPES:
            raise AgentReportStoreError(f"unsupported reducer event_type: {normalized_event_type}")
        return cls(
            event_type=normalized_event_type,
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
            writer=_normalize_slug(writer, field_name="writer"),
            reason=_normalize_text(reason, field_name="reason", allow_empty=False, limit=_MAX_SUMMARY),
            evidence_refs=_normalize_text_list(evidence_refs, field_name="evidence_refs", allow_empty=True),
            confidence=_normalize_confidence(confidence),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "writer": self.writer,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True, slots=True)
class ContextSummary:
    summary_id: str
    summary: str
    source_refs: tuple[str, ...]
    confidence: AgentReportConfidence

    @classmethod
    def create(
        cls,
        *,
        summary_id: Any,
        summary: Any,
        source_refs: Iterable[Any],
        confidence: AgentReportConfidence | str,
    ) -> "ContextSummary":
        return cls(
            summary_id=_normalize_slug(summary_id, field_name="summary_id"),
            summary=_normalize_text(summary, field_name="summary", allow_empty=False, limit=_MAX_SUMMARY),
            source_refs=_normalize_path_list(source_refs, field_name="source_ref", allow_empty=False),
            confidence=_normalize_confidence(confidence),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "summary": self.summary,
            "source_refs": list(self.source_refs),
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True, slots=True)
class ReportReduction:
    report_id: str
    reduced_events: tuple[ReducedPlanEvent, ...]
    context_summaries: tuple[ContextSummary, ...]
    warnings: tuple[str, ...]
    rejected_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "reduced_events": [event.to_dict() for event in self.reduced_events],
            "context_summaries": [summary.to_dict() for summary in self.context_summaries],
            "warnings": list(self.warnings),
            "rejected_reasons": list(self.rejected_reasons),
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "event_count": len(self.reduced_events),
            "context_summary_count": len(self.context_summaries),
            "warning_count": len(self.warnings),
            "rejected_reason_count": len(self.rejected_reasons),
            "event_types": tuple(event.event_type for event in self.reduced_events),
            "context_summary_ids": tuple(summary.summary_id for summary in self.context_summaries),
        }


@dataclass(frozen=True, slots=True)
class AgentReport:
    report_id: str
    plan_id: str
    node_id: str
    agent_id: str
    role_id: str
    capsule_id: str
    read_scope: tuple[str, ...]
    observations: tuple[dict[str, Any], ...]
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    blockers: tuple[dict[str, Any], ...]
    collision_candidates: tuple[dict[str, Any], ...]
    gate_observations: tuple[dict[str, Any], ...]
    proposed_plan_events: tuple[dict[str, Any], ...]
    confidence: AgentReportConfidence
    redaction_summary: str
    created_at: str
    status: AgentReportStatus

    @classmethod
    def create(
        cls,
        *,
        report_id: Any,
        plan_id: Any,
        node_id: Any,
        agent_id: Any,
        role_id: Any,
        capsule_id: Any,
        read_scope: Iterable[Any],
        observations: Iterable[Any],
        source_refs: Iterable[Any],
        evidence_refs: Iterable[Any] = (),
        blockers: Iterable[Any] = (),
        collision_candidates: Iterable[Any] = (),
        gate_observations: Iterable[Any] = (),
        proposed_plan_events: Iterable[Any] = (),
        confidence: AgentReportConfidence | str,
        redaction_summary: Any,
        created_at: Any,
        status: AgentReportStatus | str = AgentReportStatus.SUBMITTED,
    ) -> "AgentReport":
        normalized_read_scope = _normalize_path_list(read_scope, field_name="read_scope", allow_empty=False)
        normalized_source_refs = _normalize_path_list(source_refs, field_name="source_ref", allow_empty=False)
        _assert_sources_in_scope(normalized_source_refs, normalized_read_scope)
        normalized_observations = _normalize_structured_items(observations, field_name="observations")
        if not normalized_observations:
            raise AgentReportStoreError("observations must not be empty")
        normalized_events = _normalize_structured_items(proposed_plan_events, field_name="proposed_plan_events")
        for event in normalized_events:
            _event_type(event)
        report = cls(
            report_id=_normalize_slug(report_id, field_name="report_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            role_id=_normalize_slug(role_id, field_name="role_id"),
            capsule_id=_normalize_slug(capsule_id, field_name="capsule_id"),
            read_scope=normalized_read_scope,
            observations=normalized_observations,
            source_refs=normalized_source_refs,
            evidence_refs=_normalize_text_list(evidence_refs, field_name="evidence_refs", allow_empty=True),
            blockers=_normalize_structured_items(blockers, field_name="blockers"),
            collision_candidates=_normalize_structured_items(collision_candidates, field_name="collision_candidates"),
            gate_observations=_normalize_structured_items(gate_observations, field_name="gate_observations"),
            proposed_plan_events=normalized_events,
            confidence=_normalize_confidence(confidence),
            redaction_summary=_normalize_text(
                redaction_summary,
                field_name="redaction_summary",
                allow_empty=False,
                limit=_MAX_SUMMARY,
            ),
            created_at=_normalize_timestamp(created_at, field_name="created_at"),
            status=_normalize_report_status(status),
        )
        return report

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentReport":
        if not isinstance(payload, dict):
            raise AgentReportStoreError("payload must be a dict")
        return cls.create(
            report_id=_required(payload, "report_id"),
            plan_id=_required(payload, "plan_id"),
            node_id=_required(payload, "node_id"),
            agent_id=_required(payload, "agent_id"),
            role_id=_required(payload, "role_id"),
            capsule_id=_required(payload, "capsule_id"),
            read_scope=_list(payload.get("read_scope"), field_name="read_scope"),
            observations=_list(payload.get("observations"), field_name="observations"),
            source_refs=_list(payload.get("source_refs"), field_name="source_refs"),
            evidence_refs=_list(payload.get("evidence_refs", []), field_name="evidence_refs"),
            blockers=_list(payload.get("blockers", []), field_name="blockers"),
            collision_candidates=_list(payload.get("collision_candidates", []), field_name="collision_candidates"),
            gate_observations=_list(payload.get("gate_observations", []), field_name="gate_observations"),
            proposed_plan_events=_list(payload.get("proposed_plan_events", []), field_name="proposed_plan_events"),
            confidence=_required(payload, "confidence"),
            redaction_summary=_required(payload, "redaction_summary"),
            created_at=_required(payload, "created_at"),
            status=payload.get("status", AgentReportStatus.SUBMITTED),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "capsule_id": self.capsule_id,
            "read_scope": list(self.read_scope),
            "observations": list(self.observations),
            "source_refs": list(self.source_refs),
            "evidence_refs": list(self.evidence_refs),
            "blockers": list(self.blockers),
            "collision_candidates": list(self.collision_candidates),
            "gate_observations": list(self.gate_observations),
            "proposed_plan_events": list(self.proposed_plan_events),
            "confidence": self.confidence.value,
            "redaction_summary": self.redaction_summary,
            "created_at": self.created_at,
            "status": self.status.value,
        }

    def reduce(self) -> ReportReduction:
        return reduce_agent_report(self)

    def audit_summary(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "read_scope_count": len(self.read_scope),
            "observation_count": len(self.observations),
            "source_ref_count": len(self.source_refs),
            "blocker_count": len(self.blockers),
            "collision_candidate_count": len(self.collision_candidates),
            "gate_observation_count": len(self.gate_observations),
            "proposed_event_count": len(self.proposed_plan_events),
        }


def reduce_agent_report(report: AgentReport) -> ReportReduction:
    if not isinstance(report, AgentReport):
        raise AgentReportStoreError("report must be an AgentReport")

    context_summaries = tuple(
        ContextSummary.create(
            summary_id=observation.get("id") or f"{report.report_id}-observation-{idx + 1}",
            summary=observation.get("summary") or observation.get("title") or observation.get("reason") or repr(observation),
            source_refs=observation.get("source_refs") or report.source_refs,
            confidence=observation.get("confidence") or report.confidence,
        )
        for idx, observation in enumerate(report.observations)
    )

    events: list[ReducedPlanEvent] = []
    for blocker in report.blockers:
        events.append(_event_from_item("node_blocked", report, blocker))
    for collision in report.collision_candidates:
        events.append(_event_from_item("collision_observed", report, collision))
    for gate in report.gate_observations:
        events.append(_event_from_item("gate_observed", report, gate))
    for proposed in report.proposed_plan_events:
        events.append(_event_from_item(_event_type(proposed), report, proposed))

    return ReportReduction(
        report_id=report.report_id,
        reduced_events=tuple(events),
        context_summaries=context_summaries,
        warnings=(),
        rejected_reasons=(),
    )


def _event_from_item(event_type: str, report: AgentReport, item: dict[str, Any]) -> ReducedPlanEvent:
    reason = item.get("reason") or item.get("summary") or item.get("title") or f"{event_type} from {report.report_id}"
    evidence_refs = item.get("evidence_refs") or report.evidence_refs or report.source_refs[: min(3, len(report.source_refs))]
    return ReducedPlanEvent.create(
        event_type=event_type,
        plan_id=report.plan_id,
        node_id=item.get("node_id") or report.node_id,
        writer="agent_report_reducer",
        reason=reason,
        evidence_refs=evidence_refs,
        confidence=item.get("confidence") or report.confidence,
    )


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise AgentReportStoreError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        raise AgentReportStoreError(f"{field_name} must be a list")
    if not isinstance(value, list):
        raise AgentReportStoreError(f"{field_name} must be a list")
    if len(value) > _MAX_REFS:
        raise AgentReportStoreError(f"{field_name} exceeds max item count {_MAX_REFS}")
    return value
