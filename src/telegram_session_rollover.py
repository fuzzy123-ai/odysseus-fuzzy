"""Default-off policy and caller-injected durable ledger primitives.

The ledger API deliberately creates no engine, reads no environment, and has no
runtime integration.  A later coordinator supplies a caller-owned SQLAlchemy
session or connection and owns its surrounding transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
import hashlib
import hmac
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, func, insert, literal, select, update
from sqlalchemy.exc import IntegrityError


DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_BOUNDARY = "04:00"
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_RETRY_SECONDS = 300
DEFAULT_TURN_LEASE_SECONDS = 7200
_MAX_REFERENCE_INPUT = 512
_REF_RE = re.compile(r"^h1_[0-9a-f]{32}$")
_BINDING_REF_RE = re.compile(r"^b1_[0-9a-f]{16,64}$")
_OPAQUE_TURN_REF_RE = re.compile(r"^(?:h1|t1)_[0-9a-f]{16,64}$")
_BOUNDARY_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class ConfigurationError(ValueError):
    """A supplied rollover configuration is invalid and must stay disabled."""


class ReferenceError(ValueError):
    """A value cannot safely be used to derive a pseudonymous reference."""


class LedgerError(ValueError):
    """A deterministic, content-free durable-ledger refusal."""


class RolloverState(str, Enum):
    ABSENT = "absent"
    DEFERRED_ACTIVE_TURN = "deferred_active_turn"
    DEFERRED_EXHAUSTED = "deferred_exhausted"
    BLOCKED_INVALID_BINDING = "blocked_invalid_binding"
    BLOCKED_SECURITY_POLICY = "blocked_security_policy"
    COMMITTED = "committed"


class RolloverEvent(str, Enum):
    ACTIVE_TURN = "active_turn"
    TURN_RELEASED = "turn_released"
    LEASE_EXPIRED = "lease_expired"
    READY = "ready"
    INVALID_BINDING = "invalid_binding"
    SECURITY_POLICY_BLOCKED = "security_policy_blocked"


class TurnIntakeState(str, Enum):
    PENDING = "pending"
    LEASE_RETRY = "lease_retry"
    RUNNING = "running"
    REPLY_PENDING = "reply_pending"
    COMPLETED = "completed"
    INDETERMINATE_TURN = "indeterminate_turn"
    BLOCKED_INVALID_BINDING = "blocked_invalid_binding"
    BLOCKED_SECURITY_POLICY = "blocked_security_policy"


class TurnIntakeEvent(str, Enum):
    LEASE_BUSY = "lease_busy"
    LEASE_ACQUIRED = "lease_acquired"
    REPLY_PERSISTED = "reply_persisted"
    INDETERMINATE = "indeterminate"
    REPLY_SENT = "reply_sent"
    INVALID_BINDING = "invalid_binding"
    SECURITY_POLICY_BLOCKED = "security_policy_blocked"


class ReasonCode(str, Enum):
    ACTIVE_TURN = "active_turn"
    RETRY_EXHAUSTED = "retry_exhausted"
    EXPIRED_TURN_LEASE_RECOVERED = "expired_turn_lease_recovered"
    INVALID_BINDING = "invalid_binding"
    SECURITY_POLICY = "security_policy"
    INDETERMINATE_TURN_PAIR = "indeterminate_turn_pair"


@dataclass(frozen=True)
class RolloverConfig:
    """Validated policy values.  ``reference_key`` is intentionally repr-hidden."""

    enabled: bool = False
    reference_key: bytes | None = field(default=None, repr=False)
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo(DEFAULT_TIMEZONE))
    boundary: time = time(4, 0)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_seconds: int = DEFAULT_RETRY_SECONDS
    turn_lease_seconds: int = DEFAULT_TURN_LEASE_SECONDS
    continuity_enabled: bool = False
    invalid_reason: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RolloverConfig":
        """Parse a supplied mapping without reading process environment.

        Any malformed supplied setting returns a safe disabled configuration.  It
        never guesses at a value or raises a configuration that could be enabled.
        """

        if not isinstance(values, Mapping):
            return cls(enabled=False, invalid_reason="invalid_mapping")
        try:
            enabled = _strict_bool(values.get("TELEGRAM_SESSION_ROLLOVER_ENABLED", False))
            continuity = _strict_bool(
                values.get("TELEGRAM_SESSION_CONTINUITY_ENABLED", False)
            )
            timezone = _parse_timezone(
                values.get("TELEGRAM_SESSION_ROLLOVER_TIMEZONE", DEFAULT_TIMEZONE)
            )
            boundary = _parse_boundary(
                values.get("TELEGRAM_SESSION_ROLLOVER_BOUNDARY", DEFAULT_BOUNDARY)
            )
            max_attempts = _bounded_int(
                values.get("TELEGRAM_SESSION_ROLLOVER_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
                1,
                24,
            )
            retry_seconds = _bounded_int(
                values.get("TELEGRAM_SESSION_ROLLOVER_RETRY_SECONDS", DEFAULT_RETRY_SECONDS),
                60,
                3600,
            )
            lease_seconds = _bounded_int(
                values.get("TELEGRAM_SESSION_TURN_LEASE_SECONDS", DEFAULT_TURN_LEASE_SECONDS),
                60,
                14400,
            )
            reference_key = _parse_reference_key(
                values.get("TELEGRAM_SESSION_ROLLOVER_REFERENCE_KEY")
            )
            if enabled and reference_key is None:
                raise ConfigurationError("reference_key_required")
            return cls(
                enabled=enabled,
                reference_key=reference_key,
                timezone=timezone,
                boundary=boundary,
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
                turn_lease_seconds=lease_seconds,
                continuity_enabled=continuity,
            )
        except (ConfigurationError, TypeError, ValueError) as error:
            # The reason is a short code only; key material and supplied values
            # are deliberately not included in it or the generated repr.
            return cls(enabled=False, invalid_reason=_error_code(error))


def parse_rollover_config(values: Mapping[str, Any]) -> RolloverConfig:
    """Convenience entry point for the default-off typed configuration parser."""

    return RolloverConfig.from_mapping(values)


def rollover_local_day(now: datetime, config: RolloverConfig) -> str:
    """Return the effective ISO local day for an aware observed instant."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware_datetime_required")
    local_now = now.astimezone(config.timezone)
    local_day = local_now.date()
    if local_now.timetz().replace(tzinfo=None) < config.boundary:
        local_day -= timedelta(days=1)
    return local_day.isoformat()


def rollover_is_due(active_rollover_local_day: str, now: datetime, config: RolloverConfig) -> bool:
    """Whether a binding established on ``active_rollover_local_day`` is due."""

    try:
        active_day = date.fromisoformat(active_rollover_local_day)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_active_rollover_local_day") from error
    return active_day < date.fromisoformat(rollover_local_day(now, config))


def owner_ref(reference_key: bytes, owner: str) -> str:
    """Derive the lowercase canonical owner reference."""

    normalized = _bounded_text(owner, "owner").lower()
    return _keyed_ref(reference_key, "ttd07a-owner", normalized)


def chat_handle_ref(reference_key: bytes, chat_handle: str) -> str:
    """Derive a reference from an already-stable Telegram chat handle."""

    return _keyed_ref(reference_key, "ttd07a-chat-handle", _bounded_text(chat_handle, "chat"))


def session_ref(reference_key: bytes, session_id: str) -> str:
    """Derive an evidence-only reference for a Session identifier."""

    return _keyed_ref(reference_key, "ttd07a-session", _bounded_text(session_id, "session"))


