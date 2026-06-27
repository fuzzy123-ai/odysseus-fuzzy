"""Project quality-gate planning for universal server projects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from src.live_quality_gate_command_runner import (
    LiveQualityGateCommandPlan,
    build_live_quality_gate_command_plan,
)
from src.server_project_registry import ServerProjectRecord


_GATE_TYPES = ("test", "build", "smoke", "evidence")
_DECISIONS = ("plan_ready", "hold", "blocked")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]?\s*\S*")
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|(?<![A-Za-z0-9._-])/(?:[^\s/`]+/)+)")
_BLOCKED_TEXT = (
    "git reset",
    "git clean",
    "rm -rf",
    "remove-item -recurse",
    "curl ",
    "wget ",
    "invoke-webrequest",
    "systemctl",
    "podman",
    "docker",
    "ssh ",
    "scp ",
    "gh repo create",
)


class ServerProjectQualityGateError(ValueError):
    """Raised when a project quality gate cannot be safely planned."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 260) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectQualityGateError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectQualityGateError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectQualityGateError(f"{field_name} appears to contain secret material")
    if _ABS_PATH_RE.search(text):
        raise ServerProjectQualityGateError(f"{field_name} must not contain host-local absolute paths")
    return text


def _normalize_gate_type(value: Any) -> str:
    gate_type = _normalize_text(value, field_name="gate_type").lower().replace("-", "_")
    if gate_type not in _GATE_TYPES:
        raise ServerProjectQualityGateError(f"unsupported gate_type: {value!r}")
    return gate_type


def _normalize_bool(value: Any) -> bool:
    return bool(value)


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except Exception as exc:
        raise ServerProjectQualityGateError("timeout_seconds must be an integer") from exc
    if timeout < 1 or timeout > 1800:
        raise ServerProjectQualityGateError("timeout_seconds must be between 1 and 1800")
    return timeout


def _is_blocked_command(command_text: str) -> bool:
    lowered = command_text.lower()
    return any(fragment in lowered for fragment in _BLOCKED_TEXT)


def _command_class_for_gate(gate_type: str, command_text: str) -> str:
    lowered = command_text.lower()
    if _is_blocked_command(command_text):
        if any(fragment in lowered for fragment in ("curl ", "wget ", "invoke-webrequest", "ssh ", "scp ")):
            return "blocked_network"
        if any(fragment in lowered for fragment in ("podman", "docker", "systemctl")):
            return "blocked_host_command"
        return "blocked_destructive"
    if gate_type in {"test", "smoke"}:
        if not lowered.startswith("python -m pytest "):
            return "blocked_host_command"
        return "focused_pytest"
    return "evidence_check"


def _default_specs(record: ServerProjectRecord) -> tuple["ProjectQualityGateSpec", ...]:
    slug = record.project_slug
    return (
        ProjectQualityGateSpec.create(
            gate_id="focused_tests",
            gate_type="test",
            command_text=f"python -m pytest tests/test_{slug.replace('-', '_')}.py -q",
            timeout_seconds=300,
            required=True,
        ),
        ProjectQualityGateSpec.create(
            gate_id="build_evidence",
            gate_type="build",
            command_text=f"evidence: build artifact for {slug} is recorded by the project runner",
            timeout_seconds=60,
            required=True,
        ),
        ProjectQualityGateSpec.create(
            gate_id="smoke_tests",
            gate_type="smoke",
            command_text=f"python -m pytest tests/test_{slug.replace('-', '_')}_smoke.py -q",
            timeout_seconds=300,
            required=True,
        ),
    )


