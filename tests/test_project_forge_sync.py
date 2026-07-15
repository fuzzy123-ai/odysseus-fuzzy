from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.project_forge_outbox import ProjectForgeOutbox
from src.project_forge_local import LocalProjectForge
from src.project_forge_policy import ProjectForgePolicy
from src.project_forge_sync import (
    ForgeSyncOutcome,
    ProjectForgeSyncCoordinator,
    ProjectForgeSyncError,
    ProjectForgeSyncEvidence,
    enqueue_from_commit_report,
    enqueue_sync_targets,
    reconcile_local_forge_outbox,
)


OWNER = "sync-owner@example.test"
REPO = "sync-project"
TRANSACTION = "pct_" + "a" * 32
VERSION = "pv_" + "b" * 32
COMMIT = "c" * 40
MANIFEST = {
    "schema": "odysseus.project_version_manifest.v1",
    "sha256": "sha256:" + "d" * 64,
    "reference": f"version:{VERSION}",
}


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class FakeAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def sync(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _git(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _source_repo(root: Path) -> tuple[Path, str]:
    repo = root / "source"
    repo.mkdir(parents=True)
    _git("init", "--quiet", cwd=repo)
    _git("config", "user.name", "Odysseus Test", cwd=repo)
    _git("config", "user.email", "odysseus@example.invalid", cwd=repo)
    (repo / "project.txt").write_text("persistent\n", encoding="utf-8")
    _git("add", "project.txt", cwd=repo)
    _git("commit", "--quiet", "-m", "Create project", cwd=repo)
    return repo, _git("rev-parse", "HEAD", cwd=repo)


def _evidence() -> ProjectForgeSyncEvidence:
    return ProjectForgeSyncEvidence(
        repo_id=REPO,
        transaction_id=TRANSACTION,
        version_id=VERSION,
        commit_sha=COMMIT,
        manifest_evidence=MANIFEST,
    )


def _github_policy(*, dual_backup: bool = False) -> ProjectForgePolicy:
    return ProjectForgePolicy(
        forge_mode="github",
        backup_providers=("nextcloud",) if dual_backup else (),
    )


def test_local_only_policy_enqueues_no_provider_operation(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")

    operations = enqueue_sync_targets(
        outbox=outbox,
        owner_id=OWNER,
        policy=ProjectForgePolicy(forge_mode="local"),
        evidence=_evidence(),
    )

    assert operations == ()
    assert outbox.list_operations(owner_id=OWNER, repo_id=REPO) == ()


def test_restart_reconciliation_recreates_missing_operations_from_local_forge(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    source, commit_sha = _source_repo(projects)
    forge = LocalProjectForge(root=tmp_path / "local-forge", source_roots=(projects,))
    policy = _github_policy(dual_backup=True)
    stored = forge.store_commit(
        owner_id=OWNER,
        repo_id=REPO,
        source_repo=source,
        commit_sha=commit_sha,
        idempotency_key="restart-reconcile",
        policy_snapshot=policy.to_dict(),
    )
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")

    first = reconcile_local_forge_outbox(
        outbox=outbox,
        local_forge=forge,
        owner_id=OWNER,
        repo_id=REPO,
    )
    second = reconcile_local_forge_outbox(
        outbox=outbox,
        local_forge=forge,
        owner_id=OWNER,
        repo_id=REPO,
    )

    assert {item.provider for item in first} == {"github", "nextcloud"}
    assert [item.operation_id for item in second] == [item.operation_id for item in first]
    assert all(item.version_id == stored.version_id for item in second)


def test_commit_report_helper_enqueues_policy_targets_and_replay_is_stable(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    result = SimpleNamespace(
        local_status="committed",
        repo_id=REPO,
        transaction_id=TRANSACTION,
        commit_sha=COMMIT,
    )
    report = SimpleNamespace(
        project_commit_result=result,
        version_evidence={"version_id": VERSION, "commit_sha": COMMIT},
        manifest_evidence={**MANIFEST, "payload": {"must_not": "be persisted"}},
    )

    first = enqueue_from_commit_report(
        outbox=outbox,
        owner_id=OWNER,
        report=report,
        policy=_github_policy(dual_backup=True),
    )
    replay = enqueue_from_commit_report(
        outbox=outbox,
        owner_id=OWNER,
        report=report,
        policy=_github_policy(dual_backup=True),
    )

    assert [item.provider for item in first] == ["github", "nextcloud"]
    assert [item.operation_id for item in first] == [item.operation_id for item in replay]
    assert all("payload" not in item.manifest_evidence for item in first)


def test_synced_and_already_synced_are_idempotent_and_never_redispatched(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    operation = enqueue_sync_targets(
        outbox=outbox, owner_id=OWNER, policy=_github_policy(), evidence=_evidence()
    )[0]
    adapter = FakeAdapter(
        [
            ForgeSyncOutcome(
                status="already_synced",
                idempotency_key=operation.operation_id,
                version_id=VERSION,
                commit_sha=COMMIT,
            )
        ]
    )
    coordinator = ProjectForgeSyncCoordinator(outbox=outbox, adapters={"github": adapter})

    first = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")
    second = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")

    assert first.processed == 1
    assert first.dispatches[0].status == "synced"
    assert second.processed == 0
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.idempotency_key == operation.operation_id
    assert request.operation_id == operation.operation_id
    assert not hasattr(request, "owner_id")
    assert not hasattr(request, "path")


def test_retryable_exception_uses_backoff_without_raw_exception_text(tmp_path):
    clock = MutableClock()
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox", clock=clock, retry_base_seconds=15)
    enqueue_sync_targets(outbox=outbox, owner_id=OWNER, policy=_github_policy(), evidence=_evidence())
    adapter = FakeAdapter([RuntimeError("Bearer not-a-real-secret C:/Users/private/repo")])
    coordinator = ProjectForgeSyncCoordinator(outbox=outbox, adapters={"github": adapter})

    report = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")
    persisted = outbox.load_operation(
        owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION, provider="github"
    )
    all_bytes = b"".join(path.read_bytes() for path in (tmp_path / "outbox").rglob("*.json"))

    assert report.dispatches[0].status == "retry_scheduled"
    assert persisted.last_error_code == "adapter_exception"
    assert persisted.next_attempt_at == "2026-07-13T12:00:15Z"
    assert b"Bearer" not in all_bytes and b"private" not in all_bytes
    assert coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker").processed == 0
    clock.advance(seconds=15)
    adapter.outcomes.append(ForgeSyncOutcome(status="synced"))
    assert coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker").processed == 1
    assert len(adapter.requests) == 2


def test_retryable_and_permanent_outcomes_make_mixed_provider_partial(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    enqueue_sync_targets(
        outbox=outbox,
        owner_id=OWNER,
        policy=_github_policy(dual_backup=True),
        evidence=_evidence(),
    )
    coordinator = ProjectForgeSyncCoordinator(
        outbox=outbox,
        adapters={
            "github": FakeAdapter([ForgeSyncOutcome(status="synced")]),
            "nextcloud": FakeAdapter([ForgeSyncOutcome(status="permanent_failure", error_code="remote_rejected")]),
        },
    )

    report = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker", limit=2)
    state = report.transaction_states[TRANSACTION]

    assert state.local_status == "committed"
    assert state.overall_status == "partial"
    assert state.provider_statuses == {"github": "synced", "nextcloud": "failed"}
    assert state.retry_scheduled is False


def test_retryable_outcome_schedules_retry_and_missing_adapter_is_blocked(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    enqueue_sync_targets(
        outbox=outbox,
        owner_id=OWNER,
        policy=_github_policy(dual_backup=True),
        evidence=_evidence(),
    )
    coordinator = ProjectForgeSyncCoordinator(
        outbox=outbox,
        adapters={"github": FakeAdapter([ForgeSyncOutcome(status="retryable_failure", error_code="remote_busy")])},
    )

    report = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker", limit=2)

    assert {item.provider: item.status for item in report.dispatches} == {
        "github": "retry_scheduled",
        "nextcloud": "blocked",
    }
    assert report.transaction_states[TRANSACTION].overall_status == "partial"


def test_divergence_creates_review_packet_and_preserves_local_evidence(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    operation = enqueue_sync_targets(
        outbox=outbox, owner_id=OWNER, policy=_github_policy(), evidence=_evidence()
    )[0]
    adapter = FakeAdapter(
        [ForgeSyncOutcome(status="diverged", provider_fingerprint="sha256:" + "e" * 64)]
    )
    coordinator = ProjectForgeSyncCoordinator(outbox=outbox, adapters={"github": adapter})

    report = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")
    persisted = outbox.load_operation(
        owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION, provider="github"
    )
    review = outbox.load_review(owner_id=OWNER, repo_id=REPO, review_ref=persisted.review_ref)

    assert report.dispatches[0].status == "conflict"
    assert persisted.version_id == operation.version_id
    assert persisted.commit_sha == operation.commit_sha
    assert review["review_ref"].startswith("incoming/github/")
    assert review["local_fingerprint"] == MANIFEST["sha256"]
    assert report.transaction_states[TRANSACTION].overall_status == "conflict"


def test_provider_identity_mismatch_is_conflict_not_duplicate_success(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    enqueue_sync_targets(outbox=outbox, owner_id=OWNER, policy=_github_policy(), evidence=_evidence())
    adapter = FakeAdapter(
        [ForgeSyncOutcome(status="already_synced", version_id="pv_" + "f" * 32, commit_sha=COMMIT)]
    )
    coordinator = ProjectForgeSyncCoordinator(outbox=outbox, adapters={"github": adapter})

    report = coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")

    assert report.dispatches[0].status == "conflict"
    assert report.transaction_states[TRANSACTION].overall_status == "conflict"


def test_raw_adapter_detail_is_not_an_allowed_outcome_field(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    enqueue_sync_targets(outbox=outbox, owner_id=OWNER, policy=_github_policy(), evidence=_evidence())
    adapter = FakeAdapter(
        [{"status": "retryable_failure", "detail": "token=not-a-real-token C:/Users/private"}]
    )
    coordinator = ProjectForgeSyncCoordinator(outbox=outbox, adapters={"github": adapter})

    coordinator.run_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")
    persisted = outbox.load_operation(
        owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION, provider="github"
    )
    raw = b"".join(path.read_bytes() for path in (tmp_path / "outbox").rglob("*.json"))

    assert persisted.status == "retry_scheduled"
    assert persisted.last_error_code == "adapter_exception"
    assert b"not-a-real-token" not in raw and b"C:/Users" not in raw


def test_commit_report_mismatch_and_unsafe_outcome_fail_closed(tmp_path):
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    result = SimpleNamespace(
        local_status="committed", repo_id=REPO, transaction_id=TRANSACTION, commit_sha=COMMIT
    )
    bad_report = SimpleNamespace(
        project_commit_result=result,
        version_evidence={"version_id": VERSION, "commit_sha": "1" * 40},
        manifest_evidence=MANIFEST,
    )
    with pytest.raises(ProjectForgeSyncError, match="does not match"):
        enqueue_from_commit_report(
            outbox=outbox, owner_id=OWNER, report=bad_report, policy=_github_policy()
        )
    with pytest.raises(ProjectForgeSyncError):
        ForgeSyncOutcome(status="permanent_failure", error_code="token_leaked")
