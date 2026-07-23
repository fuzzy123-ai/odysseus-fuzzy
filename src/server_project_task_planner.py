"""Adapter from AI project planner output to executable project tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.server_project_registry import ServerProjectRecord
from src.server_project_task_runner import (
    ProjectTaskCheck,
    ProjectTaskCommandRunner,
    ProjectTaskFileWrite,
    ProjectTaskPlan,
    ProjectTaskReport,
    build_project_task_plan,
    run_project_task,
)


_CHECK_PROFILES = ("auto", "python", "node", "generic", "none")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")


class ServerProjectTaskPlannerError(ValueError):
    """Raised when planner output cannot safely become a task runner input."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectTaskPlannerError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectTaskPlannerError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectTaskPlannerError(f"{field_name} appears to contain secret material")
    return text


def _normalize_profile(value: Any) -> str:
    profile = _normalize_text(value, field_name="check_profile").lower().replace("-", "_")
    if profile not in _CHECK_PROFILES:
        raise ServerProjectTaskPlannerError(f"unsupported check_profile: {value!r}")
    return profile


def _normalize_string_list(values: Iterable[Any], *, field_name: str, max_len: int = 220) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _normalize_text(value, field_name=field_name, max_len=max_len)
        if text not in result:
            result.append(text)
    return tuple(result)


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ServerProjectTaskPlannerError(f"{field_name} must be an object")
    return value


def _file_write_from_planner(value: Mapping[str, Any] | ProjectTaskFileWrite) -> ProjectTaskFileWrite:
    if isinstance(value, ProjectTaskFileWrite):
        return value
    payload = _mapping(value, field_name="file_write")
    return ProjectTaskFileWrite.create(
        path=payload.get("path"),
        content=payload.get("content", ""),
    )


def _check_from_planner(value: Mapping[str, Any] | ProjectTaskCheck) -> ProjectTaskCheck:
    if isinstance(value, ProjectTaskCheck):
        return value
    payload = _mapping(value, field_name="check")
    return ProjectTaskCheck.create(
        argv=payload.get("argv", ()),
        timeout_seconds=int(payload.get("timeout_seconds", 300)),
    )


def _dedupe_checks(checks: Iterable[ProjectTaskCheck]) -> tuple[ProjectTaskCheck, ...]:
    result: list[ProjectTaskCheck] = []
    seen: set[tuple[str, ...]] = set()
    for check in checks:
        if check.argv not in seen:
            seen.add(check.argv)
            result.append(check)
    return tuple(result)


def _default_checks(*, file_writes: tuple[ProjectTaskFileWrite, ...], check_profile: str) -> tuple[ProjectTaskCheck, ...]:
    if check_profile == "none":
        return ()
    paths = tuple(write.path for write in file_writes)
    checks: list[ProjectTaskCheck] = []
    if check_profile in {"auto", "python"} and any(path.endswith(".py") for path in paths):
        checks.append(ProjectTaskCheck.create(argv=("python", "-m", "pytest", "tests", "-q")))
    if check_profile in {"auto", "node"}:
        for path in paths:
            if path.endswith(".js"):
                checks.append(ProjectTaskCheck.create(argv=("node", "--check", path)))
    if check_profile in {"auto", "generic"} or not checks:
        checks.append(ProjectTaskCheck.create(argv=("git", "status", "--short", "--branch")))
    return _dedupe_checks(checks)


@dataclass(frozen=True, slots=True)
class PlannerTaskBundle:
    project_slug: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    check_profile: str
    task_plan: ProjectTaskPlan
    planner_blockers: tuple[str, ...]
    next_human_decision: str

    @property
    def ready_for_task_runner(self) -> bool:
        return not self.planner_blockers and bool(self.task_plan.file_writes) and bool(self.task_plan.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "check_profile": self.check_profile,
            "ready_for_task_runner": self.ready_for_task_runner,
            "task_plan": self.task_plan.to_dict(),
            "planner_blockers": list(self.planner_blockers),
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class PlannerTaskRunReport:
    bundle: PlannerTaskBundle
    task_report: ProjectTaskReport | None

    @property
    def executed(self) -> bool:
        return self.task_report.executed if self.task_report else False

    @property
    def status(self) -> str:
        if self.task_report:
            return self.task_report.status
        return "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "planner": self.bundle.to_dict(),
            "task_run": self.task_report.to_dict() if self.task_report else None,
        }


