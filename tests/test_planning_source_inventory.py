import json
import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.planning_source_inventory import build_planning_source_inventory, diff_planning_source_inventories


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
