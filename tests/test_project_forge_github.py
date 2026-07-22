from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.project_forge_github import (
    GitHubForgeReceiptStore,
    GitHubForgeSyncAdapter,
    GitHubForgeTarget,
)
from src.project_forge_sync import ForgeSyncRequest
from src.project_version_store import owner_key_for
from src.repo_push_runner import (
    RepoPushCommandResult,
    build_repo_forge_git_transport_commands,
)
from src.repo_registry import RepoRecord, RepoRemote


OPERATION = "pfo_" + "a" * 32
TRANSACTION = "pct_" + "b" * 32
VERSION = "pv_" + "c" * 32
COMMIT = "d" * 40
BRANCH = "odysseus/version-sync"
REMOTE = "fuzzy"
REMOTE_URL = "https://github.com/fuzzy123-ai/readable-project.git"
OWNER = "fuzzy123-ai"
OWNER_KEY = owner_key_for(OWNER)
TRANSPORT_COMMANDS = build_repo_forge_git_transport_commands(
    remote_name=REMOTE,
    branch_name=BRANCH,
    commit_sha=COMMIT,
    push_target_url=REMOTE_URL,
)
PUSH_COMMAND = TRANSPORT_COMMANDS.push
REMOTE_REF_COMMAND = TRANSPORT_COMMANDS.remote_ref
URL_REWRITE_COMMAND = TRANSPORT_COMMANDS.url_rewrites


def _request(*, provider: str = "github") -> ForgeSyncRequest:
    return ForgeSyncRequest(
        provider=provider,
        owner_key=OWNER_KEY,
        operation_id=OPERATION,
        idempotency_key=OPERATION,
        repo_id="readable-project",
        transaction_id=TRANSACTION,
        version_id=VERSION,
        commit_sha=COMMIT,
        manifest_evidence={
            "schema": "odysseus.project_version_manifest.v1",
            "sha256": "sha256:" + "e" * 64,
            "reference": f"version:{VERSION}",
        },
        expected_fingerprint="sha256:" + "f" * 64,
    )


def _record(
    *,
    remote_url: str = REMOTE_URL,
    remote_name: str = REMOTE,
    branch: str = BRANCH,
    push_policy: str = "push_allowed",
    provider_scope: str = "external_allowed",
    privacy_class: str = "private",
    allowed_actions: tuple[str, ...] | None = None,
) -> RepoRecord:
    return RepoRecord.create(
        repo_id="readable-project",
        title="Readable Project",
        repo_kind="project",
        owner=OWNER,
        path_ref="projects/readable-project",
        workspace_root="projects",
        project_root="projects/readable-project",
        default_branch="main",
        current_branch=branch,
        remotes=(
            RepoRemote.create(
                name=remote_name,
                url=remote_url,
                purpose="backup",
                push_policy=push_policy,
            ),
        ),
        privacy_class=privacy_class,
        provider_scope=provider_scope,
        allowed_actions=(
            allowed_actions
            if allowed_actions is not None
            else (("status", "push_plan", "push") if push_policy == "push_allowed" else ("status", "push_plan"))
        ),
        created_at="2026-07-13T12:00:00Z",
    )


def _target(repo_path: Path, **overrides) -> GitHubForgeTarget:
    values = {
        "record": _record(),
        "owner_key": OWNER_KEY,
        "repo_path": repo_path,
        "trusted_root": repo_path.parents[1],
        "remote_name": REMOTE,
        "branch_name": BRANCH,
        "auth_ready": True,
        "confirmed": True,
        "operator_go": True,
        "live_enabled": True,
    }
    values.update(overrides)
    return GitHubForgeTarget(**values)


def _repo_path(tmp_path: Path) -> Path:
    repo_path = (tmp_path / "projects" / "readable-project").resolve()
    (repo_path / ".git").mkdir(parents=True)
    return repo_path


