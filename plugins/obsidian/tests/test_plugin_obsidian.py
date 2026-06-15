import os
import re
import sys
import tempfile
import zipfile
import json
import hashlib
import importlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
from backend import vault_service
from backend import state_doc
from backend.consolidation_job import JOB_ID, REPORT_PATH, run_vault_consolidation
from backend.context_provider import PROVIDER_ID, parse_frontmatter, retrieve_vault_context
from backend.freshness import audit_knowledge, quarantine_list
from backend.hybrid_retrieval import raptor_status
from backend.knowledge_status import normalize_status
from backend.memory_status import memory_status
from backend.memory_tree import analyze_memory_tree, memory_tree_status
from backend.routes import secure_path, get_file_tree
from backend.tool_specs import DESTRUCTIVE_TOOL_NAMES, VAULT_TOOL_BY_NAME, VAULT_TOOL_SPECS, execute_vault_tool
from backend.vault_rules import MAX_MARKDOWN_LINES, RULES_NOTE_PATH
from backend.memory_capture import (
    MemoryCaptureApplyRequest,
    MemoryCaptureRequest,
    apply_memory_capture_plan,
    build_memory_capture_plan,
)
from backend.memory_spark import (
    SparkAnalyzeRequest,
    SparkApplyRequest,
    analyze_memory_health,
    apply_spark_plan,
    build_spark_plan,
)
from backend.project_planning import (
    GameDevConceptDraftRequest,
    NEW_PROJECT_FOLDER_SENTINEL,
    ProjectDescriptionImproveRequest,
    ProjectPlan,
    ProjectPlanRequest,
    ProjectPlanValidationError,
    build_gamedev_concept_draft_with_ai,
    build_project_plan,
    generate_project_plan_content,
    improve_project_description_with_ai,
    normalize_project_kind,
    normalize_project_target_folder,
    template_options,
    validate_gamedev_concept_gate,
    validate_project_plan,
)
from backend.memory_review import (
    MemoryReviewPlan,
    MemoryReviewRequest,
    MemoryReviewValidationError,
    build_memory_review_plan,
    validate_memory_review_plan,
)
from backend.vault_security import (
    VaultSecurityError,
    export_vault,
    import_vault,
    lock_vault,
    protection_status,
    set_password,
    unlock_vault,
    validate_archive_member,
)
from backend.vault_history import list_history
from backend.vault_model import extract_tags
from backend.performance_fixtures import create_large_vault_fixture, profile_graph_build
from routes.api_token_routes import TOKEN_PROFILES, _normalize_scopes
from src.model_context import estimate_tokens
from plugin import (
    get_vault_path_by_owner,
    handle_create_folder,
    handle_delete_folder,
    handle_delete_note,
    handle_list_notes,
    handle_list_tags,
    handle_graph,
    handle_add_relationship,
    handle_delete_relationship,
    handle_history,
    handle_project_plan_apply,
    handle_project_plan_preview,
    handle_project_plan_templates,
    handle_memory_review_apply,
    handle_memory_review_preview,
    handle_list_relationships,
    handle_knowledge_audit,
    handle_memory_status,
    handle_memory_tree_analyze,
    handle_memory_tree_status,
    handle_quarantine_list,
    handle_raptor_status,
    handle_read_note,
    handle_rename_item,
    handle_undo,
    handle_write_note,
    handle_search_notes,
    handle_tree,
    handle_vault_export,
    handle_vault_import,
    handle_vault_lock,
    handle_vault_set_password,
    handle_vault_status,
    handle_vault_unlock,
    PLUGIN,
    setup,
)


def test_vault_watch_signature_changes_when_file_changes():
    with tempfile.TemporaryDirectory() as tmpdir:
        first = obsidian_routes._vault_watch_signature(tmpdir)
        note_path = os.path.join(tmpdir, "note.md")
        content = b"# Hello\n"
        with open(note_path, "wb") as handle:
            handle.write(content)

        second = obsidian_routes._vault_watch_signature(tmpdir)

        assert first != second
        assert [entry[0] for entry in second[1]] == ["note.md"]
        assert second[1][0][2] == len(content)


def test_state_doc_initialize_read_and_append_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        doc = state_doc.initialize_state_doc(
            tmpdir,
            owner="alice",
            session_id="sess-1",
            goal="Ship orchestrator foundation.",
            checklist=["Create state doc", "Delegate worker task"],
            open_questions=["How much context is enough?"],
        )

        assert doc.path == state_doc.STATE_DOC_PATH
        assert doc.frontmatter["status"] == "active"
        assert doc.frontmatter["owner"] == "alice"
        assert doc.frontmatter["session_id"] == "sess-1"
        assert "## Goal" in doc.body
        assert "- [ ] Create state doc" in doc.body

        state_doc.append_step_entry(tmpdir, owner="alice", entry="State doc created.", status="done")
        updated = state_doc.append_delegation_entry(
            tmpdir,
            owner="alice",
            task="Inspect delegate interface.",
            status="done",
            summary="Interface is compact.",
        )

        assert "[done] State doc created." in updated.body
        assert "[done] Inspect delegate interface." in updated.body
        assert "Summary: Interface is compact." in updated.body


def test_state_doc_status_validation_and_update():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_doc.initialize_state_doc(
            tmpdir,
            owner=None,
            session_id=None,
            goal="Test status updates.",
        )
        done = state_doc.update_state_doc_status(tmpdir, owner=None, status="done")
        assert done.frontmatter["status"] == "done"

        with pytest.raises(ValueError):
            state_doc.update_state_doc_status(tmpdir, owner=None, status="paused")


def test_state_doc_append_reflection_updates_frontmatter_and_legacy_body():
    with tempfile.TemporaryDirectory() as tmpdir:
        legacy_content = """---
status: active
owner: alice
session_id: sess-legacy
updated: 2026-01-01T00:00:00+00:00
---
# Active Run

## Goal
Keep going.

## Delegations
"""
        vault_service.write_file(
            tmpdir,
            state_doc.STATE_DOC_PATH,
            legacy_content,
            owner="alice",
            tool="test",
        )

        updated = state_doc.append_reflection_entry(
            tmpdir,
            owner="alice",
            trigger="periodic",
            status="risk",
            assessment="Progress is drifting.",
            risks=["Worker scope is broad."],
            next_step="Delegate a narrower task.",
            note="Refocus.",
            teacher_model="teacher-model",
        )

        assert updated.frontmatter["last_reflection_at"]
        assert "## Reflections" in updated.body
        assert "[risk] periodic" in updated.body
        assert "Teacher: teacher-model" in updated.body
        assert "Risk: Worker scope is broad." in updated.body


@pytest.mark.asyncio
async def test_ai_status_returns_utility_model(monkeypatch):
    calls = []

    def fake_resolve_endpoint(prefix, owner=None):
        calls.append((prefix, owner))
        if prefix == "utility":
            return "http://utility.test/v1/chat/completions", "utility-model", {"Authorization": "Bearer secret"}
        return "http://default.test/v1/chat/completions", "default-model", {}

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

    status = await obsidian_routes.ai_status(SimpleNamespace())

    assert status == {
        "available": True,
        "role": "utility",
        "model": "utility-model",
        "endpoint_url": "http://utility.test/v1/chat/completions",
    }
    assert calls == [("utility", "alice")]


@pytest.mark.asyncio
async def test_ai_status_falls_back_to_default_model(monkeypatch):
    calls = []

    def fake_resolve_endpoint(prefix, owner=None):
        calls.append((prefix, owner))
        if prefix == "utility":
            return None, None, None
        return "http://default.test/v1/chat/completions", "default-model", {}

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

    status = await obsidian_routes.ai_status(SimpleNamespace())

    assert status == {
        "available": True,
        "role": "default",
        "model": "default-model",
        "endpoint_url": "http://default.test/v1/chat/completions",
    }
    assert calls == [("utility", "alice"), ("default", "alice")]


@pytest.mark.asyncio
async def test_ai_status_reports_unavailable_without_writing_settings(monkeypatch):
    def fake_resolve_endpoint(prefix, owner=None):
        return None, None, None

    def fail_write(*args, **kwargs):
        raise AssertionError("ai-status must not write settings")

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.settings.save_settings", fail_write)
    monkeypatch.setattr("routes.prefs_routes._save", fail_write)
    monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

    status = await obsidian_routes.ai_status(SimpleNamespace())

    assert status == {
        "available": False,
        "role": "default",
        "model": "",
        "endpoint_url": "",
    }


