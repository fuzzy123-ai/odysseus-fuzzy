from copy import deepcopy
import hashlib
import json
import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.planning_source_inventory import (
    build_planning_source_inventory,
    diff_planning_source_inventories,
    project_planning_migration_inventory,
    migration_primary_digest_matches,
)
from src.planning_roadmap_migration import (
    DOCS_ONLY_AUTHORITY_MAP_ID,
    DOCS_ONLY_AUTHORITY_PINS,
    REPO_ONLY_AUTHORITY_MAP_ID,
    REPO_ONLY_AUTHORITY_PINS,
    HISTORICAL_AUTHORITY_MAP_ID,
    HISTORICAL_AUTHORITY_PINS,
    MIGRATION_MANIFEST_SCHEMA_V1,
    MIGRATION_MANIFEST_SCHEMA_V2,
    SUCCESSOR_AUTHORITY_MAP_ID,
    SUCCESSOR_AUTHORITY_PINS,
)


def test_planning_source_inventory_scans_allowlisted_repo_plans_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "specs", "roadmaps"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "docs", "plans"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "private"), exist_ok=True)
        roadmap = {
            "plan_id": "demo-roadmap",
            "title": "Demo Roadmap",
            "parent_plan_id": "parent-plan",
            "graph_nodes": [
                {
                    "id": "node-a",
                    "depends_on": ["node-before"],
                    "unlocks": ["node-after"],
                    "source_refs": ["docs/plans/demo.md"],
                }
            ],
        }
        with open(os.path.join(tmpdir, "specs", "roadmaps", "demo-roadmap.v1.json"), "w", encoding="utf-8") as f:
            json.dump(roadmap, f)
        with open(os.path.join(tmpdir, "docs", "plans", "demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo Plan\n\napi_key=super-secret-value should be redacted.\n")
        with open(os.path.join(tmpdir, "private", "secret.md"), "w", encoding="utf-8") as f:
            f.write("# Not allowed\n")

        payload = build_planning_source_inventory(tmpdir, preview_chars=80)

        assert payload["schema"] == "planning-source-inventory-v1"
        assert payload["read_only"] is True
        assert payload["writes_supported"] is False
        assert payload["allowlist"] == ["specs/roadmaps", "docs/plans"]
        assert payload["summary"]["total_sources"] == 2
        assert payload["summary"]["stable_ids"] is True
        assert payload["summary"]["content_hashes"] == 2
        assert payload["summary"]["raw_content_bounded"] is True
        assert {item["path"] for item in payload["sources"]} == {
            "docs/plans/demo.md",
            "specs/roadmaps/demo-roadmap.v1.json",
        }
        roadmap_item = next(item for item in payload["sources"] if item["path"].endswith(".json"))
        assert roadmap_item["kind"] == "roadmap_json"
        assert roadmap_item["title"] == "Demo Roadmap"
        assert roadmap_item["plan_id"] == "demo-roadmap"
        assert set(roadmap_item["dependency_hints"]) == {"node-after", "node-before", "parent-plan"}
        assert roadmap_item["source_refs"] == ["docs/plans/demo.md"]
        assert roadmap_item["absolute_path_recorded"] is False
        plan_item = next(item for item in payload["sources"] if item["path"].endswith(".md"))
        assert plan_item["kind"] == "planning_doc"
        assert plan_item["title"] == "Demo Plan"
        assert len(plan_item["preview"]) <= 80
        assert "[redacted]" in plan_item["preview"]
        assert "super-secret-value" not in plan_item["preview"]
        assert str(tmpdir) not in json.dumps(payload)


def test_planning_source_inventory_diff_uses_stable_ids_for_incremental_changes():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "specs", "roadmaps"), exist_ok=True)
        path = os.path.join(tmpdir, "specs", "roadmaps", "demo.v1.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"plan_id": "demo", "title": "Demo"}, f)

        first = build_planning_source_inventory(tmpdir)
        first_id = first["sources"][0]["source_id"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"plan_id": "demo", "title": "Demo Updated"}, f)
        second = build_planning_source_inventory(tmpdir)

        assert second["sources"][0]["source_id"] == first_id
        diff = diff_planning_source_inventories(first, second)

        assert diff["summary"] == {
            "created": 0,
            "changed": 1,
            "deleted": 0,
            "unchanged": 0,
        }
        assert diff["changed"] == [first_id]


