"""Durable automatic delivery coordinator for IP-aware auth access alerts."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any

from src.security_auth_incident_bridge import (
    operator_notification_policy_revision,
    operator_notification_scope_fingerprint,
)
from src.security_incident_delivery import (
    SecurityIncidentDeliveryAdapter,
    TrustedTelegramDeliveryReadiness,
    build_access_alert_delivery_request,
    delivery_idempotency_identity,
)
from src.security_incident_notifications import (
    canonical_access_alert_body_ref,
    canonical_operator_notification_target_class_ref,
)


SECURITY_INCIDENT_DELIVERY_ENABLED_ENV = "ODYSSEUS_SECURITY_INCIDENT_DELIVERY_ENABLED"
STANDING_DELIVERY_POLICY = "OPS-ALERT-DELIVERY-GO"
MAX_RECONCILE_CANDIDATES = 8
DEFAULT_RECONCILE_INTERVAL_SECONDS = 30.0


def automatic_delivery_enabled(value: Any) -> bool:
    """Only the exact named true value enables automatic delivery."""
    return value == "true"


def server_owned_telegram_readiness() -> TrustedTelegramDeliveryReadiness:
    """Derive fixed readiness booleans from named server configuration only."""
    return TrustedTelegramDeliveryReadiness.from_server_configuration()


class SecurityAccessAlertDeliveryCoordinator:
    """Recover and advance only standing-policy operator notifications."""

    def __init__(
        self,
        store: Any,
        adapter: SecurityIncidentDeliveryAdapter,
        *,
        readiness_provider: Callable[[], TrustedTelegramDeliveryReadiness] = server_owned_telegram_readiness,
    ) -> None:
        if not callable(getattr(store, "pending_operator_notification_actions", None)):
            raise ValueError("security access alert delivery unavailable")
        if not isinstance(adapter, SecurityIncidentDeliveryAdapter) or not callable(readiness_provider):
            raise ValueError("security access alert delivery unavailable")
        self._store = store
        self._adapter = adapter
        self._readiness_provider = readiness_provider
        self._cursor: str | None = None

    def reconcile(self) -> dict[str, Any]:
        """Advance a bounded candidate set once; terminal states are never queried."""
        counts = {"examined": 0, "prepared": 0, "approved": 0, "attempted": 0}
        try:
            candidates = self._store.pending_operator_notification_actions(
                limit=MAX_RECONCILE_CANDIDATES, after_action_id=self._cursor,
            )
            if not candidates and self._cursor is not None:
                self._cursor = None
                candidates = self._store.pending_operator_notification_actions(
                    limit=MAX_RECONCILE_CANDIDATES,
                )
        except Exception:
            return {**counts, "status": "blocked", "raw_content_visible": False}
        delivery_attempted = False
        for candidate in candidates:
            self._cursor = candidate.action_id
            counts["examined"] += 1
            try:
                action = self._validate_candidate(candidate)
                if action.state == "proposed":
                    action = self._store.transition(
                        action_id=action.action_id, expected_version=action.version,
                        target_state="prepared",
                        audit_ref=_ref("audit", "standing_prepare", action.action_id),
                    )
                    counts["prepared"] += 1
                if action.state == "prepared":
                    action = self._store.approve(
                        action_id=action.action_id, expected_version=action.version,
                        approval_id="standing-" + _digest("approval_id", action.action_id)[:32],
                        approval_ref=_ref("approval", STANDING_DELIVERY_POLICY, action.action_id),
                        scope_fingerprint=action.scope_fingerprint,
                        policy_revision=action.policy_revision,
                        audit_ref=_ref("audit", "standing_approve", action.action_id),
                    )
                    counts["approved"] += 1
                if action.state == "approved":
                    readiness = self._readiness_provider()
                    if not readiness.values().get("send_ready") or delivery_attempted:
                        continue
                    request = build_access_alert_delivery_request(
                        action, readiness, self._store,
                    )
                    delivery_attempted = True
                    self._adapter.attempt(request)
                    counts["attempted"] += 1
            except Exception:
                continue
        return {**counts, "status": "ok", "raw_content_visible": False}

    async def run(
        self,
        wake: asyncio.Event,
        *,
        interval_seconds: float = DEFAULT_RECONCILE_INTERVAL_SECONDS,
    ) -> None:
        if not isinstance(wake, asyncio.Event) or not 1 <= interval_seconds <= 300:
            raise ValueError("security access alert delivery unavailable")
        while True:
            work = asyncio.create_task(asyncio.to_thread(self.reconcile))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
            wake.clear()

    def _validate_candidate(self, action: Any) -> Any:
        if action.action_type != "operator_notification" or action.state not in {
            "proposed", "prepared", "approved",
        }:
            raise ValueError
        context = self._store.get_incident_context_for_action(action.action_id)
        body_ref = canonical_access_alert_body_ref(
            event_class=context.event_class, accessing_ip=context.accessing_ip,
        )
        expected = delivery_idempotency_identity(
            incident_id=action.incident_id, action_id=action.action_id,
            scope_fingerprint=operator_notification_scope_fingerprint(action.incident_id),
            policy_revision=operator_notification_policy_revision(context.event_class),
            body_ref=body_ref,
            approved_target_class_ref=canonical_operator_notification_target_class_ref(),
        )
        if (
            context.suppression_decision != "notify"
            or context.notification_binding_ref != body_ref
            or action.scope_fingerprint != operator_notification_scope_fingerprint(action.incident_id)
            or action.policy_revision != operator_notification_policy_revision(context.event_class)
            or action.idempotency_key != expected
        ):
            raise ValueError
        return action


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _ref(kind: str, *parts: str) -> str:
    return f"{kind}:sha256:{_digest(*parts)}"


__all__ = [
    "DEFAULT_RECONCILE_INTERVAL_SECONDS", "MAX_RECONCILE_CANDIDATES",
    "SECURITY_INCIDENT_DELIVERY_ENABLED_ENV", "STANDING_DELIVERY_POLICY",
    "SecurityAccessAlertDeliveryCoordinator", "automatic_delivery_enabled",
    "server_owned_telegram_readiness",
]
