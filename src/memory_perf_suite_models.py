"""Offline models for Memory Durability Performance Suite runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


MODELS_SCHEMA = "odysseus.memory_perf_suite.models.v1"
PRESET_NAMES = ("quick", "standard", "stress_local")
REPORT_STATUSES = ("planned", "running", "passed", "failed", "blocked")


class MemoryPerfSuiteModelError(ValueError):
    """Raised when a Memory Perf Suite model input is invalid."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise MemoryPerfSuiteModelError(f"{field_name} must not be empty")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise MemoryPerfSuiteModelError(f"unsupported {field_name}: {value!r}")
    return text


def _nonnegative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemoryPerfSuiteModelError(f"{field_name} must be a non-negative int")
    return value


def _positive_int(value: Any, *, field_name: str) -> int:
    number = _nonnegative_int(value, field_name=field_name)
    if number == 0:
        raise MemoryPerfSuiteModelError(f"{field_name} must be greater than zero")
    return number


def _number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise MemoryPerfSuiteModelError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MemoryPerfSuiteModelError(f"{field_name} must be numeric") from None
    if number < 0:
        raise MemoryPerfSuiteModelError(f"{field_name} must be non-negative")
    return number


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryPerfSuiteModelError(f"{field_name} must be a mapping")
    return value


