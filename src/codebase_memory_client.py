"""Strict JSON-RPC health client for the isolated Codebase Memory child."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any, Mapping

from src.codebase_memory_process import (
    CBM_LOCKED_COMMIT,
    CBM_LOCKED_VERSION,
    CBM_TRANSPORT,
    CodebaseMemoryProcess,
    CodebaseMemoryProcessError,
    canonical_request,
)


CBM_ADAPTER_PROTOCOL = "odysseus.codebase_memory.adapter.v1"
MAX_CAPABILITIES = 64
MAX_ACTIVE_PROJECTS = 10_000

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_RUNTIME_CONTROL_FIELDS = frozenset(
    {
        "auto_watch",
        "auto_index",
        "ui",
        "update_check",
        "network_egress",
        "installer",
        "self_update",
        "agent_config_mutation",
        "hook_mutation",
        "instruction_mutation",
        "shared_graph_export",
        "diagnostics_files",
        "semantic_model",
        "egress_enforced",
    }
)


class CodebaseMemoryClientError(RuntimeError):
    """Content-free client error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class RuntimeControls:
    auto_watch: bool
    auto_index: bool
    ui: bool
    update_check: bool
    network_egress: bool
    installer: bool
    self_update: bool
    agent_config_mutation: bool
    hook_mutation: bool
    instruction_mutation: bool
    shared_graph_export: bool
    diagnostics_files: bool
    semantic_model: bool
    egress_enforced: bool

    def __post_init__(self) -> None:
        for field_name in _RUNTIME_CONTROL_FIELDS:
            if not isinstance(getattr(self, field_name), bool):
                raise CodebaseMemoryClientError(
                    "invalid_runtime_controls", f"{field_name} must be boolean"
                )
        forbidden_enabled = {
            field_name
            for field_name in _RUNTIME_CONTROL_FIELDS - {"egress_enforced"}
            if getattr(self, field_name)
        }
        if forbidden_enabled or not self.egress_enforced:
            raise CodebaseMemoryClientError(
                "unsafe_runtime_controls", "runtime controls do not preserve the disabled boundary"
            )

    def to_dict(self) -> dict[str, bool]:
        return {field_name: getattr(self, field_name) for field_name in sorted(_RUNTIME_CONTROL_FIELDS)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuntimeControls":
        if not isinstance(value, Mapping) or set(value) != _RUNTIME_CONTROL_FIELDS:
            raise CodebaseMemoryClientError(
                "invalid_runtime_controls", "runtime control fields are incomplete"
            )
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class EngineHandshake:
    protocol_version: str
    engine_version: str
    engine_commit: str
    transport: str
    capabilities: tuple[str, ...]
    runtime_controls: RuntimeControls

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "transport": self.transport,
            "capabilities": list(self.capabilities),
            "runtime_controls": self.runtime_controls.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EngineHealth:
    status: HealthStatus
    ready: bool
    protocol_version: str
    engine_version: str
    engine_commit: str
    capabilities: tuple[str, ...]
    runtime_controls: RuntimeControls
    active_projects: int
    successful_network_calls: int
    last_error_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "ready": self.ready,
            "protocol_version": self.protocol_version,
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "capabilities": list(self.capabilities),
            "runtime_controls": self.runtime_controls.to_dict(),
            "active_projects": self.active_projects,
            "successful_network_calls": self.successful_network_calls,
            "last_error_code": self.last_error_code,
        }


