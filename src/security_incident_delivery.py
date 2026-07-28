"""Bound, injected-only security incident delivery adapter.

The adapter deliberately contains no target selection, message body, provider,
credential, environment, or default transport composition.  It uses the
existing incident store's action/approval lifecycle as its sole durable
authority and records only opaque receipt and correlation references.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any


MAX_DRY_RUN_RETRIES = 1
MAX_DELIVERY_TIMEOUT_SECONDS = 60
DEFAULT_DELIVERY_TIMEOUT_SECONDS = 15
DELIVERY_ACTION_TYPE = "operator_notification"
DELIVERY_POLICY_GATE = "OPS-ALERT-DELIVERY-GO"
DELIVERY_REQUEST_SCHEMA = "odysseus.security_incident_delivery_request.v1"
_ACTION_ID = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")
_OPAQUE_REF = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_SCOPE_REF = re.compile(r"^scope:sha256:[0-9a-f]{64}$")
_FORBIDDEN = ("secret", "token", "cookie", "authorization", "bearer", "password", "command", "private", "provider", "environment", "raw_target", "raw_evidence", "raw_log")
_PATH = re.compile(r"[A-Za-z]:[\\/]|/(?:home|users|var|mnt|srv|opt)/|~[\\/]", re.IGNORECASE)
_READINESS_FIELDS = frozenset({"opaque_target_configured", "agent_reply_enabled", "send_ready", "raw_target_visible", "secret_values_visible"})
_PROBE_CREDENTIAL_KEYS = frozenset({"DATA_BRAVE_API_KEY", "EMBEDDING_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "NEXTCLOUD_WEBDAV_APP_PASSWORD", "ODYSSEUS_ADMIN_PASSWORD", "ODYSSEUS_INTERNAL_TOKEN", "OPENAI_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "TELEGRAM_BOT_TOKEN"})
_PROBE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAX_PROBE_ENTRIES = 4096
_READINESS_ISSUER = object()
_TRANSPORT_ISSUER = object()


@dataclass(frozen=True, slots=True)
class SecurityIncidentDeliveryReceipt:
    action_id: str
    correlation_ref: str
    receipt_ref: str
    outcome: str = "blocked_no_send"
    delivery_performed: bool = False
    retry_scheduled: bool = False
    raw_content_visible: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "correlation_ref": self.correlation_ref,
            "receipt_ref": self.receipt_ref, "outcome": self.outcome,
            "delivery_performed": False, "retry_scheduled": False,
            "raw_content_visible": False,
        }


def record_dry_run_delivery(*, action_id: Any, retry_count: Any = 0) -> SecurityIncidentDeliveryReceipt:
    """Return the legacy immutable no-send receipt without target or content."""
    if not _identifier(action_id) or isinstance(retry_count, bool) or not isinstance(retry_count, int) or not 0 <= retry_count <= MAX_DRY_RUN_RETRIES:
        raise ValueError("security incident delivery unavailable")
    digest = hashlib.sha256(f"security-incident-no-send:{action_id}".encode("utf-8")).hexdigest()
    return SecurityIncidentDeliveryReceipt(
        action_id=action_id,
        correlation_ref=f"correlation:sha256:{digest}",
        receipt_ref=f"receipt:sha256:{digest}",
    )


class TrustedTelegramDeliveryReadiness:
    """Sealed readiness derived only from the fixed redacted probe projection."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, bool], *, _issuer: object) -> None:
        if _issuer is not _READINESS_ISSUER:
            raise ValueError("trusted delivery readiness unavailable")
        self._values = _readiness(values)

    @classmethod
    def from_redacted_probe(cls, probe: Any) -> "TrustedTelegramDeliveryReadiness":
        if not isinstance(probe, Mapping): raise ValueError("trusted delivery readiness unavailable")
        expected = {"schema_id", "status", "container", "container_running", "environment_entry_count", "credential_presence", "unknown_sensitive_key_count", "raw_environment_visible", "secret_values_visible", "telegram_delivery_readiness"}
        if set(probe) != expected or probe.get("schema_id") != "odysseus.homeserver.redacted_runtime_probe.v1" or probe.get("status") != "ok" or probe.get("raw_environment_visible") is not False or probe.get("secret_values_visible") is not False or probe.get("container_running") is not True or not _safe_probe_container(probe.get("container")):
            raise ValueError("trusted delivery readiness unavailable")
        presence = probe.get("credential_presence")
        if not isinstance(presence, Mapping) or set(presence) != _PROBE_CREDENTIAL_KEYS or any(type(presence[key]) is not bool for key in _PROBE_CREDENTIAL_KEYS): raise ValueError("trusted delivery readiness unavailable")
        entries = _probe_count(probe.get("environment_entry_count")); unknown = _probe_count(probe.get("unknown_sensitive_key_count")); present = sum(presence.values())
        if present > entries or unknown > entries or present + unknown > entries: raise ValueError("trusted delivery readiness unavailable")
        return cls(probe["telegram_delivery_readiness"], _issuer=_READINESS_ISSUER)

    def values(self) -> dict[str, bool]:
        return dict(self._values)


