"""Validate the post-Round-5 parallel roadmap execution contract.

The validator keeps planning truth deterministic: every active roadmap must
have one normalized frontier, implementation lanes must be path-disjoint, QA
must be independent, and central router files remain master-only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "docs" / "plans" / "parallel-roadmap-execution-round6.json"
DEFAULT_GUIDANCE = ROOT / "docs" / "plans" / "multi-agent-execution-guidance.json"
DEFAULT_OPEN_WORK = ROOT / "docs" / "plans" / "open-work-completion-master-roadmap.json"

LANE_IDS = {"coding", "memory_data", "app_integration", "independent_qa"}
IMPLEMENTATION_LANES = LANE_IDS - {"independent_qa"}
CENTRAL_ROUTER_PATHS = {
    "docs/plans/open-work-completion-master-roadmap.json",
    "docs/plans/multi-agent-execution-guidance.json",
}
SLICE_FIELDS = {
    "id",
    "state",
    "claimable",
    "depends_on",
    "dependency_evidence",
    "allowed_paths",
    "hotfiles",
    "master_only_paths",
    "owner",
    "independent_qa_reviewer",
    "root_review_depth",
    "automatic_gates",
    "remaining_user_gates",
    "normalization_blockers",
}

MAX_ROADMAP_BYTES = 2 * 1024 * 1024
INPUT_ERROR = "parallel roadmap input must be a duplicate-free JSON object"


class ParallelRoadmapInputError(Exception):
    """Content-free boundary for malformed roadmap JSON."""


class _DuplicateJsonKey(Exception):
    pass


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite_json(_: str) -> None:
    raise ValueError


def _load(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_ROADMAP_BYTES + 1)
    except OSError:
        raise ParallelRoadmapInputError(INPUT_ERROR) from None
    if len(raw) > MAX_ROADMAP_BYTES:
        raise ParallelRoadmapInputError(INPUT_ERROR) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ParallelRoadmapInputError(INPUT_ERROR) from None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (ValueError, RecursionError, _DuplicateJsonKey):
        raise ParallelRoadmapInputError(INPUT_ERROR) from None
    if type(payload) is not dict:
        raise ParallelRoadmapInputError(INPUT_ERROR) from None
    return payload


def _invalid_input_report() -> dict[str, Any]:
    return {
        "valid": False,
        "round_id": None,
        "active_roadmap_count": 0,
        "frontier_count": 0,
        "claimable_frontier_count": 0,
        "wave_count": 0,
        "errors": [INPUT_ERROR],
    }


def _is_safe_repo_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and ".git" not in path.parts


def _list_of_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _duplicate_values(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def validate(
    plan: dict[str, Any],
    guidance: dict[str, Any],
    open_work: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []

    if plan.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if plan.get("kind") != "odysseus.parallel_roadmap_execution_round":
        errors.append("kind must be odysseus.parallel_roadmap_execution_round")
    if plan.get("round_id") != "round6":
        errors.append("round_id must be round6")

    prerequisite = plan.get("prerequisite", {})
    if prerequisite.get("round5_state") != "accepted_released":
        errors.append("prerequisite.round5_state must be accepted_released")
    if "round5_accepted_released" not in str(open_work.get("status", "")):
        errors.append("Open Work authority does not report Round 5 accepted/released")

    authority = plan.get("authority", {})
    router_paths = authority.get("central_router_paths")
    if set(router_paths or []) != CENTRAL_ROUTER_PATHS:
        errors.append("authority.central_router_paths must name the two canonical routers")
    if authority.get("router_writer") != "root/Sol master":
        errors.append("authority.router_writer must be root/Sol master")
    if authority.get("worker_router_writes_allowed") is not False:
        errors.append("worker_router_writes_allowed must be false")

    lanes = plan.get("lanes")
    if not isinstance(lanes, list):
        lanes = []
        errors.append("lanes must be a list")
    lane_ids = [lane.get("id") for lane in lanes if isinstance(lane, dict)]
    if set(lane_ids) != LANE_IDS or len(lane_ids) != len(LANE_IDS):
        errors.append("lanes must contain coding, memory_data, app_integration and independent_qa exactly once")
    qa_lanes = [lane for lane in lanes if isinstance(lane, dict) and lane.get("id") == "independent_qa"]
    if qa_lanes and qa_lanes[0].get("write_mode") != "read_only":
        errors.append("independent_qa lane must be read_only")

    active_paths = {
        entry.get("path")
        for entry in guidance.get("roadmaps", [])
        if isinstance(entry, dict) and str(entry.get("profile", "")).startswith("active_")
    }
    frontiers = plan.get("roadmap_frontiers")
    if not isinstance(frontiers, list):
        frontiers = []
        errors.append("roadmap_frontiers must be a list")
    frontier_paths = [
        item.get("roadmap_path") for item in frontiers if isinstance(item, dict)
    ]
    if set(frontier_paths) != active_paths:
        missing = sorted(active_paths - set(frontier_paths))
        extra = sorted(set(frontier_paths) - active_paths)
        errors.append(f"active roadmap coverage mismatch: missing={missing}, extra={extra}")
    for duplicate in _duplicate_values([path for path in frontier_paths if isinstance(path, str)]):
        errors.append(f"duplicate roadmap frontier: {duplicate}")

    frontier_by_id: dict[str, dict[str, Any]] = {}
    for index, frontier in enumerate(frontiers):
        label = f"roadmap_frontiers[{index}]"
        if not isinstance(frontier, dict):
            errors.append(f"{label} must be an object")
            continue
        lane = frontier.get("lane")
        if lane not in IMPLEMENTATION_LANES:
            errors.append(f"{label}.lane must be an implementation lane")
        slice_contract = frontier.get("slice")
        if not isinstance(slice_contract, dict):
            errors.append(f"{label}.slice must be an object")
            continue
        missing_fields = sorted(SLICE_FIELDS - set(slice_contract))
        if missing_fields:
            errors.append(f"{label}.slice missing fields: {missing_fields}")
            continue
        slice_id = slice_contract.get("id")
        if not isinstance(slice_id, str) or not slice_id:
            errors.append(f"{label}.slice.id must be non-empty")
        elif slice_id in frontier_by_id:
            errors.append(f"duplicate normalized slice id: {slice_id}")
        else:
            frontier_by_id[slice_id] = frontier

        for field in ("depends_on", "allowed_paths", "hotfiles", "master_only_paths", "normalization_blockers"):
            if not _list_of_strings(slice_contract.get(field)):
                errors.append(f"{label}.slice.{field} must be a list of non-empty strings")
        allowed_paths = slice_contract.get("allowed_paths", [])
        hotfiles = slice_contract.get("hotfiles", [])
        master_only_paths = slice_contract.get("master_only_paths", [])
        for field_name, values in (
            ("depends_on", slice_contract.get("depends_on", [])),
            ("allowed_paths", allowed_paths),
            ("hotfiles", hotfiles),
            ("master_only_paths", master_only_paths),
        ):
            for duplicate in _duplicate_values(values):
                errors.append(f"{label}.slice.{field_name} contains duplicate {duplicate!r}")
        for path in [*allowed_paths, *hotfiles, *master_only_paths]:
            if not _is_safe_repo_path(path):
                errors.append(f"{label}: unsafe or non-normalized path {path!r}")
        if not set(hotfiles) <= set(allowed_paths):
            errors.append(f"{label}: hotfiles must be a subset of worker allowed_paths")
        forbidden_router_paths = set(allowed_paths) & CENTRAL_ROUTER_PATHS
        if forbidden_router_paths:
            errors.append(f"{label}: worker allowed_paths contain central routers {sorted(forbidden_router_paths)}")
        if not set(master_only_paths) <= CENTRAL_ROUTER_PATHS:
            errors.append(f"{label}: master_only_paths may contain only canonical routers")

        dependencies = slice_contract.get("depends_on", [])
        dependency_evidence = slice_contract.get("dependency_evidence")
        if not isinstance(dependency_evidence, list):
            errors.append(f"{label}.slice.dependency_evidence must be a list")
        else:
            evidenced = {
                item.get("id") for item in dependency_evidence if isinstance(item, dict)
            }
            if evidenced != set(dependencies):
                errors.append(f"{label}: dependency_evidence must cover depends_on exactly")
            for evidence in dependency_evidence:
                if not isinstance(evidence, dict) or not all(
                    isinstance(evidence.get(key), str) and evidence.get(key)
                    for key in ("id", "state")
                ):
                    errors.append(f"{label}: every dependency evidence item needs id and state")

        owner = slice_contract.get("owner")
        reviewer = slice_contract.get("independent_qa_reviewer")
        if not isinstance(owner, str) or not owner:
            errors.append(f"{label}.slice.owner must be non-empty")
        if not isinstance(reviewer, str) or not reviewer:
            errors.append(f"{label}.slice.independent_qa_reviewer must be non-empty")
        if owner == reviewer:
            errors.append(f"{label}: independent QA reviewer must differ from owner")
        if slice_contract.get("root_review_depth") not in {"standard", "deep"}:
            errors.append(f"{label}.slice.root_review_depth must be standard or deep")

        automatic_gates = slice_contract.get("automatic_gates")
        if not isinstance(automatic_gates, list) or not automatic_gates:
            errors.append(f"{label}.slice.automatic_gates must be a non-empty list")
        else:
            gate_ids: list[str] = []
            for gate in automatic_gates:
                if not isinstance(gate, dict) or not all(
                    isinstance(gate.get(key), str) and gate.get(key)
                    for key in ("id", "command", "pass_condition")
                ):
                    errors.append(f"{label}: every automatic gate needs id, command and pass_condition")
                else:
                    gate_ids.append(gate["id"])
            for duplicate in _duplicate_values(gate_ids):
                errors.append(f"{label}: duplicate automatic gate id {duplicate!r}")
        user_gates = slice_contract.get("remaining_user_gates")
        if not isinstance(user_gates, list):
            errors.append(f"{label}.slice.remaining_user_gates must be a list")
            user_gates = []
        else:
            for gate in user_gates:
                if (
                    not isinstance(gate, dict)
                    or not isinstance(gate.get("id"), str)
                    or not gate.get("id")
                    or not isinstance(gate.get("blocks_current_slice"), bool)
                    or not isinstance(gate.get("reason"), str)
                    or not gate.get("reason")
                ):
                    errors.append(
                        f"{label}: every remaining user gate needs id, boolean blocks_current_slice and reason"
                    )
        blocking_user_gate = any(
            isinstance(gate, dict) and gate.get("blocks_current_slice") is True
            for gate in user_gates
        )
        if slice_contract.get("claimable") is True:
            if slice_contract.get("normalization_blockers"):
                errors.append(f"{label}: claimable slice has normalization blockers")
            if blocking_user_gate:
                errors.append(f"{label}: claimable slice has a current blocking user gate")
            if not allowed_paths:
                errors.append(f"{label}: claimable slice needs worker allowed_paths")
            owner_authority = slice_contract.get("owner_authority")
            if not isinstance(owner_authority, str) or not owner_authority:
                errors.append(f"{label}: claimable slice needs explicit source owner authority")
            if isinstance(dependency_evidence, list):
                unresolved_tokens = ("not_accepted", "pending", "blocked", "waiting", "missing", "unknown", "unreleased")
                for evidence in dependency_evidence:
                    state = evidence.get("state", "") if isinstance(evidence, dict) else ""
                    if any(token in state.lower() for token in unresolved_tokens):
                        errors.append(
                            f"{label}: claimable slice has unresolved dependency evidence {state!r}"
                        )

    waves = plan.get("waves")
    if not isinstance(waves, list) or not waves:
        waves = []
        errors.append("waves must be a non-empty list")
    scheduled_ids: list[str] = []
    seen_wave_ids: set[str] = set()
    for wave_index, wave in enumerate(waves):
        label = f"waves[{wave_index}]"
        if not isinstance(wave, dict):
            errors.append(f"{label} must be an object")
            continue
        wave_id = wave.get("id")
        if not isinstance(wave_id, str) or not wave_id:
            errors.append(f"{label}.id must be non-empty")
        elif wave_id in seen_wave_ids:
            errors.append(f"duplicate wave id: {wave_id}")
        dependencies = wave.get("depends_on_wave_ids", [])
        if not _list_of_strings(dependencies):
            errors.append(f"{label}.depends_on_wave_ids must be a list of prior wave IDs")
        else:
            unknown_or_forward = sorted(set(dependencies) - seen_wave_ids)
            if unknown_or_forward:
                errors.append(
                    f"{label}: wave dependencies must reference prior waves only: {unknown_or_forward}"
                )
        if isinstance(wave_id, str) and wave_id:
            seen_wave_ids.add(wave_id)
        slice_ids = wave.get("implementation_slice_ids")
        if not _list_of_strings(slice_ids):
            errors.append(f"{label}.implementation_slice_ids must be a list of slice IDs")
            continue
        if wave.get("qa_lane") != "independent_qa":
            errors.append(f"{label}.qa_lane must be independent_qa")
        qa_assignments = wave.get("qa_assignments")
        if not isinstance(qa_assignments, dict):
            qa_assignments = {}
            errors.append(f"{label}.qa_assignments must be an object")
        elif set(qa_assignments) != set(slice_ids):
            errors.append(f"{label}.qa_assignments must cover scheduled slices exactly")
        path_owners: dict[str, str] = {}
        lane_owners: dict[str, str] = {}
        wave_state = wave.get("state")
        terminal_wave = isinstance(wave_state, str) and (
            wave_state.startswith("repo_accepted")
            or wave_state.startswith("blocked_")
        )
        for slice_id in slice_ids:
            scheduled_ids.append(slice_id)
            frontier = frontier_by_id.get(slice_id)
            if frontier is None:
                errors.append(f"{label}: unknown slice id {slice_id}")
                continue
            contract = frontier["slice"]
            slice_state = contract.get("state")
            slice_terminal = isinstance(slice_state, str) and slice_state.startswith(("accepted_", "repo_accepted", "released_"))
            if contract.get("claimable") is not True and not terminal_wave and not slice_terminal:
                errors.append(f"{label}: scheduled slice {slice_id} is not claimable")
            if terminal_wave and contract.get("claimable") is not False:
                errors.append(
                    f"{label}: terminal wave slice {slice_id} must not remain claimable"
                )
            lane = frontier["lane"]
            if lane in lane_owners:
                errors.append(f"{label}: implementation lane {lane} scheduled more than once")
            lane_owners[lane] = slice_id
            assigned_reviewer = qa_assignments.get(slice_id)
            if assigned_reviewer != contract.get("independent_qa_reviewer"):
                errors.append(
                    f"{label}: QA assignment for {slice_id} must match its normalized independent reviewer"
                )
            if assigned_reviewer == contract.get("owner"):
                errors.append(f"{label}: QA assignment for {slice_id} is not independent")
            for path in contract.get("allowed_paths", []):
                if path in path_owners:
                    errors.append(
                        f"{label}: path collision {path} between {path_owners[path]} and {slice_id}"
                    )
                path_owners[path] = slice_id
    for duplicate in _duplicate_values(scheduled_ids):
        errors.append(f"slice scheduled in multiple waves: {duplicate}")

    return {
        "valid": not errors,
        "round_id": plan.get("round_id"),
        "active_roadmap_count": len(active_paths),
        "frontier_count": len(frontiers),
        "claimable_frontier_count": sum(
            1
            for frontier in frontiers
            if isinstance(frontier, dict)
            and isinstance(frontier.get("slice"), dict)
            and frontier["slice"].get("claimable") is True
        ),
        "wave_count": len(waves),
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--guidance", type=Path, default=DEFAULT_GUIDANCE)
    parser.add_argument("--open-work", type=Path, default=DEFAULT_OPEN_WORK)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = validate(
            _load(args.plan),
            _load(args.guidance),
            _load(args.open_work),
        )
    except ParallelRoadmapInputError:
        report = _invalid_input_report()
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print("PASS" if report["valid"] else "FAIL")
        for error in report["errors"]:
            print(f"- {error}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
