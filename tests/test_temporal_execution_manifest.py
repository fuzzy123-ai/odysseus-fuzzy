from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from src.planning_revision_store import PlanningRevisionStore
from src.definition_authority_snapshot import build_definition_authority_snapshot
from src.planning_definition_contract import (
    APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS,
    APPROVAL_SCHEMA_MAX_DEPTH,
    compute_roadmap_content_hash,
)
from src.temporal_runtime.contracts import (
    EXECUTION_MANIFEST_SCHEMA_ID,
    ExecutionContractError,
    ExecutionPolicy,
)
from src.temporal_runtime.manifest import ManifestBuildError, build_execution_manifest
from tests.test_planning_definition_projection import definition_fixture


OWNER = "owner:alice"


def _read_model(document: dict | None = None) -> dict:
    value = document or definition_fixture(include_draft=False)
    store = PlanningRevisionStore([(OWNER, value, "definition.json")])
    roadmap = value["roadmaps"][0]
    return store.get_roadmap(
        OWNER,
        value["project"]["project_id"],
        roadmap["roadmap_id"],
        revision=roadmap["revision"],
    )


def _policy(**overrides) -> ExecutionPolicy:
    values = {
        "queue_scope": "named_roadmap",
        "supervision_mode": "unattended_long_run",
        "mutation_authority": "repo_only",
        "selected_route": {
            "entrypoint": "/abc",
            "skills": [{"id": "abc", "purpose": "orchestrator"}],
            "model": {"value": "surface_default", "reason": "surface does not enforce a model"},
        },
        "hotfiles": (),
        "max_parallel_activities": 1,
        "retry_budget": 2,
        "deadline_at": "2026-07-16T10:00:00+02:00",
    }
    values.update(overrides)
    return ExecutionPolicy.create(**values)


def _manifest(**overrides):
    return build_execution_manifest(
        _read_model(),
        owner_scope_ref=OWNER,
        start_request_id=overrides.pop("start_request_id", "start-request-0001"),
        policy=overrides.pop("policy", _policy()),
        **overrides,
    )


def _read_model_with_gate() -> dict:
    document = definition_fixture(include_draft=False)
    roadmap = document["roadmaps"][0]
    roadmap["nodes"][0]["gate_ids"] = ["gate-live"]
    roadmap["gates"] = [
        {
            "gate_id": "gate-live",
            "kind": "live",
            "title": "Live approval",
            "blocks": ["prepare"],
            "decision_needed": "Approve the exact target.",
            "safe_default": "Do not mutate the target.",
            "approval_scope_schema": {"type": "object", "additionalProperties": False},
            "required_verification_rule_ids": ["rule-static"],
        }
    ]
    roadmap["done_contract"]["required_gate_ids"] = ["gate-live"]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = roadmap[
        "content_hash"
    ]
    return _read_model(document)


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
            "unevaluatedProperties": {},
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
        {"type": "object", "additionalProperties": False, "oneOf": [False]},
        "approval_scope_not_closed",
    ),
    (
        {"type": "object", "additionalProperties": False, "then": False},
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


def test_same_inputs_produce_the_same_complete_manifest_hash():
    first = _manifest()
    second = _manifest()

    assert first == second
    payload = first.to_payload()
    assert payload["schema_id"] == EXECUTION_MANIFEST_SCHEMA_ID
    required = {
        "agent_run_id", "owner_scope_ref", "project_id", "roadmap_id",
        "planning_revision", "planning_content_hash", "definition_snapshot_ref", "normalized_dag",
        "done_contract", "queue_scope", "supervision_mode", "mutation_authority",
        "selected_route", "allowed_paths", "blocked_paths", "hotfiles",
        "max_parallel_activities", "retry_budget", "deadline_at",
        "start_request_id", "manifest_hash",
    }
    assert required <= set(payload)
    assert payload["manifest_hash"].startswith("sha256:")
    assert len(json.dumps(payload).encode("utf-8")) < 262_144


@pytest.mark.parametrize(("schema", "expected_code"), _CLOSED_SCOPE_ADVERSARIAL_CASES)
def test_rehashed_manifest_input_rejects_open_or_general_approval_schemas(
    schema,
    expected_code: str,
) -> None:
    read_model = _read_model_with_gate()
    roadmap = read_model["roadmap"]
    roadmap["gates"][0]["approval_scope_schema"] = deepcopy(schema)
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"][roadmap["roadmap_id"]]["content_hash"] = roadmap[
        "content_hash"
    ]

    with pytest.raises(ManifestBuildError) as raised:
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-closed-profile",
            policy=_policy(),
        )

    assert raised.value.code == expected_code


