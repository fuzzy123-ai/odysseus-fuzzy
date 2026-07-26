from datetime import datetime, time, timedelta, timezone
import hashlib
import hmac
import json
import threading

import pytest
from sqlalchemy import create_engine, delete, insert, inspect, update
from sqlalchemy.orm import Session as OrmSession, sessionmaker

from core import database as database_module
from core import database_migrations
from core.database import Base, ChatMessage, Session
from plugins.telegram.stores import (
    DbAuthoritativeTelegramSessionBridge,
    TelegramRolloverBridgeError,
    build_db_authoritative_rollover_runtime,
)

from src.telegram_session_rollover import (
    AtomicTelegramSessionRolloverService,
    ReasonCode,
    LedgerError,
    RolloverConfig,
    RolloverEvent,
    RolloverRecord,
    RolloverState,
    TurnIntakeEvent,
    TurnIntakeState,
    TurnMessageMarker,
    TelegramRolloverLedger,
    TelegramBindingMutationCoordinator,
    TelegramRolloverRuntime,
    TelegramRolloverSweepResult,
    TelegramTurnCoordinator,
    TelegramTurnLease,
    advance_rollover_state,
    advance_turn_intake_state,
    build_rollover_evidence,
    chat_handle_ref,
    owner_ref,
    reconcile_running_turn,
    rollover_is_due,
    rollover_local_day,
    session_ref,
    transport_update_ref,
)


KEY = b"k" * 32


class _InterleavingDatabase:
    """Deterministically changes one row just before a repository CAS write."""

    def __init__(self, database, target_table, interleave):
        self._database = database
        self._target_table = target_table
        self._interleave = interleave
        self._fired = False

    def begin_nested(self):
        return self._database.begin_nested()

    def connection(self):
        return self._database.connection()

    def execute(self, statement, *args, **kwargs):
        if (
            not self._fired
            and getattr(statement, "is_update", False)
            and getattr(statement, "table", None) is self._target_table
        ):
            self._fired = True
            self._interleave(self._database)
        return self._database.execute(statement, *args, **kwargs)


class _PreSavepointInsertDatabase:
    """Insert a known uniqueness winner immediately before a nested transaction."""

    def __init__(self, database, preinsert):
        self._database = database
        self._preinsert = preinsert
        self._fired = False

    def begin_nested(self):
        if not self._fired:
            self._fired = True
            self._preinsert(self._database)
        return self._database.begin_nested()

    def connection(self):
        return self._database.connection()

    def execute(self, statement, *args, **kwargs):
        return self._database.execute(statement, *args, **kwargs)


def _ledger_database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    database = sessionmaker(bind=engine, autoflush=False)()
    database.add(
        Session(
            id="session-private-id",
            name="Synthetic",
            endpoint_url="http://synthetic.invalid",
            model="synthetic",
            owner="alice",
        )
    )
    database.commit()
    return engine, database


def _ledger_identity():
    return owner_ref(KEY, "alice"), chat_handle_ref(KEY, "chat_a1b2c3d4")


def _turn_coordinator_fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    database = factory()
    database.add(
        Session(
            id="session-private-id",
            name="Synthetic",
            endpoint_url="http://synthetic.invalid",
            model="synthetic",
            owner="alice",
        )
    )
    ledger = TelegramRolloverLedger(database, KEY)
    binding = ledger.get_or_create_binding(
        owner_reference=owner_ref(KEY, "alice"),
        chat_reference=chat_handle_ref(KEY, "chat_a1b2c3d4"),
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    database.commit()
    database.close()
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    coordinator = TelegramTurnCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY, turn_lease_seconds=60),
        now=lambda: now,
    )
    return factory, binding, coordinator, now


def _a3_database(*session_owners: tuple[str, str]):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    database = sessionmaker(bind=engine, autoflush=False)()
    for session_id, owner in session_owners:
        database.add(
            Session(
                id=session_id,
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner=owner,
            )
        )
    database.commit()
    return database


def _assert_ledger_schema(schema):
    expected = {
        "telegram_session_bindings",
        "telegram_session_rollovers",
        "telegram_turn_intakes",
        "telegram_rollover_metadata",
    }
    assert expected <= set(schema.get_table_names())
    checks = {
        name: {item["name"] for item in schema.get_check_constraints(name)}
        for name in expected
    }
    assert {"ck_tsb_scope", "ck_tsb_turn_lease_shape", "ck_tsb_generation_nonnegative", "ck_tsb_active_rollover_day_format"} <= checks["telegram_session_bindings"]
    assert {"ck_tsr_status", "ck_tsr_committed_new_session", "ck_tsr_attempt_count", "ck_tsr_rollover_day_format"} <= checks["telegram_session_rollovers"]
    assert {"ck_tti_status", "ck_tti_retry_count", "ck_tti_scope"} <= checks["telegram_turn_intakes"]
    assert {"ck_trm_singleton_id", "ck_trm_schema_version", "ck_trm_fingerprint_length"} <= checks["telegram_rollover_metadata"]
    expected_foreign_keys = {
        "telegram_session_bindings": {"sessions"},
        "telegram_session_rollovers": {"telegram_session_bindings", "sessions"},
        "telegram_turn_intakes": {"telegram_session_bindings", "sessions"},
    }
    for table, targets in expected_foreign_keys.items():
        foreign_keys = schema.get_foreign_keys(table)
        assert {item["referred_table"] for item in foreign_keys} == targets
        assert all(item.get("options", {}).get("ondelete") == "RESTRICT" for item in foreign_keys)
    expected_unique_columns = {
        "telegram_session_bindings": ["owner_ref", "chat_handle_ref", "scope"],
        "telegram_session_rollovers": ["binding_id", "rollover_local_day"],
        "telegram_turn_intakes": ["owner_ref", "chat_handle_ref", "transport_update_ref"],
    }
    for table, columns in expected_unique_columns.items():
        assert any(index["unique"] and index["column_names"] == columns for index in schema.get_indexes(table))
    forbidden = {"prompt", "message", "reply", "provider", "owner", "chat_id", "update_id", "message_id"}
    for table in expected:
        assert not (forbidden & {column["name"] for column in schema.get_columns(table)})


def test_rollover_schema_fresh_install_has_four_tables_and_constraints():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    schema = inspect(engine)
    _assert_ledger_schema(schema)


def test_rollover_schema_upgrade_is_idempotent_and_preserves_existing_sessions(monkeypatch):
    engine = create_engine("sqlite://")
    Session.__table__.create(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            Session.__table__.insert().values(
                id="legacy-session", name="Legacy", endpoint_url="http://synthetic.invalid", model="synthetic"
            )
        )
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "DATABASE_URL", "sqlite:///:memory:")
    database_migrations._migrate_telegram_session_rollover_tables()
    database_migrations._migrate_telegram_session_rollover_tables()
    schema = inspect(engine)
    _assert_ledger_schema(schema)
    with engine.connect() as connection:
        assert connection.execute(Session.__table__.select()).mappings().one()["id"] == "legacy-session"