class InjectedSecurityIncidentDeliveryTransport:
    """Sealed test-only transport injection; no production transport is composed here."""

    __slots__ = ("_effect",)

    def __init__(self, effect: Callable[[Mapping[str, Any]], Any], *, _issuer: object) -> None:
        if _issuer is not _TRANSPORT_ISSUER:
            raise ValueError("delivery transport unavailable")
        self._effect = effect

    def invoke(self, request: Mapping[str, Any]) -> Any:
        return self._effect(request)


def issue_test_delivery_transport(effect: Any) -> InjectedSecurityIncidentDeliveryTransport:
    if not callable(effect) or getattr(effect, "security_incident_delivery_test_fake", False) is not True:
        raise ValueError("delivery transport unavailable")
    return InjectedSecurityIncidentDeliveryTransport(effect, _issuer=_TRANSPORT_ISSUER)


@dataclass(frozen=True, slots=True)
class SecurityIncidentDeliveryRequest:
    incident_id: str
    action_id: str
    action_version: int
    scope_fingerprint: str
    policy_revision: str
    body_ref: str
    approved_target_class_ref: str
    channel: str
    grant_expires_at: float
    timeout_seconds: int
    telegram_delivery_readiness: Mapping[str, bool]
    policy_gate: str = DELIVERY_POLICY_GATE
    schema: str = DELIVERY_REQUEST_SCHEMA

    @classmethod
    def from_mapping(cls, value: Any) -> "SecurityIncidentDeliveryRequest":
        expected = {
            "schema", "incident_id", "action_id", "action_version", "scope_fingerprint",
            "policy_revision", "body_ref", "channel", "grant_expires_at", "timeout_seconds",
            "telegram_delivery_readiness", "approved_target_class_ref", "policy_gate",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("invalid delivery request")
        request = cls(**dict(value))
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema != DELIVERY_REQUEST_SCHEMA or self.policy_gate != DELIVERY_POLICY_GATE:
            raise ValueError("invalid delivery request")
        if not _identifier(self.incident_id) or not _identifier(self.action_id):
            raise ValueError("invalid delivery request")
        if type(self.action_version) is not int or self.action_version < 1:
            raise ValueError("invalid delivery request")
        if not isinstance(self.scope_fingerprint, str) or not _SCOPE_REF.fullmatch(self.scope_fingerprint):
            raise ValueError("invalid delivery request")
        if not _opaque(self.policy_revision) or not isinstance(self.body_ref, str) or not self.body_ref.startswith("body:sha256:") or not _opaque(self.body_ref) or not isinstance(self.approved_target_class_ref, str) or not self.approved_target_class_ref.startswith("target_class:sha256:") or not _opaque(self.approved_target_class_ref):
            raise ValueError("invalid delivery request")
        if self.channel != "telegram":
            raise ValueError("invalid delivery request")
        if isinstance(self.grant_expires_at, bool) or not isinstance(self.grant_expires_at, (int, float)) or not math.isfinite(float(self.grant_expires_at)) or float(self.grant_expires_at) < 0:
            raise ValueError("invalid delivery request")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or not 1 <= self.timeout_seconds <= MAX_DELIVERY_TIMEOUT_SECONDS:
            raise ValueError("invalid delivery request")
        if not isinstance(self.telegram_delivery_readiness, TrustedTelegramDeliveryReadiness):
            raise ValueError("invalid delivery readiness")
        _readiness(self.telegram_delivery_readiness.values())


class SecurityIncidentDeliveryAdapter:
    """Consume exactly one durable delivery approval through an injected transport."""

    def __init__(self, store: Any, *, transport: Any = None, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._transport = transport if _approved_transport(transport) else None
        self._clock = clock
        self._lock = threading.RLock()

    def attempt(self, request_value: Any) -> dict[str, Any]:
        try:
            request = _request(request_value)
        except Exception:
            return self._blocked("invalid_delivery_request")
        if self._transport is None:
            return self._blocked("delivery_transport_unavailable")
        with self._lock:
            try:
                started_at = self._now()
                preflight = self._preflight(request, now=started_at)
            except Exception:
                return self._blocked("delivery_clock_unavailable")
            if not preflight:
                return self._blocked("durable_delivery_preflight_rejected")
            correlation_ref = _correlation_ref(request)
            try:
                # This immediate recheck narrows drift to the interval between
                # this read and the store's atomic compare-and-set transition.
                # The opaque approved target class is bound durably, while
                # actual host-side target resolution remains outside this adapter.
                if not self._preflight(request, now=self._now()):
                    return self._blocked("durable_delivery_preflight_rejected")
                executing = self._store.transition(
                    action_id=request.action_id, expected_version=request.action_version,
                    target_state="executing", audit_ref=correlation_ref,
                )
            except Exception:
                return self._blocked("durable_delivery_preflight_rejected")
            try:
                response = _transport_result(self._transport.invoke(_transport_request(request)))
                completed_at = self._now()
                if completed_at < started_at or completed_at - started_at > request.timeout_seconds or completed_at > request.grant_expires_at:
                    raise TimeoutError
            except Exception:
                self._record_unknown(executing, correlation_ref)
                return self._unknown("delivery_attempt_outcome_unknown", correlation_ref=correlation_ref)
            try:
                self._store.transition(
                    action_id=request.action_id, expected_version=executing.version,
                    target_state="executed", audit_ref=_acknowledgement_ref(correlation_ref, response["receipt_ref"]),
                    receipt_ref=response["receipt_ref"],
                )
            except Exception:
                self._record_unknown(executing, correlation_ref)
                return self._unknown("delivery_attempt_outcome_unknown", correlation_ref=correlation_ref)
            return self._success(request, correlation_ref, response["receipt_ref"])

    def readback(self, request_value: Any) -> dict[str, Any]:
        """Read independently from the store; it never invokes a transport."""
        try:
            request = _request(request_value)
            action = self._store.get_action(request.action_id)
            approval = self._store.get_approval(request.action_id)
            events = tuple(self._store.audit_events(request.action_id))
        except Exception:
            return self._blocked("durable_delivery_readback_unavailable")
        if not self._durably_bound(request, action, approval) or approval.consumed_at is None:
            return self._blocked("durable_delivery_binding_mismatch")
        correlation_ref = _correlation_ref(request)
        if not any(event.event_type == "action_executing" and event.action_version == request.action_version + 1 and event.reference == correlation_ref for event in events):
            return self._blocked("durable_delivery_attempt_unavailable")
        if action.state == "executed":
            if action.version != request.action_version + 2 or not _receipt_ref(action.receipt_ref) or not any(event.event_type == "action_executed" and event.action_version == request.action_version + 2 and event.reference == _acknowledgement_ref(correlation_ref, action.receipt_ref) for event in events):
                return self._blocked("durable_delivery_receipt_unavailable")
            return self._success(request, correlation_ref, action.receipt_ref)
        if action.state == "failed":
            failure_ref = _failure_ref(correlation_ref)
            if action.version != request.action_version + 2 or action.failure_ref != failure_ref or not any(event.event_type == "action_failed" and event.action_version == request.action_version + 2 and event.reference == correlation_ref for event in events):
                return self._blocked("durable_delivery_attempt_unavailable")
            return self._unknown("delivery_attempt_outcome_unknown", correlation_ref=correlation_ref)
        if action.state == "executing":
            return self._unknown("delivery_attempt_outcome_unknown", correlation_ref=correlation_ref)
        return self._blocked("durable_delivery_nonterminal_state")

    def _preflight(self, request: SecurityIncidentDeliveryRequest, *, now: float) -> bool:
        try:
            action = self._store.get_action(request.action_id)
            approval = self._store.get_approval(request.action_id)
        except Exception:
            return False
        return self._durably_bound(request, action, approval) and action.state == "approved" and action.version == request.action_version and approval.consumed_at is None and request.telegram_delivery_readiness.values()["send_ready"] and now + request.timeout_seconds <= request.grant_expires_at

    @staticmethod
    def _durably_bound(request: SecurityIncidentDeliveryRequest, action: Any, approval: Any) -> bool:
        return bool(
            action.incident_id == request.incident_id and action.action_type == DELIVERY_ACTION_TYPE
            and action.scope_fingerprint == request.scope_fingerprint
            and action.policy_revision == request.policy_revision and action.idempotency_key == delivery_idempotency_key(request)
            and float(action.expires_at) == float(request.grant_expires_at)
            and approval is not None and approval.action_id == request.action_id
            and approval.action_version == request.action_version and approval.scope_fingerprint == request.scope_fingerprint
            and approval.policy_revision == request.policy_revision
        )

    def _record_unknown(self, executing: Any, correlation_ref: str) -> None:
        try:
            self._store.transition(
                action_id=executing.action_id, expected_version=executing.version,
                target_state="failed", audit_ref=correlation_ref,
                failure_ref=_failure_ref(correlation_ref),
            )
        except Exception:
            pass

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError("invalid delivery clock")
        return float(value)

    @staticmethod
    def _blocked(reason: str, *, correlation_ref: str | None = None) -> dict[str, Any]:
        result = {"status": "blocked", "reason": reason, "delivery_performed": False, "retry_scheduled": False, "raw_content_visible": False}
        if correlation_ref is not None:
            result["correlation_ref"] = correlation_ref
        return result

    @staticmethod
    def _unknown(reason: str, *, correlation_ref: str) -> dict[str, Any]:
        return {"status": "unknown", "reason": reason, "correlation_ref": correlation_ref, "delivery_performed": None, "retry_scheduled": False, "raw_content_visible": False}

    @staticmethod
    def _success(request: SecurityIncidentDeliveryRequest, correlation_ref: str, receipt_ref: str) -> dict[str, Any]:
        return {
            "status": "acknowledged", "action_id": request.action_id,
            "action_version": request.action_version, "receipt_ref": receipt_ref,
            "correlation_ref": correlation_ref, "delivery_performed": True,
            "retry_scheduled": False, "raw_content_visible": False,
        }


def delivery_idempotency_key(request_value: Any) -> str:
    request = _request(request_value)
    values = {
        "incident_id": request.incident_id, "action_id": request.action_id,
        "scope_fingerprint": request.scope_fingerprint, "policy_revision": request.policy_revision,
        "body_ref": request.body_ref, "channel": request.channel,
        "approved_target_class_ref": request.approved_target_class_ref,
        "grant_expires_at": float(request.grant_expires_at),
        "telegram_delivery_readiness": request.telegram_delivery_readiness.values(),
        "policy_gate": request.policy_gate,
    }
    return "delivery-" + hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_server_owned_delivery_request(
    action: Any,
    readiness: Any,
    *,
    timeout_seconds: int = DEFAULT_DELIVERY_TIMEOUT_SECONDS,
) -> SecurityIncidentDeliveryRequest:
    """Bind the one canonical operator notification to a durable approval.

    This deliberately accepts an action record and a sealed readiness object,
    never HTTP-supplied message, target, channel, probe, or provider details.
    """
    try:
        if (
            getattr(action, "action_type") != DELIVERY_ACTION_TYPE
            or getattr(action, "state") != "approved"
            or type(getattr(action, "version")) is not int
        ):
            raise ValueError
        from src.security_incident_notifications import (
            canonical_operator_notification_body_ref,
            canonical_operator_notification_target_class_ref,
        )
        request = SecurityIncidentDeliveryRequest(
            incident_id=getattr(action, "incident_id"),
            action_id=getattr(action, "action_id"),
            action_version=getattr(action, "version"),
            scope_fingerprint=getattr(action, "scope_fingerprint"),
            policy_revision=getattr(action, "policy_revision"),
            body_ref=canonical_operator_notification_body_ref(),
            approved_target_class_ref=canonical_operator_notification_target_class_ref(),
            channel="telegram",
            grant_expires_at=getattr(action, "expires_at"),
            timeout_seconds=timeout_seconds,
            telegram_delivery_readiness=readiness,
        )
        request.validate()
        if getattr(action, "idempotency_key") != delivery_idempotency_key(request):
            raise ValueError
        return request
    except Exception:
        raise ValueError("server-owned security incident delivery unavailable") from None


def is_sealed_security_incident_delivery_transport(value: Any) -> bool:
    """Return whether a server-owned delivery transport can be used safely."""
    return _approved_transport(value)


def _request(value: Any) -> SecurityIncidentDeliveryRequest:
    request = value if isinstance(value, SecurityIncidentDeliveryRequest) else SecurityIncidentDeliveryRequest.from_mapping(value)
    request.validate()
    return request


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_ACTION_ID.fullmatch(value)) and not _PATH.search(value) and not any(marker in value.lower() for marker in _FORBIDDEN)


def _opaque(value: Any) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_REF.fullmatch(value)) and not _PATH.search(value) and not any(marker in value.lower() for marker in _FORBIDDEN)


