from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import pytest

from src.headless_write_agent_pipeline import ApprovalCapability, HeadlessCommitEvidence
from src.headless_write_agent_state import (
    AuthorityScope,
    HeadlessWriteAgentStateError,
    HeadlessWriteAgentStateStore,
)


DIFF_DIGEST = "sha256:" + "d" * 64
CHECKS_DIGEST = "sha256:" + "c" * 64


@dataclass
class FakeClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))


@pytest.fixture
def scope() -> AuthorityScope:
    return AuthorityScope.create(
        owner_id="local-user",
        repo_id="project-one",
        task_id="task-one",
        plan_id="plan-one",
        slice_id="slice-one",
        agent_run_id="bob-run-one",
    )


@pytest.fixture
def store(tmp_path, clock) -> HeadlessWriteAgentStateStore:
    return HeadlessWriteAgentStateStore(tmp_path / "hwa-authority.sqlite3", clock=clock)


def _claim(store, scope, *, claim_id="claim-first-0001", claimant="worker-a", lease=600):
    return store.acquire_claim(
        scope,
        claim_id=claim_id,
        claimant_ref=claimant,
        lease_seconds=lease,
    )


def _capability(scope: AuthorityScope, fence: int, **overrides) -> ApprovalCapability:
    values = {
        "capability_id": "hwa_cap_" + "a" * 32,
        "nonce": "hwa_nonce_" + "b" * 32,
        "stage": "project_commit",
        "owner_id": scope.owner_id,
        "repo_id": scope.repo_id,
        "task_id": scope.task_id,
        "plan_id": scope.plan_id,
        "slice_id": scope.slice_id,
        "agent_run_id": scope.agent_run_id,
        "approver_ref": "operator:charlie",
        "policy_version": "hwa-policy-v1",
        "input_digest": DIFF_DIGEST,
        "allowed_paths": ["src/project", "tests/test_project.py"],
        "blocked_paths": ["src/project/secrets"],
        "lease_fence": fence,
        "max_attempts": 2,
        "issued_at": "2026-07-15T10:00:00Z",
        "expires_at": "2026-07-15T10:10:00Z",
    }
    values.update(overrides)
    return ApprovalCapability.create(**values)


def _evidence(scope: AuthorityScope, fence: int, **overrides) -> HeadlessCommitEvidence:
    values = {
        "evidence_ref": "hwa_evd_" + "e" * 32,
        "owner_id": scope.owner_id,
        "repo_id": scope.repo_id,
        "task_id": scope.task_id,
        "plan_id": scope.plan_id,
        "slice_id": scope.slice_id,
        "agent_run_id": scope.agent_run_id,
        "worktree_ref": "coding-worktree:task-one",
        "lease_fence": fence,
        "base_commit_sha": "1" * 40,
        "diff_digest": DIFF_DIGEST,
        "checks_digest": CHECKS_DIGEST,
        "reviewed_paths": ["src/project/service.py", "tests/test_project.py"],
        "reviewer_ref": "review:charlie:42",
        "checks_passed": True,
        "content_reviewed": True,
        "verified_at": "2026-07-15T10:00:00Z",
    }
    values.update(overrides)
    return HeadlessCommitEvidence.create(**values)


def _reserve(store, capability, scope, *, reservation="reservation-effect-0001", digest=DIFF_DIGEST):
    return store.reserve_capability(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        scope=scope,
        stage=capability.stage.value,
        input_digest=digest,
        reservation_id=reservation,
    )


def test_two_store_instances_contend_and_exactly_one_claim_wins(tmp_path, clock, scope):
    first_store = HeadlessWriteAgentStateStore(tmp_path / "shared.sqlite3", clock=clock)
    second_store = HeadlessWriteAgentStateStore(tmp_path / "shared.sqlite3", clock=clock)

    winner = _claim(first_store, scope)
    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        _claim(second_store, scope, claim_id="claim-second-0002", claimant="worker-b")

    assert raised.value.code == "claim_conflict"
    assert second_store.get_claim(scope) == winner


def test_expired_or_released_claim_reclaims_with_strictly_higher_fence(store, clock, scope):
    first = _claim(store, scope, lease=60)
    clock.advance(seconds=61)
    reclaimed = _claim(store, scope, claim_id="claim-second-0002", claimant="worker-b")
    released = store.release_claim(
        scope,
        claim_id=reclaimed.claim_id,
        fence=reclaimed.fence,
    )
    third = _claim(store, scope, claim_id="claim-third-0003", claimant="worker-c")

    assert reclaimed.fence > first.fence
    assert released.state == "released"
    assert third.fence > reclaimed.fence


