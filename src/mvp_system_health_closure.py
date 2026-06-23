"""MVP Roadmap 4 system health host-agent progress model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_STATUSES = ("go", "repo_open", "needs_operator_input", "needs_live_go", "needs_design", "blocked", "deferred")
_SLICE_CLASSES = ("safe_offline", "repo_only", "needs_live_go", "needs_design", "blocked")


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported system health closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported system health closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class SystemHealthClosureGate:
    gate_id: str
    title: str
    status: str
    slice_class: str
    reason: str

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        title: Any,
        status: Any,
        slice_class: Any,
        reason: Any,
    ) -> "SystemHealthClosureGate":
        return cls(
            gate_id=_normalize_text(gate_id, field_name="gate_id").strip().lower(),
            title=_normalize_text(title, field_name="title"),
            status=_normalize_status(status),
            slice_class=_normalize_slice_class(slice_class),
            reason=_normalize_text(reason, field_name="reason"),
        )

    @property
    def complete(self) -> bool:
        return self.status == "go"

    def to_dict(self) -> dict[str, str]:
        return {
            "gate_id": self.gate_id,
            "title": self.title,
            "status": self.status,
            "slice_class": self.slice_class,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SystemHealthClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[SystemHealthClosureGate, ...]
    percent_complete: int
    why_not_100: str
    recommended_next_human_decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "roadmap_id": self.roadmap_id,
            "title": self.title,
            "percent_complete": self.percent_complete,
            "why_not_100": self.why_not_100,
            "recommended_next_human_decision": self.recommended_next_human_decision,
            "gates": tuple(gate.to_dict() for gate in self.gates),
        }

    def to_markdown_row(self) -> str:
        reason = "-" if self.percent_complete == 100 else self.why_not_100
        return f"| 4 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[SystemHealthClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[SystemHealthClosureGate]) -> SystemHealthClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_system_health_closure_report(
    *,
    plugin_foundation_bundle_go: bool = True,
    health_agent_interface_go: bool = True,
    basic_collectors_go: bool = True,
    advanced_collectors_go: bool = True,
    rule_engine_alerting_go: bool = True,
    ops_security_readiness_go: bool = True,
    host_agent_plan_reviewed_go: bool = False,
    local_api_consumer_plan_go: bool = False,
    host_agent_runtime_live_go: bool = False,
    dashboard_and_alert_ui_live_go: bool = False,
) -> SystemHealthClosureReport:
    gates = (
        SystemHealthClosureGate.create(
            gate_id="plugin_foundation_bundle",
            title="Plugin foundation bundle",
            status="go" if plugin_foundation_bundle_go else "blocked",
            slice_class="repo_only",
            reason=(
                "plugin foundation, audit, readiness, review, score, and release summaries exist"
                if plugin_foundation_bundle_go
                else "plugin foundation bundle is missing or blocked"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="health_agent_interface",
            title="Health-agent snapshot interface",
            status="go" if health_agent_interface_go else "blocked",
            slice_class="repo_only",
            reason=(
                "sanitized snapshot interface is modeled without core host commands"
                if health_agent_interface_go
                else "health-agent interface contract is missing or blocked"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="basic_collectors",
            title="Basic collector normalization",
            status="go" if basic_collectors_go else "blocked",
            slice_class="repo_only",
            reason=(
                "basic CPU, memory, disk, load, and uptime collectors degrade safely"
                if basic_collectors_go
                else "basic collector normalization is missing or blocked"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="advanced_collectors",
            title="Advanced collector normalization",
            status="go" if advanced_collectors_go else "blocked",
            slice_class="repo_only",
            reason=(
                "advanced sensor, SMART, update, and reboot collectors expose unsupported/unknown states"
                if advanced_collectors_go
                else "advanced collector normalization is missing or blocked"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="rule_engine_alerting",
            title="Rule engine and alert dedupe",
            status="go" if rule_engine_alerting_go else "blocked",
            slice_class="repo_only",
            reason=(
                "rule engine, alert severity, dedupe, and cooldown behavior are modeled"
                if rule_engine_alerting_go
                else "rule engine or alerting model is missing or blocked"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="ops_security_readiness",
            title="Ops and security readiness",
            status="go" if ops_security_readiness_go else "blocked",
            slice_class="repo_only",
            reason=(
                "ops readiness keeps host access outside core and blocks auto-repair"
                if ops_security_readiness_go
                else "ops/security readiness is missing or blocked"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="host_agent_plan_reviewed",
            title="Host-agent MVP plan reviewed",
            status="go" if host_agent_plan_reviewed_go else "needs_operator_input",
            slice_class="repo_only",
            reason=(
                "host scope, install method, snapshot contract, permissions, rollback, and secrets policy are reviewed"
                if host_agent_plan_reviewed_go
                else "manual host-agent scope, install, permissions, rollback, and secrets review is still required"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="local_api_consumer_plan",
            title="Local API consumer plan",
            status="go" if local_api_consumer_plan_go else "needs_operator_input",
            slice_class="repo_only",
            reason=(
                "local API consumer contract, offline fixture, timeout, and payload policy are reviewed"
                if local_api_consumer_plan_go
                else "manual local API consumer contract, fixture, timeout, and payload review is still required"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="host_agent_runtime_live",
            title="Host-agent runtime live smoke",
            status="go" if host_agent_runtime_live_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "Debian host-agent runtime produced sanitized local snapshots with redacted evidence"
                if host_agent_runtime_live_go
                else "needs explicit operator Go for Debian host-agent install/start/local snapshot smoke"
            ),
        ),
        SystemHealthClosureGate.create(
            gate_id="dashboard_and_alert_ui_live",
            title="Dashboard and alert UI live",
            status="go" if dashboard_and_alert_ui_live_go else "needs_design",
            slice_class="needs_design",
            reason=(
                "operator-facing dashboard and alert controls are live on the redesigned UI"
                if dashboard_and_alert_ui_live_go
                else "dashboard and alert UX are deferred until backend gates are stable and UI is redesigned"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 4 is complete; continue to Telegram Voice Pipeline."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.status == "needs_operator_input":
            next_decision = "Review or defer the host-agent/operator planning gates before claiming System Health runtime closure."
        elif first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer the host-agent live smoke before claiming System Health runtime closure."
        elif first_incomplete.slice_class == "needs_design":
            next_decision = "Keep System Health UI deferred until the backend host-agent path is closed."
        else:
            next_decision = f"Resolve {first_incomplete.title} before System Health closure."
    return SystemHealthClosureReport(
        roadmap_id="system_health_checker_host_agent",
        title="System Health Checker Host-Agent",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )
