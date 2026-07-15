"""Persisted-truth adapter between Temporal Light and the Agent product API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence

from temporalio.api.enums.v1 import EventType, PendingActivityState, WorkflowExecutionStatus
from temporalio.client import Client

from src.abc_execution_service import (
    ABCExecutionRequest,
    ABCExecutionService,
    RUN_START_STORE_SCHEMA_ID,
)
from src.agent_operation_projection import (
    MAX_RUN_PAGE_SIZE,
    AgentOperationProjectionError,
    build_agent_operation_projection,
    decode_history_cursor,
    project_history,
)
from src.planning_agent_handoff import build_agent_plan_handoff
from src.temporal_runtime.commands import CommandRequest
from src.temporal_runtime.contracts import ExecutionPolicy, RunStartReceipt


class AgentOperationAdapterError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class PersistedAgentRun:
    owner_scope_ref: str
    project_id: str
    receipt: dict[str, Any]
    manifest: dict[str, Any]

    @property
    def agent_run_id(self) -> str:
        return str(self.receipt["agent_run_id"])


class TemporalExecutionReader(Protocol):
    async def snapshot(self, workflow_id: str, workflow_run_id: str) -> Mapping[str, Any]: ...

    async def history(
        self,
        workflow_id: str,
        workflow_run_id: str,
        *,
        history_segment: int,
        after_event_id: int,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def execute_command(
        self,
        workflow_id: str,
        workflow_run_id: str,
        request: CommandRequest,
    ) -> Mapping[str, Any]: ...


class RuntimeReceiptReader(Protocol):
    def read_runtime_receipts(self, agent_run_id: str) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...


class AgentRunStarter(Protocol):
    async def start(self, owner_scope_ref: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


PolicyFactory = Callable[[str, Mapping[str, Any]], ExecutionPolicy]


class PersistentRunCatalog:
    """Read-only owner index over the durable TLR-02 run-start store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def list_owned(self, owner_scope_ref: str) -> list[PersistedAgentRun]:
        owner = str(owner_scope_ref or "").strip()
        if not owner:
            raise AgentOperationAdapterError("owner_required", "owner scope is required")
        document = self._read()
        owned: list[PersistedAgentRun] = []
        for raw in document["records"].values():
            if not isinstance(raw, Mapping) or raw.get("state") != "started":
                continue
            manifest = raw.get("manifest")
            receipt_payload = raw.get("receipt")
            if not isinstance(manifest, Mapping) or not isinstance(receipt_payload, Mapping):
                raise AgentOperationAdapterError(
                    "run_store_corrupt", "started run is missing its persisted truth"
                )
            if manifest.get("owner_scope_ref") != owner:
                continue
            try:
                receipt = RunStartReceipt.from_payload(receipt_payload).to_payload()
            except Exception as exc:
                raise AgentOperationAdapterError(
                    "run_store_corrupt", "run receipt is invalid"
                ) from exc
            project_id = str(manifest.get("project_id") or "")
            planning_ref = receipt["planning_ref"]
            identity_matches = (
                raw.get("agent_run_id") == receipt["agent_run_id"]
                and raw.get("workflow_id") == receipt["workflow_id"]
                and raw.get("manifest_hash") == receipt["manifest_hash"]
                and manifest.get("agent_run_id", receipt["agent_run_id"])
                == receipt["agent_run_id"]
                and manifest.get("manifest_hash", receipt["manifest_hash"])
                == receipt["manifest_hash"]
                and project_id == planning_ref["project_id"]
            )
            if not project_id or not identity_matches:
                raise AgentOperationAdapterError(
                    "run_store_corrupt", "run manifest and receipt identity differ"
                )
            owned.append(
                PersistedAgentRun(
                    owner_scope_ref=owner,
                    project_id=project_id,
                    receipt=receipt,
                    manifest=dict(manifest),
                )
            )
        return sorted(owned, key=lambda item: item.agent_run_id)

    def require_owned(self, owner_scope_ref: str, agent_run_id: str) -> PersistedAgentRun:
        target = str(agent_run_id or "")
        for item in self.list_owned(owner_scope_ref):
            if item.agent_run_id == target:
                return item
        # Deliberately indistinguishable from a run owned by somebody else.
        raise AgentOperationAdapterError("run_not_found", "Agent run was not found")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_id": RUN_START_STORE_SCHEMA_ID, "records": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentOperationAdapterError(
                "run_store_corrupt", "run-start store is unreadable"
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_id") != RUN_START_STORE_SCHEMA_ID
            or not isinstance(value.get("records"), dict)
        ):
            raise AgentOperationAdapterError(
                "run_store_corrupt", "run-start store schema is invalid"
            )
        return value