def test_rollover_repository_reserves_one_binding_day_and_reloads_winner(tmp_path):
    engine, database = _ledger_database()
    owner, chat = _ledger_identity()
    ledger = TelegramRolloverLedger(database, KEY)
    binding = ledger.get_or_create_binding(
        owner_reference=owner,
        chat_reference=chat,
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(
            turn_lease_ref="bad",
            active_turn_ref="bad",
            turn_lease_expires_at=datetime(2026, 7, 25),
            turn_started_at=datetime(2026, 7, 24),
        )
    )
    with pytest.raises(LedgerError, match="invalid_binding_row"):
        ledger.get_binding(binding.id)
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(
            turn_lease_ref=None,
            active_turn_ref=None,
            turn_lease_expires_at=None,
            turn_started_at=None,
        )
    )
    bad_binding_id = "b1_" + "0" * 32
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(id=bad_binding_id)
    )
    with pytest.raises(LedgerError, match="invalid_binding_relationship"):
        ledger.get_binding(bad_binding_id)
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == bad_binding_id)
        .values(id=binding.id)
    )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(active_rollover_local_day="2026-02-30")
    )
    with pytest.raises(LedgerError, match="invalid_binding_row"):
        ledger.get_binding(binding.id)
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(active_rollover_local_day="2026-07-24")
    )
    database.execute(
        update(Session.__table__).where(Session.id == "session-private-id").values(owner="bob")
    )
    with pytest.raises(LedgerError, match="invalid_binding_relationship"):
        ledger.get_binding(binding.id)
    database.execute(
        update(Session.__table__).where(Session.id == "session-private-id").values(owner="alice")
    )
    first = ledger.reserve_or_get_rollover(
        binding_id=binding.id,
        rollover_local_day="2026-07-25",
        expected_generation=0,
        state=RolloverState.DEFERRED_ACTIVE_TURN,
        attempt_count=1,
        retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
        reason_code=ReasonCode.ACTIVE_TURN,
        max_attempts=8,
    )
    database.commit()
    other = sessionmaker(bind=engine, autoflush=False)()
    other.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=1)
    )
    reloaded = TelegramRolloverLedger(other, KEY).reserve_or_get_rollover(
        binding_id=binding.id,
        rollover_local_day="2026-07-25",
        expected_generation=0,
        state=RolloverState.DEFERRED_ACTIVE_TURN,
        attempt_count=1,
        retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
        reason_code=ReasonCode.ACTIVE_TURN,
        max_attempts=8,
    )
    assert reloaded.id == first.id
    assert reloaded.binding_id == binding.id
    assert reloaded.old_session_id == "session-private-id"
    assert reloaded.status is RolloverState.DEFERRED_ACTIVE_TURN
    assert reloaded.attempt_count == 1
    assert reloaded.retry_after is not None
    assert reloaded.reason_code is ReasonCode.ACTIVE_TURN
    bad_rollover_id = "r1_" + "0" * 32
    other.execute(
        update(database_module.TelegramSessionRollover.__table__)
        .where(database_module.TelegramSessionRollover.id == first.id)
        .values(id=bad_rollover_id)
    )
    with pytest.raises(LedgerError, match="invalid_rollover_relationship"):
        TelegramRolloverLedger(other, KEY).reserve_or_get_rollover(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            reason_code=ReasonCode.ACTIVE_TURN,
            max_attempts=8,
        )
    other.execute(
        update(database_module.TelegramSessionRollover.__table__)
        .where(database_module.TelegramSessionRollover.id == bad_rollover_id)
        .values(id=first.id)
    )
    other.execute(
        Session.__table__.insert().values(
            id="session-bob",
            name="Synthetic Bob",
            endpoint_url="http://synthetic.invalid",
            model="synthetic",
            owner="bob",
        )
    )
    other.execute(
        update(database_module.TelegramSessionRollover.__table__)
        .where(database_module.TelegramSessionRollover.id == first.id)
        .values(old_session_id="session-bob")
    )
    with pytest.raises(LedgerError, match="invalid_rollover_relationship"):
        TelegramRolloverLedger(other, KEY).reserve_or_get_rollover(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            reason_code=ReasonCode.ACTIVE_TURN,
            max_attempts=8,
        )
    other.execute(
        update(database_module.TelegramSessionRollover.__table__)
        .where(database_module.TelegramSessionRollover.id == first.id)
        .values(old_session_id="session-private-id")
    )
    with pytest.raises(LedgerError, match="stale_generation_fence"):
        TelegramRolloverLedger(other, KEY).reserve_or_get_rollover(
            binding_id=binding.id,
            rollover_local_day="2026-07-26",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 26, tzinfo=timezone.utc),
            reason_code=ReasonCode.ACTIVE_TURN,
            max_attempts=8,
        )
    exhausted = TelegramRolloverLedger(other, KEY).reserve_or_get_rollover(
        binding_id=binding.id,
        rollover_local_day="2026-07-26",
        expected_generation=1,
        state=RolloverState.DEFERRED_EXHAUSTED,
        attempt_count=2,
        retry_after=datetime(2026, 7, 26, tzinfo=timezone.utc),
        reason_code=ReasonCode.RETRY_EXHAUSTED,
        max_attempts=2,
    )
    assert exhausted.status is RolloverState.DEFERRED_EXHAUSTED
    assert exhausted.attempt_count == 2
    assert len(
        other.execute(database_module.TelegramSessionRollover.__table__.select()).mappings().all()
    ) == 2

    rollback_engine, rollback_database = _ledger_database()
    rollback_owner, rollback_chat = _ledger_identity()
    TelegramRolloverLedger(rollback_database, KEY).get_or_create_binding(
        owner_reference=rollback_owner,
        chat_reference=rollback_chat,
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    rollback_database.rollback()
    with rollback_engine.connect() as fresh:
        assert fresh.execute(
            database_module.TelegramRolloverMetadata.__table__.select()
        ).mappings().all() == []
        assert fresh.execute(
            database_module.TelegramSessionBinding.__table__.select()
        ).mappings().all() == []

    connection_engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=connection_engine)
    with connection_engine.begin() as seed:
        seed.execute(
            Session.__table__.insert().values(
                id="session-private-id",
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner="alice",
            )
        )
    connection = connection_engine.connect()
    try:
        TelegramRolloverLedger(connection, KEY).get_or_create_binding(
            owner_reference=rollback_owner,
            chat_reference=rollback_chat,
            scope="normal",
            active_session_id="session-private-id",
            active_rollover_local_day="2026-07-24",
        )
        connection.rollback()
    finally:
        connection.close()
    with connection_engine.connect() as fresh:
        assert fresh.execute(
            database_module.TelegramRolloverMetadata.__table__.select()
        ).mappings().all() == []
        assert fresh.execute(
            database_module.TelegramSessionBinding.__table__.select()
        ).mappings().all() == []

    file_engine = create_engine(f"sqlite:///{tmp_path / 'ledger-rollback.db'}")
    Base.metadata.create_all(bind=file_engine)
    with file_engine.begin() as seed:
        seed.execute(
            Session.__table__.insert().values(
                id="session-private-id",
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner="alice",
            )
        )
    outer = sessionmaker(bind=file_engine, autoflush=False)()
    outer.execute(
        Session.__table__.insert().values(
            id="outer-unrelated",
            name="Outer",
            endpoint_url="http://synthetic.invalid",
            model="synthetic",
            owner="alice",
        )
    )
    bootstrap = TelegramRolloverLedger(outer, KEY)
    assert bootstrap.get_binding("b1_" + "f" * 32) is None
    loser_id = "b1_" + hmac.new(
        KEY, f"ttd07a-binding\0{rollback_owner}\0{rollback_chat}\0normal".encode(), hashlib.sha256
    ).hexdigest()[:32]
    binding_table = database_module.TelegramSessionBinding.__table__
    loser = _PreSavepointInsertDatabase(
        outer,
        lambda db: db.execute(
            binding_table.insert().values(
                id=loser_id,
                owner_ref=rollback_owner,
                chat_handle_ref=rollback_chat,
                scope="normal",
                active_session_id="session-private-id",
                active_rollover_local_day="2026-07-24",
                generation=0,
                projection_status="current",
                projection_generation=0,
            )
        ),
    )
    winner = TelegramRolloverLedger(loser, KEY).get_or_create_binding(
        owner_reference=rollback_owner,
        chat_reference=rollback_chat,
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    assert winner.id == loser_id
    assert outer.execute(Session.__table__.select().where(Session.id == "outer-unrelated")).mappings().one()
    assert outer.execute(binding_table.select()).mappings().one()["id"] == loser_id
    outer.rollback()
    with file_engine.connect() as fresh:
        assert fresh.execute(
            Session.__table__.select().where(Session.id == "outer-unrelated")
        ).mappings().all() == []
        assert fresh.execute(binding_table.select()).mappings().all() == []


def test_rollover_repository_rejects_invalid_identity_state_and_key_mismatch():
    _, database = _ledger_database()
    owner, chat = _ledger_identity()
    ledger = TelegramRolloverLedger(database, KEY)
    binding = ledger.get_or_create_binding(
        owner_reference=owner,
        chat_reference=chat,
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    with pytest.raises(LedgerError, match="invalid_scope"):
        ledger.get_or_create_binding(
            owner_reference=owner,
            chat_reference=chat,
            scope="other",
            active_session_id="session-private-id",
            active_rollover_local_day="2026-07-24",
        )
    with pytest.raises(LedgerError, match="conflicting_binding_replay"):
        ledger.get_or_create_binding(
            owner_reference=owner,
            chat_reference=chat,
            scope="normal",
            active_session_id="session-private-id",
            active_rollover_local_day="2026-07-25",
        )
    with pytest.raises(LedgerError, match="stale_generation_fence"):
        ledger.reserve_or_get_rollover(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=1,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            reason_code=ReasonCode.ACTIVE_TURN,
            max_attempts=8,
        )
    with pytest.raises(LedgerError, match="invalid_rollover_retry"):
        ledger.persist_rollover_deferral(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=0,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            max_attempts=8,
        )
    with pytest.raises(LedgerError, match="invalid_rollover_retry"):
        ledger.reserve_or_get_rollover(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            reason_code="active_turn",
            max_attempts=8,
        )
    rollover = ledger.reserve_or_get_rollover(
        binding_id=binding.id,
        rollover_local_day="2026-07-25",
        expected_generation=0,
        state=RolloverState.DEFERRED_ACTIVE_TURN,
        attempt_count=1,
        retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
        reason_code=ReasonCode.ACTIVE_TURN,
        max_attempts=8,
    )
    rollover_table = database_module.TelegramSessionRollover.__table__
    interleaved = _InterleavingDatabase(
        database,
        rollover_table,
        lambda db: db.execute(
            update(rollover_table)
            .where(rollover_table.c.id == rollover.id)
            .values(
                status=RolloverState.DEFERRED_EXHAUSTED.value,
                attempt_count=2,
                retry_after=datetime(2026, 7, 25),
                reason_code=ReasonCode.RETRY_EXHAUSTED.value,
            )
        ),
    )
    with pytest.raises(LedgerError, match="stale_row_state"):
        TelegramRolloverLedger(interleaved, KEY).persist_rollover_deferral(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            max_attempts=8,
        )
    database.execute(
        update(database_module.TelegramSessionRollover.__table__)
        .where(database_module.TelegramSessionRollover.id == rollover.id)
        .values(status=RolloverState.DEFERRED_ACTIVE_TURN.value, attempt_count=0, retry_after=None, reason_code=None)
    )
    with pytest.raises(LedgerError, match="invalid_rollover_row"):
        ledger.reserve_or_get_rollover(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            reason_code=ReasonCode.ACTIVE_TURN,
            max_attempts=8,
        )
    database.execute(
        update(database_module.TelegramSessionRollover.__table__)
        .where(database_module.TelegramSessionRollover.id == rollover.id)
        .values(
            attempt_count=1,
            retry_after=datetime(2026, 7, 25),
            reason_code=ReasonCode.ACTIVE_TURN.value,
        )
    )
    matching_generation = _InterleavingDatabase(
        database,
        rollover_table,
        lambda db: (
            db.execute(
                update(database_module.TelegramSessionBinding.__table__)
                .where(database_module.TelegramSessionBinding.id == binding.id)
                .values(generation=1)
            ),
            db.execute(
                update(rollover_table)
                .where(rollover_table.c.id == rollover.id)
                .values(
                    status=RolloverState.DEFERRED_ACTIVE_TURN.value,
                    attempt_count=1,
                    retry_after=datetime(2026, 7, 25),
                    reason_code=ReasonCode.ACTIVE_TURN.value,
                )
            ),
        ),
    )
    with pytest.raises(LedgerError, match="stale_generation_fence"):
        TelegramRolloverLedger(matching_generation, KEY).persist_rollover_deferral(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            max_attempts=8,
        )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=0)
    )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=1)
    )
    with pytest.raises(LedgerError, match="stale_generation_fence"):
        ledger.persist_rollover_deferral(
            binding_id=binding.id,
            rollover_local_day="2026-07-25",
            expected_generation=0,
            state=RolloverState.DEFERRED_ACTIVE_TURN,
            attempt_count=1,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
            max_attempts=8,
        )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=0)
    )
    with pytest.raises(LedgerError, match="reference_key_mismatch"):
        TelegramRolloverLedger(database, b"q" * 32).get_binding(binding.id)
    with pytest.raises(LedgerError, match="reference_key_mismatch"):
        TelegramRolloverLedger(database, b"short")
    database.execute(delete(database_module.TelegramRolloverMetadata.__table__))
    with pytest.raises(LedgerError, match="reference_key_mismatch"):
        ledger.get_binding(binding.id)
    assert database.execute(
        database_module.TelegramRolloverMetadata.__table__.select()
    ).mappings().all() == []


