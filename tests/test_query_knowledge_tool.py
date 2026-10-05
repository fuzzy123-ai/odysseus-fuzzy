import asyncio
import json

from src.agent_tools import ToolBlock
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_query import (
    FederatedQuery,
    ProviderRegistration,
    ProviderResultItem,
    ProviderResultPage,
    QueryMode,
    UnifiedSourceIndexQueryPlanner,
)


NOW = "2026-07-23T12:00:00Z"
OWNER_ID = "owner_" + "a" * 32
OTHER_OWNER_ID = "owner_" + "b" * 32
OWNER_SCOPE = "owner:" + OWNER_ID


class _Provider:
    provider_id = "fixture.lexical"
    provider_kind = QueryMode.LEXICAL
    supported_domains = frozenset({"code", "document", "memory", "native_knowledge"})

    def __init__(self, item):
        self.item = item
        self.requests = []

    def query(self, request):
        self.requests.append(request)
        return ProviderResultPage((self.item,), snapshot_ref="fixture.snapshot")


class _CapturingPlanner:
    def __init__(self, planner):
        self._planner = planner
        self.request = None

    def execute(self, request):
        self.request = request
        return self._planner.execute(request)


def _planner(*, policy=ContentPolicy.INLINE_LOCAL, owner="user:alice"):
    owner_scope = OWNER_SCOPE if owner == "user:alice" else "owner:" + OTHER_OWNER_ID
    source = SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref="fixture:document",
        classification=Classification.PRIVATE,
        content_policy=policy,
        provider_ref="fixture.docs",
    )
    version = SourceVersionRecord.create(
        source, revision_ref="fixture:1", content_hash=content_hash("safe snippet"),
        version_observed_at=NOW, indexed_at=NOW,
    )
    chunk = __import__("src.unified_source_index_contract", fromlist=["ChunkRecord"]).ChunkRecord.create(
        version, locator=TextRangeLocator(0, 12), extractor_profile_ref="fixture-v1",
        content_hash=content_hash("safe snippet"),
        content="safe snippet" if policy is ContentPolicy.INLINE_LOCAL else None, indexed_at=NOW,
    )
    provider = _Provider(ProviderResultItem(chunk.evidence_ref(), 0.9, snippet="safe snippet"))
    return UnifiedSourceIndexQueryPlanner((ProviderRegistration(provider),)), provider


def _call(payload, *, owner="alice"):
    from src.tool_execution import execute_tool_block
    from src.agent_tools.knowledge_tools import set_query_knowledge_owner_resolver

    set_query_knowledge_owner_resolver(
        lambda username: {
            "alice": OWNER_ID,
            "mallory": OTHER_OWNER_ID,
        }.get(username)
    )
    try:
        return asyncio.run(execute_tool_block(
            ToolBlock("query_knowledge", json.dumps(payload)), owner=owner,
        ))
    finally:
        set_query_knowledge_owner_resolver(None)


def _payload(**changes):
    value = {
        "query": "safe query", "domain": "document", "mode": "lexical",
        "scope": [], "budget": {"limit": 5, "max_candidates": 10, "time_budget_ms": 500},
        "classification": "private",
    }
    value.update(changes)
    return value


def test_canonical_owner_scoped_read_only_projection_uses_one_injected_planner():
    from src.agent_tools.knowledge_tools import set_query_knowledge_planner

    planner, provider = _planner()
    capturing_planner = _CapturingPlanner(planner)
    set_query_knowledge_planner(capturing_planner)
    try:
        description, result = _call(_payload())
    finally:
        set_query_knowledge_planner(None)

    assert description == "query_knowledge"
    assert result["exit_code"] == 0
    assert result["exact_read_required"] is True
    assert result["write_performed"] is False
    assert result["provider_spans_visible"] is False
    assert len(result["results"]) == 1
    evidence = provider.item.evidence
    assert result["results"][0] == {
        "evidence": {
            "record_id": evidence.record_id,
            "record_kind": evidence.record_kind.value,
            "source_id": evidence.source_id,
            "source_version_id": evidence.source_version_id,
            "locator": evidence.locator.to_dict(),
            "content_hash": evidence.content_hash,
            "policy_evidence_id": evidence.policy_evidence.evidence_id,
        },
        "classification": "private",
        "content_policy": "inline_local",
        "stale": False,
        "snippet_clipped": False,
        "snippet": "safe snippet",
    }
    assert "owner_scope" not in json.dumps(result)
    assert len(provider.requests) == 1
    assert provider.requests[0].owner_scope == OWNER_SCOPE
    assert provider.requests[0].query_text == "safe query"
    assert capturing_planner.request is not None
    assert capturing_planner.request.limit == 5


def test_code_document_and_memory_share_the_same_handler_and_schema_contract():
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.agent_tools.knowledge_tools import set_query_knowledge_planner
    from src.builtin_tool_catalog import build_builtin_descriptor_catalog, definitions_by_id
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_security import PLAN_MODE_READONLY_TOOLS

    schema = next(item for item in FUNCTION_TOOL_SCHEMAS if item["function"]["name"] == "query_knowledge")
    assert "query_knowledge" in TOOL_TAGS
    assert "query_knowledge" in TOOL_HANDLERS
    assert "query_knowledge" in BUILTIN_TOOL_DESCRIPTIONS
    assert "query_knowledge" in PLAN_MODE_READONLY_TOOLS
    definition = definitions_by_id()["query_knowledge"]
    descriptor = build_builtin_descriptor_catalog().resolve("query_knowledge")
    assert definition.effect_class == "read"
    assert descriptor.lifecycle.value == "deferred"
    assert descriptor.risk_level.value == "elevated"
    assert descriptor.permission.value == "owner"
    assert descriptor.default_enabled is False
    assert schema["function"]["parameters"]["additionalProperties"] is False
    assert schema["function"]["parameters"]["properties"]["domain"]["enum"] == ["code", "document", "memory", "native_knowledge"]
    planner, provider = _planner()
    set_query_knowledge_planner(planner)
    try:
        for domain in ("code", "document", "memory", "native_knowledge"):
            _, result = _call(_payload(domain=domain))
            assert result["exit_code"] == 0
            assert result["domain"] == domain
    finally:
        set_query_knowledge_planner(None)
    assert len(provider.requests) == 4


def test_invalid_foreign_or_over_budget_input_and_missing_planner_fail_closed():
    from src.agent_tools.knowledge_tools import query_knowledge, set_query_knowledge_planner

    set_query_knowledge_planner(None)
    _, unavailable = _call(_payload())
    assert unavailable == {"error": "query_knowledge_unavailable", "exit_code": 1, "content_included": False}
    cases = [
        _payload(owner_scope="user:mallory"),
        _payload(domain="other"),
        _payload(scope=["not-a-usi-source"]),
        _payload(domain=[]),
        _payload(mode={"unexpected": "shape"}),
        _payload(classification=[]),
        _payload(scope=[{"unexpected": "shape"}]),
        _payload(query="x" * 513),
        _payload(budget={"limit": 101, "max_candidates": 10, "time_budget_ms": 500}),
    ]
    for payload in cases:
        _, result = _call(payload)
        assert result == {
            "error": "query_knowledge_invalid_request",
            "exit_code": 1,
            "content_included": False,
        }
        assert "safe query" not in json.dumps(result)
        assert "mallory" not in json.dumps(result)

    oversized = asyncio.run(query_knowledge("x" * (8 * 1024 + 1), {"owner": "alice"}))
    assert oversized == {
        "error": "query_knowledge_invalid_request",
        "exit_code": 1,
        "content_included": False,
    }

    class _FailingPlanner:
        def execute(self, _query):
            raise RuntimeError("raw provider failure must not escape")

    set_query_knowledge_planner(_FailingPlanner())
    try:
        _, failed = _call(_payload())
    finally:
        set_query_knowledge_planner(None)
    assert failed == {"error": "query_knowledge_failed", "exit_code": 1, "content_included": False}


