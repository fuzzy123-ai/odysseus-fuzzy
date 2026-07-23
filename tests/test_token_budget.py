import pytest

from src.token_budget import TokenBudget, count_text_tokens, split_budget


def test_count_text_tokens_uses_shared_estimator_without_message_overhead():
    assert count_text_tokens("") == 0
    assert count_text_tokens("abcdefghij") == 3


def test_split_budget_exposes_estimated_char_windows():
    budget = split_budget(30, 6, model_hint="local-gemma")

    assert budget.max_tokens == 30
    assert budget.overlap_tokens == 6
    assert budget.model_hint == "local-gemma"
    assert budget.max_chars_estimate == 30
    assert budget.overlap_chars_estimate == 6
    assert budget.to_dict()["estimator"] == "sentencepiece_family_conservative_utf8_upper_bound"
    assert budget.to_dict()["estimator_mode"] == "fallback"
    assert budget.to_dict()["estimator_confidence"] == "conservative_upper_bound"


def test_direct_token_budget_construction_derives_matching_provenance():
    direct = TokenBudget(30, 6, model_hint="local-gemma")
    factory = split_budget(30, 6, model_hint="local-gemma")

    assert direct.to_dict() == factory.to_dict()


@pytest.mark.parametrize(
    ("max_tokens", "overlap_tokens"),
    [(0, 0), (10, -1), (10, 10), (10, 11)],
)
def test_split_budget_rejects_invalid_windows(max_tokens, overlap_tokens):
    with pytest.raises(ValueError):
        split_budget(max_tokens, overlap_tokens)
