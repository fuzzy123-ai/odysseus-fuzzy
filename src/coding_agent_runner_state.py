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
    ) -> "CodingRunnerState":
        normalized_phase = _phase(phase)
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
        )

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
        return CodingRunnerState.create(
            task_id=payload.get("task_id", ""),
            repo_id=payload.get("repo_id", ""),
            phase=payload.get("phase", "planned"),
            progress_percent=payload.get("progress_percent", 0),
            gates_waiting=payload.get("gates_waiting") or (),
            blockers=payload.get("blockers") or (),
            next_human_decision=payload.get("next_human_decision", ""),
            event_count=payload.get("event_count", 1),
        )

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
        if getattr(plan, "decision", "") == "plan_ready":
            state = CodingRunnerState.create(
                task_id=task_id,
                repo_id=repo_id,
                phase="scoped",
                progress_percent=20,
                next_human_decision=next_human_decision,
            )
        else:
            state = CodingRunnerState.create(
                task_id=task_id,
                repo_id=repo_id,
                phase="blocked",
                progress_percent=0,
                gates_waiting=_gates_from_blockers(blockers),
                blockers=blockers,
                next_human_decision=next_human_decision,
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
    raw_status = clarification_run.get("status")
    status = raw_status if type(raw_status) is str else ""
    ready = clarification_run.get("ready_for_plan") is True
    raw_unresolved = clarification_run.get("unresolved_required_count")
    unresolved = _progress(raw_unresolved if type(raw_unresolved) is int else 0)
    unresolved_is_zero = type(raw_unresolved) is int and raw_unresolved == 0
    clarification_id = _safe_label(
        clarification_run.get("clarification_id") or "clarification",
        "clarification_id",
    )
    inconsistent_readiness = (
        (status == "ready_for_plan") != ready
        or (
            status in {"ready_for_plan", "understanding_review"}
            and not unresolved_is_zero
        )
    )
    if ready and status == "ready_for_plan" and unresolved_is_zero:
        phase = "ready_for_plan"
        progress = 15
        gates = ("create_plan",)
        blockers: tuple[str, ...] = ()
        decision = "Clarification is ready for plan; create or approve the coding plan next."
    elif status == "understanding_review" and unresolved_is_zero and not ready:
        phase = "understanding_review"
        progress = 10
        gates = ("confirm_understanding",)
        blockers = ()
        decision = "Review and confirm the understanding summary before creating a coding plan."
    else:
        phase = "clarifying"
        progress = 5
        gates = ("clarification_required",)
        blocker = (
            f"clarification {clarification_id} has inconsistent readiness state"
            if inconsistent_readiness
            else f"clarification {clarification_id} has {unresolved} unresolved required question(s)"
        )
        blockers = (blocker,)
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
