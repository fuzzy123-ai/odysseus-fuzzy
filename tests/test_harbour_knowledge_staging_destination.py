from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import sqlite3
import threading

import pytest

from src.harbour_authority import AuthenticatedPrincipalIssuer, TrustedPrincipal
from src.causal_claim_store import CausalClaimStore
from src.harbour_knowledge_migration import (
    CausalMigrationSource,
    migrate_prepared_knowledge_migration,
    prepare_authenticated_zero_census_knowledge_migration,
)
from src.harbour_knowledge_staging_destination import (
    HarbourKnowledgeStagingDestinationService,
    StagingMigrationCompletionReference,
    StagingDestinationError,
    StagingDestinationLease,
    StagingDestinationReceipt,
    _CONTEXT_SCHEMA,
    _canonical,
)
from src.harbour_knowledge_store import HarbourKnowledgeStore, HarbourKnowledgeStoreError, KnowledgeHead


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
SNAPSHOT = "sha256:" + "a" * 64
POLICY = "sha256:" + "b" * 64
PROJECTION = "sha256:" + "c" * 64
BACKUP = "sha256:" + "d" * 64
OTHER = "sha256:" + "e" * 64
LIFECYCLE = ("tombstone", "restore", "request_purge", "complete_purge")
HEAD_DRIFT = {
    "owner_id": "bob", "knowledge_id": "note-2", "knowledge_kind": "document",
    "current_revision_id": "another-revision", "current_payload_sha256": OTHER,
    "generation": 99, "state": "tombstoned",
    "tombstoned_at": "2026-08-28T01:00:00.000000Z",
    "purge_requested_at": "2026-08-28T02:00:00.000000Z",
}
EVIDENCE_FIELDS = ("policy_receipt", "projection_deletion_receipts", "backup_deletion_receipts")


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


