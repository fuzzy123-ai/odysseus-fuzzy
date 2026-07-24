import hashlib
import json
import os
from pathlib import Path

import pytest

from src.planning_mcp_service import (
    CANONICAL_ROADMAP_KIND,
    LEGACY_HARBOR_ROADMAP_KIND,
    PlanningMcpService,
    PlanningServiceError,
    planning_create_roadmap_draft,
    planning_get_context_pack,
    planning_list_roadmaps,
    planning_propose_patch,
    planning_read_roadmap,
    planning_search_roadmaps,
    planning_validate_roadmap,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _roadmap_payload(*, slice_count: int = 2) -> dict:
    return {
        "schema_version": 1,
        "kind": CANONICAL_ROADMAP_KIND,
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "revision": 1,
        "created_at": "2026-07-10T06:00:00Z",
        "updated_at": "2026-07-10T06:00:00Z",
        "title": "Core Planning Roadmap",
        "goal": "Ship a bounded planning service.",
        "status": "planned",
        "source_refs": ["src/planning_source_inventory.py"],
        "slices": [
            {
                "id": f"slice-{index}",
                "title": f"Slice {index}",
                "objective": "Implement a bounded read-only capability " + ("x" * 280),
                "class": "repo_only",
                "status": "planned",
                "depends_on": [f"slice-{index - 1}"] if index else [],
                "source_refs": ["src/planning_source_inventory.py"],
            }
            for index in range(slice_count)
        ],
        "gates": [
            {
                "id": "read-go",
                "class": "repo_only",
                "status": "open",
                "decision_needed": "Approve the bounded read contract.",
            }
        ],
        "gate_refs": ["read-go"],
        "dependency_refs": [],
        "verification": ["python -m pytest tests/test_planning_mcp_service.py"],
        "stop_rules": ["Stop on path escape."],
    }


@pytest.fixture
def planning_repo(tmp_path: Path) -> tuple[Path, Path]:
    roadmap = tmp_path / "docs" / "plans" / "core-roadmap.json"
    payload = _roadmap_payload()
    payload["provider_note"] = "token=synthetic-secret-value"
    _write_json(roadmap, payload)
    _write_json(tmp_path / "docs" / "plans" / "not-a-roadmap.json", {"name": "settings"})
    _write_json(
        tmp_path / "specs" / "roadmaps" / "legacy.v1.json",
        {
            "schema_version": 1,
            "plan_id": "legacy-map",
            "title": "Legacy Search Roadmap",
            "goal": "Keep legacy planning discoverable.",
            "status": "running",
            "graph_nodes": [{"node_id": "legacy-slice", "title": "Legacy Slice", "status": "ready"}],
            "source_refs": ["docs/plans/core-roadmap.json"],
            "verification": [],
            "stop_rules": [],
        },
    )
    return tmp_path, roadmap


def _definition_v2() -> dict:
    from src.planning_definition_contract import compute_roadmap_content_hash

    roadmap = {
        "roadmap_id": "core-map",
        "project_id": "demo-project",
        "revision": 1,
        "content_hash": "sha256:" + ("0" * 64),
        "revision_state": "approved",
        "title": "Core Planning Roadmap",
        "objective": "Ship a bounded definition service.",
        "assumptions": [],
        "constraints": ["Planning never stores Agent runtime state."],
        "nodes": [
            {
                "node_id": "service-contract",
                "kind": "work",
                "title": "Definition service contract",
                "objective": "Expose immutable planning intent.",
                "depends_on": [],
                "gate_ids": ["definition-go"],
                "deliverables": ["Definition-only MCP response"],
                "allowed_paths": ["src/planning_mcp_service.py"],
                "blocked_paths": [],
                "capability_requirements": [],
                "verification_rule_ids": ["definition-tests"],
            }
        ],
        "edges": [],
        "gates": [
            {
                "gate_id": "definition-go",
                "kind": "repo",
                "title": "Definition contract gate",
                "blocks": ["service-contract"],
                "decision_needed": "Confirm the immutable definition contract.",
                "safe_default": "Keep the definition read-only.",
                "approval_scope_schema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                    "additionalProperties": False,
                },
                "required_verification_rule_ids": ["definition-tests"],
            }
        ],
        "done_contract": {
            "required_node_ids": ["service-contract"],
            "required_gate_ids": ["definition-go"],
            "verification_rules": [
                {
                    "rule_id": "definition-tests",
                    "kind": "test",
                    "description": "The focused definition contract tests pass.",
                }
            ],
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": ["src/planning_mcp_service.py"],
        "created_at": "2026-07-15T06:00:00Z",
        "updated_at": "2026-07-15T06:00:00Z",
    }
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    return {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": "demo-project",
            "title": "Demo project",
            "objective": "Exercise the Planning MCP definition boundary.",
            "scope": {"in": ["Planning definitions"], "out": ["Agent execution"]},
            "constraints": ["No runtime state in Planning."],
            "roadmap_refs": ["core-map"],
            "latest_approved_revision": {
                "core-map": {
                    "revision": 1,
                    "content_hash": roadmap["content_hash"],
                }
            },
            "draft_refs": [],
        },
        "roadmaps": [roadmap],
    }


@pytest.fixture
def definition_service(tmp_path: Path) -> tuple[PlanningMcpService, dict]:
    from src.planning_revision_store import PlanningRevisionStore

    definition = _definition_v2()
    store = PlanningRevisionStore(
        [("owner-1", definition, "demo-project.json")],
        cursor_secret=b"definition-service-test-secret",
    )
    return (
        PlanningMcpService(
            tmp_path,
            definition_store=store,
            definition_owner="owner-1",
        ),
        definition,
    )


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            *(str(key).lower() for key in value),
            *(nested for item in value.values() for nested in _nested_keys(item)),
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _nested_keys(item)}
    return set()


def test_definition_v2_validation_is_content_free_and_recursively_rejects_runtime_fields(
    tmp_path: Path,
):
    from copy import deepcopy

    from src.planning_definition_contract import (
        GATE_RUNTIME_FIELD_DENYLIST,
        RUNTIME_FIELD_DENYLIST,
    )

    service = PlanningMcpService(tmp_path)
    definition = _definition_v2()
    result = service.validate_definition(definition)

    assert result == {
        "schema_id": "odysseus.planning.definition_validation.v2",
        "validation": {
            "schema_id": "odysseus.planning.definition.v2",
            "project_id": "demo-project",
            "roadmap_hashes": [
                {
                    "roadmap_id": "core-map",
                    "revision": 1,
                    "content_hash": definition["roadmaps"][0]["content_hash"],
                }
            ],
        },
        "read_only": True,
        "writes_performed": False,
    }
    for forbidden in sorted(RUNTIME_FIELD_DENYLIST):
        candidate = deepcopy(definition)
        candidate["project"]["scope"]["deep"] = {forbidden: "synthetic"}
        with pytest.raises(PlanningServiceError) as rejected:
            service.validate_definition(candidate)
        assert rejected.value.code == "runtime_field_forbidden"
    for forbidden in sorted(GATE_RUNTIME_FIELD_DENYLIST):
        candidate = deepcopy(definition)
        candidate["roadmaps"][0]["gates"][0][forbidden] = "synthetic"
        with pytest.raises(PlanningServiceError) as rejected:
            service.validate_definition(candidate)
        assert rejected.value.code == "runtime_field_forbidden"


def test_definition_gate_read_returns_only_requirements_targets_and_safe_defaults(
    definition_service,
):
    from src.planning_definition_contract import (
        GATE_RUNTIME_FIELD_DENYLIST,
        RUNTIME_FIELD_DENYLIST,
    )

    service, definition = definition_service
    result = service.read_gate_definitions(
        "demo-project",
        "core-map",
        revision_or_latest_approved=1,
        node_id="service-contract",
    )

    assert result["schema_id"] == "odysseus.planning.gate_definitions.v2"
    assert result["revision"] == 1
    assert result["content_hash"] == definition["roadmaps"][0]["content_hash"]
    assert result["gate_definitions"] == [definition["roadmaps"][0]["gates"][0]]
    assert result["read_only"] is True
    assert result["writes_performed"] is False
    assert _nested_keys(result).isdisjoint(RUNTIME_FIELD_DENYLIST | GATE_RUNTIME_FIELD_DENYLIST)


