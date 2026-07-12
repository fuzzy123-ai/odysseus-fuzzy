"""Bounded dummy-SDL verification for generated Pygame programs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from src.pygame_headless_contract import (
    PygameHeadlessContractError,
    build_pygame_headless_plan,
    evaluate_pygame_headless_evidence,
)


_MAX_SOURCE_BYTES = 2_000_000
_MAX_CAPTURE_OUTPUT = 3_000
_SECRET_ENV_RE = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|COOKIE|AUTH)", re.IGNORECASE)

_CAPTURE_HARNESS = r'''
import runpy
import socket
import sys

import pygame

source_path, screenshot_path = sys.argv[1], sys.argv[2]
max_frames, capture_frame = int(sys.argv[3]), int(sys.argv[4])

def _network_blocked(*args, **kwargs):
    raise RuntimeError("network disabled during pygame headless verification")

socket.socket = _network_blocked
socket.create_connection = _network_blocked

class _BoundedStop(BaseException):
    pass

state = {"frames": 0, "captured": False}

def _after_present():
    state["frames"] += 1
    surface = pygame.display.get_surface()
    if state["frames"] >= capture_frame and not state["captured"] and surface is not None:
        pygame.image.save(surface, screenshot_path)
        state["captured"] = True
    if state["frames"] >= max_frames:
        raise _BoundedStop()

_real_flip = pygame.display.flip
_real_update = pygame.display.update

def _flip(*args, **kwargs):
    result = _real_flip(*args, **kwargs)
    _after_present()
    return result

def _update(*args, **kwargs):
    result = _real_update(*args, **kwargs)
    _after_present()
    return result

pygame.display.flip = _flip
pygame.display.update = _update

try:
    runpy.run_path(source_path, run_name="__main__")
except _BoundedStop:
    pass
finally:
    pygame.quit()
'''


class VerifyPygameHeadlessTool:
    """Verify syntax/import/frame presentation without claiming interactivity."""

    async def execute(self, content: str, ctx: dict) -> dict[str, Any]:
        try:
            args = json.loads((content or "").strip() or "{}")
        except json.JSONDecodeError:
            return self._error("arguments must be a JSON object")
        if not isinstance(args, dict):
            return self._error("arguments must be a JSON object")

        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return self._error("path is required")
        max_frames = args.get("max_frames", 120)
        timeout_seconds = args.get("timeout_seconds", 10)
        capture_frame = args.get("capture_frame", 1)

        try:
            from src.tool_execution import _resolve_tool_path, agent_cwd

            workspace = Path(agent_cwd()).resolve(strict=True)
            source = Path(
                _resolve_tool_path(
                    raw_path,
                    owner=ctx.get("owner"),
                    tool="verify_pygame_headless",
                    mode="read",
                )
            ).resolve(strict=True)
            source_ref = source.relative_to(workspace).as_posix()
            screenshot_ref = str(
                args.get("screenshot_path")
                or f"artifacts/pygame/{source.stem}-headless.png"
            ).strip().replace("\\", "/")
            plan = build_pygame_headless_plan(
                source_ref=source_ref,
                screenshot_ref=screenshot_ref,
                max_frames=max_frames,
                timeout_seconds=timeout_seconds,
                screenshot_frame=capture_frame,
            )
            screenshot = Path(
                _resolve_tool_path(
                    screenshot_ref,
                    owner=ctx.get("owner"),
                    tool="verify_pygame_headless",
                    mode="write",
                    content_size=0,
                )
            ).resolve(strict=False)
            screenshot.relative_to(workspace)
        except (OSError, ValueError, PygameHeadlessContractError) as exc:
            return self._error(str(exc))

        if source.stat().st_size <= 0 or source.stat().st_size > _MAX_SOURCE_BYTES:
            return self._error("source file is empty or exceeds the headless verification limit")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        try:
            screenshot.unlink(missing_ok=True)
        except OSError:
            return self._error("previous screenshot artifact could not be replaced")

        syntax_ok = False
        try:
            source_text = await asyncio.to_thread(source.read_text, encoding="utf-8")
            compile(source_text, source_ref, "exec")
            syntax_ok = True
        except (OSError, UnicodeError, SyntaxError) as exc:
            return self._result(
                plan,
                syntax_ok=False,
                import_ok=False,
                run_ok=False,
                screenshot_ok=False,
                detail=f"Syntax check failed: {type(exc).__name__}",
            )

        env = self._headless_environment()
        import_result = await self._run_process(
            [sys.executable, "-P", "-c", "import pygame; print(pygame.version.ver)"],
            timeout=min(int(plan.limits.timeout_seconds), 15),
            cwd=workspace,
            env=env,
        )
        import_ok = import_result[0] == 0 and not import_result[3]
        if not import_ok:
            return self._result(
                plan,
                syntax_ok=syntax_ok,
                import_ok=False,
                run_ok=False,
                screenshot_ok=False,
                detail="Pygame import probe failed: " + self._safe_process_detail(import_result, workspace),
            )

        run_result = await self._run_process(
            [
                sys.executable,
                "-P",
                "-c",
                _CAPTURE_HARNESS,
                str(source),
                str(screenshot),
                str(plan.limits.max_frames),
                str(plan.limits.screenshot_frame),
            ],
            timeout=plan.limits.timeout_seconds,
            cwd=workspace,
            env=env,
        )
        run_ok = run_result[0] == 0 and not run_result[3]
        screenshot_ok, digest, size = await asyncio.to_thread(self._verify_png, screenshot, plan.screenshot.max_bytes)
        detail = self._safe_process_detail(run_result, workspace)
        return self._result(
            plan,
            syntax_ok=syntax_ok,
            import_ok=import_ok,
            run_ok=run_ok,
            screenshot_ok=screenshot_ok,
            screenshot_hash=digest,
            screenshot_size=size,
            detail=detail,
        )

    async def _run_process(
        self,
        argv: list[str],
        *,
        timeout: int,
        cwd: Path,
        env: dict[str, str],
    ) -> tuple[int, str, str, bool]:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            stdout, stderr = await process.communicate()
        return (
            int(process.returncode if process.returncode is not None else 1),
            stdout.decode("utf-8", errors="replace")[:_MAX_CAPTURE_OUTPUT],
            stderr.decode("utf-8", errors="replace")[:_MAX_CAPTURE_OUTPUT],
            timed_out,
        )

    @staticmethod
    def _headless_environment() -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not _SECRET_ENV_RE.search(key)
        }
        env.update(
            {
                "SDL_VIDEODRIVER": "dummy",
                "SDL_AUDIODRIVER": "dummy",
                "PYGAME_HIDE_SUPPORT_PROMPT": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return env

    @staticmethod
    def _verify_png(path: Path, max_bytes: int) -> tuple[bool, str, int]:
        try:
            size = path.stat().st_size
            if size < 1_024 or size > max_bytes:
                return False, "", size
            from PIL import Image

            with Image.open(path) as image:
                image.verify()
                if image.format != "PNG" or image.width <= 0 or image.height <= 0:
                    return False, "", size
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return True, digest, size
        except (OSError, ValueError):
            return False, "", 0

    @staticmethod
    def _safe_process_detail(result: tuple[int, str, str, bool], workspace: Path) -> str:
        rc, stdout, stderr, timed_out = result
        text = (stderr or stdout or "no process output").strip()
        text = text.replace(str(workspace), "<workspace>")
        text = text.replace(str(workspace).replace("\\", "/"), "<workspace>")
        if timed_out:
            return "timed out within the bounded verification window"
        return f"exit {rc}: {text[:500]}"

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"error": f"verify_pygame_headless: {message}", "exit_code": 1}

    @staticmethod
    def _result(
        plan,
        *,
        syntax_ok: bool,
        import_ok: bool,
        run_ok: bool,
        screenshot_ok: bool,
        screenshot_hash: str = "",
        screenshot_size: int = 0,
        detail: str = "",
    ) -> dict[str, Any]:
        status = evaluate_pygame_headless_evidence(
            syntax_check_passed=syntax_ok,
            pygame_import_probe_passed=import_ok,
            bounded_frame_run_passed=run_ok,
            screenshot_artifact_recorded=screenshot_ok,
        ).to_redacted_dict()
        verified = bool(status["headless_verified"])
        evidence = {
            "schema": "odysseus.interactive_artifact_claims.v1",
            "syntax_verified": {"status": "verified" if syntax_ok else "not_verified"},
            "headless_tested": {"status": "verified" if verified else "not_verified"},
            "visual_inspected": {"status": "not_verified"},
            "download_ready": {"status": "not_verified"},
            "interactive_preview_ready": {"status": "not_verified"},
        }
        if screenshot_hash:
            evidence["screenshot"] = {
                "ref": plan.screenshot.ref,
                "hash": screenshot_hash,
                "size": screenshot_size,
                "status": "verified" if screenshot_ok else "not_verified",
            }
        if verified:
            output = (
                f"Pygame headless verification passed. Screenshot: {plan.screenshot.ref} "
                f"(SHA-256 {screenshot_hash}). This is not an interactive browser preview. "
                "Publish the .py file with publish_artifact; publish the PNG with inspect_image=true before any visual-quality claim."
            )
        else:
            output = (
                "Pygame headless verification failed or is incomplete. "
                f"Missing evidence: {', '.join(status['missing_evidence']) or 'unknown'}. "
                f"Detail: {detail or 'no detail'}"
            )
        return {
            "output": output,
            "exit_code": 0 if verified else 1,
            "pygame_headless_plan": plan.to_redacted_dict(),
            "headless_evidence": status,
            "artifact_evidence": evidence,
            "screenshot_ref": plan.screenshot.ref if screenshot_ok else "",
        }
