import os
import sys
import tempfile
import zipfile
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.routes import secure_path, get_file_tree
from backend.project_planning import (
    ProjectPlan,
    ProjectPlanRequest,
    ProjectPlanValidationError,
    build_project_plan,
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
from backend.performance_fixtures import create_large_vault_fixture, profile_graph_build
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
        assert any(edge["target"] == "Architecture.md" for edge in graph["edges"])


def test_project_plan_preview_validates_schema_paths_tags_and_conflicts():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects", "Demo"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"), "w", encoding="utf-8") as f:
            f.write("# Existing")

        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="A small planning target.",
            kind="software",
        ))

        assert plan.project.slug == "demo-app"
        assert plan.conflicts == [{"path": "Projects/Demo/00 Projektuebersicht.md", "reason": "file_exists"}]
        first = plan.files[0]
        assert first.path == "Projects/Demo/00 Projektuebersicht.md"
        assert "#project/demo-app" in first.tags
        assert "#type/project" in first.tags
        assert "#status/draft" in first.tags

        plan_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        bad = ProjectPlan(**plan_payload)
        bad.files[0].path = "../escape.md"
        with pytest.raises(ProjectPlanValidationError):
            validate_project_plan(tmpdir, bad)

        bad = ProjectPlan(**plan_payload)
        bad.files[0].tags = ["#project/demo-app", "#status/draft"]
        with pytest.raises(ProjectPlanValidationError):
            validate_project_plan(tmpdir, bad)


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
        assert "shared_tag" in edge_types
        assert "depends_on" in edge_types

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
        assert "shared_tag" in edge_types
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

    class MockContext:
        logger = SimpleNamespace(warning=lambda *args, **kwargs: None)

        def add_router(self, router):
            registered_routers.append(router)

        def register_tool(self, spec):
            registered_tools.append(spec)

    ctx = MockContext()
    setup(ctx)

    assert len(registered_routers) == 1
    tool_names = {spec["name"] for spec in registered_tools}
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
    assert "obsidian_project_plan_preview" in tool_names
    assert "obsidian_project_plan_apply" in tool_names
    assert "obsidian_memory_review_preview" in tool_names
    assert "obsidian_memory_review_apply" in tool_names
    assert "obsidian_create_folder" in tool_names
    assert "obsidian_rename_item" in tool_names
    assert "obsidian_delete_note" in tool_names
    assert "obsidian_delete_folder" in tool_names
    assert "obsidian_vault_status" in tool_names
    assert "obsidian_vault_set_password" in tool_names
    assert "obsidian_vault_lock" in tool_names
    assert "obsidian_vault_unlock" in tool_names
    assert "obsidian_vault_remove_password" in tool_names
    assert "obsidian_vault_export" in tool_names
    assert "obsidian_vault_import" in tool_names
