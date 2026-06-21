import pytest

from src.handoff_mailbox import ParsedHandoff
from src.quality_gates import QualityGateStatus
from src.runtime_quality_gates import (
    GitStatusSnapshot,
    RuntimeCommandResult,
    RuntimeQualityGateError,
    RuntimeQualityGateInput,
    RuntimeQualityGateRunRequest,
    TestExecutionSnapshot as CommandSnapshot,
    evaluate_runtime_quality_gates,
    run_scoped_runtime_quality_gates,
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


class _FakeCommandRunner:
    def __init__(self, *, git_status: str = "## dev...origin/dev\n", rev: str = "abcdef1\n", test_exit: int = 0):
        self.git_status = git_status
        self.rev = rev
        self.test_exit = test_exit
        self.calls = []

    def __call__(self, argv, *, cwd, timeout_seconds):
        normalized = tuple(argv)
        self.calls.append((normalized, str(cwd), timeout_seconds))
        if normalized == ("git", "status", "--short", "--branch"):
            return RuntimeCommandResult.create(argv=normalized, exit_code=0, output_summary=self.git_status)
        if normalized == ("git", "rev-parse", "--short", "HEAD"):
            return RuntimeCommandResult.create(argv=normalized, exit_code=0, output_summary=self.rev)
        return RuntimeCommandResult.create(
            argv=normalized,
            exit_code=self.test_exit,
            output_summary="2 passed" if self.test_exit == 0 else "1 failed",
        )


def _run_request(**overrides) -> RuntimeQualityGateRunRequest:
    payload = {
        "agent_run_id": "run-auto5",
        "plan_node_id": "auto5",
        "subject_ref": "AUTO5-git-test-quality-gates",
        "verified_at": "2026-06-16T21:35:00Z",
        "verified_by": "Charlie",
        "handoff": _handoff(),
        "repo_root": ".",
        "test_commands": ["python -m pytest tests/test_runtime_quality_gates.py"],
        "allowed_files": ["src/runtime_quality_gates.py", "tests/test_runtime_quality_gates.py"],
        "hot_files": ["plugins/obsidian/frontend/main.js"],
    }
    payload.update(overrides)
    return RuntimeQualityGateRunRequest.create(**payload)


def test_scoped_runner_collects_git_and_focused_pytest_evidence():
    runner = _FakeCommandRunner()

    result = run_scoped_runtime_quality_gates(_run_request(), command_runner=runner)

    assert result.verified_done is True
    assert result.audit_summary()["git"]["branch"] == "dev"
    assert [call[0] for call in runner.calls] == [
        ("git", "status", "--short", "--branch"),
        ("git", "rev-parse", "--short", "HEAD"),
        ("python", "-m", "pytest", "tests/test_runtime_quality_gates.py"),
    ]


def test_scoped_runner_blocks_dirty_git_status():
    runner = _FakeCommandRunner(git_status="## dev\n M src/runtime_quality_gates.py\n")

    result = run_scoped_runtime_quality_gates(_run_request(), command_runner=runner)

    assert result.verified_done is False
    assert "git-block" in result.gate_result.blocking_gate_ids
    assert result.git_status.unstaged_files == ("src/runtime_quality_gates.py",)


def test_scoped_runner_turns_failed_pytest_into_test_gate_failure():
    runner = _FakeCommandRunner(test_exit=1)

    result = run_scoped_runtime_quality_gates(_run_request(), command_runner=runner)

    assert result.verified_done is False
    assert "tests-fail" in result.gate_result.blocking_gate_ids


def test_scoped_runner_rejects_unfocused_or_shell_commands():
    with pytest.raises(RuntimeQualityGateError, match="focused pytest"):
        _run_request(test_commands=["python -m pip install pytest"])

    with pytest.raises(RuntimeQualityGateError, match="shell operators"):
        _run_request(test_commands=["python -m pytest tests/test_runtime_quality_gates.py && curl https://example.com"])


def test_scoped_runner_rejects_non_tests_pytest_targets():
    with pytest.raises(RuntimeQualityGateError, match="tests/ paths"):
        _run_request(test_commands=["python -m pytest src/runtime_quality_gates.py"])
