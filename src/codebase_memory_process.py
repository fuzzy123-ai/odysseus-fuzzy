"""Disabled-by-default, bounded process isolation for Codebase Memory.

Importing this module starts nothing.  A caller must provide explicit paths,
an affirmative enabled flag, and an egress-enforcement receipt before a child
process can be launched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol


CBM_LOCKED_VERSION = "0.9.0"
CBM_LOCKED_COMMIT = "b637e3330c96cfe452da623db068c241aaa3ec01"
CBM_PROCESS_CONTRACT = "odysseus.codebase_memory.process.v1"
CBM_TRANSPORT = "stdio"

DEFAULT_STARTUP_TIMEOUT_S = 5.0
DEFAULT_REQUEST_TIMEOUT_S = 5.0
DEFAULT_SHUTDOWN_TIMEOUT_S = 2.0
DEFAULT_MAX_MESSAGE_BYTES = 1_048_576

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_FORBIDDEN_ARGUMENTS = {
    "install",
    "--install",
    "--ui",
    "--watch",
    "--auto-watch",
    "--auto-index",
    "--update",
    "--update-check",
    "--self-update",
    "--export-shared-graph",
    "--write-config",
    "--write-hooks",
}
_FORBIDDEN_ARGUMENT_PREFIXES = tuple(
    item + "=" for item in _FORBIDDEN_ARGUMENTS if item.startswith("--")
)
_INHERITED_ENV_ALLOWLIST = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
    }
)
_CONTROL_ENV = {
    "CBM_TRANSPORT": "stdio",
    "CBM_AUTO_WATCH": "false",
    "CBM_AUTO_INDEX": "false",
    "CBM_UI_ENABLED": "false",
    "CBM_UPDATE_CHECK": "false",
    "CBM_NETWORK_EGRESS": "false",
    "CBM_INSTALLER_ENABLED": "false",
    "CBM_SELF_UPDATE": "false",
    "CBM_AGENT_CONFIG_MUTATION": "false",
    "CBM_HOOK_MUTATION": "false",
    "CBM_INSTRUCTION_MUTATION": "false",
    "CBM_SHARED_GRAPH_EXPORT": "false",
    "CBM_DIAGNOSTICS_FILES": "false",
    "CBM_SEMANTIC_MODEL": "false",
}


class CodebaseMemoryProcessError(RuntimeError):
    """Content-free process error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProcessState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EgressEnforcementReceipt:
    enforced: bool
    mechanism: str
    scope_ref: str
    successful_network_calls: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.enforced, bool):
            raise CodebaseMemoryProcessError("invalid_egress_receipt", "egress enforced must be boolean")
        object.__setattr__(self, "mechanism", _token(self.mechanism, "egress mechanism"))
        object.__setattr__(self, "scope_ref", _token(self.scope_ref, "egress scope_ref"))
        if (
            isinstance(self.successful_network_calls, bool)
            or not isinstance(self.successful_network_calls, int)
            or self.successful_network_calls < 0
        ):
            raise CodebaseMemoryProcessError(
                "invalid_egress_receipt", "successful_network_calls must be a non-negative integer"
            )

    @property
    def ready(self) -> bool:
        return self.enforced and self.successful_network_calls == 0


