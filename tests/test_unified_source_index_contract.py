import json

import pytest

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeOccurrenceRecords,
    CodeRangeLocator,
    ContentPolicy,
    DerivedRunKind,
    DerivedRunRecord,
    EntityKind,
    EntityRecord,
    IndexJobKind,
    IndexJobRecord,
    LineageReason,
    LineageRecord,
    MessageRangeLocator,
    PageRangeLocator,
    ProjectionKind,
    ProjectionManifest,
    RelationKind,
    RelationRecord,
    RowRangeLocator,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    UnifiedSourceIndexContractError,
    canonical_json,
    content_hash,
    locator_from_dict,
    normalized_locator,
    record_from_json,
)


NOW = "2026-07-13T10:00:00Z"
LATER = "2026-07-13T11:00:00Z"
CONFIG_HASH = "sha256:" + "c" * 64


def _source(
    canonical_ref: str = "repo:alpha/src/main.py",
    *,
    owner_scope: str = "user:alice",
    classification: Classification = Classification.PRIVATE,
    policy: ContentPolicy = ContentPolicy.INLINE_LOCAL,
) -> SourceRecord:
    return SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=canonical_ref,
        classification=classification,
        content_policy=policy,
        provider_ref="local-git",
        source_created_at="2026-07-10T08:00:00+02:00",
        first_seen_at="2026-07-11T07:00:00Z",
        source_modified_at="2026-07-12T09:00:00Z",
        valid_from=NOW,
    )


def _version(source: SourceRecord, revision: str = "git:abc123", body: str = "same body") -> SourceVersionRecord:
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(body),
        version_observed_at=NOW,
        indexed_at=LATER,
        valid_from=NOW,
    )


def _chunk(
    version: SourceVersionRecord,
    locator: TextRangeLocator | CodeRangeLocator,
    body: str = "same body",
) -> ChunkRecord:
    return ChunkRecord.create(
        version,
        locator=locator,
        extractor_profile_ref="tree-sitter-python@1",
        content_hash=content_hash(body),
        content=body if version.content_policy is ContentPolicy.INLINE_LOCAL else None,
        indexed_at=LATER,
    )


def test_occurrence_ids_distinguish_identical_content_by_source_and_position():
    source_a = _source("repo:alpha/src/a.py")
    source_b = _source("repo:alpha/src/b.py")
    version_a = _version(source_a)
    version_b = _version(source_b)

    first = _chunk(version_a, TextRangeLocator(0, 9))
    other_position = _chunk(version_a, TextRangeLocator(20, 29))
    other_source = _chunk(version_b, TextRangeLocator(0, 9))

    assert first.content_hash == other_position.content_hash == other_source.content_hash
    assert len({first.chunk_id, other_position.chunk_id, other_source.chunk_id}) == 3
    assert source_a.source_id != source_b.source_id
    assert version_a.source_version_id != version_b.source_version_id

    recreated = _chunk(version_a, TextRangeLocator(0, 9))
    assert recreated.chunk_id == first.chunk_id
    assert first.chunk_id.startswith("usi_chunk_")


def test_code_occurrence_aggregate_rejects_foreign_parent_chains_and_locator_kinds():
    source_a = _source("repo:alpha/src/a.py")
    source_b = _source("repo:alpha/src/b.py")
    version_a = _version(source_a)
    other_version_a = _version(source_a, revision="git:def456", body="other body")
    code_chunk = _chunk(
        version_a,
        CodeRangeLocator("src/a.py", 1, 0, 2, 0),
    )

    assert CodeOccurrenceRecords(source_a, version_a, code_chunk).chunk == code_chunk
    with pytest.raises(UnifiedSourceIndexContractError, match="foreign source parent"):
        CodeOccurrenceRecords(source_b, version_a, code_chunk)
    with pytest.raises(UnifiedSourceIndexContractError, match="foreign version parent"):
        CodeOccurrenceRecords(source_a, other_version_a, code_chunk)
    with pytest.raises(UnifiedSourceIndexContractError, match="CodeRangeLocator"):
        CodeOccurrenceRecords(
            source_a,
            version_a,
            _chunk(version_a, TextRangeLocator(0, 4), "same body"),
        )