def test_turn_intake_repository_persists_retry_and_terminal_states_content_free():
    _, database = _ledger_database()
    owner, chat = _ledger_identity()
    ledger = TelegramRolloverLedger(database, KEY)
    binding = ledger.get_or_create_binding(
        owner_reference=owner,
        chat_reference=chat,
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    intake = ledger.get_or_create_turn_intake(
        owner_reference=owner,
        chat_reference=chat,
        transport_update_reference=transport_update_ref(KEY, 123, 456),
        scope="normal",
        binding_id=binding.id,
        expected_session_id="session-private-id",
        expected_generation=0,
    )
    intake_table = database_module.TelegramTurnIntake.__table__
    bad_intake_id = "t1_" + "0" * 32
    database.execute(
        update(intake_table).where(intake_table.c.id == intake.id).values(id=bad_intake_id)
    )
    with pytest.raises(LedgerError, match="invalid_turn_intake_relationship"):
        ledger.get_turn_intake(
            owner_reference=owner,
            chat_reference=chat,
            transport_update_reference=transport_update_ref(KEY, 123, 456),
        )
    database.execute(
        update(intake_table).where(intake_table.c.id == bad_intake_id).values(id=intake.id)
    )
    database.execute(
        Session.__table__.insert().values(
            id="session-bob",
            name="Synthetic Bob",
            endpoint_url="http://synthetic.invalid",
            model="synthetic",
            owner="bob",
        )
    )
    database.execute(
        update(intake_table)
        .where(intake_table.c.id == intake.id)
        .values(expected_session_id="session-bob")
    )
    with pytest.raises(LedgerError, match="invalid_turn_intake_relationship"):
        ledger.get_turn_intake(
            owner_reference=owner,
            chat_reference=chat,
            transport_update_reference=transport_update_ref(KEY, 123, 456),
        )
    database.execute(
        update(intake_table)
        .where(intake_table.c.id == intake.id)
        .values(expected_session_id="session-private-id", scope="secure")
    )
    with pytest.raises(LedgerError, match="invalid_turn_intake_relationship"):
        ledger.get_turn_intake(
            owner_reference=owner,
            chat_reference=chat,
            transport_update_reference=transport_update_ref(KEY, 123, 456),
        )
    database.execute(
        update(intake_table)
        .where(intake_table.c.id == intake.id)
        .values(scope="normal")
    )
    database.execute(
        update(database_module.TelegramTurnIntake.__table__)
        .where(database_module.TelegramTurnIntake.id == intake.id)
        .values(status=TurnIntakeState.LEASE_RETRY.value, retry_count=0, next_retry_at=None)
    )
    with pytest.raises(LedgerError, match="invalid_turn_intake_row"):
        ledger.get_or_create_turn_intake(
            owner_reference=owner,
            chat_reference=chat,
            transport_update_reference=transport_update_ref(KEY, 123, 456),
            scope="normal",
            binding_id=binding.id,
            expected_session_id="session-private-id",
            expected_generation=0,
        )
    database.execute(
        update(database_module.TelegramTurnIntake.__table__)
        .where(database_module.TelegramTurnIntake.id == intake.id)
        .values(status=TurnIntakeState.PENDING.value, retry_count=0, next_retry_at=None, reason_code=None)
    )
    interleaved = _InterleavingDatabase(
        database,
        intake_table,
        lambda db: db.execute(
            update(intake_table)
            .where(intake_table.c.id == intake.id)
            .values(status=TurnIntakeState.RUNNING.value, retry_count=0, next_retry_at=None, reason_code=None)
        ),
    )
    with pytest.raises(LedgerError, match="stale_row_state"):
        TelegramRolloverLedger(interleaved, KEY).advance_turn_intake(
            intake_id=intake.id,
            expected_generation=0,
            event=TurnIntakeEvent.LEASE_BUSY,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )
    database.execute(
        update(intake_table)
        .where(intake_table.c.id == intake.id)
        .values(status=TurnIntakeState.PENDING.value, retry_count=0, next_retry_at=None, reason_code=None)
    )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=1)
    )
    with pytest.raises(LedgerError, match="stale_generation_fence"):
        ledger.advance_turn_intake(
            intake_id=intake.id,
            expected_generation=0,
            event=TurnIntakeEvent.LEASE_BUSY,
            retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
        )
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=0)
    )
    retry = ledger.advance_turn_intake(
        intake_id=intake.id,
        expected_generation=0,
        event=TurnIntakeEvent.LEASE_BUSY,
        retry_after=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    running = ledger.advance_turn_intake(
        intake_id=intake.id, expected_generation=0, event=TurnIntakeEvent.LEASE_ACQUIRED
    )
    reply_pending = ledger.advance_turn_intake(
        intake_id=intake.id, expected_generation=0, event=TurnIntakeEvent.REPLY_PERSISTED
    )
    completed = ledger.advance_turn_intake(
        intake_id=intake.id, expected_generation=0, event=TurnIntakeEvent.REPLY_SENT
    )
    assert retry.status is TurnIntakeState.LEASE_RETRY and retry.retry_count == 1
    assert running.status is TurnIntakeState.RUNNING
    assert reply_pending.status is TurnIntakeState.REPLY_PENDING
    assert completed.status is TurnIntakeState.COMPLETED
    database.execute(
        update(database_module.TelegramSessionBinding.__table__)
        .where(database_module.TelegramSessionBinding.id == binding.id)
        .values(generation=1)
    )
    assert ledger.get_turn_intake(
        owner_reference=owner,
        chat_reference=chat,
        transport_update_reference=transport_update_ref(KEY, 123, 456),
    ) == completed
    assert ledger.get_or_create_turn_intake(
        owner_reference=owner,
        chat_reference=chat,
        transport_update_reference=transport_update_ref(KEY, 123, 456),
        scope="normal",
        binding_id=binding.id,
        expected_session_id="session-private-id",
        expected_generation=1,
    ) == completed
    for scope, binding_id, session_id in (
        ("secure", binding.id, "session-private-id"),
        ("normal", "b1_" + "0" * 32, "session-private-id"),
        ("normal", binding.id, "other-session"),
    ):
        with pytest.raises(LedgerError, match="conflicting_intake_replay"):
            ledger.get_or_create_turn_intake(
                owner_reference=owner,
                chat_reference=chat,
                transport_update_reference=transport_update_ref(KEY, 123, 456),
                scope=scope,
                binding_id=binding_id,
                expected_session_id=session_id,
                expected_generation=1,
            )
    assert ledger.advance_turn_intake(
        intake_id=intake.id, expected_generation=0, event=TurnIntakeEvent.REPLY_SENT
    ) == completed
    with pytest.raises(LedgerError, match="stale_generation_fence"):
        ledger.get_or_create_turn_intake(
            owner_reference=owner,
            chat_reference=chat,
            transport_update_reference=transport_update_ref(KEY, 124, 456),
            scope="normal",
            binding_id=binding.id,
            expected_session_id="session-private-id",
            expected_generation=0,
        )
    with pytest.raises(LedgerError, match="invalid_turn_intake_transition"):
        ledger.advance_turn_intake(
            intake_id=intake.id, expected_generation=0, event=TurnIntakeEvent.LEASE_ACQUIRED
        )


def test_db_authoritative_bridge_prefers_existing_binding_without_legacy_import(tmp_path):
    database = _a3_database(("session-alice", "alice"))
    handle = "chat_012345abcdef"
    ledger = TelegramRolloverLedger(database, KEY)
    binding = ledger.get_or_create_binding(
        owner_reference=owner_ref(KEY, "alice"),
        chat_reference=chat_handle_ref(KEY, handle),
        scope="normal",
        active_session_id="session-alice",
        active_rollover_local_day="2026-07-24",
    )
    database.commit()
    legacy = tmp_path / "telegram_session_bridge.json"
    legacy.write_text("{not-json", encoding="utf-8")
    bridge = DbAuthoritativeTelegramSessionBridge(
        database=database,
        owner="alice",
        reference_key=KEY,
        legacy_path=legacy,
        rollover_local_day="2026-07-24",
    )
    assert bridge.resolve(stable_chat_handle=handle, scope="normal") == {
        "binding_id": binding.id,
        "scope": "normal",
        "active_session_id": "session-alice",
        "generation": 0,
        "source": "database",
        "raw_identity_visible": False,
    }
    assert bridge.import_legacy_once() == ()
    assert legacy.read_text(encoding="utf-8") == "{not-json"
    # A structurally valid but unusable legacy Session for the same natural
    # key is also ignored: DB precedence is applied before Session validation.
    legacy.write_text(json.dumps({"sessions": {
        handle: {"session_id": "missing-or-cross-owner"}
    }}), encoding="utf-8")
    assert bridge.import_legacy_once() == ()
    assert legacy.read_text(encoding="utf-8") == json.dumps({"sessions": {
        handle: {"session_id": "missing-or-cross-owner"}
    }})
    # Fingerprint verification happens before bridge HMAC-reference derivation;
    # the mismatched key cannot create a second owner-scoped binding.
    with pytest.raises(TelegramRolloverBridgeError):
        DbAuthoritativeTelegramSessionBridge(
            database=database,
            owner="alice",
            reference_key=b"Q" * 32,
            legacy_path=legacy,
            rollover_local_day="2026-07-24",
        )
    assert len(database.execute(
        database_module.TelegramSessionBinding.__table__.select()
    ).mappings().all()) == 1


def test_legacy_bridge_imports_valid_normal_and_secure_slots_once(tmp_path):
    database = _a3_database(
        ("session-normal", "alice"),
        ("session-secure", "alice"),
        ("session-next", "alice"),
        ("session-fallback", "alice"),
    )
    handle = "chat_012345abcdef"
    next_handle = "chat_fedcba987654"
    fallback_handle = "chat_abcdeffedcba"
    raw_legacy_key = "123456789"
    legacy = tmp_path / "telegram_session_bridge.json"
    legacy.write_text(json.dumps({"sessions": {
        raw_legacy_key: {
            "chat_handle": handle,
            "normal_session_id": "session-normal",
            "secure_session_id": "session-secure",
        }
    }}), encoding="utf-8")
    bridge = DbAuthoritativeTelegramSessionBridge(
        database=database,
        owner="alice",
        reference_key=KEY,
        legacy_path=legacy,
        rollover_local_day="2026-07-24",
    )
    imported = bridge.import_legacy_once()
    assert {(item["stable_chat_handle"], item["scope"], item["generation"]) for item in imported} == {
        (handle, "normal", 0), (handle, "secure", 0)
    }
    database.commit()
    statuses = database.execute(
        database_module.TelegramSessionBinding.__table__.select()
    ).mappings().all()
    assert {row["projection_status"] for row in statuses} == {"stale"}
    # An existing natural key skips only that slot.  The same owner may still
    # import another missing chat/scope identity from a later legacy snapshot.
    legacy.write_text(json.dumps({"sessions": {
        handle: {"normal_session_id": "session-normal"},
        next_handle: {"normal_session_id": "session-next"},
    }}), encoding="utf-8")
    assert {(item["stable_chat_handle"], item["scope"]) for item in bridge.import_legacy_once()} == {
        (next_handle, "normal")
    }
    database.commit()
    assert bridge.import_legacy_once() == ()
    assert bridge.resolve(stable_chat_handle=handle, scope="normal")["active_session_id"] == "session-normal"
    assert bridge.resolve(stable_chat_handle=handle, scope="secure")["active_session_id"] == "session-secure"
    # Legacy-store compatibility: a non-empty ``session_id`` still supplies
    # normal when both scoped slots are explicitly present but empty.
    legacy.write_text(json.dumps({"sessions": {
        fallback_handle: {
            "session_id": "session-fallback",
            "normal_session_id": "",
            "secure_session_id": "",
        }
    }}), encoding="utf-8")
    assert {(item["stable_chat_handle"], item["scope"]) for item in bridge.import_legacy_once()} == {
        (fallback_handle, "normal")
    }
    assert bridge.resolve(stable_chat_handle=fallback_handle, scope="normal")["active_session_id"] == "session-fallback"
    database.commit()
    persisted = database.execute(database_module.TelegramSessionBinding.__table__.select()).mappings().all()
    assert raw_legacy_key not in repr(persisted)
    bindings = bridge._ledger.list_bindings_for_owner(owner_reference=owner_ref(KEY, "alice"))
    handle_by_reference = {
        chat_handle_ref(KEY, handle): handle,
        chat_handle_ref(KEY, next_handle): next_handle,
        chat_handle_ref(KEY, fallback_handle): fallback_handle,
    }
    database.commit()
    projected = bridge.project_compatibility(
        stable_handle_by_binding={
            binding.id: handle_by_reference[binding.chat_handle_ref]
            for binding in bindings
        }
    )
    assert projected["written"] is True
    database.commit()
    assert {
        row["projection_status"]
        for row in database.execute(
            database_module.TelegramSessionBinding.__table__.select()
        ).mappings().all()
    } == {"current"}


def test_legacy_bridge_rejects_malformed_missing_or_cross_owner_sessions_without_writes(tmp_path):
    handle = "chat_012345abcdef"
    cases = (
        ("{broken", (("session-alice", "alice"),)),
        (json.dumps({"sessions": {handle: {"session_id": "missing"}}}), (("session-alice", "alice"),)),
        (json.dumps({"sessions": {handle: {"session_id": "session-bob"}}}), (("session-bob", "bob"),)),
        (json.dumps({"sessions": {"bad-key": {"session_id": "session-alice"}}}), (("session-alice", "alice"),)),
        (
            json.dumps({"sessions": {
                "1": {"chat_handle": handle, "session_id": "session-alice"},
                "2": {"chat_handle": handle, "session_id": "session-alice-two"},
            }}),
            (("session-alice", "alice"), ("session-alice-two", "alice")),
        ),
    )
    for index, (source, owners) in enumerate(cases):
        database = _a3_database(*owners)
        legacy = tmp_path / f"legacy-{index}.json"
        legacy.write_text(source, encoding="utf-8")
        bridge = DbAuthoritativeTelegramSessionBridge(
            database=database,
            owner="alice",
            reference_key=KEY,
            legacy_path=legacy,
            rollover_local_day="2026-07-24",
        )
        with pytest.raises(TelegramRolloverBridgeError):
            bridge.import_legacy_once()
        assert legacy.read_text(encoding="utf-8") == source
        assert database.execute(
            database_module.TelegramSessionBinding.__table__.select()
        ).mappings().all() == []


def test_legacy_bridge_projects_stable_handles_atomically_and_blocks_multi_owner_conflict(tmp_path, monkeypatch):
    database = _a3_database(
        ("session-alice", "alice"),
        ("session-secure", "alice"),
        ("session-bob", "bob"),
    )
    handle = "chat_012345abcdef"
    second_handle = "chat_111111111111"
    alice_ref = owner_ref(KEY, "alice")
    chat_ref = chat_handle_ref(KEY, handle)
    second_chat_ref = chat_handle_ref(KEY, second_handle)
    legacy = tmp_path / "telegram_session_bridge.json"
    legacy.write_text(json.dumps({"sessions": {
        handle: {
            "normal_session_id": "session-alice",
            "secure_session_id": "session-secure",
        }
    }}), encoding="utf-8")
    bridge = DbAuthoritativeTelegramSessionBridge(
        database=database,
        owner="alice",
        reference_key=KEY,
        legacy_path=legacy,
        rollover_local_day="2026-07-24",
    )
    imported = bridge.import_legacy_once()
    assert {(item["scope"], item["generation"]) for item in imported} == {
        ("normal", 0), ("secure", 0)
    }
    bindings_by_scope = {
        binding.scope: binding
        for binding in bridge._ledger.list_bindings_for_owner(owner_reference=alice_ref)
    }
    normal = bindings_by_scope["normal"]
    secure = bindings_by_scope["secure"]
    mapping = {normal.id: handle, secure.id: handle}
    database.commit()
    before_replace_failure = legacy.read_text(encoding="utf-8")

    # Projection cannot inherit a dirty caller transaction; the caller keeps
    # commit/rollback ownership even on a refusal.
    database.execute(database_module.TelegramSessionBinding.__table__.select())
    with pytest.raises(TelegramRolloverBridgeError):
        bridge.project_compatibility(stable_handle_by_binding=mapping)
    database.rollback()

    def assert_committed_stale():
        assert {
            row["projection_status"]
            for row in database.execute(
                database_module.TelegramSessionBinding.__table__.select()
            ).mappings().all()
        } == {"stale"}
        database.commit()

    def fail_setup(*_args, **_kwargs):
        raise OSError("simulated setup failure")

    with monkeypatch.context() as patch:
        patch.setattr("plugins.telegram.stores.tempfile.mkstemp", fail_setup)
        with pytest.raises(TelegramRolloverBridgeError):
            bridge.project_compatibility(stable_handle_by_binding=mapping)
    database.rollback()
    assert legacy.read_text(encoding="utf-8") == before_replace_failure
    assert_committed_stale()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    with monkeypatch.context() as patch:
        patch.setattr("plugins.telegram.stores.os.replace", fail_replace)
        with pytest.raises(TelegramRolloverBridgeError):
            bridge.project_compatibility(stable_handle_by_binding=mapping)
    database.rollback()
    assert legacy.read_text(encoding="utf-8") == before_replace_failure
    assert_committed_stale()

    original_list_bindings = bridge._ledger.list_bindings_for_owner
    with monkeypatch.context() as patch:
        patch.setattr(
            bridge._ledger,
            "list_bindings_for_owner",
            lambda **kwargs: tuple(reversed(original_list_bindings(**kwargs))),
        )
        result = bridge.project_compatibility(stable_handle_by_binding=mapping)
    assert result["written"] is True
    database.commit()
    projected = json.loads(legacy.read_text(encoding="utf-8"))
    assert set(projected["sessions"]) == {handle}
    assert projected["sessions"][handle]["active_session_id"] == "session-alice"
    assert projected["sessions"][handle]["last_selected_scope"] == "normal"
    assert "h1_" not in legacy.read_text(encoding="utf-8")
    before_conflict = legacy.read_text(encoding="utf-8")
    assert {
        row["projection_status"]
        for row in database.execute(
            database_module.TelegramSessionBinding.__table__.select()
        ).mappings().all()
    } == {"current"}
    database.commit()
    bridge._ledger.get_or_create_binding(
        owner_reference=owner_ref(KEY, "bob"),
        chat_reference=chat_ref,
        scope="normal",
        active_session_id="session-bob",
        active_rollover_local_day="2026-07-24",
    )
    alice_second = bridge._ledger.get_or_create_binding(
        owner_reference=alice_ref,
        chat_reference=second_chat_ref,
        scope="normal",
        active_session_id="session-alice",
        active_rollover_local_day="2026-07-24",
    )
    bridge._ledger.get_or_create_binding(
        owner_reference=owner_ref(KEY, "bob"),
        chat_reference=second_chat_ref,
        scope="normal",
        active_session_id="session-bob",
        active_rollover_local_day="2026-07-24",
    )
    database.commit()
    blocked = bridge.project_compatibility(
        stable_handle_by_binding={
            normal.id: handle,
            secure.id: handle,
            alice_second.id: second_handle,
        }
    )
    database.commit()
    assert blocked == {"status": "blocked_multi_owner", "written": False, "raw_identity_visible": False}
    assert legacy.read_text(encoding="utf-8") == before_conflict
    statuses = database.execute(
        database_module.TelegramSessionBinding.__table__.select()
    ).mappings().all()
    blocked_alice = [
        row for row in statuses
        if row["owner_ref"] == alice_ref and row["chat_handle_ref"] in {chat_ref, second_chat_ref}
    ]
    assert len(blocked_alice) == 3
    assert {row["projection_status"] for row in blocked_alice} == {"blocked_multi_owner"}


def _a4_service_binding(database, *, owner="alice", scope="normal", session_id="session-old"):
    ledger = TelegramRolloverLedger(database, KEY)
    binding = ledger.get_or_create_binding(
        owner_reference=owner_ref(KEY, owner),
        chat_reference=chat_handle_ref(KEY, "chat_012345abcdef"),
        scope=scope,
        active_session_id=session_id,
        active_rollover_local_day="2026-07-23",
    )
    database.commit()
    return binding


def _a4_service(database):
    return AtomicTelegramSessionRolloverService(
        database=database,
        config=RolloverConfig(enabled=True, reference_key=KEY),
    )


def test_atomic_rollover_creates_one_session_archives_old_and_advances_binding():
    database = _a3_database(("session-old", "alice"))
    binding = _a4_service_binding(database)
    TelegramRolloverLedger(database, KEY).reserve_or_get_rollover(
        binding_id=binding.id,
        rollover_local_day="2026-07-24",
        expected_generation=0,
        state=RolloverState.DEFERRED_ACTIVE_TURN,
        attempt_count=1,
        retry_after=datetime.now(timezone.utc) + timedelta(minutes=5),
        reason_code=ReasonCode.ACTIVE_TURN,
        max_attempts=8,
    )
    database.add(ChatMessage(id="old-message", session_id="session-old", role="user", content="synthetic"))
    database.execute(update(Session.__table__).where(Session.__table__.c.id == "session-old").values(
        headers={"Authorization": "never-copy"}, rag=True, folder="never-copy",
        is_important=True, mode="agent", crew_member_id="never-copy",
        total_input_tokens=91, total_output_tokens=17,
    ))
    database.commit()
    database.execute(Session.__table__.select())
    with pytest.raises(LedgerError, match="rollover_requires_clean_transaction"):
        _a4_service(database).rotate_binding(
            binding_id=binding.id, rollover_local_day="2026-07-24", replacement_session_id="dirty-refusal"
        )
    database.rollback()
    result = _a4_service(database).rotate_binding(
        binding_id=binding.id,
        rollover_local_day="2026-07-24",
        replacement_session_id="session-replacement",
    )
    assert result.status == "committed"
    database.commit()
    rows = database.execute(Session.__table__.select()).mappings().all()
    assert {row["id"] for row in rows} == {"session-old", "session-replacement"}
    assert next(row for row in rows if row["id"] == "session-old")["archived"] is True
    replacement = next(row for row in rows if row["id"] == "session-replacement")
    assert replacement["headers"] == {} and replacement["rag"] is False
    assert replacement["folder"] is None and replacement["is_important"] is False
    assert replacement["mode"] is None and replacement["crew_member_id"] is None
    assert replacement["total_input_tokens"] == 0 and replacement["total_output_tokens"] == 0
    messages = database.execute(ChatMessage.__table__.select()).mappings().all()
    assert [message["session_id"] for message in messages] == ["session-old"]
    persisted = database.execute(database_module.TelegramSessionBinding.__table__.select()).mappings().one()
    assert persisted["active_session_id"] == "session-replacement"
    assert persisted["generation"] == 1 and persisted["projection_status"] == "stale"
    rollover = database.execute(database_module.TelegramSessionRollover.__table__.select()).mappings().one()
    assert rollover["status"] == "committed" and rollover["new_session_id"] == "session-replacement"
    fresh = sessionmaker(bind=database.get_bind(), autoflush=False)()
    try:
        assert fresh.execute(Session.__table__.select().where(Session.__table__.c.id == "session-replacement")).mappings().one()["archived"] is False
    finally:
        fresh.close()


def test_atomic_rollover_uniqueness_loser_reloads_winner_without_second_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rollover-winner.sqlite'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    database = factory()
    database.add(Session(
        id="session-old", name="Synthetic", endpoint_url="http://synthetic.invalid",
        model="synthetic", owner="alice",
    ))
    database.commit()
    binding = _a4_service_binding(database)
    assert _a4_service(database).rotate_binding(
        binding_id=binding.id, rollover_local_day="2026-07-24", replacement_session_id="session-winner"
    ).status == "committed"
    loser_database = factory()
    with pytest.raises(LedgerError, match="database_busy"):
        _a4_service(loser_database).rotate_binding(
            binding_id=binding.id, rollover_local_day="2026-07-24", replacement_session_id="session-loser"
        )
    database.commit()
    loser_database.rollback()
    loser_database.execute(update(Session.__table__).where(Session.__table__.c.id == "session-winner").values(owner="bob"))
    loser_database.commit()
    winner = _a4_service(loser_database).rotate_binding(
        binding_id=binding.id, rollover_local_day="2026-07-24", replacement_session_id="session-loser"
    )
    assert winner.status == "committed" and winner.new_session_id == "session-winner"
    loser_database.commit()
    assert {row["id"] for row in loser_database.execute(Session.__table__.select()).mappings().all()} == {
        "session-old", "session-winner"
    }
    loser_database.close()
    database.close()


def test_atomic_rollover_rejects_owner_scope_or_security_mismatch_without_mutation():
    owner_mismatch = _a3_database(("session-old", "alice"))
    binding = _a4_service_binding(owner_mismatch)
    owner_mismatch.execute(update(Session.__table__).where(Session.__table__.c.id == "session-old").values(owner="bob"))
    owner_mismatch.commit()
    blocked = _a4_service(owner_mismatch).rotate_binding(
        binding_id=binding.id, rollover_local_day="2026-07-24", replacement_session_id="never-owner"
    )
    owner_mismatch.commit()
    assert blocked.status == "blocked_invalid_binding"
    assert owner_mismatch.execute(Session.__table__.select()).mappings().all()[0]["archived"] is False

    secure = _a3_database(("session-old", "alice"))
    secure_binding = _a4_service_binding(secure, scope="secure")
    blocked_secure = _a4_service(secure).rotate_binding(
        binding_id=secure_binding.id, rollover_local_day="2026-07-24", replacement_session_id="never-secure"
    )
    secure.commit()
    assert blocked_secure.status == "blocked_security_policy"
    assert {row["id"] for row in secure.execute(Session.__table__.select()).mappings().all()} == {"session-old"}

    secure_valid = _a3_database(("session-old", "alice"))
    secure_valid.execute(update(Session.__table__).where(Session.__table__.c.id == "session-old").values(
        endpoint_url="http://host.docker.internal:11434/v1", model="synthetic"
    ))
    secure_valid.commit()
    valid_binding = _a4_service_binding(secure_valid, scope="secure")
    assert _a4_service(secure_valid).rotate_binding(
        binding_id=valid_binding.id, rollover_local_day="2026-07-24", replacement_session_id="secure-valid"
    ).status == "committed"

    tampered = _a3_database(("session-old", "alice"))
    tampered_binding = _a4_service_binding(tampered)
    tampered.execute(insert(database_module.TelegramSessionRollover.__table__).values(
        id="r1_" + "f" * 32,
        binding_id=tampered_binding.id,
        rollover_local_day="2026-07-24",
        status="deferred_active_turn",
        old_session_id="session-old",
        attempt_count=1,
        retry_after=datetime.now(timezone.utc) + timedelta(minutes=5),
        reason_code="active_turn",
    ))
    tampered.commit()
    with pytest.raises(LedgerError, match="invalid_rollover_identity"):
        _a4_service(tampered).rotate_binding(
            binding_id=tampered_binding.id, rollover_local_day="2026-07-24", replacement_session_id="never-tampered"
        )
    tampered.rollback()


def test_atomic_rollover_rolls_back_session_binding_archive_and_terminal_row_together():
    database = _a3_database(("session-old", "alice"))
    binding = _a4_service_binding(database)
    assert _a4_service(database).rotate_binding(
        binding_id=binding.id, rollover_local_day="2026-07-24", replacement_session_id="rolled-back"
    ).status == "committed"
    database.rollback()
    persisted = database.execute(database_module.TelegramSessionBinding.__table__.select()).mappings().one()
    assert persisted["active_session_id"] == "session-old" and persisted["generation"] == 0
    old = database.execute(Session.__table__.select().where(Session.__table__.c.id == "session-old")).mappings().one()
    assert old["archived"] is False
    assert {row["id"] for row in database.execute(Session.__table__.select()).mappings().all()} == {"session-old"}
    assert database.execute(database_module.TelegramSessionRollover.__table__.select()).mappings().all() == []


def test_rollover_config_is_default_off_and_invalid_values_fail_closed():
    default = RolloverConfig.from_mapping({})
    assert default.enabled is False
    assert default.timezone.key == "Europe/Berlin"
    assert default.boundary.hour == 4

    enabled = RolloverConfig.from_mapping(
        {"TELEGRAM_SESSION_ROLLOVER_ENABLED": "true", "TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY": KEY}
    )
    assert enabled.enabled is True
    assert KEY.decode() not in repr(enabled)

    for invalid in (
        {"TELEGRAM_SESSION_ROLLOVER_ENABLED": "yes"},
        {"TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY": None},
        {"TELEGRAM_SESSION_ROLLOVER_TIMEZONE": "Not/AZone"},
        {"TELEGRAM_SESSION_ROLLOVER_BOUNDARY": "4:00"},
        {"TELEGRAM_SESSION_ROLLOVER_MAX_ATTEMPTS": "25"},
        {"TELEGRAM_SESSION_ROLLOVER_RETRY_SECONDS": "59"},
        {"TELEGRAM_SESSION_TURN_LEASE_SECONDS": "14401"},
    ):
        values = {
            "TELEGRAM_SESSION_ROLLOVER_ENABLED": "true",
            "TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY": KEY,
            **invalid,
        }
        invalid_config = RolloverConfig.from_mapping(values)
        assert invalid_config.enabled is False
        assert invalid_config.invalid_reason
    invalid_mapping = RolloverConfig.from_mapping(object())
    assert invalid_mapping.enabled is False
    assert invalid_mapping.invalid_reason == "invalid_mapping"


def test_rollover_local_day_handles_boundary_dst_and_missed_days():
    config = RolloverConfig.from_mapping({"TELEGRAM_SESSION_ROLLOVER_BOUNDARY": "04:00"})
    assert rollover_local_day(datetime(2026, 1, 2, 2, 0, tzinfo=timezone.utc), config) == "2026-01-01"
    assert rollover_local_day(datetime(2026, 1, 2, 3, 0, tzinfo=timezone.utc), config) == "2026-01-02"
    # The DST jump in Berlin skips local 02:00, but each observed instant still
    # maps deterministically through its actual local wall clock.
    assert rollover_local_day(datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc), config) == "2026-03-28"
    assert rollover_local_day(datetime(2026, 3, 29, 2, 30, tzinfo=timezone.utc), config) == "2026-03-29"
    assert rollover_is_due("2026-03-25", datetime(2026, 3, 29, 2, 30, tzinfo=timezone.utc), config)
    assert not rollover_is_due("2026-03-29", datetime(2026, 3, 29, 2, 30, tzinfo=timezone.utc), config)
    assert rollover_local_day(datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), config) == "2026-10-24"
    assert rollover_local_day(datetime(2026, 10, 25, 3, 30, tzinfo=timezone.utc), config) == "2026-10-25"
    custom_boundary = RolloverConfig.from_mapping({"TELEGRAM_SESSION_ROLLOVER_BOUNDARY": "03:30"})
    assert rollover_local_day(datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc), custom_boundary) == "2026-10-25"
    with pytest.raises(ValueError, match="aware_datetime_required"):
        rollover_local_day(datetime(2026, 1, 1, 4, 0), config)


