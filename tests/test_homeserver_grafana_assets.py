from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "ops" / "homeserver" / "observability-podman" / "grafana"
PROMETHEUS_ROOT = ASSET_ROOT.parent / "prometheus"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validator():
    return _load_module("homeserver_grafana_assets", ASSET_ROOT / "validate_assets.py")


def _builder():
    return _load_module("homeserver_grafana_builder", ASSET_ROOT / "build_dashboards.py")


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_grafana_assets_validate_offline_without_live_actions():
    report = _validator().validate_assets()

    assert report["valid"] is True, report["errors"]
    assert report["errors"] == ()
    assert report["secret_file_present"] is False
    assert report["live_actions_performed"] is False
    assert report["host_commands_performed"] is False
    assert report["network_io_performed"] is False
    assert report["compose"] == {
        "image": "docker.io/grafana/grafana:13.1.0",
        "loopback_binding": True,
        "data_volume": "odysseus-grafana-data-v1",
    }
    assert report["dashboards"] == {
        "dashboard_count": 6,
        "panel_count": 51,
        "query_count": 56,
    }
    assert report["alerts"]["alert_count"] == 12
    assert report["alerts"]["promtool_test_count"] == 12


def test_grafana_compose_is_private_fail_closed_and_rollback_safe():
    compose = _yaml(ASSET_ROOT / "compose.yml")
    service = compose["services"]["grafana"]

    assert set(compose["services"]) == {"grafana"}
    assert service["image"] == "docker.io/grafana/grafana:13.1.0"
    assert service["restart"] == "no"
    assert service["ports"] == ["127.0.0.1:3000:3000"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "privileged" not in service
    assert "network_mode" not in service
    assert service["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert service["environment"]["GF_UNIFIED_ALERTING_ENABLED"] == "false"
    assert service["environment"]["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana_admin_password"
    assert "http://127.0.0.1:3000/api/health" in " ".join(service["healthcheck"]["test"])
    assert compose["volumes"]["grafana-data"]["name"] == "odysseus-grafana-data-v1"

    mounts = {mount["target"]: mount for mount in service["volumes"]}
    assert mounts["/etc/grafana/provisioning"]["read_only"] is True
    assert mounts["/var/lib/grafana/dashboards"]["read_only"] is True
    assert mounts["/run/secrets/grafana_admin_password"] == {
        "type": "bind",
        "source": "./secrets/grafana_admin_password",
        "target": "/run/secrets/grafana_admin_password",
        "read_only": True,
    }


def test_provisioning_uses_only_stable_uid_and_gated_url_variable():
    datasource = _yaml(ASSET_ROOT / "provisioning" / "datasources" / "prometheus.yml")["datasources"][0]
    provider = _yaml(ASSET_ROOT / "provisioning" / "dashboards" / "memory.yml")["providers"][0]

    assert datasource["uid"] == "odysseus-prometheus"
    assert datasource["url"] == "$PROMETHEUS_URL"
    assert datasource["isDefault"] is True
    assert datasource["editable"] is False
    assert datasource["jsonData"]["manageAlerts"] is False
    assert "secureJsonData" not in datasource
    assert "basicAuth" not in datasource
    assert provider["folderUid"] == "odysseus-memory"
    assert provider["disableDeletion"] is True
    assert provider["editable"] is False
    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"


def test_six_dashboards_are_canonical_current_and_content_free():
    validator = _validator()
    builder = _builder()

    assert builder.write_dashboards(check=True) == []
    assert set(builder.build_payloads()) == set(validator.EXPECTED_DASHBOARDS)
    for name, (expected_uid, expected_title, expected_panels) in validator.EXPECTED_DASHBOARDS.items():
        path = ASSET_ROOT / "dashboards" / name
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        assert text == json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        assert payload["uid"] == expected_uid
        assert payload["title"] == expected_title
        assert payload["editable"] is False
        assert payload["refresh"] == "30s"
        assert len(payload["panels"]) == expected_panels
        assert payload["links"] == []
        assert all(panel["datasource"] == builder.DATASOURCE for panel in payload["panels"])
        queries = [target["expr"] for panel in payload["panels"] for target in panel["targets"]]
        assert all(validator._balanced_promql(query) for query in queries)
        encoded_queries = "\n".join(queries)
        for forbidden in ("owner=", "user=", "vault=", "path=", "source=", "query=", "prompt=", "model="):
            assert forbidden not in encoded_queries

    cache = builder.build_payloads()["cache.json"]
    hit_ratio_steps = cache["panels"][0]["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert hit_ratio_steps == [
        {"color": "red", "value": None},
        {"color": "orange", "value": 0.4},
        {"color": "green", "value": 0.6},
    ]


def test_alert_rules_freeze_threshold_samples_and_maintenance_contracts():
    validator = _validator()
    rules = _yaml(PROMETHEUS_ROOT / "rules" / "memory-alerts.rules.yml")
    alert_map = {
        rule["alert"]: rule
        for rule in rules["groups"][0]["rules"]
        if "alert" in rule
    }

    assert set(alert_map) == validator.EXPECTED_ALERTS
    assert len(alert_map) == 12
    for name, (expected_for, fragments) in validator.ALERT_CONTRACTS.items():
        assert alert_map[name]["for"] == expected_for
        assert all(fragment in alert_map[name]["expr"] for fragment in fragments)
        assert validator._balanced_promql(alert_map[name]["expr"])
        assert alert_map[name]["labels"]["severity"] in {"warning", "critical"}

    for name in validator.MAINTENANCE_SUPPRESSED:
        assert "job:odysseus_memory_maintenance_recent:bool == 0" in alert_map[name]["expr"]
    for name in validator.REBUILD_ALERTS:
        assert "maintenance" not in alert_map[name]["expr"]


def test_promtool_matrix_evaluates_every_alert_in_no_data_state():
    validator = _validator()
    payload = _yaml(PROMETHEUS_ROOT / "rules" / "memory-alerts.test.yml")

    tests = payload["tests"][0]["alert_rule_test"]
    assert payload["rule_files"] == ["memory-recording.rules.yml", "memory-alerts.rules.yml"]
    assert len(tests) == 12
    assert {test["alertname"] for test in tests} == validator.EXPECTED_ALERTS
    assert all(test["exp_alerts"] == [] for test in tests)
    assert all(test["eval_time"] == "1m" for test in tests)


def test_systemd_docs_and_secret_placeholder_keep_gro11_default_off():
    unit = (ASSET_ROOT / "grafana-podman.service").read_text(encoding="utf-8")
    readme = (ASSET_ROOT / "README.md").read_text(encoding="utf-8")
    ignore = (ASSET_ROOT / "secrets" / ".gitignore").read_text(encoding="utf-8")

    assert "ConditionPathExists=%h/.config/odysseus-observability/GRAFANA_ACTIVATION_GO" in unit
    assert "EnvironmentFile=%h/.config/odysseus-observability/grafana.env" in unit
    assert "ExecStartPre=/usr/bin/test -r ./secrets/grafana_admin_password" in unit
    assert "Restart=no" in unit
    assert "sudo" not in unit
    assert "--privileged" not in unit
    assert "default-off" in readme
    assert "GRO-LIVE-ACTIVATION" in readme
    assert "GRO-11 permits deterministic generation and offline validation only" in readme
    assert not (ASSET_ROOT / "secrets" / "grafana_admin_password").exists()
    assert ignore.splitlines()[0] == "*"