def transport_update_ref(
    reference_key: bytes, update_id: int | None, message_id: int | None
) -> str:
    """Derive the update ref from a strict, content-free integer tuple."""

    update = _optional_bounded_integer(update_id, "update_id")
    message = _optional_bounded_integer(message_id, "message_id")
    if update is None and message is None:
        raise ReferenceError("update_or_message_id_required")
    normalized_update = "" if update is None else str(update)
    normalized_message = "" if message is None else str(message)
    return _keyed_ref(
        reference_key,
        "ttd07a-update",
        f"{normalized_update}:{normalized_message}",
    )


@dataclass(frozen=True)
class RolloverRecord:
    """The persistable, content-free portion of a day's state-machine row."""

    state: RolloverState = RolloverState.ABSENT
    attempt_count: int = 0
    retry_after: datetime | None = None
    reason_code: ReasonCode | None = None


@dataclass(frozen=True)
class RolloverTransition:
    record: RolloverRecord
    retry_eligible: bool
    commit_eligible: bool


def rollover_retry_eligible(record: RolloverRecord, now: datetime) -> bool:
    """Check retry timing without changing state; ``now`` must be aware."""

    _require_aware(now)
    if record.retry_after is None:
        return True
    _require_aware(record.retry_after)
    return now >= record.retry_after


def advance_rollover_state(
    record: RolloverRecord,
    *,
    event: RolloverEvent,
    now: datetime,
    config: RolloverConfig,
    lease_expires_at: datetime | None = None,
    matching_in_process_turn_present: bool | None = None,
) -> RolloverTransition:
    """Apply one allowlisted, pure rollover transition.

    A release, expiry, or normal current-day wake-up can commit a deferred row,
    including ``deferred_exhausted``.  Exhaustion only stops active-lease retry
    churn; it is never a permanent rollover suppression state.
    """

    _require_aware(now)
    _validate_policy_config(config)
    if lease_expires_at is not None:
        _require_aware(lease_expires_at)
    _validate_record(record, config)

    if not isinstance(event, RolloverEvent):
        raise ValueError("invalid_rollover_transition")
    if record.state in {
        RolloverState.COMMITTED,
        RolloverState.BLOCKED_INVALID_BINDING,
        RolloverState.BLOCKED_SECURITY_POLICY,
    }:
        return RolloverTransition(record, False, False)
    if event is RolloverEvent.INVALID_BINDING:
        if record.state is not RolloverState.ABSENT:
            raise ValueError("invalid_rollover_transition")
        return RolloverTransition(
            RolloverRecord(RolloverState.BLOCKED_INVALID_BINDING, record.attempt_count, None, ReasonCode.INVALID_BINDING),
            False,
            False,
        )
    if event is RolloverEvent.SECURITY_POLICY_BLOCKED:
        if record.state is not RolloverState.ABSENT:
            raise ValueError("invalid_rollover_transition")
        return RolloverTransition(
            RolloverRecord(RolloverState.BLOCKED_SECURITY_POLICY, record.attempt_count, None, ReasonCode.SECURITY_POLICY),
            False,
            False,
        )
    if event is RolloverEvent.ACTIVE_TURN:
        if lease_expires_at is None or lease_expires_at <= now:
            raise ValueError("invalid_active_turn_lease")
        if record.state is RolloverState.DEFERRED_EXHAUSTED:
            return RolloverTransition(record, False, False)
        if not rollover_retry_eligible(record, now):
            return RolloverTransition(record, False, False)
        attempts = min(record.attempt_count + 1, config.max_attempts)
        exhausted = attempts >= config.max_attempts
        next_retry = _next_retry(now, lease_expires_at, config.retry_seconds)
        next_state = (
            RolloverState.DEFERRED_EXHAUSTED if exhausted else RolloverState.DEFERRED_ACTIVE_TURN
        )
        reason = ReasonCode.RETRY_EXHAUSTED if exhausted else ReasonCode.ACTIVE_TURN
        return RolloverTransition(
            RolloverRecord(next_state, attempts, next_retry, reason), True, False
        )

    if event is RolloverEvent.LEASE_EXPIRED:
        if (
            record.state not in {RolloverState.DEFERRED_ACTIVE_TURN, RolloverState.DEFERRED_EXHAUSTED}
            or lease_expires_at is None
            or lease_expires_at > now
            or matching_in_process_turn_present is not False
        ):
            raise ValueError("invalid_expired_lease_recovery")
        return RolloverTransition(
            RolloverRecord(
                RolloverState.COMMITTED,
                record.attempt_count,
                None,
                ReasonCode.EXPIRED_TURN_LEASE_RECOVERED,
            ),
            True,
            True,
        )
    if event is RolloverEvent.TURN_RELEASED:
        if record.state not in {RolloverState.DEFERRED_ACTIVE_TURN, RolloverState.DEFERRED_EXHAUSTED}:
            raise ValueError("invalid_rollover_transition")
        return RolloverTransition(
            RolloverRecord(RolloverState.COMMITTED, record.attempt_count, None, None),
            True,
            True,
        )
    # A normal current-day wake-up has already established that no active lease
    # exists.  It may commit an absent or deferred daily row.
    if event is RolloverEvent.READY:
        if record.state not in {
            RolloverState.ABSENT,
            RolloverState.DEFERRED_ACTIVE_TURN,
            RolloverState.DEFERRED_EXHAUSTED,
        }:
            raise ValueError("invalid_rollover_transition")
        return RolloverTransition(
            RolloverRecord(RolloverState.COMMITTED, record.attempt_count, None, None),
            True,
            True,
        )
    raise ValueError("invalid_rollover_transition")


@dataclass(frozen=True)
class TurnMessageMarker:
    """Only a role and opaque turn ref are needed for crash reconciliation."""

    role: str
    telegram_turn_ref: str


@dataclass(frozen=True)
class TurnReconciliation:
    state: TurnIntakeState
    reason_code: ReasonCode | None
    automatic_replay_allowed: bool = False


def advance_turn_intake_state(
    state: TurnIntakeState, event: TurnIntakeEvent
) -> TurnIntakeState:
    """Apply the content-free, allowlisted Telegram intake lifecycle.

    This is deliberately independent of persistence and payloads.  A caller
    persists the returned state atomically with its lease/reply evidence.
    """

    if not isinstance(state, TurnIntakeState) or not isinstance(event, TurnIntakeEvent):
        raise ValueError("invalid_turn_intake_transition")
    terminal_events = {
        TurnIntakeState.COMPLETED: TurnIntakeEvent.REPLY_SENT,
        TurnIntakeState.INDETERMINATE_TURN: TurnIntakeEvent.INDETERMINATE,
        TurnIntakeState.BLOCKED_INVALID_BINDING: TurnIntakeEvent.INVALID_BINDING,
        TurnIntakeState.BLOCKED_SECURITY_POLICY: TurnIntakeEvent.SECURITY_POLICY_BLOCKED,
    }
    if state in terminal_events:
        if event is terminal_events[state]:
            return state
        raise ValueError("invalid_turn_intake_transition")
    transitions = {
        (TurnIntakeState.PENDING, TurnIntakeEvent.LEASE_BUSY): TurnIntakeState.LEASE_RETRY,
        (TurnIntakeState.PENDING, TurnIntakeEvent.LEASE_ACQUIRED): TurnIntakeState.RUNNING,
        (TurnIntakeState.LEASE_RETRY, TurnIntakeEvent.LEASE_BUSY): TurnIntakeState.LEASE_RETRY,
        (TurnIntakeState.LEASE_RETRY, TurnIntakeEvent.LEASE_ACQUIRED): TurnIntakeState.RUNNING,
        (TurnIntakeState.RUNNING, TurnIntakeEvent.REPLY_PERSISTED): TurnIntakeState.REPLY_PENDING,
        (TurnIntakeState.RUNNING, TurnIntakeEvent.INDETERMINATE): TurnIntakeState.INDETERMINATE_TURN,
        (TurnIntakeState.REPLY_PENDING, TurnIntakeEvent.REPLY_SENT): TurnIntakeState.COMPLETED,
    }
    if event is TurnIntakeEvent.INVALID_BINDING and state in {
        TurnIntakeState.PENDING,
        TurnIntakeState.LEASE_RETRY,
    }:
        return TurnIntakeState.BLOCKED_INVALID_BINDING
    if event is TurnIntakeEvent.SECURITY_POLICY_BLOCKED and state in {
        TurnIntakeState.PENDING,
        TurnIntakeState.LEASE_RETRY,
    }:
        return TurnIntakeState.BLOCKED_SECURITY_POLICY
    try:
        return transitions[(state, event)]
    except KeyError as error:
        raise ValueError("invalid_turn_intake_transition") from error


