import json

import pytest

from src import agent_task_ledger


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_task_ledger_records_redacted_task_state(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path))

    record = agent_task_ledger.record_task_event(
        task_id="tg_task_123",
        task_type="website_research_to_memory",
        status="waiting_for_gate",
        owner="telegram-chat-12345",
        correlation_id="tg_task_123",
        target_ref="https://www.asv-bw.de/",
        progress_percent=12,
        gates_waiting=("live_web_target_approval", "memory_write_policy"),
        summary="Waiting for approved target and memory policy.",
    )

    encoded = json.dumps(record, sort_keys=True)
    assert record["owner_hash"].startswith("sha256:")
    assert record["target_ref"] == "https://www.asv-bw.de/"
    assert record["progress_percent"] == 12
    assert record["raw_content_visible"] is False
    assert "telegram-chat-12345" not in encoded
    assert _read_lines(agent_task_ledger.ledger_path())[0]["task_id"] == "tg_task_123"


def test_task_ledger_rejects_secret_or_raw_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path))

    with pytest.raises(agent_task_ledger.AgentTaskLedgerError):
        agent_task_ledger.build_task_record(
            task_id="tg_task_123",
            task_type="website_research_to_memory",
            status="running",
            summary="private raw text should never land here",
        )


def test_task_ledger_read_filters_and_summarizes(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path))

    agent_task_ledger.record_task_event(
        task_id="tg_task_1",
        task_type="website_research_to_memory",
        status="running",
        progress_percent=20,
    )
    agent_task_ledger.record_task_event(
        task_id="tg_task_1",
        task_type="website_research_to_memory",
        status="done",
        progress_percent=100,
    )
    agent_task_ledger.record_task_event(
        task_id="tg_task_2",
        task_type="sandbox_code_execution",
        status="waiting_for_gate",
        progress_percent=0,
    )

    result = agent_task_ledger.read_task_records(task_id="tg_task_1")
    assert result["count"] == 2
    assert result["records"][0]["status"] == "done"
    assert result["summary"]["latest_progress"]["tg_task_1"] == 100
    assert result["summary"]["by_type"]["website_research_to_memory"] == 2
