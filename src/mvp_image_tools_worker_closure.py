"""MVP Roadmap 9 image tools worker final-smoke progress model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_STATUSES = ("go", "repo_open", "needs_live_go", "needs_design", "blocked", "deferred")
_SLICE_CLASSES = ("safe_offline", "repo_only", "needs_live_go", "needs_design", "blocked")


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported image tools worker closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported image tools worker closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class ImageToolsWorkerClosureGate:
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
    ) -> "ImageToolsWorkerClosureGate":
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
class ImageToolsWorkerClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[ImageToolsWorkerClosureGate, ...]
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
        return f"| 9 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[ImageToolsWorkerClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[ImageToolsWorkerClosureGate]) -> ImageToolsWorkerClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_image_tools_worker_closure_report(
    *,
    worker_contract_go: bool = True,
    core_client_go: bool = True,
    route_integration_go: bool = True,
    isolated_worker_mvp_go: bool = True,
    fake_worker_smoke_go: bool = True,
    core_dependency_isolation_go: bool = True,
    ui_cookbook_contract_go: bool = True,
    telegram_image_readiness_go: bool = True,
    manual_remove_bg_smoke_go: bool = False,
    image_tools_ui_live_go: bool = False,
) -> ImageToolsWorkerClosureReport:
    gates = (
        ImageToolsWorkerClosureGate.create(
            gate_id="worker_contract",
            title="Image worker contract",
            status="go" if worker_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "worker modes, config, errors, payload limits and security boundaries are documented"
                if worker_contract_go
                else "image tools worker contract is missing or blocked"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="core_client",
            title="Core worker client",
            status="go" if core_client_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "core client handles disabled, timeout, unreachable, payload and PNG response semantics"
                if core_client_go
                else "core worker client still needs structured error and response handling"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="route_integration",
            title="Remove-BG route integration",
            status="go" if route_integration_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "gallery remove-bg route calls the worker client after privilege checks and maps errors"
                if route_integration_go
                else "remove-bg route still needs safe worker-client integration"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="isolated_worker_mvp",
            title="Isolated worker MVP",
            status="go" if isolated_worker_mvp_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "workers/image_tools_worker exposes /remove-background with structured JSON errors"
                if isolated_worker_mvp_go
                else "isolated worker MVP endpoint is missing or incomplete"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="fake_worker_smoke",
            title="Fake worker smoke",
            status="go" if fake_worker_smoke_go else "repo_open",
            slice_class="safe_offline",
            reason=(
                "worker response builder can be smoke-tested with a fake PNG without importing rembg"
                if fake_worker_smoke_go
                else "offline fake worker smoke is missing"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="core_dependency_isolation",
            title="Core dependency isolation",
            status="go" if core_dependency_isolation_go else "blocked",
            slice_class="repo_only",
            reason=(
                "core starts without hard rembg, PIL or worker dependency imports on the client path"
                if core_dependency_isolation_go
                else "core path has hard image-tool dependency leakage"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="ui_cookbook_contract",
            title="UI and cookbook contract",
            status="go" if ui_cookbook_contract_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "setup/error wording is frozen for the later UI and cookbook redesign"
                if ui_cookbook_contract_go
                else "UI/cookbook setup language still needs a contract"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="telegram_image_readiness",
            title="Telegram image action readiness",
            status="go" if telegram_image_readiness_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "Telegram image actions can reuse the worker client without new core dependencies"
                if telegram_image_readiness_go
                else "Telegram image-action integration is not yet wired to the worker client"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="manual_remove_bg_smoke",
            title="Manual Remove-BG smoke",
            status="go" if manual_remove_bg_smoke_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "operator-approved live worker Remove-BG run has redacted evidence"
                if manual_remove_bg_smoke_go
                else "final real Remove-BG/Image smoke needs explicit operator Go and worker runtime"
            ),
        ),
        ImageToolsWorkerClosureGate.create(
            gate_id="image_tools_ui_live",
            title="Image tools UI live",
            status="go" if image_tools_ui_live_go else "needs_design",
            slice_class="needs_design",
            reason=(
                "new UI exposes image-worker setup and error states"
                if image_tools_ui_live_go
                else "image-worker UI/status controls are deferred until the shared UI redesign"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 9 is complete; continue to GameDev Mount Write Smoke."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.status == "repo_open":
            next_decision = "Continue backend-safe worker integration, starting with Telegram image-action readiness."
        elif first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer the final manual Remove-BG smoke; it should run only with an explicit worker runtime Go."
        elif first_incomplete.slice_class == "needs_design":
            next_decision = "Keep image-worker UI deferred until the shared UI redesign."
        else:
            next_decision = f"Resolve {first_incomplete.title} before image tools worker closure."
    return ImageToolsWorkerClosureReport(
        roadmap_id="image_tools_worker_final_smoke",
        title="Image Tools Worker Final Smoke",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )
