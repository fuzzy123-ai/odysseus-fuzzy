import json

import pytest

from src.ai_lens_events import AiLensEventType, AiLensStatus
from src.unified_source_index_context import (
    ContextProjectionBudget,
    ContextProjectionRequest,
    UnifiedSourceIndexContextBridge,
    UnifiedSourceIndexContextError,
)
from src.unified_source_index_contract import (
    ChunkRecord,
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
    ProviderStatus,
    QueryMode,
    UnifiedSourceIndexQueryPlanner,
)


NOW = "2026-07-17T08:00:00Z"


def _chunk(
    canonical_ref,
    text,
    *,
    owner="user:alice",
    classification=Classification.PRIVATE,
    start=0,
):
    source = SourceRecord(
        owner_scope=owner,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref=canonical_ref,
        classification=classification,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="fake.docs",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="rev:1",
        content_hash=content_hash(text),
        version_observed_at=NOW,
        indexed_at=NOW,
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text,
        indexed_at=NOW,
    )
    return source, version, chunk


class _Provider:
    provider_id = "fake.lexical"
    provider_kind = QueryMode.LEXICAL

    def __init__(self, items, *, clipped=False):
        self.items = tuple(items)
        self.clipped = clipped

    def query(self, request):
        return ProviderResultPage(
            self.items[: request.limit],
            clipped=self.clipped,
            snapshot_ref="snapshot.lexical",
        )


def _result(chunk, score=0.9, *, snippet="provider snippet", stale=False):
    return ProviderResultItem(
        evidence=chunk.evidence_ref(),
        score=score,
        snippet=snippet,
        stale=stale,
        why_selected="lexical_match",
    )


def _page(*items, owner="user:alice", hybrid=False, clipped=False):
    provider = _Provider(items, clipped=clipped)
    planner = UnifiedSourceIndexQueryPlanner((ProviderRegistration(provider),))
    return planner.execute(
        FederatedQuery(
            owner_scope=owner,
            query_text="odyssey",
            classification_ceiling=Classification.UNKNOWN,
            modes=(QueryMode.HYBRID,) if hybrid else (QueryMode.LEXICAL,),
            limit=20,
        )
    )


def _request(**changes):
    values = dict(
        owner_scope="user:alice",
        conversation_ref="conversation-001",
        turn_ref="turn-001",
        created_at=NOW,
        model_ref="model-001",
    )
    values.update(changes)
    return ContextProjectionRequest(**values)


def test_projection_keeps_exact_version_and_locator_without_copying_snippet_content():
    source, version, chunk = _chunk("doc:exact", "odyssey exact evidence")
    private_snippet = "NEVER_COPY_THIS_PRIVATE_SNIPPET"
    page = _page(_result(chunk, snippet=private_snippet))

    projected = UnifiedSourceIndexContextBridge().project(page, _request())

    assert len(projected.context_items) == 1
    item = projected.context_items[0]
    assert item.selection_state == "included"
    assert item.source_ref.ref_id == source.source_id
    assert item.source_ref.section_ref == chunk.chunk_id
    assert item.source_revision_ref == version.source_version_id
    assert item.redacted_preview is None
    assert projected.answer_pack.complete is True
    assert len(projected.provenance) == 1
    exact = projected.provenance[0]
    assert exact.evidence == chunk.evidence_ref()
    assert exact.source_version_ref.record_id == version.source_version_id
    assert exact.locator == chunk.locator
    assert exact.provenance_id in item.why_selected.evidence_refs

    diagnostics = json.dumps(
        {
            "item": item.to_dict(),
            "pack": projected.answer_pack.to_dict(),
            "events": [event.to_dict() for event in projected.lens_events],
        },
        sort_keys=True,
    )
    assert private_snippet not in diagnostics
    assert exact.to_exact_read_dict()["evidence"]["locator"] == chunk.locator.to_dict()


def test_item_and_token_budget_clips_context_and_emits_honest_pack_counts():
    _, _, first = _chunk("doc:first", "odyssey " + "a" * 12, start=0)
    _, _, second = _chunk("doc:second", "odyssey " + "b" * 12, start=100)
    page = _page(_result(first, 0.9), _result(second, 0.8))
    budget = ContextProjectionBudget(
        max_context_items=2,
        max_included_items=1,
        max_tokens=100,
        max_tokens_per_item=100,
    )

    projected = UnifiedSourceIndexContextBridge().project(
        page,
        _request(budget=budget),
    )

    assert [item.selection_state for item in projected.context_items] == [
        "included",
        "clipped",
    ]
    assert projected.answer_pack.included_count == 1
    assert projected.answer_pack.clipped_count == 1
    assert projected.answer_pack.truncated is True
    assert projected.answer_pack.complete is False
    assert len(projected.provenance) == 1
    assert [event.status for event in projected.lens_events] == [
        AiLensStatus.SUCCEEDED,
        AiLensStatus.SKIPPED,
        AiLensStatus.PARTIAL,
    ]


