"""Contract tests for SAR-09 synthetic, content-free optimization receipts."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import benchmark_agent_policy_load as policy
from scripts import benchmark_llm_response_cache as response_cache
from scripts import benchmark_llm_transport as transport


ROOT = Path(__file__).resolve().parents[1]
_PARALLEL_WRITER_CODE = r"""
import sys
import time
from pathlib import Path
from scripts import benchmark_agent_policy_load as policy
from scripts import benchmark_llm_response_cache as cache
from scripts import benchmark_llm_transport as transport

ready = Path(sys.argv[1])
gate = Path(sys.argv[2])
output = Path(sys.argv[3])
section_name = sys.argv[4]
builders = {
    transport.SECTION_LLM_TRANSPORT: transport.build_synthetic_transport_section,
    transport.SECTION_AGENT_POLICY_LOAD: policy.build_synthetic_agent_policy_section,
    transport.SECTION_LLM_RESPONSE_CACHE: cache.build_synthetic_response_cache_section,
}
section = builders[section_name]()
ready.write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 20.0
while not gate.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("parallel-writer gate timed out")
    time.sleep(0.005)
transport.update_baseline(output, section_name, section)
"""


def _http2_adoptable_inputs():
    return {
        "concurrency": 16,
        "request_count": 100,
        "http1_p95_latency_ms": 100.0,
        "http2_p95_latency_ms": 85.0,
        "http1_error_count": 2,
        "http2_error_count": 2,
        "local_tls_compatible": True,
        "proxy_compatible": True,
        "local_provider_compatible": True,
        "measured_local_evidence": True,
    }


def test_http2_adopts_only_at_exact_threshold_with_all_checks_green():
    decision = transport.decide_http2(**_http2_adoptable_inputs())

    assert decision["result"] == "adopt"
    assert decision["observed"]["p95_improvement_percent"] == 15.0
    assert all(decision["criteria"].values())


def test_http2_uses_unrounded_value_below_threshold():
    inputs = _http2_adoptable_inputs()
    inputs["http2_p95_latency_ms"] = 85.0000004

    decision = transport.decide_http2(**inputs)

    assert decision["observed"]["p95_improvement_percent"] == 15.0
    assert decision["criteria"]["p95_improvement_at_least_15_percent"] is False
    assert decision["result"] == "retain_current"


@pytest.mark.parametrize(
    "override,failed_criterion",
    [
        ({"concurrency": 8}, "concurrency_is_16"),
        ({"http2_p95_latency_ms": 85.0001}, "p95_improvement_at_least_15_percent"),
        ({"http2_error_count": 3}, "error_rate_not_worse"),
        ({"local_tls_compatible": False}, "local_tls_compatible"),
        ({"proxy_compatible": False}, "proxy_compatible"),
        ({"local_provider_compatible": False}, "local_provider_compatible"),
        ({"measured_local_evidence": False}, "measured_local_evidence"),
    ],
)
def test_http2_retains_current_when_any_adoption_criterion_fails(
    override,
    failed_criterion,
):
    inputs = _http2_adoptable_inputs()
    inputs.update(override)

    decision = transport.decide_http2(**inputs)

    assert decision["result"] == "retain_current"
    assert failed_criterion in decision["reason_codes"]


def test_synthetic_transport_is_deterministic_and_cannot_adopt():
    first = transport.build_synthetic_transport_section()
    second = transport.build_synthetic_transport_section()

    assert first == second
    assert first["inputs"]["concurrency"] == 16
    assert first["decision"]["result"] == "retain_current"
    assert first["decision"]["criteria"]["measured_local_evidence"] is False
    assert first["compatibility"] == {
        "local_provider": "not_measured",
        "local_tls": "not_measured",
        "proxy": "not_measured",
    }
    assert first["network_calls_performed"] is False
    assert first["provider_calls_performed"] is False


@pytest.mark.parametrize(
    "query_ms,start_ms",
    [
        (10.000001, 1_000.0),
        (5.0, 200.0),
    ],
)
def test_agent_policy_cache_adopts_when_either_latency_threshold_and_invalidation_pass(
    query_ms,
    start_ms,
):
    decision = policy.decide_agent_policy_cache(
        iterations=1_000,
        p95_query_latency_ms=query_ms,
        p95_start_latency_ms=start_ms,
        synchronous_invalidation_proven=True,
    )

    assert decision["result"] == "adopt"


def test_agent_policy_thresholds_are_strict_and_require_1000_starts_and_invalidation():
    exact_boundary = policy.decide_agent_policy_cache(
        iterations=1_000,
        p95_query_latency_ms=10.0,
        p95_start_latency_ms=500.0,
        synchronous_invalidation_proven=True,
    )
    too_few = policy.decide_agent_policy_cache(
        iterations=999,
        p95_query_latency_ms=11.0,
        p95_start_latency_ms=500.0,
        synchronous_invalidation_proven=True,
    )
    no_invalidation = policy.decide_agent_policy_cache(
        iterations=1_000,
        p95_query_latency_ms=11.0,
        p95_start_latency_ms=500.0,
        synchronous_invalidation_proven=False,
    )

    assert exact_boundary["result"] == "retain_current"
    assert "latency_threshold_exceeded" in exact_boundary["reason_codes"]
    assert too_few["result"] == "retain_current"
    assert "at_least_1000_starts" in too_few["reason_codes"]
    assert no_invalidation["result"] == "retain_current"
    assert "synchronous_invalidation_proven" in no_invalidation["reason_codes"]


def test_agent_policy_uses_unrounded_share_above_threshold():
    decision = policy.decide_agent_policy_cache(
        iterations=1_000,
        p95_query_latency_ms=2.0000004,
        p95_start_latency_ms=100.0,
        synchronous_invalidation_proven=True,
    )

    assert decision["observed"]["query_share_percent"] == 2.0
    assert decision["criteria"]["latency_threshold_exceeded"] is True
    assert decision["result"] == "adopt"


def test_synthetic_agent_policy_1000_start_receipt_is_stable_and_retains_current():
    first = policy.build_synthetic_agent_policy_section(iterations=1_000)
    second = policy.build_synthetic_agent_policy_section(iterations=1_000)

    assert first == second
    assert first["inputs"] == {"iterations": 1_000}
    assert first["decision"]["result"] == "retain_current"
    assert first["invalidation"]["synchronous_proof"] == "not_proven"
    assert first["network_calls_performed"] is False
    assert first["provider_calls_performed"] is False


def test_response_cache_baseline_contains_only_contract_counters():
    first = response_cache.build_synthetic_response_cache_section(blocks=64, capacity=3)
    second = response_cache.build_synthetic_response_cache_section(blocks=64, capacity=3)

    assert first == second
    metrics = first["metrics"]
    assert set(metrics) == {"fifo", "lru"}
    assert set(metrics["fifo"]) == response_cache.CACHE_METRIC_NAMES
    assert set(metrics["lru"]) == response_cache.CACHE_METRIC_NAMES
    assert metrics["fifo"]["hit_count"] < metrics["lru"]["hit_count"]
    assert metrics["fifo"]["eviction_count"] >= metrics["lru"]["eviction_count"]
    assert first["current_policy"] == "fifo"
    assert first["candidate_policy"] == "lru"
    assert first["decision"]["result"] == "retain_current"
    assert first["decision"]["criteria"]["measured_runtime_evidence"] is False
    assert first["decision"]["criteria"]["trace_distinguishes_policies"] is True
    assert first["network_calls_performed"] is False
    assert first["provider_calls_performed"] is False


def test_response_cache_adopts_only_with_runtime_evidence_and_thresholds():
    section = response_cache.build_synthetic_response_cache_section()

    decision = response_cache.decide_response_cache(
        fifo_metrics=section["metrics"]["fifo"],
        lru_metrics=section["metrics"]["lru"],
        measured_runtime_evidence=True,
    )

    assert decision["result"] == "adopt"
    assert all(decision["criteria"].values())


def test_sequential_cli_updates_preserve_sections_and_are_byte_stable(tmp_path, capsys):
    output = tmp_path / "baseline.json"

    assert transport.main(["--synthetic", "--output", str(output)]) == 0
    transport_receipt = json.loads(capsys.readouterr().out)
    assert transport_receipt["section"] == transport.SECTION_LLM_TRANSPORT

    assert policy.main([
        "--synthetic",
        "--iterations",
        "1000",
        "--output",
        str(output),
    ]) == 0
    policy_receipt = json.loads(capsys.readouterr().out)
    assert policy_receipt["section"] == transport.SECTION_AGENT_POLICY_LOAD

    assert response_cache.main(["--synthetic", "--output", str(output)]) == 0
    cache_receipt = json.loads(capsys.readouterr().out)
    assert cache_receipt["section"] == transport.SECTION_LLM_RESPONSE_CACHE

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["kind"] == transport.BASELINE_KIND
    assert set(document["sections"]) == transport.ALLOWED_SECTION_NAMES
    transport_before = document["sections"][transport.SECTION_LLM_TRANSPORT]
    cache_before = document["sections"][transport.SECTION_LLM_RESPONSE_CACHE]

    before = output.read_bytes()
    assert policy.main([
        "--synthetic",
        "--iterations",
        "1000",
        "--output",
        str(output),
    ]) == 0
    capsys.readouterr()
    assert output.read_bytes() == before

    after = json.loads(output.read_text(encoding="utf-8"))
    assert after["sections"][transport.SECTION_LLM_TRANSPORT] == transport_before
    assert after["sections"][transport.SECTION_LLM_RESPONSE_CACHE] == cache_before


def test_parallel_process_writers_preserve_all_disjoint_sections(tmp_path):
    output = tmp_path / "parallel-baseline.json"
    gate = tmp_path / "start.gate"
    section_names = sorted(transport.ALLOWED_SECTION_NAMES)
    processes: list[subprocess.Popen[str]] = []
    ready_paths: list[Path] = []
    try:
        for index, section_name in enumerate(section_names):
            ready = tmp_path / f"writer-{index}.ready"
            ready_paths.append(ready)
            processes.append(subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _PARALLEL_WRITER_CODE,
                    str(ready),
                    str(gate),
                    str(output),
                    section_name,
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ))

        deadline = time.monotonic() + 20.0
        while not all(path.exists() for path in ready_paths):
            failed = [process for process in processes if process.poll() not in (None, 0)]
            assert not failed, [process.communicate() for process in failed]
            assert time.monotonic() < deadline, "parallel writers did not become ready"
            time.sleep(0.01)
        gate.write_text("go", encoding="utf-8")

        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, (stdout, stderr)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()

    document = transport.load_baseline(output)
    assert set(document["sections"]) == transport.ALLOWED_SECTION_NAMES
    lock_directory = output.with_name(f".{output.name}.sar09-writer-lock")
    assert not lock_directory.exists()


def test_cli_defaults_are_synthetic_and_offline(tmp_path, capsys):
    output = tmp_path / "default-baseline.json"

    transport.main(["--output", str(output)])
    capsys.readouterr()
    policy.main(["--output", str(output)])
    capsys.readouterr()
    response_cache.main(["--output", str(output)])
    capsys.readouterr()

    document = json.loads(output.read_text(encoding="utf-8"))
    for section in document["sections"].values():
        assert section["measurement_origin"] == "synthetic_contract"
        assert section["network_calls_performed"] is False
        assert section["provider_calls_performed"] is False
        assert section["live_actions_performed"] is False


def test_baseline_contains_no_content_or_private_identity_fields(tmp_path):
    output = tmp_path / "baseline.json"
    transport.update_baseline(
        output,
        transport.SECTION_LLM_TRANSPORT,
        transport.build_synthetic_transport_section(),
    )
    transport.update_baseline(
        output,
        transport.SECTION_AGENT_POLICY_LOAD,
        policy.build_synthetic_agent_policy_section(),
    )
    transport.update_baseline(
        output,
        transport.SECTION_LLM_RESPONSE_CACHE,
        response_cache.build_synthetic_response_cache_section(),
    )

    encoded = output.read_text(encoding="utf-8").lower()
    for forbidden in (
        '"api_key"',
        '"cache_key"',
        '"content"',
        '"credential"',
        '"endpoint"',
        '"output"',
        '"password"',
        '"path"',
        '"payload"',
        '"prompt"',
        '"secret"',
        '"text"',
        '"token"',
        '"url"',
        "://",
        "\\users\\",
        "/home/",
        "/users/",
    ):
        assert forbidden not in encoded


def test_content_free_validator_fails_closed_without_overwriting(tmp_path):
    output = tmp_path / "baseline.json"
    bad_section = transport.build_synthetic_transport_section()
    bad_section["prompt"] = "synthetic but forbidden"

    with pytest.raises(ValueError, match="keys mismatch"):
        transport.update_baseline(output, transport.SECTION_LLM_TRANSPORT, bad_section)

    assert not output.exists()


def _seed_valid_baseline(output: Path) -> bytes:
    transport.update_baseline(
        output,
        transport.SECTION_LLM_TRANSPORT,
        transport.build_synthetic_transport_section(),
    )
    return output.read_bytes()


def _reconcile_decision_shell(decision: dict) -> None:
    failed = sorted(
        key for key, passed in decision["criteria"].items() if not passed
    )
    decision["result"] = "adopt" if not failed else "retain_current"
    decision["reason_codes"] = failed or ["all_adoption_criteria_satisfied"]


@pytest.mark.parametrize(
    "section_name,builder,criterion",
    [
        (
            transport.SECTION_LLM_TRANSPORT,
            transport.build_synthetic_transport_section,
            "p95_improvement_at_least_15_percent",
        ),
        (
            transport.SECTION_AGENT_POLICY_LOAD,
            policy.build_synthetic_agent_policy_section,
            "latency_threshold_exceeded",
        ),
        (
            transport.SECTION_LLM_RESPONSE_CACHE,
            response_cache.build_synthetic_response_cache_section,
            "candidate_hit_rate_improvement_at_least_5_points",
        ),
    ],
)
def test_metric_derived_criterion_rejects_consistent_decision_tampering(
    tmp_path,
    section_name,
    builder,
    criterion,
):
    output = tmp_path / "baseline.json"
    before = _seed_valid_baseline(output)
    bad_section = copy.deepcopy(builder())
    bad_section["decision"]["criteria"][criterion] = not bad_section["decision"][
        "criteria"
    ][criterion]
    _reconcile_decision_shell(bad_section["decision"])

    with pytest.raises(ValueError, match="conflicts with receipt"):
        transport.update_baseline(output, section_name, bad_section)

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "section_name,builder,observed_key",
    [
        (
            transport.SECTION_LLM_TRANSPORT,
            transport.build_synthetic_transport_section,
            "p95_improvement_percent",
        ),
        (
            transport.SECTION_AGENT_POLICY_LOAD,
            policy.build_synthetic_agent_policy_section,
            "query_share_percent",
        ),
        (
            transport.SECTION_LLM_RESPONSE_CACHE,
            response_cache.build_synthetic_response_cache_section,
            "hit_rate_improvement_percentage_points",
        ),
    ],
)
def test_metric_derived_observed_rejects_tampering_without_overwrite(
    tmp_path,
    section_name,
    builder,
    observed_key,
):
    output = tmp_path / "baseline.json"
    before = _seed_valid_baseline(output)
    bad_section = copy.deepcopy(builder())
    bad_section["decision"]["observed"][observed_key] += 0.000001

    with pytest.raises(ValueError, match="observed conflicts with metrics"):
        transport.update_baseline(output, section_name, bad_section)

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_output", "arbitrary generated material"),
        ("endpoint_url", "https://private.invalid/v1"),
        ("private_file", "/srv/private/data"),
    ],
)
def test_alias_and_content_fields_are_rejected_without_overwrite(tmp_path, field, value):
    output = tmp_path / "baseline.json"
    before = _seed_valid_baseline(output)
    bad_section = transport.build_synthetic_transport_section()
    bad_section[field] = value

    with pytest.raises(ValueError, match="keys mismatch"):
        transport.update_baseline(output, transport.SECTION_LLM_TRANSPORT, bad_section)

    assert output.read_bytes() == before


def test_unknown_section_is_rejected_without_overwrite(tmp_path):
    output = tmp_path / "baseline.json"
    before = _seed_valid_baseline(output)

    with pytest.raises(ValueError, match="unknown SAR-09 baseline section"):
        transport.update_baseline(output, "transport_alias", {})

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "case",
    [
        "wrong_integer_type",
        "wrong_enum",
        "wrong_boolean_type",
        "nan",
        "positive_infinity",
        "negative_infinity",
        "missing_field",
        "unknown_nested_field",
        "arbitrary_reason_code",
    ],
)
def test_transport_schema_rejects_invalid_values_without_overwrite(tmp_path, case):
    output = tmp_path / "baseline.json"
    before = _seed_valid_baseline(output)
    bad_section = copy.deepcopy(transport.build_synthetic_transport_section())

    if case == "wrong_integer_type":
        bad_section["inputs"]["concurrency"] = True
    elif case == "wrong_enum":
        bad_section["compatibility"]["proxy"] = "greenish"
    elif case == "wrong_boolean_type":
        bad_section["network_calls_performed"] = 0
    elif case == "nan":
        bad_section["metrics"]["http1_p95_latency_ms"] = float("nan")
    elif case == "positive_infinity":
        bad_section["metrics"]["http1_p95_latency_ms"] = float("inf")
    elif case == "negative_infinity":
        bad_section["metrics"]["http2_p95_latency_ms"] = float("-inf")
    elif case == "missing_field":
        del bad_section["metrics"]["http2_error_count"]
    elif case == "unknown_nested_field":
        bad_section["decision"]["endpoint_url"] = "https://private.invalid"
    elif case == "arbitrary_reason_code":
        bad_section["decision"]["reason_codes"] = ["model_output_available"]

    with pytest.raises(ValueError):
        transport.update_baseline(output, transport.SECTION_LLM_TRANSPORT, bad_section)

    assert output.read_bytes() == before


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_in_existing_file_is_rejected_without_rewrite(tmp_path, constant):
    output = tmp_path / "baseline.json"
    valid = json.loads(_seed_valid_baseline(output).decode("utf-8"))
    valid["sections"][transport.SECTION_LLM_TRANSPORT]["metrics"][
        "http1_p95_latency_ms"
    ] = {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}[constant]
    output.write_text(json.dumps(valid, allow_nan=True), encoding="utf-8")
    before = output.read_bytes()

    with pytest.raises(ValueError, match="unreadable or invalid JSON"):
        transport.update_baseline(
            output,
            transport.SECTION_AGENT_POLICY_LOAD,
            policy.build_synthetic_agent_policy_section(),
        )

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_top_level",
        "missing_top_level",
        "unknown_existing_section",
        "wrong_section_type",
    ],
)
def test_existing_root_schema_mismatch_is_fail_closed(tmp_path, mutation):
    output = tmp_path / "baseline.json"
    document = transport.empty_baseline()
    if mutation == "unknown_top_level":
        document["model_output"] = "forbidden"
    elif mutation == "missing_top_level":
        del document["roadmap_id"]
    elif mutation == "unknown_existing_section":
        document["sections"]["unknown"] = {}
    elif mutation == "wrong_section_type":
        document["sections"] = []
    output.write_text(json.dumps(document), encoding="utf-8")
    before = output.read_bytes()

    with pytest.raises(ValueError):
        transport.update_baseline(
            output,
            transport.SECTION_AGENT_POLICY_LOAD,
            policy.build_synthetic_agent_policy_section(),
        )

    assert output.read_bytes() == before


def test_response_cache_schema_rejects_alias_and_non_counter_metric(tmp_path):
    output = tmp_path / "baseline.json"
    before = _seed_valid_baseline(output)
    bad_section = response_cache.build_synthetic_response_cache_section()
    bad_section["metrics"]["fifo"]["model_output"] = 1

    with pytest.raises(ValueError, match="keys mismatch"):
        transport.update_baseline(
            output,
            transport.SECTION_LLM_RESPONSE_CACHE,
            bad_section,
        )

    assert output.read_bytes() == before


def test_json_serializer_rejects_nonfinite_receipt(capsys):
    bad_section = transport.build_synthetic_transport_section()
    bad_section["metrics"]["http1_p95_latency_ms"] = float("nan")

    with pytest.raises(ValueError):
        transport.emit_receipt(transport.SECTION_LLM_TRANSPORT, bad_section)

    assert capsys.readouterr().out == ""


def test_existing_wrong_schema_fails_closed(tmp_path):
    output = tmp_path / "baseline.json"
    output.write_text('{"schema_version": 99}\n', encoding="utf-8")
    before = output.read_bytes()

    with pytest.raises(ValueError):
        transport.update_baseline(
            output,
            transport.SECTION_LLM_TRANSPORT,
            transport.build_synthetic_transport_section(),
        )

    assert output.read_bytes() == before