def test_planning_source_inventory_route_returns_admin_repo_inventory(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "specs", "roadmaps"), exist_ok=True)
        with open(os.path.join(tmpdir, "specs", "roadmaps", "route.v1.json"), "w", encoding="utf-8") as f:
            json.dump({"plan_id": "route-plan", "title": "Route Plan"}, f)

        monkeypatch.setenv("ODYSSEUS_ROOT", tmpdir)
        monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
        app = FastAPI()
        app.include_router(setup_roadmap_routes())

        response = TestClient(app).get("/api/roadmap/planning-sources/inventory?preview_chars=8")

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["total_sources"] == 1
        assert payload["sources"][0]["plan_id"] == "route-plan"
        assert len(payload["sources"][0]["preview"]) <= 8


def test_roadmap_status_text_does_not_create_authority_roles():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write = lambda name, value: open(
            os.path.join(tmpdir, "specs", "roadmaps", name), "w", encoding="utf-8"
        ).write(value)
        os.makedirs(os.path.join(tmpdir, "specs", "roadmaps"), exist_ok=True)
        _write("current.v1.json", json.dumps({"plan_id": "current", "title": "Current"}))
        _write(
            "superseded.v1.json",
            json.dumps({"plan_id": "old", "status": "superseded", "superseded_by": "current.v1.json"}),
        )

        sources = {item["path"]: item for item in build_planning_source_inventory(tmpdir)["sources"]}

        assert sources["specs/roadmaps/current.v1.json"]["authority_disposition"] == "current_source_of_truth"
        assert sources["specs/roadmaps/superseded.v1.json"]["authority_disposition"] == "superseded_plan_source"


def test_invalid_duplicate_key_and_non_object_roadmaps_fail_closed_for_authority():
    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap_dir = os.path.join(tmpdir, "specs", "roadmaps")
        os.makedirs(roadmap_dir, exist_ok=True)
        with open(os.path.join(roadmap_dir, "duplicate.v1.json"), "w", encoding="utf-8") as handle:
            handle.write('{"plan_id":"first","plan_id":"second"}')
        with open(os.path.join(roadmap_dir, "array.v1.json"), "w", encoding="utf-8") as handle:
            handle.write("[]")
        with open(os.path.join(roadmap_dir, "malformed-field.v1.json"), "w", encoding="utf-8") as handle:
            json.dump({"plan_id": "bad", "superseded_by": ""}, handle)
        with open(os.path.join(roadmap_dir, "unknown-status.v1.json"), "w", encoding="utf-8") as handle:
            json.dump({"plan_id": "unknown", "status": "mystery"}, handle)
        with open(os.path.join(roadmap_dir, "non-string-successor.v1.json"), "w", encoding="utf-8") as handle:
            json.dump({"plan_id": "bad-successor", "superseded_by": []}, handle)

        sources = {item["path"]: item for item in build_planning_source_inventory(tmpdir)["sources"]}

        assert sources["specs/roadmaps/duplicate.v1.json"]["authority_disposition"] == "invalid_plan_source"
        assert sources["specs/roadmaps/array.v1.json"]["authority_disposition"] == "invalid_plan_source"
        assert sources["specs/roadmaps/malformed-field.v1.json"]["authority_disposition"] == "invalid_plan_source"
        assert sources["specs/roadmaps/unknown-status.v1.json"]["authority_disposition"] == "current_source_of_truth"
        assert sources["specs/roadmaps/non-string-successor.v1.json"]["authority_disposition"] == "invalid_plan_source"


def test_nested_and_top_level_status_markers_do_not_control_authority():
    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap_dir = os.path.join(tmpdir, "specs", "roadmaps")
        os.makedirs(roadmap_dir, exist_ok=True)
        with open(os.path.join(roadmap_dir, "nested.v1.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "plan_id": "nested",
                    "graph_nodes": [{"status": "superseded", "superseded_by": "other.v1.json"}],
                    "metadata": {"status": "obsolete"},
                },
                handle,
            )
        with open(os.path.join(roadmap_dir, "top-level.v1.json"), "w", encoding="utf-8") as handle:
            json.dump({"plan_id": "top", "status": "retired"}, handle)

        sources = {item["path"]: item for item in build_planning_source_inventory(tmpdir)["sources"]}

        assert sources["specs/roadmaps/nested.v1.json"]["authority_disposition"] == "current_source_of_truth"
        assert sources["specs/roadmaps/top-level.v1.json"]["authority_disposition"] == "superseded_plan_source"