def test_foreign_evidence_and_reference_only_snippets_are_not_exposed():
    from src.agent_tools.knowledge_tools import set_query_knowledge_planner

    planner, _ = _planner(policy=ContentPolicy.REFERENCE_ONLY)
    set_query_knowledge_planner(planner)
    try:
        _, result = _call(_payload())
    finally:
        set_query_knowledge_planner(None)
    assert result["exit_code"] == 0
    assert "snippet" not in result["results"][0]
    assert result["results"][0]["evidence"]["locator"] == {
        "kind": "text_range", "start_char": 0, "end_char": 12,
    }
    assert result["results"][0]["evidence"]["content_hash"] == content_hash("safe snippet")
    assert result["results"][0]["evidence"]["policy_evidence_id"]

    foreign, _ = _planner(owner="user:mallory")
    set_query_knowledge_planner(foreign)
    try:
        _, result = _call(_payload())
    finally:
        set_query_knowledge_planner(None)
    assert result == {
        "error": "query_knowledge_failed",
        "exit_code": 1,
        "content_included": False,
    }
    assert "mallory" not in json.dumps(result)

    missing = UnifiedSourceIndexQueryPlanner(())
    set_query_knowledge_planner(missing)
    try:
        _, result = _call(_payload())
    finally:
        set_query_knowledge_planner(None)
    assert result == {
        "error": "query_knowledge_failed",
        "exit_code": 1,
        "content_included": False,
    }


def test_target_owner_resolves_through_injected_auth_and_never_uses_actor_or_json_owner():
    from src.agent_tools.knowledge_tools import (
        query_knowledge,
        set_query_knowledge_owner_resolver,
        set_query_knowledge_planner,
    )

    planner, provider = _planner()
    captured = _CapturingPlanner(planner)
    calls = []

    def resolver(username):
        calls.append(username)
        return OWNER_ID if username == "alice" else None

    set_query_knowledge_owner_resolver(resolver)
    set_query_knowledge_planner(captured)
    try:
        result = asyncio.run(query_knowledge(
            json.dumps(_payload()), {"owner": "alice", "actor": "mallory"},
        ))
        assert result["exit_code"] == 0
        assert captured.request.owner_scope == OWNER_SCOPE
        assert calls == ["alice"]

        unknown = asyncio.run(query_knowledge(
            json.dumps(_payload()), {"owner": "unknown", "actor": "alice"},
        ))
        forged = dict(_payload(), owner="alice", owner_scope=OWNER_SCOPE)
        forged_result = asyncio.run(query_knowledge(
            json.dumps(forged), {"owner": "unknown", "actor": "alice"},
        ))
        assert unknown["error"] == "query_knowledge_invalid_request"
        assert forged_result["error"] == "query_knowledge_invalid_request"
        assert len(provider.requests) == 1
    finally:
        set_query_knowledge_planner(None)
        set_query_knowledge_owner_resolver(None)


def test_missing_or_failed_owner_resolver_never_calls_planner():
    from src.agent_tools.knowledge_tools import (
        query_knowledge,
        set_query_knowledge_owner_resolver,
        set_query_knowledge_planner,
    )

    class CountingPlanner:
        calls = 0

        def execute(self, _query):
            self.calls += 1
            raise AssertionError("unresolved owner must stop before planner")

    planner = CountingPlanner()
    set_query_knowledge_planner(planner)
    set_query_knowledge_owner_resolver(None)
    try:
        no_resolver = asyncio.run(query_knowledge(
            json.dumps(_payload()), {"owner": "alice"},
        ))
        assert no_resolver["error"] == "query_knowledge_invalid_request"

        def failed_resolver(_username):
            raise RuntimeError("private resolver detail")

        set_query_knowledge_owner_resolver(failed_resolver)
        failed = asyncio.run(query_knowledge(
            json.dumps(_payload()), {"owner": "alice"},
        ))
        assert failed["error"] == "query_knowledge_invalid_request"
        assert "private resolver detail" not in json.dumps(failed)
        assert planner.calls == 0
    finally:
        set_query_knowledge_planner(None)
        set_query_knowledge_owner_resolver(None)


def test_unsupported_domain_returns_bounded_unavailable_without_provider_call():
    from src.agent_tools.knowledge_tools import set_query_knowledge_planner

    class NoDomainProvider:
        provider_id = "fixture.unbounded-domain"
        provider_kind = QueryMode.LEXICAL

        def __init__(self):
            self.requests = []

        def query(self, request):
            self.requests.append(request)
            raise AssertionError("unsupported provider must not be invoked")

    provider = NoDomainProvider()
    planner = UnifiedSourceIndexQueryPlanner((ProviderRegistration(provider),))
    set_query_knowledge_planner(planner)
    try:
        _, result = _call(_payload(mode="hybrid"))
    finally:
        set_query_knowledge_planner(None)
    assert result == {
        "error": "query_knowledge_unavailable",
        "exit_code": 1,
        "content_included": False,
    }
    assert provider.requests == []
