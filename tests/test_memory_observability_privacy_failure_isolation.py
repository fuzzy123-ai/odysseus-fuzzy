from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.memory_runtime_metrics import (
    MEMORY_RUNTIME_LABEL_ENUMS,
    MemoryRuntimeMetricsRegistry,
    MemoryRuntimeMetricsSnapshot,
    RuntimeMetricSnapshot,
)
from src.observability_metrics import (
    ObservabilityMetricsError,
    build_process_runtime_metrics_snapshot,
    build_runtime_metric_sample,
    render_process_runtime_metrics,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY_ROOT = ROOT / "ops" / "homeserver" / "observability-podman"
PROMETHEUS_ROOT = OBSERVABILITY_ROOT / "prometheus"
GRAFANA_ROOT = OBSERVABILITY_ROOT / "grafana"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "hostile_value",
    [
        r"C:\Users\synthetic\vault\private.md",
        "/home/synthetic/vault/private.md",
        "../../vault/private.md",
        "synthetic query with private terms",
        "Bearer SYNTHETIC_NOT_A_TOKEN",
        "source-id:synthetic-123456",
        "vault-id:synthetic-private",
        "raw_prompt: synthetic instructions",
        "user@example.invalid",
        "session-synthetic-private",
    ],
)
def test_hostile_content_is_rejected_without_entering_registry_or_exporter(hostile_value):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    accepted = registry.increment_counter(
        "odysseus_memory_operations_total",
        {
            "component": "memory",
            "operation": "query",
            "outcome": "success",
            "runtime": hostile_value,
        },
    )

    assert accepted is False
    with pytest.raises(ObservabilityMetricsError):
        build_runtime_metric_sample(
            "odysseus_memory_operations_total",
            1,
            labels={
                "component": "memory",
                "operation": "query",
                "outcome": "success",
                "runtime": hostile_value,
            },
        )
    encoded = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert hostile_value not in encoded
    assert registry.snapshot().dropped_samples_total == 1
    assert registry.snapshot().labelset_count == 1


def test_registry_reaches_exact_256_prometheus_series_then_fails_closed():
    registry = MemoryRuntimeMetricsRegistry.for_tests(max_series=256)
    scalar_updates: list[tuple[str, str, dict[str, str]]] = []

    for cache_result in MEMORY_RUNTIME_LABEL_ENUMS["cache_result"]:
        for runtime in MEMORY_RUNTIME_LABEL_ENUMS["runtime"]:
            scalar_updates.append(
                (
                    "counter",
                    "odysseus_raptor_cache_requests_total",
                    {"cache_result": cache_result, "runtime": runtime},
                )
            )

    for component in MEMORY_RUNTIME_LABEL_ENUMS["component"]:
        for operation in MEMORY_RUNTIME_LABEL_ENUMS["operation"]:
            for runtime in MEMORY_RUNTIME_LABEL_ENUMS["runtime"]:
                scalar_updates.append(
                    (
                        "gauge",
                        "odysseus_memory_worker_queue_depth",
                        {
                            "component": component,
                            "operation": operation,
                            "runtime": runtime,
                        },
                    )
                )

    for name in (
        "odysseus_raptor_cache_entries",
        "odysseus_query_cache_entries",
        "odysseus_query_cache_bytes",
        "odysseus_raptor_rebuild_sources",
        "odysseus_raptor_rebuild_sources_per_second",
        "odysseus_raptor_rebuild_rss_delta_bytes",
        "odysseus_raptor_artifact_age_seconds",
    ):
        for runtime in MEMORY_RUNTIME_LABEL_ENUMS["runtime"]:
            scalar_updates.append(("gauge", name, {"runtime": runtime}))

    for component in MEMORY_RUNTIME_LABEL_ENUMS["component"]:
        for operation in MEMORY_RUNTIME_LABEL_ENUMS["operation"]:
            for outcome in MEMORY_RUNTIME_LABEL_ENUMS["outcome"]:
                for runtime in MEMORY_RUNTIME_LABEL_ENUMS["runtime"]:
                    scalar_updates.append(
                        (
                            "counter",
                            "odysseus_memory_operations_total",
                            {
                                "component": component,
                                "operation": operation,
                                "outcome": outcome,
                                "runtime": runtime,
                            },
                        )
                    )

    # The closed contract intentionally permits more labelsets than the
    # fail-closed registry cap. Sample a bounded set of valid scalar labelsets
    # across representative counter/gauge families, reserving the 209th
    # distinct labelset for the overflow assertion.
    assert len(scalar_updates) > 208
    for kind, name, labels in scalar_updates[:208]:
        if kind == "counter":
            assert registry.increment_counter(name, labels)
        else:
            assert registry.set_gauge(name, labels, 0)

    assert registry.observe_histogram(
        "odysseus_memory_operation_duration_seconds",
        {
            "component": "memory",
            "operation": "query",
            "phase": "total",
            "outcome": "success",
            "runtime": "benchmark",
        },
        0.1,
    )
    assert registry.observe_histogram(
        "odysseus_memory_event_loop_lag_seconds",
        {"component": "memory", "operation": "query", "runtime": "benchmark"},
        0.01,
    )
    assert registry.observe_histogram(
        "odysseus_metrics_render_duration_seconds",
        {"outcome": "success", "runtime": "benchmark"},
        0.005,
    )

    at_limit = registry.snapshot()
    assert at_limit.prometheus_series_count == 256
    assert at_limit.labelset_count == 212  # drop + 208 scalar + 3 histogram labelsets
    assert at_limit.dropped_samples_total == 0

    overflow_kind, overflow_name, overflow_labels = scalar_updates[208]
    if overflow_kind == "counter":
        assert registry.increment_counter(overflow_name, overflow_labels) is False
    else:
        assert registry.set_gauge(overflow_name, overflow_labels, 1) is False
    overflow = registry.snapshot()
    assert overflow.prometheus_series_count == 256
    assert overflow.labelset_count == 212
    assert overflow.dropped_samples_total == 1


