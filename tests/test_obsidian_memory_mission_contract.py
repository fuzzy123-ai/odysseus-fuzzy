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
    raptor_dir = vault_dir / ".obsidian" / "odysseus" / "raptor"
    raptor_dir.mkdir(parents=True)
    (raptor_dir / "index.json").write_text(
        json.dumps({"source_hashes": {"Active.md": "sha256:stale"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: str(vault_dir))

    context = retrieve_vault_context("alice", "needle", 256, "chat")
    memory = context["memory"]
    assert memory["summary"]["readiness_state"] == "blocked"
    assert memory["summary"]["readiness_gap_names"] == [
        "freshness_filtering_not_active",
        "needs_review_items",
        "source_hash_changed",
    ]
    assert set(memory["readiness_by_family"]) == {"freshness", "raptor"}
    assert memory["raptor"]["lineage_flags"] == {
        "dirty": True,
        "missing": False,
        "tainted": False,
        "invalid_index": False,
        "invalid_summaries": False,
    }
    assert memory["freshness_isolation_flags"] == memory["summary"]["freshness_isolation_flags"]
    assert memory["freshness_isolation_flags"] == memory["summary"]["isolation_flags"]
    assert memory["raptor_lineage_flags"] == memory["summary"]["raptor_lineage_flags"]
    assert memory["raptor_lineage_flags"] == memory["raptor"]["lineage_flags"]
    assert memory["raptor_write_gate"] == memory["summary"]["raptor_write_gate"]
    assert memory["raptor_write_gate"] == memory["raptor"]["write_gate"]
    assert memory["retrieval_policy"] == memory["summary"]["retrieval_policy"]
    assert memory["retrieval_policy"] == {
        "filtering_state": "audit_only",
        "default_retrieval_is_filtered": False,
        "isolated_knowledge_retained_in_audit": True,
        "excluded_relevant_count": 0,
    }
    memory["summary"]["warnings"] = ["Freshness Gate filtered 1 stale item(s)."]

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
    assert snapshot["summary"]["readiness_gate"] == {
        "required": True,
        "satisfied": False,
        "state": "blocked",
        "families": 2,
        "ready_families": 0,
        "blocked_families": ["freshness", "raptor"],
        "gaps": [
            "freshness_filtering_not_active",
            "needs_review_items",
            "source_hash_changed",
        ],
    }
    assert set(snapshot["summary"]["readiness_by_family"]) == {"freshness", "raptor"}
    assert snapshot["summary"]["readiness_by_family"]["freshness"]["gaps"] == [
        "freshness_filtering_not_active",
        "needs_review_items",
    ]
    assert snapshot["summary"]["readiness_by_family"]["raptor"]["gaps"] == ["source_hash_changed"]
    assert snapshot["summary"]["memory_diagnostics"] == {
        "retrieval_policy": {
            "default_retrieval_is_filtered": False,
            "excluded_relevant_count": 0,
            "filtering_state": "audit_only",
            "isolated_knowledge_retained_in_audit": True,
        },
        "freshness_isolation_flags": {
            "conflicts": False,
            "filtering_active": False,
            "isolated": True,
            "needs_review": True,
            "quarantined": False,
        },
        "raptor_lineage_flags": {
            "dirty": True,
            "invalid_index": False,
            "invalid_summaries": False,
            "missing": False,
            "tainted": False,
        },
        "raptor_write_gate": {
            "feature_enabled": False,
            "feature_flag": "obsidian_raptor_enabled",
            "rebuild_enabled": False,
            "rebuild_feature_flag": "obsidian_raptor_rebuild_enabled",
            "gaps": [
                "raptor_feature_flag_disabled",
                "raptor_rebuild_feature_flag_disabled",
            ],
            "state": "blocked",
            "writes_supported": False,
        },
    }
    assert snapshot["summary"]["memory_diagnostics_state"] == "attention"
    assert snapshot["summary"]["memory_diagnostics_active_flags"] == {
        "freshness_isolation_flags": ["isolated", "needs_review"],
        "raptor_lineage_flags": ["dirty"],
    }
    assert snapshot["summary"]["memory_warnings"] == ["Freshness Gate filtered 1 stale item(s)."]
    assert snapshot["summary"]["memory_warnings_state"] == "attention"
    assert snapshot["summary"]["latest_blocker"]["family"] == "raptor"
    assert snapshot["summary"]["latest_blocker"]["gaps"] == ["source_hash_changed"]
    assert "resolve_readiness_gaps" in snapshot["next_actions"]
    assert "inspect_memory_diagnostics" in snapshot["next_actions"]
    assert "inspect_memory_warnings" in snapshot["next_actions"]
