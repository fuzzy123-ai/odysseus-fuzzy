import pytest

from src.unified_source_index_adapters import (
    AdapterCapability,
    AdapterScope,
    DeterministicFakeSourceAdapter,
    ExactReadRequest,
    ExtractionProfile,
    FakeAdapterDocument,
    PolicyReadContext,
    SourceAdapter,
    SourceAdapterError,
    SourceUnavailableError,
    UnavailableObservation,
    UnavailableReason,
    validate_adapter_output,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    RecordKind,
    RecordRef,
    SourceKind,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)


def _capability(
    *,
    owner_scope: str = "user:alice",
    content_policy: ContentPolicy = ContentPolicy.INLINE_LOCAL,
    exact: bool = True,
) -> AdapterCapability:
    return AdapterCapability(
        adapter_id="fake.docs",
        adapter_version="v1",
        owner_scope=owner_scope,
        domain_kind="personal_docs",
        source_kind=SourceKind.DOCUMENT,
        content_policy=content_policy,
        classification_ceiling=Classification.SENSITIVE,
        supports_exact_reads=exact,
        max_discovery_page=10,
        max_extract_items=10,
    )


def _adapter(*documents, capability=None):
    return DeterministicFakeSourceAdapter(
        capability or _capability(),
        tuple(documents)
        or (
            FakeAdapterDocument("doc:alpha", "rev:1", "alpha content"),
        ),
    )


def _scope(adapter, *, ceiling=Classification.SENSITIVE, source_ids=()):
    return AdapterScope(
        adapter.describe_capability().owner_scope,
        ceiling,
        tuple(source_ids),
    )


def test_fake_adapter_satisfies_protocol_and_bounded_cursor_discovery():
    adapter = _adapter(
        FakeAdapterDocument("doc:alpha", "rev:1", "alpha"),
        FakeAdapterDocument("doc:beta", "rev:2", "beta"),
        FakeAdapterDocument("doc:gamma", "rev:3", "gamma"),
    )
    scope = _scope(adapter)

    first = adapter.discover(scope, cursor="", limit=2, time_budget_ms=100)
    second = adapter.discover(
        scope,
        cursor=first.next_cursor,
        limit=2,
        time_budget_ms=100,
    )

    assert isinstance(adapter, SourceAdapter)
    assert len(first.items) == 2
    assert first.clipped is True
    assert len(second.items) == 1
    assert second.clipped is False
    assert [item.source.source_id for item in (*first.items, *second.items)] == sorted(
        item.source.source_id for item in (*first.items, *second.items)
    )
    assert all(item.fingerprint.startswith("sha256:") for item in first.items)


def test_version_extract_and_exact_read_preserve_identity_policy_and_bounds():
    adapter = _adapter(
        FakeAdapterDocument("doc:alpha", "rev:1", "alpha content"),
    )
    discovery = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0]
    observed = adapter.observe_version(discovery.source.ref())
    assert isinstance(observed, SourceVersionRecord)
    profile = ExtractionProfile("text-v1", max_items=10, max_chars=5, time_budget_ms=100)
    extraction = adapter.extract(observed, profile)

    validate_adapter_output(
        adapter.describe_capability(),
        _scope(adapter, source_ids=(discovery.source.source_id,)),
        discovery,
        observed,
        extraction,
    )
    assert extraction.clipped is True
    assert extraction.warnings == ("content_clipped",)
    assert extraction.chunks[0].content == "alpha"
    assert extraction.chunks[0].content_hash == content_hash("alpha")

    exact = adapter.read_exact(
        ExactReadRequest(extraction.chunks[0].evidence_ref(), max_chars=3),
        PolicyReadContext(
            "user:alice",
            Classification.SENSITIVE,
            allow_inline_content=True,
        ),
    )
    assert exact.content == "alp"
    assert exact.clipped is True
    assert exact.content_hash == content_hash("alp")


def test_reference_only_extraction_never_returns_stored_inline_content():
    adapter = _adapter(
        FakeAdapterDocument("doc:remote", "etag:1", "remote body"),
        capability=_capability(content_policy=ContentPolicy.REFERENCE_ONLY),
    )
    discovery = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0]
    version = adapter.observe_version(discovery.source.ref())
    assert isinstance(version, SourceVersionRecord)
    extraction = adapter.extract(
        version,
        ExtractionProfile("text-v1", 10, 100, 100),
    )

    assert extraction.chunks[0].content is None
    assert extraction.chunks[0].content_hash == content_hash("remote body")
    validate_adapter_output(
        adapter.describe_capability(),
        _scope(adapter),
        discovery,
        version,
        extraction,
    )


