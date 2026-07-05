import pytest

from src.model_preference_export import ModelPreferenceExportError, build_redacted_preference_pair
from src.model_reward_contract import (
    ModelEpisode,
    ModelEpisodeAction,
    ModelEpisodeOutcome,
    ModelEpisodeState,
)
from src.model_reward_scorer import score_episode


def _episode(model: str, citation_count: int, confidence: float):
    episode = ModelEpisode.create(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="evidence_summary",
            owner_label="synthetic",
            retrieval_doc_count=2,
            citation_required=True,
        ),
        action=ModelEpisodeAction.create(
            answer_mode="local",
            provider="Ollama",
            model=model,
            endpoint_ref=f"endpoint:{model}",
            prompt_template_id="memory-answer-v1",
            retrieval_depth=2,
            max_tokens=300,
        ),
        outcome=ModelEpisodeOutcome.create(status="success", citation_count=citation_count, confidence=confidence),
    )
    return score_episode(episode)


def test_preference_export_builds_redacted_pair():
    pair = build_redacted_preference_pair(
        pair_id="pair-1",
        winner=_episode("gemma4:e4b", 2, 0.9),
        loser=_episode("tiny", 0, 0.2),
    )

    record = pair.to_record()
    assert record["schema"] == "odysseus.model_preference_export.v1"
    assert record["winner_score"] > record["loser_score"]
    assert record["raw_prompt_visible"] is False
    assert record["raw_output_visible"] is False


def test_preference_export_blocks_real_unredacted_export():
    with pytest.raises(ModelPreferenceExportError):
        build_redacted_preference_pair(
            pair_id="pair-1",
            winner=_episode("gemma4:e4b", 2, 0.9),
            loser=_episode("tiny", 0, 0.2),
            synthetic_or_redacted=False,
        )
