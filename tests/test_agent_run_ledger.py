import json
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import agent_run_ledger
from src.mission_status import summarize_mission


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


def test_summarize_tool_start_adds_shell_policy_metadata():
    summary = agent_run_ledger.summarize_sse_event(
        'data: {"type": "tool_start", "tool": "bash", "round": 1, "command": "rm -rf build"}\n\n'
    )

    assert summary["type"] == "tool_start"
    assert summary["command_preview"] == "rm -rf build"
    assert summary["command_policy"] == {
        "tier": "danger",
        "reason": "dangerous_pattern",
        "requires_confirmation": True,
        "blocked": False,
        "audit": True,
    }


def test_summarize_tool_output_records_screenshot_presence_not_data():
    summary = agent_run_ledger.summarize_sse_event(
        'data: {"type": "tool_output", "tool": "builtin_browser", "round": 1, '
        '"exit_code": 0, "screenshot": "data:image/png;base64,secretpixels", "output": "page ok"}\n\n'
    )

    assert summary["type"] == "tool_output"
    assert summary["tool"] == "builtin_browser"
    assert summary["has_screenshot"] is True
    assert "secretpixels" not in json.dumps(summary)


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


def test_summarize_run_reports_status_tools_metrics_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "summary-session"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_start", "tool": "bash", "command": "echo hello", "round": 1}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "bash", "round": 1, "exit_code": 0, "output": "hello"}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "metrics", "data": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "usage_source": "real"}}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    summary = agent_run_ledger.summarize_run(session_id, tail=2)

    assert summary["exists"] is True
    assert summary["status"] == "done"
    assert summary["event_count"] == 5
    assert summary["event_counts"] == {"run_started": 1, "sse_event": 3, "run_status": 1}
    assert summary["tools"]["bash"] == {"starts": 1, "outputs": 1, "blocked": 0, "last_exit_code": 0}
    assert summary["last_metrics"] == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "usage_source": "real",
    }
    assert [event["event"] for event in summary["tail"]] == ["sse_event", "run_status"]


def test_summarize_mission_infers_worker_and_verifier_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-session"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_start", "tool": "delegate", "round": 1}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "delegate", "round": 1, "exit_code": 0, "output": "worker done"}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_start", "tool": "bash", "round": 2, "command": "python -m pytest tests/test_agent_run_ledger.py"}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "bash", "round": 2, "exit_code": 0, "output": "passed"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    snapshot = summarize_mission(session_id, tail=1)

    assert snapshot["status"] == "done"
    assert snapshot["phases"]["manager"]["status"] == "done"
    assert snapshot["phases"]["worker"]["status"] == "done"
    assert snapshot["phases"]["worker"]["starts"] == 1
    assert snapshot["phases"]["verifier"]["status"] == "done"
    assert snapshot["phases"]["verifier"]["starts"] == 1
    assert snapshot["phases"]["verifier"]["policy_tiers"] == {"safe": 1}
    assert snapshot["phases"]["verifier"]["last_command_policy"] == {
        "tier": "safe",
        "reason": "known_safe_command",
        "requires_confirmation": False,
        "blocked": False,
        "audit": False,
    }
    assert snapshot["summary"]["status"] == "done"
    assert snapshot["summary"]["worker_status"] == "done"
    assert snapshot["summary"]["verifier_status"] == "done"
    assert snapshot["summary"]["verification_required"] is True
    assert snapshot["summary"]["verification_satisfied"] is True
    assert snapshot["summary"]["verification_evidence"] == {"test_command": 1}
    assert snapshot["summary"]["verification_gaps"] == []
    assert snapshot["summary"]["policy_tiers"] == {"safe": 1}
    assert [node["id"] for node in snapshot["dag"]["nodes"]] == ["manager", "worker", "verifier"]
    assert snapshot["dag"]["edges"] == [
        {"source": "manager", "target": "worker", "kind": "delegates"},
        {"source": "worker", "target": "verifier", "kind": "handoff"},
        {"source": "manager", "target": "verifier", "kind": "direct_verification"},
    ]
    assert snapshot["dag"]["nodes"][1]["starts"] == 1
    assert snapshot["dag"]["nodes"][2]["status"] == "done"
    assert "run_focused_verification" not in snapshot["next_actions"]
    assert "worker done" not in json.dumps(snapshot)
    assert "passed" not in json.dumps(snapshot)


