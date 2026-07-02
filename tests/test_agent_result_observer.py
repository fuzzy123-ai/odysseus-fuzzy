import pytest

from src.agent_result_observer import ResultArtifact, ResultEvidenceBundle, ResultObserverError, build_sandbox_result_evidence


def test_result_evidence_bundle_sets_warning_verdict():
    bundle = ResultEvidenceBundle.create(
        run_id="run1",
        artifacts=[
            ResultArtifact.create(
                kind="screenshot",
                artifact_ref="reports/run1/screen.png",
                summary="layout changed as expected",
                status="warning",
            )
        ],
    )

    assert bundle.to_dict()["verdict"] == "warning"
    assert bundle.to_dict()["raw_content_visible"] is False


def test_result_artifact_rejects_secret_summary():
    with pytest.raises(ResultObserverError):
        ResultArtifact.create(
            kind="log_tail",
            artifact_ref="reports/run1/log.txt",
            summary="password=secret",
        )


def test_sandbox_result_evidence_attaches_exit_code_and_next_action():
    payload = build_sandbox_result_evidence(
        job_id="pytest_smoke",
        exit_code=1,
        stdout_artifact="reports/sandbox/stdout.log",
        stderr_artifact="reports/sandbox/stderr.log",
        summary="Focused tests failed.",
    )

    assert payload["verdict"] == "failed"
    assert payload["exit_code"] == 1
    assert payload["next_action"] == "inspect_failure"
    assert payload["raw_content_visible"] is False
