"""Pure UI-control marker handlers for agent tools."""

import json
import hashlib
import re
from typing import Any


_CLARIFICATION_SCHEMA = "odysseus.clarification_request.v2"
_QUESTION_TYPES = {
    "single_select",
    "multi_select",
    "boolean",
    "short_text",
    "long_text",
    "number",
    "date",
    "resource_ref",
}
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_PRIVATE_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")


def handle_ask_user_marker(content: str) -> tuple[str, dict[str, Any]]:
    """Return the ask-user marker payload for the agent loop/frontend bridge."""
    raw = (content or "").strip()
    try:
        parsed = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        parsed = {}
    if isinstance(parsed, dict) and parsed.get("schema") == _CLARIFICATION_SCHEMA:
        return _handle_clarification_request(parsed)
    question, options, multi = "", [], False
    if isinstance(parsed, dict):
        question = str(parsed.get("question", "")).strip()
        multi = bool(parsed.get("multi") or parsed.get("multiSelect"))
        for opt in parsed.get("options") or []:
            if isinstance(opt, dict):
                label = str(opt.get("label", "")).strip()
                descr = str(opt.get("description", "")).strip()
            elif isinstance(opt, str):
                label, descr = opt.strip(), ""
            else:
                continue
            if label:
                options.append({"label": label, "description": descr})
    else:
        question = raw
    if not question or len(options) < 2:
        return "ask_user: invalid", {
            "error": (
                "ask_user needs a non-empty `question` and at least 2 `options` "
                "(each an object with a `label`, optional `description`) or a valid "
                "`odysseus.clarification_request.v2` payload."
            ),
            "exit_code": 1,
        }
    unsafe = _unsafe_text(question) or any(_unsafe_text(item.get("label")) or _unsafe_text(item.get("description")) for item in options)
    if unsafe:
        return "ask_user: invalid", {"error": "ask_user payload contains secret, private path, or unsafe content.", "exit_code": 1}
    options = options[:6]
    clarification_request = _legacy_to_clarification_request(question=question, options=options, multi=multi)
    desc = f"ask_user: {question[:80]}"
    labels = ", ".join(o["label"] for o in options)
    return desc, {
        "ask_user": {"question": question, "options": options, "multi": multi},
        "clarification_request": clarification_request,
        "output": f"Asked the user: {question}\nOptions: {labels}\nAwaiting their selection.",
        "exit_code": 0,
    }


