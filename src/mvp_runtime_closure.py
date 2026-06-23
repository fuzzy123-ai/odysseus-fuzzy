"""MVP Roadmap 1 runtime closure progress model.

This module is intentionally offline-only. It records which runtime closure
gates are already supported by repo evidence and which still need explicit
operator/live evidence before Roadmap 1 can be called complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_STATUSES = ("go", "needs_live_go", "needs_design", "blocked", "deferred")
_SLICE_CLASSES = ("safe_offline", "repo_only", "needs_live_go", "needs_design", "blocked")


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported runtime closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported runtime closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class RuntimeClosureGate:
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
    ) -> "RuntimeClosureGate":
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
class RuntimeClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[RuntimeClosureGate, ...]
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
        return f"| 1 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[RuntimeClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[RuntimeClosureGate]) -> RuntimeClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_runtime_closure_report(
    *,
    updater_backend_contract_go: bool = True,
    updater_server_runtime_evidence_go: bool = False,
    updates_backups_live_smoke_go: bool = False,
    mcp_offline_policy_go: bool = True,
    mcp_runtime_plugin_present_go: bool = False,
    mcp_local_route_smoke_go: bool = False,
    telegram_text_offline_boundary_go: bool = True,
    telegram_text_live_roundtrip_go: bool = False,
) -> RuntimeClosureReport:
    """Build the offline progress view for MVP Roadmap 1.

    Defaults reflect the conservative repo-evidence posture: offline contracts
    are present, while live server/MCP/Telegram smoke evidence still needs
    explicit operator approval and redacted evidence.
    """

    gates = (
        RuntimeClosureGate.create(
            gate_id="updater_backend_contract",
            title="Updates and backups backend contract",
            status="go" if updater_backend_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "admin-gated update and backup status/action contract exists"
                if updater_backend_contract_go
                else "backend update/backup contract is missing or blocked"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="updater_server_runtime_evidence",
            title="Updater server runtime evidence",
            status="go" if updater_server_runtime_evidence_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "server runtime reports latest deployed version and clean updater state"
                if updater_server_runtime_evidence_go
                else "needs server-local /api/version and updater/timer evidence"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="updates_backups_live_smoke",
            title="Updates and backups live smoke",
            status="go" if updates_backups_live_smoke_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "Debian live smoke covered status, backup, and gated update actions"
                if updates_backups_live_smoke_go
                else "needs explicit operator Go for Debian status/backup/update smoke evidence"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="mcp_offline_policy",
            title="MCP offline route and policy coverage",
            status="go" if mcp_offline_policy_go else "blocked",
            slice_class="repo_only",
            reason=(
                "MCP route, policy, notification, and owner gates have offline coverage"
                if mcp_offline_policy_go
                else "MCP offline policy coverage is missing or red"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="mcp_runtime_plugin_present",
            title="MCP plugin present in rebuilt runtime",
            status="go" if mcp_runtime_plugin_present_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "rebuilt runtime includes the MCP plugin directory"
                if mcp_runtime_plugin_present_go
                else "needs rebuilt runtime/container evidence that MCP plugin code is present"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="mcp_local_route_smoke",
            title="MCP local route smoke",
            status="go" if mcp_local_route_smoke_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "server-local MCP route smoke passed without remote exposure"
                if mcp_local_route_smoke_go
                else "needs server-local MCP route smoke after runtime rebuild"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="telegram_text_offline_boundary",
            title="Telegram text offline boundary",
            status="go" if telegram_text_offline_boundary_go else "blocked",
            slice_class="repo_only",
            reason=(
                "Telegram text intake, allowlist, bridge, and identifier redaction have offline coverage"
                if telegram_text_offline_boundary_go
                else "Telegram text offline boundary coverage is missing or red"
            ),
        ),
        RuntimeClosureGate.create(
            gate_id="telegram_text_live_roundtrip",
            title="Telegram text live roundtrip",
            status="go" if telegram_text_live_roundtrip_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "one live text roundtrip is recorded with redacted evidence"
                if telegram_text_live_roundtrip_go
                else "needs explicit operator Go for one redacted live Telegram text roundtrip"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 1 is complete; continue to Secure Data Mode Runtime Hooks."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        next_decision = (
            "Grant or defer the next live runtime smoke gate, starting with server-local updater evidence."
            if first_incomplete.slice_class == "needs_live_go"
            else f"Resolve {first_incomplete.title} before live closure."
        )
    return RuntimeClosureReport(
        roadmap_id="runtime_closure_gates",
        title="Runtime Closure Gates",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )
