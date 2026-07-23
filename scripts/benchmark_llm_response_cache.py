"""Deterministic, content-free SAR-09 response-cache baseline."""

from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_llm_transport import (  # noqa: E402
    DEFAULT_OUTPUT,
    SECTION_LLM_RESPONSE_CACHE,
    emit_receipt,
    update_baseline,
)


CACHE_METRIC_NAMES = frozenset({
    "churn_count",
    "eviction_count",
    "hit_count",
    "miss_count",
})


def synthetic_access_trace(*, blocks: int = 64) -> tuple[int, ...]:
    if type(blocks) is not int or not 1 <= blocks <= 1_000_000:
        raise ValueError("blocks must be a positive integer")
    trace: list[int] = []
    for block in range(blocks):
        first = block * 4
        # With capacity three, FIFO evicts `first` before its final access;
        # LRU keeps it because the middle access refreshes recency.
        trace.extend((first, first + 1, first + 2, first, first + 3, first))
    return tuple(trace)


def simulate_cache_policy(
    trace: tuple[int, ...],
    *,
    capacity: int = 3,
    policy: str,
) -> dict[str, int]:
    if type(capacity) is not int or not 1 <= capacity <= 1_000_000:
        raise ValueError("capacity must be a positive integer")
    if policy not in {"fifo", "lru"}:
        raise ValueError("policy must be fifo or lru")
    if type(trace) is not tuple or not trace or any(type(value) is not int for value in trace):
        raise ValueError("trace must be a non-empty tuple of integer identities")

    cache: OrderedDict[int, None] = OrderedDict()
    seen: set[int] = set()
    counters = {
        "churn_count": 0,
        "eviction_count": 0,
        "hit_count": 0,
        "miss_count": 0,
    }

    for identity in trace:
        if identity in cache:
            counters["hit_count"] += 1
            if policy == "lru":
                cache.move_to_end(identity)
            continue

        counters["miss_count"] += 1
        if identity in seen:
            counters["churn_count"] += 1
        seen.add(identity)
        if len(cache) >= capacity:
            cache.popitem(last=False)
            counters["eviction_count"] += 1
        cache[identity] = None

    return counters


def decide_response_cache(
    *,
    fifo_metrics: dict[str, int],
    lru_metrics: dict[str, int],
    measured_runtime_evidence: bool,
) -> dict[str, Any]:
    if set(fifo_metrics) != CACHE_METRIC_NAMES or set(lru_metrics) != CACHE_METRIC_NAMES:
        raise ValueError("cache decisions require only contract counters")
    if type(measured_runtime_evidence) is not bool:
        raise ValueError("measured_runtime_evidence must be a boolean")
    for label, metrics in (("fifo", fifo_metrics), ("lru", lru_metrics)):
        if any(type(value) is not int or value < 0 for value in metrics.values()):
            raise ValueError(f"{label} counters must be non-negative integers")
        if metrics["churn_count"] > metrics["miss_count"]:
            raise ValueError(f"{label} churn cannot exceed misses")
        if metrics["eviction_count"] > metrics["miss_count"]:
            raise ValueError(f"{label} evictions cannot exceed misses")

    fifo_accesses = fifo_metrics["hit_count"] + fifo_metrics["miss_count"]
    lru_accesses = lru_metrics["hit_count"] + lru_metrics["miss_count"]
    if fifo_accesses <= 0 or fifo_accesses != lru_accesses:
        raise ValueError("cache policy access totals must be equal and positive")
    raw_improvement_points = (
        (lru_metrics["hit_count"] / lru_accesses)
        - (fifo_metrics["hit_count"] / fifo_accesses)
    ) * 100.0
    criteria = {
        "candidate_evictions_not_higher": (
            lru_metrics["eviction_count"] <= fifo_metrics["eviction_count"]
        ),
        "candidate_hit_rate_improvement_at_least_5_points": (
            raw_improvement_points >= 5.0
        ),
        "measured_runtime_evidence": measured_runtime_evidence is True,
        "trace_distinguishes_policies": fifo_metrics != lru_metrics,
    }
    failed = sorted(name for name, passed in criteria.items() if not passed)
    return {
        "result": "adopt" if not failed else "retain_current",
        "thresholds": {
            "maximum_additional_evictions": 0,
            "minimum_hit_rate_improvement_percentage_points": 5.0,
        },
        "observed": {
            "hit_rate_improvement_percentage_points": round(raw_improvement_points, 6),
        },
        "criteria": criteria,
        "reason_codes": failed or ["all_adoption_criteria_satisfied"],
    }


def build_synthetic_response_cache_section(
    *,
    blocks: int = 64,
    capacity: int = 3,
) -> dict[str, Any]:
    trace = synthetic_access_trace(blocks=blocks)
    fifo_metrics = simulate_cache_policy(trace, capacity=capacity, policy="fifo")
    lru_metrics = simulate_cache_policy(trace, capacity=capacity, policy="lru")
    decision = decide_response_cache(
        fifo_metrics=fifo_metrics,
        lru_metrics=lru_metrics,
        measured_runtime_evidence=False,
    )
    return {
        "schema_version": 1,
        "benchmark_id": "SAR-09-llm-response-cache",
        "measurement_origin": "synthetic_contract",
        "live_actions_performed": False,
        "network_calls_performed": False,
        "provider_calls_performed": False,
        "current_policy": "fifo",
        "candidate_policy": "lru",
        "metrics": {
            "fifo": fifo_metrics,
            "lru": lru_metrics,
        },
        "decision": decision,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the deterministic SAR-09 synthetic response-cache baseline."
    )
    parser.add_argument(
        "--synthetic",
        action="store_const",
        const="synthetic",
        dest="mode",
        default="synthetic",
        help="Use deterministic local samples (the default and only local mode).",
    )
    parser.add_argument("--blocks", type=int, default=64)
    parser.add_argument("--capacity", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    section = build_synthetic_response_cache_section(
        blocks=args.blocks,
        capacity=args.capacity,
    )
    update_baseline(args.output, SECTION_LLM_RESPONSE_CACHE, section)
    emit_receipt(SECTION_LLM_RESPONSE_CACHE, section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
