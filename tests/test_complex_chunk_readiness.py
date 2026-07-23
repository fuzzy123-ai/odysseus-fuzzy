import asyncio
import json

from src.complex_chunk_readiness import build_complex_chunk_readiness
from src.gemma_multihop_chunk_benchmark import deterministic_multihop_fixture_call, run_multihop_benchmark
from src.memory_perf_suite_raptor import run_raptor_graph_scale_simulation


def test_complex_chunk_readiness_go_from_synthetic_reports(tmp_path) -> None:
    multihop = asyncio.run(
        run_multihop_benchmark(
            model="gemma3:4b",
            provider="local_ollama",
            call_model=deterministic_multihop_fixture_call,
            retrieval_budget=4,
        )
    )
    raptor = run_raptor_graph_scale_simulation(
        run_dir=tmp_path,
        node_count=100_000,
        edges_per_node=3,
        output_node_budget=1_000,
        output_edge_budget=2_500,
    )

    readiness = build_complex_chunk_readiness(multihop_report=multihop, raptor_report=raptor)
    encoded = json.dumps(readiness, sort_keys=True)

    assert readiness["schema"] == "odysseus.complex_chunk_readiness.v1"
    assert readiness["status"] == "go"
    assert readiness["go"] is True
    assert readiness["evidence_packets"]["multihop"]["retrieval_precision_pass_rate"] == 100.0
    assert readiness["evidence_packets"]["raptor"]["node_count"] == 100_000
    assert readiness["evidence_packets"]["raptor"]["clipped"] is True
    assert readiness["raw_content_visible"] is False
    assert "rootless Podman pods" not in encoded
    assert "raw_prompt" not in encoded
    assert "raw_output" not in encoded


def test_complex_chunk_readiness_partial_and_no_go_thresholds(tmp_path) -> None:
    multihop = {
        "schema": "odysseus.gemma_multihop_chunk_benchmark.v1",
        "status": "failed",
        "score": 75,
        "corpus_chunk_count": 80,
        "retrieval_budget": 4,
        "metrics": {
            "case_count": 4,
            "retrieval_precision_pass_rate": 82,
            "evidence_pass_rate": 90,
            "policy_pass_rate": 90,
            "avg_budget_waste_rate": 0.1,
        },
    }
    raptor = run_raptor_graph_scale_simulation(run_dir=tmp_path)
    partial = build_complex_chunk_readiness(multihop_report=multihop, raptor_report=raptor)
    no_go = build_complex_chunk_readiness(multihop_report=None, raptor_report=raptor)

    assert partial["status"] == "partial"
    assert "multihop_benchmark_partial" in partial["warnings"]
    assert no_go["status"] == "no_go"
    assert "multihop_evidence_missing" in no_go["blockers"]
