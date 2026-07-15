from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.planning_definition_contract import (
    FORBIDDEN_EXECUTION_STATES,
    GATE_RUNTIME_FIELD_DENYLIST,
    PLANNING_DEFINITION_SCHEMA_ID,
    RUNTIME_FIELD_DENYLIST,
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_planning_definition,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "specs" / "planning_definition.v2.schema.json"


def _canonical_document() -> dict:
    roadmap = {
        "roadmap_id": "roadmap-a",
        "project_id": "project-a",
        "revision": 1,
        "content_hash": "sha256:" + ("0" * 64),
        "revision_state": "approved",
        "title": "Canonical roadmap",
        "objective": "Describe immutable work without execution state.",
        "assumptions": ["The repository is available."],
        "constraints": ["Planning cannot launch an Agent run."],
        "nodes": [
            {
                "node_id": "prepare",
                "kind": "work",
                "title": "Prepare",
                "objective": "Prepare a deterministic definition.",
                "depends_on": [],
                "gate_ids": [],
                "deliverables": ["Validated definition"],
                "allowed_paths": ["src/planning_definition_contract.py"],
                "blocked_paths": [".env.example"],
                "capability_requirements": ["Python"],
                "verification_rule_ids": ["rule-static"],
            },
            {
                "node_id": "ship",
                "kind": "milestone",
                "title": "Ship",
                "objective": "Reach the declared completion boundary.",
                "depends_on": ["prepare"],
                "gate_ids": ["gate-live"],
                "deliverables": ["Approved handoff"],
                "allowed_paths": ["docs/plans/example.json"],
                "blocked_paths": [],
                "capability_requirements": ["Operator approval"],
                "verification_rule_ids": ["rule-test"],
            },
        ],
        "edges": [{"from": "ship", "to": "prepare", "kind": "depends_on"}],
        "gates": [
            {
                "gate_id": "gate-live",
                "kind": "live",
                "title": "Live authorization",
                "blocks": ["ship"],
                "decision_needed": "Authorize the named external target.",
                "safe_default": "Do not mutate the external target.",
                "approval_scope_schema": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                "required_verification_rule_ids": ["rule-test"],
            }
        ],
        "done_contract": {
            "required_node_ids": ["prepare", "ship"],
            "required_gate_ids": ["gate-live"],
            "verification_rules": [
                {
                    "rule_id": "rule-static",
                    "kind": "static",
                    "description": "The definition passes structural validation.",
                },
                {
                    "rule_id": "rule-test",
                    "kind": "test",
                    "description": "The focused contract suite passes.",
                },
            ],
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": ["docs/plans/source-roadmap.json"],
        "created_at": "2026-07-15T07:00:00+02:00",
        "updated_at": "2026-07-15T07:00:00+02:00",
    }
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    return {
        "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
        "project": {
            "project_id": "project-a",
            "title": "Canonical project",
            "objective": "Author immutable Planning definitions.",
            "scope": {"in": ["Planning definitions"], "out": ["Agent execution"]},
            "constraints": ["No runtime state in Planning."],
            "roadmap_refs": ["roadmap-a"],
            "latest_approved_revision": {
                "roadmap-a": {"revision": 1, "content_hash": roadmap["content_hash"]}
            },
            "draft_refs": [
                {
                    "draft_id": "draft-a",
                    "roadmap_id": "roadmap-a",
                    "base_revision": 1,
                    "base_hash": roadmap["content_hash"],
                }
            ],
        },
        "roadmaps": [roadmap],
    }


def _rehash(document: dict) -> None:
    for roadmap in document["roadmaps"]:
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    by_revision = {
        (roadmap["roadmap_id"], roadmap["revision"]): roadmap
        for roadmap in document["roadmaps"]
    }
    for roadmap_id, reference in document["project"]["latest_approved_revision"].items():
        target = by_revision.get((roadmap_id, reference["revision"]))
        if target is not None:
            reference["content_hash"] = target["content_hash"]
    for draft in document["project"]["draft_refs"]:
        target = by_revision.get((draft["roadmap_id"], draft["base_revision"]))
        if target is not None:
            draft["base_hash"] = target["content_hash"]


def _assert_reason(document: dict, reason: str) -> PlanningDefinitionContractError:
    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_planning_definition(document)
    assert raised.value.reason_code == reason
    assert raised.value.path.startswith("$")
    return raised.value


def test_canonical_definition_validates_with_content_free_receipt() -> None:
    document = _canonical_document()

    receipt = validate_planning_definition(document).to_dict()

    assert receipt == {
        "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
        "project_id": "project-a",
        "roadmap_hashes": [
            {
                "roadmap_id": "roadmap-a",
                "revision": 1,
                "content_hash": document["roadmaps"][0]["content_hash"],
            }
        ],
    }
    assert "objective" not in json.dumps(receipt)


def test_published_schema_has_the_same_closed_top_level_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_id", "project", "roadmaps"}
    assert schema["properties"]["schema_id"]["const"] == PLANNING_DEFINITION_SCHEMA_ID
    assert schema["$defs"]["doneContract"]["properties"]["completion_rule"]["const"] == (
        "all_required_nodes_and_gates"
    )
    assert "command" not in schema["$defs"]["verificationRule"]["properties"]


def test_content_hash_is_deterministic_across_mapping_key_order() -> None:
    roadmap = _canonical_document()["roadmaps"][0]
    reordered = dict(reversed(list(roadmap.items())))
    reordered["nodes"] = [dict(reversed(list(node.items()))) for node in roadmap["nodes"]]

    assert compute_roadmap_content_hash(reordered) == roadmap["content_hash"]


def test_changed_content_with_stale_hash_is_rejected() -> None:
    document = _canonical_document()
    document["roadmaps"][0]["objective"] = "Changed without a new hash."

    _assert_reason(document, "content_hash_mismatch")


def test_changed_approved_revision_is_rejected_against_immutable_baseline() -> None:
    document = _canonical_document()
    original_hash = document["roadmaps"][0]["content_hash"]
    document["roadmaps"][0]["objective"] = "Changed and re-hashed."
    _rehash(document)

    _assert_reason_with_baseline(document, {("roadmap-a", 1): original_hash})


def _assert_reason_with_baseline(document: dict, baseline: dict) -> None:
    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_planning_definition(document, approved_hashes=baseline)
    assert raised.value.reason_code == "approved_revision_immutable"


@pytest.mark.parametrize("field", sorted(RUNTIME_FIELD_DENYLIST))
def test_every_runtime_field_is_rejected_recursively(field: str) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["nested"] = {field: "definition-looking-value"}

    error = _assert_reason(document, "runtime_field_forbidden")
    assert field in error.path


@pytest.mark.parametrize("state", sorted(FORBIDDEN_EXECUTION_STATES))
def test_every_execution_state_is_rejected_recursively(state: str) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["properties"]["status"] = {"enum": [state]}

    _assert_reason(document, "execution_state_forbidden")


@pytest.mark.parametrize("field", sorted(GATE_RUNTIME_FIELD_DENYLIST))
def test_every_gate_decision_field_is_rejected(field: str) -> None:
    document = _canonical_document()
    document["roadmaps"][0]["gates"][0][field] = "runtime value"

    _assert_reason(document, "runtime_field_forbidden")


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("project_roadmap", "missing_reference"),
        ("project_latest", "missing_reference"),
        ("roadmap_project", "missing_reference"),
        ("draft_base", "missing_reference"),
        ("node_dependency", "missing_reference"),
        ("node_gate", "missing_reference"),
        ("node_rule", "missing_reference"),
        ("edge_endpoint", "missing_reference"),
        ("gate_target", "invalid_gate_target"),
        ("gate_rule", "missing_reference"),
        ("completion_node", "invalid_completion_reference"),
        ("completion_gate", "invalid_completion_reference"),
    ],
)
def test_reference_failures_are_fail_closed(case: str, reason: str) -> None:
    document = _canonical_document()
    roadmap = document["roadmaps"][0]
    if case == "project_roadmap":
        document["project"]["roadmap_refs"] = ["missing-roadmap"]
    elif case == "project_latest":
        document["project"]["latest_approved_revision"]["roadmap-a"]["revision"] = 99
    elif case == "roadmap_project":
        roadmap["project_id"] = "missing-project"
    elif case == "draft_base":
        document["project"]["draft_refs"][0]["base_revision"] = 99
    elif case == "node_dependency":
        roadmap["nodes"][1]["depends_on"] = ["missing-node"]
    elif case == "node_gate":
        roadmap["nodes"][1]["gate_ids"] = ["missing-gate"]
    elif case == "node_rule":
        roadmap["nodes"][0]["verification_rule_ids"] = ["missing-rule"]
    elif case == "edge_endpoint":
        roadmap["edges"][0]["to"] = "missing-node"
    elif case == "gate_target":
        roadmap["gates"][0]["blocks"] = ["missing-node"]
    elif case == "gate_rule":
        roadmap["gates"][0]["required_verification_rule_ids"] = ["missing-rule"]
    elif case == "completion_node":
        roadmap["done_contract"]["required_node_ids"] = ["missing-node"]
    elif case == "completion_gate":
        roadmap["done_contract"]["required_gate_ids"] = ["missing-gate"]
    _rehash(document)

    _assert_reason(document, reason)