@pytest.mark.parametrize("mode", ["items", "iter", "get"])
@pytest.mark.parametrize("error_type", [RuntimeError, RecursionError])
def test_manifest_inherits_normalized_hostile_approval_mapping_failure(
    mode: str,
    error_type: type[Exception],
) -> None:
    read_model = _read_model_with_gate()
    roadmap = read_model["roadmap"]
    scope = {"type": "object", "additionalProperties": False}
    roadmap["gates"][0]["approval_scope_schema"] = scope
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"][roadmap["roadmap_id"]][
        "content_hash"
    ] = roadmap["content_hash"]
    roadmap["gates"][0]["approval_scope_schema"] = _HostileApprovalMapping(
        scope,
        mode,
        error_type,
    )

    with pytest.raises(ManifestBuildError) as raised:
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-hostile-scope",
            policy=_policy(),
        )

    assert raised.value.code == "approval_schema_capture_failed"
    assert raised.value.path == "$.roadmaps[0].gates[0].approval_scope_schema"


def test_manifest_has_only_an_exact_compact_definition_snapshot_reference():
    read_model = _read_model()
    snapshot = build_definition_authority_snapshot(read_model)
    manifest = build_execution_manifest(
        read_model,
        owner_scope_ref=OWNER,
        start_request_id="start-request-0001",
        policy=_policy(),
    )
    reference = manifest.to_payload()["definition_snapshot_ref"]

    assert reference == snapshot.reference_payload()
    assert set(reference) == {
        "schema_id", "project_id", "roadmap_id", "planning_revision",
        "planning_content_hash", "snapshot_hash",
    }
    assert "normalized_dag" not in reference
    assert manifest.definition_snapshot_ref.snapshot_hash == snapshot.snapshot_hash


def test_manifest_compatibility_projections_come_only_from_snapshot_payload():
    document = definition_fixture(include_draft=False)
    roadmap = document["roadmaps"][0]
    roadmap["nodes"].append(
        {
            "node_id": "ship",
            "kind": "milestone",
            "title": "Ship",
            "objective": "Finish the approved work.",
            "depends_on": [],
            "gate_ids": [],
            "deliverables": ["Handoff"],
            "allowed_paths": ["src/ship.py"],
            "blocked_paths": [],
            "capability_requirements": ["Python"],
            "verification_rule_ids": ["rule-static"],
        }
    )
    roadmap["edges"] = [{"from": "ship", "to": "prepare", "kind": "depends_on"}]
    roadmap["done_contract"]["required_node_ids"] = ["prepare", "ship"]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = roadmap["content_hash"]
    read_model = _read_model(document)
    snapshot_payload = build_definition_authority_snapshot(read_model).to_payload()

    manifest = build_execution_manifest(
        read_model, owner_scope_ref=OWNER, start_request_id="start-request-0007", policy=_policy()
    )

    assert manifest.normalized_dag.to_payload() == snapshot_payload["normalized_dag"]
    assert manifest.done_contract.to_value() == snapshot_payload["done_contract"]
    assert list(manifest.allowed_paths) == snapshot_payload["allowed_paths"]
    assert list(manifest.blocked_paths) == snapshot_payload["blocked_paths"]
    assert next(node for node in manifest.normalized_dag.to_payload()["nodes"] if node["node_id"] == "ship")["depends_on"] == ["prepare"]


