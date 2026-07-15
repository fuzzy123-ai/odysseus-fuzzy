from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routes.planning_definition_routes import setup_planning_definition_routes
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import (
    TEMPORARY_REPOSITORY_MARKER,
    TEMPORARY_REPOSITORY_SCHEMA,
    PlanningRevisionRepository,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)
from tests.test_planning_definition_projection import definition_fixture


CURSOR_SECRET = b"0123456789abcdef0123456789abcdef"


def _owner(request):
    return request.headers.get("X-Test-Owner")


def _admin(request):
    return request.headers.get("X-Test-Admin") == "yes"


def _csrf(request):
    return request.headers.get("X-Test-CSRF") == "valid"


def _headers(
    owner: str = "alice",
    *,
    admin: bool = True,
    csrf: bool = False,
) -> dict[str, str]:
    headers = {"X-Test-Owner": owner}
    if admin:
        headers["X-Test-Admin"] = "yes"
    if csrf:
        headers["X-Test-CSRF"] = "valid"
    return headers


def _store(*, projects: int = 1) -> PlanningRevisionStore:
    records = [
        (
            "alice",
            definition_fixture(f"project-{index}", f"roadmap-{index}"),
            f"alice-{index}.json",
        )
        for index in range(projects)
    ]
    records.append(("bob", definition_fixture("project-b", "roadmap-b"), "bob.json"))
    return PlanningRevisionStore(records, cursor_secret=CURSOR_SECRET)


def _client(
    *,
    store: PlanningRevisionStore | None = None,
    write_service: PlanningRevisionRepository | None = None,
    fail_closed: bool = False,
):
    selected = store or _store()
    kwargs = {}
    if not fail_closed:
        kwargs = {
            "owner_resolver": _owner,
            "admin_gate": _admin,
            "csrf_gate": _csrf,
        }
    app = FastAPI()
    router = setup_planning_definition_routes(
        selected,
        write_service=write_service,
        **kwargs,
    )
    app.include_router(router)
    return TestClient(app), selected, router


def _write_repository(tmp_path: Path) -> tuple[PlanningRevisionRepository, dict]:
    root = tmp_path / "planning-route-repository"
    root.mkdir()
    (root / TEMPORARY_REPOSITORY_MARKER).write_text(
        TEMPORARY_REPOSITORY_SCHEMA,
        encoding="utf-8",
    )
    document = definition_fixture("project-0", "roadmap-0", include_draft=False)
    (root / "definition.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (
        PlanningRevisionRepository(
            root,
            owner="alice",
            cursor_secret=CURSOR_SECRET,
        ),
        document,
    )


def test_route_factory_declares_five_reads_three_writes_and_one_handoff() -> None:
    _client_instance, _selected, router = _client()
    surface = {
        (route.path, frozenset(route.methods or set()))
        for route in router.routes
    }

    assert surface == {
        ("/api/planning/projects", frozenset({"GET"})),
        ("/api/planning/projects/{project_id}", frozenset({"GET"})),
        ("/api/planning/projects/{project_id}/roadmaps", frozenset({"GET"})),
        (
            "/api/planning/projects/{project_id}/roadmaps/{roadmap_id}",
            frozenset({"GET"}),
        ),
        (
            "/api/planning/projects/{project_id}/roadmaps/{roadmap_id}/revisions",
            frozenset({"GET"}),
        ),
        (
            "/api/planning/projects/{project_id}/roadmaps/{roadmap_id}/drafts",
            frozenset({"POST"}),
        ),
        (
            "/api/planning/projects/{project_id}/roadmaps/{roadmap_id}/drafts/{draft_id}/validate",
            frozenset({"POST"}),
        ),
        (
            "/api/planning/projects/{project_id}/roadmaps/{roadmap_id}/drafts/{draft_id}/actions",
            frozenset({"POST"}),
        ),
        (
            "/api/planning/projects/{project_id}/roadmaps/{roadmap_id}/agent-handoff",
            frozenset({"POST"}),
        ),
    }


def test_factory_defaults_fail_closed_without_owner_or_admin_integration() -> None:
    client, _store_instance, _router = _client(fail_closed=True)

    response = client.get("/api/planning/projects")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


