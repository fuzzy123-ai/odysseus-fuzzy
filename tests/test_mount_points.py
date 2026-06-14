"""Owner-scoped mount points for virtual /mnt file-tool access."""

import json
import os

import pytest

from src.agent_tools import ToolBlock
from src.tool_execution import _direct_fallback, _resolve_tool_path, execute_tool_block


@pytest.fixture
def mount_file(tmp_path, monkeypatch):
    path = tmp_path / "mounts.json"
    monkeypatch.setattr("core.mount_manager.MOUNTS_FILE", str(path))
    return path


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(
        "src.tool_execution.owner_is_admin_or_single_user",
        lambda owner: True,
    )


def _write_mounts(mount_file, mounts):
    mount_file.write_text(json.dumps({"mounts": mounts}, indent=2), encoding="utf-8")


def _mount(owner, host_path, *, read_only=False, allowed_tools=None):
    return {
        "name": "project",
        "host_path": str(host_path),
        "virtual_path": "/mnt/project",
        "owner": owner,
        "read_only": read_only,
        "enabled": True,
        "allowed_tools": allowed_tools or [],
    }


def test_resolves_owner_mount_path(mount_file, tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    target = host / "note.txt"
    target.write_text("hello", encoding="utf-8")
    _write_mounts(mount_file, [_mount("alice", host)])

    resolved = _resolve_tool_path("/mnt/project/note.txt", owner="alice", tool="read_file")
    assert resolved == os.path.realpath(target)


def test_owner_isolation_blocks_other_users_mount(mount_file, tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [_mount("alice", host)])

    with pytest.raises(ValueError) as exc:
        _resolve_tool_path("/mnt/project/note.txt", owner="bob", tool="read_file")
    assert "not mounted for this user" in str(exc.value)


def test_virtual_path_escape_is_rejected(mount_file, tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [_mount("alice", host)])

    with pytest.raises(ValueError) as exc:
        _resolve_tool_path("/mnt/project/../outside.txt", owner="alice", tool="read_file")
    assert "must not contain" in str(exc.value)


def test_sensitive_paths_inside_mount_are_rejected(mount_file, tmp_path):
    host = tmp_path / "host"
    sensitive = host / ".ssh"
    sensitive.mkdir(parents=True)
    (sensitive / "authorized_keys").write_text("secret", encoding="utf-8")
    _write_mounts(mount_file, [_mount("alice", host)])

    with pytest.raises(ValueError) as exc:
        _resolve_tool_path("/mnt/project/.ssh/authorized_keys", owner="alice", tool="read_file")
    assert "sensitive path" in str(exc.value)


@pytest.mark.asyncio
async def test_read_file_uses_mount_owner_from_request(mount_file, tmp_path, admin):
    alice = tmp_path / "alice"
    bob = tmp_path / "bob"
    alice.mkdir()
    bob.mkdir()
    (alice / "same.txt").write_text("alice data", encoding="utf-8")
    (bob / "same.txt").write_text("bob data", encoding="utf-8")
    _write_mounts(mount_file, [_mount("alice", alice), _mount("bob", bob)])

    _desc, result = await execute_tool_block(
        ToolBlock("read_file", "/mnt/project/same.txt"),
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert result["output"] == "alice data"


@pytest.mark.asyncio
async def test_write_file_respects_mount_read_only(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [_mount("alice", host, read_only=True)])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/new.txt\nbody"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert "read-only" in result["error"]
    assert not (host / "new.txt").exists()


@pytest.mark.asyncio
async def test_write_file_can_create_inside_writable_mount(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [_mount("alice", host)])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/new.txt\nbody"),
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert (host / "new.txt").read_text(encoding="utf-8") == "body"


@pytest.mark.asyncio
async def test_allowed_tools_limit_applies_to_mount(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    (host / "note.txt").write_text("hello", encoding="utf-8")
    _write_mounts(mount_file, [_mount("alice", host, allowed_tools=["ls"])])

    result = await _direct_fallback("read_file", "/mnt/project/note.txt", owner="alice")
    assert result["exit_code"] == 1
    assert "does not allow tool" in result["error"]

    result = await _direct_fallback("ls", "/mnt/project", owner="alice")
    assert result["exit_code"] == 0
    assert "note.txt" in result["output"]