def test_stale_result_is_excluded_by_default_and_can_be_explicitly_included():
    _, _, chunk = _chunk("doc:stale", "odyssey stale")
    page = _page(_result(chunk, stale=True))
    bridge = UnifiedSourceIndexContextBridge()

    excluded = bridge.project(page, _request())
    included = bridge.project(page, _request(allow_stale=True))

    assert excluded.context_items[0].selection_state == "excluded"
    assert excluded.context_items[0].freshness.state == "stale"
    assert excluded.answer_pack.excluded_count == 1
    assert excluded.answer_pack.stale_count == 1
    assert excluded.provenance == ()
    assert excluded.lens_events[0].event_type is AiLensEventType.CONTEXT_ITEM_EXCLUDED
    assert excluded.lens_events[0].status is AiLensStatus.SKIPPED
    assert included.context_items[0].selection_state == "included"
    assert included.provenance[0].evidence == chunk.evidence_ref()
    assert included.lens_events[0].status is AiLensStatus.WARNING


def test_normal_route_blocks_sensitive_context_while_secure_local_route_can_include_it():
    _, _, chunk = _chunk(
        "doc:sensitive",
        "odyssey sensitive",
        classification=Classification.SENSITIVE,
    )
    page = _page(_result(chunk))
    bridge = UnifiedSourceIndexContextBridge()

    normal = bridge.project(page, _request())
    secure = bridge.project(
        page,
        _request(model_locality="local", security_mode="secure"),
    )

    assert normal.context_items[0].selection_state == "blocked"
    assert normal.context_items[0].redaction_state == "blocked"
    assert normal.context_items[0].summary is None
    assert normal.provenance == ()
    assert normal.lens_events[0].status is AiLensStatus.BLOCKED
    assert secure.context_items[0].selection_state == "included"
    assert secure.context_items[0].redaction_state == "metadata_only"
    assert secure.provenance[0].evidence == chunk.evidence_ref()


def test_owner_mismatch_fails_closed_before_context_or_lens_payload_is_created():
    _, _, bob_chunk = _chunk("doc:bob", "odyssey bob", owner="user:bob")
    bob_page = _page(_result(bob_chunk), owner="user:bob")

    with pytest.raises(UnifiedSourceIndexContextError, match="owner scope"):
        UnifiedSourceIndexContextBridge().project(bob_page, _request())


def test_partial_provider_page_is_named_in_pack_and_lens_pack_event():
    _, _, chunk = _chunk("doc:partial", "odyssey partial")
    page = _page(_result(chunk), hybrid=True)

    projected = UnifiedSourceIndexContextBridge().project(page, _request())

    assert page.partial is True
    assert projected.answer_pack.complete is False
    assert projected.answer_pack.truncated is False
    assert projected.answer_pack.missing_expected_source_types == (
        "semantic",
        "symbol",
        "graph",
        "timeline",
    )
    assert projected.answer_pack.review.required is True
    assert projected.lens_events[-1].status is AiLensStatus.PARTIAL
    assert projected.lens_events[-1].event_type is AiLensEventType.CONTEXT_PACK_COMPOSED


def test_context_item_output_cap_reports_unrepresented_results_without_losing_included_provenance():
    chunks = [
        _chunk(f"doc:{index}", f"odyssey item {index}", start=index * 100)[2]
        for index in range(3)
    ]
    page = _page(*(_result(chunk, 0.9 - index / 10) for index, chunk in enumerate(chunks)))
    budget = ContextProjectionBudget(
        max_context_items=2,
        max_included_items=2,
        max_tokens=100,
        max_tokens_per_item=100,
    )

    projected = UnifiedSourceIndexContextBridge().project(page, _request(budget=budget))

    assert projected.represented_count == 2
    assert projected.unrepresented_count == 1
    assert len(projected.provenance) == 2
    assert projected.answer_pack.truncated is True
    assert projected.partial is True


def test_invalid_secure_route_and_unbounded_budget_fail_before_projection():
    with pytest.raises(UnifiedSourceIndexContextError, match="local model"):
        _request(security_mode="secure", model_locality="api")
    with pytest.raises(UnifiedSourceIndexContextError, match="max_context_items"):
        ContextProjectionBudget(max_context_items=65)
    with pytest.raises(UnifiedSourceIndexContextError, match="UTC"):
        _request(created_at="2026-07-17T10:00:00+02:00")


def test_provider_clipping_remains_partial_in_context_projection():
    _, _, chunk = _chunk("doc:clipped", "odyssey clipped")
    page = _page(_result(chunk), clipped=True)

    projected = UnifiedSourceIndexContextBridge().project(page, _request())

    assert page.outcomes[0].status is ProviderStatus.PARTIAL
    assert page.clipped is True
    assert projected.answer_pack.truncated is True
    assert projected.answer_pack.complete is False
    assert projected.partial is True
