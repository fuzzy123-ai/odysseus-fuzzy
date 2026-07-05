import json

import pytest

from src import model_episode_store
from src.model_episode_store import append_model_episode, read_model_episodes
from src.model_reward_contract import (
    ModelEpisode,
    ModelEpisodeAction,
    ModelEpisodeOutcome,
    ModelEpisodeState,
)
from src.model_reward_scorer import score_episode


def _scored_episode():
    episode = ModelEpisode.create(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="evidence_summary",
            owner_label="owner:test",
            retrieval_doc_count=2,
            citation_required=True,
        ),
        action=ModelEpisodeAction.create(
            answer_mode="local",
            provider="Ollama",
            model="gemma4:e4b",
            endpoint_ref="endpoint:local",
            prompt_template_id="memory-answer-v1",
            retrieval_depth=2,
            max_tokens=300,
        ),
        outcome=ModelEpisodeOutcome.create(status="success", citation_count=2, confidence=0.9, duration_ms=800),
    )
    return score_episode(episode)


def test_model_episode_store_appends_and_reads_redacted_records(tmp_path, monkeypatch):
    monkeypatch.setattr(model_episode_store, "MODEL_EPISODE_DIR", str(tmp_path))

    written = append_model_episode(_scored_episode(), day="2026-07-05")
    result = read_model_episodes(day="2026-07-05", owner="owner:test", model="gemma4:e4b")

    assert written["raw_prompt_visible"] is False
    assert result["schema"] == "odysseus.model_episode_store.v1"
    assert result["count"] == 1
    assert result["records"][0]["surface"] == "memory.answer"
    assert result["records"][0]["total_score"] > 0
    assert "prompt" not in result["records"][0]


def test_model_episode_store_rejects_raw_prompt_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(model_episode_store, "MODEL_EPISODE_DIR", str(tmp_path))
    record = _scored_episode().to_record()
    record["messages"] = [{"role": "user", "content": "private raw text"}]

    with pytest.raises(model_episode_store.ModelEpisodeStoreError):
        append_model_episode(record, day="2026-07-05")


def test_model_episode_store_skips_corrupt_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(model_episode_store, "MODEL_EPISODE_DIR", str(tmp_path))
    path = model_episode_store.episode_store_path("2026-07-05")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json\n" + json.dumps(_scored_episode().to_record()) + "\n", encoding="utf-8")

    result = read_model_episodes(day="2026-07-05")

    assert result["count"] == 1
    assert result["skipped"] == 1
