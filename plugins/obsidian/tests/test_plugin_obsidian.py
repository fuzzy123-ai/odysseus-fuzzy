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
import backend.memory_status as memory_status_backend
import backend.tool_specs as tool_specs_backend
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
    MemoryReviewApplyRequest,
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
from backend.performance_fixtures import (
    LARGE_VAULT_RC_MEDIAN_THRESHOLD_MS,
    LARGE_VAULT_RC_NOTE_COUNT,
    LARGE_VAULT_RC_WORST_THRESHOLD_MS,
    create_large_vault_fixture,
    profile_graph_build,
    profile_graph_build_baseline,
)
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
    handle_memory_capture_apply,
    handle_memory_capture_preview,
    handle_project_plan_gamedev_draft,
    handle_project_plan_apply,
    handle_project_plan_improve_description,
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
    handle_spark_apply,
    handle_spark_analyze,
    handle_spark_plan,
    handle_undo,
    handle_write_note,
    handle_search_notes,
    handle_tree,
    handle_vault_export,
    handle_vault_import,
    handle_vault_lock,
    handle_vault_remove_password,
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
            "excluded_relevant_count": 0,
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
            "RAPTOR index metadata is invalid; rebuild remains disabled in the MVP."
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
    assert "warnings" in memory_status_spec.description
    assert all("owner" not in spec.input_schema.get("properties", {}) for spec in VAULT_TOOL_SPECS)