def reconcile_running_turn(
    telegram_turn_ref: str, markers: Sequence[TurnMessageMarker]
) -> TurnReconciliation:
    """Fail closed while reconciling a crashed ``running`` intake.

    Exactly one matching user marker and one matching assistant marker make a
    reply reusable.  Everything else is terminal ``indeterminate_turn`` and
    deliberately forbids automatic model or tool replay.
    """

    if not isinstance(telegram_turn_ref, str) or not _OPAQUE_TURN_REF_RE.fullmatch(telegram_turn_ref):
        raise ValueError("invalid_telegram_turn_ref")
    if _is_exact_turn_pair(telegram_turn_ref, markers):
        return TurnReconciliation(TurnIntakeState.REPLY_PENDING, None, False)
    return TurnReconciliation(
        TurnIntakeState.INDETERMINATE_TURN,
        ReasonCode.INDETERMINATE_TURN_PAIR,
        False,
    )


@dataclass(frozen=True)
class LedgerBinding:
    """Content-free view of one immutable Telegram binding identity."""

    id: str
    owner_ref: str
    chat_handle_ref: str
    scope: str
    active_session_id: str
    active_rollover_local_day: str
    generation: int


@dataclass(frozen=True)
class LedgerRollover:
    """Content-free view of the one reservation for a binding and local day."""

    id: str
    binding_id: str
    rollover_local_day: str
    status: RolloverState
    old_session_id: str
    new_session_id: str | None
    attempt_count: int
    retry_after: datetime | None
    reason_code: ReasonCode | None


@dataclass(frozen=True)
class LedgerTurnIntake:
    """Content-free view of a replay-safe Telegram turn intake."""

    id: str
    owner_ref: str
    chat_handle_ref: str
    transport_update_ref: str
    scope: str
    binding_id: str
    expected_session_id: str
    status: TurnIntakeState
    retry_count: int
    next_retry_at: datetime | None
    reason_code: ReasonCode | None


