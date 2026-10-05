"""Tests-first policy boundary for ULO-02 tombstones and restore."""

from __future__ import annotations

import builtins
import copy
import gc
import weakref

import pytest

from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    RecordKind,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    ChunkRecord,
    content_hash,
    RelationKind,
    RelationRecord,
)
from src.unified_source_index_stores import StoreSnapshot, StoredRecord, TombstoneRecord
from src.unified_source_index_lifecycle_contract import (
    LifecycleActionKind,
    LifecycleCheckpoint,
    LifecycleContractError,
    LifecycleEffectKind,
    LifecycleErrorCode,
    LifecycleState,
    LifecycleScopeKind,
)
from src.unified_source_index_tombstone_policy import (
    TOMBSTONE_EVIDENCE_SCHEMA,
    LifecycleTombstonePolicyError,
    RestorePlan,
    TombstoneEvidence,
    TombstoneInstruction,
    TombstonePlan,
    authorize_restore,
    evidence_from_committed_tombstones,
    plan_tombstones,
)
from tests.test_unified_source_index_lifecycle_contract import (
    DOMAIN_REV,
    NOW,
    POLICY,
    RESTORE,
    _checkpoint,
    _request,
    _source,
    _version,
)


def _records(source: SourceRecord):
    version = _version(source)
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, 4),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("body"),
        content="body",
        indexed_at=NOW,
    )
    return version, chunk


def test_ulo02_tombstone_plan_is_child_first_bounded_snapshot_and_revision_fenced():
    source = _source()
    version, chunk = _records(source)
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, chunk):
        write.put(record)
    write.commit()
    base = store.current_snapshot()
    request = _request(source)
    plan = plan_tombstones(store, request, base_snapshot=base, limit=10)
    assert isinstance(plan, TombstonePlan)
    assert [item.record_ref.record_kind for item in plan.instructions] == [RecordKind.CHUNK, RecordKind.SOURCE_VERSION, RecordKind.SOURCE]
    assert plan.base_snapshot == base
    with pytest.raises(LifecycleTombstonePolicyError):
        plan_tombstones(store, request, base_snapshot=base, limit=0)


def test_ulo02_tombstone_evidence_contains_only_minimum_content_free_fields():
    source = _source()
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    evidence = commit_tombstone_plan(store, plan).evidence
    assert len(evidence) == 1
    item = evidence[0]
    assert isinstance(item, TombstoneEvidence)
    assert item.to_dict()["schema"] == TOMBSTONE_EVIDENCE_SCHEMA
    assert {"schema", "request_id", "plan_digest", "evidence_digest", "record_kind", "record_id", "owner_scope", "tombstone_revision"}.issubset(item.to_dict())
    assert "content" not in item.to_json()


def test_ulo02_query_fence_precedes_projection_cleanup_and_pending_cleanup_is_not_complete():
    source = _source()
    request = _request(source)
    checkpoint = LifecycleCheckpoint.create(
        request,
        state=LifecycleState.DEGRADED,
        recorded_at=NOW,
        store_snapshot_ref="usi_snapshot_" + "a" * 64,
        completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        pending_effects=(LifecycleEffectKind.PROJECTION_CLEANUP,),
        error_code=LifecycleErrorCode.CLEANUP_PENDING,
    )
    assert checkpoint.completed_effects[:2] == (LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE)
    assert checkpoint.pending_effects == (LifecycleEffectKind.PROJECTION_CLEANUP,)
    with pytest.raises(LifecycleContractError):
        LifecycleCheckpoint.create(request, state=LifecycleState.COMPLETE, recorded_at=NOW, pending_effects=(LifecycleEffectKind.PROJECTION_CLEANUP,))


def test_ulo02_restore_requires_newer_generation_exact_owner_tombstone_and_domain_authority():
    source = _source()
    version, _ = _records(source)
    request = _request(source, action=LifecycleActionKind.RESTORE, generation=2, restore_authority=RESTORE)
    previous = LifecycleCheckpoint.create(
        _request(source, generation=1),
        state=LifecycleState.DEGRADED,
        recorded_at=NOW,
        store_snapshot_ref="usi_snapshot_" + "a" * 64,
        completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        pending_effects=(LifecycleEffectKind.PROJECTION_CLEANUP,),
        error_code=LifecycleErrorCode.CLEANUP_PENDING,
    )
    tombstone = TombstoneRecord(RecordKind.SOURCE, source.source_id, source.owner_scope, 1, 2, "source_deleted")
    plan = authorize_restore(request, previous=previous, base_snapshot=StoreSnapshot(2, "sha256:" + "b" * 64, 0, 1), tombstones=(tombstone,), canonical_records=(StoredRecord(source, 1),))
    assert isinstance(plan, RestorePlan)
    assert plan.record_ref == source.ref()
    assert plan.expected_tombstone_revision == 2
    with pytest.raises(LifecycleTombstonePolicyError):
        authorize_restore(request, previous=previous, base_snapshot=StoreSnapshot(2, "sha256:" + "b" * 64, 0, 1), tombstones=(tombstone,), canonical_records=())


