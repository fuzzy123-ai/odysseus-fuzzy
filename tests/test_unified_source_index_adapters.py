from dataclasses import dataclass
from enum import StrEnum
from dataclasses import FrozenInstanceError

import pytest

from src.unified_source_index_adapters import (
    AdapterCapability,
    AdapterScope,
    DeterministicFakeSourceAdapter,
    ExactReadRequest,
    ExtractionProfile,
    ExtractionPageBudget,
    FakeAdapterDocument,
    PagedExtractionPage,
    PagedSourceAdapter,
    PolicyReadContext,
    SourceAdapter,
    SourceAdapterError,
    SourceUnavailableError,
    UnavailableObservation,
    UnavailableReason,
    _capture_exact_contract_value,
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


def test_paged_capability_and_page_are_opt_in_without_changing_legacy_protocol():
    legacy = _capability()
    paged = AdapterCapability(
        adapter_id="fake.paged",
        adapter_version="v1",
        owner_scope="user:alice",
        domain_kind="personal_docs",
        source_kind=SourceKind.DOCUMENT,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.SENSITIVE,
        supports_exact_reads=True,
        max_discovery_page=10,
        max_extract_items=1_000,
        required_job_profile_ref="paged-v1",
    )
    adapter = _adapter()
    source = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0].source
    version = adapter.observe_version(source.ref())
    assert isinstance(version, SourceVersionRecord)

    class PageOnly:
        def extract_page(
            self,
            source_version,
            profile,
            *,
            cursor,
            sequence_start,
        ):
            return PagedExtractionPage(
                source_version=source_version,
                sequence_start=sequence_start,
                records=(),
                final_record_count=0,
                final_records_digest=(
                    "sha256:e3b0c44298fc1c149afbf4c8996fb924"
                    "27ae41e4649b934ca495991b7852b855"
                ),
            )

    page_only = PageOnly()
    page = page_only.extract_page(
        version,
        ExtractionProfile("paged-v1", 100, 100, 100),
        cursor="",
        sequence_start=0,
    )

    assert legacy.required_job_profile_ref == ""
    assert paged.required_job_profile_ref == "paged-v1"
    assert isinstance(page_only, PagedSourceAdapter)
    assert not isinstance(page_only, SourceAdapter)
    assert page.records == ()
    assert page.next_cursor == ""
    assert page.warnings == ()


def test_budgeted_paging_contract_is_explicit_immutable_and_exactly_bounded():
    class IntegerSubclass(int):
        pass

    assert _capability().max_total_extract_chars == 1_000_000
    assert not _capability().supports_budgeted_paging
    opted_in = AdapterCapability(
        adapter_id="fake.budgeted",
        adapter_version="v1",
        owner_scope="user:alice",
        domain_kind="personal_docs",
        source_kind=SourceKind.DOCUMENT,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.SENSITIVE,
        supports_exact_reads=True,
        required_job_profile_ref="paged-v1",
        max_total_extract_chars=8_388_608,
        supports_budgeted_paging=True,
    )
    assert opted_in.max_total_extract_chars == 8_388_608
    budget = ExtractionPageBudget(1, 262_144, 500)
    assert ExtractionPageBudget(1, 0, 1).max_chars == 0
    with pytest.raises(FrozenInstanceError):
        budget.max_chars = 1
    with pytest.raises(SourceAdapterError, match="exact integer"):
        ExtractionPageBudget(True, 1, 1)
    with pytest.raises(SourceAdapterError, match="between 1"):
        ExtractionPageBudget(101, 1, 1)
    with pytest.raises(SourceAdapterError, match="between 0 and 1000000"):
        ExtractionPageBudget(1, 1_000_001, 1)
    with pytest.raises(SourceAdapterError, match="exact integers"):
        ExtractionPageBudget(1, 1, IntegerSubclass(2))
    with pytest.raises(SourceAdapterError, match="exact integer"):
        AdapterCapability(
            adapter_id="fake.budgeted",
            adapter_version="v1",
            owner_scope="user:alice",
            domain_kind="personal_docs",
            source_kind=SourceKind.DOCUMENT,
            content_policy=ContentPolicy.INLINE_LOCAL,
            classification_ceiling=Classification.SENSITIVE,
            supports_exact_reads=True,
            max_total_extract_chars=True,
        )
    with pytest.raises(SourceAdapterError, match="exact integer"):
        AdapterCapability(
            adapter_id="fake.budgeted",
            adapter_version="v1",
            owner_scope="user:alice",
            domain_kind="personal_docs",
            source_kind=SourceKind.DOCUMENT,
            content_policy=ContentPolicy.INLINE_LOCAL,
            classification_ceiling=Classification.SENSITIVE,
            supports_exact_reads=True,
            max_total_extract_chars=IntegerSubclass(2),
        )
    with pytest.raises(SourceAdapterError, match="between 1"):
        AdapterCapability(
            adapter_id="fake.budgeted",
            adapter_version="v1",
            owner_scope="user:alice",
            domain_kind="personal_docs",
            source_kind=SourceKind.DOCUMENT,
            content_policy=ContentPolicy.INLINE_LOCAL,
            classification_ceiling=Classification.SENSITIVE,
            supports_exact_reads=True,
            max_total_extract_chars=8_388_609,
            required_job_profile_ref="paged-v1",
            supports_budgeted_paging=True,
        )
    with pytest.raises(SourceAdapterError, match="requires a generation-bound"):
        AdapterCapability(
            adapter_id="fake.budgeted",
            adapter_version="v1",
            owner_scope="user:alice",
            domain_kind="personal_docs",
            source_kind=SourceKind.DOCUMENT,
            content_policy=ContentPolicy.INLINE_LOCAL,
            classification_ceiling=Classification.SENSITIVE,
            supports_exact_reads=True,
            supports_budgeted_paging=True,
        )


