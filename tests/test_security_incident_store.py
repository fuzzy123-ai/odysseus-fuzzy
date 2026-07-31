from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from src.security_incident_store import ConflictError, RedactionError, SecurityIncidentStore
import src.security_incident_store as incident_store_module
import src.security_incident_store_migrations as migrations
from src.security_incident_store_migrations import (
    SCHEMA_VERSION,
    SQLITE_APPLICATION_ID,
    SecurityIncidentMigrationError,
    apply_migrations,
)


SCOPE = "scope:sha256:" + "a" * 64
POLICY = "policy:sha256:" + "b" * 64


def test_pending_operator_notification_query_is_bounded_and_excludes_terminal_states(tmp_path):
    store = SecurityIncidentStore(tmp_path / "pending.sqlite", clock=lambda: 100)
    incident = store.create_incident(
        incident_id="incident-pending", incident_ref="incident:sha256:" + "a" * 64,
        audit_ref="audit:sha256:" + "b" * 64,
    )
    action = store.create_action(
        action_id="action-pending", incident_id=incident.incident_id,
        action_type="operator_notification", scope_fingerprint=SCOPE,
        policy_revision=POLICY, idempotency_key="idempotency-pending",
        ttl_seconds=60, audit_ref="audit:sha256:" + "c" * 64,
    )
    assert store.pending_operator_notification_actions(limit=1) == (action,)
    prepared = store.transition(
        action_id=action.action_id, expected_version=action.version,
        target_state="prepared", audit_ref="audit:sha256:" + "d" * 64,
    )
    denied = store.transition(
        action_id=prepared.action_id, expected_version=prepared.version,
        target_state="denied", audit_ref="audit:sha256:" + "e" * 64,
    )
    assert denied.state == "denied"
    assert store.pending_operator_notification_actions() == ()
    with pytest.raises(Exception):
        store.pending_operator_notification_actions(limit=65)


def _ref(kind: str, char: str = "c") -> str:
    return f"{kind}:sha256:{char * 64}"


def _store(tmp_path: Path, clock=lambda: 100.0) -> SecurityIncidentStore:
    return SecurityIncidentStore(tmp_path / "incidents.sqlite3", clock=clock)


