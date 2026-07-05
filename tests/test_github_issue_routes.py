from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, GitHubIssueRecord
from routes.github_issue_routes import setup_github_issue_routes


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add_all(
            [
                GitHubIssueRecord(
                    id="issue-1",
                    owner="default",
                    provider="github",
                    repository="fuzzy123-ai/odysseus-fuzzy",
                    external_id="1",
                    title="Telegram inbox attachment fails",
                    body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
                    state="open",
                    labels_json=["area/inbox", "priority/high"],
                ),
                GitHubIssueRecord(
                    id="issue-2",
                    owner="default",
                    provider="github",
                    repository="fuzzy123-ai/odysseus-fuzzy",
                    external_id="2",
                    title="Telegram inbox attachment fails",
                    body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
                    state="closed",
                    labels_json=["area/inbox"],
                ),
            ]
        )
        db.commit()
    return Session


def _client():
    app = FastAPI()
    app.include_router(
        setup_github_issue_routes(
            session_factory=_session_factory(),
            require_admin_fn=lambda request: None,
        )
    )
    return TestClient(app)


def test_github_issue_readiness_reports_local_counts_and_live_gates():
    response = _client().get("/api/github-issues/readiness", params={"repository": "fuzzy123-ai/odysseus-fuzzy"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["local_issue_count"] == 2
    assert payload["local_open_issue_count"] == 1
    assert payload["sync"]["status"] == "needs_live_go"
    assert payload["writes"]["requires_live_go"] is True


def test_github_issue_readiness_reports_ready_sync_when_env_is_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_LIVE_ENABLED", "true")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOWED_REPOSITORIES", "fuzzy123-ai/odysseus-fuzzy")
    monkeypatch.setenv("GITHUB_ISSUE_SYNC_ALLOW_PUBLIC_UNAUTHENTICATED", "true")

    response = _client().get("/api/github-issues/readiness", params={"repository": "fuzzy123-ai/odysseus-fuzzy"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sync"]["status"] == "ready_for_confirmed_sync"
    assert payload["sync"]["auth_ready"] is True
    assert payload["sync"]["provider_writes_performed"] == 0


def test_github_issue_duplicates_route_returns_top_candidates():
    response = _client().post(
        "/api/github-issues/duplicates",
        json={
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "title": "Telegram inbox attachment fails",
            "body": "Telegram file upload is not processed by universal inbox memory.",
            "top_k": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["index"]["indexed"] == 2
    candidates = payload["github_issue_duplicates"]["candidates"]
    assert [candidate["external_id"] for candidate in candidates] == ["1", "2"]
    assert all("possible duplicate" in candidate["reason"] for candidate in candidates)


def test_github_issue_sync_route_uses_gated_tool(monkeypatch):
    async def fake_manage(content, owner=None):
        return {"status": "needs_live_go", "owner": owner, "exit_code": 0}

    monkeypatch.setattr("routes.github_issue_routes.do_manage_github_issues", fake_manage)
    response = _client().post(
        "/api/github-issues/sync",
        json={
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "max_items": 5,
            "confirmed": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_live_go"
    assert payload["owner"] == "default"


def test_github_issue_write_plan_set_fields_does_not_write_provider():
    response = _client().post(
        "/api/github-issues/write-plan",
        json={
            "action": "set_fields",
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "issue_ref": "#1",
            "fields": {"priority": "high", "area": "inbox"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "confirmation_required"
    assert payload["requires_live_go"] is False
    assert payload["fields"] == {"priority": "high", "area": "inbox"}
    assert [item["status"] for item in payload["write_report"]] == ["planned", "planned"]


def test_github_issue_write_plan_create_triaged_blocks_duplicates():
    response = _client().post(
        "/api/github-issues/write-plan",
        json={
            "action": "create_triaged",
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "title": "Telegram inbox attachment fails",
            "body": "File upload through Telegram is not processed by the universal inbox memory pipeline.",
            "fields": {"type": "bug", "priority": "high", "area": "inbox"},
            "confirmed": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked_by_duplicate_candidate"
    assert payload["requires_live_go"] is False
    assert payload["github_issue_duplicates"]["blocks_auto_create"] is True
