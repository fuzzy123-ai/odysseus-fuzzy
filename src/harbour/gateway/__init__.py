"""Gateway bounded context: Worker Gateway, Sandbox & Execution Runtimes.

This module consolidates the coding agent backend, runner state, sandbox bridges,
headless write agent state, and the worker execution isolation policy.
"""

from __future__ import annotations

from typing import Any

import src.coding_agent_backend as CodingAgentBackend
from src.coding_agent_backend import (
    CodingAgentBackendError,
    CodingCheckCommand,
    CodingCommandResult,
    CodingPatchOperation,
    CodingPatchResult,
    CodingWorktreeTransactionReceipt,
    CodingExactReadReceipt,
    CodingPatchTransactionReport,
    CodingToolCapsule,
    CodingTaskPlan,
    CodingWorktreeReport,
    CodingTransactionalWorktreeReport,
    CodingQualityGateReport,
    CodingDoneGateReport,
    CodingPublishPlan,
    CodingHandoffPlan,
    CodingSubagentContract,
    CodingSubagentPlan,
    apply_coding_patch_transaction,
    apply_coding_patch_set,
    build_coding_publish_plan,
    build_coding_subagent_plan,
    build_coding_task_plan,
    build_coding_tool_capsules,
    build_coding_worktree_transaction_receipt,
    build_transactional_coding_handoff_plan,
    capture_coding_exact_read_receipt,
    create_coding_worktree,
    create_transactional_coding_worktree,
    evaluate_coding_done_gate,
    evaluate_coding_quality_gate,
    evaluate_coding_worktree_gate,
)

from src.coding_agent_runner_state import (
    CodingRunnerState as CodingAgentRunnerState,
    CodingRunnerState,
    CodingRunnerStateStore,
    CodingRunnerStateError,
    transition_from_sandbox_dispatch,
    transition_from_clarification_run,
    transition_from_task_control_event,
    record_advisory_memory_checkpoint,
    record_transactional_worktree_receipt,
)

import src.coding_agent_sandbox_bridge as CodingAgentSandboxBridge
from src.coding_agent_sandbox_bridge import (
    CodingAgentSandboxBridgeError,
    CodingSandboxDispatch,
    build_sandbox_jobs_for_coding_plan,
    dispatch_coding_checks_to_sandbox,
    sandbox_status_to_coding_result,
)

from src.headless_write_agent_state import (
    HeadlessWriteAgentStateStore as HeadlessWriteAgentState,
    HeadlessWriteAgentStateStore,
    HeadlessWriteAgentStateError,
    AuthorityScope,
    ClaimRecord,
    CapabilityRecord,
    CommitAuthoritySnapshot,
    ControlRecord,
    EffectRecord,
    AdmissionLimits,
)

from src.harbour.gateway.coordinator import (
    HarbourWorkerGatewayCoordinator,
    WorkerLeaseTicket,
    WorkerExecutionRequest,
    WorkerExecutionResponse,
    HarbourWorkerGatewayError,
    WorkerGatewayIsolationError,
    WorkerGatewayLeaseError,
    WorkerGatewayQualityGateError,
)

WORKER_EXECUTION_POLICY: dict[str, Any] = {
    "schema": "odysseus.harbour.worker_execution_policy.v1",
    "isolation_mode": "git_worktree_sandbox_pod",
    "lease_required": True,
    "direct_main_process_execution_allowed": False,
    "sandbox_dispatch_required": True,
    "worktree_isolation_required": True,
    "principles": [
        "Isolation in dedicated git worktrees or container sandbox pods",
        "Lease-based execution claims with strict expiration bounds",
        "Never run mutating code or checks directly in the main repository process",
    ],
}

__all__ = [
    # Coordinator & Lease DTOs
    "HarbourWorkerGatewayCoordinator",
    "WorkerLeaseTicket",
    "WorkerExecutionRequest",
    "WorkerExecutionResponse",
    "HarbourWorkerGatewayError",
    "WorkerGatewayIsolationError",
    "WorkerGatewayLeaseError",
    "WorkerGatewayQualityGateError",
    # Execution Policy
    "WORKER_EXECUTION_POLICY",
    # Coding Agent Backend
    "CodingAgentBackend",
    "CodingAgentBackendError",
    "CodingCheckCommand",
    "CodingCommandResult",
    "CodingPatchOperation",
    "CodingPatchResult",
    "CodingWorktreeTransactionReceipt",
    "CodingExactReadReceipt",
    "CodingPatchTransactionReport",
    "CodingToolCapsule",
    "CodingTaskPlan",
    "CodingWorktreeReport",
    "CodingTransactionalWorktreeReport",
    "CodingQualityGateReport",
    "CodingDoneGateReport",
    "CodingPublishPlan",
    "CodingHandoffPlan",
    "CodingSubagentContract",
    "CodingSubagentPlan",
    "apply_coding_patch_transaction",
    "apply_coding_patch_set",
    "build_coding_publish_plan",
    "build_coding_subagent_plan",
    "build_coding_task_plan",
    "build_coding_tool_capsules",
    "build_coding_worktree_transaction_receipt",
    "build_transactional_coding_handoff_plan",
    "capture_coding_exact_read_receipt",
    "create_coding_worktree",
    "create_transactional_coding_worktree",
    "evaluate_coding_done_gate",
    "evaluate_coding_quality_gate",
    "evaluate_coding_worktree_gate",
    # Runner State
    "CodingAgentRunnerState",
    "CodingRunnerState",
    "CodingRunnerStateStore",
    "CodingRunnerStateError",
    "transition_from_sandbox_dispatch",
    "transition_from_clarification_run",
    "transition_from_task_control_event",
    "record_advisory_memory_checkpoint",
    "record_transactional_worktree_receipt",
    # Sandbox Bridge
    "CodingAgentSandboxBridge",
    "CodingAgentSandboxBridgeError",
    "CodingSandboxDispatch",
    "build_sandbox_jobs_for_coding_plan",
    "dispatch_coding_checks_to_sandbox",
    "sandbox_status_to_coding_result",
    # Headless Write Agent State
    "HeadlessWriteAgentState",
    "HeadlessWriteAgentStateStore",
    "HeadlessWriteAgentStateError",
    "AuthorityScope",
    "ClaimRecord",
    "CapabilityRecord",
    "CommitAuthoritySnapshot",
    "ControlRecord",
    "EffectRecord",
    "AdmissionLimits",
]