class TelegramRolloverLedger:
    """A transaction-neutral repository over caller-owned SQLAlchemy state.

    The supplied ``database`` must be a SQLAlchemy ``Session`` or ``Connection``.
    The repository never commits, rolls back an outer transaction, creates an
    engine, or reads process configuration.  Duplicate insert handling uses a
    nested transaction so a uniqueness loser reloads the one winner while the
    caller's wider unit of work remains intact.
    """

    def __init__(self, database: Any, reference_key: bytes):
        if not callable(getattr(database, "execute", None)) or not callable(
            getattr(database, "begin_nested", None)
        ):
            raise LedgerError("invalid_database_handle")
        self._database = database
        self._reference_key = _ledger_reference_key(reference_key)

    def get_or_create_binding(
        self,
        *,
        owner_reference: str,
        chat_reference: str,
        scope: str,
        active_session_id: str,
        active_rollover_local_day: str,
    ) -> LedgerBinding:
        tables = self._verified_tables()
        _validate_ledger_ref(owner_reference, "owner_ref")
        _validate_ledger_ref(chat_reference, "chat_handle_ref")
        _validate_scope(scope)
        _validate_internal_id(active_session_id, "active_session_id")
        _validate_local_day(active_rollover_local_day)
        _validate_session_owner(
            self._database, tables["session"], self._reference_key, active_session_id, owner_reference
        )
        binding_table = tables["binding"]
        where = (
            binding_table.c.owner_ref == owner_reference,
            binding_table.c.chat_handle_ref == chat_reference,
            binding_table.c.scope == scope,
        )
        existing = _select_one(self._database, binding_table, *where)
        if existing is not None:
            binding = self._binding_view(tables, existing)
            if (
                binding.active_session_id != active_session_id
                or binding.active_rollover_local_day != active_rollover_local_day
            ):
                raise LedgerError("conflicting_binding_replay")
            return binding
        binding_id = _ledger_opaque_id(
            self._reference_key,
            "ttd07a-binding",
            (owner_reference, chat_reference, scope),
            "b1",
        )
        row = _insert_or_reload(
            self._database,
            binding_table,
            {
                "id": binding_id,
                "owner_ref": owner_reference,
                "chat_handle_ref": chat_reference,
                "scope": scope,
                "active_session_id": active_session_id,
                "active_rollover_local_day": active_rollover_local_day,
                "generation": 0,
                "projection_status": "current",
                "projection_generation": 0,
            },
            where,
        )
        binding = self._binding_view(tables, row)
        if (
            binding.active_session_id != active_session_id
            or binding.active_rollover_local_day != active_rollover_local_day
        ):
            raise LedgerError("conflicting_binding_replay")
        return binding

    def get_binding(self, binding_id: str) -> LedgerBinding | None:
        tables = self._verified_tables()
        _validate_binding_id(binding_id)
        row = _select_one(self._database, tables["binding"], tables["binding"].c.id == binding_id)
        return None if row is None else self._binding_view(tables, row)

    def get_turn_intake(
        self, *, owner_reference: str, chat_reference: str, transport_update_reference: str
    ) -> LedgerTurnIntake | None:
        """Load the durable natural-key duplicate without creation authority."""

        tables = self._verified_tables()
        _validate_ledger_ref(owner_reference, "owner_ref")
        _validate_ledger_ref(chat_reference, "chat_handle_ref")
        _validate_ledger_ref(transport_update_reference, "transport_update_ref")
        table = tables["intake"]
        row = _select_one(
            self._database,
            table,
            table.c.owner_ref == owner_reference,
            table.c.chat_handle_ref == chat_reference,
            table.c.transport_update_ref == transport_update_reference,
        )
        return None if row is None else self._intake_view(tables, row)

    def reserve_or_get_rollover(
        self,
        *,
        binding_id: str,
        rollover_local_day: str,
        expected_generation: int,
        state: RolloverState,
        attempt_count: int,
        retry_after: datetime | None,
        reason_code: ReasonCode | None,
        max_attempts: int,
    ) -> LedgerRollover:
        tables = self._verified_tables()
        _validate_binding_id(binding_id)
        _validate_local_day(rollover_local_day)
        rollover_table = tables["rollover"]
        where = (
            rollover_table.c.binding_id == binding_id,
            rollover_table.c.rollover_local_day == rollover_local_day,
        )
        existing = _select_one(self._database, rollover_table, *where)
        if existing is not None:
            return self._rollover_view(tables, existing)
        reservation = _validated_rollover_values(
            state=state,
            attempt_count=attempt_count,
            retry_after=retry_after,
            reason_code=reason_code,
            new_session_id=None,
            committed_at=None,
            max_attempts=max_attempts,
        )
        binding = self._binding_for_generation(tables, binding_id, expected_generation)
        rollover_id = _ledger_opaque_id(
            self._reference_key,
            "ttd07a-rollover",
            (binding_id, rollover_local_day),
            "r1",
        )
        row = _insert_or_reload(
            self._database,
            rollover_table,
            {
                "id": rollover_id,
                "binding_id": binding.id,
                "rollover_local_day": rollover_local_day,
                "status": reservation["status"],
                "old_session_id": binding.active_session_id,
                "attempt_count": reservation["attempt_count"],
                "retry_after": reservation["retry_after"],
                "reason_code": reservation["reason_code"],
            },
            where,
            generation_condition=(
                tables["binding"].c.id == binding_id,
                tables["binding"].c.generation == expected_generation,
            ),
        )
        return self._rollover_view(tables, row)

    def persist_rollover_deferral(
        self,
        *,
        binding_id: str,
        rollover_local_day: str,
        expected_generation: int,
        state: RolloverState,
        attempt_count: int,
        retry_after: datetime,
        max_attempts: int,
    ) -> LedgerRollover:
        """Persist the bounded active-turn retry portion of the policy only."""

        tables = self._verified_tables()
        _validate_binding_id(binding_id)
        _validate_local_day(rollover_local_day)
        self._binding_for_generation(tables, binding_id, expected_generation)
        values = _validated_rollover_values(
            state=state,
            attempt_count=attempt_count,
            retry_after=retry_after,
            reason_code=(
                ReasonCode.ACTIVE_TURN
                if state is RolloverState.DEFERRED_ACTIVE_TURN
                else ReasonCode.RETRY_EXHAUSTED
                if state is RolloverState.DEFERRED_EXHAUSTED
                else None
            ),
            new_session_id=None,
            committed_at=None,
            max_attempts=max_attempts,
        )
        table = tables["rollover"]
        row = _select_one(
            self._database,
            table,
            table.c.binding_id == binding_id,
            table.c.rollover_local_day == rollover_local_day,
        )
        if row is None:
            raise LedgerError("rollover_not_found")
        current = self._rollover_view(tables, row)
        if current.status in {
            RolloverState.COMMITTED,
            RolloverState.BLOCKED_INVALID_BINDING,
            RolloverState.BLOCKED_SECURITY_POLICY,
        }:
            raise LedgerError("immutable_rollover")
        if (
            current.status is RolloverState.DEFERRED_EXHAUSTED
            and state is not RolloverState.DEFERRED_EXHAUSTED
        ):
            raise LedgerError("invalid_rollover_transition")
        result = self._database.execute(
            update(table)
            .where(
                table.c.id == current.id,
                exists(
                    select(literal(1)).where(
                        tables["binding"].c.id == binding_id,
                        tables["binding"].c.generation == expected_generation,
                    )
                ),
                *_row_value_conditions(
                    table, row, ("status", "attempt_count", "retry_after", "reason_code")
                ),
            )
            .values(
                status=values["status"],
                attempt_count=values["attempt_count"],
                retry_after=values["retry_after"],
                reason_code=values["reason_code"],
                updated_at=_ledger_now(),
            )
        )
        if result.rowcount != 1:
            reloaded = _select_one(self._database, table, table.c.id == current.id)
            if reloaded is not None:
                self._binding_for_generation(tables, binding_id, expected_generation)
                outcome = self._rollover_view(tables, reloaded)
                if _rollover_matches_values(outcome, values):
                    return outcome
            raise LedgerError("stale_row_state")
        return self._rollover_view(
            tables, _select_one(self._database, table, table.c.id == current.id)
        )

    def get_or_create_turn_intake(
        self,
        *,
        owner_reference: str,
        chat_reference: str,
        transport_update_reference: str,
        scope: str,
        binding_id: str,
        expected_session_id: str,
        expected_generation: int,
    ) -> LedgerTurnIntake:
        tables = self._verified_tables()
        _validate_ledger_ref(owner_reference, "owner_ref")
        _validate_ledger_ref(chat_reference, "chat_handle_ref")
        _validate_ledger_ref(transport_update_reference, "transport_update_ref")
        _validate_scope(scope)
        _validate_binding_id(binding_id)
        _validate_internal_id(expected_session_id, "expected_session_id")
        intake_table = tables["intake"]
        where = (
            intake_table.c.owner_ref == owner_reference,
            intake_table.c.chat_handle_ref == chat_reference,
            intake_table.c.transport_update_ref == transport_update_reference,
        )
        existing = _select_one(self._database, intake_table, *where)
        if existing is not None:
            intake = self._intake_view(tables, existing)
            if not _intake_identity_matches(
                intake, scope, binding_id, expected_session_id
            ):
                raise LedgerError("conflicting_intake_replay")
            return intake
        binding = self._binding_for_generation(tables, binding_id, expected_generation)
        if (
            binding.owner_ref != owner_reference
            or binding.chat_handle_ref != chat_reference
            or binding.scope != scope
            or binding.active_session_id != expected_session_id
        ):
            raise LedgerError("invalid_binding_fence")
        intake_id = _ledger_opaque_id(
            self._reference_key,
            "ttd07a-turn-intake",
            (owner_reference, chat_reference, transport_update_reference),
            "t1",
        )
        row = _insert_or_reload(
            self._database,
            intake_table,
            {
                "id": intake_id,
                "owner_ref": owner_reference,
                "chat_handle_ref": chat_reference,
                "transport_update_ref": transport_update_reference,
                "scope": scope,
                "binding_id": binding_id,
                "expected_session_id": expected_session_id,
                "status": TurnIntakeState.PENDING.value,
                "retry_count": 0,
            },
            where,
            generation_condition=(
                tables["binding"].c.id == binding_id,
                tables["binding"].c.generation == expected_generation,
            ),
        )
        intake = self._intake_view(tables, row)
        if not _intake_identity_matches(intake, scope, binding_id, expected_session_id):
            raise LedgerError("conflicting_intake_replay")
        return intake

    def advance_turn_intake(
        self,
        *,
        intake_id: str,
        expected_generation: int,
        event: TurnIntakeEvent,
        retry_after: datetime | None = None,
    ) -> LedgerTurnIntake:
        """Apply one allowlisted turn lifecycle event without committing."""

        tables = self._verified_tables()
        _validate_turn_id(intake_id)
        if not isinstance(event, TurnIntakeEvent):
            raise LedgerError("invalid_turn_intake_transition")
        table = tables["intake"]
        row = _select_one(self._database, table, table.c.id == intake_id)
        if row is None:
            raise LedgerError("turn_intake_not_found")
        current = self._intake_view(tables, row)
        try:
            next_state = advance_turn_intake_state(current.status, event)
        except ValueError as error:
            raise LedgerError("invalid_turn_intake_transition") from error
        if current.status in {
            TurnIntakeState.COMPLETED,
            TurnIntakeState.INDETERMINATE_TURN,
            TurnIntakeState.BLOCKED_INVALID_BINDING,
            TurnIntakeState.BLOCKED_SECURITY_POLICY,
        }:
            # The pure policy permits the matching terminal event to be
            # idempotent.  Preserve the row byte-for-byte rather than even
            # bumping ``updated_at`` on a completed or indeterminate intake.
            return current
        self._binding_for_generation(tables, current.binding_id, expected_generation)
        retry_count = current.retry_count
        next_retry = None
        if event is TurnIntakeEvent.LEASE_BUSY:
            if retry_after is None:
                raise LedgerError("retry_after_required")
            retry_count += 1
            if retry_count > 24:
                raise LedgerError("retry_limit_exceeded")
            next_retry = _ledger_timestamp(retry_after)
        elif retry_after is not None:
            raise LedgerError("unexpected_retry_after")
        reason = _turn_reason_for_event(event)
        result = self._database.execute(
            update(table)
            .where(
                table.c.id == intake_id,
                exists(
                    select(literal(1)).where(
                        tables["binding"].c.id == current.binding_id,
                        tables["binding"].c.generation == expected_generation,
                    )
                ),
                *_row_value_conditions(
                    table, row, ("status", "retry_count", "next_retry_at", "reason_code")
                ),
            )
            .values(
                status=next_state.value,
                retry_count=retry_count,
                next_retry_at=next_retry,
                reason_code=None if reason is None else reason.value,
                updated_at=_ledger_now(),
            )
        )
        if result.rowcount != 1:
            reloaded = _select_one(self._database, table, table.c.id == intake_id)
            if reloaded is not None:
                self._binding_for_generation(tables, current.binding_id, expected_generation)
                outcome = self._intake_view(tables, reloaded)
                if _intake_matches_values(outcome, next_state, retry_count, next_retry, reason):
                    return outcome
            raise LedgerError("stale_row_state")
        return self._intake_view(tables, _select_one(self._database, table, table.c.id == intake_id))

    def _verified_tables(self) -> Mapping[str, Any]:
        tables = _ledger_tables()
        _verify_reference_key(self._database, tables, self._reference_key)
        return tables

    def _binding_view(self, tables: Mapping[str, Any], row: Mapping[str, Any]) -> LedgerBinding:
        binding = _binding_from_row(row)
        expected_id = _ledger_opaque_id(
            self._reference_key,
            "ttd07a-binding",
            (binding.owner_ref, binding.chat_handle_ref, binding.scope),
            "b1",
        )
        if not hmac.compare_digest(binding.id, expected_id) or not _session_belongs_to_owner(
            self._database,
            tables["session"],
            self._reference_key,
            binding.active_session_id,
            binding.owner_ref,
        ):
            raise LedgerError("invalid_binding_relationship")
        return binding

    def _rollover_view(self, tables: Mapping[str, Any], row: Mapping[str, Any]) -> LedgerRollover:
        rollover = _rollover_from_row(row)
        binding_row = _select_one(
            self._database, tables["binding"], tables["binding"].c.id == rollover.binding_id
        )
        if binding_row is None:
            raise LedgerError("invalid_rollover_relationship")
        binding = self._binding_view(tables, binding_row)
        expected_id = _ledger_opaque_id(
            self._reference_key,
            "ttd07a-rollover",
            (rollover.binding_id, rollover.rollover_local_day),
            "r1",
        )
        sessions = (rollover.old_session_id, rollover.new_session_id)
        if (
            not hmac.compare_digest(rollover.id, expected_id)
            or any(
                session_id is not None
                and not _session_belongs_to_owner(
                    self._database, tables["session"], self._reference_key, session_id, binding.owner_ref
                )
                for session_id in sessions
            )
        ):
            raise LedgerError("invalid_rollover_relationship")
        return rollover

    def _intake_view(self, tables: Mapping[str, Any], row: Mapping[str, Any]) -> LedgerTurnIntake:
        intake = _intake_from_row(row)
        binding_row = _select_one(
            self._database, tables["binding"], tables["binding"].c.id == intake.binding_id
        )
        if binding_row is None:
            raise LedgerError("invalid_turn_intake_relationship")
        binding = self._binding_view(tables, binding_row)
        expected_id = _ledger_opaque_id(
            self._reference_key,
            "ttd07a-turn-intake",
            (intake.owner_ref, intake.chat_handle_ref, intake.transport_update_ref),
            "t1",
        )
        if (
            intake.owner_ref != binding.owner_ref
            or intake.chat_handle_ref != binding.chat_handle_ref
            or intake.scope != binding.scope
            or not hmac.compare_digest(intake.id, expected_id)
            or not _session_belongs_to_owner(
                self._database,
                tables["session"],
                self._reference_key,
                intake.expected_session_id,
                binding.owner_ref,
            )
        ):
            raise LedgerError("invalid_turn_intake_relationship")
        return intake

    def _binding_for_generation(
        self, tables: Mapping[str, Any], binding_id: str, expected_generation: int
    ) -> LedgerBinding:
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation < 0:
            raise LedgerError("invalid_generation_fence")
        table = tables["binding"]
        row = _select_one(self._database, table, table.c.id == binding_id)
        if row is None:
            raise LedgerError("binding_not_found")
        binding = self._binding_view(tables, row)
        if binding.generation != expected_generation:
            raise LedgerError("stale_generation_fence")
        return binding


