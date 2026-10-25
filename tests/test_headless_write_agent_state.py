from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager

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


def _persist_commit_authority(store, scope, *, status="issued"):
    claim = _claim(store, scope)
    capability = _capability(scope, claim.fence)
    evidence = _evidence(scope, claim.fence)
    store.issue_capability(capability)
    store.record_evidence(evidence, claim_id=claim.claim_id, fence=claim.fence)
    if status in {"reserved", "consumed"}:
        _reserve(store, capability, scope)
    if status == "consumed":
        store.consume_capability(
            capability_id=capability.capability_id,
            reservation_id="reservation-effect-0001",
        )
    return claim, capability, evidence


def _update_store(store, sql, parameters):
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()


def _stored_value(store, table, key, reference, column):
    connection = sqlite3.connect(store.path)
    try:
        row = connection.execute(
            f"SELECT {column} FROM {table} WHERE {key} = ?", (reference,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row[0]


def _clone_blob_identity(
    store,
    table,
    key,
    source_reference,
    *,
    blob_reference=None,
    overrides=None,
):
    connection = sqlite3.connect(store.path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        row = connection.execute(
            f"SELECT * FROM {table} WHERE {key} = ?", (source_reference,)
        ).fetchone()
        assert row is not None
        values = dict(row)
        values[key] = (blob_reference or source_reference).encode("ascii")
        values.update(overrides or {})
        columns = tuple(values)
        connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(values[column] for column in columns),
        )
        connection.commit()
    finally:
        connection.close()


def _control_reference(store, *, level="run"):
    connection = sqlite3.connect(store.path)
    try:
        row = connection.execute(
            "SELECT control_key FROM hwa_controls WHERE level = ?", (level,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row[0]


def _assert_commit_corrupt(store, scope, claim, capability, evidence):
    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        _load(store, scope, claim, capability, evidence)
    assert raised.value.code == "commit_authority_corrupt"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def _load(store, scope, claim, capability, evidence):
    return store.load_commit_authority(
        capability_id=capability.capability_id,
        evidence_ref=evidence.evidence_ref,
        scope=scope,
        claim_id=claim.claim_id,
        fence=claim.fence,
    )


def test_load_commit_authority_reconstructs_exact_typed_capability_and_evidence(
    store, scope
):
    claim, capability, evidence = _persist_commit_authority(store, scope)
    snapshot = _load(store, scope, claim, capability, evidence)
    assert snapshot.capability == capability
    assert snapshot.evidence == evidence
    assert snapshot.capability_status == "issued"
    assert type(snapshot.capability) is ApprovalCapability
    assert type(snapshot.evidence) is HeadlessCommitEvidence
    assert snapshot.capability.allowed_paths is not capability.allowed_paths
    assert snapshot.evidence.reviewed_paths is not evidence.reviewed_paths


def test_load_commit_authority_survives_store_reopen(tmp_path, clock, scope, monkeypatch):
    path = tmp_path / "commit-authority.sqlite3"
    first = HeadlessWriteAgentStateStore(path, clock=clock)
    claim, capability, evidence = _persist_commit_authority(first, scope)
    reopened = HeadlessWriteAgentStateStore(path, clock=clock)
    original_read = reopened._read
    observed = []

    @contextmanager
    def guarded_read():
        with original_read() as connection:
            observed.append(connection.in_transaction)
            original_execute = connection.execute

            class GuardedConnection:
                def execute(self, sql, parameters=()):
                    observed.append(connection.in_transaction)
                    row = original_execute(sql, parameters)
                    if "FROM hwa_capabilities" in sql:
                        writer = sqlite3.connect(path, timeout=0)
                        try:
                            writer.execute(
                                "UPDATE hwa_evidence SET verified_at = ? WHERE evidence_ref = ?",
                                ("2026-07-15T09:59:59Z", evidence.evidence_ref),
                            )
                            writer.commit()
                        finally:
                            writer.close()
                    return row

            yield GuardedConnection()

    monkeypatch.setattr(reopened, "_read", guarded_read)
    snapshot = _load(reopened, scope, claim, capability, evidence)
    assert snapshot.capability == capability
    assert snapshot.evidence == evidence
    assert observed
    assert all(observed)
    assert len(observed) >= 5


def test_load_commit_authority_returns_bounded_issued_reserved_and_consumed_status(
    tmp_path, clock, scope
):
    for status in ("issued", "reserved", "consumed"):
        store = HeadlessWriteAgentStateStore(tmp_path / f"{status}.sqlite3", clock=clock)
        claim, capability, evidence = _persist_commit_authority(store, scope, status=status)
        snapshot = _load(store, scope, claim, capability, evidence)
        assert snapshot.capability_status == status
        assert not hasattr(snapshot, "reservation_id")
        assert not hasattr(snapshot, "payload_json")


def test_load_commit_authority_rejects_missing_capability_or_evidence(store, scope):
    claim, capability, evidence = _persist_commit_authority(store, scope)
    cases = (
        ("hwa_cap_" + "f" * 32, evidence.evidence_ref),
        (capability.capability_id, "hwa_evd_" + "f" * 32),
    )
    for capability_id, evidence_ref in cases:
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            store.load_commit_authority(
                capability_id=capability_id,
                evidence_ref=evidence_ref,
                scope=scope,
                claim_id=claim.claim_id,
                fence=claim.fence,
            )
        assert raised.value.code == "commit_authority_not_found"

    for table, key, reference, alternate in (
        (
            "hwa_capabilities",
            "capability_id",
            capability.capability_id,
            "hwa_cap_" + "f" * 32,
        ),
        (
            "hwa_evidence",
            "evidence_ref",
            evidence.evidence_ref,
            "hwa_evd_" + "f" * 32,
        ),
    ):
        for mode in ("lone_blob", "text_and_blob"):
            case_store = HeadlessWriteAgentStateStore(
                store.path.with_name(f"{table}-{mode}.sqlite3"), clock=store._clock
            )
            case_claim, case_capability, case_evidence = _persist_commit_authority(
                case_store, scope
            )
            case_reference = (
                case_capability.capability_id
                if table == "hwa_capabilities"
                else case_evidence.evidence_ref
            )
            if mode == "lone_blob":
                _update_store(
                    case_store,
                    f"UPDATE {table} SET {key} = ? WHERE {key} = ?",
                    (case_reference.encode("ascii"), case_reference),
                )
            else:
                overrides = (
                    {"nonce": "hwa_nonce_" + "f" * 32}
                    if table == "hwa_capabilities"
                    else None
                )
                _clone_blob_identity(
                    case_store,
                    table,
                    key,
                    case_reference,
                    overrides=overrides,
                )
            _assert_commit_corrupt(
                case_store, scope, case_claim, case_capability, case_evidence
            )

        missing_store = HeadlessWriteAgentStateStore(
            store.path.with_name(f"{table}-missing-blob.sqlite3"), clock=store._clock
        )
        missing_claim, missing_capability, missing_evidence = _persist_commit_authority(
            missing_store, scope
        )
        source_reference = (
            missing_capability.capability_id
            if table == "hwa_capabilities"
            else missing_evidence.evidence_ref
        )
        overrides = (
            {"nonce": "hwa_nonce_" + "f" * 32}
            if table == "hwa_capabilities"
            else None
        )
        _clone_blob_identity(
            missing_store,
            table,
            key,
            source_reference,
            blob_reference=alternate,
            overrides=overrides,
        )
        if table == "hwa_capabilities":
            missing_capability = _capability(
                scope,
                missing_claim.fence,
                capability_id=alternate,
                nonce="hwa_nonce_" + "f" * 32,
            )
        else:
            missing_evidence = _evidence(
                scope, missing_claim.fence, evidence_ref=alternate
            )
        _assert_commit_corrupt(
            missing_store, scope, missing_claim, missing_capability, missing_evidence
        )


def test_load_commit_authority_rejects_cross_scope_and_cross_pair_identity(
    tmp_path, clock, scope
):
    store = HeadlessWriteAgentStateStore(tmp_path / "cross-pair.sqlite3", clock=clock)
    claim, capability, evidence = _persist_commit_authority(store, scope)
    other_scope = AuthorityScope.create(
        owner_id=scope.owner_id,
        repo_id=scope.repo_id,
        task_id="task-two",
        plan_id=scope.plan_id,
        slice_id=scope.slice_id,
        agent_run_id="bob-run-two",
    )
    other_claim = _claim(
        store, other_scope, claim_id="claim-other-0002", claimant="worker-b"
    )
    other_capability = _capability(
        other_scope,
        other_claim.fence,
        capability_id="hwa_cap_" + "f" * 32,
        nonce="hwa_nonce_" + "f" * 32,
    )
    store.issue_capability(other_capability)
    for capability_id, load_scope, load_claim in (
        (other_capability.capability_id, scope, claim),
        (capability.capability_id, other_scope, other_claim),
    ):
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            store.load_commit_authority(
                capability_id=capability_id,
                evidence_ref=evidence.evidence_ref,
                scope=load_scope,
                claim_id=load_claim.claim_id,
                fence=load_claim.fence,
            )
        assert raised.value.code == "commit_authority_binding_mismatch"


def test_load_commit_authority_rejects_digest_stage_and_fence_binding_mismatch(
    tmp_path, clock, scope
):
    cases = (
        ("hwa_capabilities", "stage", "workspace_write"),
        ("hwa_capabilities", "input_digest", "sha256:" + "f" * 64),
        ("hwa_capabilities", "lease_fence", 2),
        ("hwa_evidence", "lease_fence", 2),
    )
    for index, (table, column, value) in enumerate(cases):
        store = HeadlessWriteAgentStateStore(tmp_path / f"binding-{index}.sqlite3", clock=clock)
        claim, capability, evidence = _persist_commit_authority(store, scope)
        key = "capability_id" if table == "hwa_capabilities" else "evidence_ref"
        reference = capability.capability_id if table == "hwa_capabilities" else evidence.evidence_ref
        _update_store(store, f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (value, reference))
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            _load(store, scope, claim, capability, evidence)
        assert raised.value.code == "commit_authority_binding_mismatch"


def test_load_commit_authority_rejects_released_expired_or_reclaimed_claim(
    tmp_path, clock, scope
):
    for claim_state in ("released", "expired", "reclaimed"):
        case_clock = FakeClock(clock.value)
        store = HeadlessWriteAgentStateStore(
            tmp_path / f"claim-{claim_state}.sqlite3", clock=case_clock
        )
        claim, capability, evidence = _persist_commit_authority(store, scope)
        if claim_state == "released":
            store.release_claim(scope, claim_id=claim.claim_id, fence=claim.fence)
        else:
            case_clock.advance(seconds=601)
            if claim_state == "reclaimed":
                _claim(store, scope, claim_id="claim-reclaimed-0002", claimant="worker-b")
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            _load(store, scope, claim, capability, evidence)
        assert raised.value.code in {"stale_claim", "stale_fence"}

    alias_store = HeadlessWriteAgentStateStore(
        tmp_path / "claim-scope-key-alias.sqlite3", clock=clock
    )
    claim, capability, evidence = _persist_commit_authority(alias_store, scope)
    _clone_blob_identity(
        alias_store,
        "hwa_claims",
        "scope_key",
        scope.key,
        overrides={"claim_id": "claim-alias-0002"},
    )
    _assert_commit_corrupt(alias_store, scope, claim, capability, evidence)

    claim_id_store = HeadlessWriteAgentStateStore(
        tmp_path / "claim-id-alias.sqlite3", clock=clock
    )
    claim, capability, evidence = _persist_commit_authority(claim_id_store, scope)
    _clone_blob_identity(
        claim_id_store,
        "hwa_claims",
        "claim_id",
        claim.claim_id,
        overrides={"scope_key": "hwa_scope_" + "f" * 64},
    )
    _assert_commit_corrupt(claim_id_store, scope, claim, capability, evidence)


def test_load_commit_authority_rejects_paused_or_killed_scope(
    tmp_path, clock, scope
):
    for blocked in ("paused", "killed"):
        store = HeadlessWriteAgentStateStore(
            tmp_path / f"{blocked}.sqlite3", clock=clock
        )
        claim, capability, evidence = _persist_commit_authority(store, scope)
        store.set_control(
            level="run",
            owner_id=scope.owner_id,
            repo_id=scope.repo_id,
            agent_run_id=scope.agent_run_id,
            paused=blocked == "paused",
            killed=blocked == "killed",
            reason_ref=f"{blocked}-run-reason-0001",
        )
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            _load(store, scope, claim, capability, evidence)
        assert raised.value.code == "authority_blocked"

    alias_store = HeadlessWriteAgentStateStore(
        tmp_path / "control-key-alias.sqlite3", clock=clock
    )
    claim, capability, evidence = _persist_commit_authority(alias_store, scope)
    alias_store.set_control(
        level="run",
        owner_id=scope.owner_id,
        repo_id=scope.repo_id,
        agent_run_id=scope.agent_run_id,
        paused=False,
        killed=False,
        reason_ref="control-alias-reason-0001",
    )
    control_key = _control_reference(alias_store)
    _clone_blob_identity(
        alias_store,
        "hwa_controls",
        "control_key",
        control_key,
        overrides={"paused": 1},
    )
    _assert_commit_corrupt(alias_store, scope, claim, capability, evidence)

    for index, identity_column in enumerate(("owner_id", "repo_id", "agent_run_id")):
        candidate_store = HeadlessWriteAgentStateStore(
            tmp_path / f"control-candidate-{identity_column}.sqlite3", clock=clock
        )
        claim, capability, evidence = _persist_commit_authority(candidate_store, scope)
        candidate_store.set_control(
            level="run",
            owner_id=scope.owner_id,
            repo_id=scope.repo_id,
            agent_run_id=scope.agent_run_id,
            paused=False,
            killed=False,
            reason_ref="control-candidate-reason-0001",
        )
        source_reference = getattr(scope, identity_column)
        _clone_blob_identity(
            candidate_store,
            "hwa_controls",
            identity_column,
            source_reference,
            overrides={
                "control_key": "hwa_control_" + str(index + 1) * 64,
                "paused": 1,
            },
        )
        _assert_commit_corrupt(
            candidate_store, scope, claim, capability, evidence
        )

    for identity_column in ("owner_id", "repo_id", "agent_run_id"):
        case_store = HeadlessWriteAgentStateStore(
            tmp_path / f"control-{identity_column}.sqlite3", clock=clock
        )
        claim, capability, evidence = _persist_commit_authority(case_store, scope)
        case_store.set_control(
            level="run",
            owner_id=scope.owner_id,
            repo_id=scope.repo_id,
            agent_run_id=scope.agent_run_id,
            paused=True,
            killed=False,
            reason_ref="control-identity-reason-0001",
        )
        control_key = _control_reference(case_store)
        value = _stored_value(
            case_store,
            "hwa_controls",
            "control_key",
            control_key,
            identity_column,
        )
        _update_store(
            case_store,
            f"UPDATE hwa_controls SET {identity_column} = ? WHERE control_key = ?",
            (value.encode("ascii"), control_key),
        )
        _assert_commit_corrupt(case_store, scope, claim, capability, evidence)


def test_load_commit_authority_rejects_malformed_non_object_missing_extra_duplicate_or_noncanonical_payload(
    tmp_path, clock, scope
):
    payloads = (
        "{",
        "[]",
        '{"capability_id":"duplicate","capability_id":"duplicate"}',
        "{}",
        '{"unexpected":true}',
        '{ "unexpected" : true }',
    )
    for index, payload_json in enumerate(payloads):
        store = HeadlessWriteAgentStateStore(
            tmp_path / f"payload-{index}.sqlite3", clock=clock
        )
        claim, capability, evidence = _persist_commit_authority(store, scope)
        _update_store(
            store,
            "UPDATE hwa_capabilities SET payload_json = ? WHERE capability_id = ?",
            (payload_json, capability.capability_id),
        )
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            _load(store, scope, claim, capability, evidence)
        assert raised.value.code == "commit_authority_corrupt"


def test_load_commit_authority_rejects_sql_column_payload_disagreement(
    tmp_path, clock, scope
):
    cases = (
        ("hwa_capabilities", "owner_id", "different-owner"),
        ("hwa_capabilities", "policy_version", "other-policy"),
        ("hwa_evidence", "repo_id", "different-repo"),
        ("hwa_evidence", "verified_at", "2026-07-15T09:59:59Z"),
    )
    storage_cases = (
        ("hwa_capabilities", "lease_fence", b"1"),
        ("hwa_capabilities", "max_attempts", b"2"),
        ("hwa_evidence", "lease_fence", b"1"),
    )
    for index, (table, column, value) in enumerate((*cases, *storage_cases)):
        store = HeadlessWriteAgentStateStore(tmp_path / f"sql-{index}.sqlite3", clock=clock)
        claim, capability, evidence = _persist_commit_authority(store, scope)
        key = "capability_id" if table == "hwa_capabilities" else "evidence_ref"
        reference = capability.capability_id if table == "hwa_capabilities" else evidence.evidence_ref
        _update_store(store, f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (value, reference))
        if isinstance(value, bytes):
            connection = sqlite3.connect(store.path)
            try:
                raw_type, raw_value = connection.execute(
                    f"SELECT typeof({column}), {column} FROM {table} WHERE {key} = ?",
                    (reference,),
                ).fetchone()
            finally:
                connection.close()
            assert raw_type == "blob"
            assert type(raw_value) is bytes
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            _load(store, scope, claim, capability, evidence)
        assert raised.value.code == "commit_authority_corrupt"
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    text_columns = {
        "hwa_claims": (
            "scope_key",
            "claim_id",
            "owner_id",
            "repo_id",
            "task_id",
            "plan_id",
            "slice_id",
            "agent_run_id",
            "claimant_ref",
            "state",
            "acquired_at",
            "lease_expires_at",
            "last_heartbeat_at",
            "last_progress_at",
        ),
        "hwa_capabilities": (
            "capability_id",
            "nonce",
            "scope_key",
            "owner_id",
            "repo_id",
            "task_id",
            "plan_id",
            "slice_id",
            "agent_run_id",
            "stage",
            "input_digest",
            "policy_version",
            "issued_at",
            "expires_at",
            "status",
            "payload_json",
        ),
        "hwa_evidence": (
            "evidence_ref",
            "scope_key",
            "owner_id",
            "repo_id",
            "task_id",
            "plan_id",
            "slice_id",
            "agent_run_id",
            "verified_at",
            "payload_json",
        ),
        "hwa_controls": (
            "control_key",
            "level",
            "owner_id",
            "repo_id",
            "agent_run_id",
            "reason_ref",
            "updated_at",
        ),
    }
    integer_columns = {
        "hwa_claims": ("fence",),
        "hwa_capabilities": ("lease_fence", "max_attempts"),
        "hwa_evidence": ("lease_fence",),
        "hwa_controls": ("paused", "killed", "version"),
    }
    for table, columns in (*text_columns.items(), *integer_columns.items()):
        for column in columns:
            case_store = HeadlessWriteAgentStateStore(
                tmp_path / f"raw-{table}-{column}.sqlite3", clock=clock
            )
            claim, capability, evidence = _persist_commit_authority(case_store, scope)
            if table == "hwa_claims":
                key, reference = "scope_key", scope.key
            elif table == "hwa_capabilities":
                key, reference = "capability_id", capability.capability_id
            elif table == "hwa_evidence":
                key, reference = "evidence_ref", evidence.evidence_ref
            else:
                case_store.set_control(
                    level="run",
                    owner_id=scope.owner_id,
                    repo_id=scope.repo_id,
                    agent_run_id=scope.agent_run_id,
                    paused=False,
                    killed=False,
                    reason_ref="raw-control-reason-0001",
                )
                key, reference = "control_key", _control_reference(case_store)
            current = _stored_value(case_store, table, key, reference, column)
            malformed = str(current).encode("ascii")
            _update_store(
                case_store,
                f"UPDATE {table} SET {column} = ? WHERE {key} = ?",
                (malformed, reference),
            )
            _assert_commit_corrupt(case_store, scope, claim, capability, evidence)

    released_store = HeadlessWriteAgentStateStore(
        tmp_path / "raw-hwa-claims-released-at.sqlite3", clock=clock
    )
    claim, capability, evidence = _persist_commit_authority(released_store, scope)
    _update_store(
        released_store,
        "UPDATE hwa_claims SET released_at = ? WHERE scope_key = ?",
        ("2026-07-15T10:00:00Z", scope.key),
    )
    _assert_commit_corrupt(released_store, scope, claim, capability, evidence)

    for table, column in (
        ("hwa_claims", "fence"),
        ("hwa_capabilities", "lease_fence"),
        ("hwa_capabilities", "max_attempts"),
        ("hwa_evidence", "lease_fence"),
        ("hwa_controls", "paused"),
        ("hwa_controls", "killed"),
        ("hwa_controls", "version"),
    ):
        for malformed in ("not-an-integer", 1.5):
            case_store = HeadlessWriteAgentStateStore(
                tmp_path / f"numeric-{table}-{column}-{type(malformed).__name__}.sqlite3",
                clock=clock,
            )
            claim, capability, evidence = _persist_commit_authority(case_store, scope)
            if table == "hwa_claims":
                key, reference = "scope_key", scope.key
            elif table == "hwa_capabilities":
                key, reference = "capability_id", capability.capability_id
            elif table == "hwa_evidence":
                key, reference = "evidence_ref", evidence.evidence_ref
            else:
                case_store.set_control(
                    level="run",
                    owner_id=scope.owner_id,
                    repo_id=scope.repo_id,
                    agent_run_id=scope.agent_run_id,
                    paused=False,
                    killed=False,
                    reason_ref="numeric-control-reason-0001",
                )
                key, reference = "control_key", _control_reference(case_store)
            _update_store(
                case_store,
                f"UPDATE {table} SET {column} = ? WHERE {key} = ?",
                (malformed, reference),
            )
            _assert_commit_corrupt(case_store, scope, claim, capability, evidence)


def test_load_commit_authority_errors_are_fresh_and_never_leak_sqlite_or_json_text(
    tmp_path, clock, scope, monkeypatch
):
    payloads = ("{", '"private database path C:/users/example"', '{"secret":"value"}')
    for index, payload_json in enumerate(payloads):
        store = HeadlessWriteAgentStateStore(
            tmp_path / f"error-{index}.sqlite3", clock=clock
        )
        claim, capability, evidence = _persist_commit_authority(store, scope)
        _update_store(
            store,
            "UPDATE hwa_evidence SET payload_json = ? WHERE evidence_ref = ?",
            (payload_json, evidence.evidence_ref),
        )
        errors = []
        for _ in range(2):
            with pytest.raises(HeadlessWriteAgentStateError) as raised:
                _load(store, scope, claim, capability, evidence)
            errors.append(raised.value)
            assert raised.value.code == "commit_authority_corrupt"
            assert raised.value.__cause__ is None
            assert raised.value.__context__ is None
            assert payload_json not in str(raised.value)
            assert str(store.path) not in str(raised.value)
        assert errors[0] is not errors[1]

    store = HeadlessWriteAgentStateStore(tmp_path / "fresh-boundary.sqlite3", clock=clock)
    claim, capability, evidence = _persist_commit_authority(store, scope)
    cases = (
        (
            {"capability_id": object()},
            "commit_authority_corrupt",
            None,
        ),
        (
            {"fence": True},
            "commit_authority_corrupt",
            None,
        ),
        (
            {"claim_id": "wrong"},
            "commit_authority_corrupt",
            None,
        ),
    )
    for overrides, code, patch_target in cases:
        arguments = {
            "capability_id": capability.capability_id,
            "evidence_ref": evidence.evidence_ref,
            "scope": scope,
            "claim_id": claim.claim_id,
            "fence": claim.fence,
        }
        arguments.update(overrides)
        failures = []
        for _ in range(2):
            with pytest.raises(HeadlessWriteAgentStateError) as raised:
                store.load_commit_authority(**arguments)
            failures.append(raised.value)
            assert raised.value.code == code
            assert raised.value.__cause__ is None
            assert raised.value.__context__ is None
        assert failures[0] is not failures[1]

    for fault in (
        lambda: (_ for _ in ()).throw(ValueError("hostile clock private text")),
        lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("private sqlite text")),
    ):
        target = "_clock" if "clock" in repr(fault.__code__.co_consts) else "_connect"
        replacement = fault if target == "_clock" else fault
        with monkeypatch.context() as context:
            context.setattr(store, target, replacement)
            failures = []
            for _ in range(2):
                with pytest.raises(HeadlessWriteAgentStateError) as raised:
                    _load(store, scope, claim, capability, evidence)
                failures.append(raised.value)
                assert raised.value.code == "commit_authority_corrupt"
                assert raised.value.__cause__ is None
                assert raised.value.__context__ is None
            assert failures[0] is not failures[1]

    with monkeypatch.context() as context:
        context.setattr(
            ApprovalCapability,
            "create",
            classmethod(
                lambda cls, **kwargs: (_ for _ in ()).throw(
                    ValueError("private constructor text")
                )
            ),
        )
        with pytest.raises(HeadlessWriteAgentStateError) as raised:
            _load(store, scope, claim, capability, evidence)
        assert raised.value.code == "commit_authority_corrupt"
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    for interruption in (KeyboardInterrupt, SystemExit):
        with monkeypatch.context() as context:
            context.setattr(
                store,
                "_clock",
                lambda: (_ for _ in ()).throw(interruption()),
            )
            with pytest.raises(interruption):
                _load(store, scope, claim, capability, evidence)


def test_load_commit_authority_exposes_no_raw_mapping_private_path_or_forbidden_field(
    store, scope
):
    claim, capability, evidence = _persist_commit_authority(store, scope)
    snapshot = _load(store, scope, claim, capability, evidence)
    second = _load(store, scope, claim, capability, evidence)
    assert snapshot.capability.allowed_paths == second.capability.allowed_paths
    assert snapshot.capability.allowed_paths is not second.capability.allowed_paths
    assert type(snapshot).__name__ == "CommitAuthoritySnapshot"
    assert not isinstance(snapshot, dict)
    assert not isinstance(snapshot.capability, sqlite3.Row)
    assert not isinstance(snapshot.evidence, sqlite3.Row)
    assert not hasattr(snapshot, "payload_json")
    assert not hasattr(snapshot, "database_path")
    assert not hasattr(snapshot, "reservation_id")

    connection = sqlite3.connect(store.path)
    try:
        connection.execute(
            "UPDATE hwa_capabilities SET payload_json = ? WHERE capability_id = ?",
            ("{}", capability.capability_id),
        )
        connection.execute(
            "UPDATE hwa_evidence SET payload_json = ? WHERE evidence_ref = ?",
            ("{}", evidence.evidence_ref),
        )
        connection.commit()
    finally:
        connection.close()
    assert snapshot.capability == capability
    assert snapshot.evidence == evidence

    source = inspect.getsource(HeadlessWriteAgentStateStore.load_commit_authority)
    tree = ast.parse(inspect.cleandoc(source))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls.intersection({"int", "str", "bool", "bytes"})
    assert "_assert_current_claim" not in source
    assert "_assert_effect_allowed" not in source
    assert "SELECT *" not in source.upper()
