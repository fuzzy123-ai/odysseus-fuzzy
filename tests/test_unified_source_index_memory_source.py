from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import MappingProxyType

import pytest

import src.unified_source_index_sources.memory as memory_source_module
from src.memory_owner_eligibility import (
    MEMORY_ELIGIBILITY_SCHEMA,
    MemoryOwnerEligibilitySnapshot,
    capture_memory_owner_eligibility_snapshot,
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
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterOperation,
)
from src.unified_source_index_source_registry import SourceAdapterRegistry
from src.unified_source_index_sources.memory import (
    MEMORY_SOURCE_ADAPTER_ID,
    MAX_DISCOVERY_LIMIT,
    MemoryDiscoveryPage,
    MemoryExactRead,
    MemoryRecordEvidence,
    MemoryRecordFieldLocator,
    MemorySourceAdapter,
    MemorySourceAdapterError,
    MemorySourceAuthorityBinding,
    MemorySourceDescriptor,
    MemorySourceOccurrence,
    MemoryUnavailableReason,
    create_memory_source_authority_binding,
    memory_source_capability_manifest,
    memory_source_registration,
)


POLICY_REF = "sha256:" + "a" * 64
REVIEW_REF = "sha256:" + "b" * 64
ALICE_SCOPE = OwnerScope.for_subject_id("owner_0123456789abcdef0123456789abcdef")
BOB_SCOPE = OwnerScope.for_subject_id("owner_fedcba9876543210fedcba9876543210")


def _stamp(**changes):
    value = {
        "schema": MEMORY_ELIGIBILITY_SCHEMA,
        "source_status": "active",
        "acceptance_status": "accepted",
        "incognito": False,
        "policy_status": "go",
        "policy_evidence_ref": POLICY_REF,
        "review_status": "accepted",
        "review_evidence_ref": REVIEW_REF,
    }
    value.update(changes)
    return value


def _record(
    *,
    memory_id="memory-one",
    owner="alice",
    text="synthetic accepted body",
    timestamp=1_700_000_000,
    stamp=None,
    **extra,
):
    value = {
        "id": memory_id,
        "owner": owner,
        "text": text,
        "timestamp": timestamp,
        "source": "synthetic",
        "category": "fixture",
        "uses": 0,
        "metadata": {"memory_eligibility": _stamp() if stamp is None else stamp},
    }
    value.update(extra)
    return value


def _snapshot(tmp_path: Path, rows, *, owner="alice") -> MemoryOwnerEligibilitySnapshot:
    path = tmp_path / "memory.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return capture_memory_owner_eligibility_snapshot(path, owner=owner)


def _adapter(tmp_path: Path, rows=None, *, scope=ALICE_SCOPE, **limits):
    snapshot = _snapshot(tmp_path, rows or [_record()])
    binding = create_memory_source_authority_binding(owner_scope=scope, snapshot=snapshot)
    adapter = MemorySourceAdapter(binding=binding, snapshot=snapshot, **limits)
    expected = {
        "expected_binding_digest": binding.binding_digest,
        "expected_snapshot_digest": snapshot.snapshot_digest,
    }
    return adapter, binding, snapshot, expected


def _first_descriptor(adapter, expected):
    page = adapter.discover(limit=1, **expected)
    assert len(page.items) == 1
    return page.items[0]


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if type(value) is type(MappingProxyType({})):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def test_manifest_and_registration_are_closed_default_off_and_effect_free():
    manifest = memory_source_capability_manifest()

    assert manifest.adapter_id == MEMORY_SOURCE_ADAPTER_ID
    assert manifest.adapter_version == "v1"
    assert manifest.domain_id == "personal_memory"
    assert manifest.source_kind is SourceKind.MEMORY
    assert manifest.content_policy is ContentPolicy.INLINE_LOCAL
    assert manifest.classification_ceiling is Classification.SENSITIVE
    assert manifest.owner_scope_requirement is OwnerScopeRequirement.IMMUTABLE_OPAQUE
    assert manifest.provider_constraint is ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY
    assert manifest.query_capability is QueryCapability.EXACT_READER
    assert manifest.productive_default_enabled is False
    assert set(manifest.operations) == set(SourceAdapterOperation)

    registration = memory_source_registration()
    assert registration.factory is None
    registry = SourceAdapterRegistry((registration,))
    assert registry.select(MEMORY_SOURCE_ADAPTER_ID).generation_ref == manifest.generation_ref