@dataclass(frozen=True, slots=True)
class ProjectQualityGateSpec:
    gate_id: str
    gate_type: str
    command_text: str
    timeout_seconds: int
    required: bool

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        gate_type: Any,
        command_text: Any,
        timeout_seconds: Any = 300,
        required: Any = True,
    ) -> "ProjectQualityGateSpec":
        normalized_type = _normalize_gate_type(gate_type)
        normalized_command = _normalize_text(command_text, field_name="command_text")
        return cls(
            gate_id=_normalize_text(gate_id, field_name="gate_id", max_len=80).lower().replace("-", "_"),
            gate_type=normalized_type,
            command_text=normalized_command,
            timeout_seconds=_normalize_timeout(timeout_seconds),
            required=_normalize_bool(required),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "gate_type": self.gate_type,
            "command_text": self.command_text,
            "timeout_seconds": self.timeout_seconds,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class ProjectQualityGateResult:
    spec: ProjectQualityGateSpec
    command_plan: LiveQualityGateCommandPlan

    @property
    def decision(self) -> str:
        return self.command_plan.decision.decision

    @property
    def ready(self) -> bool:
        return self.decision == "plan_ready"

    @property
    def blocker(self) -> str:
        if self.ready:
            return ""
        return f"{self.spec.gate_id}: {self.command_plan.decision.next_action}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "decision": self.decision,
            "ready": self.ready,
            "blocker": self.blocker,
            "command_plan": self.command_plan.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProjectQualityGateBundle:
    project_slug: str
    chat_scope: str
    decision: str
    required_gate_count: int
    ready_gate_count: int
    blockers: tuple[str, ...]
    results: tuple[ProjectQualityGateResult, ...]
    next_human_decision: str

    @property
    def deploy_gate_ready(self) -> bool:
        return self.decision == "plan_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "chat_scope": self.chat_scope,
            "decision": self.decision,
            "deploy_gate_ready": self.deploy_gate_ready,
            "required_gate_count": self.required_gate_count,
            "ready_gate_count": self.ready_gate_count,
            "blockers": list(self.blockers),
            "results": [result.to_dict() for result in self.results],
            "next_human_decision": self.next_human_decision,
        }


def build_project_quality_gate_bundle(
    *,
    record: ServerProjectRecord,
    gate_specs: Iterable[ProjectQualityGateSpec | dict[str, Any]] | None = None,
) -> ProjectQualityGateBundle:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectQualityGateError("record must be a ServerProjectRecord")
    specs = _coerce_specs(record, gate_specs)
    results = tuple(_build_result(spec) for spec in specs)
    required_results = tuple(result for result in results if result.spec.required)
    blockers = tuple(result.blocker for result in required_results if result.blocker)
    if any(result.decision == "blocked" for result in required_results):
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"
    next_human_decision = (
        "Quality gates are ready for operator review; keep execution separate from this dry-run bundle."
        if decision == "plan_ready"
        else "Replace blocked or incomplete project gates with focused, bounded, redacted checks."
    )
    return ProjectQualityGateBundle(
        project_slug=record.project_slug,
        chat_scope=record.chat_scope,
        decision=decision,
        required_gate_count=len(required_results),
        ready_gate_count=sum(1 for result in required_results if result.ready),
        blockers=blockers,
        results=results,
        next_human_decision=next_human_decision,
    )


def _coerce_specs(
    record: ServerProjectRecord,
    gate_specs: Iterable[ProjectQualityGateSpec | dict[str, Any]] | None,
) -> tuple[ProjectQualityGateSpec, ...]:
    if gate_specs is None:
        return _default_specs(record)
    specs: list[ProjectQualityGateSpec] = []
    for raw in gate_specs:
        if isinstance(raw, ProjectQualityGateSpec):
            specs.append(raw)
        elif isinstance(raw, dict):
            specs.append(ProjectQualityGateSpec.create(**raw))
        else:
            raise ServerProjectQualityGateError("gate_specs must contain ProjectQualityGateSpec objects or dicts")
    if not specs:
        raise ServerProjectQualityGateError("at least one quality gate is required")
    return tuple(specs)


def _build_result(spec: ProjectQualityGateSpec) -> ProjectQualityGateResult:
    command_class = _command_class_for_gate(spec.gate_type, spec.command_text)
    command_plan = build_live_quality_gate_command_plan(
        command_class=command_class,
        command_text=spec.command_text,
        timeout_seconds=spec.timeout_seconds,
        redacted_log_policy="project-gate-command-only-no-secrets",
        operator_approval_required=True,
    )
    return ProjectQualityGateResult(spec=spec, command_plan=command_plan)
