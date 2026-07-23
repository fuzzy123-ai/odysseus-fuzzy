from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "ops" / "homeserver" / "observability-podman" / "prometheus"


def _load_validator():
    path = ASSET_ROOT / "validate_assets.py"
    spec = importlib.util.spec_from_file_location("homeserver_prometheus_assets", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _yaml(name: str):
    return yaml.safe_load((ASSET_ROOT / name).read_text(encoding="utf-8"))


def test_prometheus_assets_validate_offline_without_live_actions():
    report = _load_validator().validate_assets()

    assert report["valid"] is True, report["errors"]
    assert report["errors"] == ()
    assert report["secret_file_present"] is False
    assert report["live_actions_performed"] is False
    assert report["host_commands_performed"] is False
    assert report["network_io_performed"] is False
    assert report["compose"] == {
        "image": "quay.io/prometheus/prometheus:v3.12.0",
        "loopback_binding": True,
        "data_volume": "odysseus-prometheus-data-v1",
    }
    assert report["prometheus"]["retention_time"] == "30d"
    assert report["prometheus"]["retention_size"] == "5GB"
    assert report["rules"]["recording_rule_count"] == 13


def test_compose_is_private_unprivileged_health_checked_and_rollback_safe():
    compose = _yaml("compose.yml")
    service = compose["services"]["prometheus"]

    assert set(compose["services"]) == {"prometheus"}
    assert service["image"] == "quay.io/prometheus/prometheus:v3.12.0"
    assert service["restart"] == "no"
    assert service["ports"] == ["127.0.0.1:9090:9090"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "privileged" not in service
    assert "network_mode" not in service
    assert "environment" not in service
    assert service["healthcheck"]["timeout"] == "5s"
    assert service["healthcheck"]["retries"] == 3
    assert "/-/healthy" in " ".join(service["healthcheck"]["test"])
    assert compose["volumes"]["prometheus-data"]["name"] == "odysseus-prometheus-data-v1"

    mounts = {item["target"]: item for item in service["volumes"]}
    assert mounts["/run/secrets/odysseus_metrics_token"] == {
        "type": "bind",
        "source": "./secrets/odysseus_metrics_token",
        "target": "/run/secrets/odysseus_metrics_token",
        "read_only": True,
    }
    assert mounts["/prometheus"]["type"] == "volume"
    assert mounts["/prometheus"]["source"] == "prometheus-data"


def test_prometheus_scrape_uses_exact_token_file_limits_and_retention():
    config = _yaml("prometheus.yml")
    job = config["scrape_configs"][0]

    assert config["global"] == {
        "scrape_interval": "15s",
        "scrape_timeout": "5s",
        "evaluation_interval": "15s",
    }
    assert config["storage"]["tsdb"]["retention"] == {"time": "30d", "size": "5GB"}
    assert "remote_write" not in config
    assert "alerting" not in config
    assert job["metrics_path"] == "/api/diagnostics/runtime-metrics"
    assert job["authorization"] == {
        "type": "Bearer",
        "credentials_file": "/run/secrets/odysseus_metrics_token",
    }
    assert job["static_configs"] == [
        {
            "targets": ["host.containers.internal:7000"],
            "labels": {"service": "odysseus", "observability_scope": "memory"},
        }
    ]
    assert job["body_size_limit"] == "1MB"
    assert job["sample_limit"] == 512
    assert job["label_limit"] == 8
    assert job["target_limit"] == 1
    assert job["honor_labels"] is False
    assert job["follow_redirects"] is False


def test_recording_rules_are_bounded_unique_content_free_and_alert_free():
    validator = _load_validator()
    payload = _yaml("rules/memory-recording.rules.yml")
    groups = payload["groups"]

    assert len(groups) == 1
    assert groups[0]["interval"] == "15s"
    assert groups[0]["limit"] == 128
    rules = groups[0]["rules"]
    records = {rule["record"] for rule in rules}
    assert len(records) == len(rules) == 13
    assert records == validator.EXPECTED_RECORDING_RULES
    assert all("alert" not in rule for rule in rules)
    assert all(validator._balanced_promql(rule["expr"]) for rule in rules)

    encoded = (ASSET_ROOT / "rules" / "memory-recording.rules.yml").read_text(encoding="utf-8")
    for forbidden in ("owner=", "user=", "vault=", "path=", "query=", "prompt=", "model="):
        assert forbidden not in encoded


def test_systemd_template_and_secret_placeholder_fail_closed():
    unit = (ASSET_ROOT / "prometheus-podman.service").read_text(encoding="utf-8")
    secret = ASSET_ROOT / "secrets" / "odysseus_metrics_token"
    ignore = (ASSET_ROOT / "secrets" / ".gitignore").read_text(encoding="utf-8")

    assert "ConditionPathExists=%h/.config/odysseus-observability/ACTIVATION_GO" in unit
    assert "ExecStartPre=/usr/bin/test -r ./secrets/odysseus_metrics_token" in unit
    assert "ExecStart=/usr/bin/podman-compose -f compose.yml up -d" in unit
    assert "Restart=no" in unit
    assert "sudo" not in unit
    assert "--privileged" not in unit
    assert not secret.exists()
    assert ignore.splitlines()[0] == "*"


def test_docs_explicitly_keep_gro10_offline_and_default_off():
    readme = (ASSET_ROOT / "README.md").read_text(encoding="utf-8")
    context = (ROOT / "ops" / "homeserver" / "CONTEXT.md").read_text(encoding="utf-8")

    assert "default-off" in readme
    assert "Do not create the activation marker" in readme
    assert "GRO-LIVE-ACTIVATION" in readme
    assert "GRO-10 permits offline lint and tests only" in context
    normalized_context = " ".join(context.split())
    assert "not evidence of a live deployment" in normalized_context
