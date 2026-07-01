from src.rag_text_chunking import (
    build_chunk_metadata,
    split_structured_text_into_chunks,
    split_text_into_chunks,
    split_text_into_token_chunks,
)
from src.rag_vector import VectorRAG


def test_split_text_into_chunks_keeps_short_text_unchanged():
    assert split_text_into_chunks("short text", chunk_size=100, overlap=10) == ["short text"]


def test_split_text_into_chunks_respects_sentence_boundaries_with_overlap():
    text = "Alpha one. Beta two. Gamma three. Delta four."

    chunks = split_text_into_chunks(text, chunk_size=24, overlap=12)

    assert chunks == [
        "Alpha one. Beta two.",
        "Beta two. Gamma three.",
        "Gamma three. Delta four.",
    ]


def test_split_text_into_chunks_hard_splits_long_sentence():
    chunks = split_text_into_chunks("x" * 25, chunk_size=10, overlap=2)

    assert chunks == ["x" * 10, "x" * 10, "x" * 9]


def test_vectorrag_split_wrapper_uses_shared_chunking():
    rag = VectorRAG.__new__(VectorRAG)

    assert rag._split_into_chunks("Alpha. Beta.", chunk_size=8, overlap=0) == [
        "Alpha.",
        "Beta.",
    ]


def test_split_text_into_token_chunks_uses_token_budget_adapter():
    text = "Alpha one. Beta two. Gamma three. Delta four."

    chunks = split_text_into_token_chunks(text, max_tokens=8, overlap_tokens=4)

    assert chunks == [
        "Alpha one. Beta two.",
        "Beta two. Gamma three.",
        "Gamma three. Delta four.",
    ]


def test_split_structured_text_into_chunks_keeps_code_fence_atomic():
    text = "\n\n".join(
        [
            "# Plan",
            "Intro paragraph with enough text.",
            "```python\nprint('alpha')\nprint('beta')\n```",
            "Follow-up paragraph.",
        ]
    )

    chunks = split_structured_text_into_chunks(text, chunk_size=70, overlap=0)

    fenced = "```python\nprint('alpha')\nprint('beta')\n```"
    assert any(fenced in chunk for chunk in chunks)
    assert not any("```python" in chunk and "```" not in chunk.removeprefix("```python") for chunk in chunks)


def test_split_structured_text_into_chunks_keeps_markdown_table_rows_together():
    text = "\n\n".join(
        [
            "# Metrics",
            "| Name | Value |\n| --- | ---: |\n| Alpha | 1 |\n| Beta | 2 |",
            "Conclusion paragraph.",
        ]
    )

    chunks = split_structured_text_into_chunks(text, chunk_size=65, overlap=0)

    assert any("| Alpha | 1 |\n| Beta | 2 |" in chunk for chunk in chunks)


def test_split_structured_text_into_chunks_treats_form_feed_as_page_boundary():
    text = "Page one has a complete paragraph.\fPage two has another complete paragraph."

    chunks = split_structured_text_into_chunks(text, chunk_size=45, overlap=0)

    assert chunks == [
        "Page one has a complete paragraph.",
        "Page two has another complete paragraph.",
    ]


def test_build_chunk_metadata_tracks_offsets_hashes_and_overlap():
    text = "Alpha one. Beta two. Gamma three. Delta four."
    chunks = split_text_into_chunks(text, chunk_size=24, overlap=12)

    metadata = [item.to_dict() for item in build_chunk_metadata(text, chunks)]

    assert metadata[0]["splitter_version"] == "rag_structured_v1"
    assert metadata[0]["source_hash"].startswith("sha256:")
    assert metadata[0]["char_start"] == 0
    assert metadata[0]["overlap_from_previous"] == 0
    assert metadata[1]["char_start"] < metadata[0]["char_end"]
    assert metadata[1]["overlap_from_previous"] > 0
    assert metadata[1]["token_end_est"] >= metadata[1]["token_start_est"]
