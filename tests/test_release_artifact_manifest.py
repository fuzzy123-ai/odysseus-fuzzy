from pathlib import Path

from src.release_artifact_manifest import (
    ReleaseArtifact,
    build_release_artifact_manifest,
    default_release_artifacts,
)


def test_default_release_artifact_manifest_passes_in_checkout():
    manifest = build_release_artifact_manifest()

    assert manifest.ok
    assert manifest.missing_required_paths == ()
    assert "docs/plans/1.0-evidence-release-checklist.md" in {item.path for item in manifest.artifacts}
    assert "src/local_release_readiness_bundle.py" in {item.path for item in manifest.artifacts}


def test_manifest_reports_missing_required_paths(tmp_path):
    manifest = build_release_artifact_manifest(
        root=tmp_path,
        artifacts=(
            ReleaseArtifact("exists.md", "exists"),
            ReleaseArtifact("missing.md", "missing"),
        ),
    )

    (tmp_path / "exists.md").write_text("ok", encoding="utf-8")
    manifest = build_release_artifact_manifest(
        root=tmp_path,
        artifacts=(
            ReleaseArtifact("exists.md", "exists"),
            ReleaseArtifact("missing.md", "missing"),
        ),
    )

    assert not manifest.ok
    assert manifest.missing_required_paths == ("missing.md",)


def test_optional_missing_artifact_does_not_block(tmp_path):
    manifest = build_release_artifact_manifest(
        root=tmp_path,
        artifacts=(ReleaseArtifact("optional.md", "optional", required=False),),
    )

    assert manifest.ok
    assert manifest.missing_required_paths == ()


def test_manifest_to_dict_is_stable(tmp_path):
    (tmp_path / "present.md").write_text("ok", encoding="utf-8")
    manifest = build_release_artifact_manifest(
        root=tmp_path,
        artifacts=(
            ReleaseArtifact("present.md", "present"),
            ReleaseArtifact("missing.md", "missing"),
        ),
    )

    assert manifest.to_dict() == {
        "ok": False,
        "artifacts": (
            {"path": "present.md", "role": "present", "required": True},
            {"path": "missing.md", "role": "missing", "required": True},
        ),
        "missing_required_paths": ("missing.md",),
    }


def test_default_release_artifacts_are_unique():
    paths = [item.path for item in default_release_artifacts()]

    assert len(paths) == len(set(paths))
