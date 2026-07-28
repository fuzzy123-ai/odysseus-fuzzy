"""Deterministic, redacted explanations for brokered security incidents.

This is a read-only projection.  It accepts only existing incident models and
validated SIRP-02 evidence envelopes; it does not create authority, dispatch
an action, or infer an effect without a receipt.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Iterable, Mapping

from src.security_evidence_broker import (
    SECURITY_EVIDENCE_SCHEMA,
    SecurityEvidenceEnvelope,
    build_security_evidence_envelope,
    is_opaque_digest_ref,
)
from src.security_incident_model import SECURITY_INCIDENT_SCHEMA, SecurityIncidentModelError, summarize_incident
from src.security_response_policy import SecurityResponsePolicyError, action_policy_disposition, decide_incident_response


SECURITY_INCIDENT_EXPLANATION_SCHEMA = "odysseus.security_incident_explanation.v1"
EXPLANATION_CLASSIFIER_FAMILY = "deterministic_offline_rules"
EXPLANATION_CLASSIFIER_REVISION = "sirp03-r1"
_REF = re.compile(r"^([a-z][a-z0-9_-]{1,31}):sha256:[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset({"token", "tokens", "cookie", "cookies", "authorization", "header", "headers", "chat_id", "private_document", "private_email", "raw_provider", "command", "commands", "raw_content"})
_FORBIDDEN_MARKERS = ("authorization:", "bearer ", "api_key", "password=", "cookie:", "token=", "chat_id", "private_document_text", "private_email_body", "raw provider", "raw_output")
_PATH = re.compile(r"(?:[a-z]:[\\/]|/(?:home|users|var|mnt|srv|opt)/|~[\\/])", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class SecurityIncidentExplanationError(ValueError):
    """Raised when an explanation input would cross the redaction boundary."""


def build_security_incident_explanation(
    incident: Mapping[str, Any],
    *,
    evidence: Iterable[SecurityEvidenceEnvelope | Mapping[str, Any]] = (),
    approved_gates: Iterable[Any] = (),
    classifier_family: str = EXPLANATION_CLASSIFIER_FAMILY,
    classifier_revision: str = EXPLANATION_CLASSIFIER_REVISION,
) -> dict[str, Any]:
    """Build the exact, bounded SIRP explanation contract offline."""

    _reject_unsafe(incident)
    if not isinstance(incident, Mapping) or incident.get("schema") != SECURITY_INCIDENT_SCHEMA or incident.get("raw_content_visible") is not False:
        raise SecurityIncidentExplanationError("incident is not a redacted security incident")
    try:
        incident_summary = summarize_incident(incident)
        policy = decide_incident_response(incident, approved_gates=approved_gates, incident_mode=True)
    except (SecurityIncidentModelError, SecurityResponsePolicyError) as exc:
        raise SecurityIncidentExplanationError("incident policy validation failed") from exc
    supplied_envelopes = tuple(sorted((_validated_envelope(item) for item in evidence), key=lambda item: item["evidence_ref"]))
    if len(supplied_envelopes) > 8:
        raise SecurityIncidentExplanationError("too many evidence envelopes")
    _ensure_unique_envelopes(supplied_envelopes)
    _validate_incident_collections(incident)
    if classifier_family != EXPLANATION_CLASSIFIER_FAMILY or classifier_revision != EXPLANATION_CLASSIFIER_REVISION:
        raise SecurityIncidentExplanationError("classifier provenance must be the fixed deterministic revision")
    family = EXPLANATION_CLASSIFIER_FAMILY
    revision = EXPLANATION_CLASSIFIER_REVISION
    envelopes, binding_gaps, declared_missing_count, nonopaque_declared_count = _bound_envelopes(incident, supplied_envelopes)
    missing, conflicts = _evidence_gaps(incident, envelopes)
    missing = tuple(sorted(set(missing) | set(binding_gaps)))
    auth = _auth_projection(envelopes, conflicts)
    actions = _action_rationale(incident, policy)
    incident_version = _incident_version(incident)
    unknowns = {"execution_receipt_not_available", "verification_not_available", *missing, *conflicts}
    if incident_version == "not_available":
        unknowns.add("incident_version_not_available")
    containment = _containment_state(incident)
    if containment["state"] == "unknown":
        unknowns.add("containment_claim_without_receipt_authority")
    observation_window = _observation_window(incident, len(envelopes))
    if observation_window["start"] == "not_available" or observation_window["end"] == "not_available":
        unknowns.add("observation_time_not_available")
        missing = tuple(sorted(set(missing) | {"observation_time_not_available"}))
    known_unknowns = tuple(sorted(unknowns))
    explanation = {
        "schema": SECURITY_INCIDENT_EXPLANATION_SCHEMA,
        "incident_id": incident_summary["incident_id"],
        "incident_version": incident_version,
        "evidence_refs": tuple(item["evidence_ref"] for item in envelopes),
        "observation_window": observation_window,
        "classifier_family_and_revision": {"family": family, "revision": revision},
        "observed_signal_summary": {"trigger": _summary(incident.get("trigger"), "trigger"), "severity": incident_summary["severity"], "evidence_count": len(envelopes)},
        "affected_surfaces": tuple(sorted(set(incident_summary["affected_surfaces"])))[:8],
        "confidence_and_level": {"confidence": incident_summary["confidence"], "level": incident_summary["level"], "level_name": incident_summary["level_name"]},
        "policy_decision_and_reason": {"decision": policy["decision"], "reason": policy["reason"]},
        "recommended_action_ids": tuple(action["action_id"] for action in actions),
        "why_each_action_is_allowed_gated_blocked_or_denied": actions,
        "missing_or_conflicting_evidence": {"missing": missing, "conflicting": conflicts, "declared_evidence_missing_count": declared_missing_count, "nonopaque_declared_evidence_count": nonopaque_declared_count},
        "operator_next_step": "review_redacted_evidence_and_policy",
        "auth_outcome": auth["outcome"],
        "principal_ref": auth["principal_ref"],
        "source_familiarity": auth["source_familiarity"],
        "session_created": auth["session_created"],
        "affected_session_refs": auth["affected_session_refs"],
        "containment_state": containment,
        "evidence_freshness": {"state": "unknown", "brokered_evidence_count": len(envelopes)},
        "known_unknowns": known_unknowns,
        "raw_content_visible": False,
    }
    _reject_unsafe(explanation)
    return explanation


def explain_security_incident(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility-friendly name for the strict explanation builder."""

    return build_security_incident_explanation(*args, **kwargs)


