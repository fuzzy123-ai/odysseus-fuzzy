"""Pre-send truth and tone guard for Telegram replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from src.claim_evidence_gate import ClaimEvidenceFinding, evaluate_response_claims
from src.telegram_todo_truth import tool_events_from_telegram_todo_truth_envelope


_ALREADY_UNVERIFIED_RE = re.compile(r"\bnicht\s+verifiziert\b|\bunverified\b", re.IGNORECASE)
_DELEGATE_ALIBI_RE = re.compile(
    r"\b(delegate|delegat|subagent|worker)\b.{0,80}\b(falsch|fehlerhaft|rueckmeldung|gemeldet|schuld)\b",
    re.IGNORECASE,
)
_DEPENDENCY_SUCCESS_RE = re.compile(
    r"\b(?:ich\s+habe|ich\s+hab|installiert|eingerichtet|pygame)\b.*\b(?:pygame|installiert|installation)\b",
    re.IGNORECASE,
)
_SCREENSHOT_SUCCESS_RE = re.compile(
    r"\b(?:screenshot|bildschirmfoto)\b.*\b(?:erstellt|angelegt|gesendet|geschickt|verschickt|fertig)\b",
    re.IGNORECASE,
)
_TELEGRAM_SEND_SUCCESS_RE = re.compile(
    r"\btelegram\b.*\b(?:gesendet|geschickt|verschickt|sent|erfolgreich)\b",
    re.IGNORECASE,
)
_JUBILANT_WORD_RE = re.compile(
    r"\b(?:alles\s+erledigt|fertig!?|geschafft!?|super!?|perfekt!?|erfolgreich!?)\b",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001faff"
    "\U00002600-\U000027bf"
    "]"
)


@dataclass(frozen=True, slots=True)
class TelegramTruthGateResult:
    text: str
    status: str
    findings: tuple[ClaimEvidenceFinding, ...] = ()

    @property
    def changed(self) -> bool:
        return self.status != "verified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.telegram_truth_gate.v1",
            "status": self.status,
            "changed": self.changed,
            "findings": tuple(item.to_dict() for item in self.findings),
        }


def gate_telegram_reply_text(
    text: Any,
    tool_events: Iterable[Mapping[str, Any]] = (),
    *,
    repo_root: Path | str | None = None,
    todo_truth_envelope: Mapping[str, Any] | None = None,
) -> TelegramTruthGateResult:
    """Return Telegram text with unsupported success claims made explicit.

    This guard is intentionally conservative and runs immediately before
    Telegram delivery. It does not try to prove all text true; it only prevents
    high-risk success, send, screenshot, dependency, and delegate-alibi claims
    from leaving the process without machine-readable evidence.
    """

    original = str(text or "")
    events = tuple(event for event in tool_events if isinstance(event, Mapping))
    envelope_events = tool_events_from_telegram_todo_truth_envelope(todo_truth_envelope)
    if envelope_events:
        events = (*events, *envelope_events)
    report = evaluate_response_claims(original, events, repo_root=repo_root)
    findings = list(report.unsupported)
    findings.extend(_synthetic_unsupported_findings(original, events))

    if not findings:
        return TelegramTruthGateResult(text=_normalize_whitespace(original), status="verified", findings=())

    gated = _strip_unverified_jubilation(original)
    if not _ALREADY_UNVERIFIED_RE.search(gated):
        gated = (
            f"{gated.rstrip()}\n\n"
            f"Status: nicht verifiziert. Ich habe fuer diese Erfolgsbehauptung noch keine "
            f"maschinenlesbare Evidence im Tool- oder Dateisystem-Kontext."
        )
    return TelegramTruthGateResult(
        text=_normalize_whitespace(gated),
        status="unknown",
        findings=tuple(findings),
    )


def _synthetic_unsupported_findings(text: str, events: tuple[Mapping[str, Any], ...]) -> tuple[ClaimEvidenceFinding, ...]:
    findings: list[ClaimEvidenceFinding] = []
    if _DELEGATE_ALIBI_RE.search(text) and not _has_event(events, ("delegate", "subagent", "worker")):
        findings.append(ClaimEvidenceFinding(
            "delegate_alibi",
            "unsupported",
            "delegate explanation has no delegate event evidence",
        ))
    if _DEPENDENCY_SUCCESS_RE.search(text) and not _has_success_event(events, ("pip", "install", "pygame")):
        findings.append(ClaimEvidenceFinding(
            "dependency_installed",
            "unsupported",
            "dependency install claim has no successful install event evidence",
        ))
    if _SCREENSHOT_SUCCESS_RE.search(text) and not _has_success_event(
        events,
        ("screenshot", "bildschirmfoto", ".png", ".jpg", ".webp"),
    ):
        findings.append(ClaimEvidenceFinding(
            "screenshot_created",
            "unsupported",
            "screenshot claim has no artifact or screenshot event evidence",
        ))
    if _TELEGRAM_SEND_SUCCESS_RE.search(text) and not _has_success_event(events, ("telegram", "send", "sent")):
        findings.append(ClaimEvidenceFinding(
            "telegram_sent",
            "unsupported",
            "telegram send claim has no outbound event evidence",
        ))
    return tuple(findings)


def _strip_unverified_jubilation(text: str) -> str:
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    cleaned: list[str] = []
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            cleaned.append(part)
            continue
        part = _EMOJI_RE.sub("", part)
        part = _JUBILANT_WORD_RE.sub("", part)
        cleaned.append(part)
    return "".join(cleaned)


def _has_event(events: tuple[Mapping[str, Any], ...], needles: tuple[str, ...]) -> bool:
    lowered = tuple(item.lower() for item in needles)
    return any(any(needle in _event_text(event) for needle in lowered) for event in events)


def _has_success_event(events: tuple[Mapping[str, Any], ...], needles: tuple[str, ...]) -> bool:
    return any(_event_success(event) and any(needle.lower() in _event_text(event) for needle in needles) for event in events)


def _event_success(event: Mapping[str, Any]) -> bool:
    exit_code = event.get("exit_code")
    text = _event_text(event)
    return exit_code in (0, "0", None) and not re.search(r"\b(error|failed|fehler|traceback|blocked|abgelehnt)\b", text)


def _event_text(event: Mapping[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("tool", "command", "output", "stdout", "stderr")).lower()


def _normalize_whitespace(text: str) -> str:
    lines = [re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in str(text or "").splitlines()]
    return "\n".join(lines).strip()
