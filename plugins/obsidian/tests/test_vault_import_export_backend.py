import json
import os
import sys
import tempfile
import zipfile
from io import BytesIO

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend import vault_security
from backend.vault_security import (
    VaultSecurityError,
    export_vault,
    import_vault,
    lock_vault,
    protection_status,
    set_password,
    validate_archive_member,
)
from plugin import (
    handle_history,
    handle_vault_export,
    handle_vault_import,
    handle_vault_lock,
    handle_vault_remove_password,
    handle_vault_set_password,
    handle_vault_status,
    handle_vault_unlock,
)


def test_archive_member_validation_blocks_escape_paths():
    dangerous_paths = [
        "../escape.md",
        "notes/../../escape.md",
        "/tmp/escape.md",
        "C:\\temp\\escape.md",
        ".odysseus-vault.json",
        "vault.bin",
        ".obsidian/history.json",
        ".obsidian/relationships.json",
        ".obsidian/project_planning_sessions.json",
    ]

    for path in dangerous_paths:
        with pytest.raises(VaultSecurityError):
            validate_archive_member(path)

    assert validate_archive_member("Projects/Plan.md") == "Projects/Plan.md"


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


def test_import_rejects_duplicate_archive_paths_case_insensitively():
    with tempfile.TemporaryDirectory() as vault:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("Projects/Plan.md", "# One\n")
            zf.writestr("projects/PLAN.md", "# Two\n")

        with pytest.raises(VaultSecurityError, match="duplicate paths"):
            import_vault(vault, buffer.getvalue())

        assert not os.path.exists(os.path.join(vault, "Projects", "Plan.md"))


def test_import_does_not_partially_write_when_archive_read_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as vault:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("Projects/One.md", "# One\n")
            zf.writestr("Projects/Two.md", "# Two\n")

        real_open = vault_security.zipfile.ZipFile.open
        calls = {"count": 0}

        def flaky_open(self, name, mode="r", pwd=None, *, force_zip64=False):
            info_name = getattr(name, "filename", name)
            if mode == "r" and info_name == "Projects/Two.md":
                calls["count"] += 1
                raise RuntimeError("simulated read failure")
            return real_open(self, name, mode=mode, pwd=pwd, force_zip64=force_zip64)

        monkeypatch.setattr(vault_security.zipfile.ZipFile, "open", flaky_open)

        with pytest.raises(VaultSecurityError, match="encrypted archive unsupported"):
            import_vault(vault, buffer.getvalue())

        assert calls["count"] == 1
        assert not os.path.exists(os.path.join(vault, "Projects", "One.md"))
        assert not os.path.exists(os.path.join(vault, "Projects", "Two.md"))


def test_import_rejects_traversal_archive_without_writing_outside():
    with tempfile.TemporaryDirectory() as vault:
        marker = os.path.abspath(os.path.join(vault, "..", "escape.md"))
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("../escape.md", "nope")

        with pytest.raises(VaultSecurityError):
            import_vault(vault, buffer.getvalue())

        assert not os.path.exists(marker)


@pytest.mark.parametrize("member_name", [
    ".odysseus-vault.json",
    "vault.bin",
    ".obsidian/history.json",
    ".obsidian/relationships.json",
    ".obsidian/project_planning_sessions.json",
])
def test_import_rejects_reserved_internal_archive_entries(member_name):
    with tempfile.TemporaryDirectory() as vault:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(member_name, "nope")

        with pytest.raises(VaultSecurityError) as exc:
            import_vault(vault, buffer.getvalue())

        assert "reserved" in str(exc.value).lower() or "metadata" in str(exc.value).lower()
        assert not os.path.exists(os.path.join(vault, ".obsidian", "history.json"))
        assert not os.path.exists(os.path.join(vault, ".obsidian", "relationships.json"))
        assert not os.path.exists(os.path.join(vault, ".obsidian", "project_planning_sessions.json"))


def test_import_rejects_archive_that_only_contains_export_wrapper_files():
    with tempfile.TemporaryDirectory() as vault:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("odysseus-vault.json", json.dumps({"format": "odysseus-obsidian-vault", "encrypted": False}))
            zf.writestr("vault.bin", b"plain payload that should never be imported directly")

        with pytest.raises(VaultSecurityError) as exc:
            import_vault(vault, buffer.getvalue())

        assert "reserved" in str(exc.value).lower() or "metadata" in str(exc.value).lower()
        assert os.listdir(vault) == []


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


@pytest.mark.asyncio
async def test_vault_password_values_do_not_leak_to_outputs_state_or_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        secret = "unique vault password 27349"
        export_secret = "unique export password 81273"

        set_res = await handle_vault_set_password(json.dumps({
            "password": secret,
            "confirm": True,
        }), owner="alice")
        assert set_res["exit_code"] == 0
        assert secret not in set_res.get("output", "")
        assert secret not in set_res.get("error", "")

        state_path = os.path.join(tmpdir, ".odysseus-vault.json")
        with open(state_path, "r", encoding="utf-8") as f:
            state_content = f.read()
        assert secret not in state_content
        assert "password_hash" in state_content

        lock_res = await handle_vault_lock("", owner="alice")
        assert lock_res["exit_code"] == 0

        unlock_res = await handle_vault_unlock(json.dumps({"password": secret}), owner="alice")
        assert unlock_res["exit_code"] == 0
        assert secret not in unlock_res.get("output", "")
        assert secret not in unlock_res.get("error", "")

        export_res = await handle_vault_export(json.dumps({
            "password": export_secret,
            "confirm": True,
        }), owner="alice")
        assert export_res["exit_code"] == 0
        assert export_secret not in export_res.get("output", "")
        assert export_secret not in export_res.get("error", "")

        history_res = await handle_history('{"limit": 20}', owner="alice")
        assert history_res["exit_code"] == 0
        assert secret not in history_res["output"]
        assert export_secret not in history_res["output"]

        remove_res = await handle_vault_remove_password(json.dumps({
            "password": secret,
            "confirm": True,
        }), owner="alice")
        assert remove_res["exit_code"] == 0
        assert secret not in remove_res.get("output", "")
        assert secret not in remove_res.get("error", "")
