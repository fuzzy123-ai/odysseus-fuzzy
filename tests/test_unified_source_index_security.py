"""USI-14 synthetic security and privacy acceptance; no productive sources."""

import json

import pytest

from src.ai_lens_events import AiLensEvent
from src.memory_runtime_metrics import MemoryRuntimeMetricsRegistry
from src.unified_source_index_context import (
    ContextProjectionRequest,
    UnifiedSourceIndexContextBridge,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    UnifiedSourceIndexContractError,
    content_hash,
    locator_from_dict,
)
from src.unified_source_index_diagnostics import UnifiedSourceIndexDiagnostics
from src.unified_source_index_lexical import build_fts_expression
from src.unified_source_index_query import (
    FederatedQuery,
    FederatedQueryError,
    ProviderRegistration,
    ProviderResultItem,
    ProviderResultPage,
    ProviderStatus,
    QueryMode,
    UnifiedSourceIndexQueryPlanner,
)
from src.unified_source_index_query_contract import LexicalMatchMode
from src.unified_source_index_sqlite import (
    SQLiteUnifiedSourceIndexError,
    SQLiteUnifiedSourceIndexStore,
)


NOW = "2026-07-23T18:00:00Z"
PRIVATE = "USI14_PRIVATE_SENTINEL"


def _records(text=PRIVATE, *, owner="user:alice", classification=Classification.PRIVATE,
             policy=ContentPolicy.INLINE_LOCAL):
    source = SourceRecord(
        owner_scope=owner,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref="doc:usi14",
        classification=classification,
        content_policy=policy,
        provider_ref="fake.usi14",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="rev:usi14",
        content_hash=content_hash(text),
        version_observed_at=NOW,
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text if policy is ContentPolicy.INLINE_LOCAL else None,
    )
    return source, version, chunk


class _Provider:
    def __init__(self, provider_id, kind, items=(), error=None):
        self.provider_id = provider_id
        self.provider_kind = kind
        self.items = tuple(items)
        self.error = error

    def query(self, _request):
        if self.error:
            raise self.error
        return ProviderResultPage(self.items, snapshot_ref="snapshot.usi14")


def _item(chunk, *, snippet=""):
    return ProviderResultItem(chunk.evidence_ref(), 0.9, snippet=snippet, why_selected="synthetic")


def _page(*providers):
    return UnifiedSourceIndexQueryPlanner(
        tuple(ProviderRegistration(provider) for provider in providers)
    ).execute(FederatedQuery(
        owner_scope="user:alice",
        query_text="synthetic",
        classification_ceiling=Classification.SENSITIVE,
        modes=tuple(provider.provider_kind for provider in providers),
    ))


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def test_owner_classification_and_content_policy_negatives_fail_closed():
    sensitive, _, _ = _records(classification=Classification.SENSITIVE,
                               policy=ContentPolicy.REFERENCE_ONLY)
    with pytest.raises(UnifiedSourceIndexContractError, match="classification cannot weaken"):
        SourceVersionRecord.create(sensitive, revision_ref="rev:weaken", content_hash=content_hash("x"),
                                   version_observed_at=NOW, classification=Classification.PRIVATE)
    with pytest.raises(UnifiedSourceIndexContractError, match="content_policy cannot weaken"):
        SourceVersionRecord.create(sensitive, revision_ref="rev:weaken", content_hash=content_hash("x"),
                                   version_observed_at=NOW, content_policy=ContentPolicy.INLINE_LOCAL)

    alice, _, allowed = _records("allowed")
    bob, _, forbidden = _records(PRIVATE, owner="user:bob")
    with pytest.raises(UnifiedSourceIndexContractError, match="cross-owner"):
        SourceScope.create((alice.policy_evidence(), bob.policy_evidence()))

    page = _page(_Provider("fake.semantic", QueryMode.SEMANTIC, (_item(allowed), _item(forbidden))))
    assert page.items == ()
    assert page.outcomes[0].status is ProviderStatus.FAILED
    assert PRIVATE not in json.dumps([
        {"status": outcome.status.value, "error_code": outcome.error_code}
        for outcome in page.outcomes
    ])


@pytest.mark.parametrize("path", (
    "../secret.py", "..\\secret.py", "/etc/passwd", "~/.ssh/id", "C:\\private.py",
    "src//main.py", "src/./main.py", "src/../main.py", "src/" + "x" * 1024,
))
def test_traversal_and_malformed_locators_fail_closed(path):
    with pytest.raises(UnifiedSourceIndexContractError):
        CodeRangeLocator(path, 1, 0, 2, 0)
    with pytest.raises(UnifiedSourceIndexContractError):
        locator_from_dict({"kind": "code_range", "path": "src/a.py", "start_line": 1})
    with pytest.raises(UnifiedSourceIndexContractError):
        locator_from_dict({
            "kind": "code_range", "path": "src/a.py", "start_line": 1,
            "start_column": 0, "end_line": 2, "end_column": 0, "extra": "no",
        })