def test_dependency_cycle_is_rejected() -> None:
    document = _canonical_document()
    document["roadmaps"][0]["nodes"][0]["depends_on"] = ["ship"]
    _rehash(document)

    _assert_reason(document, "dependency_cycle")


@pytest.mark.parametrize(
    "target",
    ["node", "gate", "verification_rule", "roadmap_revision", "draft"],
)
def test_identifiers_that_form_lookup_keys_are_unique(target: str) -> None:
    document = _canonical_document()
    roadmap = document["roadmaps"][0]
    if target == "node":
        roadmap["nodes"].append(deepcopy(roadmap["nodes"][0]))
    elif target == "gate":
        roadmap["gates"].append(deepcopy(roadmap["gates"][0]))
    elif target == "verification_rule":
        rules = roadmap["done_contract"]["verification_rules"]
        rules.append(deepcopy(rules[0]))
    elif target == "roadmap_revision":
        document["roadmaps"].append(deepcopy(roadmap))
    elif target == "draft":
        drafts = document["project"]["draft_refs"]
        drafts.append(deepcopy(drafts[0]))
    _rehash(document)

    _assert_reason(document, "duplicate_id")


def test_unknown_definition_field_is_rejected() -> None:
    document = _canonical_document()
    document["roadmaps"][0]["nodes"][0]["progress"] = 0

    _assert_reason(document, "unknown_field")


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/absolute", ".git/config"])
def test_repository_paths_are_relative_and_non_private(path: str) -> None:
    document = _canonical_document()
    document["roadmaps"][0]["nodes"][0]["allowed_paths"] = [path]

    _assert_reason(document, "invalid_repo_path")


def test_validator_has_no_agent_or_temporal_dependency() -> None:
    source = (ROOT / "src" / "planning_definition_contract.py").read_text(encoding="utf-8")

    assert "import temporal" not in source.lower()
    assert "from temporal" not in source.lower()
    assert "import agent" not in source.lower()
    assert "from agent" not in source.lower()
