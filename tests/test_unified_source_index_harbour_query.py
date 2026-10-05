from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    RecordKind,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_harbour_query import (
    HarbourKnowledgeQueryGuard,
    HarbourKnowledgeQueryGuardError,
)
from src.unified_source_index_lexical import (
    LexicalRetrievalError,
    UnifiedSourceIndexLexicalRetriever,
)
import src.unified_source_index_lexical as lexical_module
from src.unified_source_index_query_contract import LexicalMatchMode, LexicalQuery
from src.unified_source_index_sources.knowledge import project_harbour_knowledge_index_records
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import TombstoneRecord


OWNER = "owner_" + "a" * 32
AUTH = "sha256:" + "a" * 64
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
TEXT = "harbour lifecycle lexical sample"


def _fixture(tmp_path, *, chunks=1):
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="query-test", key_id="query-key", secret=b"k" * 32,
        clock=lambda: NOW,
    )
    principal = issuer.issue(
        subject_id=OWNER, owner_id=OWNER, auth_context_digest=AUTH,
        ttl_seconds=3600, nonce="query-test-nonce",
    )
    harbour = HarbourKnowledgeStore(
        tmp_path / "harbour.sqlite3", trusted_issuers=(issuer,), clock=lambda: NOW,
    )
    receipt = harbour.create_object(
        principal,
        owner_id=OWNER,
        knowledge_id="query-note",
        knowledge_kind="note",
        payload_bytes=TEXT.encode("utf-8"),
        payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain",
        classification="private",
        idempotency_key="query-create-001",
    )
    snapshot = harbour.capture_index_snapshot(principal, owner_id=OWNER)
    owner_scope = "owner:" + OWNER
    projection = project_harbour_knowledge_index_records(owner_scope, snapshot.records[0])
    usi = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.sqlite3")
    records = [projection.source, projection.source_version]
    for index in range(chunks):
        start = index * 100
        records.append(ChunkRecord.create(
            projection.source_version,
            locator=TextRangeLocator(start, start + len(TEXT)),
            extractor_profile_ref="text-v1",
            content_hash=content_hash(TEXT),
            content=TEXT,
            indexed_at="2026-10-01T00:00:00Z",
        ))
    write = usi.begin_write(usi.current_snapshot())
    for record in records:
        write.put(record)
    write.commit()
    guard = HarbourKnowledgeQueryGuard(
        store=harbour,
        principal=principal,
        owner_id=OWNER,
        owner_scope=owner_scope,
        classification_ceiling=Classification.SENSITIVE,
    )
    return issuer, principal, receipt, harbour, usi, guard, owner_scope


def _query(owner_scope, **changes):
    values = dict(
        owner_scope=owner_scope,
        query_text="harbour lifecycle",
        match_mode=LexicalMatchMode.TOKEN,
        classification_ceiling=Classification.SENSITIVE,
        limit=1,
        max_candidates=10,
        snippet_chars=80,
    )
    values.update(changes)
    return LexicalQuery(**values)


def test_guarded_page_captures_once_pins_one_read_and_checks_both_parents(
    tmp_path, monkeypatch,
):
    _, _, _, harbour, usi, guard, owner_scope = _fixture(tmp_path)
    counts = {"capture": 0, "get": []}
    original_capture = harbour.capture_index_snapshot
    original_begin_read = usi.begin_read

    def forbidden_payload(*args, **kwargs):
        raise AssertionError("guard must not read Harbour payloads")

    def capture(*args, **kwargs):
        counts["capture"] += 1
        return original_capture(*args, **kwargs)

    class CountingRead:
        def __init__(self, read):
            self._read = read

        @property
        def snapshot(self):
            return self._read.snapshot

        @property
        def closed(self):
            return self._read.closed

        def get(self, kind, record_id, *, owner_scope, include_tombstone=False):
            counts["get"].append((kind, include_tombstone))
            return self._read.get(
                kind, record_id, owner_scope=owner_scope,
                include_tombstone=include_tombstone,
            )

        def list_records(self, kind, *, owner_scope, limit, cursor="", include_tombstones=False):
            return self._read.list_records(
                kind, owner_scope=owner_scope, limit=limit, cursor=cursor,
                include_tombstones=include_tombstones,
            )

        def close(self):
            return self._read.close()

    monkeypatch.setattr(harbour, "capture_index_snapshot", capture)
    monkeypatch.setattr(harbour, "read_payload_at_generation", forbidden_payload)
    monkeypatch.setattr(usi, "begin_read", lambda snapshot=None: CountingRead(original_begin_read(snapshot)))
    original_snippet = lexical_module._snippet

    def snippet_after_parent_reads(content, tokens, max_chars):
        assert len(counts["get"]) == 2
        return original_snippet(content, tokens, max_chars)

    monkeypatch.setattr(lexical_module, "_snippet", snippet_after_parent_reads)
    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    page = retriever.search(_query(owner_scope))
    assert len(page.items) == 1
    assert counts["capture"] == 1
    assert counts["get"] == [
        (RecordKind.SOURCE, True), (RecordKind.SOURCE_VERSION, True),
    ]


