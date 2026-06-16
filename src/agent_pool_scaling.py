"""N-agent scaling model for orchestration planning.

AUTO8 is a design/model slice only. It allocates known agents to queued work
under budgets and locks; it never creates agents or dispatches real messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_TEXT = 180
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]+$")
_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")


class AgentPoolScalingError(ValueError):
    """Raised when an agent pool scaling payload is unsafe or invalid."""


class AgentPoolDecision(StrEnum):
    ASSIGN = "assign"
    WAIT = "wait"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class AgentPoolMember:
    agent_id: str
    role_id: str
    max_parallel_runs: int
    active_runs: int
    token_budget_remaining: int
    allowed_file_roots: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        agent_id: Any,
        role_id: Any,
        max_parallel_runs: Any,
        active_runs: Any,
        token_budget_remaining: Any,
        allowed_file_roots: Iterable[Any],
    ) -> "AgentPoolMember":
        max_parallel = _int(max_parallel_runs, field_name="max_parallel_runs")
        active = _int(active_runs, field_name="active_runs")
        budget = _int(token_budget_remaining, field_name="token_budget_remaining")
        if max_parallel <= 0:
            raise AgentPoolScalingError("max_parallel_runs must be > 0")
        if active < 0:
            raise AgentPoolScalingError("active_runs must be >= 0")
        if active > max_parallel:
            raise AgentPoolScalingError("active_runs must not exceed max_parallel_runs")
        if budget < 0:
            raise AgentPoolScalingError("token_budget_remaining must be >= 0")
        roots = _paths(allowed_file_roots, field_name="allowed_file_roots", allow_empty=False)
        return cls(
            agent_id=_slug(agent_id, field_name="agent_id"),
            role_id=_slug(role_id, field_name="role_id"),
            max_parallel_runs=max_parallel,
            active_runs=active,
            token_budget_remaining=budget,
            allowed_file_roots=roots,
        )

    @property
    def available_slots(self) -> int:
        return self.max_parallel_runs - self.active_runs


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_id: str
    required_role: str
    required_files: tuple[str, ...]
    estimated_tokens: int
    priority: int

    @classmethod
    def create(
        cls,
        *,
        work_id: Any,
        required_role: Any,
        required_files: Iterable[Any],
        estimated_tokens: Any,
        priority: Any = 50,
    ) -> "WorkItem":
        tokens = _int(estimated_tokens, field_name="estimated_tokens")
        if tokens <= 0:
            raise AgentPoolScalingError("estimated_tokens must be > 0")
        prio = _int(priority, field_name="priority")
        if prio < 0 or prio > 100:
            raise AgentPoolScalingError("priority must be between 0 and 100")
        return cls(
            work_id=_slug(work_id, field_name="work_id"),
            required_role=_slug(required_role, field_name="required_role"),
            required_files=_paths(required_files, field_name="required_files", allow_empty=False),
            estimated_tokens=tokens,
            priority=prio,
        )


@dataclass(frozen=True, slots=True)
class FileLock:
    path: str
    owner_agent_id: str
    work_id: str

    @classmethod
    def create(cls, *, path: Any, owner_agent_id: Any, work_id: Any) -> "FileLock":
        return cls(
            path=_paths([path], field_name="path", allow_empty=False)[0],
            owner_agent_id=_slug(owner_agent_id, field_name="owner_agent_id"),
            work_id=_slug(work_id, field_name="work_id"),
        )


@dataclass(frozen=True, slots=True)
class AgentPoolPolicy:
    max_total_parallel_runs: int
    reserve_token_floor: int
    require_registered_agent: bool = True

    @classmethod
    def create(
        cls,
        *,
        max_total_parallel_runs: Any,
        reserve_token_floor: Any = 0,
        require_registered_agent: Any = True,
    ) -> "AgentPoolPolicy":
        max_total = _int(max_total_parallel_runs, field_name="max_total_parallel_runs")
        reserve = _int(reserve_token_floor, field_name="reserve_token_floor")
        if max_total <= 0:
            raise AgentPoolScalingError("max_total_parallel_runs must be > 0")
        if reserve < 0:
            raise AgentPoolScalingError("reserve_token_floor must be >= 0")
        return cls(
            max_total_parallel_runs=max_total,
            reserve_token_floor=reserve,
            require_registered_agent=bool(require_registered_agent),
        )


@dataclass(frozen=True, slots=True)
class AssignmentDecision:
    decision: AgentPoolDecision
    work_id: str
    agent_id: str
    reason: str
    warnings: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        decision: AgentPoolDecision | str,
        work_id: Any,
        agent_id: Any = "",
        reason: Any,
        warnings: Iterable[Any] = (),
    ) -> "AssignmentDecision":
        normalized_decision = decision if isinstance(decision, AgentPoolDecision) else AgentPoolDecision(str(decision))
        return cls(
            decision=normalized_decision,
            work_id=_slug(work_id, field_name="work_id"),
            agent_id=_slug(agent_id, field_name="agent_id") if str(agent_id or "").strip() else "",
            reason=_text(reason, field_name="reason", allow_empty=False),
            warnings=tuple(_text(item, field_name="warning", allow_empty=False) for item in warnings),
        )


@dataclass(frozen=True, slots=True)
class AssignmentPlan:
    decisions: tuple[AssignmentDecision, ...]

    @property
    def assigned_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.decision == AgentPoolDecision.ASSIGN)

    def audit_summary(self) -> dict[str, Any]:
        return {
            "decision_count": len(self.decisions),
            "assigned_count": self.assigned_count,
            "blocked_count": sum(1 for item in self.decisions if item.decision == AgentPoolDecision.BLOCK),
            "waiting_count": sum(1 for item in self.decisions if item.decision == AgentPoolDecision.WAIT),
            "decisions": tuple(
                {
                    "work_id": item.work_id,
                    "decision": item.decision.value,
                    "agent_id": item.agent_id,
                    "reason": item.reason,
                }
                for item in self.decisions
            ),
        }


def plan_agent_pool_assignments(
    *,
    agents: Iterable[AgentPoolMember],
    work_items: Iterable[WorkItem],
    locks: Iterable[FileLock],
    policy: AgentPoolPolicy,
) -> AssignmentPlan:
    if not isinstance(policy, AgentPoolPolicy):
        raise AgentPoolScalingError("policy must be an AgentPoolPolicy")
    agent_list = tuple(agents)
    work_list = tuple(sorted(work_items, key=lambda item: (-item.priority, item.work_id)))
    lock_list = tuple(locks)
    if any(not isinstance(agent, AgentPoolMember) for agent in agent_list):
        raise AgentPoolScalingError("agents must contain AgentPoolMember items")
    if any(not isinstance(work, WorkItem) for work in work_list):
        raise AgentPoolScalingError("work_items must contain WorkItem items")
    if any(not isinstance(lock, FileLock) for lock in lock_list):
        raise AgentPoolScalingError("locks must contain FileLock items")
    if len({agent.agent_id for agent in agent_list}) != len(agent_list):
        raise AgentPoolScalingError("agent_id must be unique")

    active_total = sum(agent.active_runs for agent in agent_list)
    provisional_active = {agent.agent_id: agent.active_runs for agent in agent_list}
    remaining_budget = {agent.agent_id: agent.token_budget_remaining for agent in agent_list}
    decisions: list[AssignmentDecision] = []

    for work in work_list:
        lock_blocker = _lock_blocker(work, lock_list)
        if lock_blocker:
            decisions.append(
                AssignmentDecision.create(
                    decision=AgentPoolDecision.BLOCK,
                    work_id=work.work_id,
                    reason=f"file locked by {lock_blocker.owner_agent_id}:{lock_blocker.path}",
                )
            )
            continue
        if active_total >= policy.max_total_parallel_runs:
            decisions.append(
                AssignmentDecision.create(
                    decision=AgentPoolDecision.WAIT,
                    work_id=work.work_id,
                    reason="global parallel run budget exhausted",
                )
            )
            continue

        candidates = [
            agent
            for agent in agent_list
            if agent.role_id == work.required_role
            and provisional_active[agent.agent_id] < agent.max_parallel_runs
            and remaining_budget[agent.agent_id] - work.estimated_tokens >= policy.reserve_token_floor
            and _files_allowed(work.required_files, agent.allowed_file_roots)
        ]
        if not candidates:
            decisions.append(
                AssignmentDecision.create(
                    decision=AgentPoolDecision.BLOCK if policy.require_registered_agent else AgentPoolDecision.WAIT,
                    work_id=work.work_id,
                    reason="no registered agent satisfies role, budget, capacity, and file roots",
                )
            )
            continue

        chosen = sorted(candidates, key=lambda agent: (-agent.token_budget_remaining, agent.agent_id))[0]
        provisional_active[chosen.agent_id] += 1
        remaining_budget[chosen.agent_id] -= work.estimated_tokens
        active_total += 1
        decisions.append(
            AssignmentDecision.create(
                decision=AgentPoolDecision.ASSIGN,
                work_id=work.work_id,
                agent_id=chosen.agent_id,
                reason="registered agent has capacity, budget, role, and file scope",
            )
        )
    return AssignmentPlan(decisions=tuple(decisions))


def _lock_blocker(work: WorkItem, locks: tuple[FileLock, ...]) -> FileLock | None:
    locked = {lock.path: lock for lock in locks}
    for path in work.required_files:
        if path in locked:
            return locked[path]
    return None


def _files_allowed(required_files: tuple[str, ...], allowed_roots: tuple[str, ...]) -> bool:
    for path in required_files:
        if not any(path == root or path.startswith(root.rstrip("/") + "/") for root in allowed_roots):
            return False
    return True


def _paths(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        path = str(value or "").strip().replace("\\", "/")
        if not path:
            continue
        if _ABS_WINDOWS_RE.match(str(value)) or path.startswith("/") or ".." in path.split("/"):
            raise AgentPoolScalingError(f"{field_name} must contain repo-relative paths")
        if not _SAFE_PATH_RE.fullmatch(path):
            raise AgentPoolScalingError(f"{field_name} contains unsupported characters")
        normalized.append(path)
    if not normalized and not allow_empty:
        raise AgentPoolScalingError(f"{field_name} must not be empty")
    return tuple(dict.fromkeys(normalized))


def _int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AgentPoolScalingError(f"{field_name} must be an int") from None


def _slug(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        raise AgentPoolScalingError(f"{field_name} must not be empty")
    return text


def _text(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = " ".join(str(value or "").split())
    if not text and not allow_empty:
        raise AgentPoolScalingError(f"{field_name} must not be empty")
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 3] + "..."
    return text