def _receipt_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("receipt:sha256:") and _opaque(value)


def _readiness(value: Any) -> dict[str, bool]:
    if not isinstance(value, Mapping) or set(value) != _READINESS_FIELDS or any(type(value[key]) is not bool for key in _READINESS_FIELDS):
        raise ValueError("invalid delivery readiness")
    if value["raw_target_visible"] is not False or value["secret_values_visible"] is not False or value["send_ready"] != (value["opaque_target_configured"] and value["agent_reply_enabled"]):
        raise ValueError("invalid delivery readiness")
    return {key: value[key] for key in sorted(_READINESS_FIELDS)}


def _approved_transport(value: Any) -> bool:
    if isinstance(value, InjectedSecurityIncidentDeliveryTransport):
        return True
    try:
        from src.security_incident_telegram_transport import is_production_security_incident_telegram_transport
        return bool(is_production_security_incident_telegram_transport(value))
    except Exception:
        return False


def _probe_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_PROBE_ENTRIES:
        raise ValueError("trusted delivery readiness unavailable")
    return value


def _safe_probe_container(value: Any) -> bool:
    return isinstance(value, str) and bool(_PROBE_CONTAINER.fullmatch(value)) and not _PATH.search(value) and not _IP.search(value)


def _correlation_ref(request: SecurityIncidentDeliveryRequest) -> str:
    return "correlation:sha256:" + hashlib.sha256(("delivery-correlation|" + delivery_idempotency_key(request) + "|" + str(request.action_version)).encode("utf-8")).hexdigest()