def create_or_get_binding(database: Any, reference_key: bytes, **kwargs: Any) -> LedgerBinding:
    """Caller-injected convenience wrapper for immutable binding creation."""

    return TelegramRolloverLedger(database, reference_key).get_or_create_binding(**kwargs)


def reserve_or_get_rollover(database: Any, reference_key: bytes, **kwargs: Any) -> LedgerRollover:
    """Caller-injected convenience wrapper for the daily idempotency winner."""

    return TelegramRolloverLedger(database, reference_key).reserve_or_get_rollover(**kwargs)


def create_or_get_turn_intake(
    database: Any, reference_key: bytes, **kwargs: Any
) -> LedgerTurnIntake:
    """Caller-injected convenience wrapper for content-free update intake."""

    return TelegramRolloverLedger(database, reference_key).get_or_create_turn_intake(**kwargs)


def get_turn_intake(database: Any, reference_key: bytes, **kwargs: Any) -> LedgerTurnIntake | None:
    """Caller-injected duplicate lookup with no binding creation authority."""

    return TelegramRolloverLedger(database, reference_key).get_turn_intake(**kwargs)


def _ledger_tables() -> Mapping[str, Any]:
    # Importing model metadata is not an engine lookup; every SQL operation is
    # still issued through the caller-provided Session or Connection.
    from core.database import (
        TelegramRolloverMetadata,
        TelegramSessionBinding,
        TelegramSessionRollover,
        TelegramTurnIntake,
        Session,
    )

    return {
        "binding": TelegramSessionBinding.__table__,
        "rollover": TelegramSessionRollover.__table__,
        "intake": TelegramTurnIntake.__table__,
        "metadata": TelegramRolloverMetadata.__table__,
        "session": Session.__table__,
    }


def _verify_reference_key(database: Any, tables: Mapping[str, Any], reference_key: bytes) -> None:
    metadata_table = tables["metadata"]
    metadata = _select_one(database, metadata_table, metadata_table.c.id == "reference_key_v1")
    fingerprint = hashlib.sha256(b"ttd07a-key-fingerprint\0" + reference_key).hexdigest()
    if metadata is not None:
        stored = metadata["reference_key_fingerprint"]
        if (
            metadata["schema_version"] != 1
            or not isinstance(stored, str)
            or not hmac.compare_digest(stored, fingerprint)
        ):
            raise LedgerError("reference_key_mismatch")
        return
    for table_name in ("binding", "rollover", "intake"):
        if database.execute(select(func.count()).select_from(tables[table_name])).scalar_one() != 0:
            raise LedgerError("reference_key_mismatch")
    row = _insert_or_reload(
        database,
        metadata_table,
        {
            "id": "reference_key_v1",
            "schema_version": 1,
            "reference_key_fingerprint": fingerprint,
        },
        (metadata_table.c.id == "reference_key_v1",),
    )
    stored = row["reference_key_fingerprint"]
    if not isinstance(stored, str) or not hmac.compare_digest(stored, fingerprint):
        raise LedgerError("reference_key_mismatch")


