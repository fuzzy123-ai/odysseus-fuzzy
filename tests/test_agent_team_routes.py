from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_agent_team_card_route_is_admin_gated_and_returns_safe_payload(monkeypatch):
    import routes.agent_team_routes as agent_team_routes

    admin_calls = []
    monkeypatch.setattr(agent_team_routes, "require_admin", lambda request: admin_calls.append(request))

    app = FastAPI()
    app.include_router(agent_team_routes.setup_agent_team_routes())

    response = TestClient(app).get("/api/agents/team-card")

    assert response.status_code == 200
    assert admin_calls
    payload = response.json()
    assert [agent["agent_id"] for agent in payload["team"]] == ["charlie", "alice", "bob"]
    assert payload["hidden_workers"] == []
    assert "Team:" in payload["prompt_text"]
    assert "rules" in payload
    assert "audit" in payload
    assert "token=" not in str(payload).lower()
    assert "chat_id" not in str(payload).lower()
