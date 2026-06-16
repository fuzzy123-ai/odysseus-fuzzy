import pytest

from src.handoff_mailbox import (
    DispatchMailbox,
    HandoffMailboxError,
    HandoffStatus,
    MailboxMessage,
    MailboxMessageStatus,
    ParsedHandoff,
    parse_handoff_text,
)
from src.thread_lifecycle_bridge import ThreadRef
from src.thread_registry import ThreadRegistry


def _thread_ref(
    *,
    thread_id: str = "019-thread",
    agent_id: str = "bob",
    agent_run_id: str = "run-b",
    node_id: str = "auto3",
) -> ThreadRef:
    return ThreadRef.create(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_run_id=agent_run_id,
        plan_id="auto3-plan",
        node_id=node_id,
    )


def _handoff() -> ParsedHandoff:
    return ParsedHandoff.create(
        agent="Bob",
        slice_id="AUTO3-handoff-parser-and-mailbox",
        status="done",
        commit="abcdef1",
        changed_files=["src/handoff_mailbox.py", "tests/test_handoff_mailbox.py"],
        tests=["pytest tests/test_handoff_mailbox.py"],
        evidence=["green focused test"],
    )


def test_parse_german_handoff_text():
    parsed = parse_handoff_text(
        """
        Agent: Bob
        Slice: AUTO3-handoff-parser-and-mailbox
        Status: fertig
        Commit: ABCDEF1
        Geänderte Dateien:
        - src/handoff_mailbox.py
        - tests/test_handoff_mailbox.py
        Tests: pytest tests/test_handoff_mailbox.py
        Evidence: green focused test
        Blocker: -
        Nächster Slice: AUTO4-heartbeat-runtime-loop
        """
    )

    assert parsed.status == HandoffStatus.DONE
    assert parsed.commit == "abcdef1"
    assert parsed.changed_files == ("src/handoff_mailbox.py", "tests/test_handoff_mailbox.py")
    assert parsed.tests == ("pytest tests/test_handoff_mailbox.py",)
    assert parsed.requires_charlie_action is True


def test_missing_required_field_is_rejected():
    with pytest.raises(HandoffMailboxError, match="missing required handoff field: slice_id"):
        parse_handoff_text("Agent: Alice\nStatus: done\nEvidence: docs-only")


def test_unsafe_changed_file_path_is_rejected():
    with pytest.raises(HandoffMailboxError, match="safe repo-relative"):
        ParsedHandoff.create(
            agent="bob",
            slice_id="auto3",
            status="done",
            changed_files=["../secrets.env"],
            evidence=["claimed"],
        )


def test_done_handoff_requires_evidence_commit_or_tests():
    with pytest.raises(HandoffMailboxError, match="done handoff requires"):
        ParsedHandoff.create(agent="bob", slice_id="auto3", status="done")


def test_blocked_handoff_requires_blocker():
    with pytest.raises(HandoffMailboxError, match="blocked handoff requires blocker"):
        ParsedHandoff.create(agent="bob", slice_id="auto3", status="blocked")


def test_handoff_status_requires_next_slice():
    with pytest.raises(HandoffMailboxError, match="requires next_slice"):
        ParsedHandoff.create(agent="alice", slice_id="auto3", status="handoff", evidence=["ready"])


def test_mailbox_queues_dispatch_for_registered_run():
    registry = ThreadRegistry()
    registry.register(_thread_ref(agent_id="bob", agent_run_id="run-b", node_id="auto3"))
    mailbox = DispatchMailbox()

    message = mailbox.queue_for_run(
        registry=registry,
        agent_run_id="run-b",
        expected_agent_id="bob",
        expected_node_id="auto3",
        prompt_summary="Continue with AUTO4 after verified AUTO3 handoff",
        allowed_action="send",
        source_handoff=_handoff(),
    )

    assert message.status == MailboxMessageStatus.QUEUED
    assert mailbox.audit_summary()["queued_count"] == 1
    assert message.thread_ref.thread_id == "019-thread"


def test_mailbox_rejects_duplicate_message():
    mailbox = DispatchMailbox()
    message = MailboxMessage.create(
        thread_ref=_thread_ref(),
        prompt_summary="Send next slice",
        allowed_action="send",
        source_handoff=_handoff(),
    )

    mailbox.queue(message)
    with pytest.raises(HandoffMailboxError, match="already queued"):
        mailbox.queue(message)


def test_mailbox_rejects_registry_mismatch():
    registry = ThreadRegistry()
    registry.register(_thread_ref(agent_id="alice", agent_run_id="run-a", node_id="auto3"))

    with pytest.raises(HandoffMailboxError, match="agent_id mismatch"):
        DispatchMailbox().queue_for_run(
            registry=registry,
            agent_run_id="run-a",
            expected_agent_id="bob",
            expected_node_id="auto3",
            prompt_summary="Send next slice",
            allowed_action="send",
            source_handoff=_handoff(),
        )


def test_mailbox_roundtrips_through_dict_and_validates_id():
    mailbox = DispatchMailbox()
    message = MailboxMessage.create(
        thread_ref=_thread_ref(),
        prompt_summary="Send next slice",
        allowed_action="send",
        source_handoff=_handoff(),
    )
    mailbox.queue(message)

    loaded = DispatchMailbox.from_dict(mailbox.to_dict())

    assert loaded.audit_summary()["message_count"] == 1
    assert loaded.messages[message.message_id].source_handoff.commit == "abcdef1"

    payload = mailbox.to_dict()
    payload["messages"][0]["message_id"] = "tampered"
    with pytest.raises(HandoffMailboxError, match="message_id does not match"):
        DispatchMailbox.from_dict(payload)
