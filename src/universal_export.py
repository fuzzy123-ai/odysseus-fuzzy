"""Universal export intent and capability planning.

The first implementation deliberately plans exports only. It does not call
LibreOffice, ffmpeg, OCR, Blender, or other external converters.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Mapping

from src.universal_inbox_file_types import classify_universal_inbox_file


EXPORT_INTENT_SCHEMA = "odysseus.universal_export.intent.v1"
EXPORT_PLAN_SCHEMA = "odysseus.universal_export.plan.v1"

DOCUMENT_TARGETS = frozenset({"pdf", "docx", "md", "txt", "html", "csv", "xlsx"})
IMAGE_TARGETS = frozenset({"png", "jpg", "jpeg", "webp", "tiff", "bmp"})
AUDIO_TARGETS = frozenset({"mp3", "wav", "ogg", "opus", "flac", "aac", "m4a"})
VIDEO_TARGETS = frozenset({"mp4", "webm", "mov"})
ASSET_TARGETS = frozenset({"glb", "gltf", "obj", "fbx", "stl"})

KNOWN_TARGETS = DOCUMENT_TARGETS | IMAGE_TARGETS | AUDIO_TARGETS | VIDEO_TARGETS | ASSET_TARGETS

_TARGET_ALIASES = {
    "jpeg": "jpg",
    "jpg": "jpg",
    "jpegs": "jpg",
    "bilder": "png",
    "bild": "png",
    "image": "png",
    "images": "png",
    "foto": "jpg",
    "fotos": "jpg",
    "word": "docx",
    "excel": "xlsx",
    "markdown": "md",
    "wave": "wav",
    "waveform": "wav",
}

_FORMAT_RE = re.compile(
    r"(?:\.|\b)("
    + "|".join(sorted({*KNOWN_TARGETS, *_TARGET_ALIASES.keys()}, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

_EXPORT_WORD_RE = re.compile(
    r"\b(export|convert|konvertier|wandle|mach|mache|erstell|erstelle|zurueck|schick|send|speicher)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UniversalExportIntent:
    status: str
    target_format: str
    action: str
    input_ref: str
    reason: str
    review_required: bool = True
    raw_content_visible: bool = False
    schema: str = EXPORT_INTENT_SCHEMA

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "ready": self.ready,
            "target_format": self.target_format,
            "action": self.action,
            "input_ref": self.input_ref,
            "reason": self.reason,
            "review_required": self.review_required,
            "raw_content_visible": False,
        }


@dataclass(frozen=True)
class UniversalExportPlan:
    status: str
    reason: str
    source_family: str
    source_suffix: str
    target_format: str
    action: str
    required_tool: str
    local_only: bool
    review_required: bool
    reason_codes: tuple[str, ...]
    raw_content_visible: bool = False
    schema: str = EXPORT_PLAN_SCHEMA

    @property
    def executable_now(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "executable_now": self.executable_now,
            "source_family": self.source_family,
            "source_suffix": self.source_suffix,
            "target_format": self.target_format,
            "action": self.action,
            "required_tool": self.required_tool,
            "local_only": self.local_only,
            "review_required": self.review_required,
            "reason_codes": self.reason_codes,
            "raw_content_visible": False,
        }


def parse_universal_export_intent(
    text: str,
    *,
    recent_input_ref: str = "last_inbox_file",
    recent_input_available: bool = True,
) -> UniversalExportIntent:
    prompt = str(text or "").strip()
    target = _target_from_text(prompt)
    if not target:
        return UniversalExportIntent(
            status="not_export_intent",
            target_format="",
            action="",
            input_ref="",
            reason="target_format_missing",
        )
    if not _EXPORT_WORD_RE.search(prompt):
        return UniversalExportIntent(
            status="not_export_intent",
            target_format=target,
            action="convert",
            input_ref="",
            reason="export_action_missing",
        )
    if not recent_input_available:
        return UniversalExportIntent(
            status="blocked",
            target_format=target,
            action="convert",
            input_ref=recent_input_ref,
            reason="recent_input_missing",
        )
    return UniversalExportIntent(
        status="ready",
        target_format=target,
        action=_action_for_target(target),
        input_ref=recent_input_ref,
        reason="recent_input_and_target_detected",
    )


def build_universal_export_plan(
    source_filename_or_path: str | Path,
    target_format: str,
    *,
    mime_type: str | None = None,
    dsgvo_mode: bool = False,
    local_only: bool | None = None,
) -> UniversalExportPlan:
    source = classify_universal_inbox_file(source_filename_or_path, mime_type=mime_type)
    target = _normalize_target(target_format)
    if not target:
        return _blocked_plan(source, target_format="", reason="unknown_target_format")

    effective_local_only = bool(dsgvo_mode or local_only)
    capability = _capability_for(source.family, source.suffix, target)
    if capability is None:
        return UniversalExportPlan(
            status="blocked",
            reason="conversion_not_supported",
            source_family=source.family,
            source_suffix=source.suffix,
            target_format=target,
            action="convert",
            required_tool="",
            local_only=effective_local_only,
            review_required=True,
            reason_codes=("conversion_not_supported",),
        )

    action, tool, reason_codes = capability
    return UniversalExportPlan(
        status="planned",
        reason="converter_tool_required",
        source_family=source.family,
        source_suffix=source.suffix,
        target_format=target,
        action=action,
        required_tool=tool,
        local_only=effective_local_only,
        review_required=True,
        reason_codes=tuple(reason_codes),
    )


def build_universal_export_plan_from_intent(
    source_filename_or_path: str | Path,
    intent: UniversalExportIntent | Mapping[str, Any],
    *,
    mime_type: str | None = None,
    dsgvo_mode: bool = False,
) -> UniversalExportPlan:
    payload = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)
    if str(payload.get("status") or "") != "ready":
        source = classify_universal_inbox_file(source_filename_or_path, mime_type=mime_type)
        return UniversalExportPlan(
            status="blocked",
            reason=str(payload.get("reason") or "intent_not_ready"),
            source_family=source.family,
            source_suffix=source.suffix,
            target_format=str(payload.get("target_format") or ""),
            action=str(payload.get("action") or "convert"),
            required_tool="",
            local_only=bool(dsgvo_mode),
            review_required=True,
            reason_codes=("intent_not_ready",),
        )
    return build_universal_export_plan(
        source_filename_or_path,
        str(payload.get("target_format") or ""),
        mime_type=mime_type,
        dsgvo_mode=dsgvo_mode,
    )


def _capability_for(family: str, suffix: str, target: str) -> tuple[str, str, tuple[str, ...]] | None:
    if target in DOCUMENT_TARGETS:
        if target == "pdf" and family in {"document", "text", "message"}:
            return ("convert", "libreoffice_or_pandoc", ("tool_check_required", "no_original_overwrite"))
        if target in {"txt", "md", "html"} and family in {"document", "text", "message"}:
            return ("extract_or_convert", "local_extractor_or_pandoc", ("tool_check_required", "no_original_overwrite"))
        if target in {"csv", "xlsx"} and suffix in {".csv", ".tsv", ".xlsx", ".xls", ".ods"}:
            return ("table_convert", "libreoffice_or_openpyxl", ("tool_check_required", "table_review_required"))

    if target in IMAGE_TARGETS:
        if family == "image":
            return ("image_convert", "pillow", ("tool_check_required", "no_original_overwrite"))
        if suffix == ".pdf":
            return ("render_pages", "poppler_or_mupdf", ("tool_check_required", "page_render_review_required"))
        if family == "asset":
            return ("render_preview", "blender_or_godot", ("tool_check_required", "asset_preview_review_required"))

    if target in AUDIO_TARGETS and family == "audio":
        return ("audio_transcode", "ffmpeg", ("tool_check_required", "no_original_overwrite"))

    if target in VIDEO_TARGETS and family in {"video", "image"}:
        return ("video_transcode", "ffmpeg", ("tool_check_required", "no_original_overwrite"))

    if target in ASSET_TARGETS and family == "asset":
        return ("asset_convert", "blender_or_assimp", ("tool_check_required", "asset_conversion_review_required"))

    return None


def _blocked_plan(source: Any, *, target_format: str, reason: str) -> UniversalExportPlan:
    return UniversalExportPlan(
        status="blocked",
        reason=reason,
        source_family=source.family,
        source_suffix=source.suffix,
        target_format=target_format,
        action="convert",
        required_tool="",
        local_only=True,
        review_required=True,
        reason_codes=(reason,),
    )


def _target_from_text(text: str) -> str:
    match = _FORMAT_RE.search(text or "")
    if not match:
        return ""
    return _normalize_target(match.group(1))


def _normalize_target(value: str) -> str:
    target = str(value or "").strip().lower().lstrip(".")
    target = _TARGET_ALIASES.get(target, target)
    return target if target in KNOWN_TARGETS else ""


def _action_for_target(target: str) -> str:
    if target in IMAGE_TARGETS:
        return "image_export"
    if target in AUDIO_TARGETS:
        return "audio_export"
    if target in ASSET_TARGETS:
        return "asset_export"
    if target in VIDEO_TARGETS:
        return "video_export"
    return "document_export"