@pytest.mark.parametrize(
    "locator",
    [
        TextRangeLocator(2, 8),
        PageRangeLocator(3, 5),
        RowRangeLocator("sheet-1", 10, 12),
        MessageRangeLocator("thread-9", 4, 6),
        CodeRangeLocator("src/module.py", 7, 4, 9, 2),
    ],
)
def test_typed_locators_have_stable_canonical_round_trips(locator):
    payload = locator.to_dict()
    restored = locator_from_dict(json.loads(canonical_json(payload)))

    assert restored == locator
    assert type(restored) is type(locator)
    assert normalized_locator(restored) == normalized_locator(locator)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TextRangeLocator(4, 4),
        lambda: PageRangeLocator(2, 1),
        lambda: RowRangeLocator("table", 8, 7),
        lambda: MessageRangeLocator("", 0, 1),
        lambda: CodeRangeLocator("../secret.py", 1, 0, 2, 0),
        lambda: locator_from_dict({"kind": "text_range", "start_char": 1}),
    ],
)
def test_invalid_or_incomplete_locators_fail_closed(factory):
    with pytest.raises(UnifiedSourceIndexContractError):
        factory()


def test_parent_policy_propagation_cannot_be_weakened_and_unknown_stays_closed():
    sensitive = _source(
        classification=Classification.SENSITIVE,
        policy=ContentPolicy.REFERENCE_ONLY,
    )

    inherited = _version(sensitive)
    assert inherited.owner_scope == sensitive.owner_scope
    assert inherited.classification is Classification.SENSITIVE
    assert inherited.content_policy is ContentPolicy.REFERENCE_ONLY

    with pytest.raises(UnifiedSourceIndexContractError, match="classification cannot weaken"):
        SourceVersionRecord.create(
            sensitive,
            revision_ref="git:def456",
            content_hash=content_hash("changed"),
            version_observed_at=NOW,
            classification=Classification.PRIVATE,
        )
    with pytest.raises(UnifiedSourceIndexContractError, match="content_policy cannot weaken"):
        SourceVersionRecord.create(
            sensitive,
            revision_ref="git:def456",
            content_hash=content_hash("changed"),
            version_observed_at=NOW,
            content_policy=ContentPolicy.INLINE_LOCAL,
        )

    unknown = _source(classification=Classification.UNKNOWN)
    with pytest.raises(UnifiedSourceIndexContractError, match="classification cannot weaken"):
        SourceVersionRecord.create(
            unknown,
            revision_ref="git:unknown",
            content_hash=content_hash("unknown"),
            version_observed_at=NOW,
            classification=Classification.PUBLIC,
        )


def test_invalid_scopes_and_tampered_or_incomplete_policy_evidence_fail_closed():
    with pytest.raises(UnifiedSourceIndexContractError):
        _source(owner_scope="*")
    with pytest.raises(UnifiedSourceIndexContractError):
        _source(owner_scope="user:")
    with pytest.raises(UnifiedSourceIndexContractError):
        SourceScope.create(())

    alice = _source(owner_scope="user:alice")
    bob = _source("repo:beta/main.py", owner_scope="user:bob")
    with pytest.raises(UnifiedSourceIndexContractError, match="cross-owner"):
        SourceScope.create((alice.policy_evidence(), bob.policy_evidence()))

    policy_payload = alice.policy_evidence().to_dict()
    del policy_payload["classification"]
    with pytest.raises(UnifiedSourceIndexContractError, match="missing fields"):
        type(alice.policy_evidence()).from_dict(policy_payload)

    tampered = alice.policy_evidence().to_dict()
    tampered["classification"] = "public"
    with pytest.raises(UnifiedSourceIndexContractError, match="canonical identity"):
        type(alice.policy_evidence()).from_dict(tampered)


