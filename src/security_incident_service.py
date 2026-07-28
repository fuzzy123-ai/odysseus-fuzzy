"""The single store-facing creation boundary for brokered evidence."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from typing import Mapping, Any
from src.security_evidence_broker import SecurityEvidenceEnvelope, build_security_evidence_envelope
from src.security_incident_store import IncidentRecord, SecurityIncidentStore

@dataclass(frozen=True, slots=True)
class BrokeredIncident:
    incident: IncidentRecord
    evidence: SecurityEvidenceEnvelope
class SecurityIncidentService:
    def __init__(self, store: SecurityIncidentStore) -> None: self._store = store
    def create_from_evidence(self, projection: Mapping[str, Any]) -> BrokeredIncident:
        evidence = build_security_evidence_envelope(projection)
        digest = evidence.dedupe_ref.rsplit(":", 1)[-1]
        incident = self._store.create_incident(incident_id=f"inc-{digest[:24]}", incident_ref=evidence.evidence_ref, audit_ref=f"audit:sha256:{hashlib.sha256(evidence.correlation_ref.encode()).hexdigest()}")
        return BrokeredIncident(incident, evidence)
__all__ = ["BrokeredIncident","SecurityIncidentService"]