def test_guarded_cursor_is_stable_and_rejects_harbour_lifecycle_change(tmp_path):
    _, principal, receipt, harbour, usi, guard, owner_scope = _fixture(tmp_path, chunks=2)
    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    query = _query(owner_scope)
    first = retriever.search(query)
    assert first.next_cursor
    other_domain_query = replace(query, domain="document")
    with pytest.raises(LexicalRetrievalError, match="cursor"):
        retriever.search(replace(other_domain_query, cursor=first.next_cursor))
    wrong_key = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"w" * 32,
    )
    with pytest.raises(LexicalRetrievalError, match="cursor"):
        wrong_key.search(replace(query, cursor=first.next_cursor))
    second = retriever.search(replace(query, cursor=first.next_cursor))
    assert len(second.items) == 1
    assert second.items[0].chunk_ref.record_id != first.items[0].chunk_ref.record_id

    tombstone = harbour.tombstone(
        principal,
        owner_id=OWNER,
        knowledge_id="query-note",
        expected_head=receipt.after_head,
        idempotency_key="query-tombstone-001",
    )
    with pytest.raises(LexicalRetrievalError, match="lifecycle boundary capture failed"):
        retriever.search(replace(query, cursor=first.next_cursor))
    filtered = retriever.search(query)
    assert filtered.items == ()
    assert filtered.candidate_count == 2
    assert filtered.policy_filtered_count == 2
    assert filtered.next_cursor == ""
    assert filtered.partial is True
    assert tombstone.after_head.state == "tombstoned"


def test_explicit_domains_filter_validated_parents_and_native_requires_active_projection(tmp_path):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    write = usi.begin_write(usi.current_snapshot())
    expected = {}
    expected_sources = {}
    for kind in (SourceKind.CODE, SourceKind.DOCUMENT, SourceKind.MEMORY, SourceKind.OTHER):
        source = SourceRecord(
            owner_scope=owner_scope,
            source_kind=kind,
            canonical_ref=f"fixture:{kind.value}",
            classification=Classification.PRIVATE,
            content_policy=ContentPolicy.INLINE_LOCAL,
            provider_ref="fixture.untrusted-other" if kind is SourceKind.OTHER else "fixture.domain",
        )
        version = SourceVersionRecord.create(
            source,
            revision_ref="fixture:1",
            content_hash=content_hash("harbour lifecycle domain sample"),
            version_observed_at="2026-10-01T00:00:00Z",
            indexed_at="2026-10-01T00:00:00Z",
        )
        chunk = ChunkRecord.create(
            version,
            locator=TextRangeLocator(0, len("harbour lifecycle domain sample")),
            extractor_profile_ref="fixture-v1",
            content_hash=content_hash("harbour lifecycle domain sample"),
            content="harbour lifecycle domain sample",
            indexed_at="2026-10-01T00:00:00Z",
        )
        write.put(source)
        write.put(version)
        write.put(chunk)
        expected[kind] = chunk.chunk_id
        expected_sources[kind] = source.source_id
    write.commit()

    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"d" * 32,
    )
    for domain, kind in (
        ("code", SourceKind.CODE),
        ("document", SourceKind.DOCUMENT),
        ("memory", SourceKind.MEMORY),
    ):
        page = retriever.search(_query(owner_scope, query_text="harbour lifecycle", domain=domain, limit=10))
        assert {item.chunk_ref.record_id for item in page.items} == {expected[kind]}
        intersection = retriever.search(_query(
            owner_scope,
            query_text="harbour lifecycle",
            domain=domain,
            source_ids=(expected_sources[SourceKind.DOCUMENT],),
            limit=10,
        ))
        if kind is SourceKind.DOCUMENT:
            assert {item.chunk_ref.record_id for item in intersection.items} == {expected[kind]}
        else:
            assert intersection.items == ()
    native = retriever.search(_query(
        owner_scope, query_text="harbour lifecycle", domain="native_knowledge", limit=10,
    ))
    assert len(native.items) == 1
    assert native.items[0].chunk_ref.record_id not in expected.values()


