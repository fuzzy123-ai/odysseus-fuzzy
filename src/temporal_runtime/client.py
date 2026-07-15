"""Narrow Temporal start-client protocol and an effect-free recording fake."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol


class TemporalStartError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class TemporalStartClient(Protocol):
    async def start_workflow(
        self,
        *,
        workflow_id: str,
        task_queue: str,
        manifest: Mapping[str, Any],
    ) -> str: ...


@dataclass(frozen=True)
class RecordedWorkflowStart:
    workflow_id: str
    workflow_run_id: str
    task_queue: str
    manifest_hash: str
    manifest: Mapping[str, Any]


class RecordingTemporalClient:
    """Idempotent fake: one workflow id can represent only one manifest."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._workflows: dict[str, RecordedWorkflowStart] = {}
        self.start_call_count = 0

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        task_queue: str,
        manifest: Mapping[str, Any],
    ) -> str:
        async with self._lock:
            self.start_call_count += 1
            manifest_hash = str(manifest.get("manifest_hash") or "")
            existing = self._workflows.get(workflow_id)
            if existing is not None:
                if existing.manifest_hash != manifest_hash:
                    raise TemporalStartError("workflow_id_conflict", "workflow id has another manifest")
                return existing.workflow_run_id
            run_id = "trun-" + hashlib.sha256(
                f"{workflow_id}\0{manifest_hash}".encode("utf-8")
            ).hexdigest()[:32]
            self._workflows[workflow_id] = RecordedWorkflowStart(
                workflow_id=workflow_id,
                workflow_run_id=run_id,
                task_queue=task_queue,
                manifest_hash=manifest_hash,
                manifest=deepcopy(dict(manifest)),
            )
            return run_id

    @property
    def workflows(self) -> dict[str, RecordedWorkflowStart]:
        return dict(self._workflows)
