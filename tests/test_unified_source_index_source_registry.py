import pytest

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    IndexJobKind,
    IndexJobRecord,
    ProjectionKind,
    ProjectionManifest,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterCapabilityManifest,
    SourceAdapterManifestError,
    SourceAdapterOperation,
)
from src.unified_source_index_source_registry import (
    SourceAdapterRegistration,
    SourceAdapterRegistry,
    SourceAdapterRegistryError,
)


BASE_OPERATIONS = (
    SourceAdapterOperation.DISCOVER,
    SourceAdapterOperation.OBSERVE_VERSION,
    SourceAdapterOperation.EXTRACT,
    SourceAdapterOperation.OBSERVE_UNAVAILABLE,
    SourceAdapterOperation.READ_EXACT,
)


def _manifest(**changes):
    values = {
        "adapter_id": "memory.accepted",
        "adapter_version": "v1",
        "domain_id": "personal_memory",
        "source_kind": SourceKind.MEMORY,
        "content_policy": ContentPolicy.INLINE_LOCAL,
        "classification_ceiling": Classification.SENSITIVE,
        "owner_scope_requirement": OwnerScopeRequirement.IMMUTABLE_OPAQUE,
        "provider_constraint": ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY,
        "query_capability": QueryCapability.EXACT_READER,
        "operations": BASE_OPERATIONS,
        "exact_reader_boundary": "memory.accepted_reader",
    }
    values.update(changes)
    return SourceAdapterCapabilityManifest(**values)


def _registration(**changes):
    return SourceAdapterRegistration(_manifest(**changes))


def test_registry_enumerates_deterministically_without_invoking_a_factory():
    calls = []

    def factory():
        calls.append("called")
        raise AssertionError("registry enumeration must not instantiate adapters")

    registry = SourceAdapterRegistry(
        (
            SourceAdapterRegistration(_manifest(adapter_id="zeta.docs", domain_id="personal_docs"), factory),
            _registration(adapter_id="alpha.memory", domain_id="personal_memory"),
        )
    )

    assert [manifest.adapter_id for manifest in registry.manifests()] == ["alpha.memory", "zeta.docs"]
    assert registry.for_domain("personal_docs").adapter_id == "zeta.docs"
    assert calls == []


def test_duplicate_and_unknown_adapter_or_domain_ids_fail_closed():
    first = _registration(adapter_id="memory.one", domain_id="personal_memory")
    with pytest.raises(SourceAdapterRegistryError, match="duplicate adapter"):
        SourceAdapterRegistry((first, _registration(adapter_id="memory.one", domain_id="personal_docs")))
    with pytest.raises(SourceAdapterRegistryError, match="duplicate domain"):
        SourceAdapterRegistry((first, _registration(adapter_id="docs.one", domain_id="personal_memory")))

    registry = SourceAdapterRegistry((first,))
    with pytest.raises(SourceAdapterRegistryError, match="unknown adapter"):
        registry.select("missing.adapter")
    with pytest.raises(SourceAdapterRegistryError, match="unknown domain"):
        registry.for_domain("missing_domain")


def test_manifest_requires_explicit_owner_content_provider_and_exact_reader_policy():
    manifest = _manifest()

    assert manifest.owner_scope_requirement is OwnerScopeRequirement.IMMUTABLE_OPAQUE
    assert manifest.content_policy is ContentPolicy.INLINE_LOCAL
    assert manifest.provider_constraint is ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY
    assert manifest.query_capability is QueryCapability.EXACT_READER
    assert manifest.productive_default_enabled is False

    with pytest.raises(SourceAdapterManifestError, match="default-off"):
        _manifest(productive_default_enabled=True)
    with pytest.raises(SourceAdapterManifestError, match="metadata-only"):
        _manifest(content_policy=ContentPolicy.METADATA_ONLY)
    with pytest.raises(SourceAdapterManifestError, match="exact-reader"):
        _manifest(exact_reader_boundary="")
    with pytest.raises(SourceAdapterManifestError, match="base adapter"):
        _manifest(operations=(SourceAdapterOperation.DISCOVER,))


def test_disabled_query_cannot_smuggle_an_exact_reader_capability():
    without_exact = tuple(operation for operation in BASE_OPERATIONS if operation is not SourceAdapterOperation.READ_EXACT)
    manifest = _manifest(
        query_capability=QueryCapability.DISABLED,
        operations=without_exact,
        exact_reader_boundary="",
        content_policy=ContentPolicy.METADATA_ONLY,
        provider_constraint=ProviderConstraint.EXTERNAL_DISABLED,
    )

    assert manifest.query_capability is QueryCapability.DISABLED
    with pytest.raises(SourceAdapterManifestError, match="disabled query"):
        _manifest(query_capability=QueryCapability.DISABLED)


def test_selected_generation_binds_job_and_projection_evidence_without_runtime_effects():
    registry = SourceAdapterRegistry((_registration(),))

    selected = registry.select("memory.accepted")

    assert selected.generation_ref.startswith("usi_generation_")
    assert selected.job_profile_ref.endswith(selected.generation_ref.removeprefix("usi_generation_"))
    assert selected.projection_evidence() == {
        "implementation_ref": "adapter.memory.accepted",
        "implementation_version": "v1",
        "output_generation_ref": selected.generation_ref,
    }
    assert registry.select("memory.accepted").generation_ref == selected.generation_ref
    assert selected.generation_ref != SourceAdapterRegistry((_registration(adapter_version="v2"),)).select("memory.accepted").generation_ref


def test_selection_evidence_is_accepted_by_existing_projection_and_job_contracts():
    selected = SourceAdapterRegistry((_registration(),)).select("memory.accepted")
    source = SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.MEMORY,
        canonical_ref="memory:opaque-one",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="memory.accepted",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="revision:one",
        content_hash=content_hash("body"),
        version_observed_at="2026-08-02T00:00:00Z",
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, 4),
        extractor_profile_ref="text.v1",
        content_hash=content_hash("body"),
        content="body",
    )
    scope = SourceScope.create((chunk.policy_evidence_ref(),))

    projection = ProjectionManifest.create(
        projection_kind=ProjectionKind.LEXICAL,
        projection_profile_ref="lexical.v1",
        input_snapshot_ref="snapshot:one",
        config_hash="sha256:" + "a" * 64,
        input_evidence=(chunk.evidence_ref(),),
        **selected.projection_evidence(),
    )
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref="request:one",
        profile_ref=selected.job_profile_ref,
        max_items=1,
        time_budget_ms=1,
    )

    assert projection.output_generation_ref == selected.generation_ref
    assert job.profile_ref == selected.job_profile_ref


@pytest.mark.parametrize("field,value", [("adapter_id", "Memory"), ("domain_id", "docs/path"), ("adapter_version", "")])
def test_manifest_rejects_noncanonical_identifiers(field, value):
    with pytest.raises(SourceAdapterManifestError, match="canonical identifier"):
        _manifest(**{field: value})