def test_secure_path_prevents_traversal():
    """Verify that secure_path blocks relative path traversal attacks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_dir = os.path.abspath(tmpdir)

        safe = secure_path(vault_dir, "notes/my_note.md")
        assert safe.replace("\\", "/") == f"{vault_dir}/notes/my_note.md".replace("\\", "/")

        dangerous_paths = [
            "../traversal.md",
            "notes/../../secret.txt",
            "..\\escape",
        ]

        for path in dangerous_paths:
            with pytest.raises(HTTPException) as exc:
                secure_path(vault_dir, path)
            assert exc.value.status_code == 400
            assert "Path traversal attempt detected" in exc.value.detail


def test_vault_service_tree_search_and_text_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_folder(tmpdir, "Projects", owner="alice", tool="test")
        vault_service.create_file(
            tmpdir,
            "Projects/Plan.md",
            "# Plan\n\nShared service search target.",
            owner="alice",
            tool="test",
        )
        vault_service.create_file(
            tmpdir,
            "Notes.txt",
            "Plain text",
            owner="alice",
            tool="test",
        )

        notes = vault_service.markdown_notes(tmpdir)
        assert "Projects/Plan.md" in notes
        assert RULES_NOTE_PATH in notes
        assert vault_service.read_file(tmpdir, "Projects/Plan.md").startswith("# Plan")

        tree = vault_service.file_tree(tmpdir)
        projects = next(item for item in tree if item["path"] == "Projects")
        assert projects["children"][0]["path"] == "Projects/Plan.md"

        results = vault_service.search_markdown(tmpdir, "target")
        assert len(results) == 1
        assert results[0].path == "Projects/Plan.md"
        assert results[0].matches[0].line == 3

        vault_service.rename_item(tmpdir, "Projects/Plan.md", "Projects/Roadmap.md", owner="alice", tool="test")
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Roadmap.md"))
        vault_service.delete_file(tmpdir, "Projects/Roadmap.md", owner="alice", tool="test")
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Roadmap.md"))


def test_vault_write_enforces_rules_note_and_markdown_line_softcap():
    with tempfile.TemporaryDirectory() as tmpdir:
        content = "\n".join(f"line {i}" for i in range(MAX_MARKDOWN_LINES + 1))

        result = vault_service.write_file(
            tmpdir,
            "Long.md",
            content,
            owner="alice",
            tool="test",
        )

        assert result["success"] is True
        assert result["line_count"] == MAX_MARKDOWN_LINES + 1
        assert result["line_soft_cap"] == MAX_MARKDOWN_LINES
        assert "softcap exceeded" in result["warning"]
        assert os.path.exists(os.path.join(tmpdir, RULES_NOTE_PATH))


def test_vault_batch_dry_run_does_not_create_rules_note_but_write_reports_softcap():
    with tempfile.TemporaryDirectory() as tmpdir:
        content = "\n".join(f"line {i}" for i in range(MAX_MARKDOWN_LINES + 1))

        dry_run = vault_service.batch_operations(
            tmpdir,
            [{"action": "create_file", "path": "Long.md", "content": content}],
            owner="alice",
            tool="test",
            dry_run=True,
        )
        assert dry_run["success"] is True
        assert not os.path.exists(os.path.join(tmpdir, RULES_NOTE_PATH))

        applied = vault_service.batch_operations(
            tmpdir,
            [{"action": "create_file", "path": "Long.md", "content": content}],
            owner="alice",
            tool="test",
        )
        assert applied["success"] is True
        assert applied["results"][0]["line_count"] == MAX_MARKDOWN_LINES + 1
        assert "softcap exceeded" in applied["warnings"][0]
        assert os.path.exists(os.path.join(tmpdir, RULES_NOTE_PATH))


def test_vault_service_locking_blocks_unlocked_resolution(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("OBSIDIAN_VAULT_DIR", os.path.join(tmpdir, "{owner}"))
        vault_dir = vault_service.vault_path_for_owner("alice")
        set_password(vault_dir, "strong password")
        lock_vault(vault_dir)

        with pytest.raises(VaultSecurityError):
            vault_service.unlocked_vault_path_for_owner("alice")


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

        before = set(os.listdir(tmpdir))
        status = memory_status(tmpdir)
        after = set(os.listdir(tmpdir))

        assert before == after
        assert status["read_only"] is True
        assert status["writes_supported"] is False
        assert status["filtering_state"] == "audit_only"
        assert status["flags"]["obsidian_freshness_gate_enabled"] is True
        assert status["flags"]["obsidian_hybrid_retrieval_enabled"] is False
        assert set(status["families"]) == {"somt", "freshness", "quarantine", "raptor"}
        assert set(status["readiness_by_family"]) == {"freshness", "raptor", "somt"}
        assert status["readiness_gate"] == {
            "required": True,
            "satisfied": False,
            "state": "blocked",
            "families": 3,
            "ready_families": 0,
            "blocked_families": ["freshness", "raptor", "somt"],
            "gaps": [
                "somt_issues_present",
                "freshness_filtering_not_active",
                "needs_review_items",
                "raptor_index_missing",
            ],
        }
        assert status["summary"]["families"] == 3
        assert status["summary"]["status_families"] == 4
        assert status["summary"]["readiness_families"] == 3
        assert status["summary"]["ready_families"] == 0
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
        }
        assert status["summary"]["retrieval_policy"] == status["retrieval_policy"]
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
            "writes_supported": False,
            "state": "blocked",
            "gaps": [
                "raptor_feature_flag_disabled",
                "source_hash_lineage_verification_required",
                "dirty_summary_behavior_required",
                "raptor_rebuild_write_disabled_in_mvp",
            ],
        }
        assert status["summary"]["raptor_write_gate"] == status["raptor_write_gate"]
        assert status["summary"]["writes_supported"] is False


async def test_memory_status_route_is_read_only_unified_dashboard(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Active.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\ntype: canonical\nupdated: 2026-06-14\n---\n# Active\n")
        with open(os.path.join(tmpdir, "Conflict.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: conflict\n---\n# Conflict\n")

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        before = set(os.listdir(tmpdir))
        status = await obsidian_routes.memory_status_route(SimpleNamespace())
        after = set(os.listdir(tmpdir))

        assert before == after
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
        assert set(status["families"]) == {"somt", "freshness", "quarantine", "raptor"}
        assert set(status["readiness_by_family"]) == {"freshness", "raptor", "somt"}
        assert status["summary"]["freshness_isolation_flags"] == status["freshness_isolation_flags"]
        assert status["summary"]["raptor_lineage_flags"] == status["raptor_lineage_flags"]
        assert status["summary"]["raptor_write_gate"] == status["raptor_write_gate"]


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


def test_raptor_status_is_read_only_and_disabled_by_default():
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
            "writes_supported": False,
            "state": "blocked",
            "gaps": [
                "raptor_feature_flag_disabled",
                "source_hash_lineage_verification_required",
                "dirty_summary_behavior_required",
                "raptor_rebuild_write_disabled_in_mvp",
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
            "writes_supported": False,
            "state": "blocked",
            "gaps": [
                "source_hash_lineage_verification_required",
                "dirty_summary_behavior_required",
                "raptor_rebuild_write_disabled_in_mvp",
            ],
        }
        assert status["summary"]["write_gate"] == status["write_gate"]


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


def test_obsidian_context_provider_parses_frontmatter_lists():
    frontmatter, body = parse_frontmatter("---\ntags:\n- alpha\n- beta\npublished: true\n---\n# Body")

    assert frontmatter == {"tags": ["alpha", "beta"], "published": True}
    assert body == "# Body"


def test_vault_tool_specs_cover_dispatcher_and_classify_destructive_tools():
    names = [spec.name for spec in VAULT_TOOL_SPECS]
    memory_status_spec = VAULT_TOOL_BY_NAME["obsidian_memory_status"]

    assert len(names) == len(set(names))
    assert set(names) == set(VAULT_TOOL_BY_NAME)
    assert {"obsidian_write_note", "vault_batch", "obsidian_delete_note", "obsidian_undo"} <= DESTRUCTIVE_TOOL_NAMES
    assert {
        "obsidian_memory_tree_status",
        "obsidian_memory_status",
        "obsidian_memory_tree_analyze",
        "obsidian_knowledge_audit",
        "obsidian_quarantine_list",
        "obsidian_raptor_status",
    } <= set(names)
    assert "obsidian_raptor_status" not in DESTRUCTIVE_TOOL_NAMES
    assert "readiness_gate" in memory_status_spec.description
    assert "retrieval_policy" in memory_status_spec.description
    assert "freshness_isolation_flags" in memory_status_spec.description
    assert "raptor_lineage_flags" in memory_status_spec.description
    assert "raptor_write_gate" in memory_status_spec.description
    assert all("owner" not in spec.input_schema.get("properties", {}) for spec in VAULT_TOOL_SPECS)


def test_vault_tool_spec_executes_shared_service(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\nbody")

        result = execute_vault_tool("obsidian_read_note", tmpdir, {"path": "Demo.md"}, "alice", {"source": "test"})

        assert result == "# Demo\n\nbody"


def test_vault_tool_spec_ignores_owner_argument():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\nbody")

        result = execute_vault_tool(
            "obsidian_vault_stats",
            tmpdir,
            {"owner": "mallory"},
            "alice",
            {"source": "test"},
        )

        assert result["owner"] == "alice"


def test_vault_mcp_resolves_owner_from_trusted_environment(monkeypatch):
    vault_server = importlib.import_module("mcp_servers.vault_server")
    monkeypatch.setenv("ODYSSEUS_OWNER", "alice")
    monkeypatch.setenv("ODYSSEUS_API_TOKEN", "ody_secret")
    monkeypatch.setenv("ODYSSEUS_FALLBACK_OWNER", "mallory")

    assert vault_server._resolve_owner() == "alice"


def test_vault_mcp_rejects_token_context_without_owner(monkeypatch):
    vault_server = importlib.import_module("mcp_servers.vault_server")
    monkeypatch.delenv("ODYSSEUS_OWNER", raising=False)
    monkeypatch.delenv("ODYSSEUS_FALLBACK_OWNER", raising=False)
    monkeypatch.setenv("ODYSSEUS_API_TOKEN_ID", "tok_123")

    with pytest.raises(PermissionError):
        vault_server._resolve_owner()


def test_vault_mcp_default_owner_is_local_legacy_only(monkeypatch):
    vault_server = importlib.import_module("mcp_servers.vault_server")
    monkeypatch.delenv("ODYSSEUS_OWNER", raising=False)
    monkeypatch.delenv("ODYSSEUS_FALLBACK_OWNER", raising=False)
    monkeypatch.delenv("ODYSSEUS_API_TOKEN", raising=False)
    monkeypatch.delenv("ODYSSEUS_API_TOKEN_ID", raising=False)
    monkeypatch.delenv("ODYSSEUS_API_TOKEN_PREFIX", raising=False)

    assert vault_server._resolve_owner() == "default"


def test_current_owner_rejects_ownerless_api_token():
    request = SimpleNamespace(state=SimpleNamespace(api_token=True, api_token_owner=None))

    with pytest.raises(HTTPException) as exc:
        obsidian_routes.current_owner(request)

    assert exc.value.status_code == 403
    assert exc.value.detail == "API token has no owner"


def test_memory_capture_preview_normalizes_without_writing():
    with tempfile.TemporaryDirectory() as tmpdir:
        req = MemoryCaptureRequest(
            content="Entscheidung: Externe KI nutzt Token -> User -> genau eine Vault.",
            source="agent",
            tags=["ai memory", "#obsidian"],
        )

        plan = build_memory_capture_plan(tmpdir, req)

        assert plan.kind == "decision"
        assert plan.action == "update_canonical"
        assert plan.target_path == "AI Memory/02 Entscheidungen.md"
        assert "#type/decision" in plan.tags
        assert not os.path.exists(os.path.join(tmpdir, "AI Memory"))


def test_memory_capture_apply_writes_confirmed_plan():
    with tempfile.TemporaryDirectory() as tmpdir:
        req = MemoryCaptureRequest(
            content="Regel: MCP-Clients duerfen keinen owner aus Tool-Argumenten setzen.",
            kind="rule",
            source="agent",
            confidence="high",
        )
        plan = build_memory_capture_plan(tmpdir, req)

        result = apply_memory_capture_plan(tmpdir, plan, owner="alice", actor={"source": "test"})

        assert result["success"] is True
        target = os.path.join(tmpdir, "AI Memory", "02 Entscheidungen.md")
        with open(target, "r", encoding="utf-8") as handle:
            content = handle.read()
        assert "MCP-Clients duerfen keinen owner" in content
        assert "type: canonical" in content


def test_memory_capture_routes_medium_duplicate_to_review_queue():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(
            tmpdir,
            "Existing.md",
            "# Token Vault Rule\n\nToken User Vault Zugriff ist sicherheitsrelevant.",
            owner="alice",
            tool="test",
        )

        plan = build_memory_capture_plan(
            tmpdir,
            MemoryCaptureRequest(
                title="Token Vault Rule",
                content="Token User Vault Zugriff ist sicherheitsrelevant.",
                kind="rule",
                source="agent",
            ),
        )

        assert plan.action in {"discard_duplicate", "review_queue"}
        assert plan.duplicate_candidates


def test_spark_analyze_and_plan_find_memory_health_actions():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(tmpdir, "Loose.md", "# Loose\n\nNo links yet. #memory", owner="alice", tool="test")

        health = analyze_memory_health(tmpdir, SparkAnalyzeRequest(limit=100))
        plan = build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=100))

        assert health.total_notes == 1
        assert "Loose.md" in health.orphan_notes
        assert any(action.type == "update_canonical" for action in plan.actions)
        assert all(action.risk in {"low", "medium", "high"} for action in plan.actions)


def test_spark_apply_skips_high_risk_and_applies_selected_safe_actions():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(tmpdir, "Loose.md", "# Loose\n\nNo links yet. #memory", owner="alice", tool="test")
        plan = build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=100))
        safe = next(action for action in plan.actions if action.operations and action.risk != "high")
        high = next((action for action in plan.actions if action.risk == "high"), None)
        selected = [safe.id] + ([high.id] if high else [])

        result = apply_spark_plan(
            tmpdir,
            SparkApplyRequest(plan=plan, confirm=True, selected_action_ids=selected),
            owner="alice",
            actor={"source": "test"},
        )

        assert result["success"] is True
        assert safe.id in result["applied_actions"]
        assert os.path.exists(os.path.join(tmpdir, safe.target_path.replace("/", os.sep)))


def test_snapshot_throttling_keeps_rapid_updates_from_churning_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(vault_service, "SNAPSHOT_MIN_INTERVAL_SECONDS", 300)
        vault_service.create_file(tmpdir, "Demo.md", "v1", owner="alice", tool="test")

        vault_service.update_file(tmpdir, "Demo.md", "v2", owner="alice", tool="test")
        vault_service.update_file(tmpdir, "Demo.md", "v3", owner="alice", tool="test")

        snap_root = os.path.join(tmpdir, vault_service.SNAPSHOTS_DIR)
        snapshots = []
        for root, _dirs, files in os.walk(snap_root):
            snapshots.extend([name for name in files if name.endswith(".md")])
        assert len(snapshots) == 1


def test_batch_operations_records_batch_id_and_actor_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(tmpdir, "Demo.md", "v1", owner="alice", tool="test")

        result = vault_service.batch_operations(
            tmpdir,
            [{"action": "update_file", "path": "Demo.md", "content": "v2"}],
            owner="alice",
            tool="test_batch",
            actor={"source": "api", "token_id": "tok1", "token_prefix": "ody_1234"},
        )
        history = list_history(tmpdir, limit=5)

        assert result["success"] is True
        assert result["batch_id"]
        assert history[0]["batch_id"] == result["batch_id"]
        assert history[0]["actor"]["token_id"] == "tok1"


def test_obsidian_token_profiles_normalize_dependencies():
    assert TOKEN_PROFILES["obsidian_readonly"] == ["vault:read"]
    assert _normalize_scopes(profile="obsidian_writer") == ["vault:read", "vault:write"]
    assert _normalize_scopes(profile="obsidian_maintenance") == ["vault:read", "vault:write", "vault:delete"]


def test_obsidian_consolidation_job_writes_non_destructive_report(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "Archive"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\n---\n# Demo\n\n[[Projects/Hub]]")
        with open(os.path.join(tmpdir, "Archive", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\nNo frontmatter yet.")
        with open(os.path.join(tmpdir, "Projects", "Hub.md"), "w", encoding="utf-8") as f:
            f.write("# Hub\n\n[[Projects/Demo]]")
        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)

        result = run_vault_consolidation(
            owner="alice",
            trigger="chat.completed",
            context={"session_id": "s1", "model": "demo", "response": "not persisted"},
        )

        report_file = os.path.join(tmpdir, REPORT_PATH)
        assert result["skipped"] is False
        assert os.path.exists(report_file)
        with open(report_file, "r", encoding="utf-8") as f:
            report = json.load(f)
        assert report["safety"] == {
            "destructive_changes": False,
            "note_files_modified": False,
            "report_only": True,
        }
        assert report["context"] == {"session_id": "s1", "model": "demo"}
        assert report["duplicate_title_candidates"][0]["title"] == "demo"
        assert any(item["path"] == "Archive/Demo.md" for item in report["frontmatter_suggestions"])


def test_obsidian_consolidation_job_respects_locked_vault(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)

        result = run_vault_consolidation(owner="alice")

        assert result == {"skipped": True, "reason": "vault_locked"}
        assert not os.path.exists(os.path.join(tmpdir, REPORT_PATH))


def test_archive_member_validation_blocks_escape_paths():
    dangerous_paths = [
        "../escape.md",
        "notes/../../escape.md",
        "/tmp/escape.md",
        "C:\\temp\\escape.md",
        ".odysseus-vault.json",
    ]

    for path in dangerous_paths:
        with pytest.raises(VaultSecurityError):
            validate_archive_member(path)

    assert validate_archive_member("Projects/Plan.md") == "Projects/Plan.md"


def test_get_vault_path_by_owner(monkeypatch):
    """Verify vault isolation by username."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("src.constants.DATA_DIR", tmpdir)

        vault_user1 = get_vault_path_by_owner("user1")
        vault_user2 = get_vault_path_by_owner("user2")
        vault_default = get_vault_path_by_owner(None)

        assert "user1" in vault_user1
        assert "user2" in vault_user2
        assert "default" in vault_default

        assert os.path.isdir(vault_user1)
        assert os.path.isdir(vault_user2)
        assert os.path.isdir(vault_default)


