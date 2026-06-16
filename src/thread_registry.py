"""Thread registry for deterministic agent-run to Codex-thread assignment.

This AUTO2 preparation slice stores ThreadRef assignments and validates that
dispatch cannot become ambiguous. It does not read from or write to any real
thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.thread_lifecycle_bridge import ThreadRef


class ThreadRegistryError(ValueError):
    """Raised when thread assignments are ambiguous or invalid."""


@dataclass(slots=True)
class ThreadRegistry:
    refs_by_run_id: dict[str, ThreadRef] = field(default_factory=dict)
    refs_by_thread_id: dict[str, ThreadRef] = field(default_factory=dict)

    def register(self, ref: ThreadRef) -> None:
        if not isinstance(ref, ThreadRef):
            raise ThreadRegistryError("ref must be a ThreadRef")
        existing_for_run = self.refs_by_run_id.get(ref.agent_run_id)
        if existing_for_run is not None and existing_for_run.thread_id != ref.thread_id:
            raise ThreadRegistryError(f"agent run already assigned to another thread: {ref.agent_run_id}")
        existing_for_thread = self.refs_by_thread_id.get(ref.thread_id)
        if existing_for_thread is not None and existing_for_thread.agent_run_id != ref.agent_run_id:
            raise ThreadRegistryError(f"thread already assigned to another agent run: {ref.thread_id}")
        self.refs_by_run_id[ref.agent_run_id] = ref
        self.refs_by_thread_id[ref.thread_id] = ref

    def resolve_run(self, agent_run_id: str) -> ThreadRef:
        try:
            return self.refs_by_run_id[agent_run_id]
        except KeyError as exc:
            raise ThreadRegistryError(f"unknown agent run: {agent_run_id}") from exc

    def resolve_thread(self, thread_id: str) -> ThreadRef:
        try:
            return self.refs_by_thread_id[thread_id]
        except KeyError as exc:
            raise ThreadRegistryError(f"unknown thread: {thread_id}") from exc

    def dispatch_target(self, *, agent_run_id: str, expected_agent_id: str, expected_node_id: str) -> ThreadRef:
        ref = self.resolve_run(agent_run_id)
        if ref.agent_id != expected_agent_id:
            raise ThreadRegistryError("agent_id mismatch for dispatch target")
        if ref.node_id != expected_node_id:
            raise ThreadRegistryError("node_id mismatch for dispatch target")
        return ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "thread_refs": [
                {
                    "thread_id": ref.thread_id,
                    "agent_id": ref.agent_id,
                    "agent_run_id": ref.agent_run_id,
                    "plan_id": ref.plan_id,
                    "node_id": ref.node_id,
                }
                for ref in sorted(self.refs_by_run_id.values(), key=lambda item: item.agent_run_id)
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThreadRegistry":
        if not isinstance(payload, dict):
            raise ThreadRegistryError("payload must be a dict")
        if payload.get("schema_version") != 1:
            raise ThreadRegistryError("schema_version must be 1")
        refs = payload.get("thread_refs")
        if not isinstance(refs, list):
            raise ThreadRegistryError("thread_refs must be a list")
        registry = cls()
        for ref_payload in refs:
            if not isinstance(ref_payload, dict):
                raise ThreadRegistryError("thread_refs must contain dicts")
            registry.register(
                ThreadRef.create(
                    thread_id=_required(ref_payload, "thread_id"),
                    agent_id=_required(ref_payload, "agent_id"),
                    agent_run_id=_required(ref_payload, "agent_run_id"),
                    plan_id=_required(ref_payload, "plan_id"),
                    node_id=_required(ref_payload, "node_id"),
                )
            )
        return registry

    def audit_summary(self) -> dict[str, Any]:
        return {
            "thread_count": len(self.refs_by_thread_id),
            "run_count": len(self.refs_by_run_id),
            "agent_runs": tuple(sorted(self.refs_by_run_id)),
            "thread_ids": tuple(sorted(self.refs_by_thread_id)),
        }


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ThreadRegistryError(f"missing required field: {key}")
    return payload[key]
