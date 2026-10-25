"""Harbour Worker Gateway Coordinator.

Enforces strict decoupling of core platform and execution runtimes via:
- Path-isolated worker execution leases with fencing tokens and expiration bounds
- Strict main process / workspace_base isolation guards
- Integration with CodingRunnerStateStore and Coding Agent Backend Quality Gates
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping
import uuid

from src.coding_agent_backend import (
    CodingCommandResult,
    evaluate_coding_quality_gate,
)
from src.coding_agent_runner_state import (
    CodingRunnerState,
    CodingRunnerStateStore,
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


class HarbourWorkerGatewayError(ValueError):
    """Base exception for all Harbour Worker Gateway errors."""


class WorkerGatewayIsolationError(HarbourWorkerGatewayError):
    """Raised when worker execution violates worktree or main process isolation."""


class WorkerGatewayLeaseError(HarbourWorkerGatewayError):
    """Raised when worker lease acquisition, renewal, or collision fails."""


class WorkerGatewayQualityGateError(HarbourWorkerGatewayError):
    """Raised when quality gate evaluation fails or blocks execution."""


@dataclass(frozen=True, slots=True)
class WorkerLeaseTicket:
    """Immutable ticket representing a granted execution lease."""

    lease_id: str
    claim_id: str
    owner_principal_id: str
    task_id: str
    repo_id: str
    write_paths: tuple[str, ...]
    read_only_paths: tuple[str, ...]
    lease_expires_at: str
    fence: int
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "claim_id": self.claim_id,
            "owner_principal_id": self.owner_principal_id,
            "task_id": self.task_id,
            "repo_id": self.repo_id,
            "write_paths": list(self.write_paths),
            "read_only_paths": list(self.read_only_paths),
            "lease_expires_at": self.lease_expires_at,
            "fence": self.fence,
            "is_active": self.is_active,
        }


@dataclass(frozen=True, slots=True)
class WorkerExecutionRequest:
    """Typed execution request dispatched to worker runtimes."""

    task_plan: Any
    owner_principal_id: str = "subject:anonymous"
    operator_go: bool = False
    live_enabled: bool = False
    lease_seconds: int = 300
    implementer_id: str = "headless_worker"
    auto_release_on_completion: bool = False

    def to_dict(self) -> dict[str, Any]:
        plan_dict = (
            self.task_plan.to_dict()
            if hasattr(self.task_plan, "to_dict")
            else self.task_plan
            if isinstance(self.task_plan, dict)
            else str(self.task_plan)
        )
        return {
            "task_plan": plan_dict,
            "owner_principal_id": self.owner_principal_id,
            "operator_go": self.operator_go,
            "live_enabled": self.live_enabled,
            "lease_seconds": self.lease_seconds,
            "implementer_id": self.implementer_id,
            "auto_release_on_completion": self.auto_release_on_completion,
        }


@dataclass(frozen=True, slots=True)
class WorkerExecutionResponse:
    """Consolidated outcome of task planning, execution, and gate verification."""

    task_id: str
    lease_ticket: WorkerLeaseTicket | None = None
    runner_state: dict[str, Any] | None = None
    worktree_report: dict[str, Any] | None = None
    sandbox_dispatch: dict[str, Any] | None = None
    quality_gate: dict[str, Any] | None = None
    verification_receipt: dict[str, Any] | None = None
    lease_released: bool = False
    release_outcome: str = ""
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "lease_ticket": self.lease_ticket.to_dict() if self.lease_ticket else None,
            "runner_state": self.runner_state,
            "worktree_report": self.worktree_report,
            "sandbox_dispatch": self.sandbox_dispatch,
            "quality_gate": self.quality_gate,
            "verification_receipt": self.verification_receipt,
            "lease_released": self.lease_released,
            "release_outcome": self.release_outcome,
            "blockers": list(self.blockers),
        }


def _normalize_relative_path(path: str | Path) -> str:
    p = str(path).strip().replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/")
    return p


def _paths_collide(p1: str, p2: str) -> bool:
    norm1 = _normalize_relative_path(p1)
    norm2 = _normalize_relative_path(p2)
    if not norm1 or not norm2:
        return False
    if norm1 == "." or norm2 == ".":
        return True
    if norm1 == norm2:
        return True
    if norm1.startswith(norm2 + "/") or norm2.startswith(norm1 + "/"):
        return True
    return False


def _scopes_collide(paths1: Iterable[str], paths2: Iterable[str]) -> tuple[bool, str, str]:
    for p1 in paths1:
        for p2 in paths2:
            if _paths_collide(p1, p2):
                return True, str(p1), str(p2)
    return False, "", ""


def _is_expired(expires_at_iso: str, now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        exp = datetime.fromisoformat(expires_at_iso)
    except Exception:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return now >= exp


class HarbourWorkerGatewayCoordinator:
    """Coordinates worker execution leases, isolation boundaries, runner state, and quality gates."""

    def __init__(
        self,
        workspace_base: str | Path | None = None,
        runner_store: CodingRunnerStateStore | None = None,
    ) -> None:
        self.workspace_base = Path(workspace_base).resolve() if workspace_base is not None else Path.cwd().resolve()
        self.runner_store = runner_store or CodingRunnerStateStore()
        self._active_leases: dict[str, WorkerLeaseTicket] = {}
        self._fence_counter: int = 0
        self._lock = threading.Lock()

    def validate_worktree_isolation(self, worktree_path: str | Path | None = None) -> Path:
        """Validate that worktree is strictly isolated from the main process and workspace_base.

        Raises WorkerGatewayIsolationError if execution in main process or unisolated worktree is attempted.
        """
        if not WORKER_EXECUTION_POLICY.get("worktree_isolation_required", True):
            raise WorkerGatewayIsolationError("worktree_isolation_required is false in execution policy")
        if WORKER_EXECUTION_POLICY.get("direct_main_process_execution_allowed", False):
            raise WorkerGatewayIsolationError("direct_main_process_execution_allowed must be false")
        if worktree_path is None:
            raise WorkerGatewayIsolationError("main_process_execution_forbidden: worktree_path is required")

        target = Path(worktree_path).resolve()
        base = self.workspace_base.resolve()
        if target == base:
            raise WorkerGatewayIsolationError(
                "main_process_execution_forbidden: worktree_path cannot be the main repository workspace"
            )
        # Check if target is the main git working tree (where .git is a directory matching workspace_base/.git)
        if (target / ".git").is_dir() and (target / ".git") == (base / ".git"):
            raise WorkerGatewayIsolationError(
                "main_process_execution_forbidden: worktree_path points to the main git working tree"
            )
        return target

    def _purge_expired_leases_locked(self) -> None:
        now = datetime.now(timezone.utc)
        for lease_id, lease in list(self._active_leases.items()):
            if lease.is_active and _is_expired(lease.lease_expires_at, now):
                self._active_leases[lease_id] = WorkerLeaseTicket(
                    lease_id=lease.lease_id,
                    claim_id=lease.claim_id,
                    owner_principal_id=lease.owner_principal_id,
                    task_id=lease.task_id,
                    repo_id=lease.repo_id,
                    write_paths=lease.write_paths,
                    read_only_paths=lease.read_only_paths,
                    lease_expires_at=lease.lease_expires_at,
                    fence=lease.fence,
                    is_active=False,
                )

    def acquire_worker_lease(
        self,
        task_id: str,
        claim_id: str = "",
        owner_principal_id: str = "subject:anonymous",
        repo_id: str = "odysseus",
        write_paths: Iterable[str] = (),
        read_only_paths: Iterable[str] = (),
        ttl_seconds: int = 300,
    ) -> WorkerLeaseTicket:
        """Acquire a path-isolated worker lease.

        Raises WorkerGatewayLeaseError with 'active_scope_collision' if write_paths collide with an active lease.
        """
        norm_write = tuple(_normalize_relative_path(p) for p in write_paths if str(p).strip())
        norm_read = tuple(_normalize_relative_path(p) for p in read_only_paths if str(p).strip())

        with self._lock:
            self._purge_expired_leases_locked()

            # Collision check against active non-expired leases of other tasks
            for active_lease in self._active_leases.values():
                if not active_lease.is_active:
                    continue
                if active_lease.task_id == task_id:
                    continue
                collides, p1, p2 = _scopes_collide(norm_write, active_lease.write_paths)
                if collides:
                    raise WorkerGatewayLeaseError(
                        f"active_scope_collision: requested path '{p1}' conflicts with active lease "
                        f"'{active_lease.lease_id}' path '{p2}' for task '{active_lease.task_id}'"
                    )

            lease_id = f"lease_{uuid.uuid4().hex[:12]}"
            effective_claim = claim_id or f"claim_{task_id}"
            self._fence_counter += 1
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()

            ticket = WorkerLeaseTicket(
                lease_id=lease_id,
                claim_id=effective_claim,
                owner_principal_id=owner_principal_id,
                task_id=task_id,
                repo_id=repo_id,
                write_paths=norm_write,
                read_only_paths=norm_read,
                lease_expires_at=expires_at,
                fence=self._fence_counter,
                is_active=True,
            )
            self._active_leases[lease_id] = ticket
            return ticket

    def renew_worker_lease(
        self,
        lease_id: str,
        extension_seconds: int = 300,
    ) -> WorkerLeaseTicket:
        """Renew an existing active lease. Raises WorkerGatewayLeaseError('lease_expired') if expired."""
        with self._lock:
            lease = self._active_leases.get(lease_id)
            if lease is None:
                raise WorkerGatewayLeaseError(f"lease_not_found: lease '{lease_id}' does not exist")
            if not lease.is_active or _is_expired(lease.lease_expires_at):
                if lease.is_active:
                    self._active_leases[lease_id] = WorkerLeaseTicket(
                        lease_id=lease.lease_id,
                        claim_id=lease.claim_id,
                        owner_principal_id=lease.owner_principal_id,
                        task_id=lease.task_id,
                        repo_id=lease.repo_id,
                        write_paths=lease.write_paths,
                        read_only_paths=lease.read_only_paths,
                        lease_expires_at=lease.lease_expires_at,
                        fence=lease.fence,
                        is_active=False,
                    )
                raise WorkerGatewayLeaseError("lease_expired")

            self._fence_counter += 1
            new_expires = (datetime.now(timezone.utc) + timedelta(seconds=extension_seconds)).isoformat()
            renewed = WorkerLeaseTicket(
                lease_id=lease.lease_id,
                claim_id=lease.claim_id,
                owner_principal_id=lease.owner_principal_id,
                task_id=lease.task_id,
                repo_id=lease.repo_id,
                write_paths=lease.write_paths,
                read_only_paths=lease.read_only_paths,
                lease_expires_at=new_expires,
                fence=self._fence_counter,
                is_active=True,
            )
            self._active_leases[lease_id] = renewed
            return renewed

    def release_worker_lease(
        self,
        lease_id: str,
        check_expiry: bool = True,
    ) -> WorkerLeaseTicket:
        """Release an active lease. Raises WorkerGatewayLeaseError('lease_expired') if expired and check_expiry=True."""
        with self._lock:
            lease = self._active_leases.get(lease_id)
            if lease is None:
                raise WorkerGatewayLeaseError(f"lease_not_found: lease '{lease_id}' does not exist")
            if check_expiry and _is_expired(lease.lease_expires_at):
                if lease.is_active:
                    self._active_leases[lease_id] = WorkerLeaseTicket(
                        lease_id=lease.lease_id,
                        claim_id=lease.claim_id,
                        owner_principal_id=lease.owner_principal_id,
                        task_id=lease.task_id,
                        repo_id=lease.repo_id,
                        write_paths=lease.write_paths,
                        read_only_paths=lease.read_only_paths,
                        lease_expires_at=lease.lease_expires_at,
                        fence=lease.fence,
                        is_active=False,
                    )
                raise WorkerGatewayLeaseError("lease_expired")

            released = WorkerLeaseTicket(
                lease_id=lease.lease_id,
                claim_id=lease.claim_id,
                owner_principal_id=lease.owner_principal_id,
                task_id=lease.task_id,
                repo_id=lease.repo_id,
                write_paths=lease.write_paths,
                read_only_paths=lease.read_only_paths,
                lease_expires_at=lease.lease_expires_at,
                fence=lease.fence,
                is_active=False,
            )
            self._active_leases[lease_id] = released
            return released

    def get_lease(self, lease_id: str) -> WorkerLeaseTicket | None:
        """Retrieve a lease ticket by id, updating its active status if expired."""
        with self._lock:
            lease = self._active_leases.get(lease_id)
            if lease is None:
                return None
            if lease.is_active and _is_expired(lease.lease_expires_at):
                lease = WorkerLeaseTicket(
                    lease_id=lease.lease_id,
                    claim_id=lease.claim_id,
                    owner_principal_id=lease.owner_principal_id,
                    task_id=lease.task_id,
                    repo_id=lease.repo_id,
                    write_paths=lease.write_paths,
                    read_only_paths=lease.read_only_paths,
                    lease_expires_at=lease.lease_expires_at,
                    fence=lease.fence,
                    is_active=False,
                )
                self._active_leases[lease_id] = lease
            return lease

    def plan_and_lease_task(
        self,
        task_plan_or_request: WorkerExecutionRequest | Mapping[str, Any] | Any,
        *,
        owner_principal_id: str | None = None,
        lease_seconds: int = 300,
        write_paths: Iterable[str] = (),
        read_only_paths: Iterable[str] = (),
        claim_id: str | None = None,
    ) -> tuple[WorkerLeaseTicket, CodingRunnerState]:
        """Acquire lease, initialize/upsert runner state, and return (ticket, state)."""
        if isinstance(task_plan_or_request, WorkerExecutionRequest):
            plan = task_plan_or_request.task_plan
            owner = owner_principal_id or task_plan_or_request.owner_principal_id
            ttl = task_plan_or_request.lease_seconds
        else:
            plan = task_plan_or_request
            owner = owner_principal_id or "subject:anonymous"
            ttl = lease_seconds

        # Check policy constraints if plan is a Mapping
        if isinstance(plan, Mapping):
            if plan.get("direct_main_process_execution_allowed") is True:
                raise WorkerGatewayIsolationError("main_process_execution_forbidden: direct execution forbidden")
            if plan.get("authorizes_mutation") is True:
                raise WorkerGatewayIsolationError("task_plan cannot authorize mutation directly")
            if "worktree_path" in plan:
                self.validate_worktree_isolation(plan["worktree_path"])

        task_id = (
            getattr(plan, "task_id", None)
            or (plan.get("task_id") if isinstance(plan, Mapping) else None)
        )
        if not task_id:
            raw_sig = (
                str(plan).encode("utf-8")
                if not isinstance(plan, Mapping)
                else json.dumps(dict(plan), sort_keys=True).encode("utf-8")
            )
            task_id = f"task_{hashlib.sha256(raw_sig).hexdigest()[:12]}"

        repo_id = (
            getattr(plan, "repo_id", None)
            or (plan.get("repo_id") if isinstance(plan, Mapping) else None)
            or "odysseus"
        )

        plan_writes = (
            write_paths
            or (plan.get("write_paths") if isinstance(plan, Mapping) else getattr(plan, "write_paths", None))
            or (plan.get("allowed_paths") if isinstance(plan, Mapping) else getattr(plan, "allowed_paths", None))
            or ()
        )
        plan_reads = (
            read_only_paths
            or (plan.get("read_only_paths") if isinstance(plan, Mapping) else getattr(plan, "read_only_paths", None))
            or ()
        )

        effective_claim = (
            claim_id
            or (plan.get("claim_id") if isinstance(plan, Mapping) else getattr(plan, "claim_id", ""))
            or f"claim_{task_id}"
        )

        ticket = self.acquire_worker_lease(
            task_id=str(task_id),
            claim_id=str(effective_claim),
            owner_principal_id=owner,
            repo_id=str(repo_id),
            write_paths=plan_writes,
            read_only_paths=plan_reads,
            ttl_seconds=ttl,
        )

        # Update CodingRunnerStateStore
        if hasattr(self.runner_store, "upsert_from_task_plan") and hasattr(plan, "decision"):
            state = self.runner_store.upsert_from_task_plan(plan)
        elif isinstance(plan, Mapping):
            phase = "scoped" if not plan.get("blockers") else "blocked"
            progress = 20 if phase == "scoped" else 0
            state = CodingRunnerState.create(
                task_id=str(task_id),
                repo_id=str(repo_id),
                phase=phase,
                progress_percent=progress,
                gates_waiting=tuple(plan.get("gates_waiting") or ()),
                blockers=tuple(plan.get("blockers") or ()),
                next_human_decision=str(plan.get("next_human_decision") or ""),
            )
            state = self.runner_store.write(state)
        else:
            state = CodingRunnerState.create(
                task_id=str(task_id),
                repo_id=str(repo_id),
                phase="scoped",
                progress_percent=20,
            )
            state = self.runner_store.write(state)

        return ticket, state

    def get_runner_state(self, task_id: str) -> CodingRunnerState | None:
        """Retrieve runner state from store."""
        return self.runner_store.read(task_id)

    def evaluate_task_quality_gate(
        self,
        task_id: str,
        changed_paths: Iterable[Any] = (),
        check_results: Iterable[CodingCommandResult] = (),
        allowed_paths: Iterable[Any] = (),
        blocked_paths: Iterable[Any] = (),
        lease_id: str | None = None,
        auto_release_on_accepted: bool = False,
    ) -> dict[str, Any]:
        """Evaluate task quality gate using coding agent backend."""
        report = evaluate_coding_quality_gate(
            changed_paths=changed_paths,
            check_results=check_results,
            allowed_paths=allowed_paths,
            blocked_paths=blocked_paths,
        )
        outcome = "accepted" if report.status == "verified" else "blocked"
        lease_released = False
        if lease_id is not None:
            if outcome == "accepted" and auto_release_on_accepted:
                self.release_worker_lease(lease_id)
                lease_released = True

        return {
            "schema": "odysseus.harbour.quality_gate_evaluation.v1",
            "task_id": task_id,
            "status": report.status,
            "outcome": outcome,
            "quality_gate": report.to_dict(),
            "lease_released": lease_released,
            "release_outcome": outcome,
            "blockers": list(report.blockers),
        }

    def evaluate_and_release(
        self,
        task_id: str,
        lease_id: str,
        changed_paths: Iterable[Any] = (),
        check_results: Iterable[CodingCommandResult] = (),
        allowed_paths: Iterable[Any] = (),
        blocked_paths: Iterable[Any] = (),
    ) -> WorkerExecutionResponse:
        """Evaluate quality gate and release lease if accepted, or keep active if blocked."""
        eval_res = self.evaluate_task_quality_gate(
            task_id=task_id,
            changed_paths=changed_paths,
            check_results=check_results,
            allowed_paths=allowed_paths,
            blocked_paths=blocked_paths,
        )
        outcome = eval_res["outcome"]
        lease_released = False
        ticket = self.get_lease(lease_id)

        if outcome == "accepted":
            ticket = self.release_worker_lease(lease_id)
            lease_released = True

        runner_state = self.get_runner_state(task_id)

        return WorkerExecutionResponse(
            task_id=task_id,
            lease_ticket=ticket,
            runner_state=runner_state.to_dict() if runner_state else None,
            quality_gate=eval_res["quality_gate"],
            lease_released=lease_released,
            release_outcome=outcome,
            blockers=tuple(eval_res.get("blockers") or ()),
        )


__all__ = [
    "WORKER_EXECUTION_POLICY",
    "HarbourWorkerGatewayError",
    "WorkerGatewayIsolationError",
    "WorkerGatewayLeaseError",
    "WorkerGatewayQualityGateError",
    "WorkerLeaseTicket",
    "WorkerExecutionRequest",
    "WorkerExecutionResponse",
    "HarbourWorkerGatewayCoordinator",
]
