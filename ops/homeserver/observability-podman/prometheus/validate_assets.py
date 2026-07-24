"""Offline validation for the default-off Odysseus Prometheus assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


ASSET_ROOT = Path(__file__).resolve().parent
COMPOSE_PATH = ASSET_ROOT / "compose.yml"
PROMETHEUS_PATH = ASSET_ROOT / "prometheus.yml"
RULES_PATH = ASSET_ROOT / "rules" / "memory-recording.rules.yml"
UNIT_PATH = ASSET_ROOT / "prometheus-podman.service"
SECRET_PATH = ASSET_ROOT / "secrets" / "odysseus_metrics_token"

SCHEMA = "odysseus.homeserver.prometheus_assets.v1"
PROMETHEUS_IMAGE = "quay.io/prometheus/prometheus:v3.12.0"
DATA_VOLUME = "odysseus-prometheus-data-v1"
ACTIVATION_MARKER = "%h/.config/odysseus-observability/ACTIVATION_GO"

EXPECTED_RECORDING_RULES = frozenset(
    {
        "operation:odysseus_memory_operation_duration_seconds:p50_5m",
        "operation:odysseus_memory_operation_duration_seconds:p95_5m",
        "operation:odysseus_memory_operation_duration_seconds:p99_5m",
        "operation:odysseus_memory_operations:rate5m",
        "operation:odysseus_memory_operation_error_ratio:rate5m",
        "job:odysseus_raptor_cache_requests:rate30m",
        "job:odysseus_raptor_cache_hit_ratio:rate30m",
        "phase:odysseus_raptor_rebuild_duration_seconds:p95_15m",
        "job:odysseus_raptor_artifact_age_seconds:max",
        "operation:odysseus_memory_worker_queue_depth:max",
        "job:odysseus_query_cache_entries:max",
        "job:odysseus_query_cache_bytes:max",
        "job:odysseus_metrics_render_duration_seconds:p95_5m",
    }
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
TOKEN_LITERAL_PATTERN = re.compile(
    r"(?:authorization:\s*Bearer\s+\S+|credentials:\s*\S+|"
    r"(?:token|password|secret)\s*[=:]\s*[A-Za-z0-9_+/.=-]{12,})",
    re.IGNORECASE,
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _load_yaml(path: Path, errors: list[str]) -> Mapping[str, Any]:
    if not path.is_file():
        errors.append(f"missing:{path.relative_to(ASSET_ROOT)}")
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"invalid_yaml:{path.relative_to(ASSET_ROOT)}:{type(exc).__name__}")
        return {}
    if not isinstance(payload, Mapping):
        errors.append(f"yaml_root_not_mapping:{path.relative_to(ASSET_ROOT)}")
        return {}
    return payload


def _mount_by_target(service: Mapping[str, Any], target: str) -> Mapping[str, Any]:
    for mount in _sequence(service.get("volumes")):
        if isinstance(mount, Mapping) and mount.get("target") == target:
            return mount
    return {}


def _validate_compose(payload: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    services = _mapping(payload.get("services"))
    if set(services) != {"prometheus"}:
        errors.append("compose:services_must_be_prometheus_only")
    service = _mapping(services.get("prometheus"))
    if service.get("image") != PROMETHEUS_IMAGE:
        errors.append("compose:image_not_pinned_to_contract")
    if service.get("restart") != "no":
        errors.append("compose:restart_must_be_no")
    if service.get("read_only") is not True:
        errors.append("compose:root_filesystem_not_read_only")
    if service.get("privileged") not in (None, False):
        errors.append("compose:privileged_forbidden")
    if service.get("network_mode") == "host":
        errors.append("compose:host_network_forbidden")
    if _sequence(service.get("ports")) != ["127.0.0.1:9090:9090"]:
        errors.append("compose:binding_must_be_loopback_9090")
    if _sequence(service.get("cap_drop")) != ["ALL"]:
        errors.append("compose:cap_drop_all_required")
    if "no-new-privileges:true" not in _sequence(service.get("security_opt")):
        errors.append("compose:no_new_privileges_required")
    if service.get("environment"):
        errors.append("compose:environment_secrets_forbidden")

    health = _mapping(service.get("healthcheck"))
    health_test = " ".join(str(part) for part in _sequence(health.get("test")))
    if "http://127.0.0.1:9090/-/healthy" not in health_test:
        errors.append("compose:healthcheck_missing")
    if health.get("timeout") != "5s" or health.get("retries") != 3:
        errors.append("compose:healthcheck_not_bounded")

    config_mount = _mount_by_target(service, "/etc/prometheus/prometheus.yml")
    rules_mount = _mount_by_target(service, "/etc/prometheus/rules")
    secret_mount = _mount_by_target(service, "/run/secrets/odysseus_metrics_token")
    data_mount = _mount_by_target(service, "/prometheus")
    for name, mount in (
        ("config", config_mount),
        ("rules", rules_mount),
        ("secret", secret_mount),
    ):
        if mount.get("read_only") is not True:
            errors.append(f"compose:{name}_mount_not_read_only")
    if secret_mount.get("source") != "./secrets/odysseus_metrics_token":
        errors.append("compose:secret_file_source_mismatch")
    if data_mount.get("type") != "volume" or data_mount.get("source") != "prometheus-data":
        errors.append("compose:data_volume_missing")

    volume = _mapping(_mapping(payload.get("volumes")).get("prometheus-data"))
    if volume.get("name") != DATA_VOLUME:
        errors.append("compose:data_volume_not_versioned")
    return {
        "image": service.get("image"),
        "loopback_binding": _sequence(service.get("ports")) == ["127.0.0.1:9090:9090"],
        "data_volume": volume.get("name"),
    }


def _validate_prometheus(payload: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    global_config = _mapping(payload.get("global"))
    if global_config.get("scrape_interval") != "15s":
        errors.append("prometheus:global_scrape_interval_must_be_15s")
    if global_config.get("scrape_timeout") != "5s":
        errors.append("prometheus:global_scrape_timeout_must_be_5s")
    if global_config.get("evaluation_interval") != "15s":
        errors.append("prometheus:evaluation_interval_must_be_15s")

    retention = _mapping(_mapping(_mapping(payload.get("storage")).get("tsdb")).get("retention"))
    if retention.get("time") != "30d":
        errors.append("prometheus:retention_time_must_be_30d")
    if retention.get("size") != "5GB":
        errors.append("prometheus:retention_size_must_be_5GB")
    if payload.get("remote_write"):
        errors.append("prometheus:remote_write_forbidden")
    if payload.get("alerting"):
        errors.append("prometheus:alert_delivery_deferred_to_gro11")
    if _sequence(payload.get("rule_files")) != ["/etc/prometheus/rules/*.rules.yml"]:
        errors.append("prometheus:rule_file_contract_mismatch")

    jobs = _sequence(payload.get("scrape_configs"))
    if len(jobs) != 1 or not isinstance(jobs[0], Mapping):
        errors.append("prometheus:exactly_one_scrape_job_required")
        job: Mapping[str, Any] = {}
    else:
        job = jobs[0]
    expected_scalars = {
        "job_name": "odysseus-memory",
        "scrape_interval": "15s",
        "scrape_timeout": "5s",
        "metrics_path": "/api/diagnostics/runtime-metrics",
        "scheme": "http",
        "body_size_limit": "1MB",
        "sample_limit": 512,
        "label_limit": 8,
        "label_name_length_limit": 64,
        "label_value_length_limit": 64,
        "target_limit": 1,
    }
    for key, expected in expected_scalars.items():
        if job.get(key) != expected:
            errors.append(f"prometheus:{key}_mismatch")
    if job.get("honor_labels") is not False or job.get("follow_redirects") is not False:
        errors.append("prometheus:scrape_safety_flags_missing")

    authorization = _mapping(job.get("authorization"))
    if authorization.get("type") != "Bearer":
        errors.append("prometheus:bearer_authorization_required")
    if authorization.get("credentials_file") != "/run/secrets/odysseus_metrics_token":
        errors.append("prometheus:credentials_file_mismatch")
    if "credentials" in authorization:
        errors.append("prometheus:inline_credentials_forbidden")

    static_configs = _sequence(job.get("static_configs"))
    target = ""
    labels: Mapping[str, Any] = {}
    if len(static_configs) == 1 and isinstance(static_configs[0], Mapping):
        targets = _sequence(static_configs[0].get("targets"))
        target = str(targets[0]) if len(targets) == 1 else ""
        labels = _mapping(static_configs[0].get("labels"))
    if target != "host.containers.internal:7000":
        errors.append("prometheus:target_must_use_rootless_host_alias")
    if labels != {"service": "odysseus", "observability_scope": "memory"}:
        errors.append("prometheus:target_labels_not_fixed_contract")
    return {
        "scrape_interval": global_config.get("scrape_interval"),
        "scrape_timeout": global_config.get("scrape_timeout"),
        "retention_time": retention.get("time"),
        "retention_size": retention.get("size"),
        "target": target,
    }


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


def _validate_rules(payload: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    groups = _sequence(payload.get("groups"))
    if len(groups) != 1 or not isinstance(groups[0], Mapping):
        errors.append("rules:exactly_one_group_required")
        group: Mapping[str, Any] = {}
    else:
        group = groups[0]
    if group.get("name") != "odysseus-memory-runtime":
        errors.append("rules:group_name_mismatch")
    if group.get("interval") != "15s":
        errors.append("rules:interval_must_be_15s")
    limit = group.get("limit")
    if not isinstance(limit, int) or not 1 <= limit <= 128:
        errors.append("rules:series_limit_must_be_bounded")

    records: set[str] = set()
    for index, rule in enumerate(_sequence(group.get("rules"))):
        if not isinstance(rule, Mapping):
            errors.append(f"rules:item_{index}_not_mapping")
            continue
        if "alert" in rule:
            errors.append(f"rules:item_{index}_alert_deferred_to_gro11")
        record = str(rule.get("record") or "")
        expression = str(rule.get("expr") or "")
        if not record or record in records:
            errors.append(f"rules:item_{index}_record_missing_or_duplicate")
        records.add(record)
        if not expression or not _balanced_promql(expression):
            errors.append(f"rules:item_{index}_expression_unbalanced")
        for label in FORBIDDEN_PROMQL_LABELS:
            if re.search(rf"\b{re.escape(label)}\s*=", expression):
                errors.append(f"rules:item_{index}_private_label:{label}")
    if records != EXPECTED_RECORDING_RULES:
        errors.append("rules:recording_rule_set_mismatch")
    return {"group_count": len(groups), "recording_rule_count": len(records), "limit": limit}


def _validate_unit(errors: list[str]) -> dict[str, Any]:
    if not UNIT_PATH.is_file():
        errors.append("missing:prometheus-podman.service")
        return {}
    text = UNIT_PATH.read_text(encoding="utf-8")
    required = (
        f"ConditionPathExists={ACTIVATION_MARKER}",
        "WorkingDirectory=/opt/odysseus/ops/homeserver/observability-podman/prometheus",
        "ExecStartPre=/usr/bin/test -r ./secrets/odysseus_metrics_token",
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
        relative = path.relative_to(ASSET_ROOT)
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(text):
                errors.append(f"privacy:private_path:{relative}")
        if PRIVATE_NETWORK_PATTERN.search(text):
            errors.append(f"privacy:private_network:{relative}")
        if TOKEN_LITERAL_PATTERN.search(text):
            errors.append(f"privacy:credential_literal:{relative}")


def validate_assets() -> dict[str, Any]:
    errors: list[str] = []
    compose = _load_yaml(COMPOSE_PATH, errors)
    prometheus = _load_yaml(PROMETHEUS_PATH, errors)
    rules = _load_yaml(RULES_PATH, errors)
    compose_summary = _validate_compose(compose, errors)
    prometheus_summary = _validate_prometheus(prometheus, errors)
    rules_summary = _validate_rules(rules, errors)
    unit_summary = _validate_unit(errors)
    _validate_privacy(
        (
            COMPOSE_PATH,
            PROMETHEUS_PATH,
            RULES_PATH,
            UNIT_PATH,
            ASSET_ROOT / "README.md",
            ASSET_ROOT / "secrets" / "README.md",
        ),
        errors,
    )
    if SECRET_PATH.exists():
        errors.append("secrets:live_token_file_must_be_absent")
    return {
        "schema": SCHEMA,
        "valid": not errors,
        "errors": tuple(sorted(set(errors))),
        "compose": compose_summary,
        "prometheus": prometheus_summary,
        "rules": rules_summary,
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
        print("PROMETHEUS_ASSETS_VALID")
    else:
        for error in report["errors"]:
            print(f"ERROR {error}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

