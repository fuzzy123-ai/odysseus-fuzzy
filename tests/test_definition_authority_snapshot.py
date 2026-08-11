from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json

import pytest

import src.definition_authority_snapshot as snapshot_module
from src.definition_authority_snapshot import (
    DEFINITION_AUTHORITY_SNAPSHOT_REF_SCHEMA_ID,
    DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID,
    DefinitionAuthoritySnapshotError,
    build_definition_authority_snapshot,
    validate_definition_authority_snapshot,
    validate_definition_authority_snapshot_reference,
)
from src.planning_definition_contract import (
    APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS,
    APPROVAL_SCHEMA_MAX_DEPTH,
    compute_roadmap_content_hash,
)
from src.planning_revision_store import PlanningRevisionStore
from tests.test_planning_definition_projection import definition_fixture


OWNER = "owner:alice"


def _read_model(document: dict | None = None) -> dict:
    value = document or definition_fixture(include_draft=False)
    roadmap = value["roadmaps"][0]
    store = PlanningRevisionStore([(OWNER, value, "definition.json")])
    return store.get_roadmap(
        OWNER,
        value["project"]["project_id"],
        roadmap["roadmap_id"],
        revision=roadmap["revision"],
    )


def _rehash_current(document: dict) -> None:
    roadmap = document["roadmaps"][0]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"][roadmap["roadmap_id"]] = {
        "revision": roadmap["revision"],
        "content_hash": roadmap["content_hash"],
    }


def _rich_document() -> dict:
    document = definition_fixture(include_draft=False)
    roadmap = document["roadmaps"][0]
    roadmap["nodes"].append(
        {
            "node_id": "ship",
            "kind": "milestone",
            "title": "Ship",
            "objective": "Complete the approved definition.",
            "depends_on": ["prepare"],
            "gate_ids": ["gate-live"],
            "deliverables": ["Approved handoff"],
            "allowed_paths": ["src/ship.py"],
            "blocked_paths": [],
            "capability_requirements": ["Python"],
            "verification_rule_ids": ["rule-static"],
        }
    )
    roadmap["gates"] = [
        {
            "gate_id": "gate-live",
            "kind": "live",
            "title": "Live approval",
            "blocks": ["ship"],
            "decision_needed": "Approve the exact external target.",
            "safe_default": "Do not mutate the external target.",
            "approval_scope_schema": {"type": "object", "additionalProperties": False},
            "required_verification_rule_ids": ["rule-static"],
        }
    ]
    roadmap["edges"] = [{"from": "ship", "to": "prepare", "kind": "depends_on"}]
    roadmap["done_contract"]["required_node_ids"] = ["prepare", "ship"]
    roadmap["done_contract"]["required_gate_ids"] = ["gate-live"]
    _rehash_current(document)
    return document


def _rehash_snapshot(payload: dict) -> None:
    unsigned = {key: value for key, value in payload.items() if key != "snapshot_hash"}
    encoded = json.dumps(unsigned, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload["snapshot_hash"] = "sha256:" + hashlib.sha256(encoded).hexdigest()


class _HostileApprovalMapping(Mapping):
    def __init__(self, values: dict, mode: str, error_type: type[Exception]) -> None:
        self._values = values
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


def _nested_array_schema(depth: int) -> dict:
    schema: dict | bool = False
    for _ in range(depth):
        schema = {"type": "array", "items": schema}
    return {
        "type": "object",
        "properties": {"payload": schema},
        "additionalProperties": False,
    }


_CLOSED_SCOPE_ADVERSARIAL_CASES = [
    ({}, "approval_scope_not_closed"),
    ({"type": "string"}, "approval_scope_not_closed"),
    ({"type": "object"}, "approval_scope_not_closed"),
    ({"type": "object", "additionalProperties": True}, "approval_scope_not_closed"),
    ({"type": "object", "additionalProperties": {}}, "approval_scope_not_closed"),
    (
        {
            "type": "object",
            "additionalProperties": False,
            "unevaluatedProperties": True,
        },
        "approval_scope_not_closed",
    ),
    (
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"nested": {"type": "object"}},
        },
        "approval_scope_not_closed",
    ),
    (
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"anything": True},
        },
        "approval_scope_not_closed",
    ),
    (
        {"type": "object", "additionalProperties": False, "$ref": "#/$defs/scope"},
        "approval_scope_not_closed",
    ),
    (
        {"type": "object", "additionalProperties": False, "allOf": [False]},
        "approval_scope_not_closed",
    ),
    (
        {"type": "object", "additionalProperties": False, "if": False},
        "approval_scope_not_closed",
    ),
    (
        {
            "type": "object",
            "additionalProperties": False,
            "dependentSchemas": {"target": False},
        },
        "approval_scope_not_closed",
    ),
    (False, "invalid_type"),
]