@pytest.mark.parametrize(
    "path",
    [
        "/api/planning/projects",
        "/api/planning/projects/project-0",
        "/api/planning/projects/project-0/roadmaps",
        "/api/planning/projects/project-0/roadmaps/roadmap-0",
        "/api/planning/projects/project-0/roadmaps/roadmap-0/revisions",
    ],
)
def test_every_read_requires_owner_and_admin(path: str) -> None:
    client, _store_instance, _router = _client()

    assert client.get(path).status_code == 401
    assert client.get(path, headers=_headers(admin=False)).status_code == 403


def test_five_reads_share_one_owner_scoped_definition_model() -> None:
    client, _store_instance, _router = _client()
    headers = _headers()

    projects = client.get("/api/planning/projects", headers=headers)
    project = client.get("/api/planning/projects/project-0", headers=headers)
    roadmaps = client.get(
        "/api/planning/projects/project-0/roadmaps",
        headers=headers,
    )
    approved = client.get(
        "/api/planning/projects/project-0/roadmaps/roadmap-0",
        headers=headers,
    )
    draft = client.get(
        "/api/planning/projects/project-0/roadmaps/roadmap-0?revision=2",
        headers=headers,
    )
    revisions = client.get(
        "/api/planning/projects/project-0/roadmaps/roadmap-0/revisions",
        headers=headers,
    )

    assert [response.status_code for response in (projects, project, roadmaps, approved, draft, revisions)] == [200] * 6
    assert projects.json()["items"][0]["project_id"] == "project-0"
    assert project.json()["project"]["project_id"] == "project-0"
    assert roadmaps.json()["items"][0]["roadmap_id"] == "roadmap-0"
    assert approved.json()["roadmap"]["revision"] == 1
    assert draft.json()["roadmap"]["revision"] == 2
    assert [item["revision"] for item in revisions.json()["items"]] == [1, 2]
    assert approved.json()["roadmap"]["content_hash"] == revisions.json()["items"][0]["content_hash"]
    assert set(approved.json()["origin"]) == {"state", "source", "reason", "as_of"}
    assert approved.json()["launch_authorized"] is False


def test_agent_handoff_route_returns_only_exact_non_launching_composer_envelope() -> None:
    client, store, _router = _client()
    roadmap = store.get_roadmap(
        "alice",
        "project-0",
        "roadmap-0",
        revision=1,
    )["roadmap"]
    path = "/api/planning/projects/project-0/roadmaps/roadmap-0/agent-handoff"

    response = client.post(
        path,
        headers=_headers(),
        json={"revision": 1, "content_hash": roadmap["content_hash"]},
    )
    mismatch = client.post(
        path,
        headers=_headers(),
        json={"revision": 1, "content_hash": "sha256:" + ("f" * 64)},
    )
    non_approved = client.post(
        path,
        headers=_headers(),
        json={
            "revision": 2,
            "content_hash": store.get_roadmap(
                "alice", "project-0", "roadmap-0", revision=2
            )["roadmap"]["content_hash"],
        },
    )
    envelope = response.json()

    assert response.status_code == 200
    assert envelope["composer_text"] == (
        f"/abc run roadmap:roadmap-0@1 hash:{roadmap['content_hash']}"
    )
    assert envelope["launch_authorized"] is False
    assert envelope["read_only"] is True
    assert not set(envelope) & {
        "skill",
        "skills",
        "model",
        "models",
        "run",
        "run_id",
        "command",
        "commands",
    }
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "handoff_hash_mismatch"
    assert non_approved.status_code == 409
    assert non_approved.json()["error"] == "handoff_revision_not_approved"


