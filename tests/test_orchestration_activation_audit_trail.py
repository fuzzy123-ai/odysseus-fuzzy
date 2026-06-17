from src.orchestration_activation_audit_trail import (
    ActivationAuditError,
    ActivationAuditEvent,
    ActivationAuditTrail,
)


def test_event_creation_sanitizes_and_deduplicates_refs():
    event = ActivationAuditEvent.create(
        event_id=" Event 1 ",
        event_type="activation_requested",
        run_id="Run 1",
        slice_id="AUTO16B",
        actor="Charlie",
        timestamp="2026-06-17T10:00:00Z",
        decision="prepare_dispatch",
        reason="token=secret123 requested by operator",
        evidence_refs=("proof-b", "proof-a", "proof-a"),
        changed_files=("src/z.py", "src/a.py", "src/a.py"),
        test_refs=("pytest b", "pytest a", "pytest a"),
    )

    assert event.event_id == "event-1"
    assert event.reason == "[REDACTED]=[REDACTED] requested by operator"
    assert event.evidence_refs == ("proof-a", "proof-b")
    assert event.changed_files == ("src/a.py", "src/z.py")
    assert event.test_refs == ("pytest a", "pytest b")


def test_append_event_returns_new_trail_without_mutating_original():
    first = ActivationAuditEvent.create(
        event_id="evt-1",
        event_type="activation_requested",
        run_id="run-1",
        slice_id="auto16b",
        actor="charlie",
        timestamp="2026-06-17T10:00:00Z",
        decision="prepare_dispatch",
        reason="operator requested activation review",
    )
    second = ActivationAuditEvent.create(
        event_id="evt-2",
        event_type="preflight_checked",
        run_id="run-1",
        slice_id="auto16b",
        actor="bob",
        timestamp="2026-06-17T10:05:00Z",
        decision="dry_run_only",
        reason="preflight completed without runtime execution",
    )

    original = ActivationAuditTrail.create((first,))
    extended = original.append_event(second)

    assert len(original.events) == 1
    assert len(extended.events) == 2
    assert extended.events[-1].event_id == "evt-2"


def test_append_only_order_is_validated():
    first = ActivationAuditEvent.create(
        event_id="evt-1",
        event_type="activation_requested",
        run_id="run-1",
        slice_id="auto16b",
        actor="charlie",
        timestamp="2026-06-17T10:05:00Z",
        decision="prepare_dispatch",
        reason="operator requested activation review",
    )
    second = ActivationAuditEvent.create(
        event_id="evt-2",
        event_type="activation_deferred",
        run_id="run-1",
        slice_id="auto16b",
        actor="charlie",
        timestamp="2026-06-17T10:00:00Z",
        decision="defer",
        reason="preflight not yet complete",
    )

    try:
        ActivationAuditTrail.create((first, second))
    except ActivationAuditError as exc:
        assert "timestamp order" in str(exc)
    else:
        raise AssertionError("expected ActivationAuditError")


def test_duplicate_event_ids_are_rejected():
    event = ActivationAuditEvent.create(
        event_id="evt-1",
        event_type="gate_passed",
        run_id="run-1",
        slice_id="auto16b",
        actor="bob",
        timestamp="2026-06-17T10:00:00Z",
        decision="read_only",
        reason="gate passed",
    )

    try:
        ActivationAuditTrail.create((event, event))
    except ActivationAuditError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("expected ActivationAuditError")


def test_to_dict_is_stable():
    trail = ActivationAuditTrail.create(
        (
            ActivationAuditEvent.create(
                event_id="evt-1",
                event_type="activation_requested",
                run_id="run-1",
                slice_id="auto16b",
                actor="charlie",
                timestamp="2026-06-17T10:00:00Z",
                decision="prepare_dispatch",
                reason="operator requested activation review",
                evidence_refs=("proof-b", "proof-a"),
                changed_files=("src/b.py", "src/a.py"),
                test_refs=("pytest b", "pytest a"),
            ),
        )
    )

    assert trail.to_dict() == {
        "events": (
            {
                "event_id": "evt-1",
                "event_type": "activation_requested",
                "run_id": "run-1",
                "slice_id": "auto16b",
                "actor": "charlie",
                "timestamp": "2026-06-17T10:00:00Z",
                "decision": "prepare_dispatch",
                "reason": "operator requested activation review",
                "evidence_refs": ("proof-a", "proof-b"),
                "changed_files": ("src/a.py", "src/b.py"),
                "test_refs": ("pytest a", "pytest b"),
            },
        ),
    }