class ABCExecutionRunStarter:
    """Server-authoritative /abc start bridge; Planning still cannot launch runs."""

    def __init__(
        self,
        *,
        planning_store: Any,
        execution_service: ABCExecutionService,
        policy_factory: PolicyFactory,
    ) -> None:
        self._planning_store = planning_store
        self._execution_service = execution_service
        self._policy_factory = policy_factory

    async def start(self, owner_scope_ref: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        required = {
            "project_id",
            "roadmap_id",
            "revision",
            "content_hash",
            "start_request_id",
        }
        if set(body) != required:
            raise AgentOperationAdapterError(
                "invalid_start_request", "start request fields are not exact"
            )
        try:
            read_model = self._planning_store.get_roadmap(
                owner_scope_ref,
                body["project_id"],
                body["roadmap_id"],
                revision=body["revision"],
            )
            handoff = build_agent_plan_handoff(
                read_model,
                expected_revision=body["revision"],
                expected_hash=body["content_hash"],
            )
            policy = self._policy_factory(owner_scope_ref, handoff)
            receipt = await self._execution_service.start_run(
                ABCExecutionRequest(
                    owner_scope_ref=owner_scope_ref,
                    authenticated=True,
                    entrypoint="/abc",
                    handoff=handoff,
                    start_request_id=str(body["start_request_id"]),
                    policy=policy,
                )
            )
            return receipt.to_payload()
        except AgentOperationAdapterError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "run_start_failed")
            raise AgentOperationAdapterError(str(code), "Agent run could not be started") from exc


