import pytest

from src.agent_pool_scaling import (
    AgentPoolDecision,
    AgentPoolMember,
    AgentPoolPolicy,
    AgentPoolScalingError,
    FileLock,
    WorkItem,
    plan_agent_pool_assignments,
)


def _agent(**overrides) -> AgentPoolMember:
    payload = {
        "agent_id": "bob",
        "role_id": "backend",
        "max_parallel_runs": 2,
        "active_runs": 0,
        "token_budget_remaining": 4000,
        "allowed_file_roots": ["src", "tests"],
    }
    payload.update(overrides)
    return AgentPoolMember.create(**payload)


def _work(**overrides) -> WorkItem:
    payload = {
        "work_id": "auto8b",
        "required_role": "backend",
        "required_files": ["src/agent_pool_scaling.py", "tests/test_agent_pool_scaling.py"],
        "estimated_tokens": 1200,
        "priority": 80,
    }
    payload.update(overrides)
    return WorkItem.create(**payload)


def _policy(**overrides) -> AgentPoolPolicy:
    payload = {
        "max_total_parallel_runs": 4,
        "reserve_token_floor": 500,
    }
    payload.update(overrides)
    return AgentPoolPolicy.create(**payload)


def test_assigns_registered_agent_with_capacity_budget_role_and_file_scope():
    plan = plan_agent_pool_assignments(
        agents=[_agent()],
        work_items=[_work()],
        locks=[],
        policy=_policy(),
    )

    assert plan.assigned_count == 1
    assert plan.decisions[0].decision == AgentPoolDecision.ASSIGN
    assert plan.decisions[0].agent_id == "bob"


def test_global_parallel_budget_causes_wait_not_assignment():
    plan = plan_agent_pool_assignments(
        agents=[_agent(active_runs=1)],
        work_items=[_work()],
        locks=[],
        policy=_policy(max_total_parallel_runs=1),
    )

    assert plan.decisions[0].decision == AgentPoolDecision.WAIT
    assert "global parallel" in plan.decisions[0].reason


def test_file_lock_blocks_conflicting_work():
    plan = plan_agent_pool_assignments(
        agents=[_agent()],
        work_items=[_work(required_files=["src/agent_pool_scaling.py"])],
        locks=[
            FileLock.create(
                path="src/agent_pool_scaling.py",
                owner_agent_id="alice",
                work_id="other-slice",
            )
        ],
        policy=_policy(),
    )

    assert plan.decisions[0].decision == AgentPoolDecision.BLOCK
    assert "file locked by alice" in plan.decisions[0].reason


def test_missing_registered_agent_blocks_by_default():
    plan = plan_agent_pool_assignments(
        agents=[_agent(role_id="docs", allowed_file_roots=["docs"])],
        work_items=[_work(required_role="backend")],
        locks=[],
        policy=_policy(require_registered_agent=True),
    )

    assert plan.decisions[0].decision == AgentPoolDecision.BLOCK
    assert "no registered agent" in plan.decisions[0].reason


def test_budget_floor_blocks_candidate():
    plan = plan_agent_pool_assignments(
        agents=[_agent(token_budget_remaining=1000)],
        work_items=[_work(estimated_tokens=900)],
        locks=[],
        policy=_policy(reserve_token_floor=500),
    )

    assert plan.decisions[0].decision == AgentPoolDecision.BLOCK


def test_assignments_consume_agent_capacity_and_budget_in_priority_order():
    plan = plan_agent_pool_assignments(
        agents=[_agent(max_parallel_runs=1, token_budget_remaining=5000)],
        work_items=[
            _work(work_id="low", estimated_tokens=1000, priority=10),
            _work(work_id="high", estimated_tokens=1000, priority=90),
        ],
        locks=[],
        policy=_policy(max_total_parallel_runs=2),
    )

    assert [decision.work_id for decision in plan.decisions] == ["high", "low"]
    assert plan.decisions[0].decision == AgentPoolDecision.ASSIGN
    assert plan.decisions[1].decision == AgentPoolDecision.BLOCK


def test_duplicate_agents_are_rejected():
    with pytest.raises(AgentPoolScalingError, match="agent_id must be unique"):
        plan_agent_pool_assignments(
            agents=[_agent(agent_id="bob"), _agent(agent_id="bob")],
            work_items=[_work()],
            locks=[],
            policy=_policy(),
        )


def test_unsafe_paths_are_rejected():
    with pytest.raises(AgentPoolScalingError, match="repo-relative"):
        _work(required_files=["../outside.py"])
