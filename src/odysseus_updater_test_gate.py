"""Offline updater test gate model for Odysseus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_REPORT_STATUSES = ("ready", "partial", "blocked", "deferred")
_DECISIONS = ("go", "deferred", "no_go")
_EXECUTION_STATUSES = ("completed", "pending", "blocked", "missing")
_RESULT_LABELS = ("pass", "partial", "fail", "blocked", "pending", "missing")
_BLOCKED_PLAN_SOURCES = ("policy", "snapshot")
_MAX_TIMEOUT_SECONDS = 7200


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_slug(value: Any, *, field_name: str) -> str:
    text = (
        _normalize_text(value, field_name=field_name)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return "_".join(part for part in text.split("_") if part)


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a bool")


def _normalize_report_status(value: Any) -> str:
    status = _normalize_slug(value, field_name="status")
    if status not in _REPORT_STATUSES:
        raise ValueError(f"unsupported status: {value!r}")
    return status


def _normalize_decision(value: Any) -> str:
    decision = _normalize_slug(value, field_name="decision")
    if decision not in _DECISIONS:
        raise ValueError(f"unsupported decision: {value!r}")
    return decision


def _normalize_execution_status(value: Any) -> str:
    status = _normalize_slug(value, field_name="execution_status")
    if status not in _EXECUTION_STATUSES:
        raise ValueError(f"unsupported execution_status: {value!r}")
    return status


def _normalize_result_label(value: Any) -> str:
    label = _normalize_slug(value, field_name="result_label")
    if label not in _RESULT_LABELS:
        raise ValueError(f"unsupported result_label: {value!r}")
    return label


def _normalize_blocked_plan_source(value: Any) -> str:
    source = _normalize_slug(value, field_name="source")
    if source not in _BLOCKED_PLAN_SOURCES:
        raise ValueError(f"unsupported source: {value!r}")
    return source


def _normalize_timeout_seconds(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int")
    if value < 1 or value > _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"{field_name} must be between 1 and {_MAX_TIMEOUT_SECONDS}")
    return value


def _normalize_optional_duration(value: Any | None) -> int | None:
    if value is None:
        return None
    return _normalize_timeout_seconds(value, field_name="observed_duration_seconds")


def _normalize_string_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    items = [_normalize_text(value, field_name=field_name) for value in values]
    if not items:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(items)


@dataclass(frozen=True, slots=True)
class AllowedTestSuite:
    suite_id: str
    required: bool
    timeout_seconds: int
    summary: str

    @classmethod
    def create(cls, raw_suite: Mapping[str, Any]) -> "AllowedTestSuite":
        return cls(
            suite_id=_normalize_slug(raw_suite.get("suite_id"), field_name="suite_id"),
            required=_normalize_bool(raw_suite.get("required"), field_name="required"),
            timeout_seconds=_normalize_timeout_seconds(
                raw_suite.get("timeout_seconds"),
                field_name="timeout_seconds",
            ),
            summary=_normalize_text(
                raw_suite.get("summary") or "offline updater test suite",
                field_name="summary",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "required": self.required,
            "timeout_seconds": self.timeout_seconds,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class TestResultSnapshot:
    suite_id: str
    required: bool
    timeout_seconds: int
    execution_status: str
    result_label: str
    summary: str
    observed_duration_seconds: int | None = None
    blocked_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        suite: AllowedTestSuite,
        raw_snapshot: Mapping[str, Any],
    ) -> "TestResultSnapshot":
        execution_status = _normalize_execution_status(
            raw_snapshot.get("execution_status", "completed")
        )
        result_label = _normalize_result_label(raw_snapshot.get("result_label"))
        blocked_reason = raw_snapshot.get("blocked_reason")
        normalized_reason = (
            _normalize_text(blocked_reason, field_name="blocked_reason")
            if blocked_reason is not None
            else None
        )

        if execution_status == "completed" and result_label not in {"pass", "partial", "fail"}:
            raise ValueError("completed snapshots must use pass, partial, or fail result_label")
        if execution_status == "pending" and result_label != "pending":
            raise ValueError("pending snapshots must use result_label='pending'")
        if execution_status == "missing" and result_label != "missing":
            raise ValueError("missing snapshots must use result_label='missing'")
        if execution_status == "blocked" and result_label != "blocked":
            raise ValueError("blocked snapshots must use result_label='blocked'")
        if execution_status == "blocked" and not normalized_reason:
            raise ValueError("blocked snapshots must include blocked_reason")

        return cls(
            suite_id=suite.suite_id,
            required=suite.required,
            timeout_seconds=suite.timeout_seconds,
            execution_status=execution_status,
            result_label=result_label,
            summary=_normalize_text(raw_snapshot.get("summary"), field_name="summary"),
            observed_duration_seconds=_normalize_optional_duration(
                raw_snapshot.get("observed_duration_seconds")
            ),
            blocked_reason=normalized_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "required": self.required,
            "timeout_seconds": self.timeout_seconds,
            "execution_status": self.execution_status,
            "result_label": self.result_label,
            "summary": self.summary,
            "observed_duration_seconds": self.observed_duration_seconds,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True, slots=True)
class BlockedExecutionPlan:
    suite_id: str
    source: str
    reason: str

    @classmethod
    def create(
        cls,
        *,
        suite_id: Any,
        source: Any,
        reason: Any,
    ) -> "BlockedExecutionPlan":
        return cls(
            suite_id=_normalize_slug(suite_id, field_name="suite_id"),
            source=_normalize_blocked_plan_source(source),
            reason=_normalize_text(reason, field_name="reason"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "suite_id": self.suite_id,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class UpdaterTestGateReport:
    status: str
    decision: str
    allowed_suites: tuple[AllowedTestSuite, ...]
    results: tuple[TestResultSnapshot, ...]
    blocked_execution_plans: tuple[BlockedExecutionPlan, ...]
    reasons: tuple[str, ...]
    next_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision,
            "allowed_suites": [suite.to_dict() for suite in self.allowed_suites],
            "results": [result.to_dict() for result in self.results],
            "blocked_execution_plans": [item.to_dict() for item in self.blocked_execution_plans],
            "reasons": list(self.reasons),
            "next_actions": list(self.next_actions),
        }

    def to_compact_report(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision,
            "required_suite_ids": [suite.suite_id for suite in self.allowed_suites if suite.required],
            "optional_suite_ids": [suite.suite_id for suite in self.allowed_suites if not suite.required],
            "result_labels": {result.suite_id: result.result_label for result in self.results},
            "execution_statuses": {
                result.suite_id: result.execution_status for result in self.results
            },
            "blocked_execution_plan_ids": [
                item.suite_id for item in self.blocked_execution_plans
            ],
        }


def _default_missing_snapshot(suite: AllowedTestSuite) -> TestResultSnapshot:
    return TestResultSnapshot(
        suite_id=suite.suite_id,
        required=suite.required,
        timeout_seconds=suite.timeout_seconds,
        execution_status="missing",
        result_label="missing",
        summary=(
            "required updater test snapshot is missing from the offline gate input"
            if suite.required
            else "optional updater test snapshot is missing from the offline gate input"
        ),
        observed_duration_seconds=None,
        blocked_reason=None,
    )


def _build_allowed_suites(
    raw_suites: Iterable[Mapping[str, Any]],
) -> tuple[AllowedTestSuite, ...]:
    suites_by_id: dict[str, AllowedTestSuite] = {}
    for raw_suite in raw_suites:
        suite = AllowedTestSuite.create(raw_suite)
        if suite.suite_id in suites_by_id:
            raise ValueError(f"duplicate suite_id: {suite.suite_id}")
        suites_by_id[suite.suite_id] = suite
    if not suites_by_id:
        raise ValueError("allowed_suites must not be empty")
    return tuple(sorted(suites_by_id.values(), key=lambda item: item.suite_id))


def _build_results(
    *,
    allowed_suites: tuple[AllowedTestSuite, ...],
    raw_snapshots: Iterable[Mapping[str, Any]],
) -> tuple[tuple[TestResultSnapshot, ...], tuple[BlockedExecutionPlan, ...]]:
    allowed_by_id = {suite.suite_id: suite for suite in allowed_suites}
    results_by_id: dict[str, TestResultSnapshot] = {}
    blocked_plans: list[BlockedExecutionPlan] = []

    for raw_snapshot in raw_snapshots:
        suite_id = _normalize_slug(raw_snapshot.get("suite_id"), field_name="suite_id")
        if suite_id in results_by_id:
            raise ValueError(f"duplicate suite_id in result_snapshots: {suite_id}")
        suite = allowed_by_id.get(suite_id)
        if suite is None:
            blocked_plans.append(
                BlockedExecutionPlan.create(
                    suite_id=suite_id,
                    source="policy",
                    reason="test suite is not part of the allowed offline updater gate scope",
                )
            )
            continue
        result = TestResultSnapshot.create(suite=suite, raw_snapshot=raw_snapshot)
        results_by_id[suite_id] = result
        if result.execution_status == "blocked":
            blocked_plans.append(
                BlockedExecutionPlan.create(
                    suite_id=result.suite_id,
                    source="snapshot",
                    reason=result.blocked_reason
                    or "snapshot explicitly marks the suite as blocked",
                )
            )

    for suite in allowed_suites:
        if suite.suite_id not in results_by_id:
            results_by_id[suite.suite_id] = _default_missing_snapshot(suite)

    ordered_results = tuple(results_by_id[suite.suite_id] for suite in allowed_suites)
    ordered_blocked_plans = tuple(sorted(blocked_plans, key=lambda item: (item.suite_id, item.source)))
    return ordered_results, ordered_blocked_plans


def _derive_status(
    *,
    results: tuple[TestResultSnapshot, ...],
    blocked_execution_plans: tuple[BlockedExecutionPlan, ...],
) -> str:
    if blocked_execution_plans:
        return "blocked"

    required_results = tuple(result for result in results if result.required)
    optional_results = tuple(result for result in results if not result.required)
    required_labels = {result.result_label for result in required_results}
    optional_labels = {result.result_label for result in optional_results}

    if required_labels & {"fail", "blocked", "missing"}:
        return "blocked"
    if "pending" in required_labels:
        return "deferred"
    if "partial" in required_labels:
        return "partial"
    if optional_labels - {"pass"}:
        return "partial"
    return "ready"


def _derive_decision(status: str) -> str:
    if status == "ready":
        return "go"
    if status == "blocked":
        return "no_go"
    return "deferred"


def _derive_reasons(
    *,
    status: str,
    results: tuple[TestResultSnapshot, ...],
    blocked_execution_plans: tuple[BlockedExecutionPlan, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for result in results:
        if result.result_label == "pass":
            reasons.append(f"{result.suite_id} is recorded as a passing offline test snapshot")
        elif result.result_label == "partial":
            reasons.append(f"{result.suite_id} is only partially satisfied and keeps the test gate incomplete")
        elif result.result_label == "pending":
            reasons.append(f"{result.suite_id} is still pending structured offline review")
        elif result.result_label == "missing":
            reasons.append(f"{result.suite_id} has no structured offline snapshot")
        elif result.result_label == "blocked":
            reasons.append(f"{result.suite_id} is explicitly blocked in the offline snapshot set")
        else:
            reasons.append(f"{result.suite_id} has a failing offline snapshot")
    for blocked_plan in blocked_execution_plans:
        reasons.append(f"{blocked_plan.suite_id} is blocked by {blocked_plan.source} policy: {blocked_plan.reason}")

    if status == "ready":
        reasons.append("all allowed updater test suites are green within the offline gate model")
    elif status == "partial":
        reasons.append("required suites are not blocked, but the gate remains incomplete")
    elif status == "deferred":
        reasons.append("required suites are pending, so updater review stays deferred")
    else:
        reasons.append("at least one suite is blocked, failing, or outside the allowed updater test scope")
    return tuple(reasons)


def _derive_next_actions(
    *,
    results: tuple[TestResultSnapshot, ...],
    blocked_execution_plans: tuple[BlockedExecutionPlan, ...],
) -> tuple[str, ...]:
    actions: list[str] = []
    for blocked_plan in blocked_execution_plans:
        actions.append(
            f"Remove or replace the blocked execution plan for {blocked_plan.suite_id} before updater promotion."
        )
    for result in results:
        if result.result_label == "pass":
            continue
        if result.result_label == "pending":
            actions.append(
                f"Finalize the structured offline snapshot for {result.suite_id} within the {result.timeout_seconds}-second budget."
            )
        elif result.result_label == "partial":
            actions.append(
                f"Complete the remaining offline checks for {result.suite_id} before the updater gate can turn ready."
            )
        elif result.result_label == "missing":
            actions.append(
                f"Provide a structured offline snapshot for {result.suite_id}; no live execution should be introduced."
            )
        elif result.result_label == "blocked":
            actions.append(
                f"Resolve the blocker for {result.suite_id} and keep the recovery plan offline and bounded."
            )
        else:
            actions.append(
                f"Repair the failing offline snapshot for {result.suite_id} before updater promotion continues."
            )
    if not actions:
        actions.append("Proceed with updater review because every allowed test suite has a passing offline snapshot.")
    return tuple(actions)


def build_odysseus_updater_test_gate(
    *,
    allowed_suites: Iterable[Mapping[str, Any]],
    result_snapshots: Iterable[Mapping[str, Any]],
) -> UpdaterTestGateReport:
    allowed_suite_models = _build_allowed_suites(allowed_suites)
    results, blocked_execution_plans = _build_results(
        allowed_suites=allowed_suite_models,
        raw_snapshots=result_snapshots,
    )
    status = _normalize_report_status(
        _derive_status(
            results=results,
            blocked_execution_plans=blocked_execution_plans,
        )
    )
    return UpdaterTestGateReport(
        status=status,
        decision=_normalize_decision(_derive_decision(status)),
        allowed_suites=allowed_suite_models,
        results=results,
        blocked_execution_plans=blocked_execution_plans,
        reasons=_normalize_string_tuple(
            _derive_reasons(
                status=status,
                results=results,
                blocked_execution_plans=blocked_execution_plans,
            ),
            field_name="reasons",
        ),
        next_actions=_normalize_string_tuple(
            _derive_next_actions(
                results=results,
                blocked_execution_plans=blocked_execution_plans,
            ),
            field_name="next_actions",
        ),
    )
