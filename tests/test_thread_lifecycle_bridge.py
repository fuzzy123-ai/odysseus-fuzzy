from src.thread_lifecycle_bridge import (
    DispatchAction,
    ThreadDispatchDecision,
    ThreadDispatchRequest,
    ThreadLifecycleSnapshot,
    ThreadRef,
    ThreadStatus,
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
        handoff_status="blocked",
    )

    decision = ThreadDispatchDecision.decide(snapshot=snapshot, request=_request())

    assert decision.action == DispatchAction.BLOCKED
    assert decision.allowed is False
    assert decision.reason == "ambiguous_thread"


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
        handoff_status="ready",
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
