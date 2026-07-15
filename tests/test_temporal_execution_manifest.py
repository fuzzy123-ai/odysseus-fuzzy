from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from src.planning_revision_store import PlanningRevisionStore
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


def test_same_inputs_produce_the_same_complete_manifest_hash():
    first = _manifest()
    second = _manifest()

    assert first == second
    payload = first.to_payload()
    assert payload["schema_id"] == EXECUTION_MANIFEST_SCHEMA_ID
    required = {
        "agent_run_id", "owner_scope_ref", "project_id", "roadmap_id",
        "planning_revision", "planning_content_hash", "normalized_dag",
        "done_contract", "queue_scope", "supervision_mode", "mutation_authority",
        "selected_route", "allowed_paths", "blocked_paths", "hotfiles",
        "max_parallel_activities", "retry_budget", "deadline_at",
        "start_request_id", "manifest_hash",
    }
    assert required <= set(payload)
    assert payload["manifest_hash"].startswith("sha256:")
    assert len(json.dumps(payload).encode("utf-8")) < 262_144


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
    from src.planning_definition_contract import compute_roadmap_content_hash
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
        assert raised.value.code in {expected, "invalid_planning_definition"}


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