def test_snapshot_is_deterministic_canonical_and_defensively_immutable() -> None:
    read_model = _read_model()
    reordered = dict(reversed(list(read_model.items())))

    first = build_definition_authority_snapshot(read_model)
    second = build_definition_authority_snapshot(reordered)
    payload = first.to_payload()

    assert first == second
    assert first.canonical_bytes == second.canonical_bytes
    assert first.snapshot_hash.startswith("sha256:")
    assert payload["schema_id"] == DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID
    payload["normalized_dag"]["nodes"][0]["depends_on"].append("tamper")
    payload["done_contract"]["required_node_ids"].append("tamper")
    assert "tamper" not in first.to_payload()["normalized_dag"]["nodes"][0]["depends_on"]
    assert "tamper" not in first.to_payload()["done_contract"]["required_node_ids"]


@pytest.mark.parametrize(("schema", "expected_code"), _CLOSED_SCOPE_ADVERSARIAL_CASES)
def test_rehashed_snapshot_rejects_open_or_general_approval_schemas(
    schema,
    expected_code: str,
) -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    payload["normalized_dag"]["gates"][0]["approval_scope_schema"] = deepcopy(schema)
    _rehash_snapshot(payload)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == expected_code


@pytest.mark.parametrize("mode", ["items", "iter", "get"])
@pytest.mark.parametrize("error_type", [RuntimeError, RecursionError])
def test_rehashed_snapshot_normalizes_hostile_approval_mapping_failures(
    mode: str,
    error_type: type[Exception],
) -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    scope = {"type": "object", "additionalProperties": False}
    payload["normalized_dag"]["gates"][0]["approval_scope_schema"] = scope
    _rehash_snapshot(payload)
    payload["normalized_dag"]["gates"][0]["approval_scope_schema"] = (
        _HostileApprovalMapping(scope, mode, error_type)
    )

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == "approval_schema_capture_failed"
    assert raised.value.path == "$.normalized_dag.gates[0].approval_scope_schema"


def test_snapshot_uses_the_checked_copied_approval_schema(monkeypatch) -> None:
    checked_scope = {
        "type": "object",
        "properties": {"checked": False},
        "additionalProperties": False,
    }

    def return_checked_copy(_value, *, path: str):
        assert path.endswith("approval_scope_schema")
        return deepcopy(checked_scope)

    monkeypatch.setattr(snapshot_module, "validate_approval_scope_schema", return_checked_copy)

    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()

    assert payload["normalized_dag"]["gates"][0]["approval_scope_schema"] == checked_scope


def test_authority_relevant_definition_change_requires_a_new_snapshot_hash() -> None:
    first_document = definition_fixture(include_draft=False)
    changed_document = deepcopy(first_document)
    changed_document["roadmaps"][0]["nodes"][0]["allowed_paths"].append("src/changed.py")
    _rehash_current(changed_document)

    first = build_definition_authority_snapshot(_read_model(first_document))
    changed = build_definition_authority_snapshot(_read_model(changed_document))

    assert first.planning_content_hash != changed.planning_content_hash
    assert first.snapshot_hash != changed.snapshot_hash
    assert first.canonical_bytes != changed.canonical_bytes


def test_edge_only_dependency_is_merged_into_the_normalized_snapshot_node() -> None:
    document = _rich_document()
    document["roadmaps"][0]["nodes"][1]["depends_on"] = []
    document["roadmaps"][0]["edges"] = [{"from": "ship", "to": "prepare", "kind": "depends_on"}]
    _rehash_current(document)

    snapshot = build_definition_authority_snapshot(_read_model(document))
    nodes = {node["node_id"]: node for node in snapshot.to_payload()["normalized_dag"]["nodes"]}

    assert nodes["ship"]["depends_on"] == ["prepare"]
    assert validate_definition_authority_snapshot(snapshot.to_payload()) == snapshot.to_payload()


