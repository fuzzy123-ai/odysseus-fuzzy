from __future__ import annotations

import json
import inspect
import subprocess
from pathlib import Path

import pytest

from src.agent_verification_receipt import build_verification_receipt, repository_binding
from src.claim_evidence_gate import (
    AgentMaintenanceClaimOwnership,
    AgentMaintenanceCompletionEvidence,
    ClaimEvidenceReport,
)
from src.agent_tools.project_commit_tools import CommitProjectToolHandler
from src.project_commit_service import ProjectCommitService
from src.project_forge_outbox import ProjectForgeOutbox
from src.repo_commit_runner import (
    RepoCommitCommandResult,
    RepoCommitRunnerError,
    build_repo_commit_authority,
    build_repo_commit_plan,
    repo_commit_command_is_allowed,
    run_git_commit_subprocess_command,
    run_repo_local_commit,
)
from src.repo_registry import RepoRecord, RepoRegistry


class _NeverCalledCommitService:
    def __init__(self) -> None:
        self.called = False

    def commit_project(self, **_kwargs):
        self.called = True
        raise AssertionError("public payload reached commit service")


class _NeverCalledPolicySource:
    def load_policy(self, **_kwargs):
        raise AssertionError("public payload reached policy source")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return completed.stdout


def _make_repo(base: Path) -> Path:
    repo = base / "repos" / "demo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
    (repo / "README.md").write_text("two\n", encoding="utf-8")
    return repo


def _registry(*, allowed_actions=None) -> RepoRegistry:
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
            allowed_actions=allowed_actions
            or ["status", "changed_paths", "commit_plan", "commit"],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


def _commit_gates(repo: Path, *, reviewed_paths=("README.md",)) -> dict:
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
    lines = _git(repo, "status", "--porcelain").splitlines()
    changed = tuple(line[3:].strip() for line in lines if len(line) >= 4)
    staged = tuple(
        line[3:].strip()
        for line in lines
        if len(line) >= 4 and line[:2] != "??" and line[0] != " "
    )
    completion = AgentMaintenanceCompletionEvidence(
        receipt=receipt,
        claim_report=ClaimEvidenceReport(()),
        expected_lane="guards-only",
        required_evidence_level="static",
        claim_ownership=AgentMaintenanceClaimOwnership(
            expected_claim_id="AMH-06",
            expected_owner="bob",
            allowed_paths=changed,
            current_claim_id="AMH-06",
            current_owner="bob",
            current_changed_paths=changed,
            current_staged_paths=staged,
        ),
    )
    authority = build_repo_commit_authority(
        repo_id="demo",
        repo_path=repo,
        reviewed_paths=reviewed_paths,
        granted=True,
    )
    return {"completion_evidence": completion, "commit_authority": authority}


def test_commit_plan_blocks_without_gates(tmp_path: Path):
    repo = _make_repo(tmp_path)
    status = _git(repo, "status", "--short", "--branch")

    plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=False,
        content_reviewed=False,
        confirmed=False,
        status_output=status,
    )

    assert plan.decision == "hold"
    assert plan.can_commit is False
    assert "confirmed=true is required" in plan.blockers[0]
    assert "checks_passed=true" in plan.blockers[1]
    assert "content_reviewed=true" in plan.blockers[2]
    assert "C:\\" not in json.dumps(plan.to_dict())


