from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.unified_source_index_change_outbox import (
    BestEffortNotificationResult,
    CHANGE_OUTBOX_SCHEMA,
    LEGACY_CHANGE_OUTBOX_SCHEMA,
    SourceChangeOutbox,
    SourceChangeOutboxConflict,
    SourceChangeOutboxError,
    SourceChangeOutboxErrorCode,
    SourceChangeOutboxIntegrityError,
    SourceChangeOutboxStatus,
    notify_best_effort_after_domain_commit,
)
from src.unified_source_index_change_signal import (
    SourceChangeKind,
    SourceChangeSignal,
    SourceChangeSignalError,
)


OWNER_SCOPE = "owner:owner_" + "a" * 32
SOURCE_REF = "source_ref_" + "b" * 64
WORKER_A = "usi_worker_" + "a" * 64
WORKER_B = "usi_worker_" + "b" * 64
WORKER_C = "usi_worker_" + "c" * 64


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _signal(
    *,
    change_kind: SourceChangeKind | str = SourceChangeKind.CHANGED,
    revision_hint: str = "rev_00000001",
) -> SourceChangeSignal:
    return SourceChangeSignal.create(
        change_kind=change_kind,
        owner_scope=OWNER_SCOPE,
        domain="personal_docs",
        source_ref=SOURCE_REF,
        revision_hint=revision_hint,
    )


def _outbox(tmp_path, clock: MutableClock | None = None, **kwargs) -> SourceChangeOutbox:
    return SourceChangeOutbox(root=tmp_path / "source-change-outbox", clock=clock, **kwargs)


@pytest.mark.parametrize("change_kind", tuple(SourceChangeKind))
def test_signal_accepts_only_the_closed_change_kind_set_and_has_deterministic_identity(change_kind):
    first = _signal(change_kind=change_kind)
    replay = _signal(change_kind=change_kind)

    assert first == replay
    assert first.idempotency_key == replay.idempotency_key
    assert first.idempotency_key.startswith("usi_change_")
    assert set(first.to_dict()) == {
        "schema",
        "change_kind",
        "owner_scope",
        "domain",
        "source_ref",
        "revision_hint",
        "idempotency_key",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"change_kind": "renamed"}, "change_kind"),
        ({"owner_scope": "user:alice"}, "owner_scope"),
        ({"source_ref": "C:/synthetic/notes.txt"}, "source_ref"),
        ({"source_ref": "repo:alpha/src/secret.txt"}, "source_ref"),
        ({"revision_hint": "../contains-a-path"}, "revision_hint"),
        ({"revision_hint": "source body is not a revision hint"}, "revision_hint"),
    ],
)
def test_signal_rejects_nonopaque_identity_content_and_paths(overrides, message):
    values = {
        "change_kind": SourceChangeKind.CHANGED,
        "owner_scope": OWNER_SCOPE,
        "domain": "personal_docs",
        "source_ref": SOURCE_REF,
        "revision_hint": "rev_00000001",
    }
    values.update(overrides)

    with pytest.raises(SourceChangeSignalError, match=message):
        SourceChangeSignal.create(**values)


def test_signal_payload_schema_rejects_an_injected_body_or_provider_response_field():
    payload = _signal().to_dict()
    payload["body"] = "synthetic body"
    with pytest.raises(SourceChangeSignalError, match="schema"):
        SourceChangeSignal.from_dict(payload)

    payload = _signal().to_dict()
    payload["provider_response"] = "synthetic response"
    with pytest.raises(SourceChangeSignalError, match="schema"):
        SourceChangeSignal.from_dict(payload)


def test_signal_accepts_a_domain_opaque_reference_before_usi_has_created_its_source_record():
    signal = SourceChangeSignal.create(
        change_kind=SourceChangeKind.CREATED,
        owner_scope=OWNER_SCOPE,
        domain="personal_docs",
        source_ref="source_ref_" + "c" * 64,
        revision_hint="sha256:" + "d" * 64,
    )

    assert signal.source_ref.startswith("source_ref_")
    assert signal.revision_hint.startswith("sha256:")


def test_durable_enqueue_is_replay_safe_fails_closed_on_conflict_and_persists_no_content(tmp_path):
    outbox = _outbox(tmp_path)
    first = outbox.enqueue(_signal())
    replay = outbox.enqueue(_signal())

    assert replay == first
    assert len(outbox.list_records()) == 1
    with pytest.raises(SourceChangeOutboxConflict, match="idempotency"):
        outbox.enqueue(_signal(), max_attempts=2)

    raw = next((tmp_path / "source-change-outbox").glob("*.json")).read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["schema"] == CHANGE_OUTBOX_SCHEMA
    assert raw == json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert "body" not in raw and "provider_response" not in raw and "C:/" not in raw
    assert payload["signal"]["source_ref"] == SOURCE_REF