def _sha(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def _context(migration: str, owner: str, snapshot: str = SNAPSHOT) -> str:
    return _sha({
        "migration_id": migration,
        "owner_id": owner,
        "schema_id": "odysseus.harbour.knowledge_migration_principal_context.v1",
        "snapshot_digest": snapshot,
    })


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(issuer_id="staging-test", key_id="key", secret=b"s" * 32, clock=clock)
    service = HarbourKnowledgeStagingDestinationService(tmp_path / "private", trusted_issuers=(issuer,), clock=clock)
    return clock, issuer, service


def _principal(issuer, migration="migration-1", owner="alice", snapshot=SNAPSHOT):
    return issuer.issue(subject_id=owner + "-user", owner_id=owner, auth_context_digest=_context(migration, owner, snapshot), ttl_seconds=3600)


def _reserve(service):
    return service.reserve("migration-1", ("alice", "bob"), SNAPSHOT, 300)


def _create(session, lease, principal, payload=b"one", key="create-note-001", retention=None):
    return session.create_object(
        lease, principal, owner_id="alice", knowledge_id="note-1", knowledge_kind="note",
        payload_bytes=payload, payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
        classification="private", idempotency_key=key, retention=retention,
    )


def _closed(code, callback):
    with pytest.raises(StagingDestinationError) as raised:
        callback()
    assert raised.value.code == code
    assert raised.value.args == (code,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert raised.value.__suppress_context__ is True


def _zero_completion(tmp_path, service, issuer, name, owners=("alice",)):
    sources = []
    connections = []
    try:
        for owner in owners:
            connection = sqlite3.connect(tmp_path / f"{name}-{owner}.sqlite")
            connections.append(connection)
            source = CausalClaimStore(connection, initialize=True)
            sources.append(CausalMigrationSource(owner, source, owner))
        retention = {
            owner: {"retention_class": "user_managed", "deletion_mode": "tombstone_only",
                    "retain_until": None, "grace_until": None, "legal_hold": False}
            for owner in owners
        }
        observed_at = (NOW + timedelta(seconds=int(hashlib.sha256(name.encode()).hexdigest()[:4], 16))).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
        plan = prepare_authenticated_zero_census_knowledge_migration(
            causal_sources=tuple(sources), owner_map={owner: owner for owner in owners},
            retention_by_scope=retention, observed_at=observed_at,
        )
        principals = {
            owner: issuer.issue(subject_id=owner, owner_id=owner, auth_context_digest=_sha({
                "migration_id": plan.migration_id, "owner_id": owner,
                "schema_id": _CONTEXT_SCHEMA, "snapshot_digest": plan.source_manifest_digest,
            })) for owner in owners
        }
        completion = migrate_prepared_knowledge_migration(
            plan, staging=service, principals=principals,
        ).completion_reference
        return completion
    finally:
        for connection in connections:
            connection.close()


def _advance_selected_store(store, issuer, owner, key):
    principal = issuer.issue(
        subject_id=owner, owner_id=owner,
        auth_context_digest=_sha({"purpose": "test-only-store-advance", "key": key}),
    )
    store.create_object(
        principal, owner_id=owner, knowledge_id="rollback-probe-" + key,
        knowledge_kind="note", payload_bytes=b"rollback probe",
        payload_schema_id="harbour.note.v1", payload_media_type="text/plain",
        classification="private", idempotency_key=key,
    )


def _compensate_store_head(store, issuer, owner):
    principal = issuer.issue(
        subject_id=owner, owner_id=owner,
        auth_context_digest=_sha({"purpose": "test-only-compensation"}),
    )
    store.create_object(
        principal, owner_id=owner, knowledge_id="rollback-compensated",
        knowledge_kind="note", payload_bytes=b"temporary", payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain", classification="private", idempotency_key="compensation-create",
    )
    head = store.get_head(principal, owner_id=owner, knowledge_id="rollback-compensated")
    store.tombstone(principal, owner_id=owner, knowledge_id="rollback-compensated",
                    expected_head=head, idempotency_key="compensation-tombstone")
    tombstone = store.get_head(principal, owner_id=owner, knowledge_id="rollback-compensated")
    store.restore(principal, owner_id=owner, knowledge_id="rollback-compensated",
                  expected_head=tombstone, idempotency_key="compensation-restore")


def _all_store_ledgers(service):
    tables = ("knowledge_blobs", "knowledge_revisions", "knowledge_heads", "knowledge_events",
              "knowledge_receipts", "knowledge_idempotency")
    return {
        generation: tuple(
            tuple(tuple(row) for row in store._connection.execute("SELECT * FROM " + table))
            for table in tables
        )
        for generation, store in service._stores.items()
    }


def _staging_ledger(service):
    return tuple(
        (table, tuple(tuple(row) for row in service._connection.execute("SELECT * FROM " + table)))
        for table in (
            "staging_meta", "staging_reservations", "staging_operations",
            "staging_completions", "staging_selections",
        )
    )


def _nested_selected_runtime_guard(service):
    with service.guard_selected_runtime("alice"):
        pass


class _RollbackFailingConnection:
    def __init__(self, connection):
        self.connection = connection

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        if sql == "ROLLBACK":
            raise sqlite3.OperationalError("guard cleanup fault")
        return self.connection.execute(sql, parameters)

    def close(self):
        self.connection.close()


class _BeginFailingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.begin_attempts = 0

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE":
            self.begin_attempts += 1
            raise sqlite3.OperationalError("guard begin fault")
        return self.connection.execute(sql, parameters)


def _rollback_pair(tmp_path, service, issuer, name, first_owners=("alice",), second_owners=("alice",)):
    first = _zero_completion(tmp_path, service, issuer, name + "-a", first_owners)
    first_digest = service.select_generation(first, expected_selection_digest="sha256:" + "0" * 64)
    second = _zero_completion(tmp_path, service, issuer, name + "-b", second_owners)
    second_digest = service.select_generation(second, expected_selection_digest=first_digest)
    return first, first_digest, second, second_digest


def _legacy_rollback_pair(tmp_path, service, issuer, name):
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, name)
    service._connection.execute("DROP TRIGGER staging_selections_no_update")
    service._connection.execute(
        "UPDATE staging_selections SET intent='read_only_query' WHERE sequence=2",
    )
    row = service._connection.execute(
        "SELECT * FROM staging_selections WHERE sequence=2",
    ).fetchone()
    body = {field: row[field] for field in (
        "sequence", "prior_digest", "generation_id", "completion_record_id",
        "completion_attestation", "owner_set_digest", "migration_digest",
        "head_manifest_digest", "store_event_anchor", "intent", "selected_at",
    )}
    selection_b = _sha({"schema": "odysseus.harbour.knowledge_selection.v1", "body": body})
    attestation = hmac.new(service._key(), _canonical(body), hashlib.sha256).hexdigest()
    service._connection.execute(
        "UPDATE staging_selections SET selection_digest=?,attestation=? WHERE sequence=2",
        (selection_b, attestation),
    )
    service._connection.execute(
        "CREATE TRIGGER staging_selections_no_update BEFORE UPDATE ON staging_selections "
        "BEGIN SELECT RAISE(ABORT,'immutable'); END",
    )
    return first, selection_a, second, selection_b


def _store_closed(code, callback):
    with pytest.raises(HarbourKnowledgeStoreError) as raised:
        callback()
    assert raised.value.code == code
    assert raised.value.args == (code,)


def test_open_existing_ledger_does_not_create_missing_root(tmp_path, setup):
    _clock, issuer, _service = setup
    root = tmp_path / "absent"
    _closed("staging_destination_denied", lambda: HarbourKnowledgeStagingDestinationService.open_existing(
        root, trusted_issuers=(issuer,),
    ))
    assert not root.exists()


def test_open_existing_ledger_reuses_initialized_schema_and_closes_stores(tmp_path, setup):
    clock, issuer, service = setup
    root = tmp_path / "private"
    _reserve(service)
    service.close()
    reopened = HarbourKnowledgeStagingDestinationService.open_existing(
        root, trusted_issuers=(issuer,), clock=clock,
    )
    assert reopened.current_selection() is None
    reopened.close()


def test_legacy_read_only_a_to_b_can_conservatively_rollback_to_original_a(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "legacy-rollback", trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _legacy_rollback_pair(
        tmp_path, service, issuer, "legacy-rollback",
    )
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    receipt = service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    )
    assert receipt.outcome == "rolled_back"
    assert receipt.from_selection_digest == selection_b
    assert receipt.target_selection_digest == selection_a
    assert service.current_selection()[2] == receipt.rollback_selection_digest
    service.close()


def test_legacy_read_only_a_to_b_denies_target_store_divergence(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "legacy-rollback-diverged", trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _legacy_rollback_pair(
        tmp_path, service, issuer, "legacy-rollback-diverged",
    )
    row = service._connection.execute(
        "SELECT r.* FROM staging_reservations r JOIN staging_completions c "
        "ON c.generation_id=r.generation_id WHERE c.record_id=?",
        (object.__getattribute__(first, "_record_id"),),
    ).fetchone()
    _advance_selected_store(service._store(row), issuer, "alice", "legacy-target-divergence")
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 2
    service.close()


def test_open_existing_rejects_same_name_noop_immutability_trigger(tmp_path, setup):
    _clock, issuer, service = setup
    _reserve(service)
    service.close()
    ledger = tmp_path / "private" / "staging-reservations.sqlite"
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("DROP TRIGGER staging_selections_no_update")
        connection.execute("CREATE TRIGGER staging_selections_no_update BEFORE UPDATE ON staging_selections WHEN 0 BEGIN SELECT RAISE(ABORT,'immutable'); END")
        connection.commit()
    finally:
        connection.close()
    _closed("staging_destination_denied", lambda: HarbourKnowledgeStagingDestinationService.open_existing(
        tmp_path / "private", trusted_issuers=(issuer,),
    ))


def test_open_existing_rejects_schema_extensions_and_column_shape_tampering(tmp_path, setup):
    _clock, issuer, service = setup
    _reserve(service)
    service.close()
    ledger = tmp_path / "private" / "staging-reservations.sqlite"
    connection = sqlite3.connect(ledger)
    try:
        connection.execute("CREATE VIEW sqliteXunexpected AS SELECT value FROM staging_meta")
        connection.commit()
    finally:
        connection.close()
    _closed("staging_destination_denied", lambda: HarbourKnowledgeStagingDestinationService.open_existing(
        tmp_path / "private", trusted_issuers=(issuer,),
    ))

    # Use a separate initialized ledger for a same-name, same-column but wrong-type table.
    typed_root = tmp_path / "typed"
    typed = HarbourKnowledgeStagingDestinationService(typed_root, trusted_issuers=(issuer,))
    typed.close()
    connection = sqlite3.connect(typed_root / "staging-reservations.sqlite")
    try:
        connection.execute("ALTER TABLE staging_meta RENAME TO staging_meta_old")
        connection.execute("CREATE TABLE staging_meta (name BLOB PRIMARY KEY, value BLOB NOT NULL)")
        connection.execute("INSERT INTO staging_meta SELECT * FROM staging_meta_old")
        connection.execute("DROP TABLE staging_meta_old")
        connection.commit()
    finally:
        connection.close()
    _closed("staging_destination_denied", lambda: HarbourKnowledgeStagingDestinationService.open_existing(
        typed_root, trusted_issuers=(issuer,),
    ))


def test_closed_staging_service_denies_selection_queries_with_bounded_error(setup):
    _clock, _issuer_value, service = setup
    service.close()
    for callback in (service.current_selection, lambda: service.selected_runtime("alice")):
        with pytest.raises(StagingDestinationError) as raised:
            callback()
        assert raised.value.code == "staging_destination_denied"
        assert raised.value.__cause__ is None and raised.value.__context__ is None


_GUARD_SERVICE_REENTRIES = (
    "close", "current_selection", "selected_runtime", "select_generation",
    "rollback_generation", "reserve", "resume_or_reserve", "revoke",
    "complete_migration", "verify_completion", "seal", "_call",
    "guard_selected_runtime",
)
_GUARD_SESSION_REENTRIES = (
    "validate_principal_binding", "create_object", "append_revision", "tombstone",
    "restore", "request_purge", "complete_purge", "get_head", "get_receipt",
    "read_revision", "read_payload", "changes_since", "session.seal",
)


@pytest.mark.parametrize("entry", _GUARD_SERVICE_REENTRIES + _GUARD_SESSION_REENTRIES)
def test_guard_selected_runtime_rejects_every_stateful_reentry_before_effect(
    tmp_path, setup, entry,
):
    _clock, issuer, service = setup
    completion = _zero_completion(tmp_path, service, issuer, "guard-reentry-" + entry)
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    session, lease = service.reserve("guard-probe", ("alice",), SNAPSHOT, 300)
    principal = _principal(issuer, migration="guard-probe")
    destination = object.__getattribute__(session, "_destination_id")
    if entry == "close":
        callback = service.close
    elif entry == "current_selection":
        callback = service.current_selection
    elif entry == "selected_runtime":
        callback = lambda: service.selected_runtime(object())
    elif entry == "select_generation":
        callback = lambda: service.select_generation(None, expected_selection_digest="bad")
    elif entry == "rollback_generation":
        callback = lambda: service.rollback_generation(
            None, expected_selection_digest="bad", target_selection_digest="bad", principals={},
        )
    elif entry == "reserve":
        callback = lambda: service.reserve("bad owner", (), "bad", 0)
    elif entry == "resume_or_reserve":
        callback = lambda: service.resume_or_reserve("bad owner", (), "bad", 0)
    elif entry == "revoke":
        callback = lambda: service.revoke(None)
    elif entry == "complete_migration":
        callback = lambda: service.complete_migration(None, principals={}, expected_operations=[])
    elif entry == "verify_completion":
        callback = lambda: service.verify_completion(None)
    elif entry == "seal":
        callback = lambda: service.seal(None, completion_digest="bad")
    elif entry == "_call":
        callback = lambda: service._call(destination, None, "bad", None, object(), {})
    elif entry == "guard_selected_runtime":
        callback = lambda: _nested_selected_runtime_guard(service)
    elif entry == "validate_principal_binding":
        callback = lambda: session.validate_principal_binding(lease, principal, owner_id="alice")
    elif entry in {"create_object", "append_revision", "tombstone", "restore", "request_purge", "complete_purge"}:
        callback = lambda entry=entry: getattr(session, entry)(lease, principal, owner_id="alice")
    elif entry == "get_head":
        callback = lambda: session.get_head(lease, principal, owner_id="alice", knowledge_id="note")
    elif entry == "get_receipt":
        callback = lambda: session.get_receipt(lease, principal, owner_id="alice", receipt_id="receipt")
    elif entry in {"read_revision", "read_payload"}:
        callback = lambda entry=entry: getattr(session, entry)(
            lease, principal, owner_id="alice", revision_id="revision",
        )
    elif entry == "changes_since":
        callback = lambda: session.changes_since(
            lease, principal, owner_id="alice", after_event_seq=0,
        )
    elif entry == "session.seal":
        callback = lambda: session.seal(lease, completion_digest=OTHER)
    else:
        raise AssertionError("unhandled reentry case")

    staging_before = _staging_ledger(service)
    stores_before = _all_store_ledgers(service)
    with service.guard_selected_runtime("alice") as retained:
        assert retained is not None
        assert service._connection.in_transaction
        paths_retained = tuple(sorted(
            str(path.relative_to(service._root)) for path in service._root.rglob("*")
        ))
        assert service.rollback_authority_context_digest(
            "sha256:" + "1" * 64, "sha256:" + "2" * 64,
        ).startswith("sha256:")
        _closed("staging_destination_denied", callback)
        assert service._connection.in_transaction
        assert _staging_ledger(service) == staging_before
        assert _all_store_ledgers(service) == stores_before
        assert tuple(sorted(
            str(path.relative_to(service._root)) for path in service._root.rglob("*")
        )) == paths_retained
    assert not service._connection.in_transaction
    assert _staging_ledger(service) == staging_before
    assert _all_store_ledgers(service) == stores_before
    service.close()


@pytest.mark.parametrize("selection", ["missing", "foreign"])
def test_guard_selected_runtime_missing_or_foreign_owner_performs_zero_store_or_anchor_io(
    tmp_path, setup, monkeypatch, selection,
):
    _clock, issuer, service = setup
    if selection == "foreign":
        completion = _zero_completion(tmp_path, service, issuer, "guard-foreign")
        service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Knowledge Store or anchor I/O must not run")

    monkeypatch.setattr(service, "_store", unexpected)
    monkeypatch.setattr(service, "_selection_anchor_present", unexpected)
    with service.guard_selected_runtime("mallory") as retained:
        assert retained is None
        assert service._connection.in_transaction
    assert not service._connection.in_transaction
    service.close()


def test_guard_selected_runtime_peer_selection_waits_and_commit_targets_original_generation(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="guard-peer", key_id="key", secret=b"g" * 32, clock=clock,
    )
    root = tmp_path / "guard-peer"
    service = HarbourKnowledgeStagingDestinationService(root, trusted_issuers=(issuer,), clock=clock)
    first = _zero_completion(tmp_path, service, issuer, "guard-peer-a")
    selection_a = service.select_generation(first, expected_selection_digest="sha256:" + "0" * 64)
    second = _zero_completion(tmp_path, service, issuer, "guard-peer-b")
    peer = HarbourKnowledgeStagingDestinationService.open_existing(
        root, trusted_issuers=(issuer,), clock=clock,
    )
    attempted = threading.Event()
    finished = threading.Event()
    failures = []

    def switch_selection():
        attempted.set()
        try:
            peer.select_generation(second, expected_selection_digest=selection_a)
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=switch_selection)
    with service.guard_selected_runtime("alice") as retained:
        assert retained is not None
        original_generation, original_store = retained[2], retained[4]
        worker.start()
        assert attempted.wait(2)
        assert not finished.wait(0.05)
        _advance_selected_store(original_store, issuer, "alice", "guard-retained-commit")
        assert not finished.is_set()
    worker.join(5)
    assert not worker.is_alive() and finished.is_set() and not failures
    selected_b = service.selected_runtime("alice")
    assert selected_b is not None and selected_b[2] != original_generation
    assert original_store._connection.execute("SELECT COUNT(*) FROM knowledge_heads").fetchone()[0] == 1
    assert selected_b[4]._connection.execute("SELECT COUNT(*) FROM knowledge_heads").fetchone()[0] == 0
    peer.close()
    service.close()


def test_guard_selected_runtime_blocks_service_and_exact_store_close_until_release(tmp_path, setup):
    _clock, issuer, service = setup
    completion = _zero_completion(tmp_path, service, issuer, "guard-concurrent-close")
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    service_started = threading.Event()
    store_started = threading.Event()
    service_finished = threading.Event()
    store_finished = threading.Event()
    failures = []

    def close_service():
        service_started.set()
        try:
            service.close()
        except BaseException as exc:
            failures.append(exc)
        finally:
            service_finished.set()

    with service.guard_selected_runtime("alice") as retained:
        assert retained is not None
        store = retained[4]

        def close_store():
            store_started.set()
            try:
                store.close()
            except BaseException as exc:
                failures.append(exc)
            finally:
                store_finished.set()

        service_thread = threading.Thread(target=close_service)
        store_thread = threading.Thread(target=close_store)
        service_thread.start()
        store_thread.start()
        assert service_started.wait(2) and store_started.wait(2)
        assert not service_finished.wait(0.05)
        assert not store_finished.wait(0.05)
    service_thread.join(5)
    store_thread.join(5)
    assert not service_thread.is_alive() and not store_thread.is_alive()
    assert service_finished.is_set() and store_finished.is_set() and not failures
    assert service._connection is None and not store.available


@pytest.mark.parametrize("caller_throws", [False, True])
def test_guard_selected_runtime_postcommit_cleanup_failure_preserves_result_or_exact_exception(
    tmp_path, caller_throws,
):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="guard-cleanup", key_id="key", secret=b"c" * 32, clock=clock,
    )
    root = tmp_path / ("guard-cleanup-exception" if caller_throws else "guard-cleanup-result")
    service = HarbourKnowledgeStagingDestinationService(root, trusted_issuers=(issuer,), clock=clock)
    completion = _zero_completion(tmp_path, service, issuer, "guard-cleanup")
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    peer = HarbourKnowledgeStagingDestinationService.open_existing(
        root, trusted_issuers=(issuer,), clock=clock,
    )
    raw_connection = service._connection
    service._connection = _RollbackFailingConnection(raw_connection)
    expected_result = object()
    expected_exception = RuntimeError("caller exception")

    def invoke():
        with service.guard_selected_runtime("alice") as retained:
            assert retained is not None
            _advance_selected_store(retained[4], issuer, "alice", "guard-cleanup-write")
            if caller_throws:
                raise expected_exception
            return expected_result

    if caller_throws:
        with pytest.raises(RuntimeError) as raised:
            invoke()
        assert raised.value is expected_exception
    else:
        assert invoke() is expected_result
    assert service._connection is None
    _closed("staging_destination_denied", service.current_selection)
    peer_runtime = peer.selected_runtime("alice")
    assert peer_runtime is not None
    assert peer_runtime[4]._connection.execute("SELECT COUNT(*) FROM knowledge_heads").fetchone()[0] == 1
    peer.close()