def _tuple_of_text(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise MemoryPerfSuiteModelError(f"{field_name} must be a sequence")
    return tuple(_normalize_text(item, field_name=field_name) for item in value)


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    max_events: int
    max_event_bytes: int
    max_log_bytes: int
    max_runtime_seconds: int
    max_memory_mb: int

    @classmethod
    def create(
        cls,
        *,
        max_events: Any,
        max_event_bytes: Any,
        max_log_bytes: Any,
        max_runtime_seconds: Any,
        max_memory_mb: Any,
    ) -> "ResourceBudget":
        return cls(
            max_events=_positive_int(max_events, field_name="max_events"),
            max_event_bytes=_positive_int(max_event_bytes, field_name="max_event_bytes"),
            max_log_bytes=_positive_int(max_log_bytes, field_name="max_log_bytes"),
            max_runtime_seconds=_positive_int(max_runtime_seconds, field_name="max_runtime_seconds"),
            max_memory_mb=_positive_int(max_memory_mb, field_name="max_memory_mb"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_events": self.max_events,
            "max_event_bytes": self.max_event_bytes,
            "max_log_bytes": self.max_log_bytes,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_memory_mb": self.max_memory_mb,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceBudget":
        data = _mapping(payload, field_name="budget")
        return cls.create(
            max_events=data.get("max_events"),
            max_event_bytes=data.get("max_event_bytes"),
            max_log_bytes=data.get("max_log_bytes"),
            max_runtime_seconds=data.get("max_runtime_seconds"),
            max_memory_mb=data.get("max_memory_mb"),
        )


@dataclass(frozen=True, slots=True)
class BudgetEstimate:
    event_count: int
    average_event_bytes: int
    estimated_log_bytes: int
    estimated_runtime_seconds: int
    estimated_memory_mb: int
    within_budget: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "average_event_bytes": self.average_event_bytes,
            "estimated_log_bytes": self.estimated_log_bytes,
            "estimated_runtime_seconds": self.estimated_runtime_seconds,
            "estimated_memory_mb": self.estimated_memory_mb,
            "within_budget": self.within_budget,
            "reasons": self.reasons,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BudgetEstimate":
        data = _mapping(payload, field_name="budget_estimate")
        return cls(
            event_count=_positive_int(data.get("event_count"), field_name="event_count"),
            average_event_bytes=_positive_int(data.get("average_event_bytes"), field_name="average_event_bytes"),
            estimated_log_bytes=_positive_int(data.get("estimated_log_bytes"), field_name="estimated_log_bytes"),
            estimated_runtime_seconds=_positive_int(
                data.get("estimated_runtime_seconds"),
                field_name="estimated_runtime_seconds",
            ),
            estimated_memory_mb=_positive_int(data.get("estimated_memory_mb"), field_name="estimated_memory_mb"),
            within_budget=bool(data.get("within_budget")),
            reasons=_tuple_of_text(data.get("reasons", ()), field_name="reasons"),
        )


@dataclass(frozen=True, slots=True)
class ScenarioPreset:
    name: str
    event_count: int
    seed: int
    batch_size: int
    checkpoint_interval: int
    budget: ResourceBudget
    schema: str = MODELS_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _normalize_choice(self.name, field_name="name", choices=PRESET_NAMES))
        object.__setattr__(self, "event_count", _positive_int(self.event_count, field_name="event_count"))
        object.__setattr__(self, "seed", _nonnegative_int(self.seed, field_name="seed"))
        object.__setattr__(self, "batch_size", _positive_int(self.batch_size, field_name="batch_size"))
        object.__setattr__(
            self,
            "checkpoint_interval",
            _positive_int(self.checkpoint_interval, field_name="checkpoint_interval"),
        )
        if not isinstance(self.budget, ResourceBudget):
            raise MemoryPerfSuiteModelError("budget must be a ResourceBudget")
        estimate = self.estimate_budget()
        if not estimate.within_budget:
            raise MemoryPerfSuiteModelError(f"preset {self.name!r} exceeds budget: {', '.join(estimate.reasons)}")

    def estimate_budget(self, *, average_event_bytes: int = 720) -> BudgetEstimate:
        average_size = _positive_int(average_event_bytes, field_name="average_event_bytes")
        estimated_log_bytes = self.event_count * average_size * 2
        estimated_runtime_seconds = max(1, (self.event_count + self.batch_size - 1) // self.batch_size)
        estimated_memory_mb = max(1, (self.batch_size * average_size) // (1024 * 1024) + 1)
        reasons: list[str] = []
        if self.event_count > self.budget.max_events:
            reasons.append("event_count_exceeds_budget")
        if average_size > self.budget.max_event_bytes:
            reasons.append("average_event_bytes_exceeds_budget")
        if estimated_log_bytes > self.budget.max_log_bytes:
            reasons.append("estimated_log_bytes_exceeds_budget")
        if estimated_runtime_seconds > self.budget.max_runtime_seconds:
            reasons.append("estimated_runtime_seconds_exceeds_budget")
        if estimated_memory_mb > self.budget.max_memory_mb:
            reasons.append("estimated_memory_mb_exceeds_budget")
        if not reasons:
            reasons.append("within_resource_budget")
        return BudgetEstimate(
            event_count=self.event_count,
            average_event_bytes=average_size,
            estimated_log_bytes=estimated_log_bytes,
            estimated_runtime_seconds=estimated_runtime_seconds,
            estimated_memory_mb=estimated_memory_mb,
            within_budget=reasons == ["within_resource_budget"],
            reasons=tuple(reasons),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "name": self.name,
            "event_count": self.event_count,
            "seed": self.seed,
            "batch_size": self.batch_size,
            "checkpoint_interval": self.checkpoint_interval,
            "budget": self.budget.to_dict(),
            "budget_estimate": self.estimate_budget().to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioPreset":
        data = _mapping(payload, field_name="scenario_preset")
        return cls(
            name=data.get("name"),
            event_count=data.get("event_count"),
            seed=data.get("seed"),
            batch_size=data.get("batch_size"),
            checkpoint_interval=data.get("checkpoint_interval"),
            budget=ResourceBudget.from_dict(_mapping(data.get("budget"), field_name="budget")),
            schema=_normalize_text(data.get("schema", MODELS_SCHEMA), field_name="schema"),
        )


@dataclass(frozen=True, slots=True)
class SuiteMetric:
    name: str
    value: float
    unit: str

    @classmethod
    def create(cls, *, name: Any, value: Any, unit: Any) -> "SuiteMetric":
        return cls(
            name=_normalize_text(name, field_name="metric.name").lower().replace("-", "_"),
            value=_number(value, field_name="metric.value"),
            unit=_normalize_text(unit, field_name="metric.unit"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "unit": self.unit}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SuiteMetric":
        data = _mapping(payload, field_name="metric")
        return cls.create(name=data.get("name"), value=data.get("value"), unit=data.get("unit"))


@dataclass(frozen=True, slots=True)
class MetricsEnvelope:
    scenario_id: str
    preset_name: str
    metrics: tuple[SuiteMetric, ...]
    schema: str = MODELS_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _normalize_text(self.scenario_id, field_name="scenario_id"))
        object.__setattr__(
            self,
            "preset_name",
            _normalize_choice(self.preset_name, field_name="preset_name", choices=PRESET_NAMES),
        )
        if not isinstance(self.metrics, tuple) or not all(isinstance(metric, SuiteMetric) for metric in self.metrics):
            raise MemoryPerfSuiteModelError("metrics must be a tuple of SuiteMetric values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scenario_id": self.scenario_id,
            "preset_name": self.preset_name,
            "metrics": tuple(metric.to_dict() for metric in self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MetricsEnvelope":
        data = _mapping(payload, field_name="metrics_envelope")
        raw_metrics = data.get("metrics", ())
        if not isinstance(raw_metrics, (list, tuple)):
            raise MemoryPerfSuiteModelError("metrics must be a sequence")
        return cls(
            scenario_id=data.get("scenario_id"),
            preset_name=data.get("preset_name"),
            metrics=tuple(SuiteMetric.from_dict(_mapping(item, field_name="metric")) for item in raw_metrics),
            schema=_normalize_text(data.get("schema", MODELS_SCHEMA), field_name="schema"),
        )


@dataclass(frozen=True, slots=True)
class ReportEnvelope:
    run_id: str
    status: str
    preset: ScenarioPreset
    budget_estimate: BudgetEstimate
    metrics: MetricsEnvelope
    warnings: tuple[str, ...] = ()
    schema: str = MODELS_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _normalize_text(self.run_id, field_name="run_id"))
        object.__setattr__(self, "status", _normalize_choice(self.status, field_name="status", choices=REPORT_STATUSES))
        if not isinstance(self.preset, ScenarioPreset):
            raise MemoryPerfSuiteModelError("preset must be a ScenarioPreset")
        if not isinstance(self.budget_estimate, BudgetEstimate):
            raise MemoryPerfSuiteModelError("budget_estimate must be a BudgetEstimate")
        if not isinstance(self.metrics, MetricsEnvelope):
            raise MemoryPerfSuiteModelError("metrics must be a MetricsEnvelope")
        object.__setattr__(self, "warnings", _tuple_of_text(self.warnings, field_name="warnings"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "status": self.status,
            "preset": self.preset.to_dict(),
            "budget_estimate": self.budget_estimate.to_dict(),
            "metrics": self.metrics.to_dict(),
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReportEnvelope":
        data = _mapping(payload, field_name="report_envelope")
        return cls(
            run_id=data.get("run_id"),
            status=data.get("status"),
            preset=ScenarioPreset.from_dict(_mapping(data.get("preset"), field_name="preset")),
            budget_estimate=BudgetEstimate.from_dict(
                _mapping(data.get("budget_estimate"), field_name="budget_estimate")
            ),
            metrics=MetricsEnvelope.from_dict(_mapping(data.get("metrics"), field_name="metrics")),
            warnings=_tuple_of_text(data.get("warnings", ()), field_name="warnings"),
            schema=_normalize_text(data.get("schema", MODELS_SCHEMA), field_name="schema"),
        )


def build_scenario_preset(name: Any, *, seed: int | None = None) -> ScenarioPreset:
    preset_name = _normalize_choice(name, field_name="name", choices=PRESET_NAMES)
    defaults = {
        "quick": {
            "event_count": 25,
            "seed": 101,
            "batch_size": 5,
            "checkpoint_interval": 5,
            "budget": ResourceBudget.create(
                max_events=50,
                max_event_bytes=4096,
                max_log_bytes=256_000,
                max_runtime_seconds=30,
                max_memory_mb=64,
            ),
        },
        "standard": {
            "event_count": 250,
            "seed": 202,
            "batch_size": 25,
            "checkpoint_interval": 25,
            "budget": ResourceBudget.create(
                max_events=500,
                max_event_bytes=4096,
                max_log_bytes=2_000_000,
                max_runtime_seconds=120,
                max_memory_mb=128,
            ),
        },
        "stress_local": {
            "event_count": 5_000,
            "seed": 303,
            "batch_size": 250,
            "checkpoint_interval": 250,
            "budget": ResourceBudget.create(
                max_events=10_000,
                max_event_bytes=4096,
                max_log_bytes=50_000_000,
                max_runtime_seconds=900,
                max_memory_mb=512,
            ),
        },
    }[preset_name]
    return ScenarioPreset(
        name=preset_name,
        event_count=defaults["event_count"],
        seed=_nonnegative_int(seed, field_name="seed") if seed is not None else defaults["seed"],
        batch_size=defaults["batch_size"],
        checkpoint_interval=defaults["checkpoint_interval"],
        budget=defaults["budget"],
    )
