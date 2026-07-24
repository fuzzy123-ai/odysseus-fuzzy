from dataclasses import replace

import pytest

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
from src.unified_source_index_lexical import (
    LexicalRetrievalError,
    LexicalSearchStore,
    UnifiedSourceIndexLexicalRetriever,
    build_fts_expression,
)
from src.unified_source_index_query_contract import (
    LexicalMatchMode,
    LexicalQuery,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore


NOW = "2026-07-17T08:00:00Z"


def _source(
    canonical_ref,
    *,
    owner="user:alice",
    classification=Classification.PRIVATE,
):
    return SourceRecord(
        owner_scope=owner,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref=canonical_ref,
        classification=classification,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="fake.docs",
    )


def _version(source, revision):
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(revision),
        version_observed_at=NOW,
        indexed_at=NOW,
    )


def _chunk(version, text, start=0, *, indexed_at=NOW):
    return ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text,
        indexed_at=indexed_at,
    )


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def _query(text, mode=LexicalMatchMode.TOKEN, **changes):
    values = dict(
        owner_scope="user:alice",
        query_text=text,
        match_mode=mode,
        classification_ceiling=Classification.SENSITIVE,
        limit=20,
        max_candidates=100,
        snippet_chars=100,
    )
    values.update(changes)
    return LexicalQuery(**values)


def test_exact_token_and_prefix_modes_use_fts_and_return_exact_refs(tmp_path):
    source = _source("doc:alpha")
    version = _version(source, "rev:1")
    phrase = _chunk(version, "alpha beta phrase", 0)
    separated = _chunk(version, "alpha middle beta", 100)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, separated, phrase, version, source)
    retriever = UnifiedSourceIndexLexicalRetriever(store)

    exact = retriever.search(_query("alpha beta", LexicalMatchMode.EXACT))
    token = retriever.search(_query("alpha beta", LexicalMatchMode.TOKEN))
    prefix = retriever.search(_query("alph bet", LexicalMatchMode.PREFIX))

    assert [item.chunk_ref.record_id for item in exact.items] == [phrase.chunk_id]
    assert exact.items[0].scores.exact_bonus == 0.1
    assert {item.chunk_ref.record_id for item in token.items} == {
        phrase.chunk_id,
        separated.chunk_id,
    }
    assert {item.chunk_ref.record_id for item in prefix.items} == {
        phrase.chunk_id,
        separated.chunk_id,
    }
    item = exact.items[0]
    assert item.source_ref.record_id == source.source_id
    assert item.source_version_ref.record_id == version.source_version_id
    assert item.locator == phrase.locator
    assert item.content_hash == phrase.content_hash


def test_fts_operator_text_is_compiled_as_literal_tokens_not_query_syntax(tmp_path):
    source = _source("doc:operators")
    version = _version(source, "rev:1")
    broad_alpha = _chunk(version, "alpha only", 0)
    broad_private = _chunk(version, "private only", 100)
    literal = _chunk(version, "alpha OR private", 200)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, broad_alpha, broad_private, literal, version, source)
    retriever = UnifiedSourceIndexLexicalRetriever(store)

    expression, tokens = build_fts_expression(
        'alpha OR private " ) NOT',
        LexicalMatchMode.TOKEN,
    )
    exact = retriever.search(_query("alpha OR private", LexicalMatchMode.EXACT))

    assert tokens == ("alpha", "or", "private", "not")
    assert expression == '"alpha" AND "or" AND "private" AND "not"'
    assert [item.chunk_ref.record_id for item in exact.items] == [literal.chunk_id]
    with pytest.raises(LexicalRetrievalError, match="token count"):
        build_fts_expression("!!!", LexicalMatchMode.TOKEN)