def test_guarded_constructor_and_cursor_reject_invalid_key_and_legacy_stays_available(
    tmp_path,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    with pytest.raises(LexicalRetrievalError):
        UnifiedSourceIndexLexicalRetriever(usi, lifecycle_guard=guard)
    with pytest.raises(LexicalRetrievalError):
        UnifiedSourceIndexLexicalRetriever(
            usi, lifecycle_guard=guard, cursor_signing_key=bytearray(b"c" * 32),
        )
    legacy = UnifiedSourceIndexLexicalRetriever(usi)
    assert len(legacy.search(_query(owner_scope)).items) == 1


def test_guarded_cursor_rejects_forged_duplicate_and_noncanonical_payloads(tmp_path):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path, chunks=2)
    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    query = _query(owner_scope)
    cursor = retriever.search(query).next_cursor
    assert cursor

    forged = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
    duplicate = cursor.replace('"mac":', '"mac":"' + "0" * 64 + '","mac":', 1)
    for malformed in (forged, cursor + " ", duplicate):
        with pytest.raises(LexicalRetrievalError, match="cursor"):
            retriever.search(replace(query, cursor=malformed))


@pytest.mark.parametrize("parent_kind", ["missing", "tombstoned"])
@pytest.mark.parametrize("record_kind", [RecordKind.SOURCE, RecordKind.SOURCE_VERSION])
def test_guarded_page_filters_missing_or_tombstoned_harbour_parents(
    tmp_path, monkeypatch, record_kind, parent_kind,
):
    _, _, _, harbour, usi, guard, owner_scope = _fixture(tmp_path)
    snapshot = usi.current_snapshot()
    read = usi.begin_read(snapshot)
    chunk_hit = usi.search_chunks(
        owner_scope=owner_scope, query="harbour lifecycle", limit=1,
    )[0]
    chunk = chunk_hit.item.record
    record_id = chunk.source_id if record_kind is RecordKind.SOURCE else chunk.source_version_id
    parent_item = read.get(
        record_kind, record_id,
        owner_scope=owner_scope, include_tombstone=True,
    )
    read.close()

    original_begin_read = usi.begin_read

    class MissingOrTombstonedParentRead:
        def __init__(self, inner):
            self._inner = inner

        @property
        def snapshot(self):
            return self._inner.snapshot

        @property
        def closed(self):
            return self._inner.closed

        def get(self, kind, requested_id, **kwargs):
            if kind is record_kind and requested_id == record_id:
                if parent_kind == "missing":
                    return None
                return TombstoneRecord(
                    record_kind, record_id, owner_scope,
                    parent_item.revision, parent_item.revision + 1,
                    "fixture-lifecycle-tombstone",
                )
            return self._inner.get(kind, requested_id, **kwargs)

        def list_records(self, *args, **kwargs):
            return self._inner.list_records(*args, **kwargs)

        def close(self):
            return self._inner.close()

    monkeypatch.setattr(
        usi, "begin_read",
        lambda snapshot=None: MissingOrTombstonedParentRead(original_begin_read(snapshot)),
    )

    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    page = retriever.search(_query(owner_scope))
    assert page.items == ()
    assert page.policy_filtered_count == 1
    assert page.next_cursor == ""