def test_progress_statuses_and_unknown_strings_remain_current_authority():
    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap_dir = os.path.join(tmpdir, "specs", "roadmaps")
        os.makedirs(roadmap_dir, exist_ok=True)
        statuses = {
            "live-installed": "live_installed",
            "draft-canonical": "draft_canonical",
            "completed": "completed",
            "current-foundations": "current_foundations_accepted_execution_and_live_gates_unaccepted",
            "unknown": "mystery_progress_marker",
        }
        for name, status in statuses.items():
            with open(os.path.join(roadmap_dir, name + ".v1.json"), "w", encoding="utf-8") as handle:
                json.dump({"plan_id": name, "status": status}, handle)
        sources = {item["path"]: item for item in build_planning_source_inventory(tmpdir)["sources"]}
        assert all(
            sources["specs/roadmaps/" + name + ".v1.json"]["authority_disposition"] == "current_source_of_truth"
            for name in statuses
        )


def test_migration_inventory_accept_and_rollback_are_read_only_projections():
    inventory = {
        "schema": "planning-source-inventory-v1",
        "sources": [
            {"path": "specs/roadmaps/primary.v1.json", "source_hash": "sha256:" + "a" * 64, "authority_disposition": "current_source_of_truth"},
            {"path": "specs/roadmaps/relation.v1.json", "source_hash": "sha256:bbb", "authority_disposition": "current_source_of_truth"},
            {"path": "docs/plans/history.md", "source_hash": "sha256:ccc", "authority_disposition": "supporting_plan_source"},
        ],
    }
    batch = {
        "batch_id": "planning-migration-batch:" + ("a" * 64),
        "manifest_digest": "sha256:" + ("b" * 64),
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [{"path": "specs/roadmaps/primary.v1.json", "sha256": "a" * 64}],
        "supporting_relations": [{"path": "specs/roadmaps/relation.v1.json", "sha256": "bbb"}],
        "expected_subjects": [], "accepted_subjects": [], "normalized_payloads": {},
        "prepared_receipt_id": "p", "accepted_receipt_id": "a", "terminal_receipt_id": "",
    }
    accepted = project_planning_migration_inventory(inventory, batch)
    dispositions = {item["path"]: item["authority_disposition"] for item in accepted["sources"]}
    assert dispositions == {
        "specs/roadmaps/primary.v1.json": "migrated_read_only",
        "specs/roadmaps/relation.v1.json": "supporting_reference",
        "docs/plans/history.md": "historical_evidence",
    }
    assert inventory["sources"][0]["authority_disposition"] == "current_source_of_truth"
    batch["state"] = "rolled_back"
    rolled = project_planning_migration_inventory(inventory, batch)
    assert rolled["sources"][0]["authority_disposition"] == "active_import_candidate"


def test_supporting_relation_drift_invalidates_relation_without_primary_projection():
    inventory = {"sources": [
        {"path": "specs/roadmaps/primary.v1.json", "source_hash": "sha256:" + "a" * 64, "authority_disposition": "current_source_of_truth"},
        {"path": "specs/roadmaps/relation.v1.json", "source_hash": "sha256:changed", "authority_disposition": "current_source_of_truth"},
    ]}
    batch = {
        "batch_id": "planning-migration-batch:" + ("a" * 64), "manifest_digest": "sha256:" + ("b" * 64),
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [{"path": "specs/roadmaps/primary.v1.json", "sha256": "a" * 64}],
        "supporting_relations": [{"path": "specs/roadmaps/relation.v1.json", "sha256": "bbb"}],
        "expected_subjects": [], "accepted_subjects": [], "normalized_payloads": {},
        "prepared_receipt_id": "p", "accepted_receipt_id": "a", "terminal_receipt_id": "",
    }
    projected = project_planning_migration_inventory(inventory, batch)
    assert projected["sources"][0]["authority_disposition"] == "migrated_read_only"
    assert projected["sources"][1]["authority_disposition"] == "supporting_relation_invalid"
    assert projected["migration_projection"]["supporting_relation_valid"] is False


