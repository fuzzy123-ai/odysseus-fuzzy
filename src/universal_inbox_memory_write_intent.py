"""Dry-run write intents for Universal Inbox long-term memory.

The intent is the safe boundary before live memory or RaptorGraph writes. It
contains only redacted abstractions and provenance, never raw document content.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from src.internal_references import build_internal_reference_dict
from src.runtime_event_envelope import build_runtime_event, stable_payload_hash
from src.universal_inbox_analysis import UniversalInboxFileAnalysisPacket
from src.universal_inbox_memory import UniversalInboxMemoryAbstraction


WRITE_INTENT_SCHEMA = "odysseus.universal_inbox.memory_write_intent.v1"
MEMORY_RECORD_SCHEMA = "odysseus.universal_inbox.memory_record.v1"
RAPTORGRAPH_WRITE_EVENT_SCHEMA = "odysseus.universal_inbox.raptorgraph_write_intent.v1"

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_TEXT_RE = re.compile(
    r"(PRIVATE RAW TEXT|BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,})",
    re.IGNORECASE,
)


class UniversalInboxMemoryWriteIntentError(ValueError):
    """Raised when a write intent would be unsafe or invalid."""


@dataclass(frozen=True)
class UniversalInboxMemoryWriteIntent:
    status: str
    reason: str
    memory_records: tuple[dict[str, Any], ...]
    raptorgraph_event: dict[str, Any]
    analysis_policy: Mapping[str, Any]
    schema: str = WRITE_INTENT_SCHEMA
    dry_run: bool = True
    writes_performed: bool = False
    raw_content_visible: bool = False

    @property
    def ready_to_write(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        runtime_event = _intent_runtime_event(self)
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "ready_to_write": self.ready_to_write,
            "raw_content_visible": False,
            "correlation_id": runtime_event["correlation_id"],
            "runtime_event": runtime_event,
            "memory_records": tuple(dict(record) for record in self.memory_records),
            "raptorgraph_event": dict(self.raptorgraph_event),
            "analysis_policy": dict(self.analysis_policy),
        }


def build_universal_inbox_memory_write_intent(
    *,
    memory: UniversalInboxMemoryAbstraction | Mapping[str, Any],
    analysis: UniversalInboxFileAnalysisPacket | Mapping[str, Any],
) -> UniversalInboxMemoryWriteIntent:
    abstraction = (
        memory
        if isinstance(memory, UniversalInboxMemoryAbstraction)
        else UniversalInboxMemoryAbstraction.from_mapping(memory)
    )
    memory_event = abstraction.to_raptorgraph_event()
    analysis_payload = analysis.to_dict() if hasattr(analysis, "to_dict") else analysis
    if not isinstance(analysis_payload, Mapping):
        raise UniversalInboxMemoryWriteIntentError("analysis must be a mapping or expose to_dict()")
    policy = analysis_payload.get("policy")
    if not isinstance(policy, Mapping):
        raise UniversalInboxMemoryWriteIntentError("analysis policy is required")
    author_stamp = _analysis_author_stamp(analysis_payload)
    maintenance_route = _analysis_maintenance_route(analysis_payload)

    blocked = int(memory_event.get("blocked_field_count") or 0)
    if blocked:
        return _blocked_intent(
            reason="memory_abstraction_fields_blocked",
            memory_event=memory_event,
            policy=policy,
            author_stamp=author_stamp,
            maintenance_route=maintenance_route,
        )
    if str(policy.get("status") or "") == "no_go":
        return _blocked_intent(
            reason="analysis_policy_no_go",
            memory_event=memory_event,
            policy=policy,
            author_stamp=author_stamp,
            maintenance_route=maintenance_route,
        )
    if not bool(policy.get("memory_write_allowed")) or not bool(policy.get("raptor_write_allowed")):
        return _review_intent(
            reason="analysis_policy_requires_review",
            memory_event=memory_event,
            policy=policy,
            author_stamp=author_stamp,
            maintenance_route=maintenance_route,
        )

    record = _build_memory_record(memory_event, policy, author_stamp=author_stamp, maintenance_route=maintenance_route)
    raptor_event = _build_raptorgraph_write_event(
        memory_event,
        policy,
        memory_record_ids=(record["memory_id"],),
        author_stamp=author_stamp,
        maintenance_route=maintenance_route,
    )
    return UniversalInboxMemoryWriteIntent(
        status="ready",
        reason="policy_allows_abstract_memory_write",
        memory_records=(record,),
        raptorgraph_event=raptor_event,
        analysis_policy=dict(policy),
    )


def _blocked_intent(
    *,
    reason: str,
    memory_event: Mapping[str, Any],
    policy: Mapping[str, Any],
    author_stamp: Mapping[str, Any] | None = None,
    maintenance_route: Mapping[str, Any] | None = None,
) -> UniversalInboxMemoryWriteIntent:
    return UniversalInboxMemoryWriteIntent(
        status="blocked",
        reason=reason,
        memory_records=(),
        raptorgraph_event=_build_raptorgraph_write_event(
            memory_event,
            policy,
            memory_record_ids=(),
            author_stamp=author_stamp,
            maintenance_route=maintenance_route,
        ),
        analysis_policy=dict(policy),
    )


def _review_intent(
    *,
    reason: str,
    memory_event: Mapping[str, Any],
    policy: Mapping[str, Any],
    author_stamp: Mapping[str, Any] | None = None,
    maintenance_route: Mapping[str, Any] | None = None,
) -> UniversalInboxMemoryWriteIntent:
    return UniversalInboxMemoryWriteIntent(
        status="review",
        reason=reason,
        memory_records=(),
        raptorgraph_event=_build_raptorgraph_write_event(
            memory_event,
            policy,
            memory_record_ids=(),
            author_stamp=author_stamp,
            maintenance_route=maintenance_route,
        ),
        analysis_policy=dict(policy),
    )


def _build_memory_record(
    memory_event: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    author_stamp: Mapping[str, Any] | None = None,
    maintenance_route: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_hash = str(memory_event.get("source_hash") or "")
    if not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{32,128}", source_hash):
        raise UniversalInboxMemoryWriteIntentError("source_hash must be sha256-like")
    classification = _token(policy.get("classification") or "private", field="classification")
    document_type = _token(memory_event.get("document_type") or "reference", field="document_type")
    domain = _token(memory_event.get("domain") or "private", field="domain")
    text = _memory_text(memory_event, classification=classification, document_type=document_type)
    _reject_suspicious_text(text)
    memory_id = f"uix-{source_hash[-16:]}"
    return {
        "schema": MEMORY_RECORD_SCHEMA,
        "memory_id": memory_id,
        "internal_ref": build_internal_reference_dict("memory", memory_id, label="Memory oeffnen"),
        "source": "universal_inbox",
        "category": "document",
        "text": text,
        "metadata": {
            "classification": classification,
            "ai_classification": classification,
            "domain": domain,
            "document_type": document_type,
            "source_hash": source_hash,
            "routing_policy": str(memory_event.get("routing_policy") or ""),
            "source_provider": "universal_inbox",
            "raw_content_stored": False,
            "local_only": bool(policy.get("local_only_required")),
            "dsgvo_mode": bool(policy.get("dsgvo_mode")),
            "author_stamp": dict(author_stamp or {}),
            "maintenance_route": dict(maintenance_route or {}),
        },
    }


def _build_raptorgraph_write_event(
    memory_event: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    memory_record_ids: tuple[str, ...],
    author_stamp: Mapping[str, Any] | None = None,
    maintenance_route: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = "ready" if memory_record_ids else ("blocked" if policy.get("status") == "no_go" else "review")
    source_hash = str(memory_event.get("source_hash") or "")
    raptor_edge_id = f"uix-rg-intent-{_hash_text(source_hash + '|' + '|'.join(memory_record_ids))[-16:]}"
    memory_refs = tuple(
        build_internal_reference_dict("memory", memory_id, label="Memory oeffnen")
        for memory_id in memory_record_ids
    )
    return {
        "schema": RAPTORGRAPH_WRITE_EVENT_SCHEMA,
        "event": "universal_inbox_memory_write_intent",
        "status": status,
        "internal_ref": build_internal_reference_dict(
            "raptor_edge",
            raptor_edge_id,
            label="RaptorGraph Intent oeffnen",
        ),
        "memory_internal_refs": memory_refs,
        "source_provider": "universal_inbox",
        "classification": str(policy.get("classification") or "unknown"),
        "local_only": bool(policy.get("local_only_required")),
        "dsgvo_mode": bool(policy.get("dsgvo_mode")),
        "memory_record_ids": tuple(memory_record_ids),
        "source_hash": source_hash,
        "document_type": memory_event.get("document_type") or "unknown",
        "domain": memory_event.get("domain") or "unknown",
        "review_reasons": tuple(policy.get("review_reasons") or ()),
        "no_go_reasons": tuple(policy.get("no_go_reasons") or ()),
        "raw_content_stored": False,
        "author_stamp": dict(author_stamp or {}),
        "maintenance_route": dict(maintenance_route or {}),
        "gemma_raptor_candidate": _build_gemma_raptor_candidate(
            memory_event,
            policy,
            memory_record_ids=memory_record_ids,
            author_stamp=author_stamp,
            maintenance_route=maintenance_route,
            status=status,
        ),
    }


def _analysis_author_stamp(analysis_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), Mapping) else {}
    stamp = metadata.get("author_stamp") if isinstance(metadata, Mapping) else None
    return stamp if isinstance(stamp, Mapping) else {}


def _analysis_maintenance_route(analysis_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), Mapping) else {}
    route = metadata.get("maintenance_route") if isinstance(metadata, Mapping) else None
    if not isinstance(route, Mapping):
        return {}
    return {
        "schema": str(route.get("schema") or ""),
        "workload": str(route.get("workload") or ""),
        "action": str(route.get("action") or ""),
        "model_ref": str(route.get("model_ref") or ""),
        "provider": str(route.get("provider") or ""),
        "local_only_required": bool(route.get("local_only_required")),
        "api_escalation_allowed": bool(route.get("api_escalation_allowed")),
        "review_required": bool(route.get("review_required")),
        "reason": str(route.get("reason") or ""),
        "token_budget": int(route.get("token_budget") or 0),
        "max_input_chars": int(route.get("max_input_chars") or 0),
        "prompt_capsule_id": str(route.get("prompt_capsule_id") or ""),
        "prompt_capsule_schema": str(route.get("prompt_capsule_schema") or ""),
        "source_hashes": tuple(str(value) for value in route.get("source_hashes") or ()),
        "excerpt_hash": str(route.get("excerpt_hash") or ""),
        "raw_content_allowed": False,
        "truth_write_allowed": False,
    }


def _build_gemma_raptor_candidate(
    memory_event: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    memory_record_ids: tuple[str, ...],
    author_stamp: Mapping[str, Any] | None = None,
    maintenance_route: Mapping[str, Any] | None = None,
    status: str,
) -> dict[str, Any]:
    source_hash = str(memory_event.get("source_hash") or "")
    abstract = memory_event.get("abstract") if isinstance(memory_event.get("abstract"), Mapping) else {}
    summary_hash = _hash_text(abstract.get("summary") or "")
    classification = str(policy.get("classification") or "unknown")
    document_type = str(memory_event.get("document_type") or "unknown")
    confidence = _bounded_confidence(memory_event.get("confidence"))
    review_reasons = tuple(policy.get("review_reasons") or ())
    no_go_reasons = tuple(policy.get("no_go_reasons") or ())
    fact_id = f"uix-raptor-{_hash_text(source_hash + document_type)[-16:]}"
    return {
        "schema": "odysseus.gemma4_raptorgraph_candidate.v1",
        "status": "candidate_ready" if status == "ready" else ("blocked" if status == "blocked" else "review_required"),
        "internal_ref": build_internal_reference_dict("raptor_node", fact_id, label="Raptor-Kandidat oeffnen"),
        "memory_internal_refs": tuple(
            build_internal_reference_dict("memory", memory_id, label="Memory oeffnen")
            for memory_id in memory_record_ids
        ),
        "candidate_facts": (
            {
                "fact_id": fact_id,
                "internal_ref": build_internal_reference_dict("raptor_node", fact_id, label="Raptor-Kandidat oeffnen"),
                "kind": "document_abstraction_candidate",
                "classification": classification,
                "document_type": document_type,
                "source_hash": source_hash,
                "summary_hash": summary_hash,
                "confidence": confidence,
                "memory_record_ids": tuple(memory_record_ids),
                "memory_internal_refs": tuple(
                    build_internal_reference_dict("memory", memory_id, label="Memory oeffnen")
                    for memory_id in memory_record_ids
                ),
                "raw_content_stored": False,
            },
        ),
        "contradiction_hints": tuple(dict.fromkeys(review_reasons + no_go_reasons)),
        "provenance": {
            "source_hash": source_hash,
            "author_stamp": dict(author_stamp or {}),
            "maintenance_route": dict(maintenance_route or {}),
        },
        "requires_backend_gate": True,
        "truth_write_allowed": False,
        "raw_content_stored": False,
    }


def _memory_text(memory_event: Mapping[str, Any], *, classification: str, document_type: str) -> str:
    abstract = memory_event.get("abstract") if isinstance(memory_event.get("abstract"), Mapping) else {}
    summary = str(abstract.get("summary") or "").strip()
    if not summary:
        summary = f"Universal Inbox document classified as {classification} {document_type}."
    topics = abstract.get("topics") or ()
    if isinstance(topics, str):
        topics = (topics,)
    topic_text = ", ".join(str(topic) for topic in topics[:8]) if isinstance(topics, (tuple, list)) else ""
    lines = [
        f"Universal Inbox memory: {summary}",
        f"Classification: {classification}",
        f"Document type: {document_type}",
        "Raw document content was not stored.",
    ]
    if topic_text:
        lines.append(f"Topics: {topic_text}")
    return "\n".join(lines)


def _reject_suspicious_text(text: str) -> None:
    if _FORBIDDEN_TEXT_RE.search(text):
        raise UniversalInboxMemoryWriteIntentError("memory text appears to contain raw or secret material")
    encoded = json.dumps(text, ensure_ascii=False)
    if len(encoded) > 4000:
        raise UniversalInboxMemoryWriteIntentError("memory text exceeds safe intent length")


def _bounded_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0.0, min(1.0, confidence))


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise UniversalInboxMemoryWriteIntentError(f"{field} must be a safe token")
    return token


def _intent_correlation_id(intent: UniversalInboxMemoryWriteIntent) -> str:
    source_hash = str(intent.raptorgraph_event.get("source_hash") or "")
    if source_hash:
        return source_hash if source_hash.startswith("sha256:") else f"sha256:{source_hash}"
    return stable_payload_hash(
        {
            "status": intent.status,
            "reason": intent.reason,
            "records": len(intent.memory_records),
        }
    )


def _intent_event_status(status: str) -> str:
    if status == "ready":
        return "queued"
    if status == "review":
        return "warn"
    if status == "blocked":
        return "blocked"
    return "unknown"


def _intent_runtime_event(intent: UniversalInboxMemoryWriteIntent) -> dict[str, Any]:
    status = _intent_event_status(intent.status)
    source_hash = str(intent.raptorgraph_event.get("source_hash") or "")
    doc_id = source_hash if source_hash.startswith("sha256:") else (f"sha256:{source_hash}" if source_hash else "")
    return build_runtime_event(
        surface="universal_inbox",
        component="memory_write_intent",
        event_type="memory_write_intent",
        status=status,
        severity="warn" if status in {"warn", "blocked"} else "info",
        owner_scope="universal_inbox",
        correlation_id=_intent_correlation_id(intent),
        privacy_level="private_metadata",
        doc_id=doc_id,
        side_effects=("none",) if intent.dry_run else ("memory", "raptorgraph"),
        metadata={
            "intent_status": intent.status,
            "reason": _token(intent.reason or "unknown", field="reason"),
            "memory_records_planned": len(intent.memory_records),
            "writes_performed": bool(intent.writes_performed),
            "dry_run": bool(intent.dry_run),
            "raptor_status": _token(intent.raptorgraph_event.get("status") or "unknown", field="raptor_status"),
        },
    )
