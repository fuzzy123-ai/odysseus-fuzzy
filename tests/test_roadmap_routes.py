import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.roadmap_routes import setup_roadmap_routes


def _write_roadmap(path: Path, *, title: str = "Route Roadmap") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "harbor.planning.roadmap",
        "project_id": "route-project",
        "roadmap_id": "route-map",
        "revision": 3,
        "created_at": "2026-07-10T06:00:00Z",
        "updated_at": "2026-07-10T07:00:00Z",
        "title": title,
        "goal": "Serve a bounded admin-only roadmap document.",
        "summary": "Safe route summary",
        "status": "running",
        "source_refs": ["src/planning_mcp_service.py"],
        "slices": [{
            "id": "route-one",
            "title": "Document route",
            "objective": "Return the safe document payload.",
            "class": "repo_only",
            "status": "running",
        }],
        "gates": [{
            "id": "route-gate",
            "class": "repo_only",
            "status": "open",
            "decision_needed": "Verify the read contract.",
        }],
        "gate_refs": ["route-gate"],
        "dependency_refs": [],
        "verification": ["focused route tests"],
        "stop_rules": ["Stop before writes."],
    }, indent=2), encoding="utf-8")


def _client(monkeypatch, repo: Path, admin_calls: list | None = None) -> TestClient:
    monkeypatch.setenv("ODYSSEUS_ROOT", str(repo))
    calls = admin_calls if admin_calls is not None else []
    monkeypatch.setattr("routes.roadmap_routes.require_admin", lambda request: calls.append(request.url.path))
    app = FastAPI()
    app.include_router(setup_roadmap_routes())
    return TestClient(app)


def test_roadmap_document_route_is_admin_gated_bounded_and_read_only(tmp_path: Path, monkeypatch):
    _write_roadmap(tmp_path / "docs" / "plans" / "route-roadmap.json")
    admin_calls: list[str] = []
    client = _client(monkeypatch, tmp_path, admin_calls)

    response = client.get(
        "/api/roadmap/documents/route-project/route-map"
        "?max_items=8&canonical_json_chars=256&include_memory=true"
    )
    payload = response.json()
    encoded = response.text

    assert response.status_code == 200
    assert admin_calls == ["/api/roadmap/documents/route-project/route-map"]
    assert payload["schema"] == "odysseus.planning.roadmap_document.v1"
    assert payload["project_id"] == "route-project"
    assert payload["roadmap_id"] == "route-map"
    assert payload["revision"] == 3
    assert payload["tasks"][0]["id"] == "route-one"
    assert payload["gates"][0]["id"] == "route-gate"
    assert payload["memory_summary"]["requested"] is True
    assert payload["canonical"]["json_preview_chars"] <= 256
    assert payload["writes_supported"] is False
    assert payload["raw_content_included"] is False
    assert str(tmp_path) not in encoded
    assert "synthetic-secret-value" not in encoded


def test_roadmap_document_route_returns_safe_404_and_400_errors(tmp_path: Path, monkeypatch):
    _write_roadmap(tmp_path / "docs" / "plans" / "route-roadmap.json")
    client = _client(monkeypatch, tmp_path)

    missing = client.get("/api/roadmap/documents/route-project/missing-map")
    invalid = client.get("/api/roadmap/documents/INVALID/route-map")
    over_budget = client.get("/api/roadmap/documents/route-project/route-map?max_items=25")

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "roadmap_document_not_found"
    assert "missing-map" not in missing.text
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_document_id"
    assert "INVALID" not in invalid.text
    assert over_budget.status_code == 400
    assert over_budget.json()["detail"]["code"] == "invalid_document_budget"


def test_roadmap_document_route_fails_closed_on_ambiguous_identity(tmp_path: Path, monkeypatch):
    _write_roadmap(tmp_path / "docs" / "plans" / "one.json", title="One")
    _write_roadmap(tmp_path / "docs" / "plans" / "two.json", title="Two")
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/roadmap/documents/route-project/route-map")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "roadmap_document_ambiguous"
    assert "one.json" not in response.text
    assert "two.json" not in response.text


