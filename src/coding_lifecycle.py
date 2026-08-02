"""Canonical coding lifecycle view for coding/orchestration work.

This module is intentionally side-effect free.  It aggregates existing Coding
Agent, runner, sandbox, handoff and quality-gate payloads into one redacted
view that dashboard and route adapters can consume without starting jobs,
writing git state, dispatching threads or exposing raw tool output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from src.runtime_event_envelope import build_runtime_event, stable_payload_hash


CODING_LIFECYCLE_SCHEMA = "odysseus.coding_lifecycle.v1"

CANONICAL_CODING_LIFECYCLE_STAGES = (
    "intake",
    "scoped_task",
    "worktree_plan",
    "patch_plan",
    "checks_plan",
    "checks_result",
    "review_gate",
    "handoff",
    "publish_plan",
    "verified_done",
)

CODING_LIFECYCLE_STATUSES = (
    "pending",
    "planned",
    "running",
    "review_ready",
    "publish_ready",
    "blocked",
    "failed",
    "done",
)

_READY_DECISIONS = {"plan_ready", "created", "verified"}
_BLOCKING_DECISIONS = {"blocked", "hold", "no_go", "failed"}
_RUNNER_PHASE_TO_STATUS = {
    "planned": "planned",
    "scoped": "planned",
    "worktree_ready": "planned",
    "checks_running": "running",
    "review_ready": "review_ready",
    "publish_ready": "publish_ready",
    "done": "done",
    "blocked": "blocked",
    "failed": "failed",
}
_STAGE_SET = set(CANONICAL_CODING_LIFECYCLE_STAGES)
_STATUS_SET = set(CODING_LIFECYCLE_STATUSES)
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id|credential)\b\s*[:=]?\s*\S*"
)
_HOST_PATH_RE = re.compile(r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])", re.IGNORECASE)
_RAW_FIELD_NAMES = {
    "authorization",
    "authorization_header",
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
    "stderr_preview",
    "stdout",
    "stdout_preview",
    "token",
    "unredacted_tool_output",
}


class CodingLifecycleError(ValueError):
    """Raised when a canonical lifecycle payload would be invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class CodingLifecycleStage:
    stage: str
    status: str
    evidence_refs: tuple[str, ...] = ()
    gate_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        stage: Any,
        status: Any,
        evidence_refs: Iterable[Any] = (),
        gate_ids: Iterable[Any] = (),
        blockers: Iterable[Any] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "CodingLifecycleStage":
        normalized_stage = _canonical_stage(stage)
        normalized_status = _canonical_status(status)
        return cls(
            stage=normalized_stage,
            status=normalized_status,
            evidence_refs=tuple(_safe_ref(ref) for ref in evidence_refs if _safe_ref(ref)),
            gate_ids=tuple(dict.fromkeys(_safe_label(gate, field="gate_id") for gate in gate_ids if str(gate or ""))),
            blockers=tuple(dict.fromkeys(_safe_summary(blocker) for blocker in blockers if str(blocker or ""))),
            metadata=_safe_metadata(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "stage": self.stage,
            "status": self.status,
            "evidence_refs": self.evidence_refs,
            "gate_ids": self.gate_ids,
            "blockers": self.blockers,
            "metadata": dict(self.metadata),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


@dataclass(frozen=True, slots=True)
class CodingLifecycleState:
    coding_task_id: str
    repo_id: str
    status: str
    next_action: str
    stages: tuple[CodingLifecycleStage, ...]
    objective_hash: str = ""
    gates_waiting: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    live_git_write_allowed: bool = False
    live_thread_dispatch_allowed: bool = False
    raw_content_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": CODING_LIFECYCLE_SCHEMA,
            "coding_task_id": self.coding_task_id,
            "repo_id": self.repo_id,
            "status": self.status,
            "next_action": self.next_action,
            "objective_hash": self.objective_hash,
            "stages": tuple(stage.to_dict() for stage in self.stages),
            "gates_waiting": self.gates_waiting,
            "blockers": self.blockers,
            "live_git_write_allowed": bool(self.live_git_write_allowed),
            "live_thread_dispatch_allowed": bool(self.live_thread_dispatch_allowed),
            "raw_content_visible": False,
            "runtime_event": self.runtime_event(),
        }
        _reject_unsafe_payload(payload)
        return payload

    def runtime_event(self) -> dict[str, Any]:
        return build_runtime_event(
            surface="coding_agent",
            component="coding_lifecycle",
            event_type="lifecycle_state",
            status=_event_status(self.status),
            severity=_event_severity(self.status),
            owner_scope=f"repo:{self.repo_id}",
            correlation_id=self.coding_task_id,
            task_id=self.coding_task_id,
            gate_ids=self.gates_waiting,
            side_effects=("none",),
            metadata={
                "lifecycle_schema": CODING_LIFECYCLE_SCHEMA,
                "lifecycle_status": self.status,
                "stage_count": len(self.stages),
                "blocking_count": len(self.blockers),
                "next_action": self.next_action,
            },
        )


def build_coding_lifecycle_state(
    *,
    task_id: Any = "",
    repo_id: Any = "",
    objective: Any = "",
    coding_plan: Any = None,
    runner_state: Any = None,
    sandbox_dispatch: Any = None,
    quality_gate: Any = None,
    done_gate: Any = None,
    handoff: Any = None,
    publish_plan: Any = None,
    orchestration_node_id: Any = "",
    handoff_ref: Any = "",
    publish_plan_id: Any = "",
    live_git_write_allowed: bool = False,
    live_thread_dispatch_allowed: bool = False,
) -> CodingLifecycleState:
    """Build one redacted canonical lifecycle state from existing surfaces."""

    task = _first_present(
        task_id,
        _get(coding_plan, "task_id"),
        _get(runner_state, "task_id"),
        _get(sandbox_dispatch, "task_id"),
        _get(publish_plan, "task_id"),
        _get(handoff, "task_id"),
    )
    repo = _first_present(
        repo_id,
        _get(coding_plan, "repo_id"),
        _get(runner_state, "repo_id"),
        _get(publish_plan, "repo_id"),
        _get(handoff, "repo_id"),
        "unknown",
    )
    objective_value = _first_present(objective, _get(coding_plan, "objective"))
    task_label = _safe_label(task or f"task-{stable_payload_hash((repo, objective_value))[-16:]}", field="coding_task_id")
    repo_label = _safe_label(repo, field="repo_id")
    objective_hash = stable_payload_hash(objective_value) if str(objective_value or "").strip() else ""

    plan_decision = _choice(_get(coding_plan, "decision"))
    runner_phase = _choice(_get(runner_state, "phase"))
    dispatch_quality = _mapping(_get(sandbox_dispatch, "quality_gate"))
    quality_payload = _quality_payload(quality_gate) or dispatch_quality
    done_payload = _safe_metadata(_mapping_or_dict(done_gate))
    handoff_payload = _mapping_or_dict(handoff)
    publish_payload = _mapping_or_dict(publish_plan)

    gates_waiting = _dedupe(
        [
            *_iterable(_get(runner_state, "gates_waiting")),
            *_quality_gate_ids(quality_payload),
            *_quality_gate_ids(done_payload),
        ]
    )
    blockers = _dedupe(
        [
            *_iterable(_get(coding_plan, "blockers")),
            *_iterable(_get(runner_state, "blockers")),
            *_iterable(quality_payload.get("blockers")),
            *_iterable(done_payload.get("blockers")),
            *_iterable(handoff_payload.get("blockers")),
            *_iterable(publish_payload.get("blockers")),
        ]
    )

    stages: dict[str, CodingLifecycleStage] = {}

    stages["intake"] = CodingLifecycleStage.create(
        stage="intake",
        status="planned" if objective_hash or coding_plan is not None or runner_state is not None else "pending",
        metadata={
            "objective_hash_present": bool(objective_hash),
            "orchestration_node_id": _safe_ref(orchestration_node_id),
        },
    )
    stages["scoped_task"] = CodingLifecycleStage.create(
        stage="scoped_task",
        status=_status_for_plan_decision(plan_decision, runner_phase),
        evidence_refs=(_safe_ref(task_label),),
        blockers=_iterable(_get(coding_plan, "blockers")),
        metadata={
            "allowed_path_count": len(_iterable(_get(coding_plan, "allowed_paths"))),
            "blocked_path_count": len(_iterable(_get(coding_plan, "blocked_paths"))),
            "check_count": len(_iterable(_get(coding_plan, "checks"))),
        },
    )
    stages["worktree_plan"] = CodingLifecycleStage.create(
        stage="worktree_plan",
        status=_worktree_status(coding_plan, runner_phase),
        metadata={
            "worktree_ref_present": bool(_get(coding_plan, "worktree_ref") or _get(handoff, "source_worktree")),
            "git_mutation_executed": False,
        },
    )
    stages["patch_plan"] = CodingLifecycleStage.create(
        stage="patch_plan",
        status=_patch_status(coding_plan, quality_payload, runner_phase),
        metadata={
            "changed_path_count": len(_iterable(quality_payload.get("changed_paths"))),
            "bounded_patch_plan_only": True,
        },
    )
    stages["checks_plan"] = CodingLifecycleStage.create(
        stage="checks_plan",
        status=_checks_plan_status(coding_plan, sandbox_dispatch, runner_phase),
        metadata={
            "check_count": len(_iterable(_get(coding_plan, "checks"))),
            "sandbox_job_count": len(_iterable(_get(sandbox_dispatch, "jobs"))),
            "network_mode": "none" if sandbox_dispatch is not None else "",
            "secrets_attached": False,
        },
    )
    stages["checks_result"] = CodingLifecycleStage.create(
        stage="checks_result",
        status=_checks_result_status(quality_payload, sandbox_dispatch, runner_phase),
        evidence_refs=_dispatch_evidence_refs(sandbox_dispatch),
        gate_ids=_quality_gate_ids(quality_payload),
        blockers=_iterable(quality_payload.get("blockers")),
        metadata={
            "verified": bool(quality_payload.get("verified") or quality_payload.get("status") == "verified"),
            "warning_count": len(_iterable(quality_payload.get("warnings") or quality_payload.get("warning_gate_ids"))),
        },
    )
    stages["review_gate"] = CodingLifecycleStage.create(
        stage="review_gate",
        status=_review_status(quality_payload, done_payload, runner_phase),
        gate_ids=_quality_gate_ids(quality_payload),
        blockers=[*_iterable(quality_payload.get("blockers")), *_iterable(done_payload.get("blockers"))],
        metadata={
            "content_reviewed": bool(done_payload.get("content_reviewed")),
            "review_decision": _safe_label(done_payload.get("review_decision") or "", field="review_decision"),
        },
    )
    stages["handoff"] = CodingLifecycleStage.create(
        stage="handoff",
        status=_handoff_status(handoff_payload, runner_phase),
        evidence_refs=(_safe_ref(handoff_ref),),
        blockers=_iterable(handoff_payload.get("blockers")),
        metadata={
            "changed_path_count": len(_iterable(handoff_payload.get("changed_paths"))),
            "target_mode": _safe_label(handoff_payload.get("target_mode") or "", field="target_mode"),
        },
    )
    publish_status = _publish_status(publish_payload, runner_phase)
    publish_gates = ["CAO-GIT-WRITE-GO"] if publish_status == "publish_ready" and not live_git_write_allowed else []
    stages["publish_plan"] = CodingLifecycleStage.create(
        stage="publish_plan",
        status=publish_status,
        evidence_refs=(_safe_ref(publish_plan_id),),
        gate_ids=publish_gates,
        blockers=_iterable(publish_payload.get("blockers")),
        metadata={
            "changed_path_count": len(_iterable(publish_payload.get("changed_paths"))),
            "mutation_allowed": bool(live_git_write_allowed and publish_payload.get("mutation_allowed")),
            "preview_only": not live_git_write_allowed,
        },
    )
    if publish_gates:
        gates_waiting = _dedupe([*gates_waiting, *publish_gates])
    stages["verified_done"] = CodingLifecycleStage.create(
        stage="verified_done",
        status=_verified_done_status(done_payload, runner_phase, quality_payload),
        blockers=_iterable(done_payload.get("blockers")),
        metadata={
            "done_gate_complete": bool(done_payload.get("done") or done_payload.get("status") == "done"),
            "quality_gate_verified": bool(quality_payload.get("verified") or quality_payload.get("status") == "verified"),
        },
    )

    ordered = tuple(stages[stage] for stage in CANONICAL_CODING_LIFECYCLE_STAGES)
    status = _overall_status(ordered, runner_phase, publish_gates)
    next_action = _next_action(status, gates_waiting, publish_gates)
    if status in {"blocked", "failed"} and not blockers:
        blockers = _dedupe(blocker for stage in ordered for blocker in stage.blockers)

    state = CodingLifecycleState(
        coding_task_id=task_label,
        repo_id=repo_label,
        objective_hash=objective_hash,
        status=status,
        next_action=next_action,
        stages=ordered,
        gates_waiting=tuple(_safe_label(gate, field="gate_id") for gate in gates_waiting),
        blockers=tuple(_safe_summary(blocker) for blocker in blockers),
        live_git_write_allowed=bool(live_git_write_allowed),
        live_thread_dispatch_allowed=bool(live_thread_dispatch_allowed),
    )
    _reject_unsafe_payload(state.to_dict())
    return state


def _status_for_plan_decision(decision: str, runner_phase: str) -> str:
    if runner_phase in {"scoped", "worktree_ready", "checks_running", "review_ready", "publish_ready", "done"}:
        return "done" if runner_phase != "scoped" else "planned"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    if decision in _READY_DECISIONS:
        return "planned"
    if decision in _BLOCKING_DECISIONS:
        return "blocked"
    return "pending"


def _worktree_status(plan: Any, runner_phase: str) -> str:
    if runner_phase in {"worktree_ready", "checks_running", "review_ready", "publish_ready", "done"}:
        return "done" if runner_phase != "worktree_ready" else "planned"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    if _get(plan, "worktree_ref"):
        return "planned"
    return "pending"


def _patch_status(plan: Any, quality: Mapping[str, Any], runner_phase: str) -> str:
    if quality.get("changed_paths"):
        return "done" if not quality.get("blockers") else "blocked"
    if runner_phase in {"checks_running", "review_ready", "publish_ready", "done"}:
        return "planned"
    if _get(plan, "allowed_paths"):
        return "planned"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    return "pending"


def _checks_plan_status(plan: Any, dispatch: Any, runner_phase: str) -> str:
    if runner_phase == "checks_running":
        return "running"
    if dispatch is not None or runner_phase in {"review_ready", "publish_ready", "done"}:
        return "done"
    if _get(plan, "checks"):
        return "planned"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    return "pending"


def _checks_result_status(quality: Mapping[str, Any], dispatch: Any, runner_phase: str) -> str:
    if quality.get("blockers") or quality.get("status") == "blocked" or quality.get("verified") is False:
        return "blocked"
    if quality.get("verified") or quality.get("status") == "verified":
        return "done"
    if dispatch is not None:
        return "running" if runner_phase == "checks_running" else "planned"
    if runner_phase == "checks_running":
        return "running"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    return "pending"


def _review_status(quality: Mapping[str, Any], done_gate: Mapping[str, Any], runner_phase: str) -> str:
    if done_gate.get("blockers"):
        return "blocked"
    if done_gate.get("status") == "done" or done_gate.get("done") is True:
        return "done"
    if quality.get("blockers") or quality.get("verified") is False:
        return "blocked"
    if quality.get("verified") or runner_phase == "review_ready":
        return "review_ready"
    if runner_phase in {"publish_ready", "done"}:
        return "done"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    return "pending"


def _handoff_status(handoff: Mapping[str, Any], runner_phase: str) -> str:
    if handoff.get("blockers") or handoff.get("decision") in _BLOCKING_DECISIONS:
        return "blocked"
    if handoff.get("decision") in _READY_DECISIONS or handoff:
        return "planned"
    if runner_phase in {"publish_ready", "done"}:
        return "planned"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    return "pending"


def _publish_status(publish: Mapping[str, Any], runner_phase: str) -> str:
    if publish.get("blockers") or publish.get("commit_decision") in _BLOCKING_DECISIONS or publish.get("push_decision") in _BLOCKING_DECISIONS:
        return "blocked"
    if publish.get("ready") or publish.get("commit_decision") == "plan_ready" or publish.get("push_decision") == "plan_ready":
        return "publish_ready"
    if runner_phase == "publish_ready":
        return "publish_ready"
    if runner_phase == "done":
        return "done"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    return "pending"


def _verified_done_status(done_gate: Mapping[str, Any], runner_phase: str, quality: Mapping[str, Any]) -> str:
    # Runner phase is progress narration, not independent completion evidence.
    # Keep the v1 projection shape stable while refusing to manufacture a
    # verified terminal state from ``phase=done`` alone.
    if done_gate.get("done") is True or done_gate.get("status") == "done":
        return "done"
    if runner_phase in {"blocked", "failed"}:
        return _RUNNER_PHASE_TO_STATUS[runner_phase]
    if done_gate.get("blockers"):
        return "blocked"
    if quality.get("blockers") or quality.get("verified") is False:
        return "blocked"
    return "pending"


def _overall_status(stages: Iterable[CodingLifecycleStage], runner_phase: str, publish_gates: Iterable[str]) -> str:
    statuses = tuple(stage.status for stage in stages)
    if runner_phase == "failed" or "failed" in statuses:
        return "failed"
    if runner_phase == "blocked" or any(status == "blocked" for status in statuses):
        if publish_gates and not any(stage.status == "blocked" for stage in stages):
            return "publish_ready"
        return "blocked"
    if statuses[-1] == "done":
        return "done"
    if "publish_ready" in statuses:
        return "publish_ready"
    if "review_ready" in statuses:
        return "review_ready"
    if "running" in statuses:
        return "running"
    if any(status in {"planned", "done"} for status in statuses):
        return "planned"
    return "pending"


def _next_action(status: str, gates_waiting: Iterable[str], publish_gates: Iterable[str]) -> str:
    if status == "done":
        return "none"
    if status == "failed":
        return "inspect_failure"
    if status == "blocked":
        return "resolve_blockers"
    if publish_gates:
        return "hold_for_git_go"
    if status == "publish_ready":
        return "operator_publish_review"
    if status == "review_ready":
        return "operator_review"
    if status == "running":
        return "wait_for_checks"
    if tuple(gates_waiting):
        return "resolve_gates"
    return "continue"


def _event_status(status: str) -> str:
    return {
        "pending": "queued",
        "planned": "queued",
        "running": "running",
        "review_ready": "warn",
        "publish_ready": "warn",
        "blocked": "blocked",
        "failed": "failed",
        "done": "success",
    }[_canonical_status(status)]


def _event_severity(status: str) -> str:
    if status in {"blocked", "failed"}:
        return "warn" if status == "blocked" else "error"
    if status in {"review_ready", "publish_ready"}:
        return "notice"
    return "info"


def _quality_payload(value: Any) -> dict[str, Any]:
    payload = _mapping_or_dict(value)
    if "quality_gate" in payload and isinstance(payload["quality_gate"], Mapping):
        payload = dict(payload["quality_gate"])
    if "verified_done" in payload or "blocking_gate_ids" in payload:
        payload.setdefault("verified", bool(payload.get("verified_done")))
        payload.setdefault("blockers", tuple(payload.get("blocking_gate_ids") or ()))
    if "status" in payload:
        payload["status"] = _safe_label(payload["status"], field="quality_status")
    return _safe_metadata(payload)


def _quality_gate_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _safe_label(value, field="gate_id")
            for value in (
                [
                    *(_iterable(payload.get("blocking_gate_ids"))),
                    *(_iterable(payload.get("warning_gate_ids"))),
                    *(_iterable(payload.get("gate_ids"))),
                ]
            )
            if str(value or "")
        )
    )


