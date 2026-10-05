import pytest

from src.rag_text_chunking import (
    EXACT_CHARACTER_FEED_VERSION,
    EXACT_CHARACTER_SPLITTER_VERSION,
    MAX_EXACT_CHARACTER_FEED_CHARS,
    MAX_EXACT_CHARACTER_FEED_CHUNKS,
    MAX_EXACT_CHARACTER_FEED_FRAGMENT_CHARS,
    MAX_EXACT_CHARACTER_RANGES,
    ExactCharacterRange,
    ExactCharacterFeedState,
    build_chunk_metadata,
    feed_exact_character_text,
    new_exact_character_feed_state,
    split_text_into_exact_character_ranges,
    split_structured_text_into_chunks,
    split_text_into_chunks,
    split_text_into_token_chunks,
)
from src.rag_vector import VectorRAG
from src.token_budget import count_text_tokens


def test_split_text_into_exact_character_ranges_preserves_canonical_whitespace_payload():
    text = "Alpha.  Beta!  " * 50_000 + " " * 79

    ranges = split_text_into_exact_character_ranges(text, max_chars=262_144)

    assert EXACT_CHARACTER_SPLITTER_VERSION == "rag_exact_character_ranges_v1"
    assert MAX_EXACT_CHARACTER_RANGES == 10_000
    assert len(text) == 750_079
    assert len(ranges) == 3
    assert "".join(text[item.char_start : item.char_end] for item in ranges) == text


def test_split_text_into_exact_character_ranges_covers_unicode_and_repeated_text():
    text = "  café Ω  " * 3

    ranges = split_text_into_exact_character_ranges(text, max_chars=4)

    assert ranges == (
        ExactCharacterRange(0, 4),
        ExactCharacterRange(4, 8),
        ExactCharacterRange(8, 12),
        ExactCharacterRange(12, 16),
        ExactCharacterRange(16, 20),
        ExactCharacterRange(20, 24),
        ExactCharacterRange(24, 28),
        ExactCharacterRange(28, 30),
    )
    assert [(item.char_start, item.char_end) for item in ranges] == [
        (0, 4),
        (4, 8),
        (8, 12),
        (12, 16),
        (16, 20),
        (20, 24),
        (24, 28),
        (28, 30),
    ]
    assert all(type(item.char_start) is int and type(item.char_end) is int for item in ranges)


def test_split_text_into_exact_character_ranges_rejects_integer_subclasses():
    class IntegerSubclass(int):
        pass

    for max_chars, max_ranges in ((IntegerSubclass(1), 1), (1, IntegerSubclass(1))):
        with pytest.raises(TypeError):
            split_text_into_exact_character_ranges("text", max_chars=max_chars, max_ranges=max_ranges)


def test_split_text_into_exact_character_ranges_handles_empty_and_boundaries_deterministically():
    assert split_text_into_exact_character_ranges("", max_chars=1) == ()
    assert split_text_into_exact_character_ranges("abcd", max_chars=4) == (ExactCharacterRange(0, 4),)
    assert split_text_into_exact_character_ranges("abcde", max_chars=4) == (
        ExactCharacterRange(0, 4),
        ExactCharacterRange(4, 5),
    )


@pytest.mark.parametrize(
    ("text", "max_chars", "max_ranges"),
    [
        ("text", True, 1),
        ("text", 1, True),
        ("text", 0, 1),
        ("text", -1, 1),
        ("text", 2**63, 1),
        ("text", 1, 0),
        ("text", 1, 10_001),
        ("", 0, 1),
        ("", 1, 0),
    ],
)
def test_split_text_into_exact_character_ranges_rejects_invalid_limits(text, max_chars, max_ranges):
    with pytest.raises((TypeError, ValueError)):
        split_text_into_exact_character_ranges(text, max_chars=max_chars, max_ranges=max_ranges)


def test_split_text_into_exact_character_ranges_requires_exact_builtin_string():
    class TextSubclass(str):
        pass

    for text in (b"text", TextSubclass("text")):
        with pytest.raises(TypeError):
            split_text_into_exact_character_ranges(text, max_chars=1)