@pytest.mark.parametrize("forbidden_key", ["operator_decision", "claim_owner", "temporal_state"])
def test_runtime_authority_keys_in_valid_planning_scope_are_rejected_at_snapshot_boundary(forbidden_key) -> None:
    read_model = _read_model(_rich_document())
    roadmap = read_model["roadmap"]
    roadmap["gates"][0]["approval_scope_schema"]["properties"] = {
        forbidden_key: {"type": "string"}
    }
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"][roadmap["roadmap_id"]]["content_hash"] = roadmap["content_hash"]

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code == "runtime_field_forbidden"


@pytest.mark.parametrize("path", ["src//x.py", "src\\x.py", "~/.ssh/id_rsa", "../outside.py", "/absolute.py"])
def test_rehashed_private_or_malformed_paths_fail_closed(path: str) -> None:
    read_model = _read_model(_rich_document())
    roadmap = read_model["roadmap"]
    roadmap["nodes"][0]["allowed_paths"] = [path]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"][roadmap["roadmap_id"]]["content_hash"] = roadmap["content_hash"]

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code == "invalid_repo_path"


@pytest.mark.parametrize("control", ["\n", "\t", "\x00", "\x1f", "\x7f", "\x85"])
def test_rehashed_persisted_snapshot_paths_reject_control_characters(control: str) -> None:
    payload = build_definition_authority_snapshot(_read_model()).to_payload()
    payload["allowed_paths"] = [f"src/{control}bad.py"]
    _rehash_snapshot(payload)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == "invalid_repo_path"


def test_valid_schema_literals_and_multiline_gate_text_survive_snapshotting() -> None:
    document = _rich_document()
    scope = document["roadmaps"][0]["gates"][0]["approval_scope_schema"]
    scope.update(
        {
            "properties": {
                "status": {"enum": ["running"]},
                "effective_date": {"type": "string", "default": "effective"},
            },
            "examples": [{"status": "running", "effective_date": "effective"}],
        }
    )
    document["roadmaps"][0]["gates"][0]["decision_needed"] = "Review the target.\nThen approve the exact scope."
    _rehash_current(document)

    snapshot = build_definition_authority_snapshot(_read_model(document))

    assert snapshot.to_payload()["normalized_dag"]["gates"][0]["approval_scope_schema"] == scope


@pytest.mark.parametrize(
    "selector_fragment",
    [
        {"patternProperties": {"^cl[a]im_owner$": {"type": "string"}}},
        {"propertyNames": {"not": {"not": {"pattern": "^claim_owner$"}}}},
    ],
)
def test_rehashed_snapshot_rejects_transitive_property_name_bypasses(
    selector_fragment: dict,
) -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    scope = payload["normalized_dag"]["gates"][0]["approval_scope_schema"]
    scope.update(selector_fragment)
    _rehash_snapshot(payload)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == "runtime_field_forbidden"


def test_rehashed_snapshot_preserves_bounded_safe_property_name_selectors() -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    scope = payload["normalized_dag"]["gates"][0]["approval_scope_schema"]
    scope.update(
        {
            "patternProperties": {"^public_[a-z]{1,16}$": {"type": "string"}},
            "propertyNames": {
                "anyOf": [
                    {"const": "status"},
                    {"pattern": "^public_[a-z]{1,16}$"},
                ]
            },
        }
    )
    _rehash_snapshot(payload)

    assert validate_definition_authority_snapshot(payload) == payload


def test_persisted_snapshot_propagates_deep_schema_budget_as_contract_error() -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    payload["normalized_dag"]["gates"][0]["approval_scope_schema"] = _nested_array_schema(1_500)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.normalized_dag.gates[0].approval_scope_schema")


def test_snapshot_build_validates_deep_raw_schema_before_capture() -> None:
    read_model = _read_model(_rich_document())
    read_model["roadmap"]["gates"][0]["approval_scope_schema"] = _nested_array_schema(1_500)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.roadmaps[0].gates[0].approval_scope_schema")


