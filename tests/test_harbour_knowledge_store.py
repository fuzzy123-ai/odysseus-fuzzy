from __future__ import annotations

from dataclasses import asdict, FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_store import (
    CAUSAL_CLAIM_PAYLOAD_SCHEMA,
    KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA,
    KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA,
    HarbourKnowledgeStore,
    HarbourKnowledgeStoreError,
    KnowledgeHead,
    KnowledgeHeadMetadata,
    KnowledgeMigrationSnapshot,
    KnowledgeIndexRecord,
    KnowledgeIndexSnapshot,
    KnowledgeInboxSignal,
    KnowledgeInboxSignalSnapshot,
    validate_causal_claim_payload,
)
import src.harbour_knowledge_store as knowledge_module


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64


@pytest.fixture
def authority():
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="harbour-test", key_id="test-key", secret=b"k" * 32, clock=lambda: NOW
    )
    alice = issuer.issue(
        subject_id="alice-user", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=3600
    )
    bob = issuer.issue(
        subject_id="bob-user", owner_id="bob", auth_context_digest=SHA_B, ttl_seconds=3600
    )
    return issuer, alice, bob


def _store(tmp_path, issuer) -> HarbourKnowledgeStore:
    return HarbourKnowledgeStore(
        tmp_path / "knowledge.sqlite3", trusted_issuers=(issuer,), clock=lambda: NOW
    )


def _create(store, principal, *, owner="alice", knowledge_id="note-1", payload=b"one",
            key="create-note-001", retention=None, media_type="text/plain"):
    return store.create_object(
        principal,
        owner_id=owner,
        knowledge_id=knowledge_id,
        knowledge_kind="note",
        payload_bytes=payload,
        payload_schema_id="harbour.note.v1",
        payload_media_type=media_type,
        classification="private",
        retention=retention,
        idempotency_key=key,
    )


def _assert_code(code: str, callback) -> None:
    with pytest.raises(HarbourKnowledgeStoreError) as raised:
        callback()
    assert raised.value.code == code
    assert raised.value.args == (code,)