@pytest.mark.parametrize("fault", ["store", "completion", "anchor", "path"])
def test_guard_selected_runtime_preyield_failure_is_bounded_and_leaves_no_transaction(
    tmp_path, setup, monkeypatch, fault,
):
    _clock, issuer, service = setup
    completion = _zero_completion(tmp_path, service, issuer, "guard-preyield-" + fault)
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    selected = service.selected_runtime("alice")
    assert selected is not None
    store = selected[4]
    if fault == "store":
        monkeypatch.setattr(service, "_store", lambda _row: (_ for _ in ()).throw(RuntimeError("store")))
    elif fault == "completion":
        monkeypatch.setattr(
            service, "_verify_completion_snapshot",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("completion")),
        )
    elif fault == "anchor":
        monkeypatch.setattr(service, "_selection_anchor_present", lambda *_args, **_kwargs: False)
    else:
        monkeypatch.setattr(store, "database_path", tmp_path / "wrong.sqlite")

    _closed("staging_destination_denied", lambda: _nested_selected_runtime_guard(service))
    assert not service._connection.in_transaction
    if store.available:
        assert not store._connection.in_transaction
    service.close()


def test_guard_selected_runtime_preserves_preexisting_store_transaction_and_pending_work(
    tmp_path, setup,
):
    _clock, issuer, service = setup
    completion = _zero_completion(tmp_path, service, issuer, "guard-existing-store-transaction")
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    selected = service.selected_runtime("alice")
    assert selected is not None
    store = selected[4]
    connection = store._connection
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO knowledge_meta VALUES('guard-borrowed','caller-pending')",
    )

    _closed("staging_destination_denied", lambda: _nested_selected_runtime_guard(service))
    assert not service._connection.in_transaction
    assert connection.in_transaction
    assert connection.execute(
        "SELECT value FROM knowledge_meta WHERE key='guard-borrowed'",
    ).fetchone()[0] == "caller-pending"
    connection.execute("ROLLBACK")
    assert connection.execute(
        "SELECT value FROM knowledge_meta WHERE key='guard-borrowed'",
    ).fetchone() is None
    service.close()


def test_guard_selected_runtime_store_begin_failure_leaves_no_guard_owned_transaction(
    tmp_path, setup, monkeypatch,
):
    _clock, issuer, service = setup
    completion = _zero_completion(tmp_path, service, issuer, "guard-store-begin-failure")
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    selected = service.selected_runtime("alice")
    assert selected is not None
    store = selected[4]
    connection = store._connection
    failing = _BeginFailingConnection(connection)
    monkeypatch.setattr(store, "_conn", lambda: failing)

    _closed("staging_destination_denied", lambda: _nested_selected_runtime_guard(service))
    assert failing.begin_attempts == 1
    assert not service._connection.in_transaction
    assert not connection.in_transaction
    assert store.available and not store._quarantined
    service.close()


def test_guard_selected_runtime_leaves_ordinary_selected_runtime_result_unchanged(tmp_path, setup):
    _clock, issuer, service = setup
    completion = _zero_completion(tmp_path, service, issuer, "guard-ordinary-selected")
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    before = service.selected_runtime("alice")
    assert before is not None
    assert type(before[0]) is StagingMigrationCompletionReference
    before_verified = service.verify_completion(before[0])
    with service.guard_selected_runtime("alice") as retained:
        assert retained is not None
        assert type(retained[0]) is StagingMigrationCompletionReference
        retained_reference = retained[0]
        assert retained[1:4] == before[1:4]
        assert retained[4] is before[4]
    retained_verified = service.verify_completion(retained_reference)
    for field in (
        "migration_digest", "source_manifest_digest", "owner_set_digest", "write_count",
        "receipt_set_digest", "head_manifest_digest",
    ):
        assert getattr(retained_verified, field) == getattr(before_verified, field)
    after = service.selected_runtime("alice")
    assert after is not None
    assert type(after[0]) is StagingMigrationCompletionReference
    assert after[1:4] == before[1:4]
    assert after[4] is before[4]
    after_verified = service.verify_completion(after[0])
    for field in (
        "migration_digest", "source_manifest_digest", "owner_set_digest", "write_count",
        "receipt_set_digest", "head_manifest_digest",
    ):
        assert getattr(after_verified, field) == getattr(before_verified, field)
    service.close()


def test_fresh_private_generation_opaque_lease_and_guarded_write(setup) -> None:
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    receipt = _create(session, lease, _principal(issuer))
    assert type(receipt) is StagingDestinationReceipt
    assert receipt.outcome == "written"
    assert receipt.write_count == 1
    assert "one" not in repr(receipt)
    assert "secret" not in repr(lease)
    with pytest.raises(AttributeError):
        _ = lease._capability
    with pytest.raises(AttributeError):
        _ = session._service
    head = session.get_head(lease, _principal(issuer), owner_id="alice", knowledge_id="note-1")
    assert session.read_payload(lease, _principal(issuer), owner_id="alice", revision_id=head.current_revision_id) == b"one"


def test_per_call_snapshot_context_and_owner_set_are_enforced(setup) -> None:
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    wrong_context = _principal(issuer, snapshot="sha256:" + "b" * 64)
    _store_closed("auth_context_denied", lambda: _create(session, lease, wrong_context))
    _closed("staging_destination_denied", lambda: session.get_head(lease, _principal(issuer, owner="mallory"), owner_id="mallory", knowledge_id="note-1"))
    good = _principal(issuer)
    receipt = _create(session, lease, good)
    head = session.get_head(lease, good, owner_id="alice", knowledge_id="note-1")
    assert head.generation == 1
    assert receipt.write_count == 1


def test_exact_replay_is_detached_and_changed_input_is_denied(setup) -> None:
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    principal = _principal(issuer)
    first = _create(session, lease, principal)
    assert first == _create(session, lease, principal)
    _closed("staging_destination_denied", lambda: _create(session, lease, principal, payload=b"changed"))


def test_expiry_revoke_and_seal_are_terminal(setup) -> None:
    clock, issuer, service = setup
    session, lease = _reserve(service)
    service.revoke(lease)
    _closed("staging_destination_denied", lambda: _create(session, lease, _principal(issuer)))
    session2, lease2 = _reserve(service)
    sealed = session2.seal(lease2, completion_digest="sha256:" + "c" * 64)
    assert sealed.outcome == "sealed"
    _closed("staging_destination_denied", lambda: _create(session2, lease2, _principal(issuer)))
    session3, lease3 = service.reserve("migration-2", ("alice",), SNAPSHOT, 1)
    clock.value += timedelta(seconds=2)
    _closed("staging_destination_expired", lambda: _create(session3, lease3, _principal(issuer, migration="migration-2")))


def test_resume_rotates_capability_on_same_generation_and_invalidates_old_lease(setup) -> None:
    clock, issuer, service = setup
    session, lease = _reserve(service)
    principal = _principal(issuer)
    _create(session, lease, principal)
    old_generation = object.__getattribute__(lease, "_generation_id")
    clock.value += timedelta(seconds=301)
    resumed_session, resumed_lease = service.resume_or_reserve(
        "migration-1", ("alice", "bob"), SNAPSHOT, 300,
    )
    assert object.__getattribute__(resumed_lease, "_generation_id") == old_generation
    assert object.__getattribute__(resumed_lease, "_epoch") == 2
    _closed("staging_destination_denied", lambda: _create(session, lease, principal))
    assert resumed_session.read_payload(
        resumed_lease, principal, owner_id="alice",
        revision_id=_head(resumed_session, resumed_lease, principal).current_revision_id,
    ) == b"one"


def test_legacy_unsealed_integrity_is_validated_then_upgraded_and_legacy_seal_is_not_completion(setup) -> None:
    _clock, _issuer, service = setup
    session, lease = _reserve(service)
    row = service._connection.execute("SELECT * FROM staging_reservations").fetchone()
    owners = tuple(json.loads(row["owner_set_json"]))
    legacy = _sha({
        "generation": row["generation_id"], "migration": row["migration_id"],
        "owners": owners, "owner_digest": row["owner_set_digest"],
        "snapshot": row["snapshot_manifest_digest"], "epoch": row["epoch"],
        "state": row["state"], "expiry": row["expires_at"], "writes": row["write_count"],
    })
    service._connection.execute(
        "UPDATE staging_reservations SET integrity_digest=? WHERE destination_id=?",
        (legacy, row["destination_id"]),
    )
    resumed, rotated = service.resume_or_reserve("migration-1", ("alice", "bob"), SNAPSHOT, 300)
    assert object.__getattribute__(rotated, "_generation_id") == object.__getattribute__(lease, "_generation_id")
    assert service._connection.execute(
        "SELECT integrity_digest FROM staging_reservations WHERE destination_id=?",
        (row["destination_id"],),
    ).fetchone()[0] != legacy
    resumed.seal(rotated, completion_digest=OTHER)
    _closed("staging_destination_denied", lambda: service.resume_or_reserve("migration-1", ("alice", "bob"), SNAPSHOT, 300))


def test_closed_owner_set_rejects_duplicates_aliases_and_lease_forgery(setup) -> None:
    _clock, _issuer, service = setup
    for owners in (("alice", "alice"), ("alice", "ALICE"), (), ("bad owner",)):
        _closed("staging_destination_denied", lambda owners=owners: service.reserve("migration-1", owners, SNAPSHOT, 30))
    _session, lease = _reserve(service)
    forged = object.__new__(StagingDestinationLease)
    _closed("staging_destination_denied", lambda: service.revoke(forged))
    service.revoke(lease)


def test_restart_reconstructs_private_store_and_preserves_binding(tmp_path) -> None:
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(issuer_id="staging-test", key_id="key", secret=b"s" * 32, clock=clock)
    root = tmp_path / "private"
    original = HarbourKnowledgeStagingDestinationService(root, trusted_issuers=(issuer,), clock=clock)
    session, lease = _reserve(original)
    _create(session, lease, _principal(issuer))
    restarted = HarbourKnowledgeStagingDestinationService(root, trusted_issuers=(issuer,), clock=clock)
    # Existing capability stays valid only when the protected ledger, generation,
    # closed owner set and snapshot binding still agree.
    resumed = type(session)(restarted, object.__getattribute__(session, "_destination_id"))
    head = resumed.get_head(lease, _principal(issuer), owner_id="alice", knowledge_id="note-1")
    assert head.generation == 1