def test_schema_just_below_depth_budget_survives_fresh_and_persisted_capture() -> None:
    read_model = _read_model(_rich_document())
    roadmap = read_model["roadmap"]
    roadmap["gates"][0]["approval_scope_schema"] = _nested_array_schema(
        APPROVAL_SCHEMA_MAX_DEPTH - 1
    )
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"][roadmap["roadmap_id"]]["content_hash"] = roadmap[
        "content_hash"
    ]

    snapshot = build_definition_authority_snapshot(read_model)
    payload = snapshot.to_payload()

    assert validate_definition_authority_snapshot(payload) == payload


def test_snapshot_capture_isolated_from_post_build_caller_mutation() -> None:
    read_model = _read_model(_rich_document())
    snapshot = build_definition_authority_snapshot(read_model)
    expected = snapshot.to_payload()

    read_model["project"]["title"] = "Mutated after capture"
    read_model["roadmap"]["nodes"][0]["allowed_paths"].append("src/late.py")
    read_model["roadmap"]["done_contract"]["required_node_ids"].append("late")

    assert snapshot.to_payload() == expected


def test_fresh_capture_divergence_is_revalidated_before_snapshot_derivation(monkeypatch) -> None:
    read_model = _read_model(_rich_document())
    real_deepcopy = snapshot_module.deepcopy

    def diverging_deepcopy(value):
        captured = real_deepcopy(value)
        if isinstance(captured, dict) and captured.get("roadmap_id") == "roadmap-a":
            captured["run_id"] = "capture-diverged"
        return captured

    monkeypatch.setattr(snapshot_module, "deepcopy", diverging_deepcopy)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code == "runtime_field_forbidden"
    assert raised.value.path == "$.roadmaps[0].run_id"


def test_fresh_post_validation_recursion_during_capture_has_stable_error(monkeypatch) -> None:
    read_model = _read_model(_rich_document())

    def recursive_capture(_value):
        raise RecursionError("controlled test recursion")

    monkeypatch.setattr(snapshot_module, "deepcopy", recursive_capture)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code == "read_model_capture_failed"
    assert raised.value.path == "$"


def test_fourth_deepcopy_recursion_hook_is_not_reached_after_final_validation(monkeypatch) -> None:
    read_model = _read_model(_rich_document())
    real_deepcopy = snapshot_module.deepcopy
    calls = 0

    def fail_on_fourth_deepcopy(value):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RecursionError("post-validation fourth capture must not occur")
        return real_deepcopy(value)

    monkeypatch.setattr(snapshot_module, "deepcopy", fail_on_fourth_deepcopy)

    snapshot = build_definition_authority_snapshot(read_model)
    payload = snapshot.to_payload()

    assert validate_definition_authority_snapshot(payload) == payload
    assert calls == 3


def test_fourth_deepcopy_mutation_cannot_enter_returned_done_contract(monkeypatch) -> None:
    read_model = _read_model(_rich_document())
    expected_required_node_ids = list(read_model["roadmap"]["done_contract"]["required_node_ids"])
    real_deepcopy = snapshot_module.deepcopy
    calls = 0

    def mutate_on_fourth_deepcopy(value):
        nonlocal calls
        calls += 1
        captured = real_deepcopy(value)
        if calls == 4 and isinstance(captured, dict) and "required_node_ids" in captured:
            captured["required_node_ids"].append("capture-injected")
        return captured

    monkeypatch.setattr(snapshot_module, "deepcopy", mutate_on_fourth_deepcopy)

    snapshot = build_definition_authority_snapshot(read_model)
    payload = snapshot.to_payload()

    assert payload["done_contract"]["required_node_ids"] == expected_required_node_ids
    assert "capture-injected" not in payload["done_contract"]["required_node_ids"]
    assert validate_definition_authority_snapshot(payload) == payload
    assert calls == 3