def test_roadmap_document_surface_has_no_write_method(tmp_path: Path, monkeypatch):
    _write_roadmap(tmp_path / "docs" / "plans" / "route-roadmap.json")
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/roadmap/documents/route-project/route-map",
        json={"goal": "must not write"},
    )

    assert response.status_code == 405


def _proposal_bases(client: TestClient) -> dict:
    document = client.get("/api/roadmap/documents/route-project/route-map").json()
    return {
        "base_source_hash": document["source_hash"],
        "base_revision": document["revision"],
        "base_projection_hash": document["canonical"]["projection_hash"],
    }


def test_roadmap_document_summary_proposal_is_admin_gated_and_read_only(tmp_path: Path, monkeypatch):
    roadmap = tmp_path / "docs" / "plans" / "route-roadmap.json"
    _write_roadmap(roadmap)
    before = roadmap.read_bytes()
    admin_calls: list[str] = []
    client = _client(monkeypatch, tmp_path, admin_calls)
    bases = _proposal_bases(client)

    response = client.post(
        "/api/roadmap/documents/route-project/route-map/proposals",
        json={
            "section": "summary",
            "section_id": "summary",
            "proposed_value": "A reviewed route summary.",
            "reason": "Clarify the route document.",
            **bases,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert admin_calls == [
        "/api/roadmap/documents/route-project/route-map",
        "/api/roadmap/documents/route-project/route-map/proposals",
    ]
    assert payload["status"] == "ready"
    assert payload["operations"][0]["path"] == "/summary"
    assert payload["writes_performed"] is False
    assert payload["events_emitted"] is False
    assert payload["notifications_emitted"] is False
    assert payload["apply_supported"] is False
    assert roadmap.read_bytes() == before


def test_roadmap_document_proposal_reports_conflict_without_echoing_value(tmp_path: Path, monkeypatch):
    roadmap = tmp_path / "docs" / "plans" / "route-roadmap.json"
    _write_roadmap(roadmap)
    before = roadmap.read_bytes()
    client = _client(monkeypatch, tmp_path)
    bases = _proposal_bases(client)
    bases["base_projection_hash"] = "sha256:" + ("0" * 64)

    response = client.post(
        "/api/roadmap/documents/route-project/route-map/proposals",
        json={
            "section": "summary",
            "section_id": "summary",
            "proposed_value": "conflict-value-must-not-be-echoed",
            "reason": "Exercise conflict handling.",
            **bases,
        },
    )

    assert response.status_code == 409
    assert response.json()["status"] == "conflict"
    assert response.json()["writes_performed"] is False
    assert response.json()["conflicts"][0]["code"] == "projection_hash_mismatch"
    assert "conflict-value-must-not-be-echoed" not in response.text
    assert roadmap.read_bytes() == before


def test_roadmap_document_task_missing_is_404_and_invalid_data_is_safe_400(tmp_path: Path, monkeypatch):
    roadmap = tmp_path / "docs" / "plans" / "route-roadmap.json"
    _write_roadmap(roadmap)
    before = roadmap.read_bytes()
    client = _client(monkeypatch, tmp_path)
    document = client.get("/api/roadmap/documents/route-project/route-map").json()
    bases = {
        "base_source_hash": document["source_hash"],
        "base_revision": document["revision"],
        "base_projection_hash": document["canonical"]["projection_hash"],
    }

    missing = client.post(
        "/api/roadmap/documents/route-project/route-map/proposals",
        json={
            "section": "task",
            "section_id": "tasks",
            "task_id": "missing-task",
            "proposed_value": "Do not find this task.",
            "reason": "Exercise missing task handling.",
            **bases,
        },
    )
    candidate = json.loads(json.dumps(document["canonical"]["projection"]))
    candidate["slices"] = []
    invalid = client.post(
        "/api/roadmap/documents/route-project/route-map/proposals",
        json={
            "section": "data",
            "section_id": "data",
            "proposed_payload": candidate,
            "reason": "Exercise safe Data validation.",
            **bases,
        },
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "document_task_not_found"
    assert "missing-task" not in missing.text
    assert invalid.status_code == 400
    assert invalid.json()["status"] == "invalid"
    assert invalid.json()["rejected_value_visible"] is False
    assert "missing_slices" in {error["code"] for error in invalid.json()["validation"]["errors"]}
    assert "proposed_payload" not in invalid.text
    assert roadmap.read_bytes() == before


def test_roadmap_document_has_no_apply_endpoint(tmp_path: Path, monkeypatch):
    _write_roadmap(tmp_path / "docs" / "plans" / "route-roadmap.json")
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/roadmap/documents/route-project/route-map/apply",
        json={"patch_id": "not-applicable"},
    )

    assert response.status_code == 404


def test_section_context_get_is_admin_gated_redacted_and_never_dispatches(tmp_path: Path, monkeypatch):
    roadmap = tmp_path / "docs" / "plans" / "route-roadmap.json"
    _write_roadmap(roadmap)
    before = roadmap.read_bytes()
    admin_calls: list[str] = []
    client = _client(monkeypatch, tmp_path, admin_calls)

    response = client.get(
        "/api/roadmap/documents/route-project/route-map/context-pack"
        "?section_id=summary&include_memory=false&client_id=profile:spark"
    )
    payload = response.json()

    assert response.status_code == 200
    assert admin_calls == ["/api/roadmap/documents/route-project/route-map/context-pack"]
    assert payload["schema"] == "odysseus.planning.section_context_pack.v1"
    assert payload["section_id"] == "summary"
    assert payload["content"]["summary"] == "Safe route summary"
    assert payload["agent_dispatch_performed"] is False
    assert payload["writes_supported"] is False
    assert payload["events_emitted"] is False
    assert payload["notifications_emitted"] is False
    assert payload["audit_descriptor"]["client_id"] == "profile:spark"
    assert payload["audit_descriptor"]["section_values_visible"] is False
    assert roadmap.read_bytes() == before


def test_section_context_post_resolves_exact_task_with_value_free_audit(tmp_path: Path, monkeypatch):
    roadmap = tmp_path / "docs" / "plans" / "route-roadmap.json"
    _write_roadmap(roadmap)
    before = roadmap.read_bytes()
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/roadmap/documents/route-project/route-map/context-pack",
        json={
            "section_id": "tasks",
            "task_id": "route-one",
            "include_memory": False,
            "client_id": "C:\\private\\spark-profile.json",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["item_id"] == "route-one"
    assert payload["item_kind"] == "task"
    assert payload["content"]["items"][0]["id"] == "route-one"
    assert payload["audit_descriptor"]["client_id"].startswith("client:")
    assert payload["audit_descriptor"]["argument_fields"] == [
        "include_memory", "max_items", "project_id", "roadmap_id", "section_id", "task_id",
    ]
    assert "C:\\private" not in response.text
    assert roadmap.read_bytes() == before


def test_section_context_routes_return_safe_404_and_400(tmp_path: Path, monkeypatch):
    _write_roadmap(tmp_path / "docs" / "plans" / "route-roadmap.json")
    client = _client(monkeypatch, tmp_path)

    missing = client.get(
        "/api/roadmap/documents/route-project/route-map/context-pack"
        "?section_id=gates&gate_id=missing-gate"
    )
    invalid = client.post(
        "/api/roadmap/documents/route-project/route-map/context-pack",
        json={"section_id": "data", "item_id": "not-supported"},
    )
    unsupported = client.post(
        "/api/roadmap/documents/route-project/route-map/context-pack",
        json={"section_id": "summary", "dispatch": True},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "section_gate_not_found"
    assert "missing-gate" not in missing.text
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_section_item"
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"]["code"] == "invalid_section_context_request"