def test_migration_inventory_resolves_exact_historical_and_successor_identities():
    inventory = {"sources": [
        {"path": "specs/roadmaps/primary.v1.json", "source_hash": "sha256:" + "a" * 64, "authority_disposition": "current_source_of_truth"},
    ]}
    batch = {
        "batch_id": "planning-migration-batch:" + ("a" * 64),
        "manifest_digest": "sha256:" + ("b" * 64),
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [{"path": "specs/roadmaps/primary.v1.json", "sha256": "a" * 64}],
        "supporting_relations": [], "expected_subjects": [], "accepted_subjects": [],
        "normalized_payloads": {}, "prepared_receipt_id": "p", "accepted_receipt_id": "a",
        "terminal_receipt_id": "", "census_observation": {}, "structural_census": [],
        "principal_identity": {},
    }
    historical = {
        "schema": MIGRATION_MANIFEST_SCHEMA_V1, "owner_id": "fuzzy", "project_id": "harbour-one",
        "source_items": batch["source_items"], "supporting_relations": [], "normalized_payloads": {},
        "authority_pins": HISTORICAL_AUTHORITY_PINS, "structural_census": [],
    }
    batch["manifest_identity"] = historical
    before = deepcopy(batch)
    projected_v1 = project_planning_migration_inventory(inventory, batch)
    assert projected_v1["migration_projection"]["migrated_read_only_count"] == 1
    assert batch == before

    successor = {
        **historical,
        "schema": MIGRATION_MANIFEST_SCHEMA_V2,
        "authority_map_id": SUCCESSOR_AUTHORITY_MAP_ID,
        "authority_pins": SUCCESSOR_AUTHORITY_PINS,
    }
    batch["manifest_identity"] = successor
    before = deepcopy(batch)
    projected_v2 = project_planning_migration_inventory(inventory, batch)
    assert projected_v2["migration_projection"]["migrated_read_only_count"] == 1
    assert batch == before

    docs_only = {
        **historical,
        "schema": MIGRATION_MANIFEST_SCHEMA_V2,
        "authority_map_id": DOCS_ONLY_AUTHORITY_MAP_ID,
        "authority_pins": DOCS_ONLY_AUTHORITY_PINS,
    }
    batch["manifest_identity"] = docs_only
    before = deepcopy(batch)
    projected_v3 = project_planning_migration_inventory(inventory, batch)
    assert projected_v3["migration_projection"]["migrated_read_only_count"] == 1
    assert batch == before

    repo_only = {**historical, "schema": MIGRATION_MANIFEST_SCHEMA_V2,
                 "authority_map_id": REPO_ONLY_AUTHORITY_MAP_ID,
                 "authority_pins": REPO_ONLY_AUTHORITY_PINS}
    batch["manifest_identity"] = repo_only
    before = deepcopy(batch)
    projected_v4 = project_planning_migration_inventory(inventory, batch)
    assert projected_v4["migration_projection"]["migrated_read_only_count"] == 1
    assert batch == before


