from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.project_forge_outbox import (
    OUTBOX_OPERATION_SCHEMA,
    ProjectForgeOutbox,
    ProjectForgeOutboxConflictError,
    ProjectForgeOutboxError,
    ProjectForgeOutboxIntegrityError,
    aggregate_transaction_status,
)
from src.project_version_store import canonical_json_bytes, owner_key_for


OWNER = "forge-owner@example.test"
REPO = "sample-project"
TRANSACTION = "pct_" + "1" * 32
VERSION = "pv_" + "2" * 32
COMMIT = "3" * 40
MANIFEST = {
    "schema": "odysseus.project_version_manifest.v1",
    "sha256": "sha256:" + "4" * 64,
    "reference": f"version:{VERSION}",
}


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _store(tmp_path, clock: MutableClock | None = None, **kwargs) -> ProjectForgeOutbox:
    return ProjectForgeOutbox(root=tmp_path / "outbox", clock=clock, **kwargs)


def _enqueue(store: ProjectForgeOutbox, provider: str = "github", **overrides):
    values = {
        "owner_id": OWNER,
        "repo_id": REPO,
        "transaction_id": TRANSACTION,
        "version_id": VERSION,
        "provider": provider,
        "commit_sha": COMMIT,
        "manifest_evidence": MANIFEST,
        "policy_evidence": {"forge_mode": "github", "sync_on_commit": True},
    }
    values.update(overrides)
    return store.enqueue(**values)


def test_enqueue_two_providers_has_stable_ids_and_replay_is_idempotent(tmp_path):
    store = _store(tmp_path)

    github = _enqueue(store, "github")
    nextcloud = _enqueue(store, "nextcloud")
    replay = _enqueue(store, "github")

    assert github.operation_id == replay.operation_id
    assert github.operation_id.startswith("pfo_") and len(github.operation_id) == 36
    assert nextcloud.operation_id.startswith("pfo_")
    assert github.operation_id != nextcloud.operation_id
    assert len(store.list_operations(owner_id=OWNER, repo_id=REPO)) == 2
    persisted_paths = [str(path) for path in (tmp_path / "outbox").rglob("*.json")]
    assert OWNER not in "\n".join(persisted_paths)
    assert owner_key_for(OWNER) in "\n".join(persisted_paths)


def test_same_transaction_provider_with_changed_evidence_is_a_hard_conflict(tmp_path):
    store = _store(tmp_path)
    _enqueue(store)

    with pytest.raises(ProjectForgeOutboxConflictError):
        _enqueue(store, commit_sha="5" * 40)
    with pytest.raises(ProjectForgeOutboxConflictError):
        _enqueue(store, policy_evidence={"forge_mode": "github", "sync_on_commit": False})
    with pytest.raises(ProjectForgeOutboxConflictError):
        _enqueue(store, version_id="pv_" + "6" * 32, manifest_evidence={**MANIFEST, "reference": "version:pv_" + "6" * 32})


def test_claim_active_lease_expiry_reconcile_and_restart(tmp_path):
    clock = MutableClock()
    store = _store(tmp_path, clock, retry_base_seconds=5)
    original = _enqueue(store)

    claimed = store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker-a", lease_seconds=10)
    assert len(claimed) == 1
    assert claimed[0].status == "syncing"
    assert claimed[0].attempts == 1
    assert store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker-b") == ()

    restarted = _store(tmp_path, clock, retry_base_seconds=5)
    assert restarted.load_operation(
        owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION, provider="github"
    ).operation_id == original.operation_id
    clock.advance(seconds=11)
    recovered = restarted.reconcile(owner_id=OWNER, repo_id=REPO)
    assert recovered[0].status == "retry_scheduled"
    assert recovered[0].last_error_code == "lease_expired"
    reclaimed = restarted.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker-b")
    assert len(reclaimed) == 1
    assert reclaimed[0].operation_id == original.operation_id
    assert reclaimed[0].attempts == 2


def test_stale_same_owner_claim_token_cannot_complete_new_lease_revision(tmp_path):
    clock = MutableClock()
    store = _store(tmp_path, clock)
    _enqueue(store)
    old_claim = store.claim_due(
        owner_id=OWNER, repo_id=REPO, lease_owner="worker-a", lease_seconds=10
    )[0]
    clock.advance(seconds=11)
    store.reconcile(owner_id=OWNER, repo_id=REPO)
    new_claim = store.claim_due(
        owner_id=OWNER, repo_id=REPO, lease_owner="worker-a", lease_seconds=10
    )[0]

    assert old_claim.lease_owner == new_claim.lease_owner
    assert old_claim.lease_token != new_claim.lease_token
    with pytest.raises(ProjectForgeOutboxConflictError, match="lease revision"):
        store.mark_synced(
            owner_id=OWNER,
            repo_id=REPO,
            transaction_id=TRANSACTION,
            provider="github",
            lease_owner="worker-a",
            lease_token=old_claim.lease_token,
            provider_fingerprint="sha256:" + "e" * 64,
        )
    synced = store.mark_synced(
        owner_id=OWNER,
        repo_id=REPO,
        transaction_id=TRANSACTION,
        provider="github",
        lease_owner="worker-a",
        lease_token=new_claim.lease_token,
        provider_fingerprint="sha256:" + "e" * 64,
    )
    assert synced.status == "synced"
    assert synced.provider_fingerprint == "sha256:" + "e" * 64
    assert store.load_operation(
        owner_id=OWNER,
        repo_id=REPO,
        transaction_id=TRANSACTION,
        provider="github",
    ).provider_fingerprint == synced.provider_fingerprint


