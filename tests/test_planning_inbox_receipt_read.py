from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningSubjectBoundReceipt,
)
from tests.planning_durable_store_fixture import _canonical, durable_planning_store
from tests.test_planning_revision_store import (
    AUTH_CONTEXT,
    AUTH_NOW,
    OWNER,
    PROJECT,
    ROADMAP,
    _accepted_h1r03_store,
    _durable_authority,
    _durable_payload,
)


def _fixture_head(fixture):
    store, principal = fixture["store"], fixture["principal"]
    owner, _batch, _records, heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    selected = min(heads, key=lambda item: item["roadmap_id"])
    return store.get_head(
        principal, owner_id=owner, project_id="harbour-one",
        roadmap_id=selected["roadmap_id"],
    )


def _next_payload(store, principal, head, title: str) -> bytes:
    payload = store.read_payload(
        principal, owner_id=head.owner_id, revision_id=head.current_revision_id,
    )
    document = json.loads(payload.decode("utf-8"))
    roadmap = document["roadmaps"][0]
    roadmap["revision"] += 1
    roadmap["title"] = title
    from src.planning_definition_contract import compute_roadmap_content_hash

    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"] = {
        roadmap["roadmap_id"]: {
            "revision": roadmap["revision"],
            "content_hash": roadmap["content_hash"],
        },
    }
    document["project"]["draft_refs"] = []
    return _canonical(document)


def test_subject_bound_reader_returns_none_for_migration_only_and_blocks_ordinary_create(tmp_path):
    store, _issuer, principal, _root, manifest = _accepted_h1r03_store(tmp_path)
    project = manifest["project_id"]
    roadmap = manifest["source_items"][0]["roadmap_id"]
    current = store.get_head(
        principal, owner_id=manifest["owner_id"], project_id=project, roadmap_id=roadmap,
    )
    assert store.read_subject_bound_current_receipt(
        principal, project_id=project, roadmap_id=roadmap, expected_head=current,
    ) is None
    unbound, unbound_issuer, _legacy_principal, _unbound_root = _durable_authority(tmp_path / "unbound")
    unbound_principal = unbound_issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"receipt-reader-unbound-subject").hexdigest()[:32],
        owner_id=OWNER, auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600,
        nonce="receipt-reader-unbound-principal",
    )
    created = unbound.create_project(
        unbound_principal, owner_id=OWNER, project_id=PROJECT, roadmap_id=ROADMAP,
        payload_bytes=_durable_payload(1, "unbound-create"),
        idempotency_key="receipt-reader-unbound-create", expected_generation=0,
    )
    with pytest.raises(DurablePlanningStoreError) as denied:
        unbound.read_subject_bound_current_receipt(
            unbound_principal, project_id=PROJECT, roadmap_id=ROADMAP,
            expected_head=created.after_head,
        )
    assert denied.value.code == "not_found"


def test_subject_bound_reader_tracks_append_approve_revert_across_restart(durable_planning_store):
    fixture = durable_planning_store
    store, principal, issuer = fixture["store"], fixture["principal"], fixture["issuer"]
    current = _fixture_head(fixture)
    owner, project, roadmap = current.owner_id, current.project_id, current.roadmap_id
    before = store.read_subject_bound_current_receipt(
        principal, project_id=project, roadmap_id=roadmap, expected_head=current,
    )
    assert before is not None and before.operation == "approve_revision"
    appended = store.append_revision(
        principal, owner_id=owner, project_id=project, roadmap_id=roadmap,
        payload_bytes=_next_payload(store, principal, current, "receipt-reader-append"),
        expected_head=current, idempotency_key="receipt-reader-append-0001",
    )
    appended_result = store.read_subject_bound_current_receipt(
        principal, project_id=project, roadmap_id=roadmap, expected_head=appended.after_head,
    )
    assert appended_result is not None and appended_result.operation == "append_revision"
    with pytest.raises(DurablePlanningStoreError) as stale:
        store.read_subject_bound_current_receipt(
            principal, project_id=project, roadmap_id=roadmap, expected_head=current,
        )
    assert stale.value.code == "stale_authority"
    approved = store.approve_revision(
        principal, owner_id=owner, project_id=project, roadmap_id=roadmap,
        revision_id=appended.new_revision_id, expected_head=appended.after_head,
        idempotency_key="receipt-reader-approve-0001",
    )
    with pytest.raises(DurablePlanningStoreError) as stale_after_approve:
        store.read_subject_bound_current_receipt(
            principal, project_id=project, roadmap_id=roadmap, expected_head=appended.after_head,
        )
    assert stale_after_approve.value.code == "stale_authority"
    result = store.read_subject_bound_current_receipt(
        principal, project_id=project, roadmap_id=roadmap, expected_head=approved.after_head,
    )
    assert result is not None and result.operation == "approve_revision"
    reverted = store.revert_head(
        principal, owner_id=owner, project_id=project, roadmap_id=roadmap,
        ancestor_revision_id=current.current_revision_id, expected_head=approved.after_head,
        idempotency_key="receipt-reader-revert-0001",
    )
    with pytest.raises(DurablePlanningStoreError) as stale_after_revert:
        store.read_subject_bound_current_receipt(
            principal, project_id=project, roadmap_id=roadmap, expected_head=approved.after_head,
        )
    assert stale_after_revert.value.code == "stale_authority"
    result = store.read_subject_bound_current_receipt(
        principal, project_id=project, roadmap_id=roadmap, expected_head=reverted.after_head,
    )
    assert result is not None and result.operation == "revert_head"
    restarted = DurablePlanningRevisionStore(
        store.root, trusted_issuers=(issuer,), clock=store._clock,
    )
    assert restarted.read_subject_bound_current_receipt(
        principal, project_id=project, roadmap_id=roadmap, expected_head=reverted.after_head,
    ) == result


