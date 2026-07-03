"""Offline Universal Inbox pipeline run envelope.

This module links the dry-run stage results for discovery, ledger, extraction,
analysis, memory abstraction, routing, and policy gating. It never touches the
filesystem or provider APIs; callers pass already-produced status objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from src.runtime_event_envelope import build_runtime_event, stable_payload_hash
from src.universal_inbox_memory import (
    FORBIDDEN_MEMORY_KEYS,
    UniversalInboxMemoryAbstraction,
)


PIPELINE_SCHEMA = "odysseus.universal_inbox.pipeline_run.v1"
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_KEY_PATTERNS = (
    "raw_text",
    "content",
    "body",
    "payload",
    "bytes",
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
)
_FORBIDDEN_PIPELINE_KEYS = frozenset(
    {
        *FORBIDDEN_MEMORY_KEYS,
        "full_text",
        "email_body",
        "raw_body",
        "message_body",
        "document_text",
    }
)


class UniversalInboxPipelineError(ValueError):
    """Raised when a pipeline run envelope would be unsafe to serialize."""


@dataclass(frozen=True)
class UniversalInboxPipelineStatus:
    stage: str
    status: str
    reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        stage: str,
        payload: "UniversalInboxPipelineStatus | Mapping[str, Any] | str",
    ) -> "UniversalInboxPipelineStatus":
        if isinstance(payload, UniversalInboxPipelineStatus):
            if payload.stage != stage:
                raise UniversalInboxPipelineError("status stage mismatch")
            return payload
        if isinstance(payload, str):
            return cls(stage=stage, status=payload)
        if not isinstance(payload, Mapping):
            raise UniversalInboxPipelineError("pipeline status must be a mapping, string, or status")

        return cls(
            stage=stage,
            status=str(payload.get("status") or "unknown"),
            reasons=_normalize_reason_codes(payload.get("reasons") or ()),
            metadata=payload.get("metadata") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        metadata, blocked_count = _sanitize_mapping(self.metadata)
        payload = {
            "stage": _normalize_token(self.stage, field="stage"),
            "status": _normalize_token(self.status, field="status"),
            "reasons": self.reasons,
            "metadata": metadata,
        }
        if blocked_count:
            payload["blocked_field_count"] = blocked_count
        return payload


@dataclass(frozen=True)
class UniversalInboxExtractionPacket:
    """Runtime-only extraction data that must not be persisted by the envelope."""

    status: str = "completed"
    abstract: Mapping[str, Any] = field(default_factory=dict)
    raw_packet: Mapping[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    ephemeral: bool = True

    @classmethod
    def from_mapping(
        cls,
        payload: "UniversalInboxExtractionPacket | Mapping[str, Any]",
    ) -> "UniversalInboxExtractionPacket":
        if isinstance(payload, UniversalInboxExtractionPacket):
            return payload
        if not isinstance(payload, Mapping):
            raise UniversalInboxPipelineError("extraction packet must be a mapping or packet")
        return cls(
            status=str(payload.get("status") or "completed"),
            abstract=payload.get("abstract") or {},
            raw_packet=payload.get("raw_packet") or payload,
            reasons=_normalize_reason_codes(payload.get("reasons") or ()),
            ephemeral=bool(payload.get("ephemeral", True)),
        )

    def safe_abstract(self) -> dict[str, Any]:
        abstract, _ = _sanitize_mapping(self.abstract)
        return abstract

    def to_status(self) -> UniversalInboxPipelineStatus:
        _, blocked_count = _sanitize_mapping(self.raw_packet)
        metadata: dict[str, Any] = {
            "ephemeral": True,
            "persisted": False,
            "abstract_available": bool(self.safe_abstract()),
        }
        if blocked_count:
            metadata["blocked_field_count"] = blocked_count
        return UniversalInboxPipelineStatus(
            stage="extraction",
            status=self.status,
            reasons=self.reasons,
            metadata=metadata,
        )


@dataclass(frozen=True)
class UniversalInboxPolicyGate:
    status: str
    review_reasons: tuple[str, ...] = ()
    no_go_reasons: tuple[str, ...] = ()

    @classmethod
    def derive(
        cls,
        statuses: tuple[UniversalInboxPipelineStatus, ...],
        routing_decision: Any,
        memory_event: Mapping[str, Any],
    ) -> "UniversalInboxPolicyGate":
        no_go_reasons: list[str] = []
        review_reasons: list[str] = []

        for status in statuses:
            normalized_status = _normalize_token(status.status, field="status")
            if normalized_status in {"failed", "blocked", "no_go"}:
                no_go_reasons.extend(status.reasons or (f"{status.stage}_failed",))
            elif normalized_status in {"needs_review", "warning"}:
                review_reasons.extend(status.reasons or (f"{status.stage}_needs_review",))
            if int(status.metadata.get("blocked_field_count") or 0) > 0:
                review_reasons.append(f"{status.stage}_fields_blocked")

        routing_payload = _routing_payload(routing_decision)
        review_reasons.extend(_normalize_reason_codes(routing_payload.get("review_reasons") or ()))
        if str(routing_payload.get("status") or "") == "needs_review":
            review_reasons.append("routing_needs_review")
        if int(memory_event.get("blocked_field_count") or 0) > 0:
            review_reasons.append("memory_fields_blocked")

        no_go = tuple(dict.fromkeys(no_go_reasons))
        review = tuple(dict.fromkeys(review_reasons))
        if no_go:
            status = "no_go"
        elif review:
            status = "review"
        else:
            status = "go"
        return cls(status=status, review_reasons=review, no_go_reasons=no_go)

    @classmethod
    def from_mapping(
        cls,
        payload: "UniversalInboxPolicyGate | Mapping[str, Any]",
    ) -> "UniversalInboxPolicyGate":
        if isinstance(payload, UniversalInboxPolicyGate):
            return payload
        if not isinstance(payload, Mapping):
            raise UniversalInboxPipelineError("policy gate must be a mapping or gate")
        return cls(
            status=str(payload.get("status") or "unknown"),
            review_reasons=_normalize_reason_codes(payload.get("review_reasons") or ()),
            no_go_reasons=_normalize_reason_codes(payload.get("no_go_reasons") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": "policy_gate",
            "status": _normalize_token(self.status, field="policy gate status"),
            "review_reasons": self.review_reasons,
            "no_go_reasons": self.no_go_reasons,
        }


@dataclass(frozen=True)
class UniversalInboxPipelineRun:
    run_id: str
    discovery: UniversalInboxPipelineStatus
    ledger: UniversalInboxPipelineStatus
    extraction_packet: UniversalInboxExtractionPacket
    analysis: UniversalInboxPipelineStatus
    routing_decision: Any
    memory_abstraction: UniversalInboxMemoryAbstraction
    policy_gate: UniversalInboxPolicyGate
    schema: str = PIPELINE_SCHEMA

    @classmethod
    def from_components(
        cls,
        *,
        run_id: str,
        discovery: UniversalInboxPipelineStatus | Mapping[str, Any] | str,
        ledger: UniversalInboxPipelineStatus | Mapping[str, Any] | str,
        extraction_packet: UniversalInboxExtractionPacket | Mapping[str, Any],
        analysis: UniversalInboxPipelineStatus | Mapping[str, Any] | str,
        routing_decision: Any,
        memory_abstraction: UniversalInboxMemoryAbstraction | None = None,
        policy_gate: UniversalInboxPolicyGate | Mapping[str, Any] | None = None,
    ) -> "UniversalInboxPipelineRun":
        extraction = UniversalInboxExtractionPacket.from_mapping(extraction_packet)
        if not extraction.ephemeral:
            raise UniversalInboxPipelineError("extraction packet must be ephemeral")

        memory = memory_abstraction or UniversalInboxMemoryAbstraction.from_routing_decision(
            routing_decision,
            abstract=extraction.safe_abstract(),
        )
        statuses = (
            UniversalInboxPipelineStatus.from_mapping("discovery", discovery),
            UniversalInboxPipelineStatus.from_mapping("ledger", ledger),
            extraction.to_status(),
            UniversalInboxPipelineStatus.from_mapping("analysis", analysis),
            _status_from_routing(routing_decision),
            _status_from_memory_event(memory.to_raptorgraph_event()),
        )
        gate = (
            UniversalInboxPolicyGate.from_mapping(policy_gate)
            if policy_gate is not None
            else UniversalInboxPolicyGate.derive(statuses, routing_decision, memory.to_raptorgraph_event())
        )
        return cls(
            run_id=_normalize_run_id(run_id),
            discovery=statuses[0],
            ledger=statuses[1],
            extraction_packet=extraction,
            analysis=statuses[3],
            routing_decision=routing_decision,
            memory_abstraction=memory,
            policy_gate=gate,
        )

    def to_dict(self) -> dict[str, Any]:
        routing_payload, routing_blocked_count = _sanitize_mapping(_routing_payload(self.routing_decision))
        memory_event, memory_blocked_count = _sanitize_mapping(
            self.memory_abstraction.to_raptorgraph_event()
        )
        stages = {
            "discovery": self.discovery.to_dict(),
            "ledger": self.ledger.to_dict(),
            "extraction": self.extraction_packet.to_status().to_dict(),
            "analysis": self.analysis.to_dict(),
            "routing": _status_from_routing(self.routing_decision).to_dict(),
            "memory_abstraction": _status_from_memory_event(memory_event).to_dict(),
            "policy_gate": self.policy_gate.to_dict(),
        }

        blocked_count = routing_blocked_count + memory_blocked_count
        payload = {
            "schema": self.schema,
            "run_id": self.run_id,
            "correlation_id": _pipeline_correlation_id(self.run_id),
            "stages": stages,
            "routing_decision": routing_payload,
            "memory_abstraction_event": memory_event,
            "policy_gate": self.policy_gate.to_dict(),
            "review_reasons": self.policy_gate.review_reasons,
            "no_go_reasons": self.policy_gate.no_go_reasons,
            "runtime_event": _pipeline_runtime_event(
                run_id=self.run_id,
                policy_gate=self.policy_gate,
                blocked_count=blocked_count,
            ),
        }
        if blocked_count:
            payload["blocked_field_count"] = blocked_count
        return payload


def build_universal_inbox_pipeline_run(**kwargs: Any) -> UniversalInboxPipelineRun:
    """Build a pipeline envelope from already-computed offline components."""

    return UniversalInboxPipelineRun.from_components(**kwargs)


def _status_from_routing(routing_decision: Any) -> UniversalInboxPipelineStatus:
    payload = _routing_payload(routing_decision)
    return UniversalInboxPipelineStatus(
        stage="routing",
        status=str(payload.get("status") or "unknown"),
        reasons=_normalize_reason_codes(payload.get("review_reasons") or ()),
        metadata={
            "decision": payload.get("decision") or "",
            "safe_operation": payload.get("safe_operation") or "",
            "copy_only": bool(payload.get("copy_only", True)),
            "delete_original": bool(payload.get("delete_original", False)),
            "overwrite_existing": bool(payload.get("overwrite_existing", False)),
        },
    )


def _status_from_memory_event(memory_event: Mapping[str, Any]) -> UniversalInboxPipelineStatus:
    return UniversalInboxPipelineStatus(
        stage="memory_abstraction",
        status="completed",
        reasons=("fields_blocked",) if int(memory_event.get("blocked_field_count") or 0) else (),
        metadata={
            "event": memory_event.get("event") or "",
            "blocked_field_count": int(memory_event.get("blocked_field_count") or 0),
        },
    )


def _routing_payload(routing_decision: Any) -> Mapping[str, Any]:
    payload = routing_decision.to_dict() if hasattr(routing_decision, "to_dict") else routing_decision
    if not isinstance(payload, Mapping):
        raise UniversalInboxPipelineError("routing decision must be a mapping or expose to_dict()")
    return payload


def _sanitize_mapping(payload: Mapping[str, Any] | Any) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, Mapping):
        raise UniversalInboxPipelineError("pipeline payload must be a mapping")

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


def _sanitize_sequence(values: Any) -> tuple[tuple[Any, ...], int]:
    if isinstance(values, str):
        return (values,), 0
    if not isinstance(values, (tuple, list)):
        return (), 0

    sanitized: list[Any] = []
    blocked_count = 0
    for value in values:
        clean_value, value_blocked_count = _sanitize_value(value)
        sanitized.append(clean_value)
        blocked_count += value_blocked_count
    return tuple(sanitized), blocked_count


def _sanitize_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (tuple, list)):
        return _sanitize_sequence(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value, 0
    return str(value), 0


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _FORBIDDEN_PIPELINE_KEYS:
        return True
    return any(pattern in normalized for pattern in _SENSITIVE_KEY_PATTERNS)


def _normalize_reason_codes(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(dict.fromkeys(_normalize_token(value, field="reason") for value in values))


def _normalize_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise UniversalInboxPipelineError(f"{field} must be a safe token")
    return token


def _normalize_run_id(value: Any) -> str:
    run_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", run_id):
        raise UniversalInboxPipelineError("run_id must be a safe identifier")
    return run_id


def _pipeline_correlation_id(run_id: str) -> str:
    return stable_payload_hash({"surface": "universal_inbox", "run_id": run_id})


def _pipeline_event_status(policy_gate: UniversalInboxPolicyGate) -> str:
    if policy_gate.status == "go":
        return "success"
    if policy_gate.status == "review":
        return "warn"
    if policy_gate.status == "no_go":
        return "blocked"
    return "unknown"


def _pipeline_runtime_event(
    *,
    run_id: str,
    policy_gate: UniversalInboxPolicyGate,
    blocked_count: int,
) -> dict[str, Any]:
    status = _pipeline_event_status(policy_gate)
    return build_runtime_event(
        surface="universal_inbox",
        component="pipeline",
        event_type="pipeline_run",
        status=status,
        severity="warn" if status in {"warn", "blocked"} else "info",
        owner_scope="universal_inbox",
        correlation_id=_pipeline_correlation_id(run_id),
        privacy_level="private_metadata",
        run_id=run_id,
        metadata={
            "policy_gate": policy_gate.status,
            "review_reason_count": len(policy_gate.review_reasons),
            "no_go_reason_count": len(policy_gate.no_go_reasons),
            "blocked_field_count": blocked_count,
        },
    )
