from pathlib import Path

import pytest
from PIL import Image

from src.generated_artifact_publication import (
    GENERATED_ARTIFACT_SCHEMA,
    GeneratedArtifactPublicationError,
    publish_generated_artifact,
)
from src.upload_handler import UploadHandler


def _handler(tmp_path: Path) -> UploadHandler:
    return UploadHandler(str(tmp_path), str(tmp_path / "uploads"))


def test_generated_file_is_copied_to_owner_scoped_upload_without_source_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "mario_game.py"
    source.write_text("print('mario')\n", encoding="utf-8")
    handler = _handler(tmp_path)

    attachment = publish_generated_artifact(
        source,
        owner="alice",
        allowed_root=workspace,
        upload_handler=handler,
    )

    assert attachment["schema"] == GENERATED_ARTIFACT_SCHEMA
    assert attachment["name"] == "mario_game.py"
    assert attachment["mime"] in {"text/x-python", "text/plain"}
    assert attachment["download_ready"] is True
    assert "path" not in attachment
    assert "owner" not in attachment
    resolved = handler.resolve_upload(attachment["id"], owner="alice", allow_admin=False)
    assert resolved is not None
    assert Path(resolved["path"]).read_text(encoding="utf-8") == "print('mario')\n"
    assert handler.resolve_upload(attachment["id"], owner="bob", allow_admin=False) is None


def test_publication_deduplicates_for_same_owner_but_not_different_owner(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "game.html"
    source.write_text("<!doctype html><title>Game</title>", encoding="utf-8")
    handler = _handler(tmp_path)

    first = publish_generated_artifact(source, owner="alice", allowed_root=workspace, upload_handler=handler)
    second = publish_generated_artifact(source, owner="alice", allowed_root=workspace, upload_handler=handler)
    other = publish_generated_artifact(source, owner="bob", allowed_root=workspace, upload_handler=handler)

    assert second["id"] == first["id"]
    assert other["id"] != first["id"]


@pytest.mark.parametrize("owner", ["", "  ", None])
def test_publication_requires_owner(tmp_path, owner):
    source = tmp_path / "game.py"
    source.write_text("pass\n", encoding="utf-8")
    with pytest.raises(GeneratedArtifactPublicationError, match="owner"):
        publish_generated_artifact(source, owner=owner, allowed_root=tmp_path, upload_handler=_handler(tmp_path))


def test_publication_rejects_escape_symlink_directory_empty_and_unsafe_extension(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    handler = _handler(tmp_path)

    with pytest.raises(GeneratedArtifactPublicationError, match="outside|escapes"):
        publish_generated_artifact(outside, owner="alice", allowed_root=workspace, upload_handler=handler)
    with pytest.raises(GeneratedArtifactPublicationError, match="regular file"):
        publish_generated_artifact(workspace, owner="alice", allowed_root=workspace, upload_handler=handler)

    empty = workspace / "empty.py"
    empty.touch()
    with pytest.raises(GeneratedArtifactPublicationError, match="empty"):
        publish_generated_artifact(empty, owner="alice", allowed_root=workspace, upload_handler=handler)

    unsafe = workspace / "game.exe"
    unsafe.write_bytes(b"MZ")
    with pytest.raises(GeneratedArtifactPublicationError, match="not publishable"):
        publish_generated_artifact(unsafe, owner="alice", allowed_root=workspace, upload_handler=handler)

    link = workspace / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(GeneratedArtifactPublicationError, match="outside|symlink"):
        publish_generated_artifact(link, owner="alice", allowed_root=workspace, upload_handler=handler)


def test_display_name_is_sanitized_and_must_keep_extension(tmp_path):
    source = tmp_path / "game.py"
    source.write_text("pass\n", encoding="utf-8")
    handler = _handler(tmp_path)

    attachment = publish_generated_artifact(
        source,
        owner="alice",
        allowed_root=tmp_path,
        display_name="My Mario Game.py",
        upload_handler=handler,
    )
    assert attachment["name"] == "My_Mario_Game.py"

    with pytest.raises(GeneratedArtifactPublicationError, match="extension"):
        publish_generated_artifact(
            source,
            owner="alice",
            allowed_root=tmp_path,
            display_name="game.txt",
            upload_handler=handler,
        )


def test_fake_png_oversize_and_failed_index_write_fail_closed(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handler = _handler(tmp_path)

    fake_png = workspace / "fake.png"
    fake_png.write_bytes(b"not a png")
    with pytest.raises(GeneratedArtifactPublicationError, match="PNG"):
        publish_generated_artifact(fake_png, owner="alice", allowed_root=workspace, upload_handler=handler)

    real_png = workspace / "real.png"
    Image.new("RGB", (2, 2), (0, 120, 220)).save(real_png, format="PNG")
    handler.max_upload_size = 10
    with pytest.raises(GeneratedArtifactPublicationError, match="size limit"):
        publish_generated_artifact(real_png, owner="alice", allowed_root=workspace, upload_handler=handler)

    handler.max_upload_size = 1024
    source = workspace / "rollback.py"
    source.write_text("print('rollback')\n", encoding="utf-8")
    monkeypatch.setattr(handler, "_atomic_write_json", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(GeneratedArtifactPublicationError):
        publish_generated_artifact(source, owner="alice", allowed_root=workspace, upload_handler=handler)
    assert not list((tmp_path / "uploads").rglob("*.py"))
