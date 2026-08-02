"""Fail-closed bridge from one redacted auth event to durable incident state."""
from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from typing import Any, Mapping

from src.security_evidence_broker import SECURITY_EVIDENCE_SCHEMA, build_security_evidence_envelope
from src.security_incident_delivery import delivery_idempotency_identity
from src.security_incident_network_context import OwnPublicEgressSnapshot, decide_self_egress_suppression
from src.security_incident_notifications import (
    canonical_access_alert_body_ref,
    canonical_operator_notification_target_class_ref,
)
from src.security_incident_store import SecurityIncidentStore, validate_untrusted_incident_context_evidence


AUTH_BRIDGE_SCHEMA = "odysseus.security_auth_incident_bridge.v1"
ACTION_TTL_SECONDS = 900
AUTH_ALERT_DEDUPE_WINDOW_SECONDS = 300
_EVENT_KINDS = frozenset({"login", "step_up", "status", "logout"})


class SecurityAuthIncidentBridge:
    """Persist only relevant auth access events; failures never affect auth."""

    def __init__(self, store: SecurityIncidentStore, *, action_ttl_seconds: int = ACTION_TTL_SECONDS,
                 own_public_egress_provider: Callable[[], OwnPublicEgressSnapshot | None] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        if type(action_ttl_seconds) is not int or not 1 <= action_ttl_seconds <= 86400 or (own_public_egress_provider is not None and not callable(own_public_egress_provider)) or (clock is not None and not callable(clock)):
            raise ValueError("security auth incident bridge unavailable")
        self._store = store
        self._ttl = action_ttl_seconds
        self._own_public_egress_provider = own_public_egress_provider
        self._clock = clock or time.time

    def process(self, record: Mapping[str, Any]) -> dict[str, Any]:
        event_kind, envelope, context, audit = _record(record)
        event_class = _classify(event_kind, envelope)
        if event_class is None:
            return {"status": "ignored_routine_auth_event", "raw_content_visible": False}
        now = self._server_now()
        context, supplied_decision, supplied_reason = validate_untrusted_incident_context_evidence(event_class, context, audit, envelope.correlation_ref)
        canonical_audit = self._canonical_suppression_audit(
            event_class=event_class, correlation_ref=envelope.correlation_ref, context=context,
            supplied_decision=supplied_decision, supplied_reason=supplied_reason,
            now=now,
        )
        source_ref = str(canonical_audit["source_ref"])
        generation = (
            str(int(now // AUTH_ALERT_DEDUPE_WINDOW_SECONDS))
            if now is not None else "clock-unavailable"
        )
        incident_id = "inc-auth-" + _digest(
            "incident", envelope.evidence_ref, event_class, source_ref, generation,
        )[:24]
        incident_ref = _ref(
            "incident", envelope.evidence_ref, event_class, source_ref, generation,
        )
        audit_ref = _ref(
            "audit", envelope.correlation_ref, event_class, generation, "context",
        )
        binding_ref = canonical_access_alert_body_ref(
            event_class=event_class, accessing_ip=context.canonical_ip,
        )
        incident = self._store.create_incident(incident_id=incident_id, incident_ref=incident_ref, audit_ref=audit_ref)
        durable_context = self._store.bind_incident_context(
            incident_id=incident.incident_id, event_class=event_class, access_context=context,
            suppression_audit=canonical_audit, correlation_ref=envelope.correlation_ref, notification_binding_ref=binding_ref, audit_ref=audit_ref,
        )
        action_id = "notify-" + _digest("action", incident.incident_id, binding_ref)[:32]
        if durable_context.suppression_decision == "suppress_notification":
            return {"status": "suppressed", "incident_id": incident.incident_id, "action_created": False, "raw_content_visible": False}
        scope = operator_notification_scope_fingerprint(incident.incident_id)
        policy = operator_notification_policy_revision(event_class)
        idempotency = delivery_idempotency_identity(
            incident_id=incident.incident_id, action_id=action_id,
            scope_fingerprint=scope, policy_revision=policy, body_ref=binding_ref,
            approved_target_class_ref=canonical_operator_notification_target_class_ref(),
        )
        action = self._store.create_action(
            action_id=action_id, incident_id=incident.incident_id, action_type="operator_notification",
            scope_fingerprint=scope, policy_revision=policy, idempotency_key=idempotency,
            ttl_seconds=self._ttl, audit_ref=_ref("audit", incident.incident_id, "action"),
            metadata={"classification_ref": _ref("classification", event_class), "incident_ref": incident_ref, "policy_ref": policy},
        )
        return {"status": "action_proposed", "incident_id": incident.incident_id, "action_id": action.action_id, "action_created": not action.idempotent_replay, "raw_content_visible": False}

    def _canonical_suppression_audit(self, *, event_class: str, correlation_ref: str, context: Any,
                                     supplied_decision: str, supplied_reason: str,
                                     now: float | None) -> dict[str, Any]:
        """Use only a trusted, fresh injected snapshot to allow suppression."""
        snapshot = None
        if self._own_public_egress_provider is not None:
            try:
                candidate = self._own_public_egress_provider()
                snapshot = candidate if isinstance(candidate, OwnPublicEgressSnapshot) else None
            except Exception:
                snapshot = None
        if now is None:
            snapshot = None
        canonical = decide_self_egress_suppression(
            incident_id=correlation_ref, event_class=event_class, source_context=context,
            own_public_egress=snapshot, now=now,
        )
        if (supplied_decision, supplied_reason) != (canonical["decision"], canonical["reason_code"]):
            canonical = decide_self_egress_suppression(
                incident_id=correlation_ref, event_class=event_class, source_context=context,
                own_public_egress=None, now=now,
            )
        return canonical

    def _server_now(self) -> float | None:
        try:
            value = self._clock()
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            numeric = float(value)
            return numeric if math.isfinite(numeric) and numeric >= 0 else None
        except Exception:
            return None


def _record(value: Any):
    if not isinstance(value, Mapping) or set(value) != {"schema", "event_kind", "auth_event", "accessing_ip_context", "suppression_audit", "raw_content_visible"}:
        raise ValueError("security auth incident bridge unavailable")
    if value.get("schema") != AUTH_BRIDGE_SCHEMA or value.get("raw_content_visible") is not False:
        raise ValueError("security auth incident bridge unavailable")
    kind = value.get("event_kind")
    if kind not in _EVENT_KINDS:
        raise ValueError("security auth incident bridge unavailable")
    raw = value.get("auth_event")
    if not isinstance(raw, Mapping) or set(raw) != {"schema", "source", "event_type", "status", "severity", "dimensions", "references", "measurements", "evidence_ref", "correlation_ref", "dedupe_ref", "raw_content_visible"} or raw.get("schema") != SECURITY_EVIDENCE_SCHEMA:
        raise ValueError("security auth incident bridge unavailable")
    projection = {key: raw[key] for key in ("source", "event_type", "status", "severity", "dimensions", "references", "measurements")}
    envelope = build_security_evidence_envelope(projection)
    if raw.get("raw_content_visible") is not False or any(raw.get(key) != getattr(envelope, key) for key in ("evidence_ref", "correlation_ref", "dedupe_ref")):
        raise ValueError("security auth incident bridge unavailable")
    context, audit = value.get("accessing_ip_context"), value.get("suppression_audit")
    if not isinstance(context, Mapping) or not isinstance(audit, Mapping):
        raise ValueError("security auth incident bridge unavailable")
    return kind, envelope, context, audit


def _classify(event_kind: str, envelope: Any) -> str | None:
    dimensions = dict(envelope.dimensions)
    outcome = dimensions.get("outcome")
    if event_kind in {"status", "logout"}:
        return None
    if event_kind == "step_up":
        return "step_up_failure" if outcome in {"failed", "blocked", "unknown"} else None
    if event_kind == "login" and outcome in {"failed", "blocked", "unknown"}:
        return "authentication_failure"
    if event_kind == "login" and outcome == "success" and dimensions.get("session_created") == "yes":
        return "external_access_origin_only"
    return None


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _ref(kind: str, *parts: str) -> str:
    return f"{kind}:sha256:{_digest(*parts)}"


def operator_notification_scope_fingerprint(incident_id: str) -> str:
    return _ref("scope", "auth_incident", incident_id)


def operator_notification_policy_revision(event_class: str) -> str:
    if event_class not in {"authentication_failure", "step_up_failure", "external_access_origin_only"}:
        raise ValueError("security auth incident policy unavailable")
    return _ref("policy", "ops_alert_c4", event_class)


__all__ = [
    "ACTION_TTL_SECONDS", "AUTH_ALERT_DEDUPE_WINDOW_SECONDS", "AUTH_BRIDGE_SCHEMA",
    "SecurityAuthIncidentBridge",
    "operator_notification_policy_revision", "operator_notification_scope_fingerprint",
]