def test_old_fence_cannot_write_heartbeat_progress_evidence_or_promotion(store, clock, scope):
    old = _claim(store, scope, lease=30)
    clock.advance(seconds=31)
    current = _claim(store, scope, claim_id="claim-current-0002", claimant="worker-b")
    assert current.fence > old.fence

    operations = [
        lambda: store.renew_claim(
            scope, claim_id=old.claim_id, fence=old.fence, lease_seconds=60
        ),
        lambda: store.record_progress(scope, claim_id=old.claim_id, fence=old.fence),
        lambda: store.record_evidence(
            _evidence(scope, old.fence), claim_id=old.claim_id, fence=old.fence
        ),
        lambda: store.record_promotion(
            scope,
            claim_id=old.claim_id,
            fence=old.fence,
            effect_id="promotion-effect-0001",
            capability_id="hwa_cap_" + "a" * 32,
            evidence_ref="hwa_evd_" + "e" * 32,
            stage="project_commit",
            status="reserved",
        ),
    ]
    for operation in operations:
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            operation()
        assert raised.value.code in {"stale_fence", "stale_claim"}


def test_capability_nonce_reserves_and_consumes_once_with_idempotent_readback(store, scope):
    claim = _claim(store, scope)
    capability = _capability(scope, claim.fence)
    issued = store.issue_capability(capability)
    reserved = _reserve(store, capability, scope)
    duplicate_reserve = _reserve(store, capability, scope)
    consumed = store.consume_capability(
        capability_id=capability.capability_id,
        reservation_id="reservation-effect-0001",
    )
    duplicate_consume = store.consume_capability(
        capability_id=capability.capability_id,
        reservation_id="reservation-effect-0001",
    )

    assert issued.status == "issued"
    assert reserved == duplicate_reserve
    assert consumed == duplicate_consume
    assert consumed.status == "consumed"
    assert store.get_capability(capability.capability_id) == consumed

    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        _reserve(store, capability, scope, reservation="reservation-other-0002")
    assert raised.value.code == "capability_already_used"


def test_promotion_receipt_is_bound_to_persisted_capability_evidence_and_fence(store, scope):
    claim = _claim(store, scope)
    capability = _capability(scope, claim.fence)
    store.issue_capability(capability)
    _reserve(store, capability, scope)
    evidence = _evidence(scope, claim.fence)
    store.record_evidence(evidence, claim_id=claim.claim_id, fence=claim.fence)

    first = store.record_promotion(
        scope,
        claim_id=claim.claim_id,
        fence=claim.fence,
        effect_id="promotion-effect-0001",
        capability_id=capability.capability_id,
        evidence_ref=evidence.evidence_ref,
        stage="project_commit",
        status="reserved",
    )
    duplicate = store.record_promotion(
        scope,
        claim_id=claim.claim_id,
        fence=claim.fence,
        effect_id="promotion-effect-0001",
        capability_id=capability.capability_id,
        evidence_ref=evidence.evidence_ref,
        stage="project_commit",
        status="reserved",
    )

    assert first == duplicate
    succeeded = store.record_promotion(
        scope,
        claim_id=claim.claim_id,
        fence=claim.fence,
        effect_id="promotion-effect-0001",
        capability_id=capability.capability_id,
        evidence_ref=evidence.evidence_ref,
        stage="project_commit",
        status="succeeded",
    )
    assert succeeded["status"] == "succeeded"
    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        store.record_promotion(
            scope,
            claim_id=claim.claim_id,
            fence=claim.fence,
            effect_id="promotion-effect-0001",
            capability_id=capability.capability_id,
            evidence_ref=evidence.evidence_ref,
            stage="project_commit",
            status="failed",
        )
    assert raised.value.code == "promotion_conflict"

    unverified = _evidence(
        scope,
        claim.fence,
        evidence_ref="hwa_evd_" + "f" * 32,
        checks_passed=False,
    )
    store.record_evidence(unverified, claim_id=claim.claim_id, fence=claim.fence)
    with pytest.raises(HeadlessWriteAgentStateError) as binding_error:
        store.record_promotion(
            scope,
            claim_id=claim.claim_id,
            fence=claim.fence,
            effect_id="promotion-effect-0002",
            capability_id=capability.capability_id,
            evidence_ref=unverified.evidence_ref,
            stage="project_commit",
            status="reserved",
        )
    assert binding_error.value.code == "promotion_binding_mismatch"


@pytest.mark.parametrize("field", ["owner_id", "repo_id", "task_id"])
def test_capability_reservation_rejects_owner_repo_or_task_identity_mismatch(
    store, scope, field
):
    claim = _claim(store, scope)
    capability = _capability(scope, claim.fence)
    store.issue_capability(capability)
    changed = {
        "owner_id": scope.owner_id,
        "repo_id": scope.repo_id,
        "task_id": scope.task_id,
        "plan_id": scope.plan_id,
        "slice_id": scope.slice_id,
        "agent_run_id": scope.agent_run_id,
    }
    changed[field] = "different-value"

    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        _reserve(store, capability, AuthorityScope.create(**changed))

    assert raised.value.code == "capability_binding_mismatch"


def test_expired_capability_and_input_digest_mismatch_are_rejected(store, clock, scope):
    claim = _claim(store, scope, lease=900)
    capability = _capability(scope, claim.fence)
    store.issue_capability(capability)

    with pytest.raises(HeadlessWriteAgentStateError) as digest_error:
        _reserve(store, capability, scope, digest="sha256:" + "f" * 64)
    assert digest_error.value.code == "capability_input_mismatch"

    clock.advance(seconds=601)
    with pytest.raises(HeadlessWriteAgentStateError) as expiry_error:
        _reserve(store, capability, scope)
    assert expiry_error.value.code == "capability_expired"


