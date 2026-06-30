from src.rag_text_chunking import split_text_into_chunks
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

    assert chunks == ["x" * 10, "x" * 10, "x" * 9, "x"]


def test_vectorrag_split_wrapper_uses_shared_chunking():
    rag = VectorRAG.__new__(VectorRAG)

    assert rag._split_into_chunks("Alpha. Beta.", chunk_size=8, overlap=0) == [
        "Alpha.",
        "Beta.",
    ]
