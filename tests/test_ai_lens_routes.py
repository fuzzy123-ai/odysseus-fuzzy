import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.ai_lens_routes as ai_lens_routes
from src.ai_lens_events import AiLensEvent
from src.ai_lens_service import AiLensService, AiLensServiceLimits


BASE_TIME = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)


def _event(sequence=1, *, session_id="route-session", payload=None):
    return AiLensEvent.create(
        event_id=f"{session_id}-event-{sequence:03d}",
        session_id=session_id,
        turn_id="route-turn",
        sequence=sequence,
        created_at=BASE_TIME + timedelta(milliseconds=sequence),
        event_type="memory_hit",
        observation_origin="runtime_observation",
        truth_level="runtime_trace",
        privacy_level="metadata",
        redaction_level="metadata_only",
        summary="Bounded route test observation.",
        payload=payload or {"rank": sequence, "score": 0.8},
    )


def _limits(**overrides):
    values = {
        "max_sessions": 4,
        "max_events_per_session": 16,
        "max_bytes_per_session": 64 * 1024,
        "max_snapshot_events": 16,
        "max_snapshot_bytes": 64 * 1024,
    }
    values.update(overrides)
    return AiLensServiceLimits.create(**values)


def _runtime_service(event_count=3, *, payload=None):
    service = AiLensService(limits=_limits())
    service.ingest_batch(
        tuple(_event(sequence, payload=payload) for sequence in range(1, event_count + 1))
    )
    return service


