from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from scripts.validate_parallel_roadmap_execution import (
    CENTRAL_ROUTER_PATHS,
    DEFAULT_GUIDANCE,
    DEFAULT_OPEN_WORK,
    DEFAULT_PLAN,
    INPUT_ERROR,
    MAX_ROADMAP_BYTES,
    ParallelRoadmapInputError,
    _load as validator_load,
    main,
    validate,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _documents() -> tuple[dict, dict, dict]:
    return _load(DEFAULT_PLAN), _load(DEFAULT_GUIDANCE), _load(DEFAULT_OPEN_WORK)


def test_round6_parallel_plan_validates_against_all_active_roadmaps():
    plan, guidance, open_work = _documents()

    report = validate(plan, guidance, open_work)

    assert report["valid"], report["errors"]
    assert report["active_roadmap_count"] == report["active_frontier_count"] == 26
    assert report["retained_terminal_frontier_count"] == 1
    assert report["frontier_count"] == 27
    assert report["active_roadmap_count"] == sum(
        1 for roadmap in guidance["roadmaps"] if roadmap["profile"].startswith("active_")
    )
    assert report["claimable_frontier_count"] == 0
    assert report["wave_count"] == 3
    frontiers = {item["roadmap_path"]: item for item in plan["roadmap_frontiers"]}
    harbour = frontiers["docs/plans/harbour-one-product-roadmap.json"]
    assert harbour.get("membership_role", "active") == "active"
    assert harbour["slice"]["id"] == "H1R-04"
    assert harbour["slice"]["claimable"] is False
    assert "docs/plans/coding-agent-orchestration-consolidation-roadmap.md" not in frontiers
    assert "docs/plans/odysseus-harbor-one-autonomous-workspace-roadmap.md" not in frontiers
    acpr = frontiers["docs/plans/autonomous-coding-production-readiness-roadmap.json"]
    assert acpr["membership_role"] == "retained_terminal_wave_evidence"
    assert any(
        acpr["slice"]["id"] in wave["implementation_slice_ids"]
        and wave["state"].startswith(("repo_accepted", "blocked_"))
        for wave in plan["waves"]
    )


def _retained_frontier(plan: dict) -> dict:
    return next(
        item
        for item in plan["roadmap_frontiers"]
        if item.get("membership_role") == "retained_terminal_wave_evidence"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("unknown_role", "membership_role is unknown"),
        ("object_role", "membership_role is unknown"),
        ("array_role", "membership_role is unknown"),
        ("claimable", "retained terminal frontier must be nonclaimable"),
        ("nonterminal_slice", "retained terminal frontier must have a terminal slice state"),
        ("unsafe_path", "roadmap_path must be a safe canonical repository path"),
        ("unscheduled", "must be referenced by a terminal wave"),
        ("nonterminal_wave", "must be referenced by a terminal wave"),
    ),
)
def test_validator_rejects_invalid_retained_terminal_frontiers(mutation: str, expected: str):
    plan, guidance, open_work = _documents()
    mutated = copy.deepcopy(plan)
    retained = _retained_frontier(mutated)
    slice_id = retained["slice"]["id"]

    if mutation == "unknown_role":
        retained["membership_role"] = "invented"
    elif mutation == "object_role":
        retained["membership_role"] = {"nested": "invented"}
    elif mutation == "array_role":
        retained["membership_role"] = [["invented"]]
    elif mutation == "claimable":
        retained["slice"]["claimable"] = True
    elif mutation == "nonterminal_slice":
        retained["slice"]["state"] = "pending_review"
    elif mutation == "unsafe_path":
        original_path = retained["roadmap_path"]
        retained["roadmap_path"] = "../outside.json"
        matching_guidance = next(
            item for item in guidance["roadmaps"] if item["path"] == original_path
        )
        matching_guidance["path"] = "../outside.json"
    else:
        wave = next(
            item for item in mutated["waves"] if slice_id in item["implementation_slice_ids"]
        )
        if mutation == "unscheduled":
            wave["implementation_slice_ids"].remove(slice_id)
            wave["qa_assignments"].pop(slice_id)
        else:
            wave["state"] = "in_progress"

    report = validate(mutated, guidance, open_work)

    assert not report["valid"]
    assert any(expected in error for error in report["errors"])


