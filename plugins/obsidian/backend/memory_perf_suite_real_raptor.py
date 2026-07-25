"""Offline performance suite that executes the real Obsidian Memory backend."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Iterator

from .derived_index import (
    build_derived_index,
    derived_index_status,
    retrieve_derived_chunks,
)
from .hybrid_retrieval import raptor_status
from .memory_ledger import sync_memory_ledger
from .memory_status import memory_status
from .memory_worker import run_memory_work
from .query_layer import answer_query
from .raptor_cache import (
    clear_raptor_cache,
    notify_raptor_vault_changed,
)
from .raptor_rebuild import rebuild_raptor_artifacts


REAL_RAPTOR_BENCHMARK_SCHEMA = "odysseus.memory_perf_suite.real_raptor.v1"
REAL_RAPTOR_PROFILES = {
    "quick": {"source_count": 120, "warm_samples": 30, "release_blocking": True},
    "standard": {"source_count": 1000, "warm_samples": 30, "release_blocking": True},
    "stress": {"source_count": 5000, "warm_samples": 30, "release_blocking": False},
}
_FEATURE_ENV = {
    "ODYSSEUS_OBSIDIAN_SOMT_ENABLED": "true",
    "ODYSSEUS_OBSIDIAN_FRESHNESS_GATE_ENABLED": "true",
    "ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED": "true",
    "ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED": "true",
    "ODYSSEUS_OBSIDIAN_HYBRID_RETRIEVAL_ENABLED": "false",
}


class RealRaptorBenchmarkError(ValueError):
    """Raised when an offline real-backend benchmark request is invalid."""


def run_real_raptor_benchmark(profile: str = "quick") -> dict[str, Any]:
    normalized_profile = str(profile or "").strip().lower()
    preset = REAL_RAPTOR_PROFILES.get(normalized_profile)
    if preset is None:
        raise RealRaptorBenchmarkError(f"unsupported profile: {profile!r}")

    source_count = int(preset["source_count"])
    warm_samples = int(preset["warm_samples"])
    with tempfile.TemporaryDirectory(prefix="odysseus-real-raptor-") as temp_dir:
        vault_dir = Path(temp_dir) / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        fixture_hash = _create_fixture(vault_dir, source_count)
        with _benchmark_feature_flags():
            report = _run_backend_steps(
                vault_dir,
                profile=normalized_profile,
                source_count=source_count,
                warm_samples=warm_samples,
                fixture_hash=fixture_hash,
                release_blocking=bool(preset["release_blocking"]),
            )
    _assert_report_content_safe(report)
    return report


def _run_backend_steps(
    vault_dir: Path,
    *,
    profile: str,
    source_count: int,
    warm_samples: int,
    fixture_hash: str,
    release_blocking: bool,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    total_cpu_started = time.process_time()
    rss_started = _rss_bytes()
    peak_rss = rss_started
    clear_raptor_cache()

    rebuild, rebuild_ms, rebuild_cpu, rebuild_rss = _measure_rebuild(vault_dir, source_count)
    peak_rss = max(peak_rss, _rss_bytes())
    _value, ledger_sync_ms = _measure(lambda: sync_memory_ledger(str(vault_dir)))
    derived_build, derived_build_ms = _measure(lambda: build_derived_index(str(vault_dir)))
    peak_rss = max(peak_rss, _rss_bytes())

    clear_raptor_cache(str(vault_dir))
    raptor_cold, raptor_cold_ms = _measure(lambda: raptor_status(str(vault_dir)))
    raptor_warm, last_raptor, raptor_warm_hits = _sample(
        lambda: raptor_status(str(vault_dir)),
        warm_samples,
        hit_predicate=lambda result: bool((result.get("cache") or {}).get("hit")),
    )

    clear_raptor_cache(str(vault_dir))
    memory_cold, memory_cold_ms = _measure(lambda: memory_status(str(vault_dir)))
    memory_warm, last_memory, _memory_hits = _sample(
        lambda: memory_status(str(vault_dir)), warm_samples
    )

    retrieval, last_retrieval, _retrieval_hits = _sample(
        lambda: retrieve_derived_chunks(
            str(vault_dir),
            "synthetic architecture relationship",
            top_k=5,
            path_prefix="",
        ),
        warm_samples,
    )

    query_miss, query_miss_ms = _measure(
        lambda: answer_query(
            str(vault_dir),
            "synthetic architecture relationship",
            top_k=5,
            answer_mode="extractive",
        )
    )
    query_hits, last_query, query_warm_hits = _sample(
        lambda: answer_query(
            str(vault_dir),
            "synthetic architecture relationship",
            top_k=5,
            answer_mode="extractive",
        ),
        warm_samples,
        hit_predicate=lambda result: bool((result.get("summary") or {}).get("cache_hit")),
    )
    peak_rss = max(peak_rss, _rss_bytes())

    event_loop_lag = asyncio.run(_measure_event_loop_lag(str(vault_dir)))

    mutated_hash = _mutate_fixture(vault_dir)
    notify_raptor_vault_changed(str(vault_dir), event="benchmark_mutation")
    invalidated, invalidation_ms = _measure(lambda: raptor_status(str(vault_dir)))
    bounded_rebuild, bounded_rebuild_ms, bounded_cpu, bounded_rss = _measure_rebuild(
        vault_dir, source_count
    )
    peak_rss = max(peak_rss, _rss_bytes())

    derived = derived_index_status(str(vault_dir))
    total_wall = time.perf_counter() - total_started
    total_cpu = time.process_time() - total_cpu_started
    disk_bytes = _directory_size(vault_dir)
    rss_delta_mib = max(0, peak_rss - rss_started) / (1024 * 1024)
    rebuild_seconds = rebuild_ms / 1000.0
    sources_per_second = source_count / rebuild_seconds if rebuild_seconds > 0 else 0.0

    latencies = {
        "rebuild": _latency_summary([rebuild_ms]),
        "ledger_sync": _latency_summary([ledger_sync_ms]),
        "derived_build": _latency_summary([derived_build_ms]),
        "raptor_status_cold": _latency_summary([raptor_cold_ms]),
        "raptor_status_warm": _latency_summary(raptor_warm),
        "memory_status_cold": _latency_summary([memory_cold_ms]),
        "memory_status_warm": _latency_summary(memory_warm),
        "derived_retrieval": _latency_summary(retrieval),
        "query_cache_miss": _latency_summary([query_miss_ms]),
        "query_cache_hit": _latency_summary(query_hits),
        "source_mutation_invalidation": _latency_summary([invalidation_ms]),
        "bounded_rebuild_after_invalidation": _latency_summary([bounded_rebuild_ms]),
    }
    mutation_detected = (
        str((invalidated.get("readiness") or {}).get("state") or "") == "dirty"
        and str((invalidated.get("cache") or {}).get("result") or "") == "stale"
    )
    required_steps = {
        "rebuild": bool(rebuild.get("success")),
        "raptor_status_cold": not bool((raptor_cold.get("cache") or {}).get("hit")),
        "raptor_status_warm": bool((last_raptor.get("cache") or {}).get("hit")),
        "memory_status_cold": bool(memory_cold),
        "memory_status_warm": bool(last_memory),
        "derived_retrieval": isinstance(last_retrieval.get("results"), list),
        "query_cache_miss": not bool((query_miss.get("summary") or {}).get("cache_hit")),
        "query_cache_hit": bool((last_query.get("summary") or {}).get("cache_hit")),
        "source_mutation_invalidation": mutation_detected,
        "bounded_rebuild_after_invalidation": bool(bounded_rebuild.get("success")),
    }
    gates = {
        "required_steps": _gate(all(required_steps.values())),
        "derived_retrieval_p95_ms_lt_500": _gate(
            latencies["derived_retrieval"]["p95_ms"] < 500
        ),
        "memory_status_p95_ms_lt_750": _gate(
            latencies["memory_status_warm"]["p95_ms"] < 750
        ),
        "raptor_status_p95_ms_lt_250": _gate(
            latencies["raptor_status_warm"]["p95_ms"] < 250
        ),
        "query_cache_hit_p95_ms_lt_100": _gate(
            latencies["query_cache_hit"]["p95_ms"] < 100
        ),
        "event_loop_lag_max_ms_lte_100": _gate(event_loop_lag["max_ms"] <= 100),
        "rebuild_wall_seconds_lt_60": _gate(rebuild_seconds < 60),
        "rebuild_sources_per_second_gte_20": _gate(sources_per_second >= 20),
        "rebuild_rss_delta_mib_lt_512": _gate(rebuild_rss < 512),
        "rebuild_cpu_seconds_lt_60": _gate(rebuild_cpu < 60),
        "temporary_plus_report_mib_lt_256": _gate(disk_bytes < 256 * 1024 * 1024),
    }
    all_gates_pass = all(value == "passed" for value in gates.values())
    status = "passed" if all_gates_pass or not release_blocking else "failed"
    return {
        "schema": REAL_RAPTOR_BENCHMARK_SCHEMA,
        "profile": profile,
        "release_blocking": release_blocking,
        "status": status,
        "release_verdict": "go" if all_gates_pass else ("no_go" if release_blocking else "diagnostic"),
        "backend": "plugins.obsidian.real_memory_backend",
        "historical_simulation": {
            "schema": "odysseus.memory_perf_suite.raptor.v1",
            "classification": "historical_arithmetic_only",
            "release_evidence": False,
        },
        "profile_contract": {
            "source_count": source_count,
            "warm_samples": warm_samples,
        },
        "required_steps": required_steps,
        "gates": gates,
        "counts": {
            "source_count": source_count,
            "chunk_count": int((derived.get("summary") or {}).get("chunk_count") or 0),
            "raptor_graph_nodes": int((rebuild.get("summary") or {}).get("source_count") or 0),
            "raptor_graph_edges": int((rebuild.get("summary") or {}).get("graph_edges") or 0),
            "retrieval_results": len(last_retrieval.get("results") or []),
        },
        "latencies_ms": latencies,
        "cache": {
            "raptor_warm_requests": warm_samples,
            "raptor_warm_hits": raptor_warm_hits,
            "raptor_warm_hit_rate": _round(raptor_warm_hits / warm_samples),
            "query_warm_requests": warm_samples,
            "query_warm_hits": query_warm_hits,
            "query_warm_hit_rate": _round(query_warm_hits / warm_samples),
        },
        "resources": {
            "wall_seconds": _round(total_wall),
            "cpu_seconds": _round(total_cpu),
            "rss_delta_mib": _round(rss_delta_mib),
            "temporary_plus_report_bytes": disk_bytes,
            "rebuild_wall_seconds": _round(rebuild_seconds),
            "rebuild_cpu_seconds": _round(rebuild_cpu),
            "rebuild_rss_delta_mib": _round(rebuild_rss),
            "rebuild_sources_per_second": _round(sources_per_second),
            "bounded_rebuild_cpu_seconds": _round(bounded_cpu),
            "bounded_rebuild_rss_delta_mib": _round(bounded_rss),
        },
        "event_loop_lag_ms": event_loop_lag,
        "fixture_hashes": {
            "initial_sha256": fixture_hash,
            "mutated_sha256": mutated_hash,
        },
        "safety": {
            "temporary_synthetic_markdown_only": True,
            "network_calls": 0,
            "model_calls": 0,
            "productive_vault_actions": 0,
            "raw_content_in_report": False,
            "absolute_paths_in_report": False,
        },
    }


def render_real_raptor_markdown(report: dict[str, Any]) -> str:
    _assert_report_content_safe(report)
    lines = [
        "# Real RAPTOR Backend Benchmark",
        "",
        f"- Profile: `{report['profile']}`",
        f"- Status: `{report['status']}`",
        f"- Release verdict: `{report['release_verdict']}`",
        f"- Sources: `{report['counts']['source_count']}`",
        f"- Chunks: `{report['counts']['chunk_count']}`",
        f"- Wall seconds: `{report['resources']['wall_seconds']}`",
        f"- Event-loop lag max ms: `{report['event_loop_lag_ms']['max_ms']}`",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- {name}: `{status}`" for name, status in sorted(report["gates"].items()))
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Temporary deterministic synthetic Markdown only.",
            "- Counts, timings, fixed profile names and fixture hashes only.",
            "- No network, model, token, service or productive vault action.",
            "- The arithmetic RAPTOR simulation is historical, not release evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def report_to_json(report: dict[str, Any]) -> str:
    _assert_report_content_safe(report)
    return json.dumps(report, indent=2, sort_keys=True)


def _create_fixture(vault_dir: Path, source_count: int) -> str:
    digest = hashlib.sha256()
    folders = ("Alpha", "Beta", "Gamma", "Delta")
    for folder in folders:
        (vault_dir / folder).mkdir(parents=True, exist_ok=True)
    for index in range(source_count):
        folder = folders[index % len(folders)]
        previous = (index - 1) % source_count
        following = (index + 1) % source_count
        content = (
            "---\n"
            "status: active\n"
            "type: canonical\n"
            "updated: 2026-07-18\n"
            "---\n"
            f"# Synthetic Node {index:05d}\n\n"
            f"[[Node-{previous:05d}]] [[Node-{following:05d}]]\n\n"
            f"Deterministic architecture relationship token {index % 17}.\n"
        )
        encoded = content.encode("utf-8")
        digest.update(encoded)
        (vault_dir / folder / f"Node-{index:05d}.md").write_bytes(encoded)
    return digest.hexdigest()


def _mutate_fixture(vault_dir: Path) -> str:
    path = vault_dir / "Alpha" / "Node-00000.md"
    content = path.read_text(encoding="utf-8") + "\nDeterministic mutation marker.\n"
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _measure(function: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function()
    return result, (time.perf_counter() - started) * 1000.0


def _measure_rebuild(vault_dir: Path, source_count: int) -> tuple[dict[str, Any], float, float, float]:
    cpu_started = time.process_time()
    rss_started = _rss_bytes()
    result, elapsed_ms = _measure(
        lambda: rebuild_raptor_artifacts(
            str(vault_dir),
            max_sources=source_count,
            max_edges=max(5000, source_count * 4),
        )
    )
    cpu_seconds = max(0.0, time.process_time() - cpu_started)
    rss_delta_mib = max(0, _rss_bytes() - rss_started) / (1024 * 1024)
    return result, elapsed_ms, cpu_seconds, rss_delta_mib


def _sample(
    function: Callable[[], Any],
    count: int,
    *,
    hit_predicate: Callable[[Any], bool] | None = None,
) -> tuple[list[float], Any, int]:
    samples = []
    last_result = None
    hits = 0
    for _ in range(count):
        last_result, elapsed_ms = _measure(function)
        samples.append(elapsed_ms)
        if hit_predicate is not None and hit_predicate(last_result):
            hits += 1
    return samples, last_result, hits


async def _measure_event_loop_lag(vault_dir: str) -> dict[str, Any]:
    def real_backend_work() -> None:
        for _ in range(3):
            memory_status(vault_dir)

    task = asyncio.create_task(
        run_memory_work(vault_dir, "memory_status", "read", real_backend_work)
    )
    lags = []
    interval = 0.01
    last_tick = asyncio.get_running_loop().time()
    while not task.done():
        await asyncio.sleep(interval)
        now = asyncio.get_running_loop().time()
        lags.append(max(0.0, (now - last_tick - interval) * 1000.0))
        last_tick = now
    await task
    if not lags:
        lags.append(0.0)
    summary = _latency_summary(lags)
    return {
        "samples": len(lags),
        "p50_ms": summary["p50_ms"],
        "p95_ms": summary["p95_ms"],
        "p99_ms": summary["p99_ms"],
        "max_ms": _round(max(lags)),
    }


def _latency_summary(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(float(sample) for sample in samples)
    return {
        "samples": len(ordered),
        "p50_ms": _round(_percentile(ordered, 0.50)),
        "p95_ms": _round(_percentile(ordered, 0.95)),
        "p99_ms": _round(_percentile(ordered, 0.99)),
        "max_ms": _round(max(ordered) if ordered else 0.0),
    }


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    position = percentile * (len(samples) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return samples[lower]
    return samples[lower] + (samples[upper] - samples[lower]) * (position - lower)


def _rss_bytes() -> int:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return 0


def _directory_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _round(value: float) -> float:
    return round(float(value), 6)


def _gate(passed: bool) -> str:
    return "passed" if passed else "failed"


@contextmanager
def _benchmark_feature_flags() -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in _FEATURE_ENV}
    try:
        os.environ.update(_FEATURE_ENV)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _assert_report_content_safe(report: dict[str, Any]) -> None:
    encoded = json.dumps(report, sort_keys=True)
    forbidden = (
        str(Path.cwd()).lower(),
        "synthetic architecture relationship",
        "deterministic mutation marker",
        "node-00000.md",
        "source_path",
    )
    lowered = encoded.lower()
    if any(token and token in lowered for token in forbidden):
        raise RealRaptorBenchmarkError("benchmark report contains forbidden content")
