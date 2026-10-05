from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import routes.planning_definition_routes as planning_routes
import src.planning_revision_store as durable_module
from routes.planning_definition_routes import setup_planning_definition_routes
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    TEMPORARY_REPOSITORY_MARKER,
    TEMPORARY_REPOSITORY_SCHEMA,
    PlanningRevisionRepository,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)
from tests.test_planning_definition_projection import definition_fixture
from tests.test_planning_revision_store import _durable_payload


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
    assert source.count("_planning_definition_router = setup_default_planning_definition_routes()") == 1
    assert source.count(
        "app.state.planning_mcp_runtime = getattr(_planning_definition_router, \"_planning_mcp_runtime\", None)"
    ) == 1
    assert source.count("app.include_router(_planning_definition_router)") == 1
    assert "app.include_router(setup_default_planning_definition_routes())" not in source
    assert "setup_default_planning_definition_routes" not in source.split(
        "# Immutable Planning Definition v2 reads", 1
    )[0]


_DURABLE_PATH = "/api/planning/durable/projects/project-a/roadmaps/roadmap-a"
_BROWSER_COOKIE = "route-test-browser-session-alice"
_ORIGIN = {"Origin": "http://testserver"}
_ROUTE_ISSUER_SECRET = b"route-fixture-issuer-secret-000001"


def _default_durable_client(tmp_path, monkeypatch, *, rotated=False, username="alice"):
    import src.constants

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", base64.b64encode(
        b"rotated-route-fixture-secret-0001" if rotated else _ROUTE_ISSUER_SECRET
    ).decode("ascii"))
    monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID", "route-key-2" if rotated else "route-key-1")
    monkeypatch.setattr(src.constants, "DATA_DIR", str(tmp_path / "data"))
    captured = []

    def construct(*args, **kwargs):
        store = DurablePlanningRevisionStore(*args, **kwargs)
        captured.append(store)
        return store

    monkeypatch.setattr(planning_routes, "DurablePlanningRevisionStore", construct)
    router = planning_routes.setup_default_planning_definition_routes(definitions_root=tmp_path / "definitions")
    assert len(captured) == 1
    store = captured[0]
    manager = SimpleNamespace(
        is_configured=True,
        get_username_for_token=lambda token: {
            _BROWSER_COOKIE: "alice", "second-alice-session": "alice", "bob-session": "bob",
            "fuzzy-session": "fuzzy",
        }.get(token),
        is_admin=lambda username: username in {"alice", "bob"} or username.startswith("fuzzy"),
        active_admin_subject_identity=lambda *, username=None, subject_id=None: (
            (username, "owner_" + hashlib.sha256(
                ("fuzzy" if username.startswith("fuzzy") else username).encode()
            ).hexdigest()[:32])
            if type(username) is str and (username in {"alice", "bob"} or username.startswith("fuzzy"))
            else None
        ),
    )
    app = FastAPI()
    app.state.auth_manager = manager
    app.state.test_user = username
    app.state.test_api_token = False

    @app.middleware("http")
    async def session_state(request, call_next):
        request.state.current_user = app.state.test_user
        if app.state.test_api_token != "missing":
            request.state.api_token = app.state.test_api_token
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    client.cookies.set("odysseus_session", "fuzzy-session" if username == "fuzzy" else _BROWSER_COOKIE)
    return SimpleNamespace(client=client, store=store, app=app, manager=manager, router=router)


def _create_body(payload=None):
    return {
        "payload_b64": base64.b64encode(payload if payload is not None else _durable_payload(1, "Route first")).decode("ascii"),
        "idempotency_key": "route-create-0001",
    }


@pytest.mark.parametrize("configuration", [
    "missing-secret", "missing-key", "malformed-secret", "short-secret", "unicode-secret",
    "whitespace-secret", "noncanonical-secret", "invalid-key", "whitespace-key",
    "disabled", "malformed-auth",
])
def test_default_durable_configuration_fails_closed_without_store_or_root(tmp_path, monkeypatch, caplog, configuration):
    import src.constants

    monkeypatch.setattr(src.constants, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", base64.b64encode(_ROUTE_ISSUER_SECRET).decode("ascii"))
    monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID", "route-key")
    if configuration == "missing-secret":
        monkeypatch.delenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64")
    elif configuration == "missing-key":
        monkeypatch.delenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID")
    elif configuration in {"malformed-secret", "short-secret", "unicode-secret", "whitespace-secret", "noncanonical-secret"}:
        monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", {
            "malformed-secret": "private-invalid-secret!", "short-secret": "eA==",
            "unicode-secret": "\u00e4", "whitespace-secret": " " + base64.b64encode(_ROUTE_ISSUER_SECRET).decode("ascii"),
            "noncanonical-secret": base64.b64encode(_ROUTE_ISSUER_SECRET).decode("ascii") + "=",
        }[configuration])
    elif configuration in {"invalid-key", "whitespace-key"}:
        monkeypatch.setenv("ODYSSEUS_PLANNING_ISSUER_KEY_ID", "../key" if configuration == "invalid-key" else " key ")
    else:
        monkeypatch.setenv("AUTH_ENABLED", "false" if configuration == "disabled" else "TRUE")

    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration constructed a durable store")

    monkeypatch.setattr(planning_routes, "DurablePlanningRevisionStore", forbidden)
    router = planning_routes.setup_default_planning_definition_routes(definitions_root=tmp_path / "definitions")
    assert len(router.routes) == 9
    assert not any("/durable/" in route.path for route in router.routes)
    assert not (tmp_path / "data" / "planning" / "durable").exists()
    assert "private-invalid-secret" not in caplog.text
    assert _ROUTE_ISSUER_SECRET.decode("ascii") not in caplog.text


def test_default_durable_surface_uses_only_data_root_and_keeps_legacy_write_disabled(tmp_path, monkeypatch):
    env = _default_durable_client(tmp_path, monkeypatch)
    assert env.store.root == (tmp_path / "data" / "planning" / "durable").resolve()
    assert len(env.router.routes) == 23
    assert {"/api/planning/durable/migrations/h1r03/status",
            "/api/planning/durable/migrations/h1r03/inventory",
            "/api/planning/durable/migrations/h1r03/prepare",
            "/api/planning/durable/migrations/h1r03/accept"}.issubset({route.path for route in env.router.routes})
    assert env.client.get("/api/planning/projects").status_code == 200
    response = env.client.post("/api/planning/projects/project-a/roadmaps/roadmap-a/drafts", headers=_ORIGIN, json={})
    assert response.status_code == 403
    assert response.json()["detail"] == "Planning writes are not configured"
    assert env.client.get(_DURABLE_PATH + "/head").status_code == 404