def test_summarize_mission_treats_browser_screenshot_as_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-browser-verification"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_start", "tool": "builtin_browser", "round": 1}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "builtin_browser", "round": 1, '
        '"exit_code": 0, "screenshot": "data:image/png;base64,secretpixels", "output": "visual ok"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "done"
    assert snapshot["phases"]["verifier"]["starts"] == 1
    assert snapshot["phases"]["verifier"]["artifacts"] == {"browser_check": 1, "screenshot": 1}
    assert snapshot["summary"]["verifier_artifacts"] == {"browser_check": 1, "screenshot": 1}
    assert snapshot["summary"]["verification_satisfied"] is True
    assert snapshot["summary"]["verification_evidence"] == {"browser_check": 1, "screenshot": 1}
    assert "run_focused_verification" not in snapshot["next_actions"]
    assert "secretpixels" not in json.dumps(snapshot)


def test_summarize_mission_marks_missing_verification_as_next_action(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-needs-verification"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_status(session_id, "done")

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "idle"
    assert snapshot["summary"]["verification_required"] is True
    assert snapshot["summary"]["verification_satisfied"] is False
    assert snapshot["summary"]["verification_gaps"] == ["focused_verification_missing"]
    assert snapshot["next_actions"] == ["run_focused_verification"]


def test_summarize_mission_reports_latest_verifier_blocker(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-verifier-failed"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_start", "tool": "bash", "round": 1, "command": "python -m pytest tests/test_agent_run_ledger.py"}\n\n',
    )
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "bash", "round": 1, "exit_code": 1, "output": "failed details"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "blocked"
    assert snapshot["summary"]["latest_blocker"] == {
        "role": "verifier",
        "kind": "tool_output",
        "tool": "bash",
        "exit_code": 1,
        "reason": "nonzero_exit_code",
    }
    verifier_node = next(node for node in snapshot["dag"]["nodes"] if node["id"] == "verifier")
    assert verifier_node["status"] == "blocked"
    assert verifier_node["has_blocker"] is True
    assert "inspect_verification_failure" in snapshot["next_actions"]
    assert "failed details" not in json.dumps(snapshot)


def test_summarize_mission_treats_readiness_signal_as_verification_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-readiness-gap"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_raptor_status", "round": 1, "exit_code": 0, '
        '"output": "{\\"readiness\\":{\\"ready\\":\\"false\\",\\"state\\":\\"tainted\\",'
        '\\"gaps\\":[\\"source_isolated_from_default_retrieval\\"]}}"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    events = agent_run_ledger.read_events(session_id)
    signal = events[1]["payload"]["readiness_signal"]
    assert signal == {
        "family": "raptor",
        "source": "readiness",
        "state": "tainted",
        "ready": False,
        "gaps": ["source_isolated_from_default_retrieval"],
        "gap_count": 1,
    }
    assert events[1]["payload"]["readiness_signals"] == [signal]

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "blocked"
    assert snapshot["phases"]["verifier"]["artifacts"] == {"readiness_check": 1, "raptor_readiness": 1}
    assert snapshot["phases"]["verifier"]["readiness_gate"] == snapshot["summary"]["readiness_gate"]
    assert snapshot["summary"]["verification_evidence"] == {"readiness_check": 1, "raptor_readiness": 1}
    assert snapshot["summary"]["verification_satisfied"] is False
    assert snapshot["summary"]["verification_gaps"] == ["verification_blocked"]
    assert snapshot["summary"]["readiness_gate"] == {
        "required": True,
        "satisfied": False,
        "state": "blocked",
        "families": 1,
        "ready_families": 0,
        "blocked_families": ["raptor"],
        "gaps": ["source_isolated_from_default_retrieval"],
    }
    assert snapshot["summary"]["readiness_signals"] == [signal]
    assert snapshot["summary"]["latest_blocker"] == {
        "role": "verifier",
        "kind": "readiness_signal",
        "tool": "obsidian_raptor_status",
        "reason": "readiness_gaps",
        "family": "raptor",
        "source": "readiness",
        "state": "tainted",
        "gaps": ["source_isolated_from_default_retrieval"],
        "gap_count": 1,
    }
    assert "resolve_readiness_gaps" in snapshot["next_actions"]
    assert "inspect_verification_failure" in snapshot["next_actions"]
    verifier_node = next(node for node in snapshot["dag"]["nodes"] if node["id"] == "verifier")
    assert verifier_node["readiness_state"] == "blocked"
    assert verifier_node["readiness_blocked"] is True
    assert verifier_node["readiness_gaps"] == 1


