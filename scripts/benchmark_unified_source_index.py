#!/usr/bin/env python3
"""Run a bounded, synthetic-only USI scale evidence probe.

The reported profile may represent a million logical records, but the probe
materializes at most 1,024 synthetic records in one temporary SQLite database.
Timing and allocation values are observed sample values, never migration SLOs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import tracemalloc
from typing import Any
import sys


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.unified_source_index_backup import (
    backup_sqlite_store,
    rebuild_projections,
    restore_sqlite_backup,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_migrations import configure_sqlite_connection
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore


REPORT_SCHEMA = "odysseus.unified_source_index.scale_evidence.v1"
MAX_PHYSICAL_RECORDS = 1_024
DEFAULT_PHYSICAL_RECORDS = 128
MIN_LOGICAL_RECORDS = 1_000_000
MIN_LOGICAL_LOC = 100_000
_NOW = "2026-07-23T19:00:00Z"


def run_benchmark(
    *,
    physical_records: int = DEFAULT_PHYSICAL_RECORDS,
    logical_records: int = MIN_LOGICAL_RECORDS,
    logical_loc: int = MIN_LOGICAL_LOC,
) -> dict[str, Any]:
    """Collect bounded sample measurements in a fresh temporary database."""

    _validate_profile(physical_records, logical_records, logical_loc)
    tracemalloc.start()
    try:
        with tempfile.TemporaryDirectory(prefix="usi-scale-") as directory:
            root = Path(directory)
            store = SQLiteUnifiedSourceIndexStore(root / "sample.sqlite3")
            actual_materialized_records = _materialize_sample(store, physical_records)
            query_samples = _query_samples(store)
            index_size = _database_size(Path(store.database_path))
            contention = _contention_probe(Path(store.database_path))
            rebuild = rebuild_projections(
                store,
                rebuilders=(),
                required_rebuilders=(),
            )
            recovery_started = time.perf_counter_ns()
            backup_root = root / "backups"
            restore_root = root / "restores"
            backup_root.mkdir()
            restore_root.mkdir()
            backup = backup_sqlite_store(store, target_root=backup_root)
            restored = restore_sqlite_backup(backup, target_root=restore_root)
            recovery_elapsed = time.perf_counter_ns() - recovery_started
            if restored.restored_snapshot != store.current_snapshot():
                raise RuntimeError("restored sample snapshot does not match")
            _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return {
        "schema": REPORT_SCHEMA,
        "measurement_scope": "bounded_synthetic_sample",
        "profile": {
            "logical_loc": logical_loc,
            "logical_records": logical_records,
            "physical_records": physical_records,
            "sample_only": True,
        },
        "actual_materialized_records": actual_materialized_records,
        "query_latency_ns": {
            "p50": _percentile(query_samples, 0.50),
            "p95": _percentile(query_samples, 0.95),
            "sample_count": len(query_samples),
            "observed_nondeterministic": True,
        },
        "index_size_bytes": index_size,
        "python_tracemalloc_peak_bytes": peak,
        "writer_contention": contention,
        "recovery": {
            "status": "restored",
            "elapsed_ns": recovery_elapsed,
            "observed_nondeterministic": True,
            "snapshot_ref": backup.source_snapshot.snapshot_ref,
            "state_hash": backup.source_snapshot.state_hash,
        },
        "rebuild": {
            "status": rebuild.status,
            "fts_status": rebuild.fts_status,
            "scope": "fts_only",
            "external_projections_requested": False,
            "snapshot_ref": rebuild.snapshot.snapshot_ref,
            "missing_rebuilders": list(rebuild.missing_rebuilders),
        },
        "postgres_gate": {
            "state": "deferred_measured_gate",
            "migration_recommended": False,
            "loc_threshold_used": False,
        },
        "safety": {
            "network_calls": 0,
            "productive_source_reads": 0,
            "provider_calls": 0,
            "live_actions": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate and print a bounded sample report")
    parser.add_argument("--physical-records", type=int, default=DEFAULT_PHYSICAL_RECORDS)
    parser.add_argument("--logical-records", type=int, default=MIN_LOGICAL_RECORDS)
    parser.add_argument("--logical-loc", type=int, default=MIN_LOGICAL_LOC)
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(
            physical_records=args.physical_records,
            logical_records=args.logical_records,
            logical_loc=args.logical_loc,
        )
        _validate_report(report)
    except (RuntimeError, ValueError) as exc:
        print(f"USI scale benchmark failed: {exc}")
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


def _validate_profile(physical_records: int, logical_records: int, logical_loc: int) -> None:
    if type(physical_records) is not int or not 3 <= physical_records <= MAX_PHYSICAL_RECORDS:
        raise ValueError(f"physical_records must be between 3 and {MAX_PHYSICAL_RECORDS}")
    if type(logical_records) is not int or logical_records < MIN_LOGICAL_RECORDS:
        raise ValueError(f"logical_records must be at least {MIN_LOGICAL_RECORDS}")
    if type(logical_loc) is not int or logical_loc < MIN_LOGICAL_LOC:
        raise ValueError(f"logical_loc must be at least {MIN_LOGICAL_LOC}")


def _materialize_sample(store: SQLiteUnifiedSourceIndexStore, physical_records: int) -> int:
    source = SourceRecord(
        owner_scope="user:synthetic",
        source_kind=SourceKind.CODE,
        canonical_ref="synthetic:scale",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="synthetic.fixture",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="synthetic:v1",
        content_hash=content_hash("synthetic-scale-version"),
        version_observed_at=_NOW,
        indexed_at=_NOW,
    )
    chunks = tuple(
        ChunkRecord.create(
            version,
            locator=TextRangeLocator(index * 32, index * 32 + 31),
            extractor_profile_ref="synthetic-v1",
            content_hash=content_hash(f"synthetic benchmark text {index}"),
            content=f"synthetic benchmark text {index}",
            indexed_at=_NOW,
        )
        for index in range(physical_records - 2)
    )
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, *chunks):
        write.put(record)
    snapshot = write.commit()
    if snapshot.record_count != physical_records:
        raise RuntimeError("synthetic sample materialized an unexpected record count")
    return snapshot.record_count


def _query_samples(store: SQLiteUnifiedSourceIndexStore) -> tuple[int, ...]:
    samples: list[int] = []
    for _ in range(9):
        started = time.perf_counter_ns()
        hits = store.search_chunks(owner_scope="user:synthetic", query="synthetic", limit=20)
        elapsed = time.perf_counter_ns() - started
        if not hits:
            raise RuntimeError("synthetic FTS query returned no hits")
        samples.append(elapsed)
    return tuple(samples)


def _database_size(path: Path) -> int:
    total = 0
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.is_file():
            total += candidate.stat().st_size
    if total <= 0:
        raise RuntimeError("sample database did not occupy storage")
    return total


def _contention_probe(path: Path) -> dict[str, Any]:
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            configure_sqlite_connection(connection, busy_timeout_ms=100)
            connection.execute("BEGIN IMMEDIATE")
            held.set()
            release.wait(timeout=1)
            connection.rollback()
        finally:
            connection.close()

    worker = threading.Thread(target=holder, daemon=True)
    worker.start()
    if not held.wait(timeout=1):
        raise RuntimeError("contention holder did not acquire SQLite write lock")
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        configure_sqlite_connection(connection, busy_timeout_ms=25)
        started = time.perf_counter_ns()
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            observed = True
        else:
            connection.rollback()
            observed = False
        elapsed = time.perf_counter_ns() - started
    finally:
        connection.close()
        release.set()
        worker.join(timeout=1)
    if not observed:
        raise RuntimeError("contention probe did not observe the held write lock")
    return {
        "observed": True,
        "elapsed_ns": elapsed,
        "observed_nondeterministic": True,
    }


def _percentile(values: tuple[int, ...], quantile: float) -> int:
    if not values or quantile not in {0.50, 0.95}:
        raise ValueError("unsupported percentile input")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * quantile + 0.999999)) - 1))
    return ordered[index]


def _validate_report(report: dict[str, Any]) -> None:
    if report.get("schema") != REPORT_SCHEMA:
        raise ValueError("unexpected benchmark schema")
    profile = report.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("benchmark profile is missing")
    _validate_profile(profile.get("physical_records"), profile.get("logical_records"), profile.get("logical_loc"))
    if profile.get("sample_only") is not True:
        raise ValueError("benchmark must remain sample-only")
    if report.get("actual_materialized_records") != profile["physical_records"]:
        raise ValueError("reported materialized record count is inaccurate")
    latency = report.get("query_latency_ns")
    if not isinstance(latency, dict) or not (
        type(latency.get("sample_count")) is int
        and latency["sample_count"] > 0
        and type(latency.get("p50")) is int
        and type(latency.get("p95")) is int
        and 0 <= latency["p50"] <= latency["p95"]
        and latency.get("observed_nondeterministic") is True
    ):
        raise ValueError("query latency evidence is invalid")
    if type(report.get("index_size_bytes")) is not int or report["index_size_bytes"] <= 0:
        raise ValueError("sample index-size evidence is invalid")
    if type(report.get("python_tracemalloc_peak_bytes")) is not int or report["python_tracemalloc_peak_bytes"] <= 0:
        raise ValueError("sample allocation evidence is invalid")
    contention = report.get("writer_contention")
    if not isinstance(contention, dict) or contention.get("observed") is not True or type(contention.get("elapsed_ns")) is not int or contention["elapsed_ns"] < 0:
        raise ValueError("writer contention evidence is invalid")
    recovery = report.get("recovery")
    if not isinstance(recovery, dict) or recovery.get("status") != "restored" or type(recovery.get("elapsed_ns")) is not int or recovery["elapsed_ns"] < 0:
        raise ValueError("recovery evidence is invalid")
    rebuild = report.get("rebuild")
    if rebuild != {
        "status": "complete",
        "fts_status": "rebuilt",
        "scope": "fts_only",
        "external_projections_requested": False,
        "snapshot_ref": rebuild.get("snapshot_ref") if isinstance(rebuild, dict) else None,
        "missing_rebuilders": [],
    } or not isinstance(rebuild["snapshot_ref"], str) or not rebuild["snapshot_ref"]:
        raise ValueError("rebuild evidence is invalid")
    if report.get("postgres_gate") != {
        "state": "deferred_measured_gate",
        "migration_recommended": False,
        "loc_threshold_used": False,
    }:
        raise ValueError("benchmark cannot recommend a migration")
    if report.get("safety") != {
        "network_calls": 0,
        "productive_source_reads": 0,
        "provider_calls": 0,
        "live_actions": False,
    }:
        raise ValueError("benchmark crossed its repository-only safety boundary")


if __name__ == "__main__":
    raise SystemExit(main())
