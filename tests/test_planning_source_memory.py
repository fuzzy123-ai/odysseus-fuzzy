import json
import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.memory import MemoryManager
from src.planning_source_memory import (
    PLANNING_MEMORY_SOURCE,
    build_planning_memory_capsules,
    ingest_planning_sources_to_memory,
)


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def test_planning_memory_capsules_are_bounded_and_prioritize_current_json_roadmaps():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            os.path.join(tmpdir, "specs", "roadmaps", "current.v1.json"),
            json.dumps(
                {
                    "plan_id": "current",
                    "title": "Current Roadmap",
                    "graph_nodes": [{"id": "node-a", "depends_on": ["node-before"]}],
                }
            ),
        )
        _write(
            os.path.join(tmpdir, "docs", "plans", "historical.md"),
            "# Historical Plan\n\nsecret=very-secret-token should not leak.\n",
        )

        payload = build_planning_memory_capsules(tmpdir, preview_chars=96)

        assert payload["schema"] == "odysseus.planning_source_memory.v1"
        assert payload["read_only"] is True
        assert payload["writes_supported"] is False
        assert payload["summary"]["capsules"] == 2
        current = next(item for item in payload["capsules"] if item["kind"] == "roadmap_json")
        historical = next(item for item in payload["capsules"] if item["kind"] == "planning_doc")
        assert current["precedence_rank"] > historical["precedence_rank"]
        assert current["memory_status"] == "current_source_of_truth"
        assert current["source_ref"] == "specs/roadmaps/current.v1.json"
        assert len(historical["preview"]) <= 96
        assert "very-secret-token" not in historical["preview"]
        assert "[redacted]" in historical["preview"]


def test_planning_source_memory_ingest_is_idempotent_and_updates_changed_sources():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = os.path.join(tmpdir, "repo")
        data = os.path.join(tmpdir, "data")
        path = os.path.join(repo, "specs", "roadmaps", "current.v1.json")
        _write(path, json.dumps({"plan_id": "current", "title": "Current Roadmap"}))
        os.makedirs(data, exist_ok=True)
        manager = MemoryManager(data)

        first = ingest_planning_sources_to_memory(manager, repo, preview_chars=64)
        second = ingest_planning_sources_to_memory(manager, repo, preview_chars=64)
        _write(path, json.dumps({"plan_id": "current", "title": "Current Roadmap Updated"}))
        third = ingest_planning_sources_to_memory(manager, repo, preview_chars=64)
        os.remove(path)
        fourth = ingest_planning_sources_to_memory(manager, repo, preview_chars=64)

        memories = [item for item in manager.load_all() if item.get("source") == PLANNING_MEMORY_SOURCE]
        assert first["summary"]["created"] == 1
        assert second["summary"]["unchanged"] == 1
        assert third["summary"]["updated"] == 1
        assert fourth["summary"]["deleted_marked"] == 1
        assert len(memories) == 1
        assert memories[0]["id"].startswith("planning-source-")
        assert memories[0]["metadata"]["source_status"] == "deleted"
        assert "Current Roadmap Updated" not in memories[0]["text"]


def test_planning_source_memory_routes_return_status_and_dry_run(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = os.path.join(tmpdir, "repo")
        data = os.path.join(tmpdir, "data")
        _write(
            os.path.join(repo, "specs", "roadmaps", "route.v1.json"),
            json.dumps({"plan_id": "route-plan", "title": "Route Plan"}),
        )

        monkeypatch.setenv("ODYSSEUS_ROOT", repo)
        monkeypatch.setenv("ODYSSEUS_DATA_DIR", data)
        monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: None)
        app = FastAPI()
        app.include_router(setup_roadmap_routes())
        client = TestClient(app)

        status = client.get("/api/roadmap/planning-sources/memory/status?preview_chars=32")
        dry_run = client.post("/api/roadmap/planning-sources/memory/ingest?preview_chars=32")
        ingest = client.post("/api/roadmap/planning-sources/memory/ingest?preview_chars=32&dry_run=false")

        assert status.status_code == 200
        assert status.json()["ingest"]["capsules"] == 1
        assert dry_run.status_code == 200
        assert dry_run.json()["dry_run"] is True
        assert dry_run.json()["summary"]["created"] == 1
        assert ingest.status_code == 200
        assert ingest.json()["dry_run"] is False
        assert ingest.json()["summary"]["created"] == 1
        assert len(MemoryManager(data).load_all()) == 1
