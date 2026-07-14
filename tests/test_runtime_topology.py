from pathlib import Path

import pytest

from src.rate_limiter import RATE_LIMITER_STATE_SCOPE
from src.runtime_topology import (
    MULTI_WORKER_PREREQUISITES,
    RuntimeTopologyError,
    assert_supported_runtime_topology,
    resolve_runtime_topology,
    runtime_topology_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def test_default_topology_is_exactly_one_web_worker():
    topology = resolve_runtime_topology({})

    assert topology.web_workers == 1
    assert topology.source == "default"
    readiness = topology.readiness()
    assert readiness["ready"] is True
    assert readiness["state"] == "supported_one_web_worker"
    assert readiness["rate_limiter_scope"] == "process_local"
    assert readiness["rotating_file_log_sink_scope"] == "process_local"
    assert readiness["temporal_workers_are_web_workers"] is False


@pytest.mark.parametrize(
    "key",
    ["ODYSSEUS_WEB_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS"],
)
def test_explicit_one_worker_configuration_is_supported(key):
    topology = assert_supported_runtime_topology({key: "1"})

    assert topology.web_workers == 1
    assert topology.source == key


@pytest.mark.parametrize("value", ["2", "8", "24"])
def test_more_than_one_web_worker_fails_with_stable_reason(value):
    with pytest.raises(RuntimeTopologyError) as raised:
        assert_supported_runtime_topology({"ODYSSEUS_WEB_WORKERS": value})

    error = raised.value
    assert error.code == "unsupported_web_worker_count"
    assert error.configured_workers == int(value)
    assert "Exactly one Odysseus web worker is supported" in error.reason
    assert "auth rate limiter" in error.reason
    assert "rotating file log sink" in error.reason
    assert "Temporal workers are a separate worker domain" in error.reason


@pytest.mark.parametrize("value", ["", "0", "-1", "1.0", "many"])
def test_invalid_or_non_positive_worker_configuration_fails_closed(value):
    readiness = runtime_topology_readiness({"ODYSSEUS_WEB_WORKERS": value})

    assert readiness["ready"] is False
    assert readiness["state"] == "blocked"
    assert readiness["code"] in {
        "invalid_web_worker_count",
        "unsupported_web_worker_count",
    }


def test_temporal_worker_count_does_not_change_web_topology():
    readiness = runtime_topology_readiness({"TEMPORAL_WORKER_COUNT": "24"})

    assert readiness["ready"] is True
    assert readiness["web_workers"] == 1
    assert readiness["temporal_workers_are_web_workers"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["uvicorn", "app:app", "--workers", "2"],
        ["C:/venv/Scripts/uvicorn.exe", "app:app", "--workers=2"],
        ["/venv/lib/python/uvicorn/__main__.py", "app:app", "--workers", "24"],
    ],
)
def test_direct_uvicorn_multi_worker_cli_fails_closed(argv):
    readiness = runtime_topology_readiness({}, argv=argv)

    assert readiness["ready"] is False
    assert readiness["code"] == "unsupported_web_worker_count"
    assert readiness["source"] == "uvicorn_cli"


def test_non_uvicorn_process_arguments_do_not_masquerade_as_web_configuration():
    topology = resolve_runtime_topology({}, argv=["pytest", "--workers", "8"])

    assert topology.web_workers == 1
    assert topology.source == "default"


def test_multi_worker_readiness_stays_blocked_with_exact_prerequisites():
    readiness = runtime_topology_readiness({})

    assert readiness["multi_worker_readiness"] == {
        "ready": False,
        "state": "blocked",
        "reason": "Multi-worker web serving is not supported by the current process-local state boundaries.",
        "prerequisites": list(MULTI_WORKER_PREREQUISITES),
    }
    assert set(MULTI_WORKER_PREREQUISITES) == {
        "shared_or_distributed_auth_rate_limiter",
        "single_log_queue_listener_or_external_collector",
        "cross_process_mutable_state_audit",
        "multi_process_load_and_failure_acceptance",
    }


def test_rate_limiter_declares_its_process_local_state_boundary():
    assert RATE_LIMITER_STATE_SCOPE == "process_local"


def test_app_enforces_topology_before_serving_and_reports_it():
    source = (ROOT / "app.py").read_text(encoding="utf-8-sig")

    assert "RUNTIME_TOPOLOGY = assert_supported_runtime_topology(argv=sys.argv)" in source
    assert 'result["runtime_topology"] = RUNTIME_TOPOLOGY.readiness()' in source
    assert '"runtime_topology": RUNTIME_TOPOLOGY.readiness()' in source
    assert "workers=RUNTIME_TOPOLOGY.web_workers" in source
    assert "logging.handlers.RotatingFileHandler" in source


def test_every_shipped_web_launcher_explicitly_selects_one_worker():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    launcher = (ROOT / "launch-windows.ps1").read_text(encoding="utf-8-sig")
    runner = (ROOT / "run-server-windows.ps1").read_text(encoding="utf-8-sig")
    desktop = (ROOT / "launcher.py").read_text(encoding="utf-8-sig")
    macos_app = (ROOT / "build-macos-app.sh").read_text(encoding="utf-8-sig")
    macos_shell = (ROOT / "start-macos.sh").read_text(encoding="utf-8-sig")
    systemd = (ROOT / "odysseus-ui.service").read_text(encoding="utf-8-sig")

    assert '"--workers", "1"' in dockerfile
    assert "--workers 1" in launcher
    assert "--workers 1" in runner
    assert "workers=1" in desktop
    assert macos_app.count("--workers 1") == 2
    assert "--workers 1" in macos_shell
    assert "--workers 1" in systemd
    shipped = dockerfile + launcher + runner + desktop + macos_app + macos_shell + systemd
    assert "--workers 2" not in shipped
    assert "workers=2" not in shipped
