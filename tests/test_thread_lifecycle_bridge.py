from src.thread_lifecycle_bridge import (
    DispatchAction,
    ThreadDispatchDecision,
    ThreadDispatchRequest,
    ThreadLifecycleSnapshot,
    ThreadReadSnapshot,
    ThreadRef,
    ThreadStatus,
    ThreadTurnSummary,
    build_lifecycle_snapshot_from_thread_read,
)


def _thread_ref() -> ThreadRef:
    return ThreadRef.create(
        thread_id="019eccdd-b25b-7ae2-89e5-e4b568943fa6",
        agent_id="bob-worker",
        agent_run_id="run-42",
        plan_id="or3-plan",
        node_id="node-1",
    )


def _request(**overrides) -> ThreadDispatchRequest:
    data = {
        "thread_ref": _thread_ref(),
        "expected_agent_id": "bob-worker",
        "expected_agent_run_id": "run-42",
        "expected_node_id": "node-1",
        "prompt_summary": "Dispatch the next backend slice.",
        "allowed_action": "send",
    }
    data.update(overrides)
    return ThreadDispatchRequest.create(**data)


def test_snapshot_and_request_normalize_stably():
    thread_ref = ThreadRef.create(
        thread_id=" thread-123 ",
        agent_id=" Bob Worker ",
        agent_run_id=" Run 42 ",
        plan_id=" OR3 Plan ",
        node_id=" Node 1 ",
    )
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=thread_ref,
        thread_status="idle",
        last_seen_turn="7",
        handoff_status="none",
    )
    request = ThreadDispatchRequest.create(
        thread_ref=thread_ref,
        expected_agent_id=" Bob Worker ",
        expected_agent_run_id=" Run 42 ",
        expected_node_id=" Node 1 ",
        prompt_summary="  Dispatch the next backend slice.  ",
        allowed_action="send",
    )

    assert snapshot.thread_ref.agent_id == "bob-worker"
    assert snapshot.last_seen_turn == 7
    assert request.expected_agent_run_id == "run-42"
    assert request.prompt_summary == "Dispatch the next backend slice."


def test_ambiguous_thread_blocks_dispatch():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="ambiguous",
        last_seen_turn=8,
        handoff_status="ambiguous",
    )

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert decision.action == DispatchAction.BLOCKED
    assert decision.allowed is False
    assert decision.reason == "ambiguous_thread"


def test_thread_read_snapshot_extracts_ready_handoff_without_raw_content_dump():
    read = ThreadReadSnapshot.create(
        thread_ref=_thread_ref(),
        observed_turn_count=12,
        read_at="2026-06-21T07:45:00Z",
        turns=[
            ThreadTurnSummary.create(
                turn_index=12,
                actor="bob-worker",
                summary="structured handoff ready with focused tests",
                handoff_status="ready_for_handoff",
                status_hint="completed",
            )
        ],
    )

    snapshot = build_lifecycle_snapshot_from_thread_read(read)
    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert snapshot.thread_status == ThreadStatus.COMPLETED
    assert snapshot.last_seen_turn == 12
    assert snapshot.handoff_status == "ready_for_handoff"
    assert decision.action == DispatchAction.RESOLVE
    assert "structured handoff ready" not in repr(read.audit_summary())


def test_thread_read_ambiguity_becomes_hard_dispatch_block():
    read = ThreadReadSnapshot.create(
        thread_ref=_thread_ref(),
        observed_turn_count=13,
        read_at="2026-06-21T07:46:00Z",
        turns=[],
        ambiguous_reason="two candidate runs claim this thread",
    )

    snapshot = build_lifecycle_snapshot_from_thread_read(read)
    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert snapshot.thread_status == ThreadStatus.AMBIGUOUS
    assert snapshot.handoff_status == "ambiguous"
    assert decision.action == DispatchAction.BLOCKED
    assert decision.reason == "ambiguous_thread"


def test_thread_read_without_new_turns_goes_stale_unless_resolved():
    previous = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="idle",
        last_seen_turn=8,
        handoff_status="none",
        acknowledged_at="2026-06-21T07:40:00Z",
    )
    read = ThreadReadSnapshot.create(
        thread_ref=_thread_ref(),
        observed_turn_count=8,
        read_at="2026-06-21T07:47:00Z",
        turns=[],
    )

    stale = build_lifecycle_snapshot_from_thread_read(read, previous_snapshot=previous)

    assert stale.thread_status == ThreadStatus.STALE
    assert stale.last_seen_turn == 8


