"""Closed, offline and content-free evidence intake boundary."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

SECURITY_EVIDENCE_SCHEMA = "odysseus.security_evidence.v1"
SOURCES = frozenset({"auth_outcome", "crowdsec_decision", "reverse_proxy", "prometheus", "loki", "runtime_event", "debian_redacted_probe"})
STATUSES = frozenset({"ok", "success", "failed", "blocked", "warn", "unknown", "not_applicable"})
SEVERITIES = frozenset({"info", "notice", "warn", "error", "critical"})
_REF = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_FORBIDDEN = frozenset({"environment", "env", "log", "provider", "credential", "token", "cookie", "private", "path", "ip", "content", "password", "authorization", "secret", "raw", "body", "header"})
_MARKERS = ("authorization", "bearer ", "api_key", "password", "cookie", "token", "private_document", "private_email", "raw log", "raw output")

class SecurityEvidenceError(ValueError):
    """A content-free rejection at the evidence boundary."""

@dataclass(frozen=True, slots=True)
class SecurityEvidenceEnvelope:
    source: str; event_type: str; status: str; severity: str
    dimensions: tuple[tuple[str, str], ...]
    references: tuple[tuple[str, str], ...]
    measurements: tuple[tuple[str, int], ...]
    evidence_ref: str; correlation_ref: str; dedupe_ref: str
    schema: str = SECURITY_EVIDENCE_SCHEMA
    raw_content_visible: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "source": self.source, "event_type": self.event_type, "status": self.status, "severity": self.severity, "dimensions": dict(self.dimensions), "references": dict(self.references), "measurements": dict(self.measurements), "evidence_ref": self.evidence_ref, "correlation_ref": self.correlation_ref, "dedupe_ref": self.dedupe_ref, "raw_content_visible": False}

class SecurityEvidenceBroker:
    def envelope(self, projection: Mapping[str, Any]) -> SecurityEvidenceEnvelope: return build_security_evidence_envelope(projection)
    def correlation_key(self, projection: Mapping[str, Any]) -> str: return self.envelope(projection).correlation_ref
    def dedupe_key(self, projection: Mapping[str, Any]) -> str: return self.envelope(projection).dedupe_ref

def build_security_evidence_envelope(projection: Mapping[str, Any]) -> SecurityEvidenceEnvelope:
    if not isinstance(projection, Mapping): raise SecurityEvidenceError("evidence projection must be a mapping")
    _reject_forbidden(projection)
    if set(projection) != {"source", "event_type", "status", "severity", "dimensions", "references", "measurements"}: raise SecurityEvidenceError("evidence projection fields are not allowlisted")
    source = _enum(projection["source"], SOURCES, "source")
    event_type = _safe_token(projection["event_type"], "event_type")
    status = _enum(projection["status"], STATUSES, "status")
    severity = _enum(projection["severity"], SEVERITIES, "severity")
    dimensions = _string_map(projection["dimensions"], "dimension", 8)
    references = _ref_map(projection["references"], 8)
    measurements = _int_map(projection["measurements"], 12)
    _source_contract(source, event_type, status, severity, dict(dimensions), dict(references), dict(measurements))
    full = {"source": source, "event_type": event_type, "status": status, "severity": severity, "dimensions": dict(dimensions), "references": dict(references), "measurements": dict(measurements)}
    stable = {"source": source, "event_type": event_type, "dimensions": _stable_dimensions(source, dict(dimensions)), "references": _stable_references(source, dict(references))}
    return SecurityEvidenceEnvelope(source, event_type, status, severity, dimensions, references, measurements, _digest("evidence", full), _digest("correlation", stable), _digest("dedupe", full))

def is_opaque_digest_ref(value: Any) -> bool: return isinstance(value, str) and bool(_REF.fullmatch(value))
def _digest(kind: str, value: Mapping[str, Any]) -> str: return f"{kind}:sha256:{hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()}"
def _safe_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value.strip().lower()) or _bad_name(value.strip().lower()): raise SecurityEvidenceError(f"invalid {field}")
    return value.strip().lower()
def _enum(value: Any, values: frozenset[str], field: str) -> str:
    token = _safe_token(value, field)
    if token not in values: raise SecurityEvidenceError(f"unsupported {field}")
    return token
def _string_map(value: Any, name: str, limit: int) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or len(value) > limit: raise SecurityEvidenceError(f"invalid {name} mapping")
    return tuple(sorted((_safe_token(k, name), _safe_token(v, name)) for k,v in value.items()))
def _ref_map(value: Any, limit: int) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or len(value) > limit: raise SecurityEvidenceError("invalid reference mapping")
    result = tuple(sorted((_safe_token(k, "reference"), v) for k,v in value.items()))
    if any(not is_opaque_digest_ref(v) for _,v in result): raise SecurityEvidenceError("invalid opaque reference")
    return result
def _int_map(value: Any, limit: int) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping) or len(value) > limit: raise SecurityEvidenceError("invalid measurement mapping")
    result = tuple(sorted((_safe_token(k, "measurement"), v) for k,v in value.items()))
    if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1_000_000 for _,v in result): raise SecurityEvidenceError("invalid bounded measurement")
    return result
def _source_contract(source: str, event: str, status: str, severity: str, dim: dict[str,str], refs: dict[str,str], counts: dict[str,int]) -> None:
    allowed = {"auth_outcome": ({"authentication"}, {"outcome","source_familiarity","session_created"}, {"principal_ref"}, {"affected_ref_0","affected_ref_1","affected_ref_2","affected_ref_3"}), "crowdsec_decision": ({"decision_summary"}, {"decision_class","window"}, {"evidence_ref","scope_ref"}, set()), "reverse_proxy": ({"aggregate"}, {"surface"}, set(), set()), "prometheus": ({"metric_projection"}, {"result_type"}, {"query_ref"}, set()), "loki": ({"stream_projection"}, {"result_type"}, {"query_ref"}, set()), "runtime_event": (set(), {"surface","component"}, {"event_ref","correlation_ref"}, set()), "debian_redacted_probe": ({"readiness_projection"}, {"probe_state"}, set(), set())}
    events, required_dim, required_refs, optional_refs = allowed[source]
    if events and event not in events or not required_dim <= set(dim) or not required_refs <= set(refs) or not set(refs) <= required_refs | optional_refs: raise SecurityEvidenceError("source contract mismatch")
    if source == "auth_outcome" and (set(dim)!={"outcome","source_familiarity","session_created"} or set(counts)!={"event_count"} or status != dim["outcome"] or dim["outcome"] not in {"success","failed","blocked","unknown","not_applicable"} or dim["source_familiarity"] not in {"familiar","unfamiliar","unknown","not_applicable"} or dim["session_created"] not in {"yes","no","not_applicable"}): raise SecurityEvidenceError("invalid auth contract")
    if source == "crowdsec_decision" and (set(dim)!={"decision_class","window"} or set(counts)!={"decision_count"} or status != ("not_applicable" if dim["decision_class"] == "not_applicable" else "success") or dim["decision_class"] not in {"ban","captcha","alert","unknown","not_applicable"} or dim["window"] not in {"minute","hour","day","not_applicable"}): raise SecurityEvidenceError("invalid CrowdSec contract")
    if source == "reverse_proxy" and (set(dim)!={"surface"} or set(counts)!={"request_count","error_count"} or dim["surface"] not in {"ingress","api","webhook","admin","unknown"}): raise SecurityEvidenceError("invalid reverse proxy contract")
    if source in {"prometheus","loki"} and (set(dim)!={"result_type"} or set(counts) != ({"series_count","sample_count"} if source == "prometheus" else {"stream_count","line_count"}) or dim["result_type"] not in ({"vector","matrix","scalar","string","unknown"} if source == "prometheus" else {"streams","unknown"})): raise SecurityEvidenceError("invalid observability contract")
    if source == "runtime_event" and (event not in {"runtime_event","auth_failure","login_attempt","endpoint_probe","service_down","redaction_indicator","telegram_rate_limit"} or set(dim)!={"surface","component"} or set(counts)!={"duration_ms","retry_count"} or dim["surface"] not in {"auth","http","telegram","ops","scheduler","security","unknown"} or dim["component"] not in {"login","router","polling","podman","redaction","unknown"}): raise SecurityEvidenceError("invalid runtime event contract")
    if source == "debian_redacted_probe" and (set(dim)!={"probe_state"} or set(counts)!={"entry_count","unexpected_sensitive_count","configured_present_count","container_running"} or status != dim["probe_state"] or dim["probe_state"] not in {"ok","blocked"}): raise SecurityEvidenceError("invalid probe contract")
    if not counts: raise SecurityEvidenceError("source requires bounded measurements")
def _bad_name(value: str) -> bool: return value in _FORBIDDEN or any(x in _FORBIDDEN for x in value.split("_"))
def _stable_references(source: str, refs: Mapping[str,str]) -> dict[str,str]:
    keys={"auth_outcome": {"principal_ref"}, "crowdsec_decision": {"scope_ref"}, "prometheus": {"query_ref"}, "loki": {"query_ref"}, "runtime_event": {"correlation_ref"}}.get(source, set())
    return {key: refs[key] for key in sorted(keys)}
def _stable_dimensions(source: str, dimensions: Mapping[str,str]) -> dict[str,str]:
    keys={"reverse_proxy": {"surface"}, "runtime_event": {"surface","component"}}.get(source, set())
    return {key: dimensions[key] for key in sorted(keys)}
def _reject_forbidden(value: Any) -> None:
    if isinstance(value, Mapping):
        for k,v in value.items():
            if not isinstance(k,str) or _bad_name(k.lower()): raise SecurityEvidenceError("evidence contains a forbidden field")
            _reject_forbidden(v)
    elif isinstance(value, (list,tuple,set)):
        for v in value: _reject_forbidden(v)
    elif isinstance(value,str) and (any(x in value.lower() for x in _MARKERS) or "\\" in value or "/" in value): raise SecurityEvidenceError("evidence contains forbidden content")

__all__ = ["SECURITY_EVIDENCE_SCHEMA","SecurityEvidenceBroker","SecurityEvidenceEnvelope","SecurityEvidenceError","build_security_evidence_envelope","is_opaque_digest_ref"]
