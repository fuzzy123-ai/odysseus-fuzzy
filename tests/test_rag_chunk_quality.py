import json

import pytest

from src.rag_chunk_quality import assess_chunk_quality, assess_synthetic_recall
from src.rag_text_chunking import split_structured_text_into_chunks


def test_chunk_quality_passes_for_structured_chunks_without_private_content():
    text = "\n\n".join(
        [
            "# Notes",
            "Alpha project planning paragraph.",
            "```python\nprint('ok')\n```",
            "| Name | Value |\n| --- | ---: |\n| A | 1 |",
        ]
    )
    chunks = split_structured_text_into_chunks(text, chunk_size=80, overlap=0)

    report = assess_chunk_quality(chunks, max_budget_units=30).to_dict()
    encoded = json.dumps(report, sort_keys=True)

    assert report["passed"] is True
    assert report["duplicate_tail_rate"] == 0.0
    assert report["boundary_coherence"] == 1.0
    assert report["private_content_visible"] is False
    assert "Alpha project" not in encoded


def test_chunk_quality_flags_duplicate_tails_unclosed_fences_and_budget_overflow():
    report = assess_chunk_quality(
        [
            "full chunk with duplicated tail",
            "duplicated tail",
            "```python\nprint('open')",
            "x" * 100,
        ],
        max_budget_units=5,
    )

    assert report.passed is False
    assert report.duplicate_contained_count == 1
    assert report.unclosed_code_fence_count == 1
    assert report.budget_overflow_count >= 1


def test_synthetic_recall_gate_reports_counts_without_bodies():
    report = assess_synthetic_recall(
        ["alpha invoice details", "beta worksheet notes"],
        ["invoice", "worksheet", "missing"],
        min_recall=0.6,
    ).to_dict()

    assert report["query_count"] == 3
    assert report["hit_count"] == 2
    assert report["recall"] == pytest.approx(2 / 3)
    assert report["passed"] is True
    assert report["private_content_visible"] is False


def test_synthetic_recall_gate_blocks_low_recall():
    report = assess_synthetic_recall(["alpha only"], ["alpha", "beta"], min_recall=0.8)

    assert report.passed is False