def test_no_raw_store_or_activation_surface(setup) -> None:
    _clock, _issuer, service = setup
    for name in ("private_root", "store", "connection", "trusted_issuers"):
        with pytest.raises(AttributeError):
            getattr(service, name)
    assert not hasattr(service, "activate")
    assert not hasattr(service, "select_path")
    forged = StagingMigrationCompletionReference("a" * 64, "b" * 64)
    _closed("staging_destination_denied", lambda: service.select_generation(
        forged, expected_selection_digest="sha256:" + "0" * 64,
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 0


@pytest.mark.parametrize("tamper", ["anchor", "completion"])
def test_same_generation_selection_replay_revalidates_under_store_transaction(tmp_path, tamper):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="selection-replay", key_id="key-1", secret=b"s" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / ("selection-replay-" + tamper), trusted_issuers=(issuer,), clock=clock,
    )
    completion = _zero_completion(tmp_path, service, issuer, "selection-replay-" + tamper)
    selection = service.select_generation(
        completion, expected_selection_digest="sha256:" + "0" * 64,
    )
    selected = service.selected_runtime("alice")
    assert selected is not None
    _advance_selected_store(selected[4], issuer, "alice", "legitimate-edit-before-replay")
    assert service.select_generation(
        completion, expected_selection_digest=selection,
    ) == selection
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 1

    if tamper == "anchor":
        service._connection.execute("DROP TRIGGER staging_selections_no_update")
        row = service._connection.execute(
            "SELECT * FROM staging_selections WHERE sequence=1",
        ).fetchone()
        body = {field: row[field] for field in (
            "sequence", "prior_digest", "generation_id", "completion_record_id",
            "completion_attestation", "owner_set_digest", "migration_digest",
            "head_manifest_digest", "store_event_anchor", "intent", "selected_at",
        )}
        body["store_event_anchor"] = "sha256:" + "f" * 64
        digest = _sha({"schema": "odysseus.harbour.knowledge_selection.v1", "body": body})
        attestation = hmac.new(service._key(), _canonical(body), hashlib.sha256).hexdigest()
        service._connection.execute(
            "UPDATE staging_selections SET store_event_anchor=?,selection_digest=?,attestation=? WHERE sequence=1",
            (body["store_event_anchor"], digest, attestation),
        )
        service._connection.execute(
            "CREATE TRIGGER staging_selections_no_update BEFORE UPDATE ON staging_selections "
            "BEGIN SELECT RAISE(ABORT,'immutable'); END",
        )
        expected = digest
    else:
        selected[4]._connection.execute("DROP TRIGGER knowledge_events_no_update")
        selected[4]._connection.execute(
            "UPDATE knowledge_events SET event_digest=? WHERE event_seq=1",
            ("sha256:" + "f" * 64,),
        )
        selected[4]._connection.execute(
            "CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
            "BEGIN SELECT RAISE(ABORT,'immutable'); END",
        )
        expected = selection

    _closed("staging_destination_denied", lambda: service.select_generation(
        completion, expected_selection_digest=expected,
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 1
    service.close()


def test_rollback_returns_stable_receipt_replays_and_survives_reopen(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "rollback", trusted_issuers=(issuer,), clock=clock,
    )
    first = _zero_completion(tmp_path, service, issuer, "rollback-a")
    selection_a = service.select_generation(first, expected_selection_digest="sha256:" + "0" * 64)
    selected_a = service.selected_runtime("alice")
    assert selected_a is not None
    _advance_selected_store(selected_a[4], issuer, "alice", "advance-a-before-departure")

    second = _zero_completion(tmp_path, service, issuer, "rollback-b")
    selection_b = service.select_generation(second, expected_selection_digest=selection_a)
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    stores_before = _all_store_ledgers(service)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        second, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    assert service.current_selection()[2] == selection_b
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 2
    receipt = service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    )
    assert receipt.outcome == "rolled_back"
    assert receipt.from_selection_digest == selection_b
    assert receipt.target_selection_digest == selection_a
    assert receipt.rollback_selection_digest == service.current_selection()[2]
    assert "alice" not in repr(receipt)
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 3
    assert _all_store_ledgers(service) == stores_before
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        second, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 3
    assert service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ) == receipt
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 3
    current = service.current_selection()
    assert current is not None
    assert object.__getattribute__(current[0], "_record_id") == object.__getattribute__(first, "_record_id")
    service.close()

    reopened = HarbourKnowledgeStagingDestinationService.open_existing(
        tmp_path / "rollback", trusted_issuers=(issuer,), clock=clock,
    )
    assert reopened.current_selection()[2] == receipt.rollback_selection_digest
    fresh_principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    assert reopened.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": fresh_principal},
    ) == receipt
    assert reopened._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 3
    reopened.close()


@pytest.mark.parametrize("changed_generation", ["from", "target", "target_compensated"])
def test_rollback_denies_any_store_event_after_selection_or_departure(tmp_path, changed_generation):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / changed_generation, trusted_issuers=(issuer,), clock=clock,
    )
    first = _zero_completion(tmp_path, service, issuer, "diverge-a")
    selection_a = service.select_generation(first, expected_selection_digest="sha256:" + "0" * 64)
    second = _zero_completion(tmp_path, service, issuer, "diverge-b")
    selection_b = service.select_generation(second, expected_selection_digest=selection_a)
    selected = service.selected_runtime("alice") if changed_generation == "from" else None
    if selected is None:
        row = service._connection.execute(
            "SELECT r.* FROM staging_reservations r JOIN staging_completions c "
            "ON c.generation_id=r.generation_id WHERE c.record_id=?",
            (object.__getattribute__(first, "_record_id"),),
        ).fetchone()
        selected = (None, None, None, None, service._store(row))
    if changed_generation == "target_compensated":
        _compensate_store_head(selected[4], issuer, "alice")
    else:
        _advance_selected_store(selected[4], issuer, "alice", "diverge-" + changed_generation)
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 2
    service.close()


@pytest.mark.parametrize("case", ["missing", "extra", "wrong_owner", "wrong_context", "expired", "forged", "old_key"])
def test_rollback_requires_exact_current_trusted_principal_set(tmp_path, case):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / case, trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, "authority-" + case)
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    principals = {"alice": principal}
    if case == "missing":
        principals = {}
    elif case == "extra":
        principals["bob"] = issuer.issue(subject_id="bob", owner_id="bob", auth_context_digest=context)
    elif case == "wrong_owner":
        principals["alice"] = issuer.issue(subject_id="bob", owner_id="bob", auth_context_digest=context)
    elif case == "wrong_context":
        principals["alice"] = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest="sha256:" + "f" * 64)
    elif case == "expired":
        clock.value += timedelta(hours=2)
    elif case == "forged":
        forged = object.__new__(TrustedPrincipal)
        for field in ("_issuer_id", "_key_id", "_subject_id", "_owner_id", "_auth_context_digest",
                      "_issued_at", "_expires_at", "_nonce", "_attestation"):
            object.__setattr__(forged, field, getattr(principal, field[1:]))
        object.__setattr__(forged, "_sealed", True)
        object.__setattr__(forged, "_attestation", "hmac-sha256:" + "0" * 64)
        principals["alice"] = forged
    elif case == "old_key":
        old = AuthenticatedPrincipalIssuer(
            issuer_id="rollback-test", key_id="old-key", secret=b"o" * 32, clock=clock,
        )
        principals["alice"] = old.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals=principals,
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 2
    service.close()


def test_rollback_denies_stale_cas_nonadjacent_target_wrong_completion_and_owner_change(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "cas", trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, "cas")
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    principals = {"alice": principal}
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        second, expected_selection_digest=selection_a, target_selection_digest=selection_a,
        principals=principals,
    ))
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        second, expected_selection_digest=selection_b, target_selection_digest=selection_a,
        principals=principals,
    ))
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b, target_selection_digest="sha256:" + "9" * 64,
        principals=principals,
    ))
    third = _zero_completion(tmp_path, service, issuer, "cas-c")
    selection_c = service.select_generation(third, expected_selection_digest=selection_b)
    context_c = service.rollback_authority_context_digest(selection_c, selection_a)
    principal_c = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context_c)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        third, expected_selection_digest=selection_c, target_selection_digest=selection_a,
        principals={"alice": principal_c},
    ))
    _closed("staging_destination_denied", lambda: service.select_generation(
        first, expected_selection_digest=selection_c,
    ))
    service.close()

    ownership = HarbourKnowledgeStagingDestinationService(
        tmp_path / "ownership", trusted_issuers=(issuer,), clock=clock,
    )
    _first, owner_a, owner_b, digest_b = _rollback_pair(
        tmp_path, ownership, issuer, "owners", ("alice", "bob"), ("alice",),
    )
    context_owner = ownership.rollback_authority_context_digest(digest_b, owner_a)
    owner_principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context_owner)
    _closed("staging_destination_denied", lambda: ownership.rollback_generation(
        _first, expected_selection_digest=digest_b, target_selection_digest=owner_a,
        principals={"alice": owner_principal},
    ))
    ownership.close()