@pytest.mark.parametrize("profile", ("active_serial", "archived", None))
def test_validator_requires_retained_path_exactly_once_as_historical_done(profile: str | None):
    plan, guidance, open_work = _documents()
    mutated_guidance = copy.deepcopy(guidance)
    retained_path = _retained_frontier(plan)["roadmap_path"]
    entry = next(item for item in mutated_guidance["roadmaps"] if item["path"] == retained_path)
    if profile is None:
        mutated_guidance["roadmaps"].remove(entry)
    else:
        entry["profile"] = profile

    report = validate(plan, mutated_guidance, open_work)

    assert not report["valid"]
    assert any("exactly once in Guidance as historical_done" in error for error in report["errors"])


def test_validator_rejects_duplicate_path_or_slice_across_membership_classes():
    plan, guidance, open_work = _documents()
    retained = _retained_frontier(plan)
    duplicate_path_plan = copy.deepcopy(plan)
    duplicate_path_plan["roadmap_frontiers"][1]["roadmap_path"] = retained["roadmap_path"]
    duplicate_slice_plan = copy.deepcopy(plan)
    duplicate_slice_plan["roadmap_frontiers"][1]["slice"]["id"] = retained["slice"]["id"]

    path_report = validate(duplicate_path_plan, guidance, open_work)
    slice_report = validate(duplicate_slice_plan, guidance, open_work)

    assert not path_report["valid"]
    assert any("duplicate roadmap frontier" in error for error in path_report["errors"])
    assert not slice_report["valid"]
    assert any("duplicate normalized slice id" in error for error in slice_report["errors"])


def test_validator_rejects_invented_harbour_and_acpr_slice_identities():
    plan, guidance, open_work = _documents()
    harbour_plan = copy.deepcopy(plan)
    harbour = next(
        item
        for item in harbour_plan["roadmap_frontiers"]
        if item["roadmap_path"] == "docs/plans/harbour-one-product-roadmap.json"
    )
    harbour["slice"]["id"] = "H1R-99"

    acpr_plan = copy.deepcopy(plan)
    acpr = _retained_frontier(acpr_plan)
    original_id = acpr["slice"]["id"]
    acpr["slice"]["id"] = "ACPR-INVENTED"
    wave = next(
        item for item in acpr_plan["waves"] if original_id in item["implementation_slice_ids"]
    )
    wave["implementation_slice_ids"] = [
        "ACPR-INVENTED" if item == original_id else item
        for item in wave["implementation_slice_ids"]
    ]
    wave["qa_assignments"]["ACPR-INVENTED"] = wave["qa_assignments"].pop(original_id)

    harbour_report = validate(harbour_plan, guidance, open_work)
    acpr_report = validate(acpr_plan, guidance, open_work)

    assert not harbour_report["valid"]
    assert any("canonical identity 'H1R-04'" in error for error in harbour_report["errors"])
    assert not acpr_report["valid"]
    assert any(
        "canonical identity 'ACPR-12-transactional-worktree-and-safe-tool-capsules'" in error
        for error in acpr_report["errors"]
    )


def test_validator_requires_exactly_one_acpr_retained_frontier_and_three_waves():
    plan, guidance, open_work = _documents()
    missing_plan = copy.deepcopy(plan)
    retained = _retained_frontier(missing_plan)
    retained_id = retained["slice"]["id"]
    missing_plan["roadmap_frontiers"].remove(retained)
    wave = next(
        item for item in missing_plan["waves"] if retained_id in item["implementation_slice_ids"]
    )
    wave["implementation_slice_ids"].remove(retained_id)
    wave["qa_assignments"].pop(retained_id)

    resurrected_plan = copy.deepcopy(plan)
    resurrected = copy.deepcopy(_retained_frontier(resurrected_plan))
    resurrected["roadmap_path"] = "docs/plans/coding-agent-orchestration-consolidation-roadmap.md"
    resurrected["slice"]["id"] = "CAO-08G-verification-repair-independent-review"
    resurrected_plan["roadmap_frontiers"].append(resurrected)
    resurrected_plan["waves"].append(
        {
            "id": "ROUND6-INVENTED",
            "state": "repo_accepted_invented",
            "depends_on_wave_ids": [resurrected_plan["waves"][-1]["id"]],
            "implementation_slice_ids": [resurrected["slice"]["id"]],
            "qa_lane": "independent_qa",
            "qa_assignments": {
                resurrected["slice"]["id"]: resurrected["slice"]["independent_qa_reviewer"]
            },
        }
    )

    missing_report = validate(missing_plan, guidance, open_work)
    resurrected_report = validate(resurrected_plan, guidance, open_work)

    assert not missing_report["valid"]
    assert any("exactly 26 active frontiers plus the one ACPR" in error for error in missing_report["errors"])
    assert not resurrected_report["valid"]
    assert any("exactly 26 active frontiers plus the one ACPR" in error for error in resurrected_report["errors"])
    assert any("exactly three terminal waves" in error for error in resurrected_report["errors"])
    for replacement in (
        "docs/plans/coding-agent-orchestration-consolidation-roadmap.md",
        "docs/plans/odysseus-harbor-one-autonomous-workspace-roadmap.md",
    ):
        replacement_plan = copy.deepcopy(plan)
        _retained_frontier(replacement_plan)["roadmap_path"] = replacement
        replacement_report = validate(replacement_plan, guidance, open_work)
        assert not replacement_report["valid"]
        assert any(
            "exactly 26 active frontiers plus the one ACPR" in error
            for error in replacement_report["errors"]
        )


