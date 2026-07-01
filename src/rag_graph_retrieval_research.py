"""Offline decision helpers for graph/Brownian RAG retrieval experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphRetrievalResearchDecision:
    baseline_recall: float
    graph_recall: float
    context_inflation: float
    min_recall_gain: float = 0.05
    max_context_inflation: float = 1.25

    @property
    def recall_gain(self) -> float:
        return self.graph_recall - self.baseline_recall

    @property
    def decision(self) -> str:
        if self.recall_gain < self.min_recall_gain:
            return "no_go_recall_gain_too_low"
        if self.context_inflation > self.max_context_inflation:
            return "no_go_context_inflation_too_high"
        return "research_candidate"

    @property
    def runtime_switch_allowed(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_recall": self.baseline_recall,
            "graph_recall": self.graph_recall,
            "recall_gain": self.recall_gain,
            "context_inflation": self.context_inflation,
            "min_recall_gain": self.min_recall_gain,
            "max_context_inflation": self.max_context_inflation,
            "decision": self.decision,
            "runtime_switch_allowed": self.runtime_switch_allowed,
        }


def evaluate_graph_retrieval_research(
    *,
    baseline_recall: float,
    graph_recall: float,
    context_inflation: float,
    min_recall_gain: float = 0.05,
    max_context_inflation: float = 1.25,
) -> GraphRetrievalResearchDecision:
    return GraphRetrievalResearchDecision(
        baseline_recall=_ratio(baseline_recall, field="baseline_recall"),
        graph_recall=_ratio(graph_recall, field="graph_recall"),
        context_inflation=_positive(context_inflation, field="context_inflation"),
        min_recall_gain=_positive(min_recall_gain, field="min_recall_gain"),
        max_context_inflation=_positive(max_context_inflation, field="max_context_inflation"),
    )


def _ratio(value: float, *, field: str) -> float:
    number = float(value)
    if number < 0 or number > 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return number


def _positive(value: float, *, field: str) -> float:
    number = float(value)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number
