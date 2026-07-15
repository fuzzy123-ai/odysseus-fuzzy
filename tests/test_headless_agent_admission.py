from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.agent_pool_scaling import AgentPoolMember
from src.headless_agent_admission import (
    AdmissionDisposition,
    AdmissionPolicy,
    AdmissionRequest,
    HeadlessAgentAdmissionCoordinator,
)
from src.headless_write_agent_state import (
    AdmissionLimits,
    AuthorityScope,
    HeadlessWriteAgentStateError,
    HeadlessWriteAgentStateStore,
)


@dataclass
class FakeClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def store(tmp_path, clock) -> HeadlessWriteAgentStateStore:
    return HeadlessWriteAgentStateStore(tmp_path / "admission.sqlite3", clock=clock)


def _scope(suffix: str = "one", **overrides) -> AuthorityScope:
    values = {
        "owner_id": "owner-one",
        "repo_id": "repo-one",
        "task_id": f"task-{suffix}",
        "plan_id": "project-one",
        "slice_id": f"slice-{suffix}",
        "agent_run_id": f"run-{suffix}",
    }
    values.update(overrides)
    return AuthorityScope.create(**values)


def _agent(**overrides) -> AgentPoolMember:
    values = {
        "agent_id": "bob",
        "role_id": "backend",
        "max_parallel_runs": 2,
        "active_runs": 0,
        "token_budget_remaining": 10_000,
        "allowed_file_roots": ["src", "tests", "app.py"],
    }
    values.update(overrides)
    return AgentPoolMember.create(**values)


def _request(suffix: str = "one", **overrides) -> AdmissionRequest:
    values = {
        "work_id": f"work-{suffix}",
        "scope": _scope(suffix),
        "required_role": "backend",
        "required_files": [f"src/{suffix}.py"],
        "hotfiles": [],
        "estimated_tokens": 1_000,
        "priority": 50,
        "submitted_order": 1,
        "wait_rounds": 0,
        "lease_seconds": 90,
    }
    values.update(overrides)
    return AdmissionRequest.create(**values)


def _limits(**overrides) -> AdmissionLimits:
    values = {
        "max_global_active": 8,
        "max_owner_active": 4,
        "max_project_active": 3,
        "max_agent_active": 2,
    }
    values.update(overrides)
    return AdmissionLimits.create(**values)


def _policy(**overrides) -> AdmissionPolicy:
    values = {
        "limits": _limits(),
        "max_queue_size": 10,
        "reserve_token_floor": 500,
        "fairness_boost_per_round": 5,
        "max_attempts_per_call": 3,
    }
    values.update(overrides)
    return AdmissionPolicy.create(**values)


def _admit_direct(
    store: HeadlessWriteAgentStateStore,
    scope: AuthorityScope,
    *,
    claim_id: str,
    agent: str,
    path: str,
    hot: bool = False,
    limits: AdmissionLimits | None = None,
    lease_seconds: int = 90,
):
    return store.acquire_admitted_claim(
        scope,
        claim_id=claim_id,
        claimant_ref=agent,
        lease_seconds=lease_seconds,
        claimed_paths=(path,),
        hotfiles=((path,) if hot else ()),
        limits=limits or _limits(),
    )


def test_assignment_and_claim_are_one_persisted_admission(store):
    request = _request(hotfiles=["src/one.py"])
    batch = HeadlessAgentAdmissionCoordinator(store).admit_one(
        requests=[request],
        agents=[_agent()],
        policy=_policy(),
    )

    decision = batch.decisions[0]
    persisted = store.get_claim(request.scope)
    assert decision.disposition == AdmissionDisposition.ADMITTED
    assert decision.agent_id == "bob"
    assert decision.fence == persisted.fence
    assert persisted.claimant_ref == "bob"
    assert batch.metrics["active_paths"] == 1
    assert batch.metrics["active_hotfiles"] == 1

    with pytest.raises(HeadlessWriteAgentStateError) as downgrade:
        store.acquire_claim(
            request.scope,
            claim_id="legacy-bypass-claim-0001",
            claimant_ref="alice",
            lease_seconds=90,
        )
    assert downgrade.value.code == "admission_required"


