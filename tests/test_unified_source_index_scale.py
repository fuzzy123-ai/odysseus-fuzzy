import json

from scripts.benchmark_unified_source_index import main, run_benchmark


def test_bounded_synthetic_profile_reports_sample_only_evidence():
    report = run_benchmark(
        physical_records=16,
        logical_records=1_000_000,
        logical_loc=100_000,
    )

    assert report["schema"] == "odysseus.unified_source_index.scale_evidence.v1"
    assert report["profile"] == {
        "logical_loc": 100_000,
        "logical_records": 1_000_000,
        "physical_records": 16,
        "sample_only": True,
    }
    assert report["actual_materialized_records"] == 16
    assert report["query_latency_ns"]["p95"] >= report["query_latency_ns"]["p50"] >= 0
    assert report["index_size_bytes"] > 0
    assert report["python_tracemalloc_peak_bytes"] > 0
    assert report["writer_contention"]["observed"] is True
    assert report["recovery"]["status"] == "restored"
    assert report["rebuild"]["status"] == "complete"
    assert report["rebuild"]["scope"] == "fts_only"
    assert report["rebuild"]["external_projections_requested"] is False
    assert report["postgres_gate"] == {
        "state": "deferred_measured_gate",
        "migration_recommended": False,
        "loc_threshold_used": False,
    }
    assert "synthetic benchmark text" not in json.dumps(report)


def test_cli_check_accepts_the_minimum_declared_profile_without_materializing_it():
    assert main([
        "--check",
        "--physical-records", "8",
        "--logical-records", "1000000",
        "--logical-loc", "100000",
    ]) == 0
