"""Evidence-bound, bounded information products for AI Lens events."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from src.ai_lens_events import (
    AI_LENS_EVENT_SCHEMA,
    AiLensEvent,
    AiLensEventType,
    AiLensPrivacyLevel,
    AiLensRedactionLevel,
    AiLensTruthLevel,
    validate_event_batch,
)


AI_LENS_INSIGHTS_SCHEMA = "odysseus.ai_lens.insights.v1"
AI_LENS_INSIGHT_SCHEMA = "odysseus.ai_lens.insight.v1"
AI_LENS_SNAPSHOT_SCHEMA = "odysseus.ai_lens.snapshot.v1"
MAX_SUPPORTING_EVENT_IDS = 32
MAX_SOURCE_REFS = 16
MAX_VALUE_LIST = 16
MAX_VALUE_TEXT = 160
MAX_INSIGHT_BYTES = 8 * 1024
MAX_INSIGHTS_BYTES = 64 * 1024


class AiLensInsightError(ValueError):
    """Raised when insight evidence is invalid, unsafe, or over budget."""


class InsightStatus(StrEnum):
    AVAILABLE = "available"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


class InsightType(StrEnum):
    ANSWER_PACK_INVENTORY = "answer_pack_inventory"
    RETRIEVAL_QUALITY = "retrieval_quality"
    SOURCE_COVERAGE = "source_coverage"
    TOOL_AND_LATENCY_TRACE = "tool_and_latency_trace"
    CONFIDENCE_AND_RISK = "confidence_and_risk"
    ANSWER_PROVENANCE = "answer_provenance"


_PRIVACY_ORDER = {
    AiLensPrivacyLevel.PUBLIC: 0,
    AiLensPrivacyLevel.METADATA: 1,
    AiLensPrivacyLevel.PRIVATE_METADATA: 2,
    AiLensPrivacyLevel.SENSITIVE_METADATA: 3,
    AiLensPrivacyLevel.DSGVO_LOCAL: 4,
}
_REDACTION_ORDER = {
    AiLensRedactionLevel.NONE: 0,
    AiLensRedactionLevel.METADATA_ONLY: 1,
    AiLensRedactionLevel.REDACTED: 2,
    AiLensRedactionLevel.HASHED: 3,
    AiLensRedactionLevel.LOCAL_ONLY: 4,
}
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_PRIVATE_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users|var/lib|mnt|srv)/)", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?:authorization\s*:|bearer\s+\S+|(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+)", re.IGNORECASE)


def build_ai_lens_insights(
    evidence: AiLensEvent | Mapping[str, Any] | Iterable[AiLensEvent | Mapping[str, Any]],
    *,
    turn_id: str | None = None,
) -> dict[str, Any]:
    events, snapshot_incomplete, snapshot_truncated = _validated_input(evidence)
    if len({event.session_id for event in events}) > 1:
        raise AiLensInsightError("insight events must belong to one session")
    if len({event.observation_origin for event in events}) > 1:
        raise AiLensInsightError("fixture and runtime insight evidence must not be mixed")
    selected_turn = _select_turn(events, turn_id)
    selected = tuple(event for event in events if event.turn_id == selected_turn) if selected_turn else ()
    for event in selected:
        if event.truth_level != AiLensTruthLevel.RUNTIME_TRACE:
            raise AiLensInsightError("insights require runtime_trace evidence")
    source_flags = []
    if snapshot_incomplete:
        source_flags.append("source_snapshot_incomplete")
    if snapshot_truncated:
        source_flags.append("source_snapshot_truncated")

    products = (
        _answer_pack(selected, source_flags),
        _retrieval_quality(selected, source_flags),
        _source_coverage(selected, source_flags),
        _tool_latency(selected, source_flags),
        _confidence_risk(selected, source_flags),
        _answer_provenance(selected, source_flags),
    )
    counts = Counter(product["status"] for product in products)
    payload = {
        "schema": AI_LENS_INSIGHTS_SCHEMA,
        "session_id": events[0].session_id if events else "",
        "turn_id": selected_turn,
        "source_event_count": len(events),
        "selected_event_count": len(selected),
        "excluded_turn_count": len({event.turn_id for event in events if event.turn_id != selected_turn}),
        "source_snapshot_incomplete": snapshot_incomplete,
        "source_snapshot_truncated": snapshot_truncated,
        "insight_count": len(products),
        "available_count": counts[InsightStatus.AVAILABLE.value],
        "incomplete_count": counts[InsightStatus.INCOMPLETE.value],
        "unavailable_count": counts[InsightStatus.UNAVAILABLE.value],
        "insights": list(products),
        "raw_content_visible": False,
        "payload_bytes": 0,
    }
    size = _final_size(payload)
    if size > MAX_INSIGHTS_BYTES:
        raise AiLensInsightError("insight bundle exceeds max byte budget")
    return payload


def ai_lens_insights_json(evidence: Mapping[str, Any] | Iterable[AiLensEvent | Mapping[str, Any]], **kwargs: Any) -> str:
    return json.dumps(
        build_ai_lens_insights(evidence, **kwargs),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validated_input(evidence: AiLensEvent | Mapping[str, Any] | Iterable[AiLensEvent | Mapping[str, Any]]) -> tuple[tuple[AiLensEvent, ...], bool, bool]:
    incomplete = False
    truncated = False
    if isinstance(evidence, AiLensEvent):
        raw_events: Iterable[AiLensEvent | Mapping[str, Any]] = (evidence,)
    elif isinstance(evidence, Mapping):
        if evidence.get("schema") == AI_LENS_EVENT_SCHEMA:
            raw_events: Iterable[AiLensEvent | Mapping[str, Any]] = (evidence,)
        elif evidence.get("schema") == AI_LENS_SNAPSHOT_SCHEMA:
            if evidence.get("raw_content_visible") is not False:
                raise AiLensInsightError("snapshot raw_content_visible must be false")
            raw = evidence.get("events")
            if not isinstance(raw, list) or evidence.get("returned_event_count") != len(raw):
                raise AiLensInsightError("snapshot event count is invalid")
            if not isinstance(evidence.get("incomplete"), bool) or not isinstance(evidence.get("truncated"), bool):
                raise AiLensInsightError("snapshot completeness flags are invalid")
            incomplete = evidence["incomplete"]
            truncated = evidence["truncated"]
            raw_events = raw
        else:
            raise AiLensInsightError("insight input must be events or snapshot v1")
    else:
        raw_events = evidence
    try:
        events = validate_event_batch(raw_events)
    except (TypeError, ValueError) as exc:
        raise AiLensInsightError("insight events are invalid") from exc
    return events, incomplete, truncated


def _select_turn(events: tuple[AiLensEvent, ...], requested: str | None) -> str:
    if requested is not None:
        text = str(requested or "").strip()
        if not _SAFE_REF_RE.fullmatch(text) or text not in {event.turn_id for event in events}:
            raise AiLensInsightError("requested insight turn is invalid")
        return text
    return events[-1].turn_id if events else ""


def _answer_pack(events: tuple[AiLensEvent, ...], source_flags: list[str]) -> dict[str, Any]:
    relevant = _relevant(events, {
        AiLensEventType.CONTEXT_ITEM_SELECTED, AiLensEventType.CONTEXT_ITEM_EXCLUDED,
        AiLensEventType.CONTEXT_PACK_COMPOSED, AiLensEventType.CONTEXT_BUDGET_UPDATED,
    })
    included, included_present = _latest_int(relevant, "included_count")
    if not included_present and any(event.event_type == AiLensEventType.CONTEXT_ITEM_SELECTED for event in relevant):
        included = sum(event.event_type == AiLensEventType.CONTEXT_ITEM_SELECTED for event in relevant)
        included_present = True
    excluded, excluded_present = _latest_int(relevant, "excluded_count")
    if not excluded_present and any(event.event_type == AiLensEventType.CONTEXT_ITEM_EXCLUDED for event in relevant):
        excluded = sum(event.event_type == AiLensEventType.CONTEXT_ITEM_EXCLUDED for event in relevant)
        excluded_present = True
    fields = {
        "included_count": (included, included_present),
        "excluded_count": (excluded, excluded_present),
        "clipped_count": _latest_int(relevant, "clipped_count"),
        "token_budget": _latest_int(relevant, "token_budget"),
        "used_tokens": _latest_int(relevant, "used_tokens"),
        "sensitive_count": _latest_int(relevant, "sensitive_count"),
        "stale_count": _latest_int(relevant, "stale_count"),
    }
    return _product(InsightType.ANSWER_PACK_INVENTORY, relevant, {key: value for key, (value, _) in fields.items()}, [f"missing_{key}" for key, (_, present) in fields.items() if not present], source_flags)


def _retrieval_quality(events: tuple[AiLensEvent, ...], source_flags: list[str]) -> dict[str, Any]:
    relevant = _relevant(events, {AiLensEventType.MEMORY_HIT, AiLensEventType.RAG_HIT, AiLensEventType.RETRIEVAL_RANKING_SUMMARY})
    memory_scores = _scores(relevant, AiLensEventType.MEMORY_HIT)
    rag_scores = _scores(relevant, AiLensEventType.RAG_HIT)
    all_scores = memory_scores + rag_scores
    selected, selected_present = _latest_int(relevant, "selected_count")
    if not selected_present and any(event.event_type in {AiLensEventType.MEMORY_HIT, AiLensEventType.RAG_HIT} for event in relevant):
        selected = sum(event.event_type in {AiLensEventType.MEMORY_HIT, AiLensEventType.RAG_HIT} for event in relevant)
        selected_present = True
    below = _latest_int(relevant, "below_threshold_count")
    reasons, reasons_present = _latest_text_list(relevant, "reason_summaries")
    fields = {
        "top_memory_score": (max(memory_scores) if memory_scores else None, bool(memory_scores)),
        "top_rag_score": (max(rag_scores) if rag_scores else None, bool(rag_scores)),
        "score_spread": (round(max(all_scores) - min(all_scores), 6) if all_scores else None, bool(all_scores)),
        "selected_count": (selected, selected_present),
        "below_threshold_count": below,
        "reason_summaries": (reasons, reasons_present),
    }
    return _product(InsightType.RETRIEVAL_QUALITY, relevant, {key: value for key, (value, _) in fields.items()}, [f"missing_{key}" for key, (_, present) in fields.items() if not present], source_flags)


def _source_coverage(events: tuple[AiLensEvent, ...], source_flags: list[str]) -> dict[str, Any]:
    relevant = _relevant(events, {AiLensEventType.CONTEXT_PACK_COMPOSED, AiLensEventType.SOURCE_COVERAGE_SUMMARY, AiLensEventType.SOURCE_CONFLICT_DETECTED})
    refs = {ref.source_id: ref for event in relevant for ref in event.source_refs}
    type_counts = dict(sorted(Counter(ref.kind.value for ref in refs.values()).items()))
    dominant = None
    if type_counts:
        highest = max(type_counts.values())
        leaders = [kind for kind, count in type_counts.items() if count == highest]
        dominant = leaders[0] if len(leaders) == 1 else None
    missing, missing_present = _latest_ref_list(relevant, "missing_expected_sources")
    fields = {
        "source_type_counts": (type_counts, bool(refs)),
        "dominant_source_type": (dominant, bool(type_counts) and dominant is not None),
        "independent_source_count": (len(refs), bool(refs)),
        "conflict_count": (sum(event.event_type == AiLensEventType.SOURCE_CONFLICT_DETECTED for event in relevant), bool(relevant)),
        "missing_expected_sources": (missing, missing_present),
    }
    return _product(InsightType.SOURCE_COVERAGE, relevant, {key: value for key, (value, _) in fields.items()}, [f"missing_{key}" for key, (_, present) in fields.items() if not present], source_flags)


def _tool_latency(events: tuple[AiLensEvent, ...], source_flags: list[str]) -> dict[str, Any]:
    relevant = _relevant(events, {AiLensEventType.TOOL_CALL_STARTED, AiLensEventType.TOOL_CALL_RESULT})
    tool_ids = {ref.source_id for event in relevant for ref in event.source_refs if ref.kind.value == "tool"}
    if tool_ids:
        tool_count = len(tool_ids)
    else:
        starts = sum(event.event_type == AiLensEventType.TOOL_CALL_STARTED for event in relevant)
        results = sum(event.event_type == AiLensEventType.TOOL_CALL_RESULT for event in relevant)
        tool_count = starts if starts else results
    observed_latencies = [event.latency_ms for event in relevant if event.latency_ms > 0]
    retries = [value for event in relevant if (value := _payload_int(event, "retry_count")) is not None]
    fields = {
        "phase_durations_ms": ({"tool": sum(observed_latencies)} if observed_latencies else {}, bool(observed_latencies)),
        "tool_count": (tool_count, bool(relevant)),
        "failed_tool_count": (sum(event.status.value in {"failed", "blocked"} for event in relevant if event.event_type == AiLensEventType.TOOL_CALL_RESULT), bool(relevant)),
        "retry_count": (sum(retries) if retries else None, bool(retries)),
        "slowest_phase": ("tool" if observed_latencies else None, bool(observed_latencies)),
    }
    return _product(InsightType.TOOL_AND_LATENCY_TRACE, relevant, {key: value for key, (value, _) in fields.items()}, [f"missing_{key}" for key, (_, present) in fields.items() if not present], source_flags)


def _confidence_risk(events: tuple[AiLensEvent, ...], source_flags: list[str]) -> dict[str, Any]:
    relevant = _relevant(events, {AiLensEventType.SAFETY_GATE_TRIGGERED, AiLensEventType.SOURCE_CONFLICT_DETECTED, AiLensEventType.CONTEXT_ITEM_EXCLUDED})
    risks = [str(event.payload["risk_level"]).lower() for event in relevant if "risk_level" in event.payload]
    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    if any(risk not in risk_order for risk in risks):
        raise AiLensInsightError("risk_level evidence is invalid")
    flags, flags_present = _combined_text_list(relevant, "uncertainty_flags")
    stale_values = [event.payload.get("stale") for event in relevant if event.event_type == AiLensEventType.CONTEXT_ITEM_EXCLUDED]
    stale_present = bool(stale_values) and all(isinstance(value, bool) for value in stale_values)
    fields = {
        "risk_level": (max(risks, key=risk_order.get) if risks else None, bool(risks)),
        "uncertainty_flags": (flags, flags_present),
        "policy_gate_count": (sum(event.event_type == AiLensEventType.SAFETY_GATE_TRIGGERED for event in relevant), bool(relevant)),
        "stale_context_count": (sum(value is True for value in stale_values) if stale_present else None, stale_present),
        "redaction_count": (sum(ref.redaction_level != AiLensRedactionLevel.NONE for event in relevant for ref in event.source_refs), bool(relevant)),
    }
    return _product(InsightType.CONFIDENCE_AND_RISK, relevant, {key: value for key, (value, _) in fields.items()}, [f"missing_{key}" for key, (_, present) in fields.items() if not present], source_flags)


def _answer_provenance(events: tuple[AiLensEvent, ...], source_flags: list[str]) -> dict[str, Any]:
    relevant = _relevant(events, {AiLensEventType.ANSWER_COMPLETED, AiLensEventType.ANSWER_PROVENANCE_SUMMARY, AiLensEventType.LENS_REPLAY_SNAPSHOT_SAVED})
    segment_refs, segment_present = _combined_ref_list(relevant, "answer_segment_refs")
    context_refs, context_present = _combined_ref_list(relevant, "supporting_context_refs")
    tool_refs, tool_present = _combined_ref_list(relevant, "tool_refs")
    unsupported = _latest_int(relevant, "unsupported_segment_count")
    fields = {
        "answer_segment_refs": (segment_refs, segment_present),
        "supporting_context_refs": (context_refs, context_present),
        "tool_refs": (tool_refs, tool_present),
        "unsupported_segment_count": unsupported,
    }
    return _product(InsightType.ANSWER_PROVENANCE, relevant, {key: value for key, (value, _) in fields.items()}, [f"missing_{key}" for key, (_, present) in fields.items() if not present], source_flags)


def _product(kind: InsightType, contributors: tuple[AiLensEvent, ...], value: dict[str, Any], missing: list[str], source_flags: list[str]) -> dict[str, Any]:
    reasons = list(dict.fromkeys(missing + (source_flags if contributors else [])))
    event_ids = list(dict.fromkeys(event.event_id for event in contributors))
    refs_by_id = {ref.source_id: ref for event in contributors for ref in event.source_refs}
    all_refs = [refs_by_id[key] for key in sorted(refs_by_id)]
    refs = list(all_refs)
    if len(event_ids) > MAX_SUPPORTING_EVENT_IDS:
        event_ids = event_ids[:MAX_SUPPORTING_EVENT_IDS]
        reasons.append("supporting_event_budget")
    if len(refs) > MAX_SOURCE_REFS:
        refs = refs[:MAX_SOURCE_REFS]
        reasons.append("source_ref_budget")
    if not contributors:
        status = InsightStatus.UNAVAILABLE
        reasons = ["no_relevant_evidence"]
    elif reasons:
        status = InsightStatus.INCOMPLETE
    else:
        status = InsightStatus.AVAILABLE
    privacy = max((event.privacy_level for event in contributors), key=_PRIVACY_ORDER.get, default=AiLensPrivacyLevel.METADATA)
    redactions = [event.redaction_level for event in contributors] + [ref.redaction_level for ref in all_refs]
    redaction = max(redactions, key=_REDACTION_ORDER.get, default=AiLensRedactionLevel.METADATA_ONLY)
    payload = {
        "schema": AI_LENS_INSIGHT_SCHEMA,
        "type": kind.value,
        "status": status.value,
        "value": value,
        "summary": f"{kind.value.replace('_', ' ').title()} is {status.value} from {len(contributors)} bounded event(s).",
        "supporting_event_ids": event_ids,
        "source_refs": [ref.to_dict() for ref in refs],
        "truth_level": AiLensTruthLevel.RUNTIME_TRACE.value,
        "classification": privacy.value,
        "redaction_level": redaction.value,
        "incomplete_reasons": sorted(set(reasons)),
        "raw_content_visible": False,
    }
    if _final_size(payload) > MAX_INSIGHT_BYTES:
        raise AiLensInsightError(f"{kind.value} exceeds insight byte budget")
    return payload


def _relevant(events: tuple[AiLensEvent, ...], types: set[AiLensEventType]) -> tuple[AiLensEvent, ...]:
    return tuple(event for event in events if event.event_type in types)


def _latest_int(events: Sequence[AiLensEvent], key: str) -> tuple[int | None, bool]:
    for event in reversed(events):
        value = _payload_int(event, key)
        if value is not None:
            return value, True
    return None, False


def _payload_int(event: AiLensEvent, key: str) -> int | None:
    if key not in event.payload:
        return None
    value = event.payload[key]
    if isinstance(value, bool):
        raise AiLensInsightError(f"{key} evidence must be a non-negative integer")
    if isinstance(value, float) and not value.is_integer():
        raise AiLensInsightError(f"{key} evidence must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AiLensInsightError(f"{key} evidence must be a non-negative integer") from exc
    if number < 0 or number > 1_000_000_000:
        raise AiLensInsightError(f"{key} evidence is out of bounds")
    return number


def _scores(events: Sequence[AiLensEvent], event_type: AiLensEventType) -> list[float]:
    result = []
    for event in events:
        if event.event_type != event_type or "score" not in event.payload:
            continue
        value = event.payload["score"]
        if isinstance(value, bool):
            raise AiLensInsightError("retrieval score must be numeric")
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise AiLensInsightError("retrieval score must be numeric") from exc
        if not math.isfinite(score) or score < 0 or score > 1:
            raise AiLensInsightError("retrieval score must be finite and normalized")
        result.append(round(score, 6))
    return result


def _latest_text_list(events: Sequence[AiLensEvent], key: str) -> tuple[list[str], bool]:
    for event in reversed(events):
        if key in event.payload:
            return _safe_text_list(event.payload[key], key=key), True
    return [], False


def _combined_text_list(events: Sequence[AiLensEvent], key: str) -> tuple[list[str], bool]:
    present = False
    values = []
    for event in events:
        if key in event.payload:
            present = True
            values.extend(_safe_text_list(event.payload[key], key=key))
    normalized = sorted(set(values))
    if len(normalized) > MAX_VALUE_LIST:
        raise AiLensInsightError(f"{key} exceeds insight value budget")
    return normalized, present


def _latest_ref_list(events: Sequence[AiLensEvent], key: str) -> tuple[list[str], bool]:
    for event in reversed(events):
        if key in event.payload:
            return _safe_ref_list(event.payload[key], key=key), True
    return [], False


def _combined_ref_list(events: Sequence[AiLensEvent], key: str) -> tuple[list[str], bool]:
    present = False
    values = []
    for event in events:
        if key in event.payload:
            present = True
            values.extend(_safe_ref_list(event.payload[key], key=key))
    normalized = sorted(set(values))
    if len(normalized) > MAX_VALUE_LIST:
        raise AiLensInsightError(f"{key} exceeds insight value budget")
    return normalized, present


def _safe_text_list(value: Any, *, key: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_VALUE_LIST:
        raise AiLensInsightError(f"{key} must be a bounded list")
    result = []
    for item in value:
        text = " ".join(str(item or "").split())
        if not text or len(text) > MAX_VALUE_TEXT or _PRIVATE_PATH_RE.search(text) or _SECRET_RE.search(text):
            raise AiLensInsightError(f"{key} contains unsafe text")
        result.append(text)
    return result


def _safe_ref_list(value: Any, *, key: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_VALUE_LIST:
        raise AiLensInsightError(f"{key} must be a bounded list")
    result = [str(item or "").strip() for item in value]
    if any(not _SAFE_REF_RE.fullmatch(item) for item in result):
        raise AiLensInsightError(f"{key} contains an invalid reference")
    return result


def _final_size(payload: dict[str, Any]) -> int:
    size = 0
    for _ in range(4):
        payload["payload_bytes"] = size
        updated = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if updated == size:
            return size
        size = updated
    payload["payload_bytes"] = size
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


build_insights = build_ai_lens_insights
