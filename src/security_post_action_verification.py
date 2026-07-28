"""Sealed, offline post-action verification for durable security actions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import threading
from typing import Any

from src.security_executor_contracts import SecurityExecutionRequest, request_fingerprint


POST_ACTION_VERIFICATION_SCHEMA = "odysseus.security_post_action_verification.v1"
_OPAQUE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_OUTCOMES = frozenset({"success", "failed", "unknown"})


class PostActionVerificationError(ValueError):
    """Content-free rejection at this redacted boundary."""


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """The bounded, opaque input visible to an issued fake source."""

    action_ref: str
    action_version: int
    action_type: str
    scope_fingerprint: str
    receipt_ref: str
    schema: str = POST_ACTION_VERIFICATION_SCHEMA

    def validate(self) -> None:
        if (
            self.schema != POST_ACTION_VERIFICATION_SCHEMA or not isinstance(self.action_ref, str)
            or not _OPAQUE_REF_RE.fullmatch(self.action_ref) or type(self.action_version) is not int
            or self.action_version < 1 or not isinstance(self.action_type, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", self.action_type)
            or not isinstance(self.scope_fingerprint, str)
            or not re.fullmatch(r"scope:sha256:[0-9a-f]{64}", self.scope_fingerprint)
            or not isinstance(self.receipt_ref, str) or not _OPAQUE_REF_RE.fullmatch(self.receipt_ref)
        ):
            raise PostActionVerificationError("verification unavailable")


class VerificationObservation:
    """An issuer-sealed observation bound to one issued source and request."""

    __slots__ = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise PostActionVerificationError("verification unavailable")

    def __copy__(self) -> "VerificationObservation":
        raise PostActionVerificationError("verification unavailable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "VerificationObservation":
        raise PostActionVerificationError("verification unavailable")

    def __reduce_ex__(self, protocol: int) -> object:
        raise PostActionVerificationError("verification unavailable")


class IssuedVerificationSource:
    """An unforgeable in-process authority for one injected offline fake."""

    __slots__ = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise PostActionVerificationError("verification unavailable")

    def __copy__(self) -> "IssuedVerificationSource":
        raise PostActionVerificationError("verification unavailable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "IssuedVerificationSource":
        raise PostActionVerificationError("verification unavailable")

    def __reduce_ex__(self, protocol: int) -> object:
        raise PostActionVerificationError("verification unavailable")


class _VerificationTestIssuer:
    """Identity marker required only to construct isolated offline fixtures."""


VERIFICATION_TEST_ONLY_ISSUER = _VerificationTestIssuer()


@dataclass(slots=True)
class _SourceRecord:
    source_ref: str
    outcome: str | None


@dataclass(slots=True)
class _ObservationRecord:
    source: IssuedVerificationSource
    request: VerificationRequest
    outcome: str
    consumed: bool = False


def _build_test_source_api() -> tuple[Any, Any, Any]:
    records: dict[IssuedVerificationSource, _SourceRecord] = {}
    observations: dict[VerificationObservation, _ObservationRecord] = {}
    lock = threading.RLock()

    def issue(*, source_ref: Any, outcome: Any = "success", test_issuer: object) -> IssuedVerificationSource:
        if (
            test_issuer is not VERIFICATION_TEST_ONLY_ISSUER or not isinstance(source_ref, str)
            or not _OPAQUE_REF_RE.fullmatch(source_ref) or (outcome is not None and (type(outcome) is not str or outcome not in _OUTCOMES))
        ):
            raise PostActionVerificationError("verification unavailable")
        source = object.__new__(IssuedVerificationSource)
        with lock:
            records[source] = _SourceRecord(source_ref, outcome)
        return source

    def observe(source: Any, request: Any) -> tuple[str, str]:
        if type(source) is not IssuedVerificationSource or type(request) is not VerificationRequest:
            raise PostActionVerificationError("verification unavailable")
        request.validate()
        with lock:
            source_record = records.get(source)
            if source_record is None:
                raise PostActionVerificationError("verification unavailable")
            # This is a finite fixture lookup.  No caller-provided callback,
            # transport, predicate, or arbitrary code can run during verify.
            if source_record.outcome is None:
                raise PostActionVerificationError("verification unavailable")
            observation = object.__new__(VerificationObservation)
            observations[observation] = _ObservationRecord(source, request, source_record.outcome)
            record = observations[observation]
            record.consumed = True
            return record.outcome, source_record.source_ref

    def source_ref(source: Any) -> str:
        if type(source) is not IssuedVerificationSource:
            raise PostActionVerificationError("verification unavailable")
        with lock:
            record = records.get(source)
            if record is None:
                raise PostActionVerificationError("verification unavailable")
            return record.source_ref

    return issue, observe, source_ref


issue_test_verification_source, _observe_test_source, _issued_source_ref = _build_test_source_api()


class PostActionVerifier:
    """Persist only sealed independent observations; acknowledgement is never proof."""

    def __init__(self, store: Any, source: IssuedVerificationSource) -> None:
        if type(source) is not IssuedVerificationSource:
            raise PostActionVerificationError("verification unavailable")
        self._store = store
        self._source = source
        self._lock = threading.RLock()

    def verify(self, request_value: Any) -> dict[str, Any]:
        try:
            request = request_value if isinstance(request_value, SecurityExecutionRequest) else SecurityExecutionRequest.from_mapping(request_value)
            request.validate()
        except (TypeError, ValueError):
            return self._blocked("invalid_execution_request")
        with self._lock:
            try:
                action = self._store.get_action(request.action_id)
            except Exception:
                return self._blocked("durable_action_unavailable")
            if not self._matches_action(action, request) or not self._has_execution_audit(request):
                return self._blocked("durable_receipt_mismatch")
            replay = self._replay(action, request)
            if replay is not None:
                return replay
            if getattr(action, "state", "") != "executed":
                return self._blocked("verification_not_available")
            observation_request = VerificationRequest(
                action_ref=self._action_ref(request.action_id), action_version=request.action_version,
                action_type=request.action_type, scope_fingerprint=request.scope_fingerprint,
                receipt_ref=action.receipt_ref,
            )
            try:
                observation_request.validate()
                outcome, source_ref = _observe_test_source(self._source, observation_request)
            except Exception:
                outcome = "unknown"
                try:
                    source_ref = _issued_source_ref(self._source)
                except Exception:
                    return self._blocked("verification_source_unavailable")
            return self._persist(action, request, outcome, source_ref)

    def _persist(self, action: Any, request: SecurityExecutionRequest, outcome: str, source_ref: str) -> dict[str, Any]:
        evidence_ref = self._evidence_ref(request, outcome, source_ref)
        audit_ref = self._audit_ref(request, outcome, source_ref, evidence_ref)
        try:
            if outcome == "success":
                self._store.transition(action_id=request.action_id, expected_version=action.version, target_state="verified", audit_ref=audit_ref, verification_ref=evidence_ref)
            else:
                self._store.transition(action_id=request.action_id, expected_version=action.version, target_state="failed", audit_ref=audit_ref, failure_ref=evidence_ref)
        except Exception:
            return self._blocked("concurrent_verification_conflict")
        return self._result(outcome, evidence_ref, idempotent_replay=False)

    def _replay(self, action: Any, request: SecurityExecutionRequest) -> dict[str, Any] | None:
        if getattr(action, "state", "") == "verified":
            outcome, evidence_ref, event_type = "success", action.verification_ref, "action_verified"
        elif getattr(action, "state", "") == "failed":
            try:
                source_ref = _issued_source_ref(self._source)
            except Exception:
                return self._blocked("verification_source_unavailable")
            candidates = (("failed", self._evidence_ref(request, "failed", source_ref)), ("unknown", self._evidence_ref(request, "unknown", source_ref)))
            outcome = next((candidate for candidate, expected in candidates if action.failure_ref == expected), "")
            if not outcome:
                if isinstance(getattr(action, "failure_ref", None), str) and action.failure_ref.startswith("verification-"):
                    return self._blocked("durable_verification_mismatch")
                return None
            evidence_ref, event_type = action.failure_ref, "action_failed"
        else:
            return None
        try:
            source_ref = _issued_source_ref(self._source)
        except Exception:
            return self._blocked("verification_source_unavailable")
        expected_evidence = self._evidence_ref(request, outcome, source_ref)
        expected_audit = self._audit_ref(request, outcome, source_ref, expected_evidence)
        if evidence_ref != expected_evidence:
            return self._blocked("durable_verification_mismatch")
        try:
            events = tuple(self._store.audit_events(request.action_id))
        except Exception:
            return self._blocked("durable_audit_unavailable")
        if not any(getattr(event, "event_type", "") == event_type and getattr(event, "reference", "") == expected_audit for event in events):
            return self._blocked("durable_verification_mismatch")
        return self._result(outcome, evidence_ref, idempotent_replay=True)

    def _has_execution_audit(self, request: SecurityExecutionRequest) -> bool:
        try:
            events = tuple(self._store.audit_events(request.action_id))
        except Exception:
            return False
        expected = "audit:sha256:" + hashlib.sha256(("acknowledged|" + request_fingerprint(request)).encode("utf-8")).hexdigest()
        return any(getattr(event, "event_type", "") == "action_executed" and getattr(event, "reference", "") == expected for event in events)

    @staticmethod
    def _matches_action(action: Any, request: SecurityExecutionRequest) -> bool:
        expected_receipt = "receipt:sha256:" + hashlib.sha256(("receipt|" + request_fingerprint(request)).encode("utf-8")).hexdigest()
        return bool(getattr(action, "action_type", None) == request.action_type and getattr(action, "scope_fingerprint", None) == request.scope_fingerprint and getattr(action, "policy_revision", None) == request.policy_revision and getattr(action, "idempotency_key", None) == request.idempotency_key and getattr(action, "receipt_ref", None) == expected_receipt)

    @staticmethod
    def _action_ref(action_id: str) -> str:
        return "action:sha256:" + hashlib.sha256(action_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _evidence_ref(request: SecurityExecutionRequest, outcome: str, source_ref: str) -> str:
        prefix = "verification" if outcome == "success" else "verification-" + outcome
        return prefix + ":sha256:" + hashlib.sha256(("verification|" + request_fingerprint(request) + "|" + outcome + "|" + source_ref).encode("utf-8")).hexdigest()

    @staticmethod
    def _audit_ref(request: SecurityExecutionRequest, outcome: str, source_ref: str, evidence_ref: str) -> str:
        return "audit:sha256:" + hashlib.sha256(("verification-audit|" + request_fingerprint(request) + "|" + outcome + "|" + source_ref + "|" + evidence_ref).encode("utf-8")).hexdigest()

    @staticmethod
    def _result(outcome: str, evidence_ref: str, *, idempotent_replay: bool) -> dict[str, Any]:
        return {"schema": POST_ACTION_VERIFICATION_SCHEMA, "status": "success" if outcome == "success" else "blocked", "outcome": outcome, "verified": outcome == "success", "closure_blocked": outcome != "success", "evidence_ref": evidence_ref, "idempotent_replay": idempotent_replay, "read_only": True, "raw_content_visible": False}

    @staticmethod
    def _blocked(reason: str) -> dict[str, Any]:
        return {"schema": POST_ACTION_VERIFICATION_SCHEMA, "status": "blocked", "reason": reason, "verified": False, "closure_blocked": True, "raw_content_visible": False}


__all__ = ["IssuedVerificationSource", "POST_ACTION_VERIFICATION_SCHEMA", "PostActionVerificationError", "PostActionVerifier", "VERIFICATION_TEST_ONLY_ISSUER", "VerificationObservation", "VerificationRequest", "issue_test_verification_source"]