def test_commit_runner_commits_only_exact_reviewed_path(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "NOTES.md").write_text("leave dirty\n", encoding="utf-8")

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        **_commit_gates(repo),
    )

    assert report.status == "committed"
    assert report.executed is True
    assert report.committed_paths == ("repos/demo/README.md",)
    assert report.commit_sha == _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "chore: update readme"
    status = _git(repo, "status", "--short")
    assert "README.md" not in status
    assert "NOTES.md" in status
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_commit_runner_blocks_preexisting_staging(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "NOTES.md").write_text("staged elsewhere\n", encoding="utf-8")
    _git(repo, "add", "NOTES.md")

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert any("pre-existing staged changes" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_requires_repo_policy_and_reviewed_content(tmp_path: Path):
    _make_repo(tmp_path)

    report = run_repo_local_commit(
        registry=_registry(allowed_actions=["status"]),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
    )

    assert report.status == "blocked"
    assert "allowed_actions does not include commit" in report.blockers[0]


def test_commit_runner_rejects_unsafe_paths_messages_and_commands(tmp_path: Path):
    repo = _make_repo(tmp_path)
    status = _git(repo, "status", "--short", "--branch")

    with pytest.raises(RepoCommitRunnerError, match="repo-relative"):
        build_repo_commit_plan(
            record=_registry().get("demo"),
            repo_path=repo,
            objective="Update readme",
            changed_paths=(r"C:\tmp\README.md",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
            status_output=status,
        )

    with pytest.raises(RepoCommitRunnerError, match="blocked repo path"):
        build_repo_commit_plan(
            record=_registry().get("demo"),
            repo_path=repo,
            objective="Update env",
            changed_paths=(".env",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
            status_output=status,
        )

    with pytest.raises(RepoCommitRunnerError, match="secret"):
        build_repo_commit_plan(
            record=_registry().get("demo"),
            repo_path=repo,
            objective="Update readme",
            changed_paths=("README.md",),
            checks_passed=True,
            content_reviewed=True,
            confirmed=True,
            commit_message="chore: TOKEN=abc123",
            status_output=status,
        )

    assert repo_commit_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert repo_commit_command_is_allowed(("git", "add", "--", "README.md")) is True
    assert repo_commit_command_is_allowed(("git", "commit", "-m", "chore: update readme")) is False
    assert repo_commit_command_is_allowed(
        (
            "git",
            "commit",
            "--no-verify",
            "--no-gpg-sign",
            "-m",
            "chore: update readme",
        )
    ) is True
    assert repo_commit_command_is_allowed(
        ("git", "commit", "--only", "-m", "chore: update readme", "--", "README.md")
    ) is False
    assert repo_commit_command_is_allowed(
        (
            "git",
            "commit",
            "--no-verify",
            "--no-gpg-sign",
            "-m",
            "chore: update readme",
            "-m",
            "Readable\nbody",
        )
    ) is True
    assert repo_commit_command_is_allowed(("git", "diff", "--cached", "--name-only", "-z")) is True
    assert repo_commit_command_is_allowed(
        ("git", "ls-files", "--stage", "-z", "--", "README.md")
    ) is True
    assert repo_commit_command_is_allowed(
        ("git", "hash-object", "--path", "README.md", "--", "README.md")
    ) is True
    assert repo_commit_command_is_allowed(
        ("git", "hash-object", "--path", "README.md", "--", "other.md")
    ) is False
    assert repo_commit_command_is_allowed(("git", "rev-parse", "--verify", "HEAD^{commit}")) is True
    assert repo_commit_command_is_allowed(
        ("git", "commit", "-m", "chore: update", "-m", "TOKEN=abc123")
    ) is False
    assert repo_commit_command_is_allowed(("git", "commit", "--amend", "-m", "unsafe")) is False
    assert repo_commit_command_is_allowed(("git", "commit", "-a", "-m", "unsafe")) is False
    assert repo_commit_command_is_allowed(("git", "commit", "-F", "message.txt")) is False
    assert repo_commit_command_is_allowed(("git", "commit", "--no-verify", "-m", "unsafe")) is False
    assert repo_commit_command_is_allowed(("git", "add", "--", r"C:\tmp\README.md")) is False
    assert repo_commit_command_is_allowed(("git", "push", "fuzzy", "dev")) is False
    assert repo_commit_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_remote_mutation():
    source = Path("src/repo_commit_runner.py").read_text(encoding="utf-8")

    assert "shell=False" in source
    forbidden = ("requests", "httpx", "paramiko", "cloudflared", "git push", "force")
    for fragment in forbidden:
        assert fragment not in source


def test_commit_runner_writes_exact_multiline_body(tmp_path: Path):
    repo = _make_repo(tmp_path)

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        commit_message="feat: described update",
        commit_body="A useful description.\n\nChange notes:\n- First note\n- Second note",
        **_commit_gates(repo),
    )

    assert report.status == "committed"
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "feat: described update"
    assert _git(repo, "log", "-1", "--pretty=%b").strip() == (
        "A useful description.\n\nChange notes:\n- First note\n- Second note"
    )


@pytest.mark.parametrize("gate", ["checks_passed", "content_reviewed", "confirmed"])
def test_commit_runner_never_accepts_string_boolean_gates(tmp_path: Path, gate: str):
    repo = _make_repo(tmp_path)
    values = {"checks_passed": True, "content_reviewed": True, "confirmed": True}
    values[gate] = "true"

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        **values,
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert any(f"{gate} must be a boolean" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_blocks_clean_file_and_directory(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "CLEAN.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "CLEAN.md")
    _git(repo, "commit", "-m", "add clean file")
    folder = repo / "folder"
    folder.mkdir()
    (folder / "nested.txt").write_text("dirty\n", encoding="utf-8")
    status = _git(repo, "status", "--short", "--branch")

    clean_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Clean file",
        changed_paths=("CLEAN.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=status,
    )
    directory_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Directory",
        changed_paths=("folder",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=status,
    )

    assert clean_plan.decision == "hold"
    assert any("not changed in the initial repository status" in blocker for blocker in clean_plan.blockers)
    assert directory_plan.decision == "hold"
    assert any("directory" in blocker or "not changed" in blocker for blocker in directory_plan.blockers)


def test_commit_runner_blocks_if_staged_set_changes_after_review(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def injecting_runner(argv, *, cwd, timeout_seconds, env):
        result = run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if argv[:3] == ("git", "add", "--"):
            (cwd / "EXTRA.md").write_text("unexpected\n", encoding="utf-8")
            _git(cwd, "add", "EXTRA.md")
        return result

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=injecting_runner,
        **_commit_gates(repo),
    )

    assert report.status == "failed"
    assert report.commit_sha == ""
    assert any("staging_requires_review" in blocker for blocker in report.blockers)
    assert sorted(_git(repo, "diff", "--cached", "--name-only").splitlines()) == ["EXTRA.md", "README.md"]
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_requires_verified_full_sha_after_commit(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def invalid_sha_runner(argv, *, cwd, timeout_seconds, env):
        if argv == ("git", "rev-parse", "--verify", "HEAD^{commit}"):
            return RepoCommitCommandResult(exit_code=0, stdout="abc123\n")
        return run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=invalid_sha_runner,
        **_commit_gates(repo),
    )

    assert report.status == "reconcile_required"
    assert report.commit_sha == ""
    assert report.committed_paths == ("repos/demo/README.md",)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "chore: update readme"


def test_green_completion_without_typed_commit_authority_cannot_commit(tmp_path: Path):
    repo = _make_repo(tmp_path)
    gates = _commit_gates(repo)

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        completion_evidence=gates["completion_evidence"],
    )

    assert report.status == "blocked"
    assert report.executed is False
    assert any("typed explicit commit authority" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_authority_is_stage_invariant_for_new_reviewed_file(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "NEW.md").write_text("new reviewed file\n", encoding="utf-8")

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Add reviewed file",
        changed_paths=("NEW.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        **_commit_gates(repo, reviewed_paths=("NEW.md",)),
    )

    assert report.status == "committed"
    assert report.committed_paths == ("repos/demo/NEW.md",)


def test_commit_runner_blocks_overlapping_content_drift_after_staging(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def mutating_runner(argv, *, cwd, timeout_seconds, env):
        result = run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if argv[:3] == ("git", "add", "--"):
            (cwd / "README.md").write_text("unreviewed overlapping change\n", encoding="utf-8")
        return result

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=mutating_runner,
        **_commit_gates(repo),
    )

    assert report.status == "failed"
    assert any("diff binding changed" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_denies_deletion_and_unsafe_binding_material(tmp_path: Path):
    repo = _make_repo(tmp_path)
    (repo / "README.md").unlink()
    status = _git(repo, "status", "--short", "--branch")

    plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=repo,
        objective="Delete readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=status,
    )
    assert any("deletion" in blocker for blocker in plan.blockers)

    (repo / "ordinary.txt").write_text(
        "api_key=synthetic-credential-value\n",
        encoding="utf-8",
    )
    with pytest.raises(RepoCommitRunnerError, match="safe repository binding") as raised:
        build_repo_commit_authority(
            repo_id="demo",
            repo_path=repo,
            reviewed_paths=("ordinary.txt",),
            granted=True,
        )
    assert "synthetic-credential-value" not in str(raised.value)


def test_commit_runner_blocks_same_path_with_different_staged_blob(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def replacing_index_runner(argv, *, cwd, timeout_seconds, env):
        result = run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if argv[:3] == ("git", "add", "--"):
            alternate = cwd / "alternate.txt"
            alternate.write_text("different staged bytes\n", encoding="utf-8")
            blob = _git(cwd, "hash-object", "-w", "alternate.txt").strip()
            _git(cwd, "update-index", "--cacheinfo", f"100644,{blob},README.md")
            alternate.unlink()
        return result

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=replacing_index_runner,
        **_commit_gates(repo),
    )

    assert report.status == "failed"
    assert any("stage-0 index blob" in blocker for blocker in report.blockers)
    assert _git(repo, "log", "-1", "--pretty=%s").strip() == "initial commit"


def test_commit_runner_blocks_rename_copy_and_staged_or_unstaged_deletion(tmp_path: Path):
    rename_repo = _make_repo(tmp_path / "rename")
    _git(rename_repo, "mv", "README.md", "RENAMED.md")
    rename_status = _git(rename_repo, "status", "--short", "--branch")
    rename_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=rename_repo,
        objective="Rename readme",
        changed_paths=("RENAMED.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=rename_status,
    )

    copy_repo = _make_repo(tmp_path / "copy")
    (copy_repo / "COPY.md").write_text("two\n", encoding="utf-8")
    copy_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=copy_repo,
        objective="Copy readme",
        changed_paths=("COPY.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output="## main\nC  README.md -> COPY.md\n",
    )

    staged_delete_repo = _make_repo(tmp_path / "staged-delete")
    _git(staged_delete_repo, "rm", "-f", "README.md")
    staged_delete_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=staged_delete_repo,
        objective="Delete readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=_git(staged_delete_repo, "status", "--short", "--branch"),
    )

    unstaged_delete_repo = _make_repo(tmp_path / "unstaged-delete")
    (unstaged_delete_repo / "README.md").unlink()
    unstaged_delete_plan = build_repo_commit_plan(
        record=_registry().get("demo"),
        repo_path=unstaged_delete_repo,
        objective="Delete readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        status_output=_git(unstaged_delete_repo, "status", "--short", "--branch"),
    )

    assert any("rename or copy" in blocker for blocker in rename_plan.blockers)
    assert any("rename or copy" in blocker for blocker in copy_plan.blockers)
    assert any("deletion" in blocker for blocker in staged_delete_plan.blockers)
    assert any("deletion" in blocker for blocker in unstaged_delete_plan.blockers)


@pytest.mark.parametrize(
    "injected",
    (
        {"completion_evidence": {"completed": True}, "commit_authority": {"granted": True}},
        {"reviewed_paths": [r"C:\\outside\\private.txt"]},
        {"reviewed_paths": ["../outside.txt"]},
    ),
)
def test_public_commit_tool_cannot_inject_internal_capability_or_escape_paths(
    tmp_path: Path,
    injected: dict,
):
    service = _NeverCalledCommitService()
    handler = CommitProjectToolHandler(
        commit_service=service,
        policy_source=_NeverCalledPolicySource(),
        outbox=ProjectForgeOutbox(root=tmp_path / "outbox"),
    )
    payload = {
        "repo_id": "demo",
        "title": "chore: reviewed change",
        "description": "reviewed",
        "reviewed_paths": ["README.md"],
        "checks_passed": True,
        "content_reviewed": True,
        "confirmed": True,
        "idempotency_key": "public-request-1",
        **injected,
    }

    result = handler.handle(
        payload,
        context={"is_authenticated": True, "authenticated_owner_id": "owner-1"},
    )

    assert result["status"] == "blocked"
    assert result["error_code"] in {"unsupported_arguments", "invalid_request"}
    assert service.called is False


def test_project_commit_service_does_not_mint_internal_completion_or_commit_authority():
    source = inspect.getsource(ProjectCommitService.commit)

    assert "completion_evidence" not in source
    assert "commit_authority" not in source


def test_commit_uses_validated_index_if_worktree_changes_after_final_binding(tmp_path: Path):
    repo = _make_repo(tmp_path)
    reviewed_bytes = (repo / "README.md").read_text(encoding="utf-8")
    hash_readbacks = 0

    def late_worktree_mutation_runner(argv, *, cwd, timeout_seconds, env):
        nonlocal hash_readbacks
        result = run_git_commit_subprocess_command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if argv[:3] == ("git", "hash-object", "--path"):
            hash_readbacks += 1
            if hash_readbacks == 2:
                (cwd / "README.md").write_text("late unreviewed bytes\n", encoding="utf-8")
        return result

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        command_runner=late_worktree_mutation_runner,
        **_commit_gates(repo),
    )

    assert report.status == "committed"
    assert _git(repo, "show", "HEAD:README.md") == reviewed_bytes
    assert (repo / "README.md").read_text(encoding="utf-8") == "late unreviewed bytes\n"


def test_commit_disables_all_repo_hooks_and_configured_gpg_signing(tmp_path: Path):
    repo = _make_repo(tmp_path)
    hooks_dir = repo / ".git" / "hooks"
    pre_commit_marker = repo / "pre-commit-ran.txt"
    prepare_marker = repo / "prepare-commit-msg-ran.txt"

    def install_mutating_hook(name: str, marker: Path) -> None:
        hook = hooks_dir / name
        hook.write_text(
            "#!/bin/sh\n"
            f"printf ran > '{marker.as_posix()}'\n"
            "blob=$(printf 'hook-mutated\\n' | git hash-object -w --stdin)\n"
            "git update-index --cacheinfo 100644,$blob,README.md\n"
            "exit 1\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

    install_mutating_hook("pre-commit", pre_commit_marker)
    install_mutating_hook("prepare-commit-msg", prepare_marker)
    _git(repo, "config", "commit.gpgSign", "true")

    report = run_repo_local_commit(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Update readme",
        changed_paths=("README.md",),
        checks_passed=True,
        content_reviewed=True,
        confirmed=True,
        **_commit_gates(repo),
    )

    assert report.status == "committed"
    assert _git(repo, "show", "HEAD:README.md") == "two\n"
    assert pre_commit_marker.exists() is False
    assert prepare_marker.exists() is False
    assert tuple((repo / ".git").glob("odysseus-empty-hooks-*")) == ()