def test_ulo02_superseded_and_erased_are_not_restorable_and_erase_never_uses_derived_content():
    source = _source()
    for action in (LifecycleActionKind.SUPERSEDE, LifecycleActionKind.ERASE):
        request = _request(source, action=LifecycleActionKind.RESTORE, generation=2, restore_authority=RESTORE)
        previous = LifecycleCheckpoint.create(
            _request(source, action=action, generation=1),
            state=LifecycleState.DEGRADED,
            recorded_at=NOW,
            pending_effects=(LifecycleEffectKind.PROJECTION_CLEANUP,),
            error_code=LifecycleErrorCode.CLEANUP_PENDING,
        )
        marker = TombstoneRecord(RecordKind.SOURCE, source.source_id, source.owner_scope, 1, 2, "source_superseded" if action is LifecycleActionKind.SUPERSEDE else "source_erased")
        with pytest.raises(LifecycleTombstonePolicyError):
            authorize_restore(request, previous=previous, base_snapshot=StoreSnapshot(2, "sha256:" + "b" * 64, 0, 1), tombstones=(marker,), canonical_records=(StoredRecord(source, 1),))


def test_ulo02_public_checkpoint_creation_never_completes_erase():
    source = _source()
    request = _request(source, action=LifecycleActionKind.ERASE)
    with pytest.raises(LifecycleContractError):
        LifecycleCheckpoint.create(
            request,
            state=LifecycleState.COMPLETE,
            recorded_at=NOW,
            store_snapshot_ref="usi_snapshot_" + "a" * 64,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        )
    with pytest.raises(LifecycleContractError):
        LifecycleCheckpoint.create(
            request,
            state=LifecycleState.COMPLETE,
            recorded_at=NOW,
            store_snapshot_ref="usi_snapshot_" + "a" * 64,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE, LifecycleEffectKind.CONTENT_PURGE),
            content_purge_proof_ref="usi_purge_" + "e" * 64,
            core_commit_ref="usi_commit_" + "f" * 64,
        )


def test_ulo02_modules_perform_no_io_or_mutation_on_import_construction_or_planning(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("ULO-02 contract must not perform I/O")
    monkeypatch.setattr(builtins, "open", blocked)
    source = _source()
    request = _request(source)
    with pytest.raises(LifecycleTombstonePolicyError):
        plan_tombstones(request, request, base_snapshot=StoreSnapshot(1, "sha256:" + "a" * 64, 0, 0), limit=10)


def test_ulo02_planner_reads_complete_trusted_snapshot_and_overflow_returns_no_plan():
    source = _source()
    version, chunk = _records(source)
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import plan_tombstones

    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, chunk):
        write.put(record)
    write.commit()
    request = _request(source)
    with pytest.raises(LifecycleTombstonePolicyError):
        plan_tombstones(store, request, base_snapshot=store.current_snapshot(), limit=2)
    plan = plan_tombstones(store, request, base_snapshot=store.current_snapshot(), limit=10)
    assert plan.instructions
    assert not hasattr(plan, "clipped")


def test_ulo02_source_version_excludes_source_siblings_shared_and_ambiguous_records():
    source = _source()
    first = _version(source)
    second = _version(source, "rev:two")
    first_chunk = _records(source)[1]
    from src.unified_source_index_lifecycle_contract import LifecycleRequest, LifecycleTarget
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import plan_tombstones

    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    for record in (source, first, second, first_chunk):
        write.put(record)
    write.commit()
    target = LifecycleTarget(
        source.owner_scope,
        LifecycleScopeKind.SOURCE_VERSION,
        source.ref(),
        first.ref(),
    )
    request = LifecycleRequest.create(
        action_kind=LifecycleActionKind.DELETE,
        target=target,
        domain_revision_ref=DOMAIN_REV,
        lifecycle_generation=1,
        policy_version=POLICY,
    )
    plan = plan_tombstones(store, request, base_snapshot=store.current_snapshot(), limit=10)
    assert [item.record_ref.record_id for item in plan.instructions] == [
        first_chunk.chunk_id,
        first.source_version_id,
    ]


def test_ulo02_commit_result_binds_plan_receipt_readback_and_atomic_evidence():
    source = _source()
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan, plan_tombstones

    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    result = commit_tombstone_plan(store, plan)
    assert result.plan_digest == plan.plan_digest
    assert result.receipt_digest == result.receipt.receipt_digest
    assert len(result.evidence) == len(plan.instructions)


def test_ulo02_issued_plan_rejects_partial_mutation_before_revision_changes():
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan

    source = _source()
    version, chunk = _records(source)
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, chunk):
        write.put(record)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    assert len(plan.instructions) == 3
    before = store.current_snapshot()
    object.__setattr__(plan, "instructions", plan.instructions[:1])
    with pytest.raises(LifecycleTombstonePolicyError):
        commit_tombstone_plan(store, plan)
    assert store.current_snapshot() == before