@pytest.mark.parametrize("failure", ["second_store_acquire", "ledger_commit"])
def test_rollback_failure_unwinds_ledger_and_store_transactions(tmp_path, monkeypatch, failure):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / failure, trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, "cleanup-" + failure)
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    if failure == "second_store_acquire":
        original_verify = HarbourKnowledgeStore._verify
        calls = 0

        def fail_second(connection):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected store verification failure")
            return original_verify(connection)

        monkeypatch.setattr(HarbourKnowledgeStore, "_verify", staticmethod(fail_second))
    else:
        def deny_ledger_commit(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        service._connection.set_authorizer(deny_ledger_commit)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    if failure == "ledger_commit":
        service._connection.set_authorizer(None)
    else:
        monkeypatch.setattr(HarbourKnowledgeStore, "_verify", staticmethod(original_verify))
    assert not service._connection.in_transaction
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 2
    assert all(not store._connection.in_transaction for store in service._stores.values())
    assert service.current_selection()[2] == selection_b
    service.close()


def test_rollback_store_cleanup_failure_quarantines_and_closes_cached_store(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "cleanup-store-rollback", trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _rollback_pair(
        tmp_path, service, issuer, "cleanup-store-rollback",
    )
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    row = service._connection.execute(
        "SELECT r.* FROM staging_reservations r JOIN staging_completions c "
        "ON c.generation_id=r.generation_id WHERE c.record_id=?",
        (object.__getattribute__(first, "_record_id"),),
    ).fetchone()
    store = service._store(row)
    connection = store._conn()

    def reject_rollback(action, first_arg, second_arg, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and first_arg == "ROLLBACK":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(reject_rollback)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    assert store._quarantined
    assert store._connection is None
    assert not store.available
    # Ledger COMMIT precedes Store transaction cleanup by design so Store locks
    # remain held through the atomic selection append. Cleanup failure is reported
    # as bounded denial; the committed append is recovered by exact replay after
    # reopening with a fresh Store connection.
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 3
    _closed("staging_destination_denied", lambda: service._store(row))
    service.close()
    reopened = HarbourKnowledgeStagingDestinationService.open_existing(
        tmp_path / "cleanup-store-rollback", trusted_issuers=(issuer,), clock=clock,
    )
    recovered = reopened.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    )
    assert recovered.outcome == "rolled_back"
    assert reopened.current_selection()[2] == recovered.rollback_selection_digest
    assert reopened._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 3
    reopened.close()


def test_rollback_holds_both_store_locks_until_ledger_commit(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "concurrency", trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, "concurrency")
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    # The target Store is the older generation; locate it through its selection row.
    target_row = service._connection.execute(
        "SELECT * FROM staging_selections WHERE selection_digest=?", (selection_a,),
    ).fetchone()
    target_reservation = service._connection.execute(
        "SELECT * FROM staging_reservations WHERE generation_id=?", (target_row["generation_id"],),
    ).fetchone()
    target_store = service._store(target_reservation)
    original_digest = service._rollback_principal_set_digest
    entered = threading.Event()
    release = threading.Event()
    attempting_write = threading.Event()
    write_finished = threading.Event()
    receipts = []
    failures = []
    calls = 0

    def pause_before_append(principals, owners, authorization_context_digest):
        nonlocal calls
        result = original_digest(principals, owners, authorization_context_digest)
        calls += 1
        if calls == 2:
            entered.set()
            if not release.wait(5):
                raise RuntimeError("bounded concurrency test release timeout")
        return result

    service._rollback_principal_set_digest = pause_before_append

    def rollback():
        try:
            receipts.append(service.rollback_generation(
                first, expected_selection_digest=selection_b,
                target_selection_digest=selection_a, principals={"alice": principal},
            ))
        except BaseException as error:
            failures.append(type(error).__name__)

    def write_target():
        attempting_write.set()
        _advance_selected_store(target_store, issuer, "alice", "concurrent-after-rollback")
        write_finished.set()

    rollback_thread = threading.Thread(target=rollback)
    write_thread = threading.Thread(target=write_target)
    rollback_thread.start()
    try:
        assert entered.wait(5)
        write_thread.start()
        assert attempting_write.wait(2)
        assert not write_finished.wait(0.05)
    finally:
        release.set()
        rollback_thread.join(5)
        if write_thread.ident is not None:
            write_thread.join(5)
    assert not rollback_thread.is_alive() and not write_thread.is_alive()
    assert not failures and len(receipts) == 1 and write_finished.is_set()
    assert service.current_selection()[2] == receipts[0].rollback_selection_digest
    service.close()


def test_rollback_cannot_target_rollback_or_replay_after_advancement(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "rollback-chain", trusted_issuers=(issuer,), clock=clock,
    )
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, "rollback-chain")
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    original = service.rollback_generation(
        first, expected_selection_digest=selection_b, target_selection_digest=selection_a,
        principals={"alice": principal},
    )
    third = _zero_completion(tmp_path, service, issuer, "rollback-chain-c")
    selection_c = service.select_generation(third, expected_selection_digest=original.rollback_selection_digest)
    context_c = service.rollback_authority_context_digest(selection_c, original.rollback_selection_digest)
    principal_c = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context_c)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        third, expected_selection_digest=selection_c,
        target_selection_digest=original.rollback_selection_digest,
        principals={"alice": principal_c},
    ))
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b, target_selection_digest=selection_a,
        principals={"alice": principal},
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 4
    service.close()


def test_rollback_intent_tampering_breaks_chain_hmac(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    root = tmp_path / "tampered"
    service = HarbourKnowledgeStagingDestinationService(root, trusted_issuers=(issuer,), clock=clock)
    first, selection_a, second, selection_b = _rollback_pair(tmp_path, service, issuer, "tampered")
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    service.rollback_generation(
        first, expected_selection_digest=selection_b, target_selection_digest=selection_a,
        principals={"alice": principal},
    )
    service._connection.execute("DROP TRIGGER staging_selections_no_update")
    service._connection.execute("UPDATE staging_selections SET intent='read_only_query' WHERE sequence=3")
    service._connection.execute(
        "CREATE TRIGGER staging_selections_no_update BEFORE UPDATE ON staging_selections "
        "BEGIN SELECT RAISE(ABORT,'immutable'); END",
    )
    _closed("staging_destination_denied", service.current_selection)
    service.close()
    _closed("staging_destination_denied", lambda: HarbourKnowledgeStagingDestinationService.open_existing(
        root, trusted_issuers=(issuer,), clock=clock,
    ))


def test_rollback_rejects_tampered_store_event_evidence(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "store-tampered", trusted_issuers=(issuer,), clock=clock,
    )
    first = _zero_completion(tmp_path, service, issuer, "store-tampered-a")
    selection_a = service.select_generation(first, expected_selection_digest="sha256:" + "0" * 64)
    selected_a = service.selected_runtime("alice")
    _advance_selected_store(selected_a[4], issuer, "alice", "store-tamper-predeparture")
    second = _zero_completion(tmp_path, service, issuer, "store-tampered-b")
    selection_b = service.select_generation(second, expected_selection_digest=selection_a)
    store = selected_a[4]
    store._connection.execute("DROP TRIGGER knowledge_events_no_update")
    store._connection.execute(
        "UPDATE knowledge_events SET event_digest=? WHERE event_seq=1",
        ("sha256:" + "f" * 64,),
    )
    store._connection.execute(
        "CREATE TRIGGER knowledge_events_no_update BEFORE UPDATE ON knowledge_events "
        "BEGIN SELECT RAISE(ABORT,'immutable'); END",
    )
    context = service.rollback_authority_context_digest(selection_b, selection_a)
    principal = issuer.issue(subject_id="alice", owner_id="alice", auth_context_digest=context)
    _closed("staging_destination_denied", lambda: service.rollback_generation(
        first, expected_selection_digest=selection_b,
        target_selection_digest=selection_a, principals={"alice": principal},
    ))
    assert service._connection.execute("SELECT COUNT(*) FROM staging_selections").fetchone()[0] == 2
    service.close()


def test_legacy_read_only_selection_intent_remains_valid(tmp_path):
    clock = Clock()
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="rollback-test", key_id="key-1", secret=b"r" * 32, clock=clock,
    )
    service = HarbourKnowledgeStagingDestinationService(
        tmp_path / "legacy-selection", trusted_issuers=(issuer,), clock=clock,
    )
    completion = _zero_completion(tmp_path, service, issuer, "legacy-selection")
    service.select_generation(completion, expected_selection_digest="sha256:" + "0" * 64)
    service._connection.execute("DROP TRIGGER staging_selections_no_update")
    service._connection.execute("UPDATE staging_selections SET intent='read_only_query' WHERE sequence=1")
    row = service._connection.execute("SELECT * FROM staging_selections WHERE sequence=1").fetchone()
    body = {name: row[name] for name in (
        "sequence", "prior_digest", "generation_id", "completion_record_id", "completion_attestation",
        "owner_set_digest", "migration_digest", "head_manifest_digest", "store_event_anchor", "intent", "selected_at",
    )}
    digest = _sha({"schema": "odysseus.harbour.knowledge_selection.v1", "body": body})
    signature = hmac.new(service._key(), _canonical(body), hashlib.sha256).hexdigest()
    service._connection.execute(
        "UPDATE staging_selections SET selection_digest=?,attestation=? WHERE sequence=1",
        (digest, signature),
    )
    service._connection.execute(
        "CREATE TRIGGER staging_selections_no_update BEFORE UPDATE ON staging_selections "
        "BEGIN SELECT RAISE(ABORT,'immutable'); END",
    )
    assert service.current_selection()[2] == digest
    service.close()


def _head(session, lease, principal):
    return session.get_head(lease, principal, owner_id="alice", knowledge_id="note-1")


def _lifecycle_args(head, method):
    result = {"knowledge_id": "note-1", "expected_head": head, "idempotency_key": method + "-note-001"}
    if method == "complete_purge":
        result.update(policy_receipt=POLICY, projection_deletion_receipts=[PROJECTION], backup_deletion_receipts=[BACKUP])
    return result


def _retention(**changes):
    return {
        "retention_class": "time_bound", "deletion_mode": "purge_eligible",
        "retain_until": "2026-08-28T00:00:00.000000Z",
        "grace_until": "2026-08-28T12:00:00.000000Z", "legal_hold": False, **changes,
    }