@dataclass(frozen=True, slots=True)
class CodebaseMemoryProcessSettings:
    executable_path: Path
    config_dir: Path
    data_dir: Path
    allowed_root: Path
    launch_arguments: tuple[str, ...] = ()
    enabled: bool = False
    locked_version: str = CBM_LOCKED_VERSION
    locked_commit: str = CBM_LOCKED_COMMIT
    transport: str = CBM_TRANSPORT
    startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    shutdown_timeout_s: float = DEFAULT_SHUTDOWN_TIMEOUT_S
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    egress_receipt: EgressEnforcementReceipt | None = None

    def __post_init__(self) -> None:
        executable = _absolute_path(self.executable_path, "executable_path")
        config_dir = _absolute_path(self.config_dir, "config_dir")
        data_dir = _absolute_path(self.data_dir, "data_dir")
        allowed_root = _absolute_path(self.allowed_root, "allowed_root")
        if _contains(allowed_root, config_dir) or _contains(allowed_root, data_dir):
            raise CodebaseMemoryProcessError(
                "unsafe_path_layout", "config_dir and data_dir must remain outside allowed_root"
            )
        arguments = _arguments(self.launch_arguments)
        if not isinstance(self.enabled, bool):
            raise CodebaseMemoryProcessError("invalid_settings", "enabled must be boolean")
        if self.locked_version != CBM_LOCKED_VERSION or self.locked_commit != CBM_LOCKED_COMMIT:
            raise CodebaseMemoryProcessError("vendor_lock_mismatch", "process settings do not match the vendor lock")
        if self.transport != CBM_TRANSPORT:
            raise CodebaseMemoryProcessError("transport_not_allowed", "only stdio transport is allowed")
        for value, label, maximum in (
            (self.startup_timeout_s, "startup_timeout_s", 60.0),
            (self.request_timeout_s, "request_timeout_s", 120.0),
            (self.shutdown_timeout_s, "shutdown_timeout_s", 30.0),
        ):
            _bounded_timeout(value, label, maximum=maximum)
        if (
            isinstance(self.max_message_bytes, bool)
            or not isinstance(self.max_message_bytes, int)
            or not 1024 <= self.max_message_bytes <= 16_777_216
        ):
            raise CodebaseMemoryProcessError(
                "invalid_settings", "max_message_bytes must be between 1024 and 16777216"
            )
        if self.egress_receipt is not None and not isinstance(
            self.egress_receipt, EgressEnforcementReceipt
        ):
            raise CodebaseMemoryProcessError(
                "invalid_egress_receipt", "egress_receipt must be typed"
            )
        object.__setattr__(self, "executable_path", executable)
        object.__setattr__(self, "config_dir", config_dir)
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "allowed_root", allowed_root)
        object.__setattr__(self, "launch_arguments", arguments)
        object.__setattr__(self, "startup_timeout_s", float(self.startup_timeout_s))
        object.__setattr__(self, "request_timeout_s", float(self.request_timeout_s))
        object.__setattr__(self, "shutdown_timeout_s", float(self.shutdown_timeout_s))

    @property
    def command(self) -> tuple[str, ...]:
        return (str(self.executable_path), *self.launch_arguments)

    def content_free_summary(self) -> dict[str, Any]:
        return {
            "schema": CBM_PROCESS_CONTRACT,
            "enabled": self.enabled,
            "locked_version": self.locked_version,
            "locked_commit": self.locked_commit,
            "transport": self.transport,
            "executable_ref": _path_ref(self.executable_path),
            "config_ref": _path_ref(self.config_dir),
            "data_ref": _path_ref(self.data_dir),
            "allowed_root_ref": _path_ref(self.allowed_root),
            "launch_argument_count": len(self.launch_arguments),
            "egress_enforced": bool(self.egress_receipt and self.egress_receipt.ready),
            "runtime_controls": dict(_CONTROL_ENV),
        }


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    schema: str
    state: ProcessState
    start_count: int
    pid_present: bool
    return_code: int | None
    stop_reason: str
    transport: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "state": self.state.value,
            "start_count": self.start_count,
            "pid_present": self.pid_present,
            "return_code": self.return_code,
            "stop_reason": self.stop_reason,
            "transport": self.transport,
        }


class AsyncProcess(Protocol):
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None
    pid: int
    returncode: int | None

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessLauncher = Callable[..., Awaitable[AsyncProcess]]


async def _default_launcher(*command: str, **kwargs: Any) -> AsyncProcess:
    return await asyncio.create_subprocess_exec(*command, **kwargs)