def test_leases_are_fenced_expire_to_retry_and_respect_the_bounded_retry_budget(tmp_path):
    clock = MutableClock()
    outbox = _outbox(tmp_path, clock, default_max_attempts=2, retry_base_seconds=5)
    original = outbox.enqueue(_signal())

    first = outbox.claim_due(lease_owner=WORKER_A, lease_seconds=10)[0]
    assert first.status is SourceChangeOutboxStatus.LEASED
    assert first.attempts == 1
    assert outbox.claim_due(lease_owner=WORKER_B) == ()

    clock.advance(seconds=11)
    recovered = outbox.reconcile()[0]
    assert recovered.status is SourceChangeOutboxStatus.RETRY_SCHEDULED
    assert recovered.last_error_code == "lease_expired"
    assert outbox.claim_due(lease_owner=WORKER_B) == ()

    clock.advance(seconds=5)
    second = outbox.claim_due(lease_owner=WORKER_B)[0]
    assert second.attempts == 2
    with pytest.raises(SourceChangeOutboxConflict, match="stale"):
        outbox.mark_delivered(
            idempotency_key=original.idempotency_key,
            lease_owner=WORKER_A,
            lease_token=first.lease_token or "",
        )
    terminal = outbox.schedule_retry(
        idempotency_key=original.idempotency_key,
        lease_owner=WORKER_B,
        lease_token=second.lease_token or "",
        error_code=SourceChangeOutboxErrorCode.RETRYABLE_DELIVERY_FAILED,
    )
    assert terminal.status is SourceChangeOutboxStatus.FAILED
    assert outbox.claim_due(lease_owner=WORKER_C) == ()


@pytest.mark.parametrize(
    "unsafe_owner",
    (
        "api_token_" + "a" * 64,
        "../worker",
        "worker body content",
        "github_worker_" + "a" * 64,
    ),
)
def test_lease_owner_rejects_credential_path_body_and_provider_like_values(tmp_path, unsafe_owner):
    outbox = _outbox(tmp_path)
    outbox.enqueue(_signal())

    with pytest.raises(SourceChangeOutboxError, match="lease_owner"):
        outbox.claim_due(lease_owner=unsafe_owner)


@pytest.mark.parametrize(
    "unsafe_code",
    (
        "api_token_value",
        "../delivery-error",
        "raw body content",
        "github_provider_response",
    ),
)
def test_retry_error_rejects_credential_path_body_and_provider_like_values(tmp_path, unsafe_code):
    outbox = _outbox(tmp_path)
    outbox.enqueue(_signal())
    lease = outbox.claim_due(lease_owner=WORKER_A)[0]

    with pytest.raises(SourceChangeOutboxError, match="error_code"):
        outbox.schedule_retry(
            idempotency_key=lease.idempotency_key,
            lease_owner=WORKER_A,
            lease_token=lease.lease_token or "",
            error_code=unsafe_code,
        )
    assert outbox.load(lease.idempotency_key).status is SourceChangeOutboxStatus.LEASED


def test_fenced_delivery_marks_only_the_current_worker_lease_complete(tmp_path):
    outbox = _outbox(tmp_path)
    enqueued = outbox.enqueue(_signal())
    lease = outbox.claim_due(lease_owner=WORKER_A)[0]

    delivered = outbox.mark_delivered(
        idempotency_key=enqueued.idempotency_key,
        lease_owner=WORKER_A,
        lease_token=lease.lease_token or "",
    )

    assert delivered.status is SourceChangeOutboxStatus.DELIVERED
    assert delivered.lease_owner is None
    assert outbox.claim_due(lease_owner=WORKER_B) == ()


def test_best_effort_notifier_failure_is_nonthrowing_and_discovery_remains_the_fallback(tmp_path):
    outbox = _outbox(tmp_path)

    def failing_notifier(_record) -> None:
        raise RuntimeError("synthetic notifier outage")

    result = notify_best_effort_after_domain_commit(
        outbox=outbox,
        signal=_signal(),
        notifier=failing_notifier,
    )

    assert result.outbox_accepted is True
    assert result.notifier_attempted is True
    assert result.notifier_delivered is False
    assert result.error_code == "notifier_failed"
    assert outbox.periodic_discovery_is_correctness_fallback is True
    assert outbox.load(_signal().idempotency_key).status is SourceChangeOutboxStatus.PENDING


