"""Identifier adapters for the canonical coding lifecycle.

CAO3 keeps this layer as a pure mapper.  It translates existing Coding Agent,
Server Project and Orchestration payloads into shared lifecycle identifiers
without starting tasks, dispatching threads, touching git or exposing raw
objective/output/path material.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from src.coding_lifecycle import CODING_LIFECYCLE_SCHEMA
from src.runtime_event_envelope import build_runtime_event, stable_payload_hash


CODING_LIFECYCLE_IDENTIFIER_MAP_SCHEMA = "odysseus.coding_lifecycle.identifier_map.v1"

IDENTIFIER_KEYS = (
    "coding_task_id",
    "repo_id",
    "server_project_id",
    "server_project_task_id",
    "orchestration_node_id",
    "agent_run_ids",
    "check_job_ids",
    "gate_ids",
    "handoff_ref",
    "publish_plan_id",
)

_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id|credential)\b\s*[:=]?\s*\S*"
)
_HOST_PATH_RE = re.compile(r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])", re.IGNORECASE)
_RAW_FIELD_NAMES = {
    "authorization",
    "chat_id",
    "content",
    "credential",
    "diff",
    "document_text",
    "email_body",
    "env",
    "message_text",
    "output",
    "password",
    "patch",
    "private_document_text",
    "raw",
    "raw_content",
    "raw_output",
    "raw_prompt",
    "secret",
    "stderr",
    "stdout",
    "token",
    "unredacted_tool_output",
}


class CodingLifecycleAdapterError(ValueError):
    """Raised when an adapter cannot produce a safe identifier map."""


@dataclass(frozen=True, slots=True)
class CodingLifecycleIdentifierMap:
    coding_task_id: str = ""
    repo_id: str = ""
    server_project_id: str = ""
    server_project_task_id: str = ""
    orchestration_node_id: str = ""
    agent_run_ids: tuple[str, ...] = ()
    check_job_ids: tuple[str, ...] = ()
    gate_ids: tuple[str, ...] = ()
    handoff_ref: str = ""
    publish_plan_id: str = ""
    source_surfaces: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        coding_task_id: Any = "",
        repo_id: Any = "",
        server_project_id: Any = "",
        server_project_task_id: Any = "",
        orchestration_node_id: Any = "",
        agent_run_ids: Iterable[Any] = (),
        check_job_ids: Iterable[Any] = (),
        gate_ids: Iterable[Any] = (),
        handoff_ref: Any = "",
        publish_plan_id: Any = "",
        source_surfaces: Iterable[Any] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "CodingLifecycleIdentifierMap":
        payload = cls(
            coding_task_id=_safe_label(coding_task_id, field="coding_task_id"),
            repo_id=_safe_label(repo_id, field="repo_id"),
            server_project_id=_safe_label(server_project_id, field="server_project_id"),
            server_project_task_id=_safe_label(server_project_task_id, field="server_project_task_id"),
            orchestration_node_id=_safe_label(orchestration_node_id, field="orchestration_node_id"),
            agent_run_ids=_safe_tuple(agent_run_ids, field="agent_run_id"),
            check_job_ids=_safe_tuple(check_job_ids, field="check_job_id"),
            gate_ids=_safe_tuple(gate_ids, field="gate_id"),
            handoff_ref=_safe_label(handoff_ref, field="handoff_ref"),
            publish_plan_id=_safe_label(publish_plan_id, field="publish_plan_id"),
            source_surfaces=_safe_tuple(source_surfaces, field="source_surface"),
            metadata=_safe_metadata(metadata or {}),
        )
        _reject_unsafe_payload(payload.to_dict(include_event=False))
        return payload

    def to_dict(self, *, include_event: bool = True) -> dict[str, Any]:
        payload = {
            "schema": CODING_LIFECYCLE_IDENTIFIER_MAP_SCHEMA,
            "lifecycle_schema": CODING_LIFECYCLE_SCHEMA,
            "coding_task_id": self.coding_task_id,
            "repo_id": self.repo_id,
            "server_project_id": self.server_project_id,
            "server_project_task_id": self.server_project_task_id,
            "orchestration_node_id": self.orchestration_node_id,
            "agent_run_ids": self.agent_run_ids,
            "check_job_ids": self.check_job_ids,
            "gate_ids": self.gate_ids,
            "handoff_ref": self.handoff_ref,
            "publish_plan_id": self.publish_plan_id,
            "source_surfaces": self.source_surfaces,
            "metadata": dict(self.metadata),
            "raw_content_visible": False,
        }
        if include_event:
            payload["runtime_event"] = self.runtime_event()
        _reject_unsafe_payload(payload)
        return payload

    def runtime_event(self) -> dict[str, Any]:
        return build_runtime_event(
            surface="coding_agent",
            component="coding_lifecycle_adapters",
            event_type="identifier_map",
            status="queued",
            severity="info",
            owner_scope=f"repo:{self.repo_id or 'unknown'}",
            correlation_id=self.coding_task_id or self.server_project_task_id or self.orchestration_node_id,
            task_id=self.coding_task_id,
            run_id=self.agent_run_ids[0] if self.agent_run_ids else "",
            gate_ids=self.gate_ids,
            side_effects=("none",),
            metadata={
                "identifier_schema": CODING_LIFECYCLE_IDENTIFIER_MAP_SCHEMA,
                "source_surfaces": self.source_surfaces,
                "check_job_count": len(self.check_job_ids),
                "agent_run_count": len(self.agent_run_ids),
            },
        )


def identifiers_from_coding_agent(
    *,
    coding_plan: Any = None,
    runner_state: Any = None,
    sandbox_dispatch: Any = None,
    quality_gate: Any = None,
    handoff: Any = None,
    publish_plan: Any = None,
    orchestration_node_id: Any = "",
) -> CodingLifecycleIdentifierMap:
    task_id = _first_present(
        _get(coding_plan, "task_id"),
        _get(runner_state, "task_id"),
        _get(sandbox_dispatch, "task_id"),
        _get(handoff, "task_id"),
        _get(publish_plan, "task_id"),
    )
    repo_id = _first_present(_get(coding_plan, "repo_id"), _get(runner_state, "repo_id"), _get(handoff, "repo_id"), _get(publish_plan, "repo_id"))
    fallback_task = _derived_id("coding", repo_id, _get(coding_plan, "objective")) if not task_id else task_id
    check_job_ids = [
        *_job_ids_from_dispatch(sandbox_dispatch),
        *_derived_check_ids(fallback_task, _get(coding_plan, "checks")),
    ]
    gate_ids = [*_gate_ids_from_quality(quality_gate), *_gate_ids_from_quality(_get(sandbox_dispatch, "quality_gate"))]
    handoff_ref = _first_present(_get(handoff, "handoff_ref"), _get(handoff, "message_id"), _derived_handoff_ref(handoff))
    publish_plan_id = _first_present(_get(publish_plan, "publish_plan_id"), _derived_publish_plan_id(publish_plan, repo_id, fallback_task))
    return CodingLifecycleIdentifierMap.create(
        coding_task_id=fallback_task,
        repo_id=repo_id,
        orchestration_node_id=orchestration_node_id,
        check_job_ids=check_job_ids,
        gate_ids=gate_ids,
        handoff_ref=handoff_ref,
        publish_plan_id=publish_plan_id,
        source_surfaces=("coding_agent",),
        metadata={
            "coding_plan_present": coding_plan is not None,
            "runner_state_present": runner_state is not None,
            "sandbox_dispatch_present": sandbox_dispatch is not None,
            "quality_gate_present": quality_gate is not None,
            "handoff_present": bool(handoff),
            "publish_plan_present": bool(publish_plan),
        },
    )


def identifiers_from_server_project(
    *,
    runner_plan: Any = None,
    project_record: Any = None,
    task_plan: Any = None,
    task_report: Any = None,
) -> CodingLifecycleIdentifierMap:
    project_id = _first_present(
        _get(runner_plan, "project_id"),
        _get(project_record, "project_slug"),
        _get(_get(project_record, "project_spec"), "project_slug"),
        _get(task_plan, "project_slug"),
        _get(_get(task_report, "plan"), "project_slug"),
    )
    repo_id = _first_present(
        _get(_get(runner_plan, "project_spec"), "repo_name"),
        _get(_get(project_record, "project_spec"), "repo_name"),
        project_id,
    )
    objective = _first_present(_get(task_plan, "objective"), _get(_get(task_report, "plan"), "objective"))
    task_id = _derived_id("server-project-task", project_id, objective) if (task_plan is not None or task_report is not None) else ""
    checks_source = _first_present(_get(task_plan, "checks"), _get(_get(task_report, "plan"), "checks"), _get(runner_plan, "quality_gate_commands"))
    check_job_ids = _derived_check_ids(task_id or project_id, checks_source)
    gate_ids = list(_quality_step_gate_ids(_get(runner_plan, "planned_steps")))
    if _get(runner_plan, "operator_gate"):
        gate_ids.append("server-project-live-go")
    return CodingLifecycleIdentifierMap.create(
        repo_id=repo_id,
        server_project_id=project_id,
        server_project_task_id=task_id,
        check_job_ids=check_job_ids,
        gate_ids=gate_ids,
        source_surfaces=("server_project",),
        metadata={
            "runner_plan_present": runner_plan is not None,
            "project_record_present": project_record is not None,
            "task_plan_present": task_plan is not None,
            "task_report_present": task_report is not None,
        },
    )


def identifiers_from_orchestration(
    *,
    runtime_node: Any = None,
    thread_ref: Any = None,
    dispatch_request: Any = None,
    dispatch_intent: Any = None,
    mailbox_message: Any = None,
    parsed_handoff: Any = None,
    runtime_tick: Any = None,
) -> CodingLifecycleIdentifierMap:
    request = _first_present(dispatch_request, _get(dispatch_intent, "request"))
    ref = _first_present(thread_ref, _get(request, "thread_ref"), _get(mailbox_message, "thread_ref"))
    source_handoff = _first_present(parsed_handoff, _get(dispatch_intent, "source_handoff"), _get(mailbox_message, "source_handoff"))
    node_id = _first_present(
        _get(runtime_node, "node_id"),
        _get(runtime_node, "id"),
        _get(ref, "node_id"),
        _get(request, "expected_node_id"),
        _get(source_handoff, "slice_id"),
    )
    agent_run_ids = _dedupe(
        [
            _get(ref, "agent_run_id"),
            _get(request, "expected_agent_run_id"),
            *_iterable(_get(runtime_tick, "agent_run_ids")),
        ]
    )
    handoff_ref = _first_present(_get(mailbox_message, "message_id"), _derived_handoff_ref(source_handoff))
    return CodingLifecycleIdentifierMap.create(
        orchestration_node_id=node_id,
        agent_run_ids=agent_run_ids,
        handoff_ref=handoff_ref,
        source_surfaces=("orchestration",),
        metadata={
            "runtime_node_present": runtime_node is not None,
            "thread_ref_present": ref != "",
            "dispatch_request_present": request != "",
            "mailbox_message_present": mailbox_message is not None,
            "runtime_tick_present": runtime_tick is not None,
        },
    )


def merge_identifier_maps(*maps: CodingLifecycleIdentifierMap) -> CodingLifecycleIdentifierMap:
    if not maps:
        raise CodingLifecycleAdapterError("at least one identifier map is required")
    for item in maps:
        if not isinstance(item, CodingLifecycleIdentifierMap):
            raise CodingLifecycleAdapterError("merge inputs must be CodingLifecycleIdentifierMap instances")
    conflicts = _conflicts(maps)
    if conflicts:
        raise CodingLifecycleAdapterError("identifier conflict: " + ", ".join(conflicts))
    return CodingLifecycleIdentifierMap.create(
        coding_task_id=_first_non_empty(map_item.coding_task_id for map_item in maps),
        repo_id=_first_non_empty(map_item.repo_id for map_item in maps),
        server_project_id=_first_non_empty(map_item.server_project_id for map_item in maps),
        server_project_task_id=_first_non_empty(map_item.server_project_task_id for map_item in maps),
        orchestration_node_id=_first_non_empty(map_item.orchestration_node_id for map_item in maps),
        agent_run_ids=_flatten(map_item.agent_run_ids for map_item in maps),
        check_job_ids=_flatten(map_item.check_job_ids for map_item in maps),
        gate_ids=_flatten(map_item.gate_ids for map_item in maps),
        handoff_ref=_first_non_empty(map_item.handoff_ref for map_item in maps),
        publish_plan_id=_first_non_empty(map_item.publish_plan_id for map_item in maps),
        source_surfaces=_flatten(map_item.source_surfaces for map_item in maps),
        metadata={
            "merged_map_count": len(maps),
            "source_identifier_schemas": tuple(CODING_LIFECYCLE_IDENTIFIER_MAP_SCHEMA for _ in maps),
        },
    )


def _conflicts(maps: tuple[CodingLifecycleIdentifierMap, ...]) -> tuple[str, ...]:
    conflicts: list[str] = []
    for field_name in ("coding_task_id", "repo_id", "server_project_id", "server_project_task_id", "orchestration_node_id", "handoff_ref", "publish_plan_id"):
        values = tuple(dict.fromkeys(getattr(item, field_name) for item in maps if getattr(item, field_name)))
        if len(values) > 1:
            conflicts.append(field_name)
    return tuple(conflicts)


def _job_ids_from_dispatch(dispatch: Any) -> tuple[str, ...]:
    return tuple(_get(job, "job_id") for job in _iterable(_get(dispatch, "jobs")) if _get(job, "job_id"))


def _derived_check_ids(prefix: Any, checks: Any) -> tuple[str, ...]:
    safe_prefix = _safe_label(prefix, field="check_prefix") or "check"
    return tuple(f"{safe_prefix}-check-{index}" for index, _ in enumerate(_iterable(checks), start=1))


def _gate_ids_from_quality(value: Any) -> tuple[str, ...]:
    payload = _mapping_or_dict(value)
    gates = [
        *_iterable(payload.get("blocking_gate_ids")),
        *_iterable(payload.get("warning_gate_ids")),
        *_iterable(payload.get("gate_ids")),
    ]
    if not gates and payload.get("blockers"):
        gates = ["quality-blocked"]
    return _safe_tuple(gates, field="gate_id")


def _quality_step_gate_ids(steps: Any) -> tuple[str, ...]:
    gate_ids: list[str] = []
    for step in _iterable(steps):
        step_id = _get(step, "step_id")
        if str(step_id).startswith("quality_gate_"):
            gate_ids.append(step_id)
    return tuple(gate_ids)


def _derived_handoff_ref(handoff: Any) -> str:
    if not handoff:
        return ""
    payload = _mapping_or_dict(handoff)
    seed = {
        "agent": payload.get("agent"),
        "slice_id": payload.get("slice_id"),
        "status": payload.get("status"),
        "next_slice": payload.get("next_slice"),
    }
    return "handoff:" + stable_payload_hash(seed).split(":", 1)[1][:16]


def _derived_publish_plan_id(publish_plan: Any, repo_id: Any, task_id: Any) -> str:
    if not publish_plan:
        return ""
    payload = _mapping_or_dict(publish_plan)
    seed = {
        "repo_id": repo_id,
        "task_id": task_id,
        "branch_name": payload.get("branch_name"),
        "commit_decision": payload.get("commit_decision"),
        "push_decision": payload.get("push_decision"),
    }
    return "publish:" + stable_payload_hash(seed).split(":", 1)[1][:16]


def _derived_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}:{stable_payload_hash(parts).split(':', 1)[1][:16]}"


def _mapping_or_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        raw = value.to_dict()
        if isinstance(raw, Mapping):
            return dict(raw)
    result: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            item = getattr(value, key)
        except Exception:
            continue
        if callable(item):
            continue
        result[key] = item
    return result


def _get(value: Any, key: str, default: Any = "") -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(key, default)
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        if isinstance(payload, Mapping) and key in payload:
            return payload.get(key, default)
    return getattr(value, key, default)


def _iterable(value: Any) -> tuple[Any, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip():
            return value
    return ""


def _first_non_empty(values: Iterable[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _flatten(groups: Iterable[Iterable[str]]) -> tuple[str, ...]:
    flattened: list[str] = []
    for group in groups:
        flattened.extend(group)
    return tuple(dict.fromkeys(item for item in flattened if item))


def _dedupe(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_safe_label(value, field="identifier") for value in values if str(value or "")))


def _safe_tuple(values: Iterable[Any], *, field: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_safe_label(value, field=field) for value in values if str(value or "")))


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return stable_payload_hash(text)
    if len(text) > 180 or not _SAFE_LABEL_RE.fullmatch(text):
        return stable_payload_hash(text)
    return text


def _safe_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise CodingLifecycleAdapterError("metadata must be a mapping")
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        safe_key = _safe_label(key, field="metadata_key")
        if not safe_key:
            continue
        if safe_key.lower() in _RAW_FIELD_NAMES:
            result[f"{safe_key}_hash"] = stable_payload_hash(value)
            continue
        result[safe_key] = _safe_metadata_value(value)
    return result


def _safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(0, min(value, 1_000_000_000))
    if isinstance(value, float):
        return max(0.0, min(value, 1_000_000_000.0))
    if isinstance(value, str):
        return _safe_label(value, field="metadata_value")
    if isinstance(value, (tuple, list)):
        return tuple(_safe_metadata_value(item) for item in value[:20])
    if isinstance(value, Mapping):
        return _safe_metadata(value)
    if hasattr(value, "to_dict"):
        return _safe_metadata(_mapping_or_dict(value))
    return stable_payload_hash(value)


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    if key.lower() in _RAW_FIELD_NAMES:
        raise CodingLifecycleAdapterError("identifier map contains a raw field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _reject_unsafe_payload(nested_value, key=str(nested_key))
        return
    if isinstance(value, (tuple, list, set)):
        for item in value:
            _reject_unsafe_payload(item, key=key)
        return
    if isinstance(value, str):
        if _SECRET_RE.search(value):
            raise CodingLifecycleAdapterError("identifier map contains secret material")
        if _HOST_PATH_RE.search(value):
            raise CodingLifecycleAdapterError("identifier map contains a private host path")
