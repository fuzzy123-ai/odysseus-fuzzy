from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from src.codebase_memory_client import (
    CBM_ADAPTER_PROTOCOL,
    CodebaseMemoryClient,
    CodebaseMemoryClientError,
    HealthStatus,
    RuntimeControls,
)
from src.codebase_memory_process import (
    CBM_LOCKED_COMMIT,
    CBM_LOCKED_VERSION,
    CodebaseMemoryProcess,
    CodebaseMemoryProcessSettings,
    EgressEnforcementReceipt,
    ProcessState,
)


FAKE_SERVER = r"""
import json
import sys

mode = sys.argv[1]
controls = {
    "auto_watch": False,
    "auto_index": False,
    "ui": False,
    "update_check": False,
    "network_egress": False,
    "installer": False,
    "self_update": False,
    "agent_config_mutation": False,
    "hook_mutation": False,
    "instruction_mutation": False,
    "shared_graph_export": False,
    "diagnostics_files": False,
    "semantic_model": False,
    "egress_enforced": True,
}
capabilities = ["health", "index_status", "list_projects"]

for line in sys.stdin:
    if mode == "crash":
        raise SystemExit(7)
    request = json.loads(line)
    method = request["method"]
    request_id = request["id"]
    if mode == "malformed":
        print("{not-json", flush=True)
        continue
    if mode == "duplicate":
        print('{"jsonrpc":"2.0","jsonrpc":"2.0","id":%d,"result":{}}' % request_id, flush=True)
        continue
    if mode == "nan":
        print('{"jsonrpc":"2.0","id":%d,"result":{"value":NaN}}' % request_id, flush=True)
        continue
    if mode == "remote_error":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -1, "message": "private upstream detail"}}), flush=True)
        continue

    response_id = request_id + 1 if mode == "id_mismatch" else request_id
    if mode == "bool_id":
        response_id = True
    protocol = "unsupported.protocol.v9" if mode == "protocol_mismatch" else "odysseus.codebase_memory.adapter.v1"
    version = "9.9.9" if mode == "version_mismatch" else "0.9.0"
    runtime = dict(controls)
    if mode == "unsafe_controls":
        runtime["auto_watch"] = True
    current_capabilities = list(capabilities)
    if mode == "duplicate_capabilities":
        current_capabilities.append("health")
    if mode == "missing_health":
        current_capabilities = ["list_projects"]

    if method == "initialize":
        result = {
            "protocol_version": protocol,
            "engine_version": version,
            "engine_commit": "b637e3330c96cfe452da623db068c241aaa3ec01",
            "transport": "stdio",
            "capabilities": current_capabilities,
            "runtime_controls": runtime,
        }
    elif method == "health":
        health_capabilities = list(current_capabilities)
        if mode == "health_drift":
            health_capabilities.append("query_graph")
        result = {
            "status": "degraded" if mode == "degraded" else "healthy",
            "ready": mode != "degraded",
            "protocol_version": protocol,
            "engine_version": version,
            "engine_commit": "b637e3330c96cfe452da623db068c241aaa3ec01",
            "capabilities": health_capabilities,
            "runtime_controls": runtime,
            "active_projects": 0,
            "successful_network_calls": 1 if mode == "network_call" else 0,
            "last_error_code": "fixture_degraded" if mode == "degraded" else "",
        }
    elif method == "shutdown":
        result = {"accepted": True}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": response_id, "result": result}), flush=True)
"""


def _client(tmp_path: Path, *, mode: str = "healthy", enabled: bool = True):
    script = tmp_path / "fake_cbm_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    config = tmp_path / "config"
    data = tmp_path / "data"
    root = tmp_path / "repository"
    for path in (config, data, root):
        path.mkdir(exist_ok=True)
    settings = CodebaseMemoryProcessSettings(
        executable_path=Path(sys.executable),
        config_dir=config,
        data_dir=data,
        allowed_root=root,
        launch_arguments=("-I", "-u", str(script), mode),
        enabled=enabled,
        request_timeout_s=2.0,
        shutdown_timeout_s=0.2,
        max_message_bytes=8192,
        egress_receipt=EgressEnforcementReceipt(
            True, "test_sandbox", "cbm03_client_fixture", 0
        ),
    )
    process = CodebaseMemoryProcess(settings)
    return process, CodebaseMemoryClient(process)


def test_healthy_fake_server_returns_structured_locked_health(tmp_path: Path):
    async def scenario():
        process, client = _client(tmp_path)
        health = await client.open()
        assert health.status is HealthStatus.HEALTHY
        assert health.ready is True
        assert health.protocol_version == CBM_ADAPTER_PROTOCOL
        assert health.engine_version == CBM_LOCKED_VERSION
        assert health.engine_commit == CBM_LOCKED_COMMIT
        assert health.capabilities == ("health", "index_status", "list_projects")
        assert health.active_projects == 0
        assert health.successful_network_calls == 0
        assert health.runtime_controls.egress_enforced is True
        assert health.runtime_controls.auto_watch is False

        second = await client.health()
        assert second == health
        rendered = json.dumps(second.to_dict(), sort_keys=True)
        assert str(tmp_path) not in rendered
        assert "path" not in rendered.lower()

        await client.close()
        assert process.snapshot().state is ProcessState.STOPPED
        assert client.handshake is None

    asyncio.run(scenario())


