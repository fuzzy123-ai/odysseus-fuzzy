from src.model_reward_contract import ModelEpisodeState
from src.model_routing_policy import (
    ModelRoutingCandidate,
    explain_policy_decision,
    recommend_model_routing,
)


def _candidates():
    return [
        ModelRoutingCandidate.create(candidate_id="cloud", provider="DeepSeek", model="deepseek-chat", answer_mode="cloud"),
        ModelRoutingCandidate.create(candidate_id="local", provider="Ollama", model="gemma4:e4b", answer_mode="local"),
    ]


def test_policy_preserves_order_on_cold_start():
    decision = recommend_model_routing(
        state=ModelEpisodeState.create(surface="memory.answer", task_type="summary", owner_label="owner:test"),
        candidates=_candidates(),
        reward_history=[],
    )

    assert decision.ordered_candidate_ids == ("cloud", "local")
    assert "cold_start_preserves_order" in decision.reason_codes
    assert decision.shadow_only is True


def test_policy_reorders_when_reward_history_is_sufficient():
    decision = recommend_model_routing(
        state=ModelEpisodeState.create(surface="memory.answer", task_type="summary", owner_label="owner:test"),
        candidates=_candidates(),
        reward_history=[
            {"candidate_id": "cloud", "total_score": 10},
            {"candidate_id": "cloud", "total_score": 20},
            {"candidate_id": "local", "total_score": 80},
            {"candidate_id": "local", "total_score": 90},
        ],
    )

    assert decision.ordered_candidate_ids == ("local", "cloud")
    assert "reward_history_reorder" in decision.reason_codes


def test_policy_privacy_hard_gate_blocks_cloud_for_local_only():
    decision = recommend_model_routing(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="sensitive_summary",
            owner_label="owner:test",
            local_only_required=True,
        ),
        candidates=_candidates(),
        reward_history=[{"candidate_id": "cloud", "total_score": 100}, {"candidate_id": "cloud", "total_score": 100}],
    )

    assert decision.ordered_candidate_ids[-1] == "cloud"
    assert decision.blocked_candidate_ids == ("cloud",)
    assert "privacy_hard_gate" in decision.reason_codes


def test_policy_audit_summary_is_redacted():
    decision = recommend_model_routing(
        state=ModelEpisodeState.create(surface="memory.answer", task_type="summary", owner_label="owner:test"),
        candidates=_candidates(),
    )
    summary = explain_policy_decision(decision)

    assert summary["raw_episode_visible"] is False
    assert summary["raw_prompt_visible"] is False
    assert summary["raw_output_visible"] is False
    assert "ordered_candidate_ids" in summary