def test_agent_handoff_route_rejects_superseded_revision_without_latest_substitution() -> None:
    document = definition_fixture("project-0", "roadmap-0", include_draft=True)
    newer = document["roadmaps"][1]
    newer["revision_state"] = "approved"
    newer["content_hash"] = compute_roadmap_content_hash(newer)
    document["project"]["latest_approved_revision"]["roadmap-0"] = {
        "revision": 2,
        "content_hash": newer["content_hash"],
    }
    store = PlanningRevisionStore([("alice", document, "definition.json")])
    client, _store_instance, _router = _client(store=store)
    older = document["roadmaps"][0]

    response = client.post(
        "/api/planning/projects/project-0/roadmaps/roadmap-0/agent-handoff",
        headers=_headers(),
        json={"revision": 1, "content_hash": older["content_hash"]},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "handoff_revision_superseded"


def test_write_routes_fail_closed_without_auth_admin_csrf_or_configured_service() -> None:
    client, _store_instance, _router = _client()
    path = "/api/planning/projects/project-0/roadmaps/roadmap-0/drafts"
    body = {
        "base_revision": 1,
        "base_hash": "sha256:" + ("0" * 64),
        "idempotency_key": "route-create-0001",
        "changes": {"operation": "update", "set": {"title": "Changed"}},
    }

    assert client.post(path, json=body).status_code == 401
    assert client.post(path, headers=_headers(admin=False, csrf=True), json=body).status_code == 403
    assert client.post(path, headers=_headers(), json=body).status_code == 403
    configured = client.post(path, headers=_headers(csrf=True), json=body)
    assert configured.status_code == 403
    assert configured.json()["detail"] == "Planning writes are not configured"


def test_three_write_routes_apply_once_and_refresh_the_read_model(tmp_path: Path) -> None:
    repository, document = _write_repository(tmp_path)
    client, _store_instance, _router = _client(
        store=repository.snapshot_store(),
        write_service=repository,
    )
    headers = _headers(csrf=True)
    base = document["roadmaps"][0]
    create_body = {
        "base_revision": base["revision"],
        "base_hash": base["content_hash"],
        "idempotency_key": "route-create-0002",
        "changes": {"operation": "update", "set": {"title": "Route accepted"}},
    }

    created = client.post(
        "/api/planning/projects/project-0/roadmaps/roadmap-0/drafts",
        headers=headers,
        json=create_body,
    )
    duplicate_create = client.post(
        "/api/planning/projects/project-0/roadmaps/roadmap-0/drafts",
        headers=headers,
        json=create_body,
    )
    draft = created.json()
    project_with_draft = client.get(
        "/api/planning/projects/project-0",
        headers=_headers(),
    ).json()
    validated = client.post(
        f"/api/planning/projects/project-0/roadmaps/roadmap-0/drafts/{draft['draft_id']}/validate",
        headers=headers,
        json={"expected_draft_version": draft["draft_version"]},
    )
    validation = validated.json()
    action_body = {
        "action": "accept",
        "expected_draft_version": validation["draft_version"],
        "idempotency_key": "route-accept-0002",
    }
    accepted = client.post(
        f"/api/planning/projects/project-0/roadmaps/roadmap-0/drafts/{draft['draft_id']}/actions",
        headers=headers,
        json=action_body,
    )
    duplicate_accept = client.post(
        f"/api/planning/projects/project-0/roadmaps/roadmap-0/drafts/{draft['draft_id']}/actions",
        headers=headers,
        json=action_body,
    )
    latest = client.get(
        "/api/planning/projects/project-0/roadmaps/roadmap-0",
        headers=_headers(),
    )

    assert created.status_code == 200
    assert duplicate_create.json() == draft
    assert project_with_draft["project"]["draft_refs"] == [
        {
            "draft_id": draft["draft_id"],
            "roadmap_id": "roadmap-0",
            "base_revision": 1,
            "base_hash": base["content_hash"],
        }
    ]
    assert validated.status_code == 200
    assert accepted.status_code == 200
    assert duplicate_accept.json() == accepted.json()
    assert accepted.json()["readback_verified"] is True
    assert latest.json()["roadmap"]["revision"] == accepted.json()["accepted_revision"]
    assert latest.json()["roadmap"]["title"] == "Route accepted"
    assert client.get(
        "/api/planning/projects/project-0",
        headers=_headers(),
    ).json()["project"]["draft_refs"] == []


def test_invalid_write_payload_and_failed_definition_validation_do_not_mutate_source(
    tmp_path: Path,
) -> None:
    repository, document = _write_repository(tmp_path)
    client, _store_instance, _router = _client(
        store=repository.snapshot_store(),
        write_service=repository,
    )
    source = repository.definition_path
    before = source.read_bytes()
    headers = _headers(csrf=True)
    path = "/api/planning/projects/project-0/roadmaps/roadmap-0/drafts"

    bad_body = client.post(path, headers=headers, json={"unexpected": True})
    nodes = json.loads(json.dumps(document["roadmaps"][0]["nodes"]))
    nodes[0]["run_id"] = "runtime-leak"
    created = client.post(
        path,
        headers=headers,
        json={
            "base_revision": 1,
            "base_hash": document["roadmaps"][0]["content_hash"],
            "idempotency_key": "route-invalid-0001",
            "changes": {"operation": "update", "set": {"nodes": nodes}},
        },
    ).json()
    validation = client.post(
        f"{path}/{created['draft_id']}/validate",
        headers=headers,
        json={"expected_draft_version": 1},
    )

    assert bad_body.status_code == 422
    assert validation.status_code == 422
    assert validation.json()["error"] == "runtime_field_forbidden"
    assert source.read_bytes() == before


def test_owner_cannot_resolve_another_owners_project() -> None:
    client, _store_instance, _router = _client()

    alice = client.get("/api/planning/projects/project-b", headers=_headers("alice"))
    bob = client.get("/api/planning/projects/project-b", headers=_headers("bob"))

    assert alice.status_code == 404
    assert bob.status_code == 200
    assert "bob" not in json.dumps(bob.json()).lower()


def test_route_cursor_is_owner_collection_limit_bound_and_tamper_evident() -> None:
    client, _store_instance, _router = _client(store=_store(projects=3))
    first = client.get("/api/planning/projects?limit=1", headers=_headers()).json()
    cursor = first["next_cursor"]

    assert client.get(
        f"/api/planning/projects?limit=1&cursor={cursor}",
        headers=_headers(),
    ).status_code == 200
    for url, headers in (
        (f"/api/planning/projects?limit=1&cursor={cursor}", _headers("bob")),
        (f"/api/planning/projects?limit=2&cursor={cursor}", _headers()),
        (
            f"/api/planning/projects/project-0/roadmaps?limit=1&cursor={cursor}",
            _headers(),
        ),
        (f"/api/planning/projects?limit=1&cursor={cursor[:-1]}0", _headers()),
    ):
        response = client.get(url, headers=headers)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_cursor"
        assert "owner" not in json.dumps(response.json()).lower()


@pytest.mark.parametrize("revision", ["0", "-1", "draft", "1.5"])
def test_revision_selector_accepts_only_latest_approved_or_positive_integer(revision: str) -> None:
    client, _store_instance, _router = _client()

    response = client.get(
        f"/api/planning/projects/project-0/roadmaps/roadmap-0?revision={revision}",
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_revision"


@pytest.mark.parametrize("limit", [0, 101])
def test_route_limit_is_bounded_by_the_declared_contract(limit: int) -> None:
    client, _store_instance, _router = _client()

    response = client.get(f"/api/planning/projects?limit={limit}", headers=_headers())

    assert response.status_code == 422


def test_expected_store_failure_returns_bounded_unavailable_origin(monkeypatch) -> None:
    client, store, _router = _client()

    def unavailable(*_args, **_kwargs):
        raise PlanningRevisionStoreError(
            "definition_source_unavailable",
            "C:/private/source payload",
            origin_state="unavailable",
        )

    monkeypatch.setattr(store, "list_projects", unavailable)
    response = client.get("/api/planning/projects", headers=_headers())
    serialized = json.dumps(response.json())

    assert response.status_code == 503
    assert response.json()["origin"]["state"] == "unavailable"
    assert response.json()["raw_private_content_visible"] is False
    assert "C:/private" not in serialized


def test_unexpected_store_failure_is_redacted(monkeypatch) -> None:
    client, store, _router = _client()

    def fail(*_args, **_kwargs):
        raise RuntimeError("Bearer private-token C:/private/path")

    monkeypatch.setattr(store, "get_project", fail)
    response = client.get(
        "/api/planning/projects/project-0",
        headers=_headers(),
    )
    serialized = json.dumps(response.json())

    assert response.status_code == 503
    assert response.json()["error"] == "planning_read_unavailable"
    assert response.json()["origin"]["state"] == "error"
    assert "private-token" not in serialized
    assert "C:/private" not in serialized


def test_invalid_store_factory_input_fails_before_router_registration() -> None:
    with pytest.raises(ValueError, match="PlanningRevisionStore"):
        setup_planning_definition_routes(object())  # type: ignore[arg-type]


def test_application_registers_the_read_only_planning_router_once() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")

    assert source.count(
        "from routes.planning_definition_routes import setup_default_planning_definition_routes"
    ) == 1
    assert source.count("app.include_router(setup_default_planning_definition_routes())") == 1
    assert "setup_default_planning_definition_routes" not in source.split(
        "# Immutable Planning Definition v2 reads", 1
    )[0]
