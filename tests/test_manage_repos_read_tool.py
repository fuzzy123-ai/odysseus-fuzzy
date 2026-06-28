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


def _make_push_repo(base: Path) -> tuple[Path, Path, str, str]:
    repo = base / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "checkout", "-b", "codex/demo/work")
    (repo / "README.md").write_text("ready\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
    bare = base / "remotes" / "demo.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, text=True, check=True)
    _git(repo, "remote", "add", "fuzzy", str(bare))
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return repo, bare, branch, head


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


def _write_commit_registry(path: Path) -> None:
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
            allowed_actions=["status", "changed_paths", "commit_plan", "commit"],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    registry.save_json(path)


def _write_push_registry(path: Path) -> None:
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
                    name="fuzzy",
                    url="https://github.com/fuzzy123-ai/demo.git",
                    purpose="fork",
                    push_policy="push_allowed",
                )
            ],
            allowed_actions=["status", "push_plan", "push"],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    registry.save_json(path)


def _write_forge_registry(path: Path) -> None:
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
            privacy_class="private",
            provider_scope="external_allowed",
            created_at="2026-06-28T10:00:00Z",
        )
    )
    registry.save_json(path)


def _write_changes_registry(path: Path, *, privacy_class: str = "private") -> None:
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
            privacy_class=privacy_class,
            allowed_actions=["status", "changes", "change_history"],
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


@pytest.mark.asyncio
async def test_manage_repos_register_requires_confirmation_and_persists_redacted_registry(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "repo-registry.json"
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    payload = {
        "action": "register",
        "repo_id": "new-demo",
        "title": "New Demo",
        "owner": "fuzzy123-ai",
        "path_ref": "projects/new-demo/repo",
        "workspace_root": "projects/new-demo",
        "project_root": "projects/new-demo/repo",
        "remotes": [
            {
                "name": "fuzzy",
                "url": "https://x-access-token:secret-value@github.com/fuzzy123-ai/new-demo.git?token=abc",
                "purpose": "fork",
                "push_policy": "push_allowed",
            }
        ],
        "allowed_actions": ["status", "push"],
    }

    blocked = await do_manage_repos(json.dumps(payload), owner="admin")
    payload["confirmed"] = True
    registered = await do_manage_repos(json.dumps(payload), owner="admin")

    assert blocked["exit_code"] == 1
    assert "confirmed=true" in blocked["error"]
    assert registered["exit_code"] == 0
    assert registered["repo"]["repo_id"] == "new-demo"
    assert registered["repo"]["remotes"][0]["url_redacted"] == "https://github.com/fuzzy123-ai/new-demo.git"
    stored = json.loads(registry_path.read_text(encoding="utf-8"))
    dumped = json.dumps({"response": registered, "stored": stored})
    assert "secret-value" not in dumped
    assert "x-access-token" not in dumped
    assert str(tmp_path) not in dumped


@pytest.mark.asyncio
async def test_manage_repos_register_outside_allowed_roots_requires_operator_go(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "repo-registry.json"
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    payload = {
        "action": "register",
        "repo_id": "external-demo",
        "title": "External Demo",
        "owner": "fuzzy123-ai",
        "path_ref": "external/demo/repo",
        "workspace_root": "external/demo",
        "project_root": "external/demo/repo",
        "confirmed": True,
    }

    blocked = await do_manage_repos(json.dumps(payload), owner="admin")
    payload["operator_go"] = True
    registered = await do_manage_repos(json.dumps(payload), owner="admin")

    assert blocked["exit_code"] == 1
    assert "operator_go=true" in blocked["error"]
    assert registered["exit_code"] == 0
    assert registered["mutation"]["outside_allowed_roots"] is True


@pytest.mark.asyncio
async def test_manage_repos_forget_removes_registry_entry_without_deleting_repo(tmp_path: Path, monkeypatch):
    repo = _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))

    blocked = await do_manage_repos(json.dumps({"action": "forget", "repo_id": "demo"}), owner="admin")
    forgotten = await do_manage_repos(
        json.dumps({"action": "forget", "repo_id": "demo", "confirmed": True}),
        owner="admin",
    )
    listed = await do_manage_repos(json.dumps({"action": "list"}), owner="admin")

    assert blocked["exit_code"] == 1
    assert forgotten["exit_code"] == 0
    assert forgotten["mutation"]["files_deleted"] is False
    assert repo.exists()
    assert listed["repos"] == []


