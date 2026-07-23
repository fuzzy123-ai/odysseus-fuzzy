"""Content-free synthetic compatibility comparison for legacy USI readers.

This is migration evidence, not a production SourceAdapter and not shadow
request wiring.  Inputs contain stable references, hashes, locators and policy
metadata only; no corpus content is accepted or emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import json
import math
import re

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    CodeRangeLocator,
    ContentPolicy,
    EvidenceRef,
    MessageRangeLocator,
    PageRangeLocator,
    RowRangeLocator,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    canonical_json,
    content_hash,
    normalized_locator,
)


LEGACY_COMPARISON_SCHEMA = "odysseus.unified_source_index.legacy_comparison.v1"
MAX_LANE_RECORDS = 256

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_OWNER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[^\s:*]{1,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOCATOR_TYPES = (
    TextRangeLocator,
    PageRangeLocator,
    RowRangeLocator,
    MessageRangeLocator,
    CodeRangeLocator,
)


class LegacyComparisonError(ValueError):
    """Raised when compatibility evidence is unsafe, invalid, or unbounded."""


class LegacyLane(StrEnum):
    PERSONAL_DOCS = "personal_docs"
    CURRENT_RAG = "current_rag"
    MEMORY = "memory"
    OBSIDIAN_LENS = "obsidian_lens"


class MigrationDecision(StrEnum):
    KEEP = "keep"
    ADAPT = "adapt"
    RETIRE = "retire"


@dataclass(frozen=True, slots=True)
class CutoverThresholds:
    minimum_legacy_records: int = 1
    minimum_coverage_ratio: float = 1.0
    minimum_locator_parity: float = 1.0
    minimum_policy_parity: float = 1.0
    minimum_content_hash_parity: float = 1.0
    maximum_orphan_records: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "minimum_legacy_records",
            _integer(self.minimum_legacy_records, "minimum_legacy_records", 1, MAX_LANE_RECORDS),
        )
        for field_name in (
            "minimum_coverage_ratio",
            "minimum_locator_parity",
            "minimum_policy_parity",
            "minimum_content_hash_parity",
        ):
            object.__setattr__(self, field_name, _ratio(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "maximum_orphan_records",
            _integer(self.maximum_orphan_records, "maximum_orphan_records", 0, MAX_LANE_RECORDS),
        )


@dataclass(frozen=True, slots=True)
class LegacyLanePlan:
    lane: LegacyLane
    decision: MigrationDecision
    target_role: str
    cutover_action: str
    thresholds: CutoverThresholds = field(default_factory=CutoverThresholds)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", _enum(self.lane, LegacyLane, "lane"))
        object.__setattr__(self, "decision", _enum(self.decision, MigrationDecision, "decision"))
        object.__setattr__(self, "target_role", _text(self.target_role, "target_role", 240))
        object.__setattr__(self, "cutover_action", _text(self.cutover_action, "cutover_action", 240))
        if not isinstance(self.thresholds, CutoverThresholds):
            raise LegacyComparisonError("thresholds must be typed")


@dataclass(frozen=True, slots=True)
class LegacyObservation:
    lane: LegacyLane
    correlation_ref: str
    legacy_ref: str
    owner_scope: str
    classification: Classification
    content_policy: ContentPolicy
    locator: TextRangeLocator | PageRangeLocator | RowRangeLocator | MessageRangeLocator | CodeRangeLocator
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", _enum(self.lane, LegacyLane, "lane"))
        object.__setattr__(self, "correlation_ref", _token(self.correlation_ref, "correlation_ref"))
        object.__setattr__(self, "legacy_ref", _token(self.legacy_ref, "legacy_ref"))
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        object.__setattr__(
            self,
            "classification",
            _enum(self.classification, Classification, "classification"),
        )
        object.__setattr__(
            self,
            "content_policy",
            _enum(self.content_policy, ContentPolicy, "content_policy"),
        )
        if not isinstance(self.locator, _LOCATOR_TYPES):
            raise LegacyComparisonError("legacy locator must be typed")
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, "content_hash"))


@dataclass(frozen=True, slots=True)
class UnifiedObservation:
    lane: LegacyLane
    correlation_ref: str
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane", _enum(self.lane, LegacyLane, "lane"))
        object.__setattr__(self, "correlation_ref", _token(self.correlation_ref, "correlation_ref"))
        if not isinstance(self.evidence, EvidenceRef) or self.evidence.locator is None:
            raise LegacyComparisonError("unified observation requires located exact evidence")


@dataclass(frozen=True, slots=True)
class LaneComparison:
    plan: LegacyLanePlan
    legacy_count: int
    unified_count: int
    matched_count: int
    missing_in_unified: tuple[str, ...]
    orphan_in_unified: tuple[str, ...]
    locator_mismatches: tuple[str, ...]
    policy_mismatches: tuple[str, ...]
    content_hash_mismatches: tuple[str, ...]
    coverage_ratio: float
    locator_parity: float
    policy_parity: float
    content_hash_parity: float
    gate_failures: tuple[str, ...]
    cutover_ready: bool

    def __post_init__(self) -> None:
        if not isinstance(self.plan, LegacyLanePlan):
            raise LegacyComparisonError("lane comparison plan must be typed")
        for field_name in ("legacy_count", "unified_count", "matched_count"):
            object.__setattr__(
                self,
                field_name,
                _integer(getattr(self, field_name), field_name, 0, MAX_LANE_RECORDS),
            )
        for field_name in (
            "missing_in_unified",
            "orphan_in_unified",
            "locator_mismatches",
            "policy_mismatches",
            "content_hash_mismatches",
            "gate_failures",
        ):
            object.__setattr__(
                self,
                field_name,
                _tokens(getattr(self, field_name), field_name, MAX_LANE_RECORDS),
            )
        for field_name in (
            "coverage_ratio",
            "locator_parity",
            "policy_parity",
            "content_hash_parity",
        ):
            object.__setattr__(self, field_name, _ratio(getattr(self, field_name), field_name))
        if not isinstance(self.cutover_ready, bool) or self.cutover_ready != (not self.gate_failures):
            raise LegacyComparisonError("cutover_ready and gate_failures disagree")

    def to_dict(self) -> dict:
        return {
            "lane": self.plan.lane.value,
            "decision": self.plan.decision.value,
            "target_role": self.plan.target_role,
            "cutover_action": self.plan.cutover_action,
            "thresholds": {
                "minimum_legacy_records": self.plan.thresholds.minimum_legacy_records,
                "minimum_coverage_ratio": self.plan.thresholds.minimum_coverage_ratio,
                "minimum_locator_parity": self.plan.thresholds.minimum_locator_parity,
                "minimum_policy_parity": self.plan.thresholds.minimum_policy_parity,
                "minimum_content_hash_parity": self.plan.thresholds.minimum_content_hash_parity,
                "maximum_orphan_records": self.plan.thresholds.maximum_orphan_records,
            },
            "counts": {
                "legacy": self.legacy_count,
                "unified": self.unified_count,
                "matched": self.matched_count,
                "missing_in_unified": len(self.missing_in_unified),
                "orphan_in_unified": len(self.orphan_in_unified),
            },
            "parity": {
                "coverage": self.coverage_ratio,
                "locator": self.locator_parity,
                "policy": self.policy_parity,
                "content_hash": self.content_hash_parity,
            },
            "mismatch_refs": {
                "missing_in_unified": list(self.missing_in_unified),
                "orphan_in_unified": list(self.orphan_in_unified),
                "locator": list(self.locator_mismatches),
                "policy": list(self.policy_mismatches),
                "content_hash": list(self.content_hash_mismatches),
            },
            "gate_failures": list(self.gate_failures),
            "cutover_ready": self.cutover_ready,
        }


@dataclass(frozen=True, slots=True)
class LegacyComparisonReport:
    comparison_id: str
    fixture_profile: str
    lanes: tuple[LaneComparison, ...]
    all_gates_ready: bool
    synthetic_evidence: bool = True
    live_cutover_authorized: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparison_id", _token(self.comparison_id, "comparison_id"))
        object.__setattr__(self, "fixture_profile", _token(self.fixture_profile, "fixture_profile"))
        if (
            not isinstance(self.lanes, tuple)
            or len(self.lanes) != len(LegacyLane)
            or {item.plan.lane for item in self.lanes} != set(LegacyLane)
        ):
            raise LegacyComparisonError("report must contain every legacy lane exactly once")
        expected_ready = all(item.cutover_ready for item in self.lanes)
        if not isinstance(self.all_gates_ready, bool) or self.all_gates_ready != expected_ready:
            raise LegacyComparisonError("all_gates_ready is inconsistent")
        if self.synthetic_evidence is not True or self.live_cutover_authorized is not False:
            raise LegacyComparisonError("comparison may not authorize live cutover")

    def to_dict(self) -> dict:
        return {
            "schema": LEGACY_COMPARISON_SCHEMA,
            "comparison_id": self.comparison_id,
            "fixture_profile": self.fixture_profile,
            "synthetic_evidence": True,
            "private_corpus_accessed": False,
            "shadow_requests_sent": False,
            "dual_write_performed": False,
            "active_path_modified": False,
            "live_cutover_authorized": False,
            "all_gates_ready": self.all_gates_ready,
            "lanes": [item.to_dict() for item in self.lanes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class SyntheticComparisonFixture:
    profile: str
    legacy: tuple[LegacyObservation, ...]
    unified: tuple[UnifiedObservation, ...]


def compare_legacy_sources(
    legacy: tuple[LegacyObservation, ...],
    unified: tuple[UnifiedObservation, ...],
    *,
    fixture_profile: str,
) -> LegacyComparisonReport:
    _observations(legacy, LegacyObservation, "legacy")
    _observations(unified, UnifiedObservation, "unified")
    profile = _token(fixture_profile, "fixture_profile")
    comparisons = []
    for lane in LegacyLane:
        legacy_by_ref = _lane_map(legacy, lane)
        unified_by_ref = _lane_map(unified, lane)
        shared = sorted(legacy_by_ref.keys() & unified_by_ref.keys())
        missing = tuple(sorted(legacy_by_ref.keys() - unified_by_ref.keys()))
        orphan = tuple(sorted(unified_by_ref.keys() - legacy_by_ref.keys()))
        locator_mismatches = []
        policy_mismatches = []
        hash_mismatches = []
        for correlation_ref in shared:
            legacy_item = legacy_by_ref[correlation_ref]
            unified_item = unified_by_ref[correlation_ref]
            evidence = unified_item.evidence
            if normalized_locator(legacy_item.locator) != normalized_locator(evidence.locator):
                locator_mismatches.append(correlation_ref)
            policy = evidence.policy_evidence
            if (
                legacy_item.owner_scope != policy.owner_scope
                or legacy_item.classification is not policy.classification
                or legacy_item.content_policy is not policy.content_policy
            ):
                policy_mismatches.append(correlation_ref)
            if legacy_item.content_hash != evidence.content_hash:
                hash_mismatches.append(correlation_ref)
        matched = len(shared)
        coverage = matched / len(legacy_by_ref) if legacy_by_ref else 0.0
        locator_parity = (matched - len(locator_mismatches)) / matched if matched else 0.0
        policy_parity = (matched - len(policy_mismatches)) / matched if matched else 0.0
        hash_parity = (matched - len(hash_mismatches)) / matched if matched else 0.0
        plan = _LANE_PLANS[lane]
        failures = _gate_failures(
            plan.thresholds,
            legacy_count=len(legacy_by_ref),
            coverage=coverage,
            locator_parity=locator_parity,
            policy_parity=policy_parity,
            hash_parity=hash_parity,
            orphan_count=len(orphan),
        )
        comparisons.append(
            LaneComparison(
                plan,
                len(legacy_by_ref),
                len(unified_by_ref),
                matched,
                missing,
                orphan,
                tuple(locator_mismatches),
                tuple(policy_mismatches),
                tuple(hash_mismatches),
                coverage,
                locator_parity,
                policy_parity,
                hash_parity,
                failures,
                not failures,
            )
        )
    ordered = tuple(comparisons)
    comparison_id = "comparison." + hashlib.sha256(
        canonical_json(
            {"fixture_profile": profile, "lanes": [item.to_dict() for item in ordered]}
        ).encode("utf-8")
    ).hexdigest()
    return LegacyComparisonReport(
        comparison_id,
        profile,
        ordered,
        all(item.cutover_ready for item in ordered),
    )


def synthetic_comparison_fixture(profile: str = "complete") -> SyntheticComparisonFixture:
    profile = _token(profile, "profile")
    if profile not in {"complete", "missing", "locator_mismatch", "policy_mismatch"}:
        raise LegacyComparisonError("synthetic fixture profile is unsupported")
    legacy = []
    unified = []
    for index, lane in enumerate(LegacyLane):
        source_kind = SourceKind.MEMORY if lane is LegacyLane.MEMORY else SourceKind.DOCUMENT
        synthetic_text = f"synthetic-{lane.value}-fixture"
        source = SourceRecord(
            owner_scope="user:fixture",
            source_kind=source_kind,
            canonical_ref=f"fixture:{lane.value}",
            classification=Classification.PRIVATE,
            content_policy=ContentPolicy.INLINE_LOCAL,
            provider_ref=f"fixture.{lane.value}",
        )
        version = SourceVersionRecord.create(
            source,
            revision_ref="rev:fixture",
            content_hash=content_hash(synthetic_text),
            version_observed_at="2026-07-17T08:00:00Z",
            indexed_at="2026-07-17T08:00:00Z",
        )
        locator = TextRangeLocator(index * 100, index * 100 + len(synthetic_text))
        chunk = ChunkRecord.create(
            version,
            locator=locator,
            extractor_profile_ref="fixture-v1",
            content_hash=content_hash(synthetic_text),
            content=synthetic_text,
            indexed_at="2026-07-17T08:00:00Z",
        )
        correlation_ref = f"fixture.{lane.value}.001"
        legacy.append(
            LegacyObservation(
                lane,
                correlation_ref,
                f"legacy.{lane.value}.001",
                source.owner_scope,
                source.classification,
                source.content_policy,
                locator,
                chunk.content_hash,
            )
        )
        unified.append(UnifiedObservation(lane, correlation_ref, chunk.evidence_ref()))
    if profile == "missing":
        unified = [item for item in unified if item.lane is not LegacyLane.CURRENT_RAG]
    elif profile == "locator_mismatch":
        index = next(i for i, item in enumerate(legacy) if item.lane is LegacyLane.PERSONAL_DOCS)
        item = legacy[index]
        legacy[index] = replace(
            item,
            locator=TextRangeLocator(item.locator.start_char + 1, item.locator.end_char + 1),
        )
    elif profile == "policy_mismatch":
        index = next(i for i, item in enumerate(legacy) if item.lane is LegacyLane.MEMORY)
        legacy[index] = replace(legacy[index], classification=Classification.PUBLIC)
    return SyntheticComparisonFixture(profile, tuple(legacy), tuple(unified))


def run_synthetic_comparison(profile: str = "complete") -> LegacyComparisonReport:
    fixture = synthetic_comparison_fixture(profile)
    return compare_legacy_sources(
        fixture.legacy,
        fixture.unified,
        fixture_profile=fixture.profile,
    )


def _gate_failures(
    thresholds: CutoverThresholds,
    *,
    legacy_count: int,
    coverage: float,
    locator_parity: float,
    policy_parity: float,
    hash_parity: float,
    orphan_count: int,
) -> tuple[str, ...]:
    failures = []
    if legacy_count < thresholds.minimum_legacy_records:
        failures.append("minimum_legacy_records")
    if coverage < thresholds.minimum_coverage_ratio:
        failures.append("coverage_ratio")
    if locator_parity < thresholds.minimum_locator_parity:
        failures.append("locator_parity")
    if policy_parity < thresholds.minimum_policy_parity:
        failures.append("policy_parity")
    if hash_parity < thresholds.minimum_content_hash_parity:
        failures.append("content_hash_parity")
    if orphan_count > thresholds.maximum_orphan_records:
        failures.append("orphan_records")
    return tuple(failures)


def _observations(values: tuple, expected_type: type, field_name: str) -> None:
    if (
        not isinstance(values, tuple)
        or len(values) > len(LegacyLane) * MAX_LANE_RECORDS
        or not all(isinstance(item, expected_type) for item in values)
    ):
        raise LegacyComparisonError(f"{field_name} observations must be typed and bounded")
    identities = {(item.lane, item.correlation_ref) for item in values}
    if len(identities) != len(values):
        raise LegacyComparisonError(f"{field_name} observations contain duplicate correlations")


def _lane_map(values: tuple, lane: LegacyLane) -> dict[str, object]:
    return {item.correlation_ref: item for item in values if item.lane is lane}


def _tokens(values: tuple[str, ...], field_name: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise LegacyComparisonError(f"{field_name} must be a bounded tuple")
    normalized = tuple(sorted({_token(item, field_name) for item in values}))
    if len(normalized) != len(values):
        raise LegacyComparisonError(f"{field_name} contains duplicates")
    return normalized


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise LegacyComparisonError(f"{field_name} must be a bounded token")
    return value


def _owner_scope(value: str) -> str:
    if not isinstance(value, str) or not _OWNER_RE.fullmatch(value) or value.lower().endswith(":all"):
        raise LegacyComparisonError("owner_scope must be explicit and bounded")
    return value


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise LegacyComparisonError(f"{field_name} must be a SHA-256 reference")
    return value


def _text(value: str, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LegacyComparisonError(f"{field_name} must be bounded text")
    if any(ord(char) < 32 for char in value):
        raise LegacyComparisonError(f"{field_name} contains control characters")
    return value.strip()


def _integer(value: int, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise LegacyComparisonError(f"{field_name} is outside its bound")
    return value


def _ratio(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LegacyComparisonError(f"{field_name} must be numeric")
    normalized = round(float(value), 12)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise LegacyComparisonError(f"{field_name} is outside its bound")
    return normalized


def _enum(value, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise LegacyComparisonError(f"{field_name} is invalid") from exc


_LANE_PLANS = {
    LegacyLane.PERSONAL_DOCS: LegacyLanePlan(
        LegacyLane.PERSONAL_DOCS,
        MigrationDecision.ADAPT,
        "Retain discovery/domain reads behind a USI SourceAdapter.",
        "Disable the private in-memory query index only after parity and UIR cutover gates pass.",
    ),
    LegacyLane.CURRENT_RAG: LegacyLanePlan(
        LegacyLane.CURRENT_RAG,
        MigrationDecision.ADAPT,
        "Retain Chroma as a rebuildable semantic projection over USI occurrence refs.",
        "Retire legacy chunk identity and direct retrieval only after count, locator, policy and query parity.",
    ),
    LegacyLane.MEMORY: LegacyLanePlan(
        LegacyLane.MEMORY,
        MigrationDecision.KEEP,
        "Keep MemoryManager and lifecycle policy as canonical personal-memory truth.",
        "Add read-only USI indexing; never create memory through indexing or replace the memory write path.",
    ),
    LegacyLane.OBSIDIAN_LENS: LegacyLanePlan(
        LegacyLane.OBSIDIAN_LENS,
        MigrationDecision.RETIRE,
        "Use USI/Derived Runs for knowledge retrieval and Context Transparency for runtime observations.",
        "Remove derived_index/Lens knowledge-reader fallback after parity; retain only checkpoint and observation roles.",
    ),
}


__all__ = [
    "CutoverThresholds",
    "LEGACY_COMPARISON_SCHEMA",
    "LaneComparison",
    "LegacyComparisonError",
    "LegacyComparisonReport",
    "LegacyLane",
    "LegacyLanePlan",
    "LegacyObservation",
    "MigrationDecision",
    "SyntheticComparisonFixture",
    "UnifiedObservation",
    "compare_legacy_sources",
    "run_synthetic_comparison",
    "synthetic_comparison_fixture",
]