def test_incomplete_occurrence_evidence_and_disallowed_content_fail_closed():
    source = _source(policy=ContentPolicy.REFERENCE_ONLY)
    version = _version(source)
    chunk = _chunk(version, TextRangeLocator(0, 4), body="body")

    evidence_payload = chunk.evidence_ref().to_dict()
    evidence_payload["locator"] = None
    with pytest.raises(UnifiedSourceIndexContractError, match="typed locator"):
        type(chunk.evidence_ref()).from_dict(evidence_payload)

    with pytest.raises(UnifiedSourceIndexContractError, match="inline_local"):
        ChunkRecord.create(
            version,
            locator=TextRangeLocator(0, 4),
            extractor_profile_ref="text@1",
            content_hash=content_hash("body"),
            content="body",
        )

    with pytest.raises(UnifiedSourceIndexContractError, match="evidence_refs"):
        RelationRecord.create(
            chunk.ref(),
            chunk.ref(),
            relation_kind=RelationKind.RELATED_TO,
            method_ref="parser@1",
            confidence=1.0,
            evidence_refs=(),
        )


def test_all_record_families_have_stable_ids_and_canonical_round_trips():
    source = _source()
    version_one = _version(source, "git:one", "first body")
    version_two = _version(source, "git:two", "second body")
    chunk_one = _chunk(version_one, CodeRangeLocator("src/main.py", 1, 0, 2, 0), "first body")
    chunk_two = _chunk(version_two, CodeRangeLocator("src/main.py", 1, 0, 2, 0), "second body")
    entity = EntityRecord.create(
        version_two,
        entity_kind=EntityKind.SYMBOL,
        natural_key="module:function",
        locator=CodeRangeLocator("src/main.py", 1, 0, 2, 0),
        extractor_profile_ref="tree-sitter-python@1",
        content_hash=content_hash("second body"),
        label="function",
    )
    relation = RelationRecord.create(
        entity.ref(),
        chunk_two.ref(),
        relation_kind=RelationKind.DEFINES,
        method_ref="tree-sitter-python@1",
        confidence=1.0,
        evidence_refs=(chunk_two.evidence_ref(), entity.evidence_ref()),
    )
    lineage = LineageRecord.create(
        chunk_one.evidence_ref(),
        chunk_two.evidence_ref(),
        reason=LineageReason.EDITED,
        method_ref="git-diff@1",
        confidence=0.95,
        valid_from=NOW,
    )
    scope = SourceScope.create((chunk_two.policy_evidence_ref(),))
    projection = ProjectionManifest.create(
        projection_kind=ProjectionKind.EMBEDDING,
        projection_profile_ref="semantic-default@1",
        input_snapshot_ref="snapshot:two",
        config_hash=CONFIG_HASH,
        input_evidence=(chunk_two.evidence_ref(),),
        implementation_ref="chroma",
        implementation_version="1.0",
        output_generation_ref="generation:7",
        indexed_at=LATER,
    )
    run = DerivedRunRecord.create(
        derived_kind=DerivedRunKind.SUMMARY,
        source_scope=scope,
        input_snapshot_ref="snapshot:two",
        algorithm_ref="summary-tree",
        algorithm_version="1.0",
        config_hash=CONFIG_HASH,
        input_evidence=(chunk_two.evidence_ref(), entity.evidence_ref()),
        quality_evidence_refs=("quality:counts",),
        rebuild_evidence_ref="rebuild:summary-tree",
        max_nodes=20,
        max_depth=3,
        started_at=NOW,
        completed_at=LATER,
    )
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref="request:usi-01",
        profile_ref="extractor:python-v1",
        max_items=50,
        time_budget_ms=2_000,
    )

    records = (
        source,
        version_one,
        chunk_one,
        entity,
        relation,
        lineage,
        projection,
        run,
        job,
    )
    id_fields = (
        "source_id",
        "source_version_id",
        "chunk_id",
        "entity_id",
        "relation_id",
        "lineage_id",
        "projection_id",
        "derived_run_id",
        "job_id",
    )

    for record, id_field in zip(records, id_fields):
        encoded = record.to_json()
        restored = type(record).from_json(encoded)
        assert restored == record
        assert record_from_json(encoded) == record
        assert canonical_json(json.loads(encoded)) == encoded
        assert getattr(restored, id_field) == getattr(record, id_field)

    assert isinstance(ChunkRecord.from_json(chunk_one.to_json()).locator, CodeRangeLocator)
    assert SourceRecord.from_json(source.to_json()).classification is Classification.PRIVATE


