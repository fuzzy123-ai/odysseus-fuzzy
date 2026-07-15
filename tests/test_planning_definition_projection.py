from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.planning_definition_contract import (
    FORBIDDEN_EXECUTION_STATES,
    GATE_RUNTIME_FIELD_DENYLIST,
    RUNTIME_FIELD_DENYLIST,
    compute_roadmap_content_hash,
    validate_planning_definition,
)
from src.planning_definition_projection import (
    ORIGIN_STATES,
    PlanningDefinitionProjectionError,
    PlanningDefinitionProjector,
    origin_metadata,
)
from src.planning_revision_store import PlanningRevisionStore, PlanningRevisionStoreError


ROOT = Path(__file__).resolve().parents[1]


def definition_fixture(
    project_id: str = "project-a",
    roadmap_id: str = "roadmap-a",
    *,
    include_draft: bool = True,
) -> dict:
    approved = {
        "roadmap_id": roadmap_id,
        "project_id": project_id,
        "revision": 1,
        "content_hash": "sha256:" + ("0" * 64),
        "revision_state": "approved",
        "title": f"{roadmap_id} approved",
        "objective": "Define work without exposing runtime state.",
        "assumptions": ["The definition source is local."],
        "constraints": ["No Agent run is created."],
        "nodes": [
            {
                "node_id": "prepare",
                "kind": "work",
                "title": "Prepare",
                "objective": "Prepare the definition.",
                "depends_on": [],
                "gate_ids": [],
                "deliverables": ["Definition"],
                "allowed_paths": ["docs/plans/definition.json"],
                "blocked_paths": [],
                "capability_requirements": ["Repository read"],
                "verification_rule_ids": ["rule-static"],
            }
        ],
        "edges": [],
        "gates": [],
        "done_contract": {
            "required_node_ids": ["prepare"],
            "required_gate_ids": [],
            "verification_rules": [
                {
                    "rule_id": "rule-static",
                    "kind": "static",
                    "description": "The definition validates.",
                }
            ],
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": ["docs/plans/source.json"],
        "created_at": "2026-07-15T06:00:00Z",
        "updated_at": "2026-07-15T06:00:00Z",
    }
    approved["content_hash"] = compute_roadmap_content_hash(approved)
    roadmaps = [approved]
    drafts = []
    if include_draft:
        draft = deepcopy(approved)
        draft.update(
            {
                "revision": 2,
                "revision_state": "draft",
                "title": f"{roadmap_id} draft",
                "updated_at": "2026-07-15T07:00:00Z",
            }
        )
        draft["content_hash"] = compute_roadmap_content_hash(draft)
        roadmaps.append(draft)
        drafts.append(
            {
                "draft_id": f"draft-{roadmap_id}",
                "roadmap_id": roadmap_id,
                "base_revision": 1,
                "base_hash": approved["content_hash"],
            }
        )
    return {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": project_id,
            "title": f"{project_id} title",
            "objective": "PRIVATE OBJECTIVE PRESENT ONLY IN THE DOCUMENT READ.",
            "scope": {"in": ["Definition authoring"], "out": ["Agent execution"]},
            "constraints": ["No runtime provider calls."],
            "roadmap_refs": [roadmap_id],
            "latest_approved_revision": {
                roadmap_id: {
                    "revision": 1,
                    "content_hash": approved["content_hash"],
                }
            },
            "draft_refs": drafts,
        },
        "roadmaps": roadmaps,
    }


def _legacy_fixture() -> dict:
    return {
        "schema_version": 1,
        "kind": "odysseus.planning.roadmap",
        "project_id": "legacy-project",
        "roadmap_id": "legacy-roadmap",
        "title": "Legacy definition",
        "goal": "Retain structural intent.",
        "status": "running",
        "run_id": "private-run-id",
        "heartbeat_at": "2026-07-15T07:00:00Z",
        "created_at": "2026-07-14",
        "updated_at": "2026-07-15",
        "slice_queue": [
            {
                "id": "prepare",
                "title": "Prepare",
                "objective": "Prepare the definition.",
                "status": "completed",
                "depends_on": [],
                "allowed_paths": ["docs/plans/a.json", "C:/private/secret.txt"],
                "deliverables": ["Definition"],
                "worker_id": "private-worker",
            },
            {
                "id": "ship",
                "title": "Ship",
                "objective": "Reach the declared boundary.",
                "depends_on": ["prepare", "missing"],
                "gate_ids": ["operator-go"],
                "claim_id": "private-claim",
            },
        ],
        "gate_queue": [
            {
                "id": "operator-go",
                "title": "Operator go",
                "blocks": ["ship", "missing"],
                "decision_needed": "Name the bounded target.",
                "safe_default": "Do not mutate it.",
                "state": "approved",
                "actor": "private-operator",
                "evidence_receipt": {"raw": "private"},
            }
        ],
    }


