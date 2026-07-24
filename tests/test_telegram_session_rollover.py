from datetime import datetime, time, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from sqlalchemy import create_engine, delete, inspect, update
from sqlalchemy.orm import sessionmaker

from core import database as database_module
from core import database_migrations
from core.database import Base, Session
from plugins.telegram.stores import DbAuthoritativeTelegramSessionBridge, TelegramRolloverBridgeError

from src.telegram_session_rollover import (
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