@pytest.mark.parametrize("mutation", [
    "v1_extra_map_id", "v1_successor_pins", "v2_missing_map_id", "v2_historical_pins",
    "v2_historical_map_id", "v2_unknown_map_id", "unknown_schema", "extra_identity_key",
    "v2_docs_map_with_v2_pins", "v2_docs_map_with_unknown_pin",
])
def test_migration_inventory_rejects_hybrid_or_unknown_identity_before_mutating_input(mutation):
    inventory = {"sources": [
        {"path": "specs/roadmaps/primary.v1.json", "source_hash": "sha256:" + "a" * 64, "authority_disposition": "current_source_of_truth"},
    ]}
    batch = {
        "batch_id": "planning-migration-batch:" + ("a" * 64),
        "manifest_digest": "sha256:" + ("b" * 64),
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [], "supporting_relations": [], "expected_subjects": [], "accepted_subjects": [],
        "normalized_payloads": {}, "prepared_receipt_id": "p", "accepted_receipt_id": "a",
        "terminal_receipt_id": "", "census_observation": {}, "structural_census": [],
        "principal_identity": {},
        "manifest_identity": {
            "schema": MIGRATION_MANIFEST_SCHEMA_V1, "owner_id": "fuzzy", "project_id": "harbour-one",
            "source_items": [], "supporting_relations": [], "normalized_payloads": {},
            "authority_pins": HISTORICAL_AUTHORITY_PINS, "structural_census": [],
        },
    }
    identity = batch["manifest_identity"]
    if mutation == "v1_extra_map_id":
        identity["authority_map_id"] = HISTORICAL_AUTHORITY_MAP_ID
    elif mutation == "v1_successor_pins":
        identity["authority_pins"] = SUCCESSOR_AUTHORITY_PINS
    elif mutation == "v2_missing_map_id":
        identity["schema"] = MIGRATION_MANIFEST_SCHEMA_V2
        identity["authority_pins"] = SUCCESSOR_AUTHORITY_PINS
    elif mutation == "v2_historical_pins":
        identity.update(schema=MIGRATION_MANIFEST_SCHEMA_V2, authority_map_id=SUCCESSOR_AUTHORITY_MAP_ID)
    elif mutation == "v2_historical_map_id":
        identity.update(schema=MIGRATION_MANIFEST_SCHEMA_V2, authority_map_id=HISTORICAL_AUTHORITY_MAP_ID)
    elif mutation == "v2_unknown_map_id":
        identity.update(schema=MIGRATION_MANIFEST_SCHEMA_V2, authority_map_id="unknown-map")
        identity["authority_pins"] = SUCCESSOR_AUTHORITY_PINS
    elif mutation == "v2_docs_map_with_v2_pins":
        identity.update(schema=MIGRATION_MANIFEST_SCHEMA_V2, authority_map_id=DOCS_ONLY_AUTHORITY_MAP_ID)
        identity["authority_pins"] = SUCCESSOR_AUTHORITY_PINS
    elif mutation == "v2_docs_map_with_unknown_pin":
        identity.update(schema=MIGRATION_MANIFEST_SCHEMA_V2, authority_map_id=DOCS_ONLY_AUTHORITY_MAP_ID)
        identity["authority_pins"] = dict(DOCS_ONLY_AUTHORITY_PINS, **{"unknown/path": "0" * 40})
    elif mutation == "unknown_schema":
        identity["schema"] = "odysseus.harbour.planning_migration_manifest.v3"
    else:
        identity["unrecognized"] = True
    before_batch, before_inventory = deepcopy(batch), deepcopy(inventory)
    with pytest.raises(ValueError, match="identity"):
        project_planning_migration_inventory(inventory, batch)
    assert batch == before_batch
    assert inventory == before_inventory


@pytest.mark.parametrize("map_id,pins", [
    (REPO_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_PINS),
    (DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_PINS),
    (REPO_ONLY_AUTHORITY_MAP_ID, {key: value for key, value in REPO_ONLY_AUTHORITY_PINS.items() if key != "docs/plans/evidence/harbour-one-h1r05-repo-acceptance-20261003.receipt"}),
    (REPO_ONLY_AUTHORITY_MAP_ID, {**REPO_ONLY_AUTHORITY_PINS, "unknown/receipt": "0" * 40}),
])
def test_inventory_v4_identity_negatives_leave_inputs_unchanged(map_id, pins):
    inventory = {"sources": [{"path": "specs/roadmaps/primary.v1.json", "source_hash": "sha256:" + "a" * 64, "authority_disposition": "current_source_of_truth"}]}
    batch = {
        "batch_id": "planning-migration-batch:" + "a" * 64, "manifest_digest": "sha256:" + "b" * 64,
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted", "source_items": [],
        "supporting_relations": [], "expected_subjects": [], "accepted_subjects": [], "normalized_payloads": {},
        "prepared_receipt_id": "p", "accepted_receipt_id": "a", "terminal_receipt_id": "", "census_observation": {}, "structural_census": [], "principal_identity": {},
        "manifest_identity": {"schema": MIGRATION_MANIFEST_SCHEMA_V2, "authority_map_id": map_id, "owner_id": "fuzzy", "project_id": "harbour-one", "source_items": [], "supporting_relations": [], "normalized_payloads": {}, "authority_pins": pins, "structural_census": []},
    }
    before_inventory, before_batch = deepcopy(inventory), deepcopy(batch)
    with pytest.raises(ValueError, match="identity"):
        project_planning_migration_inventory(inventory, batch)
    assert inventory == before_inventory
    assert batch == before_batch


