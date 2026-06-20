import hashlib
import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
import backend.memory_status as memory_status_backend
from backend.freshness import audit_knowledge, quarantine_list
from backend.derived_index import build_derived_index
from backend.hybrid_retrieval import raptor_status
from backend.knowledge_status import normalize_status
from backend.memory_status import memory_status
from backend.memory_tree import analyze_memory_tree, memory_tree_status
from plugin import (
    handle_knowledge_audit,
    handle_memory_status,
    handle_memory_tree_analyze,
    handle_memory_tree_status,
    handle_quarantine_list,
    handle_raptor_status,
)


def test_memory_tree_analyzer_is_read_only_and_reports_candidates():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "AI Memory", "Canonical"), exist_ok=True)
        with open(os.path.join(tmpdir, "AI Memory", "Canonical", "Architecture.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: active\n"
                "type: canonical\n"
                "confidence: high\n"
                "last_verified_at: 2026-06-14\n"
                "---\n"
                "# Architecture\n\nLinks to [[Loose]].\n"
            )
        with open(os.path.join(tmpdir, "Loose.md"), "w", encoding="utf-8") as f:
            f.write("# Loose\n\nNo frontmatter yet.")

        before = sorted(os.listdir(tmpdir))
        report = analyze_memory_tree(tmpdir)
        status = memory_tree_status(tmpdir)
        after = sorted(os.listdir(tmpdir))

        assert before == after
        assert report["storage"]["writes_performed"] is False
        assert report["summary"]["total_notes"] == 2
        assert report["summary"]["default_retrieval"] == 2
        assert report["summary"]["isolated"] == 0
        assert report["summary"]["isolation_counts"] == {}
        assert report["readiness"] == {
            "ready": False,
            "state": "needs_review",
            "gaps": ["somt_issues_present"],
            "writes_supported": False,
        }
        assert report["readiness_signals"] == [
            {
                "family": "somt",
                "source": "readiness",
                "state": "needs_review",
                "ready": False,
                "gaps": ["somt_issues_present"],
                "gap_count": 1,
            }
        ]
        assert report["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 1,
            "ready_families": 0,
            "blocked_families": ["somt"],
            "gaps": ["somt_issues_present"],
        }
        assert any(node["truth_level"] == "canonical" for node in report["nodes"])
        assert any(node["status"] == "active" and node["default_retrieval"] is True for node in report["nodes"])
        assert any(issue["type"] == "missing_frontmatter" and issue["path"] == "Loose.md" for issue in report["issues"])
        assert status["summary"]["total_notes"] == 2
        assert status["summary"]["default_retrieval"] == 2
        assert status["summary"]["isolated"] == 0
        assert status["summary"]["isolation_counts"] == {}
        assert status["summary"]["readiness_state"] == "needs_review"
        assert status["summary"]["readiness_gaps"] == 1
        assert status["readiness"] == report["readiness"]
        assert status["readiness_signals"] == report["readiness_signals"]
        assert status["readiness_gate"] == report["readiness_gate"]
        assert status["summary"]["readiness_gate"] == report["readiness_gate"]


def test_memory_status_aggregates_read_only_readiness_layers():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\ntype: canonical\nupdated: 2026-06-14\n---\n# Active\n")
        with open(os.path.join(tmpdir, "Review.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: needs_review\n---\n# Review\n")
        build_derived_index(tmpdir)

        before = set(os.listdir(tmpdir))
        status = memory_status(tmpdir)
        after = set(os.listdir(tmpdir))

        assert before | {".obsidian"} == after
        assert status["read_only"] is True
        assert status["writes_supported"] is False
        assert status["filtering_state"] == "audit_only"
        assert status["flags"]["obsidian_freshness_gate_enabled"] is True
        assert status["flags"]["obsidian_hybrid_retrieval_enabled"] is False
        assert set(status["families"]) == {"ledger", "derived_index", "query_layer", "somt", "freshness", "quarantine", "raptor"}
        assert set(status["readiness_by_family"]) == {"ledger", "derived_index", "query_layer", "freshness", "raptor", "somt"}
        assert status["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 6,
            "ready_families": 3,
            "blocked_families": ["freshness", "raptor", "somt"],
            "gaps": [
                "somt_issues_present",
                "freshness_filtering_not_active",
                "needs_review_items",
                "raptor_index_missing",
            ],
        }
        assert status["summary"]["families"] == 6
        assert status["summary"]["status_families"] == 7
        assert status["summary"]["readiness_families"] == 6
        assert status["summary"]["ready_families"] == 3
        assert status["summary"]["blocked_families"] == ["freshness", "raptor", "somt"]
        assert status["summary"]["readiness_state"] == "blocked"
        assert status["summary"]["filtering_state"] == "audit_only"
        assert status["summary"]["readiness_gap_names"] == [
            "somt_issues_present",
            "freshness_filtering_not_active",
            "needs_review_items",
            "raptor_index_missing",
        ]
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]
        assert status["retrieval_policy"] == {
            "filtering_state": "audit_only",
            "default_retrieval_is_filtered": False,
            "isolated_knowledge_retained_in_audit": True,
            "excluded_relevant_count": 0,
        }
        assert status["summary"]["retrieval_policy"] == status["retrieval_policy"]
        assert status["summary"]["ledger_sources"] == 2
        assert status["summary"]["ledger_status_counts"] == {"indexed": 2}
        assert status["summary"]["ledger_source_types"] == {"markdown": 2}
        assert status["summary"]["derived_index_sources"] == 2
        assert status["summary"]["derived_index_chunks"] >= 2
        assert status["summary"]["query_layer_sources"] == 2
        assert status["summary"]["query_layer_chunks"] >= 2
        assert status["summary"]["default_retrieval"] == 1
        assert status["summary"]["isolated"] == 1
        assert status["summary"]["quarantine_items"] == 1
        assert status["freshness_isolation_flags"] == {
            "needs_review": True,
            "conflicts": False,
            "quarantined": False,
            "isolated": True,
            "filtering_active": False,
        }
        assert status["summary"]["freshness_isolation_flags"] == status["freshness_isolation_flags"]
        assert status["raptor_lineage_flags"] == {
            "dirty": False,
            "missing": False,
            "tainted": False,
            "invalid_index": False,
            "invalid_summaries": False,
        }
        assert status["summary"]["raptor_lineage_flags"] == status["raptor_lineage_flags"]
        assert status["raptor_write_gate"] == {
            "feature_flag": "obsidian_raptor_enabled",
            "feature_enabled": False,
            "rebuild_feature_flag": "obsidian_raptor_rebuild_enabled",
            "rebuild_enabled": False,
            "writes_supported": False,
            "state": "blocked",
            "gaps": [
                "raptor_feature_flag_disabled",
                "raptor_rebuild_feature_flag_disabled",
            ],
        }
        assert status["summary"]["raptor_write_gate"] == status["raptor_write_gate"]
        assert status["summary"]["writes_supported"] is False
        assert status["summary"]["warnings"] == status["warnings"]


def test_memory_status_preserves_compact_layer_warnings(monkeypatch):
    base_signal = {
        "family": "somt",
        "source": "readiness",
        "state": "warnings",
        "ready": False,
        "gaps": ["somt_warnings_present"],
        "gap_count": 1,
    }
    monkeypatch.setattr(memory_status_backend, "memory_tree_status", lambda vault_dir: {
        "enabled": True,
        "readiness": {"state": "warnings", "gaps": ["somt_warnings_present"]},
        "readiness_signals": [base_signal],
        "summary": {"warnings": ["somt warning"]},
        "flags": {},
        "warnings": ["somt warning", "somt warning"],
    })
    monkeypatch.setattr(memory_status_backend, "audit_knowledge", lambda vault_dir: {
        "enabled": True,
        "flags": {},
        "filtering_state": "active",
        "readiness_signals": [],
        "summary": {
            "filtering_state": "active",
            "default_retrieval": 0,
            "isolated": 0,
            "warnings": ["freshness warning"],
        },
        "warnings": [],
    })
    monkeypatch.setattr(memory_status_backend, "quarantine_list", lambda vault_dir: {
        "enabled": True,
        "flags": {},
        "summary": {"total": 0},
        "warnings": ["freshness warning"],
    })
    monkeypatch.setattr(memory_status_backend, "memory_ledger_status", lambda vault_dir: {
        "enabled": True,
        "readiness": {"state": "ready", "ready": True, "gaps": [], "writes_supported": True},
        "readiness_signals": [{
            "family": "ledger",
            "source": "readiness",
            "state": "ready",
            "ready": True,
            "gaps": [],
            "gap_count": 0,
        }],
        "summary": {"total_sources": 0, "status_counts": {}, "source_types": {}, "warnings": []},
        "warnings": [],
    })
    monkeypatch.setattr(memory_status_backend, "derived_index_status", lambda vault_dir: {
        "enabled": True,
        "readiness": {"state": "ready", "ready": True, "gaps": [], "writes_supported": True},
        "readiness_signals": [{
            "family": "derived_index",
            "source": "readiness",
            "state": "ready",
            "ready": True,
            "gaps": [],
            "gap_count": 0,
        }],
        "summary": {"source_count": 0, "chunk_count": 0, "graph_nodes": 0, "graph_edges": 0, "warnings": []},
        "warnings": [],
    })
    monkeypatch.setattr(memory_status_backend, "query_layer_status", lambda vault_dir: {
        "enabled": True,
        "readiness": {"state": "ready", "ready": True, "gaps": [], "writes_supported": False},
        "readiness_signals": [{
            "family": "query_layer",
            "source": "readiness",
            "state": "ready",
            "ready": True,
            "gaps": [],
            "gap_count": 0,
        }],
        "summary": {"source_count": 0, "chunk_count": 0, "warnings": []},
        "warnings": [],
    })
    monkeypatch.setattr(memory_status_backend, "raptor_status", lambda vault_dir: {
        "enabled": False,
        "flags": {},
        "summary": {},
        "lineage_flags": {},
        "write_gate": {},
        "warnings": ["raptor warning"],
    })

    status = memory_status_backend.memory_status("vault")

    assert status["warnings"] == ["somt warning", "freshness warning", "raptor warning"]
    assert status["summary"]["warnings"] == status["warnings"]
    assert status["readiness_gate"]["gaps"] == ["somt_warnings_present"]


def test_memory_status_propagates_raptor_metadata_gaps():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "index.json"), "w", encoding="utf-8") as f:
            json.dump({"dirty": True, "tainted": True, "source_hashes": {}}, f)

        status = memory_status(tmpdir)

        assert status["readiness_by_family"]["raptor"]["gaps"] == [
            "raptor_metadata_dirty",
            "raptor_metadata_tainted",
        ]
        assert "raptor_metadata_dirty" in status["readiness_gate"]["gaps"]
        assert "raptor_metadata_tainted" in status["readiness_gate"]["gaps"]
        assert "raptor_metadata_dirty" in status["summary"]["readiness_gap_names"]
        assert "raptor_metadata_tainted" in status["summary"]["readiness_gap_names"]
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]


