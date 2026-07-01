"""Shared token budget helpers for RAG, memory, and context planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ESTIMATOR_NAME = "model_context_chars_x_0_3"
CHARS_PER_TOKEN_ESTIMATE = 1 / 0.3


@dataclass(frozen=True, slots=True)
class TokenBudget:
    max_tokens: int
    overlap_tokens: int = 0
    model_hint: str | None = None
    estimator: str = ESTIMATOR_NAME

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must not be negative")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")

    @property
    def max_chars_estimate(self) -> int:
        return max(1, int(self.max_tokens * CHARS_PER_TOKEN_ESTIMATE))

    @property
    def overlap_chars_estimate(self) -> int:
        return max(0, int(self.overlap_tokens * CHARS_PER_TOKEN_ESTIMATE))

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "model_hint": self.model_hint,
            "estimator": self.estimator,
            "max_chars_estimate": self.max_chars_estimate,
            "overlap_chars_estimate": self.overlap_chars_estimate,
        }


def count_text_tokens(text: str, model_hint: str | None = None) -> int:
    """Return a deterministic token estimate for plain text.

    This intentionally reuses ``model_context.estimate_tokens`` as the fallback
    estimator, minus chat-message overhead. A future model-specific tokenizer can
    be plugged in behind this API without changing RAG call sites.
    """

    del model_hint  # Reserved for future tokenizer selection.
    if not text:
        return 0
    from src.model_context import estimate_tokens

    estimated = estimate_tokens([{"role": "user", "content": str(text)}]) - 4
    return max(1, estimated)


def split_budget(
    max_tokens: int,
    overlap_tokens: int = 0,
    *,
    model_hint: str | None = None,
) -> TokenBudget:
    return TokenBudget(max_tokens=max_tokens, overlap_tokens=overlap_tokens, model_hint=model_hint)