def test_persisted_capture_divergence_is_revalidated_before_return(monkeypatch) -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    real_deepcopy = snapshot_module.deepcopy

    def diverging_deepcopy(value):
        captured = real_deepcopy(value)
        if isinstance(captured, dict) and captured.get("schema_id") == DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID:
            captured["allowed_paths"].append("src/z-after-capture.py")
        return captured

    monkeypatch.setattr(snapshot_module, "deepcopy", diverging_deepcopy)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == "snapshot_hash_mismatch"


def test_persisted_post_validation_recursion_during_capture_has_stable_error(monkeypatch) -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()

    def recursive_capture(_value):
        raise RecursionError("controlled test recursion")

    monkeypatch.setattr(snapshot_module, "deepcopy", recursive_capture)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code == "read_model_capture_failed"
    assert raised.value.path == "$"


def test_snapshot_build_propagates_shared_branch_budget_as_contract_error() -> None:
    read_model = _read_model(_rich_document())
    read_model["roadmap"]["gates"][0]["approval_scope_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "propertyNames": {
            "anyOf": [False] * (APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS + 1)
        },
    }

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.roadmaps[0].gates[0].approval_scope_schema")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["normalized_dag"]["nodes"][0].__setitem__("unexpected", True),
        lambda payload: payload["normalized_dag"]["edges"][0].__setitem__("to", "unknown-node"),
        lambda payload: payload["normalized_dag"]["nodes"][0].__setitem__("depends_on", ["ship"]),
        lambda payload: payload["done_contract"].__setitem__("required_node_ids", ["unknown-node"]),
        lambda payload: payload.__setitem__("blocked_paths", payload["allowed_paths"][:1]),
        lambda payload: payload["normalized_dag"]["gates"][0]["approval_scope_schema"].__setitem__("claim_owner", {"type": "string"}),
    ],
)
def test_rehashed_unexpected_snapshot_shape_or_authority_data_fails_closed(mutate) -> None:
    payload = build_definition_authority_snapshot(_read_model(_rich_document())).to_payload()
    mutate(payload)
    _rehash_snapshot(payload)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        validate_definition_authority_snapshot(payload)

    assert raised.value.code in {"dependency_cycle", "missing_reference", "path_scope_conflict", "runtime_field_forbidden", "unknown_field"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda model: model["roadmap"].__setitem__("revision_state", "draft"),
        lambda model: model["roadmap"].__setitem__("project_id", "other-project"),
        lambda model: model["roadmap"].__setitem__("run_id", "runtime-forbidden"),
        lambda model: model["roadmap"].__setitem__("content_hash", "sha256:" + "f" * 64),
        lambda model: model["project"]["latest_approved_revision"]["roadmap-a"].__setitem__("revision", 99),
    ],
)
def test_unapproved_tampered_cross_project_runtime_and_stale_inputs_fail_closed(mutate) -> None:
    read_model = _read_model()
    mutate(read_model)

    with pytest.raises(DefinitionAuthoritySnapshotError) as raised:
        build_definition_authority_snapshot(read_model)

    assert raised.value.code in {
        "content_hash_mismatch",
        "invalid_approved_reference",
        "missing_reference",
        "plan_revision_conflict",
        "planning_revision_not_approved",
        "runtime_field_forbidden",
    }


def test_snapshot_and_reference_validation_are_exact_and_hash_bound() -> None:
    snapshot = build_definition_authority_snapshot(_read_model())
    payload = snapshot.to_payload()
    reference = snapshot.reference_payload()

    assert validate_definition_authority_snapshot(payload) == payload
    assert validate_definition_authority_snapshot_reference(reference) == reference
    assert reference["schema_id"] == DEFINITION_AUTHORITY_SNAPSHOT_REF_SCHEMA_ID
    assert set(reference) == {
        "schema_id",
        "project_id",
        "roadmap_id",
        "planning_revision",
        "planning_content_hash",
        "snapshot_hash",
    }

    payload["snapshot_hash"] = "sha256:" + "0" * 64
    reference["normalized_dag"] = {}
    with pytest.raises(DefinitionAuthoritySnapshotError) as hash_error:
        validate_definition_authority_snapshot(payload)
    with pytest.raises(DefinitionAuthoritySnapshotError) as reference_error:
        validate_definition_authority_snapshot_reference(reference)

    assert hash_error.value.code == "snapshot_hash_mismatch"
    assert reference_error.value.code == "unknown_field"