@pytest.mark.asyncio
async def test_memory_status_route_is_read_only_unified_dashboard(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\ntype: canonical\nupdated: 2026-06-14\n---\n# Active\n")
        with open(os.path.join(tmpdir, "Conflict.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: conflict\n---\n# Conflict\n")
        build_derived_index(tmpdir)

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        before = set(os.listdir(tmpdir))
        status = await obsidian_routes.memory_status_route(SimpleNamespace())
        after = set(os.listdir(tmpdir))

        assert before | {".obsidian"} == after
        assert status["read_only"] is True
        assert status["writes_supported"] is False
        assert status["filtering_state"] == "audit_only"
        assert status["summary"]["filtering_state"] == "audit_only"
        assert status["summary"]["retrieval_policy"] == status["retrieval_policy"]
        assert status["summary"]["readiness_state"] == "blocked"
        assert status["readiness_gate"]["state"] == "blocked"
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]
        assert "conflict_items" in status["summary"]["readiness_gap_names"]
        assert "conflict_items" in status["readiness_gate"]["gaps"]
        assert set(status["families"]) == {"ledger", "derived_index", "query_layer", "somt", "freshness", "quarantine", "raptor"}
        assert set(status["readiness_by_family"]) == {"ledger", "derived_index", "query_layer", "freshness", "raptor", "somt"}
        assert status["summary"]["freshness_isolation_flags"] == status["freshness_isolation_flags"]
        assert status["summary"]["raptor_lineage_flags"] == status["raptor_lineage_flags"]
        assert status["summary"]["raptor_write_gate"] == status["raptor_write_gate"]


def test_memory_tree_status_preserves_analysis_warnings(monkeypatch):
    monkeypatch.setattr("backend.memory_tree.analyze_memory_tree", lambda vault_dir, limit=200: {
        "enabled": True,
        "storage": {"mode": "vault"},
        "readiness": {"state": "warnings", "gaps": ["somt_warnings_present"]},
        "readiness_signals": [],
        "readiness_gate": {"state": "blocked", "gaps": ["somt_warnings_present"]},
        "summary": {"total_notes": 0},
        "issues": [],
        "flags": {},
        "warnings": ["Could not read Hidden.md"],
    })

    assert memory_tree_status("vault")["warnings"] == ["Could not read Hidden.md"]


@pytest.mark.asyncio
async def test_memory_layer_routes_expose_readiness_gates(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Fact.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: needs_review\n---\n# Fact\n")

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        request = SimpleNamespace()

        tree = await obsidian_routes.memory_tree(request)
        analyze = await obsidian_routes.memory_tree_analyze(request)
        audit = await obsidian_routes.knowledge_audit(request)
        quarantine = await obsidian_routes.quarantine(request)
        raptor = await obsidian_routes.raptor_status_route(request)

        for payload in (tree, analyze, audit, quarantine, raptor):
            assert payload["readiness_gate"]["required"] is True
            assert payload["summary"]["readiness_gate"] == payload["readiness_gate"]

        assert tree["readiness_gate"]["blocked_families"] == ["somt"]
        assert audit["readiness_gate"]["blocked_families"] == ["freshness"]
        assert quarantine["readiness_gate"] == audit["readiness_gate"]
        assert raptor["readiness_gate"]["blocked_families"] == ["raptor"]


def test_freshness_audit_and_quarantine_are_read_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Current.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\nupdated: 2026-06-14\n---\n# Current\n")
        with open(os.path.join(tmpdir, "Old.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: stale\nupdated: 2026-01-01\n---\n# Old\n")
        os.makedirs(os.path.join(tmpdir, "AI Memory", "Review Queue"), exist_ok=True)
        with open(os.path.join(tmpdir, "AI Memory", "Review Queue", "Candidate.md"), "w", encoding="utf-8") as f:
            f.write("---\nconfidence: low\n---\n# Candidate\n")
        os.makedirs(os.path.join(tmpdir, "AI Memory", "Quarantine"), exist_ok=True)
        with open(os.path.join(tmpdir, "AI Memory", "Quarantine", "Held.md"), "w", encoding="utf-8") as f:
            f.write("---\nupdated: 2026-06-14\n---\n# Held\n")

        before = set(os.listdir(tmpdir))
        audit = audit_knowledge(tmpdir)
        quarantine = quarantine_list(tmpdir)
        after = set(os.listdir(tmpdir))

        assert before == after
        assert audit["summary"]["current"] == 1
        assert audit["filtering_state"] == "audit_only"
        assert audit["readiness"]["ready"] is False
        assert audit["readiness"]["state"] == "needs_review"
        assert audit["readiness"]["gaps"] == [
            "freshness_filtering_not_active",
            "needs_review_items",
            "quarantined_items",
        ]
        assert audit["readiness_signals"] == [
            {
                "family": "freshness",
                "source": "readiness",
                "state": "needs_review",
                "ready": False,
                "gaps": [
                    "freshness_filtering_not_active",
                    "needs_review_items",
                    "quarantined_items",
                ],
                "gap_count": 3,
            }
        ]
        assert audit["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 1,
            "ready_families": 0,
            "blocked_families": ["freshness"],
            "gaps": [
                "freshness_filtering_not_active",
                "needs_review_items",
                "quarantined_items",
            ],
        }
        assert audit["summary"]["default_retrieval"] == 1
        assert audit["summary"]["isolated"] == 3
        assert audit["summary"]["isolation_counts"] == {"needs_review": 1, "quarantined": 1, "stale": 1}
        assert audit["isolation_flags"] == {
            "needs_review": True,
            "conflicts": False,
            "quarantined": True,
            "isolated": True,
            "filtering_active": False,
        }
        assert audit["summary"]["isolation_flags"] == audit["isolation_flags"]
        assert audit["summary"]["filtering_state"] == "audit_only"
        assert audit["summary"]["readiness_state"] == "needs_review"
        assert audit["summary"]["readiness_gaps"] == 3
        assert audit["summary"]["readiness_gate"] == audit["readiness_gate"]
        assert audit["summary"]["warnings"] == audit["warnings"]
        assert any(item["path"] == "AI Memory/Review Queue/Candidate.md" for item in audit["channels"]["needs_review"])
        assert any(item["path"] == "AI Memory/Review Queue/Candidate.md" for item in quarantine["items"])
        assert any(item["path"] == "Old.md" for item in quarantine["items"])
        assert any(item["path"] == "AI Memory/Quarantine/Held.md" for item in quarantine["items"])
        assert quarantine["flags"]["obsidian_freshness_gate_enabled"] is True
        assert quarantine["flags"]["obsidian_hybrid_retrieval_enabled"] is False
        assert quarantine["filtering_state"] == "audit_only"
        assert quarantine["readiness"]["state"] == "needs_review"
        assert quarantine["readiness_signals"] == audit["readiness_signals"]
        assert quarantine["readiness_gate"] == audit["readiness_gate"]
        assert quarantine["summary"]["default_retrieval"] == 0
        assert quarantine["summary"]["isolated"] == 3
        assert quarantine["summary"]["by_channel"] == {"needs_review": 1, "quarantined": 2}
        assert quarantine["isolation_flags"] == audit["isolation_flags"]
        assert quarantine["summary"]["isolation_flags"] == audit["isolation_flags"]
        assert quarantine["summary"]["filtering_state"] == "audit_only"
        assert quarantine["summary"]["readiness_state"] == "needs_review"
        assert quarantine["summary"]["readiness_gaps"] == 3
        assert quarantine["summary"]["readiness_gate"] == audit["readiness_gate"]
        assert quarantine["summary"]["warnings"] == quarantine["warnings"]


def test_freshness_readiness_is_ready_when_filtering_active_and_clean(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Current.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\ntype: canonical\nupdated: 2026-06-14\n---\n# Current\n")

        monkeypatch.setenv("ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED", "true")

        audit = audit_knowledge(tmpdir)

        assert audit["filtering_state"] == "active"
        assert audit["readiness"] == {
            "ready": True,
            "state": "ready",
            "gaps": [],
            "writes_supported": False,
        }
        assert audit["isolation_flags"] == {
            "needs_review": False,
            "conflicts": False,
            "quarantined": False,
            "isolated": False,
            "filtering_active": True,
        }
        assert audit["readiness_signals"] == [
            {
                "family": "freshness",
                "source": "readiness",
                "state": "ready",
                "ready": True,
                "gaps": [],
                "gap_count": 0,
            }
        ]
        assert audit["summary"]["readiness_state"] == "ready"
        assert audit["summary"]["readiness_gaps"] == 0


def test_unresolved_conflict_status_is_isolated_from_default_truth():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Conflict.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: unresolved_conflict\nupdated: 2026-06-14\n---\n# Conflict\n")

        audit = audit_knowledge(tmpdir)
        quarantine = quarantine_list(tmpdir)
        tree = analyze_memory_tree(tmpdir)

        assert audit["summary"]["conflicts"] == 1
        assert audit["summary"]["isolated"] == 1
        assert audit["channels"]["conflicts"][0]["path"] == "Conflict.md"
        assert audit["channels"]["conflicts"][0]["status"] == "conflict"
        assert quarantine["summary"]["by_status"]["conflict"] == 1
        assert quarantine["summary"]["isolated"] == 1
        assert quarantine["items"][0]["path"] == "Conflict.md"
        assert tree["nodes"][0]["status"] == "conflict"
        assert tree["nodes"][0]["default_retrieval"] is False
        assert "Unresolved conflict" in tree["nodes"][0]["isolation_reason"]
        assert tree["summary"]["default_retrieval"] == 0
        assert tree["summary"]["isolated"] == 1
        assert tree["summary"]["isolation_counts"] == {"conflict": 1}


@pytest.mark.parametrize("raw", ["unresolved_conflict", "unresolved conflict", "unresolved-conflict", "conflicted"])
def test_knowledge_status_aliases_normalize_to_conflict(raw):
    assert normalize_status(raw) == "conflict"


@pytest.mark.parametrize(("raw", "normalized"), [
    ("deprecated", "superseded"),
    ("obsolete", "superseded"),
    ("quarantine", "quarantined"),
    ("archive", "archived"),
    ("retired", "archived"),
])
def test_knowledge_status_aliases_normalize_to_isolated_statuses(raw, normalized):
    assert normalize_status(raw) == normalized


def test_freshness_gate_isolates_status_aliases():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Deprecated.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: deprecated\nupdated: 2026-06-14\n---\n# Deprecated\n")

        audit = audit_knowledge(tmpdir)
        quarantine = quarantine_list(tmpdir)

        assert audit["summary"]["quarantined"] == 1
        assert audit["channels"]["quarantined"][0]["status"] == "superseded"
        assert quarantine["summary"]["by_status"]["superseded"] == 1


def test_raptor_status_has_write_gates_disabled_by_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        status = raptor_status(tmpdir)

        assert status["enabled"] is False
        assert status["configured"] is False
        assert status["writes_supported"] is False
        assert status["readiness"] == {
            "ready": False,
            "state": "not_configured",
            "gaps": ["raptor_index_missing"],
            "writes_supported": False,
        }
        assert status["readiness_signals"] == [
            {
                "family": "raptor",
                "source": "readiness",
                "state": "not_configured",
                "ready": False,
                "gaps": ["raptor_index_missing"],
                "gap_count": 1,
            }
        ]
        assert status["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 1,
            "ready_families": 0,
            "blocked_families": ["raptor"],
            "gaps": ["raptor_index_missing"],
        }
        assert status["summary"]["readiness_state"] == "not_configured"
        assert status["summary"]["readiness_gaps"] == 1
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]
        assert status["write_gate"] == {
            "feature_flag": "obsidian_raptor_enabled",
            "feature_enabled": False,
            "rebuild_feature_flag": "obsidian_raptor_rebuild_enabled",
            "rebuild_enabled": False,
            "writes_supported": False,
            "state": "blocked",
            "gaps": [
                "raptor_feature_flag_disabled",
                "raptor_rebuild_feature_flag_disabled",
            ],
        }
        assert status["summary"]["write_gate"] == status["write_gate"]


def test_raptor_write_gate_stays_blocked_when_feature_flag_enabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        status = raptor_status(tmpdir)

        assert status["enabled"] is True
        assert status["writes_supported"] is False
        assert status["summary"]["writes_supported"] is False
        assert status["write_gate"] == {
            "feature_flag": "obsidian_raptor_enabled",
            "feature_enabled": True,
            "rebuild_feature_flag": "obsidian_raptor_rebuild_enabled",
            "rebuild_enabled": False,
            "writes_supported": False,
            "state": "blocked",
            "gaps": [
                "raptor_rebuild_feature_flag_disabled",
            ],
        }
        assert status["summary"]["write_gate"] == status["write_gate"]


def test_raptor_status_supports_writes_when_readiness_and_write_flags_are_ready(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        content = "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\nStable source.\n"
        with open(os.path.join(tmpdir, "Canon.md"), "w", encoding="utf-8") as f:
            f.write(content)
        source_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "index.json"), "w", encoding="utf-8") as f:
            json.dump({"built_at": "2026-06-14T00:00:00Z", "source_hashes": {"Canon.md": source_hash}}, f)

        status = raptor_status(tmpdir)

        assert status["readiness"]["state"] == "ready"
        assert status["write_gate"] == {
            "feature_flag": "obsidian_raptor_enabled",
            "feature_enabled": True,
            "rebuild_feature_flag": "obsidian_raptor_rebuild_enabled",
            "rebuild_enabled": True,
            "writes_supported": True,
            "state": "ready",
            "gaps": [],
        }
        assert status["writes_supported"] is True
        assert status["summary"]["writes_supported"] is True


def test_raptor_status_tracks_source_hash_lineage_without_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Canon.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\nStable source.\n")
        source_hash = "sha256:" + hashlib.sha256(
            "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\nStable source.\n".encode("utf-8")
        ).hexdigest()
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "index.json"), "w", encoding="utf-8") as f:
            json.dump({"built_at": "2026-06-14T00:00:00Z", "source_hashes": {"Canon.md": source_hash}}, f)

        before = set(os.listdir(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor")))
        status = raptor_status(tmpdir)
        after = set(os.listdir(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor")))

        assert before == after
        assert status["configured"] is True
        assert status["dirty"] is False
        assert status["tainted"] is False
        assert status["readiness"] == {
            "ready": True,
            "state": "ready",
            "gaps": [],
            "writes_supported": False,
        }
        assert status["readiness_signals"] == [
            {
                "family": "raptor",
                "source": "readiness",
                "state": "ready",
                "ready": True,
                "gaps": [],
                "gap_count": 0,
            }
        ]
        assert status["readiness_gate"] == {
            "required": True,
            "satisfied": True,
            "state": "ready",
            "families": 1,
            "ready_families": 1,
            "blocked_families": [],
            "gaps": [],
        }
        assert status["lineage"]["source_count"] == 1
        assert status["lineage"]["dirty_sources"] == []
        assert status["lineage"]["missing_sources"] == []
        assert status["lineage"]["tainted_sources"] == []
        assert status["lineage"]["summary"] == {
            "source_count": 1,
            "dirty": 0,
            "missing": 0,
            "tainted": 0,
        }
        assert status["summary"]["source_count"] == 1
        assert status["summary"]["dirty_sources"] == 0
        assert status["summary"]["missing_sources"] == 0
        assert status["summary"]["tainted_sources"] == 0
        assert status["summary"]["readiness_state"] == "ready"
        assert status["summary"]["readiness_gaps"] == 0
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]
        assert status["summary"]["writes_supported"] is False


def test_raptor_status_marks_changed_or_missing_sources_dirty():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Changed.md"), "w", encoding="utf-8") as f:
            f.write("---\ntype: canonical\nupdated: 2026-06-14\n---\n# Changed\nNew content.\n")
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "index.json"), "w", encoding="utf-8") as f:
            json.dump({
                "source_hashes": {
                    "Changed.md": "sha256:old",
                    "Missing.md": "sha256:missing",
                }
            }, f)

        status = raptor_status(tmpdir)

        assert status["dirty"] is True
        assert [item["path"] for item in status["lineage"]["dirty_sources"]] == ["Changed.md"]
        assert status["lineage"]["missing_sources"] == ["Missing.md"]
        assert status["lineage"]["summary"]["dirty"] == 1
        assert status["lineage"]["summary"]["missing"] == 1
        assert status["summary"]["dirty_sources"] == 1
        assert status["summary"]["missing_sources"] == 1
        assert status["lineage_flags"] == {
            "dirty": True,
            "missing": True,
            "tainted": False,
            "invalid_index": False,
            "invalid_summaries": False,
        }
        assert status["summary"]["lineage_flags"] == status["lineage_flags"]
        assert status["readiness"]["ready"] is False
        assert status["readiness"]["state"] == "dirty"
        assert status["readiness"]["gaps"] == ["source_hash_changed", "source_missing"]
        assert status["summary"]["readiness_state"] == "dirty"
        assert status["summary"]["readiness_gaps"] == 2
        assert status["readiness_gate"]["state"] == "blocked"
        assert status["readiness_gate"]["gaps"] == ["source_hash_changed", "source_missing"]
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]


