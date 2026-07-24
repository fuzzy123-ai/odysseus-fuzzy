"""Offline validation for default-off Odysseus Grafana and Memory alert assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


ASSET_ROOT = Path(__file__).resolve().parent
OBSERVABILITY_ROOT = ASSET_ROOT.parent
COMPOSE_PATH = ASSET_ROOT / "compose.yml"
UNIT_PATH = ASSET_ROOT / "grafana-podman.service"
DATASOURCE_PATH = ASSET_ROOT / "provisioning" / "datasources" / "prometheus.yml"
PROVIDER_PATH = ASSET_ROOT / "provisioning" / "dashboards" / "memory.yml"
DASHBOARD_ROOT = ASSET_ROOT / "dashboards"
ALERT_RULES_PATH = OBSERVABILITY_ROOT / "prometheus" / "rules" / "memory-alerts.rules.yml"
ALERT_TESTS_PATH = OBSERVABILITY_ROOT / "prometheus" / "rules" / "memory-alerts.test.yml"
SECRET_PATH = ASSET_ROOT / "secrets" / "grafana_admin_password"

SCHEMA = "odysseus.homeserver.grafana_assets.v1"
GRAFANA_IMAGE = "docker.io/grafana/grafana:13.1.0"
DATASOURCE_UID = "odysseus-prometheus"
DATA_VOLUME = "odysseus-grafana-data-v1"
ACTIVATION_MARKER = "%h/.config/odysseus-observability/GRAFANA_ACTIVATION_GO"

EXPECTED_DASHBOARDS = {
    "cache.json": ("odysseus-cache", "Cache", 8),
    "memory-overview.json": ("odysseus-memory-overview", "Memory Overview", 10),
    "query-waterfall.json": ("odysseus-query-waterfall", "Query Waterfall", 8),
    "rebuild-resource.json": ("odysseus-rebuild-resource", "Rebuild & Resource", 9),
    "slo-alerts.json": ("odysseus-slo-alerts", "SLO & Alerts", 8),
    "unified-source-index.json": (
        "odysseus-unified-source-index",
        "Unified Source Index",
        8,
    ),
}
EXPECTED_ALERTS = frozenset(
    {
        "OdysseusMemoryQueryP95High",
        "OdysseusMemoryStatusP95High",
        "OdysseusRaptorStatusP95High",
        "OdysseusMemoryEventLoopLagHigh",
        "OdysseusRaptorRebuildError",
        "OdysseusRaptorRebuildErrorsRepeated",
        "OdysseusRaptorCacheHitRateLow",
        "OdysseusQueryCacheEntriesHigh",
        "OdysseusQueryCacheBytesHigh",
        "OdysseusMetricsTargetDown",
        "OdysseusRaptorArtifactAgeHigh",
        "OdysseusMetricsSamplesDropped",
    }
)
ALERT_CONTRACTS = {
    "OdysseusMemoryQueryP95High": ("15m", ("> 0.5", "[15m]", ">= 30", "job:odysseus_memory_maintenance_recent:bool == 0")),
    "OdysseusMemoryStatusP95High": ("15m", ("> 0.75", "[15m]", ">= 30", "job:odysseus_memory_maintenance_recent:bool == 0")),
    "OdysseusRaptorStatusP95High": ("15m", ("> 0.25", "[15m]", ">= 30", "job:odysseus_memory_maintenance_recent:bool == 0")),
    "OdysseusMemoryEventLoopLagHigh": ("0m", ('le="0.1"', "[5m]", "> 0")),
    "OdysseusRaptorRebuildError": ("5m", ('operation="rebuild",outcome="error"', "[5m]", "> 0")),
    "OdysseusRaptorRebuildErrorsRepeated": ("0m", ('operation="rebuild",outcome="error"', "[15m]", ">= 2")),
    "OdysseusRaptorCacheHitRateLow": ("15m", ("< 0.6", "[30m]", ">= 20", "job:odysseus_memory_maintenance_recent:bool == 0")),
    "OdysseusQueryCacheEntriesHigh": ("0m", ("odysseus_query_cache_entries > 512",)),
    "OdysseusQueryCacheBytesHigh": ("0m", ("odysseus_query_cache_bytes > 8388608",)),
    "OdysseusMetricsTargetDown": ("2m", ('up{job="odysseus-memory"} == 0',)),
    "OdysseusRaptorArtifactAgeHigh": ("15m", ("> 86400", 'operation="raptor_status",outcome="blocked"', "[15m]", ">= 1")),
    "OdysseusMetricsSamplesDropped": ("0m", ("odysseus_metrics_samples_dropped_total[5m]", "> 0")),
}
MAINTENANCE_SUPPRESSED = frozenset(
    {
        "OdysseusMemoryQueryP95High",
        "OdysseusMemoryStatusP95High",
        "OdysseusRaptorStatusP95High",
        "OdysseusRaptorCacheHitRateLow",
    }
)
REBUILD_ALERTS = frozenset(
    {"OdysseusRaptorRebuildError", "OdysseusRaptorRebuildErrorsRepeated"}
)
FORBIDDEN_PROMQL_LABELS = frozenset(
    {"owner", "user", "vault", "path", "source", "query", "prompt", "model"}
)
PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/](?:Users|Documents|Desktop)[\\/]", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_.-]+/"),
)
PRIVATE_NETWORK_PATTERN = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
SECRET_LITERAL_PATTERN = re.compile(
    r"(?:password|token|secret)\s*[=:]\s*[A-Za-z0-9_+/.=-]{12,}", re.IGNORECASE
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _load_yaml(path: Path, errors: list[str]) -> Mapping[str, Any]:
    if not path.is_file():
        errors.append(f"missing:{path.relative_to(OBSERVABILITY_ROOT)}")
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"invalid_yaml:{path.name}:{type(exc).__name__}")
        return {}
    if not isinstance(payload, Mapping):
        errors.append(f"yaml_root_not_mapping:{path.name}")
        return {}
    return payload


def _balanced_promql(expression: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in expression:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def _mount_by_target(service: Mapping[str, Any], target: str) -> Mapping[str, Any]:
    for mount in _sequence(service.get("volumes")):
        if isinstance(mount, Mapping) and mount.get("target") == target:
            return mount
    return {}


def _validate_compose(payload: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    services = _mapping(payload.get("services"))
    if set(services) != {"grafana"}:
        errors.append("compose:services_must_be_grafana_only")
    service = _mapping(services.get("grafana"))
    if service.get("image") != GRAFANA_IMAGE:
        errors.append("compose:image_not_pinned_to_contract")
    if service.get("restart") != "no":
        errors.append("compose:restart_must_be_no")
    if service.get("read_only") is not True:
        errors.append("compose:root_filesystem_not_read_only")
    if service.get("privileged") not in (None, False) or service.get("network_mode") == "host":
        errors.append("compose:privilege_boundary_broken")
    if _sequence(service.get("ports")) != ["127.0.0.1:3000:3000"]:
        errors.append("compose:binding_must_be_loopback_3000")
    if _sequence(service.get("cap_drop")) != ["ALL"]:
        errors.append("compose:cap_drop_all_required")
    if "no-new-privileges:true" not in _sequence(service.get("security_opt")):
        errors.append("compose:no_new_privileges_required")

    environment = _mapping(service.get("environment"))
    expected_environment = {
        "PROMETHEUS_URL": "${PROMETHEUS_URL:?set PROMETHEUS_URL in the gated activation environment}",
        "GF_SECURITY_ADMIN_USER": "${GRAFANA_ADMIN_USER:?set GRAFANA_ADMIN_USER in the gated activation environment}",
        "GF_SECURITY_ADMIN_PASSWORD__FILE": "/run/secrets/grafana_admin_password",
        "GF_AUTH_ANONYMOUS_ENABLED": "false",
        "GF_USERS_ALLOW_SIGN_UP": "false",
        "GF_UNIFIED_ALERTING_ENABLED": "false",
        "GF_ANALYTICS_REPORTING_ENABLED": "false",
        "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        "GF_SECURITY_DISABLE_GRAVATAR": "true",
        "GF_PLUGINS_PLUGIN_ADMIN_ENABLED": "false",
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH": "/var/lib/grafana/dashboards/memory-overview.json",
    }
    if dict(environment) != expected_environment:
        errors.append("compose:environment_contract_mismatch")

    health = _mapping(service.get("healthcheck"))
    if "http://127.0.0.1:3000/api/health" not in " ".join(str(value) for value in _sequence(health.get("test"))):
        errors.append("compose:healthcheck_missing")
    if health.get("timeout") != "5s" or health.get("retries") != 3:
        errors.append("compose:healthcheck_not_bounded")

    expected_mounts = {
        "/etc/grafana/provisioning": ("bind", "./provisioning", True),
        "/var/lib/grafana/dashboards": ("bind", "./dashboards", True),
        "/run/secrets/grafana_admin_password": ("bind", "./secrets/grafana_admin_password", True),
        "/var/lib/grafana": ("volume", "grafana-data", None),
    }
    for target, (mount_type, source, read_only) in expected_mounts.items():
        mount = _mount_by_target(service, target)
        if mount.get("type") != mount_type or mount.get("source") != source:
            errors.append(f"compose:mount_mismatch:{target}")
        if read_only is not None and mount.get("read_only") is not read_only:
            errors.append(f"compose:mount_not_read_only:{target}")
    volume = _mapping(_mapping(payload.get("volumes")).get("grafana-data"))
    if volume.get("name") != DATA_VOLUME:
        errors.append("compose:data_volume_not_versioned")
    return {
        "image": service.get("image"),
        "loopback_binding": _sequence(service.get("ports")) == ["127.0.0.1:3000:3000"],
        "data_volume": volume.get("name"),
    }


def _validate_provisioning(
    datasource: Mapping[str, Any], provider: Mapping[str, Any], errors: list[str]
) -> dict[str, Any]:
    datasources = _sequence(datasource.get("datasources"))
    item = _mapping(datasources[0]) if len(datasources) == 1 else {}
    expected = {
        "uid": DATASOURCE_UID,
        "type": "prometheus",
        "access": "proxy",
        "url": "$PROMETHEUS_URL",
        "isDefault": True,
        "editable": False,
    }
    for key, value in expected.items():
        if item.get(key) != value:
            errors.append(f"datasource:{key}_mismatch")
    if item.get("secureJsonData") or item.get("basicAuth"):
        errors.append("datasource:embedded_secret_forbidden")
    json_data = _mapping(item.get("jsonData"))
    if json_data.get("manageAlerts") is not False or json_data.get("timeInterval") != "15s":
        errors.append("datasource:query_contract_mismatch")

    providers = _sequence(provider.get("providers"))
    dashboard_provider = _mapping(providers[0]) if len(providers) == 1 else {}
    options = _mapping(dashboard_provider.get("options"))
    if dashboard_provider.get("folderUid") != "odysseus-memory":
        errors.append("provider:folder_uid_mismatch")
    if dashboard_provider.get("editable") is not False or dashboard_provider.get("disableDeletion") is not True:
        errors.append("provider:immutability_contract_mismatch")
    if options.get("path") != "/var/lib/grafana/dashboards":
        errors.append("provider:path_mismatch")
    return {
        "datasource_uid": item.get("uid"),
        "datasource_url": item.get("url"),
        "dashboard_folder_uid": dashboard_provider.get("folderUid"),
    }


def _overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return not (
        int(left["x"]) + int(left["w"]) <= int(right["x"])
        or int(right["x"]) + int(right["w"]) <= int(left["x"])
        or int(left["y"]) + int(left["h"]) <= int(right["y"])
        or int(right["y"]) + int(right["h"]) <= int(left["y"])
    )


def _validate_dashboards(errors: list[str]) -> dict[str, Any]:
    actual_names = {path.name for path in DASHBOARD_ROOT.glob("*.json")}
    if actual_names != set(EXPECTED_DASHBOARDS):
        errors.append("dashboards:file_set_mismatch")
    all_queries: list[str] = []
    total_panels = 0
    for name, (expected_uid, expected_title, expected_count) in EXPECTED_DASHBOARDS.items():
        path = DASHBOARD_ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            errors.append(f"dashboards:invalid_json:{name}")
            continue
        if text != json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n":
            errors.append(f"dashboards:not_canonical:{name}")
        if payload.get("uid") != expected_uid or payload.get("title") != expected_title:
            errors.append(f"dashboards:identity_mismatch:{name}")
        if payload.get("editable") is not False or payload.get("refresh") != "30s":
            errors.append(f"dashboards:provisioning_contract_mismatch:{name}")
        if payload.get("links"):
            errors.append(f"dashboards:fixed_link_forbidden:{name}")
        panels = _sequence(payload.get("panels"))
        if len(panels) != expected_count:
            errors.append(f"dashboards:panel_count_mismatch:{name}")
        total_panels += len(panels)
        panel_ids: set[int] = set()
        grids: list[Mapping[str, Any]] = []
        for index, panel_value in enumerate(panels):
            panel = _mapping(panel_value)
            panel_id = panel.get("id")
            if not isinstance(panel_id, int) or panel_id in panel_ids:
                errors.append(f"dashboards:panel_id_invalid:{name}:{index}")
            else:
                panel_ids.add(panel_id)
            if _mapping(panel.get("datasource")) != {"type": "prometheus", "uid": DATASOURCE_UID}:
                errors.append(f"dashboards:panel_datasource_mismatch:{name}:{index}")
            grid = _mapping(panel.get("gridPos"))
            if not all(isinstance(grid.get(key), int) for key in ("x", "y", "w", "h")):
                errors.append(f"dashboards:grid_invalid:{name}:{index}")
            elif int(grid["x"]) < 0 or int(grid["w"]) <= 0 or int(grid["x"]) + int(grid["w"]) > 24:
                errors.append(f"dashboards:grid_out_of_bounds:{name}:{index}")
            else:
                grids.append(grid)
            for target_value in _sequence(panel.get("targets")):
                target = _mapping(target_value)
                if _mapping(target.get("datasource")) != {"type": "prometheus", "uid": DATASOURCE_UID}:
                    errors.append(f"dashboards:target_datasource_mismatch:{name}:{index}")
                expression = str(target.get("expr") or "")
                if not expression or not _balanced_promql(expression):
                    errors.append(f"dashboards:promql_unbalanced:{name}:{index}")
                all_queries.append(expression)
                for label in FORBIDDEN_PROMQL_LABELS:
                    if re.search(rf"\b{re.escape(label)}\s*=", expression):
                        errors.append(f"dashboards:private_label:{name}:{label}")
        for index, left in enumerate(grids):
            for right in grids[index + 1 :]:
                if _overlap(left, right):
                    errors.append(f"dashboards:grid_overlap:{name}")
                    break

    query_text = "\n".join(all_queries)
    required_signals = (
        "operation:odysseus_memory_operation_duration_seconds:p95_5m",
        "odysseus_memory_event_loop_lag_seconds_bucket",
        "job:odysseus_raptor_cache_hit_ratio:rate30m",
        "odysseus_query_cache_bytes",
        "phase:odysseus_raptor_rebuild_duration_seconds:p95_15m",
        "odysseus_raptor_rebuild_rss_delta_bytes",
        "odysseus_usi_operation_duration_seconds_bucket",
        "odysseus_usi_queue_depth",
        "odysseus_usi_stale_projections",
        "odysseus_usi_records",
        "odysseus_usi_operations_total",
        'ALERTS{alertstate="firing"',
    )
    for signal in required_signals:
        if signal not in query_text:
            errors.append(f"dashboards:required_signal_missing:{signal}")
    return {
        "dashboard_count": len(actual_names),
        "panel_count": total_panels,
        "query_count": len(all_queries),
    }


def _validate_alerts(
    rules_payload: Mapping[str, Any], tests_payload: Mapping[str, Any], errors: list[str]
) -> dict[str, Any]:
    groups = _sequence(rules_payload.get("groups"))
    group = _mapping(groups[0]) if len(groups) == 1 else {}
    if group.get("name") != "odysseus-memory-alerts" or group.get("interval") != "15s":
        errors.append("alerts:group_contract_mismatch")
    if group.get("limit") != 128:
        errors.append("alerts:series_limit_mismatch")
    rules = _sequence(group.get("rules"))
    records = [_mapping(rule) for rule in rules if _mapping(rule).get("record")]
    alerts = {
        str(_mapping(rule).get("alert")): _mapping(rule)
        for rule in rules
        if _mapping(rule).get("alert")
    }
    if len(records) != 1 or records[0].get("record") != "job:odysseus_memory_maintenance_recent:bool":
        errors.append("alerts:maintenance_record_mismatch")
    if set(alerts) != EXPECTED_ALERTS:
        errors.append("alerts:alert_set_mismatch")
    for name, (expected_for, fragments) in ALERT_CONTRACTS.items():
        alert = alerts.get(name, {})
        expression = str(alert.get("expr") or "")
        if alert.get("for") != expected_for:
            errors.append(f"alerts:for_mismatch:{name}")
        if not _balanced_promql(expression):
            errors.append(f"alerts:promql_unbalanced:{name}")
        for fragment in fragments:
            if fragment not in expression:
                errors.append(f"alerts:contract_fragment_missing:{name}:{fragment}")
        labels = _mapping(alert.get("labels"))
        if labels.get("severity") not in {"warning", "critical"} or not labels.get("component"):
            errors.append(f"alerts:labels_invalid:{name}")
        for label in FORBIDDEN_PROMQL_LABELS:
            if re.search(rf"\b{re.escape(label)}\s*=", expression):
                errors.append(f"alerts:private_label:{name}:{label}")
    for name in MAINTENANCE_SUPPRESSED:
        if "job:odysseus_memory_maintenance_recent:bool == 0" not in str(alerts.get(name, {}).get("expr") or ""):
            errors.append(f"alerts:maintenance_suppression_missing:{name}")
    for name in REBUILD_ALERTS:
        if "maintenance" in str(alerts.get(name, {}).get("expr") or ""):
            errors.append(f"alerts:rebuild_must_not_be_suppressed:{name}")

    tested_names: list[str] = []
    for test_case in _sequence(tests_payload.get("tests")):
        for test in _sequence(_mapping(test_case).get("alert_rule_test")):
            entry = _mapping(test)
            name = entry.get("alertname")
            if isinstance(name, str):
                tested_names.append(name)
            if entry.get("exp_alerts") != []:
                errors.append(f"alerts:no_data_expectation_mismatch:{name}")
    if set(tested_names) != EXPECTED_ALERTS or len(tested_names) != len(EXPECTED_ALERTS):
        errors.append("alerts:promtool_test_coverage_mismatch")
    if set(_sequence(tests_payload.get("rule_files"))) != {
        "memory-recording.rules.yml",
        "memory-alerts.rules.yml",
    }:
        errors.append("alerts:promtool_rule_files_mismatch")
    return {
        "alert_count": len(alerts),
        "promtool_test_count": len(tested_names),
        "maintenance_suppressed_count": len(MAINTENANCE_SUPPRESSED),
        "rebuild_alerts_unsuppressed": all(
            "maintenance" not in str(alerts.get(name, {}).get("expr") or "")
            for name in REBUILD_ALERTS
        ),
    }


def _validate_unit(errors: list[str]) -> dict[str, Any]:
    if not UNIT_PATH.is_file():
        errors.append("missing:grafana-podman.service")
        return {}
    text = UNIT_PATH.read_text(encoding="utf-8")
    required = (
        f"ConditionPathExists={ACTIVATION_MARKER}",
        "EnvironmentFile=%h/.config/odysseus-observability/grafana.env",
        "ExecStartPre=/usr/bin/test -r ./secrets/grafana_admin_password",
        "ExecStart=/usr/bin/podman-compose -f compose.yml up -d",
        "ExecStop=-/usr/bin/podman-compose -f compose.yml stop",
        "Restart=no",
    )
    for value in required:
        if value not in text:
            errors.append(f"unit:missing:{value.split('=', 1)[0]}")
    for forbidden in ("sudo", "--privileged", "--network=host", "enable --now"):
        if forbidden in text:
            errors.append(f"unit:forbidden:{forbidden}")
    return {"activation_marker": ACTIVATION_MARKER, "installed": False, "started": False}


def _validate_privacy(paths: Iterable[Path], errors: list[str]) -> None:
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(OBSERVABILITY_ROOT)
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(text):
                errors.append(f"privacy:private_path:{relative}")
        if PRIVATE_NETWORK_PATTERN.search(text):
            errors.append(f"privacy:private_network:{relative}")
        if SECRET_LITERAL_PATTERN.search(text):
            errors.append(f"privacy:credential_literal:{relative}")


def validate_assets() -> dict[str, Any]:
    errors: list[str] = []
    compose = _load_yaml(COMPOSE_PATH, errors)
    datasource = _load_yaml(DATASOURCE_PATH, errors)
    provider = _load_yaml(PROVIDER_PATH, errors)
    alert_rules = _load_yaml(ALERT_RULES_PATH, errors)
    alert_tests = _load_yaml(ALERT_TESTS_PATH, errors)
    compose_summary = _validate_compose(compose, errors)
    provisioning_summary = _validate_provisioning(datasource, provider, errors)
    dashboard_summary = _validate_dashboards(errors)
    alert_summary = _validate_alerts(alert_rules, alert_tests, errors)
    unit_summary = _validate_unit(errors)
    _validate_privacy(
        (
            COMPOSE_PATH,
            UNIT_PATH,
            DATASOURCE_PATH,
            PROVIDER_PATH,
            ALERT_RULES_PATH,
            ALERT_TESTS_PATH,
            ASSET_ROOT / "README.md",
            ASSET_ROOT / "secrets" / "README.md",
            *sorted(DASHBOARD_ROOT.glob("*.json")),
        ),
        errors,
    )
    if SECRET_PATH.exists():
        errors.append("secrets:live_password_file_must_be_absent")
    return {
        "schema": SCHEMA,
        "valid": not errors,
        "errors": tuple(sorted(set(errors))),
        "compose": compose_summary,
        "provisioning": provisioning_summary,
        "dashboards": dashboard_summary,
        "alerts": alert_summary,
        "unit": unit_summary,
        "secret_file_present": SECRET_PATH.exists(),
        "live_actions_performed": False,
        "host_commands_performed": False,
        "network_io_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    args = parser.parse_args(argv)
    report = validate_assets()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["valid"]:
        print("GRAFANA_ASSETS_VALID")
    else:
        for error in report["errors"]:
            print(f"ERROR {error}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