def test_definition_gate_read_filters_by_node_and_never_falls_back_to_runtime_status(
    definition_service,
):
    service, _definition = definition_service

    result = service.read_gate_definitions(
        "demo-project",
        "core-map",
        node_id="unrelated-node",
    )

    assert result["gate_definitions"] == []
    assert "blockers" not in result
    assert "next_safe_actions" not in result
    assert "status" not in json.dumps(result, sort_keys=True).lower()


def test_definition_agent_handoff_is_exact_hash_pinned_and_non_launching(definition_service):
    from src.planning_agent_handoff import FORBIDDEN_HANDOFF_FIELDS

    service, definition = definition_service
    expected_hash = definition["roadmaps"][0]["content_hash"]

    result = service.create_agent_handoff(
        "demo-project",
        "core-map",
        revision_or_latest_approved="latest_approved",
    )

    assert result["schema_id"] == "odysseus.agent.plan_handoff.v1"
    assert result["revision"] == 1
    assert result["content_hash"] == expected_hash
    assert result["composer_text"] == f"/abc run roadmap:core-map@1 hash:{expected_hash}"
    assert result["requested_entrypoint"] == "/abc"
    assert result["launch_authorized"] is False
    assert result["read_only"] is True
    assert set(result).isdisjoint(FORBIDDEN_HANDOFF_FIELDS)


@pytest.mark.parametrize(
    ("tool", "replacement"),
    [
        ("planning_mark_status", {"replacement_surface": "agent"}),
        ("planning_gate_status", {"replacement_tool": "planning_read_gate_definitions"}),
    ],
)
def test_deprecated_runtime_tools_return_stable_zero_mutation_response(
    tmp_path: Path,
    tool: str,
    replacement: dict,
):
    service = PlanningMcpService(tmp_path)

    first = service.deprecated_tool_response(tool)
    second = service.deprecated_tool_response(tool)

    assert first == second
    assert first == {
        "schema_id": "odysseus.planning.deprecated_tool.v1",
        "tool": tool,
        "error": "deprecated_tool",
        **replacement,
        "read_only": True,
        "writes_performed": False,
    }


def test_definition_tools_fail_closed_without_an_injected_owner_scoped_store(tmp_path: Path):
    service = PlanningMcpService(tmp_path)

    for operation in (
        lambda: service.read_gate_definitions("demo-project", "core-map"),
        lambda: service.create_agent_handoff("demo-project", "core-map"),
    ):
        with pytest.raises(PlanningServiceError) as unavailable:
            operation()
        assert unavailable.value.code == "definition_store_unavailable"


def test_list_returns_only_roadmaps_with_stable_bounded_public_metadata(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root, preview_chars=80)

    first = service.list_roadmaps()
    second = service.list_roadmaps()

    assert first["schema"] == "odysseus.planning.roadmap_list.v1"
    assert first["read_only"] is True
    assert first["writes_supported"] is False
    assert first["summary"]["total_roadmaps"] == 2
    assert [item["source_id"] for item in first["roadmaps"]] == [
        item["source_id"] for item in second["roadmaps"]
    ]
    assert all(item["repo_relative"] for item in first["roadmaps"])
    assert all(not item["absolute_path_recorded"] for item in first["roadmaps"])
    assert all(len(item["preview"]) <= 80 for item in first["roadmaps"])
    serialized = json.dumps(first)
    assert str(root) not in serialized
    assert "synthetic-secret-value" not in serialized
    assert "not-a-roadmap.json" not in serialized


def test_read_by_path_and_source_id_has_same_logical_identity_and_redacted_raw_preview(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)
    listed = service.list_roadmaps(query="Core Planning")["roadmaps"][0]

    by_path = service.read_roadmap("docs/plans/core-roadmap.json", include_raw_preview_chars=10_000)
    by_id = service.read_roadmap(listed["source_id"], include_raw_preview_chars=10_000)

    assert by_path["schema"] == "odysseus.planning.roadmap_read.v1"
    assert by_path["logical_ids"] == by_id["logical_ids"]
    assert by_path["logical_ids"]["project_id"] == "demo-project"
    assert by_path["logical_ids"]["roadmap_id"] == "core-map"
    assert by_path["source"]["source_ref"] == "docs/plans/core-roadmap.json"
    assert by_path["roadmap"]["goal"] == "Ship a bounded planning service."
    assert len(by_path["slices"]) == 2
    assert by_path["gates"][0]["id"] == "read-go"
    assert "synthetic-secret-value" not in by_path["raw_json_preview"]
    assert "[redacted]" in by_path["raw_json_preview"]
    assert str(root) not in json.dumps(by_path)