@pytest.mark.asyncio
async def test_manage_repos_update_policy_requires_confirmation_and_revalidates(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "repo-registry.json"
    _write_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    payload = {
        "action": "update_policy",
        "repo_id": "demo",
        "privacy_class": "private",
        "provider_scope": "local_only",
        "allowed_actions": ["status", "push"],
        "remotes": [
            {
                "name": "fuzzy",
                "url": "https://github.com/fuzzy123-ai/demo.git",
                "purpose": "fork",
                "push_policy": "push_allowed",
            }
        ],
    }

    blocked = await do_manage_repos(json.dumps(payload), owner="admin")
    payload["confirmed"] = True
    updated = await do_manage_repos(json.dumps(payload), owner="admin")
    bad = await do_manage_repos(
        json.dumps(
            {
                "action": "update_policy",
                "repo_id": "demo",
                "privacy_class": "sensitive",
                "provider_scope": "external_allowed",
                "confirmed": True,
            }
        ),
        owner="admin",
    )

    assert blocked["exit_code"] == 1
    assert updated["exit_code"] == 0
    assert updated["repo"]["allowed_actions"] == ["status", "push"]
    assert updated["repo"]["remotes"][0]["push_policy"] == "push_allowed"
    assert bad["exit_code"] == 1
    assert "sensitive repos" in bad["error"]


@pytest.mark.asyncio
async def test_manage_repos_commit_plan_explains_missing_gates(tmp_path: Path, monkeypatch):
    _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_commit_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))

    result = await do_manage_repos(
        json.dumps(
            {
                "action": "commit_plan",
                "repo_id": "demo",
                "objective": "Update readme",
                "changed_paths": ["README.md"],
                "checks_passed": True,
                "content_reviewed": True,
            }
        ),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert result["commit_report"]["plan"]["decision"] == "hold"
    assert "confirmed=true is required" in result["output"]
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.asyncio
async def test_manage_repos_commit_runs_confirmed_exact_path(tmp_path: Path, monkeypatch):
    repo = _make_repo(tmp_path)
    (repo / "OTHER.md").write_text("still dirty\n", encoding="utf-8")
    registry_path = tmp_path / "repo-registry.json"
    _write_commit_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))

    result = await do_manage_repos(
        json.dumps(
            {
                "action": "commit",
                "repo_id": "demo",
                "objective": "Update readme",
                "changed_paths": ["README.md"],
                "checks_passed": True,
                "content_reviewed": True,
                "confirmed": True,
            }
        ),
        owner="admin",
    )

    assert result["exit_code"] == 0
    assert result["commit_report"]["status"] == "committed"
    assert result["commit_report"]["committed_paths"] == ["repos/demo/README.md"]
    assert "OTHER.md" in subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.asyncio