def test_guarded_all_filtered_clipped_page_has_truthful_counts_and_no_cursor(
    tmp_path, monkeypatch,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path, chunks=2)
    snapshot = usi.current_snapshot()
    read = usi.begin_read(snapshot)
    chunks = usi.search_chunks(
        owner_scope=owner_scope, query="harbour lifecycle", limit=2,
    )
    source_item = read.get(
        RecordKind.SOURCE, chunks[0].item.record.source_id,
        owner_scope=owner_scope, include_tombstone=True,
    )
    read.close()
    original_begin_read = usi.begin_read
    source_id = chunks[0].item.record.source_id

    class TombstonedSourceRead:
        def __init__(self, inner):
            self._inner = inner

        @property
        def snapshot(self):
            return self._inner.snapshot

        @property
        def closed(self):
            return self._inner.closed

        def get(self, kind, record_id, **kwargs):
            if kind is RecordKind.SOURCE and record_id == source_id:
                return TombstoneRecord(
                    RecordKind.SOURCE, source_id, owner_scope,
                    source_item.revision, source_item.revision + 1,
                    "fixture-filtered-page",
                )
            return self._inner.get(kind, record_id, **kwargs)

        def list_records(self, *args, **kwargs):
            return self._inner.list_records(*args, **kwargs)

        def close(self):
            return self._inner.close()

    monkeypatch.setattr(
        usi, "begin_read", lambda snapshot=None: TombstonedSourceRead(original_begin_read(snapshot)),
    )

    page = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    ).search(_query(owner_scope, max_candidates=1))
    assert page.items == ()
    assert page.candidate_count == 1
    assert page.policy_filtered_count == 1
    assert page.candidate_clipped is True
    assert page.partial is True
    assert page.next_cursor == ""