def _assert_closed_code(code: str, callback) -> None:
    with pytest.raises(HarbourKnowledgeStoreError) as raised:
        callback()
    assert raised.value.code == code
    assert raised.value.args == (code,)
    assert str(raised.value) == code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_create_append_cas_and_exact_idempotency(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    assert created == _create(store, alice)
    head = created.after_head
    assert head.generation == 1
    assert store.read_payload(alice, owner_id="alice", revision_id=created.new_revision_id) == b"one"

    _assert_code(
        "idempotency_conflict",
        lambda: _create(store, alice, payload=b"changed"),
    )
    appended = store.append_revision(
        alice,
        owner_id="alice",
        knowledge_id="note-1",
        payload_bytes=b"two",
        payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain",
        classification="private",
        expected_head=head,
        idempotency_key="append-note-001",
    )
    assert appended.after_head.generation == 2
    assert appended == store.append_revision(
        alice,
        owner_id="alice",
        knowledge_id="note-1",
        payload_bytes=b"two",
        payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain",
        classification="private",
        expected_head=head,
        idempotency_key="append-note-001",
    )
    _assert_code(
        "stale_authority",
        lambda: store.append_revision(
            alice,
            owner_id="alice",
            knowledge_id="note-1",
            payload_bytes=b"three",
            payload_schema_id="harbour.note.v1",
            payload_media_type="text/plain",
            classification="private",
            expected_head=head,
            idempotency_key="append-note-002",
        ),
    )
    store.close()


def test_authentication_and_owner_scope_precede_lookup(tmp_path, authority) -> None:
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    receipt = _create(store, alice)

    for knowledge_id in ("note-1", "does-not-exist"):
        _assert_code(
            "owner_scope_denied",
            lambda knowledge_id=knowledge_id: store.get_head(
                alice, owner_id="bob", knowledge_id=knowledge_id
            ),
        )
    _assert_code(
        "not_found",
        lambda: store.get_head(bob, owner_id="bob", knowledge_id="note-1"),
    )
    _assert_code(
        "not_found",
        lambda: store.read_revision(
            bob, owner_id="bob", revision_id=receipt.new_revision_id
        ),
    )
    _assert_code(
        "unauthenticated",
        lambda: store.get_head(object(), owner_id="alice", knowledge_id="note-1"),
    )
    store.close()


def test_legacy_read_payload_keeps_an_owned_snapshot_across_peer_purge(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    retention = {
        "retention_class": "time_bound", "deletion_mode": "purge_eligible",
        "retain_until": "2026-08-28T00:00:00.000000Z",
        "grace_until": "2026-08-28T12:00:00.000000Z", "legal_hold": False,
    }
    reader = _store(tmp_path, issuer)
    peer = HarbourKnowledgeStore.open_existing(reader.database_path, trusted_issuers=(issuer,), clock=lambda: NOW)
    created = _create(reader, alice, retention=retention, key="create-payload-race")
    connection = reader._connection
    fired = False

    def purge_from_peer() -> None:
        head = peer.get_head(alice, owner_id="alice", knowledge_id="note-1")
        tombstoned = peer.tombstone(
            alice, owner_id="alice", knowledge_id="note-1", expected_head=head, idempotency_key="tombstone-payload-race",
        )
        pending = peer.request_purge(
            alice, owner_id="alice", knowledge_id="note-1", expected_head=tombstoned.after_head,
            idempotency_key="request-payload-race",
        )
        peer.complete_purge(
            alice, owner_id="alice", knowledge_id="note-1", expected_head=pending.after_head,
            policy_receipt=SHA_A, projection_deletion_receipts=(SHA_B,), backup_deletion_receipts=(SHA_C,),
            idempotency_key="complete-payload-race",
        )

    class HookedConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def execute(self, sql, parameters=()):
            nonlocal fired
            if not fired and "select payload_bytes,payload_sha256 from knowledge_blobs" in sql.lower():
                fired = True
                purge_from_peer()
            return self._wrapped.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    reader._connection = HookedConnection(connection)
    assert reader.read_payload(alice, owner_id="alice", revision_id=created.new_revision_id) == b"one"
    assert fired and reader.available and not reader.quarantined and not connection.in_transaction
    _assert_closed_code("content_unavailable", lambda: reader.read_payload(
        alice, owner_id="alice", revision_id=created.new_revision_id,
    ))
    reader.close()
    peer.close()


def test_legacy_read_payload_rejects_borrowed_transaction_without_cleanup(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    connection = store._connection
    connection.execute("BEGIN")
    _assert_closed_code("storage_failed", lambda: store.read_payload(
        alice, owner_id="alice", revision_id=created.new_revision_id,
    ))
    assert connection.in_transaction
    connection.execute("ROLLBACK")
    assert store.read_payload(alice, owner_id="alice", revision_id=created.new_revision_id) == b"one"
    store.close()


def test_legacy_read_payload_owned_storage_failure_rolls_back_and_reuses(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    connection = store._connection
    failed = False

    class FailingConnection:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def execute(self, sql, parameters=()):
            nonlocal failed
            if not failed and "select payload_bytes,payload_sha256 from knowledge_blobs" in sql.lower():
                failed = True
                raise sqlite3.OperationalError()
            return self._wrapped.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    store._connection = FailingConnection(connection)
    _assert_closed_code("storage_failed", lambda: store.read_payload(
        alice, owner_id="alice", revision_id=created.new_revision_id,
    ))
    assert failed and store.available and not store.quarantined and not connection.in_transaction
    store._connection = connection
    assert store.read_payload(alice, owner_id="alice", revision_id=created.new_revision_id) == b"one"
    store.close()


def test_validate_principal_binding_is_read_only_and_context_bound(tmp_path, authority, monkeypatch) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    before = tuple(
        store._conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "knowledge_blobs", "knowledge_revisions", "knowledge_heads", "knowledge_events",
            "knowledge_receipts", "knowledge_idempotency",
        )
    )

    def no_connection() -> sqlite3.Connection:
        pytest.fail("principal binding validation must not access the database")

    monkeypatch.setattr(store, "_conn", no_connection)
    assert store.validate_principal_binding(
        alice, owner_id="alice", expected_auth_context_digest=SHA_A
    ) is None
    _assert_closed_code(
        "auth_context_denied",
        lambda: store.validate_principal_binding(
            alice, owner_id="alice", expected_auth_context_digest=SHA_B
        ),
    )
    after = tuple(
        store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "knowledge_blobs", "knowledge_revisions", "knowledge_heads", "knowledge_events",
            "knowledge_receipts", "knowledge_idempotency",
        )
    )
    assert after == before
    store.close()


def test_validate_principal_binding_has_closed_authentication_precedence(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    secondary = AuthenticatedPrincipalIssuer(
        issuer_id="harbour-test-2", key_id="test-key-2", secret=b"s" * 32, clock=lambda: NOW
    )
    foreign = AuthenticatedPrincipalIssuer(
        issuer_id="foreign", key_id="foreign-key", secret=b"f" * 32, clock=lambda: NOW
    )
    changed_secret = AuthenticatedPrincipalIssuer(
        issuer_id="harbour-test", key_id="test-key", secret=b"x" * 32, clock=lambda: NOW
    )
    expired = AuthenticatedPrincipalIssuer(
        issuer_id="expired", key_id="expired-key", secret=b"e" * 32,
        clock=lambda: datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
    )
    future = AuthenticatedPrincipalIssuer(
        issuer_id="future", key_id="future-key", secret=b"u" * 32,
        clock=lambda: datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc),
    )
    store = HarbourKnowledgeStore(
        tmp_path / "knowledge.sqlite3",
        trusted_issuers=(issuer, secondary, expired, future),
        clock=lambda: NOW,
    )
    secondary_alice = secondary.issue(
        subject_id="second-alice", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=3600
    )
    assert store.validate_principal_binding(
        alice, owner_id="alice", expected_auth_context_digest=SHA_A
    ) is None
    assert store.validate_principal_binding(
        secondary_alice, owner_id="alice", expected_auth_context_digest=SHA_A
    ) is None

    for principal in (
        foreign.issue(subject_id="foreign-alice", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=3600),
        changed_secret.issue(subject_id="forged-alice", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=3600),
        expired.issue(subject_id="expired-alice", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=60),
        future.issue(subject_id="future-alice", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=60),
        object(),
    ):
        _assert_closed_code(
            "unauthenticated",
            lambda principal=principal: store.validate_principal_binding(
                principal, owner_id="alice", expected_auth_context_digest=SHA_A
            ),
        )
    for owner_id in ("bob", "bad owner"):
        _assert_closed_code(
            "owner_scope_denied",
            lambda owner_id=owner_id: store.validate_principal_binding(
                alice, owner_id=owner_id, expected_auth_context_digest=SHA_A
            ),
        )
    _assert_closed_code(
        "unauthenticated",
        lambda: store.validate_principal_binding(
            object(), owner_id="alice", expected_auth_context_digest="not-a-digest"
        ),
    )
    for digest in ("not-a-digest", "sha256:" + "A" * 64):
        _assert_closed_code(
            "validation_failed",
            lambda digest=digest: store.validate_principal_binding(
                alice, owner_id="alice", expected_auth_context_digest=digest
            ),
        )
    store.close()


def test_restart_replays_heads_and_exact_payload(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    receipt = _create(store, alice)
    expected = receipt.after_head
    store.close()

    reopened = _store(tmp_path, issuer)
    assert reopened.available
    assert reopened.get_head(alice, owner_id="alice", knowledge_id="note-1") == expected
    assert reopened.read_payload(
        alice, owner_id="alice", revision_id=receipt.new_revision_id
    ) == b"one"
    changes = reopened.changes_since(alice, owner_id="alice", after_event_seq=0)
    assert len(changes) == 1
    assert changes[0]["command_kind"] == "create_object"
    reopened.close()


def test_corruption_is_quarantined_read_only_without_reinitialization(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    store = _store(tmp_path, issuer)
    _create(store, alice)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER knowledge_events_no_update")
    connection.commit()
    connection.close()

    reopened = _store(tmp_path, issuer)
    assert reopened.quarantined
    assert not reopened.available
    _assert_code(
        "store_corrupt",
        lambda: reopened.get_head(alice, owner_id="alice", knowledge_id="note-1"),
    )
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0] == 1
    reopened.close()


def test_tombstone_blocks_payload_and_restore_recovers_it(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    tombstone = store.tombstone(
        alice, owner_id="alice", knowledge_id="note-1",
        expected_head=created.after_head, idempotency_key="tombstone-note-001",
    )
    assert tombstone.after_head.state == "tombstoned"
    _assert_code(
        "content_unavailable",
        lambda: store.read_payload(
            alice, owner_id="alice", revision_id=created.new_revision_id
        ),
    )
    restored = store.restore(
        alice, owner_id="alice", knowledge_id="note-1",
        expected_head=tombstone.after_head, idempotency_key="restore-note-001",
    )
    assert restored.after_head.state == "active"
    assert store.read_payload(
        alice, owner_id="alice", revision_id=created.new_revision_id
    ) == b"one"
    store.close()


def test_purge_requires_closed_evidence_and_erases_only_payload_blobs(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    retention = {
        "retention_class": "time_bound",
        "deletion_mode": "purge_eligible",
        "retain_until": "2026-08-28T00:00:00.000000Z",
        "grace_until": "2026-08-28T12:00:00.000000Z",
        "legal_hold": False,
    }
    store = _store(tmp_path, issuer)
    created = _create(store, alice, retention=retention)
    tombstoned = store.tombstone(
        alice, owner_id="alice", knowledge_id="note-1",
        expected_head=created.after_head, idempotency_key="tombstone-note-001",
    )
    pending = store.request_purge(
        alice, owner_id="alice", knowledge_id="note-1",
        expected_head=tombstoned.after_head, idempotency_key="purge-request-001",
    )
    _assert_code(
        "validation_failed",
        lambda: store.complete_purge(
            alice, owner_id="alice", knowledge_id="note-1", expected_head=pending.after_head,
            policy_receipt=SHA_A, projection_deletion_receipts=(), backup_deletion_receipts=(SHA_C,),
            idempotency_key="purge-complete-001",
        ),
    )
    purged = store.complete_purge(
        alice, owner_id="alice", knowledge_id="note-1", expected_head=pending.after_head,
        policy_receipt=SHA_A, projection_deletion_receipts=(SHA_B,), backup_deletion_receipts=(SHA_C,),
        idempotency_key="purge-complete-001",
    )
    assert purged.after_head.state == "purged"
    assert store.read_revision(
        alice, owner_id="alice", revision_id=created.new_revision_id
    ).payload_sha256 == "sha256:" + hashlib.sha256(b"one").hexdigest()
    _assert_code(
        "content_unavailable",
        lambda: store.read_payload(
            alice, owner_id="alice", revision_id=created.new_revision_id
        ),
    )
    store.close()
    reopened = _store(tmp_path, issuer)
    assert reopened.available
    assert reopened.get_head(alice, owner_id="alice", knowledge_id="note-1").state == "purged"
    reopened.close()
    with sqlite3.connect(tmp_path / "knowledge.sqlite3") as check:
        assert check.execute("SELECT COUNT(*) FROM knowledge_blobs").fetchone()[0] == 0
        assert check.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0] == 1
        assert check.execute("SELECT COUNT(*) FROM knowledge_events").fetchone()[0] == 4


def _causal_payload(**changes) -> bytes:
    body = {
        "legacy_claim_ref": SHA_A,
        "legacy_claim_digest": SHA_B,
        "claim_id": "claim-1",
        "claim_version": 1,
        "policy_ref": "policy-1",
        "source_version": SHA_C,
        "candidate_actor_ref": "legacy-actor-1",
        "evidence_refs": [SHA_A, SHA_B],
        "epistemic_state": "candidate",
        "legacy_head_sequence": 0,
        "legacy_head_digest": SHA_C,
        "transition": None,
    }
    body.update(changes)
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _causal_transition(
    from_state: str,
    to_state: str,
    event_id: str,
    *,
    relation_kind: str = "none",
    related_claim_ref: str | None = None,
    prior_claim_version: int | None = None,
    reason_code: str | None = None,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "from_state": from_state,
        "to_state": to_state,
        "reviewer_ref": "independent-reviewer",
        "evidence_refs": [SHA_A],
        "relation_kind": relation_kind,
        "related_claim_ref": related_claim_ref,
        "prior_claim_version": prior_claim_version,
        "reason_code": reason_code,
    }


def _create_causal(store, principal, *, knowledge_id="causal-1", key="create-causal-001"):
    return store.create_object(
        principal,
        owner_id="alice",
        knowledge_id=knowledge_id,
        knowledge_kind="causal_claim",
        payload_bytes=_causal_payload(),
        payload_schema_id=CAUSAL_CLAIM_PAYLOAD_SCHEMA,
        payload_media_type="application/json",
        classification="private",
        idempotency_key=key,
    )


def _append_causal(store, principal, head, payload, key):
    return store.append_revision(
        principal,
        owner_id="alice",
        knowledge_id="causal-1",
        payload_bytes=payload,
        payload_schema_id=CAUSAL_CLAIM_PAYLOAD_SCHEMA,
        payload_media_type="application/json",
        classification="private",
        expected_head=head,
        idempotency_key=key,
    )


def test_causal_claim_payload_is_closed_and_store_enforces_it(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    validated = validate_causal_claim_payload(_causal_payload())
    assert tuple(sorted(validated)) == tuple(sorted((
        "legacy_claim_ref", "legacy_claim_digest", "claim_id", "claim_version", "policy_ref",
        "source_version", "candidate_actor_ref", "evidence_refs", "epistemic_state",
        "legacy_head_sequence", "legacy_head_digest", "transition",
    )))
    hostile = json.loads(_causal_payload())
    hostile["raw_claim_text"] = "must never be admitted"
    _assert_code(
        "validation_failed",
        lambda: validate_causal_claim_payload(
            json.dumps(hostile, sort_keys=True).encode("utf-8")
        ),
    )
    _assert_code(
        "validation_failed",
        lambda: validate_causal_claim_payload(
            _causal_payload(epistemic_state="supported", legacy_head_sequence=0)
        ),
    )

    store = _store(tmp_path, issuer)
    receipt = _create_causal(store, alice)
    assert receipt.after_head.knowledge_kind == "causal_claim"
    _assert_code(
        "validation_failed",
        lambda: store.create_object(
            alice,
            owner_id="alice",
            knowledge_id="causal-2",
            knowledge_kind="causal_claim",
            payload_bytes=b"{}",
            payload_schema_id=CAUSAL_CLAIM_PAYLOAD_SCHEMA,
            payload_media_type="application/json",
            classification="private",
            idempotency_key="create-causal-002",
        ),
    )
    store.close()


def test_causal_append_enforces_exact_aggregate_history_and_targets(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create_causal(store, alice)

    hostile_payloads = (
        _causal_payload(
            epistemic_state="under_review", legacy_head_sequence=2, legacy_head_digest=SHA_D,
            transition=_causal_transition("candidate", "under_review", "event-jump"),
        ),
        _causal_payload(
            claim_id="changed-claim", epistemic_state="under_review", legacy_head_sequence=1,
            legacy_head_digest=SHA_D,
            transition=_causal_transition("candidate", "under_review", "event-identity"),
        ),
        _causal_payload(
            epistemic_state="supported", legacy_head_sequence=1, legacy_head_digest=SHA_D,
            transition=_causal_transition("under_review", "supported", "event-wrong-from"),
        ),
    )
    for index, payload in enumerate(hostile_payloads):
        _assert_code(
            "invalid_causal_history",
            lambda payload=payload, index=index: _append_causal(
                store, alice, created.after_head, payload, f"hostile-causal-{index:02d}"
            ),
        )

    reviewing = _append_causal(
        store, alice, created.after_head,
        _causal_payload(
            epistemic_state="under_review", legacy_head_sequence=1, legacy_head_digest=SHA_D,
            transition=_causal_transition("candidate", "under_review", "event-review"),
        ),
        "append-causal-review",
    )
    supported = _append_causal(
        store, alice, reviewing.after_head,
        _causal_payload(
            epistemic_state="supported", legacy_head_sequence=2, legacy_head_digest=SHA_E,
            transition=_causal_transition("under_review", "supported", "event-support"),
        ),
        "append-causal-support",
    )
    assert supported.after_head.generation == 3
    _assert_code(
        "invalid_causal_history",
        lambda: _append_causal(
            store, alice, supported.after_head,
            _causal_payload(
                epistemic_state="superseded", legacy_head_sequence=3, legacy_head_digest=SHA_F,
                transition=_causal_transition(
                    "supported", "superseded", "event-missing-target",
                    relation_kind="supersession", related_claim_ref=SHA_F, prior_claim_version=1,
                ),
            ),
            "append-causal-missing-target",
        ),
    )
    store.close()


def test_restart_quarantines_hash_consistent_impossible_causal_history(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    store = _store(tmp_path, issuer)
    created = _create_causal(store, alice)
    reviewing = _append_causal(
        store, alice, created.after_head,
        _causal_payload(
            epistemic_state="under_review", legacy_head_sequence=1, legacy_head_digest=SHA_D,
            transition=_causal_transition("candidate", "under_review", "event-review"),
        ),
        "append-causal-review",
    )
    _append_causal(
        store, alice, reviewing.after_head,
        _causal_payload(
            epistemic_state="supported", legacy_head_sequence=2, legacy_head_digest=SHA_E,
            transition=_causal_transition("under_review", "supported", "event-support"),
        ),
        "append-causal-support",
    )
    store.close()

    corrupt_payload = _causal_payload(
        epistemic_state="under_review", legacy_head_sequence=2, legacy_head_digest=SHA_F,
        transition=_causal_transition("candidate", "under_review", "event-corrupt"),
    )
    payload_sha = "sha256:" + hashlib.sha256(corrupt_payload).hexdigest()
    blob_id = "knowledge-blob:" + hashlib.sha256(
        knowledge_module._canonical(
            {"owner_id": "alice", "knowledge_id": "causal-1", "payload_sha256": payload_sha}
        )
    ).hexdigest()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    for trigger in (
        "knowledge_blobs_no_update", "knowledge_revisions_no_update",
        "knowledge_events_no_update", "knowledge_receipts_no_update",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")
    revision = connection.execute(
        "SELECT * FROM knowledge_revisions WHERE owner_id='alice' AND knowledge_id='causal-1' AND revision=3"
    ).fetchone()
    revision_record = knowledge_module._revision_record(revision)
    revision_record["payload_sha256"] = payload_sha
    revision_record["blob_id"] = blob_id
    revision_digest = knowledge_module._canonical_digest(revision_record)
    connection.execute(
        "UPDATE knowledge_blobs SET blob_id=?,payload_sha256=?,payload_size=?,payload_bytes=? WHERE blob_id=?",
        (blob_id, payload_sha, len(corrupt_payload), corrupt_payload, revision["blob_id"]),
    )
    connection.execute(
        "UPDATE knowledge_revisions SET payload_sha256=?,blob_id=?,revision_digest=? WHERE revision_id=?",
        (payload_sha, blob_id, revision_digest, revision["revision_id"]),
    )
    connection.execute(
        "UPDATE knowledge_heads SET current_payload_sha256=? WHERE owner_id='alice' AND knowledge_id='causal-1'",
        (payload_sha,),
    )
    event = connection.execute("SELECT * FROM knowledge_events WHERE event_seq=3").fetchone()
    event_body = knowledge_module._strict_json(event["event_json"])
    event_body["after_head"]["current_payload_sha256"] = payload_sha
    after_head_json = knowledge_module._json(event_body["after_head"])
    event_digest = knowledge_module._canonical_digest(event_body)
    connection.execute(
        "UPDATE knowledge_events SET after_head_json=?,event_digest=?,event_json=? WHERE event_seq=3",
        (after_head_json, event_digest, knowledge_module._json(event_body)),
    )
    receipt = connection.execute(
        "SELECT * FROM knowledge_receipts WHERE receipt_id=?", (event["receipt_id"],)
    ).fetchone()
    receipt_body = knowledge_module._strict_json(receipt["receipt_json"])
    receipt_body["after_head"]["current_payload_sha256"] = payload_sha
    receipt_body["output_digest"] = knowledge_module._canonical_digest(
        {"after_head": receipt_body["after_head"], "new_revision_id": receipt_body["new_revision_id"]}
    )
    receipt_json = knowledge_module._json(receipt_body)
    connection.execute(
        "UPDATE knowledge_receipts SET receipt_json=?,receipt_digest=? WHERE receipt_id=?",
        (receipt_json, knowledge_module._canonical_digest(receipt_body), event["receipt_id"]),
    )
    for schema_sql in knowledge_module._SCHEMA_SQL:
        if any(schema_sql.startswith(f"CREATE TRIGGER {trigger} ") for trigger in (
            "knowledge_blobs_no_update", "knowledge_revisions_no_update",
            "knowledge_events_no_update", "knowledge_receipts_no_update",
        )):
            connection.execute(schema_sql)
    connection.commit()
    connection.close()

    reopened = _store(tmp_path, issuer)
    assert reopened.quarantined
    _assert_code(
        "store_corrupt",
        lambda: reopened.get_head(alice, owner_id="alice", knowledge_id="causal-1"),
    )
    reopened.close()


def test_public_values_are_frozen_and_content_free(tmp_path, authority) -> None:
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    receipt = _create(store, alice)
    assert "one" not in repr(receipt)
    assert "one" not in repr(receipt.after_head)
    with pytest.raises((AttributeError, TypeError)):
        receipt.after_head.state = "purged"  # type: ignore[misc]
    forged = KnowledgeHead(**{**receipt.after_head.__dict__, "generation": 99}) if hasattr(receipt.after_head, "__dict__") else None
    assert forged is None  # slots prevent dictionary-based forgery/copy leaks
    store.close()


def _database_image(path):
    # -shm is SQLite's ephemeral lock/index artifact, not protected data.
    return {suffix: Path(str(path) + suffix).read_bytes()
            for suffix in ("", "-wal", "-journal")
            if Path(str(path) + suffix).exists()}


def _assert_admission_image(path, before):
    after = _database_image(path)
    if "-wal" not in before and "-wal" in after:
        assert len(after.pop("-wal")) in (0, 32)
    assert after == before


def _wal_fixture(tmp_path, issuer, alice, *, drift=None, existing_shm=False):
    """Detach a synthetic, uncheckpointed WAL image from its fixture writer."""
    origin = _store(tmp_path / "origin", issuer)
    receipt = _create(origin, alice)
    if drift == "schema":
        origin._connection.execute("DROP TRIGGER knowledge_events_no_update")
    elif drift == "replay":
        origin._connection.execute("UPDATE knowledge_heads SET generation=99")
    path = tmp_path / "knowledge.sqlite3"
    suffixes = ("", "-wal", "-shm") if existing_shm else ("", "-wal")
    for suffix in suffixes:
        Path(str(path) + suffix).write_bytes(Path(str(origin.database_path) + suffix).read_bytes())
    origin.close()
    assert Path(str(path) + "-wal").stat().st_size > 0
    assert Path(str(path) + "-shm").exists() is existing_shm
    return path, receipt


@pytest.mark.parametrize("existing_shm", [False, True])
@pytest.mark.parametrize("drift", [None, "schema", "replay"])
def test_wal_admission_preserves_main_and_wal_separately_from_shm(
    tmp_path, authority, monkeypatch, existing_shm, drift,
):
    issuer, alice, _bob = authority
    path, receipt = _wal_fixture(tmp_path, issuer, alice, drift=drift, existing_shm=existing_shm)
    before = _database_image(path)
    shm_path = Path(str(path) + "-shm")
    shm_identity = shm_path.stat().st_ino if existing_shm else None
    verify = HarbourKnowledgeStore._verify
    observations = []

    def observed_verify(self, connection):
        assert _database_image(path) == before
        try:
            return verify(connection)
        finally:
            assert _database_image(path) == before
            observations.append(shm_path.exists())

    def forbidden_cleanup(*args, **kwargs):
        pytest.fail("admission must not directly delete or rewrite SQLite sidecars")

    with monkeypatch.context() as patch:
        patch.setattr(HarbourKnowledgeStore, "_verify", observed_verify)
        patch.setattr(knowledge_module.os, "unlink", forbidden_cleanup)
        patch.setattr(knowledge_module.os, "remove", forbidden_cleanup)
        patch.setattr(Path, "write_bytes", forbidden_cleanup)
        store = _store(tmp_path, issuer)
    assert observations and all(observations)  # SQLite may build the WAL index.
    assert _database_image(path) == before
    assert shm_path.exists()
    if existing_shm:
        assert shm_path.stat().st_ino == shm_identity
    if drift is None:
        assert store.available and len(observations) == 2
        assert store.get_head(alice, owner_id="alice", knowledge_id="note-1") == receipt.after_head
        assert store.read_payload(alice, owner_id="alice", revision_id=receipt.new_revision_id) == b"one"
    else:
        assert store.quarantined and len(observations) == 1
        _assert_closed_code("store_corrupt", lambda: store.get_head(alice, owner_id="alice", knowledge_id="note-1"))
    store.close()
    if drift is not None:
        assert _database_image(path) == before
        assert shm_path.exists()


def test_rejected_locked_wal_reverification_does_not_checkpoint_on_close(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    path, _receipt = _wal_fixture(tmp_path, issuer, alice)
    connect = sqlite3.connect
    protected = None
    statements = []

    def changed_before_runtime(database, *args, **kwargs):
        nonlocal protected
        if str(database).endswith("?mode=rw"):
            # A concurrent WAL commit can happen while read-only admission has
            # its older snapshot; runtime must verify the latest locked image.
            attacker = connect(path, isolation_level=None)
            attacker.execute("UPDATE knowledge_heads SET generation=99")
            attacker.close()
            protected = _database_image(path)
            connection = connect(database, *args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(knowledge_module.sqlite3, "connect", changed_before_runtime)
    store = _store(tmp_path, issuer)
    assert store.quarantined and not store.available
    assert protected is not None and _database_image(path) == protected
    assert statements[0] == "BEGIN IMMEDIATE"
    assert not any("=" in statement for statement in statements if statement.startswith("PRAGMA"))
    _assert_closed_code("store_corrupt", lambda: store.get_head(alice, owner_id="alice", knowledge_id="note-1"))
    store.close()
    assert _database_image(path) == protected


@pytest.mark.parametrize("fault", ["read_only_schema", "locked_schema", "new_wal_frames", "existing_wal_frames"])
def test_rejected_admission_closes_connections_without_artifact_cleanup(tmp_path, authority, monkeypatch, fault):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    if fault == "existing_wal_frames":
        path, _receipt = _wal_fixture(tmp_path, issuer, alice)
    else:
        initial = _store(tmp_path, issuer)
        _create(initial, alice)
        initial.close()
        assert not Path(str(path) + "-wal").exists()
    connect = sqlite3.connect
    if fault == "read_only_schema":
        with connect(path) as attacker:
            attacker.execute("DROP TRIGGER knowledge_events_no_update")
        attacker.close()
    before = _database_image(path)
    connections = []
    statements = []
    protected = None
    verified = []
    verify = HarbourKnowledgeStore._verify

    def observed_verify(self, connection):
        result = verify(connection)
        verified.append(connection)
        return result

    def observed_connect(database, *args, **kwargs):
        nonlocal protected
        if str(database).endswith("?mode=rw"):
            with connect(path) as attacker:
                if fault == "locked_schema":
                    attacker.execute("DROP TRIGGER knowledge_events_no_update")
                else:
                    # Commit real WAL frames but leave a valid v1 schema and
                    # replay state. The artifact guard, not _verify, must deny
                    # these new frames, whether or not a WAL already existed.
                    attacker.execute("UPDATE knowledge_heads SET generation=2")
                    attacker.execute("UPDATE knowledge_heads SET generation=1")
            attacker.close()
            protected = _database_image(path)
            assert len(protected["-wal"]) > 32
        connection = connect(database, *args, **kwargs)
        if str(database).endswith(("?mode=ro", "?mode=rw")):
            connections.append(connection)
            if str(database).endswith("?mode=rw"):
                connection.set_trace_callback(statements.append)
        return connection

    def forbidden_cleanup(*args, **kwargs):
        pytest.fail("admission must not clean up database artifacts")

    with monkeypatch.context() as patch:
        patch.setattr(knowledge_module.sqlite3, "connect", observed_connect)
        patch.setattr(HarbourKnowledgeStore, "_verify", observed_verify)
        patch.setattr(knowledge_module.os, "unlink", forbidden_cleanup)
        patch.setattr(knowledge_module.os, "remove", forbidden_cleanup)
        patch.setattr(Path, "write_bytes", forbidden_cleanup)
        store = _store(tmp_path, issuer)
        assert store.quarantined and not store.available
        _assert_closed_code("store_corrupt", lambda: store.get_head(alice, owner_id="alice", knowledge_id="note-1"))
        store.close()
    assert len(connections) == (1 if fault == "read_only_schema" else 2)
    assert len(verified) == {"read_only_schema": 0, "locked_schema": 1,
                             "new_wal_frames": 2, "existing_wal_frames": 2}[fault]
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
    if protected is None:
        _assert_admission_image(path, before)
    else:
        assert _database_image(path) == protected
        assert statements[0] == "BEGIN IMMEDIATE"
        assert not any("=" in statement for statement in statements if statement.startswith("PRAGMA"))
    _assert_closed_code("closed", lambda: store.get_head(alice, owner_id="alice", knowledge_id="note-1"))


@pytest.mark.parametrize("size", [0, 32, 1, 31, 33, 4096])
def test_initially_absent_wal_artifact_size_boundary(tmp_path, authority, size):
    issuer, alice, _bob = authority
    path, _receipt = _wal_fixture(tmp_path / "header", issuer, alice)
    header = Path(str(path) + "-wal").read_bytes()[:32]
    target = tmp_path / "knowledge.sqlite3"
    target.write_bytes(path.read_bytes())
    snapshot = knowledge_module._admission_identity(target)
    artifact = (header + bytes(4096))[:size]
    wal = Path(str(target) + "-wal")
    wal.write_bytes(artifact)
    if size in (0, 32):
        knowledge_module._verify_admission_identity(target, snapshot)
    else:
        _assert_closed_code("store_corrupt", lambda: knowledge_module._verify_admission_identity(target, snapshot))
    assert wal.read_bytes() == artifact  # Rejection never truncates or deletes it.


@pytest.mark.parametrize("drift", [
    "column", "primary", "unique", "foreign", "index", "trigger", "extra", "meta", "version",
])
def test_effective_v1_schema_admission_rejects_drift_without_writes(tmp_path, authority, drift):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    connection = sqlite3.connect(path)
    for sql in knowledge_module._SCHEMA_SQL:
        if drift == "column" and sql.startswith("CREATE TABLE knowledge_blobs("):
            sql = sql.replace("payload_size INTEGER NOT NULL", "payload_size TEXT")
        elif drift == "primary" and sql.startswith("CREATE TABLE knowledge_blobs("):
            sql = sql.replace("blob_id TEXT PRIMARY KEY", "blob_id TEXT")
        elif drift == "unique" and sql.startswith("CREATE TABLE knowledge_revisions("):
            sql = sql.replace(",UNIQUE(owner_id,knowledge_id,revision)", "")
        elif drift == "foreign" and sql.startswith("CREATE TABLE knowledge_heads("):
            sql = sql.replace("PRIMARY KEY(owner_id,knowledge_id)",
                              "PRIMARY KEY(owner_id,knowledge_id),FOREIGN KEY(current_revision_id) REFERENCES knowledge_revisions(revision_id)")
        elif drift == "index" and sql.startswith("CREATE INDEX knowledge_blobs_owner_ix "):
            sql = sql.replace("(owner_id,blob_id)", "(blob_id,owner_id)")
        elif drift == "trigger" and sql.startswith("CREATE TRIGGER knowledge_events_no_update "):
            sql = sql.replace("SELECT RAISE(ABORT,'immutable')", "SELECT 1")
        connection.execute(sql)
    connection.execute("PRAGMA application_id=1213221957")
    connection.execute("PRAGMA user_version=" + ("2" if drift == "version" else "1"))
    connection.execute("INSERT INTO knowledge_meta VALUES('schema_id',?)", (knowledge_module._SCHEMA_ID,))
    if drift == "extra":
        connection.execute("CREATE TABLE unexpected(value TEXT)")
    if drift == "meta":
        connection.execute("INSERT INTO knowledge_meta VALUES('unexpected','v1')")
    connection.commit()
    connection.close()
    before = _database_image(path)
    store = _store(tmp_path, issuer)
    assert store.quarantined and not store.available
    _assert_closed_code("store_corrupt", lambda: store.get_head(alice, owner_id="alice", knowledge_id="note-1"))
    store.close()
    assert _database_image(path) == before


@pytest.mark.parametrize("content", [b"", b"not sqlite"])
def test_existing_invalid_file_is_never_initialized(tmp_path, authority, content):
    issuer, _alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    path.write_bytes(content)
    store = _store(tmp_path, issuer)
    assert store.quarantined
    store.close()
    assert _database_image(path) == {"": content}


def test_open_existing_missing_path_is_unavailable_without_creation(tmp_path, authority):
    issuer, _alice, _bob = authority
    path = tmp_path / "missing" / "knowledge.sqlite3"
    store = HarbourKnowledgeStore.open_existing(path, trusted_issuers=(issuer,), clock=lambda: NOW)
    assert not store.available and store.quarantined
    assert not path.exists() and not path.parent.exists()


def test_open_existing_store_connection_can_be_used_and_closed_across_threads(tmp_path, authority):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    created = _store(tmp_path, issuer)
    _create(created, alice)
    created.close()
    store = HarbourKnowledgeStore.open_existing(path, trusted_issuers=(issuer,), clock=lambda: NOW)
    assert store.available
    result = []
    reader = threading.Thread(target=lambda: result.append(
        store.get_head(alice, owner_id="alice", knowledge_id="note-1")
    ))
    reader.start()
    reader.join(timeout=5)
    assert not reader.is_alive()
    assert result and result[0].knowledge_id == "note-1"
    closer = threading.Thread(target=store.close)
    closer.start()
    closer.join(timeout=5)
    assert not closer.is_alive()
    assert not store.available


def test_default_and_existing_connections_support_construct_read_close_threads(tmp_path, authority):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    errors = []
    stores = []

    def construct():
        try:
            created = HarbourKnowledgeStore(path, trusted_issuers=(issuer,), clock=lambda: NOW)
            _create(created, alice)
            created.close()
            stores.append(HarbourKnowledgeStore.open_existing(
                path, trusted_issuers=(issuer,), clock=lambda: NOW,
            ))
        except BaseException as exc:
            errors.append(exc)

    creator = threading.Thread(target=construct)
    creator.start()
    creator.join(timeout=5)
    assert not creator.is_alive() and not errors and len(stores) == 1
    store = stores[0]
    reads = []
    reader = threading.Thread(target=lambda: reads.append(
        store.get_head(alice, owner_id="alice", knowledge_id="note-1")
    ))
    reader.start()
    reader.join(timeout=5)
    assert not reader.is_alive() and reads[0].knowledge_id == "note-1"
    closer = threading.Thread(target=store.close)
    closer.start()
    closer.join(timeout=5)
    assert not closer.is_alive() and not store.available


@pytest.mark.parametrize("delete_on", ["mode=ro", "mode=rw"])
def test_open_existing_deletion_at_admission_boundary_never_recreates(tmp_path, authority, monkeypatch, delete_on):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    created = _store(tmp_path, issuer)
    _create(created, alice)
    created.close()
    original_connect = knowledge_module.sqlite3.connect
    deleted = False
    ro_connection = None

    def interleaved_connect(database, *args, **kwargs):
        nonlocal deleted, ro_connection
        mode = "mode=ro" if "mode=ro" in str(database) else "mode=rw" if "mode=rw" in str(database) else ""
        if delete_on == "mode=ro" and not deleted and mode == "mode=ro":
            path.unlink()
            deleted = True
        if delete_on == "mode=rw" and not deleted and mode == "mode=rw":
            assert ro_connection is not None
            ro_connection.close()
            path.unlink()
            deleted = True
        connection = original_connect(database, *args, **kwargs)
        if mode == "mode=ro":
            ro_connection = connection
        return connection

    monkeypatch.setattr(knowledge_module.sqlite3, "connect", interleaved_connect)
    opened = HarbourKnowledgeStore.open_existing(path, trusted_issuers=(issuer,), clock=lambda: NOW)
    assert deleted and not opened.available and opened.quarantined
    opened.close()
    assert not path.exists()


def test_open_existing_rejects_same_bytes_replaced_wal_inode(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    created = _store(tmp_path, issuer)
    _create(created, alice)
    created.close()
    wal = Path(str(path) + "-wal")
    wal.write_bytes(b"same bytes")
    original_verify = knowledge_module._verify_existing_path_identity
    checks = 0

    def replace_after_identity_check(candidate, identity):
        nonlocal checks
        checks += 1
        if checks == 1:
            original_verify(candidate, identity)
            wal.unlink()
            wal.write_bytes(b"same bytes")
            return
        return original_verify(candidate, identity)

    monkeypatch.setattr(knowledge_module, "_verify_existing_path_identity", replace_after_identity_check)
    opened = HarbourKnowledgeStore.open_existing(path, trusted_issuers=(issuer,), clock=lambda: NOW)
    assert not opened.available and opened.quarantined
    opened.close()


def test_existing_database_is_reverified_under_write_lock_before_configuration(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    initial = _store(tmp_path, issuer)
    _create(initial, alice)
    initial.close()
    connect = sqlite3.connect
    before = None
    runtime_statements = []

    def interleaved_connect(database, *args, **kwargs):
        nonlocal before
        if str(database).endswith("?mode=rw"):
            with connect(path) as attacker:
                attacker.execute("DROP TRIGGER knowledge_events_no_update")
                attacker.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events BEGIN SELECT 1; END")
            attacker.close()
            before = _database_image(path)
            connection = connect(database, *args, **kwargs)
            connection.set_trace_callback(runtime_statements.append)
            return connection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(knowledge_module.sqlite3, "connect", interleaved_connect)
    store = _store(tmp_path, issuer)
    assert store.quarantined
    assert runtime_statements[0] == "BEGIN IMMEDIATE"
    assert not any("=" in statement for statement in runtime_statements if statement.startswith("PRAGMA"))
    store.close()
    assert _database_image(path) == before


def test_exclusive_creation_race_admits_competing_database_without_initialization(tmp_path, authority, monkeypatch):
    issuer, _alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    original_open = knowledge_module.os.open
    before = None

    def competing_open(target, flags, *args, **kwargs):
        nonlocal before
        assert flags & knowledge_module.os.O_EXCL
        path.write_bytes(b"competing incomplete database")
        before = _database_image(path)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(knowledge_module.os, "open", competing_open)
    store = _store(tmp_path, issuer)
    assert store.quarantined
    store.close()
    assert _database_image(path) == before


@pytest.mark.parametrize("table,sql", [
    ("knowledge_events", "UPDATE knowledge_events SET event_digest='sha256:broken'"),
    ("knowledge_receipts", "UPDATE knowledge_receipts SET receipt_json='{}'"),
    ("knowledge_idempotency", "UPDATE knowledge_idempotency SET command_digest='sha256:broken'"),
    ("knowledge_heads", "UPDATE knowledge_heads SET generation=9"),
])
def test_replay_corruption_is_non_mutating_on_restart(tmp_path, authority, table, sql):
    issuer, alice, _bob = authority
    path = tmp_path / "knowledge.sqlite3"
    store = _store(tmp_path, issuer)
    _create(store, alice)
    store.close()
    with sqlite3.connect(path) as connection:
        trigger = table + "_no_update"
        if table != "knowledge_heads":
            connection.execute("DROP TRIGGER " + trigger)
        connection.execute(sql)
        if table != "knowledge_heads":
            connection.execute(next(sql for sql in knowledge_module._SCHEMA_SQL
                                    if sql.startswith("CREATE TRIGGER " + trigger + " ")))
    connection.close()
    before = _database_image(path)
    reopened = _store(tmp_path, issuer)
    assert reopened.quarantined
    reopened.close()
    _assert_admission_image(path, before)


class _HostileString(str):
    def __eq__(self, other):
        pytest.fail("caller comparison hook executed")

    def __hash__(self):
        pytest.fail("caller hash hook executed")

    def __deepcopy__(self, memo):
        pytest.fail("caller copy hook executed")


@pytest.mark.parametrize("field,value", [
    ("owner_id", _HostileString("alice")), ("knowledge_id", _HostileString("note-1")),
    ("knowledge_kind", _HostileString("note")), ("current_revision_id", _HostileString("revision")),
    ("current_payload_sha256", _HostileString(SHA_A)), ("generation", True),
    ("state", _HostileString("active")), ("tombstoned_at", object()), ("purge_requested_at", []),
])
def test_hostile_expected_head_fields_fail_before_hooks_or_transaction(tmp_path, authority, monkeypatch, field, value):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    hostile = replace(created.after_head, **{field: value})
    monkeypatch.setattr(store, "_transaction", lambda: pytest.fail("invalid head reached transaction"))
    _assert_closed_code("stale_authority", lambda: store.tombstone(
        alice, owner_id="alice", knowledge_id="note-1", expected_head=hostile, idempotency_key="tombstone-hostile"))
    assert store.available
    store.close()


def test_expected_head_snapshot_does_not_reread_mutated_caller(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    original = _create(store, alice).after_head
    transaction = store._transaction

    def mutate_before_transaction():
        object.__setattr__(original, "generation", 99)
        object.__setattr__(original, "state", "purged")
        return transaction()

    monkeypatch.setattr(store, "_transaction", mutate_before_transaction)
    receipt = store.tombstone(alice, owner_id="alice", knowledge_id="note-1",
                              expected_head=original, idempotency_key="tombstone-snapshot")
    assert receipt.before_head.generation == 1 and receipt.before_head.state == "active"
    assert receipt.after_head.generation == 2
    store.close()


@pytest.mark.parametrize("field,value", [
    ("retention_class", _HostileString("time_bound")), ("deletion_mode", []),
    ("retain_until", "not-a-timeZ"), ("grace_until", _HostileString("2026-08-28T00:00:00Z")),
    ("legal_hold", 0),
])
def test_hostile_retention_is_detached_and_does_not_quarantine(tmp_path, authority, field, value):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    retention = {"retention_class": "time_bound", "deletion_mode": "purge_eligible",
                 "retain_until": None, "grace_until": None, "legal_hold": False, field: value}
    _assert_closed_code("validation_failed", lambda: _create(store, alice, retention=retention))
    assert store.available and store._connection.total_changes == 1  # only schema meta
    _create(store, alice)
    store.close()


def _pending_purge(store, alice, *, retention=None):
    retention = retention or {"retention_class": "time_bound", "deletion_mode": "purge_eligible",
                              "retain_until": "2026-08-28T00:00:00.000000Z",
                              "grace_until": "2026-08-28T12:00:00.000000Z", "legal_hold": False}
    created = _create(store, alice, retention=retention)
    tombstone = store.tombstone(alice, owner_id="alice", knowledge_id="note-1",
                                expected_head=created.after_head, idempotency_key="tombstone-purge-test")
    return store.request_purge(alice, owner_id="alice", knowledge_id="note-1",
                               expected_head=tombstone.after_head, idempotency_key="request-purge-test")


def _complete(store, alice, head, **changes):
    args = dict(owner_id="alice", knowledge_id="note-1", expected_head=head, policy_receipt=SHA_A,
                projection_deletion_receipts=(SHA_B,), backup_deletion_receipts=(SHA_C,),
                idempotency_key="complete-purge-test")
    args.update(changes)
    return store.complete_purge(alice, **args)


@pytest.mark.parametrize("field,value", [
    ("policy_receipt", _HostileString(SHA_A)), ("policy_receipt", "not-a-digest"),
    ("projection_deletion_receipts", ()), ("projection_deletion_receipts", (SHA_C, SHA_B)),
    ("projection_deletion_receipts", (SHA_B, SHA_B)), ("backup_deletion_receipts", (_HostileString(SHA_C),)),
    ("backup_deletion_receipts", iter((SHA_C,))),
])
def test_complete_purge_rejects_noncanonical_evidence_without_writes(tmp_path, authority, field, value):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    changes = store._connection.total_changes
    _assert_closed_code("validation_failed", lambda: _complete(store, alice, pending.after_head, **{field: value}))
    assert store.available and store._connection.total_changes == changes
    assert store._connection.execute("SELECT COUNT(*) FROM knowledge_blobs").fetchone()[0] == 1
    store.close()


def test_complete_purge_binds_v1_command_and_replays_without_any_writes(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    receipt = _complete(store, alice, pending.after_head)
    principal_ref = knowledge_module.authenticate_principal(alice, trusted_issuers=(issuer,), now=NOW)
    command = {"kind": "complete_purge", "owner_id": "alice", "knowledge_id": "note-1",
               "expected_head": knowledge_module._head_dict(pending.after_head),
               "evidence": {"policy_receipt": SHA_A, "projection_deletion_receipts": [SHA_B], "backup_deletion_receipts": [SHA_C]},
               "principal_digest": principal_ref.principal_digest, "idempotency_key": "complete-purge-test"}
    encoded = json.dumps(command, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    expected_digest = "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()
    assert receipt.input_digest == expected_digest
    event = store.changes_since(alice, owner_id="alice", after_event_seq=3)[0]
    assert event["command_digest"] == expected_digest
    assert "evidence" not in event and "principal_digest" not in event
    assert store._connection.execute("SELECT command_digest FROM knowledge_idempotency WHERE idempotency_key='complete-purge-test'").fetchone()[0] == expected_digest
    changes = store._connection.total_changes
    assert _complete(store, alice, pending.after_head) == receipt
    assert store._connection.total_changes == changes
    for change in ({"policy_receipt": SHA_D}, {"projection_deletion_receipts": (SHA_D,)},
                   {"backup_deletion_receipts": (SHA_D,)}, {"expected_head": receipt.after_head}):
        _assert_closed_code("idempotency_conflict", lambda change=change: _complete(store, alice, pending.after_head, **change))
    other_principal = issuer.issue(subject_id="second-alice", owner_id="alice", auth_context_digest=SHA_A, ttl_seconds=3600)
    _assert_closed_code("idempotency_conflict", lambda: _complete(store, other_principal, pending.after_head))
    _assert_closed_code("stale_authority", lambda: _complete(store, alice, pending.after_head, idempotency_key="new-complete-key"))
    assert store._connection.total_changes == changes
    store.close()
    reopened = _store(tmp_path, issuer)
    assert reopened.available
    changes = reopened._connection.total_changes
    assert _complete(reopened, alice, pending.after_head) == receipt
    assert reopened._connection.total_changes == changes
    reopened.close()


def test_complete_purge_snapshots_evidence_before_transaction(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    projection, backup = [SHA_B], [SHA_C]
    transaction = store._transaction

    def mutate():
        projection[:] = [SHA_D]
        backup.clear()
        return transaction()

    monkeypatch.setattr(store, "_transaction", mutate)
    receipt = _complete(store, alice, pending.after_head, projection_deletion_receipts=projection, backup_deletion_receipts=backup)
    assert _complete(store, alice, pending.after_head) == receipt
    store.close()


@pytest.mark.parametrize("deadline", ["retain_until", "grace_until"])
def test_complete_purge_denies_unelapsed_deadlines_without_deletion(tmp_path, authority, deadline):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice, retention={
        "retention_class": "time_bound", "deletion_mode": "purge_eligible", "legal_hold": False,
        "retain_until": None, "grace_until": None, deadline: "2026-08-30T00:00:00.000000Z"})
    changes = store._connection.total_changes
    _assert_closed_code("retention_denied", lambda: _complete(store, alice, pending.after_head))
    assert store._connection.total_changes == changes
    assert store._connection.execute("SELECT COUNT(*) FROM knowledge_blobs").fetchone()[0] == 1
    store.close()


def test_complete_purge_requires_prior_request(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    changes = store._connection.total_changes
    _assert_closed_code("invalid_lifecycle", lambda: _complete(store, alice, created.after_head))
    assert store._connection.total_changes == changes
    store.close()


def test_runtime_replay_corruption_quarantines_before_mutation(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    connection = store._connection
    connection.execute("UPDATE knowledge_heads SET generation=99")
    changes = connection.total_changes
    _assert_closed_code("store_corrupt", lambda: store.tombstone(
        alice, owner_id="alice", knowledge_id="note-1", expected_head=created.after_head, idempotency_key="corrupt-tombstone"))
    assert store.quarantined and not connection.in_transaction and connection.total_changes == changes
    store.close()


def test_public_error_surfaces_detach_library_failures(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    _assert_closed_code("validation_failed", lambda: validate_causal_claim_payload(b"{broken"))
    _assert_closed_code("validation_failed", lambda: validate_causal_claim_payload(b"[" * 1100))
    _assert_closed_code("validation_failed", lambda: store.get_head(alice, unexpected=True))
    assert store.available

    def broken_connection():
        raise sqlite3.OperationalError("private details must not escape")

    monkeypatch.setattr(store, "_conn", broken_connection)
    _assert_closed_code("storage_failed", lambda: store.get_head(alice, owner_id="alice", knowledge_id="note-1"))
    assert store.available
    store.close()


def test_principal_ref_is_validated_before_owner_comparison_or_digest(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    ref = knowledge_module.authenticate_principal(alice, trusted_issuers=(issuer,), now=NOW)
    monkeypatch.setattr(knowledge_module, "authenticate_principal", lambda *a, **kw: replace(ref, owner_id=_HostileString("alice")))
    _assert_closed_code("unauthenticated", lambda: _create(store, alice))
    assert store.available
    store.close()


def _durable_knowledge_image(connection):
    # Include payload bytes, every lifecycle row, and schema metadata. This
    # observes committed content rather than SQLite's rollback counters.
    return {
        table: tuple(tuple(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY 1,2"))
        for table in ("knowledge_meta", "knowledge_blobs", "knowledge_revisions", "knowledge_heads",
                      "knowledge_events", "knowledge_receipts", "knowledge_idempotency")
    }


@pytest.mark.parametrize("nested_method", [
    "create_object", "append_revision", "tombstone", "restore", "request_purge", "complete_purge",
])
def test_clock_caught_nested_mutation_cannot_control_outer_purge(tmp_path, authority, monkeypatch, nested_method):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    connection = store._connection
    statements, calls = [], []
    connection.set_trace_callback(statements.append)

    def clock():
        assert not connection.in_transaction
        before = tuple(statements)
        # Missing arguments deliberately prove rejection before argument
        # validation, authentication, or transaction entry of every writer.
        _assert_closed_code("storage_failed", getattr(store, nested_method))
        assert tuple(statements) == before
        calls.append(NOW)
        return NOW

    monkeypatch.setattr(store, "_clock", clock)
    completed = _complete(store, alice, pending.after_head)
    assert len(calls) == 2
    assert [sql for sql in statements if sql in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"}] == ["BEGIN IMMEDIATE", "COMMIT"]
    assert completed.after_head.state == "purged"
    assert connection.execute("SELECT COUNT(*) FROM knowledge_blobs").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM knowledge_events").fetchone()[0] == 4
    assert connection.execute("SELECT COUNT(*) FROM knowledge_receipts").fetchone()[0] == 4
    assert connection.execute("SELECT COUNT(*) FROM knowledge_idempotency").fetchone()[0] == 4
    store.close()
    reopened = _store(tmp_path, issuer)
    assert reopened.available
    assert _complete(reopened, alice, pending.after_head) == completed
    reopened.close()


@pytest.mark.parametrize("failure_call", [1, 2])
@pytest.mark.parametrize("failure_kind", ["nested", "exception", "invalid_time"])
def test_clock_failure_before_purge_transaction_leaves_exact_durable_image(
    tmp_path, authority, monkeypatch, failure_call, failure_kind,
):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    connection = store._connection
    before = _durable_knowledge_image(connection)
    statements = []
    calls = 0
    connection.set_trace_callback(statements.append)

    def clock():
        nonlocal calls
        calls += 1
        assert not connection.in_transaction
        if calls == failure_call:
            if failure_kind == "nested":
                store.restore(alice, owner_id="alice", knowledge_id="note-1",
                              expected_head=pending.after_head, idempotency_key="nested-restore")
            elif failure_kind == "exception":
                raise RuntimeError("private clock failure")
            else:
                return object()
        return NOW

    monkeypatch.setattr(store, "_clock", clock)
    _assert_closed_code("storage_failed", lambda: _complete(store, alice, pending.after_head))
    assert calls == failure_call
    assert not any(sql in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"} for sql in statements)
    assert not connection.in_transaction and store.available
    assert _durable_knowledge_image(connection) == before
    monkeypatch.setattr(store, "_clock", lambda: NOW)
    # Rejected entry released the public guard and the fixture is still usable.
    completed = _complete(store, alice, pending.after_head)
    assert completed.after_head.state == "purged"
    store.close()


def test_purge_failure_after_delete_rolls_back_all_durable_rows(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    connection = store._connection
    before = _durable_knowledge_image(connection)
    statements, clock_calls = [], []
    connection.set_trace_callback(statements.append)

    def clock():
        assert not connection.in_transaction
        clock_calls.append(NOW)
        return NOW

    def fail_record(*args, **kwargs):
        assert connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM knowledge_blobs").fetchone()[0] == 0
        raise sqlite3.OperationalError("private write failure")

    monkeypatch.setattr(store, "_clock", clock)
    monkeypatch.setattr(store, "_record_transition", fail_record)
    _assert_closed_code("storage_failed", lambda: _complete(store, alice, pending.after_head))
    assert len(clock_calls) == 2
    assert [sql for sql in statements if sql in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"}] == ["BEGIN IMMEDIATE", "ROLLBACK"]
    assert not connection.in_transaction and store.available
    assert _durable_knowledge_image(connection) == before
    store.close()
    reopened = _store(tmp_path, issuer)
    assert reopened.available and _durable_knowledge_image(reopened._connection) == before
    assert _complete(reopened, alice, pending.after_head).after_head.state == "purged"
    reopened.close()


def test_authentication_callback_runs_inside_public_entry_guard(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    authenticate = knowledge_module.authenticate_principal
    calls = []

    def callback(*args, **kwargs):
        assert not store._connection.in_transaction
        _assert_closed_code("storage_failed", lambda: _create(store, alice))
        calls.append(True)
        return authenticate(*args, **kwargs)

    monkeypatch.setattr(knowledge_module, "authenticate_principal", callback)
    assert _create(store, alice).after_head.state == "active"
    assert calls == [True]
    store.close()


def test_revision_clocks_are_normalized_before_transaction_and_shared_with_event(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    calls = []

    def clock():
        assert not store._connection.in_transaction
        calls.append(True)
        return NOW

    monkeypatch.setattr(store, "_clock", clock)
    receipt = _create(store, alice)
    assert calls == [True, True]
    row = store._connection.execute("SELECT created_at FROM knowledge_revisions").fetchone()
    assert row[0] == receipt.occurred_at
    store.close()


@pytest.mark.parametrize("nested", [False, True])
def test_authentication_callback_failure_precedes_transaction(tmp_path, authority, monkeypatch, nested):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    connection = store._connection
    before = _durable_knowledge_image(connection)
    statements = []
    connection.set_trace_callback(statements.append)

    def callback(*args, **kwargs):
        if nested:
            _create(store, alice)
        raise RuntimeError("private authentication callback failure")

    monkeypatch.setattr(knowledge_module, "authenticate_principal", callback)
    _assert_closed_code("storage_failed", lambda: _complete(store, alice, pending.after_head))
    assert not any(sql in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"} for sql in statements)
    assert _durable_knowledge_image(connection) == before
    assert store.available and not connection.in_transaction
    store.close()


def test_purge_commit_rejection_rolls_back_owned_transaction(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    pending = _pending_purge(store, alice)
    connection = store._connection
    before = _durable_knowledge_image(connection)

    def reject_commit(action, argument, *unused):
        if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(reject_commit)
    _assert_closed_code("store_corrupt", lambda: _complete(store, alice, pending.after_head))
    connection.set_authorizer(None)
    assert not connection.in_transaction
    assert _durable_knowledge_image(connection) == before
    store.close()
    reopened = _store(tmp_path, issuer)
    assert reopened.available and _durable_knowledge_image(reopened._connection) == before
    reopened.close()


_SNAPSHOT_SCHEMA = "odysseus.harbour.knowledge_migration_snapshot.v1"


def _vector_json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _vector_hash(value):
    raw = _vector_json(value).encode("ascii")
    return "sha256:" + hashlib.sha256(len(raw).to_bytes(8, "big") + raw).hexdigest()


def _vector_domain(kind, value):
    return _vector_hash({"schema_id": _SNAPSHOT_SCHEMA, "kind": kind, "value": value})


def _metadata_fixture(knowledge_id="fixture-note", owner="alice"):
    # Literal synthetic persisted metadata, independent of the production
    # writer/serializer. No payload is present in this fixture's database.
    revision = {
        "revision_id": "revision:" + knowledge_id, "owner_id": owner,
        "knowledge_id": knowledge_id, "knowledge_kind": "note", "revision": 1,
        "parent_revision_id": None, "payload_schema_id": "harbour.note.v1",
        "payload_media_type": "text/plain", "payload_sha256": SHA_A,
        "blob_id": "knowledge-blob:" + hashlib.sha256(_vector_json({
            "owner_id": owner, "knowledge_id": knowledge_id, "payload_sha256": SHA_A,
        }).encode("ascii")).hexdigest(),
        "classification": "private", "source_revisions": [], "provenance": [], "references": [],
        "retention": {"retention_class": "user_managed", "deletion_mode": "tombstone_only",
                      "retain_until": None, "grace_until": None, "legal_hold": False},
        "created_at": "2026-08-29T12:00:00.000000Z",
        "principal_ref": {"issuer_id": "fixture-issuer", "subject_id": "fixture-subject",
                          "owner_id": owner, "auth_context_digest": SHA_B, "principal_digest": SHA_C},
    }
    head = {
        "owner_id": owner, "knowledge_id": knowledge_id, "knowledge_kind": "note",
        "current_revision_id": revision["revision_id"], "current_payload_sha256": SHA_A,
        "generation": 1, "state": "active", "tombstoned_at": None, "purge_requested_at": None,
    }
    return head, revision


def _insert_metadata(connection, head, revision, *, source_padding=0, overrides=None):
    connection.execute("INSERT INTO knowledge_heads VALUES(?,?,?,?,?,?,?,?,?)", tuple(head.values()))
    _insert_revision(connection, revision, source_padding=source_padding, overrides=overrides)


def _insert_revision(connection, revision, *, source_padding=0, overrides=None):
    columns = (
        "revision_id", "owner_id", "knowledge_id", "knowledge_kind", "revision", "parent_revision_id",
        "payload_schema_id", "payload_media_type", "payload_sha256", "blob_id", "classification",
        "source_revisions_json", "provenance_json", "references_json", "retention_json", "created_at",
        "principal_ref_json", "revision_digest",
    )
    values = (
        revision["revision_id"], revision["owner_id"], revision["knowledge_id"], revision["knowledge_kind"],
        revision["revision"], revision["parent_revision_id"], revision["payload_schema_id"],
        revision["payload_media_type"], revision["payload_sha256"], revision["blob_id"], revision["classification"],
        _vector_json(revision["source_revisions"]).ljust(source_padding), _vector_json(revision["provenance"]),
        _vector_json(revision["references"]), _vector_json(revision["retention"]), revision["created_at"],
        _vector_json(revision["principal_ref"]), _vector_hash(revision),
    )
    # Inject corrupt fixture bytes at creation. Production immutability stays
    # intact; these tests never disable or replace a Store trigger.
    stored = dict(zip(columns, values))
    stored.update(overrides or {})
    connection.execute("INSERT INTO knowledge_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       tuple(stored[column] for column in columns))


def _vector_snapshot(owner, pairs):
    owner_digest = _vector_domain("owner", owner)
    records = []
    for head, revision in pairs:
        metadata = {
            "knowledge_id_digest": _vector_domain("knowledge_id", revision["knowledge_id"]),
            "revision_id_digest": _vector_domain("revision_id", revision["revision_id"]),
            "revision": revision["revision"], "generation": head["generation"], "state": head["state"],
            "knowledge_kind": revision["knowledge_kind"], "payload_sha256": revision["payload_sha256"],
            "classification_digest": _vector_domain("classification", revision["classification"]),
            "retention_digest": _vector_domain("retention", revision["retention"]),
            "provenance_digest": _vector_domain("provenance", revision["provenance"]),
            "source_revisions_digest": _vector_domain("source_revisions", revision["source_revisions"]),
            "references_digest": _vector_domain("references", revision["references"]),
            "revision_metadata_digest": _vector_domain("revision_metadata", revision),
            "head_metadata_digest": _vector_domain("head_metadata", head),
        }
        records.append({**metadata, "record_digest": _vector_domain(
            "head_record", {"owner_ref_digest": owner_digest, "metadata": metadata},
        )})
    records.sort(key=lambda record: (record["knowledge_id_digest"], record["revision"]))
    boundary = _vector_domain("head_boundary", {
        "owner_ref_digest": owner_digest, "record_digests": [record["record_digest"] for record in records],
    })
    body = {"schema_id": _SNAPSHOT_SCHEMA, "owner_ref_digest": owner_digest,
            "captured_head_boundary": boundary, "heads": records}
    return {**body, "heads": tuple(records), "snapshot_digest": _vector_domain("snapshot", body)}


def _capture(store, principal, owner="alice", expected=None):
    return store.capture_migration_snapshot(principal, owner_id=owner, expected_head_boundary=expected)


def _capture_index(store, principal, owner="alice", expected=None):
    return store.capture_index_snapshot(principal, owner_id=owner, expected_head_boundary=expected)


def _signal_principal(issuer, *, owner="owner_0123456789abcdef0123456789abcdef"):
    return issuer.issue(subject_id=owner, owner_id=owner, auth_context_digest=SHA_A, ttl_seconds=3600), owner


def _capture_signals(store, principal, owner, limit=100, expected=None):
    return store.capture_inbox_signal_snapshot(
        principal, owner_id=owner, limit=limit, expected_head_boundary=expected,
    )


def _purge_pending_signal(store, principal, owner, *, knowledge_id="note-1", suffix="001"):
    created = _create(store, principal, owner=owner, knowledge_id=knowledge_id, key=f"create-signal-{suffix}",
                      retention={"retention_class": "time_bound", "deletion_mode": "purge_eligible",
                                 "retain_until": "2026-08-28T00:00:00.000000Z",
                                 "grace_until": "2026-08-28T12:00:00.000000Z", "legal_hold": False})
    tombstone = store.tombstone(principal, owner_id=owner, knowledge_id=knowledge_id,
                                expected_head=created.after_head, idempotency_key=f"tombstone-signal-{suffix}")
    return store.request_purge(principal, owner_id=owner, knowledge_id=knowledge_id,
                               expected_head=tombstone.after_head, idempotency_key=f"purge-signal-{suffix}")


def test_index_snapshot_is_detached_deterministic_and_excludes_payload(tmp_path, authority):
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice, payload=b"INDEX-PAYLOAD-MUST-NOT-BE-EXPORTED")
    _create(store, bob, owner="bob")
    snapshot = _capture_index(store, alice)
    assert type(snapshot) is KnowledgeIndexSnapshot
    assert snapshot.schema_id == KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA
    assert type(snapshot.records) is tuple and len(snapshot.records) == 1
    record = snapshot.records[0]
    assert type(record) is KnowledgeIndexRecord
    assert record.knowledge_id == "note-1"
    assert record.revision_id == created.new_revision_id
    assert record.state == "active" and record.generation == 1
    assert "INDEX-PAYLOAD" not in repr(snapshot)
    assert "blob_id" not in asdict(record)
    assert snapshot == _capture_index(store, alice, expected=snapshot.captured_head_boundary)
    store.close()
    restarted = _store(tmp_path, issuer)
    assert _capture_index(restarted, alice) == snapshot
    _assert_closed_code(
        "stale_authority",
        lambda: restarted.read_payload_at_generation(
            alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
                expected_generation=2, expected_payload_sha256=created.after_head.current_payload_sha256,
        ),
    )
    assert restarted.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ) == b"INDEX-PAYLOAD-MUST-NOT-BE-EXPORTED"
    restarted.close()


def test_index_snapshot_lifecycle_fences_and_owner_validation(tmp_path, authority):
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    before = _capture_index(store, alice)
    _assert_closed_code("owner_scope_denied", lambda: _capture_index(store, bob, owner="alice"))
    tombstone = store.tombstone(alice, owner_id="alice", knowledge_id="note-1",
                                expected_head=created.after_head, idempotency_key="index-tombstone-001")
    _assert_closed_code("content_unavailable", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=tombstone.after_head.generation,
        expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    _assert_closed_code("stale_authority", lambda: _capture_index(
        store, alice, expected=before.captured_head_boundary))
    restored = store.restore(alice, owner_id="alice", knowledge_id="note-1",
                             expected_head=tombstone.after_head, idempotency_key="index-restore-001")
    current = _capture_index(store, alice)
    _assert_closed_code("stale_authority", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=tombstone.after_head.generation,
        expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    assert current.records[0].generation == restored.after_head.generation
    store.close()


def test_exact_read_wal_snapshot_survives_concurrent_append(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    reader = _store(tmp_path, issuer)
    created = _create(reader, alice, payload=b"old-payload")
    writer = _store(tmp_path, issuer)
    original = knowledge_module._head_from_row
    interleaved = False

    def gate(row):
        nonlocal interleaved
        result = original(row)
        if not interleaved:
            interleaved = True
            appended = writer.append_revision(
                alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"new-payload",
                payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
                classification="private", expected_head=created.after_head,
                idempotency_key="wal-exact-read-append-001",
            )
            assert appended.after_head.generation == 2
        return result

    monkeypatch.setattr(knowledge_module, "_head_from_row", gate)
    assert reader.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ) == b"old-payload"
    assert interleaved
    _assert_closed_code("stale_authority", lambda: reader.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    writer.close()
    reader.close()


def test_index_snapshot_wal_interleave_returns_one_coherent_generation(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    reader = _store(tmp_path, issuer)
    created = _create(reader, alice, payload=b"generation-one")
    entered = threading.Event()
    committed = threading.Event()
    errors = []
    original = knowledge_module._snapshot_descriptors

    def append_while_reader_is_paused():
        external = None
        try:
            assert entered.wait(timeout=10)
            external = sqlite3.connect(tmp_path / "knowledge.sqlite3", isolation_level=None)
            external.execute("BEGIN IMMEDIATE")
            external.execute(
                "UPDATE knowledge_heads SET generation=2 WHERE owner_id='alice' AND knowledge_id='note-1'"
            )
            external.execute("COMMIT")
        except BaseException as exc:
            errors.append(exc)
        finally:
            if external is not None:
                external.close()
            committed.set()

    def gate(row):
        if not entered.is_set():
            assert reader._connection.in_transaction
            entered.set()
            assert committed.wait(timeout=10)
        return original(row)

    thread = threading.Thread(target=append_while_reader_is_paused)
    thread.start()
    monkeypatch.setattr(knowledge_module, "_snapshot_descriptors", gate)
    snapshot = _capture_index(reader, alice)
    thread.join(timeout=10)
    assert not thread.is_alive() and not errors
    assert snapshot.records[0].generation == 1
    after = _capture_index(reader, alice)
    assert after.records[0].generation == 2
    assert after.captured_head_boundary != snapshot.captured_head_boundary
    reader.close()


def test_snapshot_empty_and_full_golden_vectors(tmp_path, authority):
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    assert asdict(_capture(store, alice)) == _vector_snapshot("alice", [])
    assert asdict(_capture(store, bob, "bob")) == _vector_snapshot("bob", [])
    fixtures = [_metadata_fixture("z-note"), _metadata_fixture("a-note")]
    fixtures[0][1]["source_revisions"] = [{
        "source_revision_id": "source-r1", "source_kind": "document", "source_id": "source-1",
        "revision_ref": "revision-1", "source_content_sha256": SHA_B, "disposition": "accepted",
        "locator_digest": SHA_C, "authority_domain": "knowledge", "authority_revision": "authority-1",
        "authority_digest": SHA_D, "predecessor_source_revision_id": None,
        "observed_at": "2026-08-29T12:00:00.000000Z",
    }]
    fixtures[0][1]["provenance"] = [{"role": "origin", "source_revision_id": "source-r1",
        "knowledge_revision_id": None, "transformation_digest": None, "admission_receipt_digest": None}]
    fixtures[0][1]["references"] = [{"domain": "forge", "owner_id": "alice", "target_id": "forge-1",
                                     "revision_ref": "forge-r1", "content_sha256": SHA_E}]
    for head, revision in fixtures:
        _insert_metadata(store._connection, head, revision)
    snapshot = _capture(store, alice)
    assert asdict(snapshot) == _vector_snapshot("alice", fixtures)
    assert snapshot.heads[0].knowledge_id_digest < snapshot.heads[1].knowledge_id_digest
    assert type(snapshot) is KnowledgeMigrationSnapshot and type(snapshot.heads) is tuple
    assert all(type(head) is KnowledgeHeadMetadata for head in snapshot.heads)
    assert snapshot == _capture(store, alice, expected=snapshot.captured_head_boundary)
    with pytest.raises(FrozenInstanceError):
        snapshot.snapshot_digest = SHA_F
    with pytest.raises(FrozenInstanceError):
        snapshot.heads[0].state = "purged"
    assert not hasattr(snapshot, "__dict__") and not hasattr(snapshot.heads[0], "__dict__")
    for raw in ("alice", "z-note", "a-note", "source-r1", "fixture-subject", "private", "forge-1"):
        assert raw not in repr(snapshot) and raw not in _vector_json(asdict(snapshot))
    store.close()


def test_snapshot_full_record_matches_literal_golden_vector(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    _insert_metadata(store._connection, *_metadata_fixture())
    snapshot = _capture(store, alice)
    expected = {
        "schema_id": "odysseus.harbour.knowledge_migration_snapshot.v1",
        "owner_ref_digest": "sha256:f5242cfe8d293cb676e0583b41175753b0dabadcfe3ad9c4612fa2a6da271e22",
        "captured_head_boundary": "sha256:c04d59d78b60423a904175f59562b84db73d1b061f0374ed09810fd118ff542f",
        "heads": ({
            "knowledge_id_digest": "sha256:6f9e1581969c05e661c67e3071317db130e386d52acb3d34cda488bbdc18fb8a",
            "revision_id_digest": "sha256:3f6785681fd136b4d5173157c063d99602383bb738c6f4078aab6dd4685a58ad",
            "revision": 1,
            "generation": 1,
            "state": "active",
            "knowledge_kind": "note",
            "payload_sha256": "sha256:" + "a" * 64,
            "classification_digest": "sha256:469c092fd37b1a432a6875bf2c348f807ef487cf5cc1f15fc65606d8c3dbc737",
            "retention_digest": "sha256:eb588236e444d57fd632c92d6e3ba1141261c2a518506ff5859145e13c3a8ace",
            "provenance_digest": "sha256:4ead62003f13f6e9b81aff19a420680addd9fb7f2f772f5d72598fbccaff31ae",
            "source_revisions_digest": "sha256:63dbb051bcde91411683eab4e29c32cf2fa0a5142f1160e25b1c59914739a6e2",
            "references_digest": "sha256:c4ae1037e1a55135971d84af6dd801f7c23ec57e230dfff5222fd737793b4389",
            "revision_metadata_digest": "sha256:12009fe6ff92de9998729387574a136bbd51ffaa6ad7e70991e9d94a932c501b",
            "head_metadata_digest": "sha256:47ca8c990897e4929cfb38eefd4f983d8baa2e084604b149609cf850544a1aa1",
            "record_digest": "sha256:43ee2d8b7e27ad8cbc20209bcd44e1485f73ea6b16fa163e254649d1f95e6380",
        },),
        "snapshot_digest": "sha256:3d640196060913c26be79df449ff837c09cf6241d6c5e451271f7ca8a0597f1c",
    }
    assert asdict(snapshot) == expected
    assert snapshot.heads[0].record_digest == "sha256:43ee2d8b7e27ad8cbc20209bcd44e1485f73ea6b16fa163e254649d1f95e6380"
    assert snapshot.captured_head_boundary == "sha256:c04d59d78b60423a904175f59562b84db73d1b061f0374ed09810fd118ff542f"
    assert snapshot.snapshot_digest == "sha256:3d640196060913c26be79df449ff837c09cf6241d6c5e451271f7ca8a0597f1c"
    store.close()


def test_snapshot_deterministic_across_restart_and_unrelated_owner_writes(tmp_path, authority):
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    before = _capture(store, alice)
    _create(store, bob, owner="bob")
    assert _capture(store, alice, expected=before.captured_head_boundary) == before
    store.close()
    store = _store(tmp_path, issuer)
    assert store.available and _capture(store, alice) == before
    store.tombstone(alice, owner_id="alice", knowledge_id="note-1", expected_head=created.after_head,
                    idempotency_key="snapshot-tombstone")
    after = _capture(store, alice)
    assert after.heads[0].revision_metadata_digest == before.heads[0].revision_metadata_digest
    assert after.heads[0].head_metadata_digest != before.heads[0].head_metadata_digest
    assert after.captured_head_boundary != before.captured_head_boundary
    _assert_closed_code("stale_authority", lambda: _capture(store, alice, expected=before.captured_head_boundary))
    assert not store._connection.in_transaction and store.available
    store.close()


def test_snapshot_auth_and_exact_boundary_validation_precede_sql(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    statements = []
    store._connection.set_trace_callback(statements.append)
    _assert_closed_code("unauthenticated", lambda: _capture(store, object(), expected=object()))
    _assert_closed_code("owner_scope_denied", lambda: _capture(store, alice, owner="bob", expected=object()))
    for invalid in (False, 1, b"sha256:" + b"a" * 64, [], {}, iter(()), _HostileString(SHA_A), "SHA256:" + "A" * 64):
        _assert_closed_code("validation_failed", lambda: _capture(store, alice, expected=invalid))
    assert statements == [] and store.available
    store.close()


def test_snapshot_reads_only_owner_metadata_without_replay_or_side_effects(tmp_path, authority, monkeypatch):
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    _create(store, alice, payload=b"PRIVATE-PAYLOAD-DO-NOT-READ")
    # Corrupt another owner's metadata: it must not even be decoded.
    _insert_metadata(store._connection, *_metadata_fixture(owner="bob"),
                     overrides={"source_revisions_json": "broken"})
    connection = store._connection
    before = _durable_knowledge_image(connection)
    changes = connection.total_changes
    reads, statements = [], []

    def authorize(action, arg1, arg2, *_args):
        if action == sqlite3.SQLITE_READ:
            reads.append((arg1, arg2))
            return sqlite3.SQLITE_OK if arg1 in {"knowledge_heads", "knowledge_revisions"} else sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_PRAGMA}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def prohibited(*args, **kwargs):
        pytest.fail("snapshot entered payload, replay or connection creation")

    monkeypatch.setattr(store, "_transaction", prohibited)
    monkeypatch.setattr(store, "_verify", prohibited)
    monkeypatch.setattr(store, "read_payload", prohibited)
    monkeypatch.setattr(knowledge_module.sqlite3, "connect", prohibited)
    connection.set_authorizer(authorize)
    connection.set_trace_callback(statements.append)
    result = _capture(store, alice)
    connection.set_authorizer(None)
    assert len(result.heads) == 1 and reads
    assert statements[0] == "BEGIN DEFERRED" and statements[-1] == "ROLLBACK"
    selects = [sql for sql in statements if sql.startswith("SELECT ")]
    assert len(selects) == 4
    assert all(("WHERE h.owner_id='alice'" in sql or "WHERE owner_id='alice'" in sql)
               for sql in selects)
    assert before == _durable_knowledge_image(connection) and changes == connection.total_changes
    assert not connection.in_transaction and store.available
    store.close()


def test_snapshot_is_one_read_boundary_during_concurrent_owner_change(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    before = _capture(store, alice)
    other = _store(tmp_path, issuer)
    original = knowledge_module._snapshot_descriptors
    changed = False

    def interleave(row):
        nonlocal changed
        if not changed:
            changed = True
            assert store._connection.in_transaction
            other.tombstone(alice, owner_id="alice", knowledge_id="note-1", expected_head=created.after_head,
                            idempotency_key="concurrent-snapshot-change")
        return original(row)

    monkeypatch.setattr(knowledge_module, "_snapshot_descriptors", interleave)
    assert _capture(store, alice) == before
    assert changed and _capture(store, alice).captured_head_boundary != before.captured_head_boundary
    other.close()
    store.close()


def test_snapshot_reentry_and_borrowed_transaction_are_not_cleaned_up(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    connection = store._connection
    nested = []

    def clock():
        _assert_closed_code("storage_failed", lambda: _capture(store, alice))
        nested.append(True)
        return NOW

    monkeypatch.setattr(store, "_clock", clock)
    assert _capture(store, alice).heads == () and nested == [True]
    connection.execute("BEGIN")
    connection.execute("INSERT INTO knowledge_meta VALUES('borrowed','uncommitted')")
    statements = []
    connection.set_trace_callback(statements.append)
    _assert_closed_code("storage_failed", lambda: _capture(store, alice))
    assert statements == [] and connection.in_transaction
    assert connection.execute("SELECT value FROM knowledge_meta WHERE key='borrowed'").fetchone()[0] == "uncommitted"
    connection.execute("ROLLBACK")
    store.close()


@pytest.mark.parametrize("error,code", [(sqlite3.OperationalError, "storage_failed"), (RuntimeError, "store_corrupt")])
def test_snapshot_errors_roll_back_without_leaks(tmp_path, authority, monkeypatch, error, code):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)

    def fail(*_args):
        assert store._connection.in_transaction
        raise error("private database failure")

    monkeypatch.setattr(knowledge_module, "_capture_owner_metadata", fail)
    _assert_closed_code(code, lambda: _capture(store, alice))
    assert not store._connection.in_transaction
    assert store.quarantined == (code == "store_corrupt")
    store.close()


def test_snapshot_cleanup_failure_never_returns_a_result(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    connection = store._connection

    def reject_rollback(action, arg, *_args):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and arg == "ROLLBACK" else sqlite3.SQLITE_OK

    connection.set_authorizer(reject_rollback)
    _assert_closed_code("storage_failed", lambda: _capture(store, alice))
    assert store.quarantined and connection.in_transaction
    connection.set_authorizer(None)
    connection.execute("ROLLBACK")
    store.close()


@pytest.mark.parametrize("reader", [_capture, _capture_index], ids=["migration", "index"])
@pytest.mark.parametrize("table,column,value", [
    ("knowledge_heads", "generation", 0), ("knowledge_heads", "generation", 1.5),
    ("knowledge_heads", "state", "unknown"), ("knowledge_heads", "knowledge_kind", "document"),
    ("knowledge_heads", "current_payload_sha256", SHA_B),
    ("knowledge_heads", "current_revision_id", "absent"),
    ("knowledge_heads", "tombstoned_at", "2026-08-29T12:00:00.000000Z"),
    ("knowledge_revisions", "owner_id", "bob"), ("knowledge_revisions", "revision", 0),
    ("knowledge_revisions", "revision_digest", SHA_F), ("knowledge_revisions", "revision_digest", SHA_F.upper()),
    ("knowledge_revisions", "source_revisions_json", '[{"duplicate":null,"duplicate":null}]'),
    ("knowledge_revisions", "source_revisions_json", "[NaN]"),
    ("knowledge_revisions", "provenance_json", "[] trailing"),
    ("knowledge_revisions", "references_json", "{}"),
    ("knowledge_revisions", "retention_json", "null"),
    ("knowledge_revisions", "principal_ref_json", "{}"),
    ("knowledge_revisions", "classification", sqlite3.Binary(b"private")),
    ("knowledge_revisions", "classification", "privat\u00e9"),
    ("knowledge_revisions", "created_at", "not-a-timestamp"),
    ("knowledge_revisions", "blob_id", "knowledge-blob:" + "0" * 64),
])
def test_snapshot_corrupt_metadata_is_quarantined(tmp_path, authority, reader, table, column, value):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    if table == "knowledge_revisions":
        _insert_metadata(store._connection, *_metadata_fixture(), overrides={column: value})
    else:
        head, revision = _metadata_fixture()
        head[column] = value
        _insert_metadata(store._connection, head, revision)
    _assert_closed_code("store_corrupt", lambda: reader(store, alice))
    assert store.quarantined and not store._connection.in_transaction
    store.close()


@pytest.mark.parametrize("reader", [_capture, _capture_index], ids=["migration", "index"])
@pytest.mark.parametrize("corruption", ["revision_zero", "orphan_parent", "active_tombstone", "generation_before_revision"])
def test_snapshot_rejects_recomputed_semantic_corruption(tmp_path, authority, reader, corruption):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    head, revision = _metadata_fixture()
    if corruption == "revision_zero":
        revision["revision"] = 0
    elif corruption == "orphan_parent":
        revision.update(revision_id="revision:child", revision=2, parent_revision_id="missing-parent")
        head.update(current_revision_id="revision:child", generation=2)
    elif corruption == "active_tombstone":
        head["tombstoned_at"] = "2026-08-29T12:00:00.000000Z"
    else:
        revision.update(revision_id="revision:child", revision=2, parent_revision_id="prior-revision")
        head.update(current_revision_id="revision:child", generation=1)
    _insert_metadata(store._connection, head, revision)
    _assert_closed_code("store_corrupt", lambda: reader(store, alice))
    assert store.quarantined and not store._connection.in_transaction
    store.close()


@pytest.mark.parametrize("column,limit", [("classification", 128), ("source_revisions_json", 262144),
    ("provenance_json", 131072), ("references_json", 131072), ("retention_json", 1024), ("principal_ref_json", 1024)])
def test_snapshot_field_byte_limits_precede_transfer_and_json(tmp_path, authority, monkeypatch, column, limit):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    _insert_metadata(store._connection, *_metadata_fixture(), overrides={column: "x" * (limit + 1)})
    statements = []
    store._connection.set_trace_callback(statements.append)
    monkeypatch.setattr(knowledge_module, "_strict_json", lambda *_args: pytest.fail("oversized JSON decoded"))
    _assert_closed_code("snapshot_limit_exceeded", lambda: _capture(store, alice))
    assert len([sql for sql in statements if sql.startswith("SELECT ")]) == 1
    assert store.available and not store._connection.in_transaction
    store.close()


@pytest.mark.parametrize("value", ['[' * 2000 + ']' * 2000, '[' + ','.join('{}' for _ in range(129)) + ']'],
                         ids=["excess_depth", "excess_items"])
def test_snapshot_depth_and_item_limits_precede_json_decoder(tmp_path, authority, monkeypatch, value):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    _insert_metadata(store._connection, *_metadata_fixture(), overrides={"source_revisions_json": value})
    monkeypatch.setattr(knowledge_module, "_strict_json", lambda *_args: pytest.fail("unbounded JSON decoded"))
    _assert_closed_code("snapshot_limit_exceeded", lambda: _capture(store, alice))
    assert store.available and not store._connection.in_transaction
    store.close()


def test_snapshot_head_count_limit_precedes_missing_revision_transfer(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    store._connection.executemany("INSERT INTO knowledge_heads VALUES(?,?,?,?,?,?,?,?,?)", (
        ("alice", f"note-{index}", "note", "absent", SHA_A, 1, "active", None, None)
        for index in range(10001)
    ))
    _assert_closed_code("snapshot_limit_exceeded", lambda: _capture(store, alice))
    assert store.available and not store._connection.in_transaction
    store.close()


def test_snapshot_aggregate_byte_limit_precedes_metadata_transfer(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    for index in range(64):
        _insert_metadata(store._connection, *_metadata_fixture(f"note-{index}"), source_padding=262144)
    statements = []
    store._connection.set_trace_callback(statements.append)
    monkeypatch.setattr(knowledge_module, "_strict_json", lambda *_args: pytest.fail("aggregate overflow decoded"))
    _assert_closed_code("snapshot_limit_exceeded", lambda: _capture(store, alice))
    assert len([sql for sql in statements if sql.startswith("SELECT ")]) == 1
    assert store.available and not store._connection.in_transaction
    store.close()


@pytest.mark.parametrize("construction", ["wrong_domain", "dropped_null", "double_hash", "raw_json"])
def test_snapshot_rejects_wrong_stored_digest_constructions(tmp_path, authority, construction):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    head, revision = _metadata_fixture()
    wrong = {
        "wrong_domain": _vector_domain("revision_metadata", revision),
        "dropped_null": _vector_hash({key: value for key, value in revision.items() if value is not None}),
        "double_hash": _vector_hash(_vector_hash(revision)),
        "raw_json": "sha256:" + hashlib.sha256(_vector_json(revision).encode("ascii")).hexdigest(),
    }[construction]
    _insert_metadata(store._connection, head, revision, overrides={"revision_digest": wrong})
    _assert_closed_code("store_corrupt", lambda: _capture(store, alice))
    assert store.quarantined and not store._connection.in_transaction
    store.close()


def test_snapshot_accepts_exact_field_and_counter_limits(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    head, revision = _metadata_fixture("n" * 128)
    revision.update(revision_id="revision-limit", revision=2,
                    parent_revision_id="prior-revision", classification="c" * 128)
    head.update(current_revision_id="revision-limit", generation=2**63 - 1)
    source = {"source_revision_id": "source", "source_kind": "document", "source_id": "source-1",
              "revision_ref": "revision-1", "source_content_sha256": SHA_B, "disposition": "accepted",
              "locator_digest": SHA_C, "authority_domain": "knowledge", "authority_revision": "authority-1",
              "authority_digest": SHA_D, "predecessor_source_revision_id": None,
              "observed_at": "2026-08-29T12:00:00.000000Z"}
    revision["source_revisions"] = [{**source, "source_revision_id": f"source-{index}"} for index in range(128)]
    revision["provenance"] = [{"role": "origin", "source_revision_id": f"source-{index}",
        "knowledge_revision_id": None, "transformation_digest": None, "admission_receipt_digest": None}
        for index in range(128)]
    revision["references"] = [{"domain": "forge", "owner_id": "alice", "target_id": f"forge-{index}",
                              "revision_ref": "forge-r1", "content_sha256": SHA_E} for index in range(128)]
    overrides = {field + "_json": _vector_json(revision[field]).ljust(limit) for field, limit in (
        ("source_revisions", 262144), ("provenance", 131072), ("references", 131072),
        ("retention", 1024), ("principal_ref", 1024),
    )}
    _insert_metadata(store._connection, head, revision, overrides=overrides)
    predecessor = dict(revision, revision_id="prior-revision", revision=1, parent_revision_id=None)
    _insert_revision(store._connection, predecessor)
    snapshot = _capture(store, alice)
    assert asdict(snapshot) == _vector_snapshot("alice", [(head, revision)])
    assert snapshot.heads[0].revision == 2 and snapshot.heads[0].generation == 2**63 - 1
    assert store.available and not store._connection.in_transaction
    store.close()


def test_max_valid_media_type_survives_full_chain_exports_and_invalid_write_preserves_admission(
    tmp_path, authority,
):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    media_type = "a" * 64 + "/" + "b" * 64
    invalid_media_type = "a" * 65 + "/" + "b" * 64
    assert len(media_type) == 129 and len(invalid_media_type) == 130
    created = _create(store, alice, media_type=media_type)
    appended = store.append_revision(
        alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"two",
        payload_schema_id="harbour.note.v1", payload_media_type=media_type,
        classification="private", expected_head=created.after_head,
        idempotency_key="max-media-append-001",
    )
    migration = _capture(store, alice)
    index = _capture_index(store, alice)
    assert migration.heads[0].revision == index.records[0].revision == 2
    assert store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=appended.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ) == b"two"
    before = _durable_knowledge_image(store._connection)
    _assert_closed_code("validation_failed", lambda: store.append_revision(
        alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"three",
        payload_schema_id="harbour.note.v1", payload_media_type=invalid_media_type,
        classification="private", expected_head=appended.after_head,
        idempotency_key="invalid-media-append-001",
    ))
    assert store.available and before == _durable_knowledge_image(store._connection)
    store.close()
    reopened = _store(tmp_path, issuer)
    assert reopened.available
    assert _capture(reopened, alice) == migration
    assert _capture_index(reopened, alice) == index
    reopened.close()


def test_snapshot_accepts_ten_thousand_heads(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    connection = store._connection
    connection.execute("BEGIN")
    for index in range(10000):
        _insert_metadata(connection, *_metadata_fixture(f"note-{index}"))
    connection.execute("COMMIT")
    snapshot = _capture(store, alice)
    assert len(snapshot.heads) == 10000
    identities = [head.knowledge_id_digest for head in snapshot.heads]
    assert identities == sorted(set(identities))
    assert store.available and not connection.in_transaction
    store.close()


def test_snapshot_rejects_duplicate_opaque_head_identities(tmp_path, authority, monkeypatch):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    _insert_metadata(store._connection, *_metadata_fixture("first"))
    _insert_metadata(store._connection, *_metadata_fixture("second"))
    domain = knowledge_module._snapshot_digest
    monkeypatch.setattr(knowledge_module, "_snapshot_digest",
                        lambda kind, value: SHA_F if kind == "knowledge_id" else domain(kind, value))
    _assert_closed_code("store_corrupt", lambda: _capture(store, alice))
    assert store.quarantined and not store._connection.in_transaction
    store.close()


@pytest.mark.parametrize("lifecycle", ["active", "tombstoned", "purge_pending", "purged"])
def test_snapshot_captures_all_current_lifecycle_states_without_payload(tmp_path, authority, lifecycle):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    head, revision = _metadata_fixture()
    head["state"] = lifecycle
    if lifecycle != "active":
        head.update(tombstoned_at="2026-08-29T12:00:00.000000Z", generation=2)
    if lifecycle in {"purge_pending", "purged"}:
        head.update(purge_requested_at="2026-08-29T12:01:00.000000Z", generation=3)
    if lifecycle == "purged":
        head["generation"] = 4
    _insert_metadata(store._connection, head, revision)
    assert asdict(_capture(store, alice)) == _vector_snapshot("alice", [(head, revision)])
    store.close()


def test_index_and_payload_reads_reject_borrowed_transactions_without_rollback(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    connection = store._connection
    connection.execute("BEGIN")
    _assert_closed_code("storage_failed", lambda: store.capture_index_snapshot(alice, owner_id="alice"))
    assert connection.in_transaction
    _assert_closed_code("storage_failed", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    assert connection.in_transaction
    connection.execute("ROLLBACK")
    store.close()


def test_index_snapshot_golden_digest_and_sql_boundary(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    statements = []
    store._connection.set_trace_callback(statements.append)
    snapshot = _capture_index(store, alice)
    record = asdict(snapshot.records[0])
    record_digest_body = {"owner_id": "alice", "record": {k: v for k, v in record.items() if k != "record_digest"}}
    encoded = json.dumps({"schema_id": KNOWLEDGE_INDEX_SNAPSHOT_SCHEMA, "kind": "index_record",
                          "value": record_digest_body}, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("ascii")
    expected_record_digest = "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()
    assert record["record_digest"] == expected_record_digest
    assert snapshot.records[0].payload_sha256 == created.after_head.current_payload_sha256
    assert not any("knowledge_blobs" in statement.lower() for statement in statements)
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"))
                   for statement in statements)
    store.close()


def test_index_snapshot_full_record_matches_literal_golden_vector(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    _insert_metadata(store._connection, *_metadata_fixture())
    snapshot = _capture_index(store, alice)
    expected_record = {
        "knowledge_id": "fixture-note",
        "knowledge_kind": "note",
        "revision_id": "revision:fixture-note",
        "revision": 1,
        "parent_revision_id": None,
        "generation": 1,
        "state": "active",
        "tombstoned_at": None,
        "purge_requested_at": None,
        "payload_schema_id": "harbour.note.v1",
        "payload_media_type": "text/plain",
        "payload_sha256": "sha256:" + "a" * 64,
        "classification": "private",
        "source_revisions_json": "[]",
        "provenance_json": "[]",
        "references_json": "[]",
        "retention_json": "{\"deletion_mode\":\"tombstone_only\",\"grace_until\":null,"
                          "\"legal_hold\":false,\"retain_until\":null,\"retention_class\":\"user_managed\"}",
        "created_at": "2026-08-29T12:00:00.000000Z",
        "record_digest": "sha256:a532d4dfa844598c537702de4653042d8be42f36d451c6346851616b69ef3103",
    }
    expected = {
        "schema_id": "odysseus.harbour.knowledge_index_snapshot.v1",
        "owner_id": "alice",
        "captured_head_boundary": "sha256:a46757735f8117c522d33df2043f24f594e4222700d7480d138baf5c33676203",
        "records": (expected_record,),
        "snapshot_digest": "sha256:b0928c4288340a15879711713b2c1cc59d3a3a0d681be5e6d613fdfc7b834dd1",
    }
    assert asdict(snapshot) == expected
    assert snapshot.records[0].record_digest == "sha256:a532d4dfa844598c537702de4653042d8be42f36d451c6346851616b69ef3103"
    assert snapshot.captured_head_boundary == "sha256:a46757735f8117c522d33df2043f24f594e4222700d7480d138baf5c33676203"
    assert snapshot.snapshot_digest == "sha256:b0928c4288340a15879711713b2c1cc59d3a3a0d681be5e6d613fdfc7b834dd1"
    store.close()


@pytest.mark.parametrize("column,value", [
    ("knowledge_kind", "wrong-kind"), ("payload_schema_id", "bad schema"),
    ("payload_media_type", "bad media"), ("classification", "bad class"),
    ("parent_revision_id", "missing-parent"),
])
def test_exact_read_rejects_recomputed_revision_field_corruption(tmp_path, authority, column, value):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_revisions_no_update")
    connection.execute("DROP TRIGGER knowledge_blobs_no_update")
    row = connection.execute("SELECT * FROM knowledge_revisions WHERE revision_id=?", (created.new_revision_id,)).fetchone()
    fields = list(row)
    fields[knowledge_module._SNAPSHOT_REVISION_COLUMNS.index(column)] = value
    record = knowledge_module._revision_record(tuple(fields))
    digest = knowledge_module._canonical_digest(record)
    connection.execute(f"UPDATE knowledge_revisions SET {column}=?,revision_digest=? WHERE revision_id=?",
                       (value, digest, created.new_revision_id))
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    store.close()


@pytest.mark.parametrize("corruption", ["revision_zero", "orphan_parent", "active_tombstone"])
def test_exact_read_rejects_recomputed_semantic_corruption(tmp_path, authority, corruption):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    head, revision = _metadata_fixture()
    if corruption == "revision_zero":
        revision["revision"] = 0
    elif corruption == "orphan_parent":
        revision.update(revision_id="revision:child", revision=2, parent_revision_id="missing-parent")
        head.update(current_revision_id="revision:child", generation=2)
    else:
        head["tombstoned_at"] = "2026-08-29T12:00:00.000000Z"
    _insert_metadata(store._connection, head, revision)
    _assert_closed_code("store_corrupt", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id=head["knowledge_id"],
        revision_id=head["current_revision_id"], expected_generation=head["generation"],
        expected_payload_sha256=head["current_payload_sha256"],
    ))
    assert store.quarantined and not store._connection.in_transaction
    store.close()


def _rewrite_revision_with_valid_digest(connection, revision_id, column, value):
    connection.execute("DROP TRIGGER knowledge_revisions_no_update")
    row = connection.execute(
        "SELECT * FROM knowledge_revisions WHERE revision_id=?", (revision_id,),
    ).fetchone()
    fields = list(row)
    fields[knowledge_module._SNAPSHOT_REVISION_COLUMNS.index(column)] = value
    digest = knowledge_module._canonical_digest(knowledge_module._revision_record(tuple(fields)))
    connection.execute(
        f"UPDATE knowledge_revisions SET {column}=?,revision_digest=? WHERE revision_id=?",
        (value, digest, revision_id),
    )
    connection.execute(
        "CREATE TRIGGER knowledge_revisions_no_update BEFORE UPDATE ON knowledge_revisions "
        "BEGIN SELECT RAISE(ABORT,'immutable'); END"
    )
    connection.commit()


def _delete_revision_for_chain_fixture(connection, revision_id):
    connection.execute("DROP TRIGGER knowledge_revisions_no_delete")
    connection.execute("DELETE FROM knowledge_revisions WHERE revision_id=?", (revision_id,))
    connection.execute(
        "CREATE TRIGGER knowledge_revisions_no_delete BEFORE DELETE ON knowledge_revisions "
        "BEGIN SELECT RAISE(ABORT,'immutable'); END"
    )
    connection.commit()


@pytest.mark.parametrize("api", ["migration", "index", "exact"], ids=["migration", "index", "exact-read"])
@pytest.mark.parametrize("corruption", ["changed_ancestor_kind", "deleted_first_ancestor"])
def test_all_read_apis_reject_semantically_corrupt_ancestor_chains(tmp_path, authority, api, corruption):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    second = store.append_revision(
        alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"two",
        payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
        classification="private", expected_head=created.after_head,
        idempotency_key="ancestor-chain-append-002",
    )
    current = second
    if corruption == "changed_ancestor_kind":
        _rewrite_revision_with_valid_digest(
            store._connection, created.new_revision_id, "knowledge_kind", "document",
        )
    else:
        third = store.append_revision(
            alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"three",
            payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
            classification="private", expected_head=second.after_head,
            idempotency_key="ancestor-chain-append-003",
        )
        current = third
        _delete_revision_for_chain_fixture(store._connection, created.new_revision_id)

    if api == "migration":
        callback = lambda: _capture(store, alice)
    elif api == "index":
        callback = lambda: _capture_index(store, alice)
    else:
        callback = lambda: store.read_payload_at_generation(
            alice, owner_id="alice", knowledge_id="note-1", revision_id=current.new_revision_id,
            expected_generation=current.after_head.generation,
            expected_payload_sha256=current.after_head.current_payload_sha256,
        )
    _assert_closed_code("store_corrupt", callback)
    assert store.quarantined and not store._connection.in_transaction
    store.close()


def test_exact_read_rejects_blob_size_or_hash_corruption(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    blob = store._connection.execute("SELECT blob_id FROM knowledge_blobs").fetchone()[0]
    store._connection.execute("DROP TRIGGER knowledge_blobs_no_update")
    store._connection.execute("UPDATE knowledge_blobs SET payload_size=999 WHERE blob_id=?", (blob,))
    store._connection.commit()
    _assert_closed_code("store_corrupt", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    store.close()


@pytest.mark.parametrize("error", [sqlite3.OperationalError, OSError], ids=["sqlite", "os"])
def test_exact_read_storage_failure_rolls_back_without_quarantine_and_can_read_again(
    tmp_path, authority, monkeypatch, error,
):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice, payload=b"read-again")
    original = knowledge_module._revision_record

    def fail(_row):
        assert store._connection.in_transaction
        raise error("private storage failure")

    monkeypatch.setattr(knowledge_module, "_revision_record", fail)
    _assert_closed_code("storage_failed", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=created.after_head.generation,
        expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    assert not store._connection.in_transaction and store.available and not store.quarantined
    monkeypatch.setattr(knowledge_module, "_revision_record", original)
    assert store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=created.after_head.generation,
        expected_payload_sha256=created.after_head.current_payload_sha256,
    ) == b"read-again"
    store.close()


def test_index_auth_precedes_sql_and_external_head_fence(tmp_path, authority):
    issuer, alice, bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    statements = []
    store._connection.set_trace_callback(statements.append)
    _assert_closed_code("owner_scope_denied", lambda: store.capture_index_snapshot(bob, owner_id="alice"))
    _assert_closed_code("owner_scope_denied", lambda: store.read_payload_at_generation(
        bob, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    assert statements == []
    external = sqlite3.connect(tmp_path / "knowledge.sqlite3")
    external.execute("UPDATE knowledge_heads SET generation=2 WHERE owner_id='alice' AND knowledge_id='note-1'")
    external.commit()
    external.close()
    assert _capture_index(store, alice).records[0].generation == 2
    _assert_closed_code("stale_authority", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=1, expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    store.close()


def test_index_and_exact_read_boundaries_follow_full_lifecycle(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    retention = {
        "retention_class": "time_bound", "deletion_mode": "purge_eligible",
        "retain_until": "2026-08-28T00:00:00.000000Z",
        "grace_until": "2026-08-28T12:00:00.000000Z", "legal_hold": False,
    }
    created = _create(store, alice, retention=retention)
    previous = _capture_index(store, alice)
    appended = store.append_revision(
        alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"two",
        payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
        classification="private", retention=retention, expected_head=created.after_head,
        idempotency_key="full-life-append-001",
    )
    current = _capture_index(store, alice)
    assert current.captured_head_boundary != previous.captured_head_boundary
    _assert_closed_code("stale_authority", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=created.new_revision_id,
        expected_generation=created.after_head.generation,
        expected_payload_sha256=created.after_head.current_payload_sha256,
    ))
    assert store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=appended.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ) == b"two"

    tombstoned = store.tombstone(alice, owner_id="alice", knowledge_id="note-1",
                                 expected_head=appended.after_head, idempotency_key="full-life-tombstone-001")
    tombstone_index = _capture_index(store, alice)
    assert tombstone_index.captured_head_boundary != current.captured_head_boundary
    _assert_closed_code("content_unavailable", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=tombstoned.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ))
    restored = store.restore(alice, owner_id="alice", knowledge_id="note-1",
                              expected_head=tombstoned.after_head, idempotency_key="full-life-restore-001")
    restored_index = _capture_index(store, alice)
    assert restored_index.captured_head_boundary != tombstone_index.captured_head_boundary
    _assert_closed_code("stale_authority", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=tombstoned.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ))
    assert store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=restored.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ) == b"two"
    tombstoned_again = store.tombstone(alice, owner_id="alice", knowledge_id="note-1",
                                       expected_head=restored.after_head, idempotency_key="full-life-tombstone-002")
    after_second_tombstone = _capture_index(store, alice)
    assert after_second_tombstone.captured_head_boundary != restored_index.captured_head_boundary
    pending = store.request_purge(alice, owner_id="alice", knowledge_id="note-1",
                                  expected_head=tombstoned_again.after_head,
                                  idempotency_key="full-life-purge-request-001")
    pending_index = _capture_index(store, alice)
    assert pending_index.captured_head_boundary != after_second_tombstone.captured_head_boundary
    purged = _complete(store, alice, pending.after_head, idempotency_key="full-life-purge-complete-001")
    purged_index = _capture_index(store, alice)
    assert purged.after_head.state == "purged"
    assert purged_index.records[0].state == "purged"
    assert purged_index.captured_head_boundary != pending_index.captured_head_boundary
    _assert_closed_code("content_unavailable", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=purged.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ))
    store.close()


def test_index_fences_append_and_purge_lifecycle(tmp_path, authority):
    issuer, alice, _bob = authority
    store = _store(tmp_path, issuer)
    created = _create(store, alice)
    before = _capture_index(store, alice)
    appended = store.append_revision(
        alice, owner_id="alice", knowledge_id="note-1", payload_bytes=b"two",
        payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
        classification="private", expected_head=created.after_head, idempotency_key="index-append-001",
    )
    _assert_closed_code("stale_authority", lambda: _capture_index(store, alice, expected=before.captured_head_boundary))
    tombstone = store.tombstone(alice, owner_id="alice", knowledge_id="note-1",
                                expected_head=appended.after_head, idempotency_key="index-tombstone-002")
    _assert_closed_code("content_unavailable", lambda: store.read_payload_at_generation(
        alice, owner_id="alice", knowledge_id="note-1", revision_id=appended.new_revision_id,
        expected_generation=tombstone.after_head.generation,
        expected_payload_sha256=appended.after_head.current_payload_sha256,
    ))
    store.close()


def test_inbox_signal_snapshot_binds_current_purge_request_and_survives_restart(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    snapshot = _capture_signals(store, principal, owner)
    assert type(snapshot) is KnowledgeInboxSignalSnapshot
    assert snapshot.schema_id == KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA
    assert type(snapshot.signals) is tuple and len(snapshot.signals) == 1 and not snapshot.clipped
    assert tuple(asdict(snapshot)) == (
        "schema_id", "owner_binding_digest", "captured_head_boundary", "signals", "clipped", "snapshot_digest",
    )
    signal = snapshot.signals[0]
    assert type(signal) is KnowledgeInboxSignal
    assert signal.signal_type == "purge_requested"
    assert signal.state == "purge_pending"
    assert signal.generation == receipt.after_head.generation
    assert signal.revision == 1 and signal.event_digest.startswith("sha256:")
    assert all(value not in repr(snapshot) for value in (owner, "note-1", receipt.receipt_id))
    with pytest.raises((FrozenInstanceError, AttributeError)):
        signal.state = "active"
    boundary = snapshot.captured_head_boundary
    digest = snapshot.snapshot_digest
    store.close()
    reopened = _store(tmp_path, issuer)
    restored = _capture_signals(reopened, principal, owner, expected=boundary)
    assert restored.snapshot_digest == digest and restored == snapshot
    reopened.close()


def test_inbox_signal_snapshot_excludes_non_pending_and_enforces_sid_scope(tmp_path, authority):
    issuer, alice, bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _create(store, principal, owner=owner, key="create-active-signal")
    tombstoned = _create(store, principal, owner=owner, knowledge_id="tomb", key="create-tomb-signal")
    store.tombstone(principal, owner_id=owner, knowledge_id="tomb", expected_head=tombstoned.after_head,
                    idempotency_key="tombstone-only-signal")
    _purge_pending_signal(store, principal, owner, knowledge_id="pending", suffix="002")
    signals = _capture_signals(store, principal, owner)
    assert len(signals.signals) == 1 and signals.signals[0].state == "purge_pending"
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, alice, owner))
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, principal, "owner_abcdefabcdefabcdefabcdefabcdefab"))
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, bob, "bob"))
    alias = issuer.issue(subject_id="alias-subject", owner_id=owner, auth_context_digest=SHA_A, ttl_seconds=3600)
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, alias, owner))
    store.close()


def test_inbox_signal_snapshot_limits_boundary_and_sql_content_boundary(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner, knowledge_id="a", suffix="003")
    _purge_pending_signal(store, principal, owner, knowledge_id="b", suffix="004")
    first = _capture_signals(store, principal, owner, limit=1)
    assert first.clipped and len(first.signals) == 1
    assert first.signals[0].signal_type == "purge_requested"
    _assert_closed_code("validation_failed", lambda: _capture_signals(store, principal, owner, limit=True))
    _assert_closed_code("validation_failed", lambda: _capture_signals(store, principal, owner, limit=101))
    _assert_closed_code("stale_authority", lambda: _capture_signals(
        store, principal, owner, expected="sha256:" + "0" * 64,
    ))
    statements = []
    store._connection.set_trace_callback(statements.append)
    _capture_signals(store, principal, owner)
    assert not any("knowledge_blobs" in statement.lower() or "payload_bytes" in statement.lower() for statement in statements)
    assert sum("join knowledge_events" in statement.lower() for statement in statements) == 2
    store.close()


def test_inbox_signal_snapshot_rejects_receipt_semantic_corruption_and_preserves_borrowed_transaction(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    connection.execute("BEGIN")
    _assert_closed_code("storage_failed", lambda: _capture_signals(store, principal, owner))
    assert connection.in_transaction
    connection.execute("ROLLBACK")
    connection.execute("DROP TRIGGER knowledge_receipts_no_update")
    row = connection.execute("SELECT receipt_json FROM knowledge_receipts WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    body = json.loads(row[0])
    body["command_kind"] = "tombstone"
    text = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = knowledge_module._canonical_digest(knowledge_module._strict_json(text))
    connection.execute("UPDATE knowledge_receipts SET receipt_json=?,receipt_digest=? WHERE receipt_id=?",
                       (text, digest, receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined and not connection.in_transaction
    store.close()


def test_inbox_signal_snapshot_rejects_missing_current_event_before_value_decode(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_events_no_delete")
    connection.execute("DELETE FROM knowledge_events WHERE receipt_id=?", (receipt.receipt_id,))
    connection.execute("CREATE TRIGGER knowledge_events_no_delete BEFORE DELETE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined and not connection.in_transaction
    store.close()


@pytest.mark.parametrize("fault", ["missing_receipt", "wrong_kind", "wrong_generation"], ids=["receipt", "kind", "generation"])
def test_inbox_signal_snapshot_rejects_current_join_faults_after_positive_capture(tmp_path, authority, fault):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    if fault == "missing_receipt":
        connection.execute("DROP TRIGGER knowledge_receipts_no_delete")
        connection.execute("DELETE FROM knowledge_receipts WHERE receipt_id=?", (receipt.receipt_id,))
        connection.execute("CREATE TRIGGER knowledge_receipts_no_delete BEFORE DELETE ON knowledge_receipts "
                           "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    else:
        connection.execute("DROP TRIGGER knowledge_events_no_update")
        if fault == "wrong_kind":
            connection.execute("UPDATE knowledge_events SET command_kind='tombstone' WHERE receipt_id=?", (receipt.receipt_id,))
        else:
            connection.execute("UPDATE knowledge_events SET generation=generation+1 WHERE receipt_id=?", (receipt.receipt_id,))
        connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                           "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined and not connection.in_transaction
    store.close()


def test_inbox_signal_snapshot_purged_exclusion_and_hostile_limit_type(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    pending = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    store.complete_purge(
        principal, owner_id=owner, knowledge_id="note-1", expected_head=pending.after_head,
        policy_receipt=SHA_A, projection_deletion_receipts=(SHA_B,), backup_deletion_receipts=(SHA_C,),
        idempotency_key="purge-signal-complete-001",
    )
    empty = _capture_signals(store, principal, owner)
    assert empty.signals == () and not empty.clipped
    class IntSubclass(int):
        pass
    _assert_closed_code("validation_failed", lambda: _capture_signals(store, principal, owner, limit=IntSubclass(1)))
    store.close()


@pytest.mark.parametrize("table,column,trigger", [
    ("knowledge_events", "event_json", "knowledge_events_no_update"),
    ("knowledge_receipts", "receipt_json", "knowledge_receipts_no_update"),
])
def test_inbox_signal_snapshot_rejects_oversized_join_json_before_decoder(
    tmp_path, authority, monkeypatch, table, column, trigger,
):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    row = connection.execute(f"SELECT {column} FROM {table} WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    padded = row[0] + " " * (8193 - len(row[0]))
    connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute(f"UPDATE {table} SET {column}=? WHERE receipt_id=?", (padded, receipt.receipt_id))
    connection.execute(f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    monkeypatch.setattr(knowledge_module, "_signal_event_and_receipt", lambda *_args: pytest.fail("joined value decoded"))
    _assert_closed_code("snapshot_limit_exceeded", lambda: _capture_signals(store, principal, owner))
    assert store.available and not connection.in_transaction
    store.close()


def test_inbox_signal_snapshot_rejects_nested_writer_before_reader_clock(tmp_path, authority, monkeypatch):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner)
    calls = []
    def clock():
        calls.append(True)
        _assert_closed_code("storage_failed", lambda: _create(
            store, principal, owner=owner, knowledge_id="nested", key="nested-signal-create-001",
        ))
        return NOW
    monkeypatch.setattr(store, "_clock", clock)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    assert calls == [True]
    store.close()


@pytest.mark.parametrize("target", ["event", "receipt"])
def test_inbox_signal_snapshot_rejects_recomputed_digest_semantic_drift(tmp_path, authority, target):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    if target == "event":
        connection.execute("DROP TRIGGER knowledge_events_no_update")
        row = connection.execute("SELECT event_json FROM knowledge_events WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
        body = knowledge_module._strict_json(row[0])
        body["after_head"]["current_payload_sha256"] = SHA_B
        text = knowledge_module._json(body)
        connection.execute("UPDATE knowledge_events SET event_json=?,event_digest=? WHERE receipt_id=?",
                           (text, knowledge_module._canonical_digest(body), receipt.receipt_id))
        connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                           "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    else:
        connection.execute("DROP TRIGGER knowledge_receipts_no_update")
        row = connection.execute("SELECT receipt_json FROM knowledge_receipts WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
        body = knowledge_module._strict_json(row[0])
        body["after_head"]["current_payload_sha256"] = SHA_B
        body["output_digest"] = knowledge_module._canonical_digest(
            {"after_head": body["after_head"], "new_revision_id": None},
        )
        text = knowledge_module._json(body)
        connection.execute("UPDATE knowledge_receipts SET receipt_json=?,receipt_digest=? WHERE receipt_id=?",
                           (text, knowledge_module._canonical_digest(body), receipt.receipt_id))
        connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                           "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined and not connection.in_transaction
    store.close()


def test_inbox_signal_snapshot_owned_storage_failure_rolls_back_and_reuses(tmp_path, authority, monkeypatch):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner)
    original = knowledge_module._capture_inbox_signals
    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("private failure")
    monkeypatch.setattr(knowledge_module, "_capture_inbox_signals", fail)
    _assert_closed_code("storage_failed", lambda: _capture_signals(store, principal, owner))
    assert store.available and not store._connection.in_transaction
    monkeypatch.setattr(knowledge_module, "_capture_inbox_signals", original)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    store.close()


def test_inbox_signal_snapshot_join_aggregate_limit_precedes_value_decode(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    for index in range(65):
        _purge_pending_signal(store, principal, owner, knowledge_id=f"aggregate-{index:03d}", suffix=f"{index + 100:03d}")
    assert len(_capture_signals(store, principal, owner, limit=100).signals) == 65
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_events_no_update")
    connection.execute("DROP TRIGGER knowledge_receipts_no_update")
    for event in connection.execute("SELECT receipt_id,event_json FROM knowledge_events WHERE command_kind='request_purge'").fetchall():
        connection.execute("UPDATE knowledge_events SET event_json=? WHERE receipt_id=?",
                           (event[1] + " " * (8192 - len(event[1])), event[0]))
        receipt_row = connection.execute("SELECT receipt_json FROM knowledge_receipts WHERE receipt_id=?", (event[0],)).fetchone()
        connection.execute("UPDATE knowledge_receipts SET receipt_json=? WHERE receipt_id=?",
                           (receipt_row[0] + " " * (8192 - len(receipt_row[0])), event[0]))
    connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    statements = []
    connection.set_trace_callback(statements.append)
    _assert_closed_code("snapshot_limit_exceeded", lambda: _capture_signals(store, principal, owner, limit=100))
    signal_selects = [sql for sql in statements if "left join knowledge_events" in sql.lower()]
    assert len(signal_selects) == 1
    assert store.available and not connection.in_transaction
    store.close()


def test_inbox_signal_snapshot_cleanup_failure_quarantines_with_detached_error(tmp_path, authority, monkeypatch):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner)
    connection = store._connection
    class RollbackFailure:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def execute(self, sql, *args):
            if sql == "ROLLBACK":
                raise sqlite3.OperationalError("private rollback failure")
            return self.wrapped.execute(sql, *args)
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
    monkeypatch.setattr(store, "_connection", RollbackFailure(connection))
    _assert_closed_code("storage_failed", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined
    store.close()


def test_inbox_signal_snapshot_independent_digest_vector(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner)
    snapshot = _capture_signals(store, principal, owner)
    owner_encoded = json.dumps({"schema_id": KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA,
                                "kind": "owner_binding", "value": owner}, ensure_ascii=True,
                               allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert snapshot.owner_binding_digest == "sha256:" + hashlib.sha256(
        len(owner_encoded).to_bytes(8, "big") + owner_encoded,
    ).hexdigest()
    serialized = {
        "schema_id": snapshot.schema_id,
        "owner_binding_digest": snapshot.owner_binding_digest,
        "captured_head_boundary": snapshot.captured_head_boundary,
        "signals": [asdict(signal) for signal in snapshot.signals],
        "clipped": snapshot.clipped,
    }
    encoded = json.dumps({"schema_id": KNOWLEDGE_INBOX_SIGNAL_SNAPSHOT_SCHEMA,
                          "kind": "snapshot", "value": serialized}, ensure_ascii=True,
                         allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert snapshot.snapshot_digest == "sha256:" + hashlib.sha256(len(encoded).to_bytes(8, "big") + encoded).hexdigest()
    store.close()


def test_inbox_signal_snapshot_closed_signal_fields_and_denials_precede_sql(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    statements = []
    store._connection.set_trace_callback(statements.append)
    _assert_closed_code("unauthenticated", lambda: _capture_signals(store, object(), owner))
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, principal, "owner_abcdefabcdefabcdefabcdefabcdefab"))
    assert statements == []
    snapshot = _capture_signals(store, principal, owner)
    signal = snapshot.signals[0]
    assert tuple(asdict(signal)) == (
        "signal_handle", "signal_type", "knowledge_kind", "state", "generation", "revision",
        "revision_ref_digest", "head_metadata_digest", "revision_metadata_digest", "event_digest",
        "receipt_handle", "occurred_at", "binding_digest",
    )
    assert all(raw not in json.dumps(asdict(snapshot), sort_keys=True) for raw in (owner, "note-1", receipt.receipt_id))
    assert type(snapshot.signals) is tuple and type(signal.generation) is int and type(signal.revision) is int
    store.close()


@pytest.mark.parametrize("position", [0, 1], ids=["first", "sentinel"])
def test_inbox_signal_snapshot_never_skips_corrupt_pending_prefix(tmp_path, authority, position):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    first = _purge_pending_signal(store, principal, owner, knowledge_id="a", suffix="401")
    second = _purge_pending_signal(store, principal, owner, knowledge_id="b", suffix="402")
    assert len(_capture_signals(store, principal, owner, limit=1).signals) == 1
    target = (first, second)[position]
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_events_no_delete")
    connection.execute("DELETE FROM knowledge_events WHERE receipt_id=?", (target.receipt_id,))
    connection.execute("CREATE TRIGGER knowledge_events_no_delete BEFORE DELETE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner, limit=1))
    assert store.quarantined
    store.close()


@pytest.mark.parametrize("column,value", [("owner_id", "owner_abcdefabcdefabcdefabcdefabcdefab"), ("knowledge_id", "misjoined")])
def test_inbox_signal_snapshot_rejects_receipt_scope_column_drift(tmp_path, authority, column, value):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_receipts_no_update")
    connection.execute(f"UPDATE knowledge_receipts SET {column}=? WHERE receipt_id=?", (value, receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined
    store.close()


@pytest.mark.parametrize("fault", ["unknown", "missing", "duplicate", "occurred", "before_purge", "full_after"])
def test_inbox_signal_snapshot_rejects_closed_event_semantic_matrix(tmp_path, authority, fault):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_events_no_update")
    row = connection.execute("SELECT event_json FROM knowledge_events WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    body = knowledge_module._strict_json(row[0])
    if fault == "unknown":
        body["unknown"] = "x"
    elif fault == "missing":
        del body["receipt_id"]
    elif fault == "occurred":
        body["occurred_at"] = "2026-08-29T12:01:01.000000Z"
    elif fault == "before_purge":
        body["before_head"]["purge_requested_at"] = "2026-08-29T12:00:01.000000Z"
    elif fault == "full_after":
        body["after_head"]["current_payload_sha256"] = SHA_B
    if fault == "duplicate":
        text = knowledge_module._json(body)[:-1] + ',"event_id":"duplicate"}'
        digest_body = knowledge_module._strict_json(row[0])
    else:
        text = knowledge_module._json(body)
        digest_body = body
    if fault == "full_after":
        connection.execute("UPDATE knowledge_events SET after_head_json=?,event_json=?,event_digest=? WHERE receipt_id=?",
                           (knowledge_module._json(body["after_head"]), text,
                            knowledge_module._canonical_digest(digest_body), receipt.receipt_id))
    else:
        connection.execute("UPDATE knowledge_events SET event_json=?,event_digest=? WHERE receipt_id=?",
                           (text, knowledge_module._canonical_digest(digest_body), receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined
    store.close()


@pytest.mark.parametrize("fault", ["producer", "occurred", "output", "unknown"])
def test_inbox_signal_snapshot_rejects_closed_receipt_semantic_matrix(tmp_path, authority, fault):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_receipts_no_update")
    row = connection.execute("SELECT receipt_json FROM knowledge_receipts WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    body = knowledge_module._strict_json(row[0])
    if fault == "producer":
        body["producer"] = "other"
    elif fault == "occurred":
        body["occurred_at"] = "2026-08-29T12:01:01.000000Z"
    elif fault == "output":
        body["output_digest"] = SHA_B
    else:
        body["unknown"] = "x"
    text = knowledge_module._json(body)
    connection.execute("UPDATE knowledge_receipts SET receipt_json=?,receipt_digest=? WHERE receipt_id=?",
                       (text, knowledge_module._canonical_digest(body), receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined
    store.close()


@pytest.mark.parametrize("size,code,value_selects", [(8192, "store_corrupt", 1), (8193, "snapshot_limit_exceeded", 0)])
def test_inbox_signal_snapshot_exact_json_descriptor_boundary(tmp_path, authority, size, code, value_selects):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    row = connection.execute("SELECT event_json FROM knowledge_events WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    padded = row[0] + " " * (size - len(row[0]))
    connection.execute("DROP TRIGGER knowledge_events_no_update")
    connection.execute("UPDATE knowledge_events SET event_json=? WHERE receipt_id=?", (padded, receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    statements = []
    connection.set_trace_callback(statements.append)
    _assert_closed_code(code, lambda: _capture_signals(store, principal, owner))
    joined = [sql for sql in statements if "left join knowledge_events" in sql.lower()]
    assert len(joined) == 1 + value_selects
    assert store.quarantined is (code == "store_corrupt")
    store.close()


def test_inbox_signal_snapshot_forged_principal_and_malformed_sid_precede_sql(tmp_path, authority):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    forged = object.__new__(knowledge_module.TrustedPrincipal)
    for name in knowledge_module.TrustedPrincipal.__slots__:
        object.__setattr__(forged, name, getattr(principal, name) if name != "_attestation" else "sha256:" + "0" * 64)
    statements = []
    store._connection.set_trace_callback(statements.append)
    _assert_closed_code("unauthenticated", lambda: _capture_signals(store, forged, owner))
    alias = issuer.issue(subject_id="alias-subject", owner_id=owner, auth_context_digest=SHA_A,
                         ttl_seconds=3600, nonce="alias-subject-nonce")
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, alias, owner))
    bad_owner = issuer.issue(subject_id="owner_bad", owner_id="owner_bad", auth_context_digest=SHA_A,
                             ttl_seconds=3600, nonce="malformed-owner-nonce")
    _assert_closed_code("owner_scope_denied", lambda: _capture_signals(store, bad_owner, "owner_bad"))
    assert statements == []
    store.close()


@pytest.mark.parametrize("fault", ["generation_type", "idempotency_invalid", "idempotency_column_body_divergence"])
def test_inbox_signal_snapshot_rejects_event_scalar_and_key_semantics(tmp_path, authority, fault):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    connection.execute("DROP TRIGGER knowledge_events_no_update")
    row = connection.execute("SELECT event_json FROM knowledge_events WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    body = knowledge_module._strict_json(row[0])
    if fault == "generation_type":
        body["after_head"]["generation"] = "bad"
        connection.execute("UPDATE knowledge_events SET after_head_json=?,event_json=?,event_digest=? WHERE receipt_id=?",
                           (knowledge_module._json(body["after_head"]), knowledge_module._json(body),
                            knowledge_module._canonical_digest(body), receipt.receipt_id))
    elif fault == "idempotency_invalid":
        body["idempotency_key"] = "invalid"
        connection.execute("UPDATE knowledge_events SET idempotency_key=?,event_json=?,event_digest=? WHERE receipt_id=?",
                           ("invalid", knowledge_module._json(body), knowledge_module._canonical_digest(body), receipt.receipt_id))
    else:
        connection.execute("UPDATE knowledge_events SET idempotency_key=? WHERE receipt_id=?",
                           ("different-valid-key", receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    _assert_closed_code("store_corrupt", lambda: _capture_signals(store, principal, owner))
    assert store.quarantined
    store.close()


@pytest.mark.parametrize("size,code,value_selects", [(8192, "store_corrupt", 1), (8193, "snapshot_limit_exceeded", 0)])
def test_inbox_signal_snapshot_receipt_json_descriptor_boundary(tmp_path, authority, size, code, value_selects):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    receipt = _purge_pending_signal(store, principal, owner)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    connection = store._connection
    row = connection.execute("SELECT receipt_json FROM knowledge_receipts WHERE receipt_id=?", (receipt.receipt_id,)).fetchone()
    connection.execute("DROP TRIGGER knowledge_receipts_no_update")
    connection.execute("UPDATE knowledge_receipts SET receipt_json=? WHERE receipt_id=?",
                       (row[0] + " " * (size - len(row[0])), receipt.receipt_id))
    connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    statements = []
    connection.set_trace_callback(statements.append)
    _assert_closed_code(code, lambda: _capture_signals(store, principal, owner))
    assert len([sql for sql in statements if "left join knowledge_events" in sql.lower()]) == 1 + value_selects
    store.close()


def test_inbox_signal_snapshot_fixed_nonce_literal_golden_vector(tmp_path, authority):
    # Independently derived from fixed principal, three commands, revision,
    # chained events, index boundary, and D0 domains with stdlib only.
    issuer, _alice, _bob = authority
    owner = "owner_0123456789abcdef0123456789abcdef"
    principal = issuer.issue(subject_id=owner, owner_id=owner, auth_context_digest=SHA_A,
                             ttl_seconds=3600, nonce="inbox-golden-nonce-001")
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner)
    snapshot = _capture_signals(store, principal, owner)
    assert asdict(snapshot) == {
        "schema_id": "odysseus.harbour.knowledge_inbox_signal_snapshot.v1",
        "owner_binding_digest": "sha256:de5122ddff2f1d702228bf42836575d00ec1be69141b81b9abf573527c6dcce5",
        "captured_head_boundary": "sha256:21666fa9402ec92a3cd4aa72177e826c7df35c45d120fd9cf90639bc4ec582d6",
        "signals": ({
            "signal_handle": "sha256:de2cd2bc858421ae716c256416a2672dd4ba19d7a834b4693c434bbd916edc49",
            "signal_type": "purge_requested", "knowledge_kind": "note", "state": "purge_pending",
            "generation": 3, "revision": 1,
            "revision_ref_digest": "sha256:d6a20e178f82b44f05fde5c0f8388952cf155c9b4847e165360d867fb0501354",
            "head_metadata_digest": "sha256:642e2ccb1857263163e67f6f521052d7f60a09b977b85a5a772111f31e682e25",
            "revision_metadata_digest": "sha256:04f981e4e5f2a940478a66ba070a39acb5ddab127b1e7cccbdf24ea172f1d438",
            "event_digest": "sha256:55aafaf695eaf8d5969ab7e39f238a6bcb660dfa08f23fd80b10d4bffaa23f38",
            "receipt_handle": "sha256:60c013e94c6e90abea36eaf1617c153bc7d7d610742023ed9ee9ec2d67eaccab",
            "occurred_at": "2026-08-29T12:00:00.000000Z",
            "binding_digest": "sha256:e7dd56b809101d7c420130bc6f25944a37678d02c515fa3fa7715c2d63ebbb34",
        },),
        "clipped": False,
        "snapshot_digest": "sha256:2d6f3ccec02ef89524f022ea6966c2e9f84693b57ec73909b1a2e450c27ee953",
    }
    store.close()


def test_inbox_signal_snapshot_nested_reader_rejected_before_second_clock(tmp_path, authority, monkeypatch):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    _purge_pending_signal(store, principal, owner)
    calls = []
    def clock():
        calls.append(True)
        _assert_closed_code("storage_failed", lambda: _capture_signals(store, principal, owner))
        return NOW
    monkeypatch.setattr(store, "_clock", clock)
    assert len(_capture_signals(store, principal, owner).signals) == 1
    assert calls == [True]
    store.close()


@pytest.mark.parametrize("extra,code,value_selects", [(0, "store_corrupt", 1), (1, "snapshot_limit_exceeded", 0)])
def test_inbox_signal_snapshot_exact_aggregate_descriptor_boundary(tmp_path, authority, extra, code, value_selects):
    issuer, _alice, _bob = authority
    principal, owner = _signal_principal(issuer)
    store = _store(tmp_path, issuer)
    for index in range(65):
        _purge_pending_signal(store, principal, owner, knowledge_id=f"exact-{index:03d}", suffix=f"{index + 700:03d}")
    assert len(_capture_signals(store, principal, owner, limit=100).signals) == 65
    connection = store._connection
    lengths = " + ".join(
        "COALESCE(length(CAST(" + column + " AS BLOB)),0)" for column in (
            "h.knowledge_id", "h.generation", "e.event_seq", "e.event_id", "e.owner_id", "e.knowledge_id",
            "e.generation", "e.command_kind", "e.before_head_json", "e.after_head_json", "e.new_revision_id",
            "e.command_digest", "e.idempotency_key", "e.receipt_id", "e.prior_event_digest", "e.event_digest",
            "e.event_json", "r.receipt_id", "r.owner_id", "r.knowledge_id", "r.receipt_json", "r.receipt_digest", "r.created_at",
        )
    )
    base = connection.execute(
        "SELECT SUM(" + lengths + ") FROM knowledge_heads h LEFT JOIN knowledge_events e "
        "ON e.owner_id=h.owner_id AND e.knowledge_id=h.knowledge_id AND e.generation=h.generation "
        "LEFT JOIN knowledge_receipts r ON r.receipt_id=e.receipt_id "
        "WHERE h.owner_id=? AND h.state='purge_pending'", (owner,),
    ).fetchone()[0]
    remaining = 1_048_576 + extra - base
    rows = connection.execute("SELECT e.receipt_id,e.event_json,r.receipt_json FROM knowledge_events e "
                              "JOIN knowledge_receipts r ON r.receipt_id=e.receipt_id WHERE e.owner_id=? "
                              "AND e.command_kind='request_purge' ORDER BY e.knowledge_id", (owner,)).fetchall()
    connection.execute("DROP TRIGGER knowledge_events_no_update")
    connection.execute("DROP TRIGGER knowledge_receipts_no_update")
    for receipt_id, event_json, receipt_json in rows:
        event_add = min(max(0, 8192 - len(event_json)), remaining)
        remaining -= event_add
        receipt_add = min(max(0, 8192 - len(receipt_json)), remaining)
        remaining -= receipt_add
        connection.execute("UPDATE knowledge_events SET event_json=? WHERE receipt_id=?", (event_json + " " * event_add, receipt_id))
        connection.execute("UPDATE knowledge_receipts SET receipt_json=? WHERE receipt_id=?", (receipt_json + " " * receipt_add, receipt_id))
    assert remaining == 0
    connection.execute("CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.execute("CREATE TRIGGER knowledge_receipts_no_update BEFORE UPDATE ON knowledge_receipts "
                       "BEGIN SELECT RAISE(ABORT,'immutable'); END")
    connection.commit()
    statements = []
    connection.set_trace_callback(statements.append)
    _assert_closed_code(code, lambda: _capture_signals(store, principal, owner, limit=100))
    assert len([sql for sql in statements if "left join knowledge_events" in sql.lower()]) == 1 + value_selects
    store.close()
