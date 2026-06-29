"""File-type decisions for Universal Inbox intake.

The classifier intentionally avoids reading file contents beyond a small magic
byte sample. It gives the pipeline a stable safety/routing decision before any
parser, STT worker, archive expansion, or memory write is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path
from typing import Any


FILE_TYPE_SCHEMA = "odysseus.universal_inbox.file_type_decision.v1"

TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".html", ".htm", ".xml"})
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".epub", ".odt", ".rtf"})
EXTRACTABLE_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".avif"})
AUDIO_SUFFIXES = frozenset({".ogg", ".opus", ".mp3", ".wav", ".m4a", ".flac", ".aac"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi"})
ARCHIVE_SUFFIXES = frozenset({".zip", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"})
MESSAGE_SUFFIXES = frozenset({".eml", ".msg", ".ics", ".vcf"})
DANGEROUS_SUFFIXES = frozenset({".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".scr", ".com", ".dll", ".vbs", ".jar"})

_CATEGORY_BY_SUFFIX = {
    **{suffix: "text_extractable" for suffix in TEXT_SUFFIXES},
    **{suffix: "document_extractable" for suffix in DOCUMENT_SUFFIXES},
    **{suffix: "media_metadata" for suffix in IMAGE_SUFFIXES},
    **{suffix: "audio_transcribable" for suffix in AUDIO_SUFFIXES},
    **{suffix: "video_metadata" for suffix in VIDEO_SUFFIXES},
    **{suffix: "archive_expandable" for suffix in ARCHIVE_SUFFIXES},
    **{suffix: "structured_message" for suffix in MESSAGE_SUFFIXES},
    **{suffix: "dangerous" for suffix in DANGEROUS_SUFFIXES},
}

_MIME_PREFIX_CATEGORY = (
    ("text/", "text_extractable"),
    ("image/", "media_metadata"),
    ("audio/", "audio_transcribable"),
    ("video/", "video_metadata"),
)

_MIME_CATEGORY = {
    "application/pdf": "document_extractable",
    "application/json": "text_extractable",
    "application/xml": "text_extractable",
    "application/zip": "archive_expandable",
    "application/x-7z-compressed": "archive_expandable",
    "application/x-tar": "archive_expandable",
    "application/gzip": "archive_expandable",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document_extractable",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document_extractable",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "document_extractable",
    "text/calendar": "structured_message",
    "text/vcard": "structured_message",
    "message/rfc822": "structured_message",
}

_FAMILY_BY_CATEGORY = {
    "text_extractable": "text",
    "document_extractable": "document",
    "media_metadata": "image",
    "audio_transcribable": "audio",
    "video_metadata": "video",
    "archive_expandable": "archive",
    "structured_message": "message",
    "dangerous": "dangerous",
    "unsupported": "unknown",
}

_REVIEW_CODES = {
    "media_metadata": "image_metadata_only",
    "audio_transcribable": "audio_transcription_required",
    "video_metadata": "video_metadata_only",
    "archive_expandable": "archive_needs_review",
    "structured_message": "structured_message_needs_parser",
    "dangerous": "dangerous_type_blocked",
    "unsupported": "unsupported_type",
}


@dataclass(frozen=True)
class UniversalInboxFileTypeDecision:
    suffix: str
    mime_type: str
    category: str
    family: str
    review_required: bool
    reason_codes: tuple[str, ...]
    schema: str = FILE_TYPE_SCHEMA

    @property
    def extractable_now(self) -> bool:
        return self.category == "text_extractable" or (
            self.category == "document_extractable"
            and self.suffix in EXTRACTABLE_DOCUMENT_SUFFIXES
        )

    @property
    def blocked(self) -> bool:
        return self.category == "dangerous"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "suffix": self.suffix,
            "mime_type": self.mime_type,
            "category": self.category,
            "family": self.family,
            "review_required": self.review_required,
            "reason_codes": self.reason_codes,
            "extractable_now": self.extractable_now,
            "blocked": self.blocked,
        }
        return payload


def classify_universal_inbox_file(
    filename_or_path: str | Path,
    *,
    mime_type: str | None = None,
    sample: bytes | None = None,
) -> UniversalInboxFileTypeDecision:
    path = Path(str(filename_or_path))
    suffix = path.suffix.lower()
    normalized_mime = (mime_type or mimetypes.guess_type(path.name)[0] or "").lower()
    category = _category_from_suffix(suffix)

    if category == "unsupported":
        category = _category_from_mime(normalized_mime)
    if category == "unsupported":
        category = _category_from_magic(sample or b"")

    reason_codes: list[str] = []
    if suffix in DANGEROUS_SUFFIXES:
        category = "dangerous"
    if category == "document_extractable" and suffix not in EXTRACTABLE_DOCUMENT_SUFFIXES:
        reason_codes.append("document_extractor_pending")
    elif category in _REVIEW_CODES:
        reason_codes.append(_REVIEW_CODES[category])

    review_required = category not in {"text_extractable"} and not (
        category == "document_extractable" and suffix in EXTRACTABLE_DOCUMENT_SUFFIXES
    )
    return UniversalInboxFileTypeDecision(
        suffix=suffix,
        mime_type=normalized_mime,
        category=category,
        family=_FAMILY_BY_CATEGORY.get(category, "unknown"),
        review_required=review_required,
        reason_codes=tuple(reason_codes),
    )


def _category_from_suffix(suffix: str) -> str:
    return _CATEGORY_BY_SUFFIX.get(suffix, "unsupported")


def _category_from_mime(mime_type: str) -> str:
    if not mime_type:
        return "unsupported"
    if mime_type in _MIME_CATEGORY:
        return _MIME_CATEGORY[mime_type]
    for prefix, category in _MIME_PREFIX_CATEGORY:
        if mime_type.startswith(prefix):
            return category
    return "unsupported"


def _category_from_magic(sample: bytes) -> str:
    if not sample:
        return "unsupported"
    if sample.startswith(b"%PDF"):
        return "document_extractable"
    if sample.startswith(b"PK\x03\x04"):
        return "archive_expandable"
    if sample.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return "media_metadata"
    if sample.startswith((b"ID3", b"OggS", b"fLaC", b"RIFF")):
        return "audio_transcribable"
    if sample.startswith(b"MZ"):
        return "dangerous"
    return "unsupported"
