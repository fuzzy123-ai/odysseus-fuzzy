from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.planning_agent_handoff import (
    AGENT_PLAN_HANDOFF_SCHEMA_ID,
    FORBIDDEN_HANDOFF_FIELDS,
    PlanningAgentHandoffError,
    build_agent_plan_handoff,
    validate_agent_plan_handoff,
)
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import PlanningRevisionStore
from tests.test_planning_definition_projection import definition_fixture


ROOT = Path(__file__).resolve().parents[1]
OWNER = "alice"


def _read_model() -> tuple[dict, dict]:
    document = definition_fixture(include_draft=False)
    store = PlanningRevisionStore([(OWNER, document, "definition.json")])
    roadmap = document["roadmaps"][0]
    return (
        store.get_roadmap(
            OWNER,
            "project-a",
            "roadmap-a",
            revision=roadmap["revision"],
        ),
        roadmap,
    )


def _envelope() -> dict:
    read_model, roadmap = _read_model()
    return build_agent_plan_handoff(
        read_model,
        expected_revision=roadmap["revision"],
        expected_hash=roadmap["content_hash"],
    )


def test_exact_approved_revision_builds_hash_pinned_non_launching_envelope() -> None:
    read_model, roadmap = _read_model()

    envelope = build_agent_plan_handoff(
        read_model,
        expected_revision=1,
        expected_hash=roadmap["content_hash"],
    )

    assert envelope == {
        "schema_id": AGENT_PLAN_HANDOFF_SCHEMA_ID,
        "project_id": "project-a",
        "roadmap_id": "roadmap-a",
        "revision": 1,
        "content_hash": roadmap["content_hash"],
        "title": "roadmap-a approved",
        "requested_entrypoint": "/abc",
        "composer_text": (
            f"/abc run roadmap:roadmap-a@1 hash:{roadmap['content_hash']}"
        ),
        "launch_authorized": False,
        "read_only": True,
    }
    assert validate_agent_plan_handoff(envelope) == envelope
    assert not (set(envelope) & FORBIDDEN_HANDOFF_FIELDS)


@pytest.mark.parametrize(
    ("revision", "hash_value", "code"),
    [
        (2, None, "handoff_revision_mismatch"),
        (1, "sha256:" + ("f" * 64), "handoff_hash_mismatch"),
    ],
)
def test_requested_revision_and_hash_must_match_exactly(
    revision: int,
    hash_value: str | None,
    code: str,
) -> None:
    read_model, roadmap = _read_model()

    with pytest.raises(PlanningAgentHandoffError) as raised:
        build_agent_plan_handoff(
            read_model,
            expected_revision=revision,
            expected_hash=hash_value or roadmap["content_hash"],
        )

    assert raised.value.code == code


def test_older_approved_revision_is_rejected_when_a_newer_head_exists() -> None:
    document = definition_fixture(include_draft=True)
    newer = document["roadmaps"][1]
    newer["revision_state"] = "approved"
    newer["content_hash"] = compute_roadmap_content_hash(newer)
    document["project"]["latest_approved_revision"]["roadmap-a"] = {
        "revision": 2,
        "content_hash": newer["content_hash"],
    }
    store = PlanningRevisionStore([(OWNER, document, "definition.json")])
    older = document["roadmaps"][0]
    older_model = store.get_roadmap(
        OWNER,
        "project-a",
        "roadmap-a",
        revision=1,
    )
    newer_model = store.get_roadmap(
        OWNER,
        "project-a",
        "roadmap-a",
        revision=2,
    )

    with pytest.raises(PlanningAgentHandoffError) as superseded:
        build_agent_plan_handoff(
            older_model,
            expected_revision=1,
            expected_hash=older["content_hash"],
        )
    accepted = build_agent_plan_handoff(
        newer_model,
        expected_revision=2,
        expected_hash=newer["content_hash"],
    )

    assert superseded.value.code == "handoff_revision_superseded"
    assert accepted["revision"] == 2


@pytest.mark.parametrize("revision_state", ["draft", "in_review", "superseded", "archived", "tombstoned"])
def test_non_approved_revision_state_is_rejected(revision_state: str) -> None:
    read_model, roadmap = _read_model()
    read_model["roadmap"]["revision_state"] = revision_state

    with pytest.raises(PlanningAgentHandoffError) as raised:
        build_agent_plan_handoff(
            read_model,
            expected_revision=1,
            expected_hash=roadmap["content_hash"],
        )

    assert raised.value.code == "handoff_revision_not_approved"


def test_stale_hash_cannot_pin_mutated_definition_content() -> None:
    read_model, roadmap = _read_model()
    read_model["roadmap"]["title"] = "Tampered after hash calculation"

    with pytest.raises(PlanningAgentHandoffError) as raised:
        build_agent_plan_handoff(
            read_model,
            expected_revision=1,
            expected_hash=roadmap["content_hash"],
        )

    assert raised.value.code == "handoff_content_integrity"


@pytest.mark.parametrize("field", sorted(FORBIDDEN_HANDOFF_FIELDS))
def test_execution_influencing_fields_are_forbidden(field: str) -> None:
    envelope = _envelope()
    envelope[field] = "not allowed"

    with pytest.raises(PlanningAgentHandoffError) as raised:
        validate_agent_plan_handoff(envelope)

    assert raised.value.code == "handoff_field_forbidden"


def test_launch_authorization_and_composer_text_are_invariants() -> None:
    launch = _envelope()
    launch["launch_authorized"] = True
    composer = _envelope()
    composer["composer_text"] += " --submit"

    with pytest.raises(PlanningAgentHandoffError) as launch_error:
        validate_agent_plan_handoff(launch)
    with pytest.raises(PlanningAgentHandoffError) as composer_error:
        validate_agent_plan_handoff(composer)

    assert launch_error.value.code == "launch_forbidden"
    assert composer_error.value.code == "composer_text_mismatch"


def test_validator_returns_a_copy_and_rejects_unknown_fields() -> None:
    envelope = _envelope()
    validated = validate_agent_plan_handoff(envelope)
    validated["title"] = "changed copy"
    unknown = deepcopy(envelope)
    unknown["navigation_target"] = "Agent"

    assert envelope["title"] != validated["title"]
    with pytest.raises(PlanningAgentHandoffError) as raised:
        validate_agent_plan_handoff(unknown)
    assert raised.value.code == "unknown_field"


def test_handoff_module_has_no_agent_temporal_or_http_execution_dependency() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/planning_agent_handoff.py",
            "routes/planning_definition_routes.py",
        )
    ).lower()

    assert "import agent" not in source
    assert "from agent" not in source
    assert "import temporal" not in source
    assert "from temporal" not in source
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "/api/agent/runs" not in source