def _validated_envelope(value: SecurityEvidenceEnvelope | Mapping[str, Any]) -> dict[str, Any]:
    raw = value.to_dict() if isinstance(value, SecurityEvidenceEnvelope) else value
    _reject_unsafe(raw)
    if not isinstance(raw, Mapping) or set(raw) != {"schema", "source", "event_type", "status", "severity", "dimensions", "references", "measurements", "evidence_ref", "correlation_ref", "dedupe_ref", "raw_content_visible"} or raw.get("schema") != SECURITY_EVIDENCE_SCHEMA or raw.get("raw_content_visible") is not False:
        raise SecurityIncidentExplanationError("evidence envelope shape is invalid")
    projection = {key: raw[key] for key in ("source", "event_type", "status", "severity", "dimensions", "references", "measurements")}
    try:
        rebuilt = build_security_evidence_envelope(projection).to_dict()
    except Exception as exc:
        raise SecurityIncidentExplanationError("evidence envelope validation failed") from exc
    if any(raw[key] != rebuilt[key] for key in ("evidence_ref", "correlation_ref", "dedupe_ref")):
        raise SecurityIncidentExplanationError("evidence envelope references do not match its projection")
    if not is_opaque_digest_ref(raw["evidence_ref"]):
        raise SecurityIncidentExplanationError("evidence reference is not opaque")
    return rebuilt


