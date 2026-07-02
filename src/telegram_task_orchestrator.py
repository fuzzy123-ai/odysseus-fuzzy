"""Trusted Telegram task orchestration contracts.

This module turns Telegram runtime metadata into long-running task intents. It
is metadata-first: raw Telegram messages may be inspected in memory for intent
classification, but the returned task intent must not persist raw text, chat
ids, file ids, secrets, or host paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping
from urllib.parse import urlparse


TELEGRAM_TASK_INTENT_SCHEMA = "odysseus.telegram_task_intent.v1"

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_FORBIDDEN_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "token=",
    "chat_id",
    "file_id",
    "private raw text",
)


class TelegramTaskOrchestratorError(ValueError):
    """Raised when a Telegram task intent would be unsafe."""


@dataclass(frozen=True, slots=True)
class TelegramTaskIntent:
    task_type: str
    operator_channel: str
    message_kind: str
    workflow_intent: str
    target_kind: str
    target_ref: str
    target_status: str
    requested_output: str
    gates_required: tuple[str, ...]
    status: str
    correlation_id: str
    raw_content_visible: bool = False
    schema: str = TELEGRAM_TASK_INTENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "task_type": self.task_type,
            "operator_channel": self.operator_channel,
            "message_kind": self.message_kind,
            "workflow_intent": self.workflow_intent,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "target_status": self.target_status,
            "requested_output": self.requested_output,
            "gates_required": self.gates_required,
            "status": self.status,
            "correlation_id": self.correlation_id,
            "raw_content_visible": self.raw_content_visible,
        }
        _reject_forbidden_payload(payload)
        return payload


def build_telegram_task_intent(
    message: Mapping[str, Any],
    *,
    workflow_context: Mapping[str, Any] | None = None,
    default_page_cap: int = 50,
    default_depth: int = 3,
) -> TelegramTaskIntent:
    """Build a safe long-running task intent from Telegram metadata."""

    if not isinstance(message, Mapping):
        raise TelegramTaskOrchestratorError("telegram message must be a mapping")
    context = workflow_context if isinstance(workflow_context, Mapping) else {}
    _reject_untrusted_context(context)

    text = str(message.get("text") or "")
    message_kind = _safe_token(message.get("kind") or context.get("message_kind") or "unknown", "message_kind")
    workflow_intent = _safe_token(context.get("intent") or _classify_workflow_intent(text), "workflow_intent", allow_empty=True)
    task_type = _task_type_for_intent(workflow_intent)
    if task_type == "coding_agent_task":
        target = _repo_target_from_text(text)
        target_kind = "repo"
        target_status = "ready" if target else "needs_repo_resolution"
    else:
        target = _target_from_text(text)
        target_kind = "website" if task_type in {"website_research_to_memory", "website_research"} else "none"
        target_status = "ready" if target else ("needs_target_resolution" if _looks_like_site_research(text) else "not_required")
    requested_output = _requested_output_for_intent(workflow_intent, text)
    gates = _gates_for_task(task_type=task_type, target_status=target_status)
    status = "waiting_for_gate" if gates else "ready"
    correlation_seed = {
        "channel": "telegram",
        "message_kind": message_kind,
        "workflow_intent": workflow_intent,
        "task_type": task_type,
        "target": target,
        "page_cap": _safe_cap(default_page_cap, default_value=50),
        "depth": _safe_cap(default_depth, default_value=3),
    }
    return TelegramTaskIntent(
        task_type=task_type,
        operator_channel="telegram",
        message_kind=message_kind,
        workflow_intent=workflow_intent,
        target_kind=target_kind,
        target_ref=target,
        target_status=target_status,
        requested_output=requested_output,
        gates_required=gates,
        status=status,
        correlation_id=_stable_id(correlation_seed),
    )


def build_telegram_task_status_message(intent: TelegramTaskIntent | Mapping[str, Any]) -> str:
    """Return a concise Telegram-safe status line for a task intent."""

    payload = intent.to_dict() if isinstance(intent, TelegramTaskIntent) else dict(intent)
    _reject_forbidden_payload(payload)
    task_type = _safe_token(payload.get("task_type") or "unknown", "task_type")
    status = _safe_token(payload.get("status") or "unknown", "status")
    target_status = _safe_token(payload.get("target_status") or "", "target_status", allow_empty=True)
    gates = tuple(str(item) for item in payload.get("gates_required") or ())
    if target_status == "needs-target-resolution":
        return "Task erkannt: Website-Analyse. Mir fehlt noch ein freigegebener Ziel-Link oder eine Domain."
    if target_status == "needs-repo-resolution":
        return "Task erkannt: Coding-Agent. Mir fehlt noch ein freigegebenes Projekt/Repo und der erlaubte Scope."
    if status == "waiting-for-gate" or gates:
        gate_text = ", ".join(gates[:3])
        return f"Task erkannt: {task_type}. Warte auf Gate: {gate_text}."
    if status == "ready":
        return f"Task bereit: {task_type}."
    return f"Task-Status: {status}."


def _classify_workflow_intent(text: str) -> str:
    normalized = text.lower()
    if _looks_like_coding_task(normalized):
        return "coding-agent-task"
    if _looks_like_site_research(normalized) and any(term in normalized for term in ("gedaechtnis", "gedächtnis", "memory", "raptor")):
        return "bounded-site-research-to-memory"
    if _looks_like_site_research(normalized):
        return "bounded-site-research"
    return ""


def _looks_like_site_research(text: str) -> bool:
    normalized = text.lower()
    web_terms = ("http://", "https://", "homepage", "website", "webseite", "seite", "hilfeseite")
    research_terms = ("analys", "recherch", "untersuch", "crawl", "zusammenfass")
    return any(term in normalized for term in web_terms) and any(term in normalized for term in research_terms)


def _looks_like_coding_task(text: str) -> bool:
    normalized = text.lower()
    coding_terms = ("baue", "implement", "code", "feature", "fix", "bug", "teste", "pytest", "repo", "projekt", "project")
    action_terms = ("mach", "baue", "implement", "fix", "teste", "pruef", "prüf", "aendere", "ändere")
    return any(term in normalized for term in coding_terms) and any(term in normalized for term in action_terms)


def _task_type_for_intent(intent: str) -> str:
    normalized = intent.replace("_", "-")
    if normalized in {"coding-agent-task", "coding-task", "autonomous-coding"}:
        return "coding_agent_task"
    if normalized in {"bounded-site-research-to-memory", "web-research-to-memory"}:
        return "website_research_to_memory"
    if normalized == "bounded-site-research":
        return "website_research"
    return "chat_followup"


def _requested_output_for_intent(intent: str, text: str) -> str:
    normalized = f"{intent} {text}".lower()
    if _looks_like_coding_task(normalized):
        return "sandbox_coding_task"
    if any(term in normalized for term in ("gedaechtnis", "gedächtnis", "memory", "raptor")):
        return "memory_and_raptorgraph_candidates"
    if any(term in normalized for term in ("vergleich", "unterschied", "compare")):
        return "comparison_summary"
    return "operator_summary"


def _target_from_text(text: str) -> str:
    match = _URL_RE.search(text)
    if not match:
        if "asv" in text.lower() and "bw" in text.lower():
            return "domain:asv-bw.de"
        return ""
    parsed = urlparse(match.group(0).rstrip(".,;"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    if not re.fullmatch(r"[a-z0-9.-]{1,253}(:[0-9]{1,5})?", host):
        return ""
    return f"{parsed.scheme}://{host}/"


def _repo_target_from_text(text: str) -> str:
    match = re.search(r"\b(?:repo|projekt|project)\s+([A-Za-z0-9_.-]{2,80})", str(text or ""), re.IGNORECASE)
    if not match:
        return ""
    return "repo:" + _safe_token(match.group(1), "repo")


def _gates_for_task(*, task_type: str, target_status: str) -> tuple[str, ...]:
    gates: list[str] = []
    if task_type in {"website_research", "website_research_to_memory"}:
        if target_status == "needs_target_resolution":
            gates.append("target_resolution")
        gates.append("live_web_target_approval")
    if task_type == "website_research_to_memory":
        gates.append("memory_write_policy")
    if task_type == "coding_agent_task":
        if target_status == "needs_repo_resolution":
            gates.append("repo_resolution")
        gates.extend(["coding_task_scope_review", "sandbox_execution_policy"])
    return tuple(dict.fromkeys(gates))


def _safe_cap(value: Any, *, default_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default_value
    return max(1, min(parsed, 500))


def _stable_id(payload: Mapping[str, Any]) -> str:
    encoded = repr(sorted(payload.items())).encode("utf-8", errors="replace")
    return "tg_task_" + hashlib.sha256(encoded).hexdigest()[:16]


def _safe_token(value: Any, field: str, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        if allow_empty:
            return ""
        raise TelegramTaskOrchestratorError(f"{field} must not be empty")
    if not _SAFE_TOKEN_RE.fullmatch(text):
        raise TelegramTaskOrchestratorError(f"{field} must be a safe token")
    return text


def _reject_untrusted_context(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"text", "prompt", "content", "raw_text", "chat_id", "file_id", "path", "token", "secret"}
    for key, value in payload.items():
        key_text = str(key).strip().lower()
        if key_text in forbidden_keys:
            raise TelegramTaskOrchestratorError(f"workflow context contains untrusted field: {key_text}")
        if isinstance(value, Mapping):
            _reject_untrusted_context(value)


def _reject_forbidden_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in _FORBIDDEN_MARKERS):
        raise TelegramTaskOrchestratorError("telegram task payload contains forbidden content marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise TelegramTaskOrchestratorError("telegram task payload contains a host path")
