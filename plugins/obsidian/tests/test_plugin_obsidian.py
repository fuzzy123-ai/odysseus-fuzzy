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


