"""Deterministic, content-free SAR-09 transport benchmark contract.

The local/default mode is synthetic and performs no socket, provider, TLS or
proxy operation. Real transport evidence remains behind its separate Live-Go.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping


BASELINE_SCHEMA_VERSION = 1
BASELINE_KIND = "odysseus.system_assurance_optimization_baseline"
DEFAULT_OUTPUT = Path("docs/plans/system-assurance-optimization-baseline.json")
SECTION_LLM_TRANSPORT = "llm_transport"
SECTION_AGENT_POLICY_LOAD = "agent_policy_load"
SECTION_LLM_RESPONSE_CACHE = "llm_response_cache"
ALLOWED_SECTION_NAMES = frozenset({
    SECTION_LLM_TRANSPORT,
    SECTION_AGENT_POLICY_LOAD,
    SECTION_LLM_RESPONSE_CACHE,
})

_TOP_LEVEL_KEYS = frozenset({
    "kind",
    "live_actions_performed",
    "roadmap_id",
    "schema_version",
    "sections",
})
_SECTION_COMMON_KEYS = frozenset({
    "benchmark_id",
    "live_actions_performed",
    "measurement_origin",
    "network_calls_performed",
    "provider_calls_performed",
    "schema_version",
})
_DECISION_KEYS = frozenset({
    "criteria",
    "observed",
    "reason_codes",
    "result",
    "thresholds",
})
_TRANSPORT_CRITERIA = frozenset({
    "concurrency_is_16",
    "error_rate_not_worse",
    "local_provider_compatible",
    "local_tls_compatible",
    "measured_local_evidence",
    "p95_improvement_at_least_15_percent",
    "proxy_compatible",
})
_POLICY_CRITERIA = frozenset({
    "at_least_1000_starts",
    "latency_threshold_exceeded",
    "synchronous_invalidation_proven",
})
_CACHE_CRITERIA = frozenset({
    "candidate_evictions_not_higher",
    "candidate_hit_rate_improvement_at_least_5_points",
    "measured_runtime_evidence",
    "trace_distinguishes_policies",
})
_CACHE_COUNTER_KEYS = frozenset({
    "churn_count",
    "eviction_count",
    "hit_count",
    "miss_count",
})


def empty_baseline() -> dict[str, Any]:
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "kind": BASELINE_KIND,
        "roadmap_id": "SAR",
        "live_actions_performed": False,
        "sections": {},
    }


def _object(value: Any, location: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{location} must be an object")
    return value


def _exact_keys(value: Any, expected: frozenset[str], location: str) -> dict[str, Any]:
    obj = _object(value, location)
    if not all(type(key) is str for key in obj):
        raise ValueError(f"{location} keys must be strings")
    actual = set(obj)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{location} keys mismatch; missing={missing}, unknown={unknown}")
    return obj


def _enum(value: Any, allowed: frozenset[str], location: str) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError(f"{location} must be one of {sorted(allowed)}")
    return value


def _boolean(value: Any, location: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{location} must be a boolean")
    return value


def _integer(value: Any, minimum: int, maximum: int, location: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{location} must be an integer in [{minimum}, {maximum}]")
    return value


def _finite_number(value: Any, minimum: float, maximum: float, location: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{location} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{location} must be finite and in [{minimum}, {maximum}]")
    return number


def _fixed_number(value: Any, expected: float, location: str) -> None:
    number = _finite_number(value, expected, expected, location)
    if number != expected:
        raise ValueError(f"{location} must equal {expected}")


def _validate_common_section(
    section: dict[str, Any],
    *,
    benchmark_id: str,
    extra_keys: frozenset[str],
    location: str,
) -> None:
    _exact_keys(section, _SECTION_COMMON_KEYS | extra_keys, location)
    _integer(section["schema_version"], 1, 1, f"{location}.schema_version")
    _enum(section["benchmark_id"], frozenset({benchmark_id}), f"{location}.benchmark_id")
    _enum(
        section["measurement_origin"],
        frozenset({"synthetic_contract"}),
        f"{location}.measurement_origin",
    )
    for key in (
        "live_actions_performed",
        "network_calls_performed",
        "provider_calls_performed",
    ):
        if _boolean(section[key], f"{location}.{key}") is not False:
            raise ValueError(f"{location}.{key} must be false")


def _validate_decision_shell(
    decision: Any,
    criteria_keys: frozenset[str],
    location: str,
) -> tuple[dict[str, Any], dict[str, bool]]:
    obj = _exact_keys(decision, _DECISION_KEYS, location)
    result = _enum(obj["result"], frozenset({"adopt", "retain_current"}), f"{location}.result")
    criteria_obj = _exact_keys(obj["criteria"], criteria_keys, f"{location}.criteria")
    criteria = {
        key: _boolean(value, f"{location}.criteria.{key}")
        for key, value in criteria_obj.items()
    }
    expected_result = "adopt" if all(criteria.values()) else "retain_current"
    if result != expected_result:
        raise ValueError(f"{location}.result conflicts with criteria")

    reasons = obj["reason_codes"]
    if type(reasons) is not list:
        raise ValueError(f"{location}.reason_codes must be an array")
    expected_reasons = sorted(key for key, passed in criteria.items() if not passed)
    if not expected_reasons:
        expected_reasons = ["all_adoption_criteria_satisfied"]
    if reasons != expected_reasons:
        raise ValueError(f"{location}.reason_codes conflict with criteria")
    return obj, criteria


def _validate_transport_section(section: Any) -> None:
    location = f"sections.{SECTION_LLM_TRANSPORT}"
    obj = _object(section, location)
    _validate_common_section(
        obj,
        benchmark_id="SAR-09-llm-transport",
        extra_keys=frozenset({"compatibility", "decision", "inputs", "metrics"}),
        location=location,
    )
    inputs = _exact_keys(obj["inputs"], frozenset({"concurrency", "sample_count"}), f"{location}.inputs")
    concurrency = _integer(inputs["concurrency"], 1, 1024, f"{location}.inputs.concurrency")
    sample_count = _integer(inputs["sample_count"], 1, 10_000_000, f"{location}.inputs.sample_count")

    metrics = _exact_keys(
        obj["metrics"],
        frozenset({
            "http1_error_count",
            "http1_p95_latency_ms",
            "http2_error_count",
            "http2_p95_latency_ms",
        }),
        f"{location}.metrics",
    )
    http1_errors = _integer(metrics["http1_error_count"], 0, sample_count, f"{location}.metrics.http1_error_count")
    http2_errors = _integer(metrics["http2_error_count"], 0, sample_count, f"{location}.metrics.http2_error_count")
    http1_p95_latency_ms = _finite_number(
        metrics["http1_p95_latency_ms"],
        0.000001,
        86_400_000.0,
        f"{location}.metrics.http1_p95_latency_ms",
    )
    http2_p95_latency_ms = _finite_number(
        metrics["http2_p95_latency_ms"],
        0.0,
        86_400_000.0,
        f"{location}.metrics.http2_p95_latency_ms",
    )

    compatibility = _exact_keys(
        obj["compatibility"],
        frozenset({"local_provider", "local_tls", "proxy"}),
        f"{location}.compatibility",
    )
    for key, value in compatibility.items():
        _enum(value, frozenset({"failed", "not_measured", "passed"}), f"{location}.compatibility.{key}")

    decision, criteria = _validate_decision_shell(obj["decision"], _TRANSPORT_CRITERIA, f"{location}.decision")
    thresholds = _exact_keys(
        decision["thresholds"],
        frozenset({"minimum_p95_improvement_percent", "required_concurrency"}),
        f"{location}.decision.thresholds",
    )
    _fixed_number(thresholds["minimum_p95_improvement_percent"], 15.0, f"{location}.decision.thresholds.minimum_p95_improvement_percent")
    _integer(thresholds["required_concurrency"], 16, 16, f"{location}.decision.thresholds.required_concurrency")
    observed = _exact_keys(
        decision["observed"],
        frozenset({"p95_improvement_percent"}),
        f"{location}.decision.observed",
    )
    observed_improvement_percent = _finite_number(
        observed["p95_improvement_percent"],
        -1_000_000.0,
        100.0,
        f"{location}.decision.observed.p95_improvement_percent",
    )
    raw_improvement_percent = (
        (http1_p95_latency_ms - http2_p95_latency_ms) / http1_p95_latency_ms
    ) * 100.0
    if observed_improvement_percent != round(raw_improvement_percent, 6):
        raise ValueError(f"{location}.decision.observed conflicts with metrics")
    semantic = {
        "concurrency_is_16": concurrency == 16,
        "error_rate_not_worse": http2_errors <= http1_errors,
        "local_provider_compatible": compatibility["local_provider"] == "passed",
        "local_tls_compatible": compatibility["local_tls"] == "passed",
        "measured_local_evidence": False,
        "p95_improvement_at_least_15_percent": raw_improvement_percent >= 15.0,
        "proxy_compatible": compatibility["proxy"] == "passed",
    }
    for key, expected in semantic.items():
        if criteria[key] is not expected:
            raise ValueError(f"{location}.decision.criteria.{key} conflicts with receipt")


def _validate_policy_section(section: Any) -> None:
    location = f"sections.{SECTION_AGENT_POLICY_LOAD}"
    obj = _object(section, location)
    _validate_common_section(
        obj,
        benchmark_id="SAR-09-agent-policy-load",
        extra_keys=frozenset({"decision", "inputs", "invalidation", "metrics"}),
        location=location,
    )
    inputs = _exact_keys(obj["inputs"], frozenset({"iterations"}), f"{location}.inputs")
    iterations = _integer(inputs["iterations"], 1, 10_000_000, f"{location}.inputs.iterations")
    metrics = _exact_keys(
        obj["metrics"],
        frozenset({"p95_query_latency_ms", "p95_start_latency_ms"}),
        f"{location}.metrics",
    )
    p95_query_latency_ms = _finite_number(
        metrics["p95_query_latency_ms"],
        0.0,
        86_400_000.0,
        f"{location}.metrics.p95_query_latency_ms",
    )
    p95_start_latency_ms = _finite_number(
        metrics["p95_start_latency_ms"],
        0.000001,
        86_400_000.0,
        f"{location}.metrics.p95_start_latency_ms",
    )
    invalidation = _exact_keys(obj["invalidation"], frozenset({"synchronous_proof"}), f"{location}.invalidation")
    proof = _enum(
        invalidation["synchronous_proof"],
        frozenset({"not_proven"}),
        f"{location}.invalidation.synchronous_proof",
    )
    decision, criteria = _validate_decision_shell(obj["decision"], _POLICY_CRITERIA, f"{location}.decision")
    thresholds = _exact_keys(
        decision["thresholds"],
        frozenset({
            "minimum_iterations",
            "p95_query_latency_ms_strictly_greater_than",
            "query_share_percent_strictly_greater_than",
        }),
        f"{location}.decision.thresholds",
    )
    _integer(thresholds["minimum_iterations"], 1000, 1000, f"{location}.decision.thresholds.minimum_iterations")
    _fixed_number(thresholds["p95_query_latency_ms_strictly_greater_than"], 10.0, f"{location}.decision.thresholds.p95_query_latency_ms_strictly_greater_than")
    _fixed_number(thresholds["query_share_percent_strictly_greater_than"], 2.0, f"{location}.decision.thresholds.query_share_percent_strictly_greater_than")
    observed = _exact_keys(decision["observed"], frozenset({"query_share_percent"}), f"{location}.decision.observed")
    observed_query_share_percent = _finite_number(
        observed["query_share_percent"],
        0.0,
        1_000_000.0,
        f"{location}.decision.observed.query_share_percent",
    )
    raw_query_share_percent = (p95_query_latency_ms / p95_start_latency_ms) * 100.0
    if observed_query_share_percent != round(raw_query_share_percent, 6):
        raise ValueError(f"{location}.decision.observed conflicts with metrics")
    semantic = {
        "at_least_1000_starts": iterations >= 1000,
        "latency_threshold_exceeded": (
            p95_query_latency_ms > 10.0 or raw_query_share_percent > 2.0
        ),
        "synchronous_invalidation_proven": proof == "proven",
    }
    for key, expected in semantic.items():
        if criteria[key] is not expected:
            raise ValueError(f"{location}.decision.criteria.{key} conflicts with receipt")


def _validate_cache_counters(value: Any, location: str) -> dict[str, int]:
    counters = _exact_keys(value, _CACHE_COUNTER_KEYS, location)
    validated = {
        key: _integer(raw, 0, 1_000_000_000, f"{location}.{key}")
        for key, raw in counters.items()
    }
    if validated["hit_count"] + validated["miss_count"] <= 0:
        raise ValueError(f"{location} must contain at least one access")
    if validated["churn_count"] > validated["miss_count"]:
        raise ValueError(f"{location}.churn_count cannot exceed misses")
    if validated["eviction_count"] > validated["miss_count"]:
        raise ValueError(f"{location}.eviction_count cannot exceed misses")
    return validated


def _validate_cache_section(section: Any) -> None:
    location = f"sections.{SECTION_LLM_RESPONSE_CACHE}"
    obj = _object(section, location)
    _validate_common_section(
        obj,
        benchmark_id="SAR-09-llm-response-cache",
        extra_keys=frozenset({"candidate_policy", "current_policy", "decision", "metrics"}),
        location=location,
    )
    _enum(obj["current_policy"], frozenset({"fifo"}), f"{location}.current_policy")
    _enum(obj["candidate_policy"], frozenset({"lru"}), f"{location}.candidate_policy")
    metrics = _exact_keys(obj["metrics"], frozenset({"fifo", "lru"}), f"{location}.metrics")
    fifo = _validate_cache_counters(metrics["fifo"], f"{location}.metrics.fifo")
    lru = _validate_cache_counters(metrics["lru"], f"{location}.metrics.lru")
    if fifo["hit_count"] + fifo["miss_count"] != lru["hit_count"] + lru["miss_count"]:
        raise ValueError(f"{location}.metrics policy access totals must match")

    decision, criteria = _validate_decision_shell(obj["decision"], _CACHE_CRITERIA, f"{location}.decision")
    thresholds = _exact_keys(
        decision["thresholds"],
        frozenset({
            "maximum_additional_evictions",
            "minimum_hit_rate_improvement_percentage_points",
        }),
        f"{location}.decision.thresholds",
    )
    _integer(thresholds["maximum_additional_evictions"], 0, 0, f"{location}.decision.thresholds.maximum_additional_evictions")
    _fixed_number(thresholds["minimum_hit_rate_improvement_percentage_points"], 5.0, f"{location}.decision.thresholds.minimum_hit_rate_improvement_percentage_points")
    observed = _exact_keys(
        decision["observed"],
        frozenset({"hit_rate_improvement_percentage_points"}),
        f"{location}.decision.observed",
    )
    observed_improvement_points = _finite_number(
        observed["hit_rate_improvement_percentage_points"],
        -100.0,
        100.0,
        f"{location}.decision.observed.hit_rate_improvement_percentage_points",
    )
    access_count = fifo["hit_count"] + fifo["miss_count"]
    raw_improvement_points = (
        (lru["hit_count"] / access_count) - (fifo["hit_count"] / access_count)
    ) * 100.0
    if observed_improvement_points != round(raw_improvement_points, 6):
        raise ValueError(f"{location}.decision.observed conflicts with metrics")
    semantic = {
        "candidate_evictions_not_higher": lru["eviction_count"] <= fifo["eviction_count"],
        "candidate_hit_rate_improvement_at_least_5_points": raw_improvement_points >= 5.0,
        "measured_runtime_evidence": False,
        "trace_distinguishes_policies": fifo != lru,
    }
    for key, expected in semantic.items():
        if criteria[key] is not expected:
            raise ValueError(f"{location}.decision.criteria.{key} conflicts with receipt")


_SECTION_VALIDATORS = {
    SECTION_LLM_TRANSPORT: _validate_transport_section,
    SECTION_AGENT_POLICY_LOAD: _validate_policy_section,
    SECTION_LLM_RESPONSE_CACHE: _validate_cache_section,
}


def assert_content_free_baseline(document: Mapping[str, Any]) -> None:
    root = _exact_keys(document, _TOP_LEVEL_KEYS, "root")
    _integer(root["schema_version"], 1, 1, "root.schema_version")
    _enum(root["kind"], frozenset({BASELINE_KIND}), "root.kind")
    _enum(root["roadmap_id"], frozenset({"SAR"}), "root.roadmap_id")
    if _boolean(root["live_actions_performed"], "root.live_actions_performed") is not False:
        raise ValueError("root.live_actions_performed must be false")
    sections = _object(root["sections"], "root.sections")
    if not all(type(key) is str for key in sections):
        raise ValueError("root.sections keys must be strings")
    unknown = set(sections) - ALLOWED_SECTION_NAMES
    if unknown:
        raise ValueError(f"root.sections contains unknown sections: {sorted(unknown)}")
    for section_name, section in sections.items():
        _SECTION_VALIDATORS[section_name](section)


def load_baseline(output: Path | str) -> dict[str, Any]:
    path = Path(output).resolve()
    if not path.exists():
        return empty_baseline()

    def _reject_nonfinite_json(value: str) -> None:
        raise ValueError(f"non-finite JSON number is forbidden: {value}")

    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON key is forbidden: {key}")
            obj[key] = value
        return obj

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_unique_object,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("optimization baseline is unreadable or invalid JSON") from exc
    assert_content_free_baseline(document)
    return document


@contextmanager
def _single_writer(output: Path, timeout_seconds: float = 30.0):
    """Cross-platform single-writer lock using atomic directory creation."""
    lock_directory = output.with_name(f".{output.name}.sar09-writer-lock")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            os.mkdir(lock_directory)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for SAR-09 baseline writer")
            time.sleep(0.01)
    try:
        yield
    finally:
        os.rmdir(lock_directory)


def update_baseline(
    output: Path | str,
    section_name: str,
    section: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically merge one canonical section without dropping prior sections."""
    if section_name not in ALLOWED_SECTION_NAMES:
        raise ValueError("unknown SAR-09 baseline section")
    if not isinstance(section, Mapping):
        raise ValueError("baseline section must be an object")

    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = empty_baseline()
    candidate["sections"][section_name] = dict(section)
    assert_content_free_baseline(candidate)

    with _single_writer(path):
        document = load_baseline(path)
        document["sections"][section_name] = dict(section)
        assert_content_free_baseline(document)
        encoded = json.dumps(
            document,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_name = handle.name
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
        return document


def emit_receipt(section_name: str, section: Mapping[str, Any]) -> None:
    candidate = empty_baseline()
    candidate["sections"][section_name] = dict(section)
    assert_content_free_baseline(candidate)
    print(json.dumps(
        {"section": section_name, "receipt": dict(section)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ))


def nearest_rank_p95(values: Iterable[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("at least one sample is required")
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("samples must be finite")
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def decide_http2(
    *,
    concurrency: int,
    request_count: int,
    http1_p95_latency_ms: float,
    http2_p95_latency_ms: float,
    http1_error_count: int,
    http2_error_count: int,
    local_tls_compatible: bool,
    proxy_compatible: bool,
    local_provider_compatible: bool,
    measured_local_evidence: bool,
) -> dict[str, Any]:
    concurrency = _integer(concurrency, 1, 1024, "concurrency")
    request_count = _integer(request_count, 1, 10_000_000, "request_count")
    http1_p95_latency_ms = _finite_number(
        http1_p95_latency_ms, 0.000001, 86_400_000.0, "http1_p95_latency_ms"
    )
    http2_p95_latency_ms = _finite_number(
        http2_p95_latency_ms, 0.0, 86_400_000.0, "http2_p95_latency_ms"
    )
    http1_error_count = _integer(
        http1_error_count, 0, request_count, "http1_error_count"
    )
    http2_error_count = _integer(
        http2_error_count, 0, request_count, "http2_error_count"
    )
    for name, value in (
        ("local_tls_compatible", local_tls_compatible),
        ("proxy_compatible", proxy_compatible),
        ("local_provider_compatible", local_provider_compatible),
        ("measured_local_evidence", measured_local_evidence),
    ):
        _boolean(value, name)

    raw_improvement_percent = (
        (http1_p95_latency_ms - http2_p95_latency_ms) / http1_p95_latency_ms
    ) * 100.0
    criteria = {
        "concurrency_is_16": concurrency == 16,
        "error_rate_not_worse": http2_error_count <= http1_error_count,
        "local_provider_compatible": local_provider_compatible is True,
        "local_tls_compatible": local_tls_compatible is True,
        "measured_local_evidence": measured_local_evidence is True,
        "p95_improvement_at_least_15_percent": raw_improvement_percent >= 15.0,
        "proxy_compatible": proxy_compatible is True,
    }
    failed = sorted(name for name, passed in criteria.items() if not passed)
    return {
        "result": "adopt" if not failed else "retain_current",
        "thresholds": {
            "minimum_p95_improvement_percent": 15.0,
            "required_concurrency": 16,
        },
        "observed": {
            "p95_improvement_percent": round(raw_improvement_percent, 6),
        },
        "criteria": criteria,
        "reason_codes": failed or ["all_adoption_criteria_satisfied"],
    }


def build_synthetic_transport_section(
    *,
    sample_count: int = 160,
    concurrency: int = 16,
) -> dict[str, Any]:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")

    http1_samples = [20_000 + ((index * 97) % 4_000) for index in range(sample_count)]
    http2_samples = [17_000 + ((index * 89) % 3_000) for index in range(sample_count)]
    http1_p95_raw_ms = nearest_rank_p95(http1_samples) / 1_000.0
    http2_p95_raw_ms = nearest_rank_p95(http2_samples) / 1_000.0
    decision = decide_http2(
        concurrency=concurrency,
        request_count=sample_count,
        http1_p95_latency_ms=http1_p95_raw_ms,
        http2_p95_latency_ms=http2_p95_raw_ms,
        http1_error_count=0,
        http2_error_count=0,
        local_tls_compatible=False,
        proxy_compatible=False,
        local_provider_compatible=False,
        measured_local_evidence=False,
    )
    return {
        "schema_version": 1,
        "benchmark_id": "SAR-09-llm-transport",
        "measurement_origin": "synthetic_contract",
        "live_actions_performed": False,
        "network_calls_performed": False,
        "provider_calls_performed": False,
        "inputs": {
            "concurrency": concurrency,
            "sample_count": sample_count,
        },
        "metrics": {
            "http1_error_count": 0,
            "http1_p95_latency_ms": round(http1_p95_raw_ms, 6),
            "http2_error_count": 0,
            "http2_p95_latency_ms": round(http2_p95_raw_ms, 6),
        },
        "compatibility": {
            "local_provider": "not_measured",
            "local_tls": "not_measured",
            "proxy": "not_measured",
        },
        "decision": decision,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the deterministic SAR-09 synthetic transport receipt."
    )
    parser.add_argument(
        "--synthetic",
        action="store_const",
        const="synthetic",
        dest="mode",
        default="synthetic",
        help="Use deterministic local samples (the default and only local mode).",
    )
    parser.add_argument("--samples", type=int, default=160)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    section = build_synthetic_transport_section(
        sample_count=args.samples,
        concurrency=args.concurrency,
    )
    update_baseline(args.output, SECTION_LLM_TRANSPORT, section)
    emit_receipt(SECTION_LLM_TRANSPORT, section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
