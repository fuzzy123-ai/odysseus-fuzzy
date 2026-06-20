"""Deterministic synthetic memory events for offline durability tests."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any, Mapping

from src.memory_perf_suite_models import ScenarioPreset, build_scenario_preset


SYNTHETIC_EVENT_SCHEMA = "odysseus.memory_perf_suite.synthetic_event.v1"
FORBIDDEN_DURABLE_KEYS = frozenset(
    {
        "raw_text",
        "content",
        "body",
        "payload",
        "secret",
        "token",
        "password",
        "chat_id",
    }
)


class MemoryPerfSuiteDataError(ValueError):
    """Raised when synthetic suite data is invalid."""


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise MemoryPerfSuiteDataError(f"{field_name} must not be empty")
    return text


def _nonnegative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemoryPerfSuiteDataError(f"{field_name} must be a non-negative int")
    return value


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryPerfSuiteDataError(f"{field_name} must be a mapping")
    return value


def _tuple_of_text(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise MemoryPerfSuiteDataError(f"{field_name} must be a sequence")
    return tuple(_normalize_text(item, field_name=field_name) for item in value)


def assert_no_forbidden_durable_keys(value: Any, *, path: str = "durable_fields") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in FORBIDDEN_DURABLE_KEYS:
                raise MemoryPerfSuiteDataError(f"{path}.{normalized_key} is forbidden in durable fields")
            assert_no_forbidden_durable_keys(item, path=f"{path}.{normalized_key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_forbidden_durable_keys(item, path=f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class SyntheticMemoryEvent:
    event_id: str
    source_hash: str
    sequence: int
    occurred_at: str
    event_type: str
    subject_hash: str
    tags: tuple[str, ...]
    durable_fields: Mapping[str, Any]
    schema: str = SYNTHETIC_EVENT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _normalize_text(self.event_id, field_name="event_id"))
        object.__setattr__(self, "source_hash", _normalize_text(self.source_hash, field_name="source_hash"))
        object.__setattr__(self, "sequence", _nonnegative_int(self.sequence, field_name="sequence"))
        object.__setattr__(self, "occurred_at", _normalize_text(self.occurred_at, field_name="occurred_at"))
        object.__setattr__(self, "event_type", _normalize_text(self.event_type, field_name="event_type"))
        object.__setattr__(self, "subject_hash", _normalize_text(self.subject_hash, field_name="subject_hash"))
        object.__setattr__(self, "tags", _tuple_of_text(self.tags, field_name="tags"))
        durable_fields = dict(_mapping(self.durable_fields, field_name="durable_fields"))
        assert_no_forbidden_durable_keys(durable_fields)
        object.__setattr__(self, "durable_fields", durable_fields)

    @property
    def idempotency_key(self) -> tuple[str, str]:
        return (self.event_id, self.source_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "source_hash": self.source_hash,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "event_type": self.event_type,
            "subject_hash": self.subject_hash,
            "tags": self.tags,
            "durable_fields": dict(self.durable_fields),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SyntheticMemoryEvent":
        data = _mapping(payload, field_name="event")
        return cls(
            event_id=data.get("event_id"),
            source_hash=data.get("source_hash"),
            sequence=data.get("sequence"),
            occurred_at=data.get("occurred_at"),
            event_type=data.get("event_type"),
            subject_hash=data.get("subject_hash"),
            tags=_tuple_of_text(data.get("tags", ()), field_name="tags"),
            durable_fields=_mapping(data.get("durable_fields"), field_name="durable_fields"),
            schema=_normalize_text(data.get("schema", SYNTHETIC_EVENT_SCHEMA), field_name="schema"),
        )


def build_synthetic_memory_event(*, seed: int, sequence: int) -> SyntheticMemoryEvent:
    sequence = _nonnegative_int(sequence, field_name="sequence")
    seed = _nonnegative_int(seed, field_name="seed")
    rng = random.Random(f"mdps:{seed}:{sequence}")
    event_types = ("remember", "update", "merge", "expire")
    domains = ("project", "preference", "fact", "task")
    event_type = event_types[rng.randrange(len(event_types))]
    domain = domains[rng.randrange(len(domains))]
    source_hash = _stable_hash(f"source:{seed}:{sequence}:{domain}")[:32]
    subject_hash = _stable_hash(f"subject:{seed}:{sequence}:{rng.randrange(0, 10_000)}")[:32]
    event_id = _stable_hash(f"event:{seed}:{sequence}:{source_hash}")[:32]
    day = 1 + (sequence % 28)
    minute = sequence % 60
    occurred_at = f"2026-01-{day:02d}T00:{minute:02d}:00Z"
    durable_fields = {
        "domain": domain,
        "importance": round(rng.uniform(0.1, 0.99), 3),
        "retention_class": "durable" if event_type != "expire" else "ttl_candidate",
        "bucket": f"synthetic_{rng.randrange(1, 8)}",
        "fingerprint": _stable_hash(f"fingerprint:{seed}:{sequence}:{event_type}")[:24],
    }
    return SyntheticMemoryEvent(
        event_id=event_id,
        source_hash=source_hash,
        sequence=sequence,
        occurred_at=occurred_at,
        event_type=event_type,
        subject_hash=subject_hash,
        tags=(domain, event_type, "synthetic"),
        durable_fields=durable_fields,
    )


def generate_synthetic_memory_events(
    preset: ScenarioPreset | str = "quick",
    *,
    seed: int | None = None,
    count: int | None = None,
) -> tuple[SyntheticMemoryEvent, ...]:
    scenario = build_scenario_preset(preset, seed=seed) if isinstance(preset, str) else preset
    if not isinstance(scenario, ScenarioPreset):
        raise MemoryPerfSuiteDataError("preset must be a ScenarioPreset or preset name")
    event_count = scenario.event_count if count is None else _nonnegative_int(count, field_name="count")
    return tuple(build_synthetic_memory_event(seed=scenario.seed, sequence=sequence) for sequence in range(event_count))