@pytest.mark.parametrize(
    "selector_fragment",
    [
        {"patternProperties": {"^cl[a]im_owner$": {"type": "string"}}},
        {"propertyNames": {"not": {"not": {"pattern": "^claim_owner$"}}}},
    ],
)
def test_manifest_rejects_rehashed_transitive_property_name_bypasses(
    selector_fragment: dict,
) -> None:
    read_model = _read_model_with_gate()
    roadmap = read_model["roadmap"]
    roadmap["gates"][0]["approval_scope_schema"].update(selector_fragment)
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = roadmap[
        "content_hash"
    ]

    with pytest.raises(ManifestBuildError) as raised:
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-regex-bypass",
            policy=_policy(),
        )

    assert raised.value.code == "runtime_field_forbidden"


def test_manifest_preserves_rehashed_bounded_safe_property_name_selectors() -> None:
    read_model = _read_model_with_gate()
    roadmap = read_model["roadmap"]
    scope = roadmap["gates"][0]["approval_scope_schema"]
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
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = roadmap[
        "content_hash"
    ]

    manifest = build_execution_manifest(
        read_model,
        owner_scope_ref=OWNER,
        start_request_id="start-request-regex-positive",
        policy=_policy(),
    )

    assert manifest.normalized_dag.to_payload()["gates"][0]["approval_scope_schema"] == scope


def test_manifest_propagates_approval_schema_budget_as_contract_error() -> None:
    read_model = _read_model_with_gate()
    read_model["roadmap"]["gates"][0]["approval_scope_schema"] = {
        "type": "object",
        "additionalProperties": False,
        "propertyNames": {
            "anyOf": [
                {"const": f"public_{index}"}
                for index in range(APPROVAL_SCHEMA_MAX_COMBINATOR_ITEMS + 1)
            ]
        }
    }

    with pytest.raises(ManifestBuildError) as raised:
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-schema-budget",
            policy=_policy(),
        )

    assert raised.value.code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.roadmaps[0].gates[0].approval_scope_schema")


def test_manifest_validates_deep_raw_schema_before_snapshot_capture() -> None:
    read_model = _read_model_with_gate()
    read_model["roadmap"]["gates"][0]["approval_scope_schema"] = _nested_array_schema(1_500)

    with pytest.raises(ManifestBuildError) as raised:
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-deep-schema",
            policy=_policy(),
        )

    assert raised.value.code == "approval_schema_budget_exceeded"
    assert raised.value.path.startswith("$.roadmaps[0].gates[0].approval_scope_schema")


def test_manifest_accepts_schema_just_below_shared_depth_budget() -> None:
    read_model = _read_model_with_gate()
    roadmap = read_model["roadmap"]
    roadmap["gates"][0]["approval_scope_schema"] = _nested_array_schema(
        APPROVAL_SCHEMA_MAX_DEPTH - 1
    )
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    read_model["project"]["latest_approved_revision"][roadmap["roadmap_id"]]["content_hash"] = roadmap[
        "content_hash"
    ]

    manifest = build_execution_manifest(
        read_model,
        owner_scope_ref=OWNER,
        start_request_id="start-request-depth-boundary",
        policy=_policy(),
    )

    assert manifest.normalized_dag.to_payload()["gates"][0]["approval_scope_schema"] == roadmap[
        "gates"
    ][0]["approval_scope_schema"]


