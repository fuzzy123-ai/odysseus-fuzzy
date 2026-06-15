import sys
import tempfile
import types
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Session as DbSession
from core.database import ChatMessage as DbMsg

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
_ROOT = Path(__file__).resolve().parents[1]


def _stub_multipart_if_missing(monkeypatch):
    try:
        import python_multipart
        return
    except ImportError:
        pass
    stub = types.ModuleType("python_multipart")
    stub.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", stub)


def test_session_menu_exposes_read_only_mission_status_action():
    source = (_ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")

    assert "Mission status" in source
    assert "/api/chat/mission/${encodeURIComponent(sessionId)}?tail=8" in source
    assert "No mission status recorded for this session" in source
    assert "credentials: 'same-origin'" in source
    assert "function _missionShellPolicyText(snapshot)" in source
    assert "payload.command_policy" in source
    assert "confirmation required" in source
    assert "audit logged" in source
    assert "function _missionDagText(snapshot)" in source
    assert "snapshot?.dag?.nodes" in source
    assert "DAG:" in source
    assert "has_required_action" in source
    assert "has_blocker" in source
    assert "function _missionRequiredActionText(snapshot)" in source
    assert "snapshot?.summary?.latest_required_action" in source
    assert "Required:" in source
    assert "function _missionBlockerText(snapshot)" in source
    assert "snapshot?.summary?.latest_blocker" in source
    assert "Blocker:" in source
    assert "blocker.family" in source
    assert "blocker.source" in source
    assert "blocker.state" in source
    assert "blocker.gap_count" in source
    assert "function _missionArtifactText(snapshot)" in source
    assert "snapshot?.summary?.verifier_artifacts" in source
    assert "snapshot?.phases?.verifier?.artifacts" in source
    assert "Artifacts:" in source
    assert "function _missionVerificationText(snapshot)" in source
    assert "summary.verification_required" in source
    assert "summary.verification_satisfied" in source
    assert "summary.verification_evidence" in source
    assert "summary.verification_gaps" in source
    assert "Verification:" in source
    assert "function _missionReadinessText(snapshot)" in source
    assert "summary?.readiness_signals" in source
    assert "summary?.readiness_by_family" in source
    assert "Object.entries(byFamily)" in source
    assert "signal.readiness_gap_names" in source
    assert "Readiness:" in source
    assert "function _missionPolicyTierText(snapshot)" in source
    assert "snapshot?.summary?.policy_tiers" in source
    assert "phases[role]?.policy_tiers" in source
    assert "Policy tiers:" in source


def test_list_sessions_status_calculation(monkeypatch):
    import routes.session_routes as sr
    from src import agent_runs
    from src import agent_run_ledger

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")

    # Define session IDs for different states
    working_id = str(uuid.uuid4())
    error_id = str(uuid.uuid4())
    attention_id = str(uuid.uuid4())
    done_id = str(uuid.uuid4())

    db = _TS()
    try:
        db.query(DbMsg).delete()
        db.query(DbSession).delete()

        # Add sessions
        db.add(DbSession(id=working_id, owner="alice", name="working chat",
                         endpoint_url="http://localhost", model="gpt-4", archived=False))
        db.add(DbSession(id=error_id, owner="alice", name="error chat",
                         endpoint_url="http://localhost", model="gpt-4", archived=False))
        db.add(DbSession(id=attention_id, owner="alice", name="attention chat",
                         endpoint_url="http://localhost", model="gpt-4", archived=False))
        db.add(DbSession(id=done_id, owner="alice", name="done chat",
                         endpoint_url="http://localhost", model="gpt-4", archived=False))
        
        # Add a last message for the attention session that indicates ask_user
        db.add(DbMsg(id=str(uuid.uuid4()), session_id=attention_id, role="assistant",
                     content="asked user", meta_data='{"ask_user": {"question": "Approach?"}}'))
        
        # Add normal messages for the other sessions
        db.add(DbMsg(id=str(uuid.uuid4()), session_id=working_id, role="user", content="hello"))
        db.add(DbMsg(id=str(uuid.uuid4()), session_id=error_id, role="assistant", content="failed before"))
        db.add(DbMsg(id=str(uuid.uuid4()), session_id=done_id, role="assistant", content="completed successfully"))

        db.commit()
    finally:
        db.close()

    # Mock agent_runs behavior
    monkeypatch.setattr(agent_runs, "is_active", lambda sid: sid == working_id)
    monkeypatch.setattr(agent_runs, "get_status", lambda sid: "error" if sid == error_id else None)
    monkeypatch.setattr(agent_run_ledger, "summarize_run", lambda sid, tail=1: {"status": "error"} if sid == error_id else {})

    # Mock session_manager
    session_dict = {
        working_id: MagicMock(id=working_id, name="working chat", model="gpt-4", endpoint_url="http://localhost", rag=False, archived=False),
        error_id: MagicMock(id=error_id, name="error chat", model="gpt-4", endpoint_url="http://localhost", rag=False, archived=False),
        attention_id: MagicMock(id=attention_id, name="attention chat", model="gpt-4", endpoint_url="http://localhost", rag=False, archived=False),
        done_id: MagicMock(id=done_id, name="done chat", model="gpt-4", endpoint_url="http://localhost", rag=False, archived=False),
    }
    
    sm = MagicMock()
    sm.get_sessions_for_user.return_value = session_dict
    router = sr.setup_session_routes(sm, {})
    endpoint = next(r.endpoint for r in router.routes
                    if getattr(r, "path", "") == "/api/sessions"
                    and "GET" in getattr(r, "methods", set()))

    result = endpoint(request=MagicMock())
    
    # Map result to dictionary
    res_map = {s["id"]: s for s in result}
    
    assert res_map[working_id]["status"] == "working"
    assert res_map[error_id]["status"] == "error"
    assert res_map[attention_id]["status"] == "attention"
    assert res_map[done_id]["status"] == "done"
