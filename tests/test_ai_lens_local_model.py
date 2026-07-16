import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.ai_lens_events import AiLensEvent
from src.ai_lens_local_model import (
    AI_LENS_LOCAL_MODEL_CAPABILITY_SCHEMA,
    AI_LENS_LOCAL_MODEL_SAMPLE_SCHEMA,
    LOCAL_MODEL_TRUTH_LEVEL,
    MAX_LOCAL_MODEL_DURATION_MS,
    MAX_LOCAL_MODEL_SAMPLE_COUNT,
    AiLensLocalModelCapability,
    AiLensLocalModelCapabilityState,
    AiLensLocalModelError,
    AiLensLocalModelSampleEnvelope,
)


def _enabled_capability(**overrides):
    values = {
        "enabled": True,
        "available": True,
        "adapter_id": "fixture.aggregate-adapter",
        "supported_metrics": ("activation_norm", "attention_entropy"),
        "max_sample_count": 8,
        "max_duration_ms": 2_000,
    }
    values.update(overrides)
    return AiLensLocalModelCapability(**values)


def _aggregate_metrics():
    return {
        "activation_norm": {
            "count": 4,
            "min": 0.2,
            "max": 0.8,
            "mean": 0.5,
            "stddev": 0.1,
        },
        "attention_entropy": {
            "count": 4,
            "min": 0.1,
            "max": 0.7,
            "p50": 0.3,
            "p95": 0.6,
        },
    }


def test_capability_is_unavailable_and_disabled_by_default() -> None:
    capability = AiLensLocalModelCapability()

    assert capability.state == AiLensLocalModelCapabilityState.UNAVAILABLE
    assert capability.to_dict() == {
        "schema": AI_LENS_LOCAL_MODEL_CAPABILITY_SCHEMA,
        "state": "unavailable",
        "enabled": False,
        "available": False,
        "adapter_id": "",
        "supported_metrics": [],
        "max_sample_count": MAX_LOCAL_MODEL_SAMPLE_COUNT,
        "max_duration_ms": MAX_LOCAL_MODEL_DURATION_MS,
        "truth_level": LOCAL_MODEL_TRUTH_LEVEL,
        "runtime_probed": False,
        "raw_content_visible": False,
    }

    with pytest.raises(FrozenInstanceError):
        capability.enabled = True


def test_explicit_static_descriptor_can_be_available_but_remain_disabled() -> None:
    capability = _enabled_capability(enabled=False)

    assert capability.state == AiLensLocalModelCapabilityState.DISABLED
    assert capability.to_dict()["runtime_probed"] is False
    with pytest.raises(AiLensLocalModelError, match="unavailable or disabled"):
        AiLensLocalModelSampleEnvelope.create(
            capability=capability,
            sample_count=1,
            duration_ms=1,
            aggregate_metrics={"activation_norm": {"count": 1, "mean": 0.5}},
        )


def test_capability_rejects_private_paths_and_non_integral_budgets() -> None:
    with pytest.raises(AiLensLocalModelError, match="adapter_id"):
        _enabled_capability(adapter_id="C:/Users/private/model")
    with pytest.raises(AiLensLocalModelError, match="positive integer"):
        _enabled_capability(max_sample_count=1.5)


def test_valid_sample_is_bounded_aggregate_only_and_event_compatible() -> None:
    sample = AiLensLocalModelSampleEnvelope.create(
        capability=_enabled_capability(),
        sample_count=4,
        duration_ms=250,
        aggregate_metrics=_aggregate_metrics(),
    )

    serialized = sample.to_dict()
    assert serialized["schema"] == AI_LENS_LOCAL_MODEL_SAMPLE_SCHEMA
    assert serialized["truth_level"] == LOCAL_MODEL_TRUTH_LEVEL
    assert serialized["local_runtime_observed"] is True
    assert serialized["raw_content_visible"] is False
    assert set(serialized["aggregate_metrics"]) == {
        "activation_norm",
        "attention_entropy",
    }
    assert sample.to_event_payload() == {
        "adapter_id": "fixture.aggregate-adapter",
        "sample_count": 4,
        "duration_ms": 250,
        "aggregate_metrics": serialized["aggregate_metrics"],
        "local_runtime_observed": True,
    }

    event = AiLensEvent.create(
        event_id="local-sample-001",
        session_id="local-session",
        turn_id="local-turn",
        sequence=1,
        created_at="2026-07-16T15:30:00Z",
        event_type="local_model_internal_sample",
        truth_level=LOCAL_MODEL_TRUTH_LEVEL,
        observation_origin="runtime_observation",
        payload=sample.to_event_payload(),
        model_id="local-model",
    )
    assert event.payload["aggregate_metrics"]["activation_norm"]["mean"] == 0.5

    with pytest.raises(TypeError):
        AiLensLocalModelSampleEnvelope(
            adapter_id="bypass",
            sample_count=1,
            duration_ms=1,
            aggregate_metrics={},
        )