class CodebaseMemoryClient:
    """Expose initialization and health only; no upstream tools are public here."""

    def __init__(self, process: CodebaseMemoryProcess) -> None:
        if not isinstance(process, CodebaseMemoryProcess):
            raise CodebaseMemoryClientError("invalid_process", "process must be typed")
        self.process = process
        self._next_id = 1
        self._handshake: EngineHandshake | None = None
        self._health: EngineHealth | None = None

    @property
    def handshake(self) -> EngineHandshake | None:
        return self._handshake

    @property
    def last_health(self) -> EngineHealth | None:
        return self._health

    async def open(self) -> EngineHealth:
        if self._handshake is not None:
            raise CodebaseMemoryClientError("client_already_open", "client is already open")
        try:
            await self.process.start()
            initialize_result = await self._request(
                "initialize",
                {
                    "adapter_protocol": CBM_ADAPTER_PROTOCOL,
                    "locked_version": CBM_LOCKED_VERSION,
                    "locked_commit": CBM_LOCKED_COMMIT,
                    "transport": CBM_TRANSPORT,
                    "expose_direct_tools": False,
                },
            )
            self._handshake = _parse_handshake(initialize_result)
            self._health = await self.health()
            if self._health.status is not HealthStatus.HEALTHY or not self._health.ready:
                raise CodebaseMemoryClientError("engine_not_ready", "engine health is not ready")
            return self._health
        except asyncio.CancelledError:
            await asyncio.shield(self.process.stop(reason="client_open_cancelled"))
            self._handshake = None
            self._health = None
            raise
        except CodebaseMemoryClientError:
            await self.process.stop(reason="client_open_failed")
            self._handshake = None
            self._health = None
            raise
        except CodebaseMemoryProcessError as exc:
            await self.process.stop(reason="client_open_failed")
            self._handshake = None
            self._health = None
            raise CodebaseMemoryClientError(exc.code, "isolated process operation failed") from exc

    async def health(self) -> EngineHealth:
        if self._handshake is None:
            raise CodebaseMemoryClientError("client_not_initialized", "client is not initialized")
        result = await self._request("health", {})
        health = _parse_health(result)
        if (
            health.protocol_version != self._handshake.protocol_version
            or health.engine_version != self._handshake.engine_version
            or health.engine_commit != self._handshake.engine_commit
            or health.capabilities != self._handshake.capabilities
            or health.runtime_controls != self._handshake.runtime_controls
        ):
            raise CodebaseMemoryClientError(
                "health_drift", "health response does not match the initialization contract"
            )
        self._health = health
        return health

    async def close(self) -> None:
        if self._handshake is not None:
            try:
                await self._request("shutdown", {})
            except (CodebaseMemoryClientError, CodebaseMemoryProcessError):
                pass
        await self.process.stop(reason="client_closed")
        self._handshake = None
        self._health = None

    async def _request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        if method not in {"initialize", "health", "shutdown"}:
            raise CodebaseMemoryClientError("method_not_allowed", "method is not exposed by this adapter")
        request_id = self._next_id
        self._next_id += 1
        request = canonical_request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
        )
        try:
            raw = await self.process.exchange(request)
        except CodebaseMemoryProcessError as exc:
            raise CodebaseMemoryClientError(exc.code, "isolated process request failed") from exc
        payload = _json_object(raw)
        if set(payload) not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"}):
            raise CodebaseMemoryClientError("invalid_response", "JSON-RPC response fields are invalid")
        response_id = payload.get("id")
        if (
            payload.get("jsonrpc") != "2.0"
            or isinstance(response_id, bool)
            or not isinstance(response_id, int)
            or response_id != request_id
        ):
            raise CodebaseMemoryClientError("response_mismatch", "JSON-RPC response identity is invalid")
        if "error" in payload:
            error = payload["error"]
            if not isinstance(error, Mapping) or set(error) - {"code", "message", "data"}:
                raise CodebaseMemoryClientError("invalid_remote_error", "remote error is malformed")
            raise CodebaseMemoryClientError("remote_error", "isolated engine returned an error")
        result = payload["result"]
        if not isinstance(result, Mapping):
            raise CodebaseMemoryClientError("invalid_response", "JSON-RPC result must be an object")
        return result