def test_definition_authority_change_changes_the_manifest_reference_and_hash():
    document = definition_fixture(include_draft=False)
    changed = deepcopy(document)
    changed["roadmaps"][0]["nodes"][0]["allowed_paths"].append("src/changed.py")
    changed["roadmaps"][0]["content_hash"] = compute_roadmap_content_hash(changed["roadmaps"][0])
    changed["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = changed["roadmaps"][0]["content_hash"]

    first = build_execution_manifest(
        _read_model(document), owner_scope_ref=OWNER, start_request_id="start-request-0006", policy=_policy()
    )
    second = build_execution_manifest(
        _read_model(changed), owner_scope_ref=OWNER, start_request_id="start-request-0006", policy=_policy()
    )

    assert first.definition_snapshot_ref.snapshot_hash != second.definition_snapshot_ref.snapshot_hash
    assert first.manifest_hash != second.manifest_hash


def test_manifest_is_recursively_immutable_and_payload_is_a_copy():
    manifest = _manifest()
    payload = manifest.to_payload()
    payload["normalized_dag"]["nodes"][0]["depends_on"].append("tamper")
    payload["selected_route"]["model"]["value"] = "tamper"

    assert manifest.to_payload()["normalized_dag"]["nodes"][0]["depends_on"] == []
    assert manifest.to_payload()["selected_route"]["model"]["value"] == "surface_default"
    with pytest.raises(FrozenInstanceError):
        manifest.agent_run_id = "changed"


def test_paths_are_normalized_sorted_and_hotfiles_must_be_in_scope():
    document = definition_fixture(include_draft=False)
    node = document["roadmaps"][0]["nodes"][0]
    node["allowed_paths"] = ["src/b.py", "src/a.py"]
    node["verification_rule_ids"] = ["rule-static"]
    document["roadmaps"][0]["content_hash"] = compute_roadmap_content_hash(document["roadmaps"][0])
    document["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = document["roadmaps"][0]["content_hash"]
    read_model = _read_model(document)

    manifest = build_execution_manifest(
        read_model,
        owner_scope_ref=OWNER,
        start_request_id="start-request-0002",
        policy=_policy(hotfiles=["src/b.py"]),
    )

    assert manifest.allowed_paths == ("src/a.py", "src/b.py")
    assert manifest.hotfiles == ("src/b.py",)
    with pytest.raises(ManifestBuildError, match="hotfiles must be allowed"):
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-0003",
            policy=_policy(hotfiles=["app.py"]),
        )


def test_absolute_or_traversal_planning_paths_fail_before_hashing():
    read_model = _read_model()
    read_model["roadmap"]["nodes"][0]["allowed_paths"] = ["C:/private/file.py"]

    with pytest.raises(ManifestBuildError) as raised:
        build_execution_manifest(
            read_model,
            owner_scope_ref=OWNER,
            start_request_id="start-request-0004",
            policy=_policy(),
        )

    assert raised.value.code in {"invalid_planning_definition", "invalid_repo_path", "plan_revision_conflict"}


def test_cycle_and_missing_dependency_never_produce_a_manifest():
    for dependency, expected in (("prepare", "dependency_cycle"), ("missing", "invalid_planning_definition")):
        read_model = _read_model()
        read_model["roadmap"]["nodes"][0]["depends_on"] = [dependency]
        with pytest.raises(ManifestBuildError) as raised:
            build_execution_manifest(
                read_model,
                owner_scope_ref=OWNER,
                start_request_id=f"start-{expected}-0001",
                policy=_policy(),
            )
        assert raised.value.code in {expected, "invalid_planning_definition", "missing_reference"}


def test_route_is_agent_supplied_bounded_and_secret_free():
    with pytest.raises(ExecutionContractError, match="route must originate"):
        _policy(selected_route={"entrypoint": "Planning"})
    with pytest.raises(ExecutionContractError) as sensitive:
        _policy(selected_route={"entrypoint": "/abc", "api_key": "forbidden"})
    assert sensitive.value.code == "sensitive_field_forbidden"


@pytest.mark.parametrize("parallelism", [0, 4, True])
def test_parallelism_is_bounded(parallelism):
    with pytest.raises(ExecutionContractError, match="parallelism"):
        _policy(max_parallel_activities=parallelism)


def test_structural_inputs_change_hash_but_not_planning_data():
    read_model = _read_model()
    before = deepcopy(read_model)
    first = build_execution_manifest(
        read_model,
        owner_scope_ref=OWNER,
        start_request_id="start-request-0005",
        policy=_policy(max_parallel_activities=1),
    )
    second = build_execution_manifest(
        read_model,
        owner_scope_ref=OWNER,
        start_request_id="start-request-0005",
        policy=_policy(max_parallel_activities=2),
    )

    assert first.manifest_hash != second.manifest_hash
    assert read_model == before
