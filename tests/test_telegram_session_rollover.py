from datetime import datetime, time, timedelta, timezone
import hashlib
import hmac

import pytest

from src.telegram_session_rollover import (
    ReasonCode,
    RolloverConfig,
    RolloverEvent,
    RolloverRecord,
    RolloverState,
    TurnIntakeEvent,
    TurnIntakeState,
    TurnMessageMarker,
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