def test_rollover_state_machine_retries_without_permanent_suppression():
    config = RolloverConfig.from_mapping(
        {
            "TELEGRAM_SESSION_ROLLOVER_ENABLED": "true",
            "TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY": KEY,
            "TELEGRAM_SESSION_ROLLOVER_MAX_ATTEMPTS": "2",
            "TELEGRAM_SESSION_ROLLOVER_RETRY_SECONDS": "60",
        }
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = advance_rollover_state(
        RolloverRecord(), event=RolloverEvent.ACTIVE_TURN, now=now, config=config, lease_expires_at=now + timedelta(hours=1)
    )
    assert first.record.state is RolloverState.DEFERRED_ACTIVE_TURN
    before_retry = advance_rollover_state(first.record, event=RolloverEvent.ACTIVE_TURN, now=now + timedelta(seconds=1), config=config, lease_expires_at=now + timedelta(hours=1))
    assert before_retry.record == first.record
    exhausted = advance_rollover_state(first.record, event=RolloverEvent.ACTIVE_TURN, now=now + timedelta(seconds=60), config=config, lease_expires_at=now + timedelta(hours=1))
    assert exhausted.record.state is RolloverState.DEFERRED_EXHAUSTED
    still_exhausted = advance_rollover_state(exhausted.record, event=RolloverEvent.ACTIVE_TURN, now=now + timedelta(minutes=2), config=config, lease_expires_at=now + timedelta(hours=1))
    assert still_exhausted.record == exhausted.record
    released = advance_rollover_state(exhausted.record, event=RolloverEvent.TURN_RELEASED, now=now + timedelta(hours=2), config=config)
    assert released.record.state is RolloverState.COMMITTED
    assert released.commit_eligible is True
    recovered = advance_rollover_state(
        first.record,
        event=RolloverEvent.LEASE_EXPIRED,
        now=now + timedelta(hours=2),
        config=config,
        lease_expires_at=now + timedelta(hours=1),
        matching_in_process_turn_present=False,
    )
    assert recovered.record.reason_code is ReasonCode.EXPIRED_TURN_LEASE_RECOVERED
    for kwargs in (
        {},
        {"lease_expires_at": now + timedelta(hours=3), "matching_in_process_turn_present": False},
        {"lease_expires_at": now + timedelta(hours=1), "matching_in_process_turn_present": True},
    ):
        with pytest.raises(ValueError, match="invalid_expired_lease_recovery"):
            advance_rollover_state(first.record, event=RolloverEvent.LEASE_EXPIRED, now=now + timedelta(hours=2), config=config, **kwargs)
    with pytest.raises(ValueError, match="invalid_rollover_config"):
        advance_rollover_state(RolloverRecord(), event=RolloverEvent.READY, now=now, config=RolloverConfig())
    parser_invalid = RolloverConfig.from_mapping({"TELEGRAM_SESSION_ROLLOVER_ENABLED": "true"})
    with pytest.raises(ValueError, match="invalid_rollover_config"):
        advance_rollover_state(RolloverRecord(), event=RolloverEvent.READY, now=now, config=parser_invalid)
    for malformed_direct in (
        RolloverConfig(enabled=1, reference_key=KEY),
        RolloverConfig(enabled=True, reference_key=b"short"),
        RolloverConfig(enabled=True, reference_key=KEY, timezone=timezone.utc),
        RolloverConfig(enabled=True, reference_key=KEY, boundary=time(4, 0, 1)),
        RolloverConfig(enabled=True, reference_key=KEY, max_attempts=25),
        RolloverConfig(enabled=True, reference_key=KEY, retry_seconds=1),
        RolloverConfig(enabled=True, reference_key=KEY, turn_lease_seconds=1),
        RolloverConfig(enabled=True, reference_key=KEY, continuity_enabled=1),
    ):
        with pytest.raises(ValueError, match="invalid_rollover_config"):
            advance_rollover_state(RolloverRecord(), event=RolloverEvent.READY, now=now, config=malformed_direct)
    with pytest.raises(ValueError, match="invalid_rollover_state"):
        advance_rollover_state(RolloverRecord(state="unreviewed"), event=RolloverEvent.READY, now=now, config=config)
    with pytest.raises(ValueError, match="invalid_active_turn_lease"):
        advance_rollover_state(RolloverRecord(), event=RolloverEvent.ACTIVE_TURN, now=now, config=config)
    with pytest.raises(ValueError, match="invalid_active_turn_lease"):
        advance_rollover_state(RolloverRecord(), event=RolloverEvent.ACTIVE_TURN, now=now, config=config, lease_expires_at=now)
    with pytest.raises(ValueError, match="invalid_rollover_transition"):
        advance_rollover_state(first.record, event=RolloverEvent.INVALID_BINDING, now=now, config=config)
    malformed_deferred = RolloverRecord(RolloverState.DEFERRED_ACTIVE_TURN, 0, None, None)
    with pytest.raises(ValueError, match="invalid_rollover_record_shape"):
        advance_rollover_state(malformed_deferred, event=RolloverEvent.READY, now=now, config=config)
    malformed_recovered_commit = RolloverRecord(
        RolloverState.COMMITTED, 0, None, ReasonCode.EXPIRED_TURN_LEASE_RECOVERED
    )
    with pytest.raises(ValueError, match="invalid_rollover_record_shape"):
        advance_rollover_state(malformed_recovered_commit, event=RolloverEvent.READY, now=now, config=config)
    blocked = advance_rollover_state(RolloverRecord(), event=RolloverEvent.INVALID_BINDING, now=now, config=config)
    assert advance_rollover_state(blocked.record, event=RolloverEvent.READY, now=now, config=config).record == blocked.record
    with pytest.raises(ValueError, match="invalid_rollover_transition"):
        advance_rollover_state(blocked.record, event="ready", now=now, config=config)


def test_rollover_refs_and_evidence_are_keyed_bounded_and_content_free():
    owner = owner_ref(KEY, "  ALIce ")
    chat = chat_handle_ref(KEY, "chat_a1b2c3d4")
    session = session_ref(KEY, "session-private-id")
    update = transport_update_ref(KEY, 123, None)
    assert owner == owner_ref(KEY, "alice")
    expected_owner = "h1_" + hmac.new(KEY, b"ttd07a-owner\0alice", hashlib.sha256).hexdigest()[:32]
    assert owner == expected_owner
    assert len({owner_ref(KEY, "same"), chat_handle_ref(KEY, "same"), session_ref(KEY, "same")}) == 3
    assert len(owner) == 35 and len(chat) == 35 and len(session) == 35
    assert len({owner, chat, session, update}) == 4
    assert transport_update_ref(KEY, 0, None) != transport_update_ref(KEY, None, 0)
    with pytest.raises(ValueError):
        owner_ref(KEY, "x" * 513)

    evidence = build_rollover_evidence(
        owner_ref=owner,
        chat_handle_ref=chat,
        session_ref=session,
        scope="normal",
        rollover_local_day="2026-01-01",
        state="committed",
        attempt_count=1,
        raw_content_absent=True,
        raw_identity_absent=True,
    )
    assert "ALIce" not in repr(dict(evidence))
    assert build_rollover_evidence(state=TurnIntakeState.REPLY_PENDING)["state"] == "reply_pending"
    with pytest.raises(ValueError, match="forbidden_evidence_field"):
        build_rollover_evidence(owner_ref=owner, prompt="secret")
    with pytest.raises(ValueError, match="invalid_evidence_ref"):
        build_rollover_evidence(owner_ref="b1_" + "a" * 32)
    assert build_rollover_evidence(binding_ref="b1_" + "a" * 32)["binding_ref"].startswith("b1_")
    for field in ("raw_content_absent", "raw_identity_absent"):
        with pytest.raises(ValueError, match="invalid_evidence_boolean"):
            build_rollover_evidence(**{field: False})


def test_turn_intake_states_fail_closed_on_indeterminate():
    turn_ref = owner_ref(KEY, "turn-marker")
    exact = reconcile_running_turn(
        turn_ref,
        [TurnMessageMarker("user", turn_ref), TurnMessageMarker("assistant", turn_ref)],
    )
    assert exact.state is TurnIntakeState.REPLY_PENDING
    assert exact.automatic_replay_allowed is False
    assert advance_turn_intake_state(TurnIntakeState.PENDING, TurnIntakeEvent.LEASE_BUSY) is TurnIntakeState.LEASE_RETRY
    assert advance_turn_intake_state(TurnIntakeState.LEASE_RETRY, TurnIntakeEvent.LEASE_BUSY) is TurnIntakeState.LEASE_RETRY
    running = advance_turn_intake_state(TurnIntakeState.LEASE_RETRY, TurnIntakeEvent.LEASE_ACQUIRED)
    assert running is TurnIntakeState.RUNNING
    assert advance_turn_intake_state(TurnIntakeState.PENDING, TurnIntakeEvent.LEASE_ACQUIRED) is TurnIntakeState.RUNNING
    replied = advance_turn_intake_state(running, TurnIntakeEvent.REPLY_PERSISTED)
    completed = advance_turn_intake_state(replied, TurnIntakeEvent.REPLY_SENT)
    assert completed is TurnIntakeState.COMPLETED
    assert advance_turn_intake_state(completed, TurnIntakeEvent.REPLY_SENT) is completed
    indeterminate = advance_turn_intake_state(running, TurnIntakeEvent.INDETERMINATE)
    assert indeterminate is TurnIntakeState.INDETERMINATE_TURN
    assert advance_turn_intake_state(indeterminate, TurnIntakeEvent.INDETERMINATE) is indeterminate
    blocked = advance_turn_intake_state(TurnIntakeState.PENDING, TurnIntakeEvent.INVALID_BINDING)
    assert blocked is TurnIntakeState.BLOCKED_INVALID_BINDING
    assert advance_turn_intake_state(blocked, TurnIntakeEvent.INVALID_BINDING) is blocked
    security_blocked = advance_turn_intake_state(TurnIntakeState.PENDING, TurnIntakeEvent.SECURITY_POLICY_BLOCKED)
    assert security_blocked is TurnIntakeState.BLOCKED_SECURITY_POLICY
    assert advance_turn_intake_state(security_blocked, TurnIntakeEvent.SECURITY_POLICY_BLOCKED) is security_blocked
    with pytest.raises(ValueError, match="invalid_turn_intake_transition"):
        advance_turn_intake_state(completed, TurnIntakeEvent.LEASE_ACQUIRED)
    with pytest.raises(ValueError, match="invalid_turn_intake_transition"):
        advance_turn_intake_state(TurnIntakeState.REPLY_PENDING, TurnIntakeEvent.LEASE_BUSY)
    with pytest.raises(ValueError, match="invalid_turn_intake_transition"):
        advance_turn_intake_state(TurnIntakeState.RUNNING, TurnIntakeEvent.INVALID_BINDING)
    with pytest.raises(ValueError, match="invalid_turn_intake_transition"):
        advance_turn_intake_state(TurnIntakeState.REPLY_PENDING, TurnIntakeEvent.SECURITY_POLICY_BLOCKED)
    with pytest.raises(ValueError, match="invalid_turn_intake_transition"):
        advance_turn_intake_state(indeterminate, TurnIntakeEvent.REPLY_SENT)
    with pytest.raises(ValueError, match="invalid_turn_intake_transition"):
        advance_turn_intake_state(blocked, TurnIntakeEvent.LEASE_ACQUIRED)
    for markers in ([], [TurnMessageMarker("user", turn_ref)], [TurnMessageMarker("assistant", turn_ref), TurnMessageMarker("assistant", turn_ref)]):
        result = reconcile_running_turn(turn_ref, markers)
        assert result.state is TurnIntakeState.INDETERMINATE_TURN
        assert result.reason_code is ReasonCode.INDETERMINATE_TURN_PAIR
        assert result.automatic_replay_allowed is False
    for markers in (None, [object(), object()], [TurnMessageMarker([], turn_ref), TurnMessageMarker("assistant", turn_ref)]):
        result = reconcile_running_turn(turn_ref, markers)
        assert result.state is TurnIntakeState.INDETERMINATE_TURN


def test_turn_coordinator_uses_one_operation_session_and_fences_lifecycle():
    factory, binding, coordinator, _now = _turn_coordinator_fixture()

    acquired = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=41, message_id=9, scope="normal"
    )
    assert acquired.status == "acquired"
    assert acquired.intake is not None and acquired.intake.status is TurnIntakeState.RUNNING
    assert isinstance(acquired.lease, TelegramTurnLease)
    assert acquired.lease is not None
    assert acquired.lease.lease_ref.startswith("h1_")
    assert acquired.lease.token not in repr(acquired.lease)

    busy = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=42, message_id=10, scope="normal"
    )
    assert busy.status == "lease_busy_local_active"
    assert busy.intake is not None and busy.intake.status is TurnIntakeState.LEASE_RETRY

    renewed = coordinator.renew_turn(acquired.lease)
    assert renewed is not None and renewed.expires_at >= acquired.lease.expires_at
    reply_pending = coordinator.mark_reply_persisted(renewed)
    assert reply_pending.status is TurnIntakeState.REPLY_PENDING
    completed = coordinator.complete_and_release(renewed)
    assert completed.status is TurnIntakeState.COMPLETED

    database = factory()
    try:
        row = database.execute(
            database_module.TelegramSessionBinding.__table__.select().where(
                database_module.TelegramSessionBinding.id == binding.id
            )
        ).mappings().one()
        assert row["turn_lease_ref"] is None
        assert row["active_turn_ref"] is None
        assert row["turn_lease_expires_at"] is None
        assert row["turn_started_at"] is None
    finally:
        database.close()

    duplicate = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=41, message_id=9, scope="normal"
    )
    assert duplicate.status == "duplicate_completed"
    assert coordinator.renew_turn(renewed) is None
    assert coordinator.release_turn(renewed) is False
    forged = TelegramTurnLease(
        binding_id=renewed.binding_id,
        generation=renewed.generation,
        intake_id=renewed.intake_id,
        lease_ref="h1_" + "0" * 32,
        expires_at=renewed.expires_at,
        token=renewed.token,
    )
    assert coordinator.renew_turn(forged) is None
    assert coordinator.release_turn(forged) is False