def test_h1r03_actual_default_routes_prepare_accept_restart_and_project_durable_append(tmp_path, monkeypatch):
    from src.planning_definition_contract import compute_roadmap_content_hash
    from src.planning_roadmap_migration import FROZEN_MEMBERS, SUPPORTING_PATH, build_h1r03_migration_manifest, canonical_json_bytes

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    census_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
    source_before = {path: (Path(__file__).resolve().parents[1] / path).read_bytes()
                     for path, *_rest in FROZEN_MEMBERS}
    source_before[SUPPORTING_PATH] = (Path(__file__).resolve().parents[1] / SUPPORTING_PATH).read_bytes()

    status = env.client.get(migration + "/status")
    assert status.status_code == 200
    assert status.json()["state"] == "not_started"
    inventory = env.client.get(migration + "/inventory")
    assert inventory.status_code == 200
    assert inventory.json()["preview_chars"] == 0
    assert inventory.json()["summary"]["total_sources"] == len(manifest["structural_census"])

    prepare_body = {
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    }
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json=prepare_body)
    assert prepared.status_code == 200, prepared.text
    prepared_result = prepared.json()
    before_replay = env.store._path.read_bytes()
    replay = env.client.post(migration + "/prepare", headers=_ORIGIN, json=prepare_body)
    assert replay.status_code == 200
    assert replay.json() == prepared_result
    assert env.store._path.read_bytes() == before_replay

    accept_body = {
        "batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared_result["receipt"]["receipt_id"],
    }
    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json=accept_body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["receipt"]["after_state"] == "accepted"
    accepted_status = env.client.get(migration + "/status").json()
    assert accepted_status["state"] == "accepted"
    assert accepted_status["currentness"]["current"] is True
    assert accepted_status["currentness"]["supporting_relation_current"] is True
    assert accepted_status["currentness"]["authority_map_relation"] == "same"
    assert accepted_status["currentness"]["batch_authority_map_id"] == manifest["authority_map_id"]
    assert accepted_status["currentness"]["current_authority_map_id"] == manifest["authority_map_id"]
    accepted_inventory = env.client.get(migration + "/inventory").json()
    counts = accepted_inventory["migration_projection"]["authority_disposition_counts"]
    role_counts = {}
    for row in manifest["structural_census"]:
        role_counts[row["structural_role"]] = role_counts.get(row["structural_role"], 0) + 1
    assert counts["migrated_read_only"] == len(manifest["source_items"])
    assert counts["supporting_reference"] == len(manifest["supporting_relations"])
    assert counts["historical_evidence"] == role_counts["historical_evidence"]
    assert counts["supporting_plan_source"] == role_counts["supporting_plan_source"] + role_counts["control_plane_authority"]
    assert counts.get("current_source_of_truth", 0) == counts.get("active_import_candidate", 0) == 0

    # A fresh router/store instance reads the persisted canonical heads through
    # the existing /api/planning URLs and carries subsequent durable revisions.
    restarted = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    projects = restarted.client.get("/api/planning/projects")
    assert projects.status_code == 200
    project = next(item for item in projects.json()["items"] if item["project_id"] == "harbour-one")
    assert project["roadmap_count"] == 6
    roadmap_list = restarted.client.get("/api/planning/projects/harbour-one/roadmaps")
    assert roadmap_list.status_code == 200
    assert len(roadmap_list.json()["items"]) == 6
    roadmap_id = manifest["source_items"][0]["roadmap_id"]
    detail_path = f"/api/planning/projects/harbour-one/roadmaps/{roadmap_id}"
    detail = restarted.client.get(detail_path)
    assert detail.status_code == 200
    assert detail.json()["origin"]["source"] == "h1r03_durable_migration_accepted"

    export_path = migration + f"/exports/{roadmap_id}.json"
    exported = restarted.client.get(export_path)
    assert exported.status_code == 200
    assert exported.content == canonical_json_bytes(json.loads(exported.content))
    markdown = restarted.client.get(migration + f"/exports/{roadmap_id}.md")
    assert markdown.status_code == 200
    assert markdown.text.startswith("<!-- GENERATED READ-ONLY PLANNING VIEW; DO NOT EDIT -->")

    durable_path = f"/api/planning/durable/projects/harbour-one/roadmaps/{roadmap_id}"
    head = restarted.client.get(durable_path + "/head").json()
    document = json.loads(exported.content)
    roadmap = document["roadmaps"][0]
    roadmap["revision"] = 2
    roadmap["title"] = "H1R03 route append"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document["project"]["latest_approved_revision"][roadmap_id] = {
        "revision": 2, "content_hash": roadmap["content_hash"],
    }
    append_bytes = canonical_json_bytes(document)
    appended = restarted.client.post(durable_path + "/append", headers=_ORIGIN, json={
        "payload_b64": base64.b64encode(append_bytes).decode("ascii"),
        "expected_head": head, "idempotency_key": "h1r03-route-append-0001",
    })
    assert appended.status_code == 200, appended.text
    revision_id = appended.json()["receipt"]["new_revision_id"]
    before_approval = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    before_detail = before_approval.client.get(detail_path)
    assert before_detail.status_code == 200
    assert before_detail.json()["roadmap"]["revision"] == 1
    before_summary = next(
        item for item in before_approval.client.get(
            "/api/planning/projects/harbour-one/roadmaps"
        ).json()["items"] if item["roadmap_id"] == roadmap_id
    )
    assert before_summary["newest_revision"] == 2
    assert before_summary["latest_approved_revision"] == 1
    current_export_before_approval = before_approval.client.get(export_path)
    assert current_export_before_approval.status_code == 200
    assert json.loads(current_export_before_approval.content)["roadmaps"][0]["revision"] == 2

    approved = restarted.client.post(durable_path + f"/revisions/{revision_id}/approve", headers=_ORIGIN, json={
        "expected_head": appended.json()["head"], "idempotency_key": "h1r03-route-approve-0001",
    })
    assert approved.status_code == 200, approved.text
    after_restart = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    latest = after_restart.client.get(detail_path)
    assert latest.status_code == 200
    assert latest.json()["roadmap"]["title"] == "H1R03 route append"
    assert latest.json()["roadmap"]["revision"] == 2
    summary = next(item for item in after_restart.client.get("/api/planning/projects/harbour-one/roadmaps").json()["items"]
                   if item["roadmap_id"] == roadmap_id)
    assert summary["revision_count"] == 2 and summary["latest_approved_revision"] == 2
    current_export_after_approval = after_restart.client.get(export_path)
    assert current_export_after_approval.status_code == 200
    assert current_export_after_approval.content == current_export_before_approval.content
    assert {path: (Path(__file__).resolve().parents[1] / path).read_bytes() for path in source_before} == source_before


