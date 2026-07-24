from dataclasses import replace

import pytest

from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    RecordKind,
    RecordRef,
    TextRangeLocator,
)
from src.unified_source_index_query_contract import (
    LexicalMatchMode,
    LexicalQuery,
    MAX_QUERY_CANDIDATES,
    MAX_QUERY_RESULTS,
    MAX_RESULT_SNIPPET_CHARS,
    QueryResultItem,
    QueryResultPage,
    QueryScoreComponents,
    UnifiedSourceIndexQueryError,
)
from src.unified_source_index_stores import StoreSnapshot


def _snapshot():
    return StoreSnapshot(0, "sha256:" + "a" * 64, 0, 0)


def _item(*, stale=False, snippet="alpha", clipped=False):
    return QueryResultItem(
        owner_scope="user:alice",
        source_ref=RecordRef(RecordKind.SOURCE, "usi_source_" + "a" * 64),
        source_version_ref=RecordRef(
            RecordKind.SOURCE_VERSION,
            "usi_version_" + "b" * 64,
        ),
        chunk_ref=RecordRef(RecordKind.CHUNK, "usi_chunk_" + "c" * 64),
        locator=TextRangeLocator(0, 5),
        content_hash="sha256:" + "d" * 64,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        scores=QueryScoreComponents(0.5, 0.1, 0.6),
        snippet=snippet,
        snippet_clipped=clipped,
        stale=stale,
        indexed_at="2026-07-17T08:00:00Z",
        why_selected="lexical_exact",
    )


def test_lexical_query_is_bounded_canonical_and_cursor_independent():
    query = LexicalQuery(
        "user:alice",
        "  Alpha Beta  ",
        LexicalMatchMode.EXACT,
        Classification.PRIVATE,
        limit=10,
        max_candidates=20,
        snippet_chars=100,
        source_ids=("usi_source_" + "a" * 64,),
        stale_after="2026-07-17T07:00:00Z",
    )

    assert query.query_text == "Alpha Beta"
    assert query.fingerprint.startswith("sha256:")
    assert replace(query, cursor="opaque").fingerprint == query.fingerprint


def test_query_rejects_unbounded_or_inconsistent_budgets_and_invalid_scope():
    base = dict(
        owner_scope="user:alice",
        query_text="alpha",
        match_mode=LexicalMatchMode.TOKEN,
        classification_ceiling=Classification.PRIVATE,
    )
    with pytest.raises(UnifiedSourceIndexQueryError, match="query_text"):
        LexicalQuery(**{**base, "query_text": "\n"})
    with pytest.raises(UnifiedSourceIndexQueryError, match="max_candidates"):
        LexicalQuery(**base, limit=10, max_candidates=9)
    with pytest.raises(UnifiedSourceIndexQueryError, match="between 1"):
        LexicalQuery(**base, limit=MAX_QUERY_RESULTS + 1)
    with pytest.raises(UnifiedSourceIndexQueryError, match="between 1"):
        LexicalQuery(**base, max_candidates=MAX_QUERY_CANDIDATES + 1)
    with pytest.raises(ValueError):
        LexicalQuery(**base, source_ids=("usi_source_" + "z" * 64,))
    with pytest.raises(UnifiedSourceIndexQueryError, match="timezone"):
        LexicalQuery(**base, stale_after="2026-07-17T07:00:00")


def test_score_components_are_finite_bounded_and_exactly_additive():
    assert QueryScoreComponents(0.5, 0.1, 0.6).total == 0.6
    for values in (
        (float("nan"), 0.0, 0.0),
        (0.5, 0.3, 0.8),
        (0.5, 0.1, 0.7),
    ):
        with pytest.raises(UnifiedSourceIndexQueryError):
            QueryScoreComponents(*values)


def test_result_requires_exact_source_version_chunk_and_locator_refs():
    item = _item()

    assert item.source_ref.record_kind is RecordKind.SOURCE
    assert item.source_version_ref.record_kind is RecordKind.SOURCE_VERSION
    assert item.chunk_ref.record_kind is RecordKind.CHUNK
    assert item.locator == TextRangeLocator(0, 5)

    with pytest.raises(UnifiedSourceIndexQueryError, match="source_ref"):
        replace(item, source_ref=item.chunk_ref)
    with pytest.raises(UnifiedSourceIndexQueryError, match="snippet"):
        replace(item, snippet="x" * (MAX_RESULT_SNIPPET_CHARS + 1))
    with pytest.raises(UnifiedSourceIndexQueryError, match="content_hash"):
        replace(item, content_hash="sha256:broken")


def test_result_page_exposes_partial_clipped_and_stale_states():
    item = _item(stale=True, clipped=True)
    page = QueryResultPage(
        (item,),
        _snapshot(),
        candidate_count=3,
        policy_filtered_count=1,
        candidate_clipped=True,
        next_cursor="cursor",
    )

    assert page.partial is True
    assert page.clipped is True
    assert page.stale is True

    with pytest.raises(UnifiedSourceIndexQueryError, match="candidate_count"):
        QueryResultPage((item,), _snapshot(), candidate_count=0)