def test_summarize_mission_marks_readiness_gate_satisfied(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-readiness-gate-ready"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_memory_status", "round": 1, "exit_code": 0, '
        '"output": "{\\"readiness_by_family\\":{'
        '\\"freshness\\":{\\"source\\":\\"readiness\\",\\"state\\":\\"ready\\",'
        '\\"ready\\":true,\\"gaps\\":[],\\"gap_count\\":0},'
        '\\"raptor\\":{\\"source\\":\\"readiness\\",\\"state\\":\\"ready\\",'
        '\\"ready\\":true,\\"gaps\\":[],\\"gap_count\\":0}}}"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    snapshot = summarize_mission(session_id)

    assert snapshot["summary"]["readiness_gate"] == {
        "required": True,
        "satisfied": True,
        "state": "ready",
        "families": 2,
        "ready_families": 2,
        "blocked_families": [],
        "gaps": [],
    }
    assert "resolve_readiness_gaps" not in snapshot["next_actions"]
    verifier_node = next(node for node in snapshot["dag"]["nodes"] if node["id"] == "verifier")
    assert verifier_node["readiness_state"] == "ready"
    assert verifier_node["readiness_blocked"] is False
    assert verifier_node["readiness_gaps"] == 0


def test_agent_run_ledger_extracts_readiness_signal_from_memory_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-memory-summary"

    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_context", "round": 1, "exit_code": 0, '
        '"output": "{\\"memory\\":{\\"summary\\":{\\"freshness_readiness_state\\":\\"needs_review\\",'
        '\\"freshness_readiness_gaps\\":2,\\"raptor_readiness_state\\":\\"dirty\\",'
        '\\"raptor_readiness_gaps\\":1}}}"}\n\n',
    )

    events = agent_run_ledger.read_events(session_id)
    assert events[0]["payload"]["readiness_signal"] == {
        "family": "freshness",
        "source": "summary",
        "state": "needs_review",
        "ready": False,
        "gaps": [],
        "gap_count": 2,
    }
    assert events[0]["payload"]["readiness_signals"] == [
        {
            "family": "freshness",
            "source": "summary",
            "state": "needs_review",
            "ready": False,
            "gaps": [],
            "gap_count": 2,
        },
        {
            "family": "raptor",
            "source": "summary",
            "state": "dirty",
            "ready": False,
            "gaps": [],
            "gap_count": 1,
        },
    ]

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "blocked"
    assert snapshot["phases"]["verifier"]["artifacts"] == {
        "freshness_readiness": 1,
        "raptor_readiness": 1,
        "readiness_check": 2,
    }
    assert snapshot["summary"]["readiness_signals"] == events[0]["payload"]["readiness_signals"]
    assert snapshot["summary"]["readiness_by_family"] == {
        "freshness": {
            "family": "freshness",
            "source": "summary",
            "state": "needs_review",
            "ready": False,
            "gaps": [],
            "gap_count": 2,
        },
        "raptor": {
            "family": "raptor",
            "source": "summary",
            "state": "dirty",
            "ready": False,
            "gaps": [],
            "gap_count": 1,
        },
    }
    assert snapshot["summary"]["readiness_gate"] == {
        "required": True,
        "satisfied": False,
        "state": "blocked",
        "families": 2,
        "ready_families": 0,
        "blocked_families": ["freshness", "raptor"],
        "gaps": ["freshness", "raptor"],
    }
    assert snapshot["summary"]["latest_blocker"] == {
        "role": "verifier",
        "kind": "readiness_signal",
        "tool": "obsidian_context",
        "reason": "readiness_gaps",
        "family": "raptor",
        "source": "summary",
        "state": "dirty",
        "gaps": [],
        "gap_count": 1,
    }


def test_agent_run_ledger_prefers_explicit_memory_readiness_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-memory-explicit-readiness"

    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_context", "round": 1, "exit_code": 0, '
        '"output": "{\\"memory\\":{\\"readiness_signals\\":[{\\"family\\":\\"freshness\\",'
        '\\"source\\":\\"readiness\\",\\"state\\":\\"needs_review\\",\\"ready\\":\\"false\\",'
        '\\"gaps\\":[\\"needs_review_items\\"]},{\\"family\\":\\"raptor\\",'
        '\\"source\\":\\"readiness\\",\\"state\\":\\"not_configured\\",\\"ready\\":\\"false\\",'
        '\\"gaps\\":[\\"raptor_index_missing\\"],\\"gap_count\\":1}],\\"summary\\":{'
        '\\"freshness_readiness_state\\":\\"ready\\",\\"freshness_readiness_gaps\\":0}}}"}\n\n',
    )

    events = agent_run_ledger.read_events(session_id)

    assert events[0]["payload"]["readiness_signals"] == [
        {
            "family": "freshness",
            "source": "readiness",
            "state": "needs_review",
            "ready": False,
            "gaps": ["needs_review_items"],
            "gap_count": 1,
        },
        {
            "family": "raptor",
            "source": "readiness",
            "state": "not_configured",
            "ready": False,
            "gaps": ["raptor_index_missing"],
            "gap_count": 1,
        },
    ]