class _CorruptSnapshotRegistry:
    def __init__(self, hostile_value: str) -> None:
        self.hostile_value = hostile_value
        self.error_observations: list[tuple[str, dict[str, str], float]] = []

    def snapshot(self) -> MemoryRuntimeMetricsSnapshot:
        return MemoryRuntimeMetricsSnapshot(
            samples=(
                RuntimeMetricSnapshot(
                    name="odysseus_memory_operations_total",
                    kind="counter",
                    labels=(
                        ("component", "memory"),
                        ("operation", "query"),
                        ("outcome", "success"),
                        ("runtime", self.hostile_value),
                    ),
                    value=1.0,
                ),
            ),
            prometheus_series_count=2,
            dropped_samples_total=0,
        )

    def observe_histogram(self, name: str, labels: dict[str, str], value: float) -> bool:
        self.error_observations.append((name, labels, value))
        return True


def test_corrupt_registry_snapshot_fails_scrape_without_rendering_payload():
    hostile = r"C:\Users\synthetic\vault\never-render.md"
    registry = _CorruptSnapshotRegistry(hostile)

    with pytest.raises(ObservabilityMetricsError) as exc_info:
        build_process_runtime_metrics_snapshot(memory_registry=registry)  # type: ignore[arg-type]
    assert hostile not in str(exc_info.value)

    with pytest.raises(ObservabilityMetricsError) as render_exc:
        render_process_runtime_metrics(memory_registry=registry)  # type: ignore[arg-type]
    assert hostile not in str(render_exc.value)
    assert len(registry.error_observations) == 1
    name, labels, elapsed = registry.error_observations[0]
    assert name == "odysseus_metrics_render_duration_seconds"
    assert labels == {"outcome": "error", "runtime": "app"}
    assert elapsed >= 0


class _ExplodingMetricSink:
    def increment_counter(self, *args, **kwargs):
        raise RuntimeError("synthetic telemetry failure")

    def set_gauge(self, *args, **kwargs):
        raise RuntimeError("synthetic telemetry failure")

    def observe_histogram(self, *args, **kwargs):
        raise RuntimeError("synthetic telemetry failure")


def test_raptor_cache_miss_and_hit_survive_total_telemetry_failure(monkeypatch, tmp_path):
    from plugins.obsidian.backend import raptor_cache

    monkeypatch.setattr(
        raptor_cache,
        "get_memory_runtime_metrics_registry",
        lambda: _ExplodingMetricSink(),
    )
    raptor_cache.clear_raptor_cache()
    loader = Mock(return_value={"status": "ready", "items": [1, 2, 3]})

    first = raptor_cache.cached_raptor_payload(
        str(tmp_path),
        "privacy-failure-isolation",
        {"limit": 3},
        loader,
        external_validation_seconds=0,
    )
    second = raptor_cache.cached_raptor_payload(
        str(tmp_path),
        "privacy-failure-isolation",
        {"limit": 3},
        loader,
        external_validation_seconds=0,
    )

    assert first["status"] == "ready"
    assert second["status"] == "ready"
    assert first["cache"]["result"] == "miss"
    assert second["cache"]["result"] == "hit"
    loader.assert_called_once_with()


