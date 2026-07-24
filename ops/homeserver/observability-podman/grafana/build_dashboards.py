"""Build deterministic, provisioned Grafana dashboards for Memory observability."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


ASSET_ROOT = Path(__file__).resolve().parent
DASHBOARD_ROOT = ASSET_ROOT / "dashboards"
DATASOURCE = {"type": "prometheus", "uid": "odysseus-prometheus"}
SCHEMA_VERSION = 41


def _target(
    expression: str,
    legend: str,
    ref_id: str = "A",
    *,
    instant: bool = False,
    table: bool = False,
) -> dict[str, Any]:
    target: dict[str, Any] = {
        "datasource": DATASOURCE,
        "editorMode": "code",
        "expr": expression,
        "legendFormat": legend,
        "range": not instant,
        "refId": ref_id,
    }
    if instant:
        target["instant"] = True
    if table:
        target["format"] = "table"
    return target


def _thresholds(warning: float | None, critical: float | None) -> dict[str, Any]:
    if warning is not None and critical is not None and critical < warning:
        return {
            "mode": "absolute",
            "steps": [
                {"color": "red", "value": None},
                {"color": "orange", "value": critical},
                {"color": "green", "value": warning},
            ],
        }
    steps: list[dict[str, Any]] = [{"color": "green", "value": None}]
    if warning is not None:
        steps.append({"color": "orange", "value": warning})
    if critical is not None:
        steps.append({"color": "red", "value": critical})
    return {"mode": "absolute", "steps": steps}


def _panel(
    *,
    panel_id: int,
    title: str,
    description: str,
    panel_type: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Iterable[dict[str, Any]],
    unit: str = "short",
    decimals: int | None = None,
    warning: float | None = None,
    critical: float | None = None,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "color": {"mode": "palette-classic"},
        "mappings": [],
        "thresholds": _thresholds(warning, critical),
        "unit": unit,
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    options: dict[str, Any]
    if panel_type == "stat":
        options = {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
        }
    elif panel_type == "table":
        options = {
            "cellHeight": "sm",
            "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
            "showHeader": True,
        }
    else:
        options = {
            "legend": {"calcs": ["lastNotNull"], "displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        }
    return {
        "datasource": DATASOURCE,
        "description": description,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "id": panel_id,
        "options": options,
        "targets": list(targets),
        "title": title,
        "type": panel_type,
    }


def _dashboard(uid: str, title: str, description: str, panels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "annotations": {"list": []},
        "description": description,
        "editable": False,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": SCHEMA_VERSION,
        "tags": ["odysseus", "memory", "private-observability"],
        "templating": {"list": []},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "monday",
    }


def _memory_overview() -> dict[str, Any]:
    panels = [
        _panel(panel_id=1, title="Metrics target", description="Private token-scoped Odysseus scrape health.", panel_type="stat", x=0, y=0, w=4, h=5, targets=[_target('up{job="odysseus-memory"}', "target", instant=True)], warning=1, critical=0.5),
        _panel(panel_id=2, title="Query p95", description="Five-minute successful-query p95. Alerting requires at least 30 samples in 15 minutes.", panel_type="stat", x=4, y=0, w=5, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="query"}', "query", instant=True)], unit="s", warning=0.5, critical=0.75),
        _panel(panel_id=3, title="Memory status p95", description="Five-minute successful status p95. Alerting requires at least 30 samples in 15 minutes.", panel_type="stat", x=9, y=0, w=5, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="memory_status"}', "memory status", instant=True)], unit="s", warning=0.75, critical=1.0),
        _panel(panel_id=4, title="Raptor status p95", description="Five-minute successful RaptorGraph status p95. Alerting requires at least 30 samples in 15 minutes.", panel_type="stat", x=14, y=0, w=5, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="raptor_status"}', "raptor status", instant=True)], unit="s", warning=0.25, critical=0.5),
        _panel(panel_id=5, title="Artifact age", description="Oldest reported RaptorGraph artifact age.", panel_type="stat", x=19, y=0, w=5, h=5, targets=[_target("job:odysseus_raptor_artifact_age_seconds:max", "artifact", instant=True)], unit="s", warning=43200, critical=86400),
        _panel(panel_id=6, title="Successful latency p95", description="Five-minute p95 by bounded operation label; content labels are never collected.", panel_type="timeseries", x=0, y=5, w=16, h=9, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation=~"query|memory_status|raptor_status"}', "{{operation}}")], unit="s"),
        _panel(panel_id=7, title="Operation rate by outcome", description="Five-minute operation throughput split by typed outcome.", panel_type="timeseries", x=16, y=5, w=8, h=9, targets=[_target("operation:odysseus_memory_operations:rate5m", "{{operation}} · {{outcome}}")], unit="ops"),
        _panel(panel_id=8, title="Error ratio", description="Five-minute error ratio by operation. Missing traffic remains no-data, not success.", panel_type="timeseries", x=0, y=14, w=12, h=8, targets=[_target("operation:odysseus_memory_operation_error_ratio:rate5m", "{{operation}}")], unit="percentunit", warning=0.02, critical=0.05),
        _panel(panel_id=9, title="Worker queue depth", description="Maximum active queue depth by bounded operation.", panel_type="timeseries", x=12, y=14, w=6, h=8, targets=[_target("operation:odysseus_memory_worker_queue_depth:max", "{{operation}}")]),
        _panel(panel_id=10, title="Exporter render p95", description="Exporter self-observation for bounded scrape rendering cost.", panel_type="timeseries", x=18, y=14, w=6, h=8, targets=[_target("job:odysseus_metrics_render_duration_seconds:p95_5m", "render")], unit="s"),
    ]
    return _dashboard("odysseus-memory-overview", "Memory Overview", "Calm control-room overview of private Memory and RaptorGraph runtime health.", panels)


def _query_waterfall() -> dict[str, Any]:
    panels = [
        _panel(panel_id=1, title="Query p50", description="Successful query median over five minutes.", panel_type="stat", x=0, y=0, w=6, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p50_5m{operation="query"}', "p50", instant=True)], unit="s", warning=0.25, critical=0.5),
        _panel(panel_id=2, title="Query p95", description="Successful query p95; the alert gate requires at least 30 samples in 15 minutes.", panel_type="stat", x=6, y=0, w=6, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="query"}', "p95", instant=True)], unit="s", warning=0.5, critical=0.75),
        _panel(panel_id=3, title="Query p99", description="Successful query p99 over five minutes.", panel_type="stat", x=12, y=0, w=6, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p99_5m{operation="query"}', "p99", instant=True)], unit="s", warning=0.75, critical=1.0),
        _panel(panel_id=4, title="Queue depth", description="Current maximum query worker queue depth.", panel_type="stat", x=18, y=0, w=6, h=5, targets=[_target('operation:odysseus_memory_worker_queue_depth:max{operation="query"}', "query queue", instant=True)], warning=1, critical=4),
        _panel(panel_id=5, title="Query phase p95 waterfall", description="Raw bounded phase histogram reveals retrieve, rank, response, and total latency without content labels.", panel_type="timeseries", x=0, y=5, w=16, h=9, targets=[_target('histogram_quantile(0.95, sum by (le, phase) (rate(odysseus_memory_operation_duration_seconds_bucket{operation="query",outcome="success"}[$__rate_interval])))', "{{phase}}")], unit="s"),
        _panel(panel_id=6, title="Query outcomes", description="Five-minute typed success, error, blocked, and cache outcomes.", panel_type="timeseries", x=16, y=5, w=8, h=9, targets=[_target('operation:odysseus_memory_operations:rate5m{operation="query"}', "{{outcome}}")], unit="ops"),
        _panel(panel_id=7, title="Query percentile spread", description="Recorded percentiles use successful total-phase observations only.", panel_type="timeseries", x=0, y=14, w=12, h=8, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p50_5m{operation="query"}', "p50", "A"), _target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="query"}', "p95", "B"), _target('operation:odysseus_memory_operation_duration_seconds:p99_5m{operation="query"}', "p99", "C")], unit="s"),
        _panel(panel_id=8, title="Event-loop lag p95", description="Observed Memory event-loop lag. No series means instrumentation has not emitted a sample.", panel_type="timeseries", x=12, y=14, w=12, h=8, targets=[_target('histogram_quantile(0.95, sum by (le, operation) (rate(odysseus_memory_event_loop_lag_seconds_bucket[$__rate_interval])))', "{{operation}}")], unit="s", warning=0.05, critical=0.1),
    ]
    return _dashboard("odysseus-query-waterfall", "Query Waterfall", "Latency and outcome decomposition for the bounded Memory query path.", panels)


def _cache() -> dict[str, Any]:
    panels = [
        _panel(panel_id=1, title="Raptor cache hit ratio", description="Thirty-minute hit ratio; alerting requires at least 20 hit-or-miss requests.", panel_type="stat", x=0, y=0, w=6, h=5, targets=[_target("job:odysseus_raptor_cache_hit_ratio:rate30m", "hit ratio", instant=True)], unit="percentunit", warning=0.6, critical=0.4),
        _panel(panel_id=2, title="Raptor request rate", description="Thirty-minute hit-or-miss request rate.", panel_type="stat", x=6, y=0, w=6, h=5, targets=[_target("job:odysseus_raptor_cache_requests:rate30m", "requests", instant=True)], unit="reqps"),
        _panel(panel_id=3, title="Query cache entries", description="Bounded process-local query cache; hard limit is 512 entries.", panel_type="stat", x=12, y=0, w=6, h=5, targets=[_target("job:odysseus_query_cache_entries:max", "entries", instant=True)], warning=384, critical=512),
        _panel(panel_id=4, title="Query cache bytes", description="Bounded process-local query cache; hard limit is 8 MiB.", panel_type="stat", x=18, y=0, w=6, h=5, targets=[_target("job:odysseus_query_cache_bytes:max", "bytes", instant=True)], unit="bytes", warning=6291456, critical=8388608),
        _panel(panel_id=5, title="Raptor cache decisions", description="Five-minute cache-result rate using only the bounded cache_result label.", panel_type="timeseries", x=0, y=5, w=12, h=9, targets=[_target("sum by (cache_result) (rate(odysseus_raptor_cache_requests_total[5m]))", "{{cache_result}}")], unit="reqps"),
        _panel(panel_id=6, title="Cache hit ratio trend", description="Thirty-minute SLO signal. Sparse traffic is guarded by the alert minimum-sample contract.", panel_type="timeseries", x=12, y=5, w=12, h=9, targets=[_target("job:odysseus_raptor_cache_hit_ratio:rate30m", "hit ratio")], unit="percentunit", warning=0.6, critical=0.4),
        _panel(panel_id=7, title="Query cache occupancy", description="Query cache occupancy and byte footprint; both series are bounded gauges.", panel_type="timeseries", x=0, y=14, w=12, h=8, targets=[_target("odysseus_query_cache_entries", "entries", "A"), _target("odysseus_query_cache_bytes / 1048576", "MiB", "B")]),
        _panel(panel_id=8, title="Raptor cache occupancy", description="RaptorGraph cache entry gauge by bounded cache-kind label when present.", panel_type="timeseries", x=12, y=14, w=12, h=8, targets=[_target("odysseus_raptor_cache_entries", "{{cache_kind}}")]),
    ]
    return _dashboard("odysseus-cache", "Cache", "Cache effectiveness, demand, and hard process-local bounds.", panels)


def _rebuild_resource() -> dict[str, Any]:
    panels = [
        _panel(panel_id=1, title="Rebuild total p95", description="Fifteen-minute successful rebuild total-phase p95.", panel_type="stat", x=0, y=0, w=6, h=5, targets=[_target('phase:odysseus_raptor_rebuild_duration_seconds:p95_15m{phase="total"}', "total", instant=True)], unit="s", warning=60, critical=180),
        _panel(panel_id=2, title="Sources processed", description="Latest bounded source count; no path or source identity is exported.", panel_type="stat", x=6, y=0, w=6, h=5, targets=[_target("max(odysseus_raptor_rebuild_sources)", "sources", instant=True)]),
        _panel(panel_id=3, title="Rebuild throughput", description="Latest source throughput reported by the rebuild worker.", panel_type="stat", x=12, y=0, w=6, h=5, targets=[_target("max(odysseus_raptor_rebuild_sources_per_second)", "sources/s", instant=True)], unit="ops"),
        _panel(panel_id=4, title="Artifact age", description="Maximum RaptorGraph artifact age; blocked status plus age over 24 hours alerts.", panel_type="stat", x=18, y=0, w=6, h=5, targets=[_target("job:odysseus_raptor_artifact_age_seconds:max", "artifact", instant=True)], unit="s", warning=43200, critical=86400),
        _panel(panel_id=5, title="Rebuild phase p95", description="Fifteen-minute phase-level p95 for deterministic rebuild decomposition.", panel_type="timeseries", x=0, y=5, w=16, h=9, targets=[_target("phase:odysseus_raptor_rebuild_duration_seconds:p95_15m", "{{phase}}")], unit="s"),
        _panel(panel_id=6, title="Rebuild outcomes", description="Five-minute rebuild outcome rate. Rebuild errors are never maintenance-suppressed.", panel_type="timeseries", x=16, y=5, w=8, h=9, targets=[_target('operation:odysseus_memory_operations:rate5m{operation="rebuild"}', "{{outcome}}")], unit="ops"),
        _panel(panel_id=7, title="RSS delta", description="Resident-memory delta reported by completed rebuilds.", panel_type="timeseries", x=0, y=14, w=8, h=8, targets=[_target("odysseus_raptor_rebuild_rss_delta_bytes", "RSS delta")], unit="bytes"),
        _panel(panel_id=8, title="Rebuild resource pressure", description="CPU-seconds and peak resident memory use bounded worker observations.", panel_type="timeseries", x=8, y=14, w=10, h=8, targets=[_target("odysseus_raptor_rebuild_cpu_seconds", "CPU seconds", "A"), _target("odysseus_raptor_rebuild_peak_rss_bytes / 1048576", "peak RSS MiB", "B")]),
        _panel(panel_id=9, title="Maintenance queue", description="Rebuild and automation queue depth drives the recent-maintenance guard for latency and cache alerts.", panel_type="timeseries", x=18, y=14, w=6, h=8, targets=[_target('operation:odysseus_memory_worker_queue_depth:max{operation=~"rebuild|automation"}', "{{operation}}")]),
    ]
    return _dashboard("odysseus-rebuild-resource", "Rebuild & Resource", "RaptorGraph rebuild latency, outcomes, throughput, and resource pressure.", panels)


def _slo_alerts() -> dict[str, Any]:
    panels = [
        _panel(panel_id=1, title="Query SLO", description="500 ms p95 gate with at least 30 successful samples in 15 minutes and maintenance suppression.", panel_type="stat", x=0, y=0, w=5, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="query"}', "query", instant=True)], unit="s", warning=0.5, critical=0.75),
        _panel(panel_id=2, title="Memory status SLO", description="750 ms p95 gate with at least 30 successful samples in 15 minutes and maintenance suppression.", panel_type="stat", x=5, y=0, w=5, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="memory_status"}', "status", instant=True)], unit="s", warning=0.75, critical=1.0),
        _panel(panel_id=3, title="Raptor status SLO", description="250 ms p95 gate with at least 30 successful samples in 15 minutes and maintenance suppression.", panel_type="stat", x=10, y=0, w=5, h=5, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation="raptor_status"}', "raptor", instant=True)], unit="s", warning=0.25, critical=0.5),
        _panel(panel_id=4, title="Cache SLO", description="60 percent hit-ratio gate with at least 20 requests in 30 minutes and maintenance suppression.", panel_type="stat", x=15, y=0, w=5, h=5, targets=[_target("job:odysseus_raptor_cache_hit_ratio:rate30m", "cache", instant=True)], unit="percentunit", warning=0.6, critical=0.4),
        _panel(panel_id=5, title="Dropped samples", description="Any exporter sample rejection is a critical contract signal.", panel_type="stat", x=20, y=0, w=4, h=5, targets=[_target("sum(increase(odysseus_metrics_samples_dropped_total[5m]))", "dropped", instant=True)], critical=1),
        _panel(panel_id=6, title="Active Memory alerts", description="Prometheus alert state only; external notification delivery remains intentionally unconfigured.", panel_type="table", x=0, y=5, w=24, h=8, targets=[_target('ALERTS{alertstate="firing",alertname=~"Odysseus.*"}', "{{alertname}}", instant=True, table=True)]),
        _panel(panel_id=7, title="Latency SLO trend", description="P95 trends retain no user, vault, source, path, or query labels.", panel_type="timeseries", x=0, y=13, w=14, h=9, targets=[_target('operation:odysseus_memory_operation_duration_seconds:p95_5m{operation=~"query|memory_status|raptor_status"}', "{{operation}}")], unit="s"),
        _panel(panel_id=8, title="Cache SLO trend", description="Thirty-minute cache ratio and minimum-sample request rate shown together for operator interpretation.", panel_type="timeseries", x=14, y=13, w=10, h=9, targets=[_target("job:odysseus_raptor_cache_hit_ratio:rate30m", "hit ratio", "A"), _target("job:odysseus_raptor_cache_requests:rate30m", "request rate", "B")]),
    ]
    return _dashboard("odysseus-slo-alerts", "SLO & Alerts", "Minimum-sample-aware SLO signals and local Prometheus alert state.", panels)


def _unified_source_index() -> dict[str, Any]:
    panels = [
        _panel(panel_id=1, title="Metrics target", description="Private token-scoped Odysseus scrape health; no productive source read is triggered.", panel_type="stat", x=0, y=0, w=6, h=5, targets=[_target('up{job="odysseus-memory"}', "target", instant=True)], warning=1, critical=0.5),
        _panel(panel_id=2, title="USI operation p95", description="Five-minute p95 across bounded USI operations and successful total-phase observations.", panel_type="stat", x=6, y=0, w=6, h=5, targets=[_target('histogram_quantile(0.95, sum by (le) (rate(odysseus_usi_operation_duration_seconds_bucket{phase="total",outcome="success"}[5m])))', "p95", instant=True)], unit="s"),
        _panel(panel_id=3, title="Queue depth", description="Maximum process-local USI queue depth by bounded operation.", panel_type="stat", x=12, y=0, w=6, h=5, targets=[_target("max(odysseus_usi_queue_depth)", "queue", instant=True)], warning=1, critical=8),
        _panel(panel_id=4, title="Stale projections", description="Current aggregate stale-projection count without source or path identity.", panel_type="stat", x=18, y=0, w=6, h=5, targets=[_target("max(odysseus_usi_stale_projections)", "stale", instant=True)], warning=1, critical=10),
        _panel(panel_id=5, title="Records by kind", description="Aggregate USI record counts use only the closed record_kind label.", panel_type="timeseries", x=0, y=5, w=12, h=9, targets=[_target("max by (record_kind) (odysseus_usi_records)", "{{record_kind}}")]),
        _panel(panel_id=6, title="Operation rate by outcome", description="Five-minute USI throughput split only by bounded operation and outcome.", panel_type="timeseries", x=12, y=5, w=12, h=9, targets=[_target("sum by (operation, outcome) (rate(odysseus_usi_operations_total[5m]))", "{{operation}} · {{outcome}}")], unit="ops"),
        _panel(panel_id=7, title="Operation and phase p95", description="Five-minute successful latency by the closed operation and phase enums.", panel_type="timeseries", x=0, y=14, w=16, h=8, targets=[_target('histogram_quantile(0.95, sum by (le, operation, phase) (rate(odysseus_usi_operation_duration_seconds_bucket{outcome="success"}[$__rate_interval])))', "{{operation}} · {{phase}}")], unit="s"),
        _panel(panel_id=8, title="Error and cancellation ratio", description="Five-minute non-success ratio across bounded USI outcomes; no traffic remains no-data.", panel_type="timeseries", x=16, y=14, w=8, h=8, targets=[_target('sum by (operation) (rate(odysseus_usi_operations_total{outcome=~"error|cancelled"}[5m])) / sum by (operation) (rate(odysseus_usi_operations_total[5m]))', "{{operation}}")], unit="percentunit", warning=0.02, critical=0.05),
    ]
    return _dashboard(
        "odysseus-unified-source-index",
        "Unified Source Index",
        "Content-free health, latency, queue, projection, and aggregate record diagnostics for the default-off Unified Source Index.",
        panels,
    )


def build_payloads() -> dict[str, dict[str, Any]]:
    return {
        "cache.json": _cache(),
        "memory-overview.json": _memory_overview(),
        "query-waterfall.json": _query_waterfall(),
        "rebuild-resource.json": _rebuild_resource(),
        "slo-alerts.json": _slo_alerts(),
        "unified-source-index.json": _unified_source_index(),
    }


def rendered_payloads() -> dict[str, str]:
    return {
        name: json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        for name, payload in build_payloads().items()
    }


def write_dashboards(*, check: bool = False) -> list[str]:
    rendered = rendered_payloads()
    changed: list[str] = []
    for name, text in rendered.items():
        path = DASHBOARD_ROOT / name
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            changed.append(name)
            if not check:
                DASHBOARD_ROOT.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated dashboards differ")
    args = parser.parse_args(argv)
    changed = write_dashboards(check=args.check)
    if args.check and changed:
        print("DASHBOARDS_OUT_OF_DATE " + " ".join(changed))
        return 1
    print("DASHBOARDS_CURRENT" if not changed else "DASHBOARDS_WRITTEN " + " ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
