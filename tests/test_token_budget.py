import pytest

from src.token_budget import count_text_tokens, split_budget


def test_count_text_tokens_uses_shared_estimator_without_message_overhead():
    assert count_text_tokens("") == 0
    assert count_text_tokens("abcdefghij") == 3


def test_split_budget_exposes_estimated_char_windows():
    budget = split_budget(30, 6, model_hint="local-gemma")

    assert budget.max_tokens == 30
    assert budget.overlap_tokens == 6
    assert budget.model_hint == "local-gemma"
    assert budget.max_chars_estimate == 100
    assert budget.overlap_chars_estimate == 20
    assert budget.to_dict()["estimator"] == "model_context_chars_x_0_3"


@pytest.mark.parametrize(
    ("max_tokens", "overlap_tokens"),
    [(0, 0), (10, -1), (10, 10), (10, 11)],
)
def test_split_budget_rejects_invalid_windows(max_tokens, overlap_tokens):
    with pytest.raises(ValueError):
        split_budget(max_tokens, overlap_tokens)