def test_best_effort_storage_failure_is_nonthrowing_after_a_domain_commit(tmp_path):
    occupied_root = tmp_path / "occupied-root"
    occupied_root.write_text("synthetic", encoding="utf-8")
    outbox = SourceChangeOutbox(root=occupied_root)

    result = notify_best_effort_after_domain_commit(outbox=outbox, signal=_signal())

    assert result.outbox_accepted is False
    assert result.notifier_attempted is False
    assert result.notifier_delivered is False
    assert result.error_code == "outbox_unavailable"


def test_best_effort_contains_an_unexpected_clock_runtime_error_after_domain_commit(tmp_path):
    def failing_clock() -> datetime:
        raise RuntimeError("synthetic clock failure")

    outbox = _outbox(tmp_path, failing_clock)

    result = notify_best_effort_after_domain_commit(outbox=outbox, signal=_signal())

    assert result == BestEffortNotificationResult(
        False,
        False,
        False,
        SourceChangeOutboxErrorCode.OUTBOX_UNAVAILABLE,
    )


def test_best_effort_contains_an_unexpected_enqueue_value_error_after_domain_commit(tmp_path):
    class UnexpectedValueErrorOutbox(SourceChangeOutbox):
        def enqueue(self, signal, *, max_attempts=None):
            raise ValueError("synthetic enqueue failure")

    outbox = UnexpectedValueErrorOutbox(root=tmp_path / "unexpected-value-error")

    result = notify_best_effort_after_domain_commit(outbox=outbox, signal=_signal())

    assert result == BestEffortNotificationResult(
        False,
        False,
        False,
        SourceChangeOutboxErrorCode.OUTBOX_UNAVAILABLE,
    )


def test_best_effort_does_not_swallow_base_exceptions_at_enqueue_or_notifier_boundary(tmp_path):
    class InterruptingOutbox(SourceChangeOutbox):
        def enqueue(self, signal, *, max_attempts=None):
            raise KeyboardInterrupt("synthetic interrupt")

    with pytest.raises(KeyboardInterrupt, match="synthetic interrupt"):
        notify_best_effort_after_domain_commit(
            outbox=InterruptingOutbox(root=tmp_path / "interrupting-outbox"),
            signal=_signal(),
        )

    def interrupting_notifier(_record) -> None:
        raise KeyboardInterrupt("synthetic notifier interrupt")

    with pytest.raises(KeyboardInterrupt, match="synthetic notifier interrupt"):
        notify_best_effort_after_domain_commit(
            outbox=_outbox(tmp_path),
            signal=_signal(revision_hint="rev_00000003"),
            notifier=interrupting_notifier,
        )


def test_best_effort_rejects_programmer_type_misuse_before_the_post_commit_attempt(tmp_path):
    with pytest.raises(SourceChangeOutboxError, match="outbox must"):
        notify_best_effort_after_domain_commit(outbox=object(), signal=_signal())
    with pytest.raises(SourceChangeOutboxError, match="signal must"):
        notify_best_effort_after_domain_commit(outbox=_outbox(tmp_path), signal=object())


@pytest.mark.parametrize(
    "values",
    (
        (True, False, True, ""),
        (False, True, False, SourceChangeOutboxErrorCode.NOTIFIER_FAILED),
        (True, True, False, ""),
        (True, True, True, SourceChangeOutboxErrorCode.NOTIFIER_FAILED),
        (False, False, False, ""),
        (True, False, False, SourceChangeOutboxErrorCode.OUTBOX_UNAVAILABLE),
        (True, True, False, "github_provider_response"),
        (1, False, False, SourceChangeOutboxErrorCode.OUTBOX_UNAVAILABLE),
    ),
)
def test_best_effort_result_rejects_inconsistent_flags_or_open_error_codes(values):
    with pytest.raises(SourceChangeOutboxError):
        BestEffortNotificationResult(*values)