@pytest.mark.asyncio
async def test_tool_handlers_crud(monkeypatch):
    """Test tool handlers for listing, reading, writing, and searching notes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        res = await handle_list_notes("")
        assert res["exit_code"] == 0
        assert "No notes found" in res["output"]

        write_content = '{"path": "Project.md", "content": "# Odysseus Obsidian Integration\\n\\nThis is a test note."}'
        res = await handle_write_note(write_content)
        assert res["exit_code"] == 0
        assert "Successfully wrote note" in res["output"]
        assert os.path.exists(os.path.join(tmpdir, "Project.md"))

        read_content = '{"path": "Project.md"}'
        res = await handle_read_note(read_content)
        assert res["exit_code"] == 0
        assert "Odysseus Obsidian Integration" in res["output"]

        search_query = '{"query": "Integration"}'
        res = await handle_search_notes(search_query)
        assert res["exit_code"] == 0
        assert "Project.md" in res["output"]
        assert "Line 1:" in res["output"]


@pytest.mark.asyncio
async def test_ai_write_note_surfaces_vault_rules_softcap_warning(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        content = "\n".join(f"line {i}" for i in range(MAX_MARKDOWN_LINES + 1))

        res = await handle_write_note(json.dumps({"path": "Long.md", "content": content}))

        assert res["exit_code"] == 0
        assert "Warning:" in res["output"]
        assert "softcap exceeded" in res["output"]


@pytest.mark.asyncio
async def test_ai_tools_cover_folder_tree_rename_and_delete(monkeypatch):
    """AI handlers can perform the same core vault actions as the panel."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        res = await handle_create_folder('{"path": "Projects"}')
        assert res["exit_code"] == 0
        assert os.path.isdir(os.path.join(tmpdir, "Projects"))

        res = await handle_write_note('{"path": "Projects/Plan.md", "content": "# Plan"}')
        assert res["exit_code"] == 0

        res = await handle_tree("")
        assert res["exit_code"] == 0
        assert "Projects/Plan.md" in res["output"]

        res = await handle_rename_item('{"old_path": "Projects/Plan.md", "new_path": "Projects/Roadmap.md"}')
        assert res["exit_code"] == 0
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Roadmap.md"))

        res = await handle_delete_note('{"path": "Projects/Roadmap.md"}')
        assert res["exit_code"] == 1
        assert "Confirmation required" in res["error"]

        res = await handle_delete_note('{"path": "Projects/Roadmap.md", "confirm": true}')
        assert res["exit_code"] == 0
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Roadmap.md"))

        res = await handle_delete_folder('{"path": "Projects", "confirm": true}')
        assert res["exit_code"] == 0
        assert not os.path.exists(os.path.join(tmpdir, "Projects"))


