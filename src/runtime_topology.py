"""Supported Odysseus web-process topology.

Long-running Temporal workers and Uvicorn web workers are different runtime
domains.  This module deliberately constrains only the web process count: the
current authentication limiter and rotating file log sink are process-local,
so more than one web worker would make their behavior inconsistent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from typing import Any

from src.rate_limiter import RATE_LIMITER_STATE_SCOPE


SUPPORTED_WEB_WORKERS = 1
WEB_WORKER_ENV_KEYS = (
    "ODYSSEUS_WEB_WORKERS",
    "WEB_CONCURRENCY",
    "UVICORN_WORKERS",
)
ROTATING_FILE_LOG_SINK_SCOPE = "process_local"
MULTI_WORKER_PREREQUISITES = (
    "shared_or_distributed_auth_rate_limiter",
    "single_log_queue_listener_or_external_collector",
    "cross_process_mutable_state_audit",
    "multi_process_load_and_failure_acceptance",
)


class RuntimeTopologyError(RuntimeError):
    """Raised when startup configuration represents an unsupported topology."""

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        configured_workers: int | None = None,
        source: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.configured_workers = configured_workers
        self.source = source


@dataclass(frozen=True)
class RuntimeTopology:
    """Resolved and supported runtime topology for one Odysseus web process."""

    web_workers: int
    source: str

    def readiness(self) -> dict[str, Any]:
        reason = (
            "Exactly one Odysseus web worker is supported because the auth "
            "rate limiter and rotating file log sink are process-local. "
            "Temporal workers are a separate worker domain and do not change "
            "the web worker count."
        )
        return {
            "ready": True,
            "state": "supported_one_web_worker",
            "web_workers": self.web_workers,
            "source": self.source,
            "reason": reason,
            "process_local_components": [
                "auth_rate_limiter",
                "rotating_file_log_sink",
            ],
            "rate_limiter_scope": RATE_LIMITER_STATE_SCOPE,
            "rotating_file_log_sink_scope": ROTATING_FILE_LOG_SINK_SCOPE,
            "temporal_workers_are_web_workers": False,
            "multi_worker_readiness": {
                "ready": False,
                "state": "blocked",
                "reason": "Multi-worker web serving is not supported by the current process-local state boundaries.",
                "prerequisites": list(MULTI_WORKER_PREREQUISITES),
            },
        }


def _parse_worker_count(raw: object, *, source: str) -> int:
    value = str(raw).strip()
    if not value or not value.isdecimal():
        raise RuntimeTopologyError(
            "invalid_web_worker_count",
            f"{source} must be the integer 1; received {raw!r}.",
            source=source,
        )
    workers = int(value)
    if workers != SUPPORTED_WEB_WORKERS:
        raise RuntimeTopologyError(
            "unsupported_web_worker_count",
            (
                f"Exactly one Odysseus web worker is supported; {source} configured {workers}. "
                "The auth rate limiter and rotating file log sink are process-local. "
                "Temporal workers are a separate worker domain, not additional web workers."
            ),
            configured_workers=workers,
            source=source,
        )
    return workers


def resolve_runtime_topology(
    env: Mapping[str, object] | None = None,
    *,
    argv: Sequence[object] | None = None,
) -> RuntimeTopology:
    """Resolve the supported web worker count from explicit environment input."""

    values = env if env is not None else os.environ
    configured: list[tuple[str, int]] = []
    for key in WEB_WORKER_ENV_KEYS:
        if key in values:
            configured.append((key, _parse_worker_count(values[key], source=key)))

    command = [str(item) for item in (argv or ())]
    if command and "uvicorn" in command[0].replace("\\", "/").lower():
        index = 1
        while index < len(command):
            argument = command[index]
            if argument == "--workers":
                if index + 1 >= len(command):
                    raise RuntimeTopologyError(
                        "invalid_web_worker_count",
                        "uvicorn --workers requires the integer 1.",
                        source="uvicorn_cli",
                    )
                configured.append(
                    (
                        "uvicorn_cli",
                        _parse_worker_count(command[index + 1], source="uvicorn_cli"),
                    )
                )
                index += 2
                continue
            if argument.startswith("--workers="):
                configured.append(
                    (
                        "uvicorn_cli",
                        _parse_worker_count(argument.split("=", 1)[1], source="uvicorn_cli"),
                    )
                )
            index += 1

    if not configured:
        return RuntimeTopology(web_workers=SUPPORTED_WEB_WORKERS, source="default")

    unique_counts = {count for _, count in configured}
    if len(unique_counts) != 1:
        # This branch is defensive: _parse_worker_count currently rejects every
        # value other than one before a conflict can become supported.
        sources = ",".join(key for key, _ in configured)
        raise RuntimeTopologyError(
            "conflicting_web_worker_configuration",
            f"Web worker settings disagree across {sources}; exactly one worker is required.",
            source=sources,
        )
    return RuntimeTopology(
        web_workers=configured[0][1],
        source="+".join(key for key, _ in configured),
    )


def assert_supported_runtime_topology(
    env: Mapping[str, object] | None = None,
    *,
    argv: Sequence[object] | None = None,
) -> RuntimeTopology:
    """Fail startup unless the configured topology is explicitly supported."""

    return resolve_runtime_topology(env, argv=argv)


def runtime_topology_readiness(
    env: Mapping[str, object] | None = None,
    *,
    argv: Sequence[object] | None = None,
) -> dict[str, Any]:
    """Return a stable readiness packet without weakening startup rejection."""

    try:
        return resolve_runtime_topology(env, argv=argv).readiness()
    except RuntimeTopologyError as exc:
        return {
            "ready": False,
            "state": "blocked",
            "code": exc.code,
            "reason": exc.reason,
            "configured_workers": exc.configured_workers,
            "source": exc.source,
            "temporal_workers_are_web_workers": False,
            "multi_worker_readiness": {
                "ready": False,
                "state": "blocked",
                "prerequisites": list(MULTI_WORKER_PREREQUISITES),
            },
        }
