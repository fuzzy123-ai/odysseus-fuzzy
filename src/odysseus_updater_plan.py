"""Offline update plan model for the Odysseus updater module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_DECISIONS = ("go", "partial", "no_go", "deferred")
_RISK_LEVELS = ("low", "medium", "high", "critical")
_GATE_STATUSES = ("pass", "fail", "pending", "waived")
_DEFAULT_REQUIRED_GATE_IDS = (
    "scope_confirmed",
    "offline_slice_confirmed",
    "tests_defined",
)
_DEFAULT_OPTIONAL_GATE_IDS = (
    "operator_handoff_ready",
    "audit_report_ready",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_slug(value: Any, *, field_name: str) -> str:
    text = (
        _normalize_text(value, field_name=field_name)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return "_".join(part for part in text.split("_") if part)


def _normalize_risk_level(value: Any) -> str:
    risk_level = _normalize_slug(value, field_name="risk_level")
    if risk_level not in _RISK_LEVELS:
        raise ValueError(f"unsupported risk_level: {value!r}")
    return risk_level


def _normalize_decision(value: Any) -> str:
    decision = _normalize_slug(value, field_name="decision")
    if decision not in _DECISIONS:
        raise ValueError(f"unsupported decision: {value!r}")
    return decision


def _normalize_gate_status(value: Any) -> str:
    status = _normalize_slug(value, field_name="status")
    if status not in _GATE_STATUSES:
        raise ValueError(f"unsupported gate status: {value!r}")
    return status


def _normalize_unique_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        normalized = _normalize_text(value, field_name=field_name)
        if normalized not in items:
            items.append(normalized)
    return tuple(items)


def _normalize_string_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        normalized = _normalize_text(value, field_name=field_name)
        items.append(normalized)
    if not items:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(items)


def _derive_command_plan_id(command: Mapping[str, Any], index: int) -> str:
    raw_id = command.get("command_plan_id")
    if raw_id:
        return _normalize_slug(raw_id, field_name="command_plan_id")
    label_source = command.get("label") or command.get("summary") or command.get("argv", ("plan",))
    if isinstance(label_source, (list, tuple)):
        first_part = label_source[0] if label_source else "plan"
    else:
        first_part = label_source
    slug = _normalize_slug(first_part, field_name="command_plan_id") or "plan"
    return f"cmd_{index:02d}_{slug}"


@dataclass(frozen=True, slots=True)
class UpdatePlanGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "UpdatePlanGate":
        return cls(
            gate_id=_normalize_slug(gate_id, field_name="gate_id"),
            status=_normalize_gate_status(status),
            summary=_normalize_text(summary, field_name="summary"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class UpdatePlanCommand:
    command_plan_id: str
    argv: tuple[str, ...]
    summary: str

    @classmethod
    def create(
        cls,
        command: Mapping[str, Any],
        *,
        index: int,
    ) -> "UpdatePlanCommand":
        return cls(
            command_plan_id=_derive_command_plan_id(command, index),
            argv=_normalize_string_tuple(command.get("argv", ()), field_name="argv"),
            summary=_normalize_text(
                command.get("summary") or command.get("label") or "planned command",
                field_name="summary",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_plan_id": self.command_plan_id,
            "argv": list(self.argv),
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    source_ref: str
    current_ref: str
    target_ref: str
    reason: str
    risk_level: str
    decision: str
    required_gates: tuple[UpdatePlanGate, ...]
    optional_gates: tuple[UpdatePlanGate, ...]
    planned_commands: tuple[UpdatePlanCommand, ...]
    command_plan_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "current_ref": self.current_ref,
            "target_ref": self.target_ref,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "decision": self.decision,
            "required_gates": [gate.to_dict() for gate in self.required_gates],
            "optional_gates": [gate.to_dict() for gate in self.optional_gates],
            "planned_commands": [command.to_dict() for command in self.planned_commands],
            "command_plan_ids": list(self.command_plan_ids),
        }

    def to_compact_report(self) -> dict[str, Any]:
        return {
            "refs": {
                "source": self.source_ref,
                "current": self.current_ref,
                "target": self.target_ref,
            },
            "decision": self.decision,
            "risk_level": self.risk_level,
            "required_gate_statuses": {
                gate.gate_id: gate.status for gate in self.required_gates
            },
            "optional_gate_statuses": {
                gate.gate_id: gate.status for gate in self.optional_gates
            },
            "command_plan_ids": list(self.command_plan_ids),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Odysseus Updater Plan",
            "",
            f"- Source Ref: `{self.source_ref}`",
            f"- Current Ref: `{self.current_ref}`",
            f"- Target Ref: `{self.target_ref}`",
            f"- Decision: `{self.decision}`",
            f"- Risk Level: `{self.risk_level}`",
            f"- Reason: {self.reason}",
            f"- Command Plan IDs: {', '.join(f'`{item}`' for item in self.command_plan_ids)}",
            "",
            "## Required Gates",
        ]
        for gate in self.required_gates:
            lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        if self.optional_gates:
            lines.extend(["", "## Optional Gates"])
            for gate in self.optional_gates:
                lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        if self.planned_commands:
            lines.extend(["", "## Planned Commands"])
            for command in self.planned_commands:
                lines.append(
                    f"- `{command.command_plan_id}`: {' '.join(command.argv)}"
                    f" - {command.summary}"
                )
        return "\n".join(lines).rstrip()


def _build_gate_tuple(
    raw_gates: Iterable[Mapping[str, Any]],
    *,
    fallback_gate_ids: tuple[str, ...],
    required: bool,
) -> tuple[UpdatePlanGate, ...]:
    gates: list[UpdatePlanGate] = []
    seen_ids: set[str] = set()
    for index, gate in enumerate(raw_gates):
        gate_id = gate.get("gate_id") or (
            fallback_gate_ids[index] if index < len(fallback_gate_ids) else f"gate_{index + 1:02d}"
        )
        gate_model = UpdatePlanGate.create(
            gate_id=gate_id,
            status=gate.get("status", "pending"),
            summary=gate.get("summary") or (
                "required updater gate"
                if required
                else "optional updater gate"
            ),
        )
        if gate_model.gate_id in seen_ids:
            raise ValueError(f"duplicate gate_id: {gate_model.gate_id}")
        seen_ids.add(gate_model.gate_id)
        gates.append(gate_model)
    if required and not gates:
        raise ValueError("required_gates must not be empty")
    return tuple(sorted(gates, key=lambda item: item.gate_id))


def _build_command_tuple(commands: Iterable[Mapping[str, Any]]) -> tuple[UpdatePlanCommand, ...]:
    planned_commands: list[UpdatePlanCommand] = []
    seen_ids: set[str] = set()
    for index, command in enumerate(commands, start=1):
        planned_command = UpdatePlanCommand.create(command, index=index)
        if planned_command.command_plan_id in seen_ids:
            raise ValueError(f"duplicate command_plan_id: {planned_command.command_plan_id}")
        seen_ids.add(planned_command.command_plan_id)
        planned_commands.append(planned_command)
    if not planned_commands:
        raise ValueError("planned_commands must not be empty")
    return tuple(planned_commands)


def _derive_decision(
    *,
    required_gates: tuple[UpdatePlanGate, ...],
    optional_gates: tuple[UpdatePlanGate, ...],
) -> str:
    required_statuses = {gate.status for gate in required_gates}
    optional_statuses = {gate.status for gate in optional_gates}
    if "fail" in required_statuses:
        return "no_go"
    if "pending" in required_statuses:
        return "deferred"
    if required_statuses.issubset({"pass", "waived"}) and optional_statuses.issubset({"pass", "waived"}):
        return "go"
    if required_statuses.issubset({"pass", "waived"}):
        return "partial"
    return "deferred"


def build_odysseus_updater_plan(
    *,
    source_ref: Any,
    current_ref: Any,
    target_ref: Any,
    reason: Any,
    risk_level: Any,
    required_gates: Iterable[Mapping[str, Any]],
    optional_gates: Iterable[Mapping[str, Any]] = (),
    planned_commands: Iterable[Mapping[str, Any]] = (),
) -> UpdatePlan:
    required_gate_models = _build_gate_tuple(
        required_gates,
        fallback_gate_ids=_DEFAULT_REQUIRED_GATE_IDS,
        required=True,
    )
    optional_gate_models = _build_gate_tuple(
        optional_gates,
        fallback_gate_ids=_DEFAULT_OPTIONAL_GATE_IDS,
        required=False,
    )
    command_models = _build_command_tuple(planned_commands)
    command_plan_ids = _normalize_unique_tuple(
        (command.command_plan_id for command in command_models),
        field_name="command_plan_id",
    )
    return UpdatePlan(
        source_ref=_normalize_text(source_ref, field_name="source_ref"),
        current_ref=_normalize_text(current_ref, field_name="current_ref"),
        target_ref=_normalize_text(target_ref, field_name="target_ref"),
        reason=_normalize_text(reason, field_name="reason"),
        risk_level=_normalize_risk_level(risk_level),
        decision=_normalize_decision(
            _derive_decision(
                required_gates=required_gate_models,
                optional_gates=optional_gate_models,
            )
        ),
        required_gates=required_gate_models,
        optional_gates=optional_gate_models,
        planned_commands=command_models,
        command_plan_ids=command_plan_ids,
    )
