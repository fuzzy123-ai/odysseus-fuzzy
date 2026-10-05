"""Sentence-aware text chunking helpers for VectorRAG."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import List

from src.token_budget import count_text_tokens, split_budget

STRUCTURED_SPLITTER_VERSION = "rag_structured_v1"
EXACT_CHARACTER_SPLITTER_VERSION = "rag_exact_character_ranges_v1"
MAX_EXACT_CHARACTER_RANGES = 10_000
EXACT_CHARACTER_FEED_VERSION = "rag_exact_character_feed_v1"
MAX_EXACT_CHARACTER_FEED_CHARS = 250_000
MAX_EXACT_CHARACTER_FEED_FRAGMENT_CHARS = 250_000
MAX_EXACT_CHARACTER_FEED_CHUNKS = 4_096
MAX_EXACT_CHARACTER_FEED_BYTES = 1_000_000
MAX_EXACT_CHARACTER_FEED_STATE_INTEGER = 2**63 - 1
# This deterministic seal detects malformed or mutated state.  It is not an
# authentication token; callers own state progression.
_EXACT_CHARACTER_FEED_SEAL_KEY = b"odysseus.rag.exact-character-feed.v1"


@dataclass(frozen=True, slots=True)
class ExactCharacterRange:
    """A nonempty half-open range of Python character offsets."""

    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if type(self.char_start) is not int or type(self.char_end) is not int:
            raise TypeError("character range bounds must be built-in ints")
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ValueError("character range bounds must form a nonempty nonnegative range")


def split_text_into_exact_character_ranges(
    text: str,
    *,
    max_chars: int,
    max_ranges: int = MAX_EXACT_CHARACTER_RANGES,
) -> tuple[ExactCharacterRange, ...]:
    """Return contiguous ranges that exactly cover ``text`` without copying it."""

    if type(text) is not str:
        raise TypeError("text must be a built-in str")
    if type(max_chars) is not int or type(max_ranges) is not int:
        raise TypeError("range limits must be built-in ints")
    if not 1 <= max_chars <= 2**63 - 1:
        raise ValueError("max_chars must be between 1 and 2**63 - 1")
    if not 1 <= max_ranges <= MAX_EXACT_CHARACTER_RANGES:
        raise ValueError("max_ranges must be between 1 and MAX_EXACT_CHARACTER_RANGES")

    required_ranges = (len(text) + max_chars - 1) // max_chars
    if required_ranges > max_ranges:
        raise ValueError("exact character range limit exceeded")
    return tuple(
        ExactCharacterRange(start, min(start + max_chars, len(text)))
        for start in range(0, len(text), max_chars)
    )


@dataclass(frozen=True, slots=True)
class ExactCharacterFeedState:
    """Bounded immutable continuation state for exact character feeds."""

    version: str
    max_chars: int
    next_char_offset: int
    next_chunk_index: int
    carry: str
    terminal: bool
    _seal: str

    def __post_init__(self) -> None:
        _validate_exact_character_feed_state(self)


def _feed_state_seal(
    version: str,
    max_chars: int,
    next_char_offset: int,
    next_chunk_index: int,
    carry: str,
    terminal: bool,
) -> str:
    try:
        payload = json.dumps(
            [version, max_chars, next_char_offset, next_chunk_index, carry, terminal],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except BaseException:
        raise ValueError("invalid exact character feed state") from None
    return hashlib.sha256(_EXACT_CHARACTER_FEED_SEAL_KEY + payload).hexdigest()


def _exact_character_feed_utf8_length(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except BaseException:
        raise ValueError("invalid exact character feed text") from None


def _validate_exact_character_feed_state(state: object) -> None:
    if type(state) is not ExactCharacterFeedState:
        raise TypeError("feed state must be an exact ExactCharacterFeedState")
    try:
        fields = (
            state.version,
            state.max_chars,
            state.next_char_offset,
            state.next_chunk_index,
            state.carry,
            state.terminal,
            state._seal,
        )
    except BaseException:
        raise ValueError("invalid exact character feed state") from None
    if (
        type(fields[0]) is not str
        or fields[0] != EXACT_CHARACTER_FEED_VERSION
        or type(fields[1]) is not int
        or not 1 <= fields[1] <= MAX_EXACT_CHARACTER_FEED_CHARS
        or type(fields[2]) is not int
        or not 0 <= fields[2] <= MAX_EXACT_CHARACTER_FEED_STATE_INTEGER
        or type(fields[3]) is not int
        or not 0 <= fields[3] <= MAX_EXACT_CHARACTER_FEED_STATE_INTEGER
        or type(fields[4]) is not str
        or len(fields[4]) >= fields[1]
        or type(fields[5]) is not bool
        or type(fields[6]) is not str
        or len(fields[6]) != 64
    ):
        raise ValueError("invalid exact character feed state")
    try:
        carry_bytes = _exact_character_feed_utf8_length(fields[4])
    except BaseException:
        raise ValueError("invalid exact character feed state") from None
    if carry_bytes > MAX_EXACT_CHARACTER_FEED_BYTES:
        raise ValueError("invalid exact character feed state")
    if fields[6] != _feed_state_seal(*fields[:6]):
        raise ValueError("invalid exact character feed state")


def new_exact_character_feed_state(*, max_chars: int) -> ExactCharacterFeedState:
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_EXACT_CHARACTER_FEED_CHARS:
        raise ValueError("max_chars is outside the exact character feed bound")
    version = EXACT_CHARACTER_FEED_VERSION
    carry = ""
    terminal = False
    return ExactCharacterFeedState(
        version,
        max_chars,
        0,
        0,
        carry,
        terminal,
        _feed_state_seal(version, max_chars, 0, 0, carry, terminal),
    )


def feed_exact_character_text(
    state: ExactCharacterFeedState,
    fragment: str,
    *,
    offset: int,
    final: bool = False,
) -> tuple[tuple[str, ...], ExactCharacterFeedState]:
    """Consume one bounded contiguous fragment and emit complete exact chunks.

    Callers own linear progression of the returned state.  Offsets detect
    stale fragments relative to that supplied state; this pure utility does
    not maintain replay history or session authority.
    """

    _validate_exact_character_feed_state(state)
    if type(fragment) is not str:
        raise TypeError("fragment must be a built-in str")
    if type(offset) is not int or offset != state.next_char_offset:
        raise ValueError("fragment offset is not contiguous")
    if type(final) is not bool:
        raise TypeError("final must be a built-in bool")
    if state.terminal:
        raise ValueError("exact character feed is already terminal")
    if len(fragment) > MAX_EXACT_CHARACTER_FEED_FRAGMENT_CHARS:
        raise ValueError("fragment exceeds the exact character feed bound")
    try:
        fragment_bytes = _exact_character_feed_utf8_length(fragment)
    except BaseException:
        raise ValueError("fragment is not valid UTF-8 text") from None
    if fragment_bytes > MAX_EXACT_CHARACTER_FEED_BYTES:
        raise ValueError("fragment exceeds the exact character feed byte bound")
    if _exact_character_feed_utf8_length(state.carry) + fragment_bytes > 2 * MAX_EXACT_CHARACTER_FEED_BYTES:
        raise ValueError("processing exceeds the exact character feed byte bound")
    if offset + len(fragment) > MAX_EXACT_CHARACTER_FEED_STATE_INTEGER:
        raise ValueError("fragment offset exceeds the exact character feed bound")

    combined = state.carry + fragment
    full_chunks = len(combined) // state.max_chars
    remainder = len(combined) % state.max_chars
    chunk_count = full_chunks + (1 if final and remainder else 0)
    if chunk_count > MAX_EXACT_CHARACTER_FEED_CHUNKS:
        raise ValueError("output exceeds the exact character feed bound")
    chunks = tuple(
        combined[index * state.max_chars : (index + 1) * state.max_chars]
        for index in range(full_chunks)
    )
    consumed = full_chunks * state.max_chars
    carry = combined[consumed:]
    if final and carry:
        chunks += (carry,)
        carry = ""
    try:
        output_bytes = sum(_exact_character_feed_utf8_length(chunk) for chunk in chunks)
    except BaseException:
        raise ValueError("output is not valid UTF-8 text") from None
    if output_bytes > MAX_EXACT_CHARACTER_FEED_BYTES:
        raise ValueError("output exceeds the exact character feed byte bound")
    next_offset = offset + len(fragment)
    next_chunk_index = state.next_chunk_index + len(chunks)
    if next_chunk_index > MAX_EXACT_CHARACTER_FEED_STATE_INTEGER:
        raise ValueError("chunk index exceeds the exact character feed bound")
    terminal = final
    next_state = ExactCharacterFeedState(
        EXACT_CHARACTER_FEED_VERSION,
        state.max_chars,
        next_offset,
        next_chunk_index,
        carry,
        terminal,
        _feed_state_seal(
            EXACT_CHARACTER_FEED_VERSION,
            state.max_chars,
            next_offset,
            next_chunk_index,
            carry,
            terminal,
        ),
    )
    return chunks, next_state


@dataclass(frozen=True, slots=True)
class TextChunkMetadata:
    chunk_index: int
    splitter_version: str
    source_hash: str
    section_path: str
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    token_start_est: int
    token_end_est: int
    overlap_from_previous: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "chunk_index": self.chunk_index,
            "splitter_version": self.splitter_version,
            "source_hash": self.source_hash,
            "section_path": self.section_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "token_start_est": self.token_start_est,
            "token_end_est": self.token_end_est,
            "overlap_from_previous": self.overlap_from_previous,
        }


def split_text_into_chunks(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    sentences = re.split(r'(?<=[.!?])\s+|\n{2,}', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0

    for sentence in sentences:
        sent_len = len(sentence)

        if sent_len > chunk_size:
            if current_chunk:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_len = 0

            start = 0
            step = chunk_size - overlap
            while start < sent_len:
                end = min(start + chunk_size, sent_len)
                chunks.append(sentence[start:end])
                if end >= sent_len:
                    break
                start = start + step if step > 0 else end
            continue

        if current_len + sent_len + 1 > chunk_size and current_chunk:
            chunks.append(' '.join(current_chunk))
            overlap_sentences: List[str] = []
            overlap_len = 0
            for s in reversed(current_chunk):
                if overlap_len + len(s) > overlap:
                    break
                overlap_sentences.insert(0, s)
                overlap_len += len(s) + 1
            current_chunk = overlap_sentences
            current_len = sum(len(s) for s in current_chunk) + max(0, len(current_chunk) - 1)

        current_chunk.append(sentence)
        current_len += sent_len + (1 if current_len > 0 else 0)

    if current_chunk:
        chunks.append(' '.join(current_chunk))

    return chunks if chunks else [text]


def split_structured_text_into_chunks(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Split text on stable document structures before falling back to chars."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    current_blocks: List[str] = []
    current_len = 0

    for block in _split_structural_blocks(text):
        if len(block) > chunk_size:
            if current_blocks:
                chunks.append("\n\n".join(current_blocks))
                current_blocks = []
                current_len = 0
            chunks.extend(split_text_into_chunks(block, chunk_size=chunk_size, overlap=overlap))
            continue

        next_len = current_len + len(block) + (2 if current_blocks else 0)
        if next_len > chunk_size and current_blocks:
            chunks.append("\n\n".join(current_blocks))
            current_blocks = _overlap_blocks(current_blocks, overlap)
            current_len = _joined_len(current_blocks)

        current_blocks.append(block)
        current_len = _joined_len(current_blocks)

    if current_blocks:
        chunks.append("\n\n".join(current_blocks))
    return chunks if chunks else [text]


def split_text_into_token_chunks(
    text: str,
    *,
    max_tokens: int = 300,
    overlap_tokens: int = 60,
    model_hint: str | None = None,
) -> List[str]:
    """Split text with a token-budget API while preserving legacy behavior.

    The current implementation uses the shared deterministic estimator and maps
    the budget to the existing sentence-aware character splitter. Keeping this
    as a separate entry point lets RAG and memory callers opt in gradually.
    """

    budget = split_budget(max_tokens, overlap_tokens, model_hint=model_hint)
    chunks = split_text_into_chunks(
        text,
        chunk_size=budget.max_chars_estimate,
        overlap=budget.overlap_chars_estimate,
    )
    if not chunks:
        return []
    result: List[str] = []
    for chunk in chunks:
        if count_text_tokens(chunk, model_hint=model_hint) <= budget.max_tokens:
            result.append(chunk)
            continue
        result.extend(
            _hard_split_to_token_budget(
                chunk,
                max_tokens=budget.max_tokens,
                overlap_tokens=budget.overlap_tokens,
                model_hint=model_hint,
            )
        )
    return result


def _hard_split_to_token_budget(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    model_hint: str | None,
) -> List[str]:
    """Split a single over-budget span with measured, monotonic progress."""

    pieces: List[str] = []
    start = 0
    while start < len(text):
        end = _largest_fitting_end(text, start, max_tokens=max_tokens, model_hint=model_hint)
        if end <= start:
            # One Unicode code point can consume multiple fallback units and is
            # indivisible as Python text.  Failing explicitly is safer than
            # returning a chunk that violates the caller's declared hard cap.
            unit_count = count_text_tokens(text[start : start + 1], model_hint=model_hint)
            raise ValueError(
                f"max_tokens={max_tokens} cannot fit one text unit ({unit_count} estimated tokens)"
            )
        pieces.append(text[start:end])
        if end >= len(text):
            break
        next_start = end
        if overlap_tokens > 0:
            next_start = _overlap_start(
                text,
                segment_start=start,
                segment_end=end,
                overlap_tokens=overlap_tokens,
                model_hint=model_hint,
            )
        start = max(start + 1, next_start)
    return pieces


def _largest_fitting_end(
    text: str,
    start: int,
    *,
    max_tokens: int,
    model_hint: str | None,
) -> int:
    low, high = start + 1, len(text)
    best = start
    while low <= high:
        midpoint = (low + high) // 2
        if count_text_tokens(text[start:midpoint], model_hint=model_hint) <= max_tokens:
            best = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _overlap_start(
    text: str,
    *,
    segment_start: int,
    segment_end: int,
    overlap_tokens: int,
    model_hint: str | None,
) -> int:
    low, high = segment_start, segment_end
    best = segment_end
    while low <= high:
        midpoint = (low + high) // 2
        suffix_tokens = count_text_tokens(text[midpoint:segment_end], model_hint=model_hint)
        if suffix_tokens <= overlap_tokens:
            best = midpoint
            high = midpoint - 1
        else:
            low = midpoint + 1
    return best


def build_chunk_metadata(
    text: str,
    chunks: List[str],
    *,
    splitter_version: str = STRUCTURED_SPLITTER_VERSION,
    model_hint: str | None = None,
) -> List[TextChunkMetadata]:
    """Build provenance metadata for already-created chunks."""

    source_text = str(text or "")
    source_hash = "sha256:" + hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    metadata: List[TextChunkMetadata] = []
    previous_end = 0
    for index, chunk in enumerate(chunks):
        start = _locate_chunk(source_text, chunk, previous_end=previous_end)
        end = min(len(source_text), start + len(chunk))
        token_start = count_text_tokens(source_text[:start], model_hint=model_hint)
        token_end = token_start + count_text_tokens(chunk, model_hint=model_hint)
        metadata.append(
            TextChunkMetadata(
                chunk_index=index,
                splitter_version=splitter_version,
                source_hash=source_hash,
                section_path=_section_path_for_offset(source_text, start),
                page_start=_page_number_for_offset(source_text, start),
                page_end=_page_number_for_offset(source_text, max(start, end - 1)),
                char_start=start,
                char_end=end,
                token_start_est=token_start,
                token_end_est=token_end,
                overlap_from_previous=max(0, previous_end - start) if index else 0,
            )
        )
        previous_end = max(previous_end, end)
    return metadata


def _split_structural_blocks(text: str) -> List[str]:
    lines = text.replace("\f", "\n\n").splitlines()
    blocks: List[str] = []
    current: List[str] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        nonlocal current
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
        current = []

    for line in lines:
        stripped = line.strip()
        marker = _fence_marker(stripped)
        if marker:
            if not in_fence:
                if current:
                    flush()
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                current.append(line)
                flush()
                in_fence = False
                fence_marker = ""
                continue
            current.append(line)
            continue

        if in_fence:
            current.append(line)
            continue

        if not stripped:
            flush()
            continue

        if _is_heading(stripped) and current:
            flush()
        current.append(line)

    flush()
    return blocks


def _fence_marker(stripped_line: str) -> str:
    if stripped_line.startswith("```"):
        return "```"
    if stripped_line.startswith("~~~"):
        return "~~~"
    return ""


def _is_heading(stripped_line: str) -> bool:
    return bool(re.match(r"^#{1,6}\s+\S", stripped_line))


def _overlap_blocks(blocks: List[str], overlap: int) -> List[str]:
    if overlap <= 0:
        return []
    selected: List[str] = []
    selected_len = 0
    for block in reversed(blocks):
        block_len = len(block) + (2 if selected else 0)
        if selected_len + block_len > overlap:
            break
        selected.insert(0, block)
        selected_len += block_len
    return selected


def _joined_len(blocks: List[str]) -> int:
    if not blocks:
        return 0
    return sum(len(block) for block in blocks) + 2 * (len(blocks) - 1)


def _locate_chunk(text: str, chunk: str, *, previous_end: int) -> int:
    if not chunk:
        return previous_end
    search_start = max(0, previous_end - len(chunk))
    pos = text.find(chunk, search_start)
    if pos >= 0:
        return pos
    pos = text.find(chunk)
    if pos >= 0:
        return pos
    return min(previous_end, len(text))


def _section_path_for_offset(text: str, offset: int) -> str:
    headings: List[str] = []
    consumed = 0
    for line in text[: max(0, offset)].splitlines():
        stripped = line.strip()
        match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            headings = headings[: level - 1] + [title]
        consumed += len(line) + 1
        if consumed > offset:
            break
    return " > ".join(headings)


def _page_number_for_offset(text: str, offset: int) -> int:
    if offset <= 0:
        return 1
    return text[:offset].count("\f") + 1
