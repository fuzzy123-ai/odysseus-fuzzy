"""Immutable, redacted evidence-chain projection of durable security audit."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


SECURITY_INCIDENT_AUDIT_SCHEMA = "odysseus.security_incident_audit.v1"
_OPAQUE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_EVENT_RE = re.compile(r"^(?:incident_created|approval_consumed|action_(?:proposed|prepared|approved|denied|expired|executing|executed|verified|failed|rolled_back))$")
_TERMINAL_EVIDENCE = {"action_executed": "receipt_ref", "action_verified": "verification_ref", "action_failed": "failure_ref", "action_rolled_back": "rollback_ref"}


class SecurityIncidentAuditError(ValueError):
    """Content-free rejection of unsafe or unbound durable audit input."""


@dataclass(frozen=True, slots=True)
class AuditChainEntry:
    sequence: int
    incident_ref: str
    action_ref: str
    action_version: int
    event_type: str
    reference: str
    receipt_ref: str
    verification_ref: str
    failure_ref: str
    rollback_ref: str
    occurred_at: float


@dataclass(frozen=True, slots=True)
class AuditChainSnapshot:
    entries: tuple[AuditChainEntry, ...]
    chain_ref: str
    schema: str = SECURITY_INCIDENT_AUDIT_SCHEMA


class SecurityIncidentAuditChain:
    """Project only opaque durable evidence, never IDs or raw source data."""

    @staticmethod
    def snapshot(store: Any, *, action_id: Any | None = None) -> AuditChainSnapshot:
        try:
            records = tuple(store.audit_events(action_id))
        except Exception:
            raise SecurityIncidentAuditError("audit unavailable") from None
        evidence_by_action: dict[str, Any] = {}
        entries: list[AuditChainEntry] = []
        previous_sequence = 0
        for record in records:
            raw_action_id = getattr(record, "action_id", None)
            # `get_action_evidence()` reaches the store's expiry-maintaining
            # getter.  Never use it for proposed/prepared/approved history:
            # audit snapshots must remain a read-only view of that history.
            needs_evidence = getattr(record, "event_type", "") in _TERMINAL_EVIDENCE
            if needs_evidence and raw_action_id is not None and raw_action_id not in evidence_by_action:
                try:
                    evidence_by_action[raw_action_id] = store.get_action_evidence(raw_action_id)
                except Exception:
                    raise SecurityIncidentAuditError("audit evidence unavailable") from None
            entry = SecurityIncidentAuditChain._entry(record, evidence_by_action.get(raw_action_id))
            if entry.sequence <= previous_sequence:
                raise SecurityIncidentAuditError("audit chain invalid")
            previous_sequence = entry.sequence
            entries.append(entry)
        immutable = tuple(entries)
        snapshot = AuditChainSnapshot(immutable, SecurityIncidentAuditChain._chain_ref(immutable))
        SecurityIncidentAuditChain.verify(snapshot)
        return snapshot

    @staticmethod
    def verify(snapshot: Any) -> None:
        if type(snapshot) is not AuditChainSnapshot or snapshot.schema != SECURITY_INCIDENT_AUDIT_SCHEMA or not isinstance(snapshot.entries, tuple):
            raise SecurityIncidentAuditError("audit chain invalid")
        previous_sequence = 0
        for entry in snapshot.entries:
            if type(entry) is not AuditChainEntry or entry.sequence <= previous_sequence or not _OPAQUE_REF_RE.fullmatch(entry.incident_ref) or not _EVENT_RE.fullmatch(entry.event_type) or type(entry.action_version) is not int or entry.action_version < 0 or isinstance(entry.occurred_at, bool) or not isinstance(entry.occurred_at, float):
                raise SecurityIncidentAuditError("audit chain invalid")
            if (entry.action_ref and not _OPAQUE_REF_RE.fullmatch(entry.action_ref)) or not _OPAQUE_REF_RE.fullmatch(entry.reference):
                raise SecurityIncidentAuditError("audit chain invalid")
            fields = {"receipt_ref": entry.receipt_ref, "verification_ref": entry.verification_ref, "failure_ref": entry.failure_ref, "rollback_ref": entry.rollback_ref}
            required = _TERMINAL_EVIDENCE.get(entry.event_type)
            if required is None:
                if any(fields.values()):
                    raise SecurityIncidentAuditError("audit chain invalid")
            elif not entry.action_ref or not fields[required] or any(value for field, value in fields.items() if field != required):
                raise SecurityIncidentAuditError("audit chain invalid")
            if any(value and not _OPAQUE_REF_RE.fullmatch(value) for value in fields.values()):
                raise SecurityIncidentAuditError("audit chain invalid")
            previous_sequence = entry.sequence
        if snapshot.chain_ref != SecurityIncidentAuditChain._chain_ref(snapshot.entries):
            raise SecurityIncidentAuditError("audit chain invalid")

    @staticmethod
    def projection(snapshot: AuditChainSnapshot) -> dict[str, Any]:
        SecurityIncidentAuditChain.verify(snapshot)
        return {"schema": SECURITY_INCIDENT_AUDIT_SCHEMA, "chain_ref": snapshot.chain_ref, "entry_count": len(snapshot.entries), "entries": tuple({"sequence": item.sequence, "incident_ref": item.incident_ref, "action_ref": item.action_ref, "action_version": item.action_version, "event_type": item.event_type, "reference": item.reference, "receipt_ref": item.receipt_ref, "verification_ref": item.verification_ref, "failure_ref": item.failure_ref, "rollback_ref": item.rollback_ref, "occurred_at": item.occurred_at} for item in snapshot.entries), "raw_content_visible": False}

    @staticmethod
    def _entry(record: Any, evidence: Any) -> AuditChainEntry:
        try:
            sequence, incident_id, action_id, action_version, event_type, reference, occurred_at = record.sequence, record.incident_id, record.action_id, record.action_version, record.event_type, record.reference, record.occurred_at
            if type(sequence) is not int or sequence < 1 or not isinstance(incident_id, str) or not isinstance(action_id, (str, type(None))) or type(action_version) is not int or action_version < 0 or not isinstance(event_type, str) or not _EVENT_RE.fullmatch(event_type) or not isinstance(reference, str) or not _OPAQUE_REF_RE.fullmatch(reference) or isinstance(occurred_at, bool) or not isinstance(occurred_at, (int, float)):
                raise SecurityIncidentAuditError("audit chain invalid")
            values = {"receipt_ref": "", "verification_ref": "", "failure_ref": "", "rollback_ref": ""}
            required = _TERMINAL_EVIDENCE.get(event_type)
            if required is not None:
                if evidence is None:
                    raise SecurityIncidentAuditError("audit evidence unavailable")
                values[required] = getattr(evidence, required, "")
            return AuditChainEntry(sequence, SecurityIncidentAuditChain._opaque("incident", incident_id), "" if action_id is None else SecurityIncidentAuditChain._opaque("action", action_id), action_version, event_type, reference, values["receipt_ref"], values["verification_ref"], values["failure_ref"], values["rollback_ref"], float(occurred_at))
        except SecurityIncidentAuditError:
            raise
        except Exception:
            raise SecurityIncidentAuditError("audit chain invalid") from None

    @staticmethod
    def _opaque(kind: str, value: str) -> str:
        return kind + ":sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _chain_ref(entries: tuple[AuditChainEntry, ...]) -> str:
        body = json.dumps([(entry.sequence, entry.incident_ref, entry.action_ref, entry.action_version, entry.event_type, entry.reference, entry.receipt_ref, entry.verification_ref, entry.failure_ref, entry.rollback_ref, entry.occurred_at) for entry in entries], separators=(",", ":"), ensure_ascii=True)
        return "audit-chain:sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


__all__ = ["AuditChainEntry", "AuditChainSnapshot", "SECURITY_INCIDENT_AUDIT_SCHEMA", "SecurityIncidentAuditChain", "SecurityIncidentAuditError"]
