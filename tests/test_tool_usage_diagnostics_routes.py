from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from routes import diagnostics_routes
from src.tool_usage_analytics import ToolUsageAnalyticsService
from src.tool_usage_store import ToolUsageStore
from tests.test_tool_usage_analytics import _pair


def _service(tmp_path):
    store = ToolUsageStore(tmp_path / "route-usage.sqlite3")
    store.migrate()
    fixtures = (
        _pair(101, duration=5),
        _pair(102, duration=20, status="failed"),
        _pair(103, duration=200, status="blocked", surface="chat"),
        _pair(104, duration=2000, dynamic=True, retry=1),
    )
    store.append_events(event for pair in fixtures for event in pair)
    service = ToolUsageAnalyticsService(store)
    service.aggregate_day("2026-07-17")
    return service, store


def _app(service):
    app = FastAPI()
    app.include_router(
        diagnostics_routes.setup_diagnostics_routes(
            None,
            False,
            None,
            tool_usage_analytics=service,
        )
    )
    return app


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_admin_route_returns_only_bounded_aggregate_projection(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    client = TestClient(_app(service))

    response = client.get(
        "/api/diagnostics/tool-usage",
        params={
            "start_day": "2026-07-17",
            "end_day": "2026-07-17",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["calls"] == 4
    assert payload["active_days"] == 1
    assert payload["duration_p50_ms"] == 50
    assert payload["duration_p95_ms"] == 2500
    assert payload["retry_count"] == 1
    assert payload["coverage"] == 1.0
    assert payload["row_count"] == 4
    assert payload["rows_truncated"] is True
    assert len(payload["rows"]) == 1
    assert payload["quality"]["scope"] == "period_global"
    assert payload["raw_content_visible"] is False
    forbidden = {
        "args",
        "command",
        "content",
        "correlation_ref",
        "event_id",
        "invocation_id",
        "metadata",
        "output",
        "owner_ref",
        "payload",
        "prompt",
        "result",
        "run_ref",
        "session_ref",
        "url",
    }
    assert not (set(_keys(payload)) & forbidden)
    store.close()


@pytest.mark.parametrize(
    ("params", "expected_calls"),
    [
        ({"tool": "read_file"}, 3),
        ({"family": "code_filesystem"}, 3),
        ({"source": "plugin"}, 1),
        ({"surface": "chat"}, 1),
        ({"status": "failed"}, 1),
        ({"tool": "dynamic.plugin.unclassified", "source": "plugin"}, 1),
    ],
)
def test_controlled_filters_select_aggregate_rows(
    tmp_path, monkeypatch, params, expected_calls
):
    service, store = _service(tmp_path)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    query = {
        "start_day": "2026-07-17",
        "end_day": "2026-07-17",
        **params,
    }

    response = TestClient(_app(service)).get(
        "/api/diagnostics/tool-usage",
        params=query,
    )

    assert response.status_code == 200
    assert response.json()["calls"] == expected_calls
    assert response.json()["filters"] == {
        {
            "tool": "tool_analytics_id",
            "family": "tool_family",
            "source": "tool_source",
            "surface": "surface",
            "status": "status",
        }[key]: value
        for key, value in params.items()
    }
    store.close()


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("tool", "private_unknown_tool"),
        ("tool", "manage_rag"),
        ("family", "private-family"),
        ("source", "private-source"),
        ("surface", "private-surface"),
        ("status", "private-status"),
    ],
)
def test_unknown_filters_fail_closed(tmp_path, monkeypatch, parameter, value):
    service, store = _service(tmp_path)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)

    response = TestClient(_app(service)).get(
        "/api/diagnostics/tool-usage",
        params={
            "start_day": "2026-07-17",
            "end_day": "2026-07-17",
            parameter: value,
        },
    )

    assert response.status_code == 400
    assert value not in response.json()["detail"]
    store.close()


def test_period_and_result_limits_are_enforced(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    client = TestClient(_app(service))

    too_wide = client.get(
        "/api/diagnostics/tool-usage?start_day=2026-01-01&end_day=2026-07-17"
    )
    reversed_period = client.get(
        "/api/diagnostics/tool-usage?start_day=2026-07-18&end_day=2026-07-17"
    )
    limit_zero = client.get("/api/diagnostics/tool-usage?limit=0")
    limit_large = client.get("/api/diagnostics/tool-usage?limit=251")

    assert too_wide.status_code == 400
    assert reversed_period.status_code == 400
    assert limit_zero.status_code == 422
    assert limit_large.status_code == 422
    store.close()


@pytest.mark.parametrize("status_code", [401, 403])
def test_unauthenticated_and_non_admin_requests_fail_before_store_access(
    monkeypatch, status_code
):
    class _NeverCalled:
        def summarize(self, *_args, **_kwargs):
            raise AssertionError("store accessed before admin authorization")

    def _deny(_request):
        raise HTTPException(status_code, "denied")

    monkeypatch.setattr(diagnostics_routes, "require_admin", _deny)

    response = TestClient(_app(_NeverCalled())).get(
        "/api/diagnostics/tool-usage?start_day=2026-07-17&end_day=2026-07-17"
    )

    assert response.status_code == status_code


def test_tool_usage_surface_has_no_raw_or_mutating_route(tmp_path):
    service, store = _service(tmp_path)
    app = _app(service)
    route_prefix = "/api/diagnostics/tool-usage"
    http_operations = {
        "delete",
        "get",
        "head",
        "options",
        "patch",
        "post",
        "put",
        "trace",
    }
    try:
        public_routes = {
            path: {
                operation.upper()
                for operation in operations
                if operation in http_operations
            }
            for path, operations in app.openapi()["paths"].items()
            if path == route_prefix or path.startswith(route_prefix + "/")
        }

        assert public_routes == {route_prefix: {"GET"}}
        client = TestClient(app)
        assert client.post(route_prefix).status_code == 405
        assert client.get(route_prefix + "/raw").status_code == 404
    finally:
        store.close()


def test_default_router_path_reads_existing_sqlite_aggregate_store(tmp_path, monkeypatch):
    database = tmp_path / "app.sqlite3"
    store = ToolUsageStore(database)
    store.migrate()
    store.append_events(_pair(201))
    ToolUsageAnalyticsService(store).aggregate_day("2026-07-17")
    store.close()
    import core.database as database_module

    monkeypatch.setattr(
        database_module,
        "DATABASE_URL",
        "sqlite:///" + database.as_posix(),
    )
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    app = FastAPI()
    app.include_router(
        diagnostics_routes.setup_diagnostics_routes(None, False, None)
    )

    response = TestClient(app).get(
        "/api/diagnostics/tool-usage?start_day=2026-07-17&end_day=2026-07-17"
    )

    assert response.status_code == 200
    assert response.json()["calls"] == 1