def test_subject_bound_reader_is_opaque_frozen_and_non_mutating(durable_planning_store):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    head = _fixture_head(fixture)
    before = store._path.read_bytes()
    result = store.read_subject_bound_current_receipt(
        principal, project_id=head.project_id, roadmap_id=head.roadmap_id, expected_head=head,
    )
    assert type(result) is PlanningSubjectBoundReceipt
    assert [field.name for field in fields(result)] == [
        "receipt_handle", "current_binding_digest", "operation", "producer",
        "occurred_at", "verified", "authorizes_mutation",
    ]
    assert result.verified is True and result.authorizes_mutation is False
    assert result.receipt_handle.startswith("sha256:")
    assert result.current_binding_digest.startswith("sha256:")
    assert not any(hasattr(result, name) for name in ("receipt_id", "subject_id", "input_digest", "after_head"))
    with pytest.raises(FrozenInstanceError):
        result.operation = "append_revision"
    assert store._path.read_bytes() == before


def test_subject_bound_reader_rejects_foreign_subject_and_issuer(tmp_path):
    store, issuer, principal, root, manifest = _accepted_h1r03_store(tmp_path)
    project = manifest["project_id"]
    roadmap = manifest["source_items"][0]["roadmap_id"]
    head = store.get_head(principal, owner_id=manifest["owner_id"], project_id=project, roadmap_id=roadmap)
    replacement = issuer.issue(
        subject_id="owner_" + hashlib.sha256(b"replacement").hexdigest()[:32],
        owner_id=manifest["owner_id"], auth_context_digest=AUTH_CONTEXT,
        ttl_seconds=3600, nonce="receipt-reader-replacement",
    )
    with pytest.raises(DurablePlanningStoreError) as replaced:
        store.read_subject_bound_current_receipt(
            replacement, project_id=project, roadmap_id=roadmap, expected_head=head,
        )
    assert replaced.value.code == "migration_required"
    other_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="other-receipt-reader-issuer", key_id="planning-key-1",
        secret=b"other-receipt-reader-secret-0001", clock=lambda: AUTH_NOW,
    )
    foreign = other_issuer.issue(
        subject_id=principal.subject_id, owner_id=manifest["owner_id"],
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="receipt-reader-foreign",
    )
    trusted = DurablePlanningRevisionStore(root, trusted_issuers=(issuer, other_issuer), clock=lambda: AUTH_NOW)
    with pytest.raises(DurablePlanningStoreError) as denied:
        trusted.read_subject_bound_current_receipt(
            foreign, project_id=project, roadmap_id=roadmap, expected_head=head,
        )
    assert denied.value.code == "migration_required"


def test_subject_bound_reader_preserves_final_retained_image_fence(durable_planning_store, monkeypatch):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    head = _fixture_head(fixture)
    original_read, reads = Path.read_bytes, []

    def controlled_read(path):
        if path == store._path:
            reads.append(1)
            if len(reads) == 2:
                path.write_bytes(original_read(path) + b"\n")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", controlled_read)
    with pytest.raises(DurablePlanningStoreError) as stale:
        store.read_subject_bound_current_receipt(
            principal, project_id=head.project_id, roadmap_id=head.roadmap_id, expected_head=head,
        )
    assert stale.value.code == "stale_authority"


def test_subject_bound_reader_rejects_malformed_persisted_receipt_time(durable_planning_store):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    head = _fixture_head(fixture)
    image = json.loads(store._path.read_bytes())
    transition = next(
        item for item in reversed(image["transitions"])
        if "batch_id" not in item
        and item["after_head"]["roadmap_id"] == head.roadmap_id
        and item["after_head"]["generation"] == head.generation
    )
    image["receipts"][transition["receipt_id"]]["occurred_at"] = "malformedZ"
    store._path.write_bytes(_canonical(image))
    with pytest.raises(DurablePlanningStoreError) as denied:
        store.read_subject_bound_current_receipt(
            principal, project_id=head.project_id, roadmap_id=head.roadmap_id, expected_head=head,
        )
    assert denied.value.code == "validation_failed"


def test_subject_bound_reader_rejects_ambiguous_persisted_ordinary_transitions(durable_planning_store):
    fixture = durable_planning_store
    store, principal = fixture["store"], fixture["principal"]
    head = _fixture_head(fixture)
    image = json.loads(store._path.read_bytes())
    transition = next(
        item for item in reversed(image["transitions"])
        if "batch_id" not in item
        and item["after_head"]["roadmap_id"] == head.roadmap_id
        and item["after_head"]["generation"] == head.generation
    )
    image["transitions"].append(dict(transition))
    store._path.write_bytes(_canonical(image))
    with pytest.raises(DurablePlanningStoreError) as denied:
        store.read_subject_bound_current_receipt(
            principal, project_id=head.project_id, roadmap_id=head.roadmap_id, expected_head=head,
        )
    assert denied.value.code == "validation_failed"
