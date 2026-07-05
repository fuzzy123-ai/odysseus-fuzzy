from src.model_policy_evaluation import ModelPolicyEvaluationCase, run_offline_policy_evaluation
from src.model_reward_contract import ModelEpisodeState
from src.model_routing_policy import ModelRoutingCandidate


def test_offline_policy_evaluation_reports_redacted_aggregate():
    case = ModelPolicyEvaluationCase.create(
        case_id="synthetic-local-wins",
        state=ModelEpisodeState.create(surface="memory.answer", task_type="summary", owner_label="synthetic"),
        candidates=(
            ModelRoutingCandidate.create(candidate_id="cloud", provider="DeepSeek", model="deepseek-chat", answer_mode="cloud"),
            ModelRoutingCandidate.create(candidate_id="local", provider="Ollama", model="gemma4:e4b", answer_mode="local"),
        ),
        reward_history=(
            {"candidate_id": "cloud", "total_score": 10},
            {"candidate_id": "cloud", "total_score": 20},
            {"candidate_id": "local", "total_score": 80},
            {"candidate_id": "local", "total_score": 90},
        ),
        expected_first_candidate_id="local",
    )

    report = run_offline_policy_evaluation([case])

    assert report["schema"] == "odysseus.model_policy_evaluation.v1"
    assert report["pass_rate"] == 1.0
    assert report["raw_prompt_visible"] is False
    assert report["private_content_visible"] is False


def test_offline_policy_evaluation_handles_empty_case_list():
    report = run_offline_policy_evaluation([])

    assert report["case_count"] == 0
    assert report["pass_rate"] == 0.0
