from src.model_reward_contract import (
    ModelEpisode,
    ModelEpisodeAction,
    ModelEpisodeOutcome,
    ModelEpisodeState,
    RewardStatus,
)
from src.model_reward_scorer import score_episode, score_model_episode


def test_reward_scorer_rewards_grounded_success():
    reward = score_model_episode(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="evidence_summary",
            owner_label="owner:test",
            retrieval_doc_count=3,
            citation_required=True,
        ),
        action=ModelEpisodeAction.create(
            answer_mode="cloud",
            provider="DeepSeek",
            model="deepseek-chat",
            endpoint_ref="endpoint:deepseek",
            prompt_template_id="memory-answer-v1",
            retrieval_depth=3,
            max_tokens=500,
        ),
        outcome=ModelEpisodeOutcome.create(status="success", citation_count=3, confidence=0.9, duration_ms=1000),
    )

    assert reward.total_score > 50
    assert reward.status == RewardStatus.POSITIVE
    assert "retrieval_grounded" in reward.reason_codes


def test_reward_scorer_penalizes_missing_required_citations():
    reward = score_model_episode(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="evidence_summary",
            owner_label="owner:test",
            citation_required=True,
        ),
        action=ModelEpisodeAction.create(
            answer_mode="cloud",
            provider="DeepSeek",
            model="deepseek-chat",
            endpoint_ref="endpoint:deepseek",
            prompt_template_id="memory-answer-v1",
        ),
        outcome=ModelEpisodeOutcome.create(status="success", citation_count=0, confidence=0.7, duration_ms=1000),
    )

    assert reward.total_score < 30
    assert "missing_required_citations" in reward.reason_codes


def test_reward_scorer_blocks_cloud_when_local_only_required():
    reward = score_model_episode(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="sensitive_summary",
            owner_label="owner:test",
            local_only_required=True,
        ),
        action=ModelEpisodeAction.create(
            answer_mode="cloud",
            provider="DeepSeek",
            model="deepseek-chat",
            endpoint_ref="endpoint:deepseek",
            prompt_template_id="memory-answer-v1",
        ),
        outcome=ModelEpisodeOutcome.create(status="success", confidence=0.9),
    )

    assert reward.status == RewardStatus.BLOCKED
    assert "local_only_violation" in reward.reason_codes


def test_score_episode_returns_copy_with_reward():
    episode = ModelEpisode.create(
        state=ModelEpisodeState.create(surface="memory.answer", task_type="summary", owner_label="owner:test"),
        action=ModelEpisodeAction.create(
            answer_mode="local",
            provider="Ollama",
            model="gemma4:e4b",
            endpoint_ref="endpoint:local",
            prompt_template_id="memory-answer-v1",
        ),
        outcome=ModelEpisodeOutcome.create(status="success", confidence=0.8),
    )

    scored = score_episode(episode)

    assert scored.reward is not None
    assert scored.reward.total_score > 0
