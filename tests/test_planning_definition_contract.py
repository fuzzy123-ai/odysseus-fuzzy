from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path

import pytest

import src.planning_definition_contract as planning_contract_module
from src.planning_definition_contract import (
    APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS,
    APPROVAL_SCHEMA_MAX_DEPTH,
    APPROVAL_SCHEMA_MAX_VISITED_NODES,
    FORBIDDEN_EXECUTION_STATES,
    GATE_RUNTIME_FIELD_DENYLIST,
    PLANNING_DEFINITION_SCHEMA_ID,
    RUNTIME_FIELD_DENYLIST,
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_approval_scope_schema,
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
                    "additionalProperties": False,
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


def _closed_scope(**keywords) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        **keywords,
    }


class _HostileApprovalMapping(Mapping):
    def __init__(self, mode: str, error_type: type[Exception]) -> None:
        self._values = _closed_scope()
        self._mode = mode
        self._error_type = error_type

    def _raise(self) -> None:
        raise self._error_type("hostile approval mapping")

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        if self._mode == "iter":
            self._raise()
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def items(self):
        if self._mode == "items":
            self._raise()
        return self._values.items()

    def get(self, key, default=None):
        if self._mode == "get":
            self._raise()
        return self._values.get(key, default)


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
    schema["properties"][field] = {"type": "string"}

    error = _assert_reason(document, "runtime_field_forbidden")
    assert field in error.path


@pytest.mark.parametrize("state", sorted(FORBIDDEN_EXECUTION_STATES))
def test_execution_state_words_are_valid_approval_schema_literals(state: str) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["properties"]["status"] = {"enum": [state]}
    _rehash(document)

    validate_planning_definition(document)


def test_scope_schema_rejects_exact_runtime_fields_but_not_schema_literals() -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["properties"]["effective_date"] = {"type": "string", "default": "effective"}
    schema["properties"]["status"] = {"enum": ["running"]}
    schema["examples"] = [{"status": "running", "effective_date": "effective"}]
    _rehash(document)

    validate_planning_definition(document)

    schema["properties"]["claim_owner"] = {"type": "string"}
    _assert_reason(document, "runtime_field_forbidden")


def test_scope_schema_rejects_direct_and_nested_runtime_names() -> None:
    direct = _canonical_document()
    direct["roadmaps"][0]["gates"][0]["approval_scope_schema"]["operator_decision"] = {
        "type": "string"
    }
    _assert_reason(direct, "runtime_field_forbidden")

    nested = _canonical_document()
    nested["roadmaps"][0]["gates"][0]["approval_scope_schema"]["allOf"] = [
        {"properties": {"temporal_state": {"type": "string"}}}
    ]
    _assert_reason(nested, "approval_scope_not_closed")


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "string"},
        {"type": "object"},
        {"type": "object", "additionalProperties": True},
        {"type": "object", "additionalProperties": {}},
        {
            "type": "object",
            "additionalProperties": False,
            "unevaluatedProperties": True,
        },
        {
            "type": "object",
            "additionalProperties": False,
            "unevaluatedProperties": {},
        },
        _closed_scope(properties={"nested": {"type": "object"}}),
        _closed_scope(properties={"anything": True}),
    ],
)
def test_closed_approval_profile_rejects_every_open_object_shape(schema) -> None:
    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(schema, path="$.scope")

    assert raised.value.reason_code == "approval_scope_not_closed"


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("$ref", "#/$defs/scope"),
        ("$dynamicRef", "#scope"),
        ("$defs", {}),
        ("definitions", {}),
        ("allOf", [False]),
        ("anyOf", [False]),
        ("oneOf", [False]),
        ("not", False),
        ("if", False),
        ("then", False),
        ("else", False),
        ("dependentSchemas", {"target": False}),
    ],
)
def test_closed_approval_profile_rejects_general_evaluator_constructs(
    keyword: str,
    value,
) -> None:
    schema = _closed_scope()
    schema[keyword] = value

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(schema, path="$.scope")

    assert raised.value.reason_code == "approval_scope_not_closed"
    assert raised.value.path == f"$.scope.{keyword}"


def test_closed_profile_preserves_false_children_and_bounded_json_literals() -> None:
    schema = _closed_scope(
        properties={
            "denied": False,
            "choice": {
                "type": ["string", "null"],
                "enum": ["approve", None],
                "default": None,
                "examples": ["approve", None],
            },
            "items": {
                "type": "array",
                "items": False,
                "const": ["one", 2, True, None],
            },
        },
        propertyNames={"anyOf": [{"const": "choice"}, {"const": "items"}]},
    )

    checked = validate_approval_scope_schema(schema, path="$.scope")

    assert checked == schema
    assert checked is not schema
    with pytest.raises(PlanningDefinitionContractError) as root_false:
        validate_approval_scope_schema(False, path="$.scope")
    assert root_false.value.reason_code == "invalid_type"


