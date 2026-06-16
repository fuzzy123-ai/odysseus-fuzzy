"""Small backend contract for query budget specifications and results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class QueryBudgetError(ValueError):
    """Raised when a query budget payload is invalid or unsafe."""


class QueryBudgetStatus(StrEnum):
    WITHIN_BUDGET = "within_budget"
    PARTIAL = "partial"
    CLIPPED = "clipped"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"
    FAILED = "failed"


class QueryBudgetKind(StrEnum):
    MEMORY = "memory"
    GRAPH = "graph"
    VECTOR = "vector"
    KEYWORD = "keyword"
    UI_PAYLOAD = "ui_payload"
    JOB = "job"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise QueryBudgetError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise QueryBudgetError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise QueryBudgetError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise QueryBudgetError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_int(value: Any, *, field_name: str, allow_empty: bool = True) -> int:
    if value in (None, ""):
        if allow_empty:
            return 0
        raise QueryBudgetError(f"{field_name} must not be empty")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise QueryBudgetError(f"{field_name} must be an int") from None
    if normalized < 0:
        raise QueryBudgetError(f"{field_name} must be >= 0")
    return normalized


def _normalize_status(value: Any) -> QueryBudgetStatus:
    if isinstance(value, QueryBudgetStatus):
        return value
    normalized = _normalize_slug(value, field_name="status").replace("-", "_")
    try:
        return QueryBudgetStatus(normalized)
    except ValueError as exc:
        raise QueryBudgetError("status is not supported") from exc


def _normalize_kind(value: Any) -> QueryBudgetKind:
    if isinstance(value, QueryBudgetKind):
        return value
    normalized = _normalize_slug(value, field_name="kind").replace("-", "_")
    try:
        return QueryBudgetKind(normalized)
    except ValueError as exc:
        raise QueryBudgetError("kind is not supported") from exc


@dataclass(frozen=True, slots=True)
class QueryBudget:
    query_budget_id: str
    query_ref: str
    kind: QueryBudgetKind
    limit: int
    cursor: str
    time_budget_ms: int
    token_budget: int
    max_nodes: int
    max_edges: int
    depth: int

    @classmethod
    def create(
        cls,
        *,
        query_budget_id: Any,
        query_ref: Any,
        kind: QueryBudgetKind | str,
        limit: Any = 0,
        cursor: Any = "",
        time_budget_ms: Any = 0,
        token_budget: Any = 0,
        max_nodes: Any = 0,
        max_edges: Any = 0,
        depth: Any = 0,
    ) -> "QueryBudget":
        normalized_kind = _normalize_kind(kind)
        normalized_limit = _normalize_int(limit, field_name="limit")
        normalized_time = _normalize_int(time_budget_ms, field_name="time_budget_ms")
        normalized_tokens = _normalize_int(token_budget, field_name="token_budget")
        normalized_nodes = _normalize_int(max_nodes, field_name="max_nodes")
        normalized_edges = _normalize_int(max_edges, field_name="max_edges")
        normalized_depth = _normalize_int(depth, field_name="depth")
        if not any(
            value > 0 for value in (normalized_limit, normalized_time, normalized_tokens, normalized_nodes, normalized_edges, normalized_depth)
        ):
            raise QueryBudgetError("query budgets require at least one hard budget")
        if normalized_kind == QueryBudgetKind.GRAPH and not any(
            value > 0 for value in (normalized_nodes, normalized_edges, normalized_depth)
        ):
            raise QueryBudgetError("graph budgets require max_nodes, max_edges, or depth")
        return cls(
            query_budget_id=_normalize_slug(query_budget_id, field_name="query_budget_id"),
            query_ref=_normalize_slug(query_ref, field_name="query_ref"),
            kind=normalized_kind,
            limit=normalized_limit,
            cursor=_normalize_text(cursor, field_name="cursor", allow_empty=True),
            time_budget_ms=normalized_time,
            token_budget=normalized_tokens,
            max_nodes=normalized_nodes,
            max_edges=normalized_edges,
            depth=normalized_depth,
        )


@dataclass(frozen=True, slots=True)
class QueryBudgetResult:
    query_budget: QueryBudget
    status: QueryBudgetStatus
    returned_count: int
    scanned_count: int
    partial: bool
    clipped: bool
    exhausted: bool
    next_cursor: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        query_budget: QueryBudget,
        status: QueryBudgetStatus | str,
        returned_count: Any,
        scanned_count: Any,
        partial: bool,
        clipped: bool,
        exhausted: bool,
        next_cursor: Any = "",
        reason: Any = "",
        next_action: Any = "",
    ) -> "QueryBudgetResult":
        if not isinstance(query_budget, QueryBudget):
            raise QueryBudgetError("query_budget must be a QueryBudget")
        normalized_status = _normalize_status(status)
        normalized_returned = _normalize_int(returned_count, field_name="returned_count", allow_empty=False)
        normalized_scanned = _normalize_int(scanned_count, field_name="scanned_count", allow_empty=False)
        normalized_partial = bool(partial)
        normalized_clipped = bool(clipped)
        normalized_exhausted = bool(exhausted)
        normalized_next_cursor = _normalize_text(next_cursor, field_name="next_cursor", allow_empty=True)
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)

        if query_budget.limit and normalized_returned > query_budget.limit:
            raise QueryBudgetError("returned_count must not exceed limit")
        if normalized_scanned < normalized_returned:
            raise QueryBudgetError("scanned_count must be >= returned_count")
        if normalized_partial or normalized_clipped or normalized_exhausted:
            if not (normalized_reason or normalized_next_action):
                raise QueryBudgetError("partial, clipped, and exhausted results require reason or next_action")
        if normalized_status == QueryBudgetStatus.PARTIAL and not normalized_partial:
            raise QueryBudgetError("partial status requires partial=True")
        if normalized_status == QueryBudgetStatus.CLIPPED and not normalized_clipped:
            raise QueryBudgetError("clipped status requires clipped=True")
        if normalized_status == QueryBudgetStatus.EXHAUSTED and not normalized_exhausted:
            raise QueryBudgetError("exhausted status requires exhausted=True")
        if normalized_partial and normalized_scanned > normalized_returned and not normalized_next_cursor:
            raise QueryBudgetError("partial paged results require next_cursor")
        if normalized_next_cursor and (not normalized_partial or normalized_exhausted):
            raise QueryBudgetError("next_cursor requires a partial result that is not exhausted")
        if normalized_status in {QueryBudgetStatus.BLOCKED, QueryBudgetStatus.FAILED} and not (
            normalized_reason or normalized_next_action
        ):
            raise QueryBudgetError("blocked and failed results require reason or next_action")

        return cls(
            query_budget=query_budget,
            status=normalized_status,
            returned_count=normalized_returned,
            scanned_count=normalized_scanned,
            partial=normalized_partial,
            clipped=normalized_clipped,
            exhausted=normalized_exhausted,
            next_cursor=normalized_next_cursor,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "query_budget_id": self.query_budget.query_budget_id,
            "query_ref": self.query_budget.query_ref,
            "kind": self.query_budget.kind.value,
            "status": self.status.value,
            "returned_count": self.returned_count,
            "scanned_count": self.scanned_count,
            "partial": self.partial,
            "clipped": self.clipped,
            "exhausted": self.exhausted,
            "has_next_cursor": bool(self.next_cursor),
            "budget_flags": {
                "limit": self.query_budget.limit > 0,
                "time_budget_ms": self.query_budget.time_budget_ms > 0,
                "token_budget": self.query_budget.token_budget > 0,
                "graph_budget": any(
                    value > 0
                    for value in (self.query_budget.max_nodes, self.query_budget.max_edges, self.query_budget.depth)
                ),
            },
        }