def test_guarded_capture_or_parent_read_failure_returns_no_page_and_closes_read(
    tmp_path, monkeypatch,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    query = _query(owner_scope)

    def fail_capture(**kwargs):
        raise RuntimeError("private capture detail")

    monkeypatch.setattr(guard, "capture", fail_capture)
    with pytest.raises(LexicalRetrievalError, match="lifecycle boundary capture failed"):
        retriever.search(query)

    monkeypatch.undo()
    original_begin_read = usi.begin_read
    state = {"closed": False}

    class FailingRead:
        def __init__(self, inner):
            self._inner = inner

        @property
        def snapshot(self):
            return self._inner.snapshot

        @property
        def closed(self):
            return self._inner.closed

        def get(self, *args, **kwargs):
            raise RuntimeError("private parent detail")

        def list_records(self, *args, **kwargs):
            return self._inner.list_records(*args, **kwargs)

        def close(self):
            state["closed"] = True
            return self._inner.close()

    monkeypatch.setattr(
        usi, "begin_read", lambda snapshot=None: FailingRead(original_begin_read(snapshot)),
    )
    with pytest.raises(LexicalRetrievalError, match="lifecycle parent validation failed"):
        retriever.search(query)
    assert state["closed"] is True


@pytest.mark.parametrize("has_candidates", [True, False])
def test_incomplete_read_transaction_aborts_page_and_closes_once(
    tmp_path, monkeypatch, has_candidates,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    query = _query(
        owner_scope,
        **({} if has_candidates else {"query_text": "no matching token"}),
    )
    original_begin_read = usi.begin_read
    state = {"close": 0, "get": 0, "snippets": 0, "candidate_count": 0}
    expression, _ = lexical_module.build_fts_expression(
        query.query_text, query.match_mode,
    )
    state["candidate_count"] = len(usi.search_chunks(
        owner_scope=owner_scope,
        query=expression,
        limit=query.max_candidates + 1,
    ))

    class IncompleteRead:
        def __init__(self, inner):
            self._inner = inner

        @property
        def snapshot(self):
            return self._inner.snapshot

        @property
        def closed(self):
            return self._inner.closed

        def get(self, *args, **kwargs):
            state["get"] += 1
            return self._inner.get(*args, **kwargs)

        def close(self):
            state["close"] += 1
            return self._inner.close()

    def observe_snippet(*args, **kwargs):
        state["snippets"] += 1
        raise AssertionError("snippet must not be read for an invalid transaction")

    monkeypatch.setattr(
        usi, "begin_read", lambda snapshot=None: IncompleteRead(original_begin_read(snapshot)),
    )
    monkeypatch.setattr(lexical_module, "_snippet", observe_snippet)

    with pytest.raises(
        LexicalRetrievalError, match="lifecycle read transaction is invalid",
    ) as error:
        retriever.search(query)

    assert str(error.value) == "lifecycle read transaction is invalid"
    if has_candidates:
        assert state["candidate_count"] > 0
    else:
        assert state["candidate_count"] == 0
    assert state["get"] == 0
    assert state["snippets"] == 0
    assert state["close"] == 1


def test_scope_allows_reports_malformed_transaction_boundary(tmp_path):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    query = _query(owner_scope)
    scope = guard.capture(
        owner_scope=owner_scope,
        requested_ceiling=query.classification_ceiling,
        query_fingerprint=replace(query, cursor="").fingerprint,
    )
    chunk = usi.search_chunks(
        owner_scope=owner_scope,
        query='"harbour" AND "lifecycle"',
        limit=1,
    )[0].item.record

    class IncompleteRead:
        @property
        def snapshot(self):
            return usi.current_snapshot()

        @property
        def closed(self):
            return False

        def get(self, *args, **kwargs):
            raise AssertionError("parents must not be read through malformed boundary")

        def close(self):
            pass

    with pytest.raises(
        HarbourKnowledgeQueryGuardError, match="read transaction is invalid",
    ) as error:
        scope.allows(chunk, IncompleteRead())
    assert str(error.value) == "read transaction is invalid"


@pytest.mark.parametrize("has_candidates", [True, False])
@pytest.mark.parametrize(
    "defect",
    [
        "missing_get", "missing_list_records", "missing_close",
        "noncallable_get", "noncallable_list_records", "noncallable_close",
        "closed", "nonboolean_closed", "wrong_snapshot",
        "raising_snapshot", "raising_closed", "raising_get",
        "raising_list_records",
    ],
)
def test_guarded_boundary_rejects_bad_or_changing_members_before_candidate_use(
    tmp_path, monkeypatch, has_candidates, defect,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    query = _query(
        owner_scope,
        **({} if has_candidates else {"query_text": "no matching token"}),
    )
    retriever = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    )
    original_begin_read = usi.begin_read
    real_read = original_begin_read(usi.current_snapshot())
    state = {
        "close": 0, "get": 0, "list_records": 0, "snapshot": 0,
        "closed": 0, "snippet": 0, "search_chunks": 0,
    }

    def close():
        state["close"] += 1
        real_read.close()

    class AdversarialRead:
        @property
        def snapshot(self):
            state["snapshot"] += 1
            if defect == "raising_snapshot":
                raise RuntimeError("private snapshot failure")
            if defect == "wrong_snapshot":
                return lexical_module.StoreSnapshot(
                    real_read.snapshot.revision + 1,
                    "0" * 64,
                    real_read.snapshot.record_count,
                    real_read.snapshot.tombstone_count,
                )
            return real_read.snapshot

        @property
        def closed(self):
            state["closed"] += 1
            if defect == "closed":
                return True
            if defect == "nonboolean_closed":
                return 0
            if defect == "raising_closed":
                raise RuntimeError("private closed failure")
            return False

        @property
        def get(self):
            state["get"] += 1
            if defect == "missing_get":
                raise AttributeError("private get failure")
            if defect == "noncallable_get":
                return 1
            if defect == "raising_get":
                raise RuntimeError("private get failure")
            return real_read.get

        @property
        def list_records(self):
            state["list_records"] += 1
            if defect == "missing_list_records":
                raise AttributeError("private list failure")
            if defect == "noncallable_list_records":
                return 1
            if defect == "raising_list_records":
                raise RuntimeError("private list failure")
            return real_read.list_records

        @property
        def close(self):
            if defect == "missing_close":
                raise AttributeError("private close failure")
            if defect == "noncallable_close":
                return 1
            return close

    original_search_chunks = usi.search_chunks
    original_snippet = lexical_module._snippet

    def count_candidates(*args, **kwargs):
        state["search_chunks"] += 1
        return original_search_chunks(*args, **kwargs)

    def count_snippet(*args, **kwargs):
        state["snippet"] += 1
        return original_snippet(*args, **kwargs)

    monkeypatch.setattr(usi, "begin_read", lambda snapshot=None: AdversarialRead())
    monkeypatch.setattr(usi, "search_chunks", count_candidates)
    monkeypatch.setattr(lexical_module, "_snippet", count_snippet)

    with pytest.raises(
        LexicalRetrievalError, match="lifecycle read transaction is invalid",
    ) as error:
        retriever.search(query)

    assert str(error.value) == "lifecycle read transaction is invalid"
    assert state["search_chunks"] == 0
    assert state["snippet"] == 0
    assert state["snapshot"] == 1
    assert state["closed"] == 1
    assert state["get"] == 1
    assert state["list_records"] == 1
    assert state["close"] == (0 if defect in {"missing_close", "noncallable_close"} else 1)
    assert state["get"] < 2
    assert state["list_records"] < 2


@pytest.mark.parametrize("has_candidates", [True, False])
def test_guarded_boundary_uses_captured_methods_after_validation(
    tmp_path, monkeypatch, has_candidates,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    query = _query(
        owner_scope,
        **({} if has_candidates else {"query_text": "no matching token"}),
    )
    real_read = usi.begin_read(usi.current_snapshot())
    state = {"snapshot": 0, "closed": 0, "get": 0, "list_records": 0, "close": 0}

    class ChangingRead:
        @property
        def close(self):
            state["close"] += 1
            if state["close"] > 1:
                raise RuntimeError("close property reread")
            return real_read.close

        @property
        def snapshot(self):
            state["snapshot"] += 1
            if state["snapshot"] > 1:
                raise RuntimeError("snapshot property reread")
            return real_read.snapshot

        @property
        def closed(self):
            state["closed"] += 1
            if state["closed"] > 1:
                raise RuntimeError("closed property reread")
            return False

        @property
        def get(self):
            state["get"] += 1
            if state["get"] > 1:
                raise RuntimeError("get property reread")
            return real_read.get

        @property
        def list_records(self):
            state["list_records"] += 1
            if state["list_records"] > 1:
                raise RuntimeError("list property reread")
            return real_read.list_records

    monkeypatch.setattr(usi, "begin_read", lambda snapshot=None: ChangingRead())
    page = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    ).search(query)
    assert bool(page.items) is has_candidates
    assert state == {"snapshot": 1, "closed": 1, "get": 1, "list_records": 1, "close": 1}


def test_guarded_page_accepts_a_coherent_non_harbour_provider_chain(tmp_path):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    source = SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.TEXT,
        canonical_ref="fixture://independent-note",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="independent.fixture",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="revision-1",
        content_hash=content_hash(TEXT),
        version_observed_at="2026-10-01T00:00:00Z",
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, len(TEXT)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(TEXT),
        content=TEXT,
        indexed_at="2026-10-01T00:00:00Z",
    )
    write = usi.begin_write(usi.current_snapshot())
    for record in (source, version, chunk):
        write.put(record)
    write.commit()

    page = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    ).search(_query(owner_scope, source_ids=(source.source_id,)))
    assert len(page.items) == 1
    assert page.items[0].chunk_ref.record_id == chunk.chunk_id