def test_binding_is_deterministic_closed_and_content_free(tmp_path):
    private_text = "synthetic-private-marker"
    snapshot = _snapshot(tmp_path, [_record(text=private_text)])
    first = create_memory_source_authority_binding(owner_scope=ALICE_SCOPE, snapshot=snapshot)
    second = create_memory_source_authority_binding(owner_scope=ALICE_SCOPE, snapshot=snapshot)

    assert first == second
    assert first.owner_scope == ALICE_SCOPE
    assert first.owner_ref == snapshot.owner_ref
    assert first.source_digest == snapshot.source_digest
    assert first.snapshot_digest == snapshot.snapshot_digest
    assert first.adapter_generation == memory_source_capability_manifest().generation_ref
    assert private_text not in repr(first)
    assert "alice" not in repr(first)
    assert first.binding_digest.startswith("sha256:")


def test_bounded_discovery_uses_hashed_refs_and_stable_per_record_revisions(tmp_path):
    rows = [
        _record(memory_id="b", text="second"),
        _record(memory_id="a", text="first"),
    ]
    adapter, binding, snapshot, expected = _adapter(tmp_path, rows)

    first = adapter.discover(limit=1, **expected)
    second = adapter.discover(cursor=first.next_cursor, limit=1, **expected)
    descriptors = first.items + second.items

    assert first.next_cursor == 1
    assert second.next_cursor is None
    assert first.binding_digest == binding.binding_digest
    assert first.snapshot_digest == snapshot.snapshot_digest
    assert all(item.source.source_kind is SourceKind.MEMORY for item in descriptors)
    assert all(item.source.provider_ref == "memory.accepted" for item in descriptors)
    assert all(item.source.canonical_ref.startswith("memory:record:") for item in descriptors)
    assert all(item.source_version.revision_ref == item.evidence.record_digest for item in descriptors)
    encoded = repr(descriptors)
    assert "alice" not in encoded
    assert "first" not in encoded
    assert "second" not in encoded
    assert "memory-one" not in encoded


def test_observe_extract_and_exact_read_use_canonical_usi_records(tmp_path):
    text = "line one\nline two"
    adapter, _, _, expected = _adapter(tmp_path, [_record(text=text)])
    descriptor = _first_descriptor(adapter, expected)

    observed = adapter.observe_version(descriptor.evidence.record_ref, **expected)
    occurrence = adapter.extract(descriptor.evidence.record_ref, **expected)
    exact = adapter.read_exact(occurrence.locator, **expected)

    assert observed == descriptor
    assert type(occurrence.source) is SourceRecord
    assert type(occurrence.source_version) is SourceVersionRecord
    assert type(occurrence.chunk) is ChunkRecord
    assert occurrence.source.classification is Classification.SENSITIVE
    assert occurrence.source.content_policy is ContentPolicy.INLINE_LOCAL
    assert occurrence.chunk.classification is Classification.SENSITIVE
    assert occurrence.chunk.content_policy is ContentPolicy.INLINE_LOCAL
    assert occurrence.chunk.content == text
    assert occurrence.chunk.content_hash == content_hash(text)
    assert occurrence.locator.text_range == TextRangeLocator(0, len(text))
    assert occurrence.evidence.policy_evidence_ref == POLICY_REF
    assert occurrence.evidence.review_evidence_ref == REVIEW_REF
    assert exact.content == text
    assert exact.content_hash == content_hash(text)
    assert text not in repr(occurrence)
    assert text not in repr(exact)