def test_unavailable_observation_is_content_free_and_exact_read_fails():
    adapter = _adapter(
        FakeAdapterDocument("doc:gone", "rev:gone", "private body", available=False),
    )
    discovery = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0]

    observed = adapter.observe_version(discovery.source.ref())

    assert isinstance(observed, UnavailableObservation)
    assert observed.reason is UnavailableReason.DELETED
    assert "private body" not in repr(observed)
    synthetic_version = SourceVersionRecord.create(
        discovery.source,
        revision_ref="rev:gone",
        content_hash=content_hash("private body"),
        version_observed_at="2026-01-01T00:00:00Z",
    )
    synthetic_chunk = ChunkRecord.create(
        synthetic_version,
        locator=TextRangeLocator(0, len("private body")),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("private body"),
        content="private body",
    )
    with pytest.raises(SourceUnavailableError):
        adapter.read_exact(
            ExactReadRequest(synthetic_chunk.evidence_ref(), max_chars=10),
            PolicyReadContext("user:alice", Classification.SENSITIVE, True),
        )


def test_discovery_cursor_scope_owner_and_checksum_fail_closed():
    adapter = _adapter(
        FakeAdapterDocument("doc:alpha", "rev:1", "alpha"),
        FakeAdapterDocument("doc:beta", "rev:2", "beta"),
    )
    scope = _scope(adapter)
    cursor = adapter.discover(
        scope, cursor="", limit=1, time_budget_ms=100
    ).next_cursor

    replacement = "A" if cursor[-1] != "A" else "B"
    with pytest.raises(SourceAdapterError, match="cursor"):
        adapter.discover(
            scope,
            cursor=cursor[:-1] + replacement,
            limit=1,
            time_budget_ms=100,
        )
    with pytest.raises(SourceAdapterError, match="owner"):
        adapter.discover(
            AdapterScope("user:bob", Classification.SENSITIVE),
            cursor="",
            limit=1,
            time_budget_ms=100,
        )
    with pytest.raises(SourceAdapterError, match="another adapter or scope"):
        adapter.discover(
            AdapterScope("user:alice", Classification.PRIVATE),
            cursor=cursor,
            limit=1,
            time_budget_ms=100,
        )


def test_classification_source_scope_and_exact_read_policy_fail_closed():
    adapter = _adapter(
        FakeAdapterDocument(
            "doc:sensitive",
            "rev:1",
            "sensitive",
            classification=Classification.SENSITIVE,
        )
    )
    with pytest.raises(SourceAdapterError, match="classification ceiling"):
        adapter.discover(
            _scope(adapter, ceiling=Classification.PRIVATE),
            cursor="",
            limit=1,
            time_budget_ms=100,
        )

    discovery = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0]
    version = adapter.observe_version(discovery.source.ref())
    assert isinstance(version, SourceVersionRecord)
    extraction = adapter.extract(version, ExtractionProfile("text-v1", 10, 100, 100))
    with pytest.raises(SourceAdapterError, match="source scope"):
        validate_adapter_output(
            adapter.describe_capability(),
            AdapterScope(
                "user:alice",
                Classification.SENSITIVE,
                ("usi_source_" + "a" * 64,),
            ),
            discovery,
            version,
            extraction,
        )
    with pytest.raises(SourceAdapterError, match="not allowed"):
        adapter.read_exact(
            ExactReadRequest(extraction.chunks[0].evidence_ref(), 10),
            PolicyReadContext("user:alice", Classification.SENSITIVE, False),
        )


def test_capability_and_canonical_refs_reject_unsafe_or_unbounded_inputs():
    with pytest.raises(SourceAdapterError, match="metadata-only"):
        _capability(content_policy=ContentPolicy.METADATA_ONLY, exact=True)
    with pytest.raises(SourceAdapterError, match="secret-bearing"):
        FakeAdapterDocument(
            "https://provider.test/doc?access_token=secret",
            "rev:1",
            "body",
        )
    with pytest.raises(SourceAdapterError, match="between 1"):
        ExtractionProfile("text-v1", max_items=0, max_chars=10, time_budget_ms=10)
    with pytest.raises(ValueError):
        RecordRef(RecordKind.SOURCE, "source_" + "z" * 64)
