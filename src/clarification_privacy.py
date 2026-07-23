"""Privacy and runtime boundaries for durable clarification answers."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


SCHEMA_BOUNDARY = "odysseus.clarification_privacy_boundary.v1"
SCHEMA_MEMORY_CANDIDATE = "odysseus.clarification_memory_candidate.v1"
SCHEMA_SECURE_HANDOFF = "odysseus.clarification_secure_handoff_intent.v1"

SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|client[_-]?secret)\b\s*[:=]?\s*\S*")
PRIVATE_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")
PREFERENCE_HINT_RE = re.compile(r"(?i)\b(prefer|preference|default|always|usually|tone|style|language|locale)\b")


def contains_secret_material(value: Any) -> bool:
    return SECRET_RE.search(_flatten(value)) is not None


def contains_private_path_material(value: Any) -> bool:
    return PRIVATE_PATH_RE.search(_flatten(value)) is not None


def build_clarification_privacy_boundary(run: Mapping[str, Any], *, question_id: Any = "") -> dict[str, Any]:
    """Return the explicit non-global boundary for a clarification answer."""

    scope = str(run.get("scope") or "conversation")
    project_slug = str(run.get("project_slug") or "")
    coding_task_id = str(run.get("coding_task_id") or "")
    session_id = str(run.get("session_id") or "")
    owner = str(run.get("owner") or "")
    return {
        "schema": SCHEMA_BOUNDARY,
        "owner": _short(owner, 120),
        "session_id": _short(session_id, 120),
        "project_slug": _short(project_slug, 120),
        "coding_task_id": _short(coding_task_id, 120),
        "scope": scope if scope in {"conversation", "project", "coding_task"} else "conversation",
        "question_id": _safe_id(question_id),
        "answer_storage_scope": "coding_task" if coding_task_id else "project" if project_slug else "session",
        "global_memory_write_allowed": False,
        "raw_content_visible": False,
        "secret_handoff_required": False,
    }


def build_secure_handoff_intent(
    run: Mapping[str, Any],
    *,
    question_id: Any,
    reason: str = "secret_material_detected",
) -> dict[str, Any]:
    """Describe the safe route for secret-bearing answers without the value."""

    boundary = build_clarification_privacy_boundary(run, question_id=question_id)
    boundary["secret_handoff_required"] = True
    return {
        "schema": SCHEMA_SECURE_HANDOFF,
        "status": "secure_handoff_required",
        "reason": _short(reason, 80),
        "clarification_id": _short(run.get("clarification_id") or run.get("id") or "", 120),
        "question_id": _safe_id(question_id),
        "value_visible": False,
        "value_stored": False,
        "recommended_channel": "secure_secret_handoff",
        "boundary": boundary,
    }


def build_memory_candidate_from_answer(
    run: Mapping[str, Any],
    *,
    question: Mapping[str, Any] | None,
    question_id: Any,
    answer: Any,
) -> dict[str, Any] | None:
    """Create a reviewed memory candidate only for stable preference answers."""

    if contains_secret_material(answer) or contains_private_path_material(answer):
        return None
    question_data = question or {}
    category = str(question_data.get("category") or "")
    prompt = str(question_data.get("prompt") or "")
    qid = _safe_id(question_id)
    haystack = f"{qid} {category} {prompt}"
    if not PREFERENCE_HINT_RE.search(haystack):
        return None
    answer_summary = _answer_summary(answer)
    if not answer_summary:
        return None
    boundary = build_clarification_privacy_boundary(run, question_id=qid)
    candidate_id = "clmem-" + hashlib.sha256(
        f"{run.get('clarification_id') or run.get('id')}:{qid}:{answer_summary}".encode("utf-8", errors="replace")
    ).hexdigest()[:24]
    return {
        "schema": SCHEMA_MEMORY_CANDIDATE,
        "candidate_id": candidate_id,
        "status": "proposed",
        "requires_review": True,
        "truth_write_allowed": False,
        "candidate_type": "preference",
        "scope": boundary["answer_storage_scope"],
        "source": {
            "kind": "clarification_answer",
            "clarification_id": _short(run.get("clarification_id") or run.get("id") or "", 120),
            "question_id": qid,
        },
        "summary": answer_summary,
        "boundary": boundary,
    }


def _flatten(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value or "")


def _answer_summary(value: Any) -> str:
    text = " ".join(_flatten(value).strip().split())
    if not text:
        return ""
    text = SECRET_RE.sub("<secret>", text)
    text = PRIVATE_PATH_RE.sub("<private-path>", text)
    return _short(text, 240)


def _safe_id(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("_.-")
    return _short(text, 80)


def _short(value: Any, limit: int) -> str:
    return str(value or "")[: max(0, int(limit))]