@pytest.mark.parametrize(("setting", "replacement"), [
    ("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", None),
    ("ODYSSEUS_PLANNING_ISSUER_KEY_ID", None),
    ("ODYSSEUS_PLANNING_ISSUER_SECRET_B64", "malformed-secret-value"),
    ("ODYSSEUS_PLANNING_ISSUER_KEY_ID", "../invalid-key"),
])
def test_accepted_h1r03_read_fails_closed_when_default_issuer_is_missing(
    tmp_path, monkeypatch, setting, replacement,
):
    from fastapi import Request
    from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    census_digest = "sha256:" + hashlib.sha256(
        canonical_json_bytes(manifest["structural_census"])
    ).hexdigest()
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json={
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json={
        "batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared.json()["receipt"]["receipt_id"],
    })
    assert prepared.status_code == accepted.status_code == 200
    assert env.store._path.is_file()

    if replacement is None:
        monkeypatch.delenv(setting)
    else:
        monkeypatch.setenv(setting, replacement)
    router = planning_routes.setup_default_planning_definition_routes(
        definitions_root=tmp_path / "definitions"
    )
    app = FastAPI()
    app.state.auth_manager = env.manager

    @app.middleware("http")
    async def session_state(request: Request, call_next):
        request.state.current_user = "fuzzy"
        request.state.api_token = False
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", "fuzzy-session")
        response = client.get("/api/planning/projects")
    assert response.status_code == 503
    assert response.json()["origin"]["state"] == "unavailable"


