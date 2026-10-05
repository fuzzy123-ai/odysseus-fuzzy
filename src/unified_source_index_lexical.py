"""Bounded FTS5 lexical retrieval for the Unified Source Index."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import hmac
import json
import math
import re
from typing import Protocol, runtime_checkable

from src.unified_source_index_contract import (
    Classification,
    RecordKind,
    RecordRef,
    SourceKind,
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
from src.unified_source_index_harbour_query import (
    HarbourKnowledgeQueryGuard,
    HarbourKnowledgeQueryGuardError,
    _capture_store_read,
)
from src.unified_source_index_sqlite import ChunkSearchHit
from src.unified_source_index_stores import (
    MAX_CURSOR_CHARS,
    StoreSnapshot,
)


MAX_LEXICAL_TOKENS = 32
MAX_LEXICAL_TOKEN_CHARS = 64

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_GUARDED_CURSOR_DOMAIN = b"odysseus.knowledge.query.lexical.cursor.v1\x00"
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
    def __init__(
        self,
        store: LexicalSearchStore,
        *,
        lifecycle_guard: HarbourKnowledgeQueryGuard | None = None,
        cursor_signing_key: bytes | None = None,
    ) -> None:
        if not isinstance(store, LexicalSearchStore):
            raise LexicalRetrievalError("store must provide bounded chunk FTS search")
        if (lifecycle_guard is None) != (cursor_signing_key is None):
            raise LexicalRetrievalError("lifecycle guard and cursor key must be paired")
        if lifecycle_guard is not None and type(lifecycle_guard) is not HarbourKnowledgeQueryGuard:
            raise LexicalRetrievalError("lifecycle guard is invalid")
        if cursor_signing_key is not None and (
            type(cursor_signing_key) is not bytes
            or not 32 <= len(cursor_signing_key) <= 128
        ):
            raise LexicalRetrievalError("cursor signing key is invalid")
        self._store = store
        self._lifecycle_guard = lifecycle_guard
        self._cursor_signing_key = cursor_signing_key

    @property
    def supports_domain_queries(self) -> bool:
        return self._lifecycle_guard is not None

    def search(self, query: LexicalQuery) -> QueryResultPage:
        if not isinstance(query, LexicalQuery):
            raise LexicalRetrievalError("query must be a LexicalQuery")
        if query.domain is not None and self._lifecycle_guard is None:
            raise LexicalRetrievalError("domain-constrained lexical query is unavailable")
        if self._lifecycle_guard is not None:
            return self._search_guarded(query)
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

    def _search_guarded(self, query: LexicalQuery) -> QueryResultPage:
        assert self._lifecycle_guard is not None and self._cursor_signing_key is not None
        snapshot = self._store.current_snapshot()
        try:
            cursor_claims = (
                _decode_guarded_cursor(
                    query.cursor, snapshot, query, self._cursor_signing_key,
                )
                if query.cursor else None
            )
            scope = self._lifecycle_guard.capture(
                owner_scope=query.owner_scope,
                requested_ceiling=query.classification_ceiling,
                query_fingerprint=replace(query, cursor="").fingerprint,
                domain=query.domain,
                expected_head_boundary=cursor_claims[1] if cursor_claims else None,
                expected_snapshot_digest=cursor_claims[2] if cursor_claims else None,
            )
            if (
                scope.owner_scope != query.owner_scope
                or scope.query_fingerprint != replace(query, cursor="").fingerprint
                or scope.domain != query.domain
            ):
                raise LexicalRetrievalError("lifecycle owner does not match query")
            raw_read = self._store.begin_read(snapshot)  # type: ignore[attr-defined]
            try:
                read = _capture_store_read(
                    raw_read, expected_snapshot=snapshot,
                )
            except HarbourKnowledgeQueryGuardError:
                raise LexicalRetrievalError(
                    "lifecycle read transaction is invalid"
                ) from None
        except LexicalRetrievalError:
            raise
        except Exception:
            raise LexicalRetrievalError("lifecycle boundary capture failed") from None

        try:
            if read.snapshot != snapshot:
                raise LexicalRetrievalError("USI snapshot changed during lexical retrieval")
            after = cursor_claims[0] if cursor_claims else None
            expression, tokens = build_fts_expression(query.query_text, query.match_mode)
            try:
                hits = self._store.search_chunks(
                    owner_scope=query.owner_scope,
                    query=expression,
                    limit=query.max_candidates + 1,
                )
                if self._store.current_snapshot() != snapshot or read.snapshot != snapshot:
                    raise LexicalRetrievalError(
                        "USI snapshot changed during lexical retrieval"
                    )
                candidate_clipped = len(hits) > query.max_candidates
                candidates = hits[: query.max_candidates]
                filtered_count = 0
                items: list[QueryResultItem] = []
                for hit in candidates:
                    chunk = hit.item.record
                    try:
                        allowed = scope.allows(chunk, read)
                    except HarbourKnowledgeQueryGuardError:
                        raise LexicalRetrievalError(
                            "lifecycle parent validation failed"
                        ) from None
                    if not allowed:
                        filtered_count += 1
                        continue
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
                        lexical_score, exact_bonus,
                        round(lexical_score + exact_bonus, 12),
                    )
                    snippet, snippet_clipped = _snippet(
                        content, tokens, query.snippet_chars,
                    )
                    items.append(QueryResultItem(
                        owner_scope=chunk.owner_scope,
                        source_ref=RecordRef(RecordKind.SOURCE, chunk.source_id),
                        source_version_ref=RecordRef(
                            RecordKind.SOURCE_VERSION, chunk.source_version_id,
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
                    ))

                items.sort(key=_result_sort_key)
                if after is not None:
                    items = [item for item in items if _result_sort_key(item) > after]
                selected = tuple(items[: query.limit])
                next_cursor = ""
                if len(items) > query.limit and selected:
                    next_cursor = _encode_guarded_cursor(
                        snapshot, query, selected[-1], scope,
                        self._cursor_signing_key,
                    )
                if self._store.current_snapshot() != snapshot or read.snapshot != snapshot:
                    raise LexicalRetrievalError(
                        "USI snapshot changed during lexical retrieval"
                    )
                return QueryResultPage(
                    selected,
                    snapshot,
                    candidate_count=len(candidates),
                    policy_filtered_count=filtered_count,
                    candidate_clipped=candidate_clipped,
                    next_cursor=next_cursor,
                )
            except LexicalRetrievalError:
                raise
            except Exception:
                raise LexicalRetrievalError("guarded lexical page failed") from None
        finally:
            try:
                read.close()
            except Exception:
                raise LexicalRetrievalError("pinned USI read could not close") from None


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


def _encode_guarded_cursor(
    snapshot: StoreSnapshot,
    query: LexicalQuery,
    item: QueryResultItem,
    scope: object,
    key: bytes,
) -> str:
    body = {
        "s": "usi.lexical.guarded.v1",
        "u": snapshot.snapshot_ref,
        "q": replace(query, cursor="").fingerprint,
        "b": scope.captured_head_boundary,
        "d": scope.snapshot_digest,
        "t": item.scores.total,
        "l": item.scores.lexical,
        "i": item.chunk_ref.record_id,
    }
    mac = hmac.new(
        key,
        _GUARDED_CURSOR_DOMAIN + canonical_json(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    encoded = canonical_json({"body": body, "mac": mac})
    if len(encoded) > MAX_CURSOR_CHARS:
        raise LexicalRetrievalError("generated guarded lexical cursor exceeds its bound")
    return encoded


def _decode_guarded_cursor(
    value: str,
    snapshot: StoreSnapshot,
    query: LexicalQuery,
    key: bytes,
) -> tuple[tuple[float, float, str], str, str]:
    if type(value) is not str or not value or len(value) > MAX_CURSOR_CHARS:
        raise LexicalRetrievalError("guarded lexical cursor is invalid or unbounded")
    try:
        payload = json.loads(value, object_pairs_hook=_unique_json_object)
        if type(payload) is not dict or set(payload) != {"body", "mac"}:
            raise ValueError
        body, supplied_mac = payload["body"], payload["mac"]
        if type(body) is not dict or set(body) != {
            "s", "u", "q", "b", "d", "t", "l", "i",
        }:
            raise ValueError
        if type(supplied_mac) is not str or re.fullmatch(r"[0-9a-f]{64}", supplied_mac) is None:
            raise ValueError
        if canonical_json(payload) != value:
            raise ValueError
        expected_mac = hmac.new(
            key,
            _GUARDED_CURSOR_DOMAIN + canonical_json(body).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied_mac, expected_mac):
            raise ValueError
        if (
            body["s"] != "usi.lexical.guarded.v1"
            or body["u"] != snapshot.snapshot_ref
            or body["q"] != replace(query, cursor="").fingerprint
            or type(body["b"]) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", body["b"]) is None
            or type(body["d"]) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", body["d"]) is None
            or type(body["i"]) is not str
        ):
            raise ValueError
        chunk_ref = RecordRef(RecordKind.CHUNK, body["i"])
        total = _cursor_score(body["t"], "total")
        lexical = _cursor_score(body["l"], "lexical")
        return (-total, -lexical, chunk_ref.record_id), body["b"], body["d"]
    except LexicalRetrievalError:
        raise LexicalRetrievalError("guarded lexical cursor is invalid") from None
    except Exception:
        raise LexicalRetrievalError("guarded lexical cursor is invalid") from None


def _decode_cursor(
    value: str,
    snapshot: StoreSnapshot,
    query: LexicalQuery,
) -> tuple[float, float, str]:
    if type(value) is not str or not value or len(value) > MAX_CURSOR_CHARS:
        raise LexicalRetrievalError("lexical cursor is invalid or unbounded")
    try:
        payload = json.loads(value, object_pairs_hook=_unique_json_object)
    except LexicalRetrievalError:
        raise LexicalRetrievalError("lexical cursor is invalid") from None
    except RecursionError:
        raise LexicalRetrievalError("lexical cursor is not valid JSON") from None
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LexicalRetrievalError("lexical cursor is not valid JSON") from None
    if type(payload) is not dict or set(payload) != {"s", "r", "q", "t", "l", "i", "h"}:
        raise LexicalRetrievalError("lexical cursor fields are incomplete or unknown")
    checksum = payload.pop("h")
    if (
        type(checksum) is not str
        or any(type(payload[field]) is not str for field in ("s", "r", "q", "i"))
        or any(type(payload[field]) not in (int, float)
               for field in ("t", "l"))
    ):
        raise LexicalRetrievalError("lexical cursor is invalid")
    try:
        total = _cursor_score(payload["t"], "total")
        lexical = _cursor_score(payload["l"], "lexical")
    except LexicalRetrievalError:
        raise LexicalRetrievalError("lexical cursor is invalid") from None
    except Exception:
        raise LexicalRetrievalError("lexical cursor is invalid") from None
    if checksum != hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest():
        raise LexicalRetrievalError("lexical cursor checksum does not match")
    if (
        payload["s"] != "usi.lexical.v1"
        or payload["r"] != snapshot.snapshot_ref
        or payload["q"] != replace(query, cursor="").fingerprint
    ):
        raise LexicalRetrievalError("lexical cursor belongs to another snapshot or query")
    try:
        chunk_ref = RecordRef(RecordKind.CHUNK, payload["i"])
        return (-total, -lexical, chunk_ref.record_id)
    except LexicalRetrievalError:
        raise LexicalRetrievalError("lexical cursor is invalid") from None
    except Exception:
        raise LexicalRetrievalError("lexical cursor is invalid") from None


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
