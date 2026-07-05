"""Conservative claim-to-evidence checks for final agent replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

from src.tool_transaction_ledger import transaction_evidence_for_claim, transactions_from_tool_events


_FIRST_PERSON_ACTION_RE = re.compile(
    r"\b("
    r"ich\s+habe|ich\s+hab|ich\s+konnte|i\s+(?:have\s+)?(?:created|wrote|changed|sent|tested|ran)|"
    r"(?:erstellt|angelegt|geschrieben|geaendert|geändert|gesendet|geschickt|verschickt|getestet|ausgefuehrt|ausgeführt)"
    r")\b",
    re.IGNORECASE,
)
_FILE_ACTION_RE = re.compile(
    r"\b(erstellt|angelegt|geschrieben|geaendert|geändert|created|wrote|changed|updated)\b",
    re.IGNORECASE,
)
_TEST_ACTION_RE = re.compile(
    r"\b(tests?\s+(?:passed|pass|green|gruen|grün|durchgelaufen|bestanden)|pytest\s+(?:passed|gruen|grün)|"
    r"(?:getestet|tests?\s+ausgefuehrt|tests?\s+ausgeführt))\b",
    re.IGNORECASE,
)
_SANDBOX_ACTION_RE = re.compile(
    r"\b(sandbox(?:-|\s)?(?:run|job|command)?\s+(?:succeeded|passed|completed|erfolgreich|fertig)|"
    r"sandbox\s+war\s+erfolgreich)\b",
    re.IGNORECASE,
)
_TELEGRAM_SENT_RE = re.compile(
    r"\b(telegram.*(?:gesendet|geschickt|verschickt|sent)|(?:gesendet|geschickt|verschickt|sent).*telegram)\b",
    re.IGNORECASE,
)
_SCREENSHOT_RE = re.compile(r"\b(screenshot|bildschirmfoto)\b", re.IGNORECASE)
_PATH_TOKEN_RE = re.compile(
    r"`([^`]{1,220})`|(?:^|[\s(\"'])([A-Za-z0-9][A-Za-z0-9._/-]{0,180}\.[A-Za-z0-9]{1,12})(?=$|[\s),.!?\"'])"
)
_TEST_COMMAND_RE = re.compile(r"\b(pytest|unittest|npm\s+test|npm\s+run\s+test|pnpm\s+test|yarn\s+test|vitest|playwright\s+test)\b", re.IGNORECASE)
_SUCCESS_WORD_RE = re.compile(r"\b(ok|sent|success|succeeded|completed|passed|written|created|gesendet|geschickt|erfolgreich)\b", re.IGNORECASE)
_ERROR_WORD_RE = re.compile(r"\b(error|failed|blocked|exception|traceback|fehler|gescheitert|abgelehnt)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ClaimEvidenceFinding:
    claim_type: str
    status: str
    reason: str
    evidence: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        return self.status == "supported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "status": self.status,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ClaimEvidenceReport:
    findings: tuple[ClaimEvidenceFinding, ...]

    @property
    def unsupported(self) -> tuple[ClaimEvidenceFinding, ...]:
        return tuple(item for item in self.findings if not item.supported)

    @property
    def ok(self) -> bool:
        return not self.unsupported

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.claim_evidence_gate.v1",
            "ok": self.ok,
            "findings": tuple(item.to_dict() for item in self.findings),
            "unsupported_count": len(self.unsupported),
        }


def evaluate_response_claims(
    response: Any,
    tool_events: Iterable[Mapping[str, Any]] = (),
    *,
    repo_root: Path | str | None = None,
    tool_transactions: Iterable[Mapping[str, Any]] = (),
) -> ClaimEvidenceReport:
    """Return unsupported concrete action claims found in an assistant reply.

    The check is intentionally conservative. It only evaluates first-person or
    concrete completion language for high-risk side effects where deterministic
    evidence is available in the workspace or tool events.
    """

    text = str(response or "")
    events = tuple(event for event in tool_events if isinstance(event, Mapping))
    transactions = tuple(item for item in tool_transactions if isinstance(item, Mapping))
    if not transactions and events:
        transactions = tuple(item.to_dict() for item in transactions_from_tool_events(events, surface="claim_evidence"))
    root = Path(repo_root or Path.cwd()).resolve()
    findings: list[ClaimEvidenceFinding] = []

    if not text.strip() or not _FIRST_PERSON_ACTION_RE.search(text):
        return ClaimEvidenceReport(findings=())

    if _FILE_ACTION_RE.search(text):
        paths = _extract_repo_paths(text)
        if paths:
            tx_evidence = transaction_evidence_for_claim(transactions, "file_changed", paths)
            evidence = tuple(path for path in paths if _repo_file_exists(root, path) or _event_mentions_success(events, path))
            evidence = tuple(dict.fromkeys((*evidence, *tx_evidence)))
            if evidence:
                findings.append(ClaimEvidenceFinding("file_changed", "supported", "mentioned file path has filesystem or tool evidence", evidence))
            else:
                findings.append(ClaimEvidenceFinding("file_changed", "unsupported", "file claim mentions paths without filesystem or tool evidence", paths))

    if _TEST_ACTION_RE.search(text):
        evidence = tuple(dict.fromkeys((*_successful_test_events(events), *transaction_evidence_for_claim(transactions, "command_passed"))))
        findings.append(
            ClaimEvidenceFinding(
                "command_passed",
                "supported" if evidence else "unsupported",
                "test claim has a successful test command" if evidence else "test claim has no successful test command evidence",
                evidence,
            )
        )

    if _SANDBOX_ACTION_RE.search(text):
        evidence = tuple(dict.fromkeys((*_successful_sandbox_events(events), *transaction_evidence_for_claim(transactions, "sandbox_succeeded"))))
        findings.append(
            ClaimEvidenceFinding(
                "sandbox_succeeded",
                "supported" if evidence else "unsupported",
                "sandbox claim has a successful sandbox event" if evidence else "sandbox claim has no successful sandbox event evidence",
                evidence,
            )
        )

    if _TELEGRAM_SENT_RE.search(text):
        evidence = tuple(dict.fromkeys((*_successful_telegram_events(events), *transaction_evidence_for_claim(transactions, "telegram_sent"))))
        findings.append(
            ClaimEvidenceFinding(
                "telegram_sent",
                "supported" if evidence else "unsupported",
                "telegram claim has successful outbound evidence" if evidence else "telegram claim has no successful outbound evidence",
                evidence,
            )
        )

    if _SCREENSHOT_RE.search(text) and _TELEGRAM_SENT_RE.search(text):
        paths = tuple(path for path in _extract_repo_paths(text) if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
        evidence = tuple(path for path in paths if _repo_file_exists(root, path))
        tx_evidence = transaction_evidence_for_claim(transactions, "artifact_exists", paths)
        evidence = tuple(dict.fromkeys((*evidence, *tx_evidence)))
        if paths:
            findings.append(
                ClaimEvidenceFinding(
                    "artifact_exists",
                    "supported" if evidence else "unsupported",
                    "screenshot artifact exists" if evidence else "screenshot artifact path is not present in the workspace",
                    evidence or paths,
                )
            )

    return ClaimEvidenceReport(findings=tuple(findings))


def build_claim_evidence_correction(report: ClaimEvidenceReport) -> str:
    if report.ok:
        return ""
    claim_types = ", ".join(dict.fromkeys(item.claim_type for item in report.unsupported))
    return (
        "\n\nHinweis: Einige Erfolgsclaims sind noch nicht maschinenlesbar belegt "
        f"({claim_types}). Behandle diese Punkte als nicht verifiziert, bis passende Evidence vorliegt."
    )


def _extract_repo_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in _PATH_TOKEN_RE.finditer(text):
        raw = (match.group(1) or match.group(2) or "").strip()
        path = _safe_repo_path(raw)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)


def _safe_repo_path(value: str) -> str:
    text = value.strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return ""
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,220}", text):
        return ""
    return "/".join(parts)


def _repo_file_exists(root: Path, path: str) -> bool:
    target = (root / path).resolve()
    try:
        return (root == target or root in target.parents) and target.is_file() and not target.is_symlink()
    except OSError:
        return False


def _event_text(event: Mapping[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("tool", "command", "output", "stdout", "stderr"))


def _event_exit_ok(event: Mapping[str, Any]) -> bool:
    exit_code = event.get("exit_code")
    return exit_code in (0, "0", None) and not _ERROR_WORD_RE.search(_event_text(event))


def _event_mentions_success(events: Iterable[Mapping[str, Any]], needle: str) -> bool:
    lowered = needle.lower()
    for event in events:
        text = _event_text(event).lower()
        if lowered in text and _event_exit_ok(event) and _SUCCESS_WORD_RE.search(text):
            return True
    return False


def _successful_test_events(events: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    found: list[str] = []
    for event in events:
        text = _event_text(event)
        if _TEST_COMMAND_RE.search(text) and _event_exit_ok(event):
            found.append(str(event.get("command") or event.get("tool") or "test_command")[:160])
    return tuple(found)


def _successful_sandbox_events(events: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    found: list[str] = []
    for event in events:
        text = _event_text(event)
        if "sandbox" in text.lower() and _event_exit_ok(event) and _SUCCESS_WORD_RE.search(text):
            found.append(str(event.get("command") or event.get("tool") or "sandbox")[:160])
    return tuple(found)


def _successful_telegram_events(events: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    found: list[str] = []
    for event in events:
        text = _event_text(event)
        tool = str(event.get("tool") or "").lower()
        if "telegram" in (tool + " " + text.lower()) and _event_exit_ok(event) and _SUCCESS_WORD_RE.search(text):
            found.append(str(event.get("command") or event.get("tool") or "telegram")[:160])
    return tuple(found)
