"""Explicitly gated, content-free Gemma 3 maintenance live canary.

The module is safe to import and defaults to refusal. Network/model work is
possible only with both ``--execute`` and the exact live approval environment
value. The global maintenance setting must remain false for the entire canary;
an ephemeral typed profile enables only these bounded calls.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
import time
from typing import Any, Awaitable, Callable

from src.maintenance_llm_runtime import (
    MaintenanceLLMMessage,
    MaintenanceLLMRequest,
    MaintenanceLLMUpstreamAttempt,
    MaintenanceLLMUpstreamResponse,
    call_maintenance_llm_async,
)
from src.maintenance_model_policy import MaintenanceModelProfile
from src.observability_metrics import (
    maintenance_runtime_metrics_snapshot,
    reset_maintenance_runtime_metrics,
)
from src.settings import load_settings


SCHEMA = "odysseus.gemma3_maintenance_live_canary.v1"
APPROVAL_ENV = "GMI_LIVE_APPROVAL"
APPROVAL_VALUE = "GO GMI-LIVE-ACTIVATION"
WARMUP_CALLS = 1
MEASURED_CALLS = 20
P95_LIMIT_SECONDS = 30.0
MAX_LIMIT_SECONDS = 45.0
EVENT_LOOP_GAP_LIMIT_SECONDS = 0.1
BLOCKED_EXIT = 3

EXPECTED_SETTINGS: dict[str, Any] = {
    "maintenance_model_ref": "gemma3:4b",
    "maintenance_model_provider": "local_ollama",
    "maintenance_model_token_budget": 1200,
    "maintenance_model_max_input_chars": 6000,
    "maintenance_model_chunk_budget": 4,
    "maintenance_model_source_ref_budget": 4,
    "maintenance_model_latency_budget_ms": 45000,
    "maintenance_model_api_fallback_enabled": False,
    "maintenance_runtime_enabled": False,
}

AsyncAttempt = Callable[
    [MaintenanceLLMUpstreamAttempt], Awaitable[MaintenanceLLMUpstreamResponse]
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nearest_rank_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _settings_match(settings: dict[str, Any]) -> tuple[bool, tuple[str, ...]]:
    mismatches = tuple(
        key for key, expected in EXPECTED_SETTINGS.items() if settings.get(key) != expected
    )
    return not mismatches, mismatches


async def _heartbeat(stop: asyncio.Event, gaps: list[float]) -> None:
    interval = 0.02
    previous = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(interval)
        current = time.perf_counter()
        gaps.append(max(0.0, current - previous - interval))
        previous = current


def _request(endpoint: str) -> MaintenanceLLMRequest:
    return MaintenanceLLMRequest(
        endpoint=endpoint,
        messages=(
            MaintenanceLLMMessage(
                role="system",
                content="This is a bounded maintenance readiness check. Return READY only.",
            ),
            MaintenanceLLMMessage(role="user", content="READY"),
        ),
        profile=MaintenanceModelProfile.create(runtime_enabled=True),
        max_tokens=8,
        timeout_ms=45000,
        max_attempts=1,
        temperature=0.0,
        fallback_requested=False,
        truth_write_requested=False,
    )


async def execute_canary(
    endpoint: str,
    *,
    attempt: AsyncAttempt | None = None,
    warmup_calls: int = WARMUP_CALLS,
    measured_calls: int = MEASURED_CALLS,
) -> dict[str, Any]:
    """Execute the bounded calls and return aggregates without response text."""

    reset_maintenance_runtime_metrics()
    started_at = _utc_now()
    durations: list[float] = []
    failure_codes: list[str] = []
    warmup_success = 0
    measured_success = 0
    gaps: list[float] = []
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(stop, gaps))
    request = _request(endpoint)
    try:
        for index in range(warmup_calls + measured_calls):
            call_started = time.perf_counter()
            try:
                result = await call_maintenance_llm_async(request, attempt=attempt)
                if not result.text:
                    raise RuntimeError("empty_result")
            except Exception as exc:  # content-free closed type only
                failure_codes.append(type(exc).__name__)
                break
            duration = time.perf_counter() - call_started
            if index < warmup_calls:
                warmup_success += 1
            else:
                durations.append(duration)
                measured_success += 1
            await asyncio.sleep(0)
    finally:
        stop.set()
        await heartbeat

    p95 = _nearest_rank_percentile(durations, 0.95)
    maximum = max(durations, default=None)
    max_gap = max(gaps, default=0.0)
    metrics = maintenance_runtime_metrics_snapshot()
    gates = {
        "warmup_complete": warmup_success == warmup_calls,
        "measured_count_exact": len(durations) == measured_calls,
        "success_count_exact": measured_success == measured_calls,
        "p95_lt_30_seconds": p95 is not None and p95 < P95_LIMIT_SECONDS,
        "max_lt_45_seconds": maximum is not None and maximum < MAX_LIMIT_SECONDS,
        "event_loop_gap_lt_100ms": max_gap < EVENT_LOOP_GAP_LIMIT_SECONDS,
        "no_failures": not failure_codes,
        "fallback_used": False,
        "truth_write_performed": False,
    }
    verdict = "go" if all(
        value is True
        for key, value in gates.items()
        if key not in {"fallback_used", "truth_write_performed"}
    ) and not gates["fallback_used"] and not gates["truth_write_performed"] else "no_go"
    return {
        "schema": SCHEMA,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "model_scope": "gemma3_4b",
        "provider_scope": "local_ollama",
        "role_scope": "maintenance",
        "warmup_calls": warmup_calls,
        "warmup_success": warmup_success,
        "measured_calls": measured_calls,
        "success_calls": measured_success,
        "failure_calls": measured_calls - measured_success,
        "latency_p95_seconds": p95,
        "latency_max_seconds": maximum,
        "event_loop_max_gap_seconds": max_gap,
        "failure_codes": tuple(failure_codes),
        "process_local_metric_sample_count": len(metrics.get("samples", ())),
        "gates": gates,
        "global_runtime_changed": False,
        "response_content_recorded": False,
        "prompt_or_message_content_recorded": False,
        "verdict": verdict,
        "live_model_calls_performed": warmup_success + measured_success > 0,
    }


def evaluate_execution_gate(*, execute: bool, approval: str | None) -> dict[str, Any]:
    settings = load_settings()
    settings_valid, mismatches = _settings_match(settings)
    gates = {
        "execute_flag_present": execute,
        "exact_live_go_recorded": approval == APPROVAL_VALUE,
        "settings_contract_valid": settings_valid,
        "global_runtime_disabled": settings.get("maintenance_runtime_enabled") is False,
    }
    return {
        "allowed": all(gates.values()),
        "gates": gates,
        "setting_mismatch_keys": mismatches,
        "live_model_calls_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    gate = evaluate_execution_gate(
        execute=args.execute,
        approval=os.environ.get(APPROVAL_ENV),
    )
    if not gate["allowed"]:
        report = {
            "schema": SCHEMA,
            "status": "blocked",
            **gate,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return BLOCKED_EXIT
    if not args.endpoint:
        print(json.dumps({"schema": SCHEMA, "status": "blocked", "reason": "endpoint_missing"}))
        return BLOCKED_EXIT
    report = asyncio.run(execute_canary(args.endpoint))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "go" else BLOCKED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
