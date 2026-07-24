"""Pure, default-off policy for the Telegram session-rollover contract.

This module deliberately contains no environment access, persistence, network, or
runtime integration.  Callers supply their configuration mapping and clock, then
persist the decisions through the later transactional coordinator slice.
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
