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
from plugin import (
    get_vault_path_by_owner,
    handle_create_folder,
    handle_delete_folder,
    handle_delete_note,
    handle_list_notes,
    handle_read_note,
    handle_rename_item,
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
        assert res["exit_code"] == 0
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Roadmap.md"))

        res = await handle_delete_folder('{"path": "Projects"}')
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

        res = await handle_delete_folder('{"path": "Projects"}')

        assert res["exit_code"] == 1
        assert os.path.isdir(os.path.join(tmpdir, "Projects"))


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
        assert res["exit_code"] == 0
        assert protection_status(src)["protected"] is True

        res = await handle_vault_lock("")
        assert res["exit_code"] == 0

        res = await handle_vault_status("")
        assert '"locked": true' in res["output"]

        res = await handle_vault_unlock('{"password": "strong password"}')
        assert res["exit_code"] == 0

        export_res = await handle_vault_export('{"password": "export password"}')
        assert export_res["exit_code"] == 0

        archive_json = json.loads(export_res["output"])
        assert archive_json["encrypted"] is True

        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: dst)
        import_res = await handle_vault_import(json.dumps({
            "archive_base64": archive_json["archive_base64"],
            "password": "export password",
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
