from pathlib import Path

import pytest

from src.artifact_integrity import ArtifactIntegrityError, inspect_artifact, inspect_image_artifact


def test_inspect_artifact_returns_real_size_and_hash(tmp_path: Path):
    artifact = tmp_path / "reports" / "run.log"
    artifact.parent.mkdir()
    artifact.write_text("hello\n", encoding="utf-8")
    expected_size = len(artifact.read_bytes())

    result = inspect_artifact("reports/run.log", repo_root=tmp_path, require_exists=True, require_nonempty=True)

    assert result.exists is True
    assert result.size_bytes == expected_size
    assert result.content_hash.startswith("sha256:")
    assert result.mime_hint == "text/plain"
    assert result.status == "verified"


def test_inspect_image_artifact_rejects_text_with_png_suffix(tmp_path: Path):
    artifact = tmp_path / "reports" / "screen.png"
    artifact.parent.mkdir()
    artifact.write_text("not a png", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="supported image"):
        inspect_image_artifact("reports/screen.png", repo_root=tmp_path)


def test_inspect_image_artifact_accepts_png_signature(tmp_path: Path):
    artifact = tmp_path / "reports" / "screen.png"
    artifact.parent.mkdir()
    artifact.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    result = inspect_image_artifact("reports/screen.png", repo_root=tmp_path)

    assert result.mime_hint == "image/png"
    assert result.size_bytes > 8


def test_inspect_artifact_rejects_symlink(tmp_path: Path):
    target = tmp_path / "real.log"
    target.write_text("ok", encoding="utf-8")
    link = tmp_path / "link.log"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")

    with pytest.raises(ArtifactIntegrityError, match="regular file"):
        inspect_artifact("link.log", repo_root=tmp_path, require_exists=True)
