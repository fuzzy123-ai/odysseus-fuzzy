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
            "source_hash": "sha256:" + "a" * 64,
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
            "source_hash": "sha256:" + "a" * 64,
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
                "source_hash": "sha256:" + "b" * 64,
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
            "source_hash": "sha256:" + (f"{index + 1:064x}"),
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


def test_superseded_json_cannot_win_current_authority_precedence():
    candidates = [
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "authority_disposition": "superseded_plan_source",
            "source_id": "repo-plan:old",
            "source_ref": "specs/roadmaps/old.v1.json",
            "source_hash": "sha256:" + "e" * 64,
            "project_id": "demo-project",
            "precedence_rank": 1000,
        },
        {
            "source": "planning_source",
            "source_status": "active",
            "acceptance_status": "accepted",
            "authority_disposition": "current_source_of_truth",
            "source_id": "repo-plan:current",
            "source_ref": "specs/roadmaps/current.v1.json",
            "source_hash": "sha256:" + "f" * 64,
            "project_id": "demo-project",
            "precedence_rank": 1,
        },
    ]

    result = project_accepted_planning_memory(
        candidates,
        source_id="",
        source_ref="",
        project_id="demo-project",
        limit=1,
    )

    assert [entry["source_id"] for entry in result["entries"]] == ["repo-plan:current"]
    assert result["entries"][0]["authority_disposition"] == "current_source_of_truth"


def test_explicit_superseded_status_dominates_accepted_flag_and_rank():
    candidate = {
        "source": "planning_source",
        "source_status": "superseded",
        "acceptance_status": "accepted",
        "accepted": True,
        "source_id": "repo-plan:old",
        "source_ref": "specs/roadmaps/old.v1.json",
        "source_hash": "sha256:" + "e" * 64,
        "project_id": "demo-project",
        "precedence_rank": 1000,
    }

    result = project_accepted_planning_memory(
        [candidate], source_id="", source_ref="", project_id="demo-project", limit=1
    )

    assert result["entries"] == []
    assert result["summary"]["rejected"] == 1


def test_ingest_reclassifies_previously_promoted_superseded_memory_in_place():
    class LocalMemory:
        def __init__(self):
            self.entries = []
            self.save_calls = 0

        def load_all(self):
            return list(self.entries)

        def add_entry(self, text, **_kwargs):
            return {"id": "generated", "text": text, **_kwargs}

        def save(self, entries):
            self.entries = list(entries)
            self.save_calls += 1

    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap = os.path.join(tmpdir, "specs", "roadmaps", "current.v1.json")
        _write(roadmap, json.dumps({"plan_id": "current", "title": "Current"}))
        memory = LocalMemory()
        ingest_planning_sources_to_memory(memory, tmpdir)
        original_id = memory.entries[0]["id"]
        original_snapshot = json.dumps(memory.entries, sort_keys=True)
        _write(
            roadmap,
            json.dumps({"plan_id": "current", "title": "Former Current", "status": "superseded"}),
        )

        dry_run = ingest_planning_sources_to_memory(memory, tmpdir, dry_run=True)
        assert dry_run["summary"]["updated"] == 1
        assert json.dumps(memory.entries, sort_keys=True) == original_snapshot
        assert memory.save_calls == 1

        live = ingest_planning_sources_to_memory(memory, tmpdir)
        assert live["summary"]["updated"] == 1
        assert len(memory.entries) == 1
        assert memory.entries[0]["id"] == original_id
        assert memory.entries[0]["metadata"]["authority_disposition"] == "superseded_plan_source"
        assert memory.entries[0]["metadata"]["acceptance_status"] != "accepted"
        assert memory.entries[0]["metadata"]["precedence_rank"] == 0


def test_current_and_statusless_legacy_json_keep_compatible_current_precedence():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            os.path.join(tmpdir, "specs", "roadmaps", "legacy.v1.json"),
            json.dumps({"plan_id": "legacy", "title": "Legacy Current"}),
        )
        _write(os.path.join(tmpdir, "docs", "plans", "support.md"), "# Supporting plan")

        capsules = build_planning_memory_capsules(tmpdir)["capsules"]
        legacy = next(item for item in capsules if item["plan_id"] == "legacy")
        support = next(item for item in capsules if item["kind"] == "planning_doc")

        assert legacy["authority_disposition"] == "current_source_of_truth"
        assert legacy["memory_status"] == "current_source_of_truth"
        assert legacy["precedence_rank"] > support["precedence_rank"]


def test_global_ingest_does_not_touch_private_rows():
    class LocalMemory:
        def __init__(self, entries):
            self.entries = entries

        def load_all(self):
            return list(self.entries)

        def add_entry(self, text, **kwargs):
            return {"id": "generated", "text": text, **kwargs}

        def save(self, entries):
            self.entries = list(entries)

    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap = os.path.join(tmpdir, "specs", "roadmaps", "current.v1.json")
        _write(roadmap, json.dumps({"plan_id": "current", "title": "Current"}))
        private = {
            "id": "private-row",
            "source": PLANNING_MEMORY_SOURCE,
            "owner": "private-a",
            "text": "private text",
            "metadata": {
                "source_id": "repo-plan:private",
                "source_status": "active",
            },
        }
        memory = LocalMemory([private])
        result = ingest_planning_sources_to_memory(memory, tmpdir)
        assert result["summary"]["created"] == 1
        assert memory.entries[0] == private
        assert any(item.get("owner") is None for item in memory.entries)