@pytest.mark.parametrize(
    "document",
    (
        '{"PRIVATE_ROOT_MARKER": 1, "PRIVATE_ROOT_MARKER": 2}',
        '{"outer": {"PRIVATE_NESTED_MARKER": 1, "PRIVATE_NESTED_MARKER": 2}}',
    ),
)
def test_loader_rejects_duplicate_keys_at_every_object_depth(tmp_path: Path, document: str):
    path = tmp_path / "duplicate.json"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ParallelRoadmapInputError) as caught:
        validator_load(path)

    assert type(caught.value) is ParallelRoadmapInputError
    assert str(caught.value) == INPUT_ERROR
    assert caught.value.__cause__ is None
    assert "PRIVATE_" not in repr(caught.value)


@pytest.mark.parametrize(
    "document",
    (
        '{"PRIVATE_TRUNCATED_MARKER":',
        '{"PRIVATE_MALFORMED_MARKER": [1,}',
        '"PRIVATE_SCALAR_MARKER"',
        "[]",
        "null",
        "NaN",
    ),
)
def test_loader_reports_one_fixed_content_free_error_for_invalid_roots(
    tmp_path: Path,
    document: str,
):
    path = tmp_path / "invalid.json"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ParallelRoadmapInputError) as caught:
        validator_load(path)

    assert type(caught.value) is ParallelRoadmapInputError
    assert str(caught.value) == INPUT_ERROR
    assert caught.value.__cause__ is None
    assert "PRIVATE_" not in repr(caught.value)


def test_loader_accepts_an_exactly_bounded_json_object(tmp_path: Path):
    path = tmp_path / "exact-boundary.json"
    prefix = b'{"padding":"'
    suffix = b'"}'
    document = prefix + (b"x" * (MAX_ROADMAP_BYTES - len(prefix) - len(suffix))) + suffix
    assert len(document) == MAX_ROADMAP_BYTES
    path.write_bytes(document)

    payload = validator_load(path)

    assert type(payload) is dict
    assert len(payload["padding"]) == MAX_ROADMAP_BYTES - len(prefix) - len(suffix)


def test_loader_rejects_boundary_plus_one_without_content_leak(tmp_path: Path):
    path = tmp_path / "PRIVATE_OVERSIZE_PATH.json"
    prefix = b'{"padding":"'
    suffix = b'"}'
    document = prefix + (b"x" * (MAX_ROADMAP_BYTES + 1 - len(prefix) - len(suffix))) + suffix
    assert len(document) == MAX_ROADMAP_BYTES + 1
    path.write_bytes(document)

    with pytest.raises(ParallelRoadmapInputError) as caught:
        validator_load(path)

    assert str(caught.value) == INPUT_ERROR
    assert caught.value.__cause__ is None
    rendered = str(caught.value) + repr(caught.value)
    assert "PRIVATE_" not in rendered
    assert str(path) not in rendered


def test_loader_rejects_invalid_utf8_without_raw_decoder_error(tmp_path: Path):
    path = tmp_path / "PRIVATE_UTF8_PATH.json"
    path.write_bytes(b'{"PRIVATE_UTF8_MARKER":"\xff"}')

    with pytest.raises(ParallelRoadmapInputError) as caught:
        validator_load(path)

    assert str(caught.value) == INPUT_ERROR
    assert caught.value.__cause__ is None
    rendered = str(caught.value) + repr(caught.value)
    assert "PRIVATE_" not in rendered
    assert str(path) not in rendered
    assert "UnicodeDecodeError" not in rendered


def test_cli_reports_parser_failure_without_traceback_or_raw_exception(
    monkeypatch,
    capsys,
    tmp_path: Path,
):
    path = tmp_path / "PRIVATE_CLI_PATH.json"
    path.write_text('{"PRIVATE_CLI_MARKER":', encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_parallel_roadmap_execution.py", "--plan", str(path)],
    )

    assert main() == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["errors"] == [INPUT_ERROR]
    assert captured.err == ""
    assert "PRIVATE_" not in captured.out
    assert "JSONDecodeError" not in captured.out
    assert "ValueError" not in captured.out


