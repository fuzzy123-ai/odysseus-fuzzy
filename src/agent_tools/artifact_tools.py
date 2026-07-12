"""Agent tool for publishing generated deliverables as chat attachments."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.generated_artifact_publication import (
    GeneratedArtifactPublicationError,
    get_generated_artifact_upload_handler,
    publish_generated_artifact,
)


_VISION_FAILURE_PREFIXES = (
    "[No vision model configured",
    "[Vision is disabled",
    "[Vision analysis blocked",
    "[VL model unavailable",
)


class PublishArtifactTool:
    """Copy a workspace file into the existing owner-protected upload store."""

    async def execute(self, content: str, ctx: dict) -> dict[str, Any]:
        try:
            args = json.loads((content or "").strip() or "{}")
        except json.JSONDecodeError:
            return {"error": "publish_artifact: arguments must be a JSON object", "exit_code": 1}
        if not isinstance(args, dict):
            return {"error": "publish_artifact: arguments must be a JSON object", "exit_code": 1}

        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return {"error": "publish_artifact: path is required", "exit_code": 1}
        display_name = str(args.get("name") or "").strip() or None
        inspect_image = args.get("inspect_image", False)
        if not isinstance(inspect_image, bool):
            return {"error": "publish_artifact: inspect_image must be a boolean", "exit_code": 1}

        owner = str(ctx.get("owner") or "").strip()
        if not owner:
            return {"error": "publish_artifact: authenticated owner is required", "exit_code": 1}

        try:
            from src.tool_execution import _resolve_tool_path, agent_cwd

            source_path = _resolve_tool_path(
                raw_path,
                owner=owner,
                tool="publish_artifact",
                mode="read",
            )
            workspace_root = agent_cwd()
            handler = get_generated_artifact_upload_handler()
            attachment = await asyncio.to_thread(
                publish_generated_artifact,
                source_path,
                owner=owner,
                allowed_root=workspace_root,
                display_name=display_name,
                upload_handler=handler,
            )
        except (GeneratedArtifactPublicationError, ValueError) as exc:
            return {"error": f"publish_artifact: {exc}", "exit_code": 1}
        except Exception:
            return {"error": "publish_artifact: publication failed", "exit_code": 1}

        evidence: dict[str, Any] = {
            "schema": "odysseus.generated_artifact_evidence.v1",
            "artifact_id": attachment["id"],
            "artifact_hash": attachment["hash"],
            "download_ready": {"status": "verified"},
            "visual_inspected": {"status": "not_requested"},
            "interactive_preview_ready": {"status": "not_verified"},
        }
        output_lines = [
            f"Published {attachment['name']} as an owner-scoped chat attachment.",
            "Download readiness: verified.",
        ]

        if inspect_image:
            if not str(attachment.get("mime") or "").startswith("image/"):
                evidence["visual_inspected"] = {"status": "not_applicable", "reason": "artifact_is_not_an_image"}
                output_lines.append("Visual inspection: not applicable (artifact is not an image).")
            else:
                visual = await self._inspect_image(handler, attachment, owner)
                if visual["status"] == "verified":
                    description = visual.pop("description")
                    evidence["visual_inspected"] = visual
                    attachment["vision_model"] = visual["model"]
                    output_lines.append(
                        f"Visual inspection: verified with {visual['model']} against SHA-256 {attachment['hash']}."
                    )
                    output_lines.append("Vision result: " + description)
                else:
                    evidence["visual_inspected"] = visual
                    output_lines.append("Visual inspection: unavailable; do not claim the image was visually inspected.")

        return {
            "output": "\n".join(output_lines),
            "exit_code": 0,
            "attachment": attachment,
            "artifact_evidence": evidence,
        }

    async def _inspect_image(self, handler: Any, attachment: dict[str, Any], owner: str) -> dict[str, Any]:
        resolved = handler.resolve_upload(
            attachment["id"],
            owner=owner,
            allow_admin=False,
        )
        if not resolved:
            return {"status": "unavailable", "reason": "owner_scoped_artifact_not_resolved"}

        try:
            from src.document_processor import analyze_image_with_vl_result

            result = await asyncio.to_thread(analyze_image_with_vl_result, resolved["path"], owner=owner)
        except Exception:
            return {"status": "unavailable", "reason": "vision_provider_failed"}

        description = str((result or {}).get("text") or "").strip()
        model = str((result or {}).get("model") or "").strip()
        blocked = bool((result or {}).get("blocked_by_policy"))
        failed_text = not description or any(description.startswith(prefix) for prefix in _VISION_FAILURE_PREFIXES)
        if blocked or failed_text or not model:
            return {
                "status": "unavailable",
                "reason": "vision_provider_unavailable_or_blocked",
            }

        # Keep the existing caption editor/cache working for generated images.
        try:
            cache_dir = os.path.join(handler.upload_dir, ".vision")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, attachment["id"] + ".txt")
            with open(cache_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(description)
        except OSError:
            pass

        return {
            "status": "verified",
            "model": model,
            "description": description,
            "artifact_id": attachment["id"],
            "artifact_hash": attachment["hash"],
        }
