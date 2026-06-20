"""Offline subagent runtime contracts with a fake execution backend.

This module is the durable-worker boundary for Subagent Runtime v1. It creates
scoped run records and fake backend snapshots, but it never reads from or sends
to real Codex/Odysseus threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
import json
import re
from typing import Any, Iterable, Protocol

from src.agent_identity import AgentIdentity
from src.agent_run_store import AgentRun, AgentRunStatus
from src.context_capsule import ContextCapsule
from src.handoff_mailbox import HandoffStatus, ParsedHandoff, parse_handoff_text
from src.quality_gates import QualityGateResult
from src.runtime_quality_gates import (
    GitStatusSnapshot,
    RuntimeQualityGateInput,
    TestExecutionSnapshot,
    evaluate_runtime_quality_gates,
)
from src.thread_lifecycle_bridge import ThreadRef
from src.thread_registry import ThreadRegistry


_MAX_ID = 80
_MAX_TEXT = 400
_MAX_SUMMARY = 160
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class SubagentRuntimeError(ValueError):
    """Raised when a subagent runtime payload or transition is unsafe."""


class SubagentRunState(StrEnum):
    PLANNED = "planned"
    SPAWNED = "spawned"
    RUNNING = "running"
    PAUSED = "paused"
    HANDOFF = "handoff"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def to_agent_run_status(self) -> AgentRunStatus:
        if self in {SubagentRunState.PLANNED, SubagentRunState.SPAWNED, SubagentRunState.PAUSED}:
            return AgentRunStatus.PENDING
        if self == SubagentRunState.RUNNING:
            return AgentRunStatus.RUNNING
        if self == SubagentRunState.HANDOFF:
            return AgentRunStatus.HANDOFF
        if self == SubagentRunState.BLOCKED:
            return AgentRunStatus.BLOCKED
        if self == SubagentRunState.DONE:
            return AgentRunStatus.DONE
        if self == SubagentRunState.FAILED:
            return AgentRunStatus.FAILED
        return AgentRunStatus.SKIPPED


class SubagentTargetKind(StrEnum):
    THREAD = "thread"
    JOB = "job"


class SubagentDisplayStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    HANDOFF = "handoff"
    CLAIMED_DONE = "claimed_done"
    GATE_BLOCKED = "gate_blocked"
    VERIFIED_DONE = "verified_done"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobRef:
    job_id: str
    agent_id: str
    agent_run_id: str
    plan_id: str
    node_id: str

    @classmethod
    def create(
        cls,
        *,
        job_id: Any,
        agent_id: Any,
        agent_run_id: Any,
        plan_id: Any,
        node_id: Any,
    ) -> "JobRef":
        return cls(
            job_id=_normalize_external_id(job_id, field_name="job_id"),
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
        )


@dataclass(frozen=True, slots=True)
class SubagentRunSpec:
    agent_run_id: str
    plan_id: str
    node_id: str
    slice_id: str
    agent_id: str
    role_id: str
    objective: str
    allowed_files: tuple[str, ...]
    blocked_files: tuple[str, ...]
    inputs: dict[str, Any]
    expected_outputs: tuple[str, ...]
    tests: tuple[str, ...]
    handoff_format: tuple[str, ...]
    stop_conditions: tuple[str, ...]
    evidence_required: tuple[str, ...]
    model: str
    thinking: str
    created_at: str
    target_kind: SubagentTargetKind
    thread_id: str
    job_id: str

    @classmethod
    def create(
        cls,
        *,
        agent_run_id: Any,
        plan_id: Any,
        node_id: Any,
        slice_id: Any,
        agent_id: Any,
        role_id: Any,
        objective: Any,
        allowed_files: Iterable[Any],
        blocked_files: Iterable[Any] = (),
        inputs: dict[str, Any] | None = None,
        expected_outputs: Iterable[Any] = (),
        tests: Iterable[Any] = (),
        handoff_format: Iterable[Any] = (),
        stop_conditions: Iterable[Any] = (),
        evidence_required: Iterable[Any] = (),
        model: Any = "fake-model",
        thinking: Any = "medium",
        created_at: Any,
        target_kind: SubagentTargetKind | str = SubagentTargetKind.JOB,
        thread_id: Any = "",
        job_id: Any = "",
    ) -> "SubagentRunSpec":
        normalized_target = _normalize_target_kind(target_kind)
        normalized_thread_id = _normalize_external_id(thread_id, field_name="thread_id", allow_empty=True)
        normalized_job_id = _normalize_external_id(job_id, field_name="job_id", allow_empty=True)
        if normalized_thread_id and normalized_job_id:
            raise SubagentRuntimeError("subagent run target is ambiguous: choose thread_id or job_id")
        if normalized_target == SubagentTargetKind.THREAD and normalized_job_id:
            raise SubagentRuntimeError("thread-target runs must not include job_id")
        if normalized_target == SubagentTargetKind.JOB and normalized_thread_id:
            raise SubagentRuntimeError("job-target runs must not include thread_id")

        allowed = _normalize_path_list(allowed_files, field_name="allowed_files", allow_empty=False)
        blocked = _normalize_path_list(blocked_files, field_name="blocked_files", allow_empty=True)
        overlap = sorted(set(allowed) & set(blocked))
        if overlap:
            raise SubagentRuntimeError(f"allowed_files and blocked_files overlap: {', '.join(overlap)}")

        return cls(
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
            slice_id=_normalize_slug(slice_id, field_name="slice_id"),
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            role_id=_normalize_slug(role_id, field_name="role_id"),
            objective=_normalize_text(objective, field_name="objective", allow_empty=False),
            allowed_files=allowed,
            blocked_files=blocked,
            inputs=dict(inputs or {}),
            expected_outputs=_normalize_text_list(expected_outputs, field_name="expected_outputs"),
            tests=_normalize_text_list(tests, field_name="tests"),
            handoff_format=_normalize_text_list(handoff_format, field_name="handoff_format", allow_empty=False),
            stop_conditions=_normalize_text_list(stop_conditions, field_name="stop_conditions"),
            evidence_required=_normalize_text_list(evidence_required, field_name="evidence_required"),
            model=_normalize_text(model, field_name="model", allow_empty=False, limit=80),
            thinking=_normalize_text(thinking, field_name="thinking", allow_empty=False, limit=40),
            created_at=_normalize_timestamp(created_at, field_name="created_at"),
            target_kind=normalized_target,
            thread_id=normalized_thread_id,
            job_id=normalized_job_id,
        )

    def agent_identity(self) -> AgentIdentity:
        return AgentIdentity.create(
            agent_id=self.agent_id,
            role_id=self.role_id,
            project_id=self.plan_id,
            memory_scope="subagent-runtime",
            workspace_scope="repo-root",
            run_id=self.agent_run_id,
        )

    def to_context_capsule(self) -> ContextCapsule:
        return ContextCapsule.create(
            capsule_id=f"{self.agent_run_id}-capsule",
            objective=self.objective,
            agent_identity=self.agent_identity(),
            allowed_files=self.allowed_files,
            blocked_files=self.blocked_files,
            inputs=self.inputs,
            expected_outputs=self.expected_outputs,
            tests=self.tests,
            handoff_format=self.handoff_format,
            stop_conditions=self.stop_conditions,
            evidence_required=self.evidence_required,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "slice_id": self.slice_id,
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "target_kind": self.target_kind.value,
            "allowed_file_count": len(self.allowed_files),
            "blocked_file_count": len(self.blocked_files),
            "input_keys": tuple(sorted(_normalize_slug(key, field_name="input_key") for key in self.inputs)),
            "test_count": len(self.tests),
            "evidence_required_count": len(self.evidence_required),
        }


@dataclass(frozen=True, slots=True)
class BackendRunSnapshot:
    agent_run_id: str
    state: SubagentRunState
    backend: str
    attempts: int
    summary: str
    blocker: str = ""

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "state": self.state.value,
            "backend": self.backend,
            "attempts": self.attempts,
            "has_blocker": bool(self.blocker),
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class SubagentRun:
    spec: SubagentRunSpec
    state: SubagentRunState
    capsule: ContextCapsule
    agent_run: AgentRun
    backend: str
    thread_ref: ThreadRef | None = None
    job_ref: JobRef | None = None
    handoff: ParsedHandoff | None = None
    gate_result: QualityGateResult | None = None
    backend_snapshot: BackendRunSnapshot | None = None

    @property
    def agent_run_id(self) -> str:
        return self.spec.agent_run_id

    @property
    def verified_done(self) -> bool:
        return self.state == SubagentRunState.DONE and bool(self.gate_result and self.gate_result.verified_done)

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "state": self.state.value,
            "backend": self.backend,
            "target_kind": self.spec.target_kind.value,
            "has_thread_ref": self.thread_ref is not None,
            "has_job_ref": self.job_ref is not None,
            "capsule": {
                "capsule_id": self.capsule.capsule_id,
                "allowed_file_count": len(self.capsule.allowed_files),
                "blocked_file_count": len(self.capsule.blocked_files),
                "input_keys": tuple(sorted(self.capsule.inputs)),
                "tests": self.capsule.tests,
            },
            "agent_run": self.agent_run.audit_summary(),
            "handoff_status": self.handoff.status.value if self.handoff else "",
            "verified_done": self.verified_done,
            "blocking_gate_ids": self.gate_result.blocking_gate_ids if self.gate_result else (),
        }


@dataclass(frozen=True, slots=True)
class SubagentStatusItem:
    agent_run_id: str
    agent_id: str
    slice_id: str
    state: SubagentDisplayStatus
    backend: str
    target_kind: SubagentTargetKind
    handoff_status: str
    tests: tuple[str, ...]
    blocker: str
    next_action: str
    allowed_actions: tuple[str, ...]
    updated_at: str

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "agent_id": self.agent_id,
            "slice_id": self.slice_id,
            "state": self.state.value,
            "backend": self.backend,
            "target_kind": self.target_kind.value,
            "handoff_status": self.handoff_status,
            "test_count": len(self.tests),
            "has_blocker": bool(self.blocker),
            "next_action": self.next_action,
            "allowed_actions": self.allowed_actions,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class SubagentStatusSnapshot:
    snapshot_id: str
    plan_id: str
    items: tuple[SubagentStatusItem, ...]
    last_updated_at: str
    warnings: tuple[str, ...]

    @property
    def counts_by_state(self) -> dict[str, int]:
        return {
            status.value: sum(1 for item in self.items if item.state == status)
            for status in SubagentDisplayStatus
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "plan_id": self.plan_id,
            "run_count": len(self.items),
            "counts_by_state": {key: value for key, value in self.counts_by_state.items() if value},
            "last_updated_at": self.last_updated_at,
            "warning_count": len(self.warnings),
            "items": tuple(item.audit_summary() for item in self.items),
        }


@dataclass(slots=True)
class InMemorySubagentRuntimeStores:
    runs: dict[str, SubagentRun] = field(default_factory=dict)
    capsules: dict[str, ContextCapsule] = field(default_factory=dict)
    agent_runs: dict[str, AgentRun] = field(default_factory=dict)
    thread_registry: ThreadRegistry = field(default_factory=ThreadRegistry)
    job_refs: dict[str, JobRef] = field(default_factory=dict)

    def add(self, run: SubagentRun) -> None:
        if not isinstance(run, SubagentRun):
            raise SubagentRuntimeError("run must be a SubagentRun")
        if run.agent_run_id in self.runs:
            raise SubagentRuntimeError(f"subagent run already exists: {run.agent_run_id}")
        if (run.thread_ref is None) == (run.job_ref is None):
            raise SubagentRuntimeError("subagent run must have exactly one ThreadRef or JobRef")
        self.runs[run.agent_run_id] = run
        self.capsules[run.capsule.capsule_id] = run.capsule
        self.agent_runs[run.agent_run_id] = run.agent_run
        if run.thread_ref is not None:
            self.thread_registry.register(run.thread_ref)
        if run.job_ref is not None:
            self.job_refs[run.job_ref.job_id] = run.job_ref

    def update(self, run: SubagentRun) -> None:
        if run.agent_run_id not in self.runs:
            raise SubagentRuntimeError(f"unknown subagent run: {run.agent_run_id}")
        self.runs[run.agent_run_id] = run
        self.agent_runs[run.agent_run_id] = run.agent_run

    def resolve(self, agent_run_id: str) -> SubagentRun:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        try:
            return self.runs[normalized]
        except KeyError as exc:
            raise SubagentRuntimeError(f"unknown subagent run: {normalized}") from exc


class SubagentExecutionBackend(Protocol):
    backend_name: str

    def spawn(self, run: SubagentRun) -> BackendRunSnapshot:
        ...

    def read(self, agent_run_id: str) -> ParsedHandoff | None:
        ...

    def cancel(self, agent_run_id: str) -> BackendRunSnapshot:
        ...

    def pause(self, agent_run_id: str) -> BackendRunSnapshot:
        ...

    def resume(self, agent_run_id: str) -> BackendRunSnapshot:
        ...

    def retry(self, agent_run_id: str) -> BackendRunSnapshot:
        ...

    def status(self, agent_run_id: str) -> BackendRunSnapshot:
        ...


@dataclass(slots=True)
class FakeSubagentExecutionBackend:
    backend_name: str = "fake"
    snapshots: dict[str, BackendRunSnapshot] = field(default_factory=dict)
    handoffs: dict[str, ParsedHandoff] = field(default_factory=dict)

    def spawn(self, run: SubagentRun) -> BackendRunSnapshot:
        if not isinstance(run, SubagentRun):
            raise SubagentRuntimeError("run must be a SubagentRun")
        if run.agent_run_id in self.snapshots:
            raise SubagentRuntimeError(f"backend run already exists: {run.agent_run_id}")
        snapshot = BackendRunSnapshot(
            agent_run_id=run.agent_run_id,
            state=SubagentRunState.SPAWNED,
            backend=self.backend_name,
            attempts=1,
            summary="fake backend spawned scoped subagent run",
        )
        self.snapshots[run.agent_run_id] = snapshot
        return snapshot

    def read(self, agent_run_id: str) -> ParsedHandoff | None:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        self._require_snapshot(normalized)
        return self.handoffs.get(normalized)

    def cancel(self, agent_run_id: str) -> BackendRunSnapshot:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        current = self._require_snapshot(normalized)
        snapshot = replace(current, state=SubagentRunState.CANCELLED, summary="fake backend cancelled run")
        self.snapshots[normalized] = snapshot
        return snapshot

    def pause(self, agent_run_id: str) -> BackendRunSnapshot:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        current = self._require_snapshot(normalized)
        if current.state in {SubagentRunState.DONE, SubagentRunState.CANCELLED}:
            raise SubagentRuntimeError("completed or cancelled runs cannot be paused")
        snapshot = replace(current, state=SubagentRunState.PAUSED, summary="fake backend paused run")
        self.snapshots[normalized] = snapshot
        return snapshot

    def resume(self, agent_run_id: str) -> BackendRunSnapshot:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        current = self._require_snapshot(normalized)
        if current.state != SubagentRunState.PAUSED:
            raise SubagentRuntimeError("only paused runs can be resumed")
        snapshot = replace(current, state=SubagentRunState.SPAWNED, summary="fake backend resumed run")
        self.snapshots[normalized] = snapshot
        return snapshot

    def retry(self, agent_run_id: str) -> BackendRunSnapshot:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        current = self._require_snapshot(normalized)
        snapshot = replace(
            current,
            state=SubagentRunState.SPAWNED,
            attempts=current.attempts + 1,
            summary="fake backend retry scheduled",
            blocker="",
        )
        self.snapshots[normalized] = snapshot
        return snapshot

    def status(self, agent_run_id: str) -> BackendRunSnapshot:
        return self._require_snapshot(_normalize_slug(agent_run_id, field_name="agent_run_id"))

    def set_handoff(self, agent_run_id: str, handoff: ParsedHandoff | str) -> BackendRunSnapshot:
        normalized = _normalize_slug(agent_run_id, field_name="agent_run_id")
        current = self._require_snapshot(normalized)
        parsed = parse_handoff_text(handoff) if isinstance(handoff, str) else handoff
        if not isinstance(parsed, ParsedHandoff):
            raise SubagentRuntimeError("handoff must be a ParsedHandoff or handoff text")
        self.handoffs[normalized] = parsed
        state = _state_for_handoff(parsed)
        snapshot = replace(current, state=state, summary=f"fake backend read {parsed.status.value} handoff")
        self.snapshots[normalized] = snapshot
        return snapshot

    def _require_snapshot(self, agent_run_id: str) -> BackendRunSnapshot:
        try:
            return self.snapshots[agent_run_id]
        except KeyError as exc:
            raise SubagentRuntimeError(f"unknown backend run: {agent_run_id}") from exc


_TOOL_STORES = InMemorySubagentRuntimeStores()
_TOOL_BACKEND = FakeSubagentExecutionBackend()


def spawn_subagent_from_tool(content: str) -> dict[str, Any]:
    args = _parse_tool_args(content)
    spec = SubagentRunSpec.create(
        agent_run_id=args.get("agent_run_id") or _default_run_id(args),
        plan_id=_required_arg(args, "plan_id"),
        node_id=_required_arg(args, "node_id"),
        slice_id=_required_arg(args, "slice_id"),
        agent_id=_required_arg(args, "agent_id"),
        role_id=args.get("role_id") or "worker",
        objective=_required_arg(args, "objective"),
        allowed_files=_list_arg(args.get("allowed_files")),
        blocked_files=_list_arg(args.get("blocked_files", [])),
        inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else {},
        expected_outputs=_list_arg(args.get("expected_outputs", [])),
        tests=_list_arg(args.get("tests", [])),
        handoff_format=_list_arg(args.get("handoff_format") or ["Agent", "Slice", "Status", "Evidence"]),
        stop_conditions=_list_arg(args.get("stop_conditions", [])),
        evidence_required=_list_arg(args.get("evidence_required", [])),
        model=args.get("model") or "fake-model",
        thinking=args.get("thinking") or "medium",
        created_at=args.get("created_at") or _now_utc(),
        target_kind=args.get("target_kind") or "job",
        thread_id=args.get("thread_id") or "",
        job_id=args.get("job_id") or "",
    )
    run = create_subagent_run(spec, stores=_TOOL_STORES, backend=_TOOL_BACKEND)
    return {
        "status": run.state.value,
        "exit_code": 0,
        "summary": "fake subagent run spawned; no live thread action was taken",
        "run": run.audit_summary(),
    }


def manage_subagents_from_tool(content: str) -> dict[str, Any]:
    args = _parse_tool_args(content)
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        return {
            "status": "ok",
            "exit_code": 0,
            "runs": [run.audit_summary() for run in sorted(_TOOL_STORES.runs.values(), key=lambda item: item.agent_run_id)],
        }
    if action == "snapshot":
        plan_id = _normalize_slug(_required_arg(args, "plan_id"), field_name="plan_id")
        runs = tuple(
            sync_subagent_backend_status(run, backend=_TOOL_BACKEND, stores=_TOOL_STORES)
            for run in sorted(tuple(_TOOL_STORES.runs.values()), key=lambda item: item.agent_run_id)
            if run.spec.plan_id == plan_id
        )
        snapshot = build_subagent_status_snapshot(
            runs,
            plan_id=plan_id,
            last_updated_at=args.get("last_updated_at") or _now_utc(),
        )
        return {"status": "ok", "exit_code": 0, "snapshot": snapshot.audit_summary()}
    agent_run_id = _required_arg(args, "agent_run_id")
    if action == "status":
        run = _TOOL_STORES.resolve(agent_run_id)
        snapshot = _TOOL_BACKEND.status(run.agent_run_id)
        return {"status": snapshot.state.value, "exit_code": 0, "snapshot": snapshot.audit_summary(), "run": run.audit_summary()}
    if action == "cancel":
        snapshot = _TOOL_BACKEND.cancel(agent_run_id)
        run = sync_subagent_backend_status(_TOOL_STORES.resolve(agent_run_id), backend=_TOOL_BACKEND, stores=_TOOL_STORES)
        return {"status": snapshot.state.value, "exit_code": 0, "snapshot": snapshot.audit_summary(), "run": run.audit_summary()}
    if action == "pause":
        snapshot = _TOOL_BACKEND.pause(agent_run_id)
        run = sync_subagent_backend_status(_TOOL_STORES.resolve(agent_run_id), backend=_TOOL_BACKEND, stores=_TOOL_STORES)
        return {"status": snapshot.state.value, "exit_code": 0, "snapshot": snapshot.audit_summary(), "run": run.audit_summary()}
    if action == "resume":
        snapshot = _TOOL_BACKEND.resume(agent_run_id)
        run = sync_subagent_backend_status(_TOOL_STORES.resolve(agent_run_id), backend=_TOOL_BACKEND, stores=_TOOL_STORES)
        return {"status": snapshot.state.value, "exit_code": 0, "snapshot": snapshot.audit_summary(), "run": run.audit_summary()}
    if action == "retry":
        snapshot = _TOOL_BACKEND.retry(agent_run_id)
        run = sync_subagent_backend_status(_TOOL_STORES.resolve(agent_run_id), backend=_TOOL_BACKEND, stores=_TOOL_STORES)
        return {"status": snapshot.state.value, "exit_code": 0, "snapshot": snapshot.audit_summary(), "run": run.audit_summary()}
    if action == "read":
        handoff = _TOOL_BACKEND.read(agent_run_id)
        return {
            "status": "handoff" if handoff else "empty",
            "exit_code": 0,
            "handoff": handoff.to_dict() if handoff else None,
        }
    raise SubagentRuntimeError("manage_subagents action must be list, snapshot, status, pause, resume, cancel, retry, or read")


def build_subagent_status_snapshot(
    runs: Iterable[SubagentRun],
    *,
    plan_id: str,
    last_updated_at: str,
    warnings: Iterable[Any] = (),
) -> SubagentStatusSnapshot:
    normalized_plan_id = _normalize_slug(plan_id, field_name="plan_id")
    normalized_updated = _normalize_timestamp(last_updated_at, field_name="last_updated_at")
    run_tuple = tuple(runs)
    if any(not isinstance(run, SubagentRun) for run in run_tuple):
        raise SubagentRuntimeError("runs must contain SubagentRun items")
    if any(run.spec.plan_id != normalized_plan_id for run in run_tuple):
        raise SubagentRuntimeError("all subagent runs must belong to the requested plan_id")
    items = tuple(
        sorted(
            (_status_item(run, updated_at=normalized_updated) for run in run_tuple),
            key=lambda item: item.agent_run_id,
        )
    )
    return SubagentStatusSnapshot(
        snapshot_id=f"{normalized_plan_id}-subagents",
        plan_id=normalized_plan_id,
        items=items,
        last_updated_at=normalized_updated,
        warnings=_normalize_text_list(warnings, field_name="warnings"),
    )


def run_subagent_fake_e2e_smoke() -> SubagentStatusSnapshot:
    stores = InMemorySubagentRuntimeStores()
    backend = FakeSubagentExecutionBackend()
    alice = create_subagent_run(_smoke_spec("alice", "sub7a"), stores=stores, backend=backend)
    bob = create_subagent_run(_smoke_spec("bob", "sub7b"), stores=stores, backend=backend)

    alice_verified = apply_subagent_handoff_and_gates(
        alice,
        handoff=_smoke_handoff("alice", "sub7a"),
        git_status=GitStatusSnapshot.create(branch="dev", clean=True, commit="abcde01"),
        test_results=[
            TestExecutionSnapshot.create(
                command="python -m pytest tests/test_subagent_runtime_e2e.py",
                exit_code=0,
                summary="alice fake smoke passed",
            )
        ],
        verified_at="2026-06-20T11:05:00Z",
        verified_by="charlie",
        stores=stores,
    )
    bob_blocked = apply_subagent_handoff_and_gates(
        bob,
        handoff=_smoke_handoff("bob", "sub7b"),
        git_status=GitStatusSnapshot.create(
            branch="dev",
            clean=False,
            commit="bcdef12",
            unstaged_files=["src/subagent_runtime.py"],
        ),
        test_results=[
            TestExecutionSnapshot.create(
                command="python -m pytest tests/test_subagent_runtime_e2e.py",
                exit_code=0,
                summary="bob fake smoke passed but git gate blocked",
            )
        ],
        verified_at="2026-06-20T11:06:00Z",
        verified_by="charlie",
        stores=stores,
    )
    return build_subagent_status_snapshot(
        [alice_verified, bob_blocked],
        plan_id="subagent-runtime-v1",
        last_updated_at="2026-06-20T11:07:00Z",
    )


def create_subagent_run(
    spec: SubagentRunSpec,
    *,
    stores: InMemorySubagentRuntimeStores | None = None,
    backend: SubagentExecutionBackend | None = None,
) -> SubagentRun:
    if not isinstance(spec, SubagentRunSpec):
        raise SubagentRuntimeError("spec must be a SubagentRunSpec")
    runtime_stores = stores or InMemorySubagentRuntimeStores()
    execution_backend = backend or FakeSubagentExecutionBackend()

    capsule = spec.to_context_capsule()
    agent_run = AgentRun.create(
        agent_run_id=spec.agent_run_id,
        plan_id=spec.plan_id,
        node_id=spec.node_id,
        slice_id=spec.slice_id,
        agent_id=spec.agent_id,
        role_id=spec.role_id,
        model=spec.model,
        thinking=spec.thinking,
        status=AgentRunStatus.PENDING,
        started_at=spec.created_at,
        completed_at="",
        changed_files=[],
        tests=[],
        commit="",
        warnings=[],
        errors=[],
        blocker="",
        next_action="await fake backend handoff",
        evidence=[],
    )
    thread_ref, job_ref = _target_ref(spec)
    run = SubagentRun(
        spec=spec,
        state=SubagentRunState.PLANNED,
        capsule=capsule,
        agent_run=agent_run,
        backend=execution_backend.backend_name,
        thread_ref=thread_ref,
        job_ref=job_ref,
    )
    runtime_stores.add(run)
    snapshot = execution_backend.spawn(run)
    spawned = replace(run, state=snapshot.state, backend_snapshot=snapshot)
    runtime_stores.update(spawned)
    return spawned


def sync_subagent_backend_status(
    run: SubagentRun,
    *,
    backend: SubagentExecutionBackend,
    stores: InMemorySubagentRuntimeStores | None = None,
) -> SubagentRun:
    snapshot = backend.status(run.agent_run_id)
    updated = replace(run, state=snapshot.state, backend_snapshot=snapshot)
    if stores is not None:
        stores.update(updated)
    return updated


def apply_subagent_handoff_and_gates(
    run: SubagentRun,
    *,
    handoff: ParsedHandoff | str,
    git_status: GitStatusSnapshot,
    test_results: Iterable[TestExecutionSnapshot],
    verified_at: str,
    verified_by: str,
    hot_files: Iterable[Any] = (),
    stores: InMemorySubagentRuntimeStores | None = None,
) -> SubagentRun:
    if not isinstance(run, SubagentRun):
        raise SubagentRuntimeError("run must be a SubagentRun")
    parsed = parse_handoff_text(handoff) if isinstance(handoff, str) else handoff
    if not isinstance(parsed, ParsedHandoff):
        raise SubagentRuntimeError("handoff must be a ParsedHandoff or handoff text")
    if parsed.agent != run.spec.agent_id or parsed.slice_id != run.spec.slice_id:
        raise SubagentRuntimeError("handoff agent or slice does not match subagent run")

    gate_input = RuntimeQualityGateInput.create(
        agent_run_id=run.agent_run_id,
        plan_node_id=run.spec.node_id,
        subject_ref=run.spec.slice_id,
        verified_at=verified_at,
        verified_by=verified_by,
        handoff=parsed,
        git_status=git_status,
        test_results=tuple(test_results),
        changed_files=parsed.changed_files,
        allowed_files=run.spec.allowed_files,
        hot_files=hot_files,
    )
    gate_result = evaluate_runtime_quality_gates(gate_input)
    state = SubagentRunState.DONE if gate_result.verified_done else _state_for_handoff(parsed, gate_blocked=True)
    updated_agent_run = _agent_run_from_handoff(
        run,
        handoff=parsed,
        state=state,
        completed_at=verified_at,
        gate_result=gate_result,
    )
    updated = replace(
        run,
        state=state,
        handoff=parsed,
        gate_result=gate_result,
        agent_run=updated_agent_run,
    )
    if stores is not None:
        stores.update(updated)
    return updated


def _target_ref(spec: SubagentRunSpec) -> tuple[ThreadRef | None, JobRef | None]:
    if spec.target_kind == SubagentTargetKind.THREAD:
        return (
            ThreadRef.create(
                thread_id=spec.thread_id or f"fake-thread-{spec.agent_run_id}",
                agent_id=spec.agent_id,
                agent_run_id=spec.agent_run_id,
                plan_id=spec.plan_id,
                node_id=spec.node_id,
            ),
            None,
        )
    return (
        None,
        JobRef.create(
            job_id=spec.job_id or f"fake-job-{spec.agent_run_id}",
            agent_id=spec.agent_id,
            agent_run_id=spec.agent_run_id,
            plan_id=spec.plan_id,
            node_id=spec.node_id,
        ),
    )


def _state_for_handoff(handoff: ParsedHandoff, *, gate_blocked: bool = False) -> SubagentRunState:
    if gate_blocked and handoff.status == HandoffStatus.DONE:
        return SubagentRunState.BLOCKED
    if handoff.status == HandoffStatus.DONE:
        return SubagentRunState.DONE
    if handoff.status == HandoffStatus.BLOCKED:
        return SubagentRunState.BLOCKED
    if handoff.status == HandoffStatus.FAILED:
        return SubagentRunState.FAILED
    if handoff.status == HandoffStatus.HANDOFF:
        return SubagentRunState.HANDOFF
    if handoff.status == HandoffStatus.RUNNING:
        return SubagentRunState.RUNNING
    return SubagentRunState.BLOCKED


def _agent_run_from_handoff(
    run: SubagentRun,
    *,
    handoff: ParsedHandoff,
    state: SubagentRunState,
    completed_at: str,
    gate_result: QualityGateResult,
) -> AgentRun:
    blocked_ids = ", ".join(gate_result.blocking_gate_ids)
    blocker = handoff.blocker
    errors: tuple[str, ...] = ()
    next_action = handoff.next_slice or "await Charlie verification"
    if state == SubagentRunState.BLOCKED and not blocker:
        blocker = f"runtime gates blocked: {blocked_ids}"
        next_action = "resolve runtime gate blockers"
    if state == SubagentRunState.FAILED:
        errors = (handoff.blocker or "handoff failed",)
    if state == SubagentRunState.CANCELLED:
        next_action = "cancelled by fake backend"
    return AgentRun.create(
        agent_run_id=run.agent_run_id,
        plan_id=run.spec.plan_id,
        node_id=run.spec.node_id,
        slice_id=run.spec.slice_id,
        agent_id=run.spec.agent_id,
        role_id=run.spec.role_id,
        model=run.spec.model,
        thinking=run.spec.thinking,
        status=state.to_agent_run_status(),
        started_at=run.agent_run.started_at,
        completed_at=completed_at,
        changed_files=handoff.changed_files,
        tests=handoff.tests,
        commit=handoff.commit,
        warnings=[],
        errors=errors,
        blocker=blocker,
        next_action=next_action,
        evidence=handoff.evidence,
    )


def _status_item(run: SubagentRun, *, updated_at: str) -> SubagentStatusItem:
    state = _display_status(run)
    return SubagentStatusItem(
        agent_run_id=run.agent_run_id,
        agent_id=run.spec.agent_id,
        slice_id=run.spec.slice_id,
        state=state,
        backend=run.backend,
        target_kind=run.spec.target_kind,
        handoff_status=run.handoff.status.value if run.handoff else "",
        tests=run.agent_run.evidence.tests,
        blocker=run.agent_run.blocker,
        next_action=run.agent_run.next_action,
        allowed_actions=_allowed_actions_for(state),
        updated_at=updated_at,
    )


def _display_status(run: SubagentRun) -> SubagentDisplayStatus:
    if run.verified_done:
        return SubagentDisplayStatus.VERIFIED_DONE
    if run.handoff and run.handoff.status == HandoffStatus.DONE and not run.gate_result:
        return SubagentDisplayStatus.CLAIMED_DONE
    if run.handoff and run.handoff.status == HandoffStatus.DONE and run.gate_result and not run.gate_result.verified_done:
        return SubagentDisplayStatus.GATE_BLOCKED
    if run.state in {SubagentRunState.PLANNED, SubagentRunState.SPAWNED}:
        return SubagentDisplayStatus.PLANNED
    if run.state == SubagentRunState.RUNNING:
        return SubagentDisplayStatus.RUNNING
    if run.state == SubagentRunState.PAUSED:
        return SubagentDisplayStatus.PAUSED
    if run.state == SubagentRunState.HANDOFF:
        return SubagentDisplayStatus.HANDOFF
    if run.state == SubagentRunState.BLOCKED:
        return SubagentDisplayStatus.BLOCKED
    if run.state == SubagentRunState.FAILED:
        return SubagentDisplayStatus.FAILED
    if run.state == SubagentRunState.CANCELLED:
        return SubagentDisplayStatus.CANCELLED
    if run.state == SubagentRunState.DONE:
        return SubagentDisplayStatus.CLAIMED_DONE
    return SubagentDisplayStatus.BLOCKED


def _allowed_actions_for(status: SubagentDisplayStatus) -> tuple[str, ...]:
    if status in {
        SubagentDisplayStatus.PLANNED,
        SubagentDisplayStatus.RUNNING,
        SubagentDisplayStatus.HANDOFF,
        SubagentDisplayStatus.CLAIMED_DONE,
    }:
        return ("pause", "cancel", "retry")
    if status == SubagentDisplayStatus.PAUSED:
        return ("resume", "cancel")
    if status in {SubagentDisplayStatus.GATE_BLOCKED, SubagentDisplayStatus.BLOCKED, SubagentDisplayStatus.FAILED}:
        return ("retry",)
    return ()


def _smoke_spec(agent_id: str, node_id: str) -> SubagentRunSpec:
    return SubagentRunSpec.create(
        agent_run_id=f"sub7-{agent_id}-run",
        plan_id="subagent-runtime-v1",
        node_id=node_id,
        slice_id=node_id,
        agent_id=agent_id,
        role_id="worker",
        objective=f"Run fake SUB7 smoke for {agent_id}.",
        allowed_files=["src/subagent_runtime.py", "tests/test_subagent_runtime_e2e.py"],
        tests=["python -m pytest tests/test_subagent_runtime_e2e.py"],
        handoff_format=["Agent", "Slice", "Status", "Evidence"],
        evidence_required=["green fake smoke"],
        created_at="2026-06-20T11:00:00Z",
        target_kind="job",
    )


def _smoke_handoff(agent_id: str, slice_id: str) -> ParsedHandoff:
    return ParsedHandoff.create(
        agent=agent_id,
        slice_id=slice_id,
        status="done",
        commit="abcde01" if agent_id == "alice" else "bcdef12",
        changed_files=["src/subagent_runtime.py", "tests/test_subagent_runtime_e2e.py"],
        tests=["python -m pytest tests/test_subagent_runtime_e2e.py"],
        evidence=[f"{agent_id} fake handoff parsed"],
    )


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise SubagentRuntimeError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise SubagentRuntimeError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise SubagentRuntimeError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_external_id(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise SubagentRuntimeError(f"{field_name} must not be empty")
    if len(text) > 120:
        raise SubagentRuntimeError(f"{field_name} exceeds max length 120")
    if "\\" in text or re.match(r"^[A-Za-z]:", text) or text.startswith("/") or ".." in text.split("/"):
        raise SubagentRuntimeError(f"{field_name} must not be path-like or absolute")
    return text


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not text and not allow_empty:
        raise SubagentRuntimeError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_text_list(values: Iterable[Any], *, field_name: str, allow_empty: bool = True) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True, limit=_MAX_SUMMARY)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    if not allow_empty and not normalized:
        raise SubagentRuntimeError(f"{field_name} must not be empty")
    return tuple(normalized)


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SubagentRuntimeError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise SubagentRuntimeError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise SubagentRuntimeError(f"{field_name} must be repo-relative")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SubagentRuntimeError(f"{field_name} must not contain traversal segments")
    return "/".join(parts)


def _normalize_path_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value, field_name=field_name)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    if not allow_empty and not normalized:
        raise SubagentRuntimeError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if len(text) > 40 or not _TIMESTAMP_RE.fullmatch(text):
        raise SubagentRuntimeError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _normalize_target_kind(value: SubagentTargetKind | str) -> SubagentTargetKind:
    if isinstance(value, SubagentTargetKind):
        return value
    normalized = _normalize_slug(value, field_name="target_kind")
    try:
        return SubagentTargetKind(normalized)
    except ValueError as exc:
        raise SubagentRuntimeError("target_kind must be thread or job") from exc


def _parse_tool_args(content: str) -> dict[str, Any]:
    raw = str(content or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SubagentRuntimeError("tool content must be JSON") from exc
    if not isinstance(parsed, dict):
        raise SubagentRuntimeError("tool content must be a JSON object")
    return parsed


def _required_arg(args: dict[str, Any], key: str) -> Any:
    value = args.get(key)
    if value is None or str(value).strip() == "":
        raise SubagentRuntimeError(f"{key} is required")
    return value


def _list_arg(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    if isinstance(value, str):
        return tuple(item.strip() for item in value.splitlines() if item.strip())
    raise SubagentRuntimeError("list argument must be a list or newline-delimited string")


def _default_run_id(args: dict[str, Any]) -> str:
    parts = (
        args.get("plan_id") or "plan",
        args.get("node_id") or "node",
        args.get("agent_id") or "agent",
    )
    return "-".join(str(part) for part in parts)


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