@pytest.mark.asyncio
async def test_ai_delete_folder_refuses_non_empty_folder(monkeypatch):
    """Folder deletion is intentionally conservative for AI-triggered actions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Plan.md"), "w", encoding="utf-8") as f:
            f.write("# Plan")

        res = await handle_delete_folder('{"path": "Projects", "confirm": true}')

        assert res["exit_code"] == 1
        assert os.path.isdir(os.path.join(tmpdir, "Projects"))


@pytest.mark.asyncio
async def test_ai_rename_refuses_folder_into_itself(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        os.makedirs(os.path.join(tmpdir, "Projects", "Nested"), exist_ok=True)

        res = await handle_rename_item('{"old_path": "Projects", "new_path": "Projects/Nested/Projects"}')

        assert res["exit_code"] == 1
        assert "itself" in res["error"]
        assert os.path.isdir(os.path.join(tmpdir, "Projects", "Nested"))


@pytest.mark.asyncio
async def test_ai_write_requires_confirmation_before_overwrite(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        res = await handle_write_note('{"path": "Project.md", "content": "# One"}')
        assert res["exit_code"] == 0

        res = await handle_write_note('{"path": "Project.md", "content": "# Two"}')
        assert res["exit_code"] == 1
        assert "Confirmation required" in res["error"]

        res = await handle_write_note('{"path": "Project.md", "content": "# Two", "confirm": true}')
        assert res["exit_code"] == 0
        with open(os.path.join(tmpdir, "Project.md"), "r", encoding="utf-8") as f:
            assert f.read() == "# Two"


@pytest.mark.asyncio
async def test_ai_tags_and_graph_include_implicit_tags_links_and_mentions(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Roadmap.md"), "w", encoding="utf-8") as f:
            f.write("# Roadmap\n\n#planning links to [[Architecture]] and mentions Architecture.")
        with open(os.path.join(tmpdir, "Architecture.md"), "w", encoding="utf-8") as f:
            f.write("# Architecture\n\n#planning")

        tags_res = await handle_list_tags("")
        assert tags_res["exit_code"] == 0
        tags = json.loads(tags_res["output"])
        assert {tag["name"] for tag in tags} >= {"roadmap", "architecture", "planning"}
        assert next(tag for tag in tags if tag["name"] == "planning")["files"] == [
            "Architecture.md",
            "Roadmap.md",
        ]

        graph_res = await handle_graph("")
        assert graph_res["exit_code"] == 0
        graph = json.loads(graph_res["output"])["graph"]
        edge_types = {edge["type"] for edge in graph["edges"]}
        assert "wiki_link" in edge_types
        assert "shared_tag" in edge_types
        assert "filename_mention" in edge_types
        assert any(edge["target"] == "Architecture.md" for edge in graph["edges"])


def test_tag_index_ignores_headings_code_inline_code_and_urls():
    content = "\n".join([
        "# Heading stays a heading",
        "Text with #real-tag and #project/demo.",
        "## Subheading also stays a heading",
        "Inline `#code-tag` is ignored.",
        "URL https://example.test/#url-tag is ignored.",
        "```",
        "# fenced-code-tag",
        "```",
    ])

    tags = extract_tags(content, "Notes/Demo.md")

    assert set(tags["explicit_tags"]) == {"project/demo", "real-tag"}
    assert "heading" not in tags["explicit_tags"]
    assert "subheading" not in tags["explicit_tags"]
    assert "code-tag" not in tags["explicit_tags"]
    assert "url-tag" not in tags["explicit_tags"]


def test_project_plan_preview_validates_schema_paths_tags_and_conflicts():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects", "Demo"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"), "w", encoding="utf-8") as f:
            f.write("# Existing")

        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="A small planning target.",
            custom_focus="Emphasize offline-first decisions.",
            kind="software",
        ))

        assert plan.project.slug == "demo-app"
        assert "Nutzerdefinierte Schwerpunkte" in plan.project.summary
        assert "offline-first" in plan.files[0].content
        assert plan.conflicts == [{"path": "Projects/Demo/00 Projektuebersicht.md", "reason": "file_exists"}]
        first = plan.files[0]
        assert first.path == "Projects/Demo/00 Projektuebersicht.md"
        assert "#project/demo-app" in first.tags
        assert "#type/project" in first.tags
        assert "#status/draft" in first.tags
        assert first.links == [
            "[[Projects/Demo/01 Anforderungen]]",
            "[[Projects/Demo/02 Architektur]]",
            "[[Projects/Demo/03 Implementierungsplan]]",
            "[[Projects/Demo/04 Testplan]]",
            "[[Projects/Demo/05 Risiken und offene Fragen]]",
            "[[Projects/Demo/APIs und Schnittstellen]]",
            "[[Projects/Demo/Datenmodell]]",
            "[[Projects/Demo/Entscheidungen/ADR-0001-Grundarchitektur]]",
        ]
        assert all(file.links == ["[[Projects/Demo/00 Projektuebersicht]]"] for file in plan.files[1:])
        assert plan.relationships == []

        plan_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        bad = ProjectPlan(**plan_payload)
        bad.files[0].path = "../escape.md"
        with pytest.raises(ProjectPlanValidationError):
            validate_project_plan(tmpdir, bad)

        bad = ProjectPlan(**plan_payload)
        bad.files[0].tags = ["#project/demo-app", "#status/draft"]
        with pytest.raises(ProjectPlanValidationError):
            validate_project_plan(tmpdir, bad)


def test_project_plan_templates_drive_distinct_project_kinds_and_aliases():
    options = template_options()
    kind_labels = {item["key"]: item["label"] for item in options["kinds"]}
    assert kind_labels["sec_ops"] == "Sec-Ops"
    assert kind_labels["teaching"] == "Teaching"
    assert kind_labels["game_dev"] == "GameDev"
    assert "ops" not in kind_labels
    assert normalize_project_kind("ops") == "sec_ops"
    assert normalize_project_kind("Unterricht") == "teaching"
    assert normalize_project_kind("Education") == "teaching"
    assert normalize_project_kind("GameDev") == "game_dev"
    assert normalize_project_kind("game-dev") == "game_dev"

    with tempfile.TemporaryDirectory() as tmpdir:
        plans = {
            kind: build_project_plan(tmpdir, ProjectPlanRequest(
                target_folder=f"Projects/{kind}",
                title=f"{kind} Demo",
                description="Template coverage.",
                kind=kind,
            ))
            for kind in ["software", "research", "writing", "sec_ops", "generic", "teaching", "game_dev"]
        }

        software_paths = {file.path for file in plans["software"].files}
        assert "Projects/software/APIs und Schnittstellen.md" in software_paths
        assert "Projects/software/Datenmodell.md" in software_paths

        assert {file.path for file in plans["research"].files} != software_paths
        assert any(file.path.endswith("01 Forschungsfrage.md") for file in plans["research"].files)
        assert any(file.path.endswith("02 Gliederung.md") for file in plans["writing"].files)
        assert any(file.path.endswith("04 Incident Response.md") for file in plans["sec_ops"].files)
        assert any(file.path.endswith("02 Arbeitspakete.md") for file in plans["generic"].files)
        assert any(file.path.endswith("03 Engine and Architecture.md") for file in plans["game_dev"].files)
        assert any(file.path.endswith("09 Risks and Open Questions.md") for file in plans["game_dev"].files)

        teaching_paths = [file.path for file in plans["teaching"].files]
        assert len(teaching_paths) == 9
        assert teaching_paths == [
            "Projects/teaching/00 Unterrichtsuebersicht.md",
            "Projects/teaching/01 Rahmenkriterien.md",
            "Projects/teaching/02 Kompetenzen und Bildungsplan.md",
            "Projects/teaching/03 Wissenschaftliche Recherche.md",
            "Projects/teaching/04 Didaktische Reduktion.md",
            "Projects/teaching/05 Verlaufsplan.md",
            "Projects/teaching/06 Materialien.md",
            "Projects/teaching/07 Loesungen und Erwartungshorizont.md",
            "Projects/teaching/08 Kritische Review.md",
        ]

        game_paths = [file.path for file in plans["game_dev"].files]
        assert len(game_paths) == 10
        assert game_paths == [
            "Projects/game_dev/00 Game Overview.md",
            "Projects/game_dev/01 Scope and MVP.md",
            "Projects/game_dev/02 Core Gameplay Loop.md",
            "Projects/game_dev/03 Engine and Architecture.md",
            "Projects/game_dev/04 Gameplay Systems.md",
            "Projects/game_dev/05 Content and Level Design.md",
            "Projects/game_dev/06 Art Audio UI Pipeline.md",
            "Projects/game_dev/07 Production Plan.md",
            "Projects/game_dev/08 Testing and Balancing.md",
            "Projects/game_dev/09 Risks and Open Questions.md",
        ]
        assert "[[Projects/game_dev/00 Game Overview]]" not in plans["game_dev"].files[0].links
        assert all(file.links == ["[[Projects/game_dev/00 Game Overview]]"] for file in plans["game_dev"].files[1:])


def test_project_plan_new_folder_sentinel_is_resolved_without_preview_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        assert normalize_project_target_folder(f"{NEW_PROJECT_FOLDER_SENTINEL}::Projects", "demo-app") == "Projects/demo-app"
        assert normalize_project_target_folder(f"{NEW_PROJECT_FOLDER_SENTINEL}::", "demo-app") == "demo-app"

        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder=f"{NEW_PROJECT_FOLDER_SENTINEL}::",
            title="Demo App",
            description="Preview only.",
            kind="generic",
        ))

        assert plan.target_folder == "demo-app"
        assert all(file.path.startswith("demo-app/") for file in plan.files)
        assert not os.path.exists(os.path.join(tmpdir, "demo-app"))


@pytest.mark.asyncio
async def test_project_plan_ai_improves_description():
    async def fake_llm(messages, **kwargs):
        assert "kein Denkprotokoll" in messages[0]["content"]
        assert "keine Meta-Kommentare" in messages[0]["content"]
        assert "Der Nutzer will" in messages[0]["content"]
        assert "Sprache der eigentlichen Projekteingabe" in messages[0]["content"]
        assert "Korrigiere offensichtliche Tippfehler still" in messages[0]["content"]
        assert "Verbessere diesen Projektkontext" in messages[-1]["content"]
        assert "Nutzerdefinierte Schwerpunkte" in messages[-1]["content"]
        assert "Beginne direkt mit den lokalisierten Entsprechungen" in messages[-1]["content"]
        assert "Geplantes Ergebnis" in messages[-1]["content"]
        assert "Schreibe keine Saetze ueber die Eingabe" in messages[-1]["content"]
        assert "Nenne keine internen Ueberlegungen" in messages[-1]["content"]
        assert "Differenzierung" in messages[-1]["content"]
        return "Ziel: klarer Unterrichtsplan.\nOffene Fragen: Bundesland klaeren."

    improved = await improve_project_description_with_ai(
        ProjectDescriptionImproveRequest(
            title="Hasen",
            description="mach unterricht",
            custom_focus="Differenzierung und Zeitrealismus beachten.",
            kind="teaching",
        ),
        llm_call=fake_llm,
    )

    assert "klarer Unterrichtsplan" in improved
    assert "Offene Fragen" in improved


@pytest.mark.asyncio
async def test_project_plan_ai_strips_prompt_improvement_metatext():
    async def fake_llm(messages, **kwargs):
        return (
            "Wir haben die Eingabe: Projektart Research, Projekttitel Beer.\n"
            "Der Nutzer will eine verbesserte Projektbeschreibung.\n\n"
            "Project type: Research\n"
            "Project title: Beer\n"
            "Goal: Research the history of beer with verified sources.\n"
            "Open questions: Define geography and depth."
        )

    improved = await improve_project_description_with_ai(
        ProjectDescriptionImproveRequest(
            title="Beer",
            description="Research the history of beer.",
            custom_focus="make sure every link is true to its proposed content. No fake news!",
            kind="research",
        ),
        llm_call=fake_llm,
    )

    assert improved.startswith("Project type: Research")
    assert "Wir haben" not in improved
    assert "Der Nutzer will" not in improved
    assert "Research the history of beer" in improved


@pytest.mark.asyncio
async def test_project_plan_gamedev_draft_and_approval_gate():
    async def fake_llm(messages, **kwargs):
        assert "editable GameDev concept draft" in messages[0]["content"]
        assert "worker/unit complexity" in messages[-1]["content"]
        assert "pathfinding risk first" in messages[-1]["content"]
        return (
            "# GameDev Concept Draft\n\n"
            "## MVP Scope\nA tiny 2D strategy prototype.\n\n"
            "## Engine and Tech Assumptions\nGodot 2D.\n\n"
            "## Production Risks\nWorker units need pathfinding and task queues.\n\n"
            "## Open Questions\nMap size and win condition."
        )

    draft = await build_gamedev_concept_draft_with_ai(
        GameDevConceptDraftRequest(
            title="Worker Fields",
            description="2D strategy game in Godot with workers.",
            custom_focus="pathfinding risk first",
            kind="GameDev",
        ),
        llm_call=fake_llm,
    )
    assert "Worker units" in draft["draft"]
    assert draft["warnings"] == []

    blocked = ProjectPlanRequest(
        target_folder="Games/Worker Fields",
        title="Worker Fields",
        description="2D strategy game in Godot with workers.",
        kind="GameDev",
        generate_content=True,
    )
    with pytest.raises(ProjectPlanValidationError):
        validate_gamedev_concept_gate(blocked)

    approved = ProjectPlanRequest(
        target_folder="Games/Worker Fields",
        title="Worker Fields",
        description="Original prompt.",
        kind="GameDev",
        generate_content=True,
        concept_approved=True,
        approved_concept=draft["draft"],
    )
    validate_gamedev_concept_gate(approved)
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, approved)
        assert plan.project.kind == "game_dev"
        assert "Worker units need pathfinding" in plan.project.summary
        assert "Worker units need pathfinding" in plan.files[0].content


@pytest.mark.asyncio
async def test_project_plan_ai_generation_is_sequential_context_chain():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a useful project folder.",
            custom_focus="Prioritize API boundaries.",
            kind="generic",
        ))
        calls = []

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            calls.append({"system": system, "user": user})
            if "einzelne Markdown-Datei" in system:
                assert "Prioritize API boundaries" in user
                match = re.search(r"Zieldatei \d+ von \d+: (.+)", user)
                path = match.group(1)
                return f"# Generated {path}\n\nContent built from sequential context."
            if "Kontextzusammenfassung" in system:
                match = re.search(r"Neu generierte Datei: (.+)", user)
                path = match.group(1)
                previous = user.split("Bisherige Kontextzusammenfassung:\n", 1)[1].split("\n\nNeu generierte Datei:", 1)[0]
                return f"{previous}\nCTX after {path}"
            raise AssertionError("unexpected prompt")

        enriched = await generate_project_plan_content(plan, llm_call=fake_llm)

        generation_prompts = [call["user"] for call in calls if "Zieldatei" in call["user"]]
        assert len(generation_prompts) == len(enriched.files)
        assert "Bisher generierte Dateien: noch keine" in generation_prompts[0]
        assert "CTX after Projects/Demo/00 Projektuebersicht.md" in generation_prompts[1]
        assert "CTX after Projects/Demo/03 Entscheidungen.md" in generation_prompts[-1]
        assert "Generated Projects/Demo/00 Projektuebersicht.md" in enriched.files[0].content


@pytest.mark.asyncio
async def test_project_plan_ai_generation_emits_progress_in_file_order():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a useful project folder.",
            kind="generic",
        ))
        events = []

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                path = re.search(r"Zieldatei \d+ von \d+: (.+)", user).group(1)
                return f"# Generated {path}\n\nSequential content."
            if "Kontextzusammenfassung" in system:
                path = re.search(r"Neu generierte Datei: (.+)", user).group(1)
                return f"CTX after {path}"
            raise AssertionError("unexpected prompt")

        async def progress(event):
            events.append(dict(event))

        await generate_project_plan_content(plan, llm_call=fake_llm, progress_callback=progress)

        started = [event for event in events if event["type"] == "file_started"]
        done = [event for event in events if event["type"] == "file_done"]
        assert [event["index"] for event in started] == list(range(len(plan.files)))
        assert [event["index"] for event in done] == list(range(len(plan.files)))
        assert done[0]["file"]["path"] == "Projects/Demo/00 Projektuebersicht.md"
        assert "Sequential content" in done[0]["file"]["content"]


@pytest.mark.asyncio
async def test_project_plan_ai_generation_retries_and_keeps_partial_preview():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a useful project folder.",
            kind="generic",
        ))
        attempts = {}

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                match = re.search(r"Zieldatei \d+ von \d+: (.+)", user)
                path = match.group(1)
                attempts[path] = attempts.get(path, 0) + 1
                if path.endswith("00 Projektuebersicht.md"):
                    raise RuntimeError("temporary model error")
                assert "Generierungswarnung fuer Projects/Demo/00 Projektuebersicht.md" in user
                return f"# Generated {path}\n\nContinued after warning."
            if "Kontextzusammenfassung" in system:
                match = re.search(r"Neu generierte Datei: (.+)", user)
                return f"CTX after {match.group(1)}"
            raise AssertionError("unexpected prompt")

        enriched = await generate_project_plan_content(plan, llm_call=fake_llm, max_attempts=3)

        assert attempts["Projects/Demo/00 Projektuebersicht.md"] == 3
        assert any("AI generation failed for Projects/Demo/00 Projektuebersicht.md after 3 attempts" in warning for warning in enriched.warnings)
        assert "Klaeren und ausarbeiten" in enriched.files[0].content
        assert "Generated Projects/Demo/01 Ziele.md" in enriched.files[1].content


@pytest.mark.asyncio
async def test_project_plan_preview_stream_emits_sse_events(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                path = re.search(r"Zieldatei \d+ von \d+: (.+)", user).group(1)
                return f"# Generated {path}\n\nStreamed content."
            if "Kontextzusammenfassung" in system:
                path = re.search(r"Neu generierte Datei: (.+)", user).group(1)
                return f"CTX after {path}"
            raise AssertionError("unexpected prompt")

        monkeypatch.setattr(obsidian_routes, "project_planning_llm_call", lambda owner: fake_llm)
        response = await obsidian_routes.project_plan_preview_stream(ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a streamed project folder.",
            kind="generic",
            generate_content=True,
        ), SimpleNamespace())

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        stream = "".join(chunks)

        assert "event: plan_started" in stream
        assert "event: file_started" in stream
        assert "event: file_done" in stream
        assert "event: plan_done" in stream
        assert stream.index("event: plan_started") < stream.index("event: file_started")
        assert "Streamed content" in stream


@pytest.mark.asyncio
async def test_project_plan_sessions_are_recoverable_and_non_destructive(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)

        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    target_folder=f"{NEW_PROJECT_FOLDER_SENTINEL}::Projects",
                    title="Recoverable Demo",
                    description="Create a recoverable planning session.",
                    kind="software",
                    generate_content=True,
                )
            ),
            SimpleNamespace(),
        )

        assert created["status"] == "draft"
        assert created["target_folder"] == "Projects/recoverable-demo"
        assert created["debug_events"][0]["message"] == "Session created"
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "recoverable-demo"))

        listed = await obsidian_routes.project_plan_sessions(SimpleNamespace())
        assert [session["id"] for session in listed["sessions"]] == [created["id"]]

        loaded = await obsidian_routes.project_plan_session_get(created["id"], SimpleNamespace())
        assert loaded["request"]["title"] == "Recoverable Demo"

        deleted = await obsidian_routes.project_plan_session_delete(created["id"], SimpleNamespace())
        assert deleted == {"success": True, "session_id": created["id"]}
        listed = await obsidian_routes.project_plan_sessions(SimpleNamespace())
        assert listed["sessions"] == []


@pytest.mark.asyncio
async def test_project_plan_session_preview_stream_persists_progress(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                path = re.search(r"Zieldatei \d+ von \d+: (.+)", user).group(1)
                return f"# Generated {path}\n\nStreamed content."
            if "Kontextzusammenfassung" in system:
                path = re.search(r"Neu generierte Datei: (.+)", user).group(1)
                return f"CTX after {path}"
            raise AssertionError("unexpected prompt")

        monkeypatch.setattr(obsidian_routes, "project_planning_llm_call", lambda owner: fake_llm)
        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    target_folder="Projects/Demo",
                    title="Demo",
                    description="Create a streamed project folder.",
                    kind="software",
                    generate_content=True,
                )
            ),
            SimpleNamespace(),
        )

        response = await obsidian_routes.project_plan_session_preview_stream(
            created["id"],
            obsidian_routes.ProjectPlanSessionPreviewRequest(),
            SimpleNamespace(),
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        stream = "".join(chunks)

        assert "event: session_updated" in stream
        assert "event: plan_done" in stream
        loaded = await obsidian_routes.project_plan_session_get(created["id"], SimpleNamespace())
        assert loaded["status"] == "ready"
        assert loaded["progress"]["phase"] == "ready"
        assert loaded["plan"]["target_folder"] == "Projects/Demo"
        assert any(event["phase"] == "file_started" for event in loaded["debug_events"])
        assert any(event["phase"] == "ready" for event in loaded["debug_events"])
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))


@pytest.mark.asyncio
async def test_project_plan_session_apply_marks_created_and_hides_from_active(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        request_payload = ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo",
            description="Build a graphable project plan.",
            kind="software",
            generate_content=False,
        )
        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(request=request_payload),
            SimpleNamespace(),
        )
        plan = build_project_plan(tmpdir, request_payload)
        obsidian_routes._update_project_plan_session(
            tmpdir,
            created["id"],
            plan=plan.model_dump() if hasattr(plan, "model_dump") else plan.dict(),
            status="ready",
        )

        result = await obsidian_routes.project_plan_session_apply(
            created["id"],
            obsidian_routes.ProjectPlanSessionApplyRequest(confirm=True),
            SimpleNamespace(),
        )

        assert result["success"] is True
        assert result["session"]["status"] == "created"
        assert any(event["phase"] == "created" for event in result["session"]["debug_events"])
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))
        listed = await obsidian_routes.project_plan_sessions(SimpleNamespace())
        assert listed["sessions"] == []


@pytest.mark.asyncio
async def test_project_plan_tools_preview_apply_and_graph(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        templates = await handle_project_plan_templates("", owner="alice")
        assert templates["exit_code"] == 0
        assert "software" in templates["output"]

        preview = await handle_project_plan_preview(json.dumps({
            "target_folder": "Projects/Demo",
            "title": "Demo App",
            "description": "Build a graphable project plan.",
            "kind": "software",
        }), owner="alice")
        assert preview["exit_code"] == 0
        plan = json.loads(preview["output"])
        assert plan["conflicts"] == []
        assert len(plan["files"]) >= 6
        assert "Projects/Demo/00 Projektuebersicht.md" in {item["path"] for item in plan["files"]}

        blocked = await handle_project_plan_apply(json.dumps({"plan": plan}), owner="alice")
        assert blocked["exit_code"] == 1
        assert "Confirmation required" in blocked["error"]

        applied = await handle_project_plan_apply(json.dumps({"plan": plan, "confirm": True}), owner="alice")
        assert applied["exit_code"] == 0
        result = json.loads(applied["output"])
        assert "Projects/Demo/00 Projektuebersicht.md" in result["created_files"]
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))

        graph_res = await handle_graph("{}", owner="alice")
        graph = json.loads(graph_res["output"])["graph"]
        edge_types = {edge["type"] for edge in graph["edges"]}
        assert "wiki_link" in edge_types
        assert "shared_tag" not in edge_types
        assert "depends_on" not in edge_types
        assert "supports" not in edge_types

        history_res = await handle_history('{"limit": 20}', owner="alice")
        assert "obsidian_project_plan_apply" in history_res["output"]

        conflict = await handle_project_plan_preview(json.dumps({
            "target_folder": "Projects/Demo",
            "title": "Demo App",
            "description": "Build again.",
            "kind": "software",
        }), owner="alice")
        conflict_plan = json.loads(conflict["output"])
        assert conflict_plan["conflicts"]
        refused = await handle_project_plan_apply(json.dumps({"plan": conflict_plan, "confirm": True}), owner="alice")
        assert refused["exit_code"] == 1
        assert "conflicts" in refused["output"]

        new_folder_preview = await handle_project_plan_preview(json.dumps({
            "target_folder": f"{NEW_PROJECT_FOLDER_SENTINEL}::",
            "title": "Fresh Project",
            "description": "Create under the vault root only when applied.",
            "kind": "generic",
        }), owner="alice")
        assert new_folder_preview["exit_code"] == 0
        new_folder_plan = json.loads(new_folder_preview["output"])
        assert new_folder_plan["target_folder"] == "fresh-project"
        assert not os.path.exists(os.path.join(tmpdir, "fresh-project"))

        new_folder_apply = await handle_project_plan_apply(json.dumps({"plan": new_folder_plan, "confirm": True}), owner="alice")
        assert new_folder_apply["exit_code"] == 0
        assert os.path.exists(os.path.join(tmpdir, "fresh-project", "00 Projektuebersicht.md"))


def test_memory_review_preview_reuses_tags_links_and_validates_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\n#project/demo #type/project\n\nGraph memory review context.")

        plan = build_memory_review_plan(tmpdir, MemoryReviewRequest(
            candidate={
                "title": "Graph memory decision",
                "content": "Memory review should save graph decisions into Demo context.",
                "source": "chat",
                "source_ref": "thread-123",
            },
            action="save_to_obsidian",
            target_folder="Memory Review",
            note_type="decision",
            project="Demo",
            tags=["#project/demo"],
            link_paths=["Projects/Demo.md"],
        ))

        assert plan.action == "save_to_obsidian"
        assert plan.conflicts == []
        assert plan.files[0].path.startswith("Memory Review/")
        assert plan.files[0].frontmatter["source"] == "chat"
        assert "#project/demo" in plan.files[0].tags
        assert "#type/decision" in plan.files[0].tags
        assert "[[Projects/Demo]]" in plan.files[0].links
        assert any(item.path == "Projects/Demo.md" for item in plan.suggested_notes)
        assert plan.relationships[0].target == "Projects/Demo.md"

        payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        bad = MemoryReviewPlan(**payload)
        bad.files[0].path = "../escape.md"
        with pytest.raises(MemoryReviewValidationError):
            validate_memory_review_plan(tmpdir, bad)

        bad = MemoryReviewPlan(**payload)
        bad.files[0].tags = ["#memory", "#status/review"]
        with pytest.raises(MemoryReviewValidationError):
            validate_memory_review_plan(tmpdir, bad)


@pytest.mark.asyncio
async def test_memory_review_tools_apply_create_append_history_and_graph(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\n#project/demo\n\nExisting graph context.")

        preview = await handle_memory_review_preview(json.dumps({
            "candidate": {
                "title": "Demo retention decision",
                "content": "Keep the memory review workflow linked to Demo.",
                "source": "chat",
                "source_ref": "chat:42",
            },
            "action": "save_to_obsidian",
            "target_folder": "Memory Review",
            "note_type": "decision",
            "project": "Demo",
            "tags": ["#project/demo"],
            "link_paths": ["Projects/Demo.md"],
        }), owner="alice")
        assert preview["exit_code"] == 0
        plan = json.loads(preview["output"])
        assert plan["files"][0]["links"] == ["[[Projects/Demo]]"]

        blocked = await handle_memory_review_apply(json.dumps({"plan": plan}), owner="alice")
        assert blocked["exit_code"] == 1
        assert "Confirmation required" in blocked["error"]

        applied = await handle_memory_review_apply(json.dumps({"plan": plan, "confirm": True}), owner="alice")
        assert applied["exit_code"] == 0
        result = json.loads(applied["output"])
        created_path = result["created_files"][0]
        assert os.path.exists(os.path.join(tmpdir, created_path.replace("/", os.sep)))

        graph_res = await handle_graph("{}", owner="alice")
        graph = json.loads(graph_res["output"])["graph"]
        edge_types = {edge["type"] for edge in graph["edges"]}
        assert "wiki_link" in edge_types
        assert "relates_to" in edge_types

        append_preview = await handle_memory_review_preview(json.dumps({
            "candidate": {
                "title": "Append insight",
                "content": "Append this insight to Demo instead of creating another note.",
                "source": "manual",
            },
            "action": "append_to_note",
            "target_note": "Projects/Demo.md",
            "tags": ["#project/demo"],
        }), owner="alice")
        append_plan = json.loads(append_preview["output"])
        append_res = await handle_memory_review_apply(json.dumps({"plan": append_plan, "confirm": True}), owner="alice")
        assert append_res["exit_code"] == 0
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "r", encoding="utf-8") as f:
            assert "Append this insight" in f.read()

        memory_only = await handle_memory_review_preview(json.dumps({
            "candidate": {"content": "Keep only in Odysseus memory.", "source": "chat"},
            "action": "memory_only",
        }), owner="alice")
        memory_plan = json.loads(memory_only["output"])
        memory_result = await handle_memory_review_apply(json.dumps({"plan": memory_plan}), owner="alice")
        assert memory_result["exit_code"] == 0
        assert json.loads(memory_result["output"])["created_files"] == []

        history_res = await handle_history('{"limit": 20}', owner="alice")
        assert "obsidian_memory_review_apply" in history_res["output"]


@pytest.mark.asyncio
async def test_manual_relationships_are_graph_edges_and_undoable(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Roadmap.md"), "w", encoding="utf-8") as f:
            f.write("# Roadmap")
        with open(os.path.join(tmpdir, "Architecture.md"), "w", encoding="utf-8") as f:
            f.write("# Architecture")

        add_res = await handle_add_relationship(json.dumps({
            "source": "Roadmap.md",
            "target": "Architecture.md",
            "type": "depends_on",
            "reason": "Roadmap depends on architecture",
        }), owner="alice")
        assert add_res["exit_code"] == 0

        rel_res = await handle_list_relationships("", owner="alice")
        assert rel_res["exit_code"] == 0
        relationships = json.loads(rel_res["output"])
        assert relationships[0]["type"] == "depends_on"

        graph_res = await handle_graph("{}", owner="alice")
        graph = json.loads(graph_res["output"])["graph"]
        assert any(edge["type"] == "depends_on" for edge in graph["edges"])

        history_res = await handle_history('{"limit": 5}', owner="alice")
        assert history_res["exit_code"] == 0
        assert "relationship_add" in history_res["output"]

        undo_res = await handle_undo("", owner="alice")
        assert undo_res["exit_code"] == 0
        graph_res = await handle_graph("{}", owner="alice")
        graph = json.loads(graph_res["output"])["graph"]
        assert not any(edge["type"] == "depends_on" for edge in graph["edges"])


@pytest.mark.asyncio
async def test_delete_relationship_records_reversible_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "A.md"), "w", encoding="utf-8") as f:
            f.write("# A")
        with open(os.path.join(tmpdir, "B.md"), "w", encoding="utf-8") as f:
            f.write("# B")

        await handle_add_relationship('{"source": "A.md", "target": "B.md", "type": "relates_to"}')
        delete_res = await handle_delete_relationship('{"source": "A.md", "target": "B.md", "type": "relates_to"}')
        assert delete_res["exit_code"] == 0

        undo_res = await handle_undo("")
        assert undo_res["exit_code"] == 0
        graph_res = await handle_graph("{}")
        graph = json.loads(graph_res["output"])["graph"]
        assert any(edge["type"] == "relates_to" for edge in graph["edges"])


@pytest.mark.asyncio
async def test_file_write_and_rename_history_undo(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        write_res = await handle_write_note('{"path": "Plan.md", "content": "# Plan"}', owner="alice")
        assert write_res["exit_code"] == 0
        undo_res = await handle_undo("", owner="alice")
        assert undo_res["exit_code"] == 0
        assert not os.path.exists(os.path.join(tmpdir, "Plan.md"))

        await handle_write_note('{"path": "Plan.md", "content": "# Plan"}', owner="alice")
        rename_res = await handle_rename_item('{"old_path": "Plan.md", "new_path": "Roadmap.md"}', owner="alice")
        assert rename_res["exit_code"] == 0
        undo_res = await handle_undo("", owner="alice")
        assert undo_res["exit_code"] == 0
        assert os.path.exists(os.path.join(tmpdir, "Plan.md"))
        assert not os.path.exists(os.path.join(tmpdir, "Roadmap.md"))


def test_plain_vault_export_import_roundtrip():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        os.makedirs(os.path.join(src, "Projects"), exist_ok=True)
        with open(os.path.join(src, "Projects", "Plan.md"), "w", encoding="utf-8") as f:
            f.write("# Plan\n\nPlain export.")

        archive = export_vault(src)
        result = import_vault(dst, archive.data)

        assert archive.encrypted is False
        assert archive.file_count == 1
        assert result["imported_files"] == 1
        with open(os.path.join(dst, "Projects", "Plan.md"), "r", encoding="utf-8") as f:
            assert "Plain export" in f.read()


def test_import_rejects_traversal_archive_without_writing_outside():
    with tempfile.TemporaryDirectory() as vault:
        marker = os.path.abspath(os.path.join(vault, "..", "escape.md"))
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("../escape.md", "nope")

        with pytest.raises(VaultSecurityError):
            import_vault(vault, buffer.getvalue())

        assert not os.path.exists(marker)


def test_encrypted_vault_export_requires_correct_password():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        with open(os.path.join(src, "Secret.md"), "w", encoding="utf-8") as f:
            f.write("# Secret\n\nHidden content.")

        archive = export_vault(src, password="correct horse battery staple")

        assert archive.encrypted is True
        assert b"Hidden content" not in archive.data
        with pytest.raises(VaultSecurityError):
            import_vault(dst, archive.data, password="wrong password")

        result = import_vault(dst, archive.data, password="correct horse battery staple")

        assert result["imported_files"] == 1
        with open(os.path.join(dst, "Secret.md"), "r", encoding="utf-8") as f:
            assert "Hidden content" in f.read()


def test_large_vault_fixture_produces_retrievable_graph_baseline():
    with tempfile.TemporaryDirectory() as tmpdir:
        fixture = create_large_vault_fixture(tmpdir, note_count=48)
        profile = profile_graph_build(tmpdir)

        assert fixture["note_count"] == 48
        assert profile["nodes"] >= 48
        assert profile["edges"] >= 48
        assert profile["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_locked_vault_blocks_ai_file_access(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Project.md"), "w", encoding="utf-8") as f:
            f.write("# Project")
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)

        res = await handle_read_note('{"path": "Project.md"}')

        assert res["exit_code"] == 1
        assert "locked" in res["error"].lower()


@pytest.mark.asyncio
async def test_ai_vault_password_and_encrypted_archive_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: src)
        with open(os.path.join(src, "Project.md"), "w", encoding="utf-8") as f:
            f.write("# Project")

        res = await handle_vault_set_password('{"password": "strong password"}')
        assert res["exit_code"] == 1
        assert "Confirmation required" in res["error"]

        res = await handle_vault_set_password('{"password": "strong password", "confirm": true}')
        assert res["exit_code"] == 0
        assert protection_status(src)["protected"] is True

        res = await handle_vault_lock("")
        assert res["exit_code"] == 0

        res = await handle_vault_status("")
        assert '"locked": true' in res["output"]

        res = await handle_vault_unlock('{"password": "strong password"}')
        assert res["exit_code"] == 0

        export_res = await handle_vault_export('{"password": "export password"}')
        assert export_res["exit_code"] == 1
        assert "Confirmation required" in export_res["error"]

        export_res = await handle_vault_export('{"password": "export password", "confirm": true}')
        assert export_res["exit_code"] == 0

        archive_json = json.loads(export_res["output"])
        assert archive_json["encrypted"] is True

        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: dst)
        import_res = await handle_vault_import(json.dumps({
            "archive_base64": archive_json["archive_base64"],
            "password": "export password",
            "confirm": True,
        }))

        assert import_res["exit_code"] == 0
        assert os.path.exists(os.path.join(dst, "Project.md"))


def test_plugin_setup_registration():
    """Verify that setup registers routes and agent tools."""
    registered_routers = []
    registered_tools = []
    registered_context_providers = []
    registered_consolidation_jobs = []

    class MockContext:
        logger = SimpleNamespace(warning=lambda *args, **kwargs: None)

        def add_router(self, router):
            registered_routers.append(router)

        def register_tool(self, spec):
            registered_tools.append(spec)

        def register_context_provider(self, spec):
            registered_context_providers.append(spec)

        def register_consolidation_job(self, spec):
            registered_consolidation_jobs.append(spec)

    ctx = MockContext()
    setup(ctx)

    assert len(registered_routers) == 1
    assert registered_context_providers[0]["id"] == PROVIDER_ID
    assert {
        "chat",
        "agent",
        "memory",
        "readiness",
        "freshness_gate",
        "raptor",
        "hybrid_retrieval",
    } <= set(registered_context_providers[0]["capabilities"])
    assert registered_consolidation_jobs[0]["id"] == JOB_ID
    tool_names = {spec["name"] for spec in registered_tools}
    permissions = {spec["name"]: spec.get("permission") for spec in registered_tools}
    assert PLUGIN["ui"]["open"] == "/api/plugins/obsidian/app"
    assert "obsidian_list_notes" in tool_names
    assert "obsidian_tree" in tool_names
    assert "obsidian_read_note" in tool_names
    assert "obsidian_write_note" in tool_names
    assert "obsidian_search_notes" in tool_names
    assert "obsidian_list_tags" in tool_names
    assert "obsidian_graph" in tool_names
    assert "obsidian_list_relationships" in tool_names
    assert "obsidian_add_relationship" in tool_names
    assert "obsidian_delete_relationship" in tool_names
    assert "obsidian_history" in tool_names
    assert "obsidian_undo" in tool_names
    assert "obsidian_project_plan_templates" in tool_names
    assert "obsidian_project_plan_gamedev_draft" in tool_names
    assert "obsidian_project_plan_preview" in tool_names
    assert "obsidian_project_plan_apply" in tool_names
    assert "obsidian_memory_review_preview" in tool_names
    assert "obsidian_memory_review_apply" in tool_names
    assert "obsidian_memory_tree_status" in tool_names
    assert "obsidian_memory_status" in tool_names
    assert "obsidian_memory_tree_analyze" in tool_names
    assert "obsidian_knowledge_audit" in tool_names
    assert "obsidian_quarantine_list" in tool_names
    assert "obsidian_raptor_status" in tool_names
    assert "obsidian_create_folder" in tool_names
    assert "obsidian_rename_item" in tool_names
    assert "obsidian_delete_note" in tool_names
    assert "obsidian_delete_folder" in tool_names
    assert "obsidian_vault_set_password" in tool_names
    assert "obsidian_vault_lock" in tool_names
    assert "obsidian_vault_unlock" in tool_names
    assert "obsidian_vault_remove_password" in tool_names
    assert "obsidian_vault_export" in tool_names
    assert "obsidian_vault_import" in tool_names
    assert permissions["obsidian_read_note"] == "user"
    assert permissions["obsidian_write_note"] == "user"
    assert permissions["obsidian_search_notes"] == "user"
    assert permissions["obsidian_graph"] == "user"


@pytest.mark.asyncio
async def test_locked_vault_blocks_all_actions(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)

        # Test list notes
        res = await handle_list_notes("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test write note
        res = await handle_write_note('{"path": "test.md", "content": "hello"}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test delete note
        res = await handle_delete_note('{"path": "test.md", "confirm": true}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test list tags
        res = await handle_list_tags("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test graph
        res = await handle_graph("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test add relationship
        res = await handle_add_relationship('{"source": "a.md", "target": "b.md", "confirm": true}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test search notes
        res = await handle_search_notes('{"query": "hello"}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test history
        res = await handle_history("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test project plan preview
        res = await handle_project_plan_preview('{"title": "Proj", "kind": "software", "description": "desc"}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test memory review preview
        res = await handle_memory_review_preview('{"candidate": {"content": "memory"}, "action": "save_to_obsidian"}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()