def _client(monkeypatch, *, service=None, gate=None, allow_fixture=False, max_stream_bytes=64 * 1024):
    monkeypatch.setattr(ai_lens_routes, "require_admin", gate or (lambda _request: None))
    app = FastAPI()
    app.include_router(
        ai_lens_routes.setup_ai_lens_routes(
            service=service or _runtime_service(),
            allow_fixture=allow_fixture,
            max_stream_bytes=max_stream_bytes,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_every_public_route_is_admin_gated(monkeypatch):
    calls = []
    client = _client(monkeypatch, gate=lambda request: calls.append(request))

    paths = (
        "/api/ai-lens/service",
        "/api/ai-lens/sessions",
        "/api/ai-lens/sessions/route-session/snapshot",
        "/api/ai-lens/sessions/route-session/stream",
    )
    for path in paths:
        assert client.get(path).status_code == 200

    assert len(calls) == len(paths)


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_is_preserved_for_snapshot_and_stream(monkeypatch, status_code):
    def deny(_request):
        raise HTTPException(status_code, "Admin only")

    client = _client(monkeypatch, gate=deny)

    assert client.get("/api/ai-lens/sessions/route-session/snapshot").status_code == status_code
    assert client.get("/api/ai-lens/sessions/route-session/stream").status_code == status_code


def test_service_and_session_endpoints_are_read_only_and_bounded(monkeypatch):
    service = _runtime_service()
    client = _client(monkeypatch, service=service)

    service_response = client.get("/api/ai-lens/service")
    sessions_response = client.get("/api/ai-lens/sessions")

    assert service_response.status_code == 200
    assert service_response.json() == {
        "schema": "odysseus.ai_lens.service.v1",
        "mode": "runtime",
        "fixture_mode": False,
        "session_count": 1,
        "evicted_session_count": 0,
        "limits": service.limits.to_dict(),
        "raw_content_visible": False,
        "fixture_access_enabled": False,
        "write_endpoint_available": False,
        "stream_event_limit": 128,
        "stream_byte_budget": 64 * 1024,
    }
    sessions = sessions_response.json()
    assert sessions_response.status_code == 200
    assert sessions["schema"] == "odysseus.ai_lens.sessions.v1"
    assert sessions["session_count"] == 1
    assert sessions["sessions"][0]["session_id"] == "route-session"
    assert sessions["raw_content_visible"] is False

    router = ai_lens_routes.setup_ai_lens_routes(service=service)
    assert all("POST" not in route.methods for route in router.routes)
    assert service.snapshot("route-session")["retained_event_count"] == 3


def test_snapshot_route_returns_service_contract_and_enforces_limit(monkeypatch):
    service = _runtime_service(event_count=5)
    client = _client(monkeypatch, service=service)

    response = client.get("/api/ai-lens/sessions/route-session/snapshot?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "odysseus.ai_lens.snapshot.v1"
    assert payload["returned_event_count"] == 2
    assert [event["sequence"] for event in payload["events"]] == [4, 5]
    assert payload["truncated"] is True
    assert payload["raw_content_visible"] is False

    invalid = client.get("/api/ai-lens/sessions/route-session/snapshot?limit=129")
    assert invalid.status_code == 400
    private_query = "private-secret-value"
    private_error = client.get(
        f"/api/ai-lens/sessions/route-session/snapshot?limit={private_query}"
    )
    assert private_error.status_code == 400
    assert private_query not in private_error.text


def test_unknown_and_invalid_sessions_return_sanitized_4xx(monkeypatch):
    client = _client(monkeypatch)

    missing = client.get("/api/ai-lens/sessions/unknown-session/snapshot")
    private_input = "private-secret=value"
    invalid = client.get(f"/api/ai-lens/sessions/{private_input}/snapshot")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "AI Lens session not found"
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "Invalid AI Lens session identifier"
    assert private_input not in invalid.text


def test_sse_is_finite_validated_heartbeat_bounded_and_not_cached(monkeypatch):
    service = _runtime_service(event_count=4)
    client = _client(monkeypatch, service=service)

    response = client.get(
        "/api/ai-lens/sessions/route-session/stream?event_limit=3&heartbeat_every=1"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.count("event: ai_lens_event") == 3
    assert response.text.count(": heartbeat") == 3
    assert "event: stream_end" in response.text
    assert response.text.endswith("\n\n")

    data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    decoded = [json.loads(line) for line in data_lines]
    events = [item for item in decoded if item.get("schema") == "odysseus.ai_lens.event.v1"]
    assert [event["sequence"] for event in events] == [2, 3, 4]
    assert all(event["raw_content_visible"] is False for event in events)
    end = decoded[-1]
    assert end["schema"] == "odysseus.ai_lens.stream_end.v1"
    assert end["emitted_event_count"] == 3
    assert end["byte_limited"] is False


def test_sse_stream_byte_budget_stops_before_unbounded_output(monkeypatch):
    large_payload = {f"safe_field_{index}": "x" * 240 for index in range(6)}
    service = _runtime_service(event_count=4, payload=large_payload)
    client = _client(
        monkeypatch,
        service=service,
        max_stream_bytes=4_096,
    )

    response = client.get(
        "/api/ai-lens/sessions/route-session/stream?event_limit=4&heartbeat_every=1"
    )

    assert response.status_code == 200
    assert len(response.content) <= 4_096
    assert response.text.count("event: ai_lens_event") < 4
    assert '"byte_limited":true' in response.text
    assert "event: stream_end" in response.text


def test_fixture_service_requires_explicit_route_opt_in(monkeypatch):
    fixture = AiLensService.fixture(limits=_limits())

    with pytest.raises(ai_lens_routes.AiLensRouteConfigurationError, match="disabled"):
        ai_lens_routes.setup_ai_lens_routes(service=fixture)

    client = _client(monkeypatch, service=fixture, allow_fixture=True)
    response = client.get("/api/ai-lens/service")

    assert response.status_code == 200
    assert response.json()["mode"] == "fixture"
    assert response.json()["fixture_access_enabled"] is True


def test_app_registers_ai_lens_router_once_with_fixture_disabled_by_default():
    source = Path("app.py").read_text(encoding="utf-8")

    assert source.count("from routes.ai_lens_routes import setup_ai_lens_routes") == 1
    assert source.count("app.include_router(setup_ai_lens_routes())") == 1
