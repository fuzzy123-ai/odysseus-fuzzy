import json
import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes
from src.memory import MemoryManager
from src.planning_source_memory import (
    PLANNING_MEMORY_SOURCE,
    build_derived_planning_memory_records,
    build_planning_memory_capsules,
    ingest_planning_sources_to_memory,
    project_accepted_planning_memory,
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
        assert current["source"] == "planning_source"
        assert current["source_status"] == "active"
        assert current["acceptance_status"] == "accepted"
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


def test_accepted_planning_memory_projection_filters_matches_dedupes_and_never_returns_bodies():
    candidates = [
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": "repo-plan:core",
            "source_ref": "docs/plans/core.json",
            "source_hash": "sha256:aaa",
            "project_id": "demo-project",
            "roadmap_id": "core-map",
            "precedence_rank": 100,
            "preview": "Safe summary token=synthetic-secret-value",
            "text": "RAW PRIVATE BODY MUST NEVER APPEAR",
        },
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": "repo-plan:core",
            "source_ref": "docs/plans/core.json",
            "precedence_rank": 50,
            "preview": "lower precedence duplicate",
        },
        {
            "source": "planning_source",
            "metadata": {
                "source_status": "active",
                "acceptance_status": "accepted",
                "source_id": "repo-plan:related",
                "source_ref": "docs/plans/related.json",
                "project_id": "demo-project",
                "safe_summary": "Related accepted summary",
                "precedence_rank": 80,
            },
            "text": "another raw body",
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
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "candidate",
            "source_id": "repo-plan:unaccepted",
            "source_ref": "docs/plans/unaccepted.json",
            "project_id": "demo-project",
        },
        {
            "source": "other_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": "repo-plan:wrong-source",
            "source_ref": "docs/plans/wrong.json",
            "project_id": "demo-project",
        },
    ]

    result = project_accepted_planning_memory(
        candidates,
        source_id="repo-plan:core",
        source_ref="docs/plans/core.json",
        project_id="demo-project",
        roadmap_id="core-map",
        limit=10,
        preview_chars=80,
    )
    encoded = json.dumps(result)

    assert [item["source_id"] for item in result["entries"]] == ["repo-plan:core", "repo-plan:related"]
    assert result["summary"]["deduplicated"] == 1
    assert result["raw_bodies_included"] is False
    assert "RAW PRIVATE BODY" not in encoded
    assert "another raw body" not in encoded
    assert "synthetic-secret-value" not in encoded
    assert "[redacted]" in encoded
    assert all(item["raw_body_included"] is False for item in result["entries"])


def test_accepted_planning_memory_projection_is_deterministic_bounded_and_explicitly_truncated():
    candidates = [
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "source_id": f"repo-plan:{index:02d}",
            "source_ref": f"docs/plans/{index:02d}.json",
            "project_id": "demo-project",
            "precedence_rank": index,
            "preview": "x" * 500,
        }
        for index in range(20)
    ]

    first = project_accepted_planning_memory(
        candidates,
        source_id="repo-plan:target",
        source_ref="docs/plans/target.json",
        project_id="demo-project",
        limit=5,
        preview_chars=40,
    )
    second = project_accepted_planning_memory(
        reversed(candidates),
        source_id="repo-plan:target",
        source_ref="docs/plans/target.json",
        project_id="demo-project",
        limit=5,
        preview_chars=40,
    )

    assert first == second
    assert [item["precedence_rank"] for item in first["entries"]] == [19, 18, 17, 16, 15]
    assert all(len(item["preview"]) == 40 for item in first["entries"])
    assert first["summary"]["truncated"] is True
    assert first["summary"]["incomplete"] is True


def test_derived_planning_memory_uses_only_validated_safe_rebuildable_metadata():
    raw = {
        "validation": {"valid": True, "mode": "canonical"},
        "project_id": "harbor-core",
        "roadmap_id": "planning-mcp",
        "source_id": "repo-plan:abc123",
        "source_ref": "docs/plans/planning-mcp-roadmap.json",
        "source_hash": "a" * 64,
        "revision": 7,
        "safe_summary": "Safe summary token=synthetic-secret-value C:/private/roadmap.json",
        "gate_refs": ["write-go", "write-go", {"gate_id": "operator-review"}],
        "dependency_refs": ["storage-contract", "storage-contract"],
        "source_refs": ["docs/plans/source.md", "C:/private/source.md", "../escape.json"],
        "classification": "private",
        "acceptance_status": "accepted",
        "source_status": "current",
        "raw_body": "RAW PRIVATE BODY",
        "provider_output": "MUST NOT LEAK",
    }

    first = build_derived_planning_memory_records([raw])
    second = build_derived_planning_memory_records([dict(raw)])
    entry = first["entries"][0]
    encoded = json.dumps(first, sort_keys=True)

    assert first == second
    assert entry["memory_ref"] == "planning:harbor-core:planning-mcp"
    assert entry["source_hash"] == "sha256:" + "a" * 64
    assert entry["source_revision_ref"] == "repo-plan:abc123@7"
    assert entry["gate_refs"] == ["gate:operator-review", "gate:write-go"]
    assert entry["dependency_refs"] == ["roadmap:storage-contract"]
    assert entry["source_refs"] == ["docs/plans/planning-mcp-roadmap.json", "docs/plans/source.md"]
    assert entry["derived"] is True
    assert entry["rebuildable"] is True
    assert entry["source_of_truth"] is False
    assert entry["redaction"]["raw_body_included"] is False
    assert "synthetic-secret-value" not in encoded
    assert "C:/private" not in encoded
    assert "RAW PRIVATE BODY" not in encoded
    assert "MUST NOT LEAK" not in encoded


def test_derived_planning_memory_rejects_unvalidated_metadata_and_dedupes_by_stable_ids():
    def candidate(revision, *, valid=True, mode="transition"):
        return {
            "validation": {"valid": valid, "mode": mode},
            "project_id": "demo-project",
            "roadmap_id": "demo-roadmap",
            "source_id": "repo-plan:def456",
            "source_ref": "docs/plans/demo.json",
            "source_hash": ("b" if revision == 2 else "c") * 64,
            "revision": revision,
            "summary": f"Revision {revision}",
            "acceptance_status": "accepted",
            "source_status": "active",
        }

    result = build_derived_planning_memory_records(
        [candidate(1), candidate(2), candidate(3, valid=False), candidate(4, mode="draft")],
        max_records=10,
    )

    assert result["summary"] == {
        "input": 4,
        "accepted": 2,
        "rejected": 2,
        "deduplicated": 1,
        "returned": 1,
        "truncated": False,
    }
    assert result["entries"][0]["revision"] == 2
    assert result["entries"][0]["provenance"]["validation_mode"] == "transition"
    assert result["source_revision_refs"] == ["repo-plan:def456@2"]
