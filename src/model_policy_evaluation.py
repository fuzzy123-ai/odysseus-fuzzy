"""Offline evaluation helpers for RL-lite model routing policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.model_reward_contract import ModelEpisodeState
from src.model_routing_policy import ModelRoutingCandidate, recommend_model_routing


MODEL_POLICY_EVALUATION_SCHEMA = "odysseus.model_policy_evaluation.v1"


@dataclass(frozen=True, slots=True)
class ModelPolicyEvaluationCase:
    case_id: str
    state: ModelEpisodeState
    candidates: tuple[ModelRoutingCandidate, ...]
    reward_history: tuple[Mapping[str, Any], ...]
    expected_first_candidate_id: str

    @classmethod
    def create(cls, **kwargs: Any) -> "ModelPolicyEvaluationCase":
        candidates = tuple(kwargs.get("candidates") or ())
        if not candidates:
            raise ValueError("candidates must not be empty")
        return cls(
            case_id=_label(kwargs.get("case_id") or "case"),
            state=kwargs["state"],
            candidates=candidates,
            reward_history=tuple(dict(item) for item in kwargs.get("reward_history") or ()),
            expected_first_candidate_id=_label(kwargs.get("expected_first_candidate_id") or candidates[0].candidate_id),
        )


def run_offline_policy_evaluation(cases: Iterable[ModelPolicyEvaluationCase]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        decision = recommend_model_routing(
            state=case.state,
            candidates=case.candidates,
            reward_history=case.reward_history,
        )
        first = decision.ordered_candidate_ids[0] if decision.ordered_candidate_ids else ""
        rows.append(
            {
                "case_id": case.case_id,
                "passed": first == case.expected_first_candidate_id,
                "expected_first_candidate_id": case.expected_first_candidate_id,
                "actual_first_candidate_id": first,
                "reason_codes": decision.reason_codes,
            }
        )
    passed = sum(1 for row in rows if row["passed"])
    total = len(rows)
    return {
        "schema": MODEL_POLICY_EVALUATION_SCHEMA,
        "case_count": total,
        "passed_count": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "cases": rows,
        "raw_prompt_visible": False,
        "raw_output_visible": False,
        "private_content_visible": False,
    }


def _label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("label must not be empty")
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "token", "secret", "password")):
        raise ValueError("label contains forbidden secret marker")
    return text[:120]