def _insert_or_reload(
    database: Any,
    table: Any,
    values: Mapping[str, Any],
    where: Sequence[Any],
    generation_condition: Sequence[Any] | None = None,
) -> Mapping[str, Any]:
    statement = insert(table).values(**values)
    if generation_condition is not None:
        values = {**values, "created_at": _ledger_now(), "updated_at": _ledger_now()}
        columns = list(values)
        statement = insert(table).from_select(
            columns,
            select(*(literal(values[column]) for column in columns)).where(
                exists(select(literal(1)).where(*generation_condition))
            ),
        )
    try:
        _ensure_sqlite_outer_transaction(database)
        with database.begin_nested():
            result = database.execute(statement)
    except IntegrityError as error:
        row = _select_one(database, table, *where)
        if row is None:
            raise LedgerError("ledger_write_failed") from error
        return row
    if generation_condition is not None and result.rowcount != 1:
        row = _select_one(database, table, *where)
        if row is not None:
            return row
        raise LedgerError("stale_generation_fence")
    row = _select_one(database, table, *where)
    if row is None:
        raise LedgerError("ledger_write_failed")
    return row


def _ensure_sqlite_outer_transaction(database: Any) -> None:
    """Anchor a caller-owned SQLite transaction before a nested savepoint.

    Python's legacy sqlite transaction control can release a SAVEPOINT as an
    independent commit when no physical ``BEGIN`` precedes it.  The repository
    emits one explicit BEGIN only for a caller-supplied SQLite Session or
    Connection whose driver reports no active physical transaction; it never
    commits or rolls back caller state and leaves non-SQLite paths untouched.
    """

    connection = database if hasattr(database, "dialect") else database.connection()
    if getattr(getattr(connection, "dialect", None), "name", None) != "sqlite":
        return
    fairy = getattr(connection, "connection", None)
    raw = getattr(fairy, "driver_connection", None) or getattr(fairy, "connection", None)
    if raw is not None and getattr(raw, "in_transaction", True) is False:
        connection.exec_driver_sql("BEGIN")


def _select_one(database: Any, table: Any, *where: Any) -> Mapping[str, Any] | None:
    result = database.execute(select(table).where(*where)).mappings().one_or_none()
    return None if result is None else dict(result)


def _row_value_conditions(table: Any, row: Mapping[str, Any], names: Sequence[str]) -> list[Any]:
    """Build NULL-safe optimistic-CAS predicates from an observed ledger row."""

    return [
        table.c[name].is_(None) if row[name] is None else table.c[name] == row[name]
        for name in names
    ]


def _rollover_matches_values(outcome: LedgerRollover, values: Mapping[str, Any]) -> bool:
    return (
        outcome.status.value == values["status"]
        and outcome.attempt_count == values["attempt_count"]
        and outcome.retry_after == values["retry_after"]
        and (None if outcome.reason_code is None else outcome.reason_code.value) == values["reason_code"]
    )


def _intake_matches_values(
    outcome: LedgerTurnIntake,
    state: TurnIntakeState,
    retry_count: int,
    next_retry: datetime | None,
    reason: ReasonCode | None,
) -> bool:
    return (
        outcome.status is state
        and outcome.retry_count == retry_count
        and outcome.next_retry_at == next_retry
        and outcome.reason_code is reason
    )


def _intake_identity_matches(
    intake: LedgerTurnIntake, scope: str, binding_id: str, expected_session_id: str
) -> bool:
    return (
        intake.scope == scope
        and intake.binding_id == binding_id
        and intake.expected_session_id == expected_session_id
    )


def _binding_from_row(row: Mapping[str, Any]) -> LedgerBinding:
    _validate_binding_row_shape(row)
    return LedgerBinding(
        row["id"], row["owner_ref"], row["chat_handle_ref"], row["scope"],
        row["active_session_id"], row["active_rollover_local_day"], row["generation"],
    )


def _validate_binding_row_shape(row: Mapping[str, Any]) -> None:
    generation = row.get("generation")
    projection_generation = row.get("projection_generation")
    valid = (
        isinstance(row.get("id"), str)
        and _BINDING_REF_RE.fullmatch(row["id"]) is not None
        and _valid_ledger_ref(row.get("owner_ref"))
        and _valid_ledger_ref(row.get("chat_handle_ref"))
        and row.get("scope") in {"normal", "secure"}
        and _valid_internal_row_id(row.get("active_session_id"))
        and _valid_iso_day(row.get("active_rollover_local_day"))
        and isinstance(generation, int)
        and not isinstance(generation, bool)
        and generation >= 0
        and isinstance(projection_generation, int)
        and not isinstance(projection_generation, bool)
        and projection_generation >= 0
        and row.get("projection_status") in {"current", "stale", "blocked_multi_owner"}
    )
    lease_values = (
        row.get("turn_lease_ref"),
        row.get("active_turn_ref"),
        row.get("turn_lease_expires_at"),
        row.get("turn_started_at"),
    )
    if all(value is None for value in lease_values):
        lease_valid = True
    else:
        lease_valid = (
            isinstance(lease_values[0], str)
            and _OPAQUE_TURN_REF_RE.fullmatch(lease_values[0]) is not None
            and isinstance(lease_values[1], str)
            and _OPAQUE_TURN_REF_RE.fullmatch(lease_values[1]) is not None
            and _is_db_timestamp(lease_values[2])
            and _is_db_timestamp(lease_values[3])
        )
    if not valid or not lease_valid:
        raise LedgerError("invalid_binding_row")


def _rollover_from_row(row: Mapping[str, Any]) -> LedgerRollover:
    try:
        state = RolloverState(row["status"])
        reason = None if row["reason_code"] is None else ReasonCode(row["reason_code"])
    except ValueError as error:
        raise LedgerError("invalid_rollover_row") from error
    _validate_rollover_row_shape(row, state, reason)
    return LedgerRollover(
        row["id"], row["binding_id"], row["rollover_local_day"], state,
        row["old_session_id"], row["new_session_id"], row["attempt_count"],
        row["retry_after"], reason,
    )


def _intake_from_row(row: Mapping[str, Any]) -> LedgerTurnIntake:
    try:
        state = TurnIntakeState(row["status"])
        reason = None if row["reason_code"] is None else ReasonCode(row["reason_code"])
    except ValueError as error:
        raise LedgerError("invalid_turn_intake_row") from error
    _validate_intake_row_shape(row, state, reason)
    return LedgerTurnIntake(
        row["id"], row["owner_ref"], row["chat_handle_ref"], row["transport_update_ref"],
        row["scope"], row["binding_id"], row["expected_session_id"], state,
        row["retry_count"], row["next_retry_at"], reason,
    )


def _validated_rollover_values(
    *,
    state: RolloverState,
    attempt_count: int,
    retry_after: datetime | None,
    reason_code: ReasonCode | None,
    new_session_id: str | None,
    committed_at: datetime | None,
    max_attempts: int | None,
) -> Mapping[str, Any]:
    if not isinstance(state, RolloverState) or state is RolloverState.ABSENT:
        raise LedgerError("invalid_rollover_retry")
    if reason_code is not None and not isinstance(reason_code, ReasonCode):
        raise LedgerError("invalid_rollover_retry")
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 24
    ):
        raise LedgerError("invalid_rollover_retry")
    retry_at = None if retry_after is None else _ledger_timestamp(retry_after)
    if new_session_id is not None:
        _validate_internal_id(new_session_id, "new_session_id")
    committed = None if committed_at is None else _ledger_timestamp(committed_at)
    row = {
        "id": "r1_" + "0" * 32,
        "binding_id": "b1_" + "0" * 32,
        "rollover_local_day": "2000-01-01",
        "old_session_id": "synthetic",
        "new_session_id": new_session_id,
        "status": state.value,
        "attempt_count": attempt_count,
        "retry_after": retry_at,
        "reason_code": None if reason_code is None else reason_code.value,
        "committed_at": committed,
    }
    try:
        _validate_rollover_row_shape(row, state, reason_code, max_attempts=max_attempts)
    except LedgerError as error:
        raise LedgerError("invalid_rollover_retry") from error
    return {
        "status": state.value,
        "attempt_count": attempt_count,
        "retry_after": retry_at,
        "reason_code": None if reason_code is None else reason_code.value,
        "committed_at": committed,
    }


