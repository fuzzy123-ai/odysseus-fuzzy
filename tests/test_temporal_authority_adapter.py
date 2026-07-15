from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.headless_write_agent_state import (
    AdmissionLimits,
    AuthorityScope,
    HeadlessWriteAgentStateError,
    HeadlessWriteAgentStateStore,
)
from src.temporal_runtime.authority_adapter import (
    ActivityAuthorityAdapter,
    ActivityAuthorityError,
    ActivitySpecRegistry,
    RegisteredActivitySpec,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _scope(run: str = "arun-" + "a" * 32) -> AuthorityScope:
    return AuthorityScope.create(
        owner_id="owner-local",
        repo_id="repo-odysseus",
        task_id="task-tlr04",
        plan_id="plan-temporal-light",
        slice_id="slice-activities",
        agent_run_id=run,
    )


def _limits() -> AdmissionLimits:
    return AdmissionLimits.create(
        max_global_active=10,
        max_owner_active=10,
        max_project_active=10,
        max_agent_active=10,
    )


def _spec(*, backend_id: str = "fake-local", lease_seconds: int = 90) -> RegisteredActivitySpec:
    run = "arun-" + "a" * 32
    return RegisteredActivitySpec.create(
        agent_run_id=run,
        node_id="node-one",
        manifest_hash="sha256:" + "a" * 64,
        scope=_scope(run),
        backend_id=backend_id,
        claimant_ref="temporal-local-worker",
        claimed_paths=("src/example.py", "tests/test_example.py"),
        hotfiles=("src/example.py",),
        admission_limits=_limits(),
        lease_seconds=lease_seconds,
    )


def _payload(spec: RegisteredActivitySpec) -> dict[str, object]:
    return {
        "agent_run_id": spec.agent_run_id,
        "manifest_hash": spec.manifest_hash,
        "node_id": spec.node_id,
        "history_segment": 1,
    }


def _adapter(tmp_path, *, clock: MutableClock | None = None, spec=None):
    resolved_clock = clock or MutableClock()
    resolved_spec = spec or _spec()
    store = HeadlessWriteAgentStateStore(tmp_path / "authority.sqlite3", clock=resolved_clock)
    adapter = ActivityAuthorityAdapter(store, ActivitySpecRegistry((resolved_spec,)))
    return store, adapter, resolved_spec, resolved_clock


def test_registry_payload_is_exact_and_cannot_select_paths_backend_or_provider(tmp_path):
    _, adapter, spec, _ = _adapter(tmp_path)

    with pytest.raises(ActivityAuthorityError, match="scope_violation"):
        adapter.authorize({**_payload(spec), "backend_id": "fake-other"}, attempt=1)
    with pytest.raises(ActivityAuthorityError, match="scope_violation"):
        adapter.authorize({**_payload(spec), "provider_response": "private"}, attempt=1)
    with pytest.raises(ActivityAuthorityError, match="invalid_manifest"):
        adapter.authorize(
            {**_payload(spec), "manifest_hash": "sha256:" + "b" * 64},
            attempt=1,
        )


def test_success_receipt_deduplicates_without_second_claim_or_effect(tmp_path):
    store, adapter, spec, _ = _adapter(tmp_path)

    first = adapter.authorize(_payload(spec), attempt=1)
    receipt = adapter.succeed(first)
    second = adapter.authorize(_payload(spec), attempt=1)

    assert receipt.status == "succeeded"
    assert receipt.result_ref
    assert second.is_duplicate
    assert second.duplicate_result_ref == receipt.result_ref
    assert store.get_claim(spec.scope).fence == 1
    assert store.get_claim(spec.scope).state == "released"


def test_active_duplicate_is_blocked_and_expired_reservation_recovers_at_higher_fence(tmp_path):
    clock = MutableClock()
    store, adapter, spec, _ = _adapter(tmp_path, clock=clock, spec=_spec(lease_seconds=2))

    first = adapter.authorize(_payload(spec), attempt=1)
    with pytest.raises(ActivityAuthorityError, match="claim_collision"):
        adapter.authorize(_payload(spec), attempt=1)

    clock.advance(3)
    recovered = adapter.authorize(_payload(spec), attempt=1)

    assert first.claim.fence == 1
    assert recovered.claim.fence == 2
    assert store.get_effect(first.effect_id).lease_fence == 2
    adapter.cancel(recovered)


def test_stale_fence_cannot_write_receipt_after_recovery(tmp_path):
    clock = MutableClock()
    store, adapter, spec, _ = _adapter(tmp_path, clock=clock, spec=_spec(lease_seconds=1))
    stale = adapter.authorize(_payload(spec), attempt=1)
    clock.advance(2)
    current = adapter.authorize(_payload(spec), attempt=1)

    with pytest.raises(HeadlessWriteAgentStateError, match="stale_fence"):
        store.complete_effect(
            spec.scope,
            claim_id=stale.claim.claim_id,
            fence=stale.claim.fence,
            effect_id=stale.effect_id,
            status="succeeded",
            result_ref="receipt-stale",
        )

    receipt = adapter.succeed(current)
    assert receipt.lease_fence == 2
    assert receipt.status == "succeeded"


def test_terminal_effect_receipt_is_immutable(tmp_path):
    store, adapter, spec, _ = _adapter(tmp_path)
    authorized = adapter.authorize(_payload(spec), attempt=1)
    adapter.fail(authorized, failure_code="isolated_failure")

    assert store.get_effect(authorized.effect_id).status == "failed"
    with pytest.raises(ActivityAuthorityError, match="claim_collision"):
        adapter.authorize(_payload(spec), attempt=1)


def test_registration_rejects_scope_rebinding_and_unsafe_path():
    spec = _spec()
    with pytest.raises(ActivityAuthorityError, match="scope_violation"):
        RegisteredActivitySpec.create(
            agent_run_id="arun-" + "b" * 32,
            node_id=spec.node_id,
            manifest_hash=spec.manifest_hash,
            scope=spec.scope,
            backend_id=spec.backend_id,
            claimant_ref=spec.claimant_ref,
            claimed_paths=spec.claimed_paths,
            admission_limits=spec.admission_limits,
        )
    with pytest.raises(ActivityAuthorityError, match="scope_violation"):
        RegisteredActivitySpec.create(
            agent_run_id=spec.agent_run_id,
            node_id=spec.node_id,
            manifest_hash=spec.manifest_hash,
            scope=spec.scope,
            backend_id=spec.backend_id,
            claimant_ref=spec.claimant_ref,
            claimed_paths=("../outside",),
            admission_limits=spec.admission_limits,
        )
