"""Pure helpers for skills route audit and testing policy."""

import re
from typing import Optional


def _skill_test_task(skill: dict) -> str:
    """Build a self-contained test task for a skill audit run."""
    if not isinstance(skill, dict):
        skill = {}
    ctx = (skill.get("when_to_use") or skill.get("description") or skill.get("name") or "").strip()
    return (
        "Test this skill end-to-end. FIRST, set up a small realistic scenario it "
        "applies to - create any sample input it needs (e.g. a short document, a "
        "note, sample data). Do NOT ask the user for input; invent a plausible "
        "example yourself. THEN apply the skill fully to that example and show the "
        "result. Context for when this skill is used: " + (ctx or "(general)")
    )


def _should_check_retrieval_precision(skill: dict) -> bool:
    """Cheap prefilter for the expensive retrieval-precision judge."""
    broad = {
        "arch", "arch linux", "linux", "network", "networking", "wifi",
        "installation", "install", "system", "ssh", "document", "documents",
        "search", "email", "calendar", "gpu", "server", "python",
    }
    if not isinstance(skill, dict):
        return False
    tags = {str(t or "").strip().lower() for t in (skill.get("tags") or [])}
    if tags & broad:
        return True
    text = " ".join([
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("when_to_use") or ""),
    ]).lower()
    return sum(1 for t in broad if t in text) >= 2


def _audit_flag_text(*parts) -> str:
    text_parts = []
    for part in parts:
        if isinstance(part, dict):
            text_parts.extend(str(v or "") for v in part.values())
        elif isinstance(part, (list, tuple, set)):
            text_parts.extend(str(v or "") for v in part)
        else:
            text_parts.append(str(part or ""))
    return " ".join(text_parts).lower()


def _audit_generic_blocker(skill: Optional[dict], necessity: Optional[dict],
                           verdict_data: Optional[dict]) -> Optional[str]:
    """Return a short reason when a generic/trivial skill must stay draft."""
    generic_re = re.compile(
        r"\b(too[-\s]?generic|generic|trivial|capable assistant|without a saved|"
        r"not need|unnecessary|irrelevant)\b",
        re.I,
    )
    if isinstance(necessity, dict):
        reason = str(necessity.get("reason") or "")
        if necessity.get("necessary") is False and generic_re.search(reason):
            return reason or "Generic or unnecessary skill"

    if isinstance(skill, dict):
        tag_text = _audit_flag_text(skill.get("tags") or [])
        if generic_re.search(tag_text):
            return "Skill is tagged generic"

    if isinstance(verdict_data, dict):
        verdict_text = _audit_flag_text(
            verdict_data.get("summary"),
            verdict_data.get("issues") or [],
        )
        if generic_re.search(verdict_text):
            return "Audit flagged the skill as generic or unnecessary"
    return None
