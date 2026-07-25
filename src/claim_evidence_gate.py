"""Conservative claim-to-evidence checks for final agent replies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

from src.tool_transaction_ledger import transaction_evidence_for_claim, transactions_from_tool_events
from src.todo_digest_receipts import (
    digest_receipts_from_tool_events,
    todo_digest_evidence_for_claim,
    todo_digest_receipts_from_tool_events,
    validated_todo_digest_receipt_from_event,
)
from src.todo_digest_schedule_receipts import validated_todo_digest_schedule_receipt_from_event
from src.todo_receipts import todo_receipt_evidence_for_claim, todo_receipts_from_tool_events


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
_NEGATED_CLAIM_PREFIX_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|couldn't|didn't|nicht|nie|konnte\s+nicht|kein(?:e|en|em|er|es)?|unverified|unavailable|nicht\s+verifiziert)\b.{0,36}$",
    re.IGNORECASE,
)
_NEGATED_CLAIM_WITHIN_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|couldn't|didn't|nicht|nie|konnte\s+nicht|kein(?:e|en|em|er|es)?|unverified|unavailable)\b",
    re.IGNORECASE,
)
_TODO_CONTEXT_RE = re.compile(
    r"\b(?:todo[-\s]?(?:items?|tasks?)|to[-\s]?do[-\s]?(?:items?|tasks?)|"
    r"todo(?:[-\s]?(?:item|task|list|liste|eintrag|aufgabe))?|to[-\s]?do|"
    r"todos|to-dos|aufgaben|task|checklist|list(?:\s+item)?|item|aufgabe|eintrag|liste)\b",
    re.IGNORECASE,
)
_TODO_EN_ACTOR_RE = re.compile(r"\bi(?:\s+(?:have|did)|['’]ve)?\b", re.IGNORECASE)
_TODO_DE_ACTOR_RE = re.compile(r"\bich\s+(?:habe|hab)\b", re.IGNORECASE)
_TODO_NONPOSITIVE_RE = re.compile(
    r"\b(?:not|never|no|without|cannot|can't|couldn't|didn't|"
    r"nicht|nie|kein(?:e|en|em|er|es)?|ohne|"
    r"if|would|could|should|might|maybe|when|falls|wenn|w[üu]rde|k[öo]nnte|sollte|vielleicht|"
    r"will|going\s+to|want(?:\s+you)?\s+to|need(?:\s+you)?\s+to|intend\s+to|"
    r"plan(?:s)?\s+to|hope\s+to|later|tomorrow|"
    r"werde|will|m[öo]chte|plane|vorhabe|sp[äa]ter|morgen|"
    r"said|says|reported|reports|according\s+to|quoted|asked|ask|whether|"
    r"sagte|sagt|berichtete|berichtet|laut|zitiert|fragte|frage|ob)\b",
    re.IGNORECASE,
)
_TODO_QUOTED_TEXT_RE = re.compile(
    r'"[^"\n]*"|“[^”\n]*”|„[^“\n]*“|(?<!\w)\'[^\'\n]*\'(?!\w)|`[^`\n]*`'
)
_TODO_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "todo_item_created",
        re.compile(r"\b(?:added|created|saved|hinzugef[üu]gt|erstellt|angelegt|gespeichert)\b", re.IGNORECASE),
    ),
    (
        "todo_item_completed",
        re.compile(
            r"\b(?:completed|checked\s+off|marked\b.{0,48}?\b(?:as\s+)?done|done|finished|"
            r"erledigt|abgeschlossen|abgehakt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "todo_item_reopened",
        re.compile(
            r"\b(?:reopened|uncompleted|undid(?:\s+(?:the\s+)?completion)?|"
            r"wieder\s+(?:ge[öo]ffnet)|wiederer[öo]ffnet|r[üu]ckg[äa]ngig\s+gemacht)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "todo_item_removed",
        re.compile(r"\b(?:removed|deleted|gel[öo]scht|entfernt)\b", re.IGNORECASE),
    ),
    (
        "todo_list_read",
        re.compile(r"\b(?:listed|read|showed|shown|displayed|aufgelistet|gelesen|angezeigt|gezeigt)\b", re.IGNORECASE),
    ),
)
_TODO_ITEM_MUTATION_CLAIMS = frozenset(
    {
        "todo_item_created",
        "todo_item_completed",
        "todo_item_reopened",
        "todo_item_removed",
    }
)
_TODO_QUANTITY_PREFIX_RE = re.compile(
    r"\b(?:(?P<count>\d+)|(?P<one>one|eins|eine|einen|einem|einer)|"
    r"(?P<two>two|zwei|both|beide|beiden))\s*$",
    re.IGNORECASE,
)
_TODO_NUMERIC_LIKE_PREFIX_RE = re.compile(r"\d[\d.,_]*\s*$")
_TODO_PLURAL_CONTEXT_RE = re.compile(
    r"^(?:(?:todo|to-do)[-\s](?:items|tasks)|todos|to-dos|aufgaben)$",
    re.IGNORECASE,
)
_TODO_NON_TODO_TASK_PREFIX_RE = re.compile(
    r"\b(?:automation|cron|scheduled|scheduler)\s*$",
    re.IGNORECASE,
)
# Match the bounded count accepted by the Todo receipt and transaction schemas.
_MAX_TODO_CLAIM_COUNT = 1_000_000
_MAX_TODO_CLAIM_COUNT_DIGITS = len(str(_MAX_TODO_CLAIM_COUNT))
_TODO_DIGEST_CONTEXT_RE = re.compile(r"\b(?:todo\s+)?digest\b|\bzusammenfassung\b", re.IGNORECASE)
_TODO_DIGEST_CONTAINS_RE = re.compile(
    r"\b(?:appears?|is\s+included|is\s+contained|included\s+in|erscheint|ist\s+enthalten)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_EXCLUDES_RE = re.compile(
    r"\b(?:does\s+not\s+appear|no\s+longer\s+appears?|is\s+not\s+included|is\s+excluded|excluded\s+from|erscheint\s+nicht|ist\s+nicht(?:\s+\w+){0,3}\s+enthalten|ausgeschlossen)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_FUTURE_RE = re.compile(
    r"\b(?:if|would|could|can\s+you|should|might|maybe|want|need|please|will|going\s+to|tomorrow|later|"
    r"wenn|w[\u00fc]rde|k[\u00f6]nnte|sollte|vielleicht|m[\u00f6]chte|bitte|werde|morgen|sp[\u00e4]ter)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_REQUEST_HYPOTHETICAL_RE = re.compile(
    r"\b(?:if|would|could|can\s+you|should|might|maybe|want|need|please|"
    r"wenn|w[\u00fc]rde|k[\u00f6]nnte|sollte|vielleicht|m[\u00f6]chte|bitte)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_INTENTION_PREFIX_RE = re.compile(
    r"\b(?:"
    r"i\s+(?:will|won['\u2019]t|will\s+not|plan\s+to|intend\s+to|want\s+to|need\s+to|hope\s+to|am\s+going\s+to)|"
    r"i['\u2019]ll|"
    r"we\s+(?:will|won['\u2019]t|will\s+not|plan\s+to|intend\s+to|want\s+to|need\s+to|hope\s+to|are\s+going\s+to)|"
    r"we['\u2019]ll|"
    r"ich\s+(?:werde|will|m\u00f6chte|plane|habe\s+vor)|"
    r"wir\s+(?:werden|wollen|m\u00f6chten|planen|haben\s+vor)"
    r")\s+(?:nicht\s+)?(?:daf\u00fcr\s+)?(?:make|ensure|cause|try|attempt|have|sorgen|versuchen|bitten)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_TIMING_RE = re.compile(
    r"\b(?:tomorrow|morning|monday|tuesday|wednesday|thursday|friday|saturday|sunday|at\s+\d{1,2}(?::\d{2})?|send|sends|sending|sent|emailed|deliver|delivers|delivering|delivered|telegram|slack|email|ntfy|provider|run|runs|ran|running|execute|executes|executing|executed|execution|"
    r"morgen|morgens|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|um\s+\d{1,2}(?::\d{2})?|sende(?:t|n)?|gesendet|versendet|verschickt|geliefert|zugestellt|anbieter|ausf[\u00fcu]hren|ausgef[\u00fcu]hrt|ausf[\u00fch]rung|f[\u00fcu]hrt\s+aus|l[\u00e4a]uft|lief)\b",
    re.IGNORECASE,
)
_TODO_DIGEST_NEXT_RE = re.compile(r"\b(?:next\s+(?:todo\s+)?digest|n[\u00e4]chst(?:e|en|er)?\s+(?:todo\s+)?(?:digest|zusammenfassung))\b", re.IGNORECASE)


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


@dataclass(frozen=True, slots=True)
class AgentMaintenanceClaimOwnership:
    """Compatibility shape for disabled maintenance execution gates."""

    expected_claim_id: str
    expected_owner: str
    allowed_paths: tuple[str, ...]
    current_claim_id: str
    current_owner: str
    current_changed_paths: tuple[str, ...]
    current_staged_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentMaintenanceCompletionEvidence:
    """Typed evidence accepted by legacy callers without granting authority."""

    receipt: Mapping[str, Any]
    claim_report: ClaimEvidenceReport
    expected_lane: str
    required_evidence_level: str
    claim_ownership: AgentMaintenanceClaimOwnership


@dataclass(frozen=True, slots=True)
class AgentMaintenanceCompletionReport:
    completed: bool
    receipt_current: bool
    claims_current: bool
    ownership_current: bool
    expected_lane: str
    required_evidence_level: str
    actual_evidence_level: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent_maintenance_completion_gate.v1",
            "completed": self.completed,
            "receipt_current": self.receipt_current,
            "claims_current": self.claims_current,
            "ownership_current": self.ownership_current,
            "expected_lane": self.expected_lane,
            "required_evidence_level": self.required_evidence_level,
            "actual_evidence_level": self.actual_evidence_level,
            "blockers": list(self.blockers),
            "origin_authenticated": False,
            "commit_authorized": False,
            "push_authorized": False,
            "live_authorized": False,
        }


def evaluate_agent_maintenance_completion(
    evidence: AgentMaintenanceCompletionEvidence | None,
    *,
    repo_root: str | Path,
) -> AgentMaintenanceCompletionReport:
    """Fail closed while the maintenance completion verifier is gated."""

    del repo_root
    typed = isinstance(evidence, AgentMaintenanceCompletionEvidence)
    return AgentMaintenanceCompletionReport(
        completed=False,
        receipt_current=False,
        claims_current=bool(
            typed
            and isinstance(evidence.claim_report, ClaimEvidenceReport)
            and evidence.claim_report.ok
        ),
        ownership_current=False,
        expected_lane=(
            str(evidence.expected_lane or "missing") if typed else "missing"
        ),
        required_evidence_level=(
            str(evidence.required_evidence_level or "none") if typed else "none"
        ),
        actual_evidence_level="none",
        blockers=(
            "maintenance completion verification is disabled pending architecture review",
        ),
    )


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

    findings.extend(_todo_claim_findings(text, transactions, events))
    findings.extend(_todo_digest_claim_findings(text, events))

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


def _todo_claim_findings(
    text: str,
    transactions: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]] = (),
) -> tuple[ClaimEvidenceFinding, ...]:
    """Return only explicit, positive Todo claims bound to matching transactions."""
    requirements: dict[str, tuple[int, bool]] = {}
    unquoted = _TODO_QUOTED_TEXT_RE.sub(" ", text)
    for sentence in re.split(r"(?<=[.!?;\n])", unquoted):
        action_matches = sorted(
            (match.start(), match.end(), claim_type)
            for claim_type, action_pattern in _TODO_ACTION_PATTERNS
            for match in action_pattern.finditer(sentence)
        )
        todo_context_matches = tuple(_TODO_CONTEXT_RE.finditer(sentence))
        sentence_requirements: dict[str, tuple[int, bool]] = {}
        for action_start, action_end, claim_type in action_matches:
            prefix = sentence[:action_start]
            if _TODO_NONPOSITIVE_RE.search(prefix):
                continue
            has_actor = bool(_TODO_EN_ACTOR_RE.search(prefix) or _TODO_DE_ACTOR_RE.search(prefix))
            bound_contexts = tuple(
                context
                for context in todo_context_matches
                if _todo_context_binds_to_action(
                    action_start, action_end, action_matches, (context,)
                )
                and not _todo_context_is_generic_task(sentence, context)
            )
            has_bound_context = bool(bound_contexts)
            has_preceding_context = any(
                context.end() <= action_start for context in bound_contexts
            )
            is_bare_done = sentence[action_start:action_end].strip().lower() == "done"
            if is_bare_done and not has_preceding_context:
                continue
            if not has_bound_context:
                continue
            if not has_actor and (not has_preceding_context or "?" in sentence):
                continue
            if claim_type in _TODO_ITEM_MUTATION_CLAIMS:
                count, out_of_range = _todo_claim_count(sentence, bound_contexts)
            else:
                # One validated list-read receipt covers the complete snapshot,
                # regardless of how many items the response says were listed.
                count, out_of_range = 1, False
            previous = sentence_requirements.get(claim_type)
            if previous is None:
                sentence_requirements[claim_type] = (count, out_of_range)
            else:
                # Multiple success verbs in one sentence can describe the same
                # operation (for example, "added and saved two Todos"). Count
                # that sentence once, but keep a malformed numeric claim fatal.
                sentence_requirements[claim_type] = (
                    max(previous[0], count),
                    previous[1] or out_of_range,
                )

        for claim_type, (count, out_of_range) in sentence_requirements.items():
            previous_count, previous_out_of_range = requirements.get(claim_type, (0, False))
            requirements[claim_type] = (
                (
                    previous_count + count
                    if claim_type in _TODO_ITEM_MUTATION_CLAIMS
                    else max(previous_count, count)
                ),
                previous_out_of_range or out_of_range,
            )

    receipts = todo_receipts_from_tool_events(events)
    findings: list[ClaimEvidenceFinding] = []
    for claim_type, (required_count, out_of_range) in requirements.items():
        matching_receipts = tuple(
            receipt
            for receipt in receipts
            if receipt.claim_type == claim_type and receipt.verified
        )
        receipt_evidence = todo_receipt_evidence_for_claim(matching_receipts, claim_type)
        if out_of_range:
            evidence = ()
            reason = "Todo success claim has an out-of-range claimed item count"
        elif required_count > 1:
            evidence = receipt_evidence if len(matching_receipts) >= required_count else ()
            reason = (
                "Todo claim has enough unique verified semantic receipts"
                if evidence
                else "Todo claim has fewer unique verified semantic receipts than claimed"
            )
        else:
            evidence = transaction_evidence_for_claim(transactions, claim_type)
            if not evidence:
                evidence = receipt_evidence
            reason = (
                "Todo success claim has a matching verified semantic receipt"
                if evidence
                else "Todo success claim has no matching verified semantic receipt"
            )
        findings.append(
            ClaimEvidenceFinding(
                claim_type,
                "supported" if evidence else "unsupported",
                reason,
                evidence,
            )
        )
    return tuple(findings)


def _todo_claim_count(
    sentence: str, contexts: Iterable[re.Match[str]]
) -> tuple[int, bool]:
    """Return the minimum receipt count for one Todo claim sentence.

    Numbers are read only directly before the Todo context.  A numeric value
    outside the bounded protocol range is deliberately not coerced downward:
    that would let a larger free-prose claim pass on weaker evidence.
    """

    required_count = 1
    for context in contexts:
        prefix = sentence[max(0, context.start() - 32) : context.start()]
        quantity = _TODO_QUANTITY_PREFIX_RE.search(prefix)
        if quantity is not None:
            if quantity.group("one"):
                required_count = max(required_count, 1)
                continue
            if quantity.group("two"):
                required_count = max(required_count, 2)
                continue
            raw_count = quantity.group("count") or "0"
            if len(raw_count) > _MAX_TODO_CLAIM_COUNT_DIGITS:
                return required_count, True
            value = int(raw_count)
            if not 1 <= value <= _MAX_TODO_CLAIM_COUNT:
                return required_count, True
            required_count = max(required_count, value)
            continue
        if _TODO_NUMERIC_LIKE_PREFIX_RE.search(prefix):
            return required_count, True
        if _TODO_PLURAL_CONTEXT_RE.fullmatch(context.group(0)):
            required_count = max(required_count, 2)
    return required_count, False


def _todo_context_is_generic_task(
    sentence: str, context: re.Match[str]
) -> bool:
    """Keep explicitly non-Todo scheduler/automation tasks outside this gate."""

    if context.group(0).lower() not in {"task", "tasks"}:
        return False
    prefix = sentence[max(0, context.start() - 24) : context.start()]
    return bool(_TODO_NON_TODO_TASK_PREFIX_RE.search(prefix))


def _todo_digest_claim_findings(text: str, events: Iterable[Mapping[str, Any]]) -> tuple[ClaimEvidenceFinding, ...]:
    """Assess present membership and generic next-digest schedules; timing/delivery stays unsupported."""
    event_snapshot = tuple(events)
    legacy_receipts = todo_digest_receipts_from_tool_events(event_snapshot)
    if legacy_receipts:
        return _legacy_todo_digest_claim_findings(text, legacy_receipts)
    receipts = digest_receipts_from_tool_events(event_snapshot)
    findings: list[ClaimEvidenceFinding] = []
    seen: set[str] = set()
    stronger_schedule_unsupported = False
    unquoted = _TODO_QUOTED_TEXT_RE.sub(" ", text)
    for sentence in re.split(r"(?<=[.!?;\n])", unquoted):
        if not _TODO_DIGEST_CONTEXT_RE.search(sentence) or not _TODO_CONTEXT_RE.search(sentence):
            continue
        contains = _TODO_DIGEST_CONTAINS_RE.search(sentence)
        excludes = _TODO_DIGEST_EXCLUDES_RE.search(sentence)
        if not contains and not excludes:
            continue
        match = excludes or contains
        prefix = sentence[:match.start()]
        if _TODO_DIGEST_REQUEST_HYPOTHETICAL_RE.search(prefix):
            continue
        if _TODO_DIGEST_INTENTION_PREFIX_RE.search(prefix):
            continue
        if _NEGATED_CLAIM_PREFIX_RE.search(prefix):
            continue
        next_digest = bool(_TODO_DIGEST_NEXT_RE.search(sentence))
        if _TODO_DIGEST_TIMING_RE.search(sentence):
            stronger_schedule_unsupported = True
            if "todo_digest_schedule_active" not in seen:
                findings.append(ClaimEvidenceFinding(
                    "todo_digest_schedule_active", "unsupported",
                    "digest membership evidence never proves scheduling, execution, or delivery", (),
                ))
                seen.add("todo_digest_schedule_active")
            # A time, schedule, or delivery statement is not a present-tense
            # membership claim.  TTD-05B is the earliest possible proof lane.
            continue
        if _TODO_DIGEST_FUTURE_RE.search(prefix):
            continue
        claim_type = "todo_digest_excludes" if excludes else "todo_digest_contains"
        if claim_type not in seen:
            evidence = tuple(receipt["receipt_ref"] for receipt in receipts if receipt["claim_type"] == claim_type)
            findings.append(ClaimEvidenceFinding(
                claim_type,
                "supported" if evidence else "unsupported",
                "Todo digest membership has a matching verified postcondition" if evidence else "Todo digest membership has no matching verified postcondition",
                evidence,
            ))
            seen.add(claim_type)
        if next_digest and "todo_digest_schedule_active" not in seen:
            evidence = _next_digest_schedule_evidence(event_snapshot, claim_type)
            findings.append(ClaimEvidenceFinding(
                "todo_digest_schedule_active",
                "supported" if evidence else "unsupported",
                "Todo digest has a verified active future schedule" if evidence else "Todo digest has no verified active future schedule receipt",
                evidence,
            ))
            seen.add("todo_digest_schedule_active")
    if stronger_schedule_unsupported:
        without_schedule = [item for item in findings if item.claim_type != "todo_digest_schedule_active"]
        if len(without_schedule) != len(findings):
            findings = [*without_schedule, ClaimEvidenceFinding(
                "todo_digest_schedule_active", "unsupported",
                "exact timing, execution, provider, or delivery language is never proved by schedule status", (),
            )]
    return tuple(findings)


def _legacy_todo_digest_claim_findings(
    text: str, receipts: Iterable[Any]
) -> tuple[ClaimEvidenceFinding, ...]:
    """Evaluate only the redacted v1 Telegram receipt compatibility format."""
    findings: list[ClaimEvidenceFinding] = []
    unquoted = _TODO_QUOTED_TEXT_RE.sub(" ", text)
    for sentence in re.split(r"(?<=[.!?;\n])", unquoted):
        if not _TODO_DIGEST_CONTEXT_RE.search(sentence) or not _TODO_CONTEXT_RE.search(sentence):
            continue
        excludes = _TODO_DIGEST_EXCLUDES_RE.search(sentence)
        contains = _TODO_DIGEST_CONTAINS_RE.search(sentence)
        if not excludes and not contains:
            continue
        match = excludes or contains
        prefix = sentence[:match.start()]
        # Compatibility receipts only change the evidence format.  They must not
        # make a request, intention, negation, or generic future statement into
        # a factual claim that the current receipt lane would ignore.
        if _TODO_DIGEST_REQUEST_HYPOTHETICAL_RE.search(prefix):
            continue
        if _TODO_DIGEST_INTENTION_PREFIX_RE.search(prefix):
            continue
        if _NEGATED_CLAIM_PREFIX_RE.search(prefix):
            continue
        if not _TODO_DIGEST_TIMING_RE.search(sentence) and _TODO_DIGEST_FUTURE_RE.search(prefix):
            continue
        claim_type = "todo_digest_excludes" if excludes else "todo_digest_contains"
        evidence = todo_digest_evidence_for_claim(receipts, claim_type)
        findings.append(
            ClaimEvidenceFinding(
                claim_type,
                "supported" if evidence else "unsupported",
                (
                    "Todo digest membership has a matching verified compatibility receipt"
                    if evidence
                    else "Todo digest membership has no matching verified compatibility receipt"
                ),
                evidence,
            )
        )
        if _TODO_DIGEST_TIMING_RE.search(sentence):
            findings = [
                ClaimEvidenceFinding(
                    "todo_digest_schedule_active",
                    "unsupported",
                    "exact timing is not proved by a compatibility schedule receipt",
                    (),
                )
            ]
            continue
        if _TODO_DIGEST_NEXT_RE.search(sentence):
            schedule_evidence = todo_digest_evidence_for_claim(
                receipts, "todo_digest_schedule_active"
            )
            findings.append(
                ClaimEvidenceFinding(
                    "todo_digest_schedule_active",
                    "supported" if schedule_evidence else "unsupported",
                    (
                        "Todo digest has a verified active compatibility schedule"
                        if schedule_evidence
                        else "Todo digest has no verified active compatibility schedule receipt"
                    ),
                    schedule_evidence,
                )
            )
    return tuple(findings)


def _next_digest_schedule_evidence(events: Iterable[Mapping[str, Any]], membership_claim_type: str) -> tuple[str, ...]:
    """Require both proofs in one closed event for a concrete next-digest claim."""
    refs: list[str] = []
    seen: set[str] = set()
    for index, event in enumerate(events):
        if index >= 64:
            break
        membership = validated_todo_digest_receipt_from_event(event)
        schedule = validated_todo_digest_schedule_receipt_from_event(event)
        if membership is None or schedule is None or membership.get("claim_type") != membership_claim_type:
            continue
        if membership["evidence_refs"][0] != schedule["evidence_refs"][0]:
            continue
        receipt_ref = schedule["receipt_ref"]
        if receipt_ref not in seen:
            seen.add(receipt_ref)
            refs.append(receipt_ref)
        if len(refs) == 64:
            break
    return tuple(refs)


def _todo_context_binds_to_action(
    action_start: int,
    action_end: int,
    action_matches: Iterable[tuple[int, int, str]],
    todo_context_matches: Iterable[re.Match[str]],
    *,
    preceding_only: bool = False,
) -> bool:
    """Accept only a nearby Todo token whose nearest action is this action."""
    for context in todo_context_matches:
        if preceding_only and context.end() > action_start:
            continue
        distance = min(abs(context.start() - action_end), abs(context.end() - action_start))
        if distance > 72:
            continue
        nearest = min(
            action_matches,
            key=lambda candidate: min(
                abs(context.start() - candidate[1]), abs(context.end() - candidate[0])
            ),
        )
        if nearest[:2] == (action_start, action_end):
            return True
    return False


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
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 48) : match.start()]
        if (
            not _NEGATED_CLAIM_PREFIX_RE.search(prefix)
            and not _NEGATED_CLAIM_WITHIN_RE.search(match.group(0))
        ):
            return True
    return False


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
