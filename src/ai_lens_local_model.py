"""Pure capability contract for optional AI Lens local-model internals.

The module accepts only explicit, caller-supplied capability descriptors and
already-aggregated samples.  It never discovers hardware, imports or starts a
model runtime, captures tensors, or performs filesystem, network, provider,
database, or UI I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


AI_LENS_LOCAL_MODEL_CAPABILITY_SCHEMA = (
    "odysseus.ai_lens.local_model_capability.v1"
)
AI_LENS_LOCAL_MODEL_SAMPLE_SCHEMA = "odysseus.ai_lens.local_model_sample.v1"
LOCAL_MODEL_TRUTH_LEVEL = "local_model_internals"

MAX_LOCAL_MODEL_SAMPLE_COUNT = 256
MAX_LOCAL_MODEL_DURATION_MS = 60_000
MAX_LOCAL_MODEL_METRICS = 16
MAX_LOCAL_MODEL_METRIC_NAME_CHARS = 64
MAX_ABS_AGGREGATE_VALUE = 1_000_000_000_000.0

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_SAFE_METRIC_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:^[A-Za-z]:[\\/]|/(?:home|Users|var/lib|mnt|srv)/)", re.IGNORECASE
)
_AGGREGATE_FIELDS = frozenset(
    {"count", "min", "max", "mean", "stddev", "p50", "p95", "p99"}
)
_FORBIDDEN_NAME_MARKERS = (
    "raw",
    "tensor",
    "prompt",
    "completion",
    "provideroutput",
    "privatecontext",
    "documenttext",
    "messagetext",
    "tokenids",
    "weights",
    "gradients",
)
_SAMPLE_INPUT_FIELDS = frozenset(
    {
        "schema",
        "adapter_id",
        "truth_level",
        "sample_count",
        "duration_ms",
        "aggregate_metrics",
        "local_runtime_observed",
        "raw_content_visible",
    }
)


class AiLensLocalModelError(ValueError):
    """Raised when a local-model descriptor or sample fails closed."""


class AiLensLocalModelCapabilityState(StrEnum):
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ENABLED = "enabled"


def _bounded_positive_int(value: Any, *, field_name: str, maximum: int) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or (isinstance(value, str) and value.strip().isdigit())
    ):
        raise AiLensLocalModelError(f"{field_name} must be a positive integer")
    normalized = int(value)
    if normalized < 1 or normalized > maximum:
        raise AiLensLocalModelError(
            f"{field_name} must be between 1 and {maximum}"
        )
    return normalized


def _normalized_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _safe_metric_name(value: Any, *, field_name: str) -> str:
    name = str(value or "").strip()
    normalized = _normalized_name(name)
    if (
        not name
        or len(name) > MAX_LOCAL_MODEL_METRIC_NAME_CHARS
        or not _SAFE_METRIC_RE.fullmatch(name)
        or any(marker in normalized for marker in _FORBIDDEN_NAME_MARKERS)
    ):
        raise AiLensLocalModelError(
            f"{field_name} must be a safe aggregate metric name"
        )
    return name


def _safe_adapter_id(value: Any) -> str:
    adapter_id = str(value or "").strip()
    if (
        not adapter_id
        or not _SAFE_ID_RE.fullmatch(adapter_id)
        or _PRIVATE_PATH_RE.search(adapter_id)
    ):
        raise AiLensLocalModelError("adapter_id must be a bounded safe identifier")
    return adapter_id


def _finite_number(value: Any, *, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AiLensLocalModelError(f"{field_name} must be a finite number")
    if not math.isfinite(value) or abs(value) > MAX_ABS_AGGREGATE_VALUE:
        raise AiLensLocalModelError(f"{field_name} must be a bounded finite number")
    return value


def _freeze_aggregate(
    metric_name: str,
    value: Any,
    *,
    sample_count: int,
) -> Mapping[str, int | float]:
    if not isinstance(value, Mapping):
        raise AiLensLocalModelError(
            f"aggregate_metrics.{metric_name} must contain aggregate fields"
        )
    unknown = set(value) - _AGGREGATE_FIELDS
    if unknown:
        raise AiLensLocalModelError(
            f"aggregate_metrics.{metric_name} contains non-aggregate fields"
        )
    if "count" not in value or len(value) < 2:
        raise AiLensLocalModelError(
            f"aggregate_metrics.{metric_name} requires count and an aggregate value"
        )

    count = _bounded_positive_int(
        value["count"],
        field_name=f"aggregate_metrics.{metric_name}.count",
        maximum=sample_count,
    )
    result: dict[str, int | float] = {"count": count}
    for field_name in sorted(set(value) - {"count"}):
        result[field_name] = _finite_number(
            value[field_name],
            field_name=f"aggregate_metrics.{metric_name}.{field_name}",
        )

    minimum = result.get("min")
    maximum = result.get("max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise AiLensLocalModelError(
            f"aggregate_metrics.{metric_name} min must not exceed max"
        )
    if result.get("stddev", 0) < 0:
        raise AiLensLocalModelError(
            f"aggregate_metrics.{metric_name}.stddev must be non-negative"
        )
    if minimum is not None and maximum is not None:
        for field_name in ("mean", "p50", "p95", "p99"):
            item = result.get(field_name)
            if item is not None and not minimum <= item <= maximum:
                raise AiLensLocalModelError(
                    f"aggregate_metrics.{metric_name}.{field_name} must be within min and max"
                )
    percentiles = [result[name] for name in ("p50", "p95", "p99") if name in result]
    if percentiles != sorted(percentiles):
        raise AiLensLocalModelError(
            f"aggregate_metrics.{metric_name} percentiles must be ordered"
        )
    return MappingProxyType(result)


def _freeze_metrics(
    value: Any,
    *,
    supported_metrics: tuple[str, ...],
    sample_count: int,
) -> Mapping[str, Mapping[str, int | float]]:
    if not isinstance(value, Mapping) or not value:
        raise AiLensLocalModelError("aggregate_metrics must be a non-empty object")
    if len(value) > MAX_LOCAL_MODEL_METRICS:
        raise AiLensLocalModelError(
            f"aggregate_metrics must not exceed {MAX_LOCAL_MODEL_METRICS} metrics"
        )

    supported = set(supported_metrics)
    result: dict[str, Mapping[str, int | float]] = {}
    for raw_name in sorted(value, key=lambda item: str(item)):
        name = _safe_metric_name(raw_name, field_name="aggregate_metrics metric")
        if name not in supported:
            raise AiLensLocalModelError(
                "aggregate_metrics contains a metric outside the explicit capability"
            )
        result[name] = _freeze_aggregate(
            name, value[raw_name], sample_count=sample_count
        )
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class AiLensLocalModelCapability:
    """Static capability descriptor; construction never probes a runtime."""

    enabled: bool = False
    available: bool = False
    adapter_id: str = ""
    supported_metrics: tuple[str, ...] = ()
    max_sample_count: int = MAX_LOCAL_MODEL_SAMPLE_COUNT
    max_duration_ms: int = MAX_LOCAL_MODEL_DURATION_MS

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.available, bool):
            raise AiLensLocalModelError("enabled and available must be booleans")
        if self.enabled and not self.available:
            raise AiLensLocalModelError("an unavailable capability cannot be enabled")

        adapter_id = str(self.adapter_id or "").strip()
        metrics = tuple(
            _safe_metric_name(item, field_name="supported_metrics item")
            for item in self.supported_metrics
        )
        if len(metrics) > MAX_LOCAL_MODEL_METRICS:
            raise AiLensLocalModelError(
                f"supported_metrics must not exceed {MAX_LOCAL_MODEL_METRICS} items"
            )
        if len(set(metrics)) != len(metrics):
            raise AiLensLocalModelError("supported_metrics must be unique")
        if self.available:
            adapter_id = _safe_adapter_id(adapter_id)
            if not metrics:
                raise AiLensLocalModelError(
                    "an available capability requires supported_metrics"
                )
        elif adapter_id or metrics:
            raise AiLensLocalModelError(
                "an unavailable capability cannot advertise an adapter or metrics"
            )

        object.__setattr__(self, "adapter_id", adapter_id)
        object.__setattr__(self, "supported_metrics", tuple(sorted(metrics)))
        object.__setattr__(
            self,
            "max_sample_count",
            _bounded_positive_int(
                self.max_sample_count,
                field_name="max_sample_count",
                maximum=MAX_LOCAL_MODEL_SAMPLE_COUNT,
            ),
        )
        object.__setattr__(
            self,
            "max_duration_ms",
            _bounded_positive_int(
                self.max_duration_ms,
                field_name="max_duration_ms",
                maximum=MAX_LOCAL_MODEL_DURATION_MS,
            ),
        )

    @property
    def state(self) -> AiLensLocalModelCapabilityState:
        if not self.available:
            return AiLensLocalModelCapabilityState.UNAVAILABLE
        if not self.enabled:
            return AiLensLocalModelCapabilityState.DISABLED
        return AiLensLocalModelCapabilityState.ENABLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AI_LENS_LOCAL_MODEL_CAPABILITY_SCHEMA,
            "state": self.state.value,
            "enabled": self.enabled,
            "available": self.available,
            "adapter_id": self.adapter_id,
            "supported_metrics": list(self.supported_metrics),
            "max_sample_count": self.max_sample_count,
            "max_duration_ms": self.max_duration_ms,
            "truth_level": LOCAL_MODEL_TRUTH_LEVEL,
            "runtime_probed": False,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True, init=False)
class AiLensLocalModelSampleEnvelope:
    """Validated aggregate-only evidence supplied by an external live adapter."""

    adapter_id: str
    sample_count: int
    duration_ms: int
    aggregate_metrics: Mapping[str, Mapping[str, int | float]] = field(
        repr=False
    )

    @classmethod
    def create(
        cls,
        *,
        capability: AiLensLocalModelCapability,
        sample_count: Any,
        duration_ms: Any,
        aggregate_metrics: Any,
    ) -> "AiLensLocalModelSampleEnvelope":
        if not isinstance(capability, AiLensLocalModelCapability):
            raise AiLensLocalModelError("capability must be an explicit static descriptor")
        if capability.state != AiLensLocalModelCapabilityState.ENABLED:
            raise AiLensLocalModelError(
                "local-model sampling capability is unavailable or disabled"
            )
        normalized_count = _bounded_positive_int(
            sample_count,
            field_name="sample_count",
            maximum=capability.max_sample_count,
        )
        normalized_duration = _bounded_positive_int(
            duration_ms,
            field_name="duration_ms",
            maximum=capability.max_duration_ms,
        )
        instance = object.__new__(cls)
        object.__setattr__(instance, "adapter_id", capability.adapter_id)
        object.__setattr__(instance, "sample_count", normalized_count)
        object.__setattr__(instance, "duration_ms", normalized_duration)
        object.__setattr__(
            instance,
            "aggregate_metrics",
            _freeze_metrics(
                aggregate_metrics,
                supported_metrics=capability.supported_metrics,
                sample_count=normalized_count,
            ),
        )
        return instance

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        capability: AiLensLocalModelCapability,
    ) -> "AiLensLocalModelSampleEnvelope":
        if not isinstance(value, Mapping):
            raise AiLensLocalModelError("sample envelope must be an object")
        if not isinstance(capability, AiLensLocalModelCapability):
            raise AiLensLocalModelError("capability must be an explicit static descriptor")
        if set(value) - _SAMPLE_INPUT_FIELDS:
            raise AiLensLocalModelError(
                "sample envelope contains raw, private, or unsupported fields"
            )
        if value.get("schema") != AI_LENS_LOCAL_MODEL_SAMPLE_SCHEMA:
            raise AiLensLocalModelError("sample envelope schema is invalid")
        if value.get("adapter_id") != capability.adapter_id:
            raise AiLensLocalModelError(
                "sample envelope adapter_id does not match capability"
            )
        if value.get("truth_level") != LOCAL_MODEL_TRUTH_LEVEL:
            raise AiLensLocalModelError("sample envelope truth_level is invalid")
        if value.get("local_runtime_observed") is not True:
            raise AiLensLocalModelError(
                "sample envelope must declare a real local runtime observation"
            )
        if value.get("raw_content_visible") is not False:
            raise AiLensLocalModelError("sample envelope must hide raw content")
        return cls.create(
            capability=capability,
            sample_count=value.get("sample_count"),
            duration_ms=value.get("duration_ms"),
            aggregate_metrics=value.get("aggregate_metrics"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AI_LENS_LOCAL_MODEL_SAMPLE_SCHEMA,
            "adapter_id": self.adapter_id,
            "truth_level": LOCAL_MODEL_TRUTH_LEVEL,
            "sample_count": self.sample_count,
            "duration_ms": self.duration_ms,
            "aggregate_metrics": {
                metric_name: dict(values)
                for metric_name, values in self.aggregate_metrics.items()
            },
            "local_runtime_observed": True,
            "raw_content_visible": False,
        }

    def to_event_payload(self) -> dict[str, Any]:
        """Return the bounded payload accepted by the AI Lens event contract."""

        payload = self.to_dict()
        payload.pop("schema")
        payload.pop("truth_level")
        payload.pop("raw_content_visible")
        return payload