def test_source_hash_binds_original_bytes_for_invalid_utf8():
    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap_dir = os.path.join(tmpdir, "specs", "roadmaps")
        os.makedirs(roadmap_dir, exist_ok=True)
        path = os.path.join(roadmap_dir, "bytes.v1.json")
        raw = b'{"plan_id":"bytes"}\xff'
        with open(path, "wb") as handle:
            handle.write(raw)

        payload = build_planning_source_inventory(tmpdir)
        item = payload["sources"][0]
        assert item["source_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_accepted_primary_source_drift_is_not_promoted():
    inventory = {"sources": [{
        "path": "specs/roadmaps/primary.v1.json",
        "source_hash": "sha256:" + "a" * 64,
        "authority_disposition": "current_source_of_truth",
    }]}
    batch = {
        "batch_id": "planning-migration-batch:" + "a" * 64,
        "manifest_digest": "sha256:" + "b" * 64,
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [{"path": "specs/roadmaps/primary.v1.json", "sha256": "b" * 64}],
        "supporting_relations": [], "expected_subjects": [], "accepted_subjects": [],
        "normalized_payloads": {}, "prepared_receipt_id": "p", "accepted_receipt_id": "a",
        "terminal_receipt_id": "",
    }
    projected = project_planning_migration_inventory(inventory, batch)
    assert projected["sources"][0]["authority_disposition"] == "primary_source_drift"
    assert projected["migration_projection"]["primary_source_valid"] is False
    assert projected["migration_projection"]["migrated_read_only_count"] == 0


def test_primary_hash_is_required_and_manifest_paths_cannot_duplicate():
    inventory = {"sources": [{
        "path": "specs/roadmaps/primary.v1.json",
        "source_hash": "sha256:" + "a" * 64,
        "authority_disposition": "current_source_of_truth",
    }]}
    batch = {
        "batch_id": "planning-migration-batch:" + "a" * 64,
        "manifest_digest": "sha256:" + "b" * 64,
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [{"path": "specs/roadmaps/primary.v1.json"}],
        "supporting_relations": [], "expected_subjects": [], "accepted_subjects": [],
        "normalized_payloads": {}, "prepared_receipt_id": "p", "accepted_receipt_id": "a",
        "terminal_receipt_id": "",
    }
    projected = project_planning_migration_inventory(inventory, batch)
    assert projected["sources"][0]["authority_disposition"] == "primary_source_drift"
    assert projected["migration_projection"]["primary_source_valid"] is False
    batch["source_items"].append({"path": "specs/roadmaps/primary.v1.json", "sha256": "a" * 64})
    with pytest.raises(ValueError, match="paths must be unique"):
        project_planning_migration_inventory(inventory, batch)


def test_missing_primary_and_supporting_inventory_rows_fail_closed():
    batch = {
        "batch_id": "planning-migration-batch:" + "a" * 64,
        "manifest_digest": "sha256:" + "b" * 64,
        "owner_id": "fuzzy", "project_id": "harbour-one", "state": "accepted",
        "source_items": [{"path": "specs/roadmaps/primary.v1.json", "sha256": "a" * 64}],
        "supporting_relations": [{"path": "docs/plans/support.md", "sha256": "c" * 64}],
        "expected_subjects": [], "accepted_subjects": [], "normalized_payloads": {},
        "prepared_receipt_id": "p", "accepted_receipt_id": "a", "terminal_receipt_id": "",
    }
    projected = project_planning_migration_inventory({"sources": []}, batch)
    assert projected["migration_projection"]["primary_source_valid"] is False
    assert projected["migration_projection"]["supporting_relation_valid"] is False
    assert projected["migration_projection"]["migrated_read_only_count"] == 0
    batch["source_items"] = []
    batch["supporting_relations"] = [
        {"path": "docs/plans/support.md", "sha256": "c" * 64},
        {"path": "docs/plans/support.md", "sha256": "c" * 64},
    ]
    with pytest.raises(ValueError, match="paths must be unique"):
        project_planning_migration_inventory({"sources": []}, batch)


def test_allowlisted_symlink_cannot_escape_repository_root():
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
        os.makedirs(os.path.join(tmpdir, "specs"), exist_ok=True)
        os.makedirs(os.path.join(outside, "roadmaps"), exist_ok=True)
        with open(os.path.join(outside, "roadmaps", "outside.v1.json"), "w", encoding="utf-8") as handle:
            json.dump({"plan_id": "outside"}, handle)
        link = os.path.join(tmpdir, "specs", "roadmaps")
        try:
            os.symlink(os.path.join(outside, "roadmaps"), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation unavailable")
        with pytest.raises(ValueError, match="escapes repo root"):
            build_planning_source_inventory(tmpdir, allowlist=("specs/roadmaps",))


def test_allowlisted_file_symlink_cannot_escape_repository_root():
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
        roadmap_dir = os.path.join(tmpdir, "specs", "roadmaps")
        os.makedirs(roadmap_dir, exist_ok=True)
        outside_file = os.path.join(outside, "outside.v1.json")
        with open(outside_file, "w", encoding="utf-8") as handle:
            json.dump({"plan_id": "outside"}, handle)
        link = os.path.join(roadmap_dir, "linked.v1.json")
        try:
            os.symlink(outside_file, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation unavailable")
        payload = build_planning_source_inventory(tmpdir, allowlist=("specs/roadmaps",))
        assert payload["sources"] == []


def test_primary_digest_matches_declared_metadata_variants_without_rewriting_identity():
    from src.planning_roadmap_migration import FROZEN_MEMBERS, FROZEN_MEMBER_SHA256_VARIANTS
    for path, adapter, roadmap_id, blob, digest in FROZEN_MEMBERS:
        member = {"path": path, "role": "primary", "adapter": adapter,
                  "roadmap_id": roadmap_id, "blob": blob, "sha256": digest}
        before = deepcopy(member)
        assert migration_primary_digest_matches(member, "sha256:" + digest)
        for variant in FROZEN_MEMBER_SHA256_VARIANTS[path]:
            assert migration_primary_digest_matches(member, "sha256:" + variant)
        assert member == before
        assert not migration_primary_digest_matches(member, "sha256:" + "0" * 64)
        for field in ("role", "adapter", "roadmap_id", "blob", "sha256"):
            forged = dict(member, **{field: "unrecognized-declaration"})
            assert not migration_primary_digest_matches(forged, "sha256:" + digest)
        assert not migration_primary_digest_matches(member, digest)


def test_accepted_inventory_retains_actual_raw_hash_for_approved_metadata_variant():
    from src.planning_roadmap_migration import FROZEN_MEMBERS, FROZEN_MEMBER_SHA256_VARIANTS
    for path, adapter, roadmap_id, blob, digest in FROZEN_MEMBERS:
        alternatives = FROZEN_MEMBER_SHA256_VARIANTS[path] - {digest}
        if not alternatives:
            continue
        variant = sorted(alternatives)[0]
        member = {"path": path, "role": "primary", "adapter": adapter,
                  "roadmap_id": roadmap_id, "blob": blob, "sha256": digest}
        inventory = {"sources": [{"path": path, "source_hash": "sha256:" + variant}]}
        batch = {"batch_id": "planning-migration-batch:" + "a" * 64,
                 "manifest_digest": "sha256:" + "b" * 64, "owner_id": "fuzzy",
                 "project_id": "harbour-one", "state": "accepted",
                 "source_items": [member], "supporting_relations": [],
                 "expected_subjects": [], "accepted_subjects": [], "normalized_payloads": {},
                 "prepared_receipt_id": "p", "accepted_receipt_id": "a", "terminal_receipt_id": ""}
        before = deepcopy(batch)
        projected = project_planning_migration_inventory(inventory, batch)
        assert projected["migration_projection"]["primary_source_valid"] is True
        assert projected["migration_projection"]["migrated_read_only_count"] == 1
        assert projected["sources"][0]["source_hash"] == "sha256:" + variant
        assert projected["sources"][0]["authority_disposition"] == "migrated_read_only"
        assert batch == before and inventory["sources"][0].get("authority_disposition") is None
        return
    pytest.fail("declared metadata variant fixture unavailable")
