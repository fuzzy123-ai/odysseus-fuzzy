from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.agent_tools import ToolBlock
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote
from src.tool_execution import execute_tool_block
from src.tool_implementations import do_manage_repos
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _make_repo(base: Path) -> Path:
    repo = base / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", "https://x-access-token:secret-value@github.com/fuzzy123-ai/demo.git")
    readme.write_text("two\n", encoding="utf-8")
    return repo


def _write_registry(path: Path) -> None:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner="fuzzy123-ai",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            remotes=[
                RepoRemote.create(
                    name="origin",
                    url="https://github.com/fuzzy123-ai/demo.git",
                    purpose="origin",
                )
            ],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    registry.save_json(path)


@pytest.mark.asyncio
async def test_manage_repos_lists_and_reads_registered_repo(tmp_path: Path, monkeypatch):
    _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))

    listed = await do_manage_repos(json.dumps({"action": "list"}), owner="admin")
    status = await do_manage_repos(json.dumps({"action": "status", "repo_id": "demo"}), owner="admin")
    remotes = await do_manage_repos(json.dumps({"action": "remotes", "repo_id": "demo"}), owner="admin")

    assert listed["exit_code"] == 0
    assert listed["repos"][0]["repo_id"] == "demo"
    assert status["status"]["dirty"] is True
    assert any("README.md" in entry for entry in status["status"]["entries"])
    assert remotes["remotes"][0]["url_redacted"] == "https://github.com/fuzzy123-ai/demo.git"
    dumped = json.dumps({"listed": listed, "status": status, "remotes": remotes})
    assert "secret-value" not in dumped
    assert str(tmp_path) not in dumped


@pytest.mark.asyncio
async def test_manage_repos_dispatches_through_execute_tool_block(tmp_path: Path, monkeypatch):
    _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setattr("src.tool_execution._owner_is_admin", lambda owner: True)

    desc, result = await execute_tool_block(
        ToolBlock("manage_repos", json.dumps({"action": "changed_paths", "repo_id": "demo"})),
        owner="admin",
    )

    assert desc == "manage_repos"
    assert result["exit_code"] == 0
    assert result["changed_paths"] == [{"status": "M", "path": "README.md"}]


@pytest.mark.asyncio
async def test_manage_repos_blocks_unknown_repo(tmp_path: Path, monkeypatch):
    _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))

    result = await do_manage_repos(json.dumps({"action": "status", "repo_id": "missing"}), owner="admin")

    assert result["exit_code"] == 1
    assert "unknown repo" in result["error"]


def test_manage_repos_schema_index_and_security_wiring():
    schema_names = {(schema.get("function") or {}).get("name") for schema in FUNCTION_TOOL_SCHEMAS}
    assert "manage_repos" in schema_names

    from src.agent_tools import TOOL_TAGS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS

    assert "manage_repos" in TOOL_TAGS
    assert "manage_repos" in BUILTIN_TOOL_DESCRIPTIONS
    assert "manage_repos" in NON_ADMIN_BLOCKED_TOOLS
    assert "manage_repos" not in PLAN_MODE_READONLY_TOOLS


def test_source_does_not_add_write_repo_actions_to_manage_repos():
    source = Path("src/tool_implementations.py").read_text(encoding="utf-8").lower()
    start = source.index("async def do_manage_repos")
    end = source.index("def _skill_dump", start)
    section = source[start:end]

    for forbidden in (
        'action == "register"',
        'action == "forget"',
        'action == "update_policy"',
        "git push",
        "git commit",
        "git reset",
    ):
        assert forbidden not in section