class CodebaseMemoryProcess:
    """One restartable stdio child with bounded lifecycle and request exchange."""

    def __init__(
        self,
        settings: CodebaseMemoryProcessSettings,
        *,
        launcher: ProcessLauncher | None = None,
        inherited_environment: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(settings, CodebaseMemoryProcessSettings):
            raise CodebaseMemoryProcessError("invalid_settings", "settings must be typed")
        self.settings = settings
        self._launcher = launcher or _default_launcher
        self._inherited_environment = dict(
            os.environ if inherited_environment is None else inherited_environment
        )
        self._process: AsyncProcess | None = None
        self._state = ProcessState.STOPPED
        self._start_count = 0
        self._stop_reason = "not_started"
        self._exchange_lock = asyncio.Lock()

    @property
    def process(self) -> AsyncProcess | None:
        return self._process

    def snapshot(self) -> ProcessSnapshot:
        process = self._process
        return ProcessSnapshot(
            schema=CBM_PROCESS_CONTRACT,
            state=self._state,
            start_count=self._start_count,
            pid_present=bool(process and getattr(process, "pid", 0)),
            return_code=None if process is None else process.returncode,
            stop_reason=self._stop_reason,
            transport=CBM_TRANSPORT,
        )

    async def start(self) -> ProcessSnapshot:
        if not self.settings.enabled:
            raise CodebaseMemoryProcessError("process_disabled", "Codebase Memory process is disabled")
        if self._state in {ProcessState.STARTING, ProcessState.RUNNING, ProcessState.STOPPING}:
            raise CodebaseMemoryProcessError("process_already_active", "process is already active")
        receipt = self.settings.egress_receipt
        if receipt is None or not receipt.ready:
            raise CodebaseMemoryProcessError(
                "egress_not_enforced", "an enforced zero-successful-call egress receipt is required"
            )
        self._verify_paths()
        self._state = ProcessState.STARTING
        self._stop_reason = ""
        try:
            process = await asyncio.wait_for(
                self._launcher(
                    *self.settings.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.settings.data_dir),
                    env=build_scrubbed_environment(
                        self._inherited_environment, self.settings
                    ),
                    limit=self.settings.max_message_bytes + 1,
                ),
                timeout=self.settings.startup_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            self._state = ProcessState.FAILED
            self._stop_reason = "startup_timeout"
            raise CodebaseMemoryProcessError("startup_timeout", "process launch timed out") from exc
        except Exception as exc:
            self._state = ProcessState.FAILED
            self._stop_reason = "startup_failed"
            raise CodebaseMemoryProcessError("startup_failed", "process launch failed") from exc
        if process.stdin is None or process.stdout is None:
            self._process = process
            self._state = ProcessState.FAILED
            self._stop_reason = "stdio_unavailable"
            await self.stop(reason="stdio_unavailable")
            raise CodebaseMemoryProcessError("stdio_unavailable", "child stdio is unavailable")
        self._process = process
        self._state = ProcessState.RUNNING
        self._start_count += 1
        return self.snapshot()

    async def exchange(self, request: bytes, *, timeout_s: float | None = None) -> bytes:
        if not isinstance(request, bytes) or not request or b"\n" in request or b"\r" in request:
            raise CodebaseMemoryProcessError("invalid_request", "request must be one non-empty JSON line")
        if len(request) > self.settings.max_message_bytes:
            raise CodebaseMemoryProcessError("request_too_large", "request exceeds the message limit")
        timeout = self.settings.request_timeout_s if timeout_s is None else _bounded_timeout(
            timeout_s, "timeout_s", maximum=self.settings.request_timeout_s
        )
        async with self._exchange_lock:
            process = self._running_process()
            assert process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(request + b"\n")
                await asyncio.wait_for(process.stdin.drain(), timeout=timeout)
                response = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                await self.stop(reason="request_timeout")
                raise CodebaseMemoryProcessError("request_timeout", "child response timed out") from exc
            except (ValueError, asyncio.LimitOverrunError) as exc:
                await self.stop(reason="response_too_large")
                raise CodebaseMemoryProcessError(
                    "response_too_large", "child response exceeds the message limit"
                ) from exc
            except asyncio.CancelledError:
                await asyncio.shield(self.stop(reason="request_cancelled"))
                raise
            except Exception as exc:
                await self.stop(reason="stdio_failure")
                raise CodebaseMemoryProcessError("stdio_failure", "child stdio exchange failed") from exc
            if not response:
                return_code = process.returncode
                await self.stop(reason="child_exited")
                raise CodebaseMemoryProcessError(
                    "child_exited", f"child exited before response (code={return_code})"
                )
            if len(response) > self.settings.max_message_bytes:
                await self.stop(reason="response_too_large")
                raise CodebaseMemoryProcessError(
                    "response_too_large", "child response exceeds the message limit"
                )
            return response.rstrip(b"\r\n")

    async def stop(self, *, reason: str = "requested") -> ProcessSnapshot:
        safe_reason = _token(reason, "stop reason")
        process = self._process
        if process is None:
            self._state = ProcessState.STOPPED
            self._stop_reason = safe_reason
            return self.snapshot()
        self._state = ProcessState.STOPPING
        release_handle = True
        try:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(
                        process.wait(), timeout=self.settings.shutdown_timeout_s
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.wait_for(
                        process.wait(), timeout=self.settings.shutdown_timeout_s
                    )
        except (ProcessLookupError, RuntimeError):
            pass
        except asyncio.TimeoutError as exc:
            self._state = ProcessState.FAILED
            self._stop_reason = "kill_timeout"
            release_handle = False
            raise CodebaseMemoryProcessError("kill_timeout", "child did not stop after kill") from exc
        finally:
            if release_handle:
                stdin = getattr(process, "stdin", None)
                if stdin is not None:
                    try:
                        stdin.close()
                    except Exception:
                        pass
                self._process = None
        self._state = ProcessState.STOPPED
        self._stop_reason = safe_reason
        return self.snapshot()

    async def cancel(self) -> ProcessSnapshot:
        return await self.stop(reason="cancelled")

    def _running_process(self) -> AsyncProcess:
        process = self._process
        if self._state is not ProcessState.RUNNING or process is None:
            raise CodebaseMemoryProcessError("process_not_running", "process is not running")
        if process.returncode is not None:
            self._state = ProcessState.FAILED
            self._stop_reason = "child_exited"
            raise CodebaseMemoryProcessError(
                "child_exited", f"child is not running (code={process.returncode})"
            )
        return process

    def _verify_paths(self) -> None:
        if not self.settings.executable_path.is_file():
            raise CodebaseMemoryProcessError("executable_missing", "explicit executable is unavailable")
        for path, label in (
            (self.settings.config_dir, "config_dir"),
            (self.settings.data_dir, "data_dir"),
            (self.settings.allowed_root, "allowed_root"),
        ):
            if not path.is_dir():
                raise CodebaseMemoryProcessError("directory_missing", f"explicit {label} is unavailable")


def build_scrubbed_environment(
    inherited: Mapping[str, str], settings: CodebaseMemoryProcessSettings
) -> dict[str, str]:
    """Build the only environment passed to the isolated child."""

    if not isinstance(inherited, Mapping):
        raise CodebaseMemoryProcessError("invalid_environment", "inherited environment must be a mapping")
    result: dict[str, str] = {}
    for key, value in inherited.items():
        normalized_key = str(key).upper()
        if normalized_key in _INHERITED_ENV_ALLOWLIST and isinstance(value, str):
            result[normalized_key] = value
    result.update(_CONTROL_ENV)
    result.update(
        {
            "CBM_CONFIG_DIR": str(settings.config_dir),
            "CBM_DATA_DIR": str(settings.data_dir),
            "CBM_ALLOWED_ROOT": str(settings.allowed_root),
            "CBM_LOCKED_VERSION": settings.locked_version,
            "CBM_LOCKED_COMMIT": settings.locked_commit,
        }
    )
    return result


def _absolute_path(value: Any, field_name: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise CodebaseMemoryProcessError("invalid_path", f"{field_name} must be a path")
    if str(value).startswith("~"):
        raise CodebaseMemoryProcessError("invalid_path", f"{field_name} must be explicit and absolute")
    path = Path(value)
    if not path.is_absolute():
        raise CodebaseMemoryProcessError("invalid_path", f"{field_name} must be explicit and absolute")
    return path.resolve(strict=False)


def _contains(parent: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _arguments(values: Sequence[Any]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise CodebaseMemoryProcessError("invalid_arguments", "launch_arguments must be a sequence")
    items = tuple(values)
    if len(items) > 32:
        raise CodebaseMemoryProcessError("invalid_arguments", "launch_arguments are unbounded")
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or len(item) > 4096 or _CONTROL_RE.search(item):
            raise CodebaseMemoryProcessError("invalid_arguments", "launch argument is invalid")
        lowered = item.lower()
        if lowered in _FORBIDDEN_ARGUMENTS or lowered.startswith(_FORBIDDEN_ARGUMENT_PREFIXES):
            raise CodebaseMemoryProcessError("forbidden_argument", "launch argument enables a forbidden surface")
        result.append(item)
    return tuple(result)


def _bounded_timeout(value: Any, field_name: str, *, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodebaseMemoryProcessError("invalid_timeout", f"{field_name} must be numeric")
    result = float(value)
    if not 0.01 <= result <= maximum:
        raise CodebaseMemoryProcessError("invalid_timeout", f"{field_name} is outside its bounded range")
    return result


def _token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise CodebaseMemoryProcessError("invalid_token", f"{field_name} must be a bounded token")
    return value


def _path_ref(path: Path) -> str:
    normalized = str(path).replace("\\", "/").casefold()
    return "path_sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_request(value: Mapping[str, Any]) -> bytes:
    """Return one bounded JSON request without a trailing newline."""

    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CodebaseMemoryProcessError("invalid_request", "request is not canonical JSON") from exc
    return rendered
