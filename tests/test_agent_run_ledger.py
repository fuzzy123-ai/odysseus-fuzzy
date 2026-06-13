import json

from src import agent_run_ledger


def test_summarize_sse_delta_without_persisting_text():
    summary = agent_run_ledger.summarize_sse_event('data: {"delta": "secret words"}\n\n')

    assert summary == {
        "event_name": "message",
        "type": "delta",
        "delta_chars": len("secret words"),
    }
    assert "secret words" not in json.dumps(summary)


def test_summarize_tool_output_keeps_metadata_not_output():
    summary = agent_run_ledger.summarize_sse_event(
        'data: {"type": "tool_output", "tool": "bash", "round": 2, '
        '"exit_code": 0, "output": "very sensitive output"}\n\n'
    )

    assert summary["type"] == "tool_output"
    assert summary["tool"] == "bash"
    assert summary["round"] == 2
    assert summary["exit_code"] == 0
    assert summary["output_chars"] == len("very sensitive output")
    assert "very sensitive output" not in json.dumps(summary)


def test_append_and_read_events_uses_safe_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))

    agent_run_ledger.append_run_started("../bad/session")
    agent_run_ledger.append_sse_event("../bad/session", 'data: {"type": "agent_step", "round": 3}\n\n')
    agent_run_ledger.append_status("../bad/session", "done")

    events = agent_run_ledger.read_events("../bad/session")

    assert [event["event"] for event in events] == ["run_started", "sse_event", "run_status"]
    assert events[1]["payload"] == {"event_name": "message", "type": "agent_step", "round": 3}
    assert events[2]["payload"] == {"status": "done"}
    assert agent_run_ledger.ledger_path("../bad/session").parent == tmp_path