def _handle_clarification_request(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        request = _normalize_clarification_request(payload)
        ask_payload = _legacy_card_from_request(request)
    except ValueError as exc:
        return "ask_user: invalid", {"error": str(exc), "exit_code": 1}
    desc = f"ask_user: {request['intent_summary'][:80]}"
    return desc, {
        "ask_user": ask_payload,
        "clarification_request": request,
        "output": (
            f"Opened clarification run: {request['intent_summary']}\n"
            f"Questions: {len(request['questions'])}; batch {request['batch']['index']}/{request['batch']['total']}.\n"
            "Awaiting structured user input."
        ),
        "exit_code": 0,
    }


def _legacy_to_clarification_request(*, question: str, options: list[dict[str, str]], multi: bool) -> dict[str, Any]:
    key = "q_" + hashlib.sha256(question.encode("utf-8", errors="replace")).hexdigest()[:16]
    return {
        "schema": _CLARIFICATION_SCHEMA,
        "scope": "conversation",
        "intent_summary": question[:1000],
        "questions": [
            {
                "key": key,
                "type": "multi_select" if multi else "single_select",
                "prompt": question,
                "required": True,
                "reason": "The answer changes what the assistant should do next.",
                "category": "decision",
                "options": options[:6],
            }
        ],
        "batch": {"label": "Clarification", "index": 1, "total": 1, "max_visible_questions": 1},
        "defaults_visible": False,
    }


def _normalize_clarification_request(payload: dict[str, Any]) -> dict[str, Any]:
    scope = str(payload.get("scope") or "").strip()
    if scope not in {"conversation", "project", "coding_task"}:
        raise ValueError("clarification_request scope must be conversation, project, or coding_task")
    intent = _safe_text(payload.get("intent_summary"), field="intent_summary", max_len=1000)
    raw_questions = payload.get("questions") or ()
    if not isinstance(raw_questions, list) or len(raw_questions) > 100:
        raise ValueError("clarification_request questions must be a list with at most 100 items")
    questions = [_normalize_question(item) for item in raw_questions]
    keys = {item["key"] for item in questions}
    for item in questions:
        dependency = item.get("depends_on")
        if dependency and dependency not in keys:
            raise ValueError("clarification_request depends_on references an unknown question key")
    raw_batch = payload.get("batch") if isinstance(payload.get("batch"), dict) else {}
    batch = {
        "label": _safe_text(raw_batch.get("label") or "Clarification", field="batch.label", max_len=120),
        "index": _bounded_int(raw_batch.get("index"), field="batch.index", minimum=1, maximum=1000),
        "total": _bounded_int(raw_batch.get("total"), field="batch.total", minimum=1, maximum=1000),
        "max_visible_questions": _bounded_int(raw_batch.get("max_visible_questions"), field="batch.max_visible_questions", minimum=1, maximum=10),
    }
    return {
        "schema": _CLARIFICATION_SCHEMA,
        "scope": scope,
        "intent_summary": intent,
        "questions": questions,
        "batch": batch,
        "defaults_visible": bool(payload.get("defaults_visible")),
    }


def _normalize_question(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("clarification_request questions must contain objects")
    key = _question_key(payload.get("key"))
    qtype = str(payload.get("type") or "").strip()
    if qtype not in _QUESTION_TYPES:
        raise ValueError("clarification_request question type is invalid")
    question = {
        "key": key,
        "type": qtype,
        "prompt": _safe_text(payload.get("prompt"), field="question.prompt", max_len=1000),
        "required": bool(payload.get("required")),
        "reason": _safe_text(payload.get("reason"), field="question.reason", max_len=500),
    }
    category = _safe_text(payload.get("category") or "", field="question.category", max_len=80, allow_empty=True)
    if category:
        question["category"] = category
    raw_options = payload.get("options") or []
    if raw_options:
        if not isinstance(raw_options, list) or len(raw_options) > 20:
            raise ValueError("clarification_request question options must be a list with at most 20 items")
        question["options"] = [_normalize_option(item) for item in raw_options]
    if qtype in {"single_select", "multi_select"} and len(question.get("options") or ()) < 2:
        raise ValueError("select clarification questions need at least two options")
    if payload.get("default") is not None:
        _reject_unsafe(payload.get("default"))
        question["default"] = payload.get("default")
    if payload.get("depends_on"):
        question["depends_on"] = _question_key(payload.get("depends_on"))
    return question


def _normalize_option(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("clarification_request options must contain objects")
    option = {"label": _safe_text(payload.get("label"), field="option.label", max_len=80)}
    description = _safe_text(payload.get("description") or "", field="option.description", max_len=240, allow_empty=True)
    if description:
        option["description"] = description
    if payload.get("recommended") is not None:
        option["recommended"] = bool(payload.get("recommended"))
    return option


def _legacy_card_from_request(request: dict[str, Any]) -> dict[str, Any]:
    first = (request.get("questions") or [{}])[0]
    options = first.get("options") or [
        {"label": "Answer in chat", "description": "Type the requested clarification as your next message."},
        {"label": "Pause", "description": "Keep this clarification open for later."},
    ]
    return {
        "question": first.get("prompt") or request["intent_summary"],
        "options": options[:6],
        "multi": first.get("type") == "multi_select",
        "clarification_schema": _CLARIFICATION_SCHEMA,
    }


def _question_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("_.-")
    if not _KEY_RE.fullmatch(text):
        raise ValueError("clarification_request question key is invalid")
    return text


def _safe_text(value: Any, *, field: str, max_len: int, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    if len(text) > max_len:
        raise ValueError(f"{field} is too long")
    if _unsafe_text(text):
        raise ValueError("ask_user payload contains secret, private path, or unsafe content")
    return text


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer") from None
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} is out of range")
    return parsed


def _reject_unsafe(value: Any) -> None:
    if _unsafe_text(repr(value)):
        raise ValueError("ask_user payload contains secret, private path, or unsafe content")


def _unsafe_text(value: Any) -> bool:
    text = str(value or "")
    return bool(_SECRET_RE.search(text) or _PRIVATE_PATH_RE.search(text))


def handle_update_plan_marker(content: str) -> tuple[str, dict[str, Any]]:
    """Return the plan-update marker payload for the agent loop/frontend bridge."""
    raw = (content or "").strip()
    plan = ""
    try:
        parsed = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        parsed = {}
    if isinstance(parsed, dict) and parsed.get("plan"):
        plan = str(parsed.get("plan", "")).strip()
    else:
        plan = raw
    if not plan:
        return "update_plan: invalid", {
            "error": "update_plan needs a non-empty `plan` (the full updated checklist as markdown).",
            "exit_code": 1,
        }
    plan = plan[:8192]
    done = plan.count("- [x]") + plan.count("- [X]")
    total = done + plan.count("- [ ]")
    desc = f"update_plan: {done}/{total} done" if total else "update_plan"
    output = f"Plan updated ({done}/{total} steps complete)." if total else "Plan updated."
    return desc, {
        "plan_update": {"plan": plan},
        "output": output,
        "exit_code": 0,
    }