def build_planner_task_bundle(
    *,
    record: ServerProjectRecord,
    objective: Any,
    file_writes: Iterable[Mapping[str, Any] | ProjectTaskFileWrite],
    checks: Iterable[Mapping[str, Any] | ProjectTaskCheck] = (),
    acceptance_criteria: Iterable[Any] = (),
    check_profile: Any = "auto",
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    clarification_ready_for_plan: bool = True,
    clarification_id: Any = "",
) -> PlannerTaskBundle:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectTaskPlannerError("record must be a ServerProjectRecord")
    normalized_objective = _normalize_text(objective, field_name="objective", max_len=500)
    normalized_profile = _normalize_profile(check_profile)
    normalized_writes = tuple(_file_write_from_planner(item) for item in file_writes)
    explicit_checks = tuple(_check_from_planner(item) for item in checks)
    selected_checks = explicit_checks or _default_checks(
        file_writes=normalized_writes,
        check_profile=normalized_profile,
    )
    criteria = _normalize_string_list(acceptance_criteria, field_name="acceptance_criteria", max_len=260)

    planner_blockers: list[str] = []
    if not bool(clarification_ready_for_plan):
        suffix = f" ({_normalize_text(clarification_id, field_name='clarification_id', allow_empty=True, max_len=120)})" if clarification_id else ""
        planner_blockers.append(f"clarification must be ready_for_plan before planner task execution{suffix}")
    if not normalized_writes:
        planner_blockers.append("planner output must include at least one file write")
    if not selected_checks:
        planner_blockers.append("planner output must include checks or a default check profile")
    if not criteria:
        planner_blockers.append("planner output should include acceptance criteria")

    task_plan = build_project_task_plan(
        record=record,
        objective=normalized_objective,
        file_writes=normalized_writes,
        checks=selected_checks,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
    )
    return PlannerTaskBundle(
        project_slug=record.project_slug,
        objective=normalized_objective,
        acceptance_criteria=criteria,
        check_profile=normalized_profile,
        task_plan=task_plan,
        planner_blockers=tuple(planner_blockers),
        next_human_decision=_next_decision(planner_blockers, task_plan),
    )


def run_planner_task(
    *,
    record: ServerProjectRecord,
    projects_root: str,
    objective: Any,
    file_writes: Iterable[Mapping[str, Any] | ProjectTaskFileWrite],
    checks: Iterable[Mapping[str, Any] | ProjectTaskCheck] = (),
    acceptance_criteria: Iterable[Any] = (),
    check_profile: Any = "auto",
    live_enabled: bool | None = None,
    operator_decision: Any = "missing",
    clarification_ready_for_plan: bool = True,
    clarification_id: Any = "",
    command_runner: ProjectTaskCommandRunner | None = None,
) -> PlannerTaskRunReport:
    bundle = build_planner_task_bundle(
        record=record,
        objective=objective,
        file_writes=file_writes,
        checks=checks,
        acceptance_criteria=acceptance_criteria,
        check_profile=check_profile,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
        clarification_ready_for_plan=clarification_ready_for_plan,
        clarification_id=clarification_id,
    )
    if bundle.planner_blockers:
        return PlannerTaskRunReport(bundle=bundle, task_report=None)
    task_report = run_project_task(
        record=record,
        projects_root=projects_root,
        objective=bundle.task_plan.objective,
        file_writes=bundle.task_plan.file_writes,
        checks=bundle.task_plan.checks,
        live_enabled=live_enabled,
        operator_decision=operator_decision,
        command_runner=command_runner,
    )
    return PlannerTaskRunReport(bundle=bundle, task_report=task_report)


def _next_decision(blockers: list[str], task_plan: ProjectTaskPlan) -> str:
    if blockers:
        return "Planner must provide file writes, checks or check profile, and acceptance criteria before execution."
    if task_plan.can_execute:
        return "Planner task can be handed to the project task runner; commit, push and deploy remain separate gates."
    return task_plan.next_human_decision