def test_search_matches_structural_content_and_applies_filters(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    gate_hit = service.search_roadmaps("bounded read contract")
    running = service.search_roadmaps("legacy", filters={"status": "running"})
    excluded = service.search_roadmaps("legacy", filters={"status": "done"})

    assert gate_hit["summary"]["matches"] == 1
    assert gate_hit["results"][0]["roadmap_id"] == "core-map"
    assert running["results"][0]["roadmap_id"] == "legacy-map"
    assert excluded["results"] == []


@pytest.mark.parametrize(
    "unsafe_ref",
    [
        "../docs/plans/core-roadmap.json",
        "docs/plans/../plans/core-roadmap.json",
        "docs/plans/%2e%2e/%2e%2e/private.json",
        "C:\\private\\roadmap.json",
        "\\\\server\\share\\roadmap.json",
        "/home/alice/roadmap.json",
        "file://docs/plans/core-roadmap.json",
        "src/not-planning.json",
        "docs/plans/*.json",
    ],
)
def test_read_rejects_traversal_absolute_uri_and_pattern_paths(planning_repo, unsafe_ref):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    with pytest.raises(PlanningServiceError):
        service.read_roadmap(unsafe_ref)


def test_symlink_escape_is_not_listed_or_read(planning_repo, tmp_path: Path):
    root, _roadmap = planning_repo
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    _write_json(outside, _roadmap_payload())
    link = root / "docs" / "plans" / "escape-roadmap.json"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")

    service = PlanningMcpService(root)

    assert "escape-roadmap.json" not in json.dumps(service.list_roadmaps())
    with pytest.raises(PlanningServiceError) as exc:
        service.read_roadmap("docs/plans/escape-roadmap.json")
    assert exc.value.code == "path_escape"


def test_validation_is_structured_read_only_and_does_not_mutate_input():
    candidate = _roadmap_payload()
    before = json.dumps(candidate, sort_keys=True)

    valid = planning_validate_roadmap(candidate)
    invalid_candidate = _roadmap_payload()
    invalid_candidate["slices"].append(dict(invalid_candidate["slices"][0]))
    invalid_candidate["source_refs"] = ["../../private.txt"]
    invalid = planning_validate_roadmap(invalid_candidate)

    assert valid["valid"] is True
    assert valid["writes_performed"] is False
    assert json.dumps(candidate, sort_keys=True) == before
    assert invalid["valid"] is False
    assert {item["code"] for item in invalid["errors"]} >= {"duplicate_slice_id", "unsafe_source_ref"}
    assert all(set(item) == {"code", "field", "message"} for item in invalid["errors"])


def test_canonical_validation_rejects_id_path_mismatch_without_exposing_value():
    candidate = _roadmap_payload()
    result = planning_validate_roadmap(
        candidate,
        source_ref="docs/plans/roadmaps/different.roadmap.json",
    )

    assert result["valid"] is False
    mismatch = next(item for item in result["errors"] if item["code"] == "roadmap_id_path_mismatch")
    assert mismatch["field"] == "$.roadmap_id"
    assert "different" not in mismatch["message"]


def test_odysseus_planning_kind_is_canonical_and_harbor_kind_is_alias():
    canonical = _roadmap_payload()
    legacy = _roadmap_payload()
    legacy["kind"] = LEGACY_HARBOR_ROADMAP_KIND

    assert planning_validate_roadmap(canonical)["valid"] is True
    assert planning_validate_roadmap(legacy)["valid"] is True

    canonical_bad = dict(canonical)
    canonical_bad["roadmap_id"] = "wrong"
    result = planning_validate_roadmap(
        canonical_bad,
        source_ref="docs/plans/roadmaps/core-map.roadmap.json",
    )
    assert result["valid"] is False
    assert any(item["code"] == "roadmap_id_path_mismatch" for item in result["errors"])


def test_context_pack_is_bounded_source_linked_prioritizes_node_and_contains_no_raw_dump(tmp_path: Path):
    path = tmp_path / "docs" / "plans" / "large-roadmap.json"
    _write_json(path, _roadmap_payload(slice_count=40))
    service = PlanningMcpService(tmp_path, context_budget_bytes=4_096)

    pack = service.get_context_pack(
        "docs/plans/large-roadmap.json",
        task="Implement the selected planning slice.",
        node_id="slice-37",
        max_items=24,
    )

    assert pack["schema"] == "odysseus.planning.context_pack.v1"
    assert pack["roadmap_ref"]["source_ref"] == "docs/plans/large-roadmap.json"
    assert pack["slices"][0]["id"] == "slice-37"
    assert pack["raw_content_included"] is False
    assert pack["absolute_paths_visible"] is False
    assert pack["clipped"] is True
    assert pack["payload_bytes"] <= 4_096
    assert len(json.dumps(pack, separators=(",", ":")).encode("utf-8")) <= 4_096


def test_module_wrappers_match_service_contract(planning_repo):
    root, _roadmap = planning_repo

    listed = planning_list_roadmaps(root)
    source_id = listed["roadmaps"][0]["source_id"]
    read = planning_read_roadmap(root, source_id)
    searched = planning_search_roadmaps(root, "Core Planning")
    context = planning_get_context_pack(root, source_id, max_items=2)

    assert listed["schema"].endswith("roadmap_list.v1")
    assert read["schema"].endswith("roadmap_read.v1")
    assert searched["schema"].endswith("roadmap_search.v1")
    assert context["schema"].endswith("context_pack.v1")


def test_read_and_context_pack_do_not_modify_the_canonical_file(planning_repo):
    root, roadmap = planning_repo
    before = roadmap.read_bytes()
    service = PlanningMcpService(root)

    service.read_roadmap("docs/plans/core-roadmap.json", include_raw_preview_chars=200)
    service.get_context_pack("docs/plans/core-roadmap.json", task="Read only")

    assert roadmap.read_bytes() == before


def _draft_input() -> dict:
    return {
        "title": "Planning Service Follow-up",
        "goal": "Create a safe roadmap draft without writing it.",
        "mode": "Standard ABC",
        "source_refs": ["docs/plans/planning-mcp-roadmap.json"],
        "slices": [
            {
                "id": "PMCP-DRAFT-1",
                "title": "Draft service",
                "objective": "Create the validated in-memory roadmap draft.",
                "class": "repo_only",
                "owner": "Bob",
                "allowed_paths": ["src/planning_mcp_service.py"],
                "tests": ["python -m pytest tests/test_planning_mcp_service.py"],
                "done_when": "Draft validation is green and no file is written.",
            }
        ],
        "gates": [
            {
                "id": "PLANNING-WRITE-GO",
                "class": "needs_operator_go",
                "status": "open",
                "decision_needed": "Approve persistence in a later slice.",
                "blocks": ["persist-draft"],
            }
        ],
        "stop_rules": ["Stop before any write or apply action."],
        "verification": ["python -m pytest tests/test_planning_mcp_service.py"],
    }


def test_create_roadmap_draft_is_valid_deterministic_and_dry_run_only():
    source = _draft_input()
    before = json.dumps(source, sort_keys=True)

    first = planning_create_roadmap_draft(source)
    second = planning_create_roadmap_draft(source)

    assert first["schema"] == "odysseus.planning.roadmap_draft.v1"
    assert first["draft_id"] == second["draft_id"]
    assert first["draft_hash"] == second["draft_hash"]
    assert first["dry_run"] is True
    assert first["writes_performed"] is False
    assert first["events_emitted"] is False
    assert first["notifications_emitted"] is False
    assert first["persist_supported"] is False
    assert first["required_persist_gate"] == "PLANNING-WRITE-GO"
    assert first["validation"]["valid"] is True
    assert first["roadmap"]["schema_version"] == 1
    assert first["roadmap"]["mode"] == "Standard ABC"
    assert first["roadmap"]["slices"][0]["id"] == "PMCP-DRAFT-1"
    assert json.dumps(source, sort_keys=True) == before


@pytest.mark.parametrize(
    "change, expected_code",
    [
        ({"dry_run": False}, "write_not_supported"),
        ({"source_refs": ["../../private.json"]}, "unsafe_reference"),
        ({"goal": "token=synthetic-private-token"}, "forbidden_content"),
        ({"mode": "Live Provider Mode"}, "invalid_draft_mode"),
        ({"slices": []}, "invalid_draft_slices"),
    ],
)
def test_create_roadmap_draft_fails_closed_for_write_unsafe_and_invalid_inputs(change, expected_code):
    payload = _draft_input()
    payload.update(change)

    with pytest.raises(PlanningServiceError) as exc:
        planning_create_roadmap_draft(payload)

    assert exc.value.code == expected_code


def test_propose_patch_is_deterministic_bounded_auditable_and_non_mutating(tmp_path: Path):
    roadmap = tmp_path / "docs" / "plans" / "core-roadmap.json"
    _write_json(roadmap, _roadmap_payload())
    before = roadmap.read_bytes()
    service = PlanningMcpService(tmp_path)
    current = service.list_roadmaps()["roadmaps"][0]
    proposal = {
        "base_source_hash": current["source_hash"],
        "base_revision": 1,
        "changes": {
            "goal": "Ship a validated dry-run patch proposal.",
            "status": "running",
        },
    }

    first = service.propose_patch(
        current["source_id"],
        proposal,
        reason="Prepare the next bounded implementation slice.",
    )
    second = service.propose_patch(
        current["source_id"],
        proposal,
        reason="Prepare the next bounded implementation slice.",
    )

    assert first["schema"] == "odysseus.planning.patch_proposal.v1"
    assert first["patch_id"] == second["patch_id"]
    assert first["status"] == "ready"
    assert first["ready_for_apply"] is True
    assert first["dry_run"] is True
    assert first["writes_performed"] is False
    assert first["events_emitted"] is False
    assert first["notifications_emitted"] is False
    assert first["apply_supported"] is False
    assert first["required_apply_gate"] == "PLANNING-APPLY-GO"
    assert first["base_source_hash"] == current["source_hash"]
    assert first["base_revision"] == 1
    assert first["candidate_revision"] == 2
    assert first["diff"]["operation_count"] == 2
    assert {op["path"] for op in first["operations"]} == {"/goal", "/status"}
    assert all(set(op["before"]) >= {"type", "hash"} for op in first["operations"])
    assert first["conflicts"] == []
    assert first["warnings"] == []
    assert first["validation"]["valid"] is True
    assert roadmap.read_bytes() == before


def test_propose_patch_reports_optimistic_concurrency_conflicts(tmp_path: Path):
    roadmap = tmp_path / "docs" / "plans" / "core-roadmap.json"
    _write_json(roadmap, _roadmap_payload())
    service = PlanningMcpService(tmp_path)

    result = service.propose_patch(
        "docs/plans/core-roadmap.json",
        {
            "base_source_hash": "sha256:" + ("0" * 64),
            "base_revision": 99,
            "changes": {"goal": "A conflict-safe candidate."},
        },
        reason="Verify optimistic concurrency.",
    )

    assert result["status"] == "conflict"
    assert result["ready_for_apply"] is False
    assert {item["code"] for item in result["conflicts"]} == {"source_hash_mismatch", "revision_mismatch"}
    assert result["writes_performed"] is False


def test_propose_patch_warns_without_base_evidence_and_rejects_forbidden_fields(tmp_path: Path):
    roadmap = tmp_path / "docs" / "plans" / "core-roadmap.json"
    _write_json(roadmap, _roadmap_payload())
    service = PlanningMcpService(tmp_path)

    warning = service.propose_patch(
        "docs/plans/core-roadmap.json",
        {"changes": {"summary": "Bounded summary update."}},
        reason="Preview a summary update.",
    )
    assert {item["code"] for item in warning["warnings"]} == {
        "base_source_hash_missing",
        "base_revision_missing",
    }

    with pytest.raises(PlanningServiceError) as exc:
        service.propose_patch(
            "docs/plans/core-roadmap.json",
            {"changes": {"revision": 400}},
            reason="Attempt a forbidden revision change.",
        )
    assert exc.value.code == "forbidden_patch_field"


def test_patch_wrapper_is_dry_run_only_and_rejects_sensitive_content(tmp_path: Path):
    roadmap = tmp_path / "docs" / "plans" / "core-roadmap.json"
    _write_json(roadmap, _roadmap_payload())

    result = planning_propose_patch(
        tmp_path,
        "docs/plans/core-roadmap.json",
        {"changes": {"goal": "Wrapper-generated patch proposal."}},
        reason="Exercise the public wrapper.",
    )
    assert result["dry_run"] is True

    with pytest.raises(PlanningServiceError) as write_exc:
        planning_propose_patch(
            tmp_path,
            "docs/plans/core-roadmap.json",
            {"changes": {"goal": "Do not apply."}},
            reason="Reject apply.",
            dry_run=False,
        )
    assert write_exc.value.code == "apply_not_supported"

    with pytest.raises(PlanningServiceError) as secret_exc:
        planning_propose_patch(
            tmp_path,
            "docs/plans/core-roadmap.json",
            {"changes": {"summary": "password=synthetic-private-value"}},
            reason="Reject sensitive content.",
        )
    assert secret_exc.value.code == "forbidden_content"


def test_context_pack_memory_is_opt_in_and_accepts_only_matching_planning_capsules(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)
    listed = service.list_roadmaps(query="Core Planning")["roadmaps"][0]
    candidates = [
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": listed["source_id"],
            "source_ref": listed["source_ref"],
            "source_hash": listed["source_hash"],
            "project_id": "demo-project",
            "roadmap_id": "core-map",
            "precedence_rank": 100,
            "preview": "Accepted context token=synthetic-private-value",
            "text": "RAW MEMORY BODY",
        },
        {
            "source": "planning_source",
            "source_status": "deleted",
            "acceptance_status": "accepted",
            "source_id": "repo-plan:deleted",
            "source_ref": "docs/plans/deleted.json",
            "project_id": "demo-project",
        },
        {
            "source": "other_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": "repo-plan:other",
            "source_ref": "docs/plans/other.json",
            "project_id": "demo-project",
        },
    ]
    before = json.dumps(candidates, sort_keys=True)

    default_pack = service.get_context_pack(listed["source_id"], max_items=8)
    memory_pack = service.get_context_pack(
        listed["source_id"],
        max_items=8,
        include_memory=True,
        memory_capsules=candidates,
    )
    encoded = json.dumps(memory_pack)

    assert default_pack["memory_included"] is False
    assert default_pack["memory"] == []
    assert default_pack["memory_summary"]["requested"] is False
    assert memory_pack["memory_included"] is True
    assert memory_pack["memory_source"] == "injected"
    assert [item["source_id"] for item in memory_pack["memory"]] == [listed["source_id"]]
    assert memory_pack["memory"][0]["precedence_rank"] == 100
    assert memory_pack["memory"][0]["provenance"]["source_ref"] == listed["source_ref"]
    assert "RAW MEMORY BODY" not in encoded
    assert "synthetic-private-value" not in encoded
    assert "[redacted]" in encoded
    assert json.dumps(candidates, sort_keys=True) == before


