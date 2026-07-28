"""Fail-closed browser-session step-up for offline incident actions.

This module deliberately keeps grants in process memory.  It stores neither a
password, TOTP value, browser token, nor a reusable proof.  A grant is bound to
the server-derived session identity and one exact durable action revision.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import threading
import time
from typing import Any, Callable

from core.auth import RESERVED_USERNAMES
from src.security_evidence_broker import SecurityEvidenceEnvelope, build_security_evidence_envelope
from src.security_evidence_sources import auth_outcome_projection


STEP_UP_TTL_SECONDS = 300
MAX_STEP_UP_GRANTS = 256
_SYNTHETIC_IDENTITIES = frozenset(set(RESERVED_USERNAMES) | {"internal", "service"})


class SecurityActionAuthorizationError(RuntimeError):
    """Content-free rejection for every privileged security-action failure."""


@dataclass(frozen=True, slots=True)
class RedactedAuthEvent:
    envelope: SecurityEvidenceEnvelope
    raw_content_visible: bool = False


@dataclass(slots=True)
class _Grant:
    session_binding: str
    username: str
    action_id: str
    action_version: int
    scope_fingerprint: str
    policy_revision: str
    authority_revision: int
    issued_at: float
    consumed: bool = False


def build_redacted_auth_event(*, username: str, outcome: str, source_familiarity: str = "unknown", session_created: str = "not_applicable") -> RedactedAuthEvent:
    """Project an auth outcome through the existing evidence-broker contract."""
    status = outcome if outcome in {"success", "failed", "blocked", "unknown", "not_applicable"} else "unknown"
    principal_ref = "principal:sha256:" + hashlib.sha256(str(username).strip().lower().encode("utf-8")).hexdigest()
    envelope = build_security_evidence_envelope(auth_outcome_projection(
        outcome=status, principal_ref=principal_ref, source_familiarity=source_familiarity,
        session_created=session_created,
    ))
    return RedactedAuthEvent(envelope=envelope)


class SecurityActionAuthorization:
    """Issue and consume one in-memory, session-bound operator step-up grant."""

    def __init__(self, auth_manager: Any, *, clock: Callable[[], float] = time.time, capacity: int = MAX_STEP_UP_GRANTS) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        self._auth_manager = auth_manager
        self._clock = clock
        self._lock = threading.Lock()
        self._clock_lock = threading.Lock()
        self._last_now: float | None = None
        self._capacity = capacity
        self._grants: dict[tuple[str, str], _Grant] = {}

    def step_up(self, *, session_token: Any, username: Any, password: Any, totp_code: Any, action: Any, auth_kind: str) -> RedactedAuthEvent:
        """Accept password plus current live TOTP and bind it to one action."""
        principal, binding = self.verify_factors(session_token=session_token, username=username, password=password, totp_code=totp_code, auth_kind=auth_kind)
        fields = self._action_fields(action)
        fields["authority_revision"] = self._authority_revision()
        now = self._now()
        with self._lock:
            self._purge_locked(now)
            key = (binding, fields["action_id"])
            if key not in self._grants and len(self._grants) >= self._capacity:
                raise SecurityActionAuthorizationError("security action authorization unavailable")
            self._grants[key] = _Grant(
                session_binding=binding, username=principal, issued_at=now, **fields
            )
        return build_redacted_auth_event(username=principal, outcome="success")

    def step_up_with_emission(self, *, session_token: Any, username: Any, password: Any, totp_code: Any, action: Any, auth_kind: str, emit: Callable[[RedactedAuthEvent], bool]) -> RedactedAuthEvent:
        """Accept the required redacted audit event before publishing a grant."""
        if not callable(emit):
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        principal, _binding = self.verify_factors(session_token=session_token, username=username, password=password, totp_code=totp_code, auth_kind=auth_kind)
        event = build_redacted_auth_event(username=principal, outcome="success")
        # The sink runs before any grant exists and outside the grant lock.
        if emit(event) is not True:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        # Revalidate after an arbitrary in-process sink delay, then publish.
        return self.step_up(session_token=session_token, username=username, password=password, totp_code=totp_code, action=action, auth_kind=auth_kind)

    def verify_factors(self, *, session_token: Any, username: Any, password: Any, totp_code: Any, auth_kind: str) -> tuple[str, str]:
        """Verify the privileged factors without creating a reusable proof."""
        principal, binding = self._validated_browser_session(session_token, username, auth_kind)
        if not self._is_current_admin(principal) or not isinstance(password, str) or not isinstance(totp_code, str):
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        try:
            password_ok = bool(self._auth_manager.verify_password(principal, password))
            totp_ok = bool(self._auth_manager.totp_verify_live(principal, totp_code)) if password_ok else False
        except Exception:
            password_ok = totp_ok = False
        if not password_ok or not totp_ok:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        return principal, binding

    def discard(self, *, session_token: Any, action: Any) -> None:
        """Remove a newly issued grant if its required redacted audit emission fails."""
        if not isinstance(session_token, str):
            return
        try:
            action_id = self._action_fields(action)["action_id"]
        except SecurityActionAuthorizationError:
            return
        binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        with self._lock:
            self._grants.pop((binding, action_id), None)

    def consume(self, *, session_token: Any, username: Any, action: Any, auth_kind: str) -> RedactedAuthEvent:
        """Atomically consume the matching grant after revalidating session and role."""
        principal, binding = self._validated_browser_session(session_token, username, auth_kind)
        if not self._is_current_admin(principal):
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        if not self._totp_still_mandatory(principal):
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        fields = self._action_fields(action)
        fields["authority_revision"] = self._authority_revision()
        now = self._now()
        with self._lock:
            self._purge_locked(now)
            grant = self._grants.get((binding, fields["action_id"]))
            if grant is None or grant.consumed or now - grant.issued_at >= STEP_UP_TTL_SECONDS:
                raise SecurityActionAuthorizationError("security action authorization unavailable")
            if grant.username != principal or any(getattr(grant, key) != value for key, value in fields.items()):
                raise SecurityActionAuthorizationError("security action authorization unavailable")
            grant.consumed = True
        return build_redacted_auth_event(username=principal, outcome="success")

    def _validated_browser_session(self, session_token: Any, username: Any, auth_kind: str) -> tuple[str, str]:
        if auth_kind != "browser_cookie" or not isinstance(session_token, str) or not session_token:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        principal = str(username or "").strip().lower()
        if not principal or principal in _SYNTHETIC_IDENTITIES:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        try:
            valid = bool(self._auth_manager.validate_token(session_token))
            session_user = self._auth_manager.get_username_for_token(session_token)
        except Exception:
            valid = False
            session_user = None
        if not valid or str(session_user or "").strip().lower() != principal:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        # The irreversible raw token never leaves this stack frame.
        binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        return principal, binding

    def _is_current_admin(self, username: str) -> bool:
        try:
            return bool(self._auth_manager.is_admin(username))
        except Exception:
            return False

    def _totp_still_mandatory(self, username: str) -> bool:
        try:
            return bool(self._auth_manager.totp_enabled(username))
        except Exception:
            return False

    def _authority_revision(self) -> int:
        try:
            revision = self._auth_manager.security_action_revision()
        except Exception:
            raise SecurityActionAuthorizationError("security action authorization unavailable") from None
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        return revision

    def _purge_locked(self, now: float) -> None:
        expired = [key for key, grant in self._grants.items() if grant.consumed or now - grant.issued_at >= STEP_UP_TTL_SECONDS]
        for key in expired:
            self._grants.pop(key, None)

    @staticmethod
    def _action_fields(action: Any) -> dict[str, Any]:
        try:
            action_id = action.action_id
            version = action.version
            scope = action.scope_fingerprint
            policy = action.policy_revision
        except Exception as exc:
            raise SecurityActionAuthorizationError("security action authorization unavailable") from exc
        if not isinstance(action_id, str) or not isinstance(version, int) or version < 1:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        if not isinstance(scope, str) or not isinstance(policy, str):
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        return {"action_id": action_id, "action_version": version, "scope_fingerprint": scope, "policy_revision": policy}

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            raise SecurityActionAuthorizationError("security action authorization unavailable")
        now = float(value)
        with self._clock_lock:
            if self._last_now is not None and now < self._last_now:
                raise SecurityActionAuthorizationError("security action authorization unavailable")
            self._last_now = now
        return now


__all__ = ["MAX_STEP_UP_GRANTS", "RedactedAuthEvent", "STEP_UP_TTL_SECONDS", "SecurityActionAuthorization", "SecurityActionAuthorizationError", "build_redacted_auth_event"]
