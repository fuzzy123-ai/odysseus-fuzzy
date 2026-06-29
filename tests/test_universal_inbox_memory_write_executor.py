import pytest

from src import memory_provenance_ledger
from src.universal_inbox_memory_write_executor import (
    UniversalInboxMemoryWriteExecutionError,
    execute_universal_inbox_memory_write_intent,
)


READY_INTENT = {
    "status": "ready",
    "ready_to_write": True,
    "memory_records": (
        {
            "memory_id": "uix-abc",
            "source": "universal_inbox",
            "category": "document",
            "text": "Universal Inbox memory: safe abstraction",
            "metadata": {"classification": "private", "raw_content_stored": False},
        },
    ),
    "raptorgraph_event": {
        "event": "universal_inbox_memory_write_intent",
        "memory_record_ids": ("uix-abc",),
        "raw_content_stored": False,
    },
}


def test_executor_blocks_non_ready_intent():
    report = execute_universal_inbox_memory_write_intent(
        {"status": "review", "ready_to_write": False, "memory_records": ()}
    ).to_dict()

    assert report["status"] == "blocked"
    assert report["reason"] == "intent_not_ready"
    assert report["writes_performed"] is False


def test_executor_requires_review_confirmation_before_write():
    report = execute_universal_inbox_memory_write_intent(READY_INTENT).to_dict()

    assert report["status"] == "review"
    assert report["reason"] == "review_confirmation_required"
    assert report["memory_records_planned"] == 1
    assert report["writes_performed"] is False


def test_executor_dry_run_plans_after_review_without_writing():
    calls = []
    report = execute_universal_inbox_memory_write_intent(
        READY_INTENT,
        review_confirmed=True,
        dry_run=True,
        memory_writer=lambda record: calls.append(record),
    ).to_dict()

    assert report["status"] == "planned"
    assert report["reason"] == "dry_run_only"
    assert report["memory_records_planned"] == 1
    assert report["raptorgraph_events_planned"] == 1
    assert calls == []


def test_executor_live_requires_memory_writer():
    with pytest.raises(UniversalInboxMemoryWriteExecutionError):
        execute_universal_inbox_memory_write_intent(
            READY_INTENT,
            review_confirmed=True,
            dry_run=False,
        )


def test_executor_live_uses_injected_writers_only_when_explicit():
    memory_calls = []
    graph_calls = []

    report = execute_universal_inbox_memory_write_intent(
        READY_INTENT,
        review_confirmed=True,
        dry_run=False,
        memory_writer=lambda record: memory_calls.append(record),
        raptorgraph_writer=lambda event: graph_calls.append(event),
    ).to_dict()

    assert report["status"] == "written"
    assert report["writes_performed"] is True
    assert report["memory_records_written"] == 1
    assert report["raptorgraph_events_written"] == 1
    assert memory_calls[0]["memory_id"] == "uix-abc"
    assert graph_calls[0]["memory_record_ids"] == ("uix-abc",)


def test_executor_writes_redacted_memory_provenance_events(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_provenance_ledger, "MEMORY_PROVENANCE_LEDGER_DIR", str(tmp_path))

    execute_universal_inbox_memory_write_intent(
        READY_INTENT,
        review_confirmed=True,
        dry_run=True,
    )

    rows = [
        __import__("json").loads(line)
        for line in memory_provenance_ledger.ledger_path().read_text(encoding="utf-8").splitlines()
    ]
    event_types = [row["event_type"] for row in rows]
    assert "memory_write_intent" in event_types
    assert "memory_user_interaction" in event_types
    assert all(row["raw_content_visible"] is False for row in rows)
