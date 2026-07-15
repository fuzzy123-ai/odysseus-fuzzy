"""Authenticated /abc manifest pinning and idempotent fake-safe run start."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Mapping, Protocol

from src.planning_agent_handoff import (
    PlanningAgentHandoffError,
    build_agent_plan_handoff,
    validate_agent_plan_handoff,
)
from src.temporal_runtime.client import TemporalStartClient, TemporalStartError
from src.temporal_runtime.config import DEFAULT_TASK_QUEUE
from src.temporal_runtime.contracts import (
    ExecutionContractError,
    ExecutionManifest,
    ExecutionPolicy,
    FrozenObject,
    RunStartReceipt,
    canonical_json,
    freeze_json,
)
from src.temporal_runtime.manifest import (
    ManifestBuildError,
    build_execution_manifest,
    workflow_id_for,
)


RUN_START_STORE_SCHEMA_ID = "odysseus.abc.run_start_store.v1"
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}


class PlanningRevisionReader(Protocol):
    def get_roadmap(
        self,
        owner: str,
        project_id: str,
        roadmap_id: str,
        *,
        revision: str | int = "latest_approved",
    ) -> dict[str, Any]: ...


class ABCExecutionServiceError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class RunStartStoreError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ABCExecutionRequest:
    owner_scope_ref: str
    authenticated: bool
    entrypoint: str
    handoff: Mapping[str, Any]
    start_request_id: str
    policy: ExecutionPolicy


@dataclass(frozen=True)
class RunStartReservation:
    record_key: str
    created: bool
    receipt: RunStartReceipt | None


class PersistentRunStartStore:
    """Atomic local receipt store; caller supplies the owner-scoped path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self._lock = _store_lock(self.path)

    def reserve(
        self,
        *,
        owner_scope_ref: str,
        start_request_id: str,
        manifest: ExecutionManifest,
        workflow_id: str,
    ) -> RunStartReservation:
        key = _record_key(owner_scope_ref, start_request_id)
        with self._lock:
            document = self._read()
            existing = document["records"].get(key)
            if existing is not None:
                if not isinstance(existing, dict):
                    raise RunStartStoreError("store_corrupt", "run-start record is not an object")
                expected_identity = {
                    "start_request_id": start_request_id,
                    "agent_run_id": manifest.agent_run_id,
                    "workflow_id": workflow_id,
                }
                if any(existing.get(field) != value for field, value in expected_identity.items()):
                    raise RunStartStoreError("store_corrupt", "run-start reservation identity is invalid")
                if existing.get("state") not in {"reserved", "started"}:
                    raise RunStartStoreError("store_corrupt", "run-start reservation state is invalid")
                if existing.get("manifest_hash") != manifest.manifest_hash:
                    raise RunStartStoreError(
                        "idempotency_conflict",
                        "start_request_id was already reserved for another manifest",
                    )
                receipt_payload = existing.get("receipt")
                receipt = (
                    RunStartReceipt.from_payload(receipt_payload)
                    if isinstance(receipt_payload, Mapping)
                    else None
                )
                return RunStartReservation(key, False, receipt)
            document["records"][key] = {
                "start_request_id": start_request_id,
                "agent_run_id": manifest.agent_run_id,
                "workflow_id": workflow_id,
                "manifest_hash": manifest.manifest_hash,
                "manifest": manifest.to_payload(),
                "state": "reserved",
                "receipt": None,
            }
            self._write(document)
            return RunStartReservation(key, True, None)

    def complete(self, record_key: str, receipt: RunStartReceipt) -> RunStartReceipt:
        with self._lock:
            document = self._read()
            record = document["records"].get(record_key)
            if record is None:
                raise RunStartStoreError("reservation_missing", "run-start reservation is missing")
            existing = record.get("receipt")
            if isinstance(existing, Mapping):
                parsed = RunStartReceipt.from_payload(existing)
                if parsed != receipt:
                    raise RunStartStoreError("receipt_conflict", "reservation has another receipt")
                return parsed
            if record.get("manifest_hash") != receipt.manifest_hash:
                raise RunStartStoreError("receipt_conflict", "receipt manifest does not match reservation")
            record["state"] = "started"
            record["receipt"] = receipt.to_payload()
            self._write(document)
            return receipt

    def get_record(self, owner_scope_ref: str, start_request_id: str) -> dict[str, Any] | None:
        key = _record_key(owner_scope_ref, start_request_id)
        with self._lock:
            record = self._read()["records"].get(key)
            return deepcopy(record) if record is not None else None

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_id": RUN_START_STORE_SCHEMA_ID, "records": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RunStartStoreError("store_corrupt", "run-start store is unreadable") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_id") != RUN_START_STORE_SCHEMA_ID
            or not isinstance(value.get("records"), dict)
        ):
            raise RunStartStoreError("store_corrupt", "run-start store schema is invalid")
        return value

    def _write(self, document: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_json(document) + "\n"
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except OSError as exc:
            raise RunStartStoreError("store_write_failed", "run-start store could not be persisted") from exc


class ABCExecutionService:
    def __init__(
        self,
        *,
        planning_store: PlanningRevisionReader,
        start_store: PersistentRunStartStore,
        temporal_client: TemporalStartClient,
        task_queue: str = DEFAULT_TASK_QUEUE,
    ) -> None:
        self._planning_store = planning_store
        self._start_store = start_store
        self._temporal_client = temporal_client
        self._task_queue = task_queue
        self._start_lock = asyncio.Lock()

    async def start_run(self, request: ABCExecutionRequest) -> RunStartReceipt:
        if request.authenticated is not True:
            raise ABCExecutionServiceError("authentication_required", "Agent run start requires an authenticated caller")
        if request.entrypoint != "/abc":
            raise ABCExecutionServiceError("entrypoint_required", "Agent run start is available only through /abc")
        try:
            handoff = validate_agent_plan_handoff(request.handoff)
        except PlanningAgentHandoffError as exc:
            raise ABCExecutionServiceError("invalid_handoff", exc.code) from exc

        try:
            read_model = self._planning_store.get_roadmap(
                request.owner_scope_ref,
                handoff["project_id"],
                handoff["roadmap_id"],
                revision=handoff["revision"],
            )
            current_handoff = build_agent_plan_handoff(
                read_model,
                expected_revision=handoff["revision"],
                expected_hash=handoff["content_hash"],
            )
        except Exception as exc:
            if isinstance(exc, ABCExecutionServiceError):
                raise
            raise ABCExecutionServiceError("plan_revision_conflict", "Planning revision no longer matches") from exc
        if current_handoff != handoff:
            raise ABCExecutionServiceError("plan_revision_conflict", "Planning handoff changed before start")

        try:
            manifest = build_execution_manifest(
                read_model,
                owner_scope_ref=request.owner_scope_ref,
                start_request_id=request.start_request_id,
                policy=request.policy,
            )
        except (ManifestBuildError, ExecutionContractError) as exc:
            code = "plan_revision_conflict" if exc.code == "plan_revision_conflict" else "invalid_manifest"
            raise ABCExecutionServiceError(code, exc.code) from exc

        async with self._start_lock:
            workflow_id = workflow_id_for(manifest)
            try:
                reservation = self._start_store.reserve(
                    owner_scope_ref=request.owner_scope_ref,
                    start_request_id=request.start_request_id,
                    manifest=manifest,
                    workflow_id=workflow_id,
                )
            except RunStartStoreError as exc:
                raise ABCExecutionServiceError(exc.code, exc.detail) from exc
            if reservation.receipt is not None:
                return reservation.receipt
            try:
                workflow_run_id = await self._temporal_client.start_workflow(
                    workflow_id=workflow_id,
                    task_queue=self._task_queue,
                    manifest=manifest.to_payload(),
                )
            except TemporalStartError as exc:
                raise ABCExecutionServiceError("temporal_start_failed", exc.code) from exc
            planning_ref = freeze_json(
                {
                    "project_id": manifest.project_id,
                    "roadmap_id": manifest.roadmap_id,
                    "revision": manifest.planning_revision,
                    "content_hash": manifest.planning_content_hash,
                },
                path="$.planning_ref",
            )
            if not isinstance(planning_ref, FrozenObject):
                raise ABCExecutionServiceError("invalid_manifest", "planning reference is not an object")
            receipt = RunStartReceipt(
                start_request_id=manifest.start_request_id,
                agent_run_id=manifest.agent_run_id,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                manifest_hash=manifest.manifest_hash,
                planning_ref=planning_ref,
                state="started",
                task_queue=self._task_queue,
            )
            try:
                return self._start_store.complete(reservation.record_key, receipt)
            except RunStartStoreError as exc:
                raise ABCExecutionServiceError(exc.code, exc.detail) from exc


def _record_key(owner_scope_ref: str, start_request_id: str) -> str:
    return hashlib.sha256(f"{owner_scope_ref}\0{start_request_id}".encode("utf-8")).hexdigest()


def _store_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path))
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())
