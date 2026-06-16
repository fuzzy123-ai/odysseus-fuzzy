import os
import sys
import tempfile

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend import vault_service
from backend.context_provider import parse_frontmatter, retrieve_vault_context
from backend.vault_security import lock_vault, set_password
from src.model_context import estimate_tokens


def test_obsidian_context_provider_returns_stable_vault_context(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "title: Demo Project\n"
                "status: active\n"
                "tags: [demo, retrieval]\n"
                "---\n"
                "# Demo\n\nRetrieval context belongs in this body snippet.\n"
            )
        with open(os.path.join(tmpdir, "Archive.md"), "w", encoding="utf-8") as f:
            f.write("# Archive\n\nUnrelated note.")

        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)

        payload = retrieve_vault_context("alice", "demo retrieval", 128, "chat")
        repeat = retrieve_vault_context("alice", "demo retrieval", 128, "chat")

        assert payload["cache_key"] == repeat["cache_key"]
        assert len(payload["cache_key"]) == 64
        assert payload["structured_state"]["Projects/Demo.md"]["status"] == "active"
        assert payload["snippets"][0]["path"] == "Projects/Demo.md"
        assert payload["snippets"][0]["untrusted"] is True
        assert payload["sources"][0]["path"] == "Projects/Demo.md"
        assert "Retrieval context" in payload["snippets"][0]["text"]
        assert "memory" in payload
        assert payload["memory"]["retrieval_filtering"] is False
        assert payload["memory"]["filtering_state"] == "audit_only"
        assert payload["memory"]["summary"]["filtering_state"] == "audit_only"
        assert payload["memory"]["summary"]["readiness_state"] == "blocked"
        assert payload["memory"]["summary"]["readiness_gaps"] == 3
        assert payload["memory"]["summary"]["readiness_gap_names"] == [
            "freshness_filtering_not_active",
            "needs_review_items",
            "raptor_index_missing",
        ]
        assert payload["memory"]["summary"]["freshness_readiness_state"] == "needs_review"
        assert payload["memory"]["summary"]["freshness_readiness_gaps"] == 2
        assert payload["memory"]["summary"]["freshness_readiness_gap_names"] == [
            "freshness_filtering_not_active",
            "needs_review_items",
        ]
        assert payload["memory"]["summary"]["raptor_readiness_state"] == "not_configured"
        assert payload["memory"]["summary"]["raptor_readiness_gaps"] == 1
        assert payload["memory"]["summary"]["raptor_readiness_gap_names"] == ["raptor_index_missing"]
        assert payload["memory"]["readiness_signals"] == [
            {
                "family": "freshness",
                "source": "readiness",
                "state": "needs_review",
                "ready": False,
                "gaps": ["freshness_filtering_not_active", "needs_review_items"],
                "gap_count": 2,
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
        assert payload["memory"]["readiness_by_family"] == {
            "freshness": payload["memory"]["readiness_signals"][0],
            "raptor": payload["memory"]["readiness_signals"][1],
        }
        assert payload["memory"]["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 2,
            "ready_families": 0,
            "blocked_families": ["freshness", "raptor"],
            "gaps": [
                "freshness_filtering_not_active",
                "needs_review_items",
                "raptor_index_missing",
            ],
        }
        assert payload["memory"]["summary"]["readiness_gate"] == payload["memory"]["readiness_gate"]
        assert payload["memory"]["freshness_isolation_flags"] == payload["memory"]["summary"]["freshness_isolation_flags"]
        assert payload["memory"]["freshness_isolation_flags"] == payload["memory"]["summary"]["isolation_flags"]
        assert payload["memory"]["raptor_lineage_flags"] == payload["memory"]["summary"]["raptor_lineage_flags"]
        assert payload["memory"]["raptor_lineage_flags"] == payload["memory"]["raptor"]["lineage_flags"]
        assert payload["memory"]["raptor_write_gate"] == payload["memory"]["summary"]["raptor_write_gate"]
        assert payload["memory"]["raptor_write_gate"] == payload["memory"]["raptor"]["write_gate"]
        assert payload["memory"]["raptor"]["writes_supported"] is False


def test_obsidian_context_provider_respects_token_budget(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Large.md"), "w", encoding="utf-8") as f:
            f.write("# Large\n\n" + ("demo retrieval " * 400))

        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)

        payload = retrieve_vault_context("alice", "demo retrieval", 40, "chat")
        snippet_tokens = estimate_tokens([
            {"role": "system", "content": item["text"]}
            for item in payload["snippets"]
        ])

        assert snippet_tokens <= 40


def test_obsidian_context_provider_filters_freshness_when_hybrid_flag_enabled(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: active\n"
                "type: canonical\n"
                "updated: 2026-06-14\n"
                "---\n"
                "# Active\n\nneedle current source.\n"
            )
        with open(os.path.join(tmpdir, "Stale.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: stale\n---\n# Stale\n\nneedle stale source.\n")

        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        monkeypatch.setattr("backend.context_provider.search_semantic", lambda *args, **kwargs: [])

        default_payload = retrieve_vault_context("alice", "needle", 200, "chat")
        assert {source["path"] for source in default_payload["sources"]} == {"Active.md", "Stale.md"}
        assert {snippet["path"] for snippet in default_payload["snippets"]} == {"Active.md", "Stale.md"}
        assert default_payload["memory"]["retrieval_filtering"] is False
        assert default_payload["memory"]["filtering_state"] == "audit_only"
        assert default_payload["memory"]["retrieval_policy"] == {
            "filtering_state": "audit_only",
            "default_retrieval_is_filtered": False,
            "isolated_knowledge_retained_in_audit": True,
            "excluded_relevant_count": 0,
        }
        assert default_payload["memory"]["summary"]["retrieval_policy"] == default_payload["memory"]["retrieval_policy"]
        assert default_payload["memory"]["summary"]["isolated"] == 1
        assert default_payload["memory"]["summary"]["status_counts"]["stale"] == 1

        monkeypatch.setenv("ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED", "true")
        filtered_payload = retrieve_vault_context("alice", "needle", 200, "chat")

        assert [source["path"] for source in filtered_payload["sources"]] == ["Active.md"]
        assert [snippet["path"] for snippet in filtered_payload["snippets"]] == ["Active.md"]
        assert "Stale.md" not in filtered_payload["structured_state"]
        assert filtered_payload["memory"]["retrieval_filtering"] is True
        assert filtered_payload["memory"]["filtering_state"] == "active"
        assert filtered_payload["memory"]["retrieval_policy"] == {
            "filtering_state": "active",
            "default_retrieval_is_filtered": True,
            "isolated_knowledge_retained_in_audit": True,
            "excluded_relevant_count": 1,
        }
        assert filtered_payload["memory"]["summary"]["retrieval_policy"] == filtered_payload["memory"]["retrieval_policy"]
        assert filtered_payload["memory"]["summary"]["total"] == 2
        assert filtered_payload["memory"]["summary"]["default_retrieval"] == 1
        assert filtered_payload["memory"]["summary"]["isolated"] == 1
        assert filtered_payload["memory"]["summary"]["excluded_relevant"] == 1
        assert filtered_payload["memory"]["summary"]["filtering_state"] == "active"
        assert filtered_payload["memory"]["summary"]["readiness_state"] == "blocked"
        assert filtered_payload["memory"]["summary"]["readiness_gaps"] == 2
        assert filtered_payload["memory"]["summary"]["readiness_gap_names"] == [
            "quarantined_items",
            "raptor_index_missing",
        ]
        assert filtered_payload["memory"]["summary"]["freshness_readiness_state"] == "quarantined"
        assert filtered_payload["memory"]["summary"]["freshness_readiness_gaps"] == 1
        assert filtered_payload["memory"]["summary"]["freshness_readiness_gap_names"] == ["quarantined_items"]
        assert filtered_payload["memory"]["summary"]["raptor_readiness_state"] == "not_configured"
        assert filtered_payload["memory"]["summary"]["raptor_readiness_gaps"] == 1
        assert filtered_payload["memory"]["summary"]["raptor_readiness_gap_names"] == ["raptor_index_missing"]
        assert set(filtered_payload["memory"]["readiness_by_family"]) == {"freshness", "raptor"}
        assert filtered_payload["memory"]["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 2,
            "ready_families": 0,
            "blocked_families": ["freshness", "raptor"],
            "gaps": ["quarantined_items", "raptor_index_missing"],
        }
        assert filtered_payload["memory"]["summary"]["readiness_gate"] == filtered_payload["memory"]["readiness_gate"]
        assert filtered_payload["memory"]["freshness_isolation_flags"] == filtered_payload["memory"]["summary"]["freshness_isolation_flags"]
        assert filtered_payload["memory"]["freshness_isolation_flags"] == filtered_payload["memory"]["summary"]["isolation_flags"]
        assert filtered_payload["memory"]["raptor_lineage_flags"] == filtered_payload["memory"]["summary"]["raptor_lineage_flags"]
        assert filtered_payload["memory"]["raptor_lineage_flags"] == filtered_payload["memory"]["raptor"]["lineage_flags"]
        assert filtered_payload["memory"]["raptor_write_gate"] == filtered_payload["memory"]["summary"]["raptor_write_gate"]
        assert filtered_payload["memory"]["raptor_write_gate"] == filtered_payload["memory"]["raptor"]["write_gate"]
        assert filtered_payload["memory"]["summary"]["status_counts"]["stale"] == 1
        assert filtered_payload["memory"]["excluded_relevant"][0]["path"] == "Stale.md"
        assert filtered_payload["memory"]["excluded_relevant"][0]["status"] == "stale"
        assert filtered_payload["memory"]["excluded_relevant"][0]["policy"] == "implementation_status"
        assert filtered_payload["memory"]["excluded_relevant"][0]["source_hash"].startswith("sha256:")
        assert filtered_payload["memory"]["excluded_relevant"][0]["source_mtime"].endswith("Z")
        assert any("Freshness Gate filtered 1" in warning for warning in filtered_payload["warnings"])
        assert any(
            "Freshness Gate filtered 1" in warning
            for warning in filtered_payload["memory"]["summary"]["warnings"]
        )


def test_obsidian_context_provider_keeps_default_context_when_freshness_gate_disabled(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: active\n"
                "type: canonical\n"
                "updated: 2026-06-14\n"
                "---\n"
                "# Active\n\nneedle current source.\n"
            )
        with open(os.path.join(tmpdir, "Stale.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: stale\n---\n# Stale\n\nneedle stale source.\n")

        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        monkeypatch.setattr("backend.context_provider.search_semantic", lambda *args, **kwargs: [])
        monkeypatch.setenv("ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED", "true")
        monkeypatch.setenv("ODYSSEUS_OBSIDIAN_FRESHNESS_GATE_ENABLED", "false")

        payload = retrieve_vault_context("alice", "needle", 200, "chat")

        assert {source["path"] for source in payload["sources"]} == {"Active.md", "Stale.md"}
        assert {snippet["path"] for snippet in payload["snippets"]} == {"Active.md", "Stale.md"}
        assert "Stale.md" in payload["structured_state"]
        assert payload["memory"]["retrieval_filtering"] is False
        assert payload["memory"]["filtering_state"] == "disabled"
        assert payload["memory"]["retrieval_policy"] == {
            "filtering_state": "disabled",
            "default_retrieval_is_filtered": False,
            "isolated_knowledge_retained_in_audit": True,
            "excluded_relevant_count": 0,
        }
        assert payload["memory"]["summary"]["retrieval_policy"] == payload["memory"]["retrieval_policy"]
        assert payload["memory"]["excluded_relevant"] == []
        assert payload["memory"]["summary"]["total"] == 2
        assert payload["memory"]["summary"]["default_retrieval"] == 1
        assert payload["memory"]["summary"]["isolated"] == 1
        assert payload["memory"]["summary"]["excluded_relevant"] == 0
        assert payload["memory"]["summary"]["filtering_state"] == "disabled"
        assert payload["memory"]["flags"]["obsidian_hybrid_retrieval_enabled"] is True
        assert payload["memory"]["flags"]["obsidian_freshness_gate_enabled"] is False
        assert not any("Freshness Gate filtered" in warning for warning in payload["warnings"])


def test_obsidian_context_provider_filters_unresolved_conflicts_when_hybrid_flag_enabled(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: active\n"
                "type: canonical\n"
                "updated: 2026-06-14\n"
                "---\n"
                "# Active\n\nneedle current source.\n"
            )
        with open(os.path.join(tmpdir, "Conflict.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: unresolved_conflict\n"
                "updated: 2026-06-14\n"
                "---\n"
                "# Conflict\n\nneedle conflicting source.\n"
            )

        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        monkeypatch.setattr("backend.context_provider.search_semantic", lambda *args, **kwargs: [])
        monkeypatch.setenv("ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED", "true")

        payload = retrieve_vault_context("alice", "needle", 200, "chat")

        assert [source["path"] for source in payload["sources"]] == ["Active.md"]
        assert [snippet["path"] for snippet in payload["snippets"]] == ["Active.md"]
        assert "Conflict.md" not in payload["structured_state"]
        assert payload["memory"]["retrieval_filtering"] is True
        assert payload["memory"]["filtering_state"] == "active"
        assert payload["memory"]["summary"]["conflicts"] == 1
        assert payload["memory"]["summary"]["excluded_relevant"] == 1
        assert payload["memory"]["summary"]["freshness_readiness_gap_names"] == ["conflict_items"]
        assert payload["memory"]["excluded_relevant"][0]["path"] == "Conflict.md"
        assert payload["memory"]["excluded_relevant"][0]["status"] == "conflict"
        assert payload["memory"]["excluded_relevant"][0]["channel"] == "conflicts"
        assert payload["memory"]["excluded_relevant"][0]["policy"] == "implementation_status"
        assert payload["memory"]["excluded_relevant"][0]["source_hash"].startswith("sha256:")
        assert any("Freshness Gate filtered 1" in warning for warning in payload["warnings"])
        assert any(
            "Freshness Gate filtered 1" in warning
            for warning in payload["memory"]["summary"]["warnings"]
        )


def test_obsidian_context_provider_filters_needs_review_when_hybrid_flag_enabled(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: active\n"
                "type: canonical\n"
                "updated: 2026-06-14\n"
                "---\n"
                "# Active\n\nneedle current source.\n"
            )
        with open(os.path.join(tmpdir, "Review.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "status: needs_review\n"
                "updated: 2026-06-14\n"
                "---\n"
                "# Review\n\nneedle unverified source.\n"
            )

        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        monkeypatch.setattr("backend.context_provider.search_semantic", lambda *args, **kwargs: [])
        monkeypatch.setenv("ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED", "true")

        payload = retrieve_vault_context("alice", "needle", 200, "chat")

        assert [source["path"] for source in payload["sources"]] == ["Active.md"]
        assert [snippet["path"] for snippet in payload["snippets"]] == ["Active.md"]
        assert "Review.md" not in payload["structured_state"]
        assert payload["memory"]["retrieval_filtering"] is True
        assert payload["memory"]["filtering_state"] == "active"
        assert payload["memory"]["retrieval_policy"] == {
            "filtering_state": "active",
            "default_retrieval_is_filtered": True,
            "isolated_knowledge_retained_in_audit": True,
            "excluded_relevant_count": 1,
        }
        assert payload["memory"]["summary"]["needs_review"] == 1
        assert payload["memory"]["summary"]["isolated"] == 1
        assert payload["memory"]["summary"]["excluded_relevant"] == 1
        assert payload["memory"]["summary"]["freshness_readiness_gap_names"] == ["needs_review_items"]
        assert payload["memory"]["freshness_isolation_flags"]["needs_review"] is True
        assert payload["memory"]["needs_review"][0]["path"] == "Review.md"
        assert payload["memory"]["needs_review"][0]["status"] == "needs_review"
        assert payload["memory"]["excluded_relevant"][0]["path"] == "Review.md"
        assert payload["memory"]["excluded_relevant"][0]["status"] == "needs_review"
        assert payload["memory"]["excluded_relevant"][0]["channel"] == "needs_review"
        assert payload["memory"]["excluded_relevant"][0]["policy"] == "implementation_status"
        assert payload["memory"]["excluded_relevant"][0]["source_hash"].startswith("sha256:")
        assert payload["memory"]["excluded_relevant"][0]["source_mtime"].endswith("Z")
        assert any("Freshness Gate filtered 1" in warning for warning in payload["warnings"])
        assert any(
            "Freshness Gate filtered 1" in warning
            for warning in payload["memory"]["summary"]["warnings"]
        )


def test_obsidian_context_provider_respects_locked_vault(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)

        payload = retrieve_vault_context("alice", "demo", 128, "chat")

        assert payload["structured_state"] == {}
        assert payload["snippets"] == []
        assert payload["sources"] == []
    assert "locked" in payload["warnings"][0]
    assert len(payload["cache_key"]) == 64


def test_obsidian_context_provider_parses_frontmatter_lists():
    frontmatter, body = parse_frontmatter("---\ntags:\n- alpha\n- beta\npublished: true\n---\n# Body")

    assert frontmatter == {"tags": ["alpha", "beta"], "published": True}
    assert body == "# Body"
