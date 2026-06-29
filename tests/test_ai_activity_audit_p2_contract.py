from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_compare_streams_are_labeled_for_ai_activity_audit():
    src = (ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")

    assert '_chat_audit_surface = "compare" if _is_compare_session else "chat"' in src
    assert '_agent_audit_surface = "compare" if _is_compare_session else "agent"' in src
    assert "surface=_chat_audit_surface" in src
    assert "audit_surface=_agent_audit_surface" in src


def test_scheduled_task_agent_loop_writes_audited_ledgers():
    src = (ROOT / "src" / "task_scheduler.py").read_text(encoding="utf-8")

    assert "agent_run_ledger.append_run_started(session_id)" in src
    assert 'agent_run_ledger.append_event(\n                session_id,\n                "task_run_context"' in src
    assert "agent_run_ledger.append_sse_event(session_id, event_str)" in src
    assert 'audit_surface="scheduled_task"' in src
    assert 'surface="scheduled_task"' in src
