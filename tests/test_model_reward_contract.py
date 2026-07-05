import pytest

from src.model_reward_contract import (
    EpisodeOutcomeStatus,
    ModelEpisode,
    ModelEpisodeAction,
    ModelEpisodeOutcome,
    ModelEpisodeState,
    ModelReward,
    ModelRewardContractError,
    RewardStatus,
)


def _episode():
    state = ModelEpisodeState.create(
        surface="memory.answer",
        task_type="evidence_summary",
        owner_label="owner:test",
        sensitivity_flags=["dsgvo", "citation_required"],
        retrieval_doc_count=3,
        citation_required=True,
        local_only_required=False,
        context_budget_tokens=8192,
    )
    action = ModelEpisodeAction.create(
        answer_mode="cloud",
        provider="DeepSeek",
        model="deepseek-chat",
        endpoint_ref="endpoint:deepseek",
        prompt_template_id="memory-answer-v1",
        retrieval_depth=3,
        max_tokens=500,
    )
    outcome = ModelEpisodeOutcome.create(
        status="success",
        duration_ms=1234,
        citation_count=2,
        warning_codes=["model_context_below_recommended_16k"],
        confidence=0.82,
        verifier_refs=["gate:citations"],
    )
    reward = ModelReward.create(
        total_score=42,
        component_scores={"citation": 20, "latency": 4},
        status="positive",
        reason_codes=["grounded_answer"],
    )
    return ModelEpisode.create(state=state, action=action, outcome=outcome, reward=reward)


def test_model_reward_contract_builds_redacted_episode_record():
    episode = _episode()

    record = episode.to_record()
    summary = episode.audit_summary()

    assert record["schema"] == "odysseus.model_reward_contract.v1"
    assert record["raw_prompt_visible"] is False
    assert record["raw_output_visible"] is False
    assert record["private_content_visible"] is False
    assert record["outcome"]["status"] == EpisodeOutcomeStatus.SUCCESS.value
    assert record["reward"]["status"] == RewardStatus.POSITIVE.value
    assert "prompt" not in summary
    assert summary["total_score"] == 42


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_label", "C:/Users/private/Documents"),
        ("surface", "memory.answer token sk-secret"),
        ("task_type", "line\nbreak"),
    ],
)
def test_state_rejects_unsafe_labels(field, value):
    kwargs = {
        "surface": "memory.answer",
        "task_type": "summary",
        "owner_label": "owner:test",
    }
    kwargs[field] = value

    with pytest.raises(ModelRewardContractError):
        ModelEpisodeState.create(**kwargs)


def test_action_rejects_secret_markers():
    with pytest.raises(ModelRewardContractError):
        ModelEpisodeAction.create(
            answer_mode="cloud",
            provider="DeepSeek",
            model="deepseek-chat",
            endpoint_ref="Bearer sk-secret",
            prompt_template_id="memory-answer-v1",
        )


def test_reward_score_is_bounded():
    with pytest.raises(ModelRewardContractError):
        ModelReward.create(total_score=101, component_scores={}, status="positive")