class FakeTargetSource:
    def __init__(self, target):
        self.target = target
        self.calls = []

    def load_target(self, **identifiers):
        self.calls.append(identifiers)
        if isinstance(self.target, BaseException):
            raise self.target
        return self.target


class FakeGitRunner:
    def __init__(
        self,
        *,
        remote_refs=("", f"{COMMIT}\trefs/heads/{BRANCH}\n"),
        remote_url=REMOTE_URL,
        fail_kind="",
        raise_kind="",
        ancestry_exit_code=1,
        rewrite_results=None,
    ):
        self.remote_refs = list(remote_refs)
        self.remote_urls = list(
            remote_url
            if isinstance(remote_url, (tuple, list))
            else (remote_url, remote_url)
        )
        self.fail_kind = fail_kind
        self.raise_kind = raise_kind
        self.ancestry_exit_code = ancestry_exit_code
        self.rewrite_results = list(
            rewrite_results
            if rewrite_results is not None
            else (RepoPushCommandResult(exit_code=1), RepoPushCommandResult(exit_code=1))
        )
        self.calls = []

    def __call__(self, argv, *, cwd, timeout_seconds, env):
        self.calls.append((argv, cwd, timeout_seconds, dict(env)))
        kind = self._kind(argv)
        if kind == self.raise_kind:
            raise RuntimeError("Bearer not-a-real-secret C:/private/repo")
        if kind == self.fail_kind:
            return RepoPushCommandResult(exit_code=1, stderr="token=not-a-real-value")
        if kind == "verify":
            return RepoPushCommandResult(exit_code=0, stdout=COMMIT + "\n")
        if kind == "url":
            return RepoPushCommandResult(exit_code=0, stdout=self.remote_urls.pop(0) + "\n")
        if kind == "url_rewrites":
            return self.rewrite_results.pop(0)
        if kind == "remote_ref":
            return RepoPushCommandResult(exit_code=0, stdout=self.remote_refs.pop(0))
        if kind == "push":
            return RepoPushCommandResult(exit_code=0, stdout="pushed")
        if kind == "ancestry":
            return RepoPushCommandResult(exit_code=self.ancestry_exit_code)
        raise AssertionError(f"unexpected command: {argv}")

    @staticmethod
    def _kind(argv):
        if argv[:3] == ("git", "rev-parse", "--verify"):
            return "verify"
        if argv[:4] == ("git", "remote", "get-url", "--push"):
            return "url"
        if tuple(argv) == URL_REWRITE_COMMAND:
            return "url_rewrites"
        if tuple(argv) == REMOTE_REF_COMMAND:
            return "remote_ref"
        if tuple(argv) == PUSH_COMMAND:
            return "push"
        if argv[:3] == ("git", "merge-base", "--is-ancestor"):
            return "ancestry"
        return "unknown"


def _adapter(tmp_path, target, runner):
    source = FakeTargetSource(target)
    store = GitHubForgeReceiptStore(root=tmp_path / "receipts")
    return GitHubForgeSyncAdapter(
        target_source=source,
        receipt_store=store,
        command_runner=runner,
    ), source, store


def _commands(runner):
    return [call[0] for call in runner.calls]