def test_v1_store_migrates_to_private_context_schema_without_rewriting_existing_tables(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    db = sqlite3.connect(path)
    migrations._migration_1(db)
    db.execute("PRAGMA user_version=1")
    db.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
    db.commit(); db.close()
    store = SecurityIncidentStore(path)
    with store._read() as migrated:
        assert int(migrated.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        assert migrated.execute("SELECT 1 FROM sqlite_master WHERE name='incident_contexts'").fetchone() is not None


def _prepared(store: SecurityIncidentStore, *, ttl: int = 60):
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    created = store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=ttl, audit_ref=_ref("audit", "d"))
    return store.transition(action_id=created.action_id, expected_version=created.version, target_state="prepared", audit_ref=_ref("audit", "e"))


def _approved(store: SecurityIncidentStore):
    prepared = _prepared(store)
    return store.approve(action_id=prepared.action_id, expected_version=prepared.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint=SCOPE, policy_revision=POLICY, audit_ref=_ref("audit", "f"))


def _executed(store: SecurityIncidentStore):
    approved = _approved(store)
    executing = store.transition(action_id="action-one", expected_version=approved.version, target_state="executing", audit_ref=_ref("audit", "1"))
    return store.transition(action_id="action-one", expected_version=executing.version, target_state="executed", audit_ref=_ref("audit", "2"), receipt_ref=_ref("receipt"))


def test_positive_lifecycle_has_bounded_durable_projections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    executed = _executed(store)
    verified = store.transition(action_id="action-one", expected_version=executed.version, target_state="verified", audit_ref=_ref("audit", "3"), verification_ref=_ref("verification"))

    assert verified.state == "verified"
    assert store.get_incident("incident-one").incident_ref == _ref("incident")
    approval = store.get_approval("action-one")
    assert approval is not None and approval.consumed_at == 100.0
    evidence = store.get_action_evidence("action-one")
    assert evidence.receipt_ref == _ref("receipt") and evidence.verification_ref == _ref("verification")
    events = store.audit_events("action-one")
    assert [event.event_type for event in events] == [
        "action_proposed", "action_prepared", "action_approved", "approval_consumed",
        "action_executing", "action_executed", "action_verified",
    ]
    assert len(store.audit_events()) == 8


def test_failure_and_rollback_keep_distinct_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    approved = _approved(store)
    executing = store.transition(action_id="action-one", expected_version=approved.version, target_state="executing", audit_ref=_ref("audit", "1"))
    failed = store.transition(action_id="action-one", expected_version=executing.version, target_state="failed", audit_ref=_ref("audit", "2"), failure_ref=_ref("failure"))
    rolled_back = store.transition(action_id="action-one", expected_version=failed.version, target_state="rolled_back", audit_ref=_ref("audit", "3"), rollback_ref=_ref("rollback"))
    evidence = store.get_action_evidence("action-one")
    assert rolled_back.state == "rolled_back"
    assert evidence.failure_ref == _ref("failure") and evidence.rollback_ref == _ref("rollback")


def test_denial_and_expiry_are_terminal_and_expiry_is_durable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created_incident = store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    proposed = store.create_action(action_id="action-one", incident_id=created_incident.incident_id, action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit", "d"))
    denied = store.transition(action_id="action-one", expected_version=proposed.version, target_state="denied", audit_ref=_ref("audit", "e"))
    with pytest.raises(ConflictError):
        store.transition(action_id="action-one", expected_version=denied.version, target_state="prepared", audit_ref=_ref("audit", "f"))

    now = [100.0]
    expiry_dir = tmp_path / "expiry"
    expiry_dir.mkdir()
    expired_store = _store(expiry_dir, clock=lambda: now[0])
    prepared = _prepared(expired_store, ttl=1)
    now[0] = 101.0
    with pytest.raises(ConflictError):
        expired_store.approve(action_id="action-one", expected_version=prepared.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint=SCOPE, policy_revision=POLICY, audit_ref=_ref("audit", "f"))
    expired = expired_store.get_action("action-one")
    assert expired.state == "expired" and expired.version == prepared.version + 1
    assert expired_store.audit_events("action-one")[-1].event_type == "action_expired"


def test_expiry_never_rewrites_executing_or_completed_action_truth(tmp_path: Path) -> None:
    now = [100.0]
    store = _store(tmp_path, clock=lambda: now[0])
    approved = _approved(store)
    executing = store.transition(action_id="action-one", expected_version=approved.version, target_state="executing", audit_ref=_ref("audit", "1"))
    now[0] = 200.0
    executed = store.transition(action_id="action-one", expected_version=executing.version, target_state="executed", audit_ref=_ref("audit", "2"), receipt_ref=_ref("receipt"))
    assert executed.state == "executed"
    verified = store.transition(action_id="action-one", expected_version=executed.version, target_state="verified", audit_ref=_ref("audit", "3"), verification_ref=_ref("verification"))
    now[0] = 300.0
    rolled_back = store.transition(action_id="action-one", expected_version=verified.version, target_state="rolled_back", audit_ref=_ref("audit", "4"), rollback_ref=_ref("rollback"))
    assert rolled_back.state == "rolled_back" and store.get_action("action-one").state == "rolled_back"


@pytest.mark.parametrize("stage,target,reference", [
    ("proposed", "executed", "receipt_ref"),
    ("prepared", "executing", None),
    ("approved", "verified", "verification_ref"),
    ("executing", "verified", "verification_ref"),
    ("executed", "prepared", None),
    ("verified", "executed", "receipt_ref"),
])
def test_invalid_transition_families_fail_closed(tmp_path: Path, stage: str, target: str, reference: str | None) -> None:
    stage_dir = tmp_path / stage
    stage_dir.mkdir()
    store = _store(stage_dir)
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    current = store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit", "d"))
    if stage != "proposed":
        current = store.transition(action_id="action-one", expected_version=current.version, target_state="prepared", audit_ref=_ref("audit", "e"))
    if stage in {"approved", "executing", "executed", "verified"}:
        current = store.approve(action_id="action-one", expected_version=current.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint=SCOPE, policy_revision=POLICY, audit_ref=_ref("audit", "f"))
    if stage in {"executing", "executed", "verified"}:
        current = store.transition(action_id="action-one", expected_version=current.version, target_state="executing", audit_ref=_ref("audit", "1"))
    if stage in {"executed", "verified"}:
        current = store.transition(action_id="action-one", expected_version=current.version, target_state="executed", audit_ref=_ref("audit", "2"), receipt_ref=_ref("receipt"))
    if stage == "verified":
        current = store.transition(action_id="action-one", expected_version=current.version, target_state="verified", audit_ref=_ref("audit", "3"), verification_ref=_ref("verification"))
    kwargs = {reference: _ref(reference.split("_")[0])} if reference else {}
    with pytest.raises(ConflictError):
        store.transition(action_id="action-one", expected_version=current.version, target_state=target, audit_ref=_ref("audit", "9"), **kwargs)
    assert store.get_action("action-one") == current


def test_stale_scope_policy_and_approval_replay_fail_closed_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prepared = _prepared(store)
    before_events = store.audit_events("action-one")
    with pytest.raises(ConflictError):
        store.approve(action_id="action-one", expected_version=prepared.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint="scope:sha256:" + "d" * 64, policy_revision=POLICY, audit_ref=_ref("audit", "f"))
    assert store.get_action("action-one") == prepared and store.get_approval("action-one") is None
    assert store.audit_events("action-one") == before_events
    with pytest.raises(ConflictError):
        store.approve(action_id="action-one", expected_version=prepared.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint=SCOPE, policy_revision=_ref("policy", "e"), audit_ref=_ref("audit", "f"))
    approved = store.approve(action_id="action-one", expected_version=prepared.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint=SCOPE, policy_revision=POLICY, audit_ref=_ref("audit", "f"))
    with pytest.raises(ConflictError):
        store.transition(action_id="action-one", expected_version=prepared.version, target_state="executing", audit_ref=_ref("audit", "1"))
    executing = store.transition(action_id="action-one", expected_version=approved.version, target_state="executing", audit_ref=_ref("audit", "1"))
    approval = store.get_approval("action-one")
    assert approval is not None and approval.consumed_at == 100.0
    with pytest.raises(ConflictError):
        store.transition(action_id="action-one", expected_version=executing.version, target_state="executing", audit_ref=_ref("audit", "1"))


def test_idempotency_replays_only_the_identical_authoritative_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    created = store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit", "d"))
    replay = store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit", "d"))
    assert replay.action_id == created.action_id and replay.idempotent_replay
    for different in ({"action_id": "other-action"}, {"ttl_seconds": 61}, {"audit_ref": _ref("audit", "e")}):
        values = {"action_id": "action-one", "incident_id": "incident-one", "action_type": "session_invalidate_prepare", "scope_fingerprint": SCOPE, "policy_revision": POLICY, "idempotency_key": "idem-one", "ttl_seconds": 60, "audit_ref": _ref("audit", "d")}
        values.update(different)
        with pytest.raises(ConflictError):
            store.create_action(**values)


@pytest.mark.parametrize("payload", [
    {"audit_ref": {"nested": _ref("audit")}},
    {"raw_target": _ref("audit")},
    {"raw_evidence": _ref("audit")},
    {"raw_log": _ref("audit")},
    {"provider_ref": _ref("audit")},
    {"environment_ref": _ref("audit")},
    {"command_ref": _ref("audit")},
    {"audit_ref": "audit:authorization:bearer"},
    {"audit_ref": "audit:cookie:token"},
    {"audit_ref": "audit:/private/path"},
    {"audit_ref": "audit:C:\\private\\path"},
    {"audit_ref": ["secret:sha256:" + "c" * 64]},
])
def test_redaction_rejects_raw_nested_secret_and_path_corpus(tmp_path: Path, payload: dict) -> None:
    store = _store(tmp_path)
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    with pytest.raises(RedactionError):
        store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit"), metadata=payload)
    with pytest.raises(RedactionError):
        store.create_incident(incident_id="incident-two", incident_ref="incident:/private/path", audit_ref=_ref("audit"))


def test_redaction_rejects_top_level_raw_identifiers_types_and_nonopaque_refs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(RedactionError):
        store.create_incident(incident_id="token-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    with pytest.raises(RedactionError):
        store.create_incident(incident_id="incident-one", incident_ref="incident:203.0.113.7", audit_ref=_ref("audit"))
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    with pytest.raises(RedactionError):
        store.create_action(action_id="action-one", incident_id="incident-one", action_type="command_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit"))
    created = store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit"))
    prepared = store.transition(action_id="action-one", expected_version=created.version, target_state="prepared", audit_ref=_ref("audit", "d"))
    approved = store.approve(action_id="action-one", expected_version=prepared.version, approval_id="approval-one", approval_ref=_ref("approval"), scope_fingerprint=SCOPE, policy_revision=POLICY, audit_ref=_ref("audit", "e"))
    executing = store.transition(action_id="action-one", expected_version=approved.version, target_state="executing", audit_ref=_ref("audit", "f"))
    executed = store.transition(action_id="action-one", expected_version=executing.version, target_state="executed", audit_ref=_ref("audit", "1"), receipt_ref=_ref("receipt"))
    with pytest.raises(RedactionError):
        store.transition(action_id="action-one", expected_version=executed.version, target_state="verified", audit_ref=_ref("audit", "2"), verification_ref="verification:203.0.113.7")
    assert store.get_action("action-one") == executed


def test_action_type_is_compatible_with_canonical_security_contract(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    accepted = store.create_action(action_id="action-one", incident_id="incident-one", action_type="token_rotation_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit"))
    assert accepted.action_type == "token_rotation_prepare"
    with pytest.raises(RedactionError):
        store.create_action(action_id="action-two", incident_id="incident-one", action_type="arbitrary_shell", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-two", ttl_seconds=60, audit_ref=_ref("audit", "d"))


def test_reopen_schema_identity_and_append_only_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.sqlite3"
    store = SecurityIncidentStore(db_path, clock=lambda: 100.0)
    prepared = _prepared(store)
    reopened = SecurityIncidentStore(db_path, clock=lambda: 100.0)
    assert reopened.get_action("action-one").version == prepared.version
    assert reopened.get_incident("incident-one").version == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA application_id").fetchone()[0] == SQLITE_APPLICATION_ID
        original = db.execute("SELECT reference FROM audit_references WHERE action_id='action-one' LIMIT 1").fetchone()[0]
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("UPDATE audit_references SET reference=? WHERE action_id='action-one'", (_ref("audit", "f"),))
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("DELETE FROM audit_references WHERE action_id='action-one'")
        assert db.execute("SELECT reference FROM audit_references WHERE action_id='action-one' LIMIT 1").fetchone()[0] == original


def test_migration_wrong_identity_version_schema_and_collision_rollback_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrong_identity = sqlite3.connect(tmp_path / "wrong-identity.sqlite3", isolation_level=None)
    wrong_identity.execute("PRAGMA application_id=123")
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(wrong_identity)
    assert wrong_identity.execute("PRAGMA user_version").fetchone()[0] == 0
    wrong_identity.close()

    wrong_version = sqlite3.connect(tmp_path / "wrong-version.sqlite3", isolation_level=None)
    wrong_version.execute("PRAGMA user_version=2")
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(wrong_version)
    wrong_version.close()

    wrong_schema = sqlite3.connect(tmp_path / "wrong-schema.sqlite3", isolation_level=None)
    wrong_schema.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
    wrong_schema.execute("PRAGMA user_version=1")
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(wrong_schema)
    wrong_schema.close()

    collision = sqlite3.connect(tmp_path / "collision.sqlite3", isolation_level=None)
    collision.execute("CREATE TABLE incidents (wrong TEXT)")
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(collision)
    assert collision.execute("PRAGMA user_version").fetchone()[0] == 0
    assert collision.execute("SELECT count(*) FROM sqlite_master WHERE name='actions'").fetchone()[0] == 0
    collision.close()

    foreign = sqlite3.connect(tmp_path / "foreign.sqlite3", isolation_level=None)
    foreign.execute("CREATE TABLE foreign_authority (value TEXT)")
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(foreign)
    assert foreign.execute("PRAGMA user_version").fetchone()[0] == 0
    foreign.close()

    forged = sqlite3.connect(tmp_path / "forged.sqlite3", isolation_level=None)
    forged.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
    forged.execute("PRAGMA user_version=1")
    forged.executescript("""
        CREATE TABLE incidents (incident_id TEXT, incident_ref TEXT, version INTEGER, created_at REAL);
        CREATE TABLE actions (action_id TEXT, incident_id TEXT, action_type TEXT, state TEXT, version INTEGER, scope_fingerprint TEXT, policy_revision TEXT, idempotency_key TEXT, request_fingerprint TEXT, ttl_seconds REAL, expires_at REAL, receipt_ref TEXT, verification_ref TEXT, failure_ref TEXT, rollback_ref TEXT, created_at REAL, updated_at REAL);
        CREATE TABLE approvals (approval_id TEXT, action_id TEXT, action_version INTEGER, scope_fingerprint TEXT, policy_revision TEXT, approval_ref TEXT, approved_at REAL, consumed_at REAL);
        CREATE TABLE audit_references (sequence INTEGER, incident_id TEXT, action_id TEXT, action_version INTEGER, event_type TEXT, reference TEXT, occurred_at REAL);
        CREATE TRIGGER security_incident_audit_no_update BEFORE UPDATE ON audit_references BEGIN SELECT 1; END;
        CREATE TRIGGER security_incident_audit_no_delete BEFORE DELETE ON audit_references BEGIN SELECT 1; END;
    """)
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(forged)
    forged.close()

    typed_forgery = sqlite3.connect(tmp_path / "typed-forgery.sqlite3", isolation_level=None)
    typed_forgery.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
    typed_forgery.execute("PRAGMA user_version=1")
    for table, statement in migrations._TABLE_SQL.items():
        typed_forgery.execute(statement.replace("expires_at REAL NOT NULL", "expires_at TEXT NOT NULL") if table == "actions" else statement)
    for statement in migrations._INDEX_SQL.values():
        typed_forgery.execute(statement)
    for statement in migrations._TRIGGER_SQL.values():
        typed_forgery.execute(statement)
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(typed_forgery)
    typed_forgery.close()

    no_op_trigger = sqlite3.connect(tmp_path / "no-op-trigger.sqlite3", isolation_level=None)
    no_op_trigger.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
    no_op_trigger.execute("PRAGMA user_version=1")
    for statement in migrations._TABLE_SQL.values():
        no_op_trigger.execute(statement)
    for statement in migrations._INDEX_SQL.values():
        no_op_trigger.execute(statement)
    for name, statement in migrations._TRIGGER_SQL.items():
        no_op_trigger.execute(statement.replace("BEFORE UPDATE ON audit_references", "BEFORE UPDATE ON audit_references WHEN 0") if name.endswith("no_update") else statement)
    with pytest.raises(SecurityIncidentMigrationError):
        apply_migrations(no_op_trigger)
    no_op_trigger.close()

    partial = sqlite3.connect(tmp_path / "partial.sqlite3", isolation_level=None)

    def fail_after_schema_write(db: sqlite3.Connection) -> None:
        db.execute("CREATE TABLE partial_incident_store_write (value TEXT)")
        raise sqlite3.DatabaseError("synthetic migration failure")

    monkeypatch.setattr(migrations, "_MIGRATIONS", (fail_after_schema_write,))
    with pytest.raises(SecurityIncidentMigrationError):
        migrations.apply_migrations(partial)
    assert partial.execute("PRAGMA user_version").fetchone()[0] == 0
    assert partial.execute("PRAGMA application_id").fetchone()[0] == 0
    assert partial.execute("SELECT count(*) FROM sqlite_master WHERE name='partial_incident_store_write'").fetchone()[0] == 0
    partial.close()


def test_competing_compare_and_set_allows_exactly_one_writer(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.sqlite3"
    prepared = _prepared(SecurityIncidentStore(db_path, clock=lambda: 100.0))

    def deny() -> str:
        try:
            SecurityIncidentStore(db_path, clock=lambda: 100.0).transition(action_id="action-one", expected_version=prepared.version, target_state="denied", audit_ref=_ref("audit", "9"))
            return "won"
        except ConflictError:
            return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: deny(), range(2)))
    assert outcomes.count("won") == 1 and outcomes.count("lost") == 1


def test_read_connections_are_closed_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    original_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    class TrackingConnection(sqlite3.Connection):
        closed_explicitly = False

        def close(self) -> None:
            self.closed_explicitly = True
            super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(incident_store_module.sqlite3, "connect", tracked_connect)
    assert store.get_incident("incident-one").version == 1
    assert len(opened) == 1 and getattr(opened[0], "closed_explicitly")


def test_sqlite_constraints_reject_out_of_band_lifecycle_corruption(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.sqlite3"
    store = SecurityIncidentStore(db_path, clock=lambda: 100.0)
    store.create_incident(incident_id="incident-one", incident_ref=_ref("incident"), audit_ref=_ref("audit"))
    store.create_action(action_id="action-one", incident_id="incident-one", action_type="session_invalidate_prepare", scope_fingerprint=SCOPE, policy_revision=POLICY, idempotency_key="idem-one", ttl_seconds=60, audit_ref=_ref("audit", "d"))
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE actions SET state='invalid' WHERE action_id='action-one'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE actions SET ttl_seconds=86401 WHERE action_id='action-one'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO approvals(approval_id,action_id,action_version,scope_fingerprint,policy_revision,approval_ref,approved_at) VALUES(?,?,?,?,?,?,?)", ("approval-one", "action-one", 0, SCOPE, POLICY, _ref("approval"), 100.0))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO audit_references(incident_id,action_id,action_version,event_type,reference,occurred_at) VALUES(?,?,?,?,?,?)", ("incident-one", None, 1, "forged", _ref("audit"), 100.0))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO audit_references(incident_id,action_id,action_version,event_type,reference,occurred_at) VALUES(?,?,?,?,?,?)", ("incident-one", "action-one", 0, "forged", _ref("audit"), 100.0))
