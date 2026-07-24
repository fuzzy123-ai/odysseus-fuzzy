"""Owner-scoped mount points for virtual /mnt file-tool access."""

import json
import os

import pytest

from core.mount_manager import validate_mount_definition
from src.agent_tools import ToolBlock
from src.tool_execution import _direct_fallback, _resolve_tool_path, execute_tool_block


@pytest.fixture
def mount_file(tmp_path, monkeypatch):
    path = tmp_path / "mounts.json"
    monkeypatch.setattr("core.mount_manager.MOUNTS_FILE", str(path))
    return path


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setitem(execute_tool_block.__globals__, "_owner_is_admin", lambda owner: True)


def _write_mounts(mount_file, mounts):
    mount_file.write_text(json.dumps({"mounts": mounts}, indent=2), encoding="utf-8")


def _writable_policy(**overrides):
    policy = {
        "enabled": True,
        "allowed_extensions": [".txt", ".md"],
        "max_bytes": 1024,
    }
    policy.update(overrides)
    return policy


def _mount(owner, host_path, *, read_only=True, allowed_tools=None, write_policy=None):
    return {
        "name": "project",
        "host_path": str(host_path),
        "virtual_path": "/mnt/project",
        "owner": owner,
        "read_only": read_only,
        "enabled": True,
        "allowed_tools": allowed_tools or [],
        "write_policy": write_policy or {},
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
    assert result["mount_feedback"]["reason_code"] == "mount_read_only"
    assert not (host / "new.txt").exists()


@pytest.mark.asyncio
async def test_write_file_requires_write_policy_even_when_not_read_only(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [_mount("alice", host, read_only=False, allowed_tools=["write_file"])])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/new.txt\nbody"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["mount_feedback"]["reason_code"] == "write_policy_missing"
    assert not (host / "new.txt").exists()


@pytest.mark.asyncio
async def test_write_file_can_create_inside_explicit_writable_mount(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [
        _mount("alice", host, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy())
    ])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/new.txt\nbody"),
        owner="alice",
    )

    assert result["exit_code"] == 0
    assert (host / "new.txt").read_text(encoding="utf-8") == "body"
    assert "/mnt/project/new.txt" in result["output"]
    assert str(host) not in result["output"]


@pytest.mark.asyncio
async def test_allowed_tools_limit_applies_to_mount(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    (host / "note.txt").write_text("hello", encoding="utf-8")
    _write_mounts(mount_file, [_mount("alice", host, allowed_tools=["ls"])])

    result = await _direct_fallback("read_file", "/mnt/project/note.txt", owner="alice")
    assert result["exit_code"] == 1
    assert "does not allow tool" in result["error"]
    assert result["mount_feedback"]["reason_code"] == "tool_not_allowed"

    result = await _direct_fallback("ls", "/mnt/project", owner="alice")
    assert result["exit_code"] == 0
    assert "note.txt" in result["output"]
    assert "/mnt/project:" in result["output"]
    assert str(host) not in result["output"]


def test_writable_global_mount_is_rejected(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    with pytest.raises(ValueError, match="global mounts"):
        validate_mount_definition(
            _mount("*", host, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy())
        )


def test_writable_mount_without_explicit_write_tool_is_rejected(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    with pytest.raises(ValueError, match="explicitly allow"):
        validate_mount_definition(
            _mount("alice", host, read_only=False, allowed_tools=["read_file"], write_policy=_writable_policy())
        )


def test_writable_mount_on_odysseus_data_is_rejected():
    from src.constants import DATA_DIR
    with pytest.raises(ValueError, match="Odysseus data"):
        validate_mount_definition(
            _mount("alice", DATA_DIR, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy())
        )


@pytest.mark.asyncio
async def test_write_file_blocks_disallowed_extension(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [
        _mount("alice", host, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy())
    ])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/run.exe\nbody"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["mount_feedback"]["reason_code"] == "extension_not_allowed"
    assert not (host / "run.exe").exists()


@pytest.mark.asyncio
async def test_write_file_blocks_too_large_payload(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [
        _mount("alice", host, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy(max_bytes=4))
    ])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/big.txt\n12345"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["mount_feedback"]["reason_code"] == "write_too_large"
    assert not (host / "big.txt").exists()


@pytest.mark.asyncio
async def test_create_only_blocks_overwrite(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    (host / "note.txt").write_text("old", encoding="utf-8")
    _write_mounts(mount_file, [
        _mount("alice", host, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy(create_only=True))
    ])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/note.txt\nnew"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["mount_feedback"]["reason_code"] == "create_only_overwrite"
    assert (host / "note.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_write_file_blocks_symlink_target(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    outside = tmp_path / "outside"
    host.mkdir()
    outside.mkdir()
    target = outside / "target.txt"
    target.write_text("secret", encoding="utf-8")
    link = host / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    _write_mounts(mount_file, [
        _mount("alice", host, read_only=False, allowed_tools=["write_file"], write_policy=_writable_policy())
    ])

    _desc, result = await execute_tool_block(
        ToolBlock("write_file", "/mnt/project/link.txt\nnew"),
        owner="alice",
    )

    assert result["exit_code"] == 1
    assert result["mount_feedback"]["reason_code"] in {"path_escapes_mount", "unsafe_reparse_point"}
    assert target.read_text(encoding="utf-8") == "secret"


@pytest.mark.asyncio
async def test_repeated_mount_block_feedback_tells_agent_to_stop(mount_file, tmp_path, admin):
    host = tmp_path / "host"
    host.mkdir()
    _write_mounts(mount_file, [_mount("alice", host, read_only=True)])

    first = await _direct_fallback("write_file", "/mnt/project/a.txt\nbody", owner="alice")
    second = await _direct_fallback("write_file", "/mnt/project/a.txt\nbody", owner="alice")

    assert first["mount_feedback"]["reason_code"] == "mount_read_only"
    assert second["mount_feedback"]["retry_guidance"].startswith("Do not retry")


def test_app_api_blocks_mount_endpoints():
    from src.tool_implementations import _APP_API_BLOCKLIST_PREFIXES
    assert "/api/mounts" in _APP_API_BLOCKLIST_PREFIXES
