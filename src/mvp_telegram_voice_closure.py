"""MVP Roadmap 5 Telegram voice pipeline progress model."""

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
        raise ValueError("unsupported telegram voice closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported telegram voice closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class TelegramVoiceClosureGate:
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
    ) -> "TelegramVoiceClosureGate":
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
class TelegramVoiceClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[TelegramVoiceClosureGate, ...]
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
        return f"| 5 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[TelegramVoiceClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[TelegramVoiceClosureGate]) -> TelegramVoiceClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_telegram_voice_closure_report(
    *,
    operator_voice_contract_go: bool = True,
    metadata_intake_boundary_go: bool = True,
    redacted_history_readiness_go: bool = True,
    download_gate_plan_go: bool = True,
    fake_stt_boundary_go: bool = True,
    voice_agent_turn_go: bool = True,
    gated_reply_plan_go: bool = True,
    plugin_runtime_integration_go: bool = True,
    live_voice_smoke_go: bool = False,
    voice_ui_live_go: bool = False,
) -> TelegramVoiceClosureReport:
    gates = (
        TelegramVoiceClosureGate.create(
            gate_id="operator_voice_contract",
            title="Voice operator contract",
            status="go" if operator_voice_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "operator status language and separate download/STT/reply gates are documented"
                if operator_voice_contract_go
                else "operator voice contract is missing or blocked"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="metadata_intake_boundary",
            title="Metadata-only voice intake",
            status="go" if metadata_intake_boundary_go else "blocked",
            slice_class="repo_only",
            reason=(
                "voice intake stores redacted metadata and does not become agent-ready before STT"
                if metadata_intake_boundary_go
                else "metadata-only voice intake is missing or blocked"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="redacted_history_readiness",
            title="Redacted history and readiness",
            status="go" if redacted_history_readiness_go else "blocked",
            slice_class="repo_only",
            reason=(
                "history/readiness expose counts and redacted handles without raw chat or file ids"
                if redacted_history_readiness_go
                else "redacted history/readiness is missing or blocked"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="download_gate_plan",
            title="Voice download gate plan",
            status="go" if download_gate_plan_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "download planning is disabled by default, bounded, and produces safe local refs"
                if download_gate_plan_go
                else "disabled-by-default fake download boundary still needs focused implementation"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="fake_stt_boundary",
            title="Fakeable STT boundary",
            status="go" if fake_stt_boundary_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "fakeable STT requires a local ref and redacts sensitive transcript fragments"
                if fake_stt_boundary_go
                else "provider-agnostic fake STT boundary still needs focused implementation"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="voice_agent_turn",
            title="Voice transcript to agent turn",
            status="go" if voice_agent_turn_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "successful transcripts become internal Telegram voice agent prompts"
                if voice_agent_turn_go
                else "voice transcript-to-agent turn still needs focused implementation"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="gated_reply_plan",
            title="Gated Telegram text reply plan",
            status="go" if gated_reply_plan_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "reply planning remains disabled until the reply gate and reply text are present"
                if gated_reply_plan_go
                else "gated text reply plan for voice transcripts still needs implementation"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="plugin_runtime_integration",
            title="Plugin runtime integration",
            status="go" if plugin_runtime_integration_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "Telegram plugin wires the offline voice pipeline through fakeable runtime hooks"
                if plugin_runtime_integration_go
                else "Telegram plugin still needs explicit offline hook integration for download/STT/voice agent-turn flow"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="live_voice_smoke",
            title="Manual live voice smoke",
            status="go" if live_voice_smoke_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "one operator-approved live voice roundtrip has redacted evidence"
                if live_voice_smoke_go
                else "needs explicit operator Go for real Telegram voice download/STT/reply smoke"
            ),
        ),
        TelegramVoiceClosureGate.create(
            gate_id="voice_ui_live",
            title="Voice UI live",
            status="go" if voice_ui_live_go else "needs_design",
            slice_class="needs_design",
            reason=(
                "voice status and controls are live on the redesigned UI"
                if voice_ui_live_go
                else "voice UI/status controls are deferred until the shared UI redesign"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 5 is complete; continue to ORCA / Lens Naming & Backend Migration."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.status == "repo_open":
            next_decision = "Continue backend-safe Telegram voice integration, starting with plugin runtime hooks for the offline pipeline."
        elif first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer the manual live Telegram voice smoke before claiming voice pipeline closure."
        elif first_incomplete.slice_class == "needs_design":
            next_decision = "Keep Telegram voice UI deferred until backend voice hooks are closed."
        else:
            next_decision = f"Resolve {first_incomplete.title} before Telegram voice closure."
    return TelegramVoiceClosureReport(
        roadmap_id="telegram_voice_pipeline",
        title="Telegram Voice Pipeline",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )
