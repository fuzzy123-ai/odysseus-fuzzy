from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from routes.project_versioning_routes import (
    _same_origin_csrf_gate,
    setup_default_project_versioning_routes,
    setup_project_versioning_routes,
)
from src.project_forge_outbox import ProjectForgeOutbox
from src.project_forge_policy import ProjectForgePolicy, ProjectForgePolicyStore
from src.project_version_store import ProjectVersionIntegrityError, StoredProjectVersion


VERSION = "pv_" + "a" * 32
TRANSACTION = "pct_" + "b" * 32
SYNC_TRANSACTION = "pct_" + "c" * 32
COMMIT = "d" * 40
MANIFEST_SHA = "sha256:" + "e" * 64


def _stored(*, transaction_id=TRANSACTION, version_id=VERSION) -> StoredProjectVersion:
    return StoredProjectVersion(
        manifest={
            "schema": "odysseus.project_version_manifest.v1",
            "owner_key": "own_redacted",
            "repo_id": "demo",
            "transaction_id": transaction_id,
            "version_id": version_id,
            "commit_sha": COMMIT,
            "created_at": "2026-07-13T12:00:00Z",
            "policy_snapshot": {"schema": "odysseus.project_forge_policy.v1", "forge_mode": "local"},
            "version_label": "Readable version",
            "change_notes": ["Reviewed project output"],
            "artifacts": [
                {
                    "path": "private/build/output.zip",
                    "sha256": "sha256:" + "f" * 64,
                    "size": 42,
                }
            ],
        },
        manifest_sha256=MANIFEST_SHA,
    )


class FakeVersionStore:
    def __init__(self):
        self.versions = {("alice@example.test", "demo"): (_stored(),)}
        self.tampered = set()
        self.calls = []

    def iter_verified_versions(self, *, owner_id, repo_id):
        self.calls.append(("list", owner_id, repo_id))
        if (owner_id, repo_id) in self.tampered:
            raise ProjectVersionIntegrityError("C:/private/tampered manifest payload")
        return self.versions.get((owner_id, repo_id), ())


class FakeLocalForge:
    def __init__(self):
        self.store = FakeVersionStore()
        self.tampered_versions = set()
        self.calls = []

    def verify_version(self, *, owner_id, repo_id, version_id):
        self.calls.append((owner_id, repo_id, version_id))
        if (owner_id, repo_id, version_id) in self.tampered_versions:
            raise ProjectVersionIntegrityError("Bearer private manifest bytes")
        for item in self.store.versions.get((owner_id, repo_id), ()):
            if item.version_id == version_id:
                return item
        raise KeyError(version_id)


class FakeCommitHandler:
    def __init__(self):
        self.calls = []
        self.result_override = None

    def handle(self, arguments, *, context):
        self.calls.append((dict(arguments), dict(context)))
        if self.result_override is not None:
            return self.result_override
        if any(key in arguments for key in ("provider", "forge_mode", "remote")):
            return _blocked_commit("unsupported_arguments")
        if arguments.get("confirmed") is not True:
            return _blocked_commit("confirmation_required")
        return {
            "status": "committed",
            "error_code": "",
            "transaction_id": TRANSACTION,
            "repo_id": "demo",
            "commit_sha": COMMIT,
            "local_status": "committed",
            "provider_statuses": {},
            "overall_status": "committed",
            "retry_scheduled": False,
        }


def _blocked_commit(code):
    return {
        "status": "blocked",
        "error_code": code,
        "transaction_id": "",
        "repo_id": "demo",
        "commit_sha": "",
        "local_status": "blocked",
        "provider_statuses": {},
        "overall_status": "blocked",
        "retry_scheduled": False,
    }


def _commit_body(**overrides):
    body = {
        "title": "feat: retain project",
        "description": "Keep the reviewed state.",
        "reviewed_paths": ["README.md"],
        "checks_passed": True,
        "content_reviewed": True,
        "confirmed": True,
        "idempotency_key": "route-commit-1",
    }
    body.update(overrides)
    return body


def _owner(request):
    return request.headers.get("X-Test-Owner")