def test_vault_tool_spec_executes_shared_service(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\nbody")

        result = execute_vault_tool("obsidian_read_note", tmpdir, {"path": "Demo.md"}, "alice", {"source": "test"})

        assert result == "# Demo\n\nbody"


def test_vault_tool_spec_exposes_unified_memory_status_contract(monkeypatch):
    expected = {
        "read_only": True,
        "writes_supported": False,
        "readiness_gate": {"state": "blocked", "gaps": ["needs_review_items"]},
        "retrieval_policy": {"filtering_state": "audit_only"},
        "freshness_isolation_flags": {"needs_review": True},
        "raptor_lineage_flags": {"dirty": True},
        "raptor_write_gate": {"state": "blocked", "writes_supported": False},
        "warnings": ["Freshness Gate filtered 1 stale item(s)."],
        "summary": {
            "readiness_gate": {"state": "blocked", "gaps": ["needs_review_items"]},
            "warnings": ["Freshness Gate filtered 1 stale item(s)."],
        },
    }
    monkeypatch.setattr(tool_specs_backend, "memory_status", lambda vault_dir: expected)

    result = execute_vault_tool(
        "obsidian_memory_status",
        "vault",
        {"owner": "mallory"},
        "alice",
        {"source": "test"},
    )

    assert result is expected
    assert result["read_only"] is True
    assert result["writes_supported"] is False
    assert result["warnings"] == result["summary"]["warnings"]
    assert result["summary"]["readiness_gate"] == result["readiness_gate"]
    assert result["retrieval_policy"]["filtering_state"] == "audit_only"
    assert result["freshness_isolation_flags"]["needs_review"] is True
    assert result["raptor_lineage_flags"]["dirty"] is True
    assert result["raptor_write_gate"]["writes_supported"] is False


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


@pytest.mark.asyncio
async def test_obsidian_api_token_scopes_gate_vault_writes(monkeypatch):
    def token_request(scopes):
        return SimpleNamespace(
            state=SimpleNamespace(
                api_token=True,
                api_token_owner="alice",
                api_token_scopes=scopes,
                api_token_id="tok_123",
                api_token_prefix="ody",
            )
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        vault_service.create_file(tmpdir, "A.md", "# A", owner="alice", tool="test")
        vault_service.create_file(tmpdir, "B.md", "# B", owner="alice", tool="test")

        readonly_request = token_request(["vault:read"])
        files = await obsidian_routes.list_files(readonly_request)
        assert {"A.md", "B.md"} <= {item["path"] for item in files}

        with pytest.raises(HTTPException) as exc:
            await obsidian_routes.create_relationship(
                obsidian_routes.RelationshipRequest(source="A.md", target="B.md"),
                readonly_request,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == "API token missing required scope: vault:write"
        assert not os.path.exists(os.path.join(tmpdir, ".obsidian", "relationships.json"))

        writer_request = token_request(["vault:read", "vault:write"])
        result = await obsidian_routes.create_relationship(
            obsidian_routes.RelationshipRequest(source="A.md", target="B.md"),
            writer_request,
        )
        assert result["success"] is True
        assert result["relationship"]["source"] == "A.md"


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


@pytest.mark.asyncio
async def test_memory_capture_and_spark_tool_apply_require_confirmation(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        capture_preview = await handle_memory_capture_preview(json.dumps({
            "content": "Regel: Memory capture apply needs an explicit confirm gate.",
            "kind": "rule",
            "source": "agent",
            "confidence": "high",
        }), owner="alice")
        assert capture_preview["exit_code"] == 0
        capture_plan = json.loads(capture_preview["output"])

        blocked_capture = await handle_memory_capture_apply(json.dumps({"plan": capture_plan}), owner="alice")
        assert blocked_capture["exit_code"] == 1
        assert "Confirmation required" in blocked_capture["error"]
        assert not os.path.exists(os.path.join(tmpdir, "AI Memory"))

        confirmed_capture = await handle_memory_capture_apply(
            json.dumps({"plan": capture_plan, "confirm": True}),
            owner="alice",
        )
        assert confirmed_capture["exit_code"] == 0
        assert json.loads(confirmed_capture["output"])["success"] is True

        vault_service.create_file(tmpdir, "Loose.md", "# Loose\n\nNo links yet. #memory", owner="alice", tool="test")
        spark_plan = await handle_spark_plan('{"limit": 100}', owner="alice")
        assert spark_plan["exit_code"] == 0
        spark_payload = json.loads(spark_plan["output"])
        safe_action = next(
            action for action in spark_payload["actions"]
            if action["operations"] and action["risk"] != "high"
        )

        blocked_spark = await handle_spark_apply(json.dumps({
            "plan": spark_payload,
            "selected_action_ids": [safe_action["id"]],
        }), owner="alice")
        assert blocked_spark["exit_code"] == 1
        assert "Confirmation required" in blocked_spark["error"]

        confirmed_spark = await handle_spark_apply(json.dumps({
            "plan": spark_payload,
            "confirm": True,
            "selected_action_ids": [safe_action["id"]],
        }), owner="alice")
        assert confirmed_spark["exit_code"] == 0
        assert safe_action["id"] in json.loads(confirmed_spark["output"])["applied_actions"]


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
        project_plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(title="Proj", kind="software", description="desc"),
        )
        memory_review_plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(candidate={"content": "memory"}, action="save_to_obsidian"),
        )
        memory_capture_plan = build_memory_capture_plan(
            tmpdir,
            MemoryCaptureRequest(content="capture me", source="agent"),
        )
        spark_plan = build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=10))
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

        res = await handle_project_plan_templates("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_project_plan_improve_description(json.dumps({
            "title": "Proj",
            "kind": "software",
            "description": "desc",
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_project_plan_gamedev_draft(json.dumps({
            "title": "Proj",
            "description": "desc",
            "kind": "game_dev",
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_project_plan_apply(json.dumps({
            "plan": project_plan.model_dump(),
            "confirm": True,
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        # Test memory review preview
        res = await handle_memory_review_preview('{"candidate": {"content": "memory"}, "action": "save_to_obsidian"}')
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_memory_review_apply(json.dumps({
            "plan": memory_review_plan.model_dump(),
            "confirm": True,
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_memory_capture_preview(json.dumps({
            "content": "capture me",
            "source": "agent",
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_memory_capture_apply(json.dumps({
            "plan": memory_capture_plan.model_dump(),
            "confirm": True,
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_spark_analyze(json.dumps({"limit": 10}))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_spark_plan(json.dumps({"limit": 10}))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_spark_apply(json.dumps({
            "plan": spark_plan.model_dump(),
            "confirm": True,
            "selected_action_ids": [],
        }))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_memory_status("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_memory_tree_status("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_memory_tree_analyze(json.dumps({"limit": 10}))
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_knowledge_audit("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_quarantine_list("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()

        res = await handle_raptor_status("")
        assert res["exit_code"] == 1 and "locked" in res["error"].lower()


@pytest.mark.asyncio
async def test_locked_vault_blocks_route_level_content_surfaces(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Secret.md"), "w", encoding="utf-8") as f:
            f.write("# Secret\n\nhidden memory")
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)
        project_plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(title="Locked Project", kind="software", description="Should stay hidden."),
        )
        memory_review_plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(candidate={"content": "locked memory"}, action="save_to_obsidian"),
        )
        memory_capture_plan = build_memory_capture_plan(
            tmpdir,
            MemoryCaptureRequest(content="locked capture", source="agent"),
        )
        spark_apply_request = SparkApplyRequest(
            plan=build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=10)),
            confirm=True,
            selected_action_ids=[],
        )
        request = SimpleNamespace(
            state=SimpleNamespace(current_user="alice", api_token=False),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
            client=SimpleNamespace(host="127.0.0.1"),
        )

        async def assert_locked(awaitable):
            with pytest.raises(HTTPException) as exc:
                await awaitable
            assert exc.value.status_code == 423
            assert "locked" in str(exc.value.detail).lower()

        await assert_locked(obsidian_routes.list_files(request))
        await assert_locked(obsidian_routes.search_vault("hidden", request))
        await assert_locked(obsidian_routes.list_tags(request))
        await assert_locked(obsidian_routes.graph_vault(request))
        await assert_locked(obsidian_routes.project_plan_templates(request))
        await assert_locked(obsidian_routes.project_plan_sessions(request))
        await assert_locked(obsidian_routes.project_plan_session_get("missing", request))
        await assert_locked(obsidian_routes.project_plan_session_delete("missing", request))
        await assert_locked(obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    title="Locked Project",
                    kind="software",
                    description="Should not inspect a locked vault.",
                )
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_session_preview_stream(
            "missing",
            obsidian_routes.ProjectPlanSessionPreviewRequest(
                request=ProjectPlanRequest(
                    title="Locked Project",
                    kind="software",
                    description="Should not inspect a locked vault.",
                )
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_session_apply(
            "missing",
            obsidian_routes.ProjectPlanSessionApplyRequest(plan=project_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_preview(ProjectPlanRequest(
            title="Locked Project",
            kind="software",
            description="Should not inspect a locked vault.",
        ), request))
        await assert_locked(obsidian_routes.project_plan_preview_stream(ProjectPlanRequest(
            title="Locked Project",
            kind="software",
            description="Should not inspect a locked vault.",
        ), request))
        await assert_locked(obsidian_routes.project_plan_improve_description(
            ProjectDescriptionImproveRequest(
                title="Locked Project",
                kind="software",
                description="Should not inspect a locked vault.",
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_gamedev_draft(
            GameDevConceptDraftRequest(
                title="Locked Project",
                description="Should not inspect a locked vault.",
                kind="game_dev",
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_apply(
            obsidian_routes.ProjectPlanApplyRequest(plan=project_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.memory_review_preview(MemoryReviewRequest(
            candidate={"content": "locked memory"},
            action="save_to_obsidian",
        ), request))
        await assert_locked(obsidian_routes.memory_review_apply(
            MemoryReviewApplyRequest(plan=memory_review_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.memory_capture_preview(MemoryCaptureRequest(
            content="locked capture",
            source="agent",
        ), request))
        await assert_locked(obsidian_routes.memory_capture_apply(
            MemoryCaptureApplyRequest(plan=memory_capture_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.spark_analyze(SparkAnalyzeRequest(limit=10), request))
        await assert_locked(obsidian_routes.spark_plan(SparkAnalyzeRequest(limit=10), request))
        await assert_locked(obsidian_routes.spark_apply(spark_apply_request, request))
        await assert_locked(obsidian_routes.memory_tree(request))
        await assert_locked(obsidian_routes.memory_status_route(request))
        await assert_locked(obsidian_routes.memory_tree_analyze(request, limit=10))
        await assert_locked(obsidian_routes.knowledge_audit(request))
        await assert_locked(obsidian_routes.quarantine(request))
        await assert_locked(obsidian_routes.raptor_status_route(request))
