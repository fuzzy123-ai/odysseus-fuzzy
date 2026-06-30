"""Safe planning contracts for Universal File IO export workflows.

The module does not convert files, read private document content, call
Telegram, touch Nextcloud, or execute external tools. It only turns a recent
Inbox source reference and a follow-up request into a redacted export plan.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,140}$")
_TARGET_ALIASES: dict[str, str] = {
    "pdf": "pdf",
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "bild": "png",
    "image": "png",
    "page image": "png",
    "seitenbild": "png",
    "mp3": "mp3",
    "wav": "wav",
    "audio": "mp3",
    "glb": "glb",
    "gltf": "gltf",
    "fbx": "fbx",
    "obj": "obj",
    "stl": "stl",
    "thumbnail": "png",
}


class UniversalFileIOError(ValueError):
    """Raised when an export intent or plan would be unsafe."""


@dataclass(frozen=True, slots=True)
class FileCapability:
    extension: str
    family: str
    label: str
    extraction: str
    export_targets: tuple[str, ...]
    default_tool: str
    review_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "extension": self.extension,
            "family": self.family,
            "label": self.label,
            "extraction": self.extraction,
            "export_targets": self.export_targets,
            "default_tool": self.default_tool,
            "review_required": self.review_required,
        }


@dataclass(frozen=True, slots=True)
class ExportIntent:
    source_ref: str
    request_hash: str
    target_format: str
    delivery_hint: str
    dsgvo_mode: bool
    local_only: bool

    @property
    def supported(self) -> bool:
        return bool(self.target_format)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "request_hash": self.request_hash,
            "target_format": self.target_format,
            "delivery_hint": self.delivery_hint,
            "dsgvo_mode": self.dsgvo_mode,
            "local_only": self.local_only,
            "supported": self.supported,
            "raw_request_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ExportPlan:
    status: str
    source_ref: str
    source_family: str
    source_extension: str
    target_format: str
    required_tool: str
    output_ref: str
    local_only: bool
    live_execution_allowed: bool
    delivery_allowed: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    steps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_ref": self.source_ref,
            "source_family": self.source_family,
            "source_extension": self.source_extension,
            "target_format": self.target_format,
            "required_tool": self.required_tool,
            "output_ref": self.output_ref,
            "local_only": self.local_only,
            "live_execution_allowed": self.live_execution_allowed,
            "delivery_allowed": self.delivery_allowed,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "steps": self.steps,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class TelegramDeliveryPlan:
    status: str
    method: str
    target_format: str
    mime_type: str
    delivery_ready: bool
    send_allowed: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    raw_content_visible: bool = False
    host_paths_visible: bool = False
    filename_visible: bool = False
    token_value_visible: bool = False
    chat_id_value_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "method": self.method,
            "target_format": self.target_format,
            "mime_type": self.mime_type,
            "delivery_ready": self.delivery_ready,
            "send_allowed": self.send_allowed,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "raw_content_visible": False,
            "host_paths_visible": False,
            "filename_visible": False,
            "token_value_visible": False,
            "chat_id_value_visible": False,
        }


def build_file_capability_registry() -> dict[str, FileCapability]:
    """Return supported source formats keyed by lowercase extension."""

    items = [
        # Documents
        (".docx", "document", "Word document", "office_text", ("pdf", "txt"), "libreoffice_or_markitdown", True),
        (".doc", "document", "Legacy Word document", "office_text_best_effort", ("pdf", "txt"), "libreoffice", True),
        (".odt", "document", "OpenDocument text", "office_text", ("pdf", "txt"), "libreoffice", True),
        (".rtf", "document", "Rich text", "text", ("pdf", "txt"), "pandoc_or_libreoffice", False),
        (".txt", "document", "Plain text", "text", ("pdf", "md"), "pandoc_or_weasyprint", False),
        (".md", "document", "Markdown", "markdown", ("pdf", "html"), "pandoc_or_weasyprint", False),
        (".markdown", "document", "Markdown", "markdown", ("pdf", "html"), "pandoc_or_weasyprint", False),
        (".html", "document", "HTML", "html_text", ("pdf", "png"), "weasyprint_or_browser", True),
        (".htm", "document", "HTML", "html_text", ("pdf", "png"), "weasyprint_or_browser", True),
        (".pdf", "pdf", "PDF", "pdf_text_or_metadata", ("txt", "png", "jpg", "searchable_pdf"), "pypdf_or_ocr_pipeline", True),
        (".xlsx", "spreadsheet", "Excel workbook", "table_profile", ("csv", "pdf"), "libreoffice_or_markitdown", True),
        (".xls", "spreadsheet", "Legacy Excel workbook", "table_profile", ("csv", "pdf"), "libreoffice", True),
        (".ods", "spreadsheet", "OpenDocument spreadsheet", "table_profile", ("csv", "pdf"), "libreoffice", True),
        (".csv", "spreadsheet", "CSV", "table_profile", ("xlsx", "pdf"), "table_converter", False),
        (".tsv", "spreadsheet", "TSV", "table_profile", ("csv", "pdf"), "table_converter", False),
        (".pptx", "presentation", "PowerPoint deck", "slide_profile", ("pdf", "png"), "libreoffice", True),
        (".odp", "presentation", "OpenDocument presentation", "slide_profile", ("pdf", "png"), "libreoffice", True),
        # Images
        (".png", "image", "PNG image", "image_metadata", ("jpg", "webp", "pdf", "png"), "pillow", False),
        (".jpg", "image", "JPEG image", "image_metadata", ("png", "webp", "pdf", "jpg"), "pillow", False),
        (".jpeg", "image", "JPEG image", "image_metadata", ("png", "webp", "pdf", "jpg"), "pillow", False),
        (".webp", "image", "WebP image", "image_metadata", ("png", "jpg", "pdf"), "pillow", False),
        (".tiff", "image", "TIFF image", "image_metadata", ("png", "jpg", "pdf"), "pillow", True),
        (".bmp", "image", "Bitmap image", "image_metadata", ("png", "jpg", "pdf"), "pillow", False),
        (".avif", "image", "AVIF image", "image_metadata", ("png", "jpg"), "pillow_optional", True),
        (".heic", "image", "HEIC image", "image_metadata", ("png", "jpg"), "heif_converter", True),
        # Audio / video
        (".mp3", "audio", "MP3 audio", "audio_metadata", ("wav", "ogg"), "ffmpeg", True),
        (".wav", "audio", "WAV audio", "audio_metadata", ("mp3", "ogg"), "ffmpeg", True),
        (".ogg", "audio", "Ogg audio", "audio_metadata", ("mp3", "wav"), "ffmpeg", True),
        (".opus", "audio", "Opus audio", "audio_metadata", ("mp3", "wav"), "ffmpeg", True),
        (".m4a", "audio", "M4A audio", "audio_metadata", ("mp3", "wav"), "ffmpeg", True),
        (".flac", "audio", "FLAC audio", "audio_metadata", ("mp3", "wav"), "ffmpeg", True),
        (".aac", "audio", "AAC audio", "audio_metadata", ("mp3", "wav"), "ffmpeg", True),
        (".mp4", "video", "MP4 video", "video_metadata", ("png", "mp3", "webm"), "ffmpeg", True),
        (".mov", "video", "MOV video", "video_metadata", ("png", "mp3", "mp4"), "ffmpeg", True),
        (".webm", "video", "WebM video", "video_metadata", ("png", "mp3", "mp4"), "ffmpeg", True),
        (".mkv", "video", "MKV video", "video_metadata", ("png", "mp3", "mp4"), "ffmpeg", True),
        (".avi", "video", "AVI video", "video_metadata", ("png", "mp3", "mp4"), "ffmpeg", True),
        # Game development assets
        (".glb", "asset_3d", "GLB model", "asset_metadata", ("gltf", "obj", "fbx", "png"), "blender_or_assimp", True),
        (".gltf", "asset_3d", "glTF model", "asset_metadata", ("glb", "obj", "fbx", "png"), "blender_or_assimp", True),
        (".obj", "asset_3d", "OBJ model", "asset_metadata", ("glb", "gltf", "fbx", "png"), "blender_or_assimp", True),
        (".fbx", "asset_3d", "FBX model", "asset_metadata", ("glb", "gltf", "obj", "png"), "blender_or_assimp", True),
        (".blend", "asset_3d", "Blender scene", "asset_metadata", ("glb", "fbx", "obj", "png"), "blender", True),
        (".stl", "asset_3d", "STL mesh", "asset_metadata", ("obj", "glb", "png"), "blender_or_assimp", True),
        (".dae", "asset_3d", "Collada asset", "asset_metadata", ("glb", "obj", "fbx", "png"), "blender_or_assimp", True),
        (".atlas", "asset_2d", "Texture atlas", "asset_metadata", ("png", "json"), "asset_manifest_tool", True),
    ]
    return {
        ext: FileCapability(
            extension=ext,
            family=family,
            label=label,
            extraction=extraction,
            export_targets=tuple(targets),
            default_tool=tool,
            review_required=review,
        )
        for ext, family, label, extraction, targets, tool, review in items
    }


def get_file_capability(source_name_or_extension: str) -> FileCapability | None:
    ext = _extract_extension(source_name_or_extension)
    return build_file_capability_registry().get(ext)


def parse_export_intent(
    request_text: str,
    *,
    recent_source_ref: str,
    dsgvo_mode: bool = False,
    delivery_hint: str = "review",
) -> ExportIntent:
    text = " ".join(str(request_text or "").lower().split())
    if not text:
        raise UniversalFileIOError("request_text must not be empty")
    target = _detect_target_format(text)
    return ExportIntent(
        source_ref=_safe_source_ref(recent_source_ref),
        request_hash=_hash_text(text),
        target_format=target,
        delivery_hint=_normalize_delivery_hint(delivery_hint),
        dsgvo_mode=bool(dsgvo_mode),
        local_only=bool(dsgvo_mode),
    )


def build_export_plan(
    intent: ExportIntent | Mapping[str, Any],
    *,
    source_name_or_extension: str,
    live_converter_enabled: bool = False,
) -> ExportPlan:
    normalized_intent = _coerce_intent(intent)
    capability = get_file_capability(source_name_or_extension)
    source_extension = _extract_extension(source_name_or_extension)
    blockers: list[str] = []
    warnings: list[str] = []

    if capability is None:
        blockers.append("source file type is not supported by Universal File IO")
    if not normalized_intent.supported:
        blockers.append("export target could not be detected from the follow-up request")
    elif capability is not None and normalized_intent.target_format not in capability.export_targets:
        blockers.append("requested target format is not supported for this source family")

    if capability is not None and capability.review_required:
        warnings.append("source family requires review before live conversion")
    if normalized_intent.local_only:
        warnings.append("DSGVO mode forces local-only converter selection")
    if not live_converter_enabled:
        warnings.append("live converter execution is disabled; this is a plan only")

    status = "plan_ready" if not blockers else "unsupported"
    if status == "plan_ready" and (capability and capability.review_required):
        status = "needs_review"

    target = normalized_intent.target_format or "unknown"
    tool = _required_tool(capability, target) if capability and normalized_intent.supported else ""
    output_ref = _output_ref(normalized_intent.source_ref, target)
    steps = _plan_steps(capability, target, tool, normalized_intent.local_only) if not blockers else ()

    return ExportPlan(
        status=status,
        source_ref=normalized_intent.source_ref,
        source_family=capability.family if capability else "unknown",
        source_extension=source_extension,
        target_format=target,
        required_tool=tool,
        output_ref=output_ref,
        local_only=normalized_intent.local_only,
        live_execution_allowed=False,
        delivery_allowed=False,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        steps=steps,
    )


def build_telegram_delivery_plan(
    export_result: Mapping[str, Any],
    *,
    reply_gate_enabled: bool = False,
    operator_live_go: bool = False,
) -> TelegramDeliveryPlan:
    """Plan a Telegram delivery without sending files or exposing identifiers."""

    payload = dict(export_result or {})
    target = _normalize_format(str(payload.get("target_format") or ""))
    mime_type = str(payload.get("mime_type") or _mime_type_for_target(target))
    method = _telegram_delivery_method(target, mime_type)
    delivery_ready = bool(payload.get("delivery_ready") and str(payload.get("status") or "") in {"exported", "ready", "sent"})
    blockers: list[str] = []
    warnings: list[str] = []

    if not delivery_ready:
        blockers.append("export_output_not_delivery_ready")
    if not method:
        blockers.append("telegram_delivery_method_unsupported")
    if not reply_gate_enabled:
        blockers.append("telegram_reply_gate_disabled")
    if not operator_live_go:
        blockers.append("telegram_delivery_live_go_missing")
    if target in {"png", "jpg", "jpeg", "webp"}:
        warnings.append("image_delivery_should_use_reviewed_preview_policy")
    if target in {"mp3", "wav", "ogg", "opus", "flac", "aac", "m4a"}:
        warnings.append("audio_delivery_should_use_reviewed_media_policy")

    send_allowed = not blockers
    return TelegramDeliveryPlan(
        status="ready" if send_allowed else "blocked",
        method=method,
        target_format=target,
        mime_type=mime_type,
        delivery_ready=delivery_ready,
        send_allowed=send_allowed,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def _coerce_intent(value: ExportIntent | Mapping[str, Any]) -> ExportIntent:
    if isinstance(value, ExportIntent):
        return value
    if not isinstance(value, Mapping):
        raise UniversalFileIOError("intent must be an ExportIntent or mapping")
    return ExportIntent(
        source_ref=_safe_source_ref(str(value.get("source_ref") or "")),
        request_hash=_safe_hash(str(value.get("request_hash") or "")),
        target_format=_normalize_format(str(value.get("target_format") or "")),
        delivery_hint=_normalize_delivery_hint(str(value.get("delivery_hint") or "review")),
        dsgvo_mode=bool(value.get("dsgvo_mode")),
        local_only=bool(value.get("local_only") or value.get("dsgvo_mode")),
    )


def _extract_extension(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    suffix = PurePosixPath(raw).suffix.lower()
    if not suffix and raw.startswith("."):
        suffix = raw.lower()
    return suffix


def _detect_target_format(text: str) -> str:
    lowered = str(text or "").lower()
    for alias in sorted(_TARGET_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
            return _TARGET_ALIASES[alias]
    return ""


def _normalize_format(value: str) -> str:
    text = str(value or "").lower().strip().lstrip(".")
    if not text:
        return ""
    if not re.fullmatch(r"[a-z0-9_]{1,32}", text):
        raise UniversalFileIOError("target_format is unsafe")
    return text


def _normalize_delivery_hint(value: str) -> str:
    text = str(value or "review").lower().strip()
    if text not in {"review", "telegram", "ui_download", "nextcloud", "project_folder"}:
        return "review"
    return text


def _safe_source_ref(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise UniversalFileIOError("recent_source_ref must not be empty")
    if _SAFE_REF_RE.fullmatch(text) and "/" not in text and "\\" not in text:
        return text
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _safe_hash(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"sha256:[a-f0-9]{64}", text):
        return text
    if re.fullmatch(r"[a-f0-9]{64}", text):
        return f"sha256:{text}"
    return _hash_text(text)


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()}"


def _output_ref(source_ref: str, target_format: str) -> str:
    digest = hashlib.sha256(f"{source_ref}:{target_format}".encode("utf-8")).hexdigest()[:16]
    return f"export:{digest}.{target_format or 'unknown'}"


def _required_tool(capability: FileCapability | None, target_format: str) -> str:
    if capability is None:
        return ""
    if capability.extension == ".pdf" and target_format in {"png", "jpg"}:
        return "pdf_page_renderer"
    if capability.extension == ".pdf" and target_format == "searchable_pdf":
        return "ocr_pipeline"
    if capability.family == "image":
        return "pillow"
    if capability.family in {"audio", "video"}:
        return "ffmpeg"
    if capability.family.startswith("asset_3d"):
        return "blender_or_assimp"
    return capability.default_tool


def _telegram_delivery_method(target_format: str, mime_type: str) -> str:
    target = _normalize_format(target_format)
    mime = str(mime_type or "").lower()
    if target in {"png", "jpg", "jpeg", "webp"} or mime.startswith("image/"):
        return "sendPhoto"
    if target in {"mp3", "wav", "ogg", "opus", "flac", "aac", "m4a"} or mime.startswith("audio/"):
        return "sendAudio"
    if target:
        return "sendDocument"
    return ""


def _mime_type_for_target(target_format: str) -> str:
    target = _normalize_format(target_format)
    if target == "pdf":
        return "application/pdf"
    if target in {"png", "jpg", "jpeg", "webp", "tiff", "bmp"}:
        return "image/jpeg" if target in {"jpg", "jpeg"} else f"image/{target}"
    if target in {"mp3", "wav", "ogg", "opus", "flac", "aac", "m4a"}:
        return "audio/mpeg" if target == "mp3" else f"audio/{target}"
    return "application/octet-stream"


def _plan_steps(capability: FileCapability | None, target_format: str, tool: str, local_only: bool) -> tuple[str, ...]:
    if capability is None:
        return ()
    steps = [
        "load source from reviewed inbox reference",
        f"select {tool} for {capability.family} to {target_format}",
        "write export to a new output reference without overwriting the original",
        "hold delivery until a separate Telegram/UI/Nextcloud delivery gate is approved",
    ]
    if local_only:
        steps.insert(1, "enforce local-only processing before tool selection")
    return tuple(steps)


def summarize_file_capabilities(extensions: Iterable[str] | None = None) -> dict[str, Any]:
    registry = build_file_capability_registry()
    selected = tuple(_extract_extension(ext) for ext in (extensions or registry.keys()))
    caps = [registry[ext] for ext in selected if ext in registry]
    by_family: dict[str, int] = {}
    for cap in caps:
        by_family[cap.family] = by_family.get(cap.family, 0) + 1
    return {
        "count": len(caps),
        "families": by_family,
        "extensions": tuple(cap.extension for cap in caps),
        "raw_content_visible": False,
    }