def test_projection_identity_is_usi_owned_not_backend_generation_owned():
    version = _version(_source())
    chunk = _chunk(version, TextRangeLocator(0, 9))
    common = dict(
        projection_kind=ProjectionKind.EMBEDDING,
        projection_profile_ref="semantic-default@1",
        input_snapshot_ref="snapshot:one",
        config_hash=CONFIG_HASH,
        input_evidence=(chunk.evidence_ref(),),
    )
    chroma = ProjectionManifest.create(
        **common,
        implementation_ref="chroma",
        implementation_version="1.0",
        output_generation_ref="chroma:collection-7",
    )
    replacement = ProjectionManifest.create(
        **common,
        implementation_ref="another-engine",
        implementation_version="9.2",
        output_generation_ref="backend:opaque-99",
    )

    assert chroma.projection_id == replacement.projection_id
    assert chroma.output_generation_ref != replacement.output_generation_ref


def test_semantic_times_remain_distinct_and_no_created_at_alias_is_serialized():
    source = _source()
    version = _version(source)
    payload = version.to_dict()

    assert payload["source_created_at"] == "2026-07-10T06:00:00Z"
    assert payload["first_seen_at"] == "2026-07-11T07:00:00Z"
    assert payload["source_modified_at"] == "2026-07-12T09:00:00Z"
    assert payload["version_observed_at"] == NOW
    assert payload["indexed_at"] == LATER
    assert "created_at" not in payload


def test_time_windows_compare_instants_not_timestamp_strings():
    source = _source()
    payload = source.to_dict()
    payload["valid_from"] = "2026-07-13T10:00:00Z"
    payload["valid_to"] = "2026-07-13T10:00:00.100000Z"

    assert SourceRecord.from_dict(payload).valid_to.endswith(".100000Z")

    payload["valid_from"] = "2026-07-13T10:00:00.100000Z"
    payload["valid_to"] = "2026-07-13T10:00:00Z"
    with pytest.raises(UnifiedSourceIndexContractError, match="must not precede"):
        SourceRecord.from_dict(payload)


def test_source_scope_and_derived_run_require_exact_version_evidence():
    source = _source()
    first = _version(source, "git:first", "first")
    second = _version(source, "git:second", "second")
    first_chunk = _chunk(first, TextRangeLocator(0, 5), "first")
    second_chunk = _chunk(second, TextRangeLocator(0, 6), "second")
    scope = SourceScope.create((first_chunk.policy_evidence_ref(),))

    tampered = scope.to_dict()
    tampered["source_version_ids"] = []
    with pytest.raises(UnifiedSourceIndexContractError, match="exactly its versions"):
        SourceScope.from_dict(tampered)

    with pytest.raises(UnifiedSourceIndexContractError, match="escapes source versions"):
        DerivedRunRecord.create(
            derived_kind=DerivedRunKind.SUMMARY,
            source_scope=scope,
            input_snapshot_ref="snapshot:mismatch",
            algorithm_ref="summary-tree",
            algorithm_version="1.0",
            config_hash=CONFIG_HASH,
            input_evidence=(second_chunk.evidence_ref(),),
            quality_evidence_refs=("quality:counts",),
            rebuild_evidence_ref="rebuild:summary-tree",
            max_nodes=20,
            max_depth=3,
        )


def test_hash_lineage_and_factories_fail_closed_on_invalid_inputs():
    with pytest.raises(UnifiedSourceIndexContractError, match="text or bytes"):
        content_hash(3)

    version = _version(_source())
    chunk = _chunk(version, TextRangeLocator(0, 9))
    with pytest.raises(UnifiedSourceIndexContractError, match="different chunk"):
        LineageRecord.create(
            chunk.evidence_ref(),
            chunk.evidence_ref(),
            reason=LineageReason.EDITED,
            method_ref="git-diff@1",
            confidence=1.0,
        )
    with pytest.raises(UnifiedSourceIndexContractError, match="SourceVersionRecord"):
        EntityRecord.create(
            object(),
            entity_kind=EntityKind.SYMBOL,
            natural_key="module:function",
            locator=TextRangeLocator(0, 1),
            extractor_profile_ref="parser@1",
            content_hash=content_hash("x"),
        )