@pytest.mark.parametrize("phase", ["accept", "abort", "rollback"])
def test_h1r03_terminal_phase_exact_same_session_replay_is_idempotent(
    tmp_path, monkeypatch, phase,
):
    from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    census_digest = "sha256:" + hashlib.sha256(
        canonical_json_bytes(manifest["structural_census"])
    ).hexdigest()
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json={
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    assert prepared.status_code == 200
    prepared_receipt_id = prepared.json()["receipt"]["receipt_id"]
    if phase == "abort":
        body = {"batch_id": manifest["batch_id"], "prior_receipt_id": prepared_receipt_id}
        path = migration + "/abort"
        first = env.client.post(path, headers=_ORIGIN, json=body)
    else:
        accept_body = {
            "batch_id": manifest["batch_id"],
            "expected_manifest_digest": manifest["manifest_digest"],
            "expected_structural_census_digest": census_digest,
            "prior_receipt_id": prepared_receipt_id,
        }
        accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json=accept_body)
        assert accepted.status_code == 200
        if phase == "accept":
            body, path, first = accept_body, migration + "/accept", accepted
        else:
            body = {
                "batch_id": manifest["batch_id"],
                "prior_receipt_id": accepted.json()["receipt"]["receipt_id"],
            }
            path = migration + "/rollback"
            first = env.client.post(path, headers=_ORIGIN, json=body)
    assert first.status_code == 200
    before = env.store._path.read_bytes()

    if phase == "accept":
        source_path = (Path(__file__).resolve().parents[1] / manifest["source_items"][0]["path"]).resolve()
        original_read_bytes = Path.read_bytes

        def deny_source_rebuild(path):
            if path.resolve() == source_path:
                pytest.fail("completed exact accept replay reread current source authority")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", deny_source_rebuild)
    replay = env.client.post(path, headers=_ORIGIN, json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert env.store._path.read_bytes() == before

    if phase == "accept":
        rollback_body = {
            "batch_id": manifest["batch_id"],
            "prior_receipt_id": first.json()["receipt"]["receipt_id"],
        }
        rolled_back = env.client.post(migration + "/rollback", headers=_ORIGIN, json=rollback_body)
        assert rolled_back.status_code == 200
        before = env.store._path.read_bytes()
        historical_replay = env.client.post(path, headers=_ORIGIN, json=body)
        assert historical_replay.status_code == 200
        assert historical_replay.json() == first.json()
        assert env.store._path.read_bytes() == before

    changed = dict(body)
    changed["prior_receipt_id"] = "planning-migration-receipt:" + "0" * 64
    conflicting = env.client.post(path, headers=_ORIGIN, json=changed)
    assert conflicting.status_code == 409
    assert env.store._path.read_bytes() == before

    env.manager.get_username_for_token = lambda token: {
        "fuzzy-session": "fuzzy", "second-fuzzy-session": "fuzzy",
    }.get(token)
    env.client.cookies.set("odysseus_session", "second-fuzzy-session")
    other_session = env.client.post(path, headers=_ORIGIN, json=body)
    assert other_session.status_code == 409
    assert other_session.json() == {"error": "idempotency_conflict"}
    assert env.store._path.read_bytes() == before


def test_h1r03_http_accept_rechecks_sources_inside_store_transition(tmp_path, monkeypatch):
    from src.planning_roadmap_migration import (
        FROZEN_MEMBERS, build_h1r03_migration_manifest, canonical_json_bytes,
    )

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    census_digest = "sha256:" + hashlib.sha256(
        canonical_json_bytes(manifest["structural_census"])
    ).hexdigest()
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json={
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    assert prepared.status_code == 200
    before = env.store._path.read_bytes()

    target = (Path(__file__).resolve().parents[1] / FROZEN_MEMBERS[0][0]).resolve()
    original_read_bytes = Path.read_bytes
    original_accept = env.store.accept_migration
    drifted = False

    def projected_read_bytes(path):
        raw = original_read_bytes(path)
        return raw + b"\n" if drifted and path.resolve() == target else raw

    def drift_at_store_boundary(*args, **kwargs):
        nonlocal drifted
        drifted = True
        return original_accept(*args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", projected_read_bytes)
    monkeypatch.setattr(env.store, "accept_migration", drift_at_store_boundary)
    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json={
        "batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared.json()["receipt"]["receipt_id"],
    })
    assert accepted.status_code == 409
    assert accepted.json() == {"error": "stale_authority"}
    assert drifted is True
    assert env.store._path.read_bytes() == before


@pytest.mark.parametrize("drift", ["primary", "supporting_relation", "authority"])
def test_h1r03_status_separates_accepted_source_and_relation_currentness(tmp_path, monkeypatch, drift):
    from src.planning_roadmap_migration import (
        FROZEN_MEMBERS, SUPPORTING_PATH, build_h1r03_migration_manifest, canonical_json_bytes,
    )

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    census_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json={
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    assert prepared.status_code == 200, prepared.text
    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json={
        "batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared.json()["receipt"]["receipt_id"],
    })
    assert accepted.status_code == 200, accepted.text

    repo_root = Path(__file__).resolve().parents[1]
    relative = (
        SUPPORTING_PATH if drift == "supporting_relation"
        else "docs/plans/harbour-one-product-roadmap.json" if drift == "authority"
        else FROZEN_MEMBERS[0][0]
    )
    target = (repo_root / relative).resolve()
    real_read_bytes = Path.read_bytes

    def projected_source_drift(path):
        raw = real_read_bytes(path)
        if path.resolve() != target:
            return raw
        document = json.loads(raw)
        field = "test_observation" if drift == "supporting_relation" else "title"
        document[field] = str(document.get(field, "unchanged")) + ";observed-drift"
        return json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

    monkeypatch.setattr(Path, "read_bytes", projected_source_drift)
    status = env.client.get(migration + "/status")
    assert status.status_code == 200
    currentness = status.json()["currentness"]
    assert currentness["structural_current"] is True
    if drift == "primary":
        assert currentness["primary_sources_current"] is False
        assert currentness["current"] is False
        assert currentness["supporting_relation_current"] is True
    elif drift == "authority":
        assert currentness["primary_sources_current"] is True
        assert currentness["current"] is False
        assert currentness["authority_current"] is False
        assert currentness["authority_map_relation"] == "unknown"
    else:
        assert currentness["primary_sources_current"] is True
        assert currentness["current"] is True
        assert currentness["supporting_relation_current"] is False
    projected = env.client.get("/api/planning/projects/harbour-one/roadmaps")
    assert projected.status_code == 200
    assert len(projected.json()["items"]) == 6


def test_historical_accepted_batch_reports_successor_currentness_without_rewriting_heads(tmp_path, monkeypatch):
    import src.planning_roadmap_migration as migration_module
    from src.planning_roadmap_migration import (
        REPO_ONLY_AUTHORITY_MAP_ID, HISTORICAL_AUTHORITY_PINS, MIGRATION_MANIFEST_SCHEMA_V1,
        build_h1r03_migration_manifest, canonical_json_bytes,
    )

    manifest = deepcopy(build_h1r03_migration_manifest(Path(__file__).resolve().parents[1]))
    manifest["schema"] = MIGRATION_MANIFEST_SCHEMA_V1
    manifest.pop("authority_map_id")
    manifest["authority_pins"] = deepcopy(HISTORICAL_AUTHORITY_PINS)
    identity = durable_module._migration_manifest_identity(manifest)
    manifest["manifest_digest"] = durable_module._migration_digest(identity)
    manifest["batch_id"] = "planning-migration-batch:" + manifest["manifest_digest"][7:]
    monkeypatch.setattr(migration_module, "build_h1r03_migration_manifest", lambda _root: deepcopy(manifest))
    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    census_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json={
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    assert prepared.status_code == 200, prepared.text
    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json={
        "batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared.json()["receipt"]["receipt_id"],
    })
    assert accepted.status_code == 200, accepted.text
    before = env.store._path.read_bytes()
    status = env.client.get(migration + "/status").json()
    currentness = status["currentness"]
    assert currentness["batch_authority_map_id"] == "h1r03-historical-v1"
    assert currentness["current_authority_map_id"] == REPO_ONLY_AUTHORITY_MAP_ID
    assert currentness["authority_map_relation"] == "successor"
    assert currentness["authority_current"] is True
    assert currentness["current"] is True
    assert env.store._path.read_bytes() == before


@pytest.mark.parametrize("manifest_kind", ("v1", "v2", "v3"))
def test_legacy_route_exports_and_full_state_replays_survive_repo_only_v4(
    tmp_path, monkeypatch, manifest_kind,
):
    import src.planning_roadmap_migration as migration_module
    from src.planning_roadmap_migration import (
        REPO_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID, HISTORICAL_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID,
        build_h1r03_migration_manifest, canonical_json_bytes,
    )
    from tests.test_planning_revision_store import (
        _docs_only_v3_manifest, _historical_v1_manifest, _successor_v2_manifest,
    )

    repo_root = Path(__file__).resolve().parents[1]
    current_v4 = build_h1r03_migration_manifest(repo_root)
    assert current_v4["authority_map_id"] == REPO_ONLY_AUTHORITY_MAP_ID
    legacy = {
        "v1": _historical_v1_manifest,
        "v2": _successor_v2_manifest,
        "v3": _docs_only_v3_manifest,
    }[manifest_kind]()
    manifest_holder = {"value": legacy}
    monkeypatch.setattr(
        migration_module,
        "build_h1r03_migration_manifest",
        lambda _root: deepcopy(manifest_holder["value"]),
    )

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    census_digest = "sha256:" + hashlib.sha256(
        canonical_json_bytes(legacy["structural_census"])
    ).hexdigest()
    prepare_body = {
        "expected_batch_id": legacy["batch_id"],
        "expected_manifest_digest": legacy["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    }
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json=prepare_body)
    assert prepared.status_code == 200, prepared.text
    prepared_bytes = env.store._path.read_bytes()
    prepared_replay = env.client.post(migration + "/prepare", headers=_ORIGIN, json=prepare_body)
    assert prepared_replay.status_code == 200
    assert prepared_replay.content == prepared.content
    assert env.store._path.read_bytes() == prepared_bytes
    prepared_receipt_id = prepared.json()["receipt"]["receipt_id"]
    prepared_image = json.loads(prepared_bytes)
    assert prepared_image["migration_batches"][legacy["batch_id"]]["state"] == "prepared"

    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json={
        "batch_id": legacy["batch_id"],
        "expected_manifest_digest": legacy["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared_receipt_id,
    })
    assert accepted.status_code == 200, accepted.text
    state_after_accept = env.store._path.read_bytes()
    accepted_image = json.loads(state_after_accept)
    accepted_batch = accepted_image["migration_batches"][legacy["batch_id"]]
    accepted_receipt_id = accepted_batch["accepted_receipt_id"]
    accepted_receipt = accepted_image["receipts"][accepted_receipt_id]
    revision_ids = list(accepted_receipt["new_revision_ids"])
    roadmap_ids = [item["roadmap_id"] for item in legacy["source_items"]]
    assert len(roadmap_ids) == len(revision_ids) == 6

    inspection_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-planning-default",
        key_id="route-key-1",
        secret=_ROUTE_ISSUER_SECRET,
    )
    inspection_principal = inspection_issuer.issue(
        subject_id="fuzzy", owner_id="fuzzy",
        auth_context_digest="sha256:" + hashlib.sha256(b"legacy route readback").hexdigest(),
        ttl_seconds=900,
        nonce=f"legacy-{manifest_kind}-route-readback",
    )

    def store_projection(selected):
        raw = selected.store._path.read_bytes()
        image = json.loads(raw)
        batch = selected.store.get_migration_batch(
            inspection_principal, owner_id="fuzzy", batch_id=legacy["batch_id"],
        )
        accepted_view = selected.store.read_accepted_migration_records(
            inspection_principal, owner_id="fuzzy", project_id="harbour-one",
        )
        receipt_ids = (
            batch["prepared_receipt_id"], batch["accepted_receipt_id"],
        )
        heads = {}
        revisions = {}
        payloads = {}
        for roadmap_id, revision_id in zip(roadmap_ids, revision_ids):
            key = f"fuzzy:harbour-one:{roadmap_id}"
            heads[key] = deepcopy(image["heads"][key])
            revisions[revision_id] = deepcopy(image["revisions"][revision_id])
            head = selected.store.get_head(
                inspection_principal, owner_id="fuzzy", project_id="harbour-one",
                roadmap_id=roadmap_id,
            )
            assert head.current_revision_id == revision_id
            payloads[roadmap_id] = selected.store.read_payload(
                inspection_principal, owner_id="fuzzy", revision_id=revision_id,
            )
        receipts = {
            receipt_id: selected.store.read_migration_receipt(
                inspection_principal, owner_id="fuzzy", receipt_id=receipt_id,
            )
            for receipt_id in receipt_ids
        }
        return {
            "raw_state": raw,
            "batch": batch,
            "accepted_records_and_head_bindings": accepted_view,
            "receipts": receipts,
            "heads": heads,
            "head_fences": {key: image["head_fences"][key] for key in heads},
            "history_revisions": revisions,
            "payloads": payloads,
        }

    accepted_projection = store_projection(env)
    assert accepted_projection["batch"]["state"] == "accepted"
    assert set(accepted_projection["payloads"]) == set(roadmap_ids)
    for item in legacy["source_items"]:
        assert accepted_projection["payloads"][item["roadmap_id"]] == (
            legacy["normalized_payloads"][item["roadmap_id"]].encode("utf-8")
        )

    manifest_holder["value"] = current_v4
    status = env.client.get(migration + "/status")
    assert status.status_code == 200
    currentness = status.json()["currentness"]
    expected_map = {
        "v1": HISTORICAL_AUTHORITY_MAP_ID,
        "v2": SUCCESSOR_AUTHORITY_MAP_ID,
        "v3": DOCS_ONLY_AUTHORITY_MAP_ID,
    }[manifest_kind]
    assert currentness["batch_authority_map_id"] == expected_map
    assert currentness["current_authority_map_id"] == REPO_ONLY_AUTHORITY_MAP_ID
    assert currentness["authority_map_relation"] == "successor"
    expected_structural_current = legacy["structural_census"] == current_v4["structural_census"]
    assert currentness["structural_current"] is expected_structural_current
    assert currentness["primary_sources_current"] is True
    assert currentness["authority_current"] is True
    assert currentness["current"] is expected_structural_current

    def route_exports(client):
        output = {}
        for item in legacy["source_items"]:
            roadmap_id = item["roadmap_id"]
            expected_payload = legacy["normalized_payloads"][roadmap_id].encode("utf-8")
            exported = client.get(migration + f"/exports/{roadmap_id}.json")
            assert exported.status_code == 200
            assert exported.content == expected_payload
            document = json.loads(exported.content)
            roadmap = document["roadmaps"][0]

            markdown = client.get(migration + f"/exports/{roadmap_id}.md")
            assert markdown.status_code == 200
            assert markdown.text.startswith("<!-- GENERATED READ-ONLY PLANNING VIEW; DO NOT EDIT -->\n")
            assert f"# {roadmap['title']}" in markdown.text
            assert roadmap["objective"] in markdown.text
            assert "## Nodes" in markdown.text
            assert "## Source references" in markdown.text
            assert all(f"`{node['node_id']}`" in markdown.text for node in roadmap["nodes"])
            assert all(f"`{source_ref}`" in markdown.text for source_ref in roadmap["source_refs"])
            output[roadmap_id] = (exported.content, markdown.content)
        return output

    exports_before_replay = route_exports(env.client)
    assert env.store._path.read_bytes() == state_after_accept
    accept_body = {
        "batch_id": legacy["batch_id"],
        "expected_manifest_digest": legacy["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared_receipt_id,
    }
    accepted_replay = env.client.post(migration + "/accept", headers=_ORIGIN, json=accept_body)
    assert accepted_replay.status_code == 200
    assert accepted_replay.content == accepted.content
    assert env.store._path.read_bytes() == state_after_accept
    assert route_exports(env.client) == exports_before_replay

    reopened = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    assert reopened.store._path.read_bytes() == state_after_accept
    assert store_projection(reopened) == accepted_projection
    assert route_exports(reopened.client) == exports_before_replay
    assert reopened.store._path.read_bytes() == state_after_accept

    rollback = reopened.client.post(migration + "/rollback", headers=_ORIGIN, json={
        "batch_id": legacy["batch_id"], "prior_receipt_id": accepted_receipt_id,
    })
    assert rollback.status_code == 200, rollback.text
    rolled_back_bytes = reopened.store._path.read_bytes()
    rolled_back_image = json.loads(rolled_back_bytes)
    rolled_back_batch = rolled_back_image["migration_batches"][legacy["batch_id"]]
    assert rolled_back_batch["state"] == "rolled_back"
    assert rolled_back_image["heads"] == {}
    assert {
        revision_id: rolled_back_image["revisions"][revision_id]
        for revision_id in revision_ids
    } == accepted_projection["history_revisions"]
    for roadmap_id, revision_id in zip(roadmap_ids, revision_ids):
        assert reopened.store.read_payload(
            inspection_principal, owner_id="fuzzy", revision_id=revision_id,
        ) == accepted_projection["payloads"][roadmap_id]

    unavailable_exports = {}
    for roadmap_id in roadmap_ids:
        for extension in ("json", "md"):
            response = reopened.client.get(migration + f"/exports/{roadmap_id}.{extension}")
            assert response.status_code == 404
            unavailable_exports[(roadmap_id, extension)] = response.content
    assert reopened.store._path.read_bytes() == rolled_back_bytes

    rolled_back_reopened = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    assert rolled_back_reopened.store._path.read_bytes() == rolled_back_bytes
    rollback_replay = rolled_back_reopened.client.post(
        migration + "/rollback", headers=_ORIGIN,
        json={"batch_id": legacy["batch_id"], "prior_receipt_id": accepted_receipt_id},
    )
    assert rollback_replay.status_code == 200
    assert rollback_replay.content == rollback.content
    assert rolled_back_reopened.store._path.read_bytes() == rolled_back_bytes
    reopened_status = rolled_back_reopened.client.get(migration + "/status")
    assert reopened_status.status_code == 200
    assert reopened_status.json()["state"] == "rolled_back"
    assert reopened_status.json()["currentness"]["current_authority_map_id"] == REPO_ONLY_AUTHORITY_MAP_ID
    assert json.loads(rolled_back_reopened.store._path.read_bytes())["migration_batches"][legacy["batch_id"]] == rolled_back_batch
    for roadmap_id in roadmap_ids:
        for extension in ("json", "md"):
            response = rolled_back_reopened.client.get(migration + f"/exports/{roadmap_id}.{extension}")
            assert response.status_code == 404
            assert response.content == unavailable_exports[(roadmap_id, extension)]
    assert rolled_back_reopened.store._path.read_bytes() == rolled_back_bytes


def test_h1r03_routes_deny_other_owner_and_cross_origin_before_issuer_or_store(tmp_path, monkeypatch):
    other = _default_durable_client(tmp_path, monkeypatch, username="bob")

    def forbidden(*args, **kwargs):
        pytest.fail("H1R03 denied request reached issuer or store")

    monkeypatch.setattr(AuthenticatedPrincipalIssuer, "issue", forbidden)
    monkeypatch.setattr(other.store, "list_migration_batches", forbidden)
    response = other.client.get("/api/planning/durable/migrations/h1r03/status")
    assert response.status_code == 403
    assert response.json() == {"error": "unauthenticated"}
    monkeypatch.undo()
    fuzzy = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    monkeypatch.setattr(AuthenticatedPrincipalIssuer, "issue", forbidden)
    monkeypatch.setattr(fuzzy.store, "prepare_migration", forbidden)
    response = fuzzy.client.post("/api/planning/durable/migrations/h1r03/prepare", json={})
    assert response.status_code == 403
    assert response.json() == {"error": "unauthenticated"}


@pytest.mark.parametrize("corruption", ["missing_head", "corrupt_payload", "manifest_digest"])
def test_h1r03_accepted_projection_corruption_fails_closed_without_legacy_fallback(
    tmp_path, monkeypatch, corruption,
):
    from src.planning_roadmap_migration import build_h1r03_migration_manifest, canonical_json_bytes

    env = _default_durable_client(tmp_path, monkeypatch, username="fuzzy")
    migration = "/api/planning/durable/migrations/h1r03"
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    census_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(manifest["structural_census"])).hexdigest()
    prepared = env.client.post(migration + "/prepare", headers=_ORIGIN, json={
        "expected_batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
    })
    assert prepared.status_code == 200, prepared.text
    accepted = env.client.post(migration + "/accept", headers=_ORIGIN, json={
        "batch_id": manifest["batch_id"],
        "expected_manifest_digest": manifest["manifest_digest"],
        "expected_structural_census_digest": census_digest,
        "prior_receipt_id": prepared.json()["receipt"]["receipt_id"],
    })
    assert accepted.status_code == 200, accepted.text

    image = json.loads(env.store._path.read_bytes())
    if corruption == "missing_head":
        key = next(key for key in image["heads"] if key.startswith("fuzzy:harbour-one:"))
        del image["heads"][key]
    elif corruption == "corrupt_payload":
        revision_id = next(
            revision_id for revision_id, revision in image["revisions"].items()
            if revision["owner_id"] == "fuzzy" and revision["project_id"] == "harbour-one"
        )
        image["revisions"][revision_id]["payload_b64"] = base64.b64encode(b"{").decode("ascii")
    else:
        batch = next(batch for batch in image["migration_batches"].values() if batch["state"] == "accepted")
        batch["manifest_digest"] = "sha256:" + "0" * 64
    env.store._path.write_bytes(json.dumps(image, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    # The accepted durable batch exists, so a broken head/payload must produce
    # a bounded service error instead of silently reviving legacy content.
    response = env.client.get("/api/planning/projects/harbour-one/roadmaps")
    assert response.status_code == 503
    assert response.json()["error"] == "planning_read_unavailable"

@pytest.mark.parametrize("access", ["head", "revision", "create", "append", "approve", "revert"])
@pytest.mark.parametrize("failure", [
    "disabled", "malformed-auth", "bearer", "empty-authorization", "internal-header", "empty-internal-header",
    "api-token", "missing-api-state", "numeric-api-state", "no-manager", "bad-manager",
    "no-cookie", "invalid-cookie", "state-mismatch", "missing-state-user", "localhost-bypass",
    "nonadmin", "truthy-admin", "invalid-username", "manager-error",
])
def test_every_durable_route_rejects_nonbrowser_authority_before_issuer_or_store(tmp_path, monkeypatch, access, failure):
    env = _default_durable_client(tmp_path, monkeypatch)
    headers = dict(_ORIGIN)
    if failure in {"disabled", "malformed-auth"}:
        monkeypatch.setenv("AUTH_ENABLED", "false" if failure == "disabled" else "TRUE")
    elif failure in {"bearer", "empty-authorization"}:
        headers["Authorization"] = "Bearer ignored" if failure == "bearer" else ""
    elif failure in {"internal-header", "empty-internal-header"}:
        headers["X-Odysseus-Internal-Token"] = "ignored" if failure == "internal-header" else ""
    elif failure in {"api-token", "missing-api-state", "numeric-api-state"}:
        env.app.state.test_api_token = {"api-token": True, "missing-api-state": "missing", "numeric-api-state": 0}[failure]
    elif failure in {"no-manager", "bad-manager"}:
        env.app.state.auth_manager = None if failure == "no-manager" else object()
    elif failure in {"no-cookie", "invalid-cookie"}:
        env.client.cookies.clear()
        if failure == "invalid-cookie":
            env.client.cookies.set("odysseus_session", "invalid")
    elif failure in {"state-mismatch", "missing-state-user", "localhost-bypass"}:
        env.app.state.test_user = {"state-mismatch": "bob", "missing-state-user": None, "localhost-bypass": "internal-tool"}[failure]
    elif failure in {"nonadmin", "truthy-admin"}:
        env.manager.is_admin = lambda username: False if failure == "nonadmin" else 1
    elif failure == "invalid-username":
        env.manager.get_username_for_token = lambda token: " alice "
        env.app.state.test_user = " alice "
    else:
        def failed_lookup(_token):
            raise RuntimeError("private lookup detail")
        env.manager.get_username_for_token = failed_lookup

    def forbidden(*args, **kwargs):
        pytest.fail("rejected request reached issuer or store")

    monkeypatch.setattr(AuthenticatedPrincipalIssuer, "issue", forbidden)
    for method in ("get_head", "read_revision", "read_payload", "create_project", "append_revision", "approve_revision", "revert_head"):
        monkeypatch.setattr(env.store, method, forbidden)
    suffix = {"head": "/head", "revision": "/revisions/missing", "approve": "/revisions/missing/approve"}.get(access, "/" + access)
    response = (env.client.get(_DURABLE_PATH + suffix, headers=headers) if access in {"head", "revision"}
                else env.client.post(_DURABLE_PATH + suffix, headers=headers, json={}))
    assert response.status_code == 403
    assert response.json() == {"error": "unauthenticated"}


@pytest.mark.parametrize("access", ["create", "append", "approve", "revert"])
@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"}])
def test_durable_mutations_require_same_origin_before_issuance(tmp_path, monkeypatch, access, headers):
    env = _default_durable_client(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("CSRF failure reached issuer")

    monkeypatch.setattr(AuthenticatedPrincipalIssuer, "issue", forbidden)
    suffix = "/revisions/missing/approve" if access == "approve" else "/" + access
    response = env.client.post(_DURABLE_PATH + suffix, headers=headers, json={})
    assert response.status_code == 403
    assert response.json() == {"error": "unauthenticated"}


def test_default_durable_commands_readback_replay_restart_rotation_and_session_conflict(tmp_path, monkeypatch):
    env = _default_durable_client(tmp_path, monkeypatch)
    issued = []
    original_issue = AuthenticatedPrincipalIssuer.issue

    def observe_issue(self, **kwargs):
        principal = original_issue(self, **kwargs)
        assert kwargs["ttl_seconds"] == 300
        assert "nonce" not in kwargs
        assert kwargs["owner_id"] == "alice"
        assert kwargs["subject_id"] == env.manager.active_admin_subject_identity(username="alice")[1]
        assert kwargs["subject_id"] != kwargs["owner_id"]
        issued.append(principal)
        return principal

    monkeypatch.setattr(AuthenticatedPrincipalIssuer, "issue", observe_issue)
    # Whitespace, Unicode and ordering survive the HTTP boundary byte-for-byte.
    payload = b"\n  " + json.dumps(json.loads(_durable_payload(1, "Route \u00fcber")), indent=3, ensure_ascii=False).encode("utf-8") + b"\n"
    body = _create_body(payload)
    response = env.client.post(_DURABLE_PATH + "/create", headers={**_ORIGIN, "X-Test-Owner": "bob", "X-Odysseus-User": "bob"}, json=body)
    assert response.status_code == 200
    created = response.json()
    assert created["head"]["owner_id"] == "alice"
    assert set(created["head"]) == planning_routes._DURABLE_HEAD_FIELDS
    assert created["head"] == created["receipt"]["after_head"]
    revision_id = created["receipt"]["new_revision_id"]
    revision = env.client.get(_DURABLE_PATH + "/revisions/" + revision_id)
    assert revision.status_code == 200
    assert base64.b64decode(revision.json()["payload_b64"]) == payload
    assert "principal_ref" not in revision.json()["revision"]
    assert env.client.get(_DURABLE_PATH + "/head").json() == created["head"]
    assert env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=body).json() == created

    approved_body = {"expected_head": created["head"], "idempotency_key": "route-approve-0001"}
    approve_path = _DURABLE_PATH + "/revisions/" + revision_id + "/approve"
    approved_response = env.client.post(approve_path, headers=_ORIGIN, json=approved_body)
    assert approved_response.status_code == 200
    approved = approved_response.json()
    assert approved["head"]["approved_revision_id"] == revision_id
    appended_body = {"payload_b64": base64.b64encode(_durable_payload(2, "Route second")).decode("ascii"),
                     "expected_head": approved["head"], "idempotency_key": "route-append-0001"}
    appended_response = env.client.post(_DURABLE_PATH + "/append", headers=_ORIGIN, json=appended_body)
    assert appended_response.status_code == 200
    appended = appended_response.json()
    reverted_body = {"ancestor_revision_id": revision_id, "expected_head": appended["head"], "idempotency_key": "route-revert-0001"}
    reverted_response = env.client.post(_DURABLE_PATH + "/revert", headers=_ORIGIN, json=reverted_body)
    assert reverted_response.status_code == 200
    reverted = reverted_response.json()
    assert reverted["head"]["generation"] == 4
    assert reverted["receipt"]["restored_revision_id"] == revision_id
    restored = env.client.get(_DURABLE_PATH + "/revisions/" + reverted["receipt"]["new_revision_id"]).json()
    assert json.loads(base64.b64decode(restored["payload_b64"]))["roadmaps"][0]["title"] == "Route \u00fcber"
    assert env.client.get(_DURABLE_PATH + "/head").json() == reverted["head"]
    before = env.store._path.read_bytes()

    env = _default_durable_client(tmp_path, monkeypatch, rotated=True)
    for path, request_body, receipt in [
        (_DURABLE_PATH + "/create", body, created), (approve_path, approved_body, approved),
        (_DURABLE_PATH + "/append", appended_body, appended), (_DURABLE_PATH + "/revert", reverted_body, reverted),
    ]:
        replay = env.client.post(path, headers=_ORIGIN, json=request_body)
        assert replay.status_code == 200
        assert replay.json() == receipt
    assert len({principal.nonce for principal in issued}) == len(issued)
    assert len({principal.auth_context_digest for principal in issued}) == 1
    assert len({principal.key_id for principal in issued}) == 2
    assert env.store._path.read_bytes() == before
    changed_head = {**appended_body, "expected_head": {**appended_body["expected_head"], "generation": 100}}
    changed_key = {**body, "idempotency_key": "different-create-key"}
    for path, changed_body, code in [
        (_DURABLE_PATH + "/append", changed_head, "idempotency_conflict"),
        (_DURABLE_PATH + "/create", changed_key, "stale_authority"),
    ]:
        rejected = env.client.post(path, headers=_ORIGIN, json=changed_body)
        assert rejected.status_code == 409
        assert rejected.json() == {"error": code}
    assert env.store._path.read_bytes() == before
    env.client.cookies.set("odysseus_session", "second-alice-session")
    for path, request_body in [(_DURABLE_PATH + "/create", body), (approve_path, approved_body),
                               (_DURABLE_PATH + "/append", appended_body), (_DURABLE_PATH + "/revert", reverted_body)]:
        conflict = env.client.post(path, headers=_ORIGIN, json=request_body)
        assert conflict.status_code == 409
        assert conflict.json() == {"error": "idempotency_conflict"}
    assert env.store._path.read_bytes() == before
    public_and_persisted = response.text + revision.text + before.decode("utf-8")
    assert _BROWSER_COOKIE not in public_and_persisted
    assert _ROUTE_ISSUER_SECRET.decode("ascii") not in public_and_persisted
    assert "hmac-sha256:" not in public_and_persisted


@pytest.mark.parametrize("hostile", [
    b"[]", b"null", b"{", b"\xff", b'{"payload_b64":"e30=","payload_b64":"e30=","idempotency_key":"valid-key"}',
    b'{"payload_b64":NaN,"idempotency_key":"valid-key"}',
    b'{"payload_b64":"e30=","idempotency_key":"valid-key","owner_id":"bob"}',
    b'{"payload_b64":"e30="}',
    b'{"payload_b64":' + b"[" * 1100 + b"0" + b"]" * 1100 + b',"idempotency_key":"valid-key"}',
])
def test_durable_body_rejects_malformed_duplicate_missing_extra_and_deep_json_without_mutation(tmp_path, monkeypatch, hostile):
    env = _default_durable_client(tmp_path, monkeypatch)
    response = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, content=hostile)
    assert response.status_code == 422
    assert response.json() == {"error": "validation_failed"}
    assert not env.store._path.exists()


@pytest.mark.parametrize("access", ["append", "approve", "revert"])
@pytest.mark.parametrize("malformation", ["extra", "missing", "duplicate", "duplicate-head", "head-list"])
def test_durable_command_fields_are_exact_including_nested_head(tmp_path, monkeypatch, access, malformation):
    env = _default_durable_client(tmp_path, monkeypatch)
    created = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body()).json()
    body = {"expected_head": created["head"], "idempotency_key": "closed-command-0001"}
    if access == "append":
        body["payload_b64"] = base64.b64encode(_durable_payload(2, "Closed append")).decode("ascii")
    elif access == "revert":
        body["ancestor_revision_id"] = created["receipt"]["new_revision_id"]
    if malformation == "extra":
        body["owner_id"] = "bob"
    elif malformation == "missing":
        del body["idempotency_key"]
    elif malformation == "head-list":
        body["expected_head"] = []
    raw = json.dumps(body)
    if malformation == "duplicate":
        raw = raw[:-1] + ',"idempotency_key":"closed-command-0001"}'
    elif malformation == "duplicate-head":
        raw = raw.replace('"generation": 1', '"generation": 1, "generation": 1')
    suffix = "/revisions/" + created["receipt"]["new_revision_id"] + "/approve" if access == "approve" else "/" + access
    before = env.store._path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("malformed command reached store")

    monkeypatch.setattr(env.store, {"append": "append_revision", "approve": "approve_revision", "revert": "revert_head"}[access], forbidden)
    response = env.client.post(_DURABLE_PATH + suffix, headers=_ORIGIN, content=raw)
    assert response.status_code == 422
    assert response.json() == {"error": "validation_failed"}
    assert env.store._path.read_bytes() == before


@pytest.mark.parametrize("payload", [b'{"schema_version":2,"schema_version":2}', b'{"value":NaN}', b"\xff", b"[]"])
def test_durable_payload_reaches_store_parser_as_exact_bytes(tmp_path, monkeypatch, payload):
    env = _default_durable_client(tmp_path, monkeypatch)
    seen = []
    original = env.store.create_project

    def record(principal, **kwargs):
        seen.append(kwargs["payload_bytes"])
        return original(principal, **kwargs)

    monkeypatch.setattr(env.store, "create_project", record)
    response = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body(payload))
    assert response.status_code == 422
    assert response.json() == {"error": "validation_failed"}
    assert seen == [payload]
    assert not env.store._path.exists()


@pytest.mark.parametrize("encoded", [None, 1, [], {}, "e30=\n", "e30", "e31=", "e30===", "____", "\u00e4"])
def test_durable_payload_requires_strict_standard_base64(tmp_path, monkeypatch, encoded):
    env = _default_durable_client(tmp_path, monkeypatch)
    body = _create_body()
    body["payload_b64"] = encoded
    response = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=body)
    assert response.status_code == 422
    assert response.json() == {"error": "validation_failed"}
    assert not env.store._path.exists()