def test_turn_coordinator_rolls_back_and_closes_failed_operation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    events = []

    class TrackingOrmSession(OrmSession):
        def commit(self):
            events.append("commit")
            return super().commit()

        def rollback(self):
            events.append("rollback")
            return super().rollback()

        def close(self):
            events.append("close")
            return super().close()

    factory = sessionmaker(bind=engine, class_=TrackingOrmSession, autoflush=False)
    coordinator = TelegramTurnCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY, turn_lease_seconds=60),
        now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    with pytest.raises(LedgerError, match="binding_not_found"):
        coordinator.acquire_turn(
            owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=1, message_id=1, scope="normal"
        )
    assert events == ["rollback", "close"]


def test_disabled_turn_coordinator_never_opens_a_database_session():
    opened = []

    def factory():
        opened.append(True)
        raise AssertionError("disabled coordinator must not open a session")

    coordinator = TelegramTurnCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=False, reference_key=KEY),
        now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    result = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=1, message_id=1, scope="normal"
    )
    assert result.status == "disabled"
    assert opened == []


def test_turn_coordinator_commits_and_closes_successful_operation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    events = []

    class TrackingOrmSession(OrmSession):
        def commit(self):
            events.append("commit")
            return super().commit()

        def rollback(self):
            events.append("rollback")
            return super().rollback()

        def close(self):
            events.append("close")
            return super().close()

    factory = sessionmaker(bind=engine, class_=TrackingOrmSession, autoflush=False)
    database = factory()
    database.add(Session(id="session-private-id", name="Synthetic", endpoint_url="http://synthetic.invalid", model="synthetic", owner="alice"))
    ledger = TelegramRolloverLedger(database, KEY)
    ledger.get_or_create_binding(
        owner_reference=owner_ref(KEY, "alice"),
        chat_reference=chat_handle_ref(KEY, "chat_a1b2c3d4"),
        scope="normal",
        active_session_id="session-private-id",
        active_rollover_local_day="2026-07-24",
    )
    database.commit()
    database.close()
    events.clear()
    coordinator = TelegramTurnCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY, turn_lease_seconds=60),
        now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=1, message_id=1, scope="normal"
    ).status == "acquired"
    assert events == ["commit", "close"]


