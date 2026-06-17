from src.release_artifact_manifest import ReleaseArtifact, build_release_artifact_manifest
from src.release_artifact_markdown import render_release_artifact_manifest_markdown


def test_release_artifact_manifest_markdown_renders_pass(tmp_path):
    artifact_path = tmp_path / "release.md"
    artifact_path.write_text("ok", encoding="utf-8")
    manifest = build_release_artifact_manifest(
        root=tmp_path,
        artifacts=(ReleaseArtifact("release.md", "release note"),),
    )

    markdown = render_release_artifact_manifest_markdown(manifest)

    assert "Status: **PASS**" in markdown
    assert "| `release.md` | release note | yes | present |" in markdown
    assert "Missing required artifacts" not in markdown


def test_release_artifact_manifest_markdown_renders_missing_required_paths(tmp_path):
    manifest = build_release_artifact_manifest(
        root=tmp_path,
        artifacts=(ReleaseArtifact("missing.md", "release note"),),
    )

    markdown = render_release_artifact_manifest_markdown(manifest)

    assert "Status: **BLOCKED**" in markdown
    assert "| `missing.md` | release note | yes | missing |" in markdown
    assert "- `missing.md`" in markdown