class TemporalAgentOperationAdapter:
    """Owner-scoped reconstruction service with no process-local run registry."""

    def __init__(
        self,
        *,
        catalog: PersistentRunCatalog,
        temporal: TemporalExecutionReader,
        receipts: RuntimeReceiptReader | None = None,
        starter: AgentRunStarter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._catalog = catalog
        self._temporal = temporal
        self._receipts = receipts
        self._starter = starter
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def start_run(self, owner_scope_ref: str, body: Mapping[str, Any]) -> dict[str, Any]:
        if self._starter is None:
            raise AgentOperationAdapterError(
                "run_start_unavailable", "the authenticated /abc start bridge is not configured"
            )
        receipt = await self._starter.start(owner_scope_ref, body)
        return dict(receipt)

    async def list_runs(
        self,
        owner_scope_ref: str,
        *,
        project_id: str = "",
        state: str = "",
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RUN_PAGE_SIZE:
            raise AgentOperationAdapterError("invalid_limit", "limit must be between 1 and 100")
        items = self._catalog.list_owned(owner_scope_ref)
        if project_id:
            items = [item for item in items if item.project_id == project_id]
        if cursor:
            items = [item for item in items if item.agent_run_id > cursor]
        projections: list[dict[str, Any]] = []
        for item in items:
            projection = await self._project_record(item)
            if state and projection["run"]["state"] != state:
                continue
            projections.append(projection["run"])
            if len(projections) > limit:
                break
        selected = projections[:limit]
        return {
            "cursor": cursor,
            "next_cursor": selected[-1]["agent_run_id"] if selected else cursor,
            "has_more": len(projections) > limit,
            "runs": selected,
        }

    async def get_run(self, owner_scope_ref: str, agent_run_id: str) -> dict[str, Any]:
        return await self._project_record(
            self._catalog.require_owned(owner_scope_ref, agent_run_id)
        )

    async def get_history(
        self,
        owner_scope_ref: str,
        agent_run_id: str,
        *,
        after: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        record = self._catalog.require_owned(owner_scope_ref, agent_run_id)
        segment, after_event = decode_history_cursor(after)
        snapshot = await self._snapshot(record)
        current_segment = int(snapshot.get("history_segment", 0))
        if segment not in (-1, current_segment):
            raise AgentOperationAdapterError(
                "history_segment_unavailable",
                "the requested history segment is not the current persisted segment",
            )
        try:
            events = await self._temporal.history(
                record.receipt["workflow_id"],
                record.receipt["workflow_run_id"],
                history_segment=current_segment,
                after_event_id=after_event,
                limit=limit + 1,
            )
            return project_history(events, after=after, limit=limit)
        except AgentOperationProjectionError:
            raise
        except AgentOperationAdapterError:
            raise
        except Exception as exc:
            raise AgentOperationAdapterError(
                "temporal_history_unavailable", "projected execution history is unavailable"
            ) from exc

    async def command_readback(
        self,
        owner_scope_ref: str,
        agent_run_id: str,
        request: CommandRequest,
    ) -> dict[str, Any]:
        record = self._catalog.require_owned(owner_scope_ref, agent_run_id)
        receipt = await self._temporal.execute_command(
            record.receipt["workflow_id"],
            record.receipt["workflow_run_id"],
            request,
        )
        projection = await self._project_record(record)
        return {"command_receipt": dict(receipt), "run": projection["run"]}

    async def stream_history(
        self,
        owner_scope_ref: str,
        agent_run_id: str,
        *,
        after: str = "",
        page_limit: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield the persisted backlog once; reconnect resumes strictly after its last id."""

        page = await self.get_history(
            owner_scope_ref, agent_run_id, after=after, limit=page_limit
        )
        for event in page["events"]:
            yield event

    async def _project_record(self, record: PersistedAgentRun) -> dict[str, Any]:
        snapshot = await self._snapshot(record)
        runtime_receipts: Mapping[str, Sequence[Mapping[str, Any]]] = {}
        if self._receipts is not None:
            runtime_receipts = self._receipts.read_runtime_receipts(record.agent_run_id)
        receipt = record.receipt
        run = {
            **snapshot,
            "agent_run_id": receipt["agent_run_id"],
            "workflow_id": receipt["workflow_id"],
            "workflow_run_id": snapshot.get(
                "workflow_run_id", receipt["workflow_run_id"]
            ),
            "deadline_at": record.manifest.get("deadline_at"),
            "waiting_reason": _waiting_reason(str(snapshot.get("run_state") or "")),
        }
        return build_agent_operation_projection(
            plan_ref=receipt["planning_ref"],
            run=run,
            activities=tuple(snapshot.get("activities", ())),
            claims=tuple(runtime_receipts.get("claims", ())),
            gates=tuple(runtime_receipts.get("gates", ())),
            evidence=tuple(runtime_receipts.get("evidence", ())),
            observed_at=self._clock(),
        )

    async def _snapshot(self, record: PersistedAgentRun) -> dict[str, Any]:
        try:
            return dict(
                await self._temporal.snapshot(
                    record.receipt["workflow_id"], record.receipt["workflow_run_id"]
                )
            )
        except AgentOperationAdapterError:
            raise
        except Exception as exc:
            raise AgentOperationAdapterError(
                "temporal_snapshot_unavailable", "current Agent run state is unavailable"
            ) from exc


class TemporalSDKExecutionReader:
    """Allowlist-only Temporal SDK reader.  No payload or raw History escapes."""

    def __init__(self, client: Client, *, heartbeat_timeout_seconds: int = 90) -> None:
        self._client = client
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds

    async def snapshot(self, workflow_id: str, workflow_run_id: str) -> Mapping[str, Any]:
        handle = self._client.get_workflow_handle(workflow_id)
        state = dict(await handle.query("get_run_state"))
        description = await handle.describe()
        raw = description.raw_description
        info = raw.workflow_execution_info
        start_time = _proto_timestamp(info.start_time)
        close_time = _proto_timestamp(info.close_time) if info.HasField("close_time") else None
        status_name = WorkflowExecutionStatus.Name(info.status).lower().removeprefix(
            "workflow_execution_status_"
        )
        activities = [_pending_activity(item) for item in raw.pending_activities]
        state.update(
            {
                "workflow_run_id": info.execution.run_id or workflow_run_id,
                "started_at": start_time,
                "updated_at": close_time or start_time,
                "completed_at": close_time,
                "temporal_status": status_name,
                "activities": [
                    {
                        **item,
                        "heartbeat_timeout_seconds": self._heartbeat_timeout_seconds,
                    }
                    for item in activities
                ],
            }
        )
        return state

    async def history(
        self,
        workflow_id: str,
        workflow_run_id: str,
        *,
        history_segment: int,
        after_event_id: int,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        handle = self._client.get_workflow_handle(workflow_id, run_id=workflow_run_id)
        result: list[dict[str, Any]] = []
        scheduled: dict[int, tuple[str | None, str | None]] = {}
        iterator = handle.fetch_history_events(page_size=min(max(limit, 1), 200))
        async for event in iterator:
            attrs_name = event.WhichOneof("attributes") or ""
            activity_id: str | None = None
            node_id: str | None = None
            if attrs_name == "activity_task_scheduled_event_attributes":
                attrs = getattr(event, attrs_name)
                activity_id = attrs.activity_id or None
                scheduled[event.event_id] = (activity_id, node_id)
            elif attrs_name.startswith("activity_task_"):
                attrs = getattr(event, attrs_name)
                scheduled_id = int(getattr(attrs, "scheduled_event_id", 0) or 0)
                activity_id, node_id = scheduled.get(scheduled_id, (None, None))
            if event.event_id <= after_event_id:
                continue
            event_name = EventType.Name(event.event_type).lower().removeprefix("event_type_")
            result.append(
                {
                    "history_segment": history_segment,
                    "event_id": int(event.event_id),
                    "event_type": event_name,
                    "occurred_at": _proto_timestamp(event.event_time),
                    "node_id": node_id,
                    "activity_id": activity_id,
                    "summary": event_name.replace("_", " "),
                    "ref_ids": [],
                }
            )
            if len(result) >= limit:
                break
        return result

    async def execute_command(
        self,
        workflow_id: str,
        workflow_run_id: str,
        request: CommandRequest,
    ) -> Mapping[str, Any]:
        handle = self._client.get_workflow_handle(workflow_id)
        value = await handle.execute_update(
            request.command,
            request,
            id=f"{request.command_id}:{request.idempotency_key}",
        )
        if not isinstance(value, Mapping):
            raise AgentOperationAdapterError(
                "invalid_command_receipt", "Temporal returned no command receipt"
            )
        return dict(value)


class LazyTemporalSDKExecutionReader:
    """Connect once, on first request, to the pinned localhost-only runtime."""

    def __init__(
        self,
        *,
        address: str = "127.0.0.1:7233",
        namespace: str = "default",
        connect: Callable[..., Awaitable[Client]] | None = None,
    ) -> None:
        if address != "127.0.0.1:7233":
            raise AgentOperationAdapterError(
                "invalid_temporal_address", "Agent operations require 127.0.0.1:7233"
            )
        if namespace != "default":
            raise AgentOperationAdapterError(
                "invalid_temporal_namespace", "Agent operations require the default namespace"
            )
        self._address = address
        self._namespace = namespace
        self._connect = connect or Client.connect
        self._reader: TemporalSDKExecutionReader | None = None
        self._connect_lock = asyncio.Lock()

    async def snapshot(self, workflow_id: str, workflow_run_id: str) -> Mapping[str, Any]:
        return await (await self._active()).snapshot(workflow_id, workflow_run_id)

    async def history(
        self,
        workflow_id: str,
        workflow_run_id: str,
        *,
        history_segment: int,
        after_event_id: int,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        return await (await self._active()).history(
            workflow_id,
            workflow_run_id,
            history_segment=history_segment,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def execute_command(
        self,
        workflow_id: str,
        workflow_run_id: str,
        request: CommandRequest,
    ) -> Mapping[str, Any]:
        return await (await self._active()).execute_command(
            workflow_id, workflow_run_id, request
        )

    async def _active(self) -> TemporalSDKExecutionReader:
        if self._reader is not None:
            return self._reader
        async with self._connect_lock:
            if self._reader is None:
                client = await self._connect(
                    self._address,
                    namespace=self._namespace,
                )
                self._reader = TemporalSDKExecutionReader(client)
        return self._reader


def _pending_activity(value: Any) -> dict[str, Any]:
    state_name = PendingActivityState.Name(value.state).lower().removeprefix(
        "pending_activity_state_"
    )
    mapped_state = {
        "started": "running",
        "scheduled": "scheduled",
        "cancel_requested": "running",
        "paused": "retry_wait",
        "pause_requested": "retry_wait",
    }.get(state_name, "scheduled")
    error_code = None
    if value.HasField("last_failure") and value.last_failure.HasField(
        "application_failure_info"
    ):
        error_code = value.last_failure.application_failure_info.type or None
    return {
        "activity_id": value.activity_id or "unavailable",
        "node_id": "unavailable",
        "type": value.activity_type.name or "unavailable",
        "state": mapped_state,
        "attempt": max(1, int(value.attempt or 1)),
        "max_attempts": max(1, int(value.maximum_attempts or 1)),
        "retryable": int(value.attempt or 1) < max(1, int(value.maximum_attempts or 1)),
        "next_retry_at": _proto_timestamp(value.next_attempt_schedule_time)
        if value.HasField("next_attempt_schedule_time")
        else None,
        "started_at": _proto_timestamp(value.last_started_time)
        if value.HasField("last_started_time")
        else None,
        "updated_at": _proto_timestamp(value.last_heartbeat_time)
        if value.HasField("last_heartbeat_time")
        else None,
        "completed_at": None,
        "last_heartbeat_at": _proto_timestamp(value.last_heartbeat_time)
        if value.HasField("last_heartbeat_time")
        else None,
        "error_code": error_code,
    }


def _proto_timestamp(value: Any) -> str:
    return value.ToDatetime(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _waiting_reason(run_state: str) -> str | None:
    return {
        "waiting_gate": "runtime_gate",
        "waiting_signal": "external_input",
        "paused": "operator_paused",
        "cancelling": "cancellation_in_progress",
    }.get(run_state)
