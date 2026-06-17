"""Operator-controlled activation planning for AUTO orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable

from src.orchestration_runtime_readiness import ReadinessStatus, RuntimeReadinessReport


_MAX_ID = 80
_MAX_TEXT = 180
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class OperatorActivationError(ValueError):
    """Raised when orchestration activation inputs are invalid."""


class ActivationMode(StrEnum):
    DISABLED = "disabled"
    READ_ONLY = "read_only"
    PREPARE_DISPATCH = "prepare_dispatch"
    DISPATCH_REQUIRES_CONFIRM = "dispatch_requires_confirm"
    LIVE_DISPATCH_LIMITED = "live_dispatch_limited"


class ActivationAction(StrEnum):
    VIEW_DASHBOARD = "view_dashboard"
    REVIEW_REGISTRY = "review_registry"
    PREPARE_MAILBOX_DRAFT = "prepare_mailbox_draft"
    PREPARE_DISPATCH_PLAN = "prepare_dispatch_plan"
    CONFIRM_DISPATCH = "confirm_dispatch"
    EXECUTE_LIVE_DISPATCH = "execute_live_dispatch"


class ActivationDecision(StrEnum):
    ALLOW = "allow"
    PREPARE_ONLY = "prepare_only"
    BLOCK = "block"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise OperatorActivationError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise OperatorActivationError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise OperatorActivationError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise OperatorActivationError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_mode(value: ActivationMode | str) -> ActivationMode:
    if isinstance(value, ActivationMode):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "disabled": ActivationMode.DISABLED,
        "read_only": ActivationMode.READ_ONLY,
        "read-only": ActivationMode.READ_ONLY,
        "prepare_dispatch": ActivationMode.PREPARE_DISPATCH,
        "prepare-dispatch": ActivationMode.PREPARE_DISPATCH,
        "dispatch_requires_confirm": ActivationMode.DISPATCH_REQUIRES_CONFIRM,
        "dispatch-requires-confirm": ActivationMode.DISPATCH_REQUIRES_CONFIRM,
        "live_dispatch_limited": ActivationMode.LIVE_DISPATCH_LIMITED,
        "live-dispatch-limited": ActivationMode.LIVE_DISPATCH_LIMITED,
    }
    if raw not in alias_map:
        raise OperatorActivationError("unsupported activation mode")
    return alias_map[raw]


def _normalize_decision(value: ActivationDecision | str) -> ActivationDecision:
    if isinstance(value, ActivationDecision):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "allow": ActivationDecision.ALLOW,
        "prepare_only": ActivationDecision.PREPARE_ONLY,
        "prepare-only": ActivationDecision.PREPARE_ONLY,
        "block": ActivationDecision.BLOCK,
    }
    if raw not in alias_map:
        raise OperatorActivationError("unsupported activation decision")
    return alias_map[raw]


def _normalize_action(value: ActivationAction | str) -> ActivationAction:
    if isinstance(value, ActivationAction):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "view_dashboard": ActivationAction.VIEW_DASHBOARD,
        "view-dashboard": ActivationAction.VIEW_DASHBOARD,
        "review_registry": ActivationAction.REVIEW_REGISTRY,
        "review-registry": ActivationAction.REVIEW_REGISTRY,
        "prepare_mailbox_draft": ActivationAction.PREPARE_MAILBOX_DRAFT,
        "prepare-mailbox-draft": ActivationAction.PREPARE_MAILBOX_DRAFT,
        "prepare_dispatch_plan": ActivationAction.PREPARE_DISPATCH_PLAN,
        "prepare-dispatch-plan": ActivationAction.PREPARE_DISPATCH_PLAN,
        "confirm_dispatch": ActivationAction.CONFIRM_DISPATCH,
        "confirm-dispatch": ActivationAction.CONFIRM_DISPATCH,
        "execute_live_dispatch": ActivationAction.EXECUTE_LIVE_DISPATCH,
        "execute-live-dispatch": ActivationAction.EXECUTE_LIVE_DISPATCH,
    }
    if raw not in alias_map:
        raise OperatorActivationError("unsupported activation action")
    return alias_map[raw]


@dataclass(frozen=True, slots=True)
class OperatorActivationPolicy:
    requested_mode: ActivationMode
    operator_approved: bool
    allow_live_dispatch: bool

    @classmethod
    def create(
        cls,
        *,
        requested_mode: ActivationMode | str,
        operator_approved: bool,
        allow_live_dispatch: bool,
    ) -> "OperatorActivationPolicy":
        return cls(
            requested_mode=_normalize_mode(requested_mode),
            operator_approved=bool(operator_approved),
            allow_live_dispatch=bool(allow_live_dispatch),
        )


@dataclass(frozen=True, slots=True)
class ActivationPlanItem:
    action: ActivationAction
    decision: ActivationDecision
    reason: str

    @classmethod
    def create(
        cls,
        *,
        action: ActivationAction | str,
        decision: ActivationDecision | str,
        reason: Any,
    ) -> "ActivationPlanItem":
        return cls(
            action=_normalize_action(action),
            decision=_normalize_decision(decision),
            reason=_normalize_text(reason, field_name="reason", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class OrchestrationActivationPlan:
    mode: ActivationMode
    allowed_actions: tuple[ActivationPlanItem, ...]
    blocked_actions: tuple[ActivationPlanItem, ...]
    open_gap_count: int
    ok: bool
    next_safe_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "open_gap_count": self.open_gap_count,
            "ok": self.ok,
            "next_safe_action": self.next_safe_action,
            "allowed_actions": tuple(
                {
                    "action": item.action.value,
                    "decision": item.decision.value,
                    "reason": item.reason,
                }
                for item in self.allowed_actions
            ),
            "blocked_actions": tuple(
                {
                    "action": item.action.value,
                    "decision": item.decision.value,
                    "reason": item.reason,
                }
                for item in self.blocked_actions
            ),
        }


def build_orchestration_activation_plan(
    *,
    readiness: RuntimeReadinessReport,
    policy: OperatorActivationPolicy,
) -> OrchestrationActivationPlan:
    if not isinstance(readiness, RuntimeReadinessReport):
        raise OperatorActivationError("readiness must be a RuntimeReadinessReport")
    if not isinstance(policy, OperatorActivationPolicy):
        raise OperatorActivationError("policy must be an OperatorActivationPolicy")

    allowed: list[ActivationPlanItem] = [
        ActivationPlanItem.create(
            action="view_dashboard",
            decision="allow",
            reason="Dashboard inspection remains read-only and safe.",
        ),
        ActivationPlanItem.create(
            action="review_registry",
            decision="allow",
            reason="Registry review stays read-only and does not trigger hooks.",
        ),
    ]
    blocked: list[ActivationPlanItem] = []

    effective_mode = policy.requested_mode
    if effective_mode == ActivationMode.DISABLED:
        blocked.extend(
            [
                ActivationPlanItem.create(
                    action="prepare_mailbox_draft",
                    decision="block",
                    reason="Activation mode is disabled.",
                ),
                ActivationPlanItem.create(
                    action="prepare_dispatch_plan",
                    decision="block",
                    reason="Activation mode is disabled.",
                ),
                ActivationPlanItem.create(
                    action="confirm_dispatch",
                    decision="block",
                    reason="Activation mode is disabled.",
                ),
                ActivationPlanItem.create(
                    action="execute_live_dispatch",
                    decision="block",
                    reason="Activation mode is disabled.",
                ),
            ]
        )
    elif effective_mode == ActivationMode.READ_ONLY:
        blocked.extend(
            [
                ActivationPlanItem.create(
                    action="prepare_mailbox_draft",
                    decision="block",
                    reason="Read-only mode does not permit dispatch preparation.",
                ),
                ActivationPlanItem.create(
                    action="prepare_dispatch_plan",
                    decision="block",
                    reason="Read-only mode does not permit dispatch planning.",
                ),
                ActivationPlanItem.create(
                    action="confirm_dispatch",
                    decision="block",
                    reason="Read-only mode never allows dispatch confirmation.",
                ),
                ActivationPlanItem.create(
                    action="execute_live_dispatch",
                    decision="block",
                    reason="Read-only mode never allows live dispatch.",
                ),
            ]
        )
    elif effective_mode == ActivationMode.PREPARE_DISPATCH:
        allowed.extend(
            [
                ActivationPlanItem.create(
                    action="prepare_mailbox_draft",
                    decision="prepare_only",
                    reason="Mailbox drafts can be prepared without sending.",
                ),
                ActivationPlanItem.create(
                    action="prepare_dispatch_plan",
                    decision="prepare_only",
                    reason="Dispatch plans can be assembled without executing live hooks.",
                ),
            ]
        )
        blocked.extend(
            [
                ActivationPlanItem.create(
                    action="confirm_dispatch",
                    decision="block",
                    reason="Prepare-dispatch mode does not allow send confirmation.",
                ),
                ActivationPlanItem.create(
                    action="execute_live_dispatch",
                    decision="block",
                    reason="Prepare-dispatch mode never executes live dispatch.",
                ),
            ]
        )
    elif effective_mode == ActivationMode.DISPATCH_REQUIRES_CONFIRM:
        allowed.extend(
            [
                ActivationPlanItem.create(
                    action="prepare_mailbox_draft",
                    decision="prepare_only",
                    reason="Draft preparation is allowed before operator confirmation.",
                ),
                ActivationPlanItem.create(
                    action="prepare_dispatch_plan",
                    decision="prepare_only",
                    reason="Dispatch plans can be prepared pending operator confirmation.",
                ),
            ]
        )
        if policy.operator_approved:
            allowed.append(
                ActivationPlanItem.create(
                    action="confirm_dispatch",
                    decision="allow",
                    reason="Operator approval allows confirmation, but not live send execution.",
                )
            )
        else:
            blocked.append(
                ActivationPlanItem.create(
                    action="confirm_dispatch",
                    decision="block",
                    reason="Dispatch confirmation requires explicit operator approval.",
                )
            )
        blocked.append(
            ActivationPlanItem.create(
                action="execute_live_dispatch",
                decision="block",
                reason="Live dispatch remains disabled until readiness is fully clean.",
            )
        )
    elif effective_mode == ActivationMode.LIVE_DISPATCH_LIMITED:
        allowed.extend(
            [
                ActivationPlanItem.create(
                    action="prepare_mailbox_draft",
                    decision="prepare_only",
                    reason="Draft preparation stays allowed before any limited live activation.",
                ),
                ActivationPlanItem.create(
                    action="prepare_dispatch_plan",
                    decision="prepare_only",
                    reason="Dispatch planning stays preparatory until hooks are confirmed safe.",
                ),
            ]
        )
        if policy.operator_approved and policy.allow_live_dispatch and readiness.ok and not readiness.blocked:
            allowed.extend(
                [
                    ActivationPlanItem.create(
                        action="confirm_dispatch",
                        decision="allow",
                        reason="Operator approval and clean readiness allow limited dispatch confirmation.",
                    ),
                    ActivationPlanItem.create(
                        action="execute_live_dispatch",
                        decision="allow",
                        reason="Live dispatch is limited and only allowed with explicit approval and clean readiness.",
                    ),
                ]
            )
        else:
            blocked.extend(
                [
                    ActivationPlanItem.create(
                        action="confirm_dispatch",
                        decision="block",
                        reason="Live dispatch confirmation requires clean readiness and explicit operator approval.",
                    ),
                    ActivationPlanItem.create(
                        action="execute_live_dispatch",
                        decision="block",
                        reason="Live dispatch stays blocked while runtime gaps or missing operator approval remain.",
                    ),
                ]
            )
    else:
        raise OperatorActivationError("unsupported activation mode")

    return OrchestrationActivationPlan(
        mode=effective_mode,
        allowed_actions=tuple(sorted(allowed, key=lambda item: item.action.value)),
        blocked_actions=tuple(sorted(blocked, key=lambda item: item.action.value)),
        open_gap_count=readiness.open_gap_count,
        ok=effective_mode == ActivationMode.LIVE_DISPATCH_LIMITED and policy.operator_approved and policy.allow_live_dispatch and readiness.ok and not readiness.blocked,
        next_safe_action=readiness.next_safe_action,
    )
