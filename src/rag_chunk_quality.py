"""Offline quality gates for RAG chunking."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable

from src.token_budget import count_text_tokens


@dataclass(frozen=True, slots=True)
class ChunkQualityReport:
    chunk_count: int
    duplicate_contained_count: int
    unclosed_code_fence_count: int
    budget_overflow_count: int
    max_budget_units_observed: int
    average_budget_units: float

    @property
    def duplicate_tail_rate(self) -> float:
        if self.chunk_count <= 0:
            return 0.0
        return self.duplicate_contained_count / self.chunk_count

    @property
    def boundary_coherence(self) -> float:
        if self.chunk_count <= 0:
            return 1.0
        return max(0.0, 1.0 - (self.unclosed_code_fence_count / self.chunk_count))

    @property
    def passed(self) -> bool:
        return (
            self.duplicate_contained_count == 0
            and self.unclosed_code_fence_count == 0
            and self.budget_overflow_count == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_count": self.chunk_count,
            "duplicate_contained_count": self.duplicate_contained_count,
            "duplicate_tail_rate": self.duplicate_tail_rate,
            "unclosed_code_fence_count": self.unclosed_code_fence_count,
            "boundary_coherence": self.boundary_coherence,
            "budget_overflow_count": self.budget_overflow_count,
            "max_budget_units_observed": self.max_budget_units_observed,
            "average_budget_units": self.average_budget_units,
            "passed": self.passed,
            "private_content_visible": False,
        }


def assess_chunk_quality(
    chunks: Iterable[str],
    *,
    max_budget_units: int | None = None,
) -> ChunkQualityReport:
    """Return metadata-only quality metrics for a chunk set."""

    chunk_list = [str(chunk or "") for chunk in chunks if str(chunk or "")]
    budgets = [count_text_tokens(chunk) for chunk in chunk_list]
    return ChunkQualityReport(
        chunk_count=len(chunk_list),
        duplicate_contained_count=_contained_duplicate_count(chunk_list),
        unclosed_code_fence_count=sum(1 for chunk in chunk_list if _has_unclosed_fence(chunk)),
        budget_overflow_count=sum(1 for units in budgets if max_budget_units is not None and units > max_budget_units),
        max_budget_units_observed=max(budgets, default=0),
        average_budget_units=mean(budgets) if budgets else 0.0,
    )


def _contained_duplicate_count(chunks: list[str]) -> int:
    count = 0
    for index, chunk in enumerate(chunks):
        if any(index != other_index and chunk in other for other_index, other in enumerate(chunks)):
            count += 1
    return count


def _has_unclosed_fence(chunk: str) -> bool:
    return chunk.count("```") % 2 == 1 or chunk.count("~~~") % 2 == 1
