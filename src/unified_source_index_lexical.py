"""Bounded FTS5 lexical retrieval for the Unified Source Index."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Protocol, runtime_checkable

from src.unified_source_index_contract import (
    Classification,
    RecordKind,
    RecordRef,
    canonical_json,
)
from src.unified_source_index_query_contract import (
    LexicalMatchMode,
    LexicalQuery,
    QueryResultItem,
    QueryResultPage,
    QueryScoreComponents,
    UnifiedSourceIndexQueryError,
)
from src.unified_source_index_sqlite import ChunkSearchHit
from src.unified_source_index_stores import MAX_CURSOR_CHARS, StoreSnapshot


MAX_LEXICAL_TOKENS = 32
MAX_LEXICAL_TOKEN_CHARS = 64

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.PRIVATE: 1,
    Classification.SENSITIVE: 2,
    Classification.SECRET: 3,
    Classification.UNKNOWN: 4,
}


class LexicalRetrievalError(UnifiedSourceIndexQueryError):
    """Raised when lexical retrieval cannot return a consistent bounded page."""


@runtime_checkable
class LexicalSearchStore(Protocol):
    def current_snapshot(self) -> StoreSnapshot: ...

    def search_chunks(
        self,
        *,
        owner_scope: str,
        query: str,
        limit: int = 20,
    ) -> tuple[ChunkSearchHit, ...]: ...


class UnifiedSourceIndexLexicalRetriever:
    def __init__(self, store: LexicalSearchStore) -> None:
        if not isinstance(store, LexicalSearchStore):
            raise LexicalRetrievalError("store must provide bounded chunk FTS search")
        self._store = store

    def search(self, query: LexicalQuery) -> QueryResultPage:
        if not isinstance(query, LexicalQuery):
            raise LexicalRetrievalError("query must be a LexicalQuery")
        snapshot = self._store.current_snapshot()
        after = _decode_cursor(query.cursor, snapshot, query) if query.cursor else None
        expression, tokens = build_fts_expression(query.query_text, query.match_mode)
        hits = self._store.search_chunks(
            owner_scope=query.owner_scope,
            query=expression,
            limit=query.max_candidates + 1,
        )
        if self._store.current_snapshot() != snapshot:
            raise LexicalRetrievalError("USI snapshot changed during lexical retrieval")

        candidate_clipped = len(hits) > query.max_candidates
        candidates = hits[: query.max_candidates]
        filtered_count = 0
        items: list[QueryResultItem] = []
        for hit in candidates:
            chunk = hit.item.record
            if query.source_ids and chunk.source_id not in query.source_ids:
                filtered_count += 1
                continue
            if _CLASSIFICATION_RANK[chunk.classification] > _CLASSIFICATION_RANK[
                query.classification_ceiling
            ]:
                filtered_count += 1
                continue
            content = chunk.content or ""
            lexical_score = _lexical_score(hit.rank)
            exact_bonus = (
                0.1
                if query.match_mode is LexicalMatchMode.EXACT
                and _phrase_occurs(content, tokens)
                else 0.0
            )
            scores = QueryScoreComponents(
                lexical_score,
                exact_bonus,
                round(lexical_score + exact_bonus, 12),
            )
            snippet, snippet_clipped = _snippet(
                content,
                tokens,
                query.snippet_chars,
            )
            items.append(
                QueryResultItem(
                    owner_scope=chunk.owner_scope,
                    source_ref=RecordRef(RecordKind.SOURCE, chunk.source_id),
                    source_version_ref=RecordRef(
                        RecordKind.SOURCE_VERSION,
                        chunk.source_version_id,
                    ),
                    chunk_ref=chunk.ref(),
                    locator=chunk.locator,
                    content_hash=chunk.content_hash,
                    classification=chunk.classification,
                    content_policy=chunk.content_policy,
                    scores=scores,
                    snippet=snippet,
                    snippet_clipped=snippet_clipped,
                    stale=_is_stale(chunk.indexed_at, query.stale_after),
                    indexed_at=chunk.indexed_at,
                    why_selected=f"lexical_{query.match_mode.value}",
                )
            )

        items.sort(key=_result_sort_key)
        if after is not None:
            items = [item for item in items if _result_sort_key(item) > after]
        selected = tuple(items[: query.limit])
        next_cursor = ""
        if len(items) > query.limit and selected:
            next_cursor = _encode_cursor(snapshot, query, selected[-1])
        return QueryResultPage(
            selected,
            snapshot,
            candidate_count=len(candidates),
            policy_filtered_count=filtered_count,
            candidate_clipped=candidate_clipped,
            next_cursor=next_cursor,
        )


def build_fts_expression(
    query_text: str,
    match_mode: LexicalMatchMode | str,
) -> tuple[str, tuple[str, ...]]:
    """Compile user text into an operator-free FTS5 expression."""

    if not isinstance(query_text, str) or not query_text.strip():
        raise LexicalRetrievalError("lexical query text must be non-empty")
    mode = (
        match_mode
        if isinstance(match_mode, LexicalMatchMode)
        else LexicalMatchMode(match_mode)
    )
    tokens = tuple(_TOKEN_RE.findall(query_text.casefold()))
    if not tokens or len(tokens) > MAX_LEXICAL_TOKENS:
        raise LexicalRetrievalError("lexical token count is empty or unbounded")
    if any(len(token) > MAX_LEXICAL_TOKEN_CHARS for token in tokens):
        raise LexicalRetrievalError("lexical token is unbounded")
    if mode is LexicalMatchMode.EXACT:
        return f'"{" ".join(tokens)}"', tokens
    if mode is LexicalMatchMode.TOKEN:
        return " AND ".join(f'"{token}"' for token in tokens), tokens
    if mode is LexicalMatchMode.PREFIX:
        return " AND ".join(f'"{token}"*' for token in tokens), tokens
    raise LexicalRetrievalError("unsupported lexical match mode")


def _phrase_occurs(content: str, tokens: tuple[str, ...]) -> bool:
    content_tokens = tuple(_TOKEN_RE.findall(content.casefold()))
    width = len(tokens)
    return any(
        content_tokens[index : index + width] == tokens
        for index in range(max(0, len(content_tokens) - width + 1))
    )


def _lexical_score(rank: float) -> float:
    if isinstance(rank, bool) or not isinstance(rank, (int, float)) or not math.isfinite(rank):
        raise LexicalRetrievalError("FTS rank is not finite")
    bounded = max(-60.0, min(60.0, float(rank)))
    return round(1.0 / (1.0 + math.exp(bounded)), 12)


def _snippet(
    content: str,
    tokens: tuple[str, ...],
    max_chars: int,
) -> tuple[str, bool]:
    if not content:
        return "", False
    if len(content) <= max_chars:
        return content, False
    lowered = content.casefold()
    positions = [lowered.find(token) for token in tokens]
    positions = [position for position in positions if position >= 0]
    match_at = min(positions) if positions else 0
    start = max(0, match_at - max_chars // 3)
    end = min(len(content), start + max_chars)
    start = max(0, end - max_chars)
    return content[start:end], True


def _is_stale(indexed_at: str, stale_after: str) -> bool:
    if not stale_after:
        return False
    if not indexed_at:
        return True
    try:
        indexed = datetime.fromisoformat(indexed_at.replace("Z", "+00:00"))
        threshold = datetime.fromisoformat(stale_after.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LexicalRetrievalError("stale timestamp is invalid") from exc
    return indexed < threshold


def _result_sort_key(item: QueryResultItem) -> tuple[float, float, str]:
    return (-item.scores.total, -item.scores.lexical, item.chunk_ref.record_id)


def _encode_cursor(
    snapshot: StoreSnapshot,
    query: LexicalQuery,
    item: QueryResultItem,
) -> str:
    body = {
        "s": "usi.lexical.v1",
        "r": snapshot.snapshot_ref,
        "q": query.fingerprint,
        "t": item.scores.total,
        "l": item.scores.lexical,
        "i": item.chunk_ref.record_id,
    }
    checksum = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    encoded = canonical_json({**body, "h": checksum})
    if len(encoded) > MAX_CURSOR_CHARS:
        raise LexicalRetrievalError("generated lexical cursor exceeds its bound")
    return encoded


def _decode_cursor(
    value: str,
    snapshot: StoreSnapshot,
    query: LexicalQuery,
) -> tuple[float, float, str]:
    if not isinstance(value, str) or not value or len(value) > MAX_CURSOR_CHARS:
        raise LexicalRetrievalError("lexical cursor is invalid or unbounded")
    try:
        payload = json.loads(value, object_pairs_hook=_unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LexicalRetrievalError("lexical cursor is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"s", "r", "q", "t", "l", "i", "h"}:
        raise LexicalRetrievalError("lexical cursor fields are incomplete or unknown")
    checksum = payload.pop("h")
    if checksum != hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest():
        raise LexicalRetrievalError("lexical cursor checksum does not match")
    if (
        payload["s"] != "usi.lexical.v1"
        or payload["r"] != snapshot.snapshot_ref
        or payload["q"] != replace(query, cursor="").fingerprint
    ):
        raise LexicalRetrievalError("lexical cursor belongs to another snapshot or query")
    chunk_ref = RecordRef(RecordKind.CHUNK, payload["i"])
    total = _cursor_score(payload["t"], "total")
    lexical = _cursor_score(payload["l"], "lexical")
    return (-total, -lexical, chunk_ref.record_id)


def _cursor_score(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LexicalRetrievalError(f"lexical cursor {field_name} score is invalid")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise LexicalRetrievalError(f"lexical cursor {field_name} score is invalid")
    return normalized


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LexicalRetrievalError(f"lexical cursor contains duplicate field: {key}")
        result[key] = value
    return result


__all__ = [
    "LexicalRetrievalError",
    "LexicalSearchStore",
    "MAX_LEXICAL_TOKEN_CHARS",
    "MAX_LEXICAL_TOKENS",
    "UnifiedSourceIndexLexicalRetriever",
    "build_fts_expression",
]