def test_round6_wave_claimability_matches_terminal_or_active_state():
    plan, guidance, open_work = _documents()

    report = validate(plan, guidance, open_work)

    assert report["valid"], report["errors"]
    frontiers = {item["slice"]["id"]: item["slice"] for item in plan["roadmap_frontiers"]}
    for wave in plan["waves"]:
        terminal = wave["state"].startswith(("repo_accepted", "blocked_"))
        for slice_id in wave["implementation_slice_ids"]:
            contract = frontiers[slice_id]
            slice_terminal = contract["state"].startswith(("accepted_", "repo_accepted", "released_"))
            assert contract["claimable"] is (not terminal and not slice_terminal)


def test_workers_never_own_central_router_paths_and_qa_is_independent():
    plan, _, _ = _documents()

    assert set(plan["authority"]["central_router_paths"]) == CENTRAL_ROUTER_PATHS
    assert plan["authority"]["router_writer"] == "root/Sol master"
    assert plan["authority"]["worker_router_writes_allowed"] is False
    for frontier in plan["roadmap_frontiers"]:
        contract = frontier["slice"]
        assert set(contract["allowed_paths"]).isdisjoint(CENTRAL_ROUTER_PATHS)
        assert set(contract["master_only_paths"]) <= CENTRAL_ROUTER_PATHS
        assert contract["owner"] != contract["independent_qa_reviewer"]


def test_validator_rejects_cross_lane_path_collision():
    plan, guidance, open_work = _documents()
    mutated = copy.deepcopy(plan)
    wave = mutated["waves"][0]
    first_id, second_id = wave["implementation_slice_ids"][:2]
    frontiers = {item["slice"]["id"]: item for item in mutated["roadmap_frontiers"]}
    shared_path = frontiers[first_id]["slice"]["allowed_paths"][0]
    frontiers[second_id]["slice"]["allowed_paths"].append(shared_path)

    report = validate(mutated, guidance, open_work)

    assert not report["valid"]
    assert any("path collision" in error for error in report["errors"])


def test_validator_rejects_self_review_and_blocking_gate_on_claimable_slice():
    plan, guidance, open_work = _documents()
    mutated = copy.deepcopy(plan)
    frontier = next(
        item
        for item in mutated["roadmap_frontiers"]
        if item["slice"]["id"] == "ACPR-12-transactional-worktree-and-safe-tool-capsules"
    )
    contract = frontier["slice"]
    contract["claimable"] = True
    contract["independent_qa_reviewer"] = contract["owner"]
    contract["remaining_user_gates"].append(
        {
            "id": "TEST-USER-GATE",
            "blocks_current_slice": True,
            "reason": "negative contract fixture",
        }
    )

    report = validate(mutated, guidance, open_work)

    assert not report["valid"]
    assert any("reviewer must differ" in error for error in report["errors"])
    assert any("current blocking user gate" in error for error in report["errors"])


def test_validator_rejects_wave_qa_mismatch_and_forward_dependency():
    plan, guidance, open_work = _documents()
    mutated = copy.deepcopy(plan)
    first_wave = mutated["waves"][0]
    first_slice_id = first_wave["implementation_slice_ids"][0]
    first_wave["qa_assignments"][first_slice_id] = "wrong-reviewer"
    first_wave["depends_on_wave_ids"] = [mutated["waves"][1]["id"]]

    report = validate(mutated, guidance, open_work)

    assert not report["valid"]
    assert any("must match its normalized independent reviewer" in error for error in report["errors"])
    assert any("must reference prior waves only" in error for error in report["errors"])


def test_validator_rejects_claimable_unresolved_dependency_and_missing_owner_authority():
    plan, guidance, open_work = _documents()
    mutated = copy.deepcopy(plan)
    frontier = next(
        item
        for item in mutated["roadmap_frontiers"]
        if item["slice"]["id"] == "UIR-03-composition-root-and-dependency-injection"
    )
    contract = frontier["slice"]
    contract["claimable"] = True
    contract["normalization_blockers"] = []
    contract.pop("owner_authority", None)
    contract["dependency_evidence"][0]["state"] = "pending_fixture_dependency"

    report = validate(mutated, guidance, open_work)

    assert not report["valid"]
    assert any("explicit source owner authority" in error for error in report["errors"])
    assert any("unresolved dependency evidence" in error for error in report["errors"])
