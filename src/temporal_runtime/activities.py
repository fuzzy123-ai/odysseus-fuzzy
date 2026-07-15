"""Bounded Temporal Light Activity catalog and isolated execute-slice adapter."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.headless_write_agent_state import HeadlessWriteAgentStateError
from src.temporal_runtime.authority_adapter import (
    ActivityAuthorityAdapter,
    ActivityAuthorityError,
    AuthorizedActivity,
)
from src.temporal_runtime.workflows import EXECUTE_SLICE_ACTIVITY


HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_PAYLOAD_MAX_BYTES = 16_384
NON_RETRYABLE_REASON_CODES = frozenset(
    {
        "scope_violation",
        "owner_mismatch",
        "plan_revision_conflict",
        "claim_collision",
        "stale_fence",
        "live_go_missing",
        "secret_detected",
        "invalid_manifest",
        "cancelled_by_operator",
    }
)


class ActivityLogicalName(StrEnum):
    RESOLVE_EXECUTION_ROUTE = "resolve_execution_route"
    ACQUIRE_SLICE_CLAIM = "acquire_slice_claim"
    EXECUTE_SLICE = "execute_slice"
    VERIFY_SLICE_EVIDENCE = "verify_slice_evidence"
    PERSIST_EXECUTION_RECEIPT = "persist_execution_receipt"
    RELEASE_SLICE_CLAIM = "release_slice_claim"
    PROPOSE_PLANNING_OUTCOME_REVISION = "propose_planning_outcome_revision"


@dataclass(frozen=True, slots=True)
class ActivityContract:
    logical_name: ActivityLogicalName
    side_effect_class: str
    idempotency_key: str
    requires_current_fence: bool


ACTIVITY_CATALOG = (
    ActivityContract(ActivityLogicalName.RESOLVE_EXECUTION_ROUTE, "read", "manifest_hash", False),
    ActivityContract(ActivityLogicalName.ACQUIRE_SLICE_CLAIM, "authority", "scope_key", True),
    ActivityContract(ActivityLogicalName.EXECUTE_SLICE, "isolated_repo", "effect_id", True),
    ActivityContract(ActivityLogicalName.VERIFY_SLICE_EVIDENCE, "read", "effect_id", True),
    ActivityContract(ActivityLogicalName.PERSIST_EXECUTION_RECEIPT, "state", "effect_id", True),
    ActivityContract(ActivityLogicalName.RELEASE_SLICE_CLAIM, "authority", "claim_id:fence", True),
    ActivityContract(
        ActivityLogicalName.PROPOSE_PLANNING_OUTCOME_REVISION,
        "planning_proposal",
        "planning_revision:effect_id",
        True,
    ),
)


@dataclass(frozen=True, slots=True)
class SliceInvocation:
    agent_run_id: str
    node_id: str
    effect_id: str
    attempt: int
    claimed_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IsolatedExecutionResult:
    artifact_ref: str


Checkpoint = Callable[[str, int, str | None], Awaitable[None]]


class IsolatedActivityBackend(ABC):
    """Marker boundary for fake/sandboxed backends; implementations get no provider data."""

    backend_id: str

    @abstractmethod
    async def execute(
        self,
        invocation: SliceInvocation,
        checkpoint: Checkpoint,
    ) -> IsolatedExecutionResult:
        raise NotImplementedError


class IsolatedBackendError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = _safe_code(code)
        self.retryable = bool(retryable)
        super().__init__(self.code)


class FakeIsolatedActivityBackend(IsolatedActivityBackend):
    """No-effect backend used by local verification and replay-safe integration tests."""

    def __init__(self, backend_id: str = "fake-local") -> None:
        self.backend_id = _safe_backend_id(backend_id)
        self.execution_count = 0

    async def execute(
        self,
        invocation: SliceInvocation,
        checkpoint: Checkpoint,
    ) -> IsolatedExecutionResult:
        self.execution_count += 1
        await checkpoint("executing", 1, None)
        await checkpoint("verifying", 2, f"artifact:{invocation.node_id}")
        return IsolatedExecutionResult(artifact_ref=f"artifact:{invocation.node_id}")


HeartbeatSink = Callable[[Mapping[str, Any]], None]
Clock = Callable[[], datetime]


class TemporalLightActivities:
    """Temporal registration surface for strictly pre-authorized isolated work."""

    def __init__(
        self,
        authority: ActivityAuthorityAdapter,
        backends: tuple[IsolatedActivityBackend, ...],
        *,
        heartbeat_sink: HeartbeatSink | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(authority, ActivityAuthorityAdapter):
            raise TypeError("authority must be an ActivityAuthorityAdapter")
        resolved: dict[str, IsolatedActivityBackend] = {}
        for backend in backends:
            if not isinstance(backend, IsolatedActivityBackend):
                raise TypeError("only IsolatedActivityBackend implementations are allowed")
            backend_id = _safe_backend_id(backend.backend_id)
            if backend_id in resolved:
                raise ValueError("duplicate isolated backend id")
            resolved[backend_id] = backend
        if not resolved:
            raise ValueError("at least one isolated backend is required")
        self.authority = authority
        self.backends = MappingProxyType(resolved)
        self._heartbeat_sink = heartbeat_sink
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @activity.defn(name=EXECUTE_SLICE_ACTIVITY)
    async def execute_slice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        info = activity.info()
        return await self.execute_for_test(
            payload,
            attempt=info.attempt,
            activity_id=info.activity_id,
            heartbeat_sink=self._heartbeat_sink or _temporal_heartbeat,
        )

    async def execute_for_test(
        self,
        payload: Mapping[str, Any],
        *,
        attempt: int,
        activity_id: str,
        heartbeat_sink: HeartbeatSink | None = None,
    ) -> dict[str, Any]:
        sink = heartbeat_sink or self._heartbeat_sink
        if sink is None:
            raise ValueError("heartbeat_sink is required outside a Temporal Activity")
        authorized: AuthorizedActivity | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            authorized = self.authority.authorize(payload, attempt=attempt)
            if authorized.is_duplicate:
                return _activity_result(
                    authorized.spec.node_id,
                    authorized.duplicate_result_ref or "",
                )
            backend = self.backends.get(authorized.spec.backend_id)
            if backend is None:
                raise IsolatedBackendError("live_go_missing", retryable=False)

            async def checkpoint(
                phase: str,
                progress_cursor: int,
                artifact_ref: str | None,
            ) -> None:
                self.authority.heartbeat(authorized)
                sink(
                    _heartbeat_payload(
                        authorized,
                        activity_id=activity_id,
                        attempt=attempt,
                        phase=phase,
                        progress_cursor=progress_cursor,
                        artifact_ref=artifact_ref,
                        observed_at=self._now(),
                    )
                )

            await checkpoint("authorized", 0, None)
            heartbeat_task = asyncio.create_task(
                _periodic_heartbeat(checkpoint),
                name=f"tlr-heartbeat:{authorized.spec.node_id}",
            )
            invocation = SliceInvocation(
                agent_run_id=authorized.spec.agent_run_id,
                node_id=authorized.spec.node_id,
                effect_id=authorized.effect_id,
                attempt=attempt,
                claimed_paths=authorized.spec.claimed_paths,
            )
            result = await backend.execute(invocation, checkpoint)
            if not isinstance(result, IsolatedExecutionResult):
                raise IsolatedBackendError("invalid_backend_result", retryable=False)
            await checkpoint("persisting_receipt", 3, result.artifact_ref)
            receipt = self.authority.succeed(authorized)
            return _activity_result(authorized.spec.node_id, receipt.result_ref or "")
        except asyncio.CancelledError:
            if authorized is not None and not authorized.is_duplicate:
                try:
                    self.authority.cancel(authorized)
                except HeadlessWriteAgentStateError:
                    pass
            raise
        except (ActivityAuthorityError, HeadlessWriteAgentStateError) as exc:
            code = exc.code
            if authorized is not None and not authorized.is_duplicate:
                _best_effort_failure(self.authority, authorized, code)
            raise _application_error(code, detail=exc.detail) from exc
        except IsolatedBackendError as exc:
            if authorized is not None:
                _best_effort_failure(self.authority, authorized, exc.code)
            raise ApplicationError(
                exc.code,
                type=exc.code,
                non_retryable=(not exc.retryable or exc.code in NON_RETRYABLE_REASON_CODES),
            ) from exc
        except Exception as exc:
            if authorized is not None:
                _best_effort_failure(self.authority, authorized, "isolated_backend_failure")
            raise ApplicationError(
                "isolated backend failure",
                type="isolated_backend_failure",
                non_retryable=False,
            ) from exc
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


async def _periodic_heartbeat(checkpoint: Checkpoint) -> None:
    cursor = 0
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        cursor += 1
        await checkpoint("executing", cursor, None)


def _temporal_heartbeat(payload: Mapping[str, Any]) -> None:
    activity.heartbeat(dict(payload))


def _heartbeat_payload(
    authorized: AuthorizedActivity,
    *,
    activity_id: str,
    attempt: int,
    phase: str,
    progress_cursor: int,
    artifact_ref: str | None,
    observed_at: datetime,
) -> dict[str, Any]:
    payload = {
        "activity_id": _safe_code(activity_id),
        "node_id": authorized.spec.node_id,
        "attempt": attempt,
        "phase": _safe_code(phase),
        "progress_cursor": progress_cursor,
        "last_durable_artifact_ref": _safe_artifact_ref(artifact_ref),
        "lease_revision": authorized.claim.fence,
        "observed_at": observed_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > HEARTBEAT_PAYLOAD_MAX_BYTES:
        raise IsolatedBackendError("heartbeat_payload_too_large", retryable=False)
    return payload


def _activity_result(node_id: str, receipt_ref: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": "succeeded",
        "evidence_verified": True,
        "writeback_receipt": receipt_ref,
    }


def _application_error(code: str, *, detail: str) -> ApplicationError:
    return ApplicationError(
        detail,
        type=code,
        non_retryable=code in NON_RETRYABLE_REASON_CODES,
    )


def _best_effort_failure(
    authority: ActivityAuthorityAdapter,
    authorized: AuthorizedActivity,
    code: str,
) -> None:
    try:
        authority.fail(authorized, failure_code=_safe_code(code))
    except HeadlessWriteAgentStateError:
        try:
            authority.release(authorized)
        except HeadlessWriteAgentStateError:
            pass


def _safe_backend_id(value: Any) -> str:
    text = _safe_code(value)
    if not (text.startswith("fake-") or text.startswith("isolated-")):
        raise ValueError("backend id must declare fake- or isolated- boundary")
    return text


def _safe_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or not all(c.isalnum() or c in "._:@-" for c in text):
        raise ValueError("unsafe identifier")
    return text


def _safe_artifact_ref(value: Any) -> str | None:
    if value is None:
        return None
    text = _safe_code(value)
    lowered = text.lower()
    if any(part in lowered for part in ("secret", "token", "password", "credential")):
        raise IsolatedBackendError("secret_detected", retryable=False)
    return text
