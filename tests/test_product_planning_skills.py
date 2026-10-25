"""Contract tests for the bounded PPS-2 product-planning draft pack."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


for _module in (
    "sqlalchemy",
    "sqlalchemy.orm",
    "sqlalchemy.ext",
    "sqlalchemy.ext.declarative",
):
    if _module not in sys.modules:
        try:
            __import__(_module)
        except ImportError:
            sys.modules[_module] = MagicMock()

from services.memory.skill_format import Skill  # noqa: E402
from services.memory.skills import SkillsManager  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "data" / "skills" / "product-planning"

PACK = {
    "product-pressure-test": {
        "reference": "references/lens-registry.md",
        "positives": (
            "product pressure test for feature scoping",
            "Produktkl\u00e4rung f\u00fcr eine neue Funktion",
        ),
    },
    "lean-implementation-review": {
        "reference": "references/necessity-review.md",
        "positives": (
            "lean implementation review before coding",
            "schlanke Umsetzung pr\u00fcfen",
        ),
    },
    "product-decision-audit": {
        "reference": "references/audit-verdicts.md",
        "positives": (
            "pre-build product decision audit",
            "Entscheidungspr\u00fcfung vor dem Bau",
        ),
    },
}

DIRECT_NEGATORS = ("do not", "does not", "never", "must not", "cannot", "can't")
NEGATOR_AT_CLAUSE_END = re.compile(
    r"(?:do not|does not|never|must not|cannot|can't)\s+$", re.IGNORECASE
)
CLAUSE_BOUNDARY = re.compile(r"[\n;.!?]")

AUTHORITY_FAMILIES = {
    "accept": ("accept", ("accept", "accepts", "accepted", "accepting"), r"(?:a|the) decision"),
    "approve": ("approve", ("approve", "approves", "approved", "approving"), r"(?:a|the) decision"),
    "finalize": ("finalize", ("finalize", "finalizes", "finalized", "finalizing"), r"(?:a|the) decision"),
    "finalise": ("finalise", ("finalise", "finalises", "finalised", "finalising"), r"(?:a|the) decision"),
    "mark_claimable": ("mark", ("mark", "marks", "marked", "marking"), r"(?:a|the) node claimable"),
    "set_claimable": ("set", ("set", "sets", "setting"), r"(?:a|the) node claimable"),
    "claim": ("claim", ("claim", "claims", "claimed", "claiming"), r"verified completion"),
    "declare": ("declare", ("declare", "declares", "declared", "declaring"), r"verified completion"),
    "report": ("report", ("report", "reports", "reported", "reporting"), r"verified completion"),
    "mark_terminal": ("mark", ("mark", "marks", "marked", "marking"), r"(?:a|the) node (?:complete|accepted|verified)"),
    "write": ("write", ("write", "writes", "writing", "wrote", "written"), r"(?:to )?(?:PlanRuntime|PlanGraph|canonical plan state)"),
    "update": ("update", ("update", "updates", "updated", "updating"), r"(?:to )?(?:PlanRuntime|PlanGraph|canonical plan state)"),
    "persist": ("persist", ("persist", "persists", "persisted", "persisting"), r"(?:to )?(?:PlanRuntime|PlanGraph|canonical plan state)"),
    "apply": ("apply", ("apply", "applies", "applied", "applying"), r"(?:to )?(?:PlanRuntime|PlanGraph|canonical plan state)"),
    "commit": ("commit", ("commit", "commits", "committed", "committing"), r"(?:to )?(?:PlanRuntime|PlanGraph|canonical plan state)"),
}

OBJECT_EXAMPLES = {
    "accept": "a decision",
    "approve": "a decision",
    "finalize": "a decision",
    "finalise": "a decision",
    "mark_claimable": "a node claimable",
    "set_claimable": "a node claimable",
    "claim": "verified completion",
    "declare": "verified completion",
    "report": "verified completion",
    "mark_terminal": "a node complete",
    "write": "PlanRuntime",
    "update": "PlanRuntime",
    "persist": "canonical plan state",
    "apply": "PlanGraph",
    "commit": "canonical plan state",
}


def _skill_path(name: str) -> Path:
    return SKILL_ROOT / name / "SKILL.md"


def _read_skill(name: str) -> tuple[Skill, str]:
    path = _skill_path(name)
    assert path.is_file(), f"missing required PPS-2 skill: {path.relative_to(ROOT)}"
    text = path.read_text(encoding="utf-8")
    skill = Skill.from_markdown(text, path=str(path))
    assert skill.name == name
    assert skill.category == "product-planning"
    assert skill.status == "draft"
    assert skill.source == "admin"
    assert skill.owner
    for heading in ("## When to Use", "## Procedure", "## Pitfalls", "## Verification"):
        assert heading in text, f"{name} must retain the {heading[3:]} section"
    return skill, text


def _assert_reference_and_safety(name: str, skill: Skill, text: str) -> None:
    reference = PACK[name]["reference"]
    declared = re.findall(r"\]\((references/[^)]+)\)", text)
    assert declared == [reference], f"{name} must declare exactly its one bounded reference"
    reference_path = _skill_path(name).parent / reference
    assert reference_path.is_file(), f"missing reference for {name}: {reference}"
    reference_text = reference_path.read_text(encoding="utf-8")
    assert reference_text.strip(), f"reference for {name} must not be empty"
    for forbidden in (
        "ignore previous instructions",
        "system prompt",
        "api_key",
        "c:\\users\\",
        "/home/",
        "provider response",
        "guaranteed outcome",
    ):
        assert forbidden not in (text + "\n" + reference_text).lower(), (
            f"{name} contains prohibited untrusted or professional-guarantee wording: {forbidden}"
        )
    assert not _unsafe_authority_matches(text + "\n" + reference_text), (
        f"{name} contains an affirmative canonical-authority phrase"
    )


def _unsafe_authority_matches(text: str) -> list[str]:
    unsafe: list[str] = []
    for clause in CLAUSE_BOUNDARY.split(text):
        for base, forms, object_pattern in AUTHORITY_FAMILIES.values():
            verb_pattern = "|".join(re.escape(form) for form in forms)
            pattern = re.compile(
                rf"\b(?P<verb>{verb_pattern})\s+{object_pattern}\b", re.IGNORECASE
            )
            for match in pattern.finditer(clause):
                prefix = clause[: match.start("verb")]
                directly_negated = (
                    match.group("verb").casefold() == base
                    and bool(NEGATOR_AT_CLAUSE_END.search(prefix))
                )
                if not directly_negated:
                    unsafe.append(match.group(0))
    return unsafe


def _assert_pack_inventory_and_retrieval(skills: list[Skill]) -> None:
    expected_skills = sorted(f"{name}/SKILL.md" for name in PACK)
    actual_skills = sorted(str(path.relative_to(SKILL_ROOT)).replace("\\", "/") for path in SKILL_ROOT.glob("*/SKILL.md"))
    assert actual_skills == expected_skills

    expected_references = sorted(
        f"{name}/{details['reference']}" for name, details in PACK.items()
    )
    actual_references = sorted(
        str(path.relative_to(SKILL_ROOT)).replace("\\", "/")
        for path in SKILL_ROOT.glob("*/references/*")
        if path.is_file()
    )
    assert actual_references == expected_references

    manager = SkillsManager(str(ROOT / "data"))
    loaded = {entry["name"]: entry for entry in manager.load_all() if entry["name"] in PACK}
    assert set(loaded) == set(PACK)
    for skill in skills:
        reference = PACK[skill.name]["reference"]
        assert manager.read_skill_reference(skill.name, reference, owner=skill.owner)

    candidates = [loaded[name] for name in PACK]
    for name, details in PACK.items():
        for query in details["positives"]:
            matches = manager.get_relevant_skills(query, skills=candidates, max_items=3)
            assert matches and matches[0]["name"] == name, (
                f"{query!r} must select {name} first"
            )
    for query in ("fix a Python bug", "deployment status", "read this document", "small talk about lunch"):
        assert manager.get_relevant_skills(query, skills=candidates) == []


def _assert_acknowledgment() -> None:
    acknowledgment = (ROOT / "ACKNOWLEDGMENTS.md").read_text(encoding="utf-8")
    line = "Kimchi (https://github.com/chitransh-cj/kimchi) at 3dedd38aef7cd0f3c868844b058ffbd519f067f0 — MIT License"
    assert acknowledgment.count(line) == 1


def test_product_pressure_test_is_a_bounded_draft() -> None:
    skill, text = _read_skill("product-pressure-test")
    _assert_reference_and_safety(skill.name, skill, text)
    assert "one load-bearing clarification thread" in text
    assert "open unknown" in text
    assert "Clarification v2 readiness" in text


def test_lean_implementation_review_is_a_bounded_draft() -> None:
    skill, text = _read_skill("lean-implementation-review")
    _assert_reference_and_safety(skill.name, skill, text)
    for guardrail in ("validation", "security", "accessibility", "data integrity"):
        assert guardrail in text


def test_product_decision_audit_is_a_bounded_draft() -> None:
    skill, text = _read_skill("product-decision-audit")
    _assert_reference_and_safety(skill.name, skill, text)
    assert all(verdict in text for verdict in ("holds", "revise", "blocked"))
    skills = [skill]
    for name in ("product-pressure-test", "lean-implementation-review"):
        skills.append(_read_skill(name)[0])
    _assert_pack_inventory_and_retrieval(skills)
    _assert_acknowledgment()


def test_closed_clause_local_authority_contract_covers_every_inflection() -> None:
    safe = (
        "The audit does not accept the decision.",
        "Do not mark a node claimable.",
        "Never claim verified completion.",
        "The skill cannot update PlanRuntime.",
        "The report can't update PlanGraph.",
    )
    unsafe = (
        "Accept the decision and mark the node claimable.",
        "Approve the decision.",
        "Set the node claimable.",
        "Declare verified completion.",
        "Persist canonical plan state.",
        "Do not hide evidence; accept the decision and mark the node claimable.",
        "Never suppress uncertainty. Accept the decision.",
        "The audit does not hide evidence but approves the decision.",
        "Do not update notes; persist canonical plan state.",
    )
    for fixture in safe:
        assert _unsafe_authority_matches(fixture) == []
    for fixture in unsafe:
        assert _unsafe_authority_matches(fixture), fixture

    for family, (base, forms, _) in AUTHORITY_FAMILIES.items():
        object_text = OBJECT_EXAMPLES[family]
        for form in forms:
            affirmative = f"{form.title()} {object_text}."
            assert _unsafe_authority_matches(affirmative), affirmative
            negated = f"Do not {form} {object_text}."
            if form == base:
                assert _unsafe_authority_matches(negated) == [], negated
            else:
                assert _unsafe_authority_matches(negated), negated
