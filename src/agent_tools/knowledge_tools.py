"""Canonical, read-only Unified Source Index query tool boundary.

The application owns planner construction and injects it explicitly. This
module never discovers providers or opens stores, so missing integration fails
closed instead of creating a second query path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from src.unified_source_index_contract import Classification, ContentPolicy
from src.unified_source_index_query import (
    FederatedQuery,
    FederatedQueryBudget,
    FederatedQueryPage,
    MAX_QUERY_TEXT_CHARS,
    ProviderStatus,
    QueryMode,
)


_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,159}$")
_SOURCE_ID_RE = re.compile(r"^usi_source_[0-9a-f]{64}$")
_DOMAINS = frozenset({"code", "document", "memory"})
_MODES = frozenset(item.value for item in QueryMode)
_CLASSIFICATIONS = frozenset({Classification.PUBLIC.value, Classification.PRIVATE.value})
_MAX_SCOPE_IDS = 64
_MAX_LIMIT = 20
_MAX_CANDIDATES = 100
_MAX_TIME_MS = 2_000
_MAX_REQUEST_CONTENT_CHARS = 8 * 1024


class _Planner(Protocol):
    def execute(self, query: FederatedQuery) -> FederatedQueryPage: ...


_planner: _Planner | None = None


@dataclass(frozen=True, slots=True)
class QueryKnowledgeRequest:
    owner_scope: str
    query_text: str
    domain: str
    mode: QueryMode
    source_ids: tuple[str, ...]
    classification: Classification
    limit: int
    budget: FederatedQueryBudget

    def federated_query(self) -> FederatedQuery:
        return FederatedQuery(
            owner_scope=self.owner_scope,
            query_text=self.query_text,
            classification_ceiling=self.classification,
            modes=(self.mode,),
            limit=self.limit,
            source_ids=self.source_ids,
            budget=self.budget,
        )


def set_query_knowledge_planner(planner: _Planner | None) -> None:
    """Inject the sole USI planner boundary; ``None`` leaves it unavailable."""

    global _planner
    if planner is not None and not callable(getattr(planner, "execute", None)):
        raise TypeError("query knowledge planner must expose execute")
    _planner = planner


def _failure(code: str) -> dict[str, Any]:
    return {"error": code, "exit_code": 1, "content_included": False}


def _trusted_owner_scope(ctx: dict[str, Any]) -> str | None:
    owner = ctx.get("owner")
    if not isinstance(owner, str):
        return None
    value = owner.strip()
    if value.startswith("user:"):
        value = value[5:]
    return "user:" + value if _OWNER_RE.fullmatch(value) else None


def _request(content: str, ctx: dict[str, Any]) -> QueryKnowledgeRequest | None:
    if not isinstance(content, str) or len(content) > _MAX_REQUEST_CONTENT_CHARS:
        return None
    try:
        value = json.loads(content)
        if not isinstance(value, dict) or set(value) != {
            "query", "domain", "mode", "scope", "budget", "classification"
        }:
            return None
        owner_scope = _trusted_owner_scope(ctx)
        query, domain, mode = value.get("query"), value.get("domain"), value.get("mode")
        scope, classification, budget = value.get("scope"), value.get("classification"), value.get("budget")
        if (
            owner_scope is None or not isinstance(query, str) or not query.strip()
            or len(query.strip()) > MAX_QUERY_TEXT_CHARS or any(ord(char) < 32 for char in query)
            or not isinstance(domain, str) or domain not in _DOMAINS
            or not isinstance(mode, str) or mode not in _MODES
            or not isinstance(classification, str) or classification not in _CLASSIFICATIONS
            or not isinstance(scope, list) or len(scope) > _MAX_SCOPE_IDS
            or not all(isinstance(item, str) and _SOURCE_ID_RE.fullmatch(item) for item in scope)
            or len(set(scope)) != len(scope)
            or not isinstance(budget, dict) or set(budget) != {"limit", "max_candidates", "time_budget_ms"}
        ):
            return None
        limit, candidates, time_budget_ms = budget.get("limit"), budget.get("max_candidates"), budget.get("time_budget_ms")
        if (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIMIT
            or isinstance(candidates, bool) or not isinstance(candidates, int) or not limit <= candidates <= _MAX_CANDIDATES
            or isinstance(time_budget_ms, bool) or not isinstance(time_budget_ms, int) or not 1 <= time_budget_ms <= _MAX_TIME_MS
        ):
            return None
        query_mode = QueryMode(mode)
        return QueryKnowledgeRequest(
            owner_scope=owner_scope, query_text=query.strip(), domain=domain, mode=query_mode,
            source_ids=tuple(scope), classification=Classification(classification), limit=limit,
            budget=FederatedQueryBudget(
                max_providers=2 if query_mode is QueryMode.HYBRID else 1,
                max_candidates_per_provider=candidates, max_total_candidates=candidates,
                time_budget_ms=time_budget_ms, per_provider_timeout_ms=min(time_budget_ms, 750),
            ),
        )
    except (TypeError, ValueError):
        return None


def _result_item(item: Any, owner_scope: str) -> dict[str, Any] | None:
    evidence = getattr(item, "evidence", None)
    policy = getattr(evidence, "policy_evidence", None)
    if evidence is None or policy is None or policy.owner_scope != owner_scope:
        return None
    result = {
        "evidence": {
            "record_id": evidence.record_id,
            "record_kind": evidence.record_kind.value,
            "source_id": evidence.source_id,
            "source_version_id": evidence.source_version_id,
            "locator": evidence.locator.to_dict() if evidence.locator is not None else None,
            "content_hash": evidence.content_hash,
            "policy_evidence_id": policy.evidence_id,
        },
        "classification": policy.classification.value, "content_policy": policy.content_policy.value,
        "stale": bool(getattr(item, "stale", False)),
        "snippet_clipped": bool(getattr(item, "snippet_clipped", False)),
    }
    if policy.content_policy is ContentPolicy.INLINE_LOCAL:
        snippet = getattr(item, "snippet", "")
        if not isinstance(snippet, str) or len(snippet) > 2_000:
            return None
        result["snippet"] = snippet
    return result


async def query_knowledge(content: str, ctx: dict[str, Any]) -> dict[str, Any]:
    request = _request(content, ctx)
    if request is None:
        return _failure("query_knowledge_invalid_request")
    planner = _planner
    if planner is None:
        return _failure("query_knowledge_unavailable")
    try:
        page = planner.execute(request.federated_query())
        if not isinstance(page, FederatedQueryPage):
            return _failure("query_knowledge_failed")
        completed = any(
            outcome.status in {ProviderStatus.COMPLETED, ProviderStatus.PARTIAL}
            for outcome in page.outcomes
        )
        contract_failure = any(
            outcome.status is ProviderStatus.FAILED
            and outcome.error_code == "federated_query_error"
            for outcome in page.outcomes
        )
        if not completed or contract_failure:
            return _failure("query_knowledge_failed")
        results = [_result_item(item, request.owner_scope) for item in page.items]
        if any(item is None for item in results):
            return _failure("query_knowledge_failed")
        return {
            "schema": "odysseus.query_knowledge.result.v1", "domain": request.domain,
            "mode": request.mode.value, "result_count": len(results), "results": results,
            "partial": page.partial, "clipped": page.clipped, "exact_read_required": True,
            "write_performed": False, "provider_spans_visible": False, "exit_code": 0,
        }
    except Exception:
        return _failure("query_knowledge_failed")


class QueryKnowledgeTool:
    async def execute(self, content: str, ctx: dict[str, Any]) -> dict[str, Any]:
        return await query_knowledge(content, ctx)
