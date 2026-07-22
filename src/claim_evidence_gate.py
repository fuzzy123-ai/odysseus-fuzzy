"""Conservative claim-to-evidence checks for final agent replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

from src.tool_transaction_ledger import transaction_evidence_for_claim, transactions_from_tool_events
from src.todo_digest_receipts import (
    todo_digest_evidence_for_claim,
    todo_digest_receipts_from_tool_events,
)
from src.todo_receipts import (
    todo_receipt_evidence_for_claim,
    todo_receipts_from_tool_events,
)


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
_SYNTAX_VERIFIED_RE = re.compile(
    r"\b(?:syntax|syntakt(?:isch|ische))\b.{0,32}\b(?:valid|verified|passed|korrekt|gueltig|gültig|bestanden)\b",
    re.IGNORECASE,
)
_HEADLESS_VERIFIED_RE = re.compile(
    r"\b(?:headless|dummy[- ]?sdl)\b.{0,48}\b(?:verified|tested|passed|success|erfolgreich|getestet|bestanden)\b",
    re.IGNORECASE,
)
_VISUAL_INSPECTED_RE = re.compile(
    r"(?:\bvisual(?:ly)?\s+(?:inspection\s*:\s*)?(?:inspected|verified|passed)\b|"
    r"\b(?:screenshot|image|bild)\b.{0,40}\b(?:visually\s+)?(?:inspected|reviewed|checked|geprueft|geprüft)\b|"
    r"\b(?:screenshot|image|bild)\b.{0,40}\b(?:looks?\s+(?:good|perfect)|sieht\s+(?:gut|perfekt)\s+aus)\b)",
    re.IGNORECASE,
)
_DOWNLOAD_READY_RE = re.compile(
    r"\b(?:download(?:\s+is)?\s+ready|download[- ]?ready|downloadbereit|download[- ]?link|zum\s+herunterladen\s+bereit)\b",
    re.IGNORECASE,
)
_INTERACTIVE_PREVIEW_RE = re.compile(
    r"\b(?:playable\s+(?:here|in\s+the\s+browser)|spielbar(?:\s+hier|\s+im\s+browser)?|"
    r"interactive\s+preview\s+(?:is\s+)?(?:ready|working)|interaktive\s+vorschau\s+(?:ist\s+)?(?:bereit|fertig)|"
    r"laeuft\s+interaktiv|läuft\s+interaktiv)\b",
    re.IGNORECASE,
)
_TODO_CLAIM_PATTERNS = (
    (
        "todo_item_created",
        re.compile(
            r"\b(?:(?:todo|to-do)s?|aufgaben?)\b.{0,48}\b(?:gespeichert|hinzugef(?:ue|\u00fc)gt|angelegt|saved|created|added)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "todo_item_completed",
        re.compile(
            r"\b(?:(?:todo|to-do)s?|aufgaben?)\b.{0,48}\b(?:erledigt|abgehakt|completed|done)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "todo_item_reopened",
        re.compile(
            r"\b(?:(?:todo|to-do)s?|aufgaben?)\b.{0,48}\b(?:wieder\s+ge(?:oe|\u00f6)ffnet|reopened)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "todo_item_removed",
        re.compile(
            r"\b(?:(?:todo|to-do)s?|aufgaben?)\b.{0,48}\b(?:entfernt|gel(?:oe|\u00f6)scht|removed|deleted)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "todo_list_read",
        re.compile(
            r"\b(?:todo|to-do)[-\s]?(?:liste|list)\b.{0,48}\b(?:gelesen|geladen|read|loaded)\b",
            re.IGNORECASE,
        ),
    ),
)
_TODO_DIGEST_CONTAINS_RE = re.compile(
    r"\b(?:todo|to-do|aufgabe)\b.{0,80}(?:(?:erscheint|auftaucht|appears).{0,56}\b(?:digest|zusammenfassung)\b|\b(?:digest|zusammenfassung)\b.{0,40}\b(?:enthalten|included)\b)",
    re.IGNORECASE,
)
_TODO_DIGEST_EXCLUDES_RE = re.compile(
    r"\b(?:todo|to-do|aufgabe)\b.{0,80}(?:(?:nicht\s+mehr|no\s+longer|excluded).{0,56}\b(?:digest|zusammenfassung)\b|\b(?:digest|zusammenfassung)\b.{0,40}\b(?:nicht\s+(?:mehr\s+)?enthalten|excluded)\b)",
    re.IGNORECASE,
)
_TODO_DIGEST_UNVERIFIED_RE = re.compile(
    r"\b(?:nicht\s+verifiziert|unverified|nicht\s+sicher|cannot\s+verify|can't\s+verify)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_TIMING_RE = re.compile(
    r"\b(?:morgen|tomorrow|naechst(?:e|en|er|es)?|n(?:ae|ä)chste(?:n|r|s)?|next|um\s+[0-2]?\d(?::[0-5]\d)?|at\s+[0-2]?\d(?::[0-5]\d)?)\b",
    re.IGNORECASE,
)
_NEGATED_CLAIM_PREFIX_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|couldn't|didn't|nicht|nie|konnte\s+nicht|kein(?:e|en|em|er|es)?|unverified|unavailable|nicht\s+verifiziert)\b.{0,36}$",
    re.IGNORECASE,
)
_NEGATED_CLAIM_WITHIN_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|couldn't|didn't|nicht|nie|konnte\s+nicht|kein(?:e|en|em|er|es)?|unverified|unavailable)\b",
    re.IGNORECASE,
)
_TODO_QUANTITY_PREFIX_RE = re.compile(
    r"\b(?:(?P<count>[1-9]\d{0,2})|(?P<count_word>beide|both|zwei|two))\s*$",
    re.IGNORECASE,
)
_TODO_PLURAL_CLAIM_RE = re.compile(r"^(?:todos|to-dos|aufgaben)\b", re.IGNORECASE)


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
    todo_receipts = todo_receipts_from_tool_events(events)
    todo_digest_receipts = todo_digest_receipts_from_tool_events(events)
    findings: list[ClaimEvidenceFinding] = []

    if not text.strip():
        return ClaimEvidenceReport(findings=())

    first_person_action = bool(_FIRST_PERSON_ACTION_RE.search(text))

    if first_person_action and _FILE_ACTION_RE.search(text):
        paths = _extract_repo_paths(text)
        if paths:
            tx_evidence = transaction_evidence_for_claim(transactions, "file_changed", paths)
            evidence = tuple(path for path in paths if _repo_file_exists(root, path) or _event_mentions_success(events, path))
            evidence = tuple(dict.fromkeys((*evidence, *tx_evidence)))
            if evidence:
                findings.append(ClaimEvidenceFinding("file_changed", "supported", "mentioned file path has filesystem or tool evidence", evidence))
            else:
                findings.append(ClaimEvidenceFinding("file_changed", "unsupported", "file claim mentions paths without filesystem or tool evidence", paths))

    if first_person_action and _TEST_ACTION_RE.search(text):
        evidence = tuple(dict.fromkeys((*_successful_test_events(events), *transaction_evidence_for_claim(transactions, "command_passed"))))
        findings.append(
            ClaimEvidenceFinding(
                "command_passed",
                "supported" if evidence else "unsupported",
                "test claim has a successful test command" if evidence else "test claim has no successful test command evidence",
                evidence,
            )
        )

    if first_person_action and _SANDBOX_ACTION_RE.search(text):
        evidence = tuple(dict.fromkeys((*_successful_sandbox_events(events), *transaction_evidence_for_claim(transactions, "sandbox_succeeded"))))
        findings.append(
            ClaimEvidenceFinding(
                "sandbox_succeeded",
                "supported" if evidence else "unsupported",
                "sandbox claim has a successful sandbox event" if evidence else "sandbox claim has no successful sandbox event evidence",
                evidence,
            )
        )

    if first_person_action and _TELEGRAM_SENT_RE.search(text):
        evidence = tuple(dict.fromkeys((*_successful_telegram_events(events), *transaction_evidence_for_claim(transactions, "telegram_sent"))))
        findings.append(
            ClaimEvidenceFinding(
                "telegram_sent",
                "supported" if evidence else "unsupported",
                "telegram claim has successful outbound evidence" if evidence else "telegram claim has no successful outbound evidence",
                evidence,
            )
        )

    if first_person_action and _SCREENSHOT_RE.search(text) and _TELEGRAM_SENT_RE.search(text):
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

    for claim_type, pattern in _TODO_CLAIM_PATTERNS:
        matches = _positive_claim_matches(pattern, text)
        if not matches:
            continue
        required_count = max(_todo_claim_count(text, match) for match in matches)
        matching_receipt_count = len({
            receipt.receipt_ref
            for receipt in todo_receipts
            if receipt.claim_type == claim_type and receipt.verified
        })
        evidence = todo_receipt_evidence_for_claim(todo_receipts, claim_type)
        if matching_receipt_count < required_count:
            evidence = ()
        findings.append(
            ClaimEvidenceFinding(
                claim_type,
                "supported" if evidence else "unsupported",
                (
                    "Todo claim has enough unique verified semantic receipts"
                    if evidence
                    else "Todo claim has fewer unique verified semantic receipts than claimed"
                ),
                evidence,
            )
        )

    digest_claim_found = False
    excludes_match = _TODO_DIGEST_EXCLUDES_RE.search(text)
    if excludes_match:
        prefix = text[max(0, excludes_match.start() - 48) : excludes_match.start()]
        if (
            not _NEGATED_CLAIM_PREFIX_RE.search(prefix)
            and not _TODO_DIGEST_UNVERIFIED_RE.search(excludes_match.group(0))
        ):
            digest_claim_found = True
            evidence = todo_digest_evidence_for_claim(
                todo_digest_receipts,
                "todo_digest_excludes",
            )
            findings.append(
                ClaimEvidenceFinding(
                    "todo_digest_excludes",
                    "supported" if evidence else "unsupported",
                    (
                        "Todo digest exclusion has a matching read-only projection receipt"
                        if evidence
                        else "Todo digest exclusion has no matching read-only projection receipt"
                    ),
                    evidence,
                )
            )
    elif _has_positive_claim(_TODO_DIGEST_CONTAINS_RE, text):
        digest_claim_found = True
        evidence = todo_digest_evidence_for_claim(
            todo_digest_receipts,
            "todo_digest_contains",
        )
        findings.append(
            ClaimEvidenceFinding(
                "todo_digest_contains",
                "supported" if evidence else "unsupported",
                (
                    "Todo digest membership has a matching read-only projection receipt"
                    if evidence
                    else "Todo digest membership has no matching read-only projection receipt"
                ),
                evidence,
            )
        )

    if digest_claim_found and _TODO_DIGEST_TIMING_RE.search(text):
        evidence = todo_digest_evidence_for_claim(
            todo_digest_receipts,
            "todo_digest_schedule_active",
        )
        findings.append(
            ClaimEvidenceFinding(
                "todo_digest_schedule_active",
                "supported" if evidence else "unsupported",
                (
                    "Timed Todo digest claim has one active owner-scoped schedule receipt"
                    if evidence
                    else "Timed Todo digest claim has no canonical active schedule receipt"
                ),
                evidence,
            )
        )

    for claim_type, pattern, supported_reason, unsupported_reason in (
        (
            "syntax_verified",
            _SYNTAX_VERIFIED_RE,
            "syntax claim has structured verification evidence",
            "syntax claim has no structured verification evidence",
        ),
        (
            "headless_tested",
            _HEADLESS_VERIFIED_RE,
            "headless claim has bounded verification evidence",
            "headless claim has no bounded verification evidence",
        ),
        (
            "visual_inspected",
            _VISUAL_INSPECTED_RE,
            "visual claim is bound to a vision-inspected artifact",
            "visual claim has no verified vision evidence",
        ),
        (
            "download_ready",
            _DOWNLOAD_READY_RE,
            "download claim has an owner-scoped published artifact",
            "download claim has no owner-scoped publication evidence",
        ),
        (
            "interactive_preview_ready",
            _INTERACTIVE_PREVIEW_RE,
            "interactive claim has visible preview evidence",
            "interactive claim has no visible preview evidence",
        ),
    ):
        if not _has_positive_claim(pattern, text):
            continue
        evidence = _structured_claim_evidence(events, claim_type)
        findings.append(
            ClaimEvidenceFinding(
                claim_type,
                "supported" if evidence else "unsupported",
                supported_reason if evidence else unsupported_reason,
                evidence,
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


def _has_positive_claim(pattern: re.Pattern[str], text: str) -> bool:
    return bool(_positive_claim_matches(pattern, text))


def _positive_claim_matches(
    pattern: re.Pattern[str], text: str
) -> tuple[re.Match[str], ...]:
    matches: list[re.Match[str]] = []
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 48) : match.start()]
        if (
            not _NEGATED_CLAIM_PREFIX_RE.search(prefix)
            and not _NEGATED_CLAIM_WITHIN_RE.search(match.group(0))
        ):
            matches.append(match)
    return tuple(matches)


def _todo_claim_count(text: str, match: re.Match[str]) -> int:
    prefix = text[max(0, match.start() - 24) : match.start()]
    quantity = _TODO_QUANTITY_PREFIX_RE.search(prefix)
    if quantity is None:
        return 2 if _TODO_PLURAL_CLAIM_RE.match(match.group(0)) else 1
    if quantity.group("count_word"):
        return 2
    return max(1, min(int(quantity.group("count") or 1), 999))


def _structured_claim_evidence(
    events: Iterable[Mapping[str, Any]],
    claim_type: str,
) -> tuple[str, ...]:
    found: list[str] = []
    for event in events:
        payload = event.get("artifact_evidence")
        if not isinstance(payload, Mapping):
            continue
        claim = payload.get(claim_type)
        if not isinstance(claim, Mapping) or claim.get("status") != "verified":
            continue
        artifact_id = str(claim.get("artifact_id") or payload.get("artifact_id") or "").strip()
        artifact_hash = str(claim.get("artifact_hash") or payload.get("artifact_hash") or "").strip()
        if artifact_id and artifact_hash:
            found.append(f"{artifact_id}:sha256:{artifact_hash}")
        elif artifact_id:
            found.append(artifact_id)
        elif artifact_hash:
            found.append(f"sha256:{artifact_hash}")
        else:
            found.append(f"{event.get('tool') or 'artifact'}:{claim_type}")
    return tuple(dict.fromkeys(found))
