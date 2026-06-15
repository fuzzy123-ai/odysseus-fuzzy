import json

from plugins.obsidian.backend import vault_service
from plugins.obsidian.backend.context_provider import retrieve_vault_context
from src import agent_run_ledger
from src.mission_status import summarize_mission


def test_obsidian_context_memory_readiness_feeds_mission_snapshot(tmp_path, monkeypatch):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "Active.md").write_text(
        "---\nstatus: active\ntype: canonical\nupdated: 2026-06-14\n---\n# Active\n\nneedle source.\n",
        encoding="utf-8",
    )
    (vault_dir / "Review.md").write_text(
        "---\nstatus: needs_review\n---\n# Review\n\nneedle review.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: str(vault_dir))

    context = retrieve_vault_context("alice", "needle", 256, "chat")
    memory = context["memory"]
    assert memory["summary"]["readiness_state"] == "blocked"
    assert set(memory["readiness_by_family"]) == {"freshness", "raptor"}

    ledger_dir = tmp_path / "ledger"
    monkeypatch.setattr(agent_run_ledger, "AGENT_RUN_LEDGER_DIR", str(ledger_dir))
    session_id = "obsidian-memory-mission-contract"
    event = {
        "type": "tool_output",
        "tool": "obsidian_context",
        "round": 1,
        "exit_code": 0,
        "output": json.dumps({"memory": memory}),
    }

    agent_run_ledger.append_run_started(session_id)
    agent_run_ledger.append_sse_event(session_id, f"data: {json.dumps(event)}\n\n")
    agent_run_ledger.append_status(session_id, "done")

    snapshot = summarize_mission(session_id)

    assert snapshot["phases"]["verifier"]["status"] == "blocked"
    assert snapshot["phases"]["verifier"]["artifacts"] == {
        "freshness_readiness": 1,
        "raptor_readiness": 1,
        "readiness_check": 2,
    }
    assert set(snapshot["summary"]["readiness_by_family"]) == {"freshness", "raptor"}
    assert snapshot["summary"]["latest_blocker"]["family"] == "raptor"