def test_raptor_status_reports_dirty_tainted_metadata_gaps():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "index.json"), "w", encoding="utf-8") as f:
            json.dump({"dirty": True, "tainted": True, "source_hashes": {}}, f)

        status = raptor_status(tmpdir)

        assert status["dirty"] is True
        assert status["tainted"] is True
        assert status["lineage"]["dirty_sources"] == []
        assert status["lineage"]["tainted_sources"] == []
        assert status["readiness"]["ready"] is False
        assert status["readiness"]["state"] == "dirty"
        assert status["readiness"]["gaps"] == ["raptor_metadata_dirty", "raptor_metadata_tainted"]
        assert status["readiness_gate"]["gaps"] == ["raptor_metadata_dirty", "raptor_metadata_tainted"]
        assert status["summary"]["readiness_gap_names"] == ["raptor_metadata_dirty", "raptor_metadata_tainted"]
        assert status["summary"]["readiness_gate"] == status["readiness_gate"]


def test_raptor_status_reports_invalid_readiness_gap():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "index.json"), "w", encoding="utf-8") as f:
            f.write("{not json")

        status = raptor_status(tmpdir)

        assert status["dirty"] is True
        assert status["tainted"] is True
        assert status["lineage_flags"]["invalid_index"] is True
        assert status["summary"]["lineage_flags"] == status["lineage_flags"]
        assert status["readiness"]["ready"] is False
        assert status["readiness"]["state"] == "invalid"
        assert status["readiness"]["gaps"] == ["raptor_index_invalid"]
        assert status["summary"]["invalid_sources"] == 1
        assert status["summary"]["readiness_state"] == "invalid"
        assert status["summary"]["readiness_gaps"] == 1
        assert status["warnings"] == [
            "RAPTOR index metadata is invalid; rebuild can refresh derived artifacts when the write gate is enabled."
        ]
        assert status["summary"]["warnings"] == status["warnings"]