def _admin(request):
    return request.headers.get("X-Test-Admin") == "yes"


def _csrf(request):
    return request.headers.get("X-Test-CSRF") == "valid"


def _headers(owner="alice@example.test", *, admin=True, csrf=False):
    headers = {"X-Test-Owner": owner}
    if admin:
        headers["X-Test-Admin"] = "yes"
    if csrf:
        headers["X-Test-CSRF"] = "valid"
    return headers


def _client(tmp_path: Path, *, local=None, handler=None, fail_closed=False):
    policy_store = ProjectForgePolicyStore(root=tmp_path / "policies")
    local_forge = local or FakeLocalForge()
    outbox = ProjectForgeOutbox(root=tmp_path / "outbox")
    commit_handler = handler or FakeCommitHandler()
    kwargs = {}
    if not fail_closed:
        kwargs = {
            "owner_resolver": _owner,
            "admin_gate": _admin,
            "csrf_gate": _csrf,
        }
    app = FastAPI()
    app.include_router(
        setup_project_versioning_routes(
            policy_store=policy_store,
            local_forge=local_forge,
            outbox=outbox,
            commit_handler=commit_handler,
            **kwargs,
        )
    )
    return TestClient(app), policy_store, local_forge, outbox, commit_handler


def test_factory_defaults_fail_closed_without_auth_or_admin_integration(tmp_path):
    client, *_ = _client(tmp_path, fail_closed=True)

    response = client.get("/api/project-versioning/demo/policy")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_all_routes_require_owner_and_admin_and_mutations_require_csrf(tmp_path):
    client, *_ = _client(tmp_path)

    assert client.get("/api/project-versioning/demo/policy").status_code == 401
    assert client.get(
        "/api/project-versioning/demo/policy",
        headers=_headers(admin=False),
    ).status_code == 403
    assert client.put(
        "/api/project-versioning/demo/policy",
        headers=_headers(),
        json={"confirmed": True, "policy": ProjectForgePolicy().to_dict()},
    ).status_code == 403
    assert client.post(
        f"/api/project-versioning/demo/versions/{VERSION}/verify",
        headers=_headers(),
    ).status_code == 403
    assert client.post(
        "/api/project-versioning/demo/commit",
        headers=_headers(),
        json=_commit_body(),
    ).status_code == 403


def test_owner_scoped_policy_defaults_local_and_put_isolated(tmp_path):
    client, store, *_ = _client(tmp_path)
    github = ProjectForgePolicy(
        forge_mode="github",
        backup_providers=("nextcloud",),
    )

    default = client.get(
        "/api/project-versioning/demo/policy",
        headers=_headers("alice@example.test"),
    )
    saved = client.put(
        "/api/project-versioning/demo/policy",
        headers=_headers("alice@example.test", csrf=True),
        json={"confirmed": True, "policy": github.to_dict()},
    )
    bob = client.get(
        "/api/project-versioning/demo/policy",
        headers=_headers("bob@example.test"),
    )

    assert default.json()["policy"]["forge_mode"] == "local"
    assert saved.status_code == 200
    assert saved.json()["policy"]["forge_mode"] == "github"
    assert saved.json()["policy"]["backup_providers"] == ["nextcloud"]
    assert bob.json()["policy"]["forge_mode"] == "local"
    assert store.load_policy(owner_id="alice@example.test", repo_id="demo").forge_mode == "github"


def test_policy_put_requires_confirmation_and_rejects_invalid_or_secret_fields(tmp_path):
    client, store, *_ = _client(tmp_path)
    path = "/api/project-versioning/demo/policy"
    headers = _headers(csrf=True)

    unconfirmed = client.put(
        path,
        headers=headers,
        json={"confirmed": False, "policy": ProjectForgePolicy().to_dict()},
    )
    invalid = ProjectForgePolicy().to_dict()
    invalid["nextcloud"]["client_side_encryption"] = True
    invalid_response = client.put(
        path,
        headers=headers,
        json={"confirmed": True, "policy": invalid},
    )
    secret_response = client.put(
        path,
        headers=headers,
        json={
            "confirmed": True,
            "policy": {**ProjectForgePolicy().to_dict(), "remote_url": "https://user:password@example.test"},
        },
    )

    assert unconfirmed.status_code == 409
    assert invalid_response.status_code == 422
    assert secret_response.status_code == 422
    assert "password" not in json.dumps(secret_response.json()).casefold()
    assert store.load_policy(owner_id="alice@example.test", repo_id="demo").forge_mode == "local"


def test_version_list_detail_and_verify_are_owner_scoped_and_redacted(tmp_path):
    client, _, local, *_ = _client(tmp_path)
    headers = _headers()

    listed = client.get("/api/project-versioning/demo/versions", headers=headers)
    detail = client.get(
        f"/api/project-versioning/demo/versions/{VERSION}",
        headers=headers,
    )
    verified = client.post(
        f"/api/project-versioning/demo/versions/{VERSION}/verify",
        headers=_headers(csrf=True),
    )
    bob = client.get(
        f"/api/project-versioning/demo/versions/{VERSION}",
        headers=_headers("bob@example.test"),
    )

    assert listed.status_code == 200 and listed.json()["count"] == 1
    assert detail.status_code == 200
    assert verified.status_code == 200 and verified.json()["verified"] is True
    assert bob.status_code == 404
    dumped = json.dumps((listed.json(), detail.json(), verified.json()))
    assert "private/build" not in dumped
    assert "policy_snapshot" not in dumped
    assert "manifest" not in detail.json()["version"]
    assert detail.json()["version"]["artifact_count"] == 1
    assert local.calls[-1][0] == "bob@example.test"


def test_version_tamper_fails_closed_without_raw_manifest_or_provider_text(tmp_path):
    local = FakeLocalForge()
    local.tampered_versions.add(("alice@example.test", "demo", VERSION))
    client, *_ = _client(tmp_path, local=local)

    response = client.get(
        f"/api/project-versioning/demo/versions/{VERSION}",
        headers=_headers(),
    )

    assert response.status_code == 409
    dumped = json.dumps(response.json())
    assert "Bearer" not in dumped
    assert "manifest bytes" not in dumped


def test_versions_list_tamper_fails_closed(tmp_path):
    local = FakeLocalForge()
    local.store.tampered.add(("alice@example.test", "demo"))
    client, *_ = _client(tmp_path, local=local)

    response = client.get(
        "/api/project-versioning/demo/versions",
        headers=_headers(),
    )

    assert response.status_code == 409
    assert "C:/private" not in json.dumps(response.json())


def test_transaction_status_supports_local_default_and_owner_isolation(tmp_path):
    client, *_ = _client(tmp_path)

    local = client.get(
        f"/api/project-versioning/demo/transactions/{TRANSACTION}",
        headers=_headers(),
    )
    bob = client.get(
        f"/api/project-versioning/demo/transactions/{TRANSACTION}",
        headers=_headers("bob@example.test"),
    )

    assert local.status_code == 200
    assert local.json()["provider_statuses"] == {}
    assert local.json()["overall_status"] == "committed"
    assert local.json()["retry_scheduled"] is False
    assert bob.status_code == 404


def test_transaction_status_reads_owner_scoped_provider_outbox(tmp_path):
    client, _, _, outbox, _ = _client(tmp_path)
    outbox.enqueue(
        owner_id="alice@example.test",
        repo_id="demo",
        transaction_id=SYNC_TRANSACTION,
        version_id=VERSION,
        provider="github",
        commit_sha=COMMIT,
        manifest_evidence={
            "schema": "odysseus.project_version_manifest.v1",
            "sha256": MANIFEST_SHA,
            "reference": f"version:{VERSION}",
        },
        policy_evidence=ProjectForgePolicy(forge_mode="github").to_dict(),
    )

    alice = client.get(
        f"/api/project-versioning/demo/transactions/{SYNC_TRANSACTION}",
        headers=_headers(),
    )
    bob = client.get(
        f"/api/project-versioning/demo/transactions/{SYNC_TRANSACTION}",
        headers=_headers("bob@example.test"),
    )

    assert alice.status_code == 200
    assert alice.json()["provider_statuses"] == {"github": "pending"}
    assert alice.json()["overall_status"] == "sync_pending"
    assert alice.json()["retry_scheduled"] is True
    assert bob.status_code == 404