def _evidence_gaps(incident: Mapping[str, Any], envelopes: tuple[dict[str, Any], ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    missing: set[str] = set()
    conflicts: set[str] = set()
    if not envelopes:
        missing.add("brokered_evidence_not_available")
    if incident.get("evidence_refs") and not envelopes:
        missing.add("incident_evidence_not_broker_bound")
    auth = [item for item in envelopes if item["source"] == "auth_outcome"]
    if not auth:
        missing.add("auth_evidence_not_applicable")
    else:
        facts = {
            "outcome": {item["dimensions"]["outcome"] for item in auth},
            "principal_ref": {item["references"]["principal_ref"] for item in auth},
            "source_familiarity": {item["dimensions"]["source_familiarity"] for item in auth},
            "session_created": {item["dimensions"]["session_created"] for item in auth},
            "affected_session_refs": {tuple(sorted(ref for key, ref in item["references"].items() if key.startswith("affected_ref_"))) for item in auth},
        }
        conflicts.update(f"conflicting_auth_{name}" for name, values in facts.items() if len(values) > 1)
    for correlation in {item["correlation_ref"] for item in envelopes}:
        group = [item for item in envelopes if item["correlation_ref"] == correlation]
        if len(group) > 1:
            if len({item["dedupe_ref"] for item in group}) > 1:
                conflicts.add("conflicting_correlation_dedupe_truth")
            signatures = {repr((item["source"], item["event_type"], item["status"], item["severity"], item["dimensions"], item["references"], item["measurements"])) for item in group}
            if len(signatures) > 1:
                conflicts.add("conflicting_correlation_facts")
    return tuple(sorted(missing)), tuple(sorted(conflicts))


def _auth_projection(envelopes: tuple[dict[str, Any], ...], conflicts: tuple[str, ...]) -> dict[str, Any]:
    auth = [item for item in envelopes if item["source"] == "auth_outcome"]
    if not auth:
        return {"outcome": "not_applicable", "principal_ref": "not_applicable", "source_familiarity": "not_applicable", "session_created": "not_applicable", "affected_session_refs": ()}
    first = auth[0]
    refs = first["references"]
    sessions = tuple(sorted(ref for key, ref in refs.items() if key.startswith("affected_ref_")))
    has = lambda name: f"conflicting_auth_{name}" in conflicts
    return {
        "outcome": "unknown" if has("outcome") else first["dimensions"]["outcome"],
        "principal_ref": "not_available" if has("principal_ref") else _opaque_ref(refs["principal_ref"], {"principal"}),
        "source_familiarity": "unknown" if has("source_familiarity") else first["dimensions"]["source_familiarity"],
        "session_created": "unknown" if has("session_created") else first["dimensions"]["session_created"],
        "affected_session_refs": () if has("affected_session_refs") or has("principal_ref") else tuple(_opaque_ref(value, {"session"}) for value in sessions),
    }


def _action_rationale(incident: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    policy_by_id = {str(item.get("action_id") or ""): item for item in policy.get("action_results", ()) if isinstance(item, Mapping)}
    rows = []
    for action in incident.get("recommended_actions", ()):
        if not isinstance(action, Mapping):
            raise SecurityIncidentExplanationError("invalid recommended action")
        action_id = _token(action.get("action_id"), "action_id")
        action_type = _token(action.get("type"), "action_type")
        authority, gate = action_policy_disposition(action_type)
        policy_result = policy_by_id.get(action_id, {})
        policy_decision = str(policy_result.get("decision") or "denied")
        if authority == "read_only_allowed":
            disposition, reason = "allowed", "read_only_prepare_only"
        elif authority == "never_allowed_in_SIRP":
            disposition, reason = "denied", "never_allowed_in_sirp"
        elif policy_decision == "blocked":
            disposition, reason = "blocked", str(policy_result.get("reason") or "policy_blocked")
        elif policy_decision == "denied":
            disposition, reason = "denied", "policy_denied"
        else:
            disposition, reason = "gated", f"{authority}_named_gate_required"
        rows.append({"action_id": action_id, "disposition": disposition, "reason": reason, "gate": gate})
    return tuple(sorted(rows, key=lambda item: item["action_id"]))


def _observation_window(incident: Mapping[str, Any], evidence_count: int) -> dict[str, Any]:
    start = _timestamp(incident.get("created_at"))
    end = _timestamp(incident.get("updated_at"))
    if start != "not_available" and end != "not_available":
        try:
            backwards = _parse_observation_time(start) > _parse_observation_time(end)
        except TypeError as exc:
            raise SecurityIncidentExplanationError("observation window timezone is inconsistent") from exc
        if backwards:
            raise SecurityIncidentExplanationError("observation window is inconsistent")
    return {"start": start, "end": end, "evidence_count": evidence_count}


def _bound_envelopes(incident: Mapping[str, Any], envelopes: tuple[dict[str, Any], ...]) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], int, int]:
    declared = tuple(str(value) for value in incident.get("evidence_refs", ()))
    incident_refs = {value for value in declared if is_opaque_digest_ref(value)}
    nonopaque_declared_count = sum(1 for value in declared if not is_opaque_digest_ref(value))
    bound = tuple(item for item in envelopes if item["evidence_ref"] in incident_refs)
    gaps = []
    if envelopes and len(bound) != len(envelopes):
        gaps.append("unbound_broker_evidence_excluded")
    if envelopes and not bound:
        gaps.append("no_broker_evidence_bound_to_incident")
    declared_missing_count = len(incident_refs - {item["evidence_ref"] for item in bound})
    if declared_missing_count:
        gaps.append("declared_broker_evidence_not_supplied")
    if nonopaque_declared_count:
        gaps.append("declared_evidence_not_broker_bound")
    return bound, tuple(gaps), declared_missing_count, nonopaque_declared_count


def _ensure_unique_envelopes(envelopes: tuple[dict[str, Any], ...]) -> None:
    if len({item["evidence_ref"] for item in envelopes}) != len(envelopes) or len({item["dedupe_ref"] for item in envelopes}) != len(envelopes):
        raise SecurityIncidentExplanationError("duplicate evidence or dedupe reference")


def _validate_incident_collections(incident: Mapping[str, Any]) -> None:
    surfaces = incident.get("affected_surfaces", ())
    actions = incident.get("recommended_actions", ())
    if not isinstance(surfaces, (tuple, list)) or not isinstance(actions, (tuple, list)) or len(surfaces) > 8 or len(actions) > 8:
        raise SecurityIncidentExplanationError("incident collection exceeds explanation bound")
    action_ids = [str(action.get("action_id") or "") for action in actions if isinstance(action, Mapping)]
    if len(action_ids) != len(actions) or len(set(action_ids)) != len(action_ids):
        raise SecurityIncidentExplanationError("recommended action identifiers must be unique")


def _containment_state(incident: Mapping[str, Any]) -> dict[str, str]:
    action_claims_execution = any(isinstance(action, Mapping) and action.get("status") == "executed" for action in incident.get("recommended_actions", ()))
    incident_claims_containment = incident.get("status") in {"contained", "recovery"}
    if action_claims_execution or incident_claims_containment:
        return {"state": "unknown", "receipt_ref": "receipt_not_available", "verification_state": "unknown"}
    return {"state": "not_executed", "receipt_ref": "not_available", "verification_state": "unknown"}


def _incident_version(incident: Mapping[str, Any]) -> int | str:
    value = incident.get("incident_version", incident.get("version"))
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000:
        return "not_available"
    return value


def _timestamp(value: Any) -> str:
    text = str(value or "")
    if text == "1970-01-01T00:00:00Z":
        return "not_available"
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", text) else "not_available"


def _parse_observation_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SecurityIncidentExplanationError("observation window timestamp is invalid") from exc


def _opaque_ref(value: Any, prefixes: set[str]) -> str:
    match = _REF.fullmatch(str(value or ""))
    if not match or match.group(1) not in prefixes:
        raise SecurityIncidentExplanationError("reference is not an approved opaque reference")
    return str(value)


def _token(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 180 or not re.fullmatch(r"[A-Za-z0-9_.:@/-]+", text):
        raise SecurityIncidentExplanationError(f"invalid {field}")
    _reject_text(text)
    return text


def _summary(value: Any, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > 180:
        raise SecurityIncidentExplanationError(f"invalid {field}")
    _reject_text(text)
    return text


def _reject_unsafe(value: Any, *, depth: int = 0) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            key_parts = set(re.split(r"[^a-z0-9]+", lowered))
            is_required_flag = lowered == "raw_content_visible"
            forbidden_parts = {"token", "cookie", "authorization", "header"}
            private_content = ({"private", "document"} <= key_parts) or ({"private", "email"} <= key_parts)
            chat_identity = {"chat", "id"} <= key_parts
            action_flag = value.get("schema") == "odysseus.security_incident_action.v1" and is_required_flag and nested is False
            root_flag = depth == 0 and is_required_flag and nested is False
            if lowered in _FORBIDDEN_KEYS or bool(key_parts & forbidden_parts) or private_content or chat_identity or ("raw" in key_parts and not is_required_flag) or (is_required_flag and not (root_flag or action_flag)):
                raise SecurityIncidentExplanationError("forbidden explanation field")
            _reject_unsafe(nested, depth=depth + 1)
    elif isinstance(value, (tuple, list, set)):
        for nested in value:
            _reject_unsafe(nested, depth=depth + 1)
    elif isinstance(value, str):
        _reject_text(value)


def _reject_text(value: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS) or _PATH.search(value) or _IP.search(value):
        raise SecurityIncidentExplanationError("forbidden explanation content")


__all__ = ["SECURITY_INCIDENT_EXPLANATION_SCHEMA", "SecurityIncidentExplanationError", "build_security_incident_explanation", "explain_security_incident"]