@pytest.mark.parametrize("mode", (
    LexicalMatchMode.TOKEN, LexicalMatchMode.EXACT, LexicalMatchMode.PREFIX,
))
def test_fts_hostile_operators_compile_to_literal_tokens(mode):
    expression, tokens = build_fts_expression('ordinary OR private NEAR/5 "ignored"', mode)
    assert tokens == ("ordinary", "or", "private", "near", "5", "ignored")
    if mode is LexicalMatchMode.TOKEN:
        expected = '"ordinary" AND "or" AND "private" AND "near" AND "5" AND "ignored"'
    elif mode is LexicalMatchMode.EXACT:
        expected = '"ordinary or private near 5 ignored"'
    else:
        expected = '"ordinary"* AND "or"* AND "private"* AND "near"* AND "5"* AND "ignored"*'
    assert expression == expected


def test_fts_direct_binding_and_oversized_bounds_are_owner_scoped_and_sanitized(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi14.db")
    alice, version, chunk = _records("ordinary public token")
    bob, bob_version, bob_chunk = _records(PRIVATE + " private", owner="user:bob")
    _insert(store, chunk, version, alice, bob_chunk, bob_version, bob)

    hits = store.search_chunks(owner_scope="user:alice", query="ordinary OR private")
    assert [hit.item.record.chunk_id for hit in hits] == [chunk.chunk_id]
    for hostile in ('"unterminated', "' OR 1=1 --", "x" * 1025):
        with pytest.raises(SQLiteUnifiedSourceIndexError) as failed:
            store.search_chunks(owner_scope="user:alice", query=hostile)
        assert PRIVATE not in str(failed.value)


def test_federated_snippet_and_inline_content_bounds_fail_closed_without_payload_echo():
    with pytest.raises(FederatedQueryError) as query_error:
        FederatedQuery("user:alice", PRIVATE + "x" * 512, Classification.SENSITIVE)
    assert PRIVATE not in str(query_error.value)

    _, version, _ = _records("small")
    oversized = PRIVATE + "x" * 262_144
    with pytest.raises(UnifiedSourceIndexContractError) as content_error:
        ChunkRecord.create(
            version, locator=TextRangeLocator(0, len(oversized)), extractor_profile_ref="text-v1",
            content_hash=content_hash(oversized), content=oversized,
        )
    assert PRIVATE not in str(content_error.value)

    _, _, chunk = _records("small")
    with pytest.raises(FederatedQueryError) as snippet_error:
        ProviderResultItem(chunk.evidence_ref(), 0.9, snippet=PRIVATE + "x" * 2_000,
                           why_selected="synthetic")
    assert PRIVATE not in str(snippet_error.value)


def test_private_sentinel_is_absent_from_metrics_lens_payloads_and_logs(caplog):
    _, _, chunk = _records()
    page = _page(_Provider("fake.lexical", QueryMode.LEXICAL, (_item(chunk, snippet=PRIVATE),)))
    projected = UnifiedSourceIndexContextBridge().project(page, ContextProjectionRequest(
        owner_scope="user:alice", conversation_ref="conversation-usi14", turn_ref="turn-usi14",
        created_at=NOW, model_ref="model-usi14",
    ))
    diagnostics = UnifiedSourceIndexDiagnostics(MemoryRuntimeMetricsRegistry.for_tests())
    rejected = diagnostics.record_operation(PRIVATE, outcome="success", duration_seconds=0.01)
    failed_page = _page(_Provider("fake.unavailable", QueryMode.SEMANTIC, error=RuntimeError(PRIVATE)))
    acceptance_payload = json.dumps({
        "outcomes": [{"status": item.status.value, "error_code": item.error_code} for item in page.outcomes],
        "metrics": diagnostics.snapshot().to_dict(),
        "lens": [event.to_dict() for event in projected.lens_events if isinstance(event, AiLensEvent)],
        "context": [item.to_dict() for item in projected.context_items],
    }, sort_keys=True)
    assert rejected.accepted is False
    assert failed_page.outcomes[0].status is ProviderStatus.FAILED
    assert PRIVATE not in acceptance_payload
    assert PRIVATE not in caplog.text