def _validate_rollover_row_shape(
    row: Mapping[str, Any], state: RolloverState, reason: ReasonCode | None, *, max_attempts: int | None = None
) -> None:
    attempts = row.get("attempt_count")
    retry_after = row.get("retry_after")
    new_session_id = row.get("new_session_id")
    committed_at = row.get("committed_at")
    valid_attempts = isinstance(attempts, int) and not isinstance(attempts, bool) and 0 <= attempts <= 24
    valid = (
        valid_attempts
        and isinstance(row.get("id"), str)
        and re.fullmatch(r"r1_[0-9a-f]{32}", row["id"]) is not None
        and isinstance(row.get("binding_id"), str)
        and _BINDING_REF_RE.fullmatch(row["binding_id"]) is not None
        and _valid_internal_row_id(row.get("old_session_id"))
        and _valid_iso_day(row.get("rollover_local_day"))
    )
    if state is RolloverState.DEFERRED_ACTIVE_TURN:
        upper = 24 if max_attempts is None else max_attempts
        valid = valid and 1 <= attempts < upper and _is_db_timestamp(retry_after) and reason is ReasonCode.ACTIVE_TURN
        valid = valid and new_session_id is None and committed_at is None
    elif state is RolloverState.DEFERRED_EXHAUSTED:
        valid = valid and 1 <= attempts <= 24 and _is_db_timestamp(retry_after) and reason is ReasonCode.RETRY_EXHAUSTED
        if max_attempts is not None:
            valid = valid and attempts == max_attempts
        valid = valid and new_session_id is None and committed_at is None
    elif state is RolloverState.BLOCKED_INVALID_BINDING:
        valid = valid and attempts == 0 and retry_after is None and reason is ReasonCode.INVALID_BINDING
        valid = valid and new_session_id is None and committed_at is None
    elif state is RolloverState.BLOCKED_SECURITY_POLICY:
        valid = valid and attempts == 0 and retry_after is None and reason is ReasonCode.SECURITY_POLICY
        valid = valid and new_session_id is None and committed_at is None
    elif state is RolloverState.COMMITTED:
        valid = valid and retry_after is None and _valid_internal_row_id(new_session_id) and _is_db_timestamp(committed_at)
        valid = valid and (reason is None or (reason is ReasonCode.EXPIRED_TURN_LEASE_RECOVERED and attempts >= 1))
    else:
        valid = False
    if not valid:
        raise LedgerError("invalid_rollover_row")


def _validate_intake_row_shape(
    row: Mapping[str, Any], state: TurnIntakeState, reason: ReasonCode | None
) -> None:
    retries = row.get("retry_count")
    next_retry = row.get("next_retry_at")
    valid = (
        isinstance(retries, int)
        and not isinstance(retries, bool)
        and 0 <= retries <= 24
        and isinstance(row.get("id"), str)
        and re.fullmatch(r"t1_[0-9a-f]{32}", row["id"]) is not None
        and _valid_ledger_ref(row.get("owner_ref"))
        and _valid_ledger_ref(row.get("chat_handle_ref"))
        and _valid_ledger_ref(row.get("transport_update_ref"))
        and row.get("scope") in {"normal", "secure"}
        and isinstance(row.get("binding_id"), str)
        and _BINDING_REF_RE.fullmatch(row["binding_id"]) is not None
        and _valid_internal_row_id(row.get("expected_session_id"))
    )
    if state is TurnIntakeState.PENDING:
        valid = valid and retries == 0 and next_retry is None and reason is None
    elif state is TurnIntakeState.LEASE_RETRY:
        valid = valid and 1 <= retries <= 24 and _is_db_timestamp(next_retry) and reason is None
    elif state in {TurnIntakeState.RUNNING, TurnIntakeState.REPLY_PENDING, TurnIntakeState.COMPLETED}:
        valid = valid and next_retry is None and reason is None
    elif state is TurnIntakeState.INDETERMINATE_TURN:
        valid = valid and next_retry is None and reason is ReasonCode.INDETERMINATE_TURN_PAIR
    elif state is TurnIntakeState.BLOCKED_INVALID_BINDING:
        valid = valid and next_retry is None and reason is ReasonCode.INVALID_BINDING
    elif state is TurnIntakeState.BLOCKED_SECURITY_POLICY:
        valid = valid and next_retry is None and reason is ReasonCode.SECURITY_POLICY
    else:
        valid = False
    if not valid:
        raise LedgerError("invalid_turn_intake_row")


def _valid_ledger_ref(value: Any) -> bool:
    return isinstance(value, str) and _REF_RE.fullmatch(value) is not None


def _valid_internal_row_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= _MAX_REFERENCE_INPUT and "\0" not in value


def _is_db_timestamp(value: Any) -> bool:
    return isinstance(value, datetime)


def _ledger_reference_key(value: Any) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise LedgerError("reference_key_mismatch")
    return value


def _ledger_opaque_id(reference_key: bytes, domain: str, values: Sequence[str], prefix: str) -> str:
    payload = "\0".join(values).encode("utf-8")
    return f"{prefix}_" + hmac.new(reference_key, domain.encode("ascii") + b"\0" + payload, hashlib.sha256).hexdigest()[:32]


def _validate_ledger_ref(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise LedgerError(f"invalid_{label}")


def _validate_binding_id(value: Any) -> None:
    if not isinstance(value, str) or not _BINDING_REF_RE.fullmatch(value):
        raise LedgerError("invalid_binding_id")


def _validate_turn_id(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"t1_[0-9a-f]{32}", value):
        raise LedgerError("invalid_turn_intake_id")


def _validate_scope(value: Any) -> None:
    if value not in {"normal", "secure"}:
        raise LedgerError("invalid_scope")


def _validate_local_day(value: Any) -> None:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise LedgerError("invalid_rollover_local_day") from error
    if parsed.isoformat() != value:
        raise LedgerError("invalid_rollover_local_day")


def _valid_iso_day(value: Any) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def _validate_internal_id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_REFERENCE_INPUT or "\0" in value:
        raise LedgerError(f"invalid_{label}")


def _validate_session_owner(
    database: Any, session_table: Any, reference_key: bytes, session_id: str, owner_reference: str
) -> None:
    if not _session_belongs_to_owner(
        database, session_table, reference_key, session_id, owner_reference
    ):
        raise LedgerError("invalid_active_session_owner")


def _session_belongs_to_owner(
    database: Any, session_table: Any, reference_key: bytes, session_id: str, owner_reference: str
) -> bool:
    row = _select_one(database, session_table, session_table.c.id == session_id)
    if row is None or not isinstance(row.get("owner"), str):
        return False
    try:
        return hmac.compare_digest(owner_ref(reference_key, row["owner"]), owner_reference)
    except ReferenceError:
        return False


def _ledger_timestamp(value: datetime) -> datetime:
    _require_aware(value)
    return value.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _ledger_now() -> datetime:
    return datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)


def _turn_reason_for_event(event: TurnIntakeEvent) -> ReasonCode | None:
    if event is TurnIntakeEvent.INDETERMINATE:
        return ReasonCode.INDETERMINATE_TURN_PAIR
    if event is TurnIntakeEvent.INVALID_BINDING:
        return ReasonCode.INVALID_BINDING
    if event is TurnIntakeEvent.SECURITY_POLICY_BLOCKED:
        return ReasonCode.SECURITY_POLICY
    return None


_EVIDENCE_FIELDS = frozenset(
    {
        "owner_ref",
        "chat_handle_ref",
        "binding_ref",
        "old_session_ref",
        "new_session_ref",
        "session_ref",
        "scope",
        "rollover_local_day",
        "state",
        "reason_code",
        "generation",
        "attempt_count",
        "due_count",
        "committed_count",
        "deferred_count",
        "raw_content_absent",
        "raw_identity_absent",
    }
)