def test_non_finite_schema_literals_fail_before_capture() -> None:
    schema = _closed_scope(properties={"value": {"type": "number", "default": float("nan")}})

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(schema, path="$.scope")

    assert raised.value.reason_code == "invalid_json_value"


def test_approval_schema_capture_failure_is_normalized(monkeypatch) -> None:
    def fail_capture(_value):
        raise RuntimeError("controlled capture failure")

    monkeypatch.setattr(planning_contract_module, "deepcopy", fail_capture)

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(_closed_scope(), path="$.scope")

    assert raised.value.reason_code == "approval_schema_capture_failed"
    assert raised.value.path == "$.scope"


@pytest.mark.parametrize("mode", ["items", "iter", "get"])
@pytest.mark.parametrize("error_type", [RuntimeError, RecursionError])
def test_hostile_raw_approval_mapping_failures_are_normalized(
    mode: str,
    error_type: type[Exception],
) -> None:
    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(
            _HostileApprovalMapping(mode, error_type),
            path="$.scope",
        )

    assert raised.value.reason_code == "approval_schema_capture_failed"
    assert raised.value.path == "$.scope"


def test_captured_approval_schema_is_revalidated_and_returned_isolated(monkeypatch) -> None:
    raw = _closed_scope(properties={"target": {"type": "string"}})
    real_deepcopy = deepcopy

    def open_captured_scope(value):
        captured = real_deepcopy(value)
        captured["additionalProperties"] = True
        return captured

    monkeypatch.setattr(planning_contract_module, "deepcopy", open_captured_scope)
    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(raw, path="$.scope")
    assert raised.value.reason_code == "approval_scope_not_closed"

    def alter_valid_captured_scope(value):
        captured = real_deepcopy(value)
        captured["properties"]["target"]["type"] = "integer"
        return captured

    monkeypatch.setattr(planning_contract_module, "deepcopy", alter_valid_captured_scope)
    with pytest.raises(PlanningDefinitionContractError) as divergent:
        validate_approval_scope_schema(raw, path="$.scope")
    assert divergent.value.reason_code == "approval_schema_capture_failed"

    monkeypatch.setattr(planning_contract_module, "deepcopy", real_deepcopy)
    checked = validate_approval_scope_schema(raw, path="$.scope")
    raw["properties"]["target"]["type"] = "integer"
    assert checked["properties"]["target"]["type"] == "string"


@pytest.mark.parametrize("control", ["\n", "\t", "\x00", "\x1f", "\x7f", "\x85"])
def test_repository_paths_reject_all_control_characters(control: str) -> None:
    document = _canonical_document()
    document["roadmaps"][0]["nodes"][0]["allowed_paths"] = [f"src/{control}bad.py"]

    _assert_reason(document, "invalid_repo_path")


@pytest.mark.parametrize("reserved", ["claim_owner", "operator_decision", "temporal_state"])
@pytest.mark.parametrize("pattern", ["{name}", "^{name}$"])
def test_pattern_properties_reject_exact_runtime_name_patterns(
    reserved: str,
    pattern: str,
) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["patternProperties"] = {pattern.format(name=reserved): {"type": "string"}}

    _assert_reason(document, "runtime_field_forbidden")


@pytest.mark.parametrize(
    "pattern",
    [
        "^cl[a]im_owner$",
        "^claim.owner$",
        "^claim_[a-z]{5}$",
    ],
)
def test_bounded_property_name_patterns_cannot_bypass_reserved_names(pattern: str) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["patternProperties"] = {pattern: {"type": "string"}}

    _assert_reason(document, "runtime_field_forbidden")


@pytest.mark.parametrize(
    "pattern",
    [
        r"\Aclaim_owner\Z",
        "(?i)^claim_owner$",
        "(claim_owner)",
        "claim_owner|public",
        "(?!claim_owner)",
        r"\1claim_owner",
        ".*",
        ".+",
        "[a-z]*",
        "a{1,}",
        "a{1,2}b{1,2}",
    ],
)
def test_property_name_patterns_reject_ambiguous_or_unbounded_regex(pattern: str) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["patternProperties"] = {pattern: {"type": "string"}}

    _assert_reason(document, "invalid_property_name_pattern")


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("const", "claim_owner"),
        ("enum", ["operator_decision"]),
        ("default", "temporal_state"),
        ("examples", ["claim_owner"]),
        ("pattern", "^operator_decision$"),
    ],
)
def test_property_names_reject_runtime_names_exposed_by_schema_literals(
    keyword: str,
    value,
) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["propertyNames"] = {keyword: value}

    _assert_reason(document, "runtime_field_forbidden")


