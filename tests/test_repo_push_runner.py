from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.agent_verification_receipt import build_verification_receipt, repository_binding
from src.claim_evidence_gate import (
    AgentMaintenanceClaimOwnership,
    AgentMaintenanceCompletionEvidence,
    ClaimEvidenceReport,
)
from src.repo_push_runner import (
    RepoPushCommandResult,
    RepoPushRunnerError,
    build_repo_forge_ancestry_command,
    build_repo_forge_git_transport_commands,
    build_repo_push_plan,
    parse_repo_remote_head_sha,
    repo_push_command_is_allowed,
    run_git_push_subprocess_command,
    run_repo_push,
)
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _git_dir(git_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "--git-dir", str(git_dir), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


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
    _git(repo, "remote", "add", "fuzzy", bare.as_uri())
    return repo, bare, _git(repo, "branch", "--show-current"), _git(repo, "rev-parse", "HEAD")


def _registry(
    *,
    remote_name: str = "fuzzy",
    push_policy: str = "push_allowed",
    remote_url: str | None = None,
) -> RepoRegistry:
    registry = RepoRegistry()
    remotes = [
        RepoRemote.create(
            name=remote_name,
            url=remote_url or f"https://github.com/fuzzy123-ai/demo-{remote_name}.git",
            purpose="fork" if remote_name == "fuzzy" else "origin",
            push_policy=push_policy,
        )
    ]
    if not any(remote.push_policy == "push_allowed" for remote in remotes):
        remotes.append(
            RepoRemote.create(
                name="fuzzy",
                url="https://github.com/fuzzy123-ai/demo-fuzzy.git",
                purpose="fork",
                push_policy="push_allowed",
            )
        )
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
            remotes=remotes,
            allowed_actions=["status", "push_plan", "push"],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


def _push_gates(repo: Path, *, remote: str, branch: str, sha: str) -> dict:
    binding = repository_binding(repo)
    receipt = build_verification_receipt(
        {
            "lane": "guards-only",
            "strongest_evidence_level": "static",
            "checks": [
                {
                    "check_id": "current_check",
                    "required": True,
                    "status": "passed",
                    "evidence_level": "static",
                }
            ],
            "verification_limits": ["live_not_verified"],
        },
        binding_before=binding,
        binding_after=binding,
    )
    completion = AgentMaintenanceCompletionEvidence(
        receipt=receipt,
        claim_report=ClaimEvidenceReport(()),
        expected_lane="guards-only",
        required_evidence_level="static",
        claim_ownership=AgentMaintenanceClaimOwnership(
            expected_claim_id="AMH-06",
            expected_owner="bob",
            allowed_paths=("README.md",),
            current_claim_id="AMH-06",
            current_owner="bob",
            current_changed_paths=(),
            current_staged_paths=(),
        ),
    )
    authority = build_repo_push_authority(
        repo_id="demo",
        remote_name=remote,
        branch_name=branch,
        commit_sha=sha,
        granted=True,
    )
    return {"completion_evidence": completion, "push_authority": authority}


def test_push_plan_blocks_without_live_gates(tmp_path: Path):
    _repo, _bare, branch, head = _make_push_repo(tmp_path)

    report = run_repo_push(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        remote_name="fuzzy",
        branch_name=branch,
        commit_sha=head,
        confirmed=False,
        operator_go=False,
        live_enabled=False,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert "confirmed=true is required" in report.blockers[0]
    assert "operator_go=true is required" in report.blockers[1]
    assert "live_enabled=true" in report.blockers[2]
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_push_runner_pushes_current_branch_to_policy_allowed_remote(tmp_path: Path):
    repo, bare, branch, head = _make_push_repo(tmp_path)

    report = run_repo_push(
        registry=_registry(remote_url=bare.as_uri()),
        repo_id="demo",
        workspace_base=tmp_path,
        remote_name="fuzzy",
        branch_name=branch,
        commit_sha=head,
        confirmed=True,
        operator_go=True,
        live_enabled=True,
        **_push_gates(repo, remote="fuzzy", branch=branch, sha=head),
    )

    assert report.status == "pushed"
    assert report.executed is True
    assert report.pushed_ref == f"fuzzy/{branch}@{head.lower()}"
    assert f"{head}:refs/heads/{branch}" in report.plan.planned_steps[-1]["summary"]
    assert _git_dir(bare, "rev-parse", f"refs/heads/{branch}") == head
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_push_runner_uses_remote_policy_for_origin_and_protected_branch(tmp_path: Path):
    _repo, _bare, branch, head = _make_push_repo(tmp_path)

    origin_report = run_repo_push(
        registry=_registry(remote_name="origin", push_policy="read_only"),
        repo_id="demo",
        workspace_base=tmp_path,
        remote_name="origin",
        branch_name=branch,
        commit_sha=head,
        confirmed=True,
        operator_go=True,
        live_enabled=True,
    )
    protected_plan = build_repo_push_plan(
        record=_registry().get("demo"),
        remote_name="fuzzy",
        branch_name="main",
        commit_sha=head,
        actual_branch="main",
        actual_commit_sha=head,
        status_output="## main\n",
        confirmed=True,
        operator_go=True,
        live_enabled=True,
    )

    assert origin_report.status == "blocked"
    assert "push_policy is `read_only`" in origin_report.blockers[0]
    assert protected_plan.decision == "hold"
    assert "protected" in protected_plan.blockers[0]


def test_push_runner_blocks_branch_or_sha_mismatch(tmp_path: Path):
    _repo, _bare, branch, _head = _make_push_repo(tmp_path)

    wrong_branch = run_repo_push(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        remote_name="fuzzy",
        branch_name="codex/demo/other",
        commit_sha=_git(tmp_path / "repos" / "demo", "rev-parse", "HEAD"),
        confirmed=True,
        operator_go=True,
        live_enabled=True,
    )
    wrong_sha = run_repo_push(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        remote_name="fuzzy",
        branch_name=branch,
        commit_sha="d" * 40,
        confirmed=True,
        operator_go=True,
        live_enabled=True,
    )

    assert wrong_branch.status == "blocked"
    assert any("does not match current branch" in blocker for blocker in wrong_branch.blockers)
    assert wrong_sha.status == "blocked"
    assert any("does not match current HEAD" in blocker for blocker in wrong_sha.blockers)


def test_push_runner_rejects_bad_inputs_and_commands(tmp_path: Path):
    _repo, _bare, _branch, head = _make_push_repo(tmp_path)

    with pytest.raises(RepoPushRunnerError, match="remote_name"):
        build_repo_push_plan(
            record=_registry().get("demo"),
            remote_name="../origin",
            branch_name="codex/demo/work",
            commit_sha=head,
            actual_branch="codex/demo/work",
            actual_commit_sha=head,
            status_output="## codex/demo/work\n",
            confirmed=True,
            operator_go=True,
            live_enabled=True,
        )

    with pytest.raises(RepoPushRunnerError, match="full Git object id"):
        build_repo_push_plan(
            record=_registry().get("demo"),
            remote_name="fuzzy",
            branch_name="codex/demo/work",
            commit_sha="not-a-hash",
            actual_branch="codex/demo/work",
            actual_commit_sha=head,
            status_output="## codex/demo/work\n",
            confirmed=True,
            operator_go=True,
            live_enabled=True,
        )

    assert repo_push_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert repo_push_command_is_allowed(("git", "branch", "--show-current")) is True
    assert repo_push_command_is_allowed(("git", "rev-parse", "HEAD")) is True
    assert repo_push_command_is_allowed(("git", "push", "fuzzy", "codex/demo/work")) is False
    assert repo_push_command_is_allowed(("git", "push", "--force", "fuzzy", "codex/demo/work")) is False
    assert repo_push_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_provider_runtime():
    source = Path("src/repo_push_runner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared")
    for fragment in forbidden:
        assert fragment not in source


def test_forge_transport_commands_bind_full_sha_branch_and_remote_without_force():
    sha = "a" * 40
    commands = build_repo_forge_git_transport_commands(
        remote_name="fuzzy",
        branch_name="odysseus/version-sync",
        commit_sha=sha,
    )

    assert commands.verify_commit == ("git", "rev-parse", "--verify", f"{sha}^{{commit}}")
    assert commands.remote_url == ("git", "remote", "get-url", "--push", "--all", "fuzzy")
    assert commands.remote_ref == (
        "git",
        "ls-remote",
        "--heads",
        "fuzzy",
        "refs/heads/odysseus/version-sync",
    )
    assert commands.push == (
        "git",
        "push",
        "fuzzy",
        f"{sha}:refs/heads/odysseus/version-sync",
    )
    assert all(repo_push_command_is_allowed(argv) for argv in (
        commands.verify_commit,
        commands.remote_url,
        commands.remote_ref,
        commands.push,
    ))
    assert not any("force" in argument for argument in commands.push)
    assert build_repo_forge_ancestry_command(
        ancestor_sha="b" * 40,
        descendant_sha=sha,
    ) == ("git", "merge-base", "--is-ancestor", "b" * 40, sha)
    assert repo_push_command_is_allowed(("git", "push", "fuzzy", "refs/tags/v1")) is False
    assert repo_push_command_is_allowed(("git", "push", "fuzzy", "+main")) is False


def test_remote_head_parser_is_exact_and_rejects_ambiguous_or_wrong_refs():
    sha = "b" * 40
    assert parse_repo_remote_head_sha("", branch_name="odysseus/version-sync") is None
    assert parse_repo_remote_head_sha(
        f"{sha}\trefs/heads/odysseus/version-sync\n",
        branch_name="odysseus/version-sync",
    ) == sha

    with pytest.raises(RepoPushRunnerError, match="unexpected ref"):
        parse_repo_remote_head_sha(
            f"{sha}\trefs/heads/other\n",
            branch_name="odysseus/version-sync",
        )
    with pytest.raises(RepoPushRunnerError, match="ambiguous"):
        parse_repo_remote_head_sha(
            f"{sha}\trefs/heads/odysseus/version-sync\n{sha}\trefs/heads/other\n",
            branch_name="odysseus/version-sync",
        )
    assert repo_push_command_is_allowed(("git", "push", "fuzzy", "deadbee:refs/heads/work")) is False