def test_parent_child_path_prefix_and_exact_hotfile_collisions_are_fail_closed(store):
    _admit_direct(
        store,
        _scope("parent"),
        claim_id="admission-parent-0001",
        agent="bob",
        path="src/service",
    )
    with pytest.raises(HeadlessWriteAgentStateError) as prefix_error:
        _admit_direct(
            store,
            _scope("child"),
            claim_id="admission-child-0002",
            agent="alice",
            path="src/service/api.py",
        )
    assert prefix_error.value.code == "path_prefix_collision"

    _admit_direct(
        store,
        _scope("hot-a", repo_id="repo-hot"),
        claim_id="admission-hot-a-0003",
        agent="bob",
        path="app.py",
        hot=True,
    )
    with pytest.raises(HeadlessWriteAgentStateError) as hot_error:
        _admit_direct(
            store,
            _scope("hot-b", repo_id="repo-hot"),
            claim_id="admission-hot-b-0004",
            agent="alice",
            path="app.py",
            hot=True,
        )
    assert hot_error.value.code == "hotfile_collision"


@pytest.mark.parametrize("dimension", ["global", "owner", "project", "agent"])
def test_global_owner_project_and_agent_quotas_apply_inside_claim_transaction(
    tmp_path, clock, dimension
):
    store = HeadlessWriteAgentStateStore(tmp_path / f"quota-{dimension}.sqlite3", clock=clock)
    first_scope = _scope("first")
    second_scope = _scope("second")
    first_agent = "bob"
    second_agent = "alice"
    if dimension == "global":
        limits = _limits(
            max_global_active=1,
            max_owner_active=1,
            max_project_active=1,
            max_agent_active=1,
        )
        second_scope = _scope("second", owner_id="owner-two", repo_id="repo-two", plan_id="project-two")
    elif dimension == "owner":
        limits = _limits(
            max_global_active=2,
            max_owner_active=1,
            max_project_active=1,
            max_agent_active=2,
        )
        second_scope = _scope("second", repo_id="repo-two", plan_id="project-two")
    elif dimension == "project":
        limits = _limits(
            max_global_active=2,
            max_owner_active=2,
            max_project_active=1,
            max_agent_active=2,
        )
    else:
        limits = _limits(
            max_global_active=2,
            max_owner_active=2,
            max_project_active=2,
            max_agent_active=1,
        )
        second_scope = _scope("second", owner_id="owner-two", repo_id="repo-two", plan_id="project-two")
        second_agent = first_agent

    _admit_direct(
        store,
        first_scope,
        claim_id="admission-first-0001",
        agent=first_agent,
        path="src/first.py",
        limits=limits,
    )
    with pytest.raises(HeadlessWriteAgentStateError) as raised:
        _admit_direct(
            store,
            second_scope,
            claim_id="admission-second-0002",
            agent=second_agent,
            path="src/second.py",
            limits=limits,
        )

    assert raised.value.code == "admission_backpressure"
    assert raised.value.detail.startswith(dimension)


def test_bounded_queue_marks_overflow_and_admits_at_most_one(store):
    requests = [
        _request("high", priority=90, submitted_order=1),
        _request("medium", priority=50, submitted_order=2),
        _request("low", priority=10, submitted_order=3),
    ]
    batch = HeadlessAgentAdmissionCoordinator(store).admit_one(
        requests=requests,
        agents=[_agent()],
        policy=_policy(max_queue_size=2),
    )

    by_work = {item.work_id: item for item in batch.decisions}
    assert batch.admitted_count == 1
    assert by_work["work-high"].disposition == AdmissionDisposition.ADMITTED
    assert by_work["work-medium"].disposition == AdmissionDisposition.WAIT
    assert by_work["work-low"].disposition == AdmissionDisposition.WAIT
    assert by_work["work-low"].reason_code == "queue_backpressure"
    assert batch.metrics["overflow_count"] == 1


def test_durable_wait_rounds_bound_priority_starvation(store):
    old_low_priority = _request(
        "old",
        priority=0,
        submitted_order=1,
        wait_rounds=20,
    )
    new_high_priority = _request(
        "new",
        priority=100,
        submitted_order=2,
        wait_rounds=0,
    )
    policy = _policy(fairness_boost_per_round=5)

    batch = HeadlessAgentAdmissionCoordinator(store).admit_one(
        requests=[new_high_priority, old_low_priority],
        agents=[_agent()],
        policy=policy,
    )

    assert batch.decisions[0].work_id == "work-old"
    assert batch.decisions[0].disposition == AdmissionDisposition.ADMITTED
    assert batch.metrics["maximum_starvation_rounds"] == 20