def test_client_process_remains_default_off_until_explicit_enable(tmp_path: Path):
    async def scenario():
        process, client = _client(tmp_path, enabled=False)
        with pytest.raises(CodebaseMemoryClientError) as error:
            await client.open()
        assert error.value.code == "process_disabled"
        assert process.snapshot().state is ProcessState.STOPPED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("protocol_mismatch", "protocol_mismatch"),
        ("version_mismatch", "engine_version_mismatch"),
        ("unsafe_controls", "unsafe_runtime_controls"),
        ("duplicate_capabilities", "invalid_capabilities"),
        ("missing_health", "invalid_capabilities"),
    ],
)
def test_handshake_mismatch_or_unsafe_capabilities_stop_process(
    tmp_path: Path, mode: str, expected_code: str
):
    async def scenario():
        process, client = _client(tmp_path, mode=mode)
        with pytest.raises(CodebaseMemoryClientError) as error:
            await client.open()
        assert error.value.code == expected_code
        assert process.snapshot().state is ProcessState.STOPPED
        assert client.handshake is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("malformed", "malformed_json"),
        ("duplicate", "duplicate_response_field"),
        ("nan", "malformed_json"),
        ("remote_error", "remote_error"),
        ("id_mismatch", "response_mismatch"),
        ("bool_id", "response_mismatch"),
        ("crash", "child_exited"),
    ],
)
def test_malformed_error_mismatched_or_crashed_child_is_isolated(
    tmp_path: Path, mode: str, expected_code: str
):
    async def scenario():
        process, client = _client(tmp_path, mode=mode)
        with pytest.raises(CodebaseMemoryClientError) as error:
            await client.open()
        assert error.value.code == expected_code
        assert process.snapshot().state is ProcessState.STOPPED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("health_drift", "health_drift"),
        ("network_call", "network_boundary_breached"),
        ("degraded", "engine_not_ready"),
    ],
)
def test_health_drift_network_breach_or_degraded_state_blocks_open(
    tmp_path: Path, mode: str, expected_code: str
):
    async def scenario():
        process, client = _client(tmp_path, mode=mode)
        with pytest.raises(CodebaseMemoryClientError) as error:
            await client.open()
        assert error.value.code == expected_code
        assert process.snapshot().state is ProcessState.STOPPED

    asyncio.run(scenario())


def test_client_exposes_only_initialize_health_and_shutdown(tmp_path: Path):
    async def scenario():
        _process, client = _client(tmp_path)
        await client.open()
        with pytest.raises(CodebaseMemoryClientError) as error:
            await client._request("index_repository", {})
        assert error.value.code == "method_not_allowed"
        await client.close()

    asyncio.run(scenario())


def test_health_before_initialize_and_double_open_fail_closed(tmp_path: Path):
    async def scenario():
        _process, client = _client(tmp_path)
        with pytest.raises(CodebaseMemoryClientError) as health_error:
            await client.health()
        assert health_error.value.code == "client_not_initialized"

        await client.open()
        with pytest.raises(CodebaseMemoryClientError) as open_error:
            await client.open()
        assert open_error.value.code == "client_already_open"
        await client.close()

    asyncio.run(scenario())


def test_runtime_controls_require_every_disabled_surface_and_egress_proof():
    safe = {
        "auto_watch": False,
        "auto_index": False,
        "ui": False,
        "update_check": False,
        "network_egress": False,
        "installer": False,
        "self_update": False,
        "agent_config_mutation": False,
        "hook_mutation": False,
        "instruction_mutation": False,
        "shared_graph_export": False,
        "diagnostics_files": False,
        "semantic_model": False,
        "egress_enforced": True,
    }
    controls = RuntimeControls.from_dict(safe)
    assert controls.to_dict() == safe

    missing = dict(safe)
    del missing["hook_mutation"]
    with pytest.raises(CodebaseMemoryClientError) as missing_error:
        RuntimeControls.from_dict(missing)
    assert missing_error.value.code == "invalid_runtime_controls"

    unsafe = dict(safe)
    unsafe["update_check"] = True
    with pytest.raises(CodebaseMemoryClientError) as unsafe_error:
        RuntimeControls.from_dict(unsafe)
    assert unsafe_error.value.code == "unsafe_runtime_controls"

    unenforced = dict(safe)
    unenforced["egress_enforced"] = False
    with pytest.raises(CodebaseMemoryClientError) as egress_error:
        RuntimeControls.from_dict(unenforced)
    assert egress_error.value.code == "unsafe_runtime_controls"