def _dispatch_evidence_refs(dispatch: Any) -> tuple[str, ...]:
    bundle = _mapping(_get(dispatch, "evidence_bundle"))
    refs: list[str] = []
    for artifact in _iterable(bundle.get("artifacts")):
        artifact_payload = _mapping_or_dict(artifact)
        ref = artifact_payload.get("artifact_ref")
        if ref:
            refs.append(_safe_ref(ref))
    return tuple(dict.fromkeys(ref for ref in refs if ref))


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


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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


def _choice(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _canonical_stage(value: Any) -> str:
    text = _choice(value)
    if text not in _STAGE_SET:
        raise CodingLifecycleError(f"unsupported coding lifecycle stage: {value!r}")
    return text


def _canonical_status(value: Any) -> str:
    text = _choice(value)
    if text not in _STATUS_SET:
        raise CodingLifecycleError(f"unsupported coding lifecycle status: {value!r}")
    return text


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return stable_payload_hash(text)
    if len(text) > 180 or not _SAFE_LABEL_RE.fullmatch(text):
        return stable_payload_hash(text)
    return text


def _safe_ref(value: Any) -> str:
    return _safe_label(value, field="reference")


def _safe_summary(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        return stable_payload_hash(text)
    if len(text) > 240:
        return stable_payload_hash(text)
    return text


def _safe_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise CodingLifecycleError("metadata must be a mapping")
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        safe_key = _safe_label(key, field="metadata_key")
        if not safe_key:
            continue
        if safe_key.lower() in _RAW_FIELD_NAMES:
            result[f"{safe_key}_hash"] = stable_payload_hash(value)
            continue
        result[safe_key] = _safe_metadata_value(value)
    _reject_unsafe_payload(result)
    return result


def _safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(0, min(value, 1_000_000_000))
    if isinstance(value, float):
        return max(0.0, min(value, 1_000_000_000.0))
    if isinstance(value, str):
        return _safe_summary(value)
    if isinstance(value, (tuple, list)):
        return tuple(_safe_metadata_value(item) for item in value[:20])
    if isinstance(value, Mapping):
        return _safe_metadata(value)
    if hasattr(value, "to_dict"):
        return _safe_metadata(_mapping_or_dict(value))
    return stable_payload_hash(value)


def _dedupe(values: Iterable[Any]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_summary(value)
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    if key.lower() in _RAW_FIELD_NAMES:
        raise CodingLifecycleError("coding lifecycle payload contains a raw field")
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
            raise CodingLifecycleError("coding lifecycle payload contains secret material")
        if _HOST_PATH_RE.search(value):
            raise CodingLifecycleError("coding lifecycle payload contains a private host path")