def test_turn_coordinator_reconciles_only_an_expired_nonlocal_running_turn(monkeypatch):
    factory, binding, coordinator, now = _turn_coordinator_fixture()
    acquired = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=51, message_id=11, scope="normal"
    )
    assert acquired.lease is not None and acquired.intake is not None
    database = factory()
    try:
        database.execute(
            update(database_module.TelegramSessionBinding.__table__)
            .where(database_module.TelegramSessionBinding.id == binding.id)
            .values(turn_lease_expires_at=(now - timedelta(seconds=1)).replace(tzinfo=None))
        )
        database.commit()
    finally:
        database.close()

    import src.telegram_session_rollover as rollover_module

    rollover_module._ACTIVE_TELEGRAM_TURN_IDS.discard(acquired.intake.id)
    reconciled = coordinator.reconcile_crashed_turn(
        owner="alice",
        stable_chat_handle="chat_a1b2c3d4",
        update_id=51,
        message_id=11,
        markers=[
            TurnMessageMarker("user", acquired.intake.id),
            TurnMessageMarker("assistant", acquired.intake.id),
        ],
    )
    assert reconciled.status == "reconciled_reply_pending"
    assert reconciled.intake is not None and reconciled.intake.status is TurnIntakeState.REPLY_PENDING


def test_turn_coordinator_never_steals_an_expired_locally_active_turn():
    factory, binding, coordinator, now = _turn_coordinator_fixture()
    winner = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=61, message_id=12, scope="normal"
    )
    assert winner.lease is not None
    database = factory()
    try:
        database.execute(
            update(database_module.TelegramSessionBinding.__table__)
            .where(database_module.TelegramSessionBinding.id == binding.id)
            .values(turn_lease_expires_at=(now - timedelta(seconds=1)).replace(tzinfo=None))
        )
        database.commit()
    finally:
        database.close()
    blocked = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=62, message_id=13, scope="normal"
    )
    assert blocked.status == "lease_busy_local_active"
    assert blocked.intake is not None and blocked.intake.status is TurnIntakeState.LEASE_RETRY
    database = factory()
    try:
        row = database.execute(
            database_module.TelegramSessionBinding.__table__.select().where(
                database_module.TelegramSessionBinding.id == binding.id
            )
        ).mappings().one()
        assert row["active_turn_ref"] == winner.lease.intake_id
        assert row["turn_lease_ref"] == winner.lease.lease_ref
    finally:
        database.close()
    import src.telegram_session_rollover as rollover_module

    rollover_module._ACTIVE_TELEGRAM_TURN_IDS.discard(winner.lease.intake_id)


def test_turn_coordinator_fences_expired_nonlocal_running_turn_until_reconciliation():
    factory, binding, coordinator, now = _turn_coordinator_fixture()
    old = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=63, message_id=16, scope="normal"
    )
    assert old.lease is not None and old.intake is not None
    database = factory()
    try:
        database.execute(
            update(database_module.TelegramSessionBinding.__table__)
            .where(database_module.TelegramSessionBinding.id == binding.id)
            .values(turn_lease_expires_at=(now - timedelta(seconds=1)).replace(tzinfo=None))
        )
        database.commit()
    finally:
        database.close()
    import src.telegram_session_rollover as rollover_module

    rollover_module._ACTIVE_TELEGRAM_TURN_IDS.discard(old.intake.id)
    contender = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=64, message_id=17, scope="normal"
    )
    assert contender.status == "expired_turn_reconciliation_required"
    assert contender.intake is not None and contender.intake.status is TurnIntakeState.LEASE_RETRY
    database = factory()
    try:
        old_row = database.execute(
            database_module.TelegramTurnIntake.__table__.select().where(
                database_module.TelegramTurnIntake.id == old.intake.id
            )
        ).mappings().one()
        binding_row = database.execute(
            database_module.TelegramSessionBinding.__table__.select().where(
                database_module.TelegramSessionBinding.id == binding.id
            )
        ).mappings().one()
        assert old_row["status"] == TurnIntakeState.RUNNING.value
        assert binding_row["active_turn_ref"] == old.intake.id
        assert binding_row["turn_lease_ref"] == old.lease.lease_ref
    finally:
        database.close()
    recovered = coordinator.reconcile_crashed_turn(
        owner="alice",
        stable_chat_handle="chat_a1b2c3d4",
        update_id=63,
        message_id=16,
        markers=[TurnMessageMarker("user", old.intake.id), TurnMessageMarker("assistant", old.intake.id)],
    )
    assert recovered.status == "reconciled_reply_pending"
    resumed = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=64, message_id=17, scope="normal"
    )
    assert resumed.status == "acquired"


def test_turn_coordinator_nonlocal_unexpired_lease_uses_its_real_retry_deadline():
    factory, binding, coordinator, _now = _turn_coordinator_fixture()
    old = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=65, message_id=18, scope="normal"
    )
    assert old.lease is not None and old.intake is not None
    import src.telegram_session_rollover as rollover_module

    rollover_module._ACTIVE_TELEGRAM_TURN_IDS.discard(old.intake.id)
    contender = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=66, message_id=19, scope="normal"
    )
    assert contender.status == "lease_busy"
    assert contender.intake is not None
    assert contender.intake.status is TurnIntakeState.LEASE_RETRY
    assert contender.intake.next_retry_at == old.lease.expires_at
    database = factory()
    try:
        binding_row = database.execute(
            database_module.TelegramSessionBinding.__table__.select().where(
                database_module.TelegramSessionBinding.id == binding.id
            )
        ).mappings().one()
        assert binding_row["active_turn_ref"] == old.intake.id
        assert binding_row["turn_lease_ref"] == old.lease.lease_ref
        assert binding_row["turn_lease_expires_at"] == old.lease.expires_at
    finally:
        database.close()


def test_turn_coordinator_reconciles_non_exact_pair_to_terminal_indeterminate():
    factory, binding, coordinator, now = _turn_coordinator_fixture()
    acquired = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=71, message_id=14, scope="normal"
    )
    assert acquired.intake is not None
    database = factory()
    try:
        database.execute(
            update(database_module.TelegramSessionBinding.__table__)
            .where(database_module.TelegramSessionBinding.id == binding.id)
            .values(turn_lease_expires_at=(now - timedelta(seconds=1)).replace(tzinfo=None))
        )
        database.commit()
    finally:
        database.close()
    import src.telegram_session_rollover as rollover_module

    rollover_module._ACTIVE_TELEGRAM_TURN_IDS.discard(acquired.intake.id)
    reconciled = coordinator.reconcile_crashed_turn(
        owner="alice",
        stable_chat_handle="chat_a1b2c3d4",
        update_id=71,
        message_id=14,
        markers=[TurnMessageMarker("user", acquired.intake.id)],
    )
    assert reconciled.status == "reconciled_indeterminate"
    assert reconciled.intake is not None
    assert reconciled.intake.status is TurnIntakeState.INDETERMINATE_TURN