def test_commit_route_delegates_to_same_handler_with_authenticated_context(tmp_path):
    handler = FakeCommitHandler()
    client, *_, returned_handler = _client(tmp_path, handler=handler)

    response = client.post(
        "/api/project-versioning/demo/commit",
        headers=_headers(csrf=True),
        json=_commit_body(),
    )

    assert response.status_code == 200
    assert response.json()["transaction_id"] == TRANSACTION
    assert returned_handler is handler
    assert len(handler.calls) == 1
    arguments, context = handler.calls[0]
    assert arguments["repo_id"] == "demo"
    assert "provider" not in arguments and "remote" not in arguments
    assert context == {
        "is_authenticated": True,
        "authenticated_owner_id": "alice@example.test",
    }


def test_commit_confirmation_provider_choice_and_repo_mismatch_fail_closed(tmp_path):
    handler = FakeCommitHandler()
    client, *_ = _client(tmp_path, handler=handler)
    headers = _headers(csrf=True)

    confirmation = client.post(
        "/api/project-versioning/demo/commit",
        headers=headers,
        json=_commit_body(confirmed=False),
    )
    provider = client.post(
        "/api/project-versioning/demo/commit",
        headers=headers,
        json=_commit_body(provider="github"),
    )
    mismatch = client.post(
        "/api/project-versioning/demo/commit",
        headers=headers,
        json=_commit_body(repo_id="other"),
    )

    assert confirmation.status_code == 409
    assert confirmation.json()["error_code"] == "confirmation_required"
    assert provider.status_code == 400
    assert provider.json()["error_code"] == "unsupported_arguments"
    assert mismatch.status_code == 409
    assert len(handler.calls) == 2


def test_commit_result_redaction_rejects_malformed_handler_output(tmp_path):
    handler = FakeCommitHandler()
    handler.result_override = {
        **_blocked_commit("token=not-a-real-value"),
        "status": "committed",
        "transaction_id": "C:/private/transaction",
        "commit_sha": "not-a-sha",
        "local_status": "committed",
        "overall_status": "committed",
    }
    client, *_ = _client(tmp_path, handler=handler)

    response = client.post(
        "/api/project-versioning/demo/commit",
        headers=_headers(csrf=True),
        json=_commit_body(),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "commit_result_invalid"
    dumped = json.dumps(response.json())
    assert "token" not in dumped.casefold()
    assert "C:/private" not in dumped


def test_same_origin_csrf_gate_rejects_missing_cross_origin_and_cross_site():
    app = FastAPI()

    @app.post("/probe")
    def probe(request: Request):
        return {"allowed": _same_origin_csrf_gate(request)}

    client = TestClient(app)
    assert client.post("/probe").json() == {"allowed": False}
    assert client.post("/probe", headers={"Origin": "https://evil.example"}).json() == {"allowed": False}
    assert client.post(
        "/probe",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
    ).json() == {"allowed": False}
    assert client.post(
        "/probe",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    ).json() == {"allowed": True}


def test_default_router_is_wired_fail_closed_for_mutations(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ODYSSEUS_SINGLE_USER_OWNER", "route-local-owner")
    app = FastAPI()
    app.include_router(setup_default_project_versioning_routes())
    client = TestClient(app)

    response = client.put(
        "/api/project-versioning/route-wiring-probe/policy",
        json={"confirmed": True, "policy": ProjectForgePolicy().to_dict()},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed"


def test_app_mounts_default_project_versioning_router_exactly_once():
    source = Path("app.py").read_text(encoding="utf-8")

    assert source.count("app.include_router(setup_default_project_versioning_routes())") == 1
    assert source.index("app.include_router(setup_repo_routes())") < source.index(
        "app.include_router(setup_default_project_versioning_routes())"
    )
