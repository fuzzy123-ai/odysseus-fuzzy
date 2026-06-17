import pytest

from src.manual_release_evidence_artifact import ManualReleaseEvidenceArtifact
from src.manual_release_evidence_artifact_naming import build_manual_release_evidence_artifact_filename


def test_filename_is_deterministic_for_same_artifact():
    artifact = _artifact()

    name_a = build_manual_release_evidence_artifact_filename(artifact, extension="md")
    name_b = build_manual_release_evidence_artifact_filename(artifact, extension=".md")

    assert name_a == "morning-evidence-2026-06-17t10-00-00z-01234567.md"
    assert name_a == name_b


def test_filename_sanitizes_label_and_supports_other_extensions():
    artifact = _artifact(label="  Mörning Evidence / Alpha  ", generated_at="2026/06/17 10:00")

    name = build_manual_release_evidence_artifact_filename(artifact, extension="json", sha_prefix_length=12)

    assert name == "morning-evidence-alpha-2026-06-17-10-00-0123456789ab.json"


def test_empty_or_invalid_label_falls_back_to_safe_default():
    artifact = _artifact(label=" !!! ", generated_at="")

    name = build_manual_release_evidence_artifact_filename(artifact, extension="md")

    assert name == "manual-release-evidence-01234567.md"


@pytest.mark.parametrize("extension", ["", ".", "m/d", "md!", " space "])
def test_invalid_extension_is_rejected(extension):
    with pytest.raises(ValueError, match="extension"):
        build_manual_release_evidence_artifact_filename(_artifact(), extension=extension)


@pytest.mark.parametrize("sha256", ["", "abc123", "G" * 64, "a" * 63])
def test_invalid_sha256_is_rejected(sha256):
    with pytest.raises(ValueError, match="sha256"):
        build_manual_release_evidence_artifact_filename(_artifact(sha256=sha256), extension="md")


def test_invalid_sha_prefix_length_is_rejected():
    with pytest.raises(ValueError, match="sha_prefix_length"):
        build_manual_release_evidence_artifact_filename(_artifact(), extension="md", sha_prefix_length=0)

    with pytest.raises(ValueError, match="sha_prefix_length"):
        build_manual_release_evidence_artifact_filename(_artifact(), extension="md", sha_prefix_length=65)


def _artifact(
    *,
    label: str = "Morning Evidence",
    generated_at: str = "2026-06-17T10:00:00Z",
    sha256: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
) -> ManualReleaseEvidenceArtifact:
    return ManualReleaseEvidenceArtifact(
        label=label,
        generated_at=generated_at,
        ok=False,
        markdown="# report",
        json='{"ok": false}',
        sha256=sha256,
    )