@pytest.mark.parametrize(
    ("selector", "reason"),
    [
        (True, "invalid_property_name_selector"),
        ({}, "invalid_property_name_selector"),
        ({"type": "string"}, "invalid_property_name_selector"),
        ({"$ref": "#/$defs/name"}, "invalid_property_name_selector"),
        ({"$dynamicRef": "#name"}, "invalid_property_name_selector"),
        ({"pattern": ".*"}, "invalid_property_name_pattern"),
        ({"not": {"const": "public_name"}}, "runtime_field_forbidden"),
        (
            {"not": {"not": {"pattern": "^claim_owner$"}}},
            "runtime_field_forbidden",
        ),
    ],
)
def test_property_name_selector_bypasses_fail_closed(selector, reason: str) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["propertyNames"] = selector

    _assert_reason(document, reason)


@pytest.mark.parametrize(
    "selector",
    [
        False,
        {"const": "status"},
        {"enum": ["status", "effective_date"]},
        {"pattern": "^public_[a-z]{1,16}$"},
        {"allOf": [{"const": "status"}, {"pattern": "^status$"}]},
        {"anyOf": [{"const": "status"}, {"const": "effective_date"}]},
        {"oneOf": [{"const": "status"}, {"const": "effective_date"}]},
        {"not": {"pattern": "."}},
    ],
)
def test_bounded_property_name_selectors_preserve_safe_constructs(selector) -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["propertyNames"] = selector
    _rehash(document)

    validate_planning_definition(document)


@pytest.mark.parametrize("keyword", ["not", "allOf", "anyOf", "oneOf"])
def test_1500_nested_property_name_selectors_fail_with_contract_error(keyword: str) -> None:
    selector = False
    for _ in range(1_500):
        selector = {keyword: selector} if keyword == "not" else {keyword: [selector]}

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(
            _closed_scope(propertyNames=selector),
            path="$.scope",
        )

    assert raised.value.reason_code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.scope.propertyNames")


def test_1500_nested_general_schema_nodes_fail_before_python_recursion() -> None:
    schema = False
    for _ in range(1_500):
        schema = {"type": "array", "items": schema}

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(
            _closed_scope(properties={"payload": schema}),
            path="$.scope",
        )

    assert raised.value.reason_code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.scope.properties.payload.items")


def test_combinator_budget_is_shared_across_sibling_subtrees() -> None:
    half = (APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS // 2) + 1
    schema = _closed_scope(
        propertyNames={
            "allOf": [
            {"allOf": [False] * half},
            {"anyOf": [False] * half},
            ]
        }
    )

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(schema, path="$.scope")

    assert raised.value.reason_code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.scope.propertyNames.allOf")


def test_approval_schema_node_budget_is_shared_and_fails_closed() -> None:
    schema = _closed_scope(
        properties={
            f"public_{index}": False
            for index in range(APPROVAL_SCHEMA_MAX_VISITED_NODES - 1)
        }
    )

    with pytest.raises(PlanningDefinitionContractError) as raised:
        validate_approval_scope_schema(schema, path="$.scope")

    assert raised.value.reason_code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.scope.properties.public_")


def test_approval_schema_boundary_values_just_below_budgets_remain_valid() -> None:
    selector = False
    for _ in range(APPROVAL_SCHEMA_MAX_DEPTH - 2):
        selector = {"not": selector}
    validate_approval_scope_schema(
        _closed_scope(propertyNames=selector),
        path="$.depth_scope",
    )

    branches = [
        {"const": f"public_{index}"}
        for index in range(APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS - 1)
    ]
    validate_approval_scope_schema(
        _closed_scope(propertyNames={"anyOf": branches}),
        path="$.branch_scope",
    )

    properties = {
        f"public_{index}": False
        for index in range(APPROVAL_SCHEMA_MAX_VISITED_NODES - 4)
    }
    validate_approval_scope_schema(
        _closed_scope(properties=properties),
        path="$.node_scope",
    )


def test_non_reserved_name_patterns_and_regular_schema_literals_remain_valid() -> None:
    document = _canonical_document()
    schema = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    schema["properties"]["target"]["pattern"] = "(?i)^(foo|bar)+$"
    schema.update(
        {
            "patternProperties": {
                "^public_[a-z]{1,16}$": {
                    "type": "string",
                    "pattern": "(?i)^(foo|bar)+$",
                }
            },
            "propertyNames": {
                "anyOf": [
                    {"pattern": "^public_[a-z]{1,16}$"},
                    {"enum": ["status", "effective_date"]},
                ],
                "default": "effective_date",
                "examples": ["status"],
            },
            "examples": [{"claim_owner": "literal-only-not-an-authority-field"}],
        }
    )
    _rehash(document)

    validate_planning_definition(document)


def test_multiline_planning_text_and_strict_repository_paths_are_preserved() -> None:
    document = _canonical_document()
    roadmap = document["roadmaps"][0]
    roadmap["objective"] = "First line.\nSecond line.\n\tIndented acceptance note."
    _rehash(document)

    validate_planning_definition(document)

    for path in ("src//bad.py", "src\\bad.py", "~/.ssh/id_rsa", "src/../bad.py"):
        invalid = _canonical_document()
        invalid["roadmaps"][0]["nodes"][0]["allowed_paths"] = [path]
        _assert_reason(invalid, "invalid_repo_path")


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
