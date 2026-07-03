"""Deterministic pre-1.0 routing policy for simple model eligibility.

This module does not call models, tools, providers, or live services. It only
turns a bounded task description plus trusted metadata into a small routing
decision that can later feed model/tool selection without making hidden
fallback or truth-write claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping


_MAX_TEXT_CHARS = 12_000
_MAX_REASON_CODES = 12
_MAX_REF = 80
_SECRET_PATTERNS = (
    "api_key",
    "authorization:",
    "bearer ",
    "cookie:",
    "password=",
    "secret=",
    "token=",
)
_SIMPLE_RE = re.compile(
    r"\b("
    r"summarize|summary|zusammenfass(?:en|ung)|classify|klassifizier(?:e|en)|"
    r"tag|label|extract|extrahier(?:e|en)|rewrite|umformulieren|"
    r"translate|uebersetz(?:e|en)|übersetz(?:e|en)|kurz|short"
    r")\b",
    re.IGNORECASE,
)
_FOCUSED_EDIT_RE = re.compile(
    r"\b("
    r"focused edit|small edit|tiny edit|one file|single file|"
    r"kleine aenderung|kleine änderung|einzelne datei|nur diese datei"
    r")\b",
    re.IGNORECASE,
)
_STRONG_REASONING_RE = re.compile(
    r"\b("
    r"architecture|architektur|roadmap|plan|strategy|strategie|debug|bug|"
    r"root cause|ursache|in depth|vollstaendig|vollständig|komplett|"
    r"refactor|migration|multi[- ]?file|mehrere dateien"
    r")\b",
    re.IGNORECASE,
)
_TOOL_RE = re.compile(
    r"\b("
    r"run tests?|pytest|execute|ausfuehr(?:en|e)|ausführ(?:en|e)|shell|bash|"
    r"git|commit|push|clone|repo|browser|screenshot|webseite|homepage|"
    r"deploy|cloudflare|nextcloud|telegram|write file|edit file|datei schreiben|"
    r"sandbox|terminal|podman|systemd"
    r")\b",
    re.IGNORECASE,
)


class SimpleTaskRouterError(ValueError):
    """Raised when a routing request is invalid."""


class SimpleTaskRoute(StrEnum):
    MAINTENANCE_MODEL = "maintenance_model"
    STRONG_REASONING = "strong_reasoning"
    TOOL_ORCHESTRATION = "tool_orchestration"
    REVIEW = "review"


class SimpleTaskKind(StrEnum):
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    FOCUSED_EDIT = "focused_edit"
    DEBUGGING = "debugging"
    MULTI_FILE_CODING = "multi_file_coding"
    RESEARCH = "research"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SimpleTaskRoutingDecision:
    route: SimpleTaskRoute
    task_kind: SimpleTaskKind
    eligible_for_small_model: bool
    requires_tool_orchestration: bool
    requires_strong_reasoning: bool
    requires_review: bool
    local_only_required: bool
    token_budget: int
    prompt_chars: int
    reason_codes: tuple[str, ...]
    recommended_next_action: str

    def audit_summary(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "task_kind": self.task_kind.value,
            "eligible_for_small_model": self.eligible_for_small_model,
            "requires_tool_orchestration": self.requires_tool_orchestration,
            "requires_strong_reasoning": self.requires_strong_reasoning,
            "requires_review": self.requires_review,
            "local_only_required": self.local_only_required,
            "token_budget": self.token_budget,
            "prompt_chars": self.prompt_chars,
            "reason_codes": self.reason_codes,
            "recommended_next_action": self.recommended_next_action,
            "raw_prompt_visible": False,
            "raw_content_visible": False,
            "token_value_visible": False,
        }


def route_simple_task(
    task_text: Any,
    *,
    trusted_metadata: Mapping[str, Any] | None = None,
    token_budget: Any = 1200,
    local_only_required: bool = False,
) -> SimpleTaskRoutingDecision:
    text = str(task_text or "")
    if not text.strip():
        raise SimpleTaskRouterError("task_text must not be empty")
    if len(text) > _MAX_TEXT_CHARS:
        raise SimpleTaskRouterError(f"task_text exceeds max length {_MAX_TEXT_CHARS}")
    budget = _positive_int(token_budget, field_name="token_budget")
    metadata = dict(trusted_metadata or {})
    reason_codes: list[str] = []

    task_kind = _classify_task_kind(text)
    if task_kind is not SimpleTaskKind.UNKNOWN:
        reason_codes.append(f"task_kind:{task_kind.value}")

    if _contains_secret_marker(text):
        reason_codes.append("secret_like_text_detected")
        local_only_required = True

    file_count = _non_negative_int(metadata.get("file_count", 0), field_name="file_count")
    attachment_count = _non_negative_int(metadata.get("attachment_count", 0), field_name="attachment_count")
    explicit_tools = bool(metadata.get("requires_tools"))
    live_surface = bool(metadata.get("live_surface"))
    trusted_sensitive = bool(metadata.get("sensitive") or metadata.get("dsgvo_required"))

    requires_tools = explicit_tools or live_surface or bool(_TOOL_RE.search(text))
    requires_strong = bool(_STRONG_REASONING_RE.search(text))
    requires_review = bool(metadata.get("requires_review"))

    if file_count > 1:
        requires_strong = True
        reason_codes.append("multi_file_scope")
    if attachment_count > 3:
        requires_strong = True
        reason_codes.append("many_attachments")
    if budget < 256:
        requires_review = True
        reason_codes.append("token_budget_too_small")
    if trusted_sensitive:
        local_only_required = True
        reason_codes.append("trusted_sensitive_metadata")
    if requires_tools:
        reason_codes.append("tool_signal")
    if requires_strong:
        reason_codes.append("strong_reasoning_signal")
    if requires_review:
        reason_codes.append("review_signal")
    if local_only_required:
        reason_codes.append("local_only_required")

    if requires_review:
        route = SimpleTaskRoute.REVIEW
    elif requires_tools:
        route = SimpleTaskRoute.TOOL_ORCHESTRATION
    elif requires_strong:
        route = SimpleTaskRoute.STRONG_REASONING
    else:
        route = SimpleTaskRoute.MAINTENANCE_MODEL

    eligible = route is SimpleTaskRoute.MAINTENANCE_MODEL and budget >= 256
    return SimpleTaskRoutingDecision(
        route=route,
        task_kind=task_kind,
        eligible_for_small_model=eligible,
        requires_tool_orchestration=route is SimpleTaskRoute.TOOL_ORCHESTRATION,
        requires_strong_reasoning=route is SimpleTaskRoute.STRONG_REASONING,
        requires_review=route is SimpleTaskRoute.REVIEW,
        local_only_required=bool(local_only_required),
        token_budget=budget,
        prompt_chars=len(text),
        reason_codes=_dedupe_reason_codes(reason_codes),
        recommended_next_action=_next_action(route, local_only_required=bool(local_only_required)),
    )


def _classify_task_kind(text: str) -> SimpleTaskKind:
    lowered = text.lower()
    if "debug" in lowered or "bug" in lowered or "root cause" in lowered:
        return SimpleTaskKind.DEBUGGING
    if "multi-file" in lowered or "mehrere dateien" in lowered or "refactor" in lowered:
        return SimpleTaskKind.MULTI_FILE_CODING
    if "webseite" in lowered or "homepage" in lowered or "research" in lowered or "recherche" in lowered:
        return SimpleTaskKind.RESEARCH
    if _FOCUSED_EDIT_RE.search(text):
        return SimpleTaskKind.FOCUSED_EDIT
    if re.search(r"\b(classify|klassifizier|tag|label)\b", text, re.IGNORECASE):
        return SimpleTaskKind.CLASSIFICATION
    if re.search(r"\b(extract|extrahier)\b", text, re.IGNORECASE):
        return SimpleTaskKind.EXTRACTION
    if _SIMPLE_RE.search(text):
        return SimpleTaskKind.SUMMARIZATION
    return SimpleTaskKind.UNKNOWN


def _next_action(route: SimpleTaskRoute, *, local_only_required: bool) -> str:
    if local_only_required and route is SimpleTaskRoute.MAINTENANCE_MODEL:
        return "use_local_maintenance_model"
    if route is SimpleTaskRoute.MAINTENANCE_MODEL:
        return "use_maintenance_model"
    if route is SimpleTaskRoute.TOOL_ORCHESTRATION:
        return "prepare_agent_tool_orchestration"
    if route is SimpleTaskRoute.STRONG_REASONING:
        return "route_to_strong_reasoning"
    return "route_to_review"


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SimpleTaskRouterError(f"{field_name} must be an int") from None
    if number <= 0:
        raise SimpleTaskRouterError(f"{field_name} must be > 0")
    return number


def _non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SimpleTaskRouterError(f"{field_name} must be an int") from None
    if number < 0:
        raise SimpleTaskRouterError(f"{field_name} must be >= 0")
    return number


def _contains_secret_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SECRET_PATTERNS)


def _dedupe_reason_codes(reason_codes: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in reason_codes:
        code = _safe_ref(raw)
        if code in seen:
            continue
        seen.add(code)
        normalized.append(code)
        if len(normalized) >= _MAX_REASON_CODES:
            break
    return tuple(normalized)


def _safe_ref(value: Any) -> str:
    text = re.sub(r"[^a-z0-9_.:-]+", "-", str(value or "").strip().lower()).strip("-")
    return text[:_MAX_REF] or "unknown"
