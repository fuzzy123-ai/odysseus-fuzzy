import pytest

from src.handoff_mailbox import ParsedHandoff
from src.quality_gates import QualityGateStatus
from src.runtime_quality_gates import (
    GitStatusSnapshot,
    RuntimeQualityGateError,
    RuntimeQualityGateInput,
    TestExecutionSnapshot as CommandSnapshot,
    evaluate_runtime_quality_gates,
)


def _handoff(**overrides) -> ParsedHandoff:
    payload = {
        "agent": "bob",
        "slice_id": "AUTO5-git-test-quality-gates",
        "status": "done",
        "commit": "abcdef1",
        "changed_files": ["src/runtime_quality_gates.py", "tests/test_runtime_quality_gates.py"],
        "tests": ["pytest tests/test_runtime_quality_gates.py"],
        "evidence": ["focused tests green"],
    }
    payload.update(overrides)
    return ParsedHandoff.create(**payload)


def _git(**overrides) -> GitStatusSnapshot:
    payload = {
        "branch": "dev",
        "clean": True,
        "commit": "abcdef1",
        "staged_files": [],
        "unstaged_files": [],
        "untracked_files": [],
    }
    payload.update(overrides)
    return GitStatusSnapshot.create(**payload)


def _test(**overrides) -> CommandSnapshot:
    payload = {
        "command": "pytest tests/test_runtime_quality_gates.py",
        "exit_code": 0,
        "summary": "8 passed, 1 warning",
    }
    payload.update(overrides)
    return CommandSnapshot.create(**payload)


def _payload(**overrides) -> RuntimeQualityGateInput:
    payload = {
        "agent_run_id": "run-auto5",
        "plan_node_id": "auto5",
        "subject_ref": "AUTO5-git-test-quality-gates",
        "verified_at": "2026-06-16T21:35:00Z",
        "verified_by": "Charlie",
        "handoff": _handoff(),
        "git_status": _git(),
        "test_results": [_test()],
        "changed_files": ["src/runtime_quality_gates.py", "tests/test_runtime_quality_gates.py"],
        "allowed_files": ["src/runtime_quality_gates.py", "tests/test_runtime_quality_gates.py"],
        "hot_files": ["plugins/obsidian/frontend/main.js"],
    }
    payload.update(overrides)
    return RuntimeQualityGateInput.create(**payload)


def test_all_runtime_quality_gates_pass_for_clean_verified_slice():
    result = evaluate_runtime_quality_gates(_payload())

    assert result.verified_done is True
    assert result.blocking_gate_ids == ()
    assert result.audit_summary()["status_counts"]["pass"] == 6


def test_dirty_git_snapshot_blocks_verified_done():
    result = evaluate_runtime_quality_gates(
        _payload(
            git_status=_git(
                clean=False,
                unstaged_files=["src/runtime_quality_gates.py"],
            )
        )
    )

    assert result.verified_done is False
    assert "git-block" in result.blocking_gate_ids


def test_missing_real_test_execution_blocks_even_if_handoff_claims_tests():
    result = evaluate_runtime_quality_gates(_payload(test_results=[]))

    assert result.verified_done is False
    assert "tests-block" in result.blocking_gate_ids


def test_failed_test_execution_fails_gate():
    result = evaluate_runtime_quality_gates(
        _payload(test_results=[_test(exit_code=1, summary="failed")])
    )

    assert result.verified_done is False
    assert "tests-fail" in result.blocking_gate_ids
    failed_gate = next(gate for gate in result.gates if gate.gate_id == "tests-fail")
    assert failed_gate.status == QualityGateStatus.FAIL


def test_out_of_scope_changed_file_blocks():
    result = evaluate_runtime_quality_gates(
        _payload(
            changed_files=["src/runtime_quality_gates.py", "routes/gallery_routes.py"],
            allowed_files=["src/runtime_quality_gates.py"],
        )
    )

    assert result.verified_done is False
    assert "scope-block" in result.blocking_gate_ids


def test_hot_file_overlap_blocks():
    result = evaluate_runtime_quality_gates(
        _payload(
            changed_files=["plugins/obsidian/frontend/main.js"],
            allowed_files=["plugins/obsidian/frontend/main.js"],
            hot_files=["plugins/obsidian/frontend/main.js"],
        )
    )

    assert result.verified_done is False
    assert "hot-file-block" in result.blocking_gate_ids


def test_non_done_handoff_blocks():
    result = evaluate_runtime_quality_gates(
        _payload(
            handoff=_handoff(
                status="blocked",
                commit="",
                blocker="waiting for Alice contract",
                evidence=["blocked on docs"],
            )
        )
    )

    assert result.verified_done is False
    assert "handoff-block" in result.blocking_gate_ids


def test_unsafe_paths_are_rejected_before_gate_creation():
    with pytest.raises(RuntimeQualityGateError, match="repo-relative"):
        _payload(changed_files=["../outside.py"])
