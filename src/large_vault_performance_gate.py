"""Release claim gate for large-vault performance evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_CLAIMS = ("small_medium", "large_vault", "custom")
_DECISIONS = ("go", "partial", "no_go")
_MIN_LARGE_FILES = 10_000
_MIN_LARGE_SIZE_MB = 1024
_DEFAULT_INTERACTIVE_P95_MS = 750
_DEFAULT_FILTER_P95_MS = 750
_DEFAULT_GRAPH_P95_MS = 1500
_DEFAULT_REBUILD_MAX_SECONDS = 3600


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ValueError(f"unsupported {field_name}: {value!r}")
    return text


def _nonnegative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int")
    return value


def _positive_int(value: Any, *, field_name: str) -> int:
    number = _nonnegative_int(value, field_name=field_name)
    if number == 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return number


@dataclass(frozen=True, slots=True)
class VaultPerformanceEvidence:
    evidence_id: str
    file_count: int
    total_size_mb: int
    link_count: int
    interactive_p95_ms: int
    filter_p95_ms: int
    graph_p95_ms: int
    rebuild_max_seconds: int
    machine_class: str
    workload: str

    @classmethod
    def create(
        cls,
        *,
        evidence_id: Any,
        file_count: Any,
        total_size_mb: Any,
        link_count: Any,
        interactive_p95_ms: Any,
        filter_p95_ms: Any,
        graph_p95_ms: Any,
        rebuild_max_seconds: Any,
        machine_class: Any,
        workload: Any,
    ) -> "VaultPerformanceEvidence":
        return cls(
            evidence_id=_normalize_text(evidence_id, field_name="evidence_id"),
            file_count=_positive_int(file_count, field_name="file_count"),
            total_size_mb=_positive_int(total_size_mb, field_name="total_size_mb"),
            link_count=_nonnegative_int(link_count, field_name="link_count"),
            interactive_p95_ms=_nonnegative_int(interactive_p95_ms, field_name="interactive_p95_ms"),
            filter_p95_ms=_nonnegative_int(filter_p95_ms, field_name="filter_p95_ms"),
            graph_p95_ms=_nonnegative_int(graph_p95_ms, field_name="graph_p95_ms"),
            rebuild_max_seconds=_nonnegative_int(rebuild_max_seconds, field_name="rebuild_max_seconds"),
            machine_class=_normalize_text(machine_class, field_name="machine_class"),
            workload=_normalize_text(workload, field_name="workload"),
        )

    @property
    def satisfies_large_scale(self) -> bool:
        return self.file_count >= _MIN_LARGE_FILES or self.total_size_mb >= _MIN_LARGE_SIZE_MB

    @property
    def within_default_budgets(self) -> bool:
        return (
            self.interactive_p95_ms <= _DEFAULT_INTERACTIVE_P95_MS
            and self.filter_p95_ms <= _DEFAULT_FILTER_P95_MS
            and self.graph_p95_ms <= _DEFAULT_GRAPH_P95_MS
            and self.rebuild_max_seconds <= _DEFAULT_REBUILD_MAX_SECONDS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "file_count": self.file_count,
            "total_size_mb": self.total_size_mb,
            "link_count": self.link_count,
            "interactive_p95_ms": self.interactive_p95_ms,
            "filter_p95_ms": self.filter_p95_ms,
            "graph_p95_ms": self.graph_p95_ms,
            "rebuild_max_seconds": self.rebuild_max_seconds,
            "machine_class": self.machine_class,
            "workload": self.workload,
        }


@dataclass(frozen=True, slots=True)
class LargeVaultPerformanceGate:
    requested_claim: str
    decision: str
    supported_claim: str
    reasons: tuple[str, ...]
    evidence: VaultPerformanceEvidence

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_claim",
            _normalize_choice(self.requested_claim, field_name="requested_claim", choices=_CLAIMS),
        )
        object.__setattr__(self, "decision", _normalize_choice(self.decision, field_name="decision", choices=_DECISIONS))
        object.__setattr__(
            self,
            "supported_claim",
            _normalize_choice(self.supported_claim, field_name="supported_claim", choices=_CLAIMS),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_claim": self.requested_claim,
            "decision": self.decision,
            "supported_claim": self.supported_claim,
            "reasons": list(self.reasons),
            "evidence": self.evidence.to_dict(),
        }


def build_large_vault_performance_gate(
    *,
    evidence: VaultPerformanceEvidence,
    requested_claim: Any,
) -> LargeVaultPerformanceGate:
    claim = _normalize_choice(requested_claim, field_name="requested_claim", choices=_CLAIMS)
    reasons: list[str] = []

    if evidence.satisfies_large_scale:
        reasons.append("evidence scale meets the large-vault threshold")
        scale_claim = "large_vault"
    else:
        reasons.append("evidence scale is below the large-vault threshold")
        scale_claim = "small_medium"

    if evidence.within_default_budgets:
        reasons.append("all default p95 and rebuild budgets are within threshold")
        budget_ok = True
    else:
        reasons.append("one or more default p95 or rebuild budgets exceed threshold")
        budget_ok = False

    if claim == "large_vault" and scale_claim != "large_vault":
        decision = "no_go"
        supported_claim = "small_medium" if budget_ok else "custom"
        reasons.append("large-vault release claims must be downgraded to the measured scale")
    elif budget_ok:
        decision = "go"
        supported_claim = scale_claim if claim != "custom" else "custom"
    else:
        decision = "partial" if claim != "large_vault" else "no_go"
        supported_claim = "custom"

    return LargeVaultPerformanceGate(
        requested_claim=claim,
        decision=decision,
        supported_claim=supported_claim,
        reasons=tuple(reasons),
        evidence=evidence,
    )