def test_retry_is_bounded_and_terminal_failed_is_not_due(tmp_path):
    clock = MutableClock()
    store = _store(tmp_path, clock, retry_base_seconds=10, default_max_attempts=1)
    _enqueue(store)
    claimed = store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")[0]

    failed = store.schedule_retry(
        owner_id=OWNER,
        repo_id=REPO,
        transaction_id=TRANSACTION,
        provider="github",
        lease_owner="worker",
        lease_token=claimed.lease_token,
        error_code="temporary_failure",
    )

    assert failed.status == "failed"
    assert failed.next_attempt_at is None
    assert store.transaction_state(owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION).retry_scheduled is False
    assert store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker") == ()


def test_synced_is_terminal_and_aggregate_statuses_are_provider_only(tmp_path):
    store = _store(tmp_path)
    _enqueue(store, "github")
    _enqueue(store, "nextcloud")
    claimed = {
        item.provider: item
        for item in store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker", limit=2)
    }
    store.mark_synced(
        owner_id=OWNER,
        repo_id=REPO,
        transaction_id=TRANSACTION,
        provider="github",
        lease_owner="worker",
        lease_token=claimed["github"].lease_token,
        provider_fingerprint="sha256:" + "f" * 64,
    )
    store.mark_failed(
        owner_id=OWNER,
        repo_id=REPO,
        transaction_id=TRANSACTION,
        provider="nextcloud",
        lease_owner="worker",
        lease_token=claimed["nextcloud"].lease_token,
        error_code="permanent_failure",
    )

    state = store.transaction_state(owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION)
    assert state.local_status == "committed"
    assert state.overall_status == "partial"
    assert state.retry_scheduled is False
    assert store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker") == ()
    assert aggregate_transaction_status(["failed", "blocked"]) == "failed"
    assert aggregate_transaction_status(["retry_scheduled", "pending"]) == "sync_pending"
    assert aggregate_transaction_status(["synced", "conflict"]) == "conflict"


def test_divergence_persists_redacted_incoming_review_without_changing_local_version(tmp_path):
    store = _store(tmp_path)
    original = _enqueue(store)
    claimed = store.claim_due(owner_id=OWNER, repo_id=REPO, lease_owner="worker")[0]

    conflict = store.mark_conflict(
        owner_id=OWNER,
        repo_id=REPO,
        transaction_id=TRANSACTION,
        provider="github",
        lease_owner="worker",
        lease_token=claimed.lease_token,
        expected_fingerprint=claimed.request_fingerprint,
        local_fingerprint=MANIFEST["sha256"],
        provider_fingerprint="sha256:" + "7" * 64,
    )
    review = store.load_review(owner_id=OWNER, repo_id=REPO, review_ref=conflict.review_ref)

    assert conflict.status == "conflict"
    assert conflict.review_ref.startswith("incoming/github/pfr_")
    assert review["version_id"] == original.version_id
    assert review["commit_sha"] == original.commit_sha
    assert "files" not in review and "diff" not in review and "detail" not in review


def test_canonical_atomic_persistence_and_tamper_fail_closed(tmp_path):
    store = _store(tmp_path)
    operation = _enqueue(store)
    path = next((tmp_path / "outbox").rglob("github.json"))
    raw = path.read_bytes()
    payload = json.loads(raw)
    assert raw == canonical_json_bytes(payload)
    assert payload["schema"] == OUTBOX_OPERATION_SCHEMA
    assert not list(path.parent.glob("*.tmp"))

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with pytest.raises(ProjectForgeOutboxIntegrityError, match="canonical"):
        store.load_operation(owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION, provider="github")

    path.write_bytes(canonical_json_bytes({**payload, "operation_id": "pfo_" + "8" * 32}))
    with pytest.raises(ProjectForgeOutboxIntegrityError, match="operation id"):
        store.load_operation(owner_id=OWNER, repo_id=REPO, transaction_id=TRANSACTION, provider="github")


def test_raw_secret_and_private_path_evidence_is_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ProjectForgeOutboxError):
        _enqueue(store, policy_evidence={"access_token": "not-a-real-token"})
    with pytest.raises(ProjectForgeOutboxError):
        _enqueue(
            store,
            manifest_evidence={
                "schema": MANIFEST["schema"],
                "sha256": MANIFEST["sha256"],
                "reference": f"C:/Users/private/{VERSION}",
            },
        )
