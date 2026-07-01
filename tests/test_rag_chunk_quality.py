import json

from src.rag_chunk_quality import assess_chunk_quality
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