def _acknowledgement_ref(correlation_ref: str, receipt_ref: str) -> str:
    return "audit:sha256:" + hashlib.sha256(("delivery-acknowledged|" + correlation_ref + "|" + receipt_ref).encode("utf-8")).hexdigest()


def _failure_ref(correlation_ref: str) -> str:
    return "failure:sha256:" + hashlib.sha256(("delivery-failed|" + correlation_ref).encode("utf-8")).hexdigest()


def _transport_request(request: SecurityIncidentDeliveryRequest) -> dict[str, Any]:
    return {
        "schema": DELIVERY_REQUEST_SCHEMA, "action_id": request.action_id,
        "action_version": request.action_version, "body_ref": request.body_ref,
        "channel": request.channel, "approved_target_class_ref": request.approved_target_class_ref, "timeout_seconds": request.timeout_seconds,
        "raw_content_visible": False,
    }


def _transport_result(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"status", "receipt_ref"} or value.get("status") != "acknowledged" or not _receipt_ref(value.get("receipt_ref")):
        raise ValueError("invalid delivery transport result")
    return {"receipt_ref": value["receipt_ref"]}


__all__ = [
    "DELIVERY_ACTION_TYPE", "DELIVERY_POLICY_GATE", "DELIVERY_REQUEST_SCHEMA",
    "DEFAULT_DELIVERY_TIMEOUT_SECONDS", "MAX_DELIVERY_TIMEOUT_SECONDS", "MAX_DRY_RUN_RETRIES", "SecurityIncidentDeliveryAdapter",
    "SecurityIncidentDeliveryReceipt", "SecurityIncidentDeliveryRequest", "TrustedTelegramDeliveryReadiness", "InjectedSecurityIncidentDeliveryTransport", "delivery_idempotency_key", "issue_test_delivery_transport",
    "build_server_owned_delivery_request", "is_sealed_security_incident_delivery_transport", "record_dry_run_delivery",
]