def test_observe_unavailable_is_content_free_and_rejects_available_record(tmp_path):
    adapter, _, snapshot, expected = _adapter(tmp_path)
    descriptor = _first_descriptor(adapter, expected)

    with pytest.raises(MemorySourceAdapterError, match="^record_still_available$"):
        adapter.observe_unavailable(
            descriptor.evidence.record_ref,
            reason=MemoryUnavailableReason.DELETED,
            **expected,
        )

    missing = "memory:record:" + "f" * 64
    observation = adapter.observe_unavailable(
        missing,
        reason=MemoryUnavailableReason.INELIGIBLE,
        **expected,
    )
    assert observation.record_ref == missing
    assert observation.snapshot_digest == snapshot.snapshot_digest
    assert observation.observation_digest.startswith("sha256:")
    assert "alice" not in repr(observation)


def test_foreign_or_stale_expected_authority_fails_closed(tmp_path):
    adapter, binding, snapshot, expected = _adapter(tmp_path)
    foreign_binding = create_memory_source_authority_binding(
        owner_scope=BOB_SCOPE,
        snapshot=snapshot,
    )

    with pytest.raises(MemorySourceAdapterError, match="^stale_authority$"):
        adapter.discover(
            expected_binding_digest=foreign_binding.binding_digest,
            expected_snapshot_digest=snapshot.snapshot_digest,
        )
    with pytest.raises(MemorySourceAdapterError, match="^stale_authority$"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest="sha256:" + "0" * 64,
        )