def test_raptor_status_marks_review_or_quarantined_sources_tainted():
    with tempfile.TemporaryDirectory() as tmpdir:
        content = "---\nstatus: needs_review\n---\n# Candidate\nUnverified source.\n"
        with open(os.path.join(tmpdir, "Candidate.md"), "w", encoding="utf-8") as f:
            f.write(content)
        source_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        os.makedirs(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor"), exist_ok=True)
        with open(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor", "summaries.json"), "w", encoding="utf-8") as f:
            json.dump({"summaries": [{"source_hashes": {"Candidate.md": source_hash}}]}, f)

        status = raptor_status(tmpdir)

        assert status["dirty"] is False
        assert status["tainted"] is True
        assert status["lineage_flags"] == {
            "dirty": False,
            "missing": False,
            "tainted": True,
            "invalid_index": False,
            "invalid_summaries": False,
        }
        assert status["summary"]["lineage_flags"] == status["lineage_flags"]
        assert status["lineage"]["tainted_sources"][0]["path"] == "Candidate.md"
        assert status["lineage"]["tainted_sources"][0]["status"] == "needs_review"
        assert status["lineage"]["tainted_sources"][0]["channel"] == "needs_review"
        assert status["lineage"]["tainted_sources"][0]["policy"] == "implementation_status"
        assert status["lineage"]["tainted_sources"][0]["source_hash"] == source_hash
        assert status["lineage"]["tainted_sources"][0]["source_mtime"].endswith("Z")
        assert status["lineage"]["summary"]["tainted"] == 1
        assert status["summary"]["tainted_sources"] == 1
        assert status["readiness"]["ready"] is False
        assert status["readiness"]["state"] == "tainted"
        assert status["readiness"]["gaps"] == ["source_isolated_from_default_retrieval"]
        assert status["summary"]["readiness_state"] == "tainted"
        assert status["summary"]["readiness_gaps"] == 1


@pytest.mark.asyncio
async def test_memory_tree_agent_tools_are_read_only(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Fact.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: archived\n---\n# Fact\n")

        memory_tree_status_res = await handle_memory_tree_status("", owner="alice")
        assert memory_tree_status_res["exit_code"] == 0
        assert "readiness_gate" in json.loads(memory_tree_status_res["output"])
        memory_status_res = await handle_memory_status("", owner="alice")
        assert memory_status_res["exit_code"] == 0
        memory_status_payload = json.loads(memory_status_res["output"])
        assert memory_status_payload["read_only"] is True
        assert "readiness_gate" in memory_status_payload
        assert (await handle_memory_tree_analyze("{}", owner="alice"))["exit_code"] == 0
        audit_res = await handle_knowledge_audit("", owner="alice")
        assert audit_res["exit_code"] == 0
        assert "readiness_gate" in json.loads(audit_res["output"])
        quarantine = await handle_quarantine_list("", owner="alice")
        assert quarantine["exit_code"] == 0
        quarantine_payload = json.loads(quarantine["output"])
        assert "Fact.md" in quarantine["output"]
        assert "readiness_gate" in quarantine_payload
        raptor_res = await handle_raptor_status("", owner="alice")
        assert raptor_res["exit_code"] == 0
        assert "readiness_gate" in json.loads(raptor_res["output"])
