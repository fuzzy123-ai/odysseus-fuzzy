"""Deterministic Telegram truth-runtime contracts.

These helpers keep Telegram coding runs evidence-first without storing raw
chat content, host paths, or secrets. They are intentionally small and pure so
the risky behavior from the Telegram review can be regression-tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from src.agent_sandbox_contract import DEFAULT_SANDBOX_CAPABILITIES
from src.telegram_truth_gate import gate_telegram_reply_text
from src.tool_transaction_ledger import ToolTransaction, ToolTransactionStatus


TELEGRAM_TRUTH_RUNTIME_SCHEMA = "odysseus.telegram_truth_runtime.v1"
REQUIRED_RUN_STATES = (
    "accepted",
    "checking_capabilities",
    "running",
    "artifact_ready",
    "sent",
    "verified_done",
    "blocked",
    "failed",
)

_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|token|bearer\s+[A-Za-z0-9._-]{8,})"
)
_HOST_PATH_RE = re.compile(r"(?i)(^[a-z]:[\\/]|^/|/home/|/opt/|/users/|~[\\/])")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,180}$")
_PROGRAM_SCREENSHOT_RE = re.compile(
    r"\b(pong|pygame|spiel|game|programm|program|script|app|screenshot|bildschirmfoto)\b",
    re.IGNORECASE,
)
_PYGAME_RE = re.compile(r"\bpygame\b", re.IGNORECASE)
_CONFIRMATION_RE = re.compile(
    r"\b(soll\s+ich|darf\s+ich|moechtest\s+du|"
    r"bestaetig|bestaetigung|wirklich|starten\?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    status: str
    required_capabilities: tuple[str, ...]
    available_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    blockers: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    next_step: str
    raw_content_visible: bool = False
    schema: str = TELEGRAM_TRUTH_RUNTIME_SCHEMA

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "required_capabilities": self.required_capabilities,
            "available_capabilities": self.available_capabilities,
            "missing_capabilities": self.missing_capabilities,
            "blockers": self.blockers,
            "evidence_refs": self.evidence_refs,
            "next_step": self.next_step,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class TelegramRunStateEvent:
    run_id: str
    state: str
    task_type: str
    job_id: str = ""
    artifact_ref: str = ""
    transaction_id: str = ""
    message: str = ""
    raw_content_visible: bool = False
    schema: str = TELEGRAM_TRUTH_RUNTIME_SCHEMA

    def __post_init__(self) -> None:
        if self.state not in REQUIRED_RUN_STATES:
            raise ValueError(f"unsupported run state: {self.state}")
        _safe_ref(self.run_id, "run_id")
        _safe_ref(self.task_type, "task_type")
        for field, value in (
            ("job_id", self.job_id),
            ("artifact_ref", self.artifact_ref),
            ("transaction_id", self.transaction_id),
        ):
            if value:
                _safe_ref(value, field)
        if self.message and (_SECRET_RE.search(self.message) or _HOST_PATH_RE.search(self.message)):
            raise ValueError("message contains unsafe content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "state": self.state,
            "task_type": self.task_type,
            "job_id": self.job_id,
            "artifact_ref": self.artifact_ref,
            "transaction_id": self.transaction_id,
            "message": self.message,
            "raw_content_visible": False,
        }


def build_program_screenshot_capability_check(
    text: Any,
    *,
    available_capabilities: Iterable[str] | None = None,
    python_available: bool = True,
    sandbox_available: bool = True,
    telegram_photo_reply_available: bool = True,
    artifact_root_writable: bool = True,
    library_available: Mapping[str, bool] | None = None,
) -> CapabilityCheck:
    """Evaluate prerequisites before starting a Telegram coding screenshot task."""

    task_text = str(text or "")
    caps = tuple(dict.fromkeys(str(item) for item in (available_capabilities or DEFAULT_SANDBOX_CAPABILITIES)))
    cap_set = set(caps)
    library_available = dict(library_available or {})
    required = [
        "python",
        "sandbox_execution",
        "screenshot_artifacts",
        "telegram_photo_artifact_reply",
        "headless_or_gui_renderer",
    ]
    available = []
    missing = []
    blockers = []
    evidence = []

    def mark(name: str, ok: bool, evidence_ref: str, blocker: str) -> None:
        (available if ok else missing).append(name)
        if ok:
            evidence.append(evidence_ref)
        else:
            blockers.append(blocker)

    relevant = bool(_PROGRAM_SCREENSHOT_RE.search(task_text))
    mark("python", python_available and "python" in cap_set, "capability:python", "python_missing")
    mark("sandbox_execution", sandbox_available, "sandbox:available", "sandbox_unavailable")
    mark(
        "screenshot_artifacts",
        artifact_root_writable and "screenshot_artifacts" in cap_set,
        "artifact_root:writable",
        "artifact_root_not_writable",
    )
    mark(
        "telegram_photo_artifact_reply",
        telegram_photo_reply_available,
        "telegram:photo_artifact_reply",
        "telegram_photo_reply_unavailable",
    )
    renderer_ok = bool({"playwright", "browser_gui", "headless_renderer"} & cap_set)
    mark("headless_or_gui_renderer", renderer_ok, "renderer:available", "renderer_missing")

    if relevant and _PYGAME_RE.search(task_text):
        required.append("library:pygame")
        pygame_ok = bool(library_available.get("pygame"))
        mark("library:pygame", pygame_ok, "library:pygame", "library_pygame_missing")

    status = "ready" if not missing else "blocked"
    next_step = "start_sandbox_job" if status == "ready" else "report_blocker_without_success_claim"
    return CapabilityCheck(
        status=status,
        required_capabilities=tuple(required),
        available_capabilities=tuple(available),
        missing_capabilities=tuple(missing),
        blockers=tuple(blockers),
        evidence_refs=tuple(_safe_ref(item, "evidence_ref") for item in evidence),
        next_step=next_step,
    )


def build_telegram_run_state_sequence(
    run_id: str,
    *,
    task_type: str = "coding_agent_task",
    capability_check: CapabilityCheck | Mapping[str, Any] | None = None,
    transactions: Iterable[Mapping[str, Any] | ToolTransaction] = (),
    artifact_ref: str = "",
    job_id: str = "",
) -> tuple[TelegramRunStateEvent, ...]:
    """Build an auditable Telegram run-state sequence from deterministic evidence."""

    events = [
        TelegramRunStateEvent(run_id=run_id, state="accepted", task_type=task_type, job_id=job_id),
        TelegramRunStateEvent(run_id=run_id, state="checking_capabilities", task_type=task_type, job_id=job_id),
    ]
    cap_status = _capability_status(capability_check)
    if cap_status == "blocked":
        events.append(TelegramRunStateEvent(run_id=run_id, state="blocked", task_type=task_type, job_id=job_id))
        return tuple(events)

    txs = tuple(_transaction_dict(item) for item in transactions)
    failed = next((tx for tx in txs if tx.get("status") in {"failed", "blocked"}), None)
    events.append(TelegramRunStateEvent(run_id=run_id, state="running", task_type=task_type, job_id=job_id))
    if artifact_ref:
        events.append(
            TelegramRunStateEvent(
                run_id=run_id,
                state="artifact_ready",
                task_type=task_type,
                job_id=job_id,
                artifact_ref=artifact_ref,
            )
        )
    if failed:
        state = "blocked" if failed.get("status") == "blocked" else "failed"
        events.append(
            TelegramRunStateEvent(
                run_id=run_id,
                state=state,
                task_type=task_type,
                job_id=job_id,
                transaction_id=str(failed.get("transaction_id") or ""),
            )
        )
        return tuple(events)

    telegram_tx = next((tx for tx in txs if tx.get("claim_type") == "telegram_sent" and tx.get("verified_done")), None)
    verified_tx = next((tx for tx in txs if tx.get("verified_done")), None)
    if telegram_tx:
        events.append(
            TelegramRunStateEvent(
                run_id=run_id,
                state="sent",
                task_type=task_type,
                job_id=job_id,
                artifact_ref=artifact_ref,
                transaction_id=str(telegram_tx.get("transaction_id") or ""),
            )
        )
    if artifact_ref and telegram_tx and verified_tx:
        events.append(
            TelegramRunStateEvent(
                run_id=run_id,
                state="verified_done",
                task_type=task_type,
                job_id=job_id,
                artifact_ref=artifact_ref,
                transaction_id=str(verified_tx.get("transaction_id") or ""),
            )
        )
    return tuple(events)


def run_state_status_message(event: TelegramRunStateEvent | Mapping[str, Any]) -> str:
    payload = event.to_dict() if isinstance(event, TelegramRunStateEvent) else dict(event)
    state = str(payload.get("state") or "unknown")
    messages = {
        "accepted": "Status: angenommen.",
        "checking_capabilities": "Status: pruefe Voraussetzungen.",
        "running": "Status: laeuft.",
        "artifact_ready": "Status: Artefakt bereit.",
        "sent": "Status: per Telegram gesendet.",
        "verified_done": "Status: verifiziert abgeschlossen.",
        "blocked": "Blockiert: Voraussetzung fehlt.",
        "failed": "Fehlgeschlagen: Evidence zeigt einen Fehler.",
    }
    return messages.get(state, f"Status: {state}.")


def analyze_telegram_truth_regressions(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return stable offline metrics for the Telegram review failure modes."""

    unsupported_success_count = 0
    fake_delegate_blame_count = 0
    tone_gate_violation_count = 0
    repeated_confirmation_count = 0
    for case in cases:
        assistant_text = str(case.get("assistant_text") or case.get("assistant_reply") or case.get("text") or "")
        if assistant_text:
            result = gate_telegram_reply_text(assistant_text, case.get("tool_events") or ())
            if result.status != "verified":
                unsupported_success_count += 1
            claim_types = {finding.claim_type for finding in result.findings}
            if "delegate_alibi" in claim_types:
                fake_delegate_blame_count += 1
            if result.status != "verified" and _contains_jubilation(result.text):
                tone_gate_violation_count += 1
        confirmation_count = _confirmation_count(case)
        if confirmation_count > 1:
            repeated_confirmation_count += confirmation_count - 1
    return {
        "schema": TELEGRAM_TRUTH_RUNTIME_SCHEMA,
        "unsupported_success_count": unsupported_success_count,
        "repeated_confirmation_count": repeated_confirmation_count,
        "fake_delegate_blame_count": fake_delegate_blame_count,
        "tone_gate_violation_count": tone_gate_violation_count,
        "raw_content_visible": False,
    }


def _capability_status(check: CapabilityCheck | Mapping[str, Any] | None) -> str:
    if check is None:
        return "ready"
    if isinstance(check, CapabilityCheck):
        return check.status
    return str(check.get("status") or "")


def _transaction_dict(item: Mapping[str, Any] | ToolTransaction) -> dict[str, Any]:
    if isinstance(item, ToolTransaction):
        return item.to_dict()
    return dict(item)


def _confirmation_count(case: Mapping[str, Any]) -> int:
    if isinstance(case.get("conversation"), list):
        texts = [str(item.get("text") if isinstance(item, Mapping) else item) for item in case["conversation"]]
    else:
        texts = [str(case.get("assistant_text") or case.get("text") or "")]
    return sum(1 for text in texts if _CONFIRMATION_RE.search(text))


def _contains_jubilation(text: str) -> bool:
    return bool(re.search(r"\b(fertig!?|geschafft!?|perfekt!?|erfolgreich!?)\b", text, re.IGNORECASE))


def _safe_ref(value: Any, field: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > 180 or _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        raise ValueError(f"{field} contains unsafe content")
    if not _SAFE_REF_RE.fullmatch(text):
        raise ValueError(f"{field} contains unsafe characters")
    return text
