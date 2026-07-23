"""Sentence-aware text chunking helpers for VectorRAG."""

import hashlib
import re
from dataclasses import dataclass
from typing import List

from src.token_budget import count_text_tokens, split_budget

STRUCTURED_SPLITTER_VERSION = "rag_structured_v1"


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