def test_collision_recovery_is_bounded_and_can_admit_next_fair_request(store):
    _admit_direct(
        store,
        _scope("existing"),
        claim_id="admission-existing-0001",
        agent="alice",
        path="src/blocked",
    )
    colliding = _request(
        "blocked",
        scope=_scope("blocked"),
        required_files=["src/blocked/child.py"],
        priority=90,
        submitted_order=1,
    )
    clear = _request("clear", priority=80, submitted_order=2)

    batch = HeadlessAgentAdmissionCoordinator(store).admit_one(
        requests=[colliding, clear],
        agents=[_agent()],
        policy=_policy(max_attempts_per_call=2),
    )
    by_work = {item.work_id: item for item in batch.decisions}

    assert by_work["work-blocked"].reason_code == "path_prefix_collision"
    assert by_work["work-clear"].disposition == AdmissionDisposition.ADMITTED
    assert batch.metrics["attempt_count"] == 2
    assert batch.metrics["collision_count"] == 1


def test_two_coordinators_share_one_authority_store_and_only_one_hotfile_wins(
    tmp_path, clock
):
    path = tmp_path / "multi-instance.sqlite3"
    first_store = HeadlessWriteAgentStateStore(path, clock=clock)
    second_store = HeadlessWriteAgentStateStore(path, clock=clock)
    first = _request(
        "first",
        scope=_scope("first", repo_id="shared-repo"),
        required_files=["app.py"],
        hotfiles=["app.py"],
    )
    second = _request(
        "second",
        scope=_scope("second", repo_id="shared-repo"),
        required_files=["app.py"],
        hotfiles=["app.py"],
    )

    first_batch = HeadlessAgentAdmissionCoordinator(first_store).admit_one(
        requests=[first], agents=[_agent()], policy=_policy()
    )
    second_batch = HeadlessAgentAdmissionCoordinator(second_store).admit_one(
        requests=[second], agents=[_agent(agent_id="alice")], policy=_policy()
    )

    assert first_batch.admitted_count == 1
    assert second_batch.admitted_count == 0
    assert second_batch.decisions[0].reason_code == "hotfile_collision"
    assert second_store.admission_metrics()["active_claims"] == 1


def test_registered_role_path_scope_capacity_and_budget_fail_closed(store):
    cases = [
        (_agent(role_id="docs"), "no_registered_role", AdmissionDisposition.BLOCKED),
        (
            _agent(allowed_file_roots=["docs"]),
            "agent_path_scope_blocked",
            AdmissionDisposition.BLOCKED,
        ),
        (
            _agent(active_runs=2),
            "agent_capacity_or_budget_wait",
            AdmissionDisposition.WAIT,
        ),
        (
            _agent(token_budget_remaining=1_200),
            "agent_capacity_or_budget_wait",
            AdmissionDisposition.WAIT,
        ),
    ]
    for index, (agent, reason, disposition) in enumerate(cases):
        request = _request(f"case-{index}", submitted_order=index)
        batch = HeadlessAgentAdmissionCoordinator(store).admit_one(
            requests=[request],
            agents=[agent],
            policy=_policy(reserve_token_floor=500),
        )
        assert batch.decisions[0].reason_code == reason
        assert batch.decisions[0].disposition == disposition


def test_recovery_metrics_are_bounded_and_survive_reopen(tmp_path, clock):
    path = tmp_path / "metrics.sqlite3"
    store = HeadlessWriteAgentStateStore(path, clock=clock)
    scope = _scope("metrics")
    first = _admit_direct(
        store,
        scope,
        claim_id="admission-metrics-0001",
        agent="bob",
        path="src/metrics.py",
        lease_seconds=30,
    )
    clock.advance(31)
    assert store.admission_metrics()["expired_claims"] == 1
    second = _admit_direct(
        store,
        scope,
        claim_id="admission-metrics-0002",
        agent="bob",
        path="src/metrics.py",
    )

    reopened = HeadlessWriteAgentStateStore(path, clock=clock)
    metrics = reopened.admission_metrics()
    assert second.fence > first.fence
    assert metrics == {
        "active_claims": 1,
        "expired_claims": 0,
        "released_claims": 0,
        "recovered_scopes": 1,
        "max_fence": 2,
        "active_paths": 1,
        "active_hotfiles": 0,
        "paused_scopes": 0,
        "killed_scopes": 0,
    }


def test_admission_module_has_no_second_store_scheduler_or_external_effect_import():
    source = Path("src/headless_agent_admission.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".", 1)[0])

    assert not imported.intersection(
        {"asyncio", "git", "pathlib", "requests", "socket", "sqlite3", "subprocess", "threading"}
    )
