from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from src.codebase_memory_process import (
    CBM_LOCKED_COMMIT,
    CBM_LOCKED_VERSION,
    CodebaseMemoryProcess,
    CodebaseMemoryProcessError,
    CodebaseMemoryProcessSettings,
    EgressEnforcementReceipt,
    ProcessState,
    build_scrubbed_environment,
    canonical_request,
)


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake_cbm.py"
    path.write_text(body, encoding="utf-8")
    return path


def _settings(
    tmp_path: Path,
    script: Path,
    *,
    enabled: bool = True,
    receipt: EgressEnforcementReceipt | None = None,
    request_timeout_s: float = 0.5,
    shutdown_timeout_s: float = 0.2,
    max_message_bytes: int = 4096,
    extra_args: tuple[str, ...] = (),
) -> CodebaseMemoryProcessSettings:
    config = tmp_path / "config"
    data = tmp_path / "data"
    root = tmp_path / "repository"
    for path in (config, data, root):
        path.mkdir(exist_ok=True)
    return CodebaseMemoryProcessSettings(
        executable_path=Path(sys.executable),
        config_dir=config,
        data_dir=data,
        allowed_root=root,
        launch_arguments=("-I", "-u", str(script), *extra_args),
        enabled=enabled,
        request_timeout_s=request_timeout_s,
        shutdown_timeout_s=shutdown_timeout_s,
        max_message_bytes=max_message_bytes,
        egress_receipt=receipt
        or EgressEnforcementReceipt(True, "test_sandbox", "cbm03_fixture", 0),
    )


