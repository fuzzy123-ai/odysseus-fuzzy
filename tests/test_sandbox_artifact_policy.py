import pytest

from src.sandbox_artifact_policy import SandboxArtifactPolicyError, classify_sandbox_artifact


def test_artifact_policy_classifies_retention_and_redaction():
    payload = classify_sandbox_artifact(artifact_ref="reports/sandbox/out.log", kind="log", size_bytes=123)

    assert payload["retention"] == "short"
    assert payload["redaction_required"] is True
    assert payload["raw_content_visible"] is False


def test_artifact_policy_rejects_absolute_path():
    with pytest.raises(SandboxArtifactPolicyError):
        classify_sandbox_artifact(artifact_ref="C:/Users/private/out.log", kind="log")