def test_split_text_into_exact_character_ranges_rejects_cap_before_constructing_ranges(monkeypatch):
    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("range construction must not start before the cap check")

    monkeypatch.setattr("src.rag_text_chunking.ExactCharacterRange", fail_if_constructed)

    with pytest.raises(ValueError, match="^exact character range limit exceeded$"):
        split_text_into_exact_character_ranges("abc", max_chars=1, max_ranges=2)


@pytest.mark.parametrize(
    "start,end",
    [(True, 1), (0, True), (-1, 1), (1, 1), (2, 1)],
)
def test_exact_character_range_rejects_invalid_direct_construction(start, end):
    with pytest.raises((TypeError, ValueError)):
        ExactCharacterRange(start, end)


def test_exact_character_feed_preserves_offsets_whitespace_crlf_and_repeated_lines():
    fragments = ("  line\r", "\nrepeat\nrepeat  ", " caféΩ")
    state = new_exact_character_feed_state(max_chars=7)
    offset = 0
    chunks = []
    for fragment in fragments:
        emitted, state = feed_exact_character_text(state, fragment, offset=offset)
        chunks.extend(emitted)
        offset += len(fragment)
    emitted, state = feed_exact_character_text(state, "", offset=offset, final=True)
    chunks.extend(emitted)

    assert EXACT_CHARACTER_FEED_VERSION == "rag_exact_character_feed_v1"
    assert "".join(chunks) == "".join(fragments)
    assert state.next_char_offset == offset
    assert state.next_chunk_index == len(chunks)
    assert state.carry == ""
    assert state.terminal is True


def test_exact_character_feed_final_flushes_short_tail_and_empty_input_once():
    state = new_exact_character_feed_state(max_chars=8)
    emitted, state = feed_exact_character_text(state, "tail", offset=0, final=True)
    assert emitted == ("tail",)
    assert state.next_chunk_index == 1
    assert state.terminal is True
    with pytest.raises(ValueError, match="already terminal"):
        feed_exact_character_text(state, "", offset=4, final=True)

    empty = new_exact_character_feed_state(max_chars=8)
    emitted, empty = feed_exact_character_text(empty, "", offset=0, final=True)
    assert emitted == ()
    assert empty.terminal is True


def test_exact_character_feed_rejects_bad_offsets_fragment_types_and_hostile_states():
    state = new_exact_character_feed_state(max_chars=4)
    with pytest.raises(ValueError, match="not contiguous"):
        feed_exact_character_text(state, "x", offset=1)
    with pytest.raises(TypeError, match="built-in str"):
        feed_exact_character_text(state, b"x", offset=0)
    with pytest.raises(ValueError, match="fragment exceeds"):
        feed_exact_character_text(
            state,
            "x" * (MAX_EXACT_CHARACTER_FEED_FRAGMENT_CHARS + 1),
            offset=0,
        )
    with pytest.raises(ValueError, match="not valid UTF-8"):
        feed_exact_character_text(state, "\ud800", offset=0)

    with pytest.raises(ValueError, match="invalid exact character feed state"):
        ExactCharacterFeedState(
            EXACT_CHARACTER_FEED_VERSION,
            4,
            10**100,
            0,
            "",
            False,
            "0" * 64,
        )

    class FeedStateSubclass(ExactCharacterFeedState):
        pass

    with pytest.raises(TypeError, match="exact ExactCharacterFeedState"):
        FeedStateSubclass(
            state.version,
            state.max_chars,
            state.next_char_offset,
            state.next_chunk_index,
            state.carry,
            state.terminal,
            state._seal,
        )

    forged = object.__new__(ExactCharacterFeedState)
    with pytest.raises(ValueError, match="invalid exact character feed state"):
        feed_exact_character_text(forged, "x", offset=0)

    tampered = new_exact_character_feed_state(max_chars=4)
    object.__setattr__(tampered, "next_char_offset", 1)
    with pytest.raises(ValueError, match="invalid exact character feed state"):
        feed_exact_character_text(tampered, "x", offset=1)

    tampered_text = new_exact_character_feed_state(max_chars=4)
    object.__setattr__(tampered_text, "carry", "\ud800")
    with pytest.raises(ValueError, match="invalid exact character feed state"):
        feed_exact_character_text(tampered_text, "x", offset=0)

    with pytest.raises(ValueError, match="output exceeds"):
        feed_exact_character_text(
            new_exact_character_feed_state(max_chars=1),
            "x" * (MAX_EXACT_CHARACTER_FEED_CHUNKS + 1),
            offset=0,
        )


