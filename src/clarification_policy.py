"""Deterministic materiality and completeness policy for clarification intake."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from src.clarification_contract import (
    CLARIFICATION_POLICY_REVIEW_SCHEMA,
    MATERIAL_DIMENSIONS,
    MATERIAL_DIMENSION_KEYS,
    REQUIRED_MATERIAL_DIMENSION_KEYS,
)


DEFAULT_VISIBLE_QUESTION_BUDGET = 7
DEFAULT_TOTAL_QUESTION_BUDGET = 50
DEFAULT_ROUND_BUDGET = 8

_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_PRIVATE_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")
_WORD_RE = re.compile(r"[a-z0-9]+")


def evaluate_clarification_completeness(
    *,
    intent_text: Any,
    scope: str = "project",
    known_answers: Mapping[str, Any] | None = None,
    candidate_questions: Sequence[Mapping[str, Any]] | None = None,
    max_visible_questions: int = DEFAULT_VISIBLE_QUESTION_BUDGET,
    max_total_questions: int = DEFAULT_TOTAL_QUESTION_BUDGET,
    max_rounds: int = DEFAULT_ROUND_BUDGET,
) -> dict[str, Any]:
    """Return a bounded server-side review before a plan may be created."""

    safe_intent = _safe_text(intent_text, max_len=1200, field="intent_text")
    safe_scope = scope if scope in {"conversation", "project", "coding_task"} else "project"
    answers = _safe_answers(known_answers or {})
    missing_required = tuple(key for key in REQUIRED_MATERIAL_DIMENSION_KEYS if not _has_answer(answers, key))
    missing_optional = tuple(key for key in MATERIAL_DIMENSION_KEYS if key not in REQUIRED_MATERIAL_DIMENSION_KEYS and not _has_answer(answers, key))
    accepted, rejected = _filter_candidate_questions(
        candidate_questions or (),
        answers=answers,
        missing_keys=set(missing_required) | set(missing_optional),
        max_total_questions=max_total_questions,
    )
    accepted_visible = accepted[: _bounded_int(max_visible_questions, 1, 20)]
    ready = not missing_required and not any(item.get("required") for item in accepted)
    return {
        "schema": CLARIFICATION_POLICY_REVIEW_SCHEMA,
        "scope": safe_scope,
        "intent_summary": safe_intent,
        "requires_clarification": not ready,
        "ready_for_understanding_review": ready,
        "missing_required_fields": list(missing_required),
        "missing_optional_fields": list(missing_optional),
        "accepted_questions": accepted_visible,
        "accepted_question_count": len(accepted),
        "rejected_questions": rejected,
        "budgets": {
            "max_visible_questions": _bounded_int(max_visible_questions, 1, 20),
            "max_total_questions": _bounded_int(max_total_questions, 1, 200),
            "max_rounds": _bounded_int(max_rounds, 1, 30),
        },
        "policy": {
            "materiality_required": True,
            "duplicates_rejected": True,
            "answered_questions_rejected": True,
            "unsafe_content_rejected": True,
            "model_is_not_sole_judge": True,
        },
        "raw_content_visible": False,
    }


def build_deterministic_questions_for_missing_fields(missing_fields: Sequence[str]) -> tuple[dict[str, Any], ...]:
    """Build fallback questions when model-generated questions are unusable."""

    by_key = {item["key"]: item for item in MATERIAL_DIMENSIONS}
    questions: list[dict[str, Any]] = []
    for key in missing_fields[:DEFAULT_VISIBLE_QUESTION_BUDGET]:
        dimension = by_key.get(str(key))
        if not dimension:
            continue
        questions.append(
            {
                "key": dimension["key"],
                "type": "long_text" if dimension["key"] in {"outcome", "scope", "acceptance_criteria"} else "short_text",
                "prompt": f"Please clarify {dimension['label'].lower()}.",
                "required": bool(dimension["required"]),
                "reason": dimension["reason"],
                "category": dimension["key"],
            }
        )
    return tuple(questions)


def is_duplicate_question(candidate: Mapping[str, Any], previous_questions: Sequence[Mapping[str, Any]]) -> bool:
    key = str(candidate.get("key") or "").strip().lower()
    prompt = _normalize_prompt(candidate.get("prompt") or candidate.get("question") or "")
    for previous in previous_questions:
        previous_key = str(previous.get("key") or "").strip().lower()
        if key and previous_key and key == previous_key:
            return True
        if _prompt_similarity(prompt, _normalize_prompt(previous.get("prompt") or previous.get("question") or "")) >= 0.82:
            return True
    return False


def _filter_candidate_questions(
    candidates: Sequence[Mapping[str, Any]],
    *,
    answers: Mapping[str, Any],
    missing_keys: set[str],
    max_total_questions: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    total_budget = _bounded_int(max_total_questions, 1, 200)
    for raw in candidates:
        item = dict(raw)
        key = str(item.get("key") or item.get("category") or "").strip().lower()
        prompt = str(item.get("prompt") or item.get("question") or "").strip()
        reject_reason = ""
        if not key or not prompt:
            reject_reason = "invalid_question"
        elif _unsafe_text(key) or _unsafe_text(prompt) or _unsafe_text(item.get("reason", "")):
            reject_reason = "unsafe_content"
        elif key not in MATERIAL_DIMENSION_KEYS:
            reject_reason = "non_material"
        elif _has_answer(answers, key):
            reject_reason = "already_answered"
        elif key not in missing_keys and item.get("required"):
            reject_reason = "not_missing"
        elif is_duplicate_question(item, accepted):
            reject_reason = "duplicate"
        elif len(accepted) >= total_budget:
            reject_reason = "budget_exceeded"
        if reject_reason:
            rejected.append({"key": key, "prompt": prompt[:240], "reason": reject_reason})
            continue
        accepted.append(
            {
                "key": key,
                "type": str(item.get("type") or "short_text"),
                "prompt": _safe_text(prompt, max_len=1000, field="question.prompt"),
                "required": bool(item.get("required", key in REQUIRED_MATERIAL_DIMENSION_KEYS)),
                "reason": _safe_text(item.get("reason") or "The answer changes the plan.", max_len=500, field="question.reason"),
                "category": key,
            }
        )
    return accepted, rejected


def _safe_answers(answers: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in answers.items():
        safe_key = str(key or "").strip().lower()
        if safe_key in MATERIAL_DIMENSION_KEYS and not _unsafe_text(value):
            safe[safe_key] = value
    return safe


def _has_answer(answers: Mapping[str, Any], key: str) -> bool:
    value = answers.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _safe_text(value: Any, *, max_len: int, field: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > max_len:
        raise ValueError(f"{field} is too long")
    if _unsafe_text(text):
        raise ValueError(f"{field} contains unsafe content")
    return text


def _unsafe_text(value: Any) -> bool:
    text = repr(value)
    return bool(_SECRET_RE.search(text) or _PRIVATE_PATH_RE.search(text))


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return min(max(parsed, minimum), maximum)


def _normalize_prompt(value: Any) -> tuple[str, ...]:
    return tuple(word for word in _WORD_RE.findall(str(value or "").lower()) if len(word) > 2)


def _prompt_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(len(left_set | right_set), 1)
