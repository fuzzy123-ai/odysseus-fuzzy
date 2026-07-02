import pytest

from src.agent_result_observer import ResultArtifact, ResultEvidenceBundle, ResultObserverError


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