def test_missing_remote_ref_pushes_exact_commit_then_persists_redacted_receipt(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner()
    adapter, source, store = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "synced"
    assert outcome.provider_fingerprint.startswith("sha256:")
    assert _commands(runner) == [
        ("git", "rev-parse", "--verify", f"{COMMIT}^{{commit}}"),
        ("git", "remote", "get-url", "--push", "--all", REMOTE),
        URL_REWRITE_COMMAND,
        REMOTE_REF_COMMAND,
        ("git", "remote", "get-url", "--push", "--all", REMOTE),
        URL_REWRITE_COMMAND,
        PUSH_COMMAND,
        REMOTE_REF_COMMAND,
    ]
    assert not any("HEAD" in command for command in _commands(runner))
    assert all("--force" not in command for command in _commands(runner))
    assert all(not any(argument.startswith("+") for argument in command) for command in _commands(runner))
    receipt = store.load(owner_key=OWNER_KEY, repo_id="readable-project", operation_id=OPERATION)
    assert receipt is not None
    assert receipt.remote_sha == COMMIT
    assert receipt.expected_fingerprint == _request().expected_fingerprint
    assert receipt.remote_identity == "fuzzy123-ai/readable-project"
    raw = next((tmp_path / "receipts").rglob("*.json")).read_text(encoding="utf-8")
    assert "github.com" not in raw
    assert str(tmp_path) not in raw
    assert "token" not in raw.casefold()
    assert source.calls == [{
        "owner_key": OWNER_KEY,
        "operation_id": OPERATION,
        "repo_id": "readable-project",
        "transaction_id": TRANSACTION,
        "version_id": VERSION,
    }]


def test_receipt_replay_is_already_synced_without_target_or_git(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner()
    first, _, store = _adapter(tmp_path, _target(repo_path), runner)
    assert first.sync(_request()).status == "synced"
    source = FakeTargetSource(RuntimeError("must not load"))

    replay = GitHubForgeSyncAdapter(
        target_source=source,
        receipt_store=store,
        command_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    ).sync(_request())

    assert replay.status == "already_synced"
    assert source.calls == []


def test_same_remote_sha_is_already_synced_without_push_and_creates_receipt(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner(remote_refs=(f"{COMMIT}\trefs/heads/{BRANCH}\n",))
    adapter, _, store = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "already_synced"
    assert not any(FakeGitRunner._kind(command) == "push" for command in _commands(runner))
    assert store.load(owner_key=OWNER_KEY, repo_id="readable-project", operation_id=OPERATION) is not None


def test_different_remote_sha_is_diverged_without_push_or_receipt(tmp_path):
    repo_path = _repo_path(tmp_path)
    different = "1" * 40
    runner = FakeGitRunner(remote_refs=(f"{different}\trefs/heads/{BRANCH}\n",))
    adapter, _, store = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "diverged"
    assert outcome.provider_fingerprint.startswith("sha256:")
    assert not any(FakeGitRunner._kind(command) == "push" for command in _commands(runner))
    assert store.load(owner_key=OWNER_KEY, repo_id="readable-project", operation_id=OPERATION) is None


def test_remote_ancestor_allows_normal_non_force_followup_commit(tmp_path):
    repo_path = _repo_path(tmp_path)
    previous = "1" * 40
    runner = FakeGitRunner(
        remote_refs=(
            f"{previous}\trefs/heads/{BRANCH}\n",
            f"{COMMIT}\trefs/heads/{BRANCH}\n",
        ),
        ancestry_exit_code=0,
    )
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "synced"
    commands = _commands(runner)
    assert ("git", "merge-base", "--is-ancestor", previous, COMMIT) in commands
    assert PUSH_COMMAND in commands


@pytest.mark.parametrize(
    "target_overrides",
    (
        {"auth_ready": False},
        {"confirmed": False},
        {"operator_go": False},
        {"live_enabled": False},
        {"branch_name": "main"},
        {"record": _record(push_policy="read_only")},
        {"record": _record(provider_scope="local_only")},
    ),
)
def test_target_auth_live_and_branch_policy_fail_closed_before_git(tmp_path, target_overrides):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner()
    adapter, _, _ = _adapter(tmp_path, _target(repo_path, **target_overrides), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code == "target_policy_blocked"
    assert runner.calls == []


def test_default_missing_runner_blocks_without_transport_execution(tmp_path):
    repo_path = _repo_path(tmp_path)
    source = FakeTargetSource(_target(repo_path))
    adapter = GitHubForgeSyncAdapter(
        target_source=source,
        receipt_store=GitHubForgeReceiptStore(root=tmp_path / "receipts"),
    )

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code == "runner_not_configured"


@pytest.mark.parametrize(
    "actual_url",
    (
        "https://github.com/other/project.git",
        "https://oauth@github.com/fuzzy123-ai/readable-project.git",
        "https://gitlab.com/fuzzy123-ai/readable-project.git",
    ),
)
def test_actual_remote_url_must_match_configured_github_identity(tmp_path, actual_url):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner(remote_url=actual_url)
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code in {"remote_identity_invalid", "remote_identity_mismatch"}
    assert not any(FakeGitRunner._kind(command) == "push" for command in _commands(runner))


def test_multiple_push_urls_are_blocked_before_remote_ref_or_push(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner(remote_url=REMOTE_URL + "\nhttps://github.com/other/target.git")
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code == "remote_identity_ambiguous"
    assert not any(FakeGitRunner._kind(command) == "push" for command in _commands(runner))


def test_remote_identity_swap_after_first_readback_blocks_before_push(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner(
        remote_url=(REMOTE_URL, "https://github.com/other/target.git"),
    )
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code == "remote_identity_mismatch"
    assert len([command for command in _commands(runner) if FakeGitRunner._kind(command) == "url"]) == 2
    assert not any(FakeGitRunner._kind(command) == "push" for command in _commands(runner))


@pytest.mark.parametrize("blocked_check", (0, 1))
def test_url_rewrite_config_blocks_without_transport_and_leaks_no_raw_output(tmp_path, blocked_check):
    repo_path = _repo_path(tmp_path)
    malicious = RepoPushCommandResult(
        exit_code=0,
        stdout="url.https://synthetic-secret.invalid/.insteadOf\0",
    )
    rewrite_results = [RepoPushCommandResult(exit_code=1), RepoPushCommandResult(exit_code=1)]
    rewrite_results[blocked_check] = malicious
    runner = FakeGitRunner(rewrite_results=rewrite_results)
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())
    commands = _commands(runner)
    dumped = json.dumps(outcome.to_dict(), sort_keys=True)

    assert outcome.status == "blocked"
    assert outcome.error_code == "url_rewrite_config_blocked"
    assert not any(FakeGitRunner._kind(command) == "push" for command in commands)
    if blocked_check == 0:
        assert not any(FakeGitRunner._kind(command) == "remote_ref" for command in commands)
    else:
        assert commands[-1] == URL_REWRITE_COMMAND
        assert commands.count(URL_REWRITE_COMMAND) == 2
    assert "synthetic-secret" not in dumped


@pytest.mark.parametrize(
    "unsafe_result",
    (
        RepoPushCommandResult(exit_code=1, stdout="unexpected"),
        RepoPushCommandResult(exit_code=1, stderr="unexpected"),
        RepoPushCommandResult(exit_code=2),
        RepoPushCommandResult(exit_code=1, timed_out=True),
    ),
)
def test_url_rewrite_check_accepts_only_exact_empty_no_match(tmp_path, unsafe_result):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner(rewrite_results=(unsafe_result,))
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code == "url_rewrite_config_blocked"
    assert _commands(runner)[-1] == URL_REWRITE_COMMAND
    assert not any(FakeGitRunner._kind(command) == "remote_ref" for command in _commands(runner))


@pytest.mark.parametrize(
    "remote_url",
    (
        "ssh://git@github.com/fuzzy123-ai/readable-project.git",
        "git@github.com:fuzzy123-ai/readable-project.git",
    ),
)
def test_ssh_remote_scheme_is_blocked_before_git(tmp_path, remote_url):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner()
    target = _target(
        repo_path,
        record=_record(remote_url=remote_url),
    )
    adapter, _, _ = _adapter(tmp_path, target, runner)

    outcome = adapter.sync(_request())

    assert outcome.status == "blocked"
    assert outcome.error_code == "target_policy_blocked"
    assert runner.calls == []


def test_gitfile_and_owner_mismatch_are_blocked_before_git(tmp_path):
    repo_path = _repo_path(tmp_path)
    (repo_path / ".git").rmdir()
    (repo_path / ".git").write_text("gitdir: ../../outside.git\n", encoding="utf-8")
    runner = FakeGitRunner()
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)
    assert adapter.sync(_request()).error_code == "target_policy_blocked"
    assert runner.calls == []

    safe_repo = (tmp_path / "other" / "projects" / "readable-project").resolve()
    (safe_repo / ".git").mkdir(parents=True)
    mismatched = _target(
        safe_repo,
        trusted_root=safe_repo.parents[1],
        owner_key=owner_key_for("other-owner"),
    )
    adapter, _, _ = _adapter(tmp_path / "other-store", mismatched, FakeGitRunner())
    assert adapter.sync(_request()).error_code == "target_policy_blocked"


def test_retry_uses_bound_commit_even_when_checkout_head_or_branch_has_moved(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner()
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    assert adapter.sync(_request()).status == "synced"
    commands = _commands(runner)
    assert commands[0] == ("git", "rev-parse", "--verify", f"{COMMIT}^{{commit}}")
    assert all(command != ("git", "rev-parse", "HEAD") for command in commands)
    assert all(command != ("git", "branch", "--show-current") for command in commands)


@pytest.mark.parametrize(
    ("runner", "expected_status", "expected_code"),
    (
        (FakeGitRunner(fail_kind="verify"), "blocked", "commit_unavailable"),
        (FakeGitRunner(fail_kind="remote_ref"), "retryable_failure", "remote_check_failed"),
        (FakeGitRunner(fail_kind="push"), "retryable_failure", "push_failed"),
        (FakeGitRunner(remote_refs=("", "")), "retryable_failure", "remote_verify_failed"),
        (
            FakeGitRunner(remote_refs=("", f"{'2' * 40}\trefs/heads/{BRANCH}\n")),
            "diverged",
            "",
        ),
    ),
)
def test_transport_failures_are_strict_redacted_outcomes(tmp_path, runner, expected_status, expected_code):
    repo_path = _repo_path(tmp_path)
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request())

    assert outcome.status == expected_status
    assert outcome.error_code == expected_code
    dumped = json.dumps(outcome.to_dict())
    assert "token" not in dumped.casefold()
    assert str(tmp_path) not in dumped
    assert "github.com" not in dumped


def test_malformed_remote_ref_and_tampered_receipt_fail_closed(tmp_path):
    repo_path = _repo_path(tmp_path)
    malformed = FakeGitRunner(remote_refs=(f"{COMMIT}\trefs/tags/v1\n",))
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), malformed)
    assert adapter.sync(_request()).error_code == "remote_ref_invalid"

    runner = FakeGitRunner()
    adapter, _, _ = _adapter(tmp_path, _target(repo_path), runner)
    assert adapter.sync(_request()).status == "synced"
    receipt_path = next((tmp_path / "receipts").rglob("*.json"))
    envelope = json.loads(receipt_path.read_text(encoding="utf-8"))
    envelope["payload"]["commit_sha"] = "3" * 40
    receipt_path.write_text(json.dumps(envelope), encoding="utf-8")
    replay, _, _ = _adapter(tmp_path, _target(repo_path), FakeGitRunner())
    outcome = replay.sync(_request())
    assert outcome.status == "permanent_failure"
    assert outcome.error_code == "receipt_invalid"


def test_wrong_provider_is_blocked_before_target_receipt_or_git(tmp_path):
    repo_path = _repo_path(tmp_path)
    runner = FakeGitRunner()
    adapter, source, _ = _adapter(tmp_path, _target(repo_path), runner)

    outcome = adapter.sync(_request(provider="nextcloud"))

    assert outcome.status == "blocked"
    assert outcome.error_code == "provider_mismatch"
    assert source.calls == []
    assert runner.calls == []