def _pending(setup, retention=None):
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    principal = _principal(issuer)
    _create(session, lease, principal, retention=retention or _retention())
    for method in ("tombstone", "request_purge"):
        args = _lifecycle_args(_head(session, lease, principal), method)
        receipt = getattr(session, method)(lease, principal, owner_id="alice", **args)
        assert receipt == getattr(session, method)(lease, principal, owner_id="alice", **args)
    return session, lease, principal, _lifecycle_args(_head(session, lease, principal), "complete_purge")


def _ledgers(service):
    # Test-only private inspection: rejected calls must leave both ledgers exact.
    store = next(iter(service._stores.values()))
    return tuple(
        tuple(tuple(row) for row in connection.execute("SELECT * FROM " + table))
        for connection, tables in (
            (service._connection, ("staging_reservations", "staging_operations")),
            (store._connection, ("knowledge_blobs", "knowledge_revisions", "knowledge_heads",
                                 "knowledge_events", "knowledge_receipts", "knowledge_idempotency")),
        )
        for table in tables
    )


def _observe_store(monkeypatch, service, method):
    store = next(iter(service._stores.values()))
    original = getattr(store, method)
    calls = []

    def observe(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, method, observe)
    return calls


def test_complete_purge_forwards_full_evidence_and_replays_without_store_write(setup, monkeypatch):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    args["projection_deletion_receipts"] = [PROJECTION, OTHER]
    args["backup_deletion_receipts"] = [BACKUP, OTHER]
    calls = _observe_store(monkeypatch, service, "complete_purge")
    receipt = session.complete_purge(lease, principal, owner_id="alice", **args)
    assert receipt.outcome == "written"
    assert receipt.write_count == 4
    assert len(calls) == 1
    assert calls[0]["expected_head"] == args["expected_head"]
    assert calls[0]["expected_head"] is not args["expected_head"]
    for field in EVIDENCE_FIELDS:
        assert calls[0][field] == args[field]
    assert calls[0]["projection_deletion_receipts"] is not args["projection_deletion_receipts"]
    head = _head(session, lease, principal)
    assert head.state == "purged"
    assert head.generation == 4
    _store_closed("content_unavailable", lambda: session.read_payload(
        lease, principal, owner_id="alice", revision_id=head.current_revision_id,
    ))
    before = _ledgers(service)
    replay = session.complete_purge(lease, principal, owner_id="alice", **{
        **args, "projection_deletion_receipts": (PROJECTION, OTHER), "backup_deletion_receipts": (BACKUP, OTHER),
    })
    assert replay == receipt
    assert replay is not receipt
    assert len(calls) == 1
    assert _ledgers(service) == before
    store = next(iter(service._stores.values()))
    assert store._connection.execute("SELECT COUNT(*) FROM knowledge_blobs").fetchone()[0] == 0
    assert store._connection.execute("SELECT COUNT(*) FROM knowledge_events").fetchone()[0] == 4


def test_restore_forwards_and_replays_then_allows_new_tombstone(setup, monkeypatch):
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    principal = _principal(issuer)
    _create(session, lease, principal)
    args = _lifecycle_args(_head(session, lease, principal), "tombstone")
    session.tombstone(lease, principal, owner_id="alice", **args)
    _store_closed("content_unavailable", lambda: session.read_payload(
        lease, principal, owner_id="alice", revision_id=args["expected_head"].current_revision_id,
    ))
    args = _lifecycle_args(_head(session, lease, principal), "restore")
    calls = _observe_store(monkeypatch, service, "restore")
    restored = session.restore(lease, principal, owner_id="alice", **args)
    assert restored == session.restore(lease, principal, owner_id="alice", **args)
    assert len(calls) == 1
    assert session.read_payload(lease, principal, owner_id="alice", revision_id=args["expected_head"].current_revision_id) == b"one"
    again = _lifecycle_args(_head(session, lease, principal), "tombstone")
    again["idempotency_key"] = "tombstone-note-002"
    assert session.tombstone(lease, principal, owner_id="alice", **again).write_count == 4


@pytest.mark.parametrize("method", LIFECYCLE)
def test_lifecycle_missing_and_unknown_fields_are_bounded_before_hashing(setup, monkeypatch, method):
    _clock, _issuer, service = setup
    session, lease, principal, complete = _pending(setup)
    args = _lifecycle_args(complete["expected_head"], method)
    calls = _observe_store(monkeypatch, service, method)
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    malformed = [{key: value for key, value in args.items() if key != missing} for missing in args]
    malformed += [{**args, "unknown": Hostile()}, {}]
    for value in malformed:
        _closed("staging_destination_denied", lambda: getattr(session, method)(lease, principal, owner_id="alice", **value))
    assert hashes == []
    assert calls == []
    assert _ledgers(service) == before


class Hostile:
    def __iter__(self):
        raise AssertionError("caller iteration must not run")

    def __repr__(self):
        raise AssertionError("caller representation must not run")


class HostileList(list):
    def __iter__(self):
        raise AssertionError("subclass iteration must not run")


class HostileStr(str):
    def __eq__(self, other):
        raise AssertionError("subclass comparison must not run")


@pytest.mark.parametrize("method", LIFECYCLE)
@pytest.mark.parametrize("field", ["knowledge_id", "expected_head", "idempotency_key"])
def test_lifecycle_wrong_type_required_fields_fail_before_hash(setup, monkeypatch, method, field):
    _clock, _issuer, service = setup
    session, lease, principal, complete = _pending(setup)
    args = _lifecycle_args(complete["expected_head"], method)
    args[field] = Hostile()
    calls = _observe_store(monkeypatch, service, method)
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: getattr(session, method)(lease, principal, owner_id="alice", **args))
    assert calls == []
    assert hashes == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("malformation", ["uninitialized", "bool-generation", "huge-generation"])
def test_lifecycle_rejects_uninitialized_or_noninteger_head(setup, monkeypatch, malformation):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    args["expected_head"] = (
        object.__new__(KnowledgeHead) if malformation == "uninitialized"
        else replace(args["expected_head"], generation=True if malformation == "bool-generation" else 10**5000)
    )
    calls = _observe_store(monkeypatch, service, "complete_purge")
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert calls == []
    assert hashes == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", EVIDENCE_FIELDS)
@pytest.mark.parametrize("bad", [None, 7, True, {}, Hostile(), HostileList(), HostileStr("digest")], ids=["none", "int", "bool", "dict", "hostile", "list-subclass", "str-subclass"])
def test_complete_purge_bad_evidence_types_never_reach_hash_or_store(setup, monkeypatch, field, bad):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    args[field] = bad
    calls = _observe_store(monkeypatch, service, "complete_purge")
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert hashes == []
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", EVIDENCE_FIELDS)
@pytest.mark.parametrize("bad_digest", ["", "bad", "sha256:" + "A" * 64, Hostile(), HostileStr(POLICY)], ids=["empty", "invalid", "uppercase", "hostile", "subclass"])
def test_complete_purge_rejects_malformed_digest_elements(setup, monkeypatch, field, bad_digest):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    args[field] = bad_digest if field == "policy_receipt" else [bad_digest]
    calls = _observe_store(monkeypatch, service, "complete_purge")
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert hashes == []
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", EVIDENCE_FIELDS[1:])
@pytest.mark.parametrize("evidence", [[], [OTHER, PROJECTION], [PROJECTION, PROJECTION], [PROJECTION] * 129], ids=["empty", "unsorted", "duplicate", "oversized"])
def test_store_remains_validator_of_evidence_requirements(setup, monkeypatch, field, evidence):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    args[field] = evidence
    calls = _observe_store(monkeypatch, service, "complete_purge")
    before = _ledgers(service)
    _store_closed("validation_failed", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert len(calls) == 1
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", tuple(HEAD_DRIFT))
def test_complete_purge_replay_binds_all_nine_head_fields(setup, monkeypatch, field):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    session.complete_purge(lease, principal, owner_id="alice", **args)
    args["expected_head"] = replace(args["expected_head"], **{field: HEAD_DRIFT[field]})
    calls = _observe_store(monkeypatch, service, "complete_purge")
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", tuple(HEAD_DRIFT))
def test_complete_purge_wrong_type_in_each_head_field_is_bounded(setup, monkeypatch, field):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    args["expected_head"] = replace(args["expected_head"], **{field: Hostile()})
    calls = _observe_store(monkeypatch, service, "complete_purge")
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert hashes == []
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", (*EVIDENCE_FIELDS, "knowledge_id", "idempotency_key"))
def test_complete_purge_replay_rejects_changed_evidence_identity_or_key(setup, monkeypatch, field):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    session.complete_purge(lease, principal, owner_id="alice", **args)
    args[field] = {"policy_receipt": OTHER, "projection_deletion_receipts": [OTHER], "backup_deletion_receipts": [OTHER], "knowledge_id": "note-2", "idempotency_key": "complete-purge-new-key"}[field]
    calls = _observe_store(monkeypatch, service, "complete_purge")
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(lease, principal, owner_id="alice", **args))
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("method", LIFECYCLE)
@pytest.mark.parametrize("failure", ["expired", "forged", "other-session", "wrong-owner", "principal-owner", "auth-context", "unauthenticated", "expired-principal", "revoked", "sealed"])
def test_lifecycle_keeps_auth_lease_and_terminal_precedence(setup, monkeypatch, method, failure):
    clock, issuer, service = setup
    session, lease, principal, args = _pending(setup)
    owner = "alice"
    check, code = _closed, "staging_destination_denied"
    if failure == "expired":
        clock.value += timedelta(seconds=301)
        code = "staging_destination_expired"
    elif failure == "forged":
        lease = object.__new__(StagingDestinationLease)
    elif failure == "other-session":
        session, _other = _reserve(service)
    elif failure == "wrong-owner":
        owner = "mallory"
    elif failure == "principal-owner":
        owner = "bob"
        check, code = _store_closed, "owner_scope_denied"
    elif failure == "auth-context":
        principal = _principal(issuer, snapshot=OTHER)
        check, code = _store_closed, "auth_context_denied"
    elif failure == "unauthenticated":
        principal = object()
        check, code = _store_closed, "unauthenticated"
    elif failure == "expired-principal":
        principal = issuer.issue(subject_id="alice-user", owner_id="alice", auth_context_digest=_context("migration-1", "alice"), ttl_seconds=1)
        clock.value += timedelta(seconds=2)
        check, code = _store_closed, "unauthenticated"
    elif failure == "revoked":
        service.revoke(lease)
    elif failure == "sealed":
        session.seal(lease, completion_digest=OTHER)
    calls = _observe_store(monkeypatch, service, method)
    before = _ledgers(service)
    # Malformed mutation input must not override authorization precedence.
    check(code, lambda: getattr(session, method)(lease, principal, owner_id=owner, unknown=Hostile()))
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("failure", ["stale_authority", "invalid_lifecycle", "retention_denied", "validation_failed"])
def test_lifecycle_preserves_store_policy_and_state_errors(setup, failure):
    _clock, issuer, service = setup
    retention = _retention(retain_until="2026-09-01T00:00:00.000000Z") if failure == "retention_denied" else _retention()
    session, lease, principal, args = _pending(setup, retention)
    if failure == "stale_authority":
        args["expected_head"] = replace(args["expected_head"], generation=99)
    elif failure == "invalid_lifecycle":
        args = _lifecycle_args(args["expected_head"], "restore")
    elif failure == "validation_failed":
        args["idempotency_key"] = "short"
    before = _ledgers(service)
    method = "restore" if failure == "invalid_lifecycle" else "complete_purge"
    _store_closed(failure, lambda: getattr(session, method)(lease, principal, owner_id="alice", **args))
    assert _ledgers(service) == before