async def test_manage_repos_push_plan_explains_missing_live_gates(tmp_path: Path, monkeypatch):
    _repo, _bare, branch, head = _make_push_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_push_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))

    result = await do_manage_repos(
        json.dumps(
            {
                "action": "push_plan",
                "repo_id": "demo",
                "remote_name": "fuzzy",
                "branch_name": branch,
                "commit_sha": head,
            }
        ),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert result["push_report"]["plan"]["decision"] == "hold"
    assert "operator_go=true" in result["output"]
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.asyncio
async def test_manage_repos_push_runs_confirmed_policy_allowed_branch(tmp_path: Path, monkeypatch):
    _repo, bare, branch, head = _make_push_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    _write_push_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))

    result = await do_manage_repos(
        json.dumps(
            {
                "action": "push",
                "repo_id": "demo",
                "remote_name": "fuzzy",
                "branch_name": branch,
                "commit_sha": head,
                "confirmed": True,
                "operator_go": True,
                "live_enabled": True,
            }
        ),
        owner="admin",
    )

    pushed = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result["exit_code"] == 0
    assert result["push_report"]["status"] == "pushed"
    assert pushed == head
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.asyncio
async def test_manage_repos_forge_plan_explains_auth_gate(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "repo-registry.json"
    _write_forge_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))

    result = await do_manage_repos(
        json.dumps(
            {
                "action": "forge_plan",
                "repo_id": "demo",
                "provider": "github",
                "namespace": "fuzzy123-ai",
                "repo_name": "demo",
                "create_repo_requested": True,
            }
        ),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert result["forge_report"]["plan"]["decision"] == "hold"
    assert "auth_ready=true" in result["output"]
    assert "separate explicit provider-create Go" in result["forge_report"]["plan"]["next_human_decision"]
    dumped = json.dumps(result)
    assert "token" not in dumped.lower()
    assert str(tmp_path) not in dumped


@pytest.mark.asyncio
async def test_manage_repos_forge_metadata_blocks_without_provider_client(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "repo-registry.json"
    _write_forge_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))

    result = await do_manage_repos(
        json.dumps(
            {
                "action": "forge_metadata",
                "repo_id": "demo",
                "provider": "gitea",
                "namespace": "fuzzy123-ai",
                "repo_name": "demo",
                "api_base_url": "https://gitea.example.test/api/v1",
                "auth_ready": True,
                "confirmed": True,
                "operator_go": True,
                "live_enabled": True,
            }
        ),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert result["forge_report"]["status"] == "blocked"
    assert "provider client is not configured" in result["output"]
    assert result["forge_report"]["plan"]["api_base_url_redacted"] == "https://gitea.example.test/api/v1"


@pytest.mark.asyncio
async def test_manage_repos_changes_collects_repo_scoped_memory_capsule(tmp_path: Path, monkeypatch):
    _make_repo(tmp_path)
    registry_path = tmp_path / "repo-registry.json"
    history_dir = tmp_path / "repo-change-history"
    _write_changes_registry(registry_path)
    monkeypatch.setenv("ODYSSEUS_REPO_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("ODYSSEUS_REPO_WORKSPACE_BASE", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_REPO_CHANGES_HISTORY_DIR", str(history_dir))

    first = await do_manage_repos(
        json.dumps({"action": "changes", "repo_id": "demo", "hours": 24}),
        owner="admin",
    )
    second = await do_manage_repos(
        json.dumps({"action": "changes", "repo_id": "demo", "hours": 24}),
        owner="admin",
    )
    history = await do_manage_repos(
        json.dumps({"action": "change_history", "repo_id": "demo", "limit": 5}),
        owner="admin",
    )

    assert first["exit_code"] == 0
    assert first["repo_changes"]["snapshot"]["repo_id"] == "demo"
    assert first["repo_changes"]["snapshot"]["privacy_class"] == "private"
    assert first["repo_changes"]["snapshot"]["external_summary_allowed"] is False
    assert first["repo_changes"]["memory_records"][0]["source"] == "repo_recent_changes"
    assert first["repo_changes"]["raptorgraph_event"]["event"] == "repo_recent_changes_snapshot"
    assert second["repo_changes"]["persisted"] is False
    assert second["repo_changes"]["duplicate_of"] == first["repo_changes"]["snapshot"]["id"]
    assert history["history"][0]["id"] == first["repo_changes"]["snapshot"]["id"]
    dumped = json.dumps({"first": first, "second": second, "history": history}, ensure_ascii=True)
    assert "repo_root" not in dumped
    assert "numstat" not in dumped
    assert "secret-value" not in dumped
    assert str(tmp_path) not in dumped


def test_manage_repos_schema_index_and_security_wiring():
    schema_by_name = {(schema.get("function") or {}).get("name"): schema for schema in FUNCTION_TOOL_SCHEMAS}
    schema_names = set(schema_by_name)
    assert "manage_repos" in schema_names
    actions = (
        schema_by_name["manage_repos"]["function"]["parameters"]["properties"]["action"]["enum"]
    )
    assert {
        "commit_plan",
        "commit",
        "push_plan",
        "push",
        "forge_plan",
        "forge_metadata",
        "changes",
        "change_history",
        "register",
        "forget",
        "update_policy",
    }.issubset(set(actions))
    params = schema_by_name["manage_repos"]["function"]["parameters"]["properties"]
    assert {"hours", "persist", "force"}.issubset(set(params))

    from src.agent_tools import TOOL_TAGS
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS

    assert "manage_repos" in TOOL_TAGS
    assert "manage_repos" in BUILTIN_TOOL_DESCRIPTIONS
    assert "manage_repos" in NON_ADMIN_BLOCKED_TOOLS
    assert "manage_repos" not in PLAN_MODE_READONLY_TOOLS


def test_source_keeps_manage_repos_without_free_git_shell():
    source = Path("src/tool_implementations.py").read_text(encoding="utf-8").lower()
    start = source.index("async def do_manage_repos")
    end = source.index("def _skill_dump", start)
    section = source[start:end]

    for forbidden in ("git reset", "subprocess.run"):
        assert forbidden not in section