def test_heartbeat_renewal_never_falsely_updates_progress_time(store, clock, scope):
    claim = _claim(store, scope)
    initial_progress = claim.last_progress_at
    clock.advance(seconds=20)
    renewed = store.renew_claim(
        scope,
        claim_id=claim.claim_id,
        fence=claim.fence,
        lease_seconds=600,
    )

    assert renewed.last_heartbeat_at != claim.last_heartbeat_at
    assert renewed.last_progress_at == initial_progress

    clock.advance(seconds=5)
    progressed = store.record_progress(
        scope,
        claim_id=claim.claim_id,
        fence=claim.fence,
    )
    assert progressed.last_progress_at != initial_progress


@pytest.mark.parametrize("level", ["owner", "repo", "run"])
def test_owner_repo_or_run_pause_blocks_new_claim_and_effect_reservation(
    tmp_path, clock, scope, level
):
    path = tmp_path / f"control-{level}.sqlite3"
    store = HeadlessWriteAgentStateStore(path, clock=clock)
    claim = _claim(store, scope)
    capability = _capability(scope, claim.fence)
    store.issue_capability(capability)
    kwargs = {"level": level, "owner_id": scope.owner_id}
    if level in {"repo", "run"}:
        kwargs["repo_id"] = scope.repo_id
    if level == "run":
        kwargs["agent_run_id"] = scope.agent_run_id
    store.set_control(
        **kwargs,
        paused=True,
        killed=False,
        reason_ref=f"pause-{level}-reason-0001",
    )

    with pytest.raises(HeadlessWriteAgentStateError) as reserve_error:
        _reserve(store, capability, scope)
    assert reserve_error.value.code == "authority_blocked"

    clock.advance(seconds=601)
    with pytest.raises(HeadlessWriteAgentStateError) as claim_error:
        _claim(store, scope, claim_id=f"claim-after-{level}-0002", claimant="worker-b")
    assert claim_error.value.code == "authority_blocked"


def test_kill_is_persisted_and_cannot_be_reversed(store, scope):
    killed = store.set_control(
        level="run",
        owner_id=scope.owner_id,
        repo_id=scope.repo_id,
        agent_run_id=scope.agent_run_id,
        paused=False,
        killed=True,
        reason_ref="kill-run-reason-0001",
    )

    assert killed.killed is True
    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        store.set_control(
            level="run",
            owner_id=scope.owner_id,
            repo_id=scope.repo_id,
            agent_run_id=scope.agent_run_id,
            paused=False,
            killed=False,
            reason_ref="revive-run-reason-0002",
        )
    assert raised.value.code == "kill_is_terminal"


def test_restart_reopen_preserves_claim_capability_evidence_fence_and_control(
    tmp_path, clock, scope
):
    path = tmp_path / "restart.sqlite3"
    first_store = HeadlessWriteAgentStateStore(path, clock=clock)
    claim = _claim(first_store, scope)
    capability = _capability(scope, claim.fence)
    first_store.issue_capability(capability)
    _reserve(first_store, capability, scope)
    evidence = _evidence(scope, claim.fence)
    first_store.record_evidence(evidence, claim_id=claim.claim_id, fence=claim.fence)
    first_store.set_control(
        level="owner",
        owner_id=scope.owner_id,
        paused=True,
        killed=False,
        reason_ref="pause-owner-reason-0001",
    )

    reopened = HeadlessWriteAgentStateStore(path, clock=clock)
    exported = reopened.export_safe_state()

    assert reopened.get_claim(scope) == claim
    assert reopened.get_capability(capability.capability_id).status == "reserved"
    assert reopened.get_evidence(evidence.evidence_ref)["checks_digest"] == CHECKS_DIGEST
    assert exported["controls"][0]["paused"] is True
    assert exported["claims"][0]["fence"] == claim.fence


def test_persisted_payloads_reject_absolute_paths_and_contain_no_forbidden_raw_fields(
    store, scope
):
    claim = _claim(store, scope)
    unsafe = _evidence(scope, claim.fence, worktree_ref="C:/private/worktree")

    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        store.record_evidence(unsafe, claim_id=claim.claim_id, fence=claim.fence)
    assert raised.value.code == "absolute_path_forbidden"

    capability = _capability(scope, claim.fence)
    store.issue_capability(capability)
    safe = json.dumps(store.export_safe_state(), sort_keys=True).lower()
    for forbidden in (
        "access_token",
        "api_key",
        "credential",
        "password",
        "private_content",
        "private_key",
        "provider_response",
        "raw_output",
        "secret",
    ):
        assert forbidden not in safe
    assert not re.search(r'(?i)(?:[a-z]:[/\\]|"/[^/])', safe)


def test_store_module_has_no_background_scheduler_git_or_provider_effect_source():
    source = Path("src/headless_write_agent_state.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_roots.add((node.module or "").split(".", 1)[0])

    assert not imported_roots.intersection(
        {"asyncio", "git", "requests", "subprocess", "threading"}
    )
