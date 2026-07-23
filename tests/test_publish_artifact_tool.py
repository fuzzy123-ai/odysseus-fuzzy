import json
from pathlib import Path

import pytest
from PIL import Image

from src.agent_tools.artifact_tools import PublishArtifactTool
from src.generated_artifact_publication import configure_generated_artifact_publication
from src.upload_handler import UploadHandler


@pytest.fixture
def configured_handler(tmp_path):
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))
    configure_generated_artifact_publication(handler)
    return handler


def _write_png(path: Path) -> None:
    Image.new("RGB", (2, 2), (20, 180, 60)).save(path, format="PNG")


@pytest.mark.asyncio
async def test_publish_tool_returns_public_attachment_and_download_evidence(tmp_path, configured_handler, monkeypatch):
    source = tmp_path / "game.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    monkeypatch.setattr("src.tool_execution._resolve_tool_path", lambda *args, **kwargs: str(source))

    result = await PublishArtifactTool().execute(
        json.dumps({"path": "game.py", "name": "mario.py"}),
        {"owner": "alice"},
    )

    assert result["exit_code"] == 0
    assert result["attachment"]["name"] == "mario.py"
    assert result["attachment"]["download_ready"] is True
    assert "path" not in result["attachment"]
    assert result["artifact_evidence"]["download_ready"]["status"] == "verified"
    assert result["artifact_evidence"]["visual_inspected"]["status"] == "not_requested"


@pytest.mark.asyncio
async def test_publish_tool_requires_owner_path_and_boolean_inspection(tmp_path, configured_handler):
    tool = PublishArtifactTool()
    assert (await tool.execute("{}", {"owner": "alice"}))["exit_code"] == 1
    assert (await tool.execute(json.dumps({"path": "x.py"}), {}))["exit_code"] == 1
    assert (await tool.execute(json.dumps({"path": "x.py", "inspect_image": "yes"}), {"owner": "alice"}))["exit_code"] == 1


@pytest.mark.asyncio
async def test_image_inspection_is_hash_bound_and_cached(tmp_path, configured_handler, monkeypatch):
    source = tmp_path / "frame.png"
    _write_png(source)
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    monkeypatch.setattr("src.tool_execution._resolve_tool_path", lambda *args, **kwargs: str(source))
    monkeypatch.setattr(
        "src.document_processor.analyze_image_with_vl_result",
        lambda path, owner=None: {"text": "A single green game frame.", "model": "local-vl"},
    )

    result = await PublishArtifactTool().execute(
        json.dumps({"path": "frame.png", "inspect_image": True}),
        {"owner": "alice"},
    )

    visual = result["artifact_evidence"]["visual_inspected"]
    assert visual["status"] == "verified"
    assert visual["artifact_id"] == result["attachment"]["id"]
    assert visual["artifact_hash"] == result["attachment"]["hash"]
    assert result["attachment"]["vision_model"] == "local-vl"
    cache = Path(configured_handler.upload_dir) / ".vision" / (result["attachment"]["id"] + ".txt")
    assert cache.read_text(encoding="utf-8") == "A single green game frame."


@pytest.mark.asyncio
async def test_unavailable_vision_does_not_become_visual_evidence(tmp_path, configured_handler, monkeypatch):
    source = tmp_path / "frame.png"
    _write_png(source)
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    monkeypatch.setattr("src.tool_execution._resolve_tool_path", lambda *args, **kwargs: str(source))
    monkeypatch.setattr(
        "src.document_processor.analyze_image_with_vl_result",
        lambda path, owner=None: {"text": "[No vision model configured — set one]", "model": ""},
    )

    result = await PublishArtifactTool().execute(
        json.dumps({"path": "frame.png", "inspect_image": True}),
        {"owner": "alice"},
    )

    assert result["exit_code"] == 0
    assert result["artifact_evidence"]["visual_inspected"]["status"] == "unavailable"
    assert "do not claim" in result["output"]
