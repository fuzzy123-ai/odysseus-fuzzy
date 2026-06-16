import pytest

from src.tool_result_truth import ResultStatus, ResultTruthError, ToolResultTruth


def test_success_with_evidence_is_verified():
    result = ToolResultTruth.create(
        status=ResultStatus.SUCCESS,
        summary="Focused backend contract landed cleanly.",
        evidence=["pytest passed", "contract committed"],
        warnings=[],
        errors=[],
        exit_code=0,
        commit="95ec9074",
        changed_files=["src/tool_result_truth.py", "tests/test_tool_result_truth.py"],
        tests=["python -m pytest tests/test_tool_result_truth.py"],
        capsule_id="AS3B-tool-result-truth-model-spike",
    )

    assert result.verified_done is True
    assert result.status == ResultStatus.SUCCESS
    assert result.commit == "95ec9074"


def test_claimed_success_without_evidence_is_not_verified():
    result = ToolResultTruth.create(
        status="success",
        summary="Looks done.",
        evidence=[],
        warnings=[],
        errors=[],
        exit_code=0,
        commit="",
        changed_files=[],
        tests=[],
        capsule_id="AS3B-tool-result-truth-model-spike",
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.verified_done is False


@pytest.mark.parametrize("status", [ResultStatus.FAILED, ResultStatus.BLOCKED])
def test_failed_or_blocked_without_error_is_rejected(status):
    with pytest.raises(ResultTruthError):
        ToolResultTruth.create(
            status=status,
            summary="Something went wrong.",
            evidence=[],
            warnings=[],
            errors=[],
            exit_code=1,
            commit="",
            changed_files=[],
            tests=[],
            capsule_id="AS3B-tool-result-truth-model-spike",
        )


@pytest.mark.parametrize(
    "bad_path",
    [
        "../src/tool_result_truth.py",
        "/tmp/tool_result_truth.py",
        r"C:\repo\src\tool_result_truth.py",
        r"src\tool_result_truth.py",
    ],
)
def test_unsafe_changed_file_paths_are_rejected(bad_path):
    with pytest.raises(ResultTruthError):
        ToolResultTruth.create(
            status=ResultStatus.PARTIAL,
            summary="Partial backend progress.",
            evidence=["one passing check"],
            warnings=[],
            errors=[],
            exit_code=0,
            commit="",
            changed_files=[bad_path],
            tests=[],
            capsule_id="AS3B-tool-result-truth-model-spike",
        )


def test_audit_summary_keeps_status_counts_commit_and_tests_without_long_dumps():
    long_output = "tool output with lots of details " * 40
    result = ToolResultTruth.create(
        status=ResultStatus.PARTIAL,
        summary=long_output,
        evidence=[long_output],
        warnings=["needs manual check"],
        errors=[],
        exit_code=0,
        commit="c6a4c35e",
        changed_files=["src/tool_result_truth.py"],
        tests=["python -m pytest tests/test_tool_result_truth.py"],
        capsule_id="AS3B-tool-result-truth-model-spike",
    )

    summary = result.audit_summary()

    assert summary["status"] == "partial"
    assert summary["verified_done"] is False
    assert summary["commit"] == "c6a4c35e"
    assert summary["changed_file_count"] == 1
    assert summary["test_count"] == 1
    assert summary["evidence_count"] == 1
    assert summary["tests"] == ("python -m pytest tests/test_tool_result_truth.py",)
    assert long_output not in repr(summary)
    assert len(result.summary) < len(long_output)
    assert len(result.evidence[0]) < len(long_output)
