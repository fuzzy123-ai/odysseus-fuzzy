from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


REFLECTOR_SYSTEM_PROMPT = (
    "You are the teacher-backed reflector for an orchestrator agent. Your job "
    "is to assess whether the orchestrator is still moving toward the user's "
    "goal. You do not execute tools, mutate state, or take over the task. "
    "Return only compact JSON with keys: status, assessment, risks, next_step, "
    "state_doc_note."
)


async def run_reflector_assessment(
    *,
    owner: Optional[str],
    user_request: str,
    state_doc_content: str,
    actions_snapshot: str,
    trigger: str,
    round_num: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    teacher = _resolve_teacher(owner=owner)
    if teacher is None:
        return None
    url, model, headers, teacher_spec = teacher
    messages = [
        {"role": "system", "content": REFLECTOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"<trigger>{_trim(trigger, 200)}</trigger>\n"
                f"<round>{round_num if round_num is not None else ''}</round>\n\n"
                f"<user_request>\n{_trim(user_request, 3000)}\n</user_request>\n\n"
                f"<state_doc>\n{_trim(state_doc_content, 6000)}\n</state_doc>\n\n"
                f"<actions_snapshot>\n{_trim(actions_snapshot, 5000)}\n</actions_snapshot>\n\n"
                "Assess progress vs. goal, call out drift or risk, and recommend "
                "one concrete next orchestration step. Return JSON exactly like: "
                "{\"status\":\"ok|risk|blocked\",\"assessment\":\"...\","
                "\"risks\":[\"...\"],\"next_step\":\"...\",\"state_doc_note\":\"...\"}"
            ),
        },
    ]
    try:
        from src.llm_core import llm_call_async

        raw = await llm_call_async(
            url,
            model,
            messages,
            headers=headers,
            temperature=0.0,
            max_tokens=700,
            timeout=90,
            owner=owner,
            surface="reflector",
            prompt_type="orchestrator_reflector",
        )
    except Exception as exc:
        logger.warning("reflector teacher call failed (%s): %s", teacher_spec, exc)
        return None

    parsed = _parse_reflector_json(raw)
    parsed["teacher_model"] = teacher_spec
    parsed["trigger"] = trigger
    if round_num is not None:
        parsed["round"] = round_num
    return parsed


def _resolve_teacher(*, owner: Optional[str]) -> Optional[tuple[str, str, Dict[str, str], str]]:
    try:
        from src.ai_interaction import _resolve_model
        from src.settings import get_setting

        teacher_spec = str(get_setting("teacher_model", "") or "").strip()
        if not teacher_spec:
            return None
        url, model, headers = _resolve_model(teacher_spec, owner=owner)
        return url, model, headers or {}, teacher_spec
    except Exception as exc:
        logger.warning("reflector teacher model not available: %s", exc)
        return None


def _parse_reflector_json(raw: str) -> Dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE).strip()
    parsed: Dict[str, Any] = {}
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            parsed = candidate
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                candidate = json.loads(match.group(0))
                if isinstance(candidate, dict):
                    parsed = candidate
            except (TypeError, ValueError):
                parsed = {}

    if not parsed:
        parsed = {
            "status": "ok" if text else "blocked",
            "assessment": text[:1200],
            "risks": [],
            "next_step": "",
            "state_doc_note": text[:800],
        }

    status = str(parsed.get("status") or "ok").strip().lower()
    if status not in {"ok", "risk", "blocked"}:
        status = "ok"
    risks = parsed.get("risks")
    if not isinstance(risks, list):
        risks = []
    return {
        "status": status,
        "assessment": str(parsed.get("assessment") or "").strip(),
        "risks": [str(item).strip() for item in risks if str(item).strip()],
        "next_step": str(parsed.get("next_step") or "").strip(),
        "state_doc_note": str(parsed.get("state_doc_note") or "").strip(),
    }


def _trim(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[truncated]"
