"""Durable state machine for autonomous coding runner tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from src.constants import DATA_DIR
from src.coding_project_scope import CodingPlanningBinding, CodingProjectScopeError
from src.runtime_event_envelope import stable_payload_hash


RUNNER_STATE_SCHEMA = "odysseus.coding_runner_state.v1"
RUNNER_PHASES = (
    "clarifying",
    "understanding_review",
    "ready_for_plan",
    "planned",
    "scoped",
    "worktree_ready",
    "checks_running",
    "blocked",
    "review_ready",
    "publish_ready",
    "done",
    "failed",
)
_PHASE_TRANSITIONS = {
    "clarifying": {"understanding_review", "blocked", "failed"},
    "understanding_review": {"clarifying", "ready_for_plan", "blocked", "failed"},
    "ready_for_plan": {"planned", "scoped", "blocked", "failed"},
    "planned": {"scoped", "blocked", "failed"},
    "scoped": {"worktree_ready", "checks_running", "publish_ready", "blocked", "failed"},
    "worktree_ready": {"checks_running", "review_ready", "blocked", "failed"},
    "checks_running": {"review_ready", "blocked", "failed"},
    "review_ready": {"publish_ready", "blocked", "done", "failed"},
    "publish_ready": {"done", "blocked", "failed"},
    "blocked": {"clarifying", "understanding_review", "ready_for_plan", "planned", "scoped", "worktree_ready", "checks_running", "review_ready", "publish_ready", "failed"},
    "failed": {"planned", "blocked"},
    "done": set(),
}
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")


class CodingRunnerStateError(ValueError):
    """Raised when a runner state transition is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class CodingRunnerState:
    task_id: str
    repo_id: str
    phase: str
    progress_percent: int
    gates_waiting: tuple[str, ...]
    blockers: tuple[str, ...]
    next_human_decision: str
    updated_at: str
    event_count: int = 1
    planning_item_id: str = ""
    canonical_plan_revision: str = ""
    planning_acceptance_contract: str = ""
    planning_allowed_paths: tuple[str, ...] = ()
    planning_binding_digest: str = ""
    planning_gate_requirements: tuple[str, ...] = ()
    memory_checkpoint_receipt_ids: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        task_id: Any,
        repo_id: Any,
        phase: Any = "planned",
        progress_percent: Any = 0,
        gates_waiting: Iterable[Any] = (),
        blockers: Iterable[Any] = (),
        next_human_decision: Any = "",
        event_count: Any = 1,
        planning_binding: CodingPlanningBinding | Mapping[str, Any] | None = None,
        memory_checkpoint_receipt_ids: Iterable[Any] = (),
    ) -> "CodingRunnerState":
        normalized_phase = _phase(phase)
        binding = _coerce_planning_binding(planning_binding)
        receipt_ids = _checkpoint_receipt_ids(memory_checkpoint_receipt_ids)
        return cls(
            task_id=_safe_label(task_id, "task_id"),
            repo_id=_safe_label(repo_id, "repo_id"),
            phase=normalized_phase,
            progress_percent=_progress(progress_percent),
            gates_waiting=tuple(_safe_label(item, "gate") for item in gates_waiting),
            blockers=tuple(_safe_summary(item, "blocker") for item in blockers),
            next_human_decision=_safe_summary(next_human_decision, "next_human_decision"),
            updated_at=_now_iso(),
            event_count=max(1, int(event_count or 1)),
            planning_item_id=binding.planning_item_id if binding is not None else "",
            canonical_plan_revision=binding.canonical_plan_revision if binding is not None else "",
            planning_acceptance_contract=binding.acceptance_contract if binding is not None else "",
            planning_allowed_paths=binding.allowed_paths if binding is not None else (),
            planning_binding_digest=binding.binding_digest if binding is not None else "",
            planning_gate_requirements=binding.gate_requirements if binding is not None else (),
            memory_checkpoint_receipt_ids=receipt_ids,
        )

    @property
    def planning_bound(self) -> bool:
        if not (
            self.planning_item_id
            and self.canonical_plan_revision
            and self.planning_acceptance_contract
            and self.planning_allowed_paths
            and self.planning_binding_digest
            and self.planning_gate_requirements
        ):
            return False
        try:
            binding = CodingPlanningBinding.from_value(
                {
                    "status": "validated",
                    "planning_item_id": self.planning_item_id,
                    "canonical_plan_revision": self.canonical_plan_revision,
                    "acceptance_contract": self.planning_acceptance_contract,
                    "allowed_paths": self.planning_allowed_paths,
                    "gate_requirements": self.planning_gate_requirements,
                }
            )
        except CodingProjectScopeError:
            return False
        return binding.binding_digest == self.planning_binding_digest

    def planning_binding_dict(self) -> dict[str, Any] | None:
        if not self.planning_bound:
            return None
        return {
            "status": "validated",
            "planning_item_id": self.planning_item_id,
            "canonical_plan_revision": self.canonical_plan_revision,
            "acceptance_contract": self.planning_acceptance_contract,
            "allowed_paths": self.planning_allowed_paths,
            "gate_requirements": self.planning_gate_requirements,
        }

    def transition(
        self,
        *,
        phase: Any,
        progress_percent: Any | None = None,
        gates_waiting: Iterable[Any] | None = None,
        blockers: Iterable[Any] | None = None,
        next_human_decision: Any | None = None,
    ) -> "CodingRunnerState":
        target = _phase(phase)
        if target != self.phase and target not in _PHASE_TRANSITIONS[self.phase]:
            raise CodingRunnerStateError(f"invalid runner transition: {self.phase} -> {target}")
        return CodingRunnerState.create(
            task_id=self.task_id,
            repo_id=self.repo_id,
            phase=target,
            progress_percent=self.progress_percent if progress_percent is None else progress_percent,
            gates_waiting=self.gates_waiting if gates_waiting is None else gates_waiting,
            blockers=self.blockers if blockers is None else blockers,
            next_human_decision=self.next_human_decision if next_human_decision is None else next_human_decision,
            event_count=self.event_count + 1,
            planning_binding=self.planning_binding_dict(),
            memory_checkpoint_receipt_ids=self.memory_checkpoint_receipt_ids,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": RUNNER_STATE_SCHEMA,
            "task_id": self.task_id,
            "repo_id": self.repo_id,
            "phase": self.phase,
            "progress_percent": self.progress_percent,
            "gates_waiting": list(self.gates_waiting),
            "blockers": list(self.blockers),
            "next_human_decision": self.next_human_decision,
            "updated_at": self.updated_at,
            "event_count": self.event_count,
            "planning": {
                "bound": self.planning_bound,
                "item_id": self.planning_item_id,
                "canonical_plan_revision": self.canonical_plan_revision,
                "acceptance_contract": self.planning_acceptance_contract,
                "allowed_paths": list(self.planning_allowed_paths),
                "binding_digest": self.planning_binding_digest,
                "gate_requirements": list(self.planning_gate_requirements),
                "authoritative": False,
            },
            "memory_checkpoint_receipt_ids": list(self.memory_checkpoint_receipt_ids),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


class CodingRunnerStateStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(DATA_DIR) / "coding_runner_state"

    def path_for(self, task_id: Any) -> Path:
        safe = _safe_label(task_id, "task_id")
        return self.root / f"{safe}.json"

    def read(self, task_id: Any) -> CodingRunnerState | None:
        path = self.path_for(task_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CodingRunnerStateError("runner state payload must be an object")
        planning = payload.get("planning") if isinstance(payload.get("planning"), Mapping) else None
        planning_binding = None
        if planning and planning.get("bound"):
            planning_binding = {
                "status": "validated",
                "planning_item_id": planning.get("item_id"),
                "canonical_plan_revision": planning.get("canonical_plan_revision"),
                "acceptance_contract": planning.get("acceptance_contract"),
                "allowed_paths": planning.get("allowed_paths") or (),
                "gate_requirements": planning.get("gate_requirements") or (),
            }
        state = CodingRunnerState.create(
            task_id=payload.get("task_id", ""),
            repo_id=payload.get("repo_id", ""),
            phase=payload.get("phase", "planned"),
            progress_percent=payload.get("progress_percent", 0),
            gates_waiting=payload.get("gates_waiting") or (),
            blockers=payload.get("blockers") or (),
            next_human_decision=payload.get("next_human_decision", ""),
            event_count=payload.get("event_count", 1),
            planning_binding=planning_binding,
            memory_checkpoint_receipt_ids=payload.get("memory_checkpoint_receipt_ids") or (),
        )
        if planning and planning.get("bound") and planning.get("binding_digest") != state.planning_binding_digest:
            raise CodingRunnerStateError("persisted Planning binding digest does not match its canonical facts")
        return state

    def write(self, state: CodingRunnerState) -> CodingRunnerState:
        payload = state.to_dict()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path_for(state.task_id).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return state

    def upsert_from_task_plan(self, plan: Any) -> CodingRunnerState:
        task_id = getattr(plan, "task_id", "")
        repo_id = getattr(plan, "repo_id", "")
        blockers = tuple(getattr(plan, "blockers", ()) or ())
        next_human_decision = str(getattr(plan, "next_human_decision", "") or "")
        binding, binding_error = _planning_binding_from_plan(plan)
        if getattr(plan, "decision", "") == "plan_ready" and binding is not None:
            state = CodingRunnerState.create(
                task_id=task_id,
                repo_id=repo_id,
                phase="scoped",
                progress_percent=20,
                next_human_decision=next_human_decision,
                planning_binding=binding,
            )
        else:
            planning_blockers = blockers
            planning_gates = _gates_from_blockers(blockers)
            decision = next_human_decision
            if getattr(plan, "decision", "") == "plan_ready":
                planning_blockers = tuple(dict.fromkeys((*blockers, binding_error or "validated Planning binding is required")))
                planning_gates = tuple(dict.fromkeys((*planning_gates, "planning_authority")))
                decision = "Attach one current, unambiguous validated Planning item and revision before scope, worktree, or edit steps."
            state = CodingRunnerState.create(
                task_id=task_id,
                repo_id=repo_id,
                phase="blocked",
                progress_percent=0,
                gates_waiting=planning_gates,
                blockers=planning_blockers,
                next_human_decision=decision,
                planning_binding=binding,
            )
        return self.write(state)

    def transition(self, task_id: Any, **kwargs: Any) -> CodingRunnerState:
        current = self.read(task_id)
        if current is None:
            raise CodingRunnerStateError("runner state not found")
        return self.write(current.transition(**kwargs))


def transition_from_sandbox_dispatch(
    *,
    store: CodingRunnerStateStore,
    plan: Any,
    dispatch: Any,
) -> CodingRunnerState:
    """Consume sandbox dispatch evidence into the durable runner phase."""

    task_id = getattr(plan, "task_id", "")
    current = store.read(task_id)
    if current is None:
        current = store.upsert_from_task_plan(plan)
    if current.phase in {"publish_ready", "done"}:
        raise CodingRunnerStateError("sandbox dispatch cannot modify a published or completed runner state")
    if not current.planning_bound:
        return store.write(
            current.transition(
                phase="blocked",
                progress_percent=current.progress_percent,
                gates_waiting=("planning_authority",),
                blockers=("validated Planning item and revision binding is required before checks or edits",),
                next_human_decision="Attach a current validated Planning binding before continuing the coding runner.",
            )
        )
    if current.phase not in {"checks_running", "review_ready", "publish_ready", "done"}:
        current = store.write(
            current.transition(
                phase="checks_running",
                progress_percent=max(current.progress_percent, 45),
                gates_waiting=(),
                blockers=(),
                next_human_decision="Sandbox checks are running or have been dispatched; wait for redacted evidence.",
            )
        )

    quality_gate = _dispatch_quality_gate(dispatch)
    if bool(quality_gate.get("verified")):
        return store.write(
            current.transition(
                phase="review_ready",
                progress_percent=max(current.progress_percent, 65),
                gates_waiting=("operator_review",),
                blockers=(),
                next_human_decision="Review redacted sandbox evidence and approve the coding result before publish gates.",
            )
        )

    blockers = _sandbox_dispatch_blockers(dispatch, quality_gate)
    return store.write(
        current.transition(
            phase="blocked",
            progress_percent=current.progress_percent,
            gates_waiting=("sandbox_check_failure",),
            blockers=blockers,
            next_human_decision="Inspect sandbox evidence and fix the failing check before continuing.",
        )
    )


def transition_from_clarification_run(
    *,
    store: CodingRunnerStateStore,
    task_id: Any,
    repo_id: Any,
    clarification_run: Mapping[str, Any],
) -> CodingRunnerState:
    """Reflect canonical clarification state in the coding runner lifecycle."""

    if not isinstance(clarification_run, Mapping):
        raise CodingRunnerStateError("clarification_run must be an object")
    status = str(clarification_run.get("status") or "").strip().lower()
    ready = bool(clarification_run.get("ready_for_plan"))
    unresolved = _progress(clarification_run.get("unresolved_required_count") or 0)
    clarification_id = _safe_label(clarification_run.get("clarification_id") or "clarification", "clarification_id")
    if ready and status == "ready_for_plan":
        phase = "ready_for_plan"
        progress = 15
        gates = ("create_plan",)
        blockers: tuple[str, ...] = ()
        decision = "Clarification is ready for plan; create or approve the coding plan next."
    elif status == "understanding_review" and unresolved == 0:
        phase = "understanding_review"
        progress = 10
        gates = ("confirm_understanding",)
        blockers = ()
        decision = "Review and confirm the understanding summary before creating a coding plan."
    else:
        phase = "clarifying"
        progress = 5
        gates = ("clarification_required",)
        blockers = (f"clarification {clarification_id} has {unresolved} unresolved required question(s)",)
        decision = "Answer required clarification questions before creating a coding plan."
    return store.write(
        CodingRunnerState.create(
            task_id=task_id,
            repo_id=repo_id,
            phase=phase,
            progress_percent=progress,
            gates_waiting=gates,
            blockers=blockers,
            next_human_decision=decision,
        )
    )


def transition_from_task_control_event(
    *,
    store: CodingRunnerStateStore,
    event: Mapping[str, Any],
) -> CodingRunnerState:
    """Apply one redacted Telegram/workstation control event to a runner state."""

    task_type = str(event.get("task_type") or "")
    if task_type != "coding_agent_task":
        raise CodingRunnerStateError("control event is not for a coding_agent_task")
    task_id = _safe_label(event.get("task_id") or "", "task_id")
    status = _safe_label(event.get("status") or "", "control_status")
    current = store.read(task_id)
    if current is None:
        raise CodingRunnerStateError("runner state not found")
    if status == "pause_requested":
        if current.phase == "done":
            raise CodingRunnerStateError("cannot pause a completed runner state")
        return store.write(
            current.transition(
                phase="blocked",
                progress_percent=current.progress_percent,
                gates_waiting=("telegram_pause_requested",),
                blockers=("telegram pause requested",),
                next_human_decision="Runner is paused by remote control; send resume before continuing.",
            )
        )
    if status == "cancel_requested":
        if current.phase == "done":
            raise CodingRunnerStateError("cannot cancel a completed runner state")
        return store.write(
            current.transition(
                phase="blocked",
                progress_percent=current.progress_percent,
                gates_waiting=("telegram_cancel_requested",),
                blockers=("telegram cancel requested",),
                next_human_decision="Runner cancellation requested; confirm discard or create a new scoped task.",
            )
        )
    if status == "resume_requested":
        if current.phase != "blocked" or "telegram_pause_requested" not in current.gates_waiting:
            return store.write(
                current.transition(
                    phase=current.phase,
                    next_human_decision="Resume noted, but no Telegram pause gate was active.",
                )
            )
        return store.write(
            current.transition(
                phase=_resume_phase_for_progress(current.progress_percent),
                progress_percent=current.progress_percent,
                gates_waiting=(),
                blockers=(),
                next_human_decision="Remote resume accepted; continue from the next gated runner action.",
            )
        )
    raise CodingRunnerStateError("unsupported runner control event")


def record_advisory_memory_checkpoint(
    *,
    store: CodingRunnerStateStore,
    task_id: Any,
    receipt: Mapping[str, Any],
    expected_revision_binding: Any,
) -> CodingRunnerState:
    """Record a bounded advisory receipt without changing phase or gates.

    Memory checkpoints are evidence references only.  They cannot transition
    the runner, satisfy a gate, or replace the current Planning binding.
    """

    if not isinstance(receipt, Mapping):
        raise CodingRunnerStateError("memory checkpoint receipt must be an object")
    current = store.read(task_id)
    if current is None:
        raise CodingRunnerStateError("runner state not found")
    if not current.planning_bound:
        raise CodingRunnerStateError("runner state is missing validated Planning binding")
    if receipt.get("schema") != "odysseus.coding_agent.memory_checkpoint_receipt.v1":
        raise CodingRunnerStateError("memory checkpoint receipt schema is invalid")
    if receipt.get("advisory_only") is not True:
        raise CodingRunnerStateError("memory checkpoint receipt must be advisory only")
    if receipt.get("authority_effect") != "none" or receipt.get("gate_effect") != "none":
        raise CodingRunnerStateError("memory checkpoint receipt cannot affect authority or gates")
    for field_name in (
        "execution_allowed",
        "write_allowed",
        "dispatch_allowed",
        "live_effect_allowed",
    ):
        if receipt.get(field_name) is not False:
            raise CodingRunnerStateError(
                f"memory checkpoint receipt {field_name} must be false"
            )
    if receipt.get("raw_content_visible") is not False:
        raise CodingRunnerStateError("memory checkpoint receipt cannot expose raw content")
    planning = receipt.get("planning")
    if not isinstance(planning, Mapping) or (
        planning.get("planning_item_id") != current.planning_item_id
        or planning.get("canonical_plan_revision") != current.canonical_plan_revision
        or planning.get("binding_digest") != current.planning_binding_digest
    ):
        raise CodingRunnerStateError("memory checkpoint receipt does not match current Planning binding")
    if planning.get("acceptance_contract") != current.planning_acceptance_contract:
        raise CodingRunnerStateError("memory checkpoint receipt acceptance contract does not match Planning")
    allowed_paths_digest = _canonical_sha256(
        planning.get("allowed_paths_digest"), field="planning.allowed_paths_digest"
    )
    if allowed_paths_digest != stable_payload_hash(current.planning_allowed_paths):
        raise CodingRunnerStateError("memory checkpoint receipt allowed paths do not match Planning")
    raw_gate_requirements = planning.get("gate_requirements")
    if (
        not isinstance(raw_gate_requirements, (tuple, list))
        or tuple(raw_gate_requirements) != current.planning_gate_requirements
    ):
        raise CodingRunnerStateError("memory checkpoint receipt gate requirements do not match Planning")
    checkpoint = str(receipt.get("checkpoint") or "")
    if checkpoint not in {
        "planning_intake",
        "pre_edit",
        "failure_retrieval",
        "post_acceptance_writeback",
    }:
        raise CodingRunnerStateError("memory checkpoint receipt checkpoint is invalid")
    scope_key = (
        "normalized_allowed_scope"
        if checkpoint == "planning_intake"
        else "normalized_claim_scope"
    )
    scope_digest = _canonical_sha256(receipt.get("scope_digest"), field="scope_digest")
    if scope_digest != stable_payload_hash({scope_key: current.planning_allowed_paths}):
        raise CodingRunnerStateError("memory checkpoint receipt scope does not match Planning")
    current_revision_binding = _canonical_sha256(
        expected_revision_binding, field="expected_revision_binding"
    )
    receipt_revision_binding = _canonical_sha256(
        receipt.get("revision_binding"), field="revision_binding"
    )
    if receipt_revision_binding != current_revision_binding:
        raise CodingRunnerStateError("memory checkpoint receipt revision binding is stale")
    raw_receipt_id = receipt.get("receipt_id")
    if (
        not isinstance(raw_receipt_id, str)
        or raw_receipt_id != raw_receipt_id.strip().lower()
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", raw_receipt_id)
    ):
        raise CodingRunnerStateError("memory checkpoint receipt_id must be canonical SHA-256")
    receipt_id = raw_receipt_id
    canonical_payload = dict(receipt)
    canonical_payload.pop("receipt_id", None)
    _reject_unsafe_payload(canonical_payload)
    try:
        json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise CodingRunnerStateError(
            "memory checkpoint receipt payload must be canonical JSON"
        ) from exc
    if stable_payload_hash(canonical_payload) != receipt_id:
        raise CodingRunnerStateError("memory checkpoint receipt_id does not match canonical payload")
    receipt_ids = _checkpoint_receipt_ids((*current.memory_checkpoint_receipt_ids, receipt_id))
    if receipt_ids == current.memory_checkpoint_receipt_ids:
        return current
    return store.write(
        CodingRunnerState.create(
            task_id=current.task_id,
            repo_id=current.repo_id,
            phase=current.phase,
            progress_percent=current.progress_percent,
            gates_waiting=current.gates_waiting,
            blockers=current.blockers,
            next_human_decision=current.next_human_decision,
            event_count=current.event_count + 1,
            planning_binding=current.planning_binding_dict(),
            memory_checkpoint_receipt_ids=receipt_ids,
        )
    )


def _gates_from_blockers(blockers: Iterable[Any]) -> tuple[str, ...]:
    gates: list[str] = []
    for blocker in blockers:
        text = str(blocker or "").lower()
        if "operator" in text:
            gates.append("operator_go")
        elif "live" in text:
            gates.append("live_enable")
        elif "branch" in text or "worktree" in text:
            gates.append("repo_branch_permission")
        else:
            gates.append("runner_review")
    return tuple(dict.fromkeys(gates))


def _coerce_planning_binding(
    value: CodingPlanningBinding | Mapping[str, Any] | None,
) -> CodingPlanningBinding | None:
    if value is None:
        return None
    try:
        return CodingPlanningBinding.from_value(value)
    except CodingProjectScopeError as exc:
        raise CodingRunnerStateError(f"Planning binding is invalid: {exc}") from exc


def _canonical_sha256(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip().lower()
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
    ):
        raise CodingRunnerStateError(f"{field} must be canonical lowercase SHA-256")
    return value


def _planning_binding_from_plan(plan: Any) -> tuple[CodingPlanningBinding | None, str]:
    raw = getattr(plan, "planning_binding", None)
    if raw is None:
        values = {
            "status": getattr(plan, "planning_status", ""),
            "planning_item_id": getattr(plan, "planning_item_id", ""),
            "canonical_plan_revision": getattr(plan, "canonical_plan_revision", ""),
            "planning_revision": getattr(plan, "planning_revision", ""),
            "acceptance_contract": getattr(plan, "acceptance_contract", ""),
            "acceptance_criteria_id": getattr(plan, "acceptance_criteria_id", ""),
            "allowed_paths": getattr(plan, "allowed_paths", ()),
            "gate_requirements": getattr(plan, "gate_requirements", ()),
        }
        if not any(values[key] for key in ("planning_item_id", "canonical_plan_revision", "planning_revision")):
            return None, "validated Planning item and revision binding is missing"
        raw = values
    try:
        return CodingPlanningBinding.from_value(raw), ""
    except CodingProjectScopeError as exc:
        return None, f"Planning binding is invalid: {exc}"


def _checkpoint_receipt_ids(values: Iterable[Any]) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(_safe_label(value, "memory_checkpoint_receipt_id") for value in values))
    if len(ids) > 32:
        raise CodingRunnerStateError("memory checkpoint receipt count exceeds 32")
    return ids


def _dispatch_quality_gate(dispatch: Any) -> dict[str, Any]:
    quality = getattr(dispatch, "quality_gate", None)
    if isinstance(quality, Mapping):
        return dict(quality)
    if isinstance(dispatch, Mapping):
        raw = dispatch.get("quality_gate")
        if isinstance(raw, Mapping):
            return dict(raw)
    return {}


def _sandbox_dispatch_blockers(dispatch: Any, quality_gate: Mapping[str, Any]) -> tuple[str, ...]:
    blockers: list[str] = []
    for blocker in quality_gate.get("blockers") or ():
        blockers.append(_safe_summary(blocker, "sandbox_blocker"))
    statuses = getattr(dispatch, "statuses", None)
    if statuses is None and isinstance(dispatch, Mapping):
        statuses = dispatch.get("statuses") or ()
    for status in statuses or ():
        payload = status.to_dict() if hasattr(status, "to_dict") else dict(status or {})
        status_text = str(payload.get("status") or "unknown").lower()
        if status_text not in {"succeeded", "dry_run"}:
            job_id = _safe_label(payload.get("job_id") or "sandbox_job", "sandbox_job")
            blockers.append(f"sandbox job {job_id} status {status_text}")
    return tuple(dict.fromkeys(blockers or ("sandbox checks failed",)))


def _resume_phase_for_progress(progress_percent: int) -> str:
    progress = _progress(progress_percent)
    if progress >= 65:
        return "review_ready"
    if progress >= 35:
        return "worktree_ready"
    if progress >= 20:
        return "scoped"
    return "planned"


def _phase(value: Any) -> str:
    text = str(value or "planned").strip().lower().replace("-", "_")
    if text not in RUNNER_PHASES:
        raise CodingRunnerStateError(f"unsupported runner phase: {value!r}")
    return text


def _safe_label(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CodingRunnerStateError(f"{field} must not be empty")
    if _SECRET_RE.search(text):
        raise CodingRunnerStateError(f"{field} appears to contain secret material")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise CodingRunnerStateError(f"{field} must not contain host paths")
    if not _SAFE_LABEL_RE.fullmatch(text):
        text = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text[:180]


def _safe_summary(value: Any, field: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if _SECRET_RE.search(text):
        raise CodingRunnerStateError(f"{field} appears to contain secret material")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise CodingRunnerStateError(f"{field} must not contain host paths")
    return text[:240]


def _progress(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, 100))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).lower()
    if _SECRET_RE.search(encoded):
        raise CodingRunnerStateError("runner state payload contains secret material")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise CodingRunnerStateError("runner state payload contains host paths")