def test_resolved_thread_read_is_not_reprocessed_without_new_turns():
    previous = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="completed",
        last_seen_turn=14,
        handoff_status="resolved",
        dispatch_intent="resolve_handoff",
        acknowledged_at="2026-06-21T07:40:00Z",
        resolved_at="2026-06-21T07:41:00Z",
    )
    read = ThreadReadSnapshot.create(
        thread_ref=_thread_ref(),
        observed_turn_count=14,
        read_at="2026-06-21T07:48:00Z",
        turns=[],
    )

    snapshot = build_lifecycle_snapshot_from_thread_read(read, previous_snapshot=previous)

    assert snapshot is previous


def test_running_thread_is_not_restarted():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="running",
        last_seen_turn=9,
        handoff_status="none",
    )

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert decision.action == DispatchAction.WAIT
    assert decision.allowed is False
    assert decision.reason == "thread_already_running"


def test_idle_matching_thread_allows_send():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="idle",
        last_seen_turn=10,
        handoff_status="none",
    )

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert decision.action == DispatchAction.SEND
    assert decision.allowed is True
    assert decision.reason == "idle_thread_ready_for_dispatch"


def test_completed_handoff_prefers_resolve_not_resend():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="completed",
        last_seen_turn=11,
        handoff_status="ready_for_handoff",
    )

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert decision.action == DispatchAction.RESOLVE
    assert decision.allowed is False
    assert decision.reason == "thread_handoff_ready"
    assert decision.required_user_action == "advance_next_slice"


def test_agent_or_run_mismatch_blocks():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=ThreadRef.create(
            thread_id="019eccdd-b25b-7ae2-89e5-e4b568943fa6",
            agent_id="alice-worker",
            agent_run_id="run-99",
            plan_id="or3-plan",
            node_id="node-1",
        ),
        thread_status="idle",
        last_seen_turn=5,
        handoff_status="none",
    )

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert decision.action == DispatchAction.BLOCKED
    assert decision.reason == "agent_mismatch"


def test_snapshot_validates_contract_fields_and_timestamp_order():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status="completed",
        last_seen_turn=12,
        handoff_status="resolved",
        dispatch_intent="resolve_handoff",
        acknowledged_at="2026-06-16T10:00:00Z",
        resolved_at="2026-06-16T10:10:00Z",
    )

    assert snapshot.dispatch_intent == "resolve_handoff"
    assert snapshot.acknowledged_at == "2026-06-16T10:00:00Z"
    assert snapshot.resolved_at == "2026-06-16T10:10:00Z"


def test_resolved_at_must_not_be_before_acknowledged_at():
    try:
        ThreadLifecycleSnapshot.create(
            thread_ref=_thread_ref(),
            thread_status="completed",
            last_seen_turn=12,
            handoff_status="resolved",
            dispatch_intent="resolve_handoff",
            acknowledged_at="2026-06-16T10:10:00Z",
            resolved_at="2026-06-16T10:00:00Z",
        )
    except Exception as exc:
        assert "resolved_at" in str(exc)
    else:
        raise AssertionError("expected invalid timestamp order")


def test_ambiguous_thread_cannot_carry_send_instruction_intent():
    try:
        ThreadLifecycleSnapshot.create(
            thread_ref=_thread_ref(),
            thread_status="ambiguous",
            last_seen_turn=12,
            handoff_status="ambiguous",
            dispatch_intent="send_instruction",
        )
    except Exception as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("expected ambiguous send intent to be rejected")


def test_audit_summary_keeps_ids_status_action_without_long_prompt_dump():
    snapshot = ThreadLifecycleSnapshot.create(
        thread_ref=_thread_ref(),
        thread_status=ThreadStatus.IDLE,
        last_seen_turn=12,
        handoff_status="none",
    )
    long_prompt = "Please inspect and dispatch this thread carefully " * 20
    request = _request(prompt_summary=long_prompt)

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=request)
    summary = decision.audit_summary()

    assert summary["action"] == "send"
    assert summary["allowed"] is True
    assert summary["reason"] == "idle_thread_ready_for_dispatch"
    assert long_prompt not in repr(summary)
