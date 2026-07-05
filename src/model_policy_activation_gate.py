"""Activation gate for RL-lite routing policy modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


MODEL_POLICY_ACTIVATION_SCHEMA = "odysseus.model_policy_activation_gate.v1"


class ActivationGateStatus(StrEnum):
    GO = "go"
    NEEDS_REVIEW = "needs_review"
    FALLBACK_REQUIRED = "fallback_required"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ModelPolicyActivationEvidence:
    episode_count: int
    min_episodes_required: int
    offline_pass_rate: float
    min_offline_pass_rate: float
    privacy_violation_count: int = 0
    local_only_violation_count: int = 0
    regression_failure_count: int = 0

    @classmethod
    def create(cls, **kwargs: Any) -> "ModelPolicyActivationEvidence":
        return cls(
            episode_count=_nonnegative_int(kwargs.get("episode_count")),
            min_episodes_required=max(1, _nonnegative_int(kwargs.get("min_episodes_required") or 20)),
            offline_pass_rate=_ratio(kwargs.get("offline_pass_rate")),
            min_offline_pass_rate=_ratio(kwargs.get("min_offline_pass_rate") or 0.85),
            privacy_violation_count=_nonnegative_int(kwargs.get("privacy_violation_count")),
            local_only_violation_count=_nonnegative_int(kwargs.get("local_only_violation_count")),
            regression_failure_count=_nonnegative_int(kwargs.get("regression_failure_count")),
        )


@dataclass(frozen=True, slots=True)
class ModelPolicyActivationDecision:
    status: ActivationGateStatus
    reason_codes: tuple[str, ...]
    active_allowed: bool

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema": MODEL_POLICY_ACTIVATION_SCHEMA,
            "status": self.status.value,
            "reason_codes": self.reason_codes,
            "active_allowed": self.active_allowed,
            "raw_prompt_visible": False,
            "raw_output_visible": False,
            "private_content_visible": False,
        }


def evaluate_policy_activation(evidence: ModelPolicyActivationEvidence | Mapping[str, Any]) -> ModelPolicyActivationDecision:
    if not isinstance(evidence, ModelPolicyActivationEvidence):
        evidence = ModelPolicyActivationEvidence.create(**dict(evidence or {}))
    reasons: list[str] = []
    if evidence.privacy_violation_count:
        reasons.append("privacy_violations_present")
    if evidence.local_only_violation_count:
        reasons.append("local_only_violations_present")
    if evidence.regression_failure_count:
        reasons.append("regression_failures_present")
    if evidence.episode_count < evidence.min_episodes_required:
        reasons.append("insufficient_episode_count")
    if evidence.offline_pass_rate < evidence.min_offline_pass_rate:
        reasons.append("offline_pass_rate_below_threshold")

    if any(reason.endswith("_present") for reason in reasons):
        status = ActivationGateStatus.BLOCKED
    elif "offline_pass_rate_below_threshold" in reasons:
        status = ActivationGateStatus.FALLBACK_REQUIRED
    elif "insufficient_episode_count" in reasons:
        status = ActivationGateStatus.NEEDS_REVIEW
    else:
        status = ActivationGateStatus.GO
        reasons.append("activation_evidence_satisfied")
    return ModelPolicyActivationDecision(
        status=status,
        reason_codes=tuple(reasons),
        active_allowed=status == ActivationGateStatus.GO,
    )


def _nonnegative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _ratio(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(number, 1.0))