def _parse_handshake(value: Mapping[str, Any]) -> EngineHandshake:
    required = {
        "protocol_version",
        "engine_version",
        "engine_commit",
        "transport",
        "capabilities",
        "runtime_controls",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise CodebaseMemoryClientError("invalid_handshake", "handshake fields are incomplete")
    protocol = _token(value["protocol_version"], "protocol_version")
    engine_version = _token(value["engine_version"], "engine_version")
    engine_commit = _commit(value["engine_commit"])
    transport = _token(value["transport"], "transport")
    if protocol != CBM_ADAPTER_PROTOCOL:
        raise CodebaseMemoryClientError("protocol_mismatch", "adapter protocol version is unsupported")
    if engine_version != CBM_LOCKED_VERSION or engine_commit != CBM_LOCKED_COMMIT:
        raise CodebaseMemoryClientError("engine_version_mismatch", "engine does not match the vendor lock")
    if transport != CBM_TRANSPORT:
        raise CodebaseMemoryClientError("transport_mismatch", "engine transport is not stdio")
    capabilities = _capabilities(value["capabilities"])
    controls = RuntimeControls.from_dict(value["runtime_controls"])
    return EngineHandshake(protocol, engine_version, engine_commit, transport, capabilities, controls)


def _parse_health(value: Mapping[str, Any]) -> EngineHealth:
    required = {
        "status",
        "ready",
        "protocol_version",
        "engine_version",
        "engine_commit",
        "capabilities",
        "runtime_controls",
        "active_projects",
        "successful_network_calls",
        "last_error_code",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise CodebaseMemoryClientError("invalid_health", "health fields are incomplete")
    try:
        status = HealthStatus(value["status"])
    except (TypeError, ValueError) as exc:
        raise CodebaseMemoryClientError("invalid_health", "health status is invalid") from exc
    if not isinstance(value["ready"], bool):
        raise CodebaseMemoryClientError("invalid_health", "health ready must be boolean")
    protocol = _token(value["protocol_version"], "protocol_version")
    engine_version = _token(value["engine_version"], "engine_version")
    engine_commit = _commit(value["engine_commit"])
    if protocol != CBM_ADAPTER_PROTOCOL:
        raise CodebaseMemoryClientError("protocol_mismatch", "health protocol version is unsupported")
    if engine_version != CBM_LOCKED_VERSION or engine_commit != CBM_LOCKED_COMMIT:
        raise CodebaseMemoryClientError("engine_version_mismatch", "health engine does not match the vendor lock")
    capabilities = _capabilities(value["capabilities"])
    controls = RuntimeControls.from_dict(value["runtime_controls"])
    active_projects = _bounded_integer(value["active_projects"], "active_projects", MAX_ACTIVE_PROJECTS)
    network_calls = _bounded_integer(
        value["successful_network_calls"], "successful_network_calls", 1_000_000
    )
    if network_calls != 0:
        raise CodebaseMemoryClientError("network_boundary_breached", "successful network calls are non-zero")
    error_code = value["last_error_code"]
    if error_code != "":
        error_code = _token(error_code, "last_error_code")
    if status is HealthStatus.HEALTHY and not value["ready"]:
        raise CodebaseMemoryClientError("invalid_health", "healthy status must be ready")
    return EngineHealth(
        status,
        value["ready"],
        protocol,
        engine_version,
        engine_commit,
        capabilities,
        controls,
        active_projects,
        network_calls,
        error_code,
    )


def _capabilities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_CAPABILITIES:
        raise CodebaseMemoryClientError("invalid_capabilities", "capabilities must be non-empty and bounded")
    capabilities = tuple(_token(item, "capability") for item in value)
    if len(set(capabilities)) != len(capabilities) or "health" not in capabilities:
        raise CodebaseMemoryClientError(
            "invalid_capabilities", "capabilities must be unique and include health"
        )
    return tuple(sorted(capabilities))


def _json_object(raw: bytes) -> Mapping[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise CodebaseMemoryClientError("duplicate_response_field", "response has duplicate fields")
            result[key] = item
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except CodebaseMemoryClientError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise CodebaseMemoryClientError("malformed_json", "engine returned malformed JSON") from exc
    if not isinstance(value, Mapping):
        raise CodebaseMemoryClientError("invalid_response", "engine response must be an object")
    return value


def _token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise CodebaseMemoryClientError("invalid_token", f"{field_name} must be a bounded token")
    return value


def _commit(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise CodebaseMemoryClientError("invalid_commit", "engine_commit must be lowercase SHA-1")
    return value


def _bounded_integer(value: Any, field_name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise CodebaseMemoryClientError("invalid_health", f"{field_name} is outside its bounded range")
    return value
