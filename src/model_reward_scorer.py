"""Deterministic RL-lite reward scoring for redacted model episodes."""

from __future__ import annotations

from typing import Any

from src.model_reward_contract import (
    EpisodeOutcomeStatus,
    ModelEpisode,
    ModelEpisodeAction,
    ModelEpisodeOutcome,
    ModelEpisodeState,
    ModelReward,
    RewardStatus,
)


def score_model_episode(
    *,
    state: ModelEpisodeState,
    action: ModelEpisodeAction,
    outcome: ModelEpisodeOutcome,
    user_feedback: int = 0,
) -> ModelReward:
    """Score one redacted episode on a stable -100..100 scale."""

    components: dict[str, int] = {
        "outcome": _outcome_component(outcome.status),
        "citation": _citation_component(state, outcome),
        "privacy": _privacy_component(state, action),
        "confidence": _confidence_component(outcome.confidence),
        "fallback": _fallback_component(outcome),
        "latency": _latency_component(outcome.duration_ms),
        "user_feedback": _bounded(int(user_feedback or 0), -20, 20),
    }
    total = _bounded(sum(components.values()), -100, 100)
    reasons = _reason_codes(state=state, action=action, outcome=outcome, components=components)
    status = RewardStatus.POSITIVE
    if total < 0:
        status = RewardStatus.NEGATIVE
    elif total == 0:
        status = RewardStatus.NEUTRAL
    if outcome.status == EpisodeOutcomeStatus.BLOCKED or components["privacy"] < 0:
        status = RewardStatus.BLOCKED if components["privacy"] < 0 else status
    return ModelReward.create(
        total_score=total,
        component_scores=components,
        status=status.value,
        reason_codes=reasons,
    )


def score_episode(episode: ModelEpisode, *, user_feedback: int = 0) -> ModelEpisode:
    """Return a copy of an episode with deterministic reward attached."""

    reward = score_model_episode(
        state=episode.state,
        action=episode.action,
        outcome=episode.outcome,
        user_feedback=user_feedback,
    )
    return ModelEpisode.create(state=episode.state, action=episode.action, outcome=episode.outcome, reward=reward)


def _outcome_component(status: EpisodeOutcomeStatus) -> int:
    return {
        EpisodeOutcomeStatus.SUCCESS: 30,
        EpisodeOutcomeStatus.PARTIAL: 8,
        EpisodeOutcomeStatus.FALLBACK: 0,
        EpisodeOutcomeStatus.UNKNOWN: -8,
        EpisodeOutcomeStatus.BLOCKED: -15,
        EpisodeOutcomeStatus.FAILED: -30,
    }[status]


def _citation_component(state: ModelEpisodeState, outcome: ModelEpisodeOutcome) -> int:
    if not state.citation_required:
        return 5 if outcome.citation_count else 0
    if outcome.citation_count <= 0:
        return -30
    if outcome.citation_count >= min(max(state.retrieval_doc_count, 1), 3):
        return 20
    return 8


def _privacy_component(state: ModelEpisodeState, action: ModelEpisodeAction) -> int:
    if not state.local_only_required:
        return 10
    if action.answer_mode == "local" or action.provider.lower() in {"local", "ollama"}:
        return 15
    return -50


def _confidence_component(confidence: float) -> int:
    if confidence >= 0.8:
        return 15
    if confidence >= 0.5:
        return 5
    if confidence > 0:
        return -10
    return -5


def _fallback_component(outcome: ModelEpisodeOutcome) -> int:
    if outcome.fallback_reason:
        return -10
    if outcome.status == EpisodeOutcomeStatus.FALLBACK:
        return -5
    return 5


def _latency_component(duration_ms: int) -> int:
    if duration_ms <= 0:
        return 0
    if duration_ms <= 3000:
        return 5
    if duration_ms <= 12000:
        return 0
    return -8


def _reason_codes(
    *,
    state: ModelEpisodeState,
    action: ModelEpisodeAction,
    outcome: ModelEpisodeOutcome,
    components: dict[str, int],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if components["privacy"] < 0:
        reasons.append("local_only_violation")
    if state.citation_required and outcome.citation_count <= 0:
        reasons.append("missing_required_citations")
    if outcome.fallback_reason:
        reasons.append("fallback_used")
    if outcome.status in {EpisodeOutcomeStatus.FAILED, EpisodeOutcomeStatus.BLOCKED}:
        reasons.append(f"outcome_{outcome.status.value}")
    if components["confidence"] > 0:
        reasons.append("confidence_ok")
    if action.retrieval_depth > 0 and outcome.citation_count > 0:
        reasons.append("retrieval_grounded")
    return tuple(reasons[:12])


def _bounded(value: Any, lower: int, upper: int) -> int:
    return max(lower, min(int(value or 0), upper))
