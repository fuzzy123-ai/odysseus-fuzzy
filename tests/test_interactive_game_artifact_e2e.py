import json
from pathlib import Path

from PIL import Image
import pytest

from core.models import ChatMessage
from routes.chat_helpers import save_assistant_response
from src.agent_tools.artifact_tools import PublishArtifactTool
from src.agent_tools.pygame_tools import VerifyPygameHeadlessTool
from src.claim_evidence_gate import evaluate_response_claims
from src.generated_artifact_publication import configure_generated_artifact_publication
from src.upload_handler import UploadHandler


class _DeterministicHeadlessTool(VerifyPygameHeadlessTool):
    async def _run_process(self, argv, *, timeout, cwd, env):
        if "import pygame; print" in " ".join(argv):
            return 0, "2.5.7", "", False
        screenshot = Path(argv[-3])
        Image.effect_noise((128, 128), 80).convert("RGB").save(screenshot, format="PNG")
        return 0, "", "", False


class _Session:
    def __init__(self):
        self.model = "test-model"
        self.history = []

    def add_message(self, message: ChatMessage):
        self.history.append(message)


@pytest.mark.asyncio
async def test_create_verify_inspect_publish_persist_and_owner_isolate(tmp_path, monkeypatch):
    game = tmp_path / "mario_game.py"
    game.write_text(
        "import pygame\npygame.init()\npygame.display.set_mode((128, 128))\npygame.display.flip()\n",
        encoding="utf-8",
    )
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))
    configure_generated_artifact_publication(handler)
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    def resolve(raw, **kwargs):
        candidate = Path(raw)
        return str(candidate if candidate.is_absolute() else tmp_path / candidate)

    monkeypatch.setattr("src.tool_execution._resolve_tool_path", resolve)
    monkeypatch.setattr(
        "src.document_processor.analyze_image_with_vl_result",
        lambda path, owner=None: {
            "text": "A colorful Pygame frame with a visible play field.",
            "model": "local-vl",
        },
    )

    headless = await _DeterministicHeadlessTool().execute(
        json.dumps({"path": "mario_game.py", "max_frames": 5, "timeout_seconds": 3}),
        {"owner": "alice"},
    )
    assert headless["headless_evidence"]["headless_verified"] is True
    assert headless["headless_evidence"]["interactive_ready"] is False

    publisher = PublishArtifactTool()
    native = await publisher.execute(
        json.dumps({"path": "mario_game.py"}),
        {"owner": "alice"},
    )
    screenshot = await publisher.execute(
        json.dumps({"path": headless["screenshot_ref"], "inspect_image": True}),
        {"owner": "alice"},
    )
    assert native["artifact_evidence"]["download_ready"]["status"] == "verified"
    assert screenshot["artifact_evidence"]["visual_inspected"]["status"] == "verified"

    attachments = [native["attachment"], screenshot["attachment"]]
    session = _Session()
    save_assistant_response(
        session,
        session_manager=None,
        session_id="chat-1",
        full_response="Headless verification passed. Visual inspection: verified. Download is ready.",
        last_metrics={"attachments": attachments},
        incognito=True,
    )
    persisted = session.history[-1].metadata["attachments"]
    assert [item["id"] for item in persisted] == [item["id"] for item in attachments]
    assert all("path" not in item and "owner" not in item for item in persisted)

    assert handler.resolve_upload(native["attachment"]["id"], owner="alice", allow_admin=False)
    assert handler.resolve_upload(native["attachment"]["id"], owner="bob", allow_admin=False) is None

    report = evaluate_response_claims(
        session.history[-1].content,
        [
            {"tool": "verify_pygame_headless", "artifact_evidence": headless["artifact_evidence"], "exit_code": 0},
            {"tool": "publish_artifact", "artifact_evidence": native["artifact_evidence"], "exit_code": 0},
            {"tool": "publish_artifact", "artifact_evidence": screenshot["artifact_evidence"], "exit_code": 0},
        ],
        repo_root=tmp_path,
    )
    assert report.ok is True
    assert {finding.claim_type for finding in report.findings} == {
        "headless_tested",
        "visual_inspected",
        "download_ready",
    }