def test_exact_character_feed_prebounds_forged_state_before_encoding_or_sealing(monkeypatch):
    calls = {"utf8": 0, "seal": 0}

    def count_utf8(*_args, **_kwargs):
        calls["utf8"] += 1
        raise AssertionError("invalid state must be rejected before UTF-8 encoding")

    def count_seal(*_args, **_kwargs):
        calls["seal"] += 1
        raise AssertionError("invalid state must be rejected before hash serialization")

    monkeypatch.setattr("src.rag_text_chunking._exact_character_feed_utf8_length", count_utf8)
    monkeypatch.setattr("src.rag_text_chunking._feed_state_seal", count_seal)

    invalid_states = (
        (4, "x" * 4),
        (MAX_EXACT_CHARACTER_FEED_CHARS + 1, "x" * (MAX_EXACT_CHARACTER_FEED_CHARS + 1)),
        (True, "xx"),
    )
    for max_chars, carry in invalid_states:
        forged = object.__new__(ExactCharacterFeedState)
        for field, value in (
            ("version", EXACT_CHARACTER_FEED_VERSION),
            ("max_chars", max_chars),
            ("next_char_offset", 0),
            ("next_chunk_index", 0),
            ("carry", carry),
            ("terminal", False),
            ("_seal", "0" * 64),
        ):
            object.__setattr__(forged, field, value)

        with pytest.raises(ValueError, match="^invalid exact character feed state$"):
            feed_exact_character_text(forged, "", offset=0)

    assert calls == {"utf8": 0, "seal": 0}


def test_exact_character_feed_accepts_maximum_valid_carry_boundary():
    state = new_exact_character_feed_state(max_chars=4)
    emitted, state = feed_exact_character_text(state, "abc", offset=0)
    assert emitted == ()
    assert state.carry == "abc"

    emitted, state = feed_exact_character_text(state, "", offset=3, final=True)
    assert emitted == ("abc",)
    assert state.carry == ""
    assert state.terminal is True


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


def test_split_text_into_token_chunks_routes_model_and_hard_caps_unicode_code():
    model_hint = "unknown/model"
    text = "Grüße Ω code() " * 20

    chunks = split_text_into_token_chunks(
        text,
        max_tokens=24,
        overlap_tokens=4,
        model_hint=model_hint,
    )

    assert len(chunks) > 1
    assert all(count_text_tokens(chunk, model_hint=model_hint) <= 24 for chunk in chunks)


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
    text = "# Root\n\nAlpha one. Beta two.\f## Second\n\nGamma three. Delta four."
    chunks = split_text_into_chunks(text, chunk_size=24, overlap=12)

    metadata = [item.to_dict() for item in build_chunk_metadata(text, chunks)]

    assert metadata[0]["splitter_version"] == "rag_structured_v1"
    assert metadata[0]["source_hash"].startswith("sha256:")
    assert metadata[0]["char_start"] == 0
    assert metadata[0]["section_path"] == ""
    assert metadata[0]["page_start"] == 1
    assert metadata[0]["overlap_from_previous"] == 0
    assert metadata[1]["char_start"] < metadata[0]["char_end"]
    assert metadata[1]["overlap_from_previous"] > 0
    assert metadata[1]["token_end_est"] >= metadata[1]["token_start_est"]


def test_build_chunk_metadata_tracks_section_path_and_page_span():
    text = "# Root\n\nIntro paragraph.\f## Second\n\nDetails paragraph."
    chunks = split_structured_text_into_chunks(text, chunk_size=28, overlap=0)

    metadata = [item.to_dict() for item in build_chunk_metadata(text, chunks)]

    assert any(item["section_path"] == "Root" for item in metadata)
    assert any(item["section_path"] == "Root > Second" for item in metadata)
    assert max(item["page_end"] for item in metadata) == 2
