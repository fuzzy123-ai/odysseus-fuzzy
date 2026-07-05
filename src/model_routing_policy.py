"""Read-only RL-lite model routing policy recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.model_reward_contract import ModelEpisodeState


MODEL_ROUTING_POLICY_SCHEMA = "odysseus.model_routing_policy.v1"


class ModelRoutingPolicyError(ValueError):
    """Raised when policy inputs are invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class ModelRoutingCandidate:
    candidate_id: str
    provider: str
    model: str
    answer_mode: str

    @classmethod
    def create(cls, *, candidate_id: Any, provider: Any, model: Any, answer_mode: Any) -> "ModelRoutingCandidate":
        return cls(
            candidate_id=_label(candidate_id, "candidate_id"),
            provider=_label(provider, "provider"),
            model=_label(model, "model"),
            answer_mode=_label(answer_mode, "answer_mode"),
        )


@dataclass(frozen=True, slots=True)
class ModelRoutingPolicyDecision:
    ordered_candidate_ids: tuple[str, ...]
    blocked_candidate_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    shadow_only: bool = True

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema": MODEL_ROUTING_POLICY_SCHEMA,
            "ordered_candidate_ids": self.ordered_candidate_ids,
            "blocked_candidate_ids": self.blocked_candidate_ids,
            "reason_codes": self.reason_codes,
            "shadow_only": self.shadow_only,
            "raw_episode_visible": False,
            "raw_prompt_visible": False,
            "raw_output_visible": False,
        }


def recommend_model_routing(
    *,
    state: ModelEpisodeState,
    candidates: Iterable[ModelRoutingCandidate],
    reward_history: Iterable[Mapping[str, Any]] = (),
    min_observations: int = 2,
) -> ModelRoutingPolicyDecision:
    """Recommend a candidate order without changing production routing."""

    candidate_list = list(candidates)
    if not candidate_list:
        raise ModelRoutingPolicyError("at least one candidate is required")
    candidate_ids = {item.candidate_id for item in candidate_list}
    stats = _history_stats(reward_history, candidate_ids)
    blocked = tuple(
        item.candidate_id
        for item in candidate_list
        if state.local_only_required and item.answer_mode != "local" and item.provider.lower() not in {"local", "ollama"}
    )
    allowed = [item for item in candidate_list if item.candidate_id not in blocked]
    reasons = ["shadow_only", "cold_start_preserves_order"]
    if blocked:
        reasons.append("privacy_hard_gate")
    if any(stats.get(item.candidate_id, {}).get("count", 0) >= min_observations for item in allowed):
        reasons = ["shadow_only", "reward_history_reorder"] + (["privacy_hard_gate"] if blocked else [])
        allowed = sorted(
            allowed,
            key=lambda item: (
                stats.get(item.candidate_id, {}).get("count", 0) >= min_observations,
                stats.get(item.candidate_id, {}).get("avg", 0.0),
            ),
            reverse=True,
        )
    ordered = tuple(item.candidate_id for item in allowed) + blocked
    return ModelRoutingPolicyDecision(
        ordered_candidate_ids=ordered,
        blocked_candidate_ids=blocked,
        reason_codes=tuple(reasons),
        shadow_only=True,
    )


def explain_policy_decision(decision: ModelRoutingPolicyDecision) -> dict[str, Any]:
    if not isinstance(decision, ModelRoutingPolicyDecision):
        raise ModelRoutingPolicyError("decision must be a ModelRoutingPolicyDecision")
    return decision.audit_summary()


def _history_stats(history: Iterable[Mapping[str, Any]], candidate_ids: set[str]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for item in history:
        cid = str(item.get("candidate_id") or item.get("model") or "")
        if cid not in candidate_ids:
            continue
        try:
            score = float(item.get("total_score", 0))
        except (TypeError, ValueError):
            score = 0.0
        bucket = stats.setdefault(cid, {"count": 0, "sum": 0.0, "avg": 0.0})
        bucket["count"] += 1
        bucket["sum"] += score
        bucket["avg"] = bucket["sum"] / bucket["count"]
    return stats


def _label(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelRoutingPolicyError(f"{field} must not be empty")
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "token", "secret", "password")):
        raise ModelRoutingPolicyError(f"{field} contains forbidden secret marker")
    if len(text) > 120:
        raise ModelRoutingPolicyError(f"{field} exceeds max length")
    return text