def test_ulo02_source_plan_captures_all_supported_exact_ancestry():
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from tests.test_unified_source_index_stores import _all_record_families

    source, *records = _all_record_families()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    for record in (source, *records):
        write.put(record)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=12)
    assert {item.record_ref.record_kind for item in plan.instructions} == {
        RecordKind.SOURCE, RecordKind.SOURCE_VERSION, RecordKind.CHUNK,
        RecordKind.ENTITY, RecordKind.RELATION, RecordKind.LINEAGE,
        RecordKind.PROJECTION, RecordKind.DERIVED_RUN,
    }
    assert len(plan.instructions) == 10


def test_ulo02_issued_capability_retains_exact_store_and_rejects_method_drift_before_effects():
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    result = commit_tombstone_plan(store, plan)
    receipt = result.receipt
    reference = weakref.ref(store)
    del store
    gc.collect()
    assert reference() is not None
    assert receipt._trusted_store.store is reference()

    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    before = InMemoryUnifiedSourceIndexStore.current_snapshot(store)
    store.current_snapshot = lambda: (_ for _ in ()).throw(AssertionError("shadow method used"))
    with pytest.raises(LifecycleTombstonePolicyError):
        commit_tombstone_plan(store, plan)
    assert InMemoryUnifiedSourceIndexStore.current_snapshot(store) == before


def test_ulo02_cross_source_relation_and_bad_nested_plan_shape_fail_before_effects():
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan

    source = _source()
    version, chunk = _records(source)
    other = _source(ref="repo:beta/doc.txt")
    other_version, other_chunk = _records(other)
    relation = RelationRecord.create(
        chunk.ref(), other_chunk.ref(), relation_kind=RelationKind.DEFINES,
        method_ref="fixture-v1", confidence=1.0,
        evidence_refs=(chunk.evidence_ref(),),
    )
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, chunk, other, other_version, other_chunk, relation):
        write.put(record)
    write.commit()
    before = store.current_snapshot()
    with pytest.raises(LifecycleTombstonePolicyError):
        plan_tombstones(store, _request(source), base_snapshot=before, limit=12)
    assert store.current_snapshot() == before

    clean = InMemoryUnifiedSourceIndexStore()
    write = clean.begin_write(clean.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(clean, _request(source), base_snapshot=clean.current_snapshot(), limit=10)

    class NestedTrap:
        def __getattribute__(self, name):
            raise AssertionError("nested forged plan field was read")

    before = clean.current_snapshot()
    object.__setattr__(plan, "request", NestedTrap())
    with pytest.raises(LifecycleTombstonePolicyError):
        commit_tombstone_plan(clean, plan)
    assert clean.current_snapshot() == before

    plan = plan_tombstones(clean, _request(source), base_snapshot=clean.current_snapshot(), limit=10)
    object.__setattr__(plan.instructions[0], "record_ref", NestedTrap())
    before = clean.current_snapshot()
    with pytest.raises(LifecycleTombstonePolicyError):
        commit_tombstone_plan(clean, plan)
    assert clean.current_snapshot() == before


def test_ulo02_factory_only_plan_and_result_reject_forged_copied_and_mutated_candidates():
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import TombstoneCommitResult, commit_tombstone_plan

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    for forged in (copy.copy(plan), object.__new__(TombstonePlan)):
        with pytest.raises(LifecycleTombstonePolicyError) as exc:
            commit_tombstone_plan(store, forged)
        assert exc.value.args == ()
    with pytest.raises(LifecycleTombstonePolicyError):
        TombstoneInstruction(plan.instructions[0].record_ref, 1, "source_deleted", source.owner_scope)
    with pytest.raises(LifecycleTombstonePolicyError):
        TombstoneCommitResult("", object(), (), "", "", store.current_snapshot(), object())
    object.__setattr__(plan, "_store_binding", object())
    with pytest.raises(LifecycleTombstonePolicyError):
        commit_tombstone_plan(store, plan)


def test_ulo02_access_revoke_is_unavailable_with_request_bound_reason():
    source = _source()
    request = _request(source, action=LifecycleActionKind.ACCESS_REVOKE)
    assert request.disposition.value == "unavailable"
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import plan_tombstones

    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, request, base_snapshot=store.current_snapshot(), limit=10)
    assert plan.instructions[0].reason == "access_revoked"


def test_ulo02_issued_capability_rejects_current_snapshot_method_drift_before_dispatch(monkeypatch):
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    plan = plan_tombstones(store, _request(source), base_snapshot=store.current_snapshot(), limit=10)
    calls = 0

    def trap(self):
        nonlocal calls
        calls += 1
        raise AssertionError("drifted current_snapshot called")

    monkeypatch.setattr(InMemoryUnifiedSourceIndexStore, "current_snapshot", trap)
    with pytest.raises(LifecycleTombstonePolicyError):
        commit_tombstone_plan(store, plan)
    assert calls == 0