def test_turn_coordinator_refuses_to_reconcile_an_old_intake_after_binding_cutover():
    factory, binding, coordinator, now = _turn_coordinator_fixture()
    acquired = coordinator.acquire_turn(
        owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=81, message_id=15, scope="normal"
    )
    assert acquired.intake is not None
    database = factory()
    try:
        database.add(Session(id="replacement-session", name="Synthetic", endpoint_url="http://synthetic.invalid", model="synthetic", owner="alice"))
        database.flush()
        database.execute(
            update(database_module.TelegramSessionBinding.__table__)
            .where(database_module.TelegramSessionBinding.id == binding.id)
            .values(
                active_session_id="replacement-session",
                generation=1,
                turn_lease_expires_at=(now - timedelta(seconds=1)).replace(tzinfo=None),
            )
        )
        database.commit()
    finally:
        database.close()
    import src.telegram_session_rollover as rollover_module

    rollover_module._ACTIVE_TELEGRAM_TURN_IDS.discard(acquired.intake.id)
    refused = coordinator.reconcile_crashed_turn(
        owner="alice",
        stable_chat_handle="chat_a1b2c3d4",
        update_id=81,
        message_id=15,
        markers=[
            TurnMessageMarker("user", acquired.intake.id),
            TurnMessageMarker("assistant", acquired.intake.id),
        ],
    )
    assert refused.status == "stale_binding_fence"
    assert refused.intake is not None and refused.intake.status is TurnIntakeState.RUNNING


def test_turn_coordinator_concurrent_updates_choose_one_winner_and_one_busy(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'turn-coordinator.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(bind=engine)
        factory = sessionmaker(bind=engine, autoflush=False)
        database = factory()
        try:
            database.add(Session(id="session-private-id", name="Synthetic", endpoint_url="http://synthetic.invalid", model="synthetic", owner="alice"))
            ledger = TelegramRolloverLedger(database, KEY)
            ledger.get_or_create_binding(
                owner_reference=owner_ref(KEY, "alice"),
                chat_reference=chat_handle_ref(KEY, "chat_a1b2c3d4"),
                scope="normal",
                active_session_id="session-private-id",
                active_rollover_local_day="2026-07-24",
            )
            database.commit()
        finally:
            database.close()
        coordinator = TelegramTurnCoordinator(
            session_factory=factory,
            config=RolloverConfig(enabled=True, reference_key=KEY, turn_lease_seconds=60),
            now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
        )
        barrier = threading.Barrier(2)
        results = []

        def acquire(update_id):
            barrier.wait()
            results.append(coordinator.acquire_turn(
                owner="alice", stable_chat_handle="chat_a1b2c3d4", update_id=update_id, message_id=update_id, scope="normal"
            ).status)

        workers = [threading.Thread(target=acquire, args=(91,)), threading.Thread(target=acquire, args=(92,))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()
        assert results.count("acquired") == 1
        assert len(results) == 2
        assert next(status for status in results if status != "acquired").startswith("lease_busy")
    finally:
        engine.dispose()


def _binding_mutation_fixture(*, scope="normal"):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    database = factory()
    try:
        database.add(
            Session(
                id="binding-old-session",
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner="alice",
            )
        )
        ledger = TelegramRolloverLedger(database, KEY)
        binding = ledger.get_or_create_binding(
            owner_reference=owner_ref(KEY, "alice"),
            chat_reference=chat_handle_ref(KEY, "chat_a1b2c3d4"),
            scope=scope,
            active_session_id="binding-old-session",
            active_rollover_local_day="2026-07-24",
        )
        database.commit()
    finally:
        database.close()
    return engine, factory, binding, TelegramBindingMutationCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
    )


def test_binding_mutation_requires_explicit_non_placeholder_owner_without_opening_db():
    opened = []

    def factory():
        opened.append(True)
        raise AssertionError("invalid owner must fail before opening a database session")

    coordinator = TelegramBindingMutationCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
    )
    for owner in (None, "", " telegram "):
        assert coordinator.bind_or_create(
            telegram_owner=owner,
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-24",
        ).status == "owner_invalid"
    assert opened == []


def test_binding_mutation_disabled_never_opens_database_or_calls_creator():
    opened = []

    def factory():
        opened.append(True)
        raise AssertionError("disabled seam must not open a database session")

    coordinator = TelegramBindingMutationCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=False, reference_key=KEY),
    )
    result = coordinator.bind_or_create(
        telegram_owner="alice",
        stable_chat_handle="chat_a1b2c3d4",
        scope="normal",
        rollover_local_day="2026-07-24",
        create_session=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("creator called")),
    )
    assert result.status == "disabled"
    assert opened == []


def test_binding_mutation_creator_and_flush_failures_rollback_without_raw_errors():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    coordinator = TelegramBindingMutationCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
    )
    try:
        def creator_raises(**_kwargs):
            raise RuntimeError("provider detail must not escape")

        assert coordinator.bind_or_create(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-24",
            create_session=creator_raises,
        ).status == "session_creation_failed"

        failed_session_ids = []

        def creator_with_flush_failure(*, database, owner, session_id, **_kwargs):
            failed_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Synthetic",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner=owner,
                )
            )

            def fail_flush():
                raise RuntimeError("database implementation detail")

            database.flush = fail_flush
            return session_id

        assert coordinator.bind_or_create(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-24",
            create_session=creator_with_flush_failure,
        ).status == "session_creation_failed"
        database = factory()
        try:
            assert database.get(Session, failed_session_ids[0]) is None
            assert database.execute(database_module.TelegramSessionBinding.__table__.select()).all() == []
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_rejects_cross_owner_new_session_without_orphan():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    coordinator = TelegramBindingMutationCoordinator(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
    )
    try:
        wrong_session_ids = []

        def create_wrong_owner(*, database, session_id, **_kwargs):
            wrong_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Synthetic",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner="bob",
                )
            )
            return session_id

        result = coordinator.bind_or_create(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-24",
            create_session=create_wrong_owner,
        )
        assert result.status == "session_owner_mismatch"
        database = factory()
        try:
            assert database.get(Session, wrong_session_ids[0]) is None
            assert database.execute(database_module.TelegramSessionBinding.__table__.select()).mappings().all() == []
        finally:
            database.close()

        correct_session_ids = []

        def create_correct_owner(*, database, owner, session_id, **_kwargs):
            correct_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Synthetic",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner=owner,
                )
            )
            return session_id

        created = coordinator.bind_or_create(
            telegram_owner=" Alice ",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-24",
            create_session=create_correct_owner,
        )
        assert created.status == "bound_created"
        assert created.binding is not None and created.binding.generation == 0
        database = factory()
        try:
            row = database.execute(database_module.TelegramSessionBinding.__table__.select()).mappings().one()
            assert row["active_session_id"] == correct_session_ids[0]
            assert row["projection_status"] == "stale"
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_rejects_existing_session_return_without_orphan():
    engine, factory, binding, coordinator = _binding_mutation_fixture()
    created_session_ids = []
    try:
        def return_existing_session(*, database, owner, session_id, **_kwargs):
            created_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Discarded replacement",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner=owner,
                )
            )
            return binding.active_session_id

        result = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-25",
            create_replacement=return_existing_session,
        )
        assert result.status == "replacement_session_invalid"
        database = factory()
        try:
            assert database.get(Session, created_session_ids[0]) is None
            assert database.get(Session, binding.active_session_id).archived is False
            row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == binding.id
                )
            ).mappings().one()
            assert row["generation"] == binding.generation
            assert row["active_session_id"] == binding.active_session_id
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_lease_blocks_bind_rebind_and_secure_fallback():
    engine, factory, binding, coordinator = _binding_mutation_fixture(scope="secure")
    try:
        database = factory()
        try:
            database.execute(
                update(database_module.TelegramSessionBinding.__table__)
                .where(database_module.TelegramSessionBinding.id == binding.id)
                .values(
                    turn_lease_ref="h1_" + "a" * 32,
                    active_turn_ref="t1_" + "b" * 32,
                    turn_lease_expires_at=datetime(2026, 7, 26),
                    turn_started_at=datetime(2026, 7, 25),
                )
            )
            database.commit()
        finally:
            database.close()

        assert coordinator.bind_or_create(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="secure",
            rollover_local_day="2026-07-25",
        ).status == "lease_busy"
        for purpose in ("rebind", "secure_fallback"):
            assert coordinator.rebind(
                telegram_owner="alice",
                stable_chat_handle="chat_a1b2c3d4",
                scope="secure",
                rollover_local_day="2026-07-25",
                purpose=purpose,
                create_replacement=lambda **_kwargs: "must-not-run",
            ).status == "lease_busy"
    finally:
        engine.dispose()


def test_binding_mutation_existing_binding_rejects_changed_session_owner():
    engine, factory, binding, coordinator = _binding_mutation_fixture()
    try:
        database = factory()
        try:
            database.execute(
                update(Session.__table__)
                .where(Session.id == binding.active_session_id)
                .values(owner="bob")
            )
            database.commit()
        finally:
            database.close()
        refused = coordinator.bind_or_create(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-25",
        )
        assert refused.status == "binding_invalid"
    finally:
        engine.dispose()


def test_binding_mutation_existing_secure_binding_requires_security_policy():
    engine, factory, binding, coordinator = _binding_mutation_fixture(scope="secure")
    try:
        def validator_raises(**_kwargs):
            raise RuntimeError("policy diagnostic must not escape")

        for validator in (None, lambda **_kwargs: False, validator_raises):
            result = coordinator.bind_or_create(
                telegram_owner="alice",
                stable_chat_handle="chat_a1b2c3d4",
                scope="secure",
                rollover_local_day="2026-07-25",
                security_validator=validator,
            )
            assert result.status == "security_policy_blocked"
        database = factory()
        try:
            row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == binding.id
                )
            ).mappings().one()
            assert row["generation"] == binding.generation
            assert database.get(Session, binding.active_session_id).archived is False
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_rejects_non_ready_existing_and_replacement_sessions():
    engine, factory, binding, coordinator = _binding_mutation_fixture()
    try:
        database = factory()
        try:
            database.execute(
                update(Session.__table__)
                .where(Session.id == binding.active_session_id)
                .values(archived=True)
            )
            database.commit()
        finally:
            database.close()
        assert coordinator.bind_or_create(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-25",
        ).status == "binding_invalid"

        database = factory()
        try:
            database.execute(
                update(Session.__table__)
                .where(Session.id == binding.active_session_id)
                .values(archived=False)
            )
            database.commit()
        finally:
            database.close()

        incomplete_session_ids = []

        def incomplete_replacement(*, database, owner, session_id, **_kwargs):
            incomplete_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Incomplete",
                    endpoint_url="",
                    model="",
                    owner=owner,
                )
            )
            return session_id

        assert coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-25",
            create_replacement=incomplete_replacement,
        ).status == "session_creation_failed"
        database = factory()
        try:
            assert database.get(Session, incomplete_session_ids[0]) is None
            assert database.get(Session, binding.active_session_id).archived is False
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_rebind_rejects_invalid_day_before_creator_or_mutation():
    engine, factory, binding, coordinator = _binding_mutation_fixture()
    creator_calls = []
    try:
        result = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="not-a-local-day",
            create_replacement=lambda **_kwargs: creator_calls.append(True),
        )
        assert result.status == "invalid_rollover_local_day"
        assert creator_calls == []
        stale = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-23",
            create_replacement=lambda **_kwargs: creator_calls.append(True),
        )
        assert stale.status == "stale_rollover_day"
        assert creator_calls == []
        database = factory()
        try:
            row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == binding.id
                )
            ).mappings().one()
            assert row["generation"] == 0
            assert row["active_rollover_local_day"] == "2026-07-24"
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_secure_fallback_requires_secure_scope_before_creator():
    engine, _factory, _binding, coordinator = _binding_mutation_fixture()
    creator_calls = []
    try:
        result = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-25",
            purpose="secure_fallback",
            create_replacement=lambda **_kwargs: creator_calls.append(True),
        )
        assert result.status == "invalid_binding_mutation"
        assert creator_calls == []
    finally:
        engine.dispose()