def build_rollover_evidence(**fields: Any) -> Mapping[str, Any]:
    """Build a bounded content-free evidence mapping from allowlisted fields."""

    unexpected = set(fields) - _EVIDENCE_FIELDS
    if unexpected:
        raise ValueError("forbidden_evidence_field")
    for name, value in fields.items():
        _validate_evidence_field(name, value)
    # A mapping proxy prevents a caller from appending unreviewed raw data after
    # this boundary while retaining ergonomic dict-style access for serializers.
    return MappingProxyType(dict(fields))


def _strict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in {"true", "false"}:
        return value == "true"
    raise ConfigurationError("invalid_boolean")


def _parse_timezone(value: Any) -> ZoneInfo:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ConfigurationError("invalid_timezone")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ConfigurationError("invalid_timezone") from error


def _parse_boundary(value: Any) -> time:
    if not isinstance(value, str) or not _BOUNDARY_RE.fullmatch(value):
        raise ConfigurationError("invalid_boundary")
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ConfigurationError("invalid_integer")
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9]+", value):
            raise ConfigurationError("invalid_integer")
        value = int(value)
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigurationError("invalid_integer")
    return value


def _parse_reference_key(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, bytes) or len(value) < 32:
        raise ConfigurationError("invalid_reference_key")
    return value


def _error_code(error: Exception) -> str:
    code = str(error)
    return code if re.fullmatch(r"[a-z_]{1,64}", code) else "invalid_configuration"


def _bounded_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ReferenceError(f"invalid_{label}")
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_REFERENCE_INPUT or "\x00" in normalized:
        raise ReferenceError(f"invalid_{label}")
    return normalized


def _optional_bounded_integer(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 10**20:
        raise ReferenceError(f"invalid_{label}")
    return value


def _keyed_ref(reference_key: bytes, domain: str, normalized: str) -> str:
    if not isinstance(reference_key, bytes) or len(reference_key) < 32:
        raise ReferenceError("invalid_reference_key")
    if len(normalized) > _MAX_REFERENCE_INPUT:
        raise ReferenceError("reference_input_too_long")
    digest = hmac.new(
        reference_key, f"{domain}\0{normalized}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"h1_{digest}"


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_datetime_required")


def _next_retry(now: datetime, lease_expires_at: datetime | None, retry_seconds: int) -> datetime:
    retry_at = now + timedelta(seconds=retry_seconds)
    if lease_expires_at is None:
        return retry_at
    return min(retry_at, lease_expires_at)


def _validate_record(record: RolloverRecord, config: RolloverConfig) -> None:
    if not isinstance(record.state, RolloverState):
        raise ValueError("invalid_rollover_state")
    if record.reason_code is not None and not isinstance(record.reason_code, ReasonCode):
        raise ValueError("invalid_rollover_reason")
    if (
        isinstance(record.attempt_count, bool)
        or not isinstance(record.attempt_count, int)
        or not 0 <= record.attempt_count <= config.max_attempts
    ):
        raise ValueError("invalid_attempt_count")
    if record.retry_after is not None:
        _require_aware(record.retry_after)
    if record.state is RolloverState.ABSENT:
        valid = record.attempt_count == 0 and record.retry_after is None and record.reason_code is None
    elif record.state is RolloverState.DEFERRED_ACTIVE_TURN:
        valid = (
            1 <= record.attempt_count < config.max_attempts
            and record.retry_after is not None
            and record.reason_code is ReasonCode.ACTIVE_TURN
        )
    elif record.state is RolloverState.DEFERRED_EXHAUSTED:
        valid = (
            record.attempt_count == config.max_attempts
            and record.retry_after is not None
            and record.reason_code is ReasonCode.RETRY_EXHAUSTED
        )
    elif record.state is RolloverState.BLOCKED_INVALID_BINDING:
        valid = (
            record.attempt_count == 0
            and record.retry_after is None
            and record.reason_code is ReasonCode.INVALID_BINDING
        )
    elif record.state is RolloverState.BLOCKED_SECURITY_POLICY:
        valid = (
            record.attempt_count == 0
            and record.retry_after is None
            and record.reason_code is ReasonCode.SECURITY_POLICY
        )
    else:  # COMMITTED, after the enum check above.
        valid = record.retry_after is None and (
            record.reason_code is None
            or (
                record.reason_code is ReasonCode.EXPIRED_TURN_LEASE_RECOVERED
                and record.attempt_count >= 1
            )
        )
    if not valid:
        raise ValueError("invalid_rollover_record_shape")


def _validate_policy_config(config: RolloverConfig) -> None:
    if not isinstance(config, RolloverConfig):
        raise ValueError("invalid_rollover_config")
    if (
        config.enabled is not True
        or not isinstance(config.continuity_enabled, bool)
        or config.invalid_reason is not None
        or not isinstance(config.reference_key, bytes)
        or len(config.reference_key) < 32
        or not isinstance(config.timezone, ZoneInfo)
        or not isinstance(config.boundary, time)
        or config.boundary.tzinfo is not None
        or config.boundary.second != 0
        or config.boundary.microsecond != 0
        or isinstance(config.max_attempts, bool)
        or not isinstance(config.max_attempts, int)
        or not 1 <= config.max_attempts <= 24
        or isinstance(config.retry_seconds, bool)
        or not isinstance(config.retry_seconds, int)
        or not 60 <= config.retry_seconds <= 3600
        or isinstance(config.turn_lease_seconds, bool)
        or not isinstance(config.turn_lease_seconds, int)
        or not 60 <= config.turn_lease_seconds <= 14400
    ):
        raise ValueError("invalid_rollover_config")


def _validate_evidence_field(name: str, value: Any) -> None:
    if name in {"owner_ref", "chat_handle_ref", "old_session_ref", "new_session_ref", "session_ref"}:
        if not isinstance(value, str) or not _REF_RE.fullmatch(value):
            raise ValueError("invalid_evidence_ref")
    elif name == "binding_ref":
        if not isinstance(value, str) or not _BINDING_REF_RE.fullmatch(value):
            raise ValueError("invalid_evidence_ref")
    elif name == "scope" and value not in {"normal", "secure"}:
        raise ValueError("invalid_evidence_scope")
    elif name == "rollover_local_day":
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_evidence_day") from error
    elif name == "state" and value not in {
        *(item.value for item in RolloverState),
        *(item.value for item in TurnIntakeState),
    }:
        raise ValueError("invalid_evidence_state")
    elif name == "reason_code" and value is not None and value not in {item.value for item in ReasonCode}:
        raise ValueError("invalid_evidence_reason")
    elif name in {"generation", "attempt_count", "due_count", "committed_count", "deferred_count"}:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
            raise ValueError("invalid_evidence_count")
    elif name in {"raw_content_absent", "raw_identity_absent"} and value is not True:
        raise ValueError("invalid_evidence_boolean")


def _is_exact_turn_pair(telegram_turn_ref: str, markers: Any) -> bool:
    """Safely recognize the sole automatic-recovery-safe marker shape."""

    if not isinstance(markers, Sequence) or isinstance(markers, (str, bytes, bytearray)):
        return False
    if len(markers) != 2:
        return False
    expected_roles = {"user", "assistant"}
    observed_roles: list[str] = []
    for marker in markers:
        if not isinstance(marker, TurnMessageMarker):
            return False
        if (
            not isinstance(marker.role, str)
            or not isinstance(marker.telegram_turn_ref, str)
            or marker.role not in expected_roles
            or marker.telegram_turn_ref != telegram_turn_ref
        ):
            return False
        observed_roles.append(marker.role)
    return set(observed_roles) == expected_roles