def test_owner_source_scope_and_classification_ceiling_filter_before_results(tmp_path):
    alice_private_source = _source("doc:alice-private")
    alice_private_version = _version(alice_private_source, "rev:private")
    alice_private = _chunk(alice_private_version, "odyssey private")
    alice_sensitive_source = _source(
        "doc:alice-sensitive",
        classification=Classification.SENSITIVE,
    )
    alice_sensitive_version = _version(alice_sensitive_source, "rev:sensitive")
    alice_sensitive = _chunk(alice_sensitive_version, "odyssey sensitive")
    bob_source = _source("doc:bob", owner="user:bob")
    bob_version = _version(bob_source, "rev:bob")
    bob_chunk = _chunk(bob_version, "odyssey bob")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(
        store,
        alice_private,
        alice_private_version,
        alice_private_source,
        alice_sensitive,
        alice_sensitive_version,
        alice_sensitive_source,
        bob_chunk,
        bob_version,
        bob_source,
    )
    retriever = UnifiedSourceIndexLexicalRetriever(store)

    private_only = retriever.search(
        _query("odyssey", classification_ceiling=Classification.PRIVATE)
    )
    sensitive_scope = retriever.search(
        _query(
            "odyssey",
            source_ids=(alice_sensitive_source.source_id,),
        )
    )
    bob = retriever.search(
        _query("odyssey", owner_scope="user:bob")
    )

    assert [item.chunk_ref.record_id for item in private_only.items] == [
        alice_private.chunk_id
    ]
    assert private_only.policy_filtered_count == 1
    assert private_only.partial is True
    assert [item.chunk_ref.record_id for item in sensitive_scope.items] == [
        alice_sensitive.chunk_id
    ]
    assert [item.chunk_ref.record_id for item in bob.items] == [bob_chunk.chunk_id]
    assert all(item.owner_scope == "user:bob" for item in bob.items)


def test_stable_tie_break_cursor_is_snapshot_and_query_bound(tmp_path):
    source = _source("doc:paging")
    version = _version(source, "rev:1")
    chunks = tuple(_chunk(version, "same lexical text", start) for start in (0, 100, 200))
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, *chunks, version, source)
    retriever = UnifiedSourceIndexLexicalRetriever(store)
    query = _query("same lexical", limit=2, max_candidates=10)

    first = retriever.search(query)
    second = retriever.search(replace(query, cursor=first.next_cursor))

    ids = [item.chunk_ref.record_id for item in (*first.items, *second.items)]
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert ids == sorted(chunk.chunk_id for chunk in chunks)

    with pytest.raises(LexicalRetrievalError, match="another snapshot or query"):
        retriever.search(
            replace(
                query,
                query_text="different",
                cursor=first.next_cursor,
            )
        )

    extra_source = _source("doc:extra")
    extra_version = _version(extra_source, "rev:extra")
    extra_chunk = _chunk(extra_version, "same lexical extra")
    _insert(store, extra_chunk, extra_version, extra_source)
    with pytest.raises(LexicalRetrievalError, match="another snapshot or query"):
        retriever.search(replace(query, cursor=first.next_cursor))


def test_snippet_stale_and_candidate_clipping_states_are_explicit_and_bounded(tmp_path):
    source = _source("doc:bounds")
    version = _version(source, "rev:1")
    chunks = tuple(
        _chunk(
            version,
            "prefix " + "x" * 200 + f" odyssey {index}",
            index * 1_000,
            indexed_at="2026-07-16T08:00:00Z",
        )
        for index in range(4)
    )
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, *chunks, version, source)

    class CapturingStore:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.limits = []

        def current_snapshot(self):
            return self.wrapped.current_snapshot()

        def search_chunks(self, *, owner_scope, query, limit=20):
            self.limits.append(limit)
            return self.wrapped.search_chunks(
                owner_scope=owner_scope,
                query=query,
                limit=limit,
            )

    capturing = CapturingStore(store)
    assert isinstance(capturing, LexicalSearchStore)
    page = UnifiedSourceIndexLexicalRetriever(capturing).search(
        _query(
            "odyssey",
            limit=1,
            max_candidates=2,
            snippet_chars=40,
            stale_after="2026-07-17T00:00:00Z",
        )
    )

    assert capturing.limits == [3]
    assert page.candidate_count == 2
    assert page.candidate_clipped is True
    assert page.partial is True
    assert page.clipped is True
    assert page.stale is True
    assert len(page.items[0].snippet) <= 40
    assert page.items[0].snippet_clipped is True


def test_snapshot_change_during_fts_query_fails_closed(tmp_path):
    source = _source("doc:race")
    version = _version(source, "rev:1")
    chunk = _chunk(version, "race lexical")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, chunk, version, source)

    class ChangingStore:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.changed = False

        def current_snapshot(self):
            return self.wrapped.current_snapshot()

        def search_chunks(self, *, owner_scope, query, limit=20):
            hits = self.wrapped.search_chunks(
                owner_scope=owner_scope,
                query=query,
                limit=limit,
            )
            if not self.changed:
                self.changed = True
                extra_source = _source("doc:race-extra")
                extra_version = _version(extra_source, "rev:extra")
                extra_chunk = _chunk(extra_version, "race lexical extra")
                _insert(self.wrapped, extra_chunk, extra_version, extra_source)
            return hits

    with pytest.raises(LexicalRetrievalError, match="snapshot changed"):
        UnifiedSourceIndexLexicalRetriever(ChangingStore(store)).search(
            _query("race")
        )