def test_binding_mutation_rebind_commits_one_generation_and_rolls_back_failed_replacement():
    engine, factory, binding, coordinator = _binding_mutation_fixture()
    try:
        replacement_session_ids = []

        def create_replacement(*, database, owner, session_id, **_kwargs):
            replacement_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Synthetic replacement",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner=owner,
                )
            )
            return session_id

        result = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-25",
            create_replacement=create_replacement,
        )
        assert result.status == "rebound"
        assert result.binding is not None and result.binding.generation == binding.generation + 1
        database = factory()
        try:
            row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == binding.id
                )
            ).mappings().one()
            assert row["active_session_id"] == replacement_session_ids[0]
            assert row["generation"] == 1
            assert row["active_rollover_local_day"] == "2026-07-25"
            assert database.get(Session, "binding-old-session").archived is True
            assert database.get(Session, replacement_session_ids[0]).archived is False
        finally:
            database.close()

        orphan_session_ids = []

        def create_wrong_replacement(*, database, session_id, **_kwargs):
            orphan_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Synthetic wrong owner",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner="bob",
                )
            )
            return session_id

        rejected = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="normal",
            rollover_local_day="2026-07-26",
            create_replacement=create_wrong_replacement,
        )
        assert rejected.status == "session_owner_mismatch"
        database = factory()
        try:
            assert database.get(Session, orphan_session_ids[0]) is None
            row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == binding.id
                )
            ).mappings().one()
            assert row["active_session_id"] == replacement_session_ids[0]
            assert row["generation"] == 1
            assert row["active_rollover_local_day"] == "2026-07-25"
        finally:
            database.close()
    finally:
        engine.dispose()


def test_binding_mutation_secure_fallback_requires_policy_and_commits_one_replacement():
    engine, factory, binding, coordinator = _binding_mutation_fixture(scope="secure")
    try:
        secure_session_ids = []

        def create_replacement(*, database, owner, session_id, **_kwargs):
            secure_session_ids.append(session_id)
            database.add(
                Session(
                    id=session_id,
                    name="Synthetic secure replacement",
                    endpoint_url="http://synthetic.invalid",
                    model="synthetic",
                    owner=owner,
                )
            )
            return session_id

        blocked = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="secure",
            rollover_local_day="2026-07-25",
            purpose="secure_fallback",
            create_replacement=create_replacement,
        )
        assert blocked.status == "security_policy_blocked"
        database = factory()
        try:
            assert database.get(Session, secure_session_ids[0]) is None
            assert database.get(Session, "binding-old-session").archived is False
        finally:
            database.close()

        accepted = coordinator.rebind(
            telegram_owner="alice",
            stable_chat_handle="chat_a1b2c3d4",
            scope="secure",
            rollover_local_day="2026-07-25",
            purpose="secure_fallback",
            create_replacement=create_replacement,
            security_validator=lambda **_kwargs: True,
        )
        assert accepted.status == "rebound"
        assert accepted.binding is not None and accepted.binding.generation == binding.generation + 1
        database = factory()
        try:
            assert database.get(Session, "binding-old-session").archived is True
            assert database.get(Session, secure_session_ids[-1]).archived is False
        finally:
            database.close()
    finally:
        engine.dispose()


def _runtime_sweep_fixture(tmp_path, *, owner_day="2026-07-24"):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    database = factory()
    try:
        database.add_all([
            Session(
                id="runtime-alice-session",
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner="alice",
            ),
            Session(
                id="runtime-bob-session",
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner="bob",
            ),
        ])
        ledger = TelegramRolloverLedger(database, KEY)
        alice = ledger.get_or_create_binding(
            owner_reference=owner_ref(KEY, "alice"),
            chat_reference=chat_handle_ref(KEY, "chat_a1b2c3d4"),
            scope="normal",
            active_session_id="runtime-alice-session",
            active_rollover_local_day=owner_day,
        )
        bob = ledger.get_or_create_binding(
            owner_reference=owner_ref(KEY, "bob"),
            chat_reference=chat_handle_ref(KEY, "chat_b1c2d3e4"),
            scope="normal",
            active_session_id="runtime-bob-session",
            active_rollover_local_day=owner_day,
        )
        database.commit()
    finally:
        database.close()
    legacy_path = tmp_path / "telegram_session_bridge.json"
    legacy_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    runtime = build_db_authoritative_rollover_runtime(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
        telegram_owner="alice",
        legacy_path=legacy_path,
        now=lambda: now,
    )
    assert isinstance(runtime, TelegramRolloverRuntime)
    return engine, factory, alice, bob, runtime, now


def test_runtime_composition_disabled_invalid_or_missing_owner_opens_no_database():
    opened = []

    def factory():
        opened.append(True)
        raise AssertionError("invalid runtime composition must not open a database session")

    legacy_path = "not-used.json"
    assert build_db_authoritative_rollover_runtime(
        session_factory=factory,
        config=RolloverConfig(enabled=False),
        telegram_owner="alice",
        legacy_path=legacy_path,
    ) is None
    assert build_db_authoritative_rollover_runtime(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=None),
        telegram_owner="alice",
        legacy_path=legacy_path,
    ) is None
    for owner in (None, "", " telegram "):
        assert build_db_authoritative_rollover_runtime(
            session_factory=factory,
            config=RolloverConfig(enabled=True, reference_key=KEY),
            telegram_owner=owner,
            legacy_path=legacy_path,
        ) is None
    assert opened == []


def test_runtime_composition_shares_one_exact_factory_and_lock_between_coordinators(tmp_path):
    engine, factory, _alice, _bob, runtime, _now = _runtime_sweep_fixture(tmp_path)
    try:
        assert runtime.turn_coordinator._session_factory is factory
        assert runtime.binding_mutation_coordinator._session_factory is factory
        assert runtime.turn_coordinator._lock is runtime.binding_mutation_coordinator._lock
    finally:
        engine.dispose()


def test_runtime_empty_cycle_sweeps_only_due_owner_binding_and_commits_one_replacement(tmp_path):
    engine, factory, alice, bob, runtime, _now = _runtime_sweep_fixture(tmp_path)
    try:
        result = runtime.sweep_due_bindings()
        assert result.status == "sweep_ok"
        assert result.imported_count == 0
        assert result.scanned_count == 1
        assert result.committed_count == 1
        assert result.deferred_count == 0
        database = factory()
        try:
            alice_row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == alice.id
                )
            ).mappings().one()
            bob_row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == bob.id
                )
            ).mappings().one()
            assert alice_row["generation"] == 1
            assert alice_row["active_session_id"] != "runtime-alice-session"
            assert bob_row["generation"] == 0
            assert bob_row["active_session_id"] == "runtime-bob-session"
            assert database.get(Session, "runtime-alice-session").archived is True
            assert database.get(Session, "runtime-bob-session").archived is False
        finally:
            database.close()
    finally:
        engine.dispose()


def test_runtime_active_lease_defers_due_binding_without_session_replacement(tmp_path):
    engine, factory, alice, _bob, runtime, now = _runtime_sweep_fixture(tmp_path)
    try:
        database = factory()
        try:
            database.execute(
                update(database_module.TelegramSessionBinding.__table__)
                .where(database_module.TelegramSessionBinding.id == alice.id)
                .values(
                    turn_lease_ref="h1_" + "a" * 32,
                    active_turn_ref="t1_" + "b" * 32,
                    turn_lease_expires_at=(now + timedelta(minutes=5)).replace(tzinfo=None),
                    turn_started_at=now.replace(tzinfo=None),
                )
            )
            database.commit()
        finally:
            database.close()
        result = runtime.sweep_due_bindings()
        assert result.status == "sweep_ok"
        assert result.committed_count == 0
        assert result.deferred_count == 1
        database = factory()
        try:
            binding_row = database.execute(
                database_module.TelegramSessionBinding.__table__.select().where(
                    database_module.TelegramSessionBinding.id == alice.id
                )
            ).mappings().one()
            rollover_row = database.execute(
                database_module.TelegramSessionRollover.__table__.select()
            ).mappings().one()
            assert binding_row["active_session_id"] == "runtime-alice-session"
            assert binding_row["generation"] == 0
            assert rollover_row["status"] == RolloverState.DEFERRED_ACTIVE_TURN.value
        finally:
            database.close()
    finally:
        engine.dispose()


def test_runtime_import_failure_rolls_back_and_closes_the_factory_session(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    raw_factory = sessionmaker(bind=engine, autoflush=False)
    calls = {"rollback": 0, "close": 0}

    class TrackingSession:
        def __init__(self, database):
            self._database = database

        def __getattr__(self, name):
            return getattr(self._database, name)

        def rollback(self):
            calls["rollback"] += 1
            return self._database.rollback()

        def close(self):
            calls["close"] += 1
            return self._database.close()

    def factory():
        return TrackingSession(raw_factory())

    legacy_path = tmp_path / "telegram_session_bridge.json"
    legacy_path.write_text("{not-json", encoding="utf-8")
    runtime = build_db_authoritative_rollover_runtime(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
        telegram_owner="alice",
        legacy_path=legacy_path,
        now=lambda: datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )
    try:
        assert runtime is not None
        assert runtime.sweep_due_bindings().status == "import_blocked"
        assert calls == {"rollback": 1, "close": 1}
    finally:
        engine.dispose()


def test_runtime_sweep_commits_and_closes_each_short_operation(tmp_path):
    engine, raw_factory, _alice, _bob, _runtime, _now = _runtime_sweep_fixture(tmp_path)
    calls = {"commit": 0, "rollback": 0, "close": 0}

    class TrackingSession:
        def __init__(self, database):
            self._database = database

        def __getattr__(self, name):
            return getattr(self._database, name)

        def commit(self):
            calls["commit"] += 1
            return self._database.commit()

        def rollback(self):
            calls["rollback"] += 1
            return self._database.rollback()

        def close(self):
            calls["close"] += 1
            return self._database.close()

    def factory():
        return TrackingSession(raw_factory())

    runtime = build_db_authoritative_rollover_runtime(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
        telegram_owner="alice",
        legacy_path=tmp_path / "telegram_session_bridge.json",
        now=lambda: datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )
    try:
        assert runtime is not None
        assert runtime.sweep_due_bindings().committed_count == 1
        # Import, owner-scoped listing, and one atomic replacement each own a
        # committed/closed operation; no Session crosses those boundaries.
        assert calls == {"commit": 3, "rollback": 0, "close": 3}
    finally:
        engine.dispose()


def test_runtime_rotation_failure_is_content_free_and_rolls_back_closes(tmp_path, monkeypatch):
    engine, raw_factory, _alice, _bob, _runtime, _now = _runtime_sweep_fixture(tmp_path)
    duplicate_id = "duplicate-replacement"
    database = raw_factory()
    try:
        database.add(
            Session(
                id=duplicate_id,
                name="Synthetic",
                endpoint_url="http://synthetic.invalid",
                model="synthetic",
                owner="alice",
            )
        )
        database.commit()
    finally:
        database.close()
    calls = {"rollback": 0, "close": 0}

    class TrackingSession:
        def __init__(self, database):
            self._database = database

        def __getattr__(self, name):
            return getattr(self._database, name)

        def rollback(self):
            calls["rollback"] += 1
            return self._database.rollback()

        def close(self):
            calls["close"] += 1
            return self._database.close()

    def factory():
        return TrackingSession(raw_factory())

    class FixedUuid:
        hex = duplicate_id

    import src.telegram_session_rollover as rollover_module

    monkeypatch.setattr(rollover_module.uuid, "uuid4", lambda: FixedUuid())
    runtime = build_db_authoritative_rollover_runtime(
        session_factory=factory,
        config=RolloverConfig(enabled=True, reference_key=KEY),
        telegram_owner="alice",
        legacy_path=tmp_path / "telegram_session_bridge.json",
        now=lambda: datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
    )
    try:
        assert runtime is not None
        result = runtime.sweep_due_bindings()
        assert result.status == "sweep_ok"
        assert result.committed_count == 0
        assert result.blocked_count == 1
        assert calls["rollback"] == 1
        assert calls["close"] == 3
    finally:
        engine.dispose()


def test_runtime_due_evaluation_failure_is_reserialized_without_exception(tmp_path, monkeypatch):
    engine, _factory, _alice, _bob, runtime, _now = _runtime_sweep_fixture(tmp_path)
    import src.telegram_session_rollover as rollover_module

    monkeypatch.setattr(
        rollover_module,
        "rollover_is_due",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private-row-detail")),
    )
    try:
        result = runtime.sweep_due_bindings()
        assert result == TelegramRolloverSweepResult("sweep_blocked", imported_count=0)
    finally:
        engine.dispose()