@pytest.mark.parametrize("excess", [0, 1, 100_000])
def test_durable_payload_byte_limit_accepts_exactly_one_mib(tmp_path, monkeypatch, excess):
    env = _default_durable_client(tmp_path, monkeypatch)
    payload = _durable_payload(1, "Byte boundary")
    payload += b" " * (1024 * 1024 + excess - len(payload))
    response = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body(payload))
    if excess:
        assert response.status_code == 422
        assert not env.store._path.exists()
    else:
        assert response.status_code == 200
        revision_id = response.json()["receipt"]["new_revision_id"]
        readback = env.client.get(_DURABLE_PATH + "/revisions/" + revision_id)
        assert base64.b64decode(readback.json()["payload_b64"]) == payload


@pytest.mark.parametrize("field,value", [
    ("generation", True), ("generation", 0), ("generation", "1"), ("generation", 2**63),
    ("current_content_sha256", "invalid"), ("current_revision_id", {}), ("approved_revision_id", []),
    ("owner_id", "bob"), ("project_id", "project-b"), ("roadmap_id", "roadmap-b"),
    ("extra", None), ("missing", None),
])
def test_durable_hostile_expected_head_never_moves_state(tmp_path, monkeypatch, field, value):
    env = _default_durable_client(tmp_path, monkeypatch)
    created = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body()).json()
    head = dict(created["head"])
    if field == "missing":
        del head["generation"]
    else:
        head[field] = value
    before = env.store._path.read_bytes()
    response = env.client.post(_DURABLE_PATH + "/append", headers=_ORIGIN, json={
        "payload_b64": base64.b64encode(_durable_payload(2, "Never appended")).decode("ascii"),
        "expected_head": head, "idempotency_key": "hostile-head-0001",
    })
    assert response.status_code == (409 if field in {"owner_id", "project_id", "roadmap_id"} else 422)
    assert env.store._path.read_bytes() == before