def test_complete_purge_hash_and_store_use_detached_head_and_evidence(setup, monkeypatch):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    expected = replace(args["expected_head"])
    original_hash = service._safe_input

    def mutate_caller_after_normalization(value):
        object.__setattr__(args["expected_head"], "generation", 77)
        args["projection_deletion_receipts"].append(OTHER)
        args["backup_deletion_receipts"].clear()
        assert type(value["expected_head"]) is dict
        return original_hash(value)

    monkeypatch.setattr(service, "_safe_input", mutate_caller_after_normalization)
    calls = _observe_store(monkeypatch, service, "complete_purge")
    assert session.complete_purge(lease, principal, owner_id="alice", **args).write_count == 4
    assert calls[0]["expected_head"] == expected
    assert calls[0]["projection_deletion_receipts"] == [PROJECTION]
    assert calls[0]["backup_deletion_receipts"] == [BACKUP]


def test_append_revision_uses_complete_detached_head_and_metadata(setup, monkeypatch):
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    principal = _principal(issuer)
    _create(session, lease, principal)
    args = {
        "knowledge_id": "note-1", "expected_head": _head(session, lease, principal),
        "payload_bytes": b"two", "payload_schema_id": "harbour.note.v1", "payload_media_type": "text/plain",
        "classification": "private", "retention": _retention(), "idempotency_key": "append-note-001",
    }
    original_hash = service._safe_input

    def observe_input(value):
        assert set(value["expected_head"]) == set(HEAD_DRIFT)
        assert value["retention"] is not args["retention"]
        return original_hash(value)

    monkeypatch.setattr(service, "_safe_input", observe_input)
    calls = _observe_store(monkeypatch, service, "append_revision")
    receipt = session.append_revision(lease, principal, owner_id="alice", **args)
    assert receipt.write_count == 2
    assert receipt == session.append_revision(lease, principal, owner_id="alice", **args)
    assert len(calls) == 1
    assert _head(session, lease, principal).generation == 2
    for field in ("knowledge_kind", "tombstoned_at", "purge_requested_at"):
        changed = {**args, "expected_head": replace(args["expected_head"], **{field: HEAD_DRIFT[field]})}
        _closed("staging_destination_denied", lambda: session.append_revision(lease, principal, owner_id="alice", **changed))
    assert len(calls) == 1


@pytest.mark.parametrize("method", ["create_object", "append_revision"])
def test_revision_mutations_reject_unknown_or_hostile_input_before_hash(setup, monkeypatch, method):
    _clock, issuer, service = setup
    session, lease = _reserve(service)
    principal = _principal(issuer)
    _create(session, lease, principal)
    args = {
        "knowledge_id": "note-1", "payload_bytes": b"two", "payload_schema_id": "harbour.note.v1",
        "payload_media_type": "text/plain", "classification": "private", "idempotency_key": "another-key-001",
    }
    args.update({"knowledge_kind": "note"} if method == "create_object" else {"expected_head": _head(session, lease, principal)})
    circular = []
    circular.append(circular)
    malformed = [{**args, "unknown": Hostile()}, {**args, "retention": {"extra": Hostile()}}, {**args, "provenance": circular}]
    calls = _observe_store(monkeypatch, service, method)
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    before = _ledgers(service)
    for value in malformed:
        _closed("staging_destination_denied", lambda: getattr(session, method)(lease, principal, owner_id="alice", **value))
    assert calls == []
    assert hashes == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("method", (*LIFECYCLE, "create_object", "append_revision"))
@pytest.mark.parametrize("key", ["invalid-\ud800-key", "invalid-\udfff-key"], ids=["high-surrogate", "low-surrogate"])
def test_unencodable_idempotency_keys_fail_before_hash_and_sql(setup, monkeypatch, method, key):
    _clock, _issuer, service = setup
    session, lease, principal, complete = _pending(setup)
    if method in LIFECYCLE:
        args = _lifecycle_args(complete["expected_head"], method)
    else:
        args = {
            "knowledge_id": "note-1", "payload_bytes": b"two", "payload_schema_id": "harbour.note.v1",
            "payload_media_type": "text/plain", "classification": "private",
        }
        args.update({"knowledge_kind": "note"} if method == "create_object" else {"expected_head": complete["expected_head"]})
    args["idempotency_key"] = key
    hashes = []
    monkeypatch.setattr(service, "_safe_input", lambda value: hashes.append(value))
    calls = _observe_store(monkeypatch, service, method)
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: getattr(session, method)(lease, principal, owner_id="alice", **args))
    assert hashes == []
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("field", StagingDestinationLease.__slots__)
@pytest.mark.parametrize("bad", [None, Hostile(), HostileStr("forged"), True, 10**5000, "\ud800"], ids=["none", "object", "str-subclass", "bool", "huge-int", "surrogate"])
def test_forged_lease_scalars_fail_before_attestation_without_mutation(setup, monkeypatch, field, bad):
    _clock, _issuer, service = setup
    session, lease, principal, args = _pending(setup)
    forged = StagingDestinationLease(**{
        name: bad if name == field else object.__getattribute__(lease, name)
        for name in StagingDestinationLease.__slots__
    })
    attestations = []
    monkeypatch.setattr(service, "_attest", lambda *values: attestations.append(values))
    calls = _observe_store(monkeypatch, service, "complete_purge")
    before = _ledgers(service)
    _closed("staging_destination_denied", lambda: session.complete_purge(forged, principal, owner_id="alice", **args))
    assert attestations == []
    assert calls == []
    assert _ledgers(service) == before


@pytest.mark.parametrize("operation", ["complete_purge", "revoke", "seal"])
def test_uninitialized_lease_has_suppressed_error_context_and_no_write(setup, operation):
    _clock, _issuer, service = setup
    session, _lease, principal, args = _pending(setup)
    forged = object.__new__(StagingDestinationLease)
    before = _ledgers(service)
    if operation == "revoke":
        callback = lambda: service.revoke(forged)
    elif operation == "seal":
        callback = lambda: session.seal(forged, completion_digest=OTHER)
    else:
        callback = lambda: session.complete_purge(forged, principal, owner_id="alice", **args)
    _closed("staging_destination_denied", callback)
    assert _ledgers(service) == before
