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
    assert report["active_roadmap_count"] == report["frontier_count"]
    assert report["active_roadmap_count"] == 31
    assert report["claimable_frontier_count"] == 0
    assert report["wave_count"] == 3


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

    report = validate(mutated, guidance, open_work)

    assert not report["valid"]
    assert any("explicit source owner authority" in error for error in report["errors"])
    assert any("unresolved dependency evidence" in error for error in report["errors"])
