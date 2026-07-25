import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image
import pytest

pygame_tools = importlib.import_module("src.agent_tools.pygame_tools")
VerifyPygameHeadlessTool = pygame_tools.VerifyPygameHeadlessTool


class _SuccessfulTool(VerifyPygameHeadlessTool):
    async def _run_process(self, argv, *, timeout, cwd, env):
        if "import pygame; print" in " ".join(argv):
            return 0, "2.5.7", "", False
        screenshot = Path(argv[-3])
        Image.effect_noise((128, 128), 80).convert("RGB").save(screenshot, format="PNG")
        return 0, "", "", False


class _TimedOutTool(VerifyPygameHeadlessTool):
    async def _run_process(self, argv, *, timeout, cwd, env):
        if "import pygame; print" in " ".join(argv):
            return 0, "2.5.7", "", False
        return 124, "", "hung", True


def _patch_paths(monkeypatch, workspace: Path, source: Path):
    tool_execution = importlib.import_module("src.tool_execution")
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(workspace))

    def resolve(raw, **kwargs):
        candidate = Path(raw)
        return str(candidate if candidate.is_absolute() else workspace / candidate)

    monkeypatch.setattr(tool_execution, "_resolve_tool_path", resolve)


@pytest.mark.asyncio
async def test_success_is_bounded_headless_evidence_never_interactive(tmp_path, monkeypatch):
    source = tmp_path / "mario_game.py"
    source.write_text("import pygame\npygame.display.set_mode((64, 64))\npygame.display.flip()\n", encoding="utf-8")
    _patch_paths(monkeypatch, tmp_path, source)

    result = await _SuccessfulTool().execute(
        json.dumps({"path": "mario_game.py", "max_frames": 5, "timeout_seconds": 3}),
        {"owner": "alice"},
    )

    assert result["exit_code"] == 0
    assert result["headless_evidence"]["headless_verified"] is True
    assert result["headless_evidence"]["interactive_ready"] is False
    assert result["artifact_evidence"]["headless_tested"]["status"] == "verified"
    assert result["artifact_evidence"]["visual_inspected"]["status"] == "not_verified"
    assert result["artifact_evidence"]["download_ready"]["status"] == "not_verified"
    assert result["screenshot_ref"] == "artifacts/pygame/mario_game-headless.png"
    assert (tmp_path / result["screenshot_ref"]).is_file()


@pytest.mark.asyncio
async def test_timeout_cannot_claim_headless_success(tmp_path, monkeypatch):
    source = tmp_path / "game.py"
    source.write_text("while True:\n    pass\n", encoding="utf-8")
    _patch_paths(monkeypatch, tmp_path, source)

    result = await _TimedOutTool().execute(
        json.dumps({"path": "game.py", "timeout_seconds": 1}),
        {"owner": "alice"},
    )

    assert result["exit_code"] == 1
    assert result["headless_evidence"]["headless_verified"] is False
    assert "bounded_frame_run_passed" in result["headless_evidence"]["missing_evidence"]
    assert result["artifact_evidence"]["interactive_preview_ready"]["status"] == "not_verified"


@pytest.mark.asyncio
async def test_syntax_error_stops_before_runtime(tmp_path, monkeypatch):
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    _patch_paths(monkeypatch, tmp_path, source)

    result = await _SuccessfulTool().execute(json.dumps({"path": "broken.py"}), {"owner": "alice"})

    assert result["exit_code"] == 1
    assert result["artifact_evidence"]["syntax_verified"]["status"] == "not_verified"
    assert result["screenshot_ref"] == ""


@pytest.mark.asyncio
async def test_unsafe_screenshot_ref_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "game.py"
    source.write_text("pass\n", encoding="utf-8")
    _patch_paths(monkeypatch, tmp_path, source)

    result = await _SuccessfulTool().execute(
        json.dumps({"path": "game.py", "screenshot_path": "../escape.png"}),
        {"owner": "alice"},
    )

    assert result["exit_code"] == 1
    assert "safe repo-relative" in result["error"] or "unsafe path" in result["error"]


def test_subprocess_environment_drops_secret_variables(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("SAFE_SETTING", "ok")
    env = VerifyPygameHeadlessTool._headless_environment()

    assert "OPENAI_API_KEY" not in env
    assert env["SAFE_SETTING"] == "ok"
    assert env["SDL_VIDEODRIVER"] == "dummy"


@pytest.mark.asyncio
async def test_real_pygame_dummy_sdl_smoke_when_dependency_is_available(tmp_path, monkeypatch):
    candidates = [sys.executable, shutil.which("python"), shutil.which("python3")]
    pygame_python = None
    for candidate in dict.fromkeys(item for item in candidates if item):
        probe = subprocess.run(
            [candidate, "-P", "-c", "import pygame"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if probe.returncode == 0:
            pygame_python = candidate
            break
    if not pygame_python:
        pytest.skip("no local Python interpreter with pygame is available")
    monkeypatch.setattr(pygame_tools.sys, "executable", pygame_python)
    source = tmp_path / "smoke_game.py"
    source.write_text(
        """import pygame
pygame.init()
screen = pygame.display.set_mode((320, 240))
for y in range(0, 240, 3):
    pygame.draw.line(screen, ((y * 5) % 255, (y * 7) % 255, (y * 11) % 255), (0, y), (319, y), 3)
for x in range(0, 320, 5):
    pygame.draw.circle(screen, ((x * 3) % 255, 180, 60), (x, (x * 7) % 240), 4)
pygame.display.flip()
pygame.quit()
""",
        encoding="utf-8",
    )
    _patch_paths(monkeypatch, tmp_path, source)

    result = await VerifyPygameHeadlessTool().execute(
        json.dumps({"path": "smoke_game.py", "max_frames": 5, "timeout_seconds": 8}),
        {"owner": "alice"},
    )

    assert result["exit_code"] == 0, result.get("output") or result.get("error")
    assert result["headless_evidence"]["headless_verified"] is True
    assert result["headless_evidence"]["interactive_ready"] is False