@pytest.mark.parametrize(
    "mutation",
    ["foreign_owner", "mixed_parent", "source_id", "policy"],
)
def test_guarded_parent_identity_and_policy_mutations_filter_before_content(
    tmp_path, monkeypatch, mutation,
):
    _, _, _, _, usi, guard, owner_scope = _fixture(tmp_path)
    original_begin_read = usi.begin_read
    observed = {"snippet": False}

    class MutatingRead:
        def __init__(self, inner):
            self._inner = inner

        @property
        def snapshot(self):
            return self._inner.snapshot

        @property
        def closed(self):
            return self._inner.closed

        def get(self, kind, record_id, **kwargs):
            item = self._inner.get(kind, record_id, **kwargs)
            if item is None:
                return None
            if mutation == "foreign_owner" and kind is RecordKind.SOURCE:
                object.__setattr__(item.record, "owner_scope", "owner:" + "b" * 32)
            elif mutation == "mixed_parent" and kind is RecordKind.SOURCE_VERSION:
                object.__setattr__(item.record, "source_id", "usi.source." + "b" * 64)
            elif mutation == "source_id" and kind is RecordKind.SOURCE:
                object.__setattr__(item.record, "source_id", "usi.source." + "b" * 64)
            elif mutation == "policy" and kind is RecordKind.SOURCE:
                object.__setattr__(item.record, "content_policy", ContentPolicy.METADATA_ONLY)
            return item

        def list_records(self, *args, **kwargs):
            return self._inner.list_records(*args, **kwargs)

        def close(self):
            return self._inner.close()

    monkeypatch.setattr(
        usi, "begin_read", lambda snapshot=None: MutatingRead(original_begin_read(snapshot)),
    )
    original_snippet = lexical_module._snippet

    def observe_snippet(*args, **kwargs):
        observed["snippet"] = True
        return original_snippet(*args, **kwargs)

    monkeypatch.setattr(lexical_module, "_snippet", observe_snippet)
    page = UnifiedSourceIndexLexicalRetriever(
        usi, lifecycle_guard=guard, cursor_signing_key=b"c" * 32,
    ).search(_query(owner_scope))
    assert page.items == ()
    assert page.policy_filtered_count == 1
    assert observed["snippet"] is False
