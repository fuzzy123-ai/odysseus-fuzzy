"""Tests for HarbourWorkerGatewayCoordinator and execution isolation."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from src.coding_agent_backend import CodingCommandResult
from src.coding_agent_runner_state import CodingRunnerStateStore
from src.harbour.gateway import (
    HarbourWorkerGatewayCoordinator,
    HarbourWorkerGatewayError,
    WorkerGatewayIsolationError,
    WorkerGatewayLeaseError,
    WorkerGatewayQualityGateError,
    WorkerLeaseTicket,
    WorkerExecutionRequest,
    WorkerExecutionResponse,
    WORKER_EXECUTION_POLICY,
)
from src.harbour.mcp import HarbourMcpDispatcher, HarbourMcpDispatchError
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity


def test_main_process_isolation_guard(tmp_path: Path):
    """Enforces that execution in workspace_base or the main git tree is strictly forbidden."""
    workspace_repo = tmp_path / "main_repo"
    workspace_repo.mkdir()
    (workspace_repo / ".git").mkdir()

    isolated_worktree = tmp_path / "sandboxes" / "task_worktree"
    isolated_worktree.mkdir(parents=True)

    coordinator = HarbourWorkerGatewayCoordinator(workspace_base=workspace_repo)

    # 1. Direct validation of workspace_base must raise WorkerGatewayIsolationError
    with pytest.raises(WorkerGatewayIsolationError) as exc:
        coordinator.validate_worktree_isolation(workspace_repo)
    assert "main_process_execution_forbidden" in str(exc.value)

    # 2. None worktree_path must raise WorkerGatewayIsolationError
    with pytest.raises(WorkerGatewayIsolationError) as exc:
        coordinator.validate_worktree_isolation(None)
    assert "main_process_execution_forbidden" in str(exc.value)

    # 3. Isolated worktree passes cleanly
    validated_target = coordinator.validate_worktree_isolation(isolated_worktree)
    assert validated_target == isolated_worktree.resolve()

    # 4. plan_and_lease_task rejects direct main process execution flag
    with pytest.raises(WorkerGatewayIsolationError) as exc:
        coordinator.plan_and_lease_task({
            "task_id": "test-task-direct",
            "direct_main_process_execution_allowed": True,
        })
    assert "main_process_execution_forbidden" in str(exc.value)

    # 5. plan_and_lease_task rejects worktree_path pointing to workspace_base
    with pytest.raises(WorkerGatewayIsolationError) as exc:
        coordinator.plan_and_lease_task({
            "task_id": "test-task-unisolated",
            "worktree_path": str(workspace_repo),
        })
    assert "main_process_execution_forbidden" in str(exc.value)


def test_mandatory_lease_and_path_collision_guard(tmp_path: Path):
    """Enforces lease-based execution claims and prevents overlapping concurrent write paths."""
    runner_store = CodingRunnerStateStore(tmp_path / "runner_state")
    coordinator = HarbourWorkerGatewayCoordinator(
        workspace_base=tmp_path / "repo",
        runner_store=runner_store,
    )

    # Task 1 acquires lease for a specific file
    ticket1 = coordinator.acquire_worker_lease(
        task_id="task-1",
        write_paths=["src/harbour/gateway/coordinator.py"],
        ttl_seconds=300,
    )
    assert ticket1.is_active is True
    assert ticket1.task_id == "task-1"
    assert ticket1.fence == 1
    assert ticket1.write_paths == ("src/harbour/gateway/coordinator.py",)

    # Task 2 requesting exact same path must fail with active_scope_collision
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.acquire_worker_lease(
            task_id="task-2",
            write_paths=["src/harbour/gateway/coordinator.py"],
            ttl_seconds=300,
        )
    assert "active_scope_collision" in str(exc.value)

    # Task 3 requesting parent directory must fail with active_scope_collision
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.acquire_worker_lease(
            task_id="task-3",
            write_paths=["src/harbour/gateway"],
            ttl_seconds=300,
        )
    assert "active_scope_collision" in str(exc.value)

    # Task 4 requesting child path must fail with active_scope_collision
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.acquire_worker_lease(
            task_id="task-4",
            write_paths=["src/harbour/gateway/coordinator.py/child.py"],
            ttl_seconds=300,
        )
    assert "active_scope_collision" in str(exc.value)

    # Task 5 requesting repository root '.' must fail with active_scope_collision
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.acquire_worker_lease(
            task_id="task-5",
            write_paths=["."],
            ttl_seconds=300,
        )
    assert "active_scope_collision" in str(exc.value)

    # Task 6 requesting a completely different path must succeed
    ticket6 = coordinator.acquire_worker_lease(
        task_id="task-6",
        write_paths=["src/harbour/inbox/service.py"],
        ttl_seconds=300,
    )
    assert ticket6.is_active is True
    assert ticket6.task_id == "task-6"
    assert ticket6.fence == 2


def test_lease_ttl_expiration_and_renewal(tmp_path: Path):
    """Enforces expiration semantics, renewals, and expiration-gated operations."""
    runner_store = CodingRunnerStateStore(tmp_path / "runner_state")
    coordinator = HarbourWorkerGatewayCoordinator(
        workspace_base=tmp_path / "repo",
        runner_store=runner_store,
    )

    ticket = coordinator.acquire_worker_lease(
        task_id="task-ttl",
        write_paths=["src/harbour/core/store.py"],
        ttl_seconds=300,
    )
    assert ticket.fence == 1

    # Renew active lease
    renewed = coordinator.renew_worker_lease(ticket.lease_id, extension_seconds=600)
    assert renewed.is_active is True
    assert renewed.fence == 2
    assert renewed.lease_expires_at > ticket.lease_expires_at

    # Unknown lease renewal
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.renew_worker_lease("nonexistent_lease_id")
    assert "lease_not_found" in str(exc.value)

    # Simulate expiration by acquiring with negative TTL
    expired_ticket = coordinator.acquire_worker_lease(
        task_id="task-expired",
        write_paths=["src/harbour/memory/usi.py"],
        ttl_seconds=-10,
    )

    # Expired lease cannot be renewed
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.renew_worker_lease(expired_ticket.lease_id)
    assert "lease_expired" in str(exc.value)

    # Expired lease cannot be released normally
    with pytest.raises(WorkerGatewayLeaseError) as exc:
        coordinator.release_worker_lease(expired_ticket.lease_id)
    assert "lease_expired" in str(exc.value)

    # Expired lease allows new task to acquire same path without collision
    new_ticket = coordinator.acquire_worker_lease(
        task_id="task-new-successor",
        write_paths=["src/harbour/memory/usi.py"],
        ttl_seconds=300,
    )
    assert new_ticket.is_active is True
    assert new_ticket.task_id == "task-new-successor"

    # Normal release of active lease
    released = coordinator.release_worker_lease(renewed.lease_id)
    assert released.is_active is False

    # Once released, get_lease shows is_active is False
    status_ticket = coordinator.get_lease(renewed.lease_id)
    assert status_ticket is not None
    assert status_ticket.is_active is False


def test_quality_gate_evaluation_and_lease_release(tmp_path: Path):
    """Enforces quality gate evaluation and atomic release on accepted gate outcome."""
    runner_store = CodingRunnerStateStore(tmp_path / "runner_state")
    coordinator = HarbourWorkerGatewayCoordinator(
        workspace_base=tmp_path / "repo",
        runner_store=runner_store,
    )

    ticket, state = coordinator.plan_and_lease_task({
        "task_id": "task-qg-1",
        "write_paths": ["src/service.py"],
        "allowed_paths": ["src/service.py"],
    })
    assert ticket.is_active is True
    assert state.task_id == "task-qg-1"
    assert state.phase == "scoped"

    # 1. Failing check evaluation blocks release
    fail_result = CodingCommandResult(
        exit_code=1,
        stdout="",
        stderr="FAILED: assertion error",
        duration_seconds=0.25,
    )
    blocked_response = coordinator.evaluate_and_release(
        task_id="task-qg-1",
        lease_id=ticket.lease_id,
        changed_paths=["src/service.py"],
        check_results=[fail_result],
        allowed_paths=["src/service.py"],
    )
    assert blocked_response.release_outcome == "blocked"
    assert blocked_response.lease_released is False
    assert len(blocked_response.blockers) > 0
    # Lease must still be active
    assert coordinator.get_lease(ticket.lease_id).is_active is True

    # 2. Passing check evaluation releases lease with accepted outcome
    pass_result = CodingCommandResult(
        exit_code=0,
        stdout="1 passed in 0.05s",
        stderr="",
        duration_seconds=0.05,
    )
    accepted_response = coordinator.evaluate_and_release(
        task_id="task-qg-1",
        lease_id=ticket.lease_id,
        changed_paths=["src/service.py"],
        check_results=[pass_result],
        allowed_paths=["src/service.py"],
    )
    assert accepted_response.release_outcome == "accepted"
    assert accepted_response.lease_released is True
    assert len(accepted_response.blockers) == 0
    # Lease must now be released
    assert coordinator.get_lease(ticket.lease_id).is_active is False


def test_dispatcher_gateway_integration(tmp_path: Path):
    """Dispatcher integrates with HarbourWorkerGatewayCoordinator preserving all contracts."""
    runner_store = CodingRunnerStateStore(tmp_path / "runner_state")
    coordinator = HarbourWorkerGatewayCoordinator(
        workspace_base=tmp_path / "repo",
        runner_store=runner_store,
    )
    dispatcher = HarbourMcpDispatcher(
        repo_root=tmp_path / "repo",
        gateway_coordinator=coordinator,
    )
    identity = McpAuthenticatedIdentity("tok", "adm", "user-42", 1, 1, "mcp:harbour:gateway")

    # 1. Dispatch valid task plan
    dispatch_res = dispatcher.dispatch(
        "harbour_gateway_dispatch_task",
        {
            "task_plan": {
                "task_id": "mcp-task-1",
                "isolation_mode": "git_worktree_sandbox_pod",
                "lease_required": True,
                "authorizes_mutation": False,
                "write_paths": ["src/feature.py"],
            }
        },
        identity=identity,
    )
    assert dispatch_res["schema"] == "odysseus.harbour.gateway_dispatch_result.v1"
    assert dispatch_res["status"] == "dispatched"
    assert dispatch_res["task_id"] == "mcp-task-1"
    assert "lease_id" in dispatch_res
    lease_id = dispatch_res["lease_id"]

    # 2. Conflicting dispatch fails with lease_error
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_gateway_dispatch_task",
            {
                "task_plan": {
                    "task_id": "mcp-task-conflict",
                    "isolation_mode": "git_worktree_sandbox_pod",
                    "lease_required": True,
                    "authorizes_mutation": False,
                    "write_paths": ["src/feature.py"],
                }
            },
            identity=identity,
        )
    assert exc.value.code == "lease_error"
    assert "active_scope_collision" in str(exc.value)

    # 3. Unisolated worktree_path fails with policy_violation
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch(
            "harbour_gateway_dispatch_task",
            {
                "task_plan": {
                    "task_id": "mcp-task-unisolated",
                    "isolation_mode": "git_worktree_sandbox_pod",
                    "lease_required": True,
                    "authorizes_mutation": False,
                    "worktree_path": str(tmp_path / "repo"),
                }
            },
            identity=identity,
        )
    assert exc.value.code == "policy_violation"

    # 4. Get runner state for dispatched task
    state_res = dispatcher.dispatch(
        "harbour_gateway_get_runner_state",
        {"task_id": "mcp-task-1"},
        identity=identity,
    )
    assert state_res["schema"] == "odysseus.harbour.runner_state.v1"
    assert state_res["found"] is True
    assert state_res["status"] == "scoped"
    assert state_res["task_id"] == "mcp-task-1"

    # 5. Evaluate quality gate via dispatcher
    qg_res = dispatcher.dispatch(
        "harbour_gateway_evaluate_quality_gate",
        {"task_id": "mcp-task-1"},
        identity=identity,
    )
    assert qg_res["schema"] == "odysseus.harbour.quality_gate_evaluation.v1"
    assert qg_res["task_id"] == "mcp-task-1"
    assert "quality_gate" in qg_res


def test_dto_serialization_and_immutability():
    """Validates frozen dataclass invariants and serialization for gateway DTOs."""
    ticket = WorkerLeaseTicket(
        lease_id="lease-1",
        claim_id="claim-1",
        owner_principal_id="user-1",
        task_id="task-1",
        repo_id="odysseus",
        write_paths=("a.py", "b.py"),
        read_only_paths=("c.py",),
        lease_expires_at="2026-10-25T18:00:00Z",
        fence=1,
        is_active=True,
    )
    d = ticket.to_dict()
    assert d["lease_id"] == "lease-1"
    assert d["write_paths"] == ["a.py", "b.py"]
    assert d["fence"] == 1
    assert d["is_active"] is True

    # Immutability check
    with pytest.raises(Exception):
        ticket.is_active = False

    req = WorkerExecutionRequest(
        task_plan={"task_id": "task-req-1"},
        owner_principal_id="user-2",
        lease_seconds=60,
    )
    req_dict = req.to_dict()
    assert req_dict["owner_principal_id"] == "user-2"
    assert req_dict["lease_seconds"] == 60

    resp = WorkerExecutionResponse(
        task_id="task-resp-1",
        lease_ticket=ticket,
        release_outcome="accepted",
        lease_released=True,
    )
    resp_dict = resp.to_dict()
    assert resp_dict["task_id"] == "task-resp-1"
    assert resp_dict["lease_released"] is True
    assert resp_dict["release_outcome"] == "accepted"
    assert resp_dict["lease_ticket"]["lease_id"] == "lease-1"
