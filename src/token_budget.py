"""Shared token budget helpers for RAG, memory, and context planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.token_estimator import (
    LEGACY_ADAPTER_ID,
    estimate_character_capacity,
    estimate_text_tokens,
    estimator_for_model,
)

ESTIMATOR_NAME = LEGACY_ADAPTER_ID
CHARS_PER_TOKEN_ESTIMATE = 1 / 0.3


@dataclass(frozen=True, slots=True)
class TokenBudget:
    max_tokens: int
    overlap_tokens: int = 0
    model_hint: str | None = None

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must not be negative")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")

    @property
    def max_chars_estimate(self) -> int:
        return estimate_character_capacity(self.max_tokens, self.model_hint)

    @property
    def overlap_chars_estimate(self) -> int:
        if self.overlap_tokens == 0:
            return 0
        return estimate_character_capacity(self.overlap_tokens, self.model_hint)

    @property
    def estimator(self) -> str:
        return estimator_for_model(self.model_hint).adapter_id

    @property
    def estimator_revision(self) -> str:
        return estimator_for_model(self.model_hint).adapter_revision

    @property
    def estimator_mode(self) -> str:
        return "exact" if estimator_for_model(self.model_hint).exact else "fallback"

    @property
    def estimator_confidence(self) -> str:
        return estimator_for_model(self.model_hint).confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "model_hint": self.model_hint,
            "estimator": self.estimator,
            "estimator_revision": self.estimator_revision,
            "estimator_mode": self.estimator_mode,
            "estimator_confidence": self.estimator_confidence,
            "max_chars_estimate": self.max_chars_estimate,
            "overlap_chars_estimate": self.overlap_chars_estimate,
        }


def count_text_tokens(text: str, model_hint: str | None = None) -> int:
    """Return a deterministic plain-text count for the selected model route.

    Calls without a model hint retain the historical ``chars * 0.3`` result.
    A non-empty hint selects either the explicit exact reference scheme or a
    disclosed conservative offline upper-bound fallback.  No provider accuracy
    is claimed without immutable local tokenizer assets.
    """

    return estimate_text_tokens(str(text or ""), model_hint=model_hint).count


def split_budget(
    max_tokens: int,
    overlap_tokens: int = 0,
    *,
    model_hint: str | None = None,
) -> TokenBudget:
    return TokenBudget(max_tokens=max_tokens, overlap_tokens=overlap_tokens, model_hint=model_hint)