def test_durable_stale_head_changed_payload_and_byte_changes_conflict_without_mutation(tmp_path, monkeypatch):
    env = _default_durable_client(tmp_path, monkeypatch)
    body = _create_body()
    created = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=body).json()
    approved = env.client.post(_DURABLE_PATH + "/revisions/" + created["receipt"]["new_revision_id"] + "/approve",
                               headers=_ORIGIN, json={"expected_head": created["head"], "idempotency_key": "approve-current-0001"}).json()
    before = env.store._path.read_bytes()
    for payload in (_durable_payload(1, "Changed title"), base64.b64decode(body["payload_b64"]) + b"\n"):
        response = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body(payload))
        assert response.status_code == 409
        assert response.json() == {"error": "idempotency_conflict"}
    response = env.client.post(_DURABLE_PATH + "/append", headers=_ORIGIN, json={
        "payload_b64": base64.b64encode(_durable_payload(2, "Stale append")).decode("ascii"),
        "expected_head": created["head"], "idempotency_key": "stale-append-0001",
    })
    assert response.status_code == 409
    assert response.json() == {"error": "stale_authority"}
    assert env.client.get(_DURABLE_PATH + "/head").json() == approved["head"]
    assert env.store._path.read_bytes() == before


def test_durable_revision_read_compares_full_route_identity_and_owner(tmp_path, monkeypatch):
    env = _default_durable_client(tmp_path, monkeypatch)
    created = env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body()).json()
    suffix = "/revisions/" + created["receipt"]["new_revision_id"]
    for wrong in (_DURABLE_PATH.replace("project-a", "project-b"), _DURABLE_PATH.replace("roadmap-a", "roadmap-b")):
        response = env.client.get(wrong + suffix)
        assert response.status_code == 404
        assert response.json() == {"error": "not_found"}
    env.client.cookies.set("odysseus_session", "bob-session")
    env.app.state.test_user = "bob"
    for suffix in (suffix, "/head"):
        assert env.client.get(_DURABLE_PATH + suffix).status_code == 404


@pytest.mark.parametrize("code,status", [
    ("unauthenticated", 403), ("owner_scope_denied", 403), ("not_found", 404),
    ("stale_authority", 409), ("idempotency_conflict", 409), ("validation_failed", 422),
    ("migration_required", 409), ("unknown-private-detail", 409), ("exception", 409),
])
def test_durable_error_map_is_bounded_for_reads_and_commands(tmp_path, monkeypatch, code, status):
    env = _default_durable_client(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        if code == "exception":
            raise RuntimeError("private-session C:/private/path")
        raise DurablePlanningStoreError(code)

    monkeypatch.setattr(env.store, "get_head", fail)
    monkeypatch.setattr(env.store, "create_project", fail)
    for response in (env.client.get(_DURABLE_PATH + "/head"),
                     env.client.post(_DURABLE_PATH + "/create", headers=_ORIGIN, json=_create_body())):
        assert response.status_code == status
        assert response.json() == {"error": "unexpected" if code in {"exception", "unknown-private-detail"} else code}