def test_explicit_owner_ingest_updates_only_that_owner():
    class LocalMemory:
        def __init__(self, entries):
            self.entries = entries

        def load_all(self):
            return list(self.entries)

        def add_entry(self, text, **kwargs):
            return {"id": "generated", "text": text, **kwargs}

        def save(self, entries):
            self.entries = list(entries)

    with tempfile.TemporaryDirectory() as tmpdir:
        roadmap = os.path.join(tmpdir, "specs", "roadmaps", "current.v1.json")
        _write(roadmap, json.dumps({"plan_id": "current", "title": "Current"}))
        capsule = build_planning_memory_capsules(tmpdir)["capsules"][0]
        private = {
            "id": "private-row",
            "source": PLANNING_MEMORY_SOURCE,
            "owner": "private-a",
            "text": "old text",
            "metadata": {
                "source_id": capsule["source_id"],
                "source_hash": "sha256:" + "0" * 64,
                "source_status": "active",
            },
        }
        memory = LocalMemory([private])
        result = ingest_planning_sources_to_memory(memory, tmpdir, owner="private-a")
        assert result["summary"]["updated"] == 1
        assert memory.entries[0]["owner"] == "private-a"
        assert memory.entries[0]["metadata"]["source_hash"] == capsule["source_hash"]


def test_projection_rejects_private_candidates_and_invalid_hashes():
    base = {
        "source": PLANNING_MEMORY_SOURCE,
        "source_status": "active",
        "acceptance_status": "accepted",
        "source_id": "repo-plan:target",
        "source_ref": "docs/plans/target.json",
        "project_id": "demo-project",
        "source_hash": "sha256:" + "c" * 64,
    }
    candidates = [
        dict(base, owner="private-a"),
        dict(base, source_id="repo-plan:metadata-private", metadata={"owner": "private-a"}),
        dict(base, source_id="repo-plan:short", source_hash="sha256:abc"),
        dict(base, source_id="repo-plan:missing", source_hash=None),
        dict(base, source_id="repo-plan:public"),
        dict(base, source_id="repo-plan:bare", source_hash="d" * 64),
    ]
    result = project_accepted_planning_memory(
        candidates,
        source_id="",
        source_ref="",
        project_id="demo-project",
        limit=10,
    )
    assert [item["source_id"] for item in result["entries"]] == ["repo-plan:bare", "repo-plan:public"]
    assert result["summary"]["rejected"] == 4
    assert result["entries"][0]["source_hash"] == "sha256:" + "d" * 64
    assert result["entries"][1]["source_hash"] == "sha256:" + "c" * 64


def test_projection_rejects_falsy_malformed_owner_values_at_both_boundaries():
    base = {
        "source": PLANNING_MEMORY_SOURCE,
        "source_status": "active",
        "acceptance_status": "accepted",
        "source_ref": "docs/plans/owner.json",
        "project_id": "demo-project",
        "source_hash": "sha256:" + "e" * 64,
    }
    candidates = [
        dict(base, source_id="repo-plan:top-zero", owner=0),
        dict(base, source_id="repo-plan:top-false", owner=False),
        dict(base, source_id="repo-plan:top-list", owner=[]),
        dict(base, source_id="repo-plan:top-map", owner={}),
        dict(base, source_id="repo-plan:meta-zero", metadata={"owner": 0}),
        dict(base, source_id="repo-plan:meta-false", metadata={"owner": False}),
        dict(base, source_id="repo-plan:meta-list", metadata={"owner": []}),
        dict(base, source_id="repo-plan:meta-map", metadata={"owner": {}}),
        dict(base, source_id="repo-plan:shared", owner=""),
    ]
    result = project_accepted_planning_memory(
        candidates,
        source_id="",
        source_ref="",
        project_id="demo-project",
        limit=20,
    )
    assert [item["source_id"] for item in result["entries"]] == ["repo-plan:shared"]
    assert result["summary"]["rejected"] == 8


def test_global_ingest_preserves_falsy_malformed_owner_rows():
    class LocalMemory:
        def __init__(self, entries):
            self.entries = entries

        def load_all(self):
            return list(self.entries)

        def add_entry(self, text, **kwargs):
            return {"id": "generated", "text": text, **kwargs}

        def save(self, entries):
            self.entries = list(entries)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            os.path.join(tmpdir, "specs", "roadmaps", "current.v1.json"),
            json.dumps({"plan_id": "current", "title": "Current"}),
        )
        malformed_rows = [
            {"id": f"row-{index}", "source": PLANNING_MEMORY_SOURCE, "owner": owner,
             "metadata": {"source_id": f"repo-plan:malformed-{index}"}}
            for index, owner in enumerate((0, False, [], {}))
        ]
        memory = LocalMemory(malformed_rows)
        ingest_planning_sources_to_memory(memory, tmpdir)
        assert memory.entries[:4] == malformed_rows
