import pytest

from src.sandbox_artifact_policy import SandboxArtifactPolicyError, classify_sandbox_artifact


def test_artifact_policy_classifies_retention_and_redaction():
    payload = classify_sandbox_artifact(
        artifact_ref="reports/sandbox/out.log",
        kind="log",
        size_bytes=123,
        correlation_id="job1",
    )

    assert payload["retention"] == "short"
    assert payload["redaction_required"] is True
    assert payload["content_hash"].startswith("sha256:")
    assert payload["correlation_id"] == "job1"
    assert payload["raw_content_visible"] is False


def test_artifact_policy_supports_test_and_browser_artifacts():
    report = classify_sandbox_artifact(artifact_ref="reports/sandbox/pytest.xml", kind="test_report")
    network = classify_sandbox_artifact(artifact_ref="reports/sandbox/network.json", kind="network")

    assert report["kind"] == "test_report"
    assert network["kind"] == "network"


def test_artifact_policy_rejects_absolute_path():
    with pytest.raises(SandboxArtifactPolicyError):
        classify_sandbox_artifact(artifact_ref="C:/Users/private/out.log", kind="log")
