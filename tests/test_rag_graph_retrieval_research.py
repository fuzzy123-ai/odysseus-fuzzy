import pytest

from src.rag_graph_retrieval_research import evaluate_graph_retrieval_research


def test_graph_retrieval_research_can_be_candidate_but_never_runtime_switch():
    decision = evaluate_graph_retrieval_research(
        baseline_recall=0.70,
        graph_recall=0.78,
        context_inflation=1.1,
    )

    assert decision.decision == "research_candidate"
    assert decision.recall_gain == pytest.approx(0.08)
    assert decision.runtime_switch_allowed is False


def test_graph_retrieval_research_blocks_low_gain_or_context_inflation():
    low_gain = evaluate_graph_retrieval_research(
        baseline_recall=0.70,
        graph_recall=0.72,
        context_inflation=1.0,
    )
    inflated = evaluate_graph_retrieval_research(
        baseline_recall=0.70,
        graph_recall=0.80,
        context_inflation=1.4,
    )

    assert low_gain.decision == "no_go_recall_gain_too_low"
    assert inflated.decision == "no_go_context_inflation_too_high"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"baseline_recall": -0.1, "graph_recall": 0.7, "context_inflation": 1.0},
        {"baseline_recall": 0.1, "graph_recall": 1.2, "context_inflation": 1.0},
        {"baseline_recall": 0.1, "graph_recall": 0.2, "context_inflation": 0},
    ],
)
def test_graph_retrieval_research_validates_inputs(kwargs):
    with pytest.raises(ValueError):
        evaluate_graph_retrieval_research(**kwargs)