def test_agent_run_ledger_extracts_readiness_by_family(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-memory-readiness-by-family"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_context", "round": 1, "exit_code": 0, '
        '"output": "{\\"memory\\":{\\"readiness_by_family\\":{'
        '\\"freshness\\":{\\"source\\":\\"readiness\\",\\"state\\":\\"needs_review\\",'
        '\\"ready\\":false,\\"gaps\\":[\\"needs_review_items\\"],\\"gap_count\\":1},'
        '\\"raptor\\":{\\"source\\":\\"readiness\\",\\"state\\":\\"not_configured\\",'
        '\\"ready\\":false,\\"readiness_gap_names\\":[\\"raptor_index_missing\\"],\\"gap_count\\":1}}}}"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    events = agent_run_ledger.read_events(session_id)

    assert events[1]["payload"]["readiness_signals"] == [
        {
            "family": "freshness",
            "source": "readiness",
            "state": "needs_review",
            "ready": False,
            "gaps": ["needs_review_items"],
            "gap_count": 1,
        },
        {
            "family": "raptor",
            "source": "readiness",
            "state": "not_configured",
            "ready": False,
            "gaps": ["raptor_index_missing"],
            "gap_count": 1,
        },
    ]

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["artifacts"] == {
        "freshness_readiness": 1,
        "raptor_readiness": 1,
        "readiness_check": 2,
    }
    assert set(snapshot["summary"]["readiness_by_family"]) == {"freshness", "raptor"}
    assert snapshot["summary"]["latest_blocker"]["family"] == "raptor"


def test_agent_run_ledger_treats_context_memory_summary_as_memory_family(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-context-memory-summary"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_context", "round": 1, "exit_code": 0, '
        '"output": "{\\"memory\\":{\\"summary\\":{\\"readiness_state\\":\\"blocked\\",'
        '\\"readiness_gaps\\":2,\\"readiness_gap_names\\":[\\"freshness_filtering_not_active\\",'
        '\\"raptor_index_missing\\"]}}}"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    events = agent_run_ledger.read_events(session_id)
    signal = events[1]["payload"]["readiness_signal"]

    assert signal == {
        "family": "memory",
        "source": "summary",
        "state": "blocked",
        "ready": False,
        "gaps": ["freshness_filtering_not_active", "raptor_index_missing"],
        "gap_count": 2,
    }

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["artifacts"] == {"memory_readiness": 1, "readiness_check": 1}
    assert snapshot["summary"]["readiness_by_family"]["memory"] == signal
    assert snapshot["summary"]["latest_blocker"]["family"] == "memory"


def test_agent_run_ledger_uses_memory_status_hint_for_summary_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-memory-status-summary-readiness"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_memory_status", "round": 1, "exit_code": 0, '
        '"output": "{\\"summary\\":{\\"readiness_state\\":\\"blocked\\",\\"readiness_gaps\\":2,'
        '\\"readiness_gap_names\\":[\\"freshness\\",\\"raptor\\"]}}"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    events = agent_run_ledger.read_events(session_id)
    signal = events[1]["payload"]["readiness_signal"]

    assert signal == {
        "family": "memory",
        "source": "summary",
        "state": "blocked",
        "ready": False,
        "gaps": ["freshness", "raptor"],
        "gap_count": 2,
    }

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "blocked"
    assert snapshot["phases"]["verifier"]["artifacts"] == {"memory_readiness": 1, "readiness_check": 1}
    assert snapshot["summary"]["readiness_by_family"]["memory"] == signal
    assert snapshot["summary"]["latest_blocker"]["family"] == "memory"


def test_summarize_mission_treats_somt_readiness_as_verification_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-somt-readiness-gap"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_output", "tool": "obsidian_memory_tree_status", "round": 1, "exit_code": 0, '
        '"output": "{\\"readiness\\":{\\"ready\\":false,\\"state\\":\\"needs_review\\",'
        '\\"gaps\\":[\\"somt_issues_present\\"]}}"}\n\n',
    )
    agent_run_ledger.append_status(session_id, "done")

    events = agent_run_ledger.read_events(session_id)
    signal = events[1]["payload"]["readiness_signal"]
    assert signal == {
        "family": "somt",
        "source": "readiness",
        "state": "needs_review",
        "ready": False,
        "gaps": ["somt_issues_present"],
        "gap_count": 1,
    }

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "blocked"
    assert snapshot["phases"]["verifier"]["artifacts"] == {"readiness_check": 1, "somt_readiness": 1}
    assert snapshot["summary"]["readiness_by_family"]["somt"] == signal
    assert snapshot["summary"]["latest_blocker"] == {
        "role": "verifier",
        "kind": "readiness_signal",
        "tool": "obsidian_memory_tree_status",
        "reason": "readiness_gaps",
        "family": "somt",
        "source": "readiness",
        "state": "needs_review",
        "gaps": ["somt_issues_present"],
        "gap_count": 1,
    }


def test_summarize_mission_reports_latest_required_action(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    session_id = "mission-confirm-required"

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(
        session_id,
        'data: {"type": "tool_start", "tool": "bash", "round": 1, "command": "python -m pytest tests/test_agent_run_ledger.py && rm -rf build"}\n\n',
    )

    snapshot = summarize_mission(session_id, active=True)

    assert snapshot["phases"]["verifier"]["latest_required_action"] == {
        "action": "confirm_shell_command",
        "tool": "bash",
        "policy_tier": "danger",
        "reason": "dangerous_pattern",
        "command_preview": "python -m pytest tests/test_agent_run_ledger.py && rm -rf build",
    }
    assert snapshot["summary"]["latest_required_action"]["role"] == "verifier"
    assert snapshot["summary"]["latest_required_action"]["action"] == "confirm_shell_command"
    assert snapshot["summary"]["verification_required"] is False
    assert snapshot["summary"]["verification_satisfied"] is False
    assert snapshot["summary"]["verification_evidence"] == {"test_command": 1}
    verifier_node = next(node for node in snapshot["dag"]["nodes"] if node["id"] == "verifier")
    assert verifier_node["has_required_action"] is True
    assert "confirm_shell_command" in snapshot["next_actions"]


def test_chat_run_ledger_route_returns_owner_scoped_summary(tmp_path, monkeypatch):
    import routes.chat_routes as chat_routes

    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(chat_routes.agent_runs, "is_active", lambda session_id: False)
    monkeypatch.setattr(chat_routes.agent_runs, "get_status", lambda session_id: None)

    agent_run_ledger.append_run_started("route-session")
    agent_run_ledger.append_status("route-session", "done")

    app = FastAPI()
    app.include_router(chat_routes.setup_chat_routes(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ))

    response = TestClient(app).get("/api/chat/run_ledger/route-session?tail=1")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "route-session"
    assert body["status"] == "done"
    assert body["active"] is False
    assert [event["event"] for event in body["tail"]] == ["run_status"]


def test_chat_mission_route_returns_owner_scoped_snapshot(tmp_path, monkeypatch):
    import routes.chat_routes as chat_routes

    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(chat_routes.agent_runs, "is_active", lambda session_id: False)
    monkeypatch.setattr(chat_routes.agent_runs, "get_status", lambda session_id: None)

    agent_run_ledger.append_run_started("mission-route-session")
    agent_run_ledger.append_status("mission-route-session", "done")

    app = FastAPI()
    app.include_router(chat_routes.setup_chat_routes(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ))

    response = TestClient(app).get("/api/chat/mission/mission-route-session?tail=1")

    assert response.status_code == 200
    body = response.json()
    assert body["mission_id"] == "mission-route-session"
    assert body["phases"]["manager"]["status"] == "done"
    assert body["dag"]["edges"][0] == {"source": "manager", "target": "worker", "kind": "delegates"}
    assert body["summary"]["verifier_status"] == "idle"
    assert body["summary"]["verification_required"] is True
    assert body["summary"]["verification_satisfied"] is False
    assert body["summary"]["verification_gaps"] == ["focused_verification_missing"]
    assert body["next_actions"] == ["run_focused_verification"]


def test_chat_run_ledger_route_404s_without_ledger_or_active_run(tmp_path, monkeypatch):
    import routes.chat_routes as chat_routes

    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(chat_routes.agent_runs, "is_active", lambda session_id: False)
    monkeypatch.setattr(chat_routes.agent_runs, "get_status", lambda session_id: None)

    app = FastAPI()
    app.include_router(chat_routes.setup_chat_routes(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ))

    response = TestClient(app).get("/api/chat/run_ledger/missing-session")

    assert response.status_code == 404