def test_context_pack_can_use_readonly_repo_capsule_builder(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    pack = service.get_context_pack(
        "docs/plans/core-roadmap.json",
        include_memory=True,
        max_items=6,
    )

    assert pack["memory_source"] == "repo_source_builder"
    assert pack["memory_summary"]["requested"] is True
    assert pack["memory_summary"]["returned"] == 2
    target = next(item for item in pack["memory"] if item["source_ref"] == "docs/plans/core-roadmap.json")
    assert target["acceptance_status"] == "accepted"
    assert target["raw_body_included"] is False
    assert {item["source_ref"] for item in pack["memory"]} == {
        "docs/plans/core-roadmap.json",
        "specs/roadmaps/legacy.v1.json",
    }


def test_context_pack_exposes_real_roadmap_lens_summary_for_planruntime_payload(tmp_path: Path):
    roadmap = tmp_path / "specs" / "roadmaps" / "runtime.v1.json"
    _write_json(roadmap, {
        "schema_version": 1,
        "plan_id": "runtime-plan",
        "title": "Runtime Plan",
        "goal": "Provide validated graph evidence.",
        "status": "running",
        "format_decision": {"source_of_truth": "json"},
        "version_horizons": [{"id": "v1"}],
        "graph_nodes": [
            {
                "id": "runtime-one",
                "title": "Runtime One",
                "kind": "feature",
                "priority_rank": 1,
                "horizon": "v1",
                "target_version": "v1",
                "status": "ready",
                "depends_on": [],
                "unlocks": [],
                "gates": ["repo only"],
                "source_refs": ["src/plan_runtime.py"],
                "deliverables": ["bounded graph"],
            }
        ],
        "recommended_active_node": "runtime-one",
        "next_actions": [{"node_id": "runtime-one"}],
        "plan_graph_projection": {"status_mapping": {}},
        "source_refs": ["src/plan_runtime.py"],
        "verification": ["focused tests"],
        "stop_rules": ["read only"],
    })
    service = PlanningMcpService(tmp_path)

    pack = service.get_context_pack("specs/roadmaps/runtime.v1.json", max_items=8)
    lens = pack["roadmap_lens"]

    assert lens["available"] is True
    assert lens["projection"] == "roadmap_lens"
    assert lens["evidence_ref"] == "specs/roadmaps/runtime.v1.json"
    assert lens["active_node_id"] == "runtime-one"
    assert any(node["label"] == "Runtime One" for node in lens["nodes"])
    assert lens["source_of_truth"] is False
    assert lens["raw_content_included"] is False


def test_non_planruntime_context_pack_marks_lens_evidence_incomplete(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    pack = service.get_context_pack("docs/plans/core-roadmap.json", max_items=8)
    lens = pack["roadmap_lens"]

    assert lens["available"] is False
    assert lens["projection"] == "structured_read_evidence"
    assert lens["reason"] == "roadmap_not_planruntime_compatible"
    assert lens["slice_count"] == 2
    assert lens["incomplete"] is True
    assert lens["source_of_truth"] is False


def test_context_pack_memory_and_lens_obey_hard_byte_budget(tmp_path: Path):
    roadmap = tmp_path / "docs" / "plans" / "budget-roadmap.json"
    _write_json(roadmap, _roadmap_payload(slice_count=40))
    service = PlanningMcpService(tmp_path, context_budget_bytes=4_096, preview_chars=240)
    listed = service.list_roadmaps()["roadmaps"][0]
    candidates = [
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": f"repo-plan:related-{index}",
            "source_ref": f"docs/plans/related-{index}.json",
            "project_id": "demo-project",
            "roadmap_id": "related-map",
            "precedence_rank": 100 - index,
            "preview": "bounded-memory-preview-" + ("x" * 500),
        }
        for index in range(30)
    ]

    pack = service.get_context_pack(
        listed["source_id"],
        include_memory=True,
        memory_capsules=candidates,
        max_items=24,
    )
    size = len(json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    assert pack["payload_bytes"] <= 4_096
    assert size <= 4_096
    assert pack["clipped"] is True
    assert pack["memory_summary"]["truncated"] is True
    assert pack["memory_summary"]["incomplete"] is True


def test_context_pack_rejects_non_boolean_memory_option(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    with pytest.raises(PlanningServiceError) as exc:
        service.get_context_pack("docs/plans/core-roadmap.json", include_memory="yes")

    assert exc.value.code == "invalid_memory_option"


def test_read_document_resolves_stable_ids_and_returns_bounded_viewer_payload(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    document = service.read_document(
        "demo-project",
        "core-map",
        max_items=8,
        canonical_json_chars=512,
        include_memory=True,
    )
    encoded = json.dumps(document, ensure_ascii=False)

    assert document["schema"] == "odysseus.planning.roadmap_document.v1"
    assert document["read_only"] is True
    assert document["writes_supported"] is False
    assert document["project_id"] == "demo-project"
    assert document["roadmap_id"] == "core-map"
    assert document["title"] == "Core Planning Roadmap"
    assert document["goal"] == "Ship a bounded planning service."
    assert len(document["tasks"]) == 2
    assert document["tasks"] == document["slices"]
    assert document["gates"][0]["id"] == "read-go"
    assert {section["id"] for section in document["readable_sections"]} == {
        "summary", "tasks", "gates", "sources", "data",
    }
    assert document["canonical"]["projection"]["project_id"] == "demo-project"
    assert document["canonical"]["projection_hash"].startswith("sha256:")
    canonical_bytes = json.dumps(
        document["canonical"]["projection"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert document["canonical"]["projection_hash"] == "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
    assert document["canonical"]["source_hash"] == document["source_hash"]
    assert document["canonical"]["raw_source_included"] is False
    assert document["lens_summary"]["evidence_ref"] == "docs/plans/core-roadmap.json"
    assert document["memory_summary"]["requested"] is True
    assert document["memory_refs"]
    assert document["raw_content_included"] is False
    assert document["absolute_paths_visible"] is False
    assert document["payload_bytes"] <= 65_536
    assert str(root) not in encoded
    assert "synthetic-secret-value" not in encoded


def test_read_document_canonical_projection_and_preview_are_explicitly_truncated(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    document = service.read_document(
        "demo-project",
        "core-map",
        max_items=1,
        canonical_json_chars=40,
    )

    assert len(document["tasks"]) == 1
    assert document["canonical"]["json_preview_chars"] == 40
    assert document["canonical"]["truncated"] is True
    assert document["truncated"] is True
    assert document["incomplete"] is True
    assert document["budget"] == {
        "max_items": 1,
        "canonical_json_chars": 40,
        "payload_budget_bytes": 65_536,
    }


def test_read_document_supports_discovered_derived_ids(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)
    legacy = next(item for item in service.list_roadmaps()["roadmaps"] if item["roadmap_id"] == "legacy-map")

    document = service.read_document(legacy["project_id"], legacy["roadmap_id"])

    assert document["project_id"] == legacy["project_id"]
    assert document["roadmap_id"] == "legacy-map"
    assert document["source_ref"] == "specs/roadmaps/legacy.v1.json"


def test_read_document_fails_closed_for_not_found_ambiguous_ids_and_invalid_budgets(tmp_path: Path):
    first = _roadmap_payload()
    second = _roadmap_payload()
    second["title"] = "Duplicate identity"
    _write_json(tmp_path / "docs" / "plans" / "one.json", first)
    _write_json(tmp_path / "docs" / "plans" / "two.json", second)
    service = PlanningMcpService(tmp_path)

    with pytest.raises(PlanningServiceError) as ambiguous:
        service.read_document("demo-project", "core-map")
    assert ambiguous.value.code == "roadmap_document_ambiguous"

    with pytest.raises(PlanningServiceError) as missing:
        service.read_document("demo-project", "missing-map")
    assert missing.value.code == "roadmap_document_not_found"

    with pytest.raises(PlanningServiceError) as invalid_id:
        service.read_document("Demo Project", "core-map")
    assert invalid_id.value.code == "invalid_document_id"

    with pytest.raises(PlanningServiceError) as invalid_budget:
        service.read_document("demo-project", "core-map", max_items=25)
    assert invalid_budget.value.code == "invalid_document_budget"


def test_read_document_obeys_hard_payload_budget_for_large_roadmap(tmp_path: Path):
    roadmap = tmp_path / "docs" / "plans" / "large-document.json"
    _write_json(roadmap, _roadmap_payload(slice_count=100))
    service = PlanningMcpService(tmp_path)

    document = service.read_document(
        "demo-project",
        "core-map",
        max_items=24,
        canonical_json_chars=16_384,
    )
    size = len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    assert document["payload_bytes"] <= 65_536
    assert size <= 65_536
    assert document["truncated"] is True
    assert document["incomplete"] is True


def _document_edit_fixture(tmp_path: Path) -> tuple[PlanningMcpService, Path, dict, dict]:
    roadmap = tmp_path / "docs" / "plans" / "editable-roadmap.json"
    _write_json(roadmap, _roadmap_payload())
    service = PlanningMcpService(tmp_path)
    document = service.read_document("demo-project", "core-map")
    bases = {
        "base_source_hash": document["source_hash"],
        "base_revision": document["revision"],
        "base_projection_hash": document["canonical"]["projection_hash"],
    }
    return service, roadmap, document, bases


def test_document_summary_edit_is_deterministic_dry_run_and_keeps_source_byte_identical(tmp_path: Path):
    service, roadmap, _document, bases = _document_edit_fixture(tmp_path)
    before = roadmap.read_bytes()
    request = {
        "section": "summary",
        "section_id": "summary",
        "proposed_value": "A concise document summary.",
        "reason": "Clarify the roadmap document.",
        **bases,
    }

    first = service.propose_document_edit("demo-project", "core-map", request)
    second = service.propose_document_edit("demo-project", "core-map", request)

    assert first["schema"] == "odysseus.planning.roadmap_document_edit_proposal.v1"
    assert first["status"] == "ready"
    assert first["ready_for_apply"] is True
    assert first["operations"][0]["path"] == "/summary"
    assert first["draft_id"] == second["draft_id"]
    assert first["patch_id"] == second["patch_id"]
    assert first["dry_run"] is True
    assert first["writes_performed"] is False
    assert first["events_emitted"] is False
    assert first["notifications_emitted"] is False
    assert first["apply_supported"] is False
    assert first["required_apply_gate"] == "PLANNING-APPLY-GO"
    assert roadmap.read_bytes() == before


def test_document_task_edit_targets_stable_task_id_and_rejects_unknown_task(tmp_path: Path):
    service, roadmap, _document, bases = _document_edit_fixture(tmp_path)
    before = roadmap.read_bytes()
    request = {
        "section": "task",
        "section_id": "tasks",
        "task_id": "slice-1",
        "proposed_value": {"objective": "Verify the task proposal.", "status": "running"},
        "reason": "Make the task outcome explicit.",
        **bases,
    }

    proposal = service.propose_document_edit("demo-project", "core-map", request)

    assert proposal["status"] == "ready"
    assert proposal["task_id"] == "slice-1"
    assert {operation["path"] for operation in proposal["operations"]} == {
        "/slices/slice-1/objective",
        "/slices/slice-1/status",
    }
    assert proposal["patch"]["dry_run"] is True
    assert roadmap.read_bytes() == before

    request["task_id"] = "missing-task"
    with pytest.raises(PlanningServiceError) as missing:
        service.propose_document_edit("demo-project", "core-map", request)
    assert missing.value.code == "document_task_not_found"
    assert roadmap.read_bytes() == before


def test_document_data_edit_validates_canonical_json_without_writing(tmp_path: Path):
    service, roadmap, document, bases = _document_edit_fixture(tmp_path)
    before = roadmap.read_bytes()
    candidate = json.loads(json.dumps(document["canonical"]["projection"]))
    candidate["goal"] = "Ship the validated Data-mode proposal."
    candidate["status"] = "running"

    proposal = service.propose_document_edit(
        "demo-project",
        "core-map",
        {
            "section": "data",
            "section_id": "data",
            "proposed_payload": json.dumps(candidate),
            "reason": "Review canonical JSON changes.",
            **bases,
        },
    )

    assert proposal["status"] == "ready"
    assert proposal["validation"]["valid"] is True
    assert {operation["path"] for operation in proposal["operations"]} == {"/goal", "/status"}
    assert proposal["writes_performed"] is False
    assert roadmap.read_bytes() == before


def test_document_data_edit_returns_invalid_proposal_and_protects_identity(tmp_path: Path):
    service, roadmap, document, bases = _document_edit_fixture(tmp_path)
    before = roadmap.read_bytes()
    candidate = json.loads(json.dumps(document["canonical"]["projection"]))
    candidate["slices"] = []

    invalid = service.propose_document_edit(
        "demo-project",
        "core-map",
        {
            "section": "data",
            "section_id": "data",
            "proposed_payload": candidate,
            "reason": "Exercise Data validation.",
            **bases,
        },
    )

    assert invalid["status"] == "invalid"
    assert invalid["ready_for_apply"] is False
    assert invalid["validation"]["valid"] is False
    assert "missing_slices" in {error["code"] for error in invalid["validation"]["errors"]}

    candidate = json.loads(json.dumps(document["canonical"]["projection"]))
    candidate["roadmap_id"] = "different-map"
    with pytest.raises(PlanningServiceError) as identity:
        service.propose_document_edit(
            "demo-project",
            "core-map",
            {
                "section": "data",
                "section_id": "data",
                "proposed_payload": candidate,
                "reason": "Attempt an immutable identity change.",
                **bases,
            },
        )
    assert identity.value.code == "document_data_identity_mismatch"
    assert roadmap.read_bytes() == before


def test_document_edit_detects_all_optimistic_base_conflicts_before_proposal(tmp_path: Path):
    service, roadmap, _document, bases = _document_edit_fixture(tmp_path)
    before = roadmap.read_bytes()
    request = {
        "section": "summary",
        "section_id": "summary",
        "proposed_value": "This value must not overwrite newer state.",
        "reason": "Exercise optimistic concurrency.",
        **bases,
    }
    request.update({
        "base_source_hash": "sha256:" + ("0" * 64),
        "base_revision": bases["base_revision"] + 1,
        "base_projection_hash": "sha256:" + ("f" * 64),
    })

    proposal = service.propose_document_edit("demo-project", "core-map", request)

    assert proposal["status"] == "conflict"
    assert proposal["ready_for_apply"] is False
    assert proposal["operations"] == []
    assert {conflict["code"] for conflict in proposal["conflicts"]} == {
        "source_hash_mismatch",
        "revision_mismatch",
        "projection_hash_mismatch",
    }
    assert proposal["writes_performed"] is False
    assert "This value must not overwrite" not in json.dumps(proposal)
    assert roadmap.read_bytes() == before


def test_document_edit_rejects_missing_bases_and_unsupported_fields(tmp_path: Path):
    service, _roadmap, _document, bases = _document_edit_fixture(tmp_path)
    request = {
        "section": "summary",
        "section_id": "summary",
        "proposed_value": "Bounded summary.",
        "reason": "Exercise input validation.",
        **bases,
    }
    del request["base_projection_hash"]
    with pytest.raises(PlanningServiceError) as missing:
        service.propose_document_edit("demo-project", "core-map", request)
    assert missing.value.code == "invalid_document_base"

    request["base_projection_hash"] = bases["base_projection_hash"]
    request["apply"] = True
    with pytest.raises(PlanningServiceError) as unsupported:
        service.propose_document_edit("demo-project", "core-map", request)
    assert unsupported.value.code == "invalid_document_edit"


def test_section_context_summary_is_stable_redacted_and_read_only(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    first = service.get_section_context_pack(
        "demo-project",
        "core-map",
        "summary",
        include_memory=True,
    )
    second = service.get_section_context_pack(
        "demo-project",
        "core-map",
        "summary",
        include_memory=True,
    )
    encoded = json.dumps(first, ensure_ascii=False)

    assert first["schema"] == "odysseus.planning.section_context_pack.v1"
    assert first["context_pack_id"] == second["context_pack_id"]
    assert first["project_id"] == "demo-project"
    assert first["roadmap_id"] == "core-map"
    assert first["section_id"] == "summary"
    assert first["item_id"] == ""
    assert first["section_ref"] == "roadmap:demo-project:core-map:summary"
    assert first["content"]["summary"] == "Ship a bounded planning service."
    assert first["memory_refs"]
    assert first["lens_evidence_refs"] == ["docs/plans/core-roadmap.json"]
    assert first["truth_level"] == "semantic_projection"
    assert first["classification"] == "private"
    assert first["redaction_state"] == "summary_only"
    assert first["source_of_truth"] is False
    assert first["read_only"] is True
    assert first["writes_supported"] is False
    assert first["agent_dispatch_performed"] is False
    assert first["events_emitted"] is False
    assert first["notifications_emitted"] is False
    assert first["raw_content_included"] is False
    assert first["payload_bytes"] <= 32_768
    assert "synthetic-secret-value" not in encoded
    assert str(root) not in encoded


def test_section_context_tasks_and_gates_resolve_exact_stable_items(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    task_list = service.get_section_context_pack(
        "demo-project", "core-map", "tasks", max_items=1, include_memory=False,
    )
    task = service.get_section_context_pack(
        "demo-project", "core-map", "tasks", task_id="slice-1", max_items=1, include_memory=False,
    )
    gate = service.get_section_context_pack(
        "demo-project", "core-map", "gates", gate_id="read-go", include_memory=False,
    )

    assert task_list["content"]["kind"] == "tasks"
    assert len(task_list["content"]["items"]) == 1
    assert task_list["truncated"] is True
    assert task_list["incomplete"] is True
    assert task["content"]["kind"] == "task"
    assert task["content"]["items"][0]["id"] == "slice-1"
    assert task["item_id"] == "slice-1"
    assert task["item_kind"] == "task"
    assert task["item_ref"] == "roadmap:demo-project:core-map:tasks:slice-1"
    assert task["memory_refs"] == []
    assert gate["content"]["kind"] == "gate"
    assert gate["content"]["items"][0]["id"] == "read-go"
    assert gate["item_id"] == "read-go"
    assert gate["item_kind"] == "gate"


def test_section_context_sources_and_data_are_bounded_projections_without_raw_json(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    sources = service.get_section_context_pack(
        "demo-project", "core-map", "sources", max_items=4, include_memory=False,
    )
    data = service.get_section_context_pack(
        "demo-project", "core-map", "data", max_items=1, include_memory=False,
    )
    encoded = json.dumps(data, ensure_ascii=False)

    assert sources["content"]["kind"] == "sources"
    assert sources["content"]["items"] == sources["source_refs"]
    assert data["content"]["kind"] == "canonical_projection"
    assert data["content"]["raw_json_included"] is False
    assert data["content"]["canonical_json_included"] is False
    assert "slices" not in data["content"]["projection"]
    assert data["content"]["projection"]["task_refs"] == [{"id": "slice-0", "status": "planned"}]
    assert data["content"]["projection"]["gate_refs"] == [{"id": "read-go", "status": "open"}]
    assert data["truncated"] is True
    assert data["payload_bytes"] <= 32_768
    assert "provider_note" not in encoded
    assert "synthetic-secret-value" not in encoded


def test_section_context_rejects_invalid_sections_selectors_and_missing_items(planning_repo):
    root, _roadmap = planning_repo
    service = PlanningMcpService(root)

    with pytest.raises(PlanningServiceError) as invalid_section:
        service.get_section_context_pack("demo-project", "core-map", "unknown")
    assert invalid_section.value.code == "invalid_section_context"

    with pytest.raises(PlanningServiceError) as wrong_selector:
        service.get_section_context_pack("demo-project", "core-map", "summary", item_id="slice-0")
    assert wrong_selector.value.code == "invalid_section_item"

    with pytest.raises(PlanningServiceError) as disagreement:
        service.get_section_context_pack(
            "demo-project", "core-map", "tasks", item_id="slice-0", task_id="slice-1",
        )
    assert disagreement.value.code == "ambiguous_section_item"

    with pytest.raises(PlanningServiceError) as missing_task:
        service.get_section_context_pack("demo-project", "core-map", "tasks", task_id="missing-task")
    assert missing_task.value.code == "section_task_not_found"

    with pytest.raises(PlanningServiceError) as missing_gate:
        service.get_section_context_pack("demo-project", "core-map", "gates", gate_id="missing-gate")
    assert missing_gate.value.code == "section_gate_not_found"


def _memory_bridge_metadata(document: dict, *, mode: str = "canonical") -> dict:
    return {
        "validation": {"valid": True, "mode": mode},
        "project_id": document["project_id"],
        "roadmap_id": document["roadmap_id"],
        "source_id": document["source_id"],
        "source_ref": document["source_ref"],
        "source_hash": document["source_hash"],
        "revision": document["revision"],
    }


def test_document_memory_bridge_is_deterministic_bounded_dry_run_and_creates_derived_record(tmp_path: Path):
    service, roadmap, document, _bases = _document_edit_fixture(tmp_path)
    before = roadmap.read_bytes()
    metadata = _memory_bridge_metadata(document)

    first = service.plan_document_memory_bridge("demo-project", "core-map", metadata)
    second = service.plan_document_memory_bridge("demo-project", "core-map", metadata)
    entry = first["derived_entries"][0]
    operation = first["lifecycle_plan"]["operations"][0]
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)

    assert first == second
    assert first["schema"] == "odysseus.planning.document_memory_bridge_plan.v1"
    assert first["plan_id"].startswith("planning-memory-plan-")
    assert first["validation"] == {
        "valid": True,
        "mode": "canonical",
        "source_schema": "odysseus.planning.roadmap_validation.v1",
    }
    assert entry["memory_ref"] == "planning:demo-project:core-map"
    assert entry["project_id"] == "demo-project"
    assert entry["roadmap_id"] == "core-map"
    assert entry["source_id"] == document["source_id"]
    assert entry["source_hash"] == document["source_hash"]
    assert entry["revision"] == 1
    assert entry["source_of_truth"] is False
    assert entry["derived"] is True
    assert entry["rebuildable"] is True
    assert operation["operation"] == "create"
    assert first["lifecycle_plan"]["summary"] == {
        "create": 1,
        "update": 0,
        "unchanged": 0,
        "mark_deleted": 0,
        "planned": 1,
        "returned": 1,
        "truncated": False,
    }
    assert first["dry_run"] is True
    assert first["writes_supported"] is False
    assert first["writes_performed"] is False
    assert first["memory_manager_called"] is False
    assert first["vector_write_performed"] is False
    assert first["database_write_performed"] is False
    assert first["file_write_performed"] is False
    assert first["raw_json_included"] is False
    assert first["raw_body_included"] is False
    assert first["payload_bytes"] <= 65_536
    assert "slices" not in encoded
    assert "provider_note" not in encoded
    assert roadmap.read_bytes() == before


def test_document_memory_bridge_plans_unchanged_and_update_from_injected_derived_evidence(tmp_path: Path):
    service, _roadmap, document, _bases = _document_edit_fixture(tmp_path)
    metadata = _memory_bridge_metadata(document)
    created = service.plan_document_memory_bridge("demo-project", "core-map", metadata)
    current = created["derived_entries"][0]

    unchanged = service.plan_document_memory_bridge(
        "demo-project", "core-map", metadata, existing_records=[current],
    )
    stale = json.loads(json.dumps(current))
    stale["source_hash"] = "sha256:" + ("b" * 64)
    stale["content_hash"] = "sha256:" + ("c" * 64)
    updated = service.plan_document_memory_bridge(
        "demo-project", "core-map", metadata, existing_records={"entries": [stale]},
    )

    assert unchanged["lifecycle_plan"]["summary"]["unchanged"] == 1
    assert unchanged["lifecycle_plan"]["operations"][0]["operation"] == "unchanged"
    assert updated["lifecycle_plan"]["summary"]["update"] == 1
    assert updated["lifecycle_plan"]["summary"]["mark_deleted"] == 0
    assert updated["lifecycle_plan"]["operations"][0]["operation"] == "update"
    assert updated["lifecycle_plan"]["operations"][0]["previous_evidence"]["source_hash"] == stale["source_hash"]


def test_document_memory_bridge_accepts_transition_mode_without_changing_builder_behavior(tmp_path: Path):
    service, _roadmap, document, _bases = _document_edit_fixture(tmp_path)

    result = service.plan_document_memory_bridge(
        "demo-project",
        "core-map",
        _memory_bridge_metadata(document, mode="transition"),
    )

    assert result["validation"]["mode"] == "transition"
    assert result["derived_entries"][0]["provenance"]["validation_mode"] == "transition"
    assert result["derived_entries"][0]["precedence_rank"] == 80


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("project_id", "different-project", "memory_bridge_identity_conflict"),
        ("roadmap_id", "different-map", "memory_bridge_identity_conflict"),
        ("source_id", "repo-plan:different", "memory_bridge_identity_conflict"),
        ("source_hash", "sha256:" + ("f" * 64), "memory_bridge_hash_conflict"),
        ("revision", 2, "memory_bridge_revision_conflict"),
    ],
)
def test_document_memory_bridge_fails_closed_on_identity_hash_or_revision_conflict(
    tmp_path: Path,
    field: str,
    value: object,
    code: str,
):
    service, _roadmap, document, _bases = _document_edit_fixture(tmp_path)
    metadata = _memory_bridge_metadata(document)
    metadata[field] = value

    with pytest.raises(PlanningServiceError) as conflict:
        service.plan_document_memory_bridge("demo-project", "core-map", metadata)

    assert conflict.value.code == code
    assert str(value) not in conflict.value.public_message


def test_document_memory_bridge_rejects_unvalidated_or_unsafe_existing_records_without_echo(tmp_path: Path):
    service, _roadmap, document, _bases = _document_edit_fixture(tmp_path)
    metadata = _memory_bridge_metadata(document)
    invalid_validation = json.loads(json.dumps(metadata))
    invalid_validation["validation"] = {"valid": False, "mode": "canonical"}

    with pytest.raises(PlanningServiceError) as invalid:
        service.plan_document_memory_bridge("demo-project", "core-map", invalid_validation)
    assert invalid.value.code == "invalid_memory_bridge_validation"

    unsafe_existing = {
        "memory_ref": "planning:demo-project:core-map",
        "project_id": "demo-project",
        "roadmap_id": "core-map",
        "source_id": document["source_id"],
        "safe_summary": "token=synthetic-secret-value C:\\private\\roadmap.json",
    }
    with pytest.raises(PlanningServiceError) as unsafe:
        service.plan_document_memory_bridge(
            "demo-project", "core-map", metadata, existing_records=[unsafe_existing],
        )
    assert unsafe.value.code == "forbidden_content"
    assert "synthetic-secret-value" not in unsafe.value.public_message
    assert "C:\\private" not in unsafe.value.public_message


def test_document_memory_bridge_bounds_existing_records_before_lifecycle_planning(tmp_path: Path):
    service, _roadmap, document, _bases = _document_edit_fixture(tmp_path)
    metadata = _memory_bridge_metadata(document)

    with pytest.raises(PlanningServiceError) as over_budget:
        service.plan_document_memory_bridge(
            "demo-project",
            "core-map",
            metadata,
            existing_records=[{} for _index in range(101)],
        )

    assert over_budget.value.code == "existing_memory_budget_exceeded"


@pytest.mark.parametrize(
    "event_type",
    [
        "planning_context_pack_read",
        "planning_summary_refreshed",
        "planning_raptor_memory_processed",
        "planning_definition_validation_succeeded",
    ],
)
def test_planning_service_routine_events_are_explicitly_silent_without_candidate(tmp_path: Path, event_type: str):
    service = PlanningMcpService(tmp_path)
    reason = "Bounded routine Planning evidence."

    result = service.classify_planning_event(
        event_type,
        project_id="project-001",
        roadmap_id="roadmap-001",
        reason=reason,
    )
    encoded_audit = json.dumps(result["audit"], sort_keys=True)

    assert result["classification"] == "silent"
    assert result["candidate"] is None
    assert result["schema_id"] == "odysseus.planning.definition_event_classification.v2"
    assert result["audit"]["category"] == "routine_definition_event"
    assert result["audit"]["reason_code"] == "definition_event_silent_by_policy"
    assert result["audit"]["ref_fields"] == ["project_id", "roadmap_id"]
    assert result["audit"]["ref_count"] == 2
    assert result["audit"]["ref_hash"].startswith("sha256:")
    assert result["audit"]["raw_refs_visible"] is False
    assert result["audit"]["raw_reason_visible"] is False
    assert reason not in encoded_audit
    assert "project-001" not in encoded_audit
    assert "roadmap-001" not in encoded_audit
    assert result["delivery_authorized"] is False
    assert result["live_delivery_performed"] is False
    assert result["events_emitted"] is False
    assert result["notifications_emitted"] is False


@pytest.mark.parametrize(
    ("event_type", "roadmap_id", "revision", "content_hash"),
    [
        ("project_created", None, None, ""),
        ("project_deleted", None, None, ""),
        ("roadmap_created", "roadmap-001", None, ""),
        ("roadmap_deleted", "roadmap-001", None, ""),
        ("roadmap_revision_approved", "roadmap-001", 3, "sha256:" + ("a" * 64)),
        ("roadmap_revision_conflict", "roadmap-001", 3, "sha256:" + ("b" * 64)),
        ("undo_available_after_structural_delete", "roadmap-001", None, ""),
    ],
)
def test_planning_service_structural_events_create_definition_only_candidates(
    tmp_path: Path,
    event_type: str,
    roadmap_id: str | None,
    revision: int | None,
    content_hash: str,
):
    service = PlanningMcpService(tmp_path)
    reason = "A bounded structural Planning change requires attention."

    result = service.classify_planning_event(
        event_type,
        project_id="project-001",
        roadmap_id=roadmap_id,
        revision=revision,
        content_hash=content_hash,
        reason=reason,
        created_at="2026-07-10T08:00:00Z",
    )
    candidate = result["candidate"]
    encoded_audit = json.dumps(result["audit"], sort_keys=True)

    assert result["classification"] == "notification_candidate"
    assert candidate["schema_id"] == "odysseus.planning.definition_notification_candidate.v2"
    assert candidate["event_type"] == event_type
    assert candidate["project_id"] == "project-001"
    assert candidate["roadmap_id"] == roadmap_id
    assert candidate["revision"] == revision
    assert candidate["content_hash"] == content_hash
    assert candidate["reason"] == reason
    assert candidate["dedupe_key"] == result["dedupe_key"]
    assert candidate["delivery_authorized"] is False
    assert candidate["live_delivery_performed"] is False
    assert result["delivery_authorized"] is False
    assert result["live_delivery_performed"] is False
    assert result["writes_performed"] is False
    assert result["audit"]["category"] == "structural_definition_event"
    assert result["audit"]["reason_code"] == "sparse_definition_candidate_by_policy"
    assert result["audit"]["derived_entries_visible"] is False
    assert reason not in encoded_audit
    assert "project-001" not in encoded_audit


def test_planning_service_candidate_dedupe_is_deterministic_and_ignores_timestamp(tmp_path: Path):
    service = PlanningMcpService(tmp_path)
    arguments = {
        "project_id": "project-001",
        "roadmap_id": "roadmap-001",
        "reason": "A bounded roadmap creation reason.",
    }

    first = service.classify_planning_event(
        "roadmap_created", created_at="2026-07-10T08:00:00Z", **arguments,
    )
    second = service.classify_planning_event(
        "roadmap_created", created_at="2026-07-10T09:00:00Z", **arguments,
    )

    assert first["dedupe_key"] == second["dedupe_key"]
    assert first["candidate"]["dedupe_key"] == second["candidate"]["dedupe_key"]
    assert first["audit"] == second["audit"]


@pytest.mark.parametrize(
    "event_type",
    [
        "roadmap_read",
        "planning_unknown",
        "ROADMAP_CREATED",
        "",
        "planning_progress_updated",
        "planning_agent_checkpoint_written",
        "gate_blocked",
        "human_decision_required",
    ],
)
def test_planning_service_unknown_events_fail_closed_without_notification_fallback(tmp_path: Path, event_type: str):
    service = PlanningMcpService(tmp_path)

    with pytest.raises(PlanningServiceError) as invalid:
        service.classify_planning_event(
            event_type,
            project_id="project-001",
            reason="Bounded reason.",
        )

    assert invalid.value.code == "invalid_planning_event"


@pytest.mark.parametrize(
    "reason",
    [
        "token=synthetic-secret-value",
        "Read C:\\private\\roadmap.json",
        "Open https://example.invalid/private",
        "x" * 241,
    ],
)
def test_planning_service_event_reason_is_bounded_and_rejects_private_or_delivery_material(
    tmp_path: Path,
    reason: str,
):
    service = PlanningMcpService(tmp_path)

    with pytest.raises(PlanningServiceError) as invalid:
        service.classify_planning_event(
            "planning_context_pack_read",
            project_id="project-001",
            roadmap_id="roadmap-001",
            reason=reason,
        )

    assert invalid.value.code in {"forbidden_content", "invalid_planning_event_reason"}
    assert "synthetic-secret-value" not in invalid.value.public_message
    assert "C:\\private" not in invalid.value.public_message


def test_planning_service_candidate_requires_typed_event_refs_and_valid_timestamp(tmp_path: Path):
    service = PlanningMcpService(tmp_path)

    with pytest.raises(PlanningServiceError) as missing_roadmap:
        service.classify_planning_event(
            "roadmap_deleted",
            project_id="project-001",
            reason="Bounded delete reason.",
        )
    assert missing_roadmap.value.code == "invalid_planning_event_metadata"

    with pytest.raises(PlanningServiceError) as invalid_time:
        service.classify_planning_event(
            "roadmap_created",
            project_id="project-001",
            roadmap_id="roadmap-001",
            reason="Bounded definition reason.",
            created_at="not-a-time",
        )
    assert invalid_time.value.code == "invalid_planning_event_timestamp"


def test_planning_service_rejects_runtime_gate_events_even_with_definition_gate_id(tmp_path: Path):
    service = PlanningMcpService(tmp_path)

    with pytest.raises(PlanningServiceError) as invalid:
        service.classify_planning_event(
            "gate_blocked",
            project_id="project-001",
            roadmap_id="roadmap-001",
            gate_id="gate-001",
            reason="Bounded gate reason.",
        )

    assert invalid.value.code == "runtime_gate_event_forbidden"