def test_exact_read_rejects_locator_from_another_snapshot_version(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_adapter, _, _, first_expected = _adapter(first_dir, [_record(text="first")])
    second_adapter, _, _, second_expected = _adapter(second_dir, [_record(text="second")])
    old_locator = _first_descriptor(first_adapter, first_expected).locator

    with pytest.raises(MemorySourceAdapterError, match="^stale_authority$"):
        second_adapter.read_exact(old_locator, **second_expected)


@pytest.mark.parametrize(
    "stamp_changes",
    [
        {"source_status": "deleted"},
        {"acceptance_status": "rejected"},
        {"incognito": True},
        {"policy_status": "blocked"},
        {"review_status": "pending"},
        {"policy_evidence_ref": ""},
        {"review_evidence_ref": ""},
    ],
)
def test_ineligible_or_missing_policy_review_evidence_never_enters_adapter(tmp_path, stamp_changes):
    adapter, _, _, expected = _adapter(
        tmp_path,
        [_record(stamp=_stamp(**stamp_changes))],
    )
    assert adapter.discover(**expected).items == ()


def test_tampered_record_commitment_is_recomputed_before_use(tmp_path):
    private_marker = "tampered-private-marker"
    adapter, _, snapshot, expected = _adapter(tmp_path)
    record = snapshot.eligible_records[0]
    changed = _thaw(record.record)
    changed["text"] = private_marker
    object.__setattr__(record, "_record", _freeze(changed))

    with pytest.raises(MemorySourceAdapterError, match="^invalid_snapshot$") as caught:
        adapter.discover(**expected)
    assert private_marker not in str(caught.value)
    assert private_marker not in repr(caught.value)


def test_snapshot_commitment_and_binding_reject_tamper(tmp_path):
    adapter, binding, snapshot, expected = _adapter(tmp_path)
    object.__setattr__(snapshot, "snapshot_digest", "sha256:" + "1" * 64)
    with pytest.raises(MemorySourceAdapterError, match="^(invalid_snapshot|stale_authority)$"):
        adapter.discover(**expected)

    object.__setattr__(snapshot, "snapshot_digest", binding.snapshot_digest)
    object.__setattr__(binding, "source_digest", "sha256:" + "2" * 64)
    with pytest.raises(MemorySourceAdapterError, match="^invalid_binding$"):
        adapter.discover(**expected)


def test_check_use_replacement_is_rejected(monkeypatch, tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    adapter, _, _, expected = _adapter(first_dir, [_record(text="first")])
    _, _, replacement, _ = _adapter(second_dir, [_record(text="replacement")])
    real_capture = memory_source_module._capture_snapshot

    def replacing_capture(value, **kwargs):
        captured = real_capture(value, **kwargs)
        object.__setattr__(adapter, "_snapshot", replacement)
        return captured

    monkeypatch.setattr(memory_source_module, "_capture_snapshot", replacing_capture)
    with pytest.raises(MemorySourceAdapterError, match="^stale_authority$"):
        adapter.discover(**expected)


def test_hostile_scalars_mappings_and_sequences_fail_without_foreign_dispatch(tmp_path):
    adapter, binding, snapshot, expected = _adapter(tmp_path)

    class HostileStr(str):
        def __hash__(self):
            raise AssertionError("secret-hostile-hash")

        def __eq__(self, other):
            raise AssertionError("secret-hostile-eq")

    with pytest.raises(MemorySourceAdapterError, match="^invalid_request$"):
        adapter.discover(
            expected_binding_digest=HostileStr(binding.binding_digest),
            expected_snapshot_digest=snapshot.snapshot_digest,
        )
    with pytest.raises(MemorySourceAdapterError, match="^invalid_request$"):
        adapter.observe_version({"record": "foreign"}, **expected)
    with pytest.raises(MemorySourceAdapterError, match="^invalid_request$"):
        adapter.read_exact(["foreign"], **expected)

    object.__setattr__(snapshot, "eligible_records", list(snapshot.eligible_records))
    with pytest.raises(MemorySourceAdapterError, match="^invalid_snapshot$"):
        adapter.discover(**expected)


def test_adapter_limits_records_depth_nodes_and_text(tmp_path):
    snapshot = _snapshot(
        tmp_path,
        [
            _record(memory_id="a", extra={"nested": {"value": 1}}),
            _record(memory_id="b", text="another"),
        ],
    )
    binding = create_memory_source_authority_binding(owner_scope=ALICE_SCOPE, snapshot=snapshot)

    with pytest.raises(MemorySourceAdapterError, match="^invalid_snapshot$"):
        MemorySourceAdapter(binding=binding, snapshot=snapshot, max_records=1)
    with pytest.raises(MemorySourceAdapterError, match="^invalid_snapshot$"):
        MemorySourceAdapter(binding=binding, snapshot=snapshot, max_depth=3)
    with pytest.raises(MemorySourceAdapterError, match="^invalid_snapshot$"):
        MemorySourceAdapter(binding=binding, snapshot=snapshot, max_nodes=5)
    with pytest.raises(MemorySourceAdapterError, match="^invalid_snapshot$"):
        MemorySourceAdapter(binding=binding, snapshot=snapshot, max_total_text_chars=10)


def test_invalid_constructor_inputs_are_bounded(tmp_path):
    _, binding, snapshot, _ = _adapter(tmp_path)
    for bad_binding, bad_snapshot in (
        ([], snapshot),
        (binding, []),
        ({"binding": "foreign"}, snapshot),
        (binding, {"snapshot": "foreign"}),
    ):
        with pytest.raises(MemorySourceAdapterError):
            MemorySourceAdapter(binding=bad_binding, snapshot=bad_snapshot)

    with pytest.raises(MemorySourceAdapterError, match="^invalid_request$"):
        MemorySourceAdapter(binding=binding, snapshot=snapshot, max_records=True)


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("_max_records", True),
        ("_max_depth", 10**9),
        ("_max_nodes", 0),
        ("_max_total_text_chars", "unbounded"),
    ),
)
def test_operation_recaptures_reject_tampered_adapter_limits(
    tmp_path,
    attribute,
    value,
):
    adapter, _, _, expected = _adapter(tmp_path)
    object.__setattr__(adapter, attribute, value)

    with pytest.raises(MemorySourceAdapterError, match="^invalid_request$"):
        adapter.discover(**expected)


def test_output_policy_cannot_be_weakened_and_records_are_immutable(tmp_path):
    adapter, _, _, expected = _adapter(tmp_path)
    occurrence = adapter.extract(_first_descriptor(adapter, expected).evidence.record_ref, **expected)

    assert occurrence.source.classification is Classification.SENSITIVE
    assert occurrence.source_version.classification is Classification.SENSITIVE
    assert occurrence.chunk.classification is Classification.SENSITIVE
    assert occurrence.source.content_policy is ContentPolicy.INLINE_LOCAL
    with pytest.raises(FrozenInstanceError):
        occurrence.chunk.classification = Classification.PUBLIC
    with pytest.raises(Exception):
        ChunkRecord.create(
            occurrence.source_version,
            locator=occurrence.locator.text_range,
            extractor_profile_ref="memory.weaken.v1",
            content_hash=occurrence.chunk.content_hash,
            content=occurrence.chunk.content,
            classification=Classification.PUBLIC,
        )


