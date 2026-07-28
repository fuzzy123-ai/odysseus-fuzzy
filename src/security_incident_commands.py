"""Action-ID-only, non-executing incident command adapter."""

from __future__ import annotations

import hashlib
from typing import Any

from src.security_action_authorization import SecurityActionAuthorization, SecurityActionAuthorizationError


class SecurityIncidentCommandError(RuntimeError):
    """Generic command failure; detailed durable state remains server-side."""


class SecurityIncidentCommands:
    def __init__(self, store: Any, authorization: SecurityActionAuthorization) -> None:
        self._store = store
        self._authorization = authorization

    def approve(self, *, action_id: str, session_token: Any, username: Any, auth_kind: str = "browser_cookie") -> dict[str, Any]:
        action = self._action(action_id)
        event = self._consume(action, session_token=session_token, username=username, auth_kind=auth_kind)
        digest = self._digest(action.action_id, action.version, event.envelope.evidence_ref)
        try:
            result = self._store.approve(
                action_id=action.action_id, expected_version=action.version,
                approval_id=f"approval-{digest[:24]}", approval_ref=f"approval:sha256:{digest}",
                scope_fingerprint=action.scope_fingerprint, policy_revision=action.policy_revision,
                audit_ref=f"audit:sha256:{self._digest('approve', digest)}",
            )
        except Exception as exc:
            raise SecurityIncidentCommandError("security action command unavailable") from exc
        return self._result("approved", result, event.envelope.evidence_ref)

    def deny(self, *, action_id: str, session_token: Any, username: Any, auth_kind: str = "browser_cookie") -> dict[str, Any]:
        return self._transition("denied", action_id, session_token, username, auth_kind)

    def expire(self, *, action_id: str, session_token: Any, username: Any, auth_kind: str = "browser_cookie") -> dict[str, Any]:
        return self._transition("expired", action_id, session_token, username, auth_kind)

    def _transition(self, target: str, action_id: str, session_token: Any, username: Any, auth_kind: str) -> dict[str, Any]:
        action = self._action(action_id)
        event = self._consume(action, session_token=session_token, username=username, auth_kind=auth_kind)
        try:
            result = self._store.transition(
                action_id=action.action_id, expected_version=action.version, target_state=target,
                audit_ref=f"audit:sha256:{self._digest(target, action.action_id, action.version, event.envelope.evidence_ref)}",
            )
        except Exception as exc:
            raise SecurityIncidentCommandError("security action command unavailable") from exc
        return self._result(target, result, event.envelope.evidence_ref)

    def _action(self, action_id: Any) -> Any:
        if not isinstance(action_id, str):
            raise SecurityIncidentCommandError("security action command unavailable")
        try:
            return self._store.get_action(action_id)
        except Exception as exc:
            raise SecurityIncidentCommandError("security action command unavailable") from exc

    def _consume(self, action: Any, *, session_token: Any, username: Any, auth_kind: str) -> Any:
        try:
            return self._authorization.consume(session_token=session_token, username=username, action=action, auth_kind=auth_kind)
        except SecurityActionAuthorizationError as exc:
            raise SecurityIncidentCommandError("security action command unavailable") from exc

    @staticmethod
    def _digest(*values: Any) -> str:
        return hashlib.sha256("|".join(str(value) for value in values).encode("utf-8")).hexdigest()

    @staticmethod
    def _result(status: str, action: Any, evidence_ref: str) -> dict[str, Any]:
        return {
            "status": status, "action_id": action.action_id, "action_state": action.state,
            "action_version": action.version, "executed": False, "delivery_performed": False,
            "auth_evidence_ref": evidence_ref, "raw_content_visible": False,
        }


__all__ = ["SecurityIncidentCommandError", "SecurityIncidentCommands"]