def _keys(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            found.add(str(key).lower())
            found |= _keys(nested)
    elif isinstance(value, list):
        for nested in value:
            found |= _keys(nested)
    return found


def _string_values(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for nested in value.values():
            found |= _string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            found |= _string_values(nested)
    elif isinstance(value, str):
        found.add(value.lower())
    return found


def test_canonical_projection_is_deep_copied_and_byte_stable() -> None:
    projector = PlanningDefinitionProjector()
    source = definition_fixture()

    first = projector.normalize_document(source)
    second = projector.normalize_document(dict(reversed(list(source.items()))))
    first_bytes = projector.canonical_bytes(first)
    second_bytes = projector.canonical_bytes(second)
    source["project"]["title"] = "mutated after projection"

    assert first_bytes == second_bytes
    assert first["project"]["title"] == "project-a title"
    validate_planning_definition(first)


def test_legacy_projection_drops_runtime_state_and_invalid_paths_recursively() -> None:
    projected = PlanningDefinitionProjector().normalize_document(
        _legacy_fixture(),
        source_ref="docs/plans/legacy.json",
    )
    serialized = json.dumps(projected, sort_keys=True)

    validate_planning_definition(projected)
    assert projected["roadmaps"][0]["revision_state"] == "draft"
    assert projected["roadmaps"][0]["nodes"][1]["depends_on"] == ["prepare"]
    assert projected["roadmaps"][0]["gates"][0]["blocks"] == ["ship"]
    assert projected["roadmaps"][0]["nodes"][0]["allowed_paths"] == [
        "docs/plans/a.json"
    ]
    assert not (_keys(projected) & RUNTIME_FIELD_DENYLIST)
    assert not (_keys(projected) & GATE_RUNTIME_FIELD_DENYLIST)
    assert "private-run-id" not in serialized
    assert "private-worker" not in serialized
    assert "C:/private" not in serialized
    assert not (_string_values(projected) & FORBIDDEN_EXECUTION_STATES)


def test_legacy_projection_is_deterministic_and_never_exposes_absolute_source() -> None:
    projector = PlanningDefinitionProjector()
    first = projector.normalize_document(_legacy_fixture(), source_ref="C:/Users/private/plan.json")
    second = projector.normalize_document(_legacy_fixture(), source_ref="C:/Users/private/plan.json")

    assert projector.canonical_bytes(first) == projector.canonical_bytes(second)
    assert first["roadmaps"][0]["source_refs"] == []


def test_unsupported_payload_fails_closed() -> None:
    with pytest.raises(PlanningDefinitionProjectionError) as raised:
        PlanningDefinitionProjector().normalize_document({"kind": "unrelated"})

    assert raised.value.code == "unsupported_definition"


@pytest.mark.parametrize("state", sorted(ORIGIN_STATES))
def test_all_declared_origin_states_are_bounded(state: str) -> None:
    metadata = origin_metadata(
        state,
        source="planning_revision_store",
        reason="fixture",
        as_of="2026-07-15T07:00:00Z",
    )

    assert metadata["state"] == state
    assert set(metadata) == {"state", "source", "reason", "as_of"}


def test_store_is_owner_scoped_and_exact_revision_reads_never_substitute_latest() -> None:
    store = PlanningRevisionStore(
        [
            ("alice", definition_fixture(), "alice.json"),
            ("bob", definition_fixture("project-b", "roadmap-b"), "bob.json"),
        ],
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )

    assert [item["project_id"] for item in store.list_projects("alice")["items"]] == [
        "project-a"
    ]
    assert [item["project_id"] for item in store.list_projects("bob")["items"]] == [
        "project-b"
    ]
    approved = store.get_roadmap("alice", "project-a", "roadmap-a")
    draft = store.get_roadmap("alice", "project-a", "roadmap-a", revision=2)
    assert approved["roadmap"]["revision"] == 1
    assert draft["roadmap"]["revision"] == 2
    assert draft["project"]["latest_approved_revision"] == {}
    with pytest.raises(PlanningRevisionStoreError) as missing:
        store.get_roadmap("alice", "project-a", "roadmap-a", revision=3)
    assert missing.value.code == "revision_not_found"


def test_owner_scoped_cursor_rejects_tampering_cross_owner_collection_and_limit() -> None:
    records = [
        ("alice", definition_fixture(f"project-{index}", f"roadmap-{index}"), f"{index}.json")
        for index in range(3)
    ]
    records.extend(
        ("bob", definition_fixture(f"project-{index}", f"roadmap-{index}"), f"b-{index}.json")
        for index in range(3)
    )
    store = PlanningRevisionStore(
        records,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )
    first = store.list_projects("alice", limit=1)

    assert first["has_more"] is True
    assert len(first["items"]) == 1
    second = store.list_projects("alice", cursor=first["next_cursor"], limit=1)
    assert second["items"][0]["project_id"] != first["items"][0]["project_id"]
    for operation in (
        lambda: store.list_projects("bob", cursor=first["next_cursor"], limit=1),
        lambda: store.list_projects("alice", cursor=first["next_cursor"], limit=2),
        lambda: store.list_roadmaps(
            "alice", "project-0", cursor=first["next_cursor"], limit=1
        ),
        lambda: store.list_projects(
            "alice", cursor=first["next_cursor"][:-1] + "0", limit=1
        ),
    ):
        with pytest.raises(PlanningRevisionStoreError) as raised:
            operation()
        assert raised.value.code == "invalid_cursor"


def test_pagination_summaries_do_not_return_definition_body_or_paths() -> None:
    store = PlanningRevisionStore([("alice", definition_fixture(), "private/source.json")])

    projects = json.dumps(store.list_projects("alice"), sort_keys=True)
    roadmaps = json.dumps(store.list_roadmaps("alice", "project-a"), sort_keys=True)

    for payload in (projects, roadmaps):
        assert "PRIVATE OBJECTIVE" not in payload
        assert "docs/plans/definition.json" not in payload
        assert "private/source.json" not in payload
    assert store.list_projects("alice")["raw_private_content_visible"] is False


def test_revision_page_and_read_model_are_definition_only() -> None:
    store = PlanningRevisionStore([("alice", definition_fixture(), "a.json")])

    revisions = store.list_revisions("alice", "project-a", "roadmap-a")
    read_model = store.get_roadmap("alice", "project-a", "roadmap-a", revision=1)

    assert [item["revision"] for item in revisions["items"]] == [1, 2]
    assert read_model["read_only"] is True
    assert read_model["launch_authorized"] is False
    assert read_model["graph"]["nodes"] == read_model["roadmap"]["nodes"]
    assert not (_keys(read_model) & RUNTIME_FIELD_DENYLIST)
    assert PlanningDefinitionProjector().canonical_bytes(read_model) == (
        PlanningDefinitionProjector().canonical_bytes(
            store.get_roadmap("alice", "project-a", "roadmap-a", revision=1)
        )
    )


def test_conflicting_same_revision_is_rejected() -> None:
    first = definition_fixture(include_draft=False)
    second = deepcopy(first)
    second["roadmaps"][0]["objective"] = "Different immutable content."
    second["roadmaps"][0]["content_hash"] = compute_roadmap_content_hash(
        second["roadmaps"][0]
    )
    second["project"]["latest_approved_revision"]["roadmap-a"]["content_hash"] = (
        second["roadmaps"][0]["content_hash"]
    )

    with pytest.raises(PlanningRevisionStoreError) as raised:
        PlanningRevisionStore([("alice", first, "a.json"), ("alice", second, "b.json")])

    assert raised.value.code == "revision_conflict"


def test_directory_source_is_read_only_and_reports_unavailable_without_files(tmp_path: Path) -> None:
    missing = PlanningRevisionStore.from_directory(tmp_path / "missing", owner="alice")
    empty = PlanningRevisionStore.from_directory(tmp_path, owner="alice")

    assert missing.list_projects("alice")["origin"]["state"] == "unavailable"
    assert empty.list_projects("alice")["origin"]["state"] == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_projection_and_store_import_no_agent_or_temporal_provider() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/planning_definition_projection.py",
            "src/planning_revision_store.py",
            "routes/planning_definition_routes.py",
        )
    ).lower()

    assert "import temporal" not in source
    assert "from temporal" not in source
    assert "import agent" not in source
    assert "from agent" not in source