def test_noncanonical_or_tampered_persistence_fails_closed(tmp_path):
    outbox = _outbox(tmp_path)
    record = outbox.enqueue(_signal())
    path = next((tmp_path / "source-change-outbox").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(SourceChangeOutboxIntegrityError, match="canonical"):
        outbox.load(record.idempotency_key)

    unsafe_payload = {**payload, "source_path": "relative/synthetic.txt"}
    path.write_text(
        json.dumps(unsafe_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(SourceChangeOutboxIntegrityError, match="schema"):
        outbox.load(record.idempotency_key)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "status": "delivered",
                "attempts": 0,
            },
            "delivered",
        ),
        (
            {
                "status": "retry_scheduled",
                "attempts": 8,
                "max_attempts": 8,
                "next_attempt_at": "2026-08-03T12:00:05Z",
                "last_error_code": "retryable_delivery_failed",
            },
            "retry",
        ),
        (
            {
                "status": "retry_scheduled",
                "attempts": 1,
                "next_attempt_at": "2026-08-03T12:00:00Z",
                "last_error_code": "retryable_delivery_failed",
            },
            "later",
        ),
        (
            {
                "status": "failed",
                "attempts": 1,
                "max_attempts": 8,
                "last_error_code": "retryable_delivery_failed",
            },
            "failed",
        ),
    ],
)
def test_canonical_persisted_status_invariant_violations_fail_closed(tmp_path, overrides, message):
    clock = MutableClock()
    outbox = _outbox(tmp_path, clock)
    record = outbox.enqueue(_signal())
    path = next((tmp_path / "source-change-outbox").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(overrides)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(SourceChangeOutboxIntegrityError, match=message):
        outbox.load(record.idempotency_key)


def test_safe_legacy_v1_is_read_only_until_an_ordinary_mutation_rewrites_v2(tmp_path):
    clock = MutableClock()
    outbox = _outbox(tmp_path, clock)
    record = outbox.enqueue(_signal())
    path = next((tmp_path / "source-change-outbox").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema"] = LEGACY_CHANGE_OUTBOX_SCHEMA
    legacy_raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    path.write_text(legacy_raw, encoding="utf-8")

    loaded = outbox.load(record.idempotency_key)
    assert loaded.persisted_schema == LEGACY_CHANGE_OUTBOX_SCHEMA
    assert path.read_text(encoding="utf-8") == legacy_raw

    claimed = outbox.claim_due(lease_owner=WORKER_A)[0]
    assert claimed.persisted_schema == CHANGE_OUTBOX_SCHEMA
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["schema"] == CHANGE_OUTBOX_SCHEMA
    assert migrated["lease_owner"] == WORKER_A


@pytest.mark.parametrize(
    "unsafe_overrides",
    (
        {"lease_owner": "worker_a"},
        {"last_error_code": "github_provider_response"},
        {"last_error_code": "notifier_failed"},
    ),
)
def test_unsafe_legacy_v1_fails_closed_without_rewrite_or_delete(tmp_path, unsafe_overrides):
    clock = MutableClock()
    outbox = _outbox(tmp_path, clock)
    record = outbox.enqueue(_signal())
    lease = outbox.claim_due(lease_owner=WORKER_A)[0]
    path = next((tmp_path / "source-change-outbox").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema"] = LEGACY_CHANGE_OUTBOX_SCHEMA
    payload.update(unsafe_overrides)
    unsafe_raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    path.write_text(unsafe_raw, encoding="utf-8")

    with pytest.raises(SourceChangeOutboxIntegrityError):
        outbox.load(record.idempotency_key)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == unsafe_raw
    assert lease.persisted_schema == CHANGE_OUTBOX_SCHEMA


def test_claim_due_recovers_an_expired_lease_and_eventually_reclaims_without_sticking(tmp_path):
    clock = MutableClock()
    outbox = _outbox(tmp_path, clock, default_max_attempts=2, retry_base_seconds=5)
    outbox.enqueue(_signal())
    first = outbox.claim_due(lease_owner=WORKER_A, lease_seconds=10)[0]

    clock.advance(seconds=11)
    assert outbox.claim_due(lease_owner=WORKER_B) == ()
    recovered = outbox.load(first.idempotency_key)
    assert recovered.status is SourceChangeOutboxStatus.RETRY_SCHEDULED
    assert recovered.attempts == 1

    clock.advance(seconds=5)
    second = outbox.claim_due(lease_owner=WORKER_B)[0]
    assert second.attempts == 2
    delivered = outbox.mark_delivered(
        idempotency_key=second.idempotency_key,
        lease_owner=WORKER_B,
        lease_token=second.lease_token or "",
    )
    assert delivered.status is SourceChangeOutboxStatus.DELIVERED


def test_outbox_capacity_is_bounded_and_fails_closed_without_hiding_discovery_fallback(tmp_path):
    outbox = _outbox(tmp_path, max_records=1)
    outbox.enqueue(_signal())
    with pytest.raises(SourceChangeOutboxConflict, match="capacity"):
        outbox.enqueue(_signal(revision_hint="rev_00000002"))
    assert outbox.periodic_discovery_is_correctness_fallback is True
