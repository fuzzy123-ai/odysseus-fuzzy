"""Run the offline RL-lite model policy evaluation demo."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.model_policy_evaluation import ModelPolicyEvaluationCase, run_offline_policy_evaluation
from src.model_reward_contract import ModelEpisodeState
from src.model_routing_policy import ModelRoutingCandidate


def main() -> int:
    cases = [
        ModelPolicyEvaluationCase.create(
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
    ]
    print(json.dumps(run_offline_policy_evaluation(cases), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