def test_paged_page_rejects_nonexact_nested_values_before_behavior():
    adapter = _adapter()
    source = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0].source
    version = adapter.observe_version(source.ref())
    assert isinstance(version, SourceVersionRecord)
    chunk = adapter.extract(
        version, ExtractionProfile("text-v1", 10, 100, 100)
    ).chunks[0]
    callbacks = []

    class ForeignClassification(StrEnum):
        PRIVATE = "private"

    object.__setattr__(chunk, "classification", ForeignClassification.PRIVATE)
    object.__setattr__(chunk, "to_json", lambda: callbacks.append("enum"))
    with pytest.raises(SourceAdapterError, match="non-exact nested value"):
        PagedExtractionPage(
            version, 0, (chunk,), final_record_count=1,
            final_records_digest="sha256:" + "0" * 64,
        )
    assert callbacks == []

    clean_chunk = adapter.extract(
        version, ExtractionProfile("text-v1", 10, 100, 100)
    ).chunks[0]

    @dataclass(frozen=True)
    class SpoofedLocator:
        start_char: int
        end_char: int

        def to_dict(self):
            callbacks.append("spoof")
            return {"kind": "text_range", "start_char": 0, "end_char": 1}

    SpoofedLocator.__module__ = "src.unified_source_index_contract"
    object.__setattr__(clean_chunk, "locator", SpoofedLocator(0, 1))
    with pytest.raises(SourceAdapterError, match="non-exact nested value"):
        PagedExtractionPage(
            version, 0, (clean_chunk,), final_record_count=1,
            final_records_digest="sha256:" + "0" * 64,
        )
    assert callbacks == []


def test_paged_page_ignores_instance_serializer_override_and_requires_exact_warnings():
    adapter = _adapter()
    source = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0].source
    version = adapter.observe_version(source.ref())
    assert isinstance(version, SourceVersionRecord)
    chunk = adapter.extract(
        version, ExtractionProfile("text-v1", 10, 100, 100)
    ).chunks[0]
    callbacks = []
    object.__setattr__(chunk, "to_json", lambda: callbacks.append("record"))

    page = PagedExtractionPage(
        version, 0, (chunk,), final_record_count=1,
        final_records_digest="sha256:" + "0" * 64,
    )

    assert page.records[0] == chunk
    assert callbacks == []
    assert "to_json" not in getattr(page.records[0], "__dict__", {})

    class Warning(str):
        pass

    with pytest.raises(SourceAdapterError, match="exact tuple and text"):
        PagedExtractionPage(
            version, 0, (), final_record_count=0,
            final_records_digest="sha256:" + "0" * 64, warnings=["warning"],
        )
    with pytest.raises(SourceAdapterError, match="exact tuple and text"):
        PagedExtractionPage(
            version, 0, (), final_record_count=0,
            final_records_digest="sha256:" + "0" * 64,
            warnings=(Warning("warning"),),
        )


def test_mutated_enum_singleton_literal_is_rejected_at_scope_and_capture_boundaries():
    adapter = _adapter()
    source = adapter.discover(_scope(adapter), cursor="", limit=1, time_budget_ms=100).items[0].source
    version = adapter.observe_version(source.ref())
    chunk = adapter.extract(version, ExtractionProfile("text-v1", 10, 100, 100)).chunks[0]
    original = object.__getattribute__(Classification.PRIVATE, "_value_")
    object.__setattr__(Classification.PRIVATE, "_value_", "forged-private")
    try:
        with pytest.raises(SourceAdapterError):
            AdapterScope("user:alice", Classification.PRIVATE)
        with pytest.raises(SourceAdapterError):
            _capture_exact_contract_value(chunk)
        with pytest.raises(SourceAdapterError):
            PagedExtractionPage(
                version, 0, (chunk,), final_record_count=1,
                final_records_digest="sha256:" + "0" * 64,
            )
    finally:
        object.__setattr__(Classification.PRIVATE, "_value_", original)


def test_paged_page_enforces_terminal_and_nonterminal_field_modes():
    adapter = _adapter()
    source = adapter.discover(
        _scope(adapter), cursor="", limit=1, time_budget_ms=100
    ).items[0].source
    version = adapter.observe_version(source.ref())
    assert type(version) is SourceVersionRecord
    chunk = adapter.extract(
        version, ExtractionProfile("text-v1", 10, 100, 100)
    ).chunks[0]

    with pytest.raises(SourceAdapterError, match="terminal paged extraction"):
        PagedExtractionPage(version, 0, (chunk,))
    with pytest.raises(SourceAdapterError, match="nonterminal paged extraction must contain"):
        PagedExtractionPage(version, 0, (), next_cursor="next")
    with pytest.raises(SourceAdapterError, match="cannot contain terminal fields"):
        PagedExtractionPage(
            version, 0, (chunk,), next_cursor="next",
            final_record_count=1, final_records_digest="sha256:" + "0" * 64,
        )