def test_static_import_boundary_has_no_memory_manager_provider_fs_network_or_runtime():
    source_path = Path(memory_source_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    rendered = source_path.read_text(encoding="utf-8")

    forbidden_imports = {
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "requests",
        "httpx",
        "src.memory",
        "src.memory_provider",
        "src.memory_lifecycle",
        "src.memory_write_policy",
        "src.unified_source_index_stores",
        "src.rag_text_chunking",
    }
    assert not (imports & forbidden_imports)
    assert "MemoryManager" not in rendered
    assert "capture_memory_owner_eligibility_snapshot" not in rendered
    assert ".open(" not in rendered


def test_errors_and_reprs_do_not_leak_content_owner_or_path(tmp_path):
    private_marker = "never-leak-this-marker"
    adapter, binding, snapshot, expected = _adapter(tmp_path, [_record(text=private_marker)])
    descriptor = _first_descriptor(adapter, expected)
    with pytest.raises(MemorySourceAdapterError, match="^invalid_request$") as caught:
        MemoryRecordFieldLocator(
            record_ref=descriptor.locator.record_ref,
            record_digest=descriptor.locator.record_digest,
            snapshot_digest=descriptor.locator.snapshot_digest,
            binding_digest=descriptor.locator.binding_digest,
            text_range=descriptor.locator.text_range,
            locator_digest="sha256:" + "0" * 64,
        )
    rendered = str(caught.value) + repr(caught.value)
    assert private_marker not in rendered
    assert "alice" not in rendered
    assert str(tmp_path) not in rendered
    assert binding.owner_scope.value not in rendered
    assert snapshot.owner_ref not in rendered


def test_error_constructor_rejects_hostile_code_without_dispatch():
    class HostileCode(str):
        def __hash__(self):
            raise AssertionError("private")

        def __eq__(self, other):
            raise AssertionError("private")

    error = MemorySourceAdapterError(HostileCode("invalid_request"))
    assert error.code == "memory_source_adapter_failed"
    assert str(error) == "memory_source_adapter_failed"


def test_exported_value_object_constructors_detach_nested_inputs(tmp_path):
    adapter, binding, _, expected = _adapter(tmp_path)
    descriptor = _first_descriptor(adapter, expected)
    occurrence = adapter.extract(descriptor.evidence.record_ref, **expected)
    exact = adapter.read_exact(occurrence.locator, **expected)

    scope_alias = OwnerScope(binding.owner_scope.value)
    detached_binding = MemorySourceAuthorityBinding(
        owner_scope=scope_alias,
        owner_ref=binding.owner_ref,
        source_digest=binding.source_digest,
        snapshot_digest=binding.snapshot_digest,
        adapter_id=binding.adapter_id,
        adapter_version=binding.adapter_version,
        adapter_generation=binding.adapter_generation,
    )
    range_alias = TextRangeLocator(
        descriptor.locator.text_range.start_char,
        descriptor.locator.text_range.end_char,
    )
    detached_locator = MemoryRecordFieldLocator(
        record_ref=descriptor.locator.record_ref,
        record_digest=descriptor.locator.record_digest,
        snapshot_digest=descriptor.locator.snapshot_digest,
        binding_digest=descriptor.locator.binding_digest,
        text_range=range_alias,
    )
    detached_descriptor = MemorySourceDescriptor(
        descriptor.source,
        descriptor.source_version,
        descriptor.locator,
        descriptor.evidence,
    )
    detached_occurrence = MemorySourceOccurrence(
        occurrence.source,
        occurrence.source_version,
        occurrence.chunk,
        occurrence.locator,
        occurrence.evidence,
    )
    detached_exact = MemoryExactRead(
        exact.record_ref,
        exact.content,
        exact.content_hash,
        exact.locator,
        exact.evidence,
    )
    detached_page = MemoryDiscoveryPage(
        (descriptor,),
        None,
        expected["expected_snapshot_digest"],
        expected["expected_binding_digest"],
    )

    assert detached_binding.owner_scope is not scope_alias
    assert detached_locator.text_range is not range_alias
    assert detached_descriptor.source is not descriptor.source
    assert detached_descriptor.source_version is not descriptor.source_version
    assert detached_descriptor.locator is not descriptor.locator
    assert detached_descriptor.evidence is not descriptor.evidence
    assert detached_occurrence.source is not occurrence.source
    assert detached_occurrence.source_version is not occurrence.source_version
    assert detached_occurrence.chunk is not occurrence.chunk
    assert detached_occurrence.locator is not occurrence.locator
    assert detached_occurrence.evidence is not occurrence.evidence
    assert detached_exact.locator is not exact.locator
    assert detached_exact.evidence is not exact.evidence
    assert detached_page.items[0] is not descriptor

    original_provider = detached_descriptor.source.provider_ref
    original_source_id = detached_descriptor.source_version.source_id
    original_record_ref = detached_descriptor.locator.record_ref
    original_chunk_content = detached_occurrence.chunk.content
    original_range_end = detached_locator.text_range.end_char
    original_scope = detached_binding.owner_scope.value
    object.__setattr__(scope_alias, "value", BOB_SCOPE.value)
    object.__setattr__(range_alias, "end_char", range_alias.end_char + 1)
    object.__setattr__(descriptor.source, "provider_ref", "mutated.alias")
    object.__setattr__(descriptor.source_version, "source_id", "usi_source_" + "0" * 64)
    object.__setattr__(descriptor.locator, "record_ref", "memory:record:" + "0" * 64)
    object.__setattr__(descriptor.evidence, "record_ref", "memory:record:" + "1" * 64)
    object.__setattr__(occurrence.chunk, "content", "mutated caller content")

    assert detached_binding.owner_scope.value == original_scope
    assert detached_locator.text_range.end_char == original_range_end
    assert detached_descriptor.source.provider_ref == original_provider
    assert detached_descriptor.source_version.source_id == original_source_id
    assert detached_descriptor.locator.record_ref == original_record_ref
    assert detached_occurrence.chunk.content == original_chunk_content
    assert detached_exact.record_ref == original_record_ref
    assert detached_page.items[0].source.provider_ref == original_provider


def test_exported_value_object_constructors_reserialize_hostile_nested_failures(
    monkeypatch,
    tmp_path,
):
    adapter, _, _, expected = _adapter(tmp_path)
    descriptor = _first_descriptor(adapter, expected)
    occurrence = adapter.extract(descriptor.evidence.record_ref, **expected)
    private_marker = "constructor-private-marker"

    def assert_closed(callback, expected_code="invalid_snapshot"):
        with pytest.raises(MemorySourceAdapterError) as caught:
            callback()
        assert caught.value.code == expected_code
        assert caught.value.__cause__ is None
        assert private_marker not in str(caught.value)
        assert private_marker not in repr(caught.value)

    class SourceRecordSubclass(SourceRecord):
        pass

    subclass = SourceRecordSubclass.from_json(descriptor.source.to_json())
    assert_closed(
        lambda: MemorySourceDescriptor(
            subclass,
            descriptor.source_version,
            descriptor.locator,
            descriptor.evidence,
        )
    )

    class HostileClassSurface:
        @property
        def __class__(self):
            raise AssertionError(private_marker)

        @property
        def to_json(self):
            raise AssertionError(private_marker)

    assert_closed(
        lambda: MemorySourceDescriptor(
            HostileClassSurface(),
            descriptor.source_version,
            descriptor.locator,
            descriptor.evidence,
        )
    )

    class HostileProperty:
        @property
        def to_dict(self):
            raise AssertionError(private_marker)

    hostile_property_source = SourceRecord.from_json(descriptor.source.to_json())
    object.__setattr__(hostile_property_source, "canonical_ref", HostileProperty())
    assert_closed(
        lambda: MemorySourceDescriptor(
            hostile_property_source,
            descriptor.source_version,
            descriptor.locator,
            descriptor.evidence,
        )
    )

    dispatches = []

    class HostileComparable(str):
        def __eq__(self, other):
            dispatches.append("eq")
            raise AssertionError(private_marker)

        def __hash__(self):
            dispatches.append("hash")
            raise AssertionError(private_marker)

        def encode(self, *args, **kwargs):
            dispatches.append("encode")
            raise AssertionError(private_marker)

    hostile_evidence = MemoryRecordEvidence(
        descriptor.evidence.record_ref,
        descriptor.evidence.record_digest,
        descriptor.evidence.source_digest,
        descriptor.evidence.snapshot_digest,
        descriptor.evidence.binding_digest,
        descriptor.evidence.policy_evidence_ref,
        descriptor.evidence.review_evidence_ref,
        descriptor.evidence.source_id,
        descriptor.evidence.source_version_id,
    )
    object.__setattr__(
        hostile_evidence,
        "record_ref",
        HostileComparable(hostile_evidence.record_ref),
    )
    assert_closed(
        lambda: MemorySourceDescriptor(
            descriptor.source,
            descriptor.source_version,
            descriptor.locator,
            hostile_evidence,
        )
    )
    assert dispatches == []

    hostile_unicode_source = SourceRecord.from_json(descriptor.source.to_json())
    object.__setattr__(hostile_unicode_source, "canonical_ref", "\ud800")
    assert_closed(
        lambda: MemorySourceDescriptor(
            hostile_unicode_source,
            descriptor.source_version,
            descriptor.locator,
            descriptor.evidence,
        )
    )

    oversized_descriptor = MemorySourceDescriptor(
        descriptor.source,
        descriptor.source_version,
        descriptor.locator,
        descriptor.evidence,
    )
    object.__setattr__(oversized_descriptor, "source", HostileClassSurface())
    assert_closed(
        lambda: MemoryDiscoveryPage(
            (oversized_descriptor,) * (MAX_DISCOVERY_LIMIT + 1),
            None,
            expected["expected_snapshot_digest"],
            expected["expected_binding_digest"],
        )
    )
    assert_closed(
        lambda: MemoryDiscoveryPage(
            [descriptor],
            None,
            expected["expected_snapshot_digest"],
            expected["expected_binding_digest"],
        )
    )

    source_alias = SourceRecord.from_json(descriptor.source.to_json())
    original_to_json = SourceRecord.to_json

    def mutate_after_capture(value):
        serialized = original_to_json(value)
        object.__setattr__(value, "provider_ref", "mutated.after.capture")
        return serialized

    with monkeypatch.context() as scoped:
        scoped.setattr(SourceRecord, "to_json", mutate_after_capture)
        detached = MemorySourceDescriptor(
            source_alias,
            descriptor.source_version,
            descriptor.locator,
            descriptor.evidence,
        )
    assert source_alias.provider_ref == "mutated.after.capture"
    assert detached.source.provider_ref == MEMORY_SOURCE_ADAPTER_ID

    with monkeypatch.context() as scoped:
        scoped.setattr(
            SourceRecord,
            "to_json",
            lambda _value: (_ for _ in ()).throw(AssertionError(private_marker)),
        )
        assert_closed(
            lambda: MemorySourceOccurrence(
                occurrence.source,
                occurrence.source_version,
                occurrence.chunk,
                occurrence.locator,
                occurrence.evidence,
            )
        )

    sentinel = MemorySourceAdapterError("invalid_request")
    sentinel.code = private_marker
    sentinel.args = (private_marker,)
    sentinel.__cause__ = RuntimeError(private_marker)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            SourceRecord,
            "to_json",
            lambda _value: (_ for _ in ()).throw(sentinel),
        )
        with pytest.raises(MemorySourceAdapterError) as caught:
            MemorySourceDescriptor(
                descriptor.source,
                descriptor.source_version,
                descriptor.locator,
                descriptor.evidence,
            )
        assert caught.value is not sentinel
        assert caught.value.code == "invalid_snapshot"
        assert caught.value.__cause__ is None
        assert private_marker not in str(caught.value)
        assert private_marker not in repr(caught.value)