ECHO_SCRIPT = r"""
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    result = {
        "ok": True,
        "secret_present": "SECRET_TOKEN" in os.environ,
        "proxy_present": "HTTP_PROXY" in os.environ,
        "auto_watch": os.environ.get("CBM_AUTO_WATCH"),
        "update_check": os.environ.get("CBM_UPDATE_CHECK"),
        "network_egress": os.environ.get("CBM_NETWORK_EGRESS"),
        "transport": os.environ.get("CBM_TRANSPORT"),
    }
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


def test_settings_are_disabled_by_default_and_summaries_hide_absolute_paths(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)
    settings = _settings(tmp_path, script, enabled=False)
    summary = settings.content_free_summary()
    rendered = json.dumps(summary, sort_keys=True)

    assert settings.enabled is False
    assert settings.transport == "stdio"
    assert settings.locked_version == CBM_LOCKED_VERSION
    assert settings.locked_commit == CBM_LOCKED_COMMIT
    assert str(tmp_path) not in rendered
    assert summary["executable_ref"].startswith("path_sha256:")
    assert summary["runtime_controls"]["CBM_AUTO_WATCH"] == "false"


def test_construction_and_import_do_not_start_a_process(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)
    calls = 0

    async def launcher(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("launcher must not run during construction")

    process = CodebaseMemoryProcess(_settings(tmp_path, script), launcher=launcher)

    assert process.snapshot().state is ProcessState.STOPPED
    assert process.snapshot().start_count == 0
    assert calls == 0


def test_disabled_or_unenforced_process_fails_before_launcher(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)
    calls = 0

    async def launcher(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("launcher must remain blocked")

    async def scenario():
        disabled = CodebaseMemoryProcess(
            _settings(tmp_path, script, enabled=False), launcher=launcher
        )
        with pytest.raises(CodebaseMemoryProcessError, match="disabled") as disabled_error:
            await disabled.start()
        assert disabled_error.value.code == "process_disabled"

        receipt = EgressEnforcementReceipt(False, "test_sandbox", "cbm03_fixture", 0)
        blocked = CodebaseMemoryProcess(
            _settings(tmp_path, script, receipt=receipt), launcher=launcher
        )
        with pytest.raises(CodebaseMemoryProcessError, match="egress") as egress_error:
            await blocked.start()
        assert egress_error.value.code == "egress_not_enforced"

    asyncio.run(scenario())
    assert calls == 0


def test_scrubbed_environment_drops_secrets_proxies_and_home_state(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)
    settings = _settings(tmp_path, script)
    environment = build_scrubbed_environment(
        {
            "SYSTEMROOT": "C:\\Windows",
            "SECRET_TOKEN": "secret",
            "OPENAI_API_KEY": "secret",
            "HTTP_PROXY": "http://proxy.invalid",
            "HOME": "/home/alice",
            "USERPROFILE": "C:\\Users\\alice",
            "GIT_CONFIG_GLOBAL": "C:\\secret.gitconfig",
        },
        settings,
    )

    assert environment["SYSTEMROOT"] == "C:\\Windows"
    assert environment["CBM_AUTO_WATCH"] == "false"
    assert environment["CBM_AUTO_INDEX"] == "false"
    assert environment["CBM_UI_ENABLED"] == "false"
    assert environment["CBM_UPDATE_CHECK"] == "false"
    assert environment["CBM_NETWORK_EGRESS"] == "false"
    assert environment["CBM_ALLOWED_ROOT"] == str(settings.allowed_root)
    for forbidden in (
        "SECRET_TOKEN",
        "OPENAI_API_KEY",
        "HTTP_PROXY",
        "HOME",
        "USERPROFILE",
        "GIT_CONFIG_GLOBAL",
    ):
        assert forbidden not in environment


def test_fake_executable_exchange_and_lifecycle_are_bounded(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)

    async def scenario():
        process = CodebaseMemoryProcess(
            _settings(tmp_path, script),
            inherited_environment={
                "SYSTEMROOT": "C:\\Windows",
                "SECRET_TOKEN": "secret",
                "HTTP_PROXY": "http://proxy.invalid",
            },
        )
        started = await process.start()
        assert started.state is ProcessState.RUNNING
        response = await process.exchange(
            canonical_request({"jsonrpc": "2.0", "id": 1, "method": "probe"})
        )
        result = json.loads(response)["result"]
        assert result == {
            "ok": True,
            "secret_present": False,
            "proxy_present": False,
            "auto_watch": "false",
            "update_check": "false",
            "network_egress": "false",
            "transport": "stdio",
        }
        stopped = await process.stop(reason="test_complete")
        assert stopped.state is ProcessState.STOPPED
        assert stopped.start_count == 1
        assert stopped.stop_reason == "test_complete"

    asyncio.run(scenario())


def test_request_timeout_terminates_fake_child(tmp_path: Path):
    script = _script(
        tmp_path,
        "import sys, time\nfor line in sys.stdin:\n    time.sleep(60)\n",
    )

    async def scenario():
        process = CodebaseMemoryProcess(
            _settings(tmp_path, script, request_timeout_s=0.05)
        )
        await process.start()
        with pytest.raises(CodebaseMemoryProcessError) as error:
            await process.exchange(b'{"jsonrpc":"2.0","id":1}')
        assert error.value.code == "request_timeout"
        assert process.snapshot().state is ProcessState.STOPPED
        assert process.snapshot().stop_reason == "request_timeout"

    asyncio.run(scenario())


def test_child_crash_isolated_as_content_free_error(tmp_path: Path):
    script = _script(
        tmp_path,
        "import sys\nfor line in sys.stdin:\n    raise SystemExit(7)\n",
    )

    async def scenario():
        process = CodebaseMemoryProcess(_settings(tmp_path, script))
        await process.start()
        with pytest.raises(CodebaseMemoryProcessError) as error:
            await process.exchange(b'{"jsonrpc":"2.0","id":1}')
        assert error.value.code == "child_exited"
        assert process.snapshot().state is ProcessState.STOPPED

    asyncio.run(scenario())


def test_oversized_request_and_response_fail_closed(tmp_path: Path):
    oversized_script = _script(
        tmp_path,
        "import sys\nfor line in sys.stdin:\n    print('x' * 5000, flush=True)\n",
    )

    async def scenario():
        process = CodebaseMemoryProcess(
            _settings(
                tmp_path,
                oversized_script,
                max_message_bytes=1024,
                request_timeout_s=2.0,
            )
        )
        await process.start()
        with pytest.raises(CodebaseMemoryProcessError) as request_error:
            await process.exchange(b"x" * 1025)
        assert request_error.value.code == "request_too_large"

        with pytest.raises(CodebaseMemoryProcessError) as response_error:
            await process.exchange(b'{"jsonrpc":"2.0","id":1}')
        assert response_error.value.code == "response_too_large"
        assert process.snapshot().state is ProcessState.STOPPED

    asyncio.run(scenario())


def test_cancelled_exchange_stops_child_and_propagates_cancellation(tmp_path: Path):
    script = _script(
        tmp_path,
        "import sys, time\nfor line in sys.stdin:\n    time.sleep(60)\n",
    )

    async def scenario():
        process = CodebaseMemoryProcess(
            _settings(tmp_path, script, request_timeout_s=1.0)
        )
        await process.start()
        task = asyncio.create_task(
            process.exchange(b'{"jsonrpc":"2.0","id":1}')
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.snapshot().state is ProcessState.STOPPED
        assert process.snapshot().stop_reason == "request_cancelled"

    asyncio.run(scenario())


class _FakeWriter:
    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _TerminateResistantProcess:
    def __init__(self) -> None:
        self.stdin = _FakeWriter()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.pid = 42
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._killed_event = asyncio.Event()

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._killed_event.set()

    async def wait(self) -> int:
        await self._killed_event.wait()
        return -9


def test_stop_escalates_from_terminate_to_kill_within_bound(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)

    async def scenario():
        fake = _TerminateResistantProcess()

        async def launcher(*args, **kwargs):
            return fake

        process = CodebaseMemoryProcess(
            _settings(tmp_path, script, shutdown_timeout_s=0.02),
            launcher=launcher,
        )
        await process.start()
        stopped = await process.stop(reason="bounded_stop")
        assert stopped.state is ProcessState.STOPPED
        assert fake.terminated is True
        assert fake.killed is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "arguments",
    [
        ("--ui",),
        ("--ui=true",),
        ("--auto-watch",),
        ("--update-check=true",),
        ("install",),
        ("--write-hooks",),
    ],
)
def test_forbidden_runtime_arguments_fail_at_settings_boundary(
    tmp_path: Path, arguments: tuple[str, ...]
):
    script = _script(tmp_path, ECHO_SCRIPT)
    with pytest.raises(CodebaseMemoryProcessError) as error:
        _settings(tmp_path, script, extra_args=arguments)
    assert error.value.code == "forbidden_argument"


def test_paths_must_be_absolute_and_state_directories_stay_outside_source_root(tmp_path: Path):
    script = _script(tmp_path, ECHO_SCRIPT)
    with pytest.raises(CodebaseMemoryProcessError, match="absolute"):
        CodebaseMemoryProcessSettings(
            executable_path=Path("fake-cbm"),
            config_dir=tmp_path / "config",
            data_dir=tmp_path / "data",
            allowed_root=tmp_path / "repository",
        )

    repository = tmp_path / "repository"
    repository.mkdir()
    with pytest.raises(CodebaseMemoryProcessError) as error:
        CodebaseMemoryProcessSettings(
            executable_path=Path(sys.executable),
            config_dir=repository / "config",
            data_dir=tmp_path / "data",
            allowed_root=repository,
        )
    assert error.value.code == "unsafe_path_layout"