def test_runtime_metrics_endpoint_returns_generic_500_on_registry_failure(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("starlette.testclient")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from routes import diagnostics_routes
    from src import observability_metrics

    class _BrokenRegistry:
        def snapshot(self):
            raise RuntimeError("synthetic registry failure")

        def observe_histogram(self, *args, **kwargs):
            return False

    monkeypatch.setattr(
        observability_metrics,
        "get_memory_runtime_metrics_registry",
        lambda: _BrokenRegistry(),
    )
    admin_gate = Mock()
    monkeypatch.setattr(diagnostics_routes, "require_admin", admin_gate)
    app = FastAPI()

    @app.middleware("http")
    async def _browser_admin(request, call_next):
        request.state.api_token = False
        request.state.api_token_scopes = []
        request.state.current_user = "synthetic-admin"
        return await call_next(request)

    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/diagnostics/runtime-metrics"
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to retrieve runtime metrics"}
    assert "synthetic registry failure" not in response.text
    admin_gate.assert_called_once()


def test_asset_validators_reject_public_secret_and_retention_mutations():
    prometheus_validator = _load_module(
        "gro12_prometheus_validator", PROMETHEUS_ROOT / "validate_assets.py"
    )
    grafana_validator = _load_module(
        "gro12_grafana_validator", GRAFANA_ROOT / "validate_assets.py"
    )
    prometheus = yaml.safe_load(
        (PROMETHEUS_ROOT / "prometheus.yml").read_text(encoding="utf-8")
    )
    grafana_compose = yaml.safe_load(
        (GRAFANA_ROOT / "compose.yml").read_text(encoding="utf-8")
    )
    datasource = yaml.safe_load(
        (GRAFANA_ROOT / "provisioning" / "datasources" / "prometheus.yml").read_text(
            encoding="utf-8"
        )
    )
    provider = yaml.safe_load(
        (GRAFANA_ROOT / "provisioning" / "dashboards" / "memory.yml").read_text(
            encoding="utf-8"
        )
    )

    bad_prometheus = deepcopy(prometheus)
    bad_prometheus["storage"]["tsdb"]["retention"] = {"time": "365d", "size": "50GB"}
    bad_prometheus["scrape_configs"][0]["authorization"] = {
        "type": "Bearer",
        "credentials": "SYNTHETIC_NOT_A_SECRET",
    }
    prometheus_errors: list[str] = []
    prometheus_validator._validate_prometheus(bad_prometheus, prometheus_errors)
    assert "prometheus:retention_time_must_be_30d" in prometheus_errors
    assert "prometheus:retention_size_must_be_5GB" in prometheus_errors
    assert "prometheus:credentials_file_mismatch" in prometheus_errors
    assert "prometheus:inline_credentials_forbidden" in prometheus_errors

    bad_compose = deepcopy(grafana_compose)
    bad_compose["services"]["grafana"]["ports"] = ["0.0.0.0:3000:3000"]
    compose_errors: list[str] = []
    grafana_validator._validate_compose(bad_compose, compose_errors)
    assert "compose:binding_must_be_loopback_3000" in compose_errors

    bad_datasource = deepcopy(datasource)
    bad_datasource["datasources"][0]["url"] = "http://192.0.2.10:9090"
    bad_datasource["datasources"][0]["secureJsonData"] = {
        "httpHeaderValue1": "SYNTHETIC_NOT_A_SECRET"
    }
    provisioning_errors: list[str] = []
    grafana_validator._validate_provisioning(
        bad_datasource, provider, provisioning_errors
    )
    assert "datasource:url_mismatch" in provisioning_errors
    assert "datasource:embedded_secret_forbidden" in provisioning_errors


def test_live_asset_reports_remain_secret_free_default_off_and_bounded():
    prometheus_report = _load_module(
        "gro12_prometheus_live_report", PROMETHEUS_ROOT / "validate_assets.py"
    ).validate_assets()
    grafana_report = _load_module(
        "gro12_grafana_live_report", GRAFANA_ROOT / "validate_assets.py"
    ).validate_assets()

    assert prometheus_report["valid"] is True, prometheus_report["errors"]
    assert prometheus_report["prometheus"]["retention_time"] == "30d"
    assert prometheus_report["prometheus"]["retention_size"] == "5GB"
    assert prometheus_report["secret_file_present"] is False
    assert prometheus_report["live_actions_performed"] is False
    assert grafana_report["valid"] is True, grafana_report["errors"]
    assert grafana_report["provisioning"]["datasource_url"] == "$PROMETHEUS_URL"
    assert grafana_report["secret_file_present"] is False
    assert grafana_report["live_actions_performed"] is False
