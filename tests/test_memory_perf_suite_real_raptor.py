import json
from pathlib import Path
import tempfile

import pytest

from src.memory_perf_suite_real_raptor import (
    REAL_RAPTOR_BENCHMARK_SCHEMA,
    REAL_RAPTOR_PROFILES,
    RealRaptorBenchmarkError,
    _create_fixture,
    render_real_raptor_markdown,
    report_to_json,
    run_real_raptor_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]
OFFLINE_ACCEPTANCE_PATH = (
    ROOT / "docs" / "plans" / "graphrag-raptor-observability-offline-acceptance.json"
)


def test_real_quick_profile_executes_every_backend_step_without_content_leak():
    report = run_real_raptor_benchmark("quick")
    encoded = report_to_json(report)

    assert report["schema"] == REAL_RAPTOR_BENCHMARK_SCHEMA
    assert report["backend"] == "plugins.obsidian.real_memory_backend"
    assert report["profile_contract"] == {"source_count": 120, "warm_samples": 30}
    assert all(report["required_steps"].values())
    assert report["counts"]["source_count"] == 120
    assert report["counts"]["chunk_count"] >= 120
    assert report["cache"]["raptor_warm_hit_rate"] == 1.0
    assert report["cache"]["query_warm_hit_rate"] == 1.0
    assert report["historical_simulation"]["release_evidence"] is False
    assert report["safety"] == {
        "temporary_synthetic_markdown_only": True,
        "network_calls": 0,
        "model_calls": 0,
        "productive_vault_actions": 0,
        "raw_content_in_report": False,
        "absolute_paths_in_report": False,
    }
    assert "Synthetic Node" not in encoded
    assert "Node-00000.md" not in encoded
    assert "synthetic architecture relationship" not in encoded
    assert str(Path.cwd()) not in encoded


def test_profiles_and_measurement_contract_are_fixed():
    assert REAL_RAPTOR_PROFILES == {
        "quick": {"source_count": 120, "warm_samples": 30, "release_blocking": True},
        "standard": {"source_count": 1000, "warm_samples": 30, "release_blocking": True},
        "stress": {"source_count": 5000, "warm_samples": 30, "release_blocking": False},
    }
    with pytest.raises(RealRaptorBenchmarkError, match="unsupported profile"):
        run_real_raptor_benchmark("custom")


def test_fixture_hash_is_deterministic_without_persisting_fixture_paths():
    with tempfile.TemporaryDirectory() as temp_dir:
        first = Path(temp_dir) / "first"
        second = Path(temp_dir) / "second"
        first.mkdir()
        second.mkdir()
        assert _create_fixture(first, 12) == _create_fixture(second, 12)


def test_markdown_report_is_summary_only():
    report = {
        "profile": "quick",
        "status": "passed",
        "release_verdict": "go",
        "counts": {"source_count": 120, "chunk_count": 240},
        "resources": {"wall_seconds": 1.0},
        "event_loop_lag_ms": {"max_ms": 1.0},
        "gates": {"required_steps": "passed"},
    }
    markdown = render_real_raptor_markdown(report)
    assert "Profile: `quick`" in markdown
    assert "required_steps: `passed`" in markdown
    assert "raw" not in markdown.lower()
    assert json.loads(report_to_json(report))["profile"] == "quick"


def test_gro15_offline_acceptance_is_go_content_free_and_live_default_off():
    evidence = json.loads(OFFLINE_ACCEPTANCE_PATH.read_text(encoding="utf-8"))

    assert evidence["schema"] == "odysseus.graphrag_raptor_observability_offline_acceptance.v1"
    assert evidence["verdict"] == "offline_go"
    assert evidence["verification_state"] == "verified_offline_go_after_gro15_remediation"
    assert evidence["scope"]["profiles"] == ["quick", "standard"]
    assert evidence["scope"]["warm_samples_per_profile"] == 30
    assert evidence["profile_results"]["quick"]["gate_summary"] == {
        "passed": 11,
        "failed": 0,
    }
    assert evidence["profile_results"]["standard"]["gate_summary"] == {
        "passed": 11,
        "failed": 0,
    }
    assert evidence["activation_decision"] == {
        "eligible": True,
        "reason": "Both release-blocking profiles pass all eleven unchanged gates after GRO-15 remediation.",
        "thresholds_changed": False,
        "partial_can_activate": False,
        "no_go_can_activate": False,
        "user_decision_required_now": True,
    }
    assert all(value is False for value in evidence["safe_state"].values())
    assert evidence["correctness_and_privacy"]["report_content_free"] is True
    assert evidence["correctness_and_privacy"]["absolute_paths_visible"] is False
    assert evidence["correctness_and_privacy"]["credentials_visible"] is False
    encoded = json.dumps(evidence, sort_keys=True)
    for forbidden in (
        "C:\\Users\\",
        "/home/",
        "Bearer ",
        "raw_prompt",
        "document_text",
        "synthetic architecture relationship",
        "Node-00000.md",
    ):
        assert forbidden not in encoded