@pytest.mark.parametrize(
    "unsafe_field",
    [
        "raw_tensor",
        "prompt",
        "completion",
        "provider_output",
        "private_context",
        "token_ids",
        "weights",
    ],
)
def test_sample_input_rejects_raw_private_and_runtime_payload_fields(unsafe_field) -> None:
    capability = _enabled_capability()
    payload = AiLensLocalModelSampleEnvelope.create(
        capability=capability,
        sample_count=4,
        duration_ms=250,
        aggregate_metrics=_aggregate_metrics(),
    ).to_dict()
    payload[unsafe_field] = "not-retained"

    with pytest.raises(AiLensLocalModelError, match="raw, private, or unsupported"):
        AiLensLocalModelSampleEnvelope.from_dict(payload, capability=capability)


@pytest.mark.parametrize(
    "aggregate",
    [
        {"count": 1, "values": [0.1]},
        {"count": 1},
        {"count": 1, "mean": float("nan")},
        {"count": 1, "min": 2.0, "max": 1.0},
        {"count": 1, "stddev": -0.1},
    ],
)
def test_aggregate_shape_and_values_fail_closed(aggregate) -> None:
    with pytest.raises(AiLensLocalModelError):
        AiLensLocalModelSampleEnvelope.create(
            capability=_enabled_capability(),
            sample_count=1,
            duration_ms=1,
            aggregate_metrics={"activation_norm": aggregate},
        )


def test_metric_names_must_be_safe_and_explicitly_supported() -> None:
    with pytest.raises(AiLensLocalModelError, match="safe aggregate metric"):
        _enabled_capability(supported_metrics=("raw_tensor",))

    with pytest.raises(AiLensLocalModelError, match="explicit capability"):
        AiLensLocalModelSampleEnvelope.create(
            capability=_enabled_capability(),
            sample_count=1,
            duration_ms=1,
            aggregate_metrics={"layer_latency": {"count": 1, "mean": 0.1}},
        )


def test_sample_and_duration_cannot_exceed_static_capability_budget() -> None:
    capability = _enabled_capability()

    with pytest.raises(AiLensLocalModelError, match="sample_count"):
        AiLensLocalModelSampleEnvelope.create(
            capability=capability,
            sample_count=9,
            duration_ms=250,
            aggregate_metrics=_aggregate_metrics(),
        )
    with pytest.raises(AiLensLocalModelError, match="duration_ms"):
        AiLensLocalModelSampleEnvelope.create(
            capability=capability,
            sample_count=4,
            duration_ms=2_001,
            aggregate_metrics=_aggregate_metrics(),
        )


def test_round_trip_requires_honest_runtime_truth_and_matching_adapter() -> None:
    capability = _enabled_capability()
    sample = AiLensLocalModelSampleEnvelope.create(
        capability=capability,
        sample_count=4,
        duration_ms=250,
        aggregate_metrics=_aggregate_metrics(),
    )
    assert AiLensLocalModelSampleEnvelope.from_dict(
        sample.to_dict(), capability=capability
    ) == sample

    for field_name, value in (
        ("local_runtime_observed", False),
        ("truth_level", "runtime_trace"),
        ("raw_content_visible", True),
        ("adapter_id", "different-adapter"),
    ):
        payload = sample.to_dict()
        payload[field_name] = value
        with pytest.raises(AiLensLocalModelError):
            AiLensLocalModelSampleEnvelope.from_dict(
                payload, capability=capability
            )


def test_module_has_no_runtime_probe_or_external_io_imports() -> None:
    source_path = Path(__file__).resolve().parents[1] / "src" / "ai_lens_local_model.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots.isdisjoint(
        {
            "os",
            "pathlib",
            "subprocess",
            "socket",
            "requests",
            "httpx",
            "torch",
            "transformers",
            "ollama",
            "psutil",
            "sqlalchemy",
            "flask",
        }
    )
