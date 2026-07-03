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
    "planned": {"scoped", "blocked", "failed"},
    "scoped": {"worktree_ready", "checks_running", "publish_ready", "blocked", "failed"},
    "worktree_ready": {"checks_running", "review_ready", "blocked", "failed"},
    "checks_running": {"review_ready", "blocked", "failed"},
    "review_ready": {"publish_ready", "blocked", "done", "failed"},
    "publish_ready": {"done", "blocked", "failed"},
    "blocked": {"planned", "scoped", "worktree_ready", "checks_running", "review_ready", "publish_ready", "failed"},
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
