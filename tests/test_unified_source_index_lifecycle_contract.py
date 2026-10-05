"""Tests-first contract for the ULO-02 lifecycle boundary."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    IndexJobKind,
    IndexJobRecord,
    RecordKind,
    RecordRef,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_jobs import JobStage
from src.unified_source_index_stores import (
    InMemoryUnifiedSourceIndexStore,
    StoreSnapshot,
)
from src.unified_source_index_lifecycle_contract import (
    LIFECYCLE_CHECKPOINT_SCHEMA,
    LIFECYCLE_REQUEST_SCHEMA,
    LifecycleActionKind,
    LifecycleCheckpoint,
    LifecycleContractError,
    LifecycleDisposition,
    LifecycleEffectKind,
    LifecycleErrorCode,
    LifecycleRequest,
    LifecycleScopeKind,
    LifecycleState,
    LifecycleTarget,
    advance_lifecycle,
    lifecycle_checkpoint_from_job,
    make_lifecycle_job,
)


NOW = "2026-08-13T12:00:00Z"
OWNER = "user:alice"
DOMAIN_REV = "rev_" + "a" * 64
POLICY = "ulo_policy_" + "b" * 64
SIGNAL = "usi_change_" + "c" * 64
RESTORE = "usi_restore_" + "d" * 64
PURGE = "usi_purge_" + "e" * 64


def _source(owner_scope: str = OWNER, ref: str = "repo:alpha/doc.txt") -> SourceRecord:
    return SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref=ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="fixture",
        valid_from=NOW,
    )


def _version(source: SourceRecord, revision: str = "rev:one") -> SourceVersionRecord:
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash("body"),
        version_observed_at=NOW,
        indexed_at=NOW,
    )


def _target(source: SourceRecord, *, kind: LifecycleScopeKind = LifecycleScopeKind.SOURCE) -> LifecycleTarget:
    return LifecycleTarget(
        owner_scope=source.owner_scope,
        scope_kind=kind,
        source_ref=source.ref() if kind is not LifecycleScopeKind.OWNER else None,
    )


def _request(
    source: SourceRecord,
    *,
    action: LifecycleActionKind = LifecycleActionKind.DELETE,
    generation: int = 1,
    signal: str = "",
    restore_authority: str = "",
) -> LifecycleRequest:
    if action is LifecycleActionKind.RESTORE and not restore_authority:
        restore_authority = RESTORE
    return LifecycleRequest.create(
        action_kind=action,
        target=_target(source),
        domain_revision_ref=DOMAIN_REV,
        lifecycle_generation=generation,
        policy_version=POLICY,
        change_signal_ref=signal,
        restore_authority_ref=restore_authority,
    )


def _checkpoint(request: LifecycleRequest, *, state: LifecycleState = LifecycleState.REQUESTED) -> LifecycleCheckpoint:
    return LifecycleCheckpoint.create(
        request,
        state=state,
        recorded_at=NOW,
    )


def _snapshot() -> StoreSnapshot:
    return StoreSnapshot(1, "sha256:" + "f" * 64, 1, 0)


def test_ulo02_public_contract_is_exact_and_round_trips_bounded_canonical_json():
    source = _source()
    request = _request(source, signal=SIGNAL)
    assert list(LifecycleActionKind) == [
        LifecycleActionKind.ARCHIVE,
        LifecycleActionKind.ACCESS_REVOKE,
        LifecycleActionKind.SUPERSEDE,
        LifecycleActionKind.DELETE,
        LifecycleActionKind.ERASE,
        LifecycleActionKind.RESTORE,
    ]
    assert request.to_json() == json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":"))
    assert len(request.to_json()) <= 2048
    restored = LifecycleRequest.from_json(request.to_json())
    assert restored == request
    assert request.schema == LIFECYCLE_REQUEST_SCHEMA
    assert LIFECYCLE_CHECKPOINT_SCHEMA.endswith("lifecycle_checkpoint.v1")
    assert set(request.to_dict()) == {
        "schema", "action_kind", "disposition", "target", "domain_revision_ref",
        "lifecycle_generation", "policy_version", "change_signal_ref",
        "restore_authority_ref", "request_id",
    }


def test_ulo02_owner_source_version_targets_are_exact_and_cross_owner_mismatch_fails():
    source = _source()
    version = _version(source)
    target = LifecycleTarget(source.owner_scope, LifecycleScopeKind.SOURCE_VERSION, source.ref(), version.ref())
    assert target.source_ref == source.ref()
    assert target.source_version_ref == version.ref()
    with pytest.raises(LifecycleContractError):
        LifecycleTarget("user:bob", LifecycleScopeKind.OWNER, source.ref())
    with pytest.raises(LifecycleContractError):
        LifecycleTarget(source.owner_scope, LifecycleScopeKind.SOURCE, RecordRef(RecordKind.SOURCE, "usi_source_" + "0" * 64), version.ref())


def test_ulo02_action_dispositions_are_closed_and_request_ids_are_replay_stable():
    source = _source()
    requests = [_request(source, action=action) for action in LifecycleActionKind]
    assert [request.disposition for request in requests] == [
        LifecycleDisposition.UNAVAILABLE,
        LifecycleDisposition.UNAVAILABLE,
        LifecycleDisposition.SUPERSEDED,
        LifecycleDisposition.DELETED,
        LifecycleDisposition.ERASED,
        LifecycleDisposition.ELIGIBLE,
    ]
    assert len({request.request_id for request in requests}) == len(requests)
    assert _request(source).request_id == _request(source).request_id


def test_ulo02_generation_replay_collision_and_weakening_fail_closed():
    source = _source()
    first = _request(source, generation=2)
    assert advance_lifecycle(_checkpoint(first), first, next_state=LifecycleState.QUIESCING, recorded_at=NOW).lifecycle_generation == 2
    with pytest.raises(LifecycleContractError):
        advance_lifecycle(_checkpoint(first), _request(source, generation=1), next_state=LifecycleState.APPLYING, recorded_at=NOW)
    collision = _request(source, generation=2, signal=SIGNAL)
    with pytest.raises(LifecycleContractError):
        advance_lifecycle(_checkpoint(first), collision, next_state=LifecycleState.APPLYING, recorded_at=NOW)


def test_ulo02_state_transitions_persist_in_existing_deletion_job_without_new_store():
    source = _source()
    scope = SourceScope.create((source.policy_evidence(),))
    request = _request(source)
    checkpoint = _checkpoint(request)
    job = make_lifecycle_job(request, source_scope=scope, checkpoint=checkpoint)
    assert isinstance(job, IndexJobRecord)
    assert job.job_kind is IndexJobKind.DELETION
    assert job.profile_ref == "ulo02_v1"
    restored = lifecycle_checkpoint_from_job(job)
    assert restored == checkpoint
    advanced = advance_lifecycle(checkpoint, request, next_state=LifecycleState.QUIESCING, recorded_at=NOW)
    persisted = make_lifecycle_job(request, source_scope=scope, checkpoint=advanced)
    assert lifecycle_checkpoint_from_job(persisted) == advanced


def test_ulo02_change_signal_reference_is_provenance_only_and_cannot_authorize_restore():
    source = _source()
    with pytest.raises(LifecycleContractError):
        _request(source, action=LifecycleActionKind.RESTORE, signal=SIGNAL)
    delete = _request(source, signal=SIGNAL)
    assert delete.change_signal_ref == SIGNAL
    with pytest.raises(LifecycleContractError):
        LifecycleRequest.create(
            action_kind=LifecycleActionKind.RESTORE,
            target=_target(source),
            domain_revision_ref=DOMAIN_REV,
            lifecycle_generation=2,
            policy_version=POLICY,
            change_signal_ref=SIGNAL,
        )


def test_ulo02_errors_are_fresh_fixed_and_content_free():
    source = _source()
    with pytest.raises(LifecycleContractError) as first:
        _request(source, generation=True)
    with pytest.raises(LifecycleContractError) as second:
        _request(source, generation=True)
    assert first.value is not second.value
    assert first.value.code is LifecycleErrorCode.INVALID_REQUEST
    assert second.value.code is first.value.code
    assert first.value.args == ()
    assert first.value.__cause__ is None
    assert "repo:alpha" not in repr(first.value)


def test_ulo02_direct_complete_and_caller_proofs_fail_closed():
    from src.unified_source_index_lifecycle_contract import complete_lifecycle
    from src.unified_source_index_tombstone_policy import TombstoneCommitResult

    source = _source()
    request = _request(source)
    with pytest.raises(LifecycleContractError):
        complete_lifecycle(
            _checkpoint(request),
            request,
            store=InMemoryUnifiedSourceIndexStore(),
            result=object(),
            recorded_at=NOW,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        )
    with pytest.raises(LifecycleContractError) as exc:
        complete_lifecycle(
            _checkpoint(request),
            request,
            store=InMemoryUnifiedSourceIndexStore(),
            result=object.__new__(TombstoneCommitResult),
            recorded_at=NOW,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        )
    assert exc.value.args == ()


def test_ulo02_complete_reruns_exact_store_verification_and_binds_core_commit_ref():
    from src.unified_source_index_lifecycle_contract import complete_lifecycle
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan, plan_tombstones

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    request = _request(source)
    plan = plan_tombstones(store, request, base_snapshot=store.current_snapshot(), limit=10)
    result = commit_tombstone_plan(store, plan)
    completed = complete_lifecycle(
        _checkpoint(request, state=LifecycleState.VERIFYING),
        request,
        store=store,
        result=result,
        recorded_at=NOW,
        completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
    )
    assert completed.state is LifecycleState.COMPLETE
    assert completed.core_commit_ref.startswith("usi_commit_")


def test_ulo02_public_complete_paths_and_tampered_result_fail_before_completion():
    from src.unified_source_index_lifecycle_contract import complete_lifecycle
    from src.unified_source_index_tombstone_policy import commit_tombstone_plan, plan_tombstones

    source = _source()
    request = _request(source)
    with pytest.raises(LifecycleContractError):
        LifecycleCheckpoint.create(
            request, state=LifecycleState.COMPLETE, recorded_at=NOW,
            store_snapshot_ref="usi_snapshot_" + "a" * 64,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
            core_commit_ref="usi_commit_" + "b" * 64,
        )
    with pytest.raises(LifecycleContractError):
        advance_lifecycle(
            _checkpoint(request, state=LifecycleState.VERIFYING), request,
            next_state=LifecycleState.COMPLETE, recorded_at=NOW,
            store_snapshot_ref="usi_snapshot_" + "a" * 64,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        )
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    write.commit()
    result = commit_tombstone_plan(store, plan_tombstones(store, request, base_snapshot=store.current_snapshot(), limit=10))
    object.__setattr__(result, "receipt_digest", "sha256:" + "0" * 64)
    with pytest.raises(LifecycleContractError):
        complete_lifecycle(
            _checkpoint(request, state=LifecycleState.VERIFYING), request, store=store,
            result=result, recorded_at=NOW,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        )


def test_ulo02_complete_rejects_pending_effects_and_erase_without_purge_proof():
    from src.unified_source_index_lifecycle_contract import complete_lifecycle

    source = _source()
    request = _request(source)
    with pytest.raises(LifecycleContractError):
        complete_lifecycle(
            _checkpoint(request),
            request,
            store=InMemoryUnifiedSourceIndexStore(),
            result=object(),
            recorded_at=NOW,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
            pending_effects=(LifecycleEffectKind.PROJECTION_CLEANUP,),
        )
    erase = _request(source, action=LifecycleActionKind.ERASE)
    with pytest.raises(LifecycleContractError):
        complete_lifecycle(
            _checkpoint(erase),
            erase,
            store=InMemoryUnifiedSourceIndexStore(),
            result=object(),
            recorded_at=NOW,
            completed_effects=(LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE),
        )


def test_ulo02_request_projection_rejects_hostile_nested_ref_before_hash_or_getter():
    from src.unified_source_index_lifecycle_contract import _lifecycle_request_projection

    source = _source()
    request = _request(source)

    class Trap:
        def __hash__(self):
            raise AssertionError("hash invoked")

        def __eq__(self, other):
            raise AssertionError("equality invoked")

        def __getattribute__(self, name):
            if name.startswith("__"):
                return object.__getattribute__(self, name)
            raise AssertionError("getter invoked")

    ref = request.target.source_ref
    object.__setattr__(ref, "record_kind", Trap())
    with pytest.raises(LifecycleContractError):
        _lifecycle_request_projection(request)
