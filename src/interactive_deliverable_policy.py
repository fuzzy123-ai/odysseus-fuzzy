"""Deterministic deliverable selection for interactive games and GUI artifacts.

The policy intentionally keeps the request text out of the returned decision.
Only bounded booleans, counts, enums, and fixed reason codes are suitable for
audit persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


INTERACTIVE_DELIVERABLE_POLICY_SCHEMA = "odysseus.interactive_deliverable_policy.v1"
_MAX_REQUEST_CHARS = 12_000
_MAX_REASON_CODES = 8

_NATIVE_SIGNAL_RE = re.compile(
    r"(?:"
    r"\bpygame(?:-ce)?\b|"
    r"\b(?:tkinter|pyqt\d*|pyside\d*|wxpython|kivy|pyglet|arcade)\b|"
    r"\b(?:native|desktop)\s+(?:app(?:lication)?|game|gui|window)\b|"
    r"\bdesktop\s*[- ]\s*(?:app|anwendung|spiel|gui|fenster)\b|"
    r"\bnativ(?:e|er|es|en)?\s+(?:app|anwendung|spiel|gui|fenster)\b|"
    r"\bnativ(?:e|er|es|en)?\s+desktop\s*[- ]\s*(?:app|anwendung|spiel|gui|fenster)\b|"
    r"\b(?:python|py)\s*[- ]?(?:file|datei)\b|"
    r"\b(?:downloadable|herunterladbar(?:e|er|es|en)?)\s+(?:python|py|\.py)\b|"
    r"\b(?:python|py|\.py)\s+(?:download|herunterladen)\b"
    r")",
    re.IGNORECASE,
)
_BROWSER_SIGNAL_RE = re.compile(
    r"(?:"
    r"\b(?:web\s*)?browser\b|"
    r"\b(?:html5?|canvas|javascript|typescript|webgl|wasm|webassembly)\b|"
    r"\b(?:web|browser)\s*[- ]?(?:game|spiel|app|version|preview|vorschau)\b|"
    r"\b(?:in|inside)\s+(?:the\s+)?browser\b|"
    r"\bim\s+browser\b|"
    r"\b(?:play|run|test)\s+(?:it\s+)?here\b|"
    r"\b(?:hier|direkt\s+hier)\s+(?:spielen|ausfuehren|ausführen|testen)\b|"
    r"\b(?:hier|im\s+chat)\s+spielbar\b|"
    r"\b(?:spiel|game|test|preview|vorschau|browser|web)\s*[- ]?link\b"
    r")",
    re.IGNORECASE,
)
_GENERIC_LINK_RE = re.compile(r"\blink\b", re.IGNORECASE)
_DOWNLOAD_LINK_RE = re.compile(r"\bdownload\s*[- ]?link\b", re.IGNORECASE)
_INTERACTIVE_RE = re.compile(
    r"\b(?:"
    r"game|spiel|platformer|jump\s*(?:and|&)\s*run|jump\s*['-]?n\s*run|"
    r"maze|puzzle|racing|simulator|gui|interface|interactive|interaktiv|"
    r"playable|spielbar|arcade"
    r")\b",
    re.IGNORECASE,
)
_NEGATION_PREFIX_RE = re.compile(
    r"(?:\b(?:no|not|without|kein|keine|keinen|keinem|keiner|ohne)\b|"
    r"\b(?:do\s+not|don't|dont)\b)(?:\s+\w+){0,4}\s*$",
    re.IGNORECASE,
)


class InteractiveDeliverablePolicyError(ValueError):
    """Raised when an interactive deliverable request is invalid."""


class InteractiveDeliverableTarget(StrEnum):
    NATIVE_DOWNLOAD = "native_download"
    BROWSER_PREVIEW = "browser_preview"
    DUAL = "dual"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class InteractiveDeliverableDecision:
    target: InteractiveDeliverableTarget
    native_requested: bool
    browser_requested: bool
    interactive_detected: bool
    prompt_chars: int
    reason_codes: tuple[str, ...]
    schema: str = INTERACTIVE_DELIVERABLE_POLICY_SCHEMA

    @property
    def deliverable(self) -> InteractiveDeliverableTarget:
        """Compatibility-friendly name for callers building an artifact plan."""

        return self.target

    def audit_summary(self) -> dict[str, Any]:
        """Return bounded audit data without retaining the raw request."""

        return {
            "schema": self.schema,
            "target": self.target.value,
            "native_requested": self.native_requested,
            "browser_requested": self.browser_requested,
            "interactive_detected": self.interactive_detected,
            "prompt_chars": self.prompt_chars,
            "reason_codes": self.reason_codes[:_MAX_REASON_CODES],
            "raw_prompt_visible": False,
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.audit_summary()


def decide_interactive_deliverable(request_text: Any) -> InteractiveDeliverableDecision:
    """Choose the safest useful artifact format from explicit request signals.

    Native and browser signals together produce a dual deliverable. An
    interactive request with no explicit platform defaults to a browser preview
    so it can be exercised where the conversation is already taking place.
    """

    text = str(request_text or "")
    if not text.strip():
        raise InteractiveDeliverablePolicyError("request_text must not be empty")
    if len(text) > _MAX_REQUEST_CHARS:
        raise InteractiveDeliverablePolicyError(
            f"request_text exceeds max length {_MAX_REQUEST_CHARS}"
        )

    native_requested = _has_non_negated_signal(_NATIVE_SIGNAL_RE, text)
    browser_requested = _has_non_negated_signal(_BROWSER_SIGNAL_RE, text)
    if not browser_requested:
        browser_requested = _has_generic_browser_link_signal(text)
    interactive_detected = bool(_INTERACTIVE_RE.search(text))

    reasons: list[str] = []
    if native_requested:
        reasons.append("explicit_native_request")
    if browser_requested:
        reasons.append("explicit_browser_request")
    if interactive_detected:
        reasons.append("interactive_artifact_detected")

    if native_requested and browser_requested:
        target = InteractiveDeliverableTarget.DUAL
        reasons.append("dual_delivery_required")
    elif native_requested:
        target = InteractiveDeliverableTarget.NATIVE_DOWNLOAD
        reasons.append("native_download_required")
    elif browser_requested:
        target = InteractiveDeliverableTarget.BROWSER_PREVIEW
        reasons.append("browser_preview_required")
    elif interactive_detected:
        target = InteractiveDeliverableTarget.BROWSER_PREVIEW
        reasons.append("ambiguous_interactive_defaults_to_browser")
    else:
        target = InteractiveDeliverableTarget.NOT_APPLICABLE
        reasons.append("no_interactive_deliverable_signal")

    return InteractiveDeliverableDecision(
        target=target,
        native_requested=native_requested,
        browser_requested=browser_requested,
        interactive_detected=interactive_detected,
        prompt_chars=len(text),
        reason_codes=tuple(reasons[:_MAX_REASON_CODES]),
    )


def choose_interactive_deliverable(request_text: Any) -> InteractiveDeliverableDecision:
    """Alias with imperative naming for orchestration callers."""

    return decide_interactive_deliverable(request_text)


def _has_non_negated_signal(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 56) : match.start()]
        if not _NEGATION_PREFIX_RE.search(prefix):
            return True
    return False


def _has_generic_browser_link_signal(text: str) -> bool:
    for match in _GENERIC_LINK_RE.finditer(text):
        prefix = text[max(0, match.start() - 20) : match.start()]
        if _DOWNLOAD_LINK_RE.search(text[max(0, match.start() - 10) : match.end()]):
            continue
        if _NEGATION_PREFIX_RE.search(prefix):
            continue
        return True
    return False


__all__ = [
    "INTERACTIVE_DELIVERABLE_POLICY_SCHEMA",
    "InteractiveDeliverableDecision",
    "InteractiveDeliverablePolicyError",
    "InteractiveDeliverableTarget",
    "choose_interactive_deliverable",
    "decide_interactive_deliverable",
]
