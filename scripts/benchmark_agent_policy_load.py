"""Deterministic SAR-09 Agent policy-load benchmark contract."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_llm_transport import (  # noqa: E402
    DEFAULT_OUTPUT,
    SECTION_AGENT_POLICY_LOAD,
    emit_receipt,
    nearest_rank_p95,
    update_baseline,
)


def decide_agent_policy_cache(
    *,
    iterations: int,
    p95_query_latency_ms: float,
    p95_start_latency_ms: float,
    synchronous_invalidation_proven: bool,
) -> dict[str, Any]:
    if type(iterations) is not int or not 1 <= iterations <= 10_000_000:
        raise ValueError("iterations must be positive")
    if type(p95_query_latency_ms) not in (int, float) or not math.isfinite(float(p95_query_latency_ms)):
        raise ValueError("query latency must be finite")
    if type(p95_start_latency_ms) not in (int, float) or not math.isfinite(float(p95_start_latency_ms)):
        raise ValueError("start latency must be finite")
    if p95_query_latency_ms < 0 or p95_start_latency_ms <= 0:
        raise ValueError("latencies must be non-negative and start p95 positive")
    if type(synchronous_invalidation_proven) is not bool:
        raise ValueError("synchronous_invalidation_proven must be a boolean")

    raw_query_share_percent = (p95_query_latency_ms / p95_start_latency_ms) * 100.0
    criteria = {
        "at_least_1000_starts": iterations >= 1_000,
        "latency_threshold_exceeded": (
            p95_query_latency_ms > 10.0 or raw_query_share_percent > 2.0
        ),
        "synchronous_invalidation_proven": synchronous_invalidation_proven is True,
    }
    failed = sorted(name for name, passed in criteria.items() if not passed)
    return {
        "result": "adopt" if not failed else "retain_current",
        "thresholds": {
            "minimum_iterations": 1_000,
            "p95_query_latency_ms_strictly_greater_than": 10.0,
            "query_share_percent_strictly_greater_than": 2.0,
        },
        "observed": {
            "query_share_percent": round(raw_query_share_percent, 6),
        },
        "criteria": criteria,
        "reason_codes": failed or ["all_adoption_criteria_satisfied"],
    }


def build_synthetic_agent_policy_section(*, iterations: int = 1_000) -> dict[str, Any]:
    if type(iterations) is not int or not 1 <= iterations <= 10_000_000:
        raise ValueError("iterations must be positive")

    # Integer microsecond fixtures make every local run byte-for-byte stable.
    query_samples_us = [2_500 + ((index * 37) % 900) for index in range(iterations)]
    start_samples_us = [250_000 + ((index * 101) % 20_000) for index in range(iterations)]
    p95_query_raw_ms = nearest_rank_p95(query_samples_us) / 1_000.0
    p95_start_raw_ms = nearest_rank_p95(start_samples_us) / 1_000.0
    decision = decide_agent_policy_cache(
        iterations=iterations,
        p95_query_latency_ms=p95_query_raw_ms,
        p95_start_latency_ms=p95_start_raw_ms,
        synchronous_invalidation_proven=False,
    )
    return {
        "schema_version": 1,
        "benchmark_id": "SAR-09-agent-policy-load",
        "measurement_origin": "synthetic_contract",
        "live_actions_performed": False,
        "network_calls_performed": False,
        "provider_calls_performed": False,
        "inputs": {
            "iterations": iterations,
        },
        "metrics": {
            "p95_query_latency_ms": round(p95_query_raw_ms, 6),
            "p95_start_latency_ms": round(p95_start_raw_ms, 6),
        },
        "invalidation": {
            "synchronous_proof": "not_proven",
        },
        "decision": decision,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the deterministic SAR-09 synthetic Agent policy receipt."
    )
    parser.add_argument(
        "--synthetic",
        action="store_const",
        const="synthetic",
        dest="mode",
        default="synthetic",
        help="Use deterministic local samples (the default and only local mode).",
    )
    parser.add_argument("--iterations", type=int, default=1_000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    section = build_synthetic_agent_policy_section(iterations=args.iterations)
    update_baseline(args.output, SECTION_AGENT_POLICY_LOAD, section)
    emit_receipt(SECTION_AGENT_POLICY_LOAD, section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
