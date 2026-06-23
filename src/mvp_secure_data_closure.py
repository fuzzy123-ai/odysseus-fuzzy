"""MVP Roadmap 2 secure data runtime hook progress model.

This is an offline closure view. It distinguishes completed policy/model
building blocks from the remaining runtime wiring that must call those gates in
provider, retrieval, Telegram, and private-source paths.
"""

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
        raise ValueError("unsupported secure data closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported secure data closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class SecureDataClosureGate:
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
    ) -> "SecureDataClosureGate":
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
class SecureDataClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[SecureDataClosureGate, ...]
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
        return f"| 2 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[SecureDataClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[SecureDataClosureGate]) -> SecureDataClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_secure_data_closure_report(
    *,
    data_classification_model_go: bool = True,
    chat_security_state_go: bool = True,
    central_policy_gate_go: bool = True,
    local_model_routing_guard_go: bool = True,
    sensitive_retrieval_guard_go: bool = True,
    telegram_channel_policy_go: bool = True,
    provider_runtime_hook_go: bool = True,
    retrieval_runtime_hook_go: bool = True,
    telegram_runtime_hook_go: bool = True,
    private_source_runtime_hook_go: bool = True,
) -> SecureDataClosureReport:
    """Build the offline progress view for MVP Roadmap 2."""

    gates = (
        SecureDataClosureGate.create(
            gate_id="data_classification_model",
            title="Data classification model",
            status="go" if data_classification_model_go else "blocked",
            slice_class="repo_only",
            reason=(
                "public/private/sensitive/secret classification and strict merge rules exist"
                if data_classification_model_go
                else "data classification model is missing or blocked"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="chat_security_state",
            title="Immutable chat security state",
            status="go" if chat_security_state_go else "blocked",
            slice_class="repo_only",
            reason=(
                "normal/secure chat state is immutable and local-only scope is modeled"
                if chat_security_state_go
                else "immutable chat security state is missing or blocked"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="central_policy_gate",
            title="Central secure policy gate",
            status="go" if central_policy_gate_go else "blocked",
            slice_class="repo_only",
            reason=(
                "source, provider, embedding, tool, export, and ambiguous-state decisions are modeled"
                if central_policy_gate_go
                else "central secure policy gate is missing or blocked"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="local_model_routing_guard",
            title="Local-only model routing guard",
            status="go" if local_model_routing_guard_go else "blocked",
            slice_class="repo_only",
            reason=(
                "secure chats require local primary, fallback, and embedding model routes"
                if local_model_routing_guard_go
                else "local-only model routing guard is missing or blocked"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="sensitive_retrieval_guard",
            title="Sensitive retrieval guard",
            status="go" if sensitive_retrieval_guard_go else "blocked",
            slice_class="repo_only",
            reason=(
                "memory/RAG/graph guard blocks sensitive normal-chat context without refs"
                if sensitive_retrieval_guard_go
                else "sensitive retrieval guard is missing or blocked"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="telegram_channel_policy",
            title="Telegram and channel policy",
            status="go" if telegram_channel_policy_go else "blocked",
            slice_class="repo_only",
            reason=(
                "channel policy blocks sensitive Telegram/unsupported secure-flow paths"
                if telegram_channel_policy_go
                else "Telegram/channel secure policy is missing or blocked"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="provider_runtime_hook",
            title="Provider runtime hook",
            status="go" if provider_runtime_hook_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "provider selection calls secure policy before external routes"
                if provider_runtime_hook_go
                else "provider/model selection still needs direct runtime hook integration"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="retrieval_runtime_hook",
            title="Retrieval runtime hook",
            status="go" if retrieval_runtime_hook_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "memory/RAG/graph retrieval calls the sensitive retrieval guard before loading context"
                if retrieval_runtime_hook_go
                else "memory/RAG/graph retrieval still needs direct guard call-sites"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="telegram_runtime_hook",
            title="Telegram runtime hook",
            status="go" if telegram_runtime_hook_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "Telegram text/voice responses call channel policy before returning sensitive content"
                if telegram_runtime_hook_go
                else "Telegram runtime still needs direct channel-policy call-sites"
            ),
        ),
        SecureDataClosureGate.create(
            gate_id="private_source_runtime_hook",
            title="Private source runtime hook",
            status="go" if private_source_runtime_hook_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "private-source ingestion/review calls secure policy before indexing or exposure"
                if private_source_runtime_hook_go
                else "private-source ingestion still needs secure policy integration"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 2 is complete; continue to Private Data / Nextcloud Memory Ingestion."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        next_decision = (
            "Continue backend-safe runtime hook integration, starting with provider/model selection."
            if first_incomplete.status == "repo_open"
            else f"Resolve {first_incomplete.title} before secure data closure."
        )
    return SecureDataClosureReport(
        roadmap_id="secure_data_mode_runtime_hooks",
        title="Secure Data Mode Runtime Hooks",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )
