#!/usr/bin/env python3
"""Cross-platform, content-free verification lane entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent_verification_receipt import (  # noqa: E402
    ReceiptError,
    build_verification_receipt,
    repository_binding,
    validate_verification_receipt,
)

VERIFY_RUN_SCHEMA = "odysseus.verify_run.v1"
VERIFY_REGISTRY_SCHEMA = "odysseus.verify_registry.v1"
DIFF_IMPACT_PLAN_SCHEMA = "odysseus.diff_impact_plan.v1"
QUALITY_RATCHET_SCHEMA = "odysseus.quality_ratchet.v1"
DIFF_QUALITY_RECEIPT_SCHEMA = "odysseus.diff_quality_receipt.v1"
MAX_VISUAL_EVIDENCE_BYTES = 50 * 1024 * 1024
VISUAL_EVIDENCE_SUFFIXES = frozenset(
    {".gif", ".jpeg", ".jpg", ".mp4", ".png", ".webm"}
)
VISUAL_EVIDENCE_ROOTS = frozenset(
    {"artifacts", "playwright-report", "test-results"}
)


class VerifyExitCode(IntEnum):
    PASSED = 0
    FAILED = 1
    UNAVAILABLE = 2


class CheckStatus(StrEnum):
    PLANNED = "planned"
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class CheckKind(StrEnum):
    INTERNAL = "internal"
    SUBPROCESS = "subprocess"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str
    kind: CheckKind
    command: tuple[str, ...]
    timeout_seconds: int
    evidence_level: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind.value,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "evidence_level": self.evidence_level,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    check_id: str
    status: CheckStatus
    returncode: int | None
    duration_ms: int
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, spec: CheckSpec) -> dict[str, Any]:
        return {
            **spec.to_dict(),
            "status": self.status.value,
            "returncode": self.returncode,
            "duration_ms": max(0, self.duration_ms),
            "details": dict(self.details),
        }


class VerificationConfigError(ValueError):
    """Raised for an unknown or structurally invalid verification request."""


class _CheckUnavailable(RuntimeError):
    pass


_REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_RULE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_QUALITY_KINDS = ("lint", "type")


def _canonical_digest(domain: str, value: Mapping[str, Any]) -> str:
    """Hash one bounded JSON value with an unambiguous local domain."""

    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise VerificationConfigError("value is not canonical JSON") from exc
    if len(raw) > 1024 * 1024:
        raise VerificationConfigError("canonical value is too large")
    framed = domain.encode("ascii") + b"\0" + len(raw).to_bytes(8, "big") + raw
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _detached_json(value: Any) -> Any:
    """Return a JSON-only copy so caller mutation cannot change a receipt."""

    try:
        return json.loads(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise VerificationConfigError("value is not canonical JSON") from exc


def _normalized_repository_path(value: object) -> str | None:
    """Normalize a relative repository path without resolving the filesystem."""

    if type(value) is not str:
        return None
    try:
        if not value or value != unicodedata.normalize("NFC", value):
            return None
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return None
        normalized = value.replace("\\", "/")
        if (
            normalized.startswith(("/", "~", "."))
            or ":" in normalized
            or len(normalized.encode("utf-8", errors="strict")) > 4096
        ):
            return None
        parts = normalized.split("/")
        if any(
            part in {"", ".", ".."}
            or part.startswith(".")
            or part.endswith((".", " "))
            or len(part.encode("utf-8", errors="strict")) > 255
            for part in parts
        ):
            return None
        return "/".join(parts)
    except (UnicodeError, ValueError, OverflowError):
        return None


def _normalized_test_lane(value: object) -> list[str] | None:
    if type(value) is not list or not value:
        return None
    normalized: set[str] = set()
    for item in value:
        path = _normalized_repository_path(item)
        if path is None or not path.startswith("tests/") or not path.endswith(".py"):
            return None
        normalized.add(path)
    return sorted(normalized) or None


def _impact_unavailable(
    *,
    base_revision: object,
    head_revision: object,
    changed_paths: list[str],
    selected_tests: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": DIFF_IMPACT_PLAN_SCHEMA,
        "status": "unavailable",
        "selection": "full",
        "base_revision": base_revision if type(base_revision) is str else "unavailable",
        "head_revision": head_revision if type(head_revision) is str else "unavailable",
        "changed_paths": changed_paths,
        "selected_tests": selected_tests,
        "explanations": [],
        "fail_closed_reasons": sorted(set(reasons)),
    }
    payload["plan_digest"] = _canonical_digest(
        "odysseus.diff_impact_plan.v1", payload
    )
    return payload


def build_diff_impact_plan(
    *,
    base_revision: object,
    head_revision: object,
    changed_paths: object,
    mapping_rules: object,
    full_test_lane: object,
) -> dict[str, Any]:
    """Build one deterministic and explained fail-closed test-impact plan."""

    full_lane = _normalized_test_lane(full_test_lane)
    if full_lane is None:
        raise VerificationConfigError("full test lane is invalid")
    if (
        type(base_revision) is not str
        or _REVISION.fullmatch(base_revision) is None
        or type(head_revision) is not str
        or _REVISION.fullmatch(head_revision) is None
    ):
        return _impact_unavailable(
            base_revision=base_revision,
            head_revision=head_revision,
            changed_paths=[],
            selected_tests=full_lane,
            reasons=["unavailable_revision"],
        )
    if type(changed_paths) is not list or not changed_paths:
        return _impact_unavailable(
            base_revision=base_revision,
            head_revision=head_revision,
            changed_paths=[],
            selected_tests=full_lane,
            reasons=["empty_diff"],
        )
    normalized_paths: set[str] = set()
    for raw_path in changed_paths:
        path = _normalized_repository_path(raw_path)
        if path is None:
            return _impact_unavailable(
                base_revision=base_revision,
                head_revision=head_revision,
                changed_paths=[],
                selected_tests=full_lane,
                reasons=["unsafe_changed_path"],
            )
        normalized_paths.add(path)
    paths = sorted(normalized_paths)

    if type(mapping_rules) is not list:
        return _impact_unavailable(
            base_revision=base_revision,
            head_revision=head_revision,
            changed_paths=paths,
            selected_tests=full_lane,
            reasons=["invalid_mapping_rules"],
        )
    rules: list[dict[str, Any]] = []
    rule_ids: set[str] = set()
    for raw_rule in mapping_rules:
        if type(raw_rule) is not dict or set(raw_rule) != {
            "rule_id",
            "kind",
            "path",
            "tests",
        }:
            return _impact_unavailable(
                base_revision=base_revision,
                head_revision=head_revision,
                changed_paths=paths,
                selected_tests=full_lane,
                reasons=["invalid_mapping_rules"],
            )
        rule_id = raw_rule.get("rule_id")
        kind = raw_rule.get("kind")
        path = _normalized_repository_path(raw_rule.get("path"))
        tests = (
            []
            if kind == "shared" and raw_rule.get("tests") == []
            else _normalized_test_lane(raw_rule.get("tests"))
        )
        if (
            type(rule_id) is not str
            or _RULE_ID.fullmatch(rule_id) is None
            or rule_id in rule_ids
            or kind not in {"exact", "prefix", "shared"}
            or path is None
            or tests is None
        ):
            return _impact_unavailable(
                base_revision=base_revision,
                head_revision=head_revision,
                changed_paths=paths,
                selected_tests=full_lane,
                reasons=["invalid_mapping_rules"],
            )
        rule_ids.add(rule_id)
        rules.append({"rule_id": rule_id, "kind": kind, "path": path, "tests": tests})
    rules.sort(key=lambda item: item["rule_id"])

    selected: set[str] = set()
    explanations: list[dict[str, Any]] = []
    fail_reasons: set[str] = set()
    widen = False
    for path in paths:
        if path.startswith("tests/") and path.endswith(".py"):
            selected.add(path)
            explanations.append(
                {
                    "changed_path": path,
                    "rule_id": "direct_test_change",
                    "reason": "direct_test_change",
                    "selected_tests": [path],
                }
            )
            continue
        matches = [
            rule
            for rule in rules
            if path == rule["path"]
            or (
                rule["kind"] == "prefix"
                and path.startswith(rule["path"].rstrip("/") + "/")
            )
        ]
        if not matches:
            fail_reasons.add("unmapped_path")
            widen = True
            continue
        if len(matches) != 1:
            fail_reasons.add("ambiguous_mapping")
            widen = True
            continue
        rule = matches[0]
        shared = rule["kind"] == "shared"
        chosen = full_lane if shared else list(rule["tests"])
        selected.update(chosen)
        widen = widen or shared
        explanations.append(
            {
                "changed_path": path,
                "rule_id": rule["rule_id"],
                "reason": (
                    "shared_contract_widening" if shared else "reviewed_exact_mapping"
                ),
                "selected_tests": list(chosen),
            }
        )
    if widen:
        selected = set(full_lane)
    payload = {
        "schema": DIFF_IMPACT_PLAN_SCHEMA,
        "status": "planned",
        "selection": "full" if widen else "focused",
        "base_revision": base_revision,
        "head_revision": head_revision,
        "changed_paths": paths,
        "selected_tests": sorted(selected),
        "explanations": sorted(explanations, key=lambda item: item["changed_path"]),
        "fail_closed_reasons": sorted(fail_reasons),
    }
    payload["plan_digest"] = _canonical_digest(
        "odysseus.diff_impact_plan.v1", payload
    )
    return payload


def validate_diff_impact_plan(value: object) -> dict[str, Any]:
    """Validate the closed plan shape and its digest without filesystem access."""

    if type(value) is not dict:
        raise VerificationConfigError("diff impact plan is invalid")
    required = {
        "schema",
        "status",
        "selection",
        "base_revision",
        "head_revision",
        "changed_paths",
        "selected_tests",
        "explanations",
        "fail_closed_reasons",
        "plan_digest",
    }
    if set(value) != required or value.get("schema") != DIFF_IMPACT_PLAN_SCHEMA:
        raise VerificationConfigError("diff impact plan shape is invalid")
    payload = _detached_json(value)
    digest = payload.pop("plan_digest", None)
    if digest != _canonical_digest("odysseus.diff_impact_plan.v1", payload):
        raise VerificationConfigError("diff impact plan digest mismatch")
    return _detached_json(value)


def _quality_measurement(value: object) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != {"lint", "type", "coverage"}:
        return None
    debt: dict[str, dict[str, int]] = {}
    for kind in _QUALITY_KINDS:
        rows = value.get(kind)
        if type(rows) is not list:
            return None
        parsed: dict[str, int] = {}
        for row in rows:
            if type(row) is not dict or set(row) != {"rule_id", "count"}:
                return None
            rule_id, count = row.get("rule_id"), row.get("count")
            if (
                type(rule_id) is not str
                or _RULE_ID.fullmatch(rule_id) is None
                or rule_id in parsed
                or type(count) is not int
                or isinstance(count, bool)
                or count < 0
            ):
                return None
            parsed[rule_id] = count
        debt[kind] = dict(sorted(parsed.items()))
    coverage = value.get("coverage")
    if type(coverage) is not dict or set(coverage) != {"covered", "total"}:
        return None
    covered, total = coverage.get("covered"), coverage.get("total")
    if (
        type(covered) is not int
        or isinstance(covered, bool)
        or type(total) is not int
        or isinstance(total, bool)
        or total <= 0
        or not 0 <= covered <= total
    ):
        return None
    return {**debt, "coverage": {"covered": covered, "total": total}}


def evaluate_quality_ratchet(
    *, plan: object, baseline: object, candidate: object
) -> dict[str, Any]:
    """Compare exact base/candidate measurements without aggregate offsets."""

    try:
        checked_plan = validate_diff_impact_plan(plan)
    except VerificationConfigError:
        checked_plan = None
    base = _quality_measurement(baseline)
    current = _quality_measurement(candidate)
    if checked_plan is None or base is None or current is None:
        payload: dict[str, Any] = {
            "schema": QUALITY_RATCHET_SCHEMA,
            "status": "unavailable",
            "plan_digest": (
                checked_plan["plan_digest"] if checked_plan is not None else "unavailable"
            ),
            "baseline_debt": {},
            "candidate_debt": {},
            "coverage": {},
            "regressions": [],
        }
        payload["ratchet_digest"] = _canonical_digest(
            "odysseus.quality_ratchet.v1", payload
        )
        return payload

    regressions: list[dict[str, Any]] = []
    for kind in _QUALITY_KINDS:
        for rule_id in sorted(set(base[kind]) | set(current[kind])):
            before = base[kind].get(rule_id, 0)
            after = current[kind].get(rule_id, 0)
            if after > before:
                regressions.append(
                    {
                        "kind": kind,
                        "rule_id": rule_id,
                        "baseline": before,
                        "candidate": after,
                    }
                )
    before_coverage = base["coverage"]
    after_coverage = current["coverage"]
    if before_coverage["total"] != after_coverage["total"]:
        regressions.append(
            {
                "kind": "coverage",
                "reason": "coverage_denominator_changed",
                "baseline": before_coverage,
                "candidate": after_coverage,
            }
        )
    elif after_coverage["covered"] < before_coverage["covered"]:
        regressions.append(
            {
                "kind": "coverage",
                "reason": "coverage_decreased",
                "baseline": before_coverage,
                "candidate": after_coverage,
            }
        )
    payload = {
        "schema": QUALITY_RATCHET_SCHEMA,
        "status": "failed" if regressions else "passed",
        "plan_digest": checked_plan["plan_digest"],
        "baseline_debt": {kind: base[kind] for kind in _QUALITY_KINDS},
        "candidate_debt": {kind: current[kind] for kind in _QUALITY_KINDS},
        "coverage": {"baseline": before_coverage, "candidate": after_coverage},
        "regressions": regressions,
    }
    payload["ratchet_digest"] = _canonical_digest(
        "odysseus.quality_ratchet.v1", payload
    )
    return payload


def _validate_quality_ratchet(value: object, *, plan_digest: str) -> dict[str, Any]:
    if type(value) is not dict or value.get("plan_digest") != plan_digest:
        raise VerificationConfigError("quality ratchet binding is invalid")
    payload = _detached_json(value)
    digest = payload.pop("ratchet_digest", None)
    if digest != _canonical_digest("odysseus.quality_ratchet.v1", payload):
        raise VerificationConfigError("quality ratchet digest mismatch")
    return _detached_json(value)


def build_diff_quality_receipt(
    *,
    plan: object,
    ratchet: object,
    binding_before: object,
    binding_after: object,
) -> dict[str, Any]:
    """Bind one plan and ratchet only when repository identity stays exact."""

    checked_plan = validate_diff_impact_plan(plan)
    checked_ratchet = _validate_quality_ratchet(
        ratchet, plan_digest=checked_plan["plan_digest"]
    )
    before = _detached_json(binding_before)
    after = _detached_json(binding_after)
    if before != after:
        raise ReceiptError("repository changed during verification")
    if (
        type(before) is not dict
        or before.get("head_commit") != checked_plan["head_revision"]
    ):
        raise ReceiptError("repository binding does not match the impact plan")
    payload = {
        "schema": DIFF_QUALITY_RECEIPT_SCHEMA,
        "result": checked_ratchet["status"],
        "plan_digest": checked_plan["plan_digest"],
        "ratchet_digest": checked_ratchet["ratchet_digest"],
        "changed_paths": checked_plan["changed_paths"],
        "selected_tests": checked_plan["selected_tests"],
        "repository_binding": before,
    }
    payload["receipt_digest"] = _canonical_digest(
        "odysseus.diff_quality_receipt.v1", payload
    )
    return payload


CHECK_ORDER = (
    "git_diff_check",
    "changed_python_compile",
    "changed_json_parse",
    "pytest_fast",
    "pytest_full",
    "node_syntax",
    "pytest_ui_contract",
    "visual_evidence",
)

LANES: Mapping[str, tuple[str, ...]] = {
    "guards-only": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
    ),
    "fast": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
        "pytest_fast",
    ),
    "full": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
        "pytest_full",
        "node_syntax",
    ),
    "ui": (
        "git_diff_check",
        "changed_python_compile",
        "changed_json_parse",
        "pytest_ui_contract",
        "visual_evidence",
    ),
}

LANE_LIMITS: Mapping[str, tuple[str, ...]] = {
    "guards-only": (
        "integration_not_verified",
        "ui_not_verified",
        "live_not_verified",
        "temporal_not_verified",
    ),
    "fast": (
        "slow_tests_not_verified",
        "ui_not_verified",
        "live_not_verified",
        "temporal_not_verified",
    ),
    "full": (
        "ui_not_verified",
        "live_not_verified",
        "temporal_not_verified",
    ),
    "ui": (
        "visual_evidence_availability_is_not_semantic_review",
        "live_not_verified",
        "temporal_not_verified",
    ),
}


def build_check_registry() -> dict[str, CheckSpec]:
    registry = {
        "git_diff_check": CheckSpec(
            check_id="git_diff_check",
            kind=CheckKind.INTERNAL,
            command=("git", "diff", "--check", "HEAD", "--"),
            timeout_seconds=30,
            evidence_level="static",
        ),
        "changed_python_compile": CheckSpec(
            check_id="changed_python_compile",
            kind=CheckKind.INTERNAL,
            command=("internal", "compile-changed-python"),
            timeout_seconds=30,
            evidence_level="static",
        ),
        "changed_json_parse": CheckSpec(
            check_id="changed_json_parse",
            kind=CheckKind.INTERNAL,
            command=("internal", "parse-changed-json"),
            timeout_seconds=30,
            evidence_level="static",
        ),
        "pytest_fast": CheckSpec(
            check_id="pytest_fast",
            kind=CheckKind.SUBPROCESS,
            command=(
                "{python}",
                "tests/run_focus.py",
                "--fast",
                "--",
                "-q",
            ),
            timeout_seconds=1_200,
            evidence_level="fast",
        ),
        "pytest_full": CheckSpec(
            check_id="pytest_full",
            kind=CheckKind.SUBPROCESS,
            command=("{python}", "-m", "pytest", "-q"),
            timeout_seconds=3_600,
            evidence_level="full",
        ),
        "node_syntax": CheckSpec(
            check_id="node_syntax",
            kind=CheckKind.INTERNAL,
            command=("node", "--check", "static/js/**/*.js"),
            timeout_seconds=600,
            evidence_level="full",
        ),
        "pytest_ui_contract": CheckSpec(
            check_id="pytest_ui_contract",
            kind=CheckKind.SUBPROCESS,
            command=(
                "{python}",
                "tests/run_focus.py",
                "--area",
                "js",
                "--fast",
                "--",
                "-q",
            ),
            timeout_seconds=1_200,
            evidence_level="ui_contract",
        ),
        "visual_evidence": CheckSpec(
            check_id="visual_evidence",
            kind=CheckKind.EVIDENCE,
            command=("evidence", "visual-artifact"),
            timeout_seconds=30,
            evidence_level="visual",
        ),
    }
    if tuple(registry) != CHECK_ORDER:
        raise VerificationConfigError("check registry order is not canonical")
    return registry


def resolve_lane(
    lane: object,
    *,
    registry: Mapping[str, CheckSpec] | None = None,
) -> tuple[CheckSpec, ...]:
    if not isinstance(lane, str) or lane not in LANES:
        raise VerificationConfigError("unknown verification lane")
    registry = registry or build_check_registry()
    try:
        checks = tuple(registry[check_id] for check_id in LANES[lane])
    except KeyError as exc:
        raise VerificationConfigError("lane references an unavailable check") from exc
    if any(not check.required for check in checks):
        raise VerificationConfigError("lane contains a non-required check")
    return checks


def registry_payload() -> dict[str, Any]:
    registry = build_check_registry()
    return {
        "schema": VERIFY_REGISTRY_SCHEMA,
        "lanes": {lane: list(checks) for lane, checks in LANES.items()},
        "checks": [registry[check_id].to_dict() for check_id in CHECK_ORDER],
    }


def run_lane(
    lane: str,
    *,
    root: Path = ROOT,
    visual_evidence: Path | None = None,
    dry_run: bool = False,
    check_runner: Callable[[CheckSpec], CheckOutcome] | None = None,
) -> tuple[dict[str, Any], VerifyExitCode]:
    registry = build_check_registry()
    specs = resolve_lane(lane, registry=registry)
    if dry_run:
        outcomes = [
            CheckOutcome(
                check_id=spec.check_id,
                status=CheckStatus.PLANNED,
                returncode=None,
                duration_ms=0,
            )
            for spec in specs
        ]
        exit_code = VerifyExitCode.PASSED
        status = "planned"
    else:
        outcomes = []
        for spec in specs:
            if check_runner is None:
                outcome = run_check(
                    spec,
                    root=root,
                    visual_evidence=visual_evidence,
                )
            else:
                outcome = check_runner(spec)
            if outcome.check_id != spec.check_id:
                raise VerificationConfigError("check runner returned a mismatched id")
            outcomes.append(outcome)
        exit_code = _exit_code(outcomes)
        status = (
            "passed"
            if exit_code == VerifyExitCode.PASSED
            else "failed"
            if exit_code == VerifyExitCode.FAILED
            else "unavailable"
        )

    report = {
        "schema": VERIFY_RUN_SCHEMA,
        "lane": lane,
        "status": status,
        "exit_code": int(exit_code),
        "strongest_evidence_level": _strongest_level(lane, outcomes),
        "checks": [
            outcome.to_dict(spec)
            for spec, outcome in zip(specs, outcomes, strict=True)
        ],
        "verification_limits": list(LANE_LIMITS[lane]),
    }
    return report, exit_code


def run_lane_with_receipt(
    lane: str,
    *,
    root: Path = ROOT,
    visual_evidence: Path | None = None,
) -> tuple[dict[str, Any], VerifyExitCode]:
    """Run a real lane and attach a receipt only if repository state is stable."""

    binding_before = repository_binding(root)
    report, exit_code = run_lane(
        lane,
        root=root,
        visual_evidence=visual_evidence,
        dry_run=False,
        check_runner=None,
    )
    binding_after = repository_binding(root)
    receipt = build_verification_receipt(
        report,
        binding_before=binding_before,
        binding_after=binding_after,
    )
    validate_verification_receipt(receipt, root=root, expected_lane=lane)
    return {**report, "receipt": receipt}, exit_code


def run_check(
    spec: CheckSpec,
    *,
    root: Path,
    visual_evidence: Path | None,
) -> CheckOutcome:
    started = time.monotonic()
    status = CheckStatus.FAILED
    returncode: int | None = 1
    details: dict[str, Any] = {}
    try:
        if spec.check_id == "git_diff_check":
            _git_diff_check(root, timeout=spec.timeout_seconds)
        elif spec.check_id == "changed_python_compile":
            _compile_changed_python(root)
        elif spec.check_id == "changed_json_parse":
            _parse_changed_json(root)
        elif spec.check_id == "node_syntax":
            details = _node_syntax(root, timeout=spec.timeout_seconds)
        elif spec.check_id == "visual_evidence":
            details = _visual_evidence(visual_evidence, root=root)
        elif spec.kind == CheckKind.SUBPROCESS:
            _run_subprocess_check(spec, root=root)
        else:
            raise VerificationConfigError("check has no registered implementation")
        status = CheckStatus.PASSED
        returncode = 0
    except _CheckUnavailable:
        status = CheckStatus.UNAVAILABLE
        returncode = None
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        SyntaxError,
        subprocess.TimeoutExpired,
    ):
        status = CheckStatus.FAILED
        returncode = 1
    duration_ms = min(
        int((time.monotonic() - started) * 1_000),
        spec.timeout_seconds * 1_000,
    )
    return CheckOutcome(
        check_id=spec.check_id,
        status=status,
        returncode=returncode,
        duration_ms=duration_ms,
        details=details,
    )


def _git_diff_check(root: Path, *, timeout: int) -> None:
    try:
        completed = subprocess.run(
            ["git", "diff", "--check", "HEAD", "--"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _CheckUnavailable from exc
    if completed.returncode != 0:
        raise ValueError("git diff check failed")


def _changed_paths(root: Path) -> tuple[Path, ...]:
    commands = (
        ("git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--"),
        ("git", "ls-files", "--others", "--exclude-standard"),
    )
    names: set[str] = set()
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise _CheckUnavailable from exc
        if completed.returncode != 0:
            raise _CheckUnavailable
        for line in completed.stdout.decode("utf-8", errors="strict").splitlines():
            normalized = line.strip().replace("\\", "/")
            if not normalized:
                continue
            candidate = Path(normalized)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("unsafe changed path")
            names.add(candidate.as_posix())
    return tuple(root / name for name in sorted(names))


def _compile_changed_python(root: Path) -> None:
    for path in _changed_paths(root):
        if path.suffix.lower() != ".py" or not path.is_file():
            continue
        source = path.read_text(encoding="utf-8-sig")
        compile(source, "<changed-python>", "exec", dont_inherit=True)


def _parse_changed_json(root: Path) -> None:
    for path in _changed_paths(root):
        if path.suffix.lower() != ".json" or not path.is_file():
            continue
        json.loads(path.read_text(encoding="utf-8-sig"))


def _node_syntax(root: Path, *, timeout: int) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise _CheckUnavailable
    paths = tuple(sorted((root / "static" / "js").rglob("*.js")))
    if not paths:
        raise _CheckUnavailable
    started = time.monotonic()
    checked = 0
    for path in paths:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("node syntax timeout")
        completed = subprocess.run(
            [node, "--check", str(path.relative_to(root))],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("node syntax check failed")
        checked += 1
    return {"checked_file_count": checked}


def _run_subprocess_check(spec: CheckSpec, *, root: Path) -> None:
    command = [
        sys.executable if token == "{python}" else token
        for token in spec.command
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=spec.timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _CheckUnavailable from exc
    if completed.returncode != 0:
        raise ValueError("verification subprocess failed")


def _visual_evidence(path: Path | None, *, root: Path) -> dict[str, Any]:
    if path is None:
        raise _CheckUnavailable
    resolved = path.resolve()
    if not resolved.is_file() or resolved.suffix.lower() not in VISUAL_EVIDENCE_SUFFIXES:
        raise _CheckUnavailable
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise _CheckUnavailable from exc
    if not relative.parts or relative.parts[0] not in VISUAL_EVIDENCE_ROOTS:
        raise _CheckUnavailable
    size = resolved.stat().st_size
    if not 0 < size <= MAX_VISUAL_EVIDENCE_BYTES:
        raise ValueError("visual evidence size is invalid")
    with resolved.open("rb") as handle:
        header = handle.read(16)
    media_type = _validated_media_type(resolved.suffix.lower(), header)
    if media_type is None:
        raise ValueError("visual evidence format is invalid")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(block)
    return {
        "available": True,
        "bytes": size,
        "media_type": media_type,
        "sha256": digest.hexdigest(),
    }


def _validated_media_type(suffix: str, header: bytes) -> str | None:
    signatures = {
        ".gif": header.startswith((b"GIF87a", b"GIF89a")),
        ".jpeg": header.startswith(b"\xff\xd8\xff"),
        ".jpg": header.startswith(b"\xff\xd8\xff"),
        ".mp4": len(header) >= 8 and header[4:8] == b"ftyp",
        ".png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        ".webm": header.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if suffix == ".webp":
        valid = (
            len(header) >= 12
            and header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
        )
    else:
        valid = signatures.get(suffix, False)
    return suffix.lstrip(".") if valid else None


def _exit_code(outcomes: Sequence[CheckOutcome]) -> VerifyExitCode:
    if any(outcome.status == CheckStatus.FAILED for outcome in outcomes):
        return VerifyExitCode.FAILED
    if any(outcome.status == CheckStatus.UNAVAILABLE for outcome in outcomes):
        return VerifyExitCode.UNAVAILABLE
    if not outcomes or any(outcome.status != CheckStatus.PASSED for outcome in outcomes):
        return VerifyExitCode.FAILED
    return VerifyExitCode.PASSED


def _strongest_level(
    lane: str,
    outcomes: Sequence[CheckOutcome],
) -> str:
    if not outcomes or any(outcome.status != CheckStatus.PASSED for outcome in outcomes):
        return "none"
    return {
        "guards-only": "static",
        "fast": "fast",
        "full": "full",
        "ui": "ui_contract_plus_visual_artifact",
    }[lane]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one named Odysseus verification lane."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--lane", choices=tuple(LANES))
    target.add_argument("--list", action="store_true", help="print the registry")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact content-free plan without running checks",
    )
    parser.add_argument(
        "--visual-evidence",
        type=Path,
        help="existing visual artifact required by the ui lane",
    )
    parser.add_argument(
        "--receipt",
        action="store_true",
        help="attach a current content-free receipt for a real lane run",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.receipt and (args.list or args.dry_run):
        parser.error("--receipt requires a real verification lane run")
    if args.list:
        payload = registry_payload()
        exit_code = VerifyExitCode.PASSED
    elif args.receipt:
        try:
            payload, exit_code = run_lane_with_receipt(
                args.lane,
                visual_evidence=args.visual_evidence,
            )
        except ReceiptError:
            payload = {
                "schema": VERIFY_RUN_SCHEMA,
                "lane": args.lane,
                "status": "not_verified",
                "exit_code": int(VerifyExitCode.FAILED),
                "strongest_evidence_level": "none",
                "checks": [],
                "verification_limits": ["receipt_binding_unavailable"],
                "receipt": None,
            }
            exit_code = VerifyExitCode.FAILED
    else:
        payload, exit_code = run_lane(
            args.lane,
            visual_evidence=args.visual_evidence,
            dry_run=args.dry_run,
        )
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
