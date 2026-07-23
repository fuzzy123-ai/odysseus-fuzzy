"""Bounded, source-linked query/result contracts for Unified Source Index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import math
import re

from src.unified_source_index_contract import (
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    Locator,
    MessageRangeLocator,
    PageRangeLocator,
    RecordKind,
    RecordRef,
    RowRangeLocator,
    TextRangeLocator,
    canonical_json,
)
from src.unified_source_index_stores import (
    MAX_CURSOR_CHARS,
    MAX_PAGE_SIZE,
    StoreSnapshot,
    _owner_scope,
)


MAX_QUERY_TEXT_CHARS = 512
MAX_QUERY_RESULTS = 100
MAX_QUERY_CANDIDATES = 999
MAX_RESULT_SNIPPET_CHARS = 2_000

_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WHY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_LOCATOR_TYPES = (
    TextRangeLocator,
    PageRangeLocator,
    RowRangeLocator,
    MessageRangeLocator,
    CodeRangeLocator,
)


class UnifiedSourceIndexQueryError(ValueError):
    """Raised when a query or result breaks bounds, identity or policy."""


class LexicalMatchMode(StrEnum):
    EXACT = "exact"
    TOKEN = "token"
    PREFIX = "prefix"


@dataclass(frozen=True, slots=True)
class LexicalQuery:
    owner_scope: str
    query_text: str
    match_mode: LexicalMatchMode
    classification_ceiling: Classification
    limit: int = 20
    max_candidates: int = 200
    snippet_chars: int = 240
    source_ids: tuple[str, ...] = ()
    stale_after: str = ""
    cursor: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        if not isinstance(self.query_text, str):
            raise UnifiedSourceIndexQueryError("query_text must be text")
        query_text = self.query_text.strip()
        if (
            not query_text
            or len(query_text) > MAX_QUERY_TEXT_CHARS
            or any(ord(char) < 32 for char in query_text)
        ):
            raise UnifiedSourceIndexQueryError("query_text is empty, invalid or unbounded")
        object.__setattr__(self, "query_text", query_text)
        object.__setattr__(
            self,
            "match_mode",
            _enum(self.match_mode, LexicalMatchMode, "match_mode"),
        )
        object.__setattr__(
            self,
            "classification_ceiling",
            _enum(
                self.classification_ceiling,
                Classification,
                "classification_ceiling",
            ),
        )
        limit = _integer(
            self.limit,
            "limit",
            minimum=1,
            maximum=MAX_QUERY_RESULTS,
        )
        candidates = _integer(
            self.max_candidates,
            "max_candidates",
            minimum=1,
            maximum=MAX_QUERY_CANDIDATES,
        )
        if candidates < limit:
            raise UnifiedSourceIndexQueryError("max_candidates must be at least limit")
        object.__setattr__(self, "limit", limit)
        object.__setattr__(self, "max_candidates", candidates)
        object.__setattr__(
            self,
            "snippet_chars",
            _integer(
                self.snippet_chars,
                "snippet_chars",
                minimum=1,
                maximum=MAX_RESULT_SNIPPET_CHARS,
            ),
        )
        if not isinstance(self.source_ids, tuple) or len(self.source_ids) > MAX_PAGE_SIZE:
            raise UnifiedSourceIndexQueryError("source_ids must be a bounded tuple")
        source_ids = tuple(
            sorted(
                {
                    RecordRef(RecordKind.SOURCE, source_id).record_id
                    for source_id in self.source_ids
                }
            )
        )
        object.__setattr__(self, "source_ids", source_ids)
        if self.stale_after:
            _timestamp(self.stale_after, "stale_after")
        if not isinstance(self.cursor, str) or len(self.cursor) > MAX_CURSOR_CHARS:
            raise UnifiedSourceIndexQueryError("cursor is invalid or unbounded")

    @property
    def fingerprint(self) -> str:
        payload = {
            "owner_scope": self.owner_scope,
            "query_text": self.query_text,
            "match_mode": self.match_mode.value,
            "classification_ceiling": self.classification_ceiling.value,
            "limit": self.limit,
            "max_candidates": self.max_candidates,
            "snippet_chars": self.snippet_chars,
            "source_ids": self.source_ids,
            "stale_after": self.stale_after,
        }
        return "sha256:" + hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class QueryScoreComponents:
    lexical: float
    exact_bonus: float
    total: float

    def __post_init__(self) -> None:
        lexical = _score(self.lexical, "lexical", maximum=1.0)
        bonus = _score(self.exact_bonus, "exact_bonus", maximum=0.25)
        total = _score(self.total, "total", maximum=1.25)
        if total != round(lexical + bonus, 12):
            raise UnifiedSourceIndexQueryError("total score does not match components")
        object.__setattr__(self, "lexical", lexical)
        object.__setattr__(self, "exact_bonus", bonus)
        object.__setattr__(self, "total", total)


@dataclass(frozen=True, slots=True)
class QueryResultItem:
    owner_scope: str
    source_ref: RecordRef
    source_version_ref: RecordRef
    chunk_ref: RecordRef
    locator: Locator
    content_hash: str
    classification: Classification
    content_policy: ContentPolicy
    scores: QueryScoreComponents
    snippet: str
    snippet_clipped: bool
    stale: bool
    indexed_at: str
    why_selected: str = "lexical_match"

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        for value, kind, field_name in (
            (self.source_ref, RecordKind.SOURCE, "source_ref"),
            (
                self.source_version_ref,
                RecordKind.SOURCE_VERSION,
                "source_version_ref",
            ),
            (self.chunk_ref, RecordKind.CHUNK, "chunk_ref"),
        ):
            if not isinstance(value, RecordRef) or value.record_kind is not kind:
                raise UnifiedSourceIndexQueryError(f"{field_name} has the wrong kind")
        if not isinstance(self.locator, _LOCATOR_TYPES):
            raise UnifiedSourceIndexQueryError("result locator must be typed")
        if not isinstance(self.content_hash, str):
            raise UnifiedSourceIndexQueryError("content_hash must be sha256 text")
        content_hash = self.content_hash.lower()
        if not content_hash.startswith("sha256:"):
            content_hash = "sha256:" + content_hash
        if not _CONTENT_HASH_RE.fullmatch(content_hash):
            raise UnifiedSourceIndexQueryError("content_hash must be sha256 text")
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "classification",
            _enum(self.classification, Classification, "classification"),
        )
        object.__setattr__(
            self,
            "content_policy",
            _enum(self.content_policy, ContentPolicy, "content_policy"),
        )
        if not isinstance(self.scores, QueryScoreComponents):
            raise UnifiedSourceIndexQueryError("scores must be typed")
        if not isinstance(self.snippet, str) or len(self.snippet) > MAX_RESULT_SNIPPET_CHARS:
            raise UnifiedSourceIndexQueryError("snippet is invalid or unbounded")
        if not isinstance(self.snippet_clipped, bool) or not isinstance(self.stale, bool):
            raise UnifiedSourceIndexQueryError("result state flags must be boolean")
        if self.indexed_at:
            _timestamp(self.indexed_at, "indexed_at")
        if not isinstance(self.why_selected, str) or not _WHY_RE.fullmatch(
            self.why_selected
        ):
            raise UnifiedSourceIndexQueryError("why_selected must be a bounded token")


@dataclass(frozen=True, slots=True)
class QueryResultPage:
    items: tuple[QueryResultItem, ...]
    snapshot: StoreSnapshot
    candidate_count: int
    policy_filtered_count: int = 0
    candidate_clipped: bool = False
    next_cursor: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or len(self.items) > MAX_QUERY_RESULTS:
            raise UnifiedSourceIndexQueryError("result items must be a bounded tuple")
        if not all(isinstance(item, QueryResultItem) for item in self.items):
            raise UnifiedSourceIndexQueryError("result page contains an invalid item")
        if not isinstance(self.snapshot, StoreSnapshot):
            raise UnifiedSourceIndexQueryError("result snapshot must be typed")
        candidate_count = _integer(
            self.candidate_count,
            "candidate_count",
            minimum=0,
            maximum=MAX_QUERY_CANDIDATES,
        )
        filtered = _integer(
            self.policy_filtered_count,
            "policy_filtered_count",
            minimum=0,
            maximum=MAX_QUERY_CANDIDATES,
        )
        if candidate_count < len(self.items):
            raise UnifiedSourceIndexQueryError("candidate_count is smaller than result items")
        object.__setattr__(self, "candidate_count", candidate_count)
        object.__setattr__(self, "policy_filtered_count", filtered)
        if not isinstance(self.candidate_clipped, bool):
            raise UnifiedSourceIndexQueryError("candidate_clipped must be boolean")
        if not isinstance(self.next_cursor, str) or len(self.next_cursor) > MAX_CURSOR_CHARS:
            raise UnifiedSourceIndexQueryError("next_cursor is invalid or unbounded")

    @property
    def partial(self) -> bool:
        return self.candidate_clipped or self.policy_filtered_count > 0

    @property
    def clipped(self) -> bool:
        return bool(
            self.next_cursor
            or self.candidate_clipped
            or any(item.snippet_clipped for item in self.items)
        )

    @property
    def stale(self) -> bool:
        return any(item.stale for item in self.items)


def _timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise UnifiedSourceIndexQueryError(f"{field_name} is not a timestamp") from exc
    if parsed.tzinfo is None:
        raise UnifiedSourceIndexQueryError(f"{field_name} must include a timezone")
    return parsed


def _score(value: float, field_name: str, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnifiedSourceIndexQueryError(f"{field_name} score must be numeric")
    normalized = round(float(value), 12)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= maximum:
        raise UnifiedSourceIndexQueryError(f"{field_name} score is outside its bound")
    return normalized


def _integer(
    value: int,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise UnifiedSourceIndexQueryError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


def _enum(value, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise UnifiedSourceIndexQueryError(f"{field_name} is invalid") from exc


__all__ = [
    "LexicalMatchMode",
    "LexicalQuery",
    "MAX_QUERY_CANDIDATES",
    "MAX_QUERY_RESULTS",
    "MAX_QUERY_TEXT_CHARS",
    "MAX_RESULT_SNIPPET_CHARS",
    "QueryResultItem",
    "QueryResultPage",
    "QueryScoreComponents",
    "UnifiedSourceIndexQueryError",
]
