import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import diagnostics_routes
from src import ai_activity_ledger


def test_read_ai_activity_filters_and_summarizes_redacted_records(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(tmp_path))

    ai_activity_ledger.record_ai_activity(
        owner="alice",
        surface="email",
        prompt_type="email_ai_reply",
        provider="openai",
        endpoint_url="https://api.example.test/v1/chat/completions",
        model="model-a",
        messages=[{"role": "user", "content": "private invoice body"}],
        output_chars=42,
        duration_ms=120,
        status="success",
    )
    ai_activity_ledger.record_ai_activity(
        owner="alice",
        surface="calendar",
        prompt_type="calendar_quick_parse",
        provider="local",
        endpoint_url="http://127.0.0.1:11434/v1/chat/completions",
        model="local-model",
        messages=[{"role": "user", "content": "private appointment"}],
        status="error",
        error_class="HTTPException",
    )

    result = ai_activity_ledger.read_ai_activity(surface="email")
    encoded = json.dumps(result, sort_keys=True)

    assert result["count"] == 1
    assert result["summary"]["by_surface"] == {"email": 1}
    assert result["records"][0]["prompt_type"] == "email_ai_reply"
    assert "prompt_hash" not in result["records"][0]
    assert "endpoint_hash" not in result["records"][0]
    assert "private invoice body" not in encoded
    assert "/v1/chat/completions" not in encoded


def test_ai_activity_diagnostics_route_is_admin_gated_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_activity_ledger, "AI_ACTIVITY_LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))

    ai_activity_ledger.record_ai_activity(
        owner="admin",
        surface="memory",
        prompt_type="memory_file_extract",
        provider="local",
        endpoint_url="http://127.0.0.1:11434/v1/chat/completions",
        model="gemma",
        messages=[{"role": "user", "content": "private uploaded document"}],
        status="success",
    )

    response = TestClient(app).get("/api/diagnostics/ai-activity?surface=memory")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["records"][0]["surface"] == "memory"
    assert payload["records"][0]["prompt_type"] == "memory_file_extract"
    assert "private uploaded document" not in encoded
    assert "authorization" not in encoded.lower()
